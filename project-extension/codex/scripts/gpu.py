#!/usr/bin/env python3
"""gpu.py — GPU pod router for rockie.

The "tool" the agent reaches for when it needs a GPU. Iterates every
configured adapter behind a single set of verbs:

  auth         — probe each configured adapter's API for liveness
  list-gpus    — aggregate type catalog across adapters (deduped, tagged)
  price        — side-by-side cheapest spot bid + on-demand per adapter
  create       — ranked spot fallback across adapters, with cooldown
                 filter from preemption_events. On-demand-only adapters
                 join only with --allow-on-demand.
  list-pods    — aggregate. tags each pod with its adapter.
  get-pod / stop / terminate / resume — need --provider since pod-ids
                 aren't globally unique.
  cost [--json] — per-adapter live rate + cumulative + grand total.
                 --json is the LLM-ergonomic surface.
  reconcile    — read live state, recompute per-pod accrued spend, SUM
                 into budget_usage[project:<p>:dollars]. The mechanism
                 the dollars ceiling depends on.

Compute-supplier selection now lives behind the deidentified `rockie-gpu`
broker (the single GPU surface agents reach for — see the gpu-spend and
inference-engineer skills). This script ships with NO named suppliers in
its default rank; the registry and DEFAULT_SPOT_RANK are empty. The
adapter machinery stays in place for private/self-hosted use.

Inject an adapter with --providers <dotted.module.path> (e.g.
tests.fakes.fake_a for testing, or a privately-maintained adapter
module). With an empty default rank, an empty selection resolves to no
adapters.

Budget invariant: this is the ONLY code path that writes to
budget_usage.dollars. The reconcile verb writes the
SUM(gpu_pods.accrued_dollars) formula the dollars ceiling depends on.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

# Make providers/ importable when run as a script from the harness root.
_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from providers.base import (  # noqa: E402
    AuthError,
    BidRejected,
    NoCapacity,
    OutOfStock,
    Pod,
    Price,
    ProviderError,
    Provider,
    Spend,
    SpotSpec,
)

DB_PATH = _HERE.parent / "memory" / "workflow.db"
STATE_DIR = _HERE.parent / ".state"

# Compute-supplier selection moved behind the deidentified `rockie-gpu`
# broker (the single GPU surface). No named suppliers ship in the default
# rank; inject a private adapter via --providers <dotted.module.path>.
DEFAULT_SPOT_RANK: list[str] = []
ON_DEMAND_FALLBACK: list[str] = []

# Cooldown window for preemption_events: a (provider, gpu_type) pair
# that preempted within this many minutes is skipped on create rank.
COOLDOWN_MIN = 10

STORAGE_RATE_PER_GB_HR = 0.10 / 30 / 24  # ≈ $0.000139/GB/hr (best-effort)


# ─── Provider discovery ────────────────────────────────────────────────────


# name → (env_var_required, module_path, class_name). Empty by default:
# named compute suppliers are no longer wired here — selection lives behind
# the deidentified `rockie-gpu` broker. A private deployment can register
# its own adapter, or inject one ad-hoc via --providers <dotted.module.path>.
_PROVIDER_REGISTRY: dict[str, tuple[str, str, str]] = {}


def _instantiate(name_or_path: str) -> Provider | None:
    """Resolve a provider name or dotted module path to a Provider instance.

    Names from _PROVIDER_REGISTRY are gated on the env var being set.
    Dotted paths (e.g. tests.fakes.fake_a) are imported as-is and the
    module's `Provider` symbol (or the only class with the right shape)
    is instantiated. Returns None if the env var is missing or the
    module/class can't be loaded — caller decides whether to skip or warn.
    """
    if name_or_path in _PROVIDER_REGISTRY:
        env_var, module_path, class_name = _PROVIDER_REGISTRY[name_or_path]
        if not os.environ.get(env_var, "").strip():
            return None
        try:
            mod = importlib.import_module(module_path)
            return getattr(mod, class_name)()
        except (ImportError, AttributeError, AuthError):
            return None

    # Dotted module path — testing hook for fakes. The user supplied an
    # explicit path, so failures here should be loud (silent skips here
    # mean a typo'd test path silently passes), and we add cwd to
    # sys.path so `tests.fakes.X`-style names resolve from the repo root.
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    try:
        mod = importlib.import_module(name_or_path)
    except ImportError as e:
        print(f"[gpu] {name_or_path}: import failed — {e}", file=sys.stderr)
        return None
    cls = getattr(mod, "Provider", None)
    if cls is None or not isinstance(cls, type):
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if isinstance(obj, type) and attr.endswith("Provider"):
                cls = obj
                break
    if cls is None:
        print(f"[gpu] {name_or_path}: no Provider class found", file=sys.stderr)
        return None
    try:
        return cls()
    except Exception as e:
        print(f"[gpu] {name_or_path}: instantiation failed — {e}", file=sys.stderr)
        return None


def discover_providers(override: list[str] | None) -> list[Provider]:
    """Return provider instances honoring --providers override or env presence."""
    if override:
        out = []
        for name in override:
            inst = _instantiate(name)
            if inst is not None:
                out.append(inst)
            else:
                print(f"[gpu] {name}: not configured (skipping)", file=sys.stderr)
        return out
    # Default: spot rank then on-demand, gated on env presence.
    out = []
    for name in DEFAULT_SPOT_RANK + ON_DEMAND_FALLBACK:
        inst = _instantiate(name)
        if inst is not None:
            out.append(inst)
    return out


# ─── DB helpers ────────────────────────────────────────────────────────────


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA trusted_schema=1")
    conn.row_factory = sqlite3.Row
    return conn


def _project_name() -> str:
    return os.environ.get("ROCKIE_PROJECT") or pathlib.Path.cwd().name


def _persist_created_pod(pod: Pod, spec: SpotSpec, hours_estimate: float) -> None:
    """Insert the new pod into gpu_pods and charge budget upper-bound."""
    conn = _db()
    project = _project_name()
    conn.execute(
        """
        INSERT OR REPLACE INTO gpu_pods
          (id, provider, gpu_type, gpu_count, bid_per_gpu, status, project, notes)
        VALUES (?, ?, ?, ?, ?, 'CREATED', ?, ?)
        """,
        (
            pod.id,
            pod.provider,
            spec.gpu_type,
            spec.gpu_count,
            pod.bid_per_gpu,
            project,
            spec.name,
        ),
    )
    conn.commit()

    # Charge the dollars budget — upper-bound at bid × count × hours.
    # Reconcile corrects against actual spend on each invocation.
    if pod.bid_per_gpu and hours_estimate:
        est = pod.bid_per_gpu * spec.gpu_count * hours_estimate
        try:
            subprocess.run(
                [sys.executable, str(_HERE / "budget.py"), "add", "dollars", str(est)],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            pass


def _on_cooldown(provider_name: str, gpu_type: str) -> bool:
    """True if (provider, gpu_type) had a preemption_events entry within
    the cooldown window."""
    conn = _db()
    row = conn.execute(
        """
        SELECT 1 FROM preemption_events
        WHERE provider = ? AND gpu_type = ?
          AND ts > datetime('now', ?)
        LIMIT 1
        """,
        (provider_name, gpu_type, f"-{COOLDOWN_MIN} minutes"),
    ).fetchone()
    return bool(row)


def _record_preemption(provider: str, pod_id: str | None, gpu_type: str, reason: str | None) -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO preemption_events (pod_id, provider, gpu_type, reason) VALUES (?, ?, ?, ?)",
        (pod_id, provider, gpu_type, reason),
    )
    conn.commit()


# ─── Cross-provider reconcile ──────────────────────────────────────────────


def reconcile_all(providers: list[Provider], *, verbose: bool = False) -> dict[str, Any]:
    """Pull live state from each provider, recompute per-pod accrued
    dollars, SUM into budget_usage[project:<p>:dollars].

    This is the canonical implementation. scripts/runpod.py's
    single-provider reconcile is being kept in sync until it's thinned;
    this one supersedes it for cross-provider.

    Returns a dict summary the CLI prints.
    """
    conn = _db()
    now = datetime.now(timezone.utc)

    # Index live pods per provider so we can lookup by id.
    live_by_provider: dict[str, dict[str, Pod]] = {}
    failures: list[str] = []
    for prov in providers:
        try:
            pods = prov.list_pods()
        except (AuthError, ProviderError) as e:
            failures.append(f"{prov.name}: {type(e).__name__}: {e}")
            continue
        live_by_provider[prov.name] = {p.id: p for p in pods}

    rows = conn.execute(
        "SELECT id, provider, created_at, project, bid_per_gpu, gpu_count FROM gpu_pods"
    ).fetchall()

    updates = 0
    for row in rows:
        pod_id = row["id"]
        prov_name = row["provider"]
        if prov_name not in live_by_provider:
            # provider not configured this session OR list_pods failed —
            # leave the row alone so we don't zero out a known-good accrual.
            continue
        live = live_by_provider[prov_name].get(pod_id)
        if not live:
            # Pod is gone from the provider side.
            conn.execute(
                """
                UPDATE gpu_pods SET status='GONE', last_reconciled_at=datetime('now')
                WHERE id = ? AND status NOT IN ('TERMINATED','GONE')
                """,
                (pod_id,),
            )
            continue

        try:
            created = datetime.fromisoformat((row["created_at"] or "").replace(" ", "T")).replace(
                tzinfo=timezone.utc
            )
        except Exception:
            if verbose:
                print(f"[reconcile] skipping {pod_id}: bad created_at", file=sys.stderr)
            continue
        elapsed_hr = max(0.0, (now - created).total_seconds() / 3600.0)

        # Best-effort per-pod compute rate. We use the bid-at-create as
        # the rate proxy: that's the upper bound the user agreed to pay
        # per GPU-hr, and reconcile is biased to over-budget anyway.
        # When live.compute_per_hr is populated by an adapter we'd prefer
        # that — falls through to bid until adapters bump it.
        compute_rate = float(row["bid_per_gpu"] or 0) * int(row["gpu_count"] or 1)
        # Storage rate: use live volume if the adapter set Pod.metadata
        # with disk_space; else fall back to gpu_pods.gpu_count*0 (we
        # don't track requested volume_gb on gpu_pods today). Conservative
        # fallback is to charge zero storage for non-RUNNING pods we can't
        # measure — better to undercount storage than to invent.
        vol_gb = int(live.metadata.get("disk_space") or live.metadata.get("volumeInGb") or 0)
        storage_rate = STORAGE_RATE_PER_GB_HR * vol_gb

        if live.status == "RUNNING":
            rate = compute_rate + storage_rate
        else:
            # EXITED/STOPPED/PREEMPTED: pay storage only on volumes we
            # can see. Conservatively assume half compute over the
            # elapsed window (we don't know exact RUNNING-seconds).
            rate = (compute_rate * 0.5) + storage_rate

        # Detect preemption transition: was RUNNING last time, isn't now.
        # Best signal we have is `intended=running, actual!=running` from
        # adapters that surface it via metadata.
        intended = (live.metadata.get("intended_status") or "").lower()
        actual = (live.metadata.get("actual_status") or live.status or "").lower()
        if intended == "running" and actual not in ("running", ""):
            # Record preemption_events row idempotently — only one per
            # 5-minute window per pod.
            recent = conn.execute(
                """
                SELECT 1 FROM preemption_events
                WHERE pod_id = ? AND ts > datetime('now', '-5 minutes')
                LIMIT 1
                """,
                (pod_id,),
            ).fetchone()
            if not recent:
                conn.execute(
                    """
                    INSERT INTO preemption_events (pod_id, provider, gpu_type, reason)
                    VALUES (?, ?, ?, ?)
                    """,
                    (pod_id, prov_name, live.gpu_type or "?", f"intended={intended} actual={actual}"),
                )

        accrued = round(elapsed_hr * rate, 4)
        conn.execute(
            """
            UPDATE gpu_pods SET accrued_dollars = ?, last_reconciled_at = datetime('now'),
                                status = ?
            WHERE id = ?
            """,
            (accrued, live.status, pod_id),
        )
        updates += 1

    # Rewrite project-scope dollars counter = SUM(accrued_dollars).
    totals = conn.execute(
        "SELECT project, SUM(accrued_dollars) AS total FROM gpu_pods GROUP BY project"
    ).fetchall()
    for t in totals:
        proj = t["project"] or _project_name()
        total = float(t["total"] or 0)
        bkey = f"project:{proj}:dollars"
        conn.execute(
            """
            INSERT INTO budget_usage (key, project, session_id, metric, value)
            VALUES (?, ?, NULL, 'dollars', ?)
            ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = datetime('now')
            """,
            (bkey, proj, total, total),
        )

    conn.commit()

    # Stamp last-reconcile time so the budget-reconcile.sh hook's TTL works.
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "last_reconcile_ts").write_text(str(int(time.time())))

    return {
        "updated": updates,
        "providers_queried": list(live_by_provider.keys()),
        "providers_failed": failures,
        "totals": [
            {"project": t["project"] or _project_name(), "dollars": float(t["total"] or 0)}
            for t in totals
        ],
    }


# ─── Verb implementations ──────────────────────────────────────────────────


def cmd_auth(args) -> int:
    providers = discover_providers(args.providers)
    if not providers:
        print("[gpu] no adapters configured. GPU operations route through `rockie-gpu`; inject a private adapter with --providers <dotted.module.path>.", file=sys.stderr)
        return 2
    fail = 0
    for p in providers:
        try:
            p.auth()
            print(f"[auth] {p.name}: ok")
        except AuthError as e:
            print(f"[auth] {p.name}: AUTH FAILED — {e}", file=sys.stderr)
            fail += 1
        except ProviderError as e:
            print(f"[auth] {p.name}: error — {e}", file=sys.stderr)
            fail += 1
    return 0 if fail == 0 else 3


def cmd_list_gpus(args) -> int:
    providers = discover_providers(args.providers)
    if not providers:
        print("[gpu] no providers configured", file=sys.stderr)
        return 2
    rows: list[tuple[str, str, str, int | None]] = []  # (provider, name, id, mem_gb)
    for p in providers:
        try:
            for g in p.list_gpus(grep=args.grep):
                rows.append((p.name, g.name, g.id, g.memory_gb))
        except ProviderError as e:
            print(f"[gpu] {p.name}: list_gpus failed — {e}", file=sys.stderr)
    print(f"{'provider':10} {'gpu':30} {'id':40} mem(GB)")
    print("─" * 95)
    for prov, name, gid, mem in sorted(rows):
        print(f"{prov:10} {name:30} {gid:40} {mem if mem is not None else '-':>7}")
    return 0


def cmd_price(args) -> int:
    providers = discover_providers(args.providers)
    if not providers:
        return 2
    print(f"{'provider':10} {'min_bid/hr':>12} {'on_demand/hr':>14} {'stock':>6}")
    print("─" * 50)
    seen_any = False
    for p in providers:
        try:
            pr = p.price(args.gpu_type, args.gpu_count)
            seen_any = True
            mb = f"${pr.min_bid:.4f}" if pr.min_bid is not None else "-"
            od = f"${pr.on_demand:.4f}" if pr.on_demand is not None else "-"
            print(f"{p.name:10} {mb:>12} {od:>14} {pr.stock:>6}")
        except OutOfStock:
            print(f"{p.name:10} {'OutOfStock':>12} {'-':>14} {'0':>6}")
        except ProviderError as e:
            print(f"{p.name:10} {'error':>12}  {e}")
    return 0 if seen_any else 3


def cmd_create(args) -> int:
    providers = discover_providers(args.providers)
    if not providers:
        return 2

    # Build the rank: spot-supporting first, on-demand last (and only
    # if --allow-on-demand). Cooldown filter on (provider, gpu_type).
    spot_providers = [p for p in providers if p.supports_bid_auction]
    on_demand_only = [p for p in providers if not p.supports_bid_auction]

    if not args.allow_on_demand:
        on_demand_only = []

    # Apply cooldown filter — push cooldown'd providers to the back rather
    # than dropping them entirely (we still need a fallback).
    def cooldown_sort(plist: list[Provider]) -> list[Provider]:
        fresh, cooled = [], []
        for p in plist:
            (cooled if _on_cooldown(p.name, args.gpu_type) else fresh).append(p)
        return fresh + cooled

    rank = cooldown_sort(spot_providers) + on_demand_only

    spec = SpotSpec(
        gpu_type=args.gpu_type,
        gpu_count=args.gpu_count,
        volume_gb=args.volume_gb,
        disk_gb=args.disk_gb,
        bid=args.bid,
        image=args.image,
        name=args.name,
        ssh_key_id=os.environ.get(args.ssh_key_id_env, "") or None,
        env=dict(kv.split("=", 1) for kv in args.env) if args.env else {},
        extras={
            "secure": args.secure,
            "min_vcpu": args.min_vcpu,
            "min_ram": args.min_ram,
            "reliability_min": args.reliability_min,
        },
    )

    print(f"create plan: gpu={args.gpu_type}×{args.gpu_count}  hours_est={args.hours}")
    print(f"  rank: {[p.name for p in rank]}")
    if not args.yes:
        # Show price across providers as the dry-run preview.
        for p in rank:
            try:
                pr = p.price(args.gpu_type, args.gpu_count)
                mb = f"${pr.min_bid:.4f}" if pr.min_bid is not None else "-"
                od = f"${pr.on_demand:.4f}" if pr.on_demand is not None else "-"
                print(f"  · {p.name:10} min_bid={mb} on_demand={od} stock={pr.stock}")
            except OutOfStock:
                print(f"  · {p.name:10} OutOfStock")
            except ProviderError as e:
                print(f"  · {p.name:10} error: {e}")
        print("\ndry-run (pass --yes to actually provision)")
        return 0

    last_err: str | None = None
    for p in rank:
        try:
            print(f"\n[try] {p.name} …")
            pod = p.create_spot(spec, yes=True)
            if pod is None:
                last_err = f"{p.name}: dry-run guard hit unexpectedly"
                continue
            _persist_created_pod(pod, spec, args.hours)
            print(f"[created] provider={p.name} pod_id={pod.id} bid=${pod.bid_per_gpu}/h")
            if pod.ssh_endpoint:
                print(f"  ssh: {pod.ssh_endpoint}")
            else:
                print(f"  (poll `gpu.py get-pod --provider {p.name} {pod.id}` for ssh)")
            return 0
        except (OutOfStock, BidRejected, NoCapacity) as e:
            last_err = f"{p.name}: {type(e).__name__}: {e}"
            print(f"  [hop] {last_err}", file=sys.stderr)
            continue
        except AuthError as e:
            last_err = f"{p.name}: AuthError: {e}"
            print(f"  [hop] {last_err}", file=sys.stderr)
            continue
        except ProviderError as e:
            last_err = f"{p.name}: ProviderError: {e}"
            print(f"  [hop] {last_err}", file=sys.stderr)
            continue
    print(f"\n[gpu] all {len(rank)} provider(s) exhausted. last: {last_err}", file=sys.stderr)
    return 3


def cmd_list_pods(args) -> int:
    providers = discover_providers(args.providers)
    print(f"{'provider':10} {'pod_id':25} {'status':10}  ssh")
    print("─" * 90)
    for p in providers:
        try:
            for pod in p.list_pods():
                print(f"{p.name:10} {pod.id:25} {pod.status:10}  {pod.ssh_endpoint or '-'}")
        except ProviderError as e:
            print(f"{p.name:10} <error: {e}>", file=sys.stderr)
    return 0


def _resolve_provider(args, providers: list[Provider]) -> Provider | None:
    """For per-pod verbs (get-pod, stop, etc.) the user must specify
    --provider. Pod-ids aren't globally unique."""
    if not args.provider:
        print("[gpu] --provider <name> required for per-pod verbs", file=sys.stderr)
        return None
    for p in providers:
        if p.name == args.provider:
            return p
    print(f"[gpu] provider {args.provider!r} not configured", file=sys.stderr)
    return None


def cmd_get_pod(args) -> int:
    p = _resolve_provider(args, discover_providers(args.providers))
    if p is None:
        return 2
    pod = p.get_pod(args.pod_id)
    print(json.dumps({
        "id": pod.id,
        "provider": pod.provider,
        "status": pod.status,
        "ssh_endpoint": pod.ssh_endpoint,
        "gpu_type": pod.gpu_type,
        "gpu_count": pod.gpu_count,
        "metadata": pod.metadata,
    }, indent=2))
    return 0


def cmd_stop(args) -> int:
    p = _resolve_provider(args, discover_providers(args.providers))
    if p is None:
        return 2
    if not args.yes:
        print(f"dry-run: would stop {args.provider}:{args.pod_id} (--yes to confirm)")
        return 0
    p.stop(args.pod_id, yes=True)
    conn = _db()
    conn.execute(
        "UPDATE gpu_pods SET status='STOPPED', stopped_at=datetime('now') WHERE id=? AND provider=?",
        (args.pod_id, p.name),
    )
    conn.commit()
    print(f"[stopped] {p.name}:{args.pod_id}")
    return 0


def cmd_terminate(args) -> int:
    p = _resolve_provider(args, discover_providers(args.providers))
    if p is None:
        return 2
    if not args.yes:
        print(f"dry-run: would TERMINATE {args.provider}:{args.pod_id} (data LOST). --yes to confirm.")
        return 0
    p.terminate(args.pod_id, yes=True)
    conn = _db()
    conn.execute(
        "UPDATE gpu_pods SET status='TERMINATED', stopped_at=datetime('now') WHERE id=? AND provider=?",
        (args.pod_id, p.name),
    )
    conn.commit()
    print(f"[terminated] {p.name}:{args.pod_id}")
    return 0


def cmd_resume(args) -> int:
    p = _resolve_provider(args, discover_providers(args.providers))
    if p is None:
        return 2
    if not args.yes:
        print(f"dry-run: would resume {args.provider}:{args.pod_id} (--yes to confirm)")
        return 0
    p.resume(args.pod_id, yes=True, bid=args.bid)
    print(f"[resumed] {p.name}:{args.pod_id}")
    return 0


def cmd_reconcile(args) -> int:
    providers = discover_providers(args.providers)
    if not providers:
        # Don't error out; reconcile-with-no-providers is a legitimate
        # state on a fresh install. Stamp the time and exit clean.
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "last_reconcile_ts").write_text(str(int(time.time())))
        if not args.quiet:
            print("[reconcile] no providers configured — nothing to do")
        return 0
    summary = reconcile_all(providers, verbose=args.verbose)
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    if not args.quiet:
        print(
            f"[reconcile] updated {summary['updated']} pod row(s); "
            f"queried {summary['providers_queried']}; "
            f"failed {len(summary['providers_failed'])}"
        )
        for f in summary["providers_failed"]:
            print(f"  ! {f}", file=sys.stderr)
        for t in summary["totals"]:
            print(f"  {t['project']}: ${t['dollars']:.4f}")
    return 0


def cmd_cost(args) -> int:
    providers = discover_providers(args.providers)
    # Reconcile first so cumulative numbers are truthful.
    reconcile_all(providers, verbose=False) if providers else None

    snapshots: list[dict[str, Any]] = []
    conn = _db()
    project = _project_name()
    for p in providers:
        try:
            spend = p.current_spend()
        except ProviderError as e:
            snapshots.append({"provider": p.name, "error": str(e), "billing_url": p.billing_url})
            continue
        # cumulative_usd: SUM gpu_pods.accrued_dollars for this provider
        # within the current project (filter out other projects' rows).
        row = conn.execute(
            """
            SELECT COALESCE(SUM(accrued_dollars), 0) AS total
            FROM gpu_pods
            WHERE provider = ? AND COALESCE(project, ?) = ?
            """,
            (p.name, project, project),
        ).fetchone()
        spend_d = asdict(spend)
        spend_d["cumulative_usd"] = float(row["total"] or 0)
        spend_d["billing_url"] = p.billing_url
        spend_d["total_per_hr"] = spend.compute_per_hr + spend.storage_per_hr
        snapshots.append(spend_d)

    grand_total_per_hr = sum(s.get("total_per_hr", 0) for s in snapshots)
    grand_cumulative = sum(s.get("cumulative_usd", 0) for s in snapshots)
    summary = {
        "project": project,
        "providers": snapshots,
        "grand_total_per_hr": grand_total_per_hr,
        "grand_cumulative_usd": grand_cumulative,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"── GPU spend across providers · project={project} ──")
    if not snapshots:
        print("  (no providers configured)")
        return 0
    print(f"{'provider':12} {'running':>7} {'$/hr':>10} {'cumulative':>12}  billing")
    print("─" * 80)
    for s in snapshots:
        if "error" in s:
            print(f"{s['provider']:12} {'-':>7} {'error':>10} {'-':>12}  {s.get('billing_url','-')}")
            continue
        print(
            f"{s['provider']:12} {s['running_pods']:>7} "
            f"{'$'+format(s['total_per_hr'],'.4f'):>10} "
            f"{'$'+format(s['cumulative_usd'],'.4f'):>12}  "
            f"{s['billing_url']}"
        )
    print("─" * 80)
    print(
        f"{'TOTAL':12} {'':7} {'$'+format(grand_total_per_hr,'.4f'):>10} "
        f"{'$'+format(grand_cumulative,'.4f'):>12}"
    )
    return 0


def cmd_dashboard(args) -> int:
    """One-screen human dashboard. The fastest path from "is the agent
    burning money?" to a yes/no answer.

    Sections (top to bottom):
      1. Header with project + total burn rate + cumulative.
      2. Per-provider rows: status icon, running/idle, $/hr, cumulative,
         clickable billing URL.
      3. Budget gauge if a dollars ceiling is configured.
      4. Idle-storage warning if any (paying for nothing → terminate).
      5. Recent preemption events (last 30 min) if any.
      6. Footer with timestamp + reconcile-age so users can spot stale data.
    """
    providers = discover_providers(args.providers)

    # Reconcile silently so the cumulative numbers we display are fresh.
    if providers:
        reconcile_all(providers, verbose=False)

    conn = _db()
    project = _project_name()

    # Per-provider snapshot
    provider_rows: list[dict[str, Any]] = []
    for p in providers:
        try:
            spend = p.current_spend()
        except ProviderError as e:
            provider_rows.append({"provider": p.name, "error": str(e), "billing_url": p.billing_url})
            continue
        cum_row = conn.execute(
            """
            SELECT COALESCE(SUM(accrued_dollars), 0) AS total
            FROM gpu_pods
            WHERE provider = ? AND COALESCE(project, ?) = ?
            """,
            (p.name, project, project),
        ).fetchone()
        provider_rows.append({
            "provider": p.name,
            "running": spend.running_pods,
            "idle_gb": spend.idle_volume_gb,
            "compute_per_hr": spend.compute_per_hr,
            "storage_per_hr": spend.storage_per_hr,
            "total_per_hr": spend.compute_per_hr + spend.storage_per_hr,
            "cumulative_usd": float(cum_row["total"] or 0),
            "billing_url": p.billing_url,
        })

    grand_per_hr = sum(r.get("total_per_hr", 0) for r in provider_rows)
    grand_cumulative = sum(r.get("cumulative_usd", 0) for r in provider_rows)

    # Budget ceiling (optional)
    ceiling_row = conn.execute(
        """
        SELECT value FROM budget_ceilings
        WHERE key = ? OR key = 'project:dollars'
        ORDER BY (key = ?) DESC LIMIT 1
        """,
        (f"project:{project}:dollars", f"project:{project}:dollars"),
    ).fetchone() if _table_exists(conn, "budget_ceilings") else None
    ceiling = float(ceiling_row["value"]) if ceiling_row else None

    # Recent preemption events
    preempts = conn.execute(
        """
        SELECT provider, gpu_type, ts, reason
        FROM preemption_events
        WHERE ts > datetime('now', '-30 minutes')
        ORDER BY ts DESC LIMIT 5
        """
    ).fetchall()

    # Reconcile age
    last_reconcile_age = None
    last_ts_file = STATE_DIR / "last_reconcile_ts"
    if last_ts_file.exists():
        try:
            last_reconcile_age = int(time.time()) - int(last_ts_file.read_text().strip())
        except (ValueError, OSError):
            pass

    # ── Render ──
    box_w = 72
    title = f" GPU spend dashboard · project={project} "
    print("┌" + title.center(box_w - 2, "─") + "┐")

    # Top-line burn + cumulative
    burn_str = f"${grand_per_hr:.4f}/hr"
    cum_str = f"${grand_cumulative:.4f} cumulative"
    daily = grand_per_hr * 24
    line = f"  ▶ live rate: {burn_str:>16}   ≈ ${daily:.2f}/day   ·   {cum_str}"
    print("│" + line.ljust(box_w - 2) + "│")
    print("├" + "─" * (box_w - 2) + "┤")

    # Per-provider rows
    if not provider_rows:
        print("│" + "  (no adapters configured — GPU ops route through `rockie-gpu`)".ljust(box_w - 2) + "│")
    for r in provider_rows:
        if "error" in r:
            line = f"  ✗ {r['provider']:10} ERROR: {r['error'][:40]}"
            print("│" + line.ljust(box_w - 2) + "│")
            continue
        icon = "▶" if r["running"] > 0 else ("·" if r["idle_gb"] > 0 else "○")
        rate_str = f"${r['total_per_hr']:.4f}/hr"
        cum_str = f"${r['cumulative_usd']:.4f}"
        line = (
            f"  {icon} {r['provider']:10} "
            f"running={r['running']:<2} idle={r['idle_gb']:>4}GB  "
            f"{rate_str:>12}  {cum_str:>10}"
        )
        print("│" + line.ljust(box_w - 2) + "│")
        url_line = f"      → {r['billing_url']}"
        print("│" + url_line.ljust(box_w - 2) + "│")

    # Budget gauge
    if ceiling is not None and ceiling > 0:
        pct = grand_cumulative / ceiling
        bar_w = 40
        filled = min(bar_w, int(pct * bar_w))
        bar = "█" * filled + "░" * (bar_w - filled)
        warn = " ⚠ OVER" if pct >= 1.0 else (" ⚠" if pct >= 0.8 else "")
        print("├" + "─" * (box_w - 2) + "┤")
        line = f"  budget: [{bar}] {pct*100:.1f}% of ${ceiling:.2f}{warn}"
        print("│" + line.ljust(box_w - 2) + "│")

    # Idle-storage warning
    idle_total = sum(r.get("idle_gb", 0) for r in provider_rows if "error" not in r)
    if idle_total > 0 and grand_per_hr > 0:
        running_total = sum(r.get("running", 0) for r in provider_rows if "error" not in r)
        if running_total == 0:
            print("├" + "─" * (box_w - 2) + "┤")
            line = f"  ⚠ {idle_total}GB on stopped pods accruing storage. Terminate to stop bleed."
            print("│" + line.ljust(box_w - 2) + "│")

    # Preemption events
    if preempts:
        print("├" + "─" * (box_w - 2) + "┤")
        line = f"  ⚡ {len(preempts)} preemption(s) in last 30 min:"
        print("│" + line.ljust(box_w - 2) + "│")
        for ev in preempts:
            line = f"      {ev['ts']}  {ev['provider']}/{ev['gpu_type']}"
            print("│" + line.ljust(box_w - 2) + "│")

    # Footer
    print("├" + "─" * (box_w - 2) + "┤")
    age_str = f"{last_reconcile_age}s ago" if last_reconcile_age is not None else "—"
    line = f"  reconciled {age_str}    `gpu.py cost --json` for LLM-readable"
    print("│" + line.ljust(box_w - 2) + "│")
    print("└" + "─" * (box_w - 2) + "┘")
    return 0


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


# ─── argparse ──────────────────────────────────────────────────────────────


def _add_providers_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--providers",
        type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
        default=None,
        help="Comma-separated provider names or dotted module paths. Default: env-discovered.",
    )


def _check_gpu_mode() -> int | None:
    """Honor ROCKIE_GPU_MODE. Return an exit code to short-circuit
    main() if the mode opts out of the router; None to continue.

    Modes:
      "router" / unset — default; cross-provider router (this script).
      "custom"        — user has their own GPU setup; gpu.py is bypassed.
                        Agent should reach for the /gpu-custom skill.
      "none"          — no GPU layer at all. Smoke / docs / autopilot
                        without compute provisioning.
    """
    mode = (os.environ.get("ROCKIE_GPU_MODE", "router") or "router").strip().lower()
    if mode in ("router", ""):
        return None
    if mode == "custom":
        print(
            "[gpu] ROCKIE_GPU_MODE=custom — bypassing the cross-provider router.\n"
            "      Your custom GPU setup is documented in .codex/gpu-custom.md\n"
            "      (or run /gpu-custom-setup if you haven't completed onboarding).\n"
            "      For agent-driven GPU operations in custom mode, use the\n"
            "      /gpu-custom skill instead of this CLI.",
            file=sys.stderr,
        )
        return 0
    if mode == "none":
        print(
            "[gpu] ROCKIE_GPU_MODE=none — GPU layer disabled.\n"
            "      Set ROCKIE_GPU_MODE=router (or unset) in .env to enable.",
            file=sys.stderr,
        )
        return 0
    print(f"[gpu] unknown ROCKIE_GPU_MODE={mode!r}; valid: router | custom | none", file=sys.stderr)
    return 2


def main() -> int:
    rc = _check_gpu_mode()
    if rc is not None:
        return rc

    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd")

    ap = sub.add_parser("auth", help="Probe each configured provider's API")
    _add_providers_arg(ap)
    ap.set_defaults(func=cmd_auth)

    lp = sub.add_parser("list-gpus", help="Aggregate GPU type catalog")
    _add_providers_arg(lp)
    lp.add_argument("--grep", help="Filter by substring of name or id")
    lp.set_defaults(func=cmd_list_gpus)

    pp = sub.add_parser("price", help="Side-by-side spot+on-demand price across providers")
    _add_providers_arg(pp)
    pp.add_argument("gpu_type")
    pp.add_argument("--gpu-count", type=int, default=1)
    pp.set_defaults(func=cmd_price)

    cp = sub.add_parser("create", help="Ranked-fallback create across providers")
    _add_providers_arg(cp)
    cp.add_argument("--gpu-type", required=True)
    cp.add_argument("--gpu-count", type=int, default=1)
    cp.add_argument("--bid", type=float, help="$/GPU-hr; default = each provider's current min")
    cp.add_argument("--hours", type=float, default=1.0, help="Estimated runtime for budget upper-bound")
    cp.add_argument("--volume-gb", type=int, default=40)
    cp.add_argument("--disk-gb", type=int, default=40)
    cp.add_argument("--image", default="")
    cp.add_argument("--name", default="rockie-spot")
    cp.add_argument("--env", nargs="*", default=[], help="KEY=VALUE pairs")
    cp.add_argument("--secure", action="store_true", help="(RunPod) use SECURE-cloud only")
    cp.add_argument("--min-vcpu", type=int, default=4)
    cp.add_argument("--min-ram", type=int, default=16)
    cp.add_argument("--reliability-min", type=float, default=0.95, help="(Vast) host-quality floor")
    cp.add_argument(
        "--ssh-key-id-env",
        default="PRIME_SSH_KEY_ID",
        help="Env var holding the SSH key id (Prime needs it)",
    )
    cp.add_argument(
        "--allow-on-demand",
        action="store_true",
        help="Permit on-demand-only providers to be considered last (none by default)",
    )
    cp.add_argument("--yes", action="store_true")
    cp.set_defaults(func=cmd_create)

    lpp = sub.add_parser("list-pods", help="Aggregate pods across providers")
    _add_providers_arg(lpp)
    lpp.set_defaults(func=cmd_list_pods)

    gp = sub.add_parser("get-pod", help="Fetch one pod (requires --provider)")
    _add_providers_arg(gp)
    gp.add_argument("pod_id")
    gp.add_argument("--provider", required=True)
    gp.set_defaults(func=cmd_get_pod)

    sp = sub.add_parser("stop", help="Pause a pod (requires --provider). Dry-run unless --yes.")
    _add_providers_arg(sp)
    sp.add_argument("pod_id")
    sp.add_argument("--provider", required=True)
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_stop)

    tp = sub.add_parser("terminate", help="DELETE a pod (requires --provider). Dry-run unless --yes.")
    _add_providers_arg(tp)
    tp.add_argument("pod_id")
    tp.add_argument("--provider", required=True)
    tp.add_argument("--yes", action="store_true")
    tp.set_defaults(func=cmd_terminate)

    rsp = sub.add_parser("resume", help="Resume a pod (requires --provider). Dry-run unless --yes.")
    _add_providers_arg(rsp)
    rsp.add_argument("pod_id")
    rsp.add_argument("--provider", required=True)
    rsp.add_argument("--bid", type=float, help="(RunPod) spot bid; bid=None on RunPod = on-demand")
    rsp.add_argument("--yes", action="store_true")
    rsp.set_defaults(func=cmd_resume)

    rc = sub.add_parser(
        "reconcile",
        help="Pull live state, recompute accrued cost, rewrite budget.dollars",
    )
    _add_providers_arg(rc)
    rc.add_argument("--verbose", "-v", action="store_true")
    rc.add_argument("--quiet", "-q", action="store_true")
    rc.add_argument("--json", action="store_true", help="machine-readable summary")
    rc.set_defaults(func=cmd_reconcile)

    cp_cost = sub.add_parser("cost", help="Cross-provider spend snapshot + cumulative")
    _add_providers_arg(cp_cost)
    cp_cost.add_argument("--json", action="store_true", help="machine-readable summary (LLM-ergonomic)")
    cp_cost.set_defaults(func=cmd_cost)

    db = sub.add_parser(
        "dashboard",
        help="One-screen human dashboard: live rates, cumulative, billing URLs, budget gauge",
    )
    _add_providers_arg(db)
    db.set_defaults(func=cmd_dashboard)

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
