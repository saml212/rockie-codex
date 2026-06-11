#!/usr/bin/env python3
"""scaffold_agent.py -- turn a declared goal into a PORTABLE agent repo.

Input: an agent.config.json (the LangGraph-Assistants config-over-code split --
a stable harness + a versioned per-customer config) OR equivalent CLI flags.
Output: a complete, portable git-ready agent repo on the OpenClaw/Claude-Code
harness:

    <target>/
      CLAUDE.md              the agent's constitution + its declared goal
      agent.config.json      the versioned per-customer config (goal/model/tools/policies)
      manifest.json          agent manifest (name, version, capabilities, provenance)
      .mcp.json              MCP / tool wiring
      .claude/skills/        the agent's own skills (incl. self-adversarial critic by default)
      .claude/hooks/         the agent's safeguards (a real pre-action policy gate)
      policies/              declared policy statements (one .md per policy)
      tests/                 proven-execution tests for the built agent

BIASED TO EMIT SELF-ADVERSARIAL AGENTS: by default the scaffold wires the
generated agent with its OWN internal critic loop (a copy of the gauntlet engine
+ a domain critic prompt), exactly like Atlas's partner-critic. Pass
--no-self-adversarial to opt out.

stdlib-only. No network, no pip, no hardcoded absolute paths.

    scaffold_agent.py --goal "..." --out ./my-agent [flags]
    scaffold_agent.py --config agent.config.json --out ./my-agent
    scaffold_agent.py --selftest        # scaffold into a temp dir + assert structure
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0"
# Per spec clarification #2: a generated agent runs as a 2nd Fly machine of
# kind=agent. The deep-research foundation is GPT-Researcher. These are the
# scaffold defaults; the declarer overrides via config.
DEFAULT_RUNTIME = "fly-machine:kind=agent"
DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_RESEARCH_FOUNDATION = "gpt-researcher"


# --------------------------------------------------------------------------- #
# Config resolution (config-over-code: one stable shape, versioned per agent)
# --------------------------------------------------------------------------- #

def default_config(goal: str) -> dict:
    """The canonical agent.config.json shape with sane defaults."""
    return {
        "schema_version": SCHEMA_VERSION,
        "name": _slug(goal) or "declared-agent",
        "goal": goal,
        "runtime": DEFAULT_RUNTIME,
        "model": DEFAULT_MODEL,
        "research_foundation": DEFAULT_RESEARCH_FOUNDATION,
        "tools": [],            # MCP servers / tool names the agent is wired to
        "policies": [],         # declared policy statements (free-text rules)
        "adversarial": {
            # Default: the built agent SHIPS its own internal critic loop.
            "self_adversarial": True,
            # AI-content-detection is OPT-IN -- only when output must read human.
            "ai_detection": False,
            "max_rounds": 6,
            "required_consecutive_passes": 2,
        },
        "outputs": [],          # a-la-carte deliverable kinds (e.g. "slides","report")
    }


def merge_cli(cfg: dict, args) -> dict:
    """Overlay CLI flags onto a config dict (flags win where provided)."""
    if args.goal:
        cfg["goal"] = args.goal.strip()
        if not cfg.get("name"):
            cfg["name"] = _slug(args.goal)
    if args.name:
        cfg["name"] = _slug(args.name)
    if args.model:
        cfg["model"] = args.model
    if args.runtime:
        cfg["runtime"] = args.runtime
    if args.tools:
        cfg["tools"] = [t.strip() for t in args.tools.split(",") if t.strip()]
    if args.policies:
        cfg["policies"] = [p.strip() for p in args.policies.split(";") if p.strip()]
    if args.outputs:
        cfg["outputs"] = [o.strip() for o in args.outputs.split(",") if o.strip()]
    adv = cfg.setdefault("adversarial", {})
    if args.self_adversarial is not None:
        adv["self_adversarial"] = args.self_adversarial
    if args.ai_detection is not None:
        adv["ai_detection"] = args.ai_detection
    adv.setdefault("max_rounds", 6)
    adv.setdefault("required_consecutive_passes", 2)
    cfg.setdefault("schema_version", SCHEMA_VERSION)
    cfg.setdefault("runtime", DEFAULT_RUNTIME)
    cfg.setdefault("model", DEFAULT_MODEL)
    cfg.setdefault("research_foundation", DEFAULT_RESEARCH_FOUNDATION)
    for k in ("tools", "policies", "outputs"):
        cfg.setdefault(k, [])
    return cfg


def validate_config(cfg: dict) -> None:
    goal = (cfg.get("goal") or "").strip()
    if not goal:
        raise ValueError("config.goal is required (declare what the agent is FOR)")
    cfg["goal"] = goal  # normalize: a goal of pure whitespace is not a goal
    if not cfg.get("name"):
        raise ValueError("config.name could not be derived; pass --name")
    adv = cfg.get("adversarial", {})
    if not isinstance(adv, dict):
        raise ValueError("config.adversarial must be an object")


def _slug(text: str, limit: int = 40) -> str:
    out = []
    for ch in (text or "").lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_/" and (out and out[-1] != "-"):
            out.append("-")
    return "".join(out).strip("-")[:limit].strip("-")


# --------------------------------------------------------------------------- #
# File templates (filled from config; no f-string brace traps)
# --------------------------------------------------------------------------- #

def claude_md(cfg: dict) -> str:
    adv = cfg["adversarial"]
    self_adv = adv.get("self_adversarial", True)
    pol_lines = "\n".join(f"- {p}" for p in cfg["policies"]) or "- (none declared yet)"
    tool_lines = ", ".join(cfg["tools"]) or "(none wired yet)"
    self_adv_block = (
        "This agent SHIPS its OWN internal adversarial critic. Before any\n"
        "deliverable is returned, run `.claude/skills/self-critic/critic_loop.py`\n"
        "over the draft: a fresh, no-memory domain critic\n"
        "(`.claude/skills/self-critic/domain-critic.md`) must pass it TWICE\n"
        "consecutively with zero CRITICALs. The clean-pass counter resets on any\n"
        "failed round. Never return a deliverable that has not converged."
        if self_adv else
        "Self-adversarial critic: OPTED OUT at scaffold time. This agent does NOT\n"
        "internally harden its own output; the builder's publish gate is the only\n"
        "adversarial layer. (Re-enable by adding the self-critic skill.)"
    )
    return f"""# {cfg['name']} — agent constitution

## Declared goal

{cfg['goal']}

This file is the agent's constitution. The goal above is the single thing this
agent exists to achieve; every skill, tool, hook, and policy below serves it.

## Runtime

- Runtime: `{cfg['runtime']}`
- Model: `{cfg['model']}`
- Research foundation: `{cfg['research_foundation']}`
- Tools wired: {tool_lines}

Configuration lives in `agent.config.json` (the versioned, per-customer config).
This `CLAUDE.md` + `.claude/` is the stable harness; the config is what changes
between customers and versions. Do not hardcode goal/model/tool choices here that
belong in the config.

## Policies (declared)

{pol_lines}

Policy statements are enforced by the hook in `.claude/hooks/policy_gate.py`
where mechanical, and by the agent's own judgment otherwise. A policy is a
promise; breaking one is a failure even if the task technically completes.

## Self-adversarial discipline

{self_adv_block}

## Proven execution

`tests/` holds proven-execution tests. Do not declare a run successful on
reasoning alone — run the tests and confirm they pass.
"""


def manifest_json(cfg: dict) -> dict:
    adv = cfg["adversarial"]
    skills = ["self-critic"] if adv.get("self_adversarial", True) else []
    return {
        "schema_version": SCHEMA_VERSION,
        "name": cfg["name"],
        "goal": cfg["goal"],
        "runtime": cfg["runtime"],
        "model": cfg["model"],
        "research_foundation": cfg["research_foundation"],
        "capabilities": {
            "tools": cfg["tools"],
            "skills": skills,
            "outputs": cfg["outputs"],
        },
        "adversarial": adv,
        "policies": cfg["policies"],
        "provenance": {
            "built_by": "agent-builder",
            "built_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def mcp_json(cfg: dict) -> dict:
    """A real .mcp.json shape. Tools listed in config become declared servers
    (command left as a placeholder the wiring step fills in per runtime)."""
    servers = {}
    for tool in cfg["tools"]:
        servers[tool] = {
            "command": "REPLACE_WITH_LAUNCH_COMMAND",
            "args": [],
            "env": {},
            "_note": f"MCP server '{tool}' declared for goal: wire its launch command.",
        }
    return {"mcpServers": servers}


def policy_gate_hook() -> str:
    """A real PreToolUse safeguard hook: reads declared policies and the
    proposed tool call from stdin, blocks on a declared deny-pattern. stdlib."""
    return '''#!/usr/bin/env python3
"""policy_gate.py -- PreToolUse safeguard hook for a built agent.

Reads the Claude-Code hook event JSON from stdin, loads the agent's declared
policies from agent.config.json, and BLOCKS the tool call (exit 2 + reason on
stderr) when it matches a declared deny-pattern. This is the mechanical half of
policy enforcement; judgment policies are enforced by the agent itself.

A policy string of the form 'deny:<substring>' blocks any tool input whose
JSON contains <substring>. All other policy strings are advisory (surfaced, not
blocked). This keeps the hook honest: it only blocks what was explicitly
declared blockable, and never silently permits a declared deny.
"""
import json
import os
import sys


def _load_policies():
    here = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(here, "..", "..", "agent.config.json")
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            return json.load(fh).get("policies", [])
    except (OSError, ValueError):
        return []


def main():
    raw = sys.stdin.read()
    try:
        event = json.loads(raw) if raw.strip() else {}
    except ValueError:
        event = {}
    payload = json.dumps(event.get("tool_input", event), ensure_ascii=False)
    for pol in _load_policies():
        if isinstance(pol, str) and pol.startswith("deny:"):
            needle = pol[len("deny:"):].strip()
            if needle and needle in payload:
                sys.stderr.write(
                    f"policy_gate: blocked by declared policy '{pol}'\\n"
                )
                return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def self_critic_loop_py() -> str:
    """The built agent's OWN internal gauntlet engine. This is a faithful,
    minimal copy of diligence-deck/critic_loop.py's loop logic, shipped INSIDE
    the generated agent so the agent is self-contained and portable (it cannot
    import a sibling skill that travels separately). Lineage is noted in the
    docstring; the agent-builder's own publish_gate.py imports the canonical
    engine rather than copying it -- only the EMITTED agent gets a copy, because
    it must stand alone."""
    return '''#!/usr/bin/env python3
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
'''


def domain_critic_md(cfg: dict) -> str:
    return f"""# domain-critic — this agent's internal adversary (one round)

You are a fresh, skeptical domain expert reviewing a draft deliverable produced
by an agent whose declared goal is:

> {cfg['goal']}

You have **no memory of any prior round** and did not write any of this. A
review that says "looks good" without checking is a failed review. Find every
reason the draft does NOT achieve the declared goal, then return the verdict.

## Grade against (in order)

1. **Goal achievement.** Does the draft actually accomplish the declared goal,
   or only gesture at it? A draft that misses the goal = CRITICAL.
2. **Soundness.** Are the claims / steps / outputs correct and internally
   consistent? A false or self-contradictory core claim = CRITICAL.
3. **Evidence.** Are assertions backed (cited, computed, demonstrated) rather
   than asserted? Unsupported load-bearing claim = MAJOR (CRITICAL if the whole
   deliverable rests on it).
4. **Policy adherence.** Does the draft respect every declared policy? A
   violated policy = CRITICAL.
5. **Slop.** Hedges, padding, vague titles, asymmetric depth = MAJOR.

## Verdict (STRICT JSON, nothing else)

`pass` is true ONLY if there are zero `critical` violations.

```json
{{
  "pass": false,
  "violations": [
    {{"check": "goal_achievement", "severity": "critical",
      "where": "section / claim", "fix": "concrete bounded action"}}
  ]
}}
```

`severity` is one of `critical | major | minor`. `fix` is a concrete action,
not "improve this". Output only the JSON object and stop.
"""


def selfcritic_skill_md(cfg: dict) -> str:
    return f"""---
name: self-critic
description: This agent's own internal adversarial critic loop. Hardens every deliverable with a fresh no-memory domain critic until it passes twice consecutively with zero CRITICALs before return.
---

# self-critic — the agent's internal adversarial flow

This skill is wired in by default (the agent-builder is biased to emit
self-adversarial agents). Before this agent returns ANY deliverable for the
goal below, harden it here.

## Goal under guard

> {cfg['goal']}

## How to run

1. Produce a draft deliverable.
2. Run `critic_loop.py` with the domain critic (`domain-critic.md`) as a fresh,
   no-memory reviewer of the draft.
3. On any CRITICAL: apply the cited fixes, re-run. The clean-pass counter resets
   to zero on any failed round.
4. Converge only on TWO consecutive zero-CRITICAL passes (cap: MAX_ROUNDS=6).
   Never return a deliverable that has not converged.

Prefer the subagent mode (a fresh subagent per round). When the agent cannot
spawn subagents, use the single-process fallback with a hard context reset
between rounds — weaker isolation, same termination rules.

Prove the loop logic any time with `critic_loop.py --selftest`.
"""


def proven_test_py(cfg: dict) -> str:
    return f'''#!/usr/bin/env python3
"""test_agent.py -- proven-execution tests for: {cfg['name']}

Goal under test: {cfg['goal']}

These are STRUCTURAL proofs that the scaffolded agent is well-formed and that
its self-adversarial engine actually runs. Domain behavior tests are added by
the builder as the agent grows; this file guarantees the harness itself works.

    python3 tests/test_agent.py        # exit 0 on pass
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _exists(rel):
    assert os.path.exists(os.path.join(ROOT, rel)), f"missing required path: {{rel}}"


def test_repo_shape():
    for rel in ("CLAUDE.md", "agent.config.json", "manifest.json", ".mcp.json"):
        _exists(rel)


def test_config_declares_goal():
    with open(os.path.join(ROOT, "agent.config.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    assert cfg.get("goal"), "config.goal must be non-empty"


def test_self_critic_engine_runs():
    """If this agent ships a self-critic, its loop engine must pass --selftest."""
    loop = os.path.join(ROOT, ".claude", "skills", "self-critic", "critic_loop.py")
    if not os.path.exists(loop):
        print("  (self-critic opted out; skipping engine selftest)")
        return
    r = subprocess.run([sys.executable, loop, "--selftest"],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"self-critic engine selftest failed:\\n{{r.stdout}}{{r.stderr}}"


def main():
    tests = [test_repo_shape, test_config_declares_goal, test_self_critic_engine_runs]
    for t in tests:
        t()
        print(f"  [ok] {{t.__name__}}")
    print("ALL PROVEN-EXECUTION TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def policy_md(statement: str, idx: int) -> str:
    return f"""# Policy {idx}

**Statement:** {statement}

This is a declared policy for the agent. Where it is mechanically enforceable
(e.g. `deny:<substring>`), `.claude/hooks/policy_gate.py` blocks violating tool
calls. Otherwise the agent enforces it by judgment. Breaking this policy is a
run failure even if the task otherwise completes.
"""


# --------------------------------------------------------------------------- #
# Scaffolding
# --------------------------------------------------------------------------- #

def _write(path: str, content: str, *, executable: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    if executable:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def scaffold(cfg: dict, target: str) -> dict:
    """Write the full portable agent repo under `target`. Returns the manifest."""
    validate_config(cfg)
    os.makedirs(target, exist_ok=True)
    adv = cfg["adversarial"]
    self_adv = adv.get("self_adversarial", True)

    # Constitution + config + manifest + tool wiring.
    _write(os.path.join(target, "CLAUDE.md"), claude_md(cfg))
    _write(os.path.join(target, "agent.config.json"),
           json.dumps(cfg, indent=2) + "\n")
    man = manifest_json(cfg)
    _write(os.path.join(target, "manifest.json"), json.dumps(man, indent=2) + "\n")
    _write(os.path.join(target, ".mcp.json"),
           json.dumps(mcp_json(cfg), indent=2) + "\n")

    # Safeguard hook.
    _write(os.path.join(target, ".claude", "hooks", "policy_gate.py"),
           policy_gate_hook(), executable=True)

    # Self-adversarial skill (DEFAULT-ON).
    if self_adv:
        base = os.path.join(target, ".claude", "skills", "self-critic")
        _write(os.path.join(base, "SKILL.md"), selfcritic_skill_md(cfg))
        _write(os.path.join(base, "critic_loop.py"),
               self_critic_loop_py(), executable=True)
        _write(os.path.join(base, "domain-critic.md"), domain_critic_md(cfg))
    else:
        # Keep .claude/skills/ present (empty marker) so the repo shape is stable.
        _write(os.path.join(target, ".claude", "skills", ".gitkeep"), "")

    # Declared policies.
    if cfg["policies"]:
        for i, pol in enumerate(cfg["policies"], 1):
            _write(os.path.join(target, "policies", f"policy-{i:02d}.md"),
                   policy_md(pol, i))
    else:
        _write(os.path.join(target, "policies", ".gitkeep"), "")

    # Proven-execution tests.
    _write(os.path.join(target, "tests", "test_agent.py"),
           proven_test_py(cfg), executable=True)

    return man


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #

def _selftest() -> int:
    failures = []

    def chk(name, cond):
        print(f"  [{'ok' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as td:
        # Default scaffold = self-adversarial ON.
        cfg = default_config("Summarize a customer's support tickets into themes")
        cfg["tools"] = ["zendesk-mcp"]
        cfg["policies"] = ["deny:DROP TABLE", "Never email a customer directly"]
        target = os.path.join(td, "agent-on")
        man = scaffold(cfg, target)

        for rel in ("CLAUDE.md", "agent.config.json", "manifest.json", ".mcp.json",
                    ".claude/hooks/policy_gate.py",
                    ".claude/skills/self-critic/SKILL.md",
                    ".claude/skills/self-critic/critic_loop.py",
                    ".claude/skills/self-critic/domain-critic.md",
                    "policies/policy-01.md", "policies/policy-02.md",
                    "tests/test_agent.py"):
            chk(f"exists: {rel}", os.path.exists(os.path.join(target, rel)))

        # Manifest declares the self-critic skill + the goal.
        chk("manifest lists self-critic skill", "self-critic" in man["capabilities"]["skills"])
        chk("manifest carries the goal", bool(man["goal"]))

        # .mcp.json declares the wired tool.
        with open(os.path.join(target, ".mcp.json"), encoding="utf-8") as fh:
            mcp = json.load(fh)
        chk(".mcp.json declares zendesk-mcp server", "zendesk-mcp" in mcp["mcpServers"])

        # CLAUDE.md embeds the declared goal.
        with open(os.path.join(target, "CLAUDE.md"), encoding="utf-8") as fh:
            cm = fh.read()
        chk("CLAUDE.md embeds the goal", "support tickets" in cm)

        # The shipped self-critic engine actually runs.
        loop = os.path.join(target, ".claude", "skills", "self-critic", "critic_loop.py")
        r = subprocess.run([sys.executable, loop, "--selftest"],
                           capture_output=True, text=True)
        chk("shipped self-critic engine --selftest exits 0", r.returncode == 0)

        # The proven-execution test the agent ships actually runs.
        t = subprocess.run([sys.executable,
                            os.path.join(target, "tests", "test_agent.py")],
                           capture_output=True, text=True)
        chk("shipped tests/test_agent.py exits 0", t.returncode == 0)

        # Opt-OUT scaffold = no self-critic skill, but repo shape still stable.
        cfg2 = default_config("Generate marketing copy variants")
        cfg2["adversarial"]["self_adversarial"] = False
        target2 = os.path.join(td, "agent-off")
        man2 = scaffold(cfg2, target2)
        chk("opt-out: no self-critic SKILL.md",
            not os.path.exists(os.path.join(target2, ".claude/skills/self-critic/SKILL.md")))
        chk("opt-out: manifest has empty skills", man2["capabilities"]["skills"] == [])
        chk("opt-out: .claude/skills/ still present",
            os.path.isdir(os.path.join(target2, ".claude/skills")))

    print("\n" + ("ALL SELFTESTS PASSED" if not failures
                  else f"SELFTEST FAILURES: {failures}"))
    return 0 if not failures else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="path to an agent.config.json to scaffold from")
    p.add_argument("--goal", help="the agent's declared goal (overrides config.goal)")
    p.add_argument("--name", help="agent name (slugified); derived from goal if omitted")
    p.add_argument("--model", help="model id (default %(default)s)", default=None)
    p.add_argument("--runtime", help="runtime (default fly-machine:kind=agent)", default=None)
    p.add_argument("--tools", help="comma-separated MCP tool names to wire")
    p.add_argument("--policies", help="semicolon-separated declared policy statements")
    p.add_argument("--outputs", help="comma-separated a-la-carte output kinds")
    p.add_argument("--self-adversarial", dest="self_adversarial",
                   action="store_true", default=None,
                   help="ship the agent with its own critic loop (DEFAULT)")
    p.add_argument("--no-self-adversarial", dest="self_adversarial",
                   action="store_false",
                   help="opt OUT of shipping the internal critic loop")
    p.add_argument("--ai-detection", dest="ai_detection",
                   action="store_true", default=None,
                   help="opt IN to the AI-content-detection sublayer at publish")
    p.add_argument("--no-ai-detection", dest="ai_detection", action="store_false",
                   help="explicitly skip the AI-detection sublayer (default)")
    p.add_argument("--out", help="target directory for the scaffolded repo")
    p.add_argument("--selftest", action="store_true",
                   help="scaffold into a temp dir and assert the structure")
    args = p.parse_args(argv)

    if args.selftest:
        return _selftest()

    if args.config:
        with open(args.config, encoding="utf-8") as fh:
            cfg = json.load(fh)
    else:
        cfg = default_config(args.goal or "")
    cfg = merge_cli(cfg, args)

    if not args.out:
        p.error("--out is required (target directory for the scaffolded repo)")
    try:
        validate_config(cfg)
    except ValueError as e:
        p.error(str(e))

    man = scaffold(cfg, args.out)
    print(f"scaffolded '{man['name']}' -> {os.path.abspath(args.out)}")
    print(f"  goal: {man['goal']}")
    print(f"  self-adversarial: {man['adversarial'].get('self_adversarial')}")
    print(f"  ai-detection (publish sublayer): {man['adversarial'].get('ai_detection')}")
    print(f"  tools: {man['capabilities']['tools'] or '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
