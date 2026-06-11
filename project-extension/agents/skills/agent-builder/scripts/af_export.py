#!/usr/bin/env python3
"""af_export.py -- serialize a scaffolded agent repo to a .af agent-file.

A `.af` (Letta-style agent-file) is a single self-describing JSON manifest that
captures enough of a built agent to reconstruct or import it elsewhere: its
constitution (CLAUDE.md), declared goal + config, the skills it ships
(.claude/skills/), the safeguards it ships (.claude/hooks/), its MCP/tool wiring
(.mcp.json), and its declared policy statements (policies/). File CONTENTS are
embedded (not just listed) so the `.af` is a true handover artifact -- you can
rebuild the repo from the `.af` alone.

Schema is documented in references/af-format.md. stdlib-only; no hardcoded paths.

    af_export.py --agent <repo> --out agent.af
    af_export.py --selftest        # scaffold a sample, export, round-trip, assert
"""

from __future__ import annotations

import argparse
import json
import os
import sys

AF_SCHEMA_VERSION = "1.0"
AF_KIND = "letta.agentfile"


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _read_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _collect_dir(root: str, subdir: str) -> list:
    """Embed every file under root/subdir as {path, content}, path RELATIVE to
    the repo root so it round-trips into an identical layout. Skips junk."""
    base = os.path.join(root, subdir)
    out = []
    if not os.path.isdir(base):
        return out
    for dirpath, _dirs, files in os.walk(base):
        for fn in sorted(files):
            if fn.endswith(".pyc") or fn == ".DS_Store":
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            out.append({"path": rel, "content": _read(full)})
    return out


def export_agent(agent_root: str) -> dict:
    """Walk a scaffolded agent repo and build the .af manifest dict."""
    if not os.path.isdir(agent_root):
        raise FileNotFoundError(f"agent repo not found: {agent_root}")
    cfg_path = os.path.join(agent_root, "agent.config.json")
    if not os.path.exists(cfg_path):
        raise FileNotFoundError(
            f"{agent_root} is not a scaffolded agent (no agent.config.json)"
        )
    cfg = _read_json(cfg_path)

    manifest = None
    man_path = os.path.join(agent_root, "manifest.json")
    if os.path.exists(man_path):
        manifest = _read_json(man_path)

    mcp = {}
    mcp_path = os.path.join(agent_root, ".mcp.json")
    if os.path.exists(mcp_path):
        mcp = _read_json(mcp_path)

    constitution = ""
    claude_path = os.path.join(agent_root, "CLAUDE.md")
    if os.path.exists(claude_path):
        constitution = _read(claude_path)

    af = {
        "af_kind": AF_KIND,
        "af_schema_version": AF_SCHEMA_VERSION,
        "name": cfg.get("name", os.path.basename(os.path.abspath(agent_root))),
        "goal": cfg.get("goal", ""),
        "config": cfg,
        "manifest": manifest,
        "constitution": constitution,         # CLAUDE.md, embedded
        "mcp": mcp,                           # .mcp.json, embedded
        "policies": cfg.get("policies", []),  # declared policy statements
        "skills": _collect_dir(agent_root, ".claude/skills"),
        "hooks": _collect_dir(agent_root, ".claude/hooks"),
        "policy_files": _collect_dir(agent_root, "policies"),
        "tests": _collect_dir(agent_root, "tests"),
    }
    return af


def write_af(af: dict, out_path: str) -> None:
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(af, fh, indent=2)
        fh.write("\n")


def _manifest_dest(target: str, rel_path: str) -> str:
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ValueError(".af entry path must be a non-empty relative path.")
    if "\0" in rel_path:
        raise ValueError(f".af entry path contains a NUL byte: {rel_path!r}")
    if os.path.isabs(rel_path) or rel_path.startswith(("/", "\\")):
        raise ValueError(f".af entry path must be relative: {rel_path!r}")
    if any(part == ".." for part in rel_path.replace("\\", "/").split("/")):
        raise ValueError(f".af entry path cannot traverse parents: {rel_path!r}")

    normalized = os.path.normpath(rel_path)
    if normalized in ("", ".") or normalized == ".." or normalized.startswith(f"..{os.sep}"):
        raise ValueError(f".af entry path must name a file under target: {rel_path!r}")

    target_abs = os.path.abspath(target)
    dest = os.path.abspath(os.path.join(target_abs, normalized))
    if os.path.commonpath([target_abs, dest]) != target_abs:
        raise ValueError(f".af entry path escapes target: {rel_path!r}")
    return dest


def reconstruct(af: dict, target: str) -> None:
    """Rebuild a repo on disk from a .af manifest -- proves the round-trip and
    is the import side of the handover format."""
    os.makedirs(target, exist_ok=True)
    if af.get("constitution"):
        with open(os.path.join(target, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write(af["constitution"])
    with open(os.path.join(target, "agent.config.json"), "w", encoding="utf-8") as fh:
        json.dump(af["config"], fh, indent=2)
        fh.write("\n")
    if af.get("manifest") is not None:
        with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(af["manifest"], fh, indent=2)
            fh.write("\n")
    if af.get("mcp"):
        with open(os.path.join(target, ".mcp.json"), "w", encoding="utf-8") as fh:
            json.dump(af["mcp"], fh, indent=2)
            fh.write("\n")
    for bucket in ("skills", "hooks", "policy_files", "tests"):
        for entry in af.get(bucket, []):
            dest = _manifest_dest(target, entry["path"])
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(entry["content"])


def validate_af(af: dict) -> None:
    for k in ("af_kind", "af_schema_version", "name", "goal", "config"):
        if k not in af:
            raise ValueError(f".af missing required key '{k}'")
    if af["af_kind"] != AF_KIND:
        raise ValueError(f".af af_kind must be {AF_KIND!r}")


# --------------------------------------------------------------------------- #
# Selftest: scaffold a sample agent, export it, round-trip it, assert fidelity
# --------------------------------------------------------------------------- #

def _selftest() -> int:
    import subprocess
    import tempfile

    # Import the scaffolder from the sibling script (same dir) so the selftest
    # exercises a REAL scaffolded repo, not a hand-built fixture.
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import scaffold_agent  # noqa: E402

    failures = []

    def chk(name, cond):
        print(f"  [{'ok' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "agent")
        cfg = scaffold_agent.default_config(
            "Triage inbound bug reports and label them by severity")
        cfg["tools"] = ["github-mcp"]
        cfg["policies"] = ["deny:rm -rf", "Never close an issue without a reason"]
        scaffold_agent.scaffold(cfg, repo)

        af = export_agent(repo)
        validate_af(af)
        chk("af_kind is letta.agentfile", af["af_kind"] == AF_KIND)
        chk("af embeds the goal", "bug reports" in af["goal"])
        chk("af embeds the constitution (CLAUDE.md)", "constitution" in af and af["constitution"])
        chk("af embeds MCP wiring", "github-mcp" in af["mcp"].get("mcpServers", {}))
        chk("af carries declared policies", "deny:rm -rf" in af["policies"])
        skill_paths = [s["path"] for s in af["skills"]]
        chk("af embeds the self-critic SKILL.md",
            any(p.endswith("self-critic/SKILL.md") for p in skill_paths))
        chk("af embeds the self-critic engine",
            any(p.endswith("self-critic/critic_loop.py") for p in skill_paths))
        chk("af embeds the safeguard hook",
            any(h["path"].endswith("policy_gate.py") for h in af["hooks"]))
        chk("af embeds the policy files",
            any(pf["path"].endswith("policy-01.md") for pf in af["policy_files"]))

        out = os.path.join(td, "agent.af")
        write_af(af, out)
        chk("write_af produced a file", os.path.exists(out))
        reloaded = _read_json(out)
        chk("reloaded .af validates", reloaded["af_kind"] == AF_KIND)

        # Round-trip: reconstruct and compare key files byte-for-byte.
        rebuilt = os.path.join(td, "rebuilt")
        reconstruct(reloaded, rebuilt)
        for rel in ("CLAUDE.md", "agent.config.json",
                    ".claude/skills/self-critic/critic_loop.py",
                    ".claude/hooks/policy_gate.py", "policies/policy-01.md"):
            a = os.path.join(repo, rel)
            b = os.path.join(rebuilt, rel)
            same = (os.path.exists(b) and _read(a) == _read(b))
            chk(f"round-trip identical: {rel}", same)

        # The reconstructed agent's shipped engine still runs (true portability).
        loop = os.path.join(rebuilt, ".claude", "skills", "self-critic", "critic_loop.py")
        if os.path.exists(loop):
            r = subprocess.run(
                [sys.executable, loop, "--selftest"], capture_output=True, text=True)
            chk("reconstructed self-critic engine --selftest exits 0", r.returncode == 0)

        escape = os.path.join(td, "escape.txt")
        malicious = dict(reloaded)
        malicious["skills"] = [{"path": "../escape.txt", "content": "escaped"}]
        try:
            reconstruct(malicious, os.path.join(td, "malicious-parent"))
            blocked_parent = False
        except ValueError:
            blocked_parent = True
        chk("reconstruct rejects parent traversal paths",
            blocked_parent and not os.path.exists(escape))

        abs_escape = os.path.join(td, "absolute-escape.txt")
        malicious_abs = dict(reloaded)
        malicious_abs["skills"] = [{"path": abs_escape, "content": "escaped"}]
        try:
            reconstruct(malicious_abs, os.path.join(td, "malicious-absolute"))
            blocked_absolute = False
        except ValueError:
            blocked_absolute = True
        chk("reconstruct rejects absolute paths",
            blocked_absolute and not os.path.exists(abs_escape))

    print("\n" + ("ALL SELFTESTS PASSED" if not failures
                  else f"SELFTEST FAILURES: {failures}"))
    return 0 if not failures else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--agent", help="path to a scaffolded agent repo to export")
    p.add_argument("--out", help="output .af path (default: <name>.af in cwd)")
    p.add_argument("--reconstruct", metavar="TARGET",
                   help="instead of exporting, rebuild a repo at TARGET from --af")
    p.add_argument("--af", help="path to a .af file (for --reconstruct)")
    p.add_argument("--selftest", action="store_true",
                   help="scaffold a sample, export, round-trip, and assert fidelity")
    args = p.parse_args(argv)

    if args.selftest:
        return _selftest()

    if args.reconstruct:
        if not args.af:
            p.error("--reconstruct requires --af <file>")
        try:
            af = _read_json(args.af)
            validate_af(af)
            reconstruct(af, args.reconstruct)
        except (FileNotFoundError, ValueError) as e:
            p.error(str(e))
        print(f"reconstructed '{af['name']}' -> {os.path.abspath(args.reconstruct)}")
        return 0

    if not args.agent:
        p.print_help()
        return 0

    try:
        af = export_agent(args.agent)
        validate_af(af)
    except (FileNotFoundError, ValueError) as e:
        p.error(str(e))  # bad input -> clean usage error, not a raw traceback
    out = args.out or f"{af['name']}.af"
    write_af(af, out)
    print(f"exported '{af['name']}' -> {os.path.abspath(out)}")
    print(f"  skills={len(af['skills'])} hooks={len(af['hooks'])} "
          f"policies={len(af['policies'])} tools={list(af['mcp'].get('mcpServers', {}))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
