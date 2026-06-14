#!/usr/bin/env python3
"""
/experiment skill — job submit + poll helper.

Submits a job to platform-context's ``POST /api/jobs/submit``, polls
``GET /api/jobs/{id}`` every 10 seconds, prints state changes + the
last log line, and exits with the job's exit code (0 for DONE, 1 for
FAILED/CANCELLED).

Reads ``ROCKIELAB_API_URL``, ``ROCKIELAB_TENANT_ID``, and
``ROCKIELAB_TENANT_TOKEN`` from env. ``ROCKIELAB_TENANT_TOKEN``
authenticates the request, but tenant identity must come from
``ROCKIELAB_TENANT_ID`` or the explicit ``--tenant-id`` override. The
headers send token auth as ``X-Tenant-Token`` and tenant identity as
``X-Tenant-Id``.

Usage::

    submit.py --gpu-type A100_80GB --gpu-count 4 \\
              --script-file path.sh --timeout 14400

CLI surface (argparse):
- ``--gpu-type``    GPU type (A40_48GB | A100_80GB | H100_SXM | H200 | B200). Required.
- ``--gpu-count``   Integer >= 1. Required.
- ``--script-file`` Path to the bash script body. Required.
- ``--timeout``     Job timeout in seconds. Required.
- ``--term-sheet-json`` Approved budget term-sheet artifact. Required unless ``--allow-ungated-submit`` is set.
- ``--budget-cents`` Optional assertion that must equal the approved term-sheet user budget.
- ``--tenant-id``   Override env-driven tenant id.
- ``--region``      Optional GPU region.
- ``--tier``        Optional capacity tier.
- ``--env``         Repeatable ``KEY=VALUE`` for the job env.
- ``--api-url``     Override ``ROCKIELAB_API_URL``.
- ``--allow-ungated-submit`` Bypass the budget-term-sheet gate. Intended for legacy/manual paths only.
- ``--no-poll``     Submit and exit; don't poll.
- ``--quiet``       Print only the final summary.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from monitor_contract import normalize_monitor_envelope
from monitoring_profiles import infer_software, resolve_profile_snapshot


TERMINAL_STATES = {"DONE", "FAILED", "CANCELLED"}
DASHBOARD_LOG_SUMMARY_LIMIT = 500
SECRET_REDACTION = "[redacted]"
SECRET_FIELD_NAMES = (
    "api_key",
    "apikey",
    "api-key",
    "cookie",
    "credentials",
    "token",
    "access_token",
    "refresh_token",
    "bearer_token",
    "license_token",
    "secret",
    "password",
    "credential",
    "signature",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
)
SECRET_FIELD_PATTERN = "|".join(re.escape(name) for name in SECRET_FIELD_NAMES)
SIGNED_URL_QUERY_PATTERN = re.compile(
    r"(?i)([?&](?:"
    r"X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token|"
    r"Signature|AWSAccessKeyId|Expires|sig|se|sp|sv|sr|skoid|sktid|skt|ske|sks|skv|"
    r"token|access_token|api_key|apikey|secret"
    r")=)([^&#\s,;}\]\)\"']+)"
)
ROCKIE_RUNTIME_USER_AGENT = "rockie-runtime/1.0 (+https://api.rockielab.com)"


def _err(msg: str, *, exit_code: int = 2) -> None:
    sys.stderr.write(f"submit.py: error: {msg}\n")
    sys.exit(exit_code)


def _http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: Optional[bytes] = None,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        _err(f"Network error talking to {url}: {e.reason}", exit_code=3)
        return 0, b""  # unreachable


def _api_url() -> str:
    raw = os.environ.get("ROCKIELAB_API_URL", "").rstrip("/")
    if not raw:
        _err("ROCKIELAB_API_URL is not set in the environment.")
    return raw


def _tenant_token() -> str:
    raw = os.environ.get("ROCKIELAB_TENANT_TOKEN", "")
    if not raw:
        _err("ROCKIELAB_TENANT_TOKEN is not set in the environment.")
    return raw


def _tenant_id(override: Optional[str]) -> str:
    raw = override or os.environ.get("ROCKIELAB_TENANT_ID", "")
    if not raw:
        _err(
            "ROCKIELAB_TENANT_ID is not set in the environment. "
            "Set it explicitly; tenant tokens are not tenant identity.",
        )
    return raw


def _headers(tenant_id: Optional[str] = None) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": ROCKIE_RUNTIME_USER_AGENT,
        "X-Tenant-Token": _tenant_token(),
        "X-Tenant-Id": _tenant_id(tenant_id),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="submit.py",
        description="Submit and tail a Rockie GPU job.",
    )
    p.add_argument("--gpu-type", required=True,
                   choices=["A40_48GB", "A100_80GB", "H100_SXM", "H200", "B200"])
    p.add_argument("--gpu-count", required=True, type=int)
    p.add_argument("--script-file", required=True,
                   help="Path to the bash script body to run on the pod.")
    p.add_argument("--timeout", required=True, type=int,
                   help="Job timeout in seconds (max 86400).")
    p.add_argument("--term-sheet-json", default=None,
                   help="Path to an approved budget term-sheet JSON artifact.")
    p.add_argument("--budget-cents", default=None, type=int,
                   help="Optional budget assertion in cents. Must equal term_sheet.user_budget_cents when provided.")
    p.add_argument("--tenant-id", default=None)
    p.add_argument("--region", default=None)
    p.add_argument("--tier", default=None)
    p.add_argument("--env", action="append", default=[],
                   help="Repeatable KEY=VALUE for the job env.")
    p.add_argument("--notebook-id", default=None,
                   help="Notebook id for dashboard Note linkage. Defaults to ROCKIELAB_NOTEBOOK_ID.")
    p.add_argument("--run-name", default=None,
                   help="Run name used for dashboard Note metadata. Defaults to the script filename stem.")
    p.add_argument("--software", default=None,
                   help="Software identifier for dashboard Note metadata. Defaults to script-based inference.")
    p.add_argument("--origin-skill", default="experiment",
                   help="Origin skill identifier for dashboard Note metadata.")
    p.add_argument("--monitoring-profile-id", default=None,
                   help="Optional monitoring profile id. Defaults to script/software inference with common.default.v1 fallback.")
    p.add_argument("--api-url", default=None,
                   help="Override ROCKIELAB_API_URL.")
    p.add_argument("--allow-ungated-submit", action="store_true",
                   help="Allow a submit without an approved term-sheet artifact.")
    p.add_argument("--no-poll", action="store_true",
                   help="Submit and exit immediately.")
    p.add_argument("--quiet", action="store_true",
                   help="Only print the final summary.")
    return p.parse_args(argv)


def _parse_env_pairs(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for kv in pairs:
        if "=" not in kv:
            _err(f"--env value must be KEY=VALUE, got {kv!r}.")
        k, _, v = kv.partition("=")
        if not k:
            _err(f"--env value has empty key: {kv!r}.")
        out[k] = v
    return out


def submit(args: argparse.Namespace) -> dict[str, Any]:
    if args.gpu_count < 1:
        _err("--gpu-count must be >= 1.")
    if args.timeout < 1:
        _err("--timeout must be >= 1.")
    if not os.path.isfile(args.script_file):
        _err(f"--script-file does not exist: {args.script_file}")

    with open(args.script_file, "r", encoding="utf-8") as fh:
        script = fh.read()

    api = (args.api_url or _api_url()).rstrip("/")
    tenant_id = _tenant_id(args.tenant_id)
    env = _parse_env_pairs(args.env)
    notebook_id = args.notebook_id or os.environ.get("ROCKIELAB_NOTEBOOK_ID")
    run_name = args.run_name or _default_run_name(args.script_file)

    spec: dict[str, Any] = {
        "gpu_type": args.gpu_type,
        "gpu_count": args.gpu_count,
    }
    if args.region:
        spec["region"] = args.region
    if args.tier:
        spec["tier"] = args.tier
    term_sheet = _load_term_sheet(
        term_sheet_json=args.term_sheet_json,
        allow_ungated_submit=args.allow_ungated_submit,
        budget_cents_override=args.budget_cents,
        spec=spec,
        timeout_seconds=args.timeout,
    )

    payload = {
        "tenant_id": tenant_id,
        "spec": spec,
        "script": script,
        "env": env,
        "timeout_seconds": args.timeout,
    }
    if term_sheet is not None:
        final_budget_cents = _final_budget_cents(term_sheet, args.budget_cents)
        if final_budget_cents is not None:
            payload["budget_cents"] = final_budget_cents
        payload["budget_term_sheet"] = term_sheet
    if notebook_id:
        software = infer_software(script, args.software)
        monitoring_profile_id, monitoring_profile_snapshot = resolve_profile_snapshot(
            profile_id=args.monitoring_profile_id,
            run_name=run_name,
            origin_skill=args.origin_skill,
            software=software,
            script=script,
        )
        payload["dashboard"] = {
            "notebook_id": notebook_id,
            "run_name": run_name,
            "origin_skill": args.origin_skill,
            "software": software,
            "monitoring_profile_id": monitoring_profile_id,
            "monitoring_profile_snapshot": monitoring_profile_snapshot,
        }
        if term_sheet is not None:
            payload["dashboard"]["monitoring_profile_snapshot"]["budget_term_sheet"] = term_sheet
    if term_sheet is not None:
        print(json.dumps(_budget_term_sheet_event(term_sheet), sort_keys=True), flush=True)
    code, body = _http_request(
        "POST",
        f"{api}/api/jobs/submit",
        headers=_headers(tenant_id),
        body=json.dumps(payload).encode("utf-8"),
    )
    parsed = _decode_json(body)
    if code == 402:
        print(json.dumps(_credit_gate_event(parsed), sort_keys=True), flush=True)
        _err(_credit_gate_error_message(parsed), exit_code=4)
    if code >= 400:
        _err(f"Submit failed ({code}): {_safe_summary(parsed)}", exit_code=5)
    return parsed


def _credit_gate_event(parsed: Any) -> dict[str, Any]:
    error = _api_error(parsed)
    return {
        "event": "credit_gate",
        "version": 1,
        "code": error.get("code") or "insufficient_balance",
        "balance_cents": error.get("balance_cents"),
        "needed_cents": error.get("needed_cents"),
        "message": _safe_summary(
            error.get("message")
            or "Rockie GPU credit is empty. Top up compute credit to run this experiment."
        ),
        "checkout": {
            "kind": "compute_top_up",
            "skus": ["compute:20", "compute:50", "compute:200", "compute:1000"],
        },
    }


def _credit_gate_error_message(parsed: Any) -> str:
    error = _api_error(parsed)
    message = error.get("message")
    if isinstance(message, str) and message.strip():
        return f"Insufficient GPU credit: {_safe_summary(message.strip())}"
    return f"Insufficient GPU credit: {_safe_summary(parsed)}"


def _api_error(parsed: Any) -> dict[str, Any]:
    if isinstance(parsed, dict):
        detail = parsed.get("detail")
        if isinstance(detail, dict):
            nested = detail.get("error")
            if isinstance(nested, dict):
                return nested
            return detail
        nested = parsed.get("error")
        if isinstance(nested, dict):
            return nested
    return {}


def _load_term_sheet(
    *,
    term_sheet_json: Optional[str],
    allow_ungated_submit: bool,
    budget_cents_override: Optional[int],
    spec: dict[str, Any],
    timeout_seconds: int,
) -> Optional[dict[str, Any]]:
    if not term_sheet_json:
        if allow_ungated_submit:
            return None
        _err("missing --term-sheet-json. Pass an approved term sheet or set --allow-ungated-submit.", exit_code=9)
    term_sheet = json.loads(Path(term_sheet_json).read_text(encoding="utf-8"))
    _validate_term_sheet_for_submit(
        term_sheet,
        budget_cents_override=budget_cents_override,
        spec=spec,
        timeout_seconds=timeout_seconds,
    )
    return term_sheet


def _validate_term_sheet_for_submit(
    term_sheet: dict[str, Any],
    *,
    budget_cents_override: Optional[int],
    spec: dict[str, Any],
    timeout_seconds: int,
) -> None:
    if term_sheet.get("availability") != "available":
        _err("term sheet is not available for submit.", exit_code=10)
    if term_sheet.get("status_code") != "available":
        _err("term sheet status_code is not available for submit.", exit_code=10)
    decision = term_sheet.get("decision") or {}
    decision_state = decision.get("state") or decision.get("decision")
    if decision_state not in {"approve", "modify_then_approve"}:
        _err(
            "term sheet is not approved for submit. Expected decision state approve or modify_then_approve.",
            exit_code=10,
        )
    if decision.get("approvable") is not True:
        _err("term sheet decision is not approvable.", exit_code=10)
    if decision.get("approved_for_submit") is not True:
        _err("term sheet is not approved_for_submit.", exit_code=10)

    compute = term_sheet.get("compute") or {}
    _require_equal("compute.gpu_type", compute.get("gpu_type"), spec.get("gpu_type"))
    _require_equal("compute.gpu_count", compute.get("gpu_count"), spec.get("gpu_count"))
    for required_field in ("region", "tier"):
        if compute.get(required_field) is None:
            _err(f"term sheet missing compute.{required_field}; refusing submit.", exit_code=10)
        _require_equal(
            f"compute.{required_field}",
            compute.get(required_field),
            spec.get(required_field),
        )

    wallclock_minutes = term_sheet.get("wallclock_minutes")
    if wallclock_minutes is None:
        _err("term sheet missing wallclock_minutes.", exit_code=10)
    quoted_seconds = int(float(wallclock_minutes) * 60)
    if timeout_seconds > quoted_seconds or quoted_seconds - timeout_seconds >= 60:
        _err(
            f"term sheet wallclock_minutes {wallclock_minutes} does not match timeout seconds {timeout_seconds}.",
            exit_code=10,
        )

    if term_sheet.get("user_budget_cents") is None:
        _err("term sheet missing user_budget_cents; refusing submit.", exit_code=11)
    estimate_cents = term_sheet.get("estimate_cents")
    final_budget = _final_budget_cents(term_sheet, budget_cents_override)
    if budget_cents_override is not None and int(budget_cents_override) != final_budget:
        _err(
            f"--budget-cents {budget_cents_override} must equal term_sheet.user_budget_cents {final_budget}.",
            exit_code=11,
        )
    if estimate_cents is not None and final_budget is not None and final_budget < int(estimate_cents):
        _err(
            f"budget_cents {final_budget} is below estimate_cents {estimate_cents}; refusing submit.",
            exit_code=11,
        )


def _require_equal(field: str, approved: Any, requested: Any) -> None:
    if str(approved) != str(requested):
        _err(f"term sheet {field} {approved!r} does not match submit request {requested!r}.", exit_code=10)


def _final_budget_cents(term_sheet: dict[str, Any], budget_cents_override: Optional[int]) -> Optional[int]:
    user_budget = term_sheet.get("user_budget_cents")
    if user_budget is not None:
        return int(user_budget)
    return None


def _budget_term_sheet_event(term_sheet: dict[str, Any]) -> dict[str, Any]:
    compute = dict(term_sheet.get("compute") or {})
    if compute:
        compute = {
            "display_provider": compute.get("display_provider"),
            "gpu_type": compute.get("gpu_type"),
            "gpu_count": compute.get("gpu_count"),
            "region": compute.get("region"),
            "tier": compute.get("tier"),
        }
    return {
        "event": "budget_term_sheet",
        "version": 1,
        "quote_id": term_sheet.get("quote_id"),
        "job_shape": term_sheet.get("job_shape"),
        "availability": term_sheet.get("availability"),
        "estimate_cents": term_sheet.get("estimate_cents"),
        "recommended_budget_cents": term_sheet.get("recommended_budget_cents"),
        "user_budget_cents": term_sheet.get("user_budget_cents"),
        "confidence_bucket": term_sheet.get("confidence_bucket"),
        "decision": (term_sheet.get("decision") or {}).get("state") or (term_sheet.get("decision") or {}).get("decision"),
        "compute": compute,
        "stages": list(term_sheet.get("stages") or []),
    }


def _decode_json(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body.decode("utf-8", errors="replace")


def _default_run_name(script_file: str) -> str:
    stem = Path(script_file).stem.replace("_", " ").replace("-", " ").strip()
    return stem or "experiment run"


def get_job(
    job_id: str,
    *,
    api_url: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> dict[str, Any]:
    api = (api_url or _api_url()).rstrip("/")
    code, body = _http_request(
        "GET",
        f"{api}/api/jobs/{urllib.parse.quote(job_id, safe='')}",
        headers=_headers(tenant_id),
    )
    parsed = _decode_json(body)
    if code == 404:
        _err(f"Job {job_id} not found.", exit_code=6)
    if code >= 400:
        _err(f"GET /jobs/{job_id} failed ({code}): {_safe_summary(parsed)}", exit_code=7)
    return parsed


def append_dashboard(
    note_id: str,
    *,
    job_id: str,
    api_url: Optional[str] = None,
    tenant_id: Optional[str] = None,
    status_patch: Optional[dict[str, Any]] = None,
    metrics: Optional[list[dict[str, Any]]] = None,
    events: Optional[list[dict[str, Any]]] = None,
    verdicts: Optional[list[dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    try:
        api = (api_url or _api_url()).rstrip("/")
        payload = {
            "writer": _dashboard_writer(job_id),
            "status_patch": status_patch or {},
            "metrics": metrics or [],
            "events": events or [],
            "verdicts": verdicts or [],
        }
        code, body = _dashboard_http_request(
            "POST",
            f"{api}/api/notes/{urllib.parse.quote(note_id, safe='')}/dashboard/append",
            headers=_headers(tenant_id),
            body=json.dumps(payload).encode("utf-8"),
        )
        parsed = _decode_json(body)
        if code == 0 or code >= 400:
            _warn_dashboard_failure("append", code, parsed)
            return None
        return parsed
    except Exception as exc:
        _warn_dashboard_failure("append", 0, {"error": f"dashboard_mutation_error: {exc}"})
        return None


def finalize_dashboard(
    note_id: str,
    *,
    job_id: str,
    api_url: Optional[str] = None,
    tenant_id: Optional[str] = None,
    outcome: str,
    summary: str,
    termination_outcome: Optional[str] = None,
    status_patch: Optional[dict[str, Any]] = None,
    verdict: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    try:
        api = (api_url or _api_url()).rstrip("/")
        payload: dict[str, Any] = {
            "writer": _dashboard_writer(job_id),
            "outcome": outcome,
            "summary": _safe_summary(summary),
            "status_patch": status_patch or {},
        }
        if termination_outcome:
            payload["termination_outcome"] = termination_outcome
        if verdict:
            payload["verdict"] = verdict
        code, body = _dashboard_http_request(
            "POST",
            f"{api}/api/notes/{urllib.parse.quote(note_id, safe='')}/dashboard/finalize",
            headers=_headers(tenant_id),
            body=json.dumps(payload).encode("utf-8"),
        )
        parsed = _decode_json(body)
        if code == 0 or code >= 400:
            _warn_dashboard_failure("finalize", code, parsed)
            return None
        return parsed
    except Exception as exc:
        _warn_dashboard_failure("finalize", 0, {"error": f"dashboard_mutation_error: {exc}"})
        return None


def _dashboard_writer(job_id: str) -> dict[str, str]:
    return {"job_backend": "gpu_job", "job_id": job_id}


def _dashboard_http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: Optional[bytes] = None,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read()
        except Exception as read_error:
            return 0, _dashboard_error_body(f"http_error_read_failed: {read_error}")
    except urllib.error.URLError as e:
        return 0, _dashboard_error_body(f"network_error: {e.reason}")
    except Exception as e:
        return 0, _dashboard_error_body(f"transport_error: {e}")


def _dashboard_error_body(error: str) -> bytes:
    return json.dumps({"error": error}).encode("utf-8")


def _dashboard_status_patch(job: dict[str, Any], *, final: bool = False) -> dict[str, Any]:
    monitor = normalize_monitor_envelope(job)
    patch: dict[str, Any] = {
        "job_state": job.get("state"),
        "log_state": "available" if job.get("last_log_line") else "unavailable",
        "monitor_owner": monitor["monitor_owner"],
        "monitor_target": monitor["monitor_target"],
        "state": monitor["state"],
        "utilization": monitor["utilization"],
        "spend": monitor["spend"],
        "artifacts": monitor["artifacts"],
    }
    for key in ("cost_actual_cents", "cost_so_far_cents"):
        if job.get(key) is not None:
            patch[key] = job.get(key)
    if job.get("last_log_line"):
        patch["last_log_summary"] = _safe_summary(job.get("last_log_line"))
    if job.get("started_at") is not None:
        patch["started_at"] = job.get("started_at")
    if final and job.get("finished_at") is not None:
        patch["finished_at"] = job.get("finished_at")
    return {k: v for k, v in patch.items() if v is not None}


def _heartbeat_verdict(job: dict[str, Any]) -> dict[str, Any]:
    state = job.get("state") or "UNKNOWN"
    compute_error = job.get("compute_error")
    if compute_error:
        return {
            "verdict": "warn",
            "reason_code": "compute_error_observed",
            "summary": _safe_summary(f"Job reported an execution warning while state is {state}."),
            "evidence_refs": ["job.compute_error"],
        }
    return {
        "verdict": "continue",
        "reason_code": "poll_heartbeat",
        "summary": _safe_summary(f"Job is still active with state {state}."),
        "next_heartbeat_seconds": 10,
        "evidence_refs": ["job.state"],
    }


def _final_verdict(job: dict[str, Any]) -> dict[str, Any]:
    state = job.get("state")
    if state == "DONE":
        return {
            "verdict": "continue",
            "reason_code": "job_completed",
            "summary": "Job completed successfully.",
            "evidence_refs": ["job.state"],
        }
    if state == "CANCELLED":
        return {
            "verdict": "warn",
            "reason_code": "job_cancelled",
            "summary": _termination_summary(job, default="Job was cancelled."),
            "evidence_refs": _termination_evidence_refs(job),
        }
    return {
        "verdict": "needs_human",
        "reason_code": _termination_reason_code(job, default="job_failed"),
        "summary": _termination_summary(job, default="Job failed before completion."),
        "evidence_refs": _termination_evidence_refs(job),
    }


def _dashboard_events(
    job: dict[str, Any],
    *,
    include_state: bool,
    include_log: bool,
    final: bool = False,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    state = job.get("state") or "UNKNOWN"
    if include_state:
        events.append(
            {
                "kind": "state",
                "summary": _safe_summary(f"Job state is {state}."),
                "payload": {"state": state},
            }
        )
    if include_log:
        log_line = job.get("last_log_line")
        if log_line:
            events.append(
                {
                    "kind": "log",
                    "summary": _safe_summary(log_line),
                    "payload": {"source": "job.last_log_line"},
                }
            )
        else:
            events.append(
                {
                    "kind": "log",
                    "summary": "No job log line is available yet.",
                    "state": "unavailable",
                    "reason_code": "log_not_available",
                }
            )
    if final and job.get("compute_error"):
        events.append(
            {
                "kind": "warning",
                "summary": _safe_summary(job.get("compute_error")),
                "payload": {"source": "job.compute_error"},
            }
        )
    return events


def _unavailable_telemetry_metrics() -> list[dict[str, Any]]:
    monitor = normalize_monitor_envelope({})
    return [
        {
            "name": "gpu_utilization",
            "state": monitor["utilization"]["state"],
            "reason_code": monitor["utilization"]["reason_code"],
            "summary": "GPU utilization telemetry is unavailable from the job status API.",
        }
    ]


def _finetune_progress_metrics(log_line: Optional[str]) -> list[dict[str, Any]]:
    if not isinstance(log_line, str) or not log_line.startswith("FINETUNE_PROGRESS "):
        return []
    suffix = log_line[len("FINETUNE_PROGRESS ") :].strip()
    try:
        progress = json.loads(suffix)
    except json.JSONDecodeError:
        return []
    if not isinstance(progress, dict):
        return []

    metrics: list[dict[str, Any]] = []
    step = progress.get("step")
    loss = progress.get("loss")
    if _is_finite_number(step):
        metrics.append({"name": "training.step", "value": step})
    if _is_finite_number(loss):
        metrics.append({"name": "training.loss", "value": loss})
    return metrics


def _is_finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _print_finetune_progress(log_line: Optional[str]) -> None:
    metrics = _finetune_progress_metrics(log_line)
    if not metrics:
        return
    values = {metric["name"]: metric["value"] for metric in metrics}
    parts = []
    if "training.step" in values:
        parts.append(f"step={values['training.step']}")
    if "training.loss" in values:
        parts.append(f"loss={values['training.loss']}")
    if parts:
        print(f"[finetune] {' '.join(parts)}", file=sys.stdout, flush=True)


def _final_outcome(state: str) -> str:
    if state == "DONE":
        return "success"
    if state == "CANCELLED":
        return "cancelled"
    return "failed"


def _final_summary(job: dict[str, Any]) -> str:
    state = job.get("state") or "UNKNOWN"
    parts = [f"Job reached terminal state {state}."]
    for key in ("cost_actual_cents", "cost_so_far_cents"):
        if job.get(key) is not None:
            parts.append(f"Cost reported: {_format_cost(job.get(key))}.")
            break
    if job.get("last_log_line"):
        parts.append(f"Last log: {_safe_summary(job['last_log_line'])}")
    if job.get("compute_error"):
        parts.append(f"Execution warning: {_safe_summary(job['compute_error'])}")
    return _safe_summary(" ".join(parts), limit=900)


def _termination_outcome(job: dict[str, Any]) -> str:
    state = job.get("state")
    if state == "DONE":
        return "not_attempted"
    compute_error = str(job.get("compute_error") or "").lower()
    if "timeout" in compute_error or "timed out" in compute_error:
        return "pending_cleanup"
    if "preempt" in compute_error or "evict" in compute_error or "interrupt" in compute_error:
        return "pending_cleanup"
    return "not_attempted"


def _termination_reason_code(job: dict[str, Any], *, default: str) -> str:
    text = str(job.get("compute_error") or "").lower()
    if "timeout" in text or "timed out" in text:
        return "timeout_observed"
    if "preempt" in text or "evict" in text or "interrupt" in text:
        return "preemption_observed"
    return default


def _termination_summary(job: dict[str, Any], *, default: str) -> str:
    if job.get("compute_error"):
        return _safe_summary(job.get("compute_error"))
    return default


def _termination_evidence_refs(job: dict[str, Any]) -> list[str]:
    refs = ["job.state"]
    if job.get("compute_error"):
        refs.append("job.compute_error")
    if job.get("last_log_line"):
        refs.append("job.last_log_line")
    return refs


def _warn_dashboard_failure(action: str, code: int, parsed: Any) -> None:
    print(
        f"submit.py: warning: dashboard {action} failed ({code}): {_safe_summary(parsed)}",
        file=sys.stderr,
        flush=True,
    )


def _safe_summary(value: Any, *, limit: int = DASHBOARD_LOG_SUMMARY_LIMIT) -> str:
    text = str(value) if value is not None else ""
    text = SIGNED_URL_QUERY_PATTERN.sub(rf"\1{SECRET_REDACTION}", text)
    text = re.sub(
        r"(?i)([\"']?\bauthorization[\"']?\s*[:=]\s*[\"']?bearer\s+)([^\s,;}\]\)\"']+)",
        rf"\1{SECRET_REDACTION}",
        text,
    )
    text = re.sub(
        rf"(?i)([\"']?(?:{SECRET_FIELD_PATTERN})[\"']?\s*[:=]\s*)([\"'])(.*?)(\2)",
        lambda match: f"{match.group(1)}{match.group(2)}{SECRET_REDACTION}{match.group(4)}",
        text,
    )
    text = re.sub(
        rf"(?i)([\"']?(?:{SECRET_FIELD_PATTERN})[\"']?\s*[:=]\s*)([^\s,;}}\]\)\"']+)",
        rf"\1{SECRET_REDACTION}",
        text,
    )
    text = re.sub(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{5,}\b", SECRET_REDACTION, text)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _format_cost(cents: Optional[Any]) -> str:
    if cents is None:
        return "unavailable"
    try:
        return f"${int(cents) / 100:.2f}"
    except (TypeError, ValueError):
        return "unavailable"


def poll(
    job_id: str,
    *,
    quiet: bool,
    api_url: Optional[str] = None,
    tenant_id: Optional[str] = None,
    dashboard_note_id: Optional[str] = None,
) -> int:
    """Poll the job until it reaches a terminal state. Return exit code."""
    last_state: Optional[str] = None
    last_log: Optional[str] = None
    heartbeat_sent = False
    finalized_dashboard = False
    while True:
        job = get_job(job_id, api_url=api_url, tenant_id=tenant_id)
        state = job.get("state")
        log_line = job.get("last_log_line")
        state_changed = state != last_state
        log_changed = bool(log_line and log_line != last_log)
        if state != last_state:
            if not quiet:
                print(
                    f"[{_now_str()}] state -> {state}",
                    file=sys.stdout,
                    flush=True,
                )
            last_state = state
        if log_line and log_line != last_log:
            if not quiet:
                print(f"[log] {_safe_summary(log_line)}", file=sys.stdout, flush=True)
                _print_finetune_progress(log_line)
            last_log = log_line

        if dashboard_note_id and state not in TERMINAL_STATES:
            include_heartbeat = not heartbeat_sent
            if state_changed or log_changed or include_heartbeat:
                metrics = []
                if include_heartbeat:
                    metrics.extend(_unavailable_telemetry_metrics())
                if log_changed:
                    metrics.extend(_finetune_progress_metrics(log_line))
                append_response = append_dashboard(
                    dashboard_note_id,
                    job_id=job_id,
                    api_url=api_url,
                    tenant_id=tenant_id,
                    status_patch=_dashboard_status_patch(job),
                    metrics=metrics,
                    events=_dashboard_events(
                        job,
                        include_state=state_changed or include_heartbeat,
                        include_log=log_changed or include_heartbeat,
                    ),
                    verdicts=[_heartbeat_verdict(job)] if include_heartbeat else [],
                )
                if include_heartbeat and append_response is not None:
                    heartbeat_sent = True

        if state in TERMINAL_STATES:
            cost_cents = (
                job.get("cost_actual_cents")
                if job.get("cost_actual_cents") is not None
                else job.get("cost_so_far_cents")
            )
            if dashboard_note_id and not finalized_dashboard:
                finalize_dashboard(
                    dashboard_note_id,
                    job_id=job_id,
                    api_url=api_url,
                    tenant_id=tenant_id,
                    outcome=_final_outcome(state),
                    summary=_final_summary(job),
                    termination_outcome=_termination_outcome(job),
                    status_patch=_dashboard_status_patch(job, final=True),
                    verdict=_final_verdict(job),
                )
                finalized_dashboard = True
            print(
                f"\nJob: {job.get('id')}\n"
                f"Shape: {job.get('gpu_count')}x{job.get('gpu_type')}\n"
                f"Final state: {state}\n"
                f"Cost: {_format_cost(cost_cents)}\n"
                f"Last log: {_safe_summary(log_line) if log_line else '(none)'}",
                file=sys.stdout,
                flush=True,
            )
            if state == "DONE":
                return 0
            if state == "CANCELLED":
                return 130  # SIGINT-style; caller can distinguish.
            return 1

        time.sleep(10)


def _now_str() -> str:
    return time.strftime("%H:%M:%S", time.localtime())


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    result = submit(args)
    job_id = result.get("job_id")
    if not args.quiet:
        print(
            f"Submitted job {job_id} "
            f"(estimated {_format_cost(result.get('estimated_cost_cents'))})",
            file=sys.stdout,
            flush=True,
        )
    if args.no_poll:
        return 0
    if not job_id:
        _err("Submit response missing job_id.", exit_code=8)
    return poll(
        job_id,
        quiet=args.quiet,
        api_url=args.api_url,
        tenant_id=args.tenant_id,
        dashboard_note_id=result.get("dashboard_note_id"),
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
