#!/usr/bin/env python3
"""critic_loop.py -- this agent's OWN internal adversarial critic loop.

LINEAGE: minimal copy of the proven diligence-deck/critic_loop.py engine
(2-consecutive-clean-pass gate, counter resets on any failed round,
MAX_ROUNDS cap, CRITICAL blocks PASS, subagent + single-process modes). It is
copied (not imported) because a portable agent repo must stand alone. Keep it
in sync with the canonical engine if that one changes.

A fresh, no-memory DOMAIN critic (domain-critic.md) reads only the rubric + the
draft deliverable and returns PASS or structured violations; the agent fixes
them; the loop re-runs until two consecutive zero-CRITICAL passes or the cap.

    critic_loop.py --selftest    # deterministic loop-logic proof, mocked verdicts
"""
from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

MAX_ROUNDS = 6
REQUIRED_CONSECUTIVE_PASSES = 2
CRITICAL, MAJOR, MINOR = "critical", "major", "minor"
_SEVERITIES = {CRITICAL, MAJOR, MINOR}


class CriticVerdictError(ValueError):
    pass


def validate_verdict(raw: dict) -> dict:
    """Re-derive pass from zero-CRITICALs; never trust the critic's self-report."""
    if not isinstance(raw, dict):
        raise CriticVerdictError("verdict must be an object")
    if "violations" not in raw or not isinstance(raw["violations"], list):
        raise CriticVerdictError("verdict.violations must be a list")
    cleaned = []
    for i, v in enumerate(raw["violations"]):
        if not isinstance(v, dict):
            raise CriticVerdictError(f"violations[{i}] must be an object")
        for k in ("check", "severity", "where", "fix"):
            if k not in v:
                raise CriticVerdictError(f"violations[{i}] missing '{k}'")
        sev = str(v["severity"]).lower()
        if sev not in _SEVERITIES:
            raise CriticVerdictError(f"violations[{i}].severity invalid")
        cleaned.append({"check": v["check"], "severity": sev,
                        "where": v["where"], "fix": v["fix"]})
    derived = not any(v["severity"] == CRITICAL for v in cleaned)
    return {"pass": derived, "violations": cleaned}


@dataclass
class RoundRecord:
    round: int
    passed: bool
    n_critical: int
    consecutive_passes_after: int


@dataclass
class LoopResult:
    converged: bool
    rounds_run: int
    mode: str
    rounds: list = field(default_factory=list)
    reason: str = ""

    def to_dict(self):
        return asdict(self)


Critic = Callable[[dict, int], dict]


def run_critic_loop(draft_path, critic, *, mode="single", fixer=None,
                    max_rounds=MAX_ROUNDS,
                    required_consecutive=REQUIRED_CONSECUTIVE_PASSES,
                    verbose=True) -> LoopResult:
    if mode not in ("subagent", "single"):
        raise ValueError("mode must be 'subagent' or 'single'")
    ctx = {"draft_path": draft_path, "mode": mode,
           "isolation_label": "single-process" if mode == "single" else "subagent"}
    result = LoopResult(converged=False, rounds_run=0, mode=mode)
    consecutive = 0
    for rnd in range(1, max_rounds + 1):
        verdict = validate_verdict(critic(ctx, rnd))
        ncrit = sum(1 for v in verdict["violations"] if v["severity"] == CRITICAL)
        passed = verdict["pass"]
        consecutive = consecutive + 1 if passed else 0  # RESET ON FAIL
        result.rounds.append(RoundRecord(rnd, passed, ncrit, consecutive))
        result.rounds_run = rnd
        if verbose:
            print(f"[round {rnd}] {'PASS' if passed else 'FAIL'} "
                  f"criticals={ncrit} consecutive={consecutive}/{required_consecutive}")
        if consecutive >= required_consecutive:
            result.converged = True
            result.reason = f"converged: {required_consecutive} clean rounds at {rnd}"
            return result
        if verdict["violations"] and fixer:
            fixer(ctx, verdict["violations"])
    result.reason = f"NOT CONVERGED: hit MAX_ROUNDS={max_rounds}"
    if verbose:
        print(result.reason, file=sys.stderr)
    return result


def make_subagent_critic(dispatch):
    return lambda ctx, rnd: dispatch(ctx, rnd)


def make_single_process_critic(reason_pass):
    return lambda ctx, rnd: reason_pass(dict(ctx, isolation_label="single-process"), rnd)


def _selftest() -> int:
    fails = []

    def chk(name, cond):
        print(f"  [{'ok' if cond else 'FAIL'}] {name}")
        if not cond:
            fails.append(name)

    clean = {"pass": True, "violations": []}
    crit = {"pass": False, "violations": [{"check": "goal", "severity": "critical",
            "where": "x", "fix": "y"}]}
    scripted = lambda seq: (lambda ctx, rnd: seq[rnd - 1])

    r = run_critic_loop("d", scripted([clean, crit, clean, clean]),
                        mode="single", verbose=False)
    chk("FAIL resets counter -> converges at round 4", r.converged and r.rounds_run == 4)
    r2 = run_critic_loop("d", scripted([clean, clean]), mode="single", verbose=False)
    chk("two clean rounds converge at 2", r2.converged and r2.rounds_run == 2)
    r3 = run_critic_loop("d", scripted([clean, crit] * MAX_ROUNDS),
                         mode="single", verbose=False)
    chk("cap fails loudly", (not r3.converged) and r3.rounds_run == MAX_ROUNDS)
    lying = {"pass": True, "violations": [crit["violations"][0]]}
    chk("CRITICAL blocks claimed pass", validate_verdict(lying)["pass"] is False)
    print("ALL SELFTESTS PASSED" if not fails else f"FAILURES: {fails}")
    return 0 if not fails else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return _selftest()
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
