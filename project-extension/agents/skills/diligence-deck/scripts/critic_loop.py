#!/usr/bin/env python3
"""critic_loop.py -- the A3 adversarial senior-partner critic LOOP.

This is the diligence agent's OWN internal adversarial flow: a fresh,
no-research-history "senior partner at a big-three firm" critic reads ONLY the
rubric + the draft findings.json (and optionally the rendered deck), returns
PASS or a structured violation list, the builder fixes the violations, and the
loop re-runs until the agent PASSES TWICE CONSECUTIVELY with zero high-severity
(CRITICAL) violations. The clean-pass counter RESETS TO ZERO on any failed
round. The loop is capped at MAX_ROUNDS and FAILS LOUDLY if it does not
converge. Mirrors the paper skill's detector-gate twice-clean gate.

This is NOT the meta agent-builder's publish gate (slice B1). This loop hardens
ONE diligence run before its deck reaches the IC; B1 is a separate gate that
decides whether the *skill itself* is fit to ship. Same shape, different scope.

The critic VERDICT schema (one round):

    {
      "pass": bool,                 # true iff zero CRITICAL violations
      "violations": [
        {
          "check":    str,          # rubric check id, e.g. "verbatim_citation"
          "severity": "critical" | "major" | "minor",
          "where":    str,          # "section 4, key_fact[1]"
          "fix":      str           # concrete bounded action
        }
      ]
    }

A `pass: true` verdict MUST carry zero `critical` violations (we re-derive and
enforce this rather than trusting the critic's self-report). MAJOR/MINOR may be
listed on a passing round as polish; they do not flip pass, but the builder is
expected to clear them before the next round.

EXECUTION MODES (both supported -- load-bearing):

  * subagent  -- dispatch a FRESH no-memory subagent per round, handed only
                 prompts/partner-critic.md + references/partner-critic-rubric.md
                 + the draft findings.json (+ reconcile.json/manifest.json to
                 check against). This is preferred: a separate context cannot be
                 coached and cannot remember what it waved through last round.

  * single    -- the DEGRADED single-process fallback. The diligence agent may
                 ITSELF be running as a subagent with NO ability to spawn nested
                 subagents (a hard runtime constraint on this fleet). In that
                 case the loop runs each round as a separate fresh REASONING
                 PASS in-process, with a HARD CONTEXT RESET between rounds: the
                 pass is handed ONLY the draft + the prompt + the rubric, and
                 carries NO memory of any prior verdict. This is WEAKER than the
                 subagent path (a single context can leak a tell it already
                 waved through), so verdicts are labelled `single-process` and
                 read accordingly. The termination rules -- CRITICAL blocks
                 PASS, two consecutive clean rounds to converge, MAX_ROUNDS cap
                 -- are IDENTICAL either way.

This module owns ONLY the loop LOGIC and the mode plumbing. The actual LLM
critic runs live at A6; here the critic is injected as a callable so the loop
logic is provable deterministically with mocked verdicts (see __main__ /
the skill's "Proven execution" block).

Usage (real run, single-process fallback -- no nested subagents needed):
    from critic_loop import run_critic_loop, make_single_process_critic
    result = run_critic_loop(
        draft_path="findings.json",
        critic=make_single_process_critic(reason_pass),  # reason_pass = your LLM call
        mode="single",
    )

CLI self-test (deterministic, mocked verdicts -- proves the loop logic):
    critic_loop.py --selftest
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

# Mirror the paper skill's detector-gate / gauntlet cap. Two consecutive clean
# rounds to converge; never run a (MAX_ROUNDS + 1)th round.
MAX_ROUNDS = 6
REQUIRED_CONSECUTIVE_PASSES = 2

CRITICAL = "critical"
MAJOR = "major"
MINOR = "minor"
_SEVERITIES = {CRITICAL, MAJOR, MINOR}


# --------------------------------------------------------------------------- #
# Verdict schema + validation
# --------------------------------------------------------------------------- #

class CriticVerdictError(ValueError):
    """A critic returned a verdict that does not match the schema."""


def validate_verdict(raw: dict) -> dict:
    """Validate a single critic verdict against the schema and RE-DERIVE pass.

    We do not trust the critic's self-reported `pass`. A verdict passes iff it
    carries zero CRITICAL violations; we recompute that here so a critic that
    says pass:true while listing a CRITICAL cannot slip through.
    """
    if not isinstance(raw, dict):
        raise CriticVerdictError(f"verdict must be an object, got {type(raw).__name__}")
    if "violations" not in raw or not isinstance(raw["violations"], list):
        raise CriticVerdictError("verdict.violations must be a list")

    cleaned = []
    for i, v in enumerate(raw["violations"]):
        if not isinstance(v, dict):
            raise CriticVerdictError(f"violations[{i}] must be an object")
        for k in ("check", "severity", "where", "fix"):
            if k not in v:
                raise CriticVerdictError(f"violations[{i}] missing required key '{k}'")
        sev = str(v["severity"]).lower()
        if sev not in _SEVERITIES:
            raise CriticVerdictError(
                f"violations[{i}].severity '{v['severity']}' not in {sorted(_SEVERITIES)}"
            )
        cleaned.append(
            {"check": v["check"], "severity": sev, "where": v["where"], "fix": v["fix"]}
        )

    criticals = [v for v in cleaned if v["severity"] == CRITICAL]
    derived_pass = len(criticals) == 0  # high-severity violation blocks PASS

    return {"pass": derived_pass, "violations": cleaned}


# --------------------------------------------------------------------------- #
# Round + loop result records
# --------------------------------------------------------------------------- #

@dataclass
class RoundRecord:
    round: int
    verdict: dict
    passed: bool
    n_critical: int
    n_major: int
    consecutive_passes_after: int  # the counter AFTER applying this round


@dataclass
class LoopResult:
    converged: bool
    rounds_run: int
    mode: str
    rounds: list = field(default_factory=list)  # list[RoundRecord]
    reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #

# A critic is any callable: (context: dict, round_index: int) -> raw verdict dict.
# The loop validates and re-derives pass from whatever it returns. `context`
# carries the draft path + optional reconcile/manifest/deck paths.
Critic = Callable[[dict, int], dict]

# A fixer is called after a FAILED or unclean round to apply the violations'
# `fix` actions to the draft. It returns nothing; it mutates the draft on disk.
# In a live run this is the builder agent re-editing findings.json. In the
# self-test it is a no-op (the mocked critic already encodes the post-fix
# verdict for each round).
Fixer = Callable[[dict, list], None]


def _noop_fixer(context: dict, violations: list) -> None:
    return None


def run_critic_loop(
    draft_path: str,
    critic: Critic,
    *,
    mode: str = "single",
    fixer: Optional[Fixer] = None,
    reconcile_path: Optional[str] = None,
    manifest_path: Optional[str] = None,
    deck_path: Optional[str] = None,
    max_rounds: int = MAX_ROUNDS,
    required_consecutive: int = REQUIRED_CONSECUTIVE_PASSES,
    verbose: bool = True,
) -> LoopResult:
    """Run the adversarial critic loop to convergence or the cap.

    Termination (mirrors the paper detector-gate):
      * Each round runs the critic on the CURRENT draft, increments the round
        counter, and validates the verdict (pass re-derived from zero CRITICALs).
      * A FAILED round (any CRITICAL) RESETS the consecutive-pass counter to 0,
        applies the fixer, and loops.
      * A PASSED round increments the counter. ONE pass does NOT converge --
        only when the counter reaches `required_consecutive` (default 2) does
        the loop converge.
      * If the round counter reaches `max_rounds` without converging, the loop
        STOPS and FAILS LOUDLY (converged=False) -- it never runs an extra round
        and never silently accepts.

    Returns a LoopResult; the caller decides whether to raise on non-convergence
    (the CLI self-test does).
    """
    if mode not in ("subagent", "single"):
        raise ValueError(f"mode must be 'subagent' or 'single', got {mode!r}")
    fixer = fixer or _noop_fixer
    context = {
        "draft_path": draft_path,
        "reconcile_path": reconcile_path,
        "manifest_path": manifest_path,
        "deck_path": deck_path,
        "mode": mode,
        # Single-process verdicts are weaker isolation; label them so a reviewer
        # of the run knows the freshness was self-imposed, not process-enforced.
        "isolation_label": "single-process" if mode == "single" else "subagent",
    }

    result = LoopResult(converged=False, rounds_run=0, mode=mode)
    consecutive = 0

    for rnd in range(1, max_rounds + 1):
        raw = critic(context, rnd)
        verdict = validate_verdict(raw)
        criticals = [v for v in verdict["violations"] if v["severity"] == CRITICAL]
        majors = [v for v in verdict["violations"] if v["severity"] == MAJOR]
        passed = verdict["pass"]

        if passed:
            consecutive += 1
        else:
            consecutive = 0  # RESET ON FAIL -- the load-bearing reset

        result.rounds.append(
            RoundRecord(
                round=rnd,
                verdict=verdict,
                passed=passed,
                n_critical=len(criticals),
                n_major=len(majors),
                consecutive_passes_after=consecutive,
            )
        )
        result.rounds_run = rnd

        if verbose:
            tag = "PASS" if passed else "FAIL"
            print(
                f"[round {rnd}] {tag}  criticals={len(criticals)} majors={len(majors)} "
                f"consecutive_passes={consecutive}/{required_consecutive} "
                f"({context['isolation_label']})"
            )

        if consecutive >= required_consecutive:
            result.converged = True
            result.reason = (
                f"converged: {required_consecutive} consecutive clean rounds "
                f"at round {rnd}"
            )
            if verbose:
                print(f"[converged] {result.reason}")
            return result

        # Not converged yet -> builder fixes the cited violations, then re-run.
        # (Even on a single PASS we keep iterating; a lone pass never unlocks.)
        if verdict["violations"]:
            fixer(context, verdict["violations"])

    # Fell out of the loop without converging: cap hit. Fail loudly.
    result.converged = False
    result.reason = (
        f"NOT CONVERGED: hit MAX_ROUNDS={max_rounds} without "
        f"{required_consecutive} consecutive clean rounds"
    )
    if verbose:
        print(f"[cap-hit] {result.reason}", file=sys.stderr)
    return result


# --------------------------------------------------------------------------- #
# Critic factories for the two execution modes
# --------------------------------------------------------------------------- #
# Both return a Critic callable. They differ ONLY in isolation mechanism; the
# loop logic above is identical for both. The live LLM call is injected so this
# file stays runtime-agnostic and deterministically testable.

def make_subagent_critic(dispatch: Callable[[dict, int], dict]) -> Critic:
    """Critic that dispatches a FRESH no-memory subagent per round.

    `dispatch(context, round_index) -> raw verdict dict` is the runtime's
    Agent/Task call. It MUST hand the subagent ONLY:
      - prompts/partner-critic.md (instructions),
      - references/partner-critic-rubric.md (the bar),
      - the draft findings.json at context['draft_path'],
      - reconcile.json / manifest.json (to check citations + reconcile coverage),
    and NOT the research history or any prior round's verdict. Each round is a
    new subagent, so freshness is process-enforced.
    """
    def _critic(context: dict, rnd: int) -> dict:
        return dispatch(context, rnd)
    return _critic


def make_single_process_critic(reason_pass: Callable[[dict, int], dict]) -> Critic:
    """Critic for the DEGRADED single-process fallback (no nested subagents).

    `reason_pass(context, round_index) -> raw verdict dict` is a fresh REASONING
    PASS the diligence agent runs IN ITS OWN PROCESS, with a HARD CONTEXT RESET:
    it must read ONLY the draft + prompts/partner-critic.md +
    references/partner-critic-rubric.md for this pass and carry NO memory of any
    prior round's verdict. The freshness is self-imposed (amnesia), which is why
    this path is weaker than make_subagent_critic and its verdicts are labelled
    single-process. The loop's termination rules are unchanged.

    This is the path that works when the diligence agent is ITSELF a subagent
    that cannot spawn nested subagents -- the load-bearing fallback.
    """
    def _critic(context: dict, rnd: int) -> dict:
        # The hard reset is the caller's contract; we surface the label so the
        # reason_pass implementation (and any audit) knows to treat it as such.
        ctx = dict(context, isolation_label="single-process")
        return reason_pass(ctx, rnd)
    return _critic


# --------------------------------------------------------------------------- #
# Deterministic self-test (mocked verdicts -- proves the LOOP LOGIC)
# --------------------------------------------------------------------------- #
# The live critic is an LLM and lands at A6. Here we drive the loop with a
# scripted sequence of verdicts to prove the four loop-logic properties without
# any model call:
#   (a) a FAIL resets the consecutive-pass counter,
#   (b) a single PASS does NOT unlock -- two consecutive PASSes are required,
#   (c) the MAX_ROUNDS cap fails loudly,
#   (d) a high-severity (CRITICAL) violation blocks PASS even if pass:true
#       was claimed.

def _scripted_critic(verdicts: list) -> Critic:
    """A mock critic that returns verdicts[round-1], for deterministic tests."""
    def _critic(context: dict, rnd: int) -> dict:
        return verdicts[rnd - 1]
    return _critic


def _crit(check="verbatim_citation", where="section 4, key_fact[1]"):
    return {"check": check, "severity": "critical", "where": where,
            "fix": "re-quote the exact source string"}


def _maj(check="declarative_title", where="section 2 headline"):
    return {"check": check, "severity": "major", "where": where,
            "fix": "rewrite as a declarative claim with the number"}


def _clean():
    return {"pass": True, "violations": []}


def _fail_critical():
    return {"pass": False, "violations": [_crit()]}


def _selftest() -> int:
    failures = []

    def check(name, cond, detail=""):
        status = "ok" if cond else "FAIL"
        print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    # (a) FAIL resets the counter: PASS, FAIL, PASS, PASS -> converges at round 4,
    #     NOT round 3 (the FAIL at round 2 wiped the round-1 pass).
    print("\n(a) FAIL resets the consecutive-pass counter")
    seq = [_clean(), _fail_critical(), _clean(), _clean()]
    r = run_critic_loop("x.json", _scripted_critic(seq), mode="single", verbose=True)
    check("converges", r.converged)
    check("converges at round 4 (not 3)", r.rounds_run == 4, f"ran {r.rounds_run}")
    check("counter reset to 0 after the round-2 FAIL",
          r.rounds[1].consecutive_passes_after == 0)
    check("round-3 pass is only the 1st consecutive (reset worked)",
          r.rounds[2].consecutive_passes_after == 1)

    # (b) a single PASS does NOT unlock: one clean round then a FAIL must not
    #     have converged at round 1.
    print("\n(b) a single PASS does NOT unlock -- needs TWO consecutive")
    seq = [_clean(), _fail_critical(), _clean(), _clean()]
    r = run_critic_loop("x.json", _scripted_critic(seq), mode="single", verbose=False)
    check("round-1 single pass did NOT converge",
          r.rounds[0].consecutive_passes_after == 1 and r.rounds_run > 1)
    # And the minimal converging case is exactly two in a row from the start:
    r2 = run_critic_loop("x.json", _scripted_critic([_clean(), _clean()]),
                         mode="single", verbose=False)
    check("two consecutive clean rounds converge at round 2",
          r2.converged and r2.rounds_run == 2)

    # (c) MAX_ROUNDS cap fails loudly: never two-in-a-row within the cap.
    print("\n(c) MAX_ROUNDS cap fails loudly")
    seq = [_clean(), _fail_critical()] * MAX_ROUNDS  # always a fail between passes
    r = run_critic_loop("x.json", _scripted_critic(seq), mode="single", verbose=False)
    check("did NOT converge", not r.converged)
    check(f"ran exactly MAX_ROUNDS={MAX_ROUNDS}", r.rounds_run == MAX_ROUNDS,
          f"ran {r.rounds_run}")
    check("reason names the cap", "MAX_ROUNDS" in r.reason)

    # (d) a CRITICAL blocks PASS even when the critic claims pass:true.
    print("\n(d) high-severity (CRITICAL) violation blocks PASS")
    lying = {"pass": True, "violations": [_crit(), _maj()]}  # claims pass w/ a CRITICAL
    v = validate_verdict(lying)
    check("re-derived pass is False despite pass:true claim", v["pass"] is False)
    # And in a loop: a CRITICAL round can never count toward convergence.
    seq = [lying, lying, lying, lying, lying, lying]
    r = run_critic_loop("x.json", _scripted_critic(seq), mode="single", verbose=False)
    check("loop with a standing CRITICAL never converges", not r.converged)
    check("no round counted as a pass",
          all(not rr.passed for rr in r.rounds))

    # Mode plumbing: subagent and single-process drive identical loop logic.
    print("\n(e) both execution modes drive identical loop logic")
    disp = make_subagent_critic(lambda ctx, rnd: _clean())
    rp = make_single_process_critic(lambda ctx, rnd: _clean())
    ra = run_critic_loop("x.json", disp, mode="subagent", verbose=False)
    rb = run_critic_loop("x.json", rp, mode="single", verbose=False)
    check("subagent mode converges in 2", ra.converged and ra.rounds_run == 2)
    check("single mode converges in 2", rb.converged and rb.rounds_run == 2)
    check("single mode labels isolation single-process", rb.mode == "single")

    print("\n" + ("ALL SELFTESTS PASSED" if not failures
                  else f"SELFTEST FAILURES: {failures}"))
    return 0 if not failures else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--selftest", action="store_true",
                   help="run the deterministic mocked-verdict loop-logic test")
    args = p.parse_args(argv)
    if args.selftest:
        return _selftest()
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
