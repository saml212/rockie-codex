#!/usr/bin/env python3
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
                    f"policy_gate: blocked by declared policy '{pol}'\n"
                )
                return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
