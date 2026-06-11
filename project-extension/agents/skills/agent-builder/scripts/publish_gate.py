#!/usr/bin/env python3
"""publish_gate.py -- the agent-builder PUBLISH GATE (slice B1).

Generalizes diligence-deck/critic_loop.py from "harden one deck" to "decide
whether a BUILT AGENT is fit to ship". The proven loop engine is IMPORTED, not
re-implemented (DRY -- single source of truth for the 2-consecutive-clean gate,
counter-reset-on-fail, MAX_ROUNDS cap, CRITICAL-blocks-PASS, and the subagent /
single-process modes). This file adds only the PUBLISH semantics on top:

  DOUBLE-LAYERED ADVERSARY
  ------------------------
  * quality sublayer (ALWAYS runs): does the agent achieve its declared goal?
    Are its tools/prompts/policies sound? Is its reasoning trace defensible?
    Prompt: prompts/quality-critic.md.
  * ai-detection sublayer (OPT-IN): only runs when the declarer cares that the
    output reads as human-authored. SKIPPED when output is understood to be
    AI-generated. Prompt: prompts/ai-detection-critic.md.

  Each round runs every ENABLED sublayer as a fresh, no-memory critic. The round
  PASSES iff every enabled sublayer returns zero CRITICALs. The pass-twice gate,
  the reset-on-any-fail, and the cap are inherited verbatim from the canonical
  engine -- a round that fails ANY sublayer fails the round and resets the
  consecutive-pass counter to zero.

  PUBLISH CONTRACT
  ----------------
  IF the agent has not passed the gauntlet TWICE with zero CRITICALs, the gate
  REFUSES to mark it publishable. The verdict is emitted as JSON:
    {publishable, converged, rounds, consecutive_clean, sublayers_run, reason}

The live LLM critics land later; here the sublayer critics are injected as
callables so the gate logic is provable deterministically with mocked verdicts.

    publish_gate.py --selftest                     # deterministic loop-logic proof
    publish_gate.py --agent <repo> --out verdict.json   # (wiring; needs live critics)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# DRY: import the proven loop engine from the sibling diligence-deck skill rather
# than copy it. The skills live side-by-side under skills/; resolve that path
# relative to THIS file (no hardcoded absolute paths).
_HERE = os.path.dirname(os.path.abspath(__file__))
_DD_SCRIPTS = os.path.normpath(
    os.path.join(_HERE, "..", "..", "diligence-deck", "scripts")
)
if _DD_SCRIPTS not in sys.path:
    sys.path.insert(0, _DD_SCRIPTS)

try:
    from critic_loop import (  # noqa: E402  (path-dependent import, intentional)
        run_critic_loop,
        validate_verdict,
        make_subagent_critic,
        make_single_process_critic,
        CRITICAL,
        MAX_ROUNDS,
        REQUIRED_CONSECUTIVE_PASSES,
    )
except ImportError as e:  # pragma: no cover - surfaced loudly, never silent
    sys.stderr.write(
        "publish_gate: could not import the canonical critic_loop engine from "
        f"{_DD_SCRIPTS!r}.\nThe agent-builder publish gate REUSES diligence-deck's "
        "engine (DRY); ensure the diligence-deck skill is present alongside "
        f"agent-builder.\nunderlying error: {e}\n"
    )
    raise

# The two adversary sublayers. quality is always on; ai_detection is opt-in.
QUALITY = "quality"
AI_DETECTION = "ai_detection"


# --------------------------------------------------------------------------- #
# Sublayer composition: turn N enabled sublayer critics into ONE round critic
# --------------------------------------------------------------------------- #

def compose_round_critic(sublayer_critics: dict):
    """Return a single Critic that, per round, runs every enabled sublayer and
    MERGES their violations. A round passes iff EVERY sublayer is CRITICAL-clean.

    `sublayer_critics`: {name -> critic_callable(context, round) -> raw verdict}.
    Each sublayer's verdict is validated/re-derived by the canonical engine's
    validate_verdict; we then tag each violation with its sublayer and merge.
    The merged verdict's `pass` is re-derived from the union of CRITICALs by the
    engine, so a CRITICAL in ANY sublayer fails the round.
    """
    if not sublayer_critics:
        raise ValueError("at least the quality sublayer must be enabled")

    def _critic(context: dict, rnd: int) -> dict:
        merged = []
        for name, sub in sublayer_critics.items():
            v = validate_verdict(sub(context, rnd))
            for viol in v["violations"]:
                merged.append(dict(viol, sublayer=name))
        # Return a raw verdict; the engine re-derives pass from CRITICAL count.
        return {"violations": merged}

    return _critic


# --------------------------------------------------------------------------- #
# The publish gate
# --------------------------------------------------------------------------- #

def run_publish_gate(
    agent_path: str,
    quality_critic,
    *,
    ai_detection_critic=None,
    mode: str = "single",
    max_rounds: int = MAX_ROUNDS,
    required_consecutive: int = REQUIRED_CONSECUTIVE_PASSES,
    verbose: bool = True,
) -> dict:
    """Drive the double-layered gauntlet over a built agent and return a verdict.

    quality_critic is REQUIRED (always-on sublayer). ai_detection_critic is
    optional (opt-in sublayer). Both are Critic callables of (context, round) ->
    raw verdict dict; the round critic composes them. The 2-pass gate / reset /
    cap come from the imported run_critic_loop.
    """
    sublayers = {QUALITY: quality_critic}
    if ai_detection_critic is not None:
        sublayers[AI_DETECTION] = ai_detection_critic
    round_critic = compose_round_critic(sublayers)

    result = run_critic_loop(
        draft_path=agent_path,
        critic=round_critic,
        mode=mode,
        max_rounds=max_rounds,
        required_consecutive=required_consecutive,
        verbose=verbose,
    )

    consecutive_clean = (
        result.rounds[-1].consecutive_passes_after if result.rounds else 0
    )
    verdict = {
        "publishable": bool(result.converged),  # ONLY publishable on convergence
        "converged": result.converged,
        "rounds": result.rounds_run,
        "consecutive_clean": consecutive_clean,
        "required_consecutive": required_consecutive,
        "sublayers_run": sorted(sublayers.keys()),
        "mode": result.mode,
        "isolation": "single-process" if mode == "single" else "subagent",
        "reason": result.reason,
        "round_log": [
            {
                "round": r.round,
                "passed": r.passed,
                "n_critical": r.n_critical,
                "consecutive_passes_after": r.consecutive_passes_after,
            }
            for r in result.rounds
        ],
    }
    return verdict


# --------------------------------------------------------------------------- #
# Deterministic selftest (mocked sublayer verdicts -- proves the gate logic)
# --------------------------------------------------------------------------- #

def _clean():
    return {"pass": True, "violations": []}


def _crit(check="goal_achievement"):
    return {"pass": False, "violations": [
        {"check": check, "severity": "critical",
         "where": "agent goal", "fix": "make the agent actually achieve its goal"}]}


def _scripted(seq):
    return lambda ctx, rnd: seq[rnd - 1]


def _selftest() -> int:
    failures = []

    def chk(name, cond, detail=""):
        print(f"  [{'ok' if cond else 'FAIL'}] {name}"
              + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    # (1) quality-only, pass/pass -> publishable, converged at round 2.
    print("\n(1) quality-only: PASS, PASS -> publishable at round 2")
    v = run_publish_gate("agent", _scripted([_clean(), _clean()]),
                         mode="single", verbose=False)
    chk("publishable", v["publishable"])
    chk("converged at round 2", v["rounds"] == 2, f"ran {v['rounds']}")
    chk("only quality sublayer ran", v["sublayers_run"] == [QUALITY])

    # (2) reset-on-fail: PASS, FAIL, PASS, PASS -> converges at round 4, not 3.
    print("\n(2) reset-on-fail: PASS, FAIL, PASS, PASS -> round 4")
    v = run_publish_gate("agent",
                         _scripted([_clean(), _crit(), _clean(), _clean()]),
                         mode="single", verbose=False)
    chk("publishable", v["publishable"])
    chk("converged at round 4 (counter reset by the fail)", v["rounds"] == 4,
        f"ran {v['rounds']}")

    # (3) a CRITICAL in the AI-DETECTION sublayer alone fails the round, even
    #     when quality is clean -- proves both sublayers gate the round.
    print("\n(3) ai-detection CRITICAL fails the round even when quality is clean")
    q = _scripted([_clean(), _clean(), _clean(), _clean()])
    # ai-detection: FAIL the first round, then clean -> converges at round 3.
    ai = _scripted([_crit("reads_ai_generated"), _clean(), _clean(), _clean()])
    v = run_publish_gate("agent", q, ai_detection_critic=ai,
                         mode="single", verbose=False)
    chk("both sublayers ran", v["sublayers_run"] == sorted([QUALITY, AI_DETECTION]))
    chk("round-1 failed (ai-detection CRITICAL) -> not converged at 2",
        v["round_log"][0]["passed"] is False)
    chk("converges at round 3", v["rounds"] == 3, f"ran {v['rounds']}")

    # (4) opt-in respected: ai-detection NOT run unless a critic is supplied.
    print("\n(4) ai-detection is OPT-IN -- skipped when no critic supplied")
    v = run_publish_gate("agent", _scripted([_clean(), _clean()]),
                         ai_detection_critic=None, mode="single", verbose=False)
    chk("ai_detection NOT in sublayers_run", AI_DETECTION not in v["sublayers_run"])

    # (5) refusal: never two-in-a-row within the cap -> NOT publishable, fails loud.
    print("\n(5) never converges -> REFUSES to publish, hits the cap")
    v = run_publish_gate("agent",
                         _scripted([_clean(), _crit()] * MAX_ROUNDS),
                         mode="single", verbose=False)
    chk("NOT publishable", not v["publishable"])
    chk("ran exactly MAX_ROUNDS", v["rounds"] == MAX_ROUNDS, f"ran {v['rounds']}")
    chk("reason names the cap", "MAX_ROUNDS" in v["reason"])

    # (6) a single PASS does NOT unlock publish.
    print("\n(6) one PASS does NOT unlock publish")
    v = run_publish_gate("agent", _scripted([_clean(), _crit(), _crit(),
                                             _crit(), _crit(), _crit()]),
                         mode="single", verbose=False)
    chk("one pass then fails -> NOT publishable", not v["publishable"])

    # (7) both execution modes drive identical gate logic.
    print("\n(7) subagent + single modes drive identical gate logic")
    va = run_publish_gate("agent", make_subagent_critic(lambda c, r: _clean()),
                          mode="subagent", verbose=False)
    vb = run_publish_gate("agent", make_single_process_critic(lambda c, r: _clean()),
                          mode="single", verbose=False)
    chk("subagent mode publishable in 2", va["publishable"] and va["rounds"] == 2)
    chk("single mode publishable in 2", vb["publishable"] and vb["rounds"] == 2)
    chk("single mode labels isolation single-process",
        vb["isolation"] == "single-process")

    print("\n" + ("ALL SELFTESTS PASSED" if not failures
                  else f"SELFTEST FAILURES: {failures}"))
    return 0 if not failures else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--selftest", action="store_true",
                   help="run the deterministic mocked-verdict gate-logic test")
    p.add_argument("--agent", help="path to a scaffolded agent repo to gate")
    p.add_argument("--ai-detection", action="store_true",
                   help="enable the opt-in AI-content-detection sublayer")
    p.add_argument("--mode", choices=("subagent", "single"), default="single")
    p.add_argument("--out", help="write the verdict JSON here (default: stdout)")
    args = p.parse_args(argv)

    if args.selftest:
        return _selftest()

    if not args.agent:
        p.print_help()
        return 0

    # Live-critic wiring: the orchestrator (builder.md) injects the real LLM
    # quality/ai-detection critics. Without them this CLI cannot grade an agent;
    # fail loudly rather than emit a fake verdict.
    sys.stderr.write(
        "publish_gate: live LLM sublayer critics are not wired into this CLI.\n"
        "Drive run_publish_gate() from the builder orchestrator, injecting the\n"
        "quality critic (always) and the ai-detection critic (when --ai-detection),\n"
        "each as a fresh no-memory subagent reading the matching prompt in prompts/.\n"
        "Run `publish_gate.py --selftest` to prove the gate LOGIC deterministically.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
