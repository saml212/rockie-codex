#!/usr/bin/env python3
"""test_agent.py -- proven-execution tests for: release-notes-writer

Goal under test: Turn a list of merged pull requests into a clear, user-facing release-notes section grouped by Added / Changed / Fixed, with one plain-language line per change.

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
    assert os.path.exists(os.path.join(ROOT, rel)), f"missing required path: {rel}"


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
    assert r.returncode == 0, f"self-critic engine selftest failed:\n{r.stdout}{r.stderr}"


def main():
    tests = [test_repo_shape, test_config_declares_goal, test_self_critic_engine_runs]
    for t in tests:
        t()
        print(f"  [ok] {t.__name__}")
    print("ALL PROVEN-EXECUTION TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
