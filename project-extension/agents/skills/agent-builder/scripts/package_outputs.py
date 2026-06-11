#!/usr/bin/env python3
"""package_outputs.py -- B5: assemble an agent's a-la-carte deliverables.

Step [9] EXPORT of the agent-builder lifecycle is "a-la-carte outputs": once an
agent repo is scaffolded (and ideally publish-gate-passed), the customer picks
any SUBSET of deliverables and this packager assembles exactly that subset into a
bundle dir (and optional tarball), each artifact content-hashed in a top-level
OUTPUTS_MANIFEST.json.

The deliverable catalog (pick any subset via --include):

  repo        a portable copy + tarball of the agent repo (stdlib `tarfile`).
  techdoc     TECHNICAL_DOCUMENTATION.md reproducing the agent's skills/prompts/
              policies/CLAUDE.md WORD-FOR-WORD (verbatim), each with a sha256.
              See the verbatim rule below.
  pptx        a powerpoint payload in the `powerpoint` skill Request Shape
              (mirrors diligence-deck/render_deck.py -- NOT re-authored), plus an
              emit_artifact arg dict for the runtime (the --emit emit-args path).
  af          calls B1's af_export.py to emit the .af handover artifact.
  policies    bundles the agent's policies/ + .claude/hooks/ (the safeguards).
  monitoring  MONITORING.md + monitoring.json: a POINTER to where this agent's
              Langfuse/observability lives (the B3 hand-off; references, builds nothing).

The WORD-FOR-WORD rule (Sam-critical): the technical documentation reproduces the
agent's skills and prompts BYTE-FOR-BYTE -- the SAME text deployed in the repo --
so the customer sees precisely what their agent is made of. Every
`.claude/skills/**/*.md` (SKILL.md + the prompts a skill ships, e.g.
domain-critic.md), `prompts/**/*.md`, `policies/**`, and `CLAUDE.md` is
embedded verbatim in fenced blocks AND a sha256 of each is recorded.
`package_outputs.py --verify <bundle> <repo>` re-hashes the LIVE repo files and
asserts the doc's embedded copies still match -- failing loudly on any drift. That
is the "word-for-word confirmation" guarantee. Full contract:
references/outputs-catalog.md.

Reuse, do not duplicate:
  - the .af export is produced by importing B1's af_export.py (sibling script).
  - the powerpoint payload mirrors diligence-deck/render_deck.py's documented
    Request Shape {prompt, slide_count, title, findings, attachments,
    template_path, theme}; we do NOT invent a new contract.

stdlib-only. No network, no pip, no hardcoded /Users paths.

    package_outputs.py --agent <repo> --include repo,techdoc,pptx,af,policies,monitoring --out <dir>
    package_outputs.py --agent <repo> --out <dir>      # selection from agent.config.json "outputs"
    package_outputs.py --verify <bundle> <repo>        # word-for-word drift check
    package_outputs.py --selftest                       # scaffold, package, assert, verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tarfile

# Import the sibling B1 scripts (same dir) so we REUSE the proven export +
# scaffolder rather than re-authoring them.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import af_export        # noqa: E402  (B1 .af serializer -- reused, not duplicated)
import scaffold_agent   # noqa: E402  (B1 scaffolder -- used by --selftest)

# The full deliverable catalog the customer may pick from.
ALL_OUTPUTS = ["repo", "techdoc", "pptx", "af", "policies", "monitoring"]

# Map an agent.config.json "outputs" kind onto a packager deliverable. The config
# uses domain words ("markdown", "slides", "report"); we translate to deliverables
# so the same a-la-carte list drives both the agent and its packaging.
CONFIG_OUTPUT_ALIASES = {
    "slides": "pptx",
    "deck": "pptx",
    "powerpoint": "pptx",
    "pptx": "pptx",
    "markdown": "techdoc",
    "report": "techdoc",
    "techdoc": "techdoc",
    "documentation": "techdoc",
    "repo": "repo",
    "af": "af",
    "policies": "policies",
    "monitoring": "monitoring",
}

# Files whose CONTENTS must be reproduced verbatim (and hashed) in the tech doc.
# These are the "what your agent is made of" surfaces Sam was emphatic about.
VERBATIM_GLOBS = (
    ("CLAUDE.md", "_is_claude_md"),
    # Every .md under a skill: its SKILL.md AND the prompts it ships (e.g. the
    # self-critic's domain-critic.md). Sam's guarantee is "skills AND prompts
    # verbatim"; in the config-over-code layout an agent's prompts live inside
    # its skills, so SKILL.md-only would silently omit the prompts half.
    (".claude/skills", "_is_md"),
    ("prompts", "_is_md"),
    ("policies", "_is_policy_file"),
)


# --------------------------------------------------------------------------- #
# small fs / hashing helpers
# --------------------------------------------------------------------------- #

def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _read_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _is_claude_md(rel: str) -> bool:
    return rel == "CLAUDE.md"


def _is_md(rel: str) -> bool:
    return rel.endswith(".md")


def _is_policy_file(rel: str) -> bool:
    # Every file under policies/ is part of "what the agent must never do".
    return not rel.endswith(".gitkeep")


# --------------------------------------------------------------------------- #
# selection resolution
# --------------------------------------------------------------------------- #

def resolve_selection(include_flag, cfg: dict) -> list:
    """Turn --include OR the config's "outputs" list into a deliverable list.

    --include wins when present. Otherwise the agent.config.json "outputs" kinds
    are translated to deliverables; techdoc is always added because every bundle
    documents what it contains, and the manifest itself is implicit.
    """
    if include_flag:
        sel = [s.strip() for s in include_flag.split(",") if s.strip()]
    else:
        sel = []
        for kind in cfg.get("outputs", []) or []:
            mapped = CONFIG_OUTPUT_ALIASES.get(str(kind).lower())
            if mapped and mapped not in sel:
                sel.append(mapped)
        if "techdoc" not in sel:
            sel.append("techdoc")  # always ship a record of what was packaged
    bad = [s for s in sel if s not in ALL_OUTPUTS]
    if bad:
        raise ValueError("unknown output(s): %s (valid: %s)"
                         % (", ".join(bad), ", ".join(ALL_OUTPUTS)))
    # stable canonical order
    return [o for o in ALL_OUTPUTS if o in sel]


# --------------------------------------------------------------------------- #
# verbatim collection (the word-for-word surface)
# --------------------------------------------------------------------------- #

def collect_verbatim(agent_root: str) -> list:
    """Return [{path, content, sha256}] for every verbatim surface, sorted by
    path. `path` is RELATIVE to the repo root so it round-trips and so --verify
    can re-hash the live file at the same location."""
    seen = {}
    for base, predicate_name in VERBATIM_GLOBS:
        predicate = globals()[predicate_name]
        full_base = os.path.join(agent_root, base)
        if os.path.isfile(full_base):
            rel = os.path.relpath(full_base, agent_root)
            if predicate(rel):
                seen[rel] = full_base
            continue
        if not os.path.isdir(full_base):
            continue
        for dirpath, _dirs, files in os.walk(full_base):
            for fn in sorted(files):
                if fn.endswith(".pyc") or fn == ".DS_Store":
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, agent_root)
                if predicate(rel):
                    seen[rel] = full
    out = []
    for rel in sorted(seen):
        content = _read(seen[rel])
        out.append({"path": rel, "content": content,
                    "sha256": _sha256_text(content)})
    return out


def _fence_for(rel: str) -> str:
    """Pick a fence language hint by extension (cosmetic; content is verbatim)."""
    if rel.endswith(".md"):
        return "markdown"
    if rel.endswith(".py"):
        return "python"
    if rel.endswith(".json"):
        return "json"
    return ""


def _safe_fence(content: str) -> str:
    """Choose a backtick fence longer than any run of backticks in the content,
    so embedding a file that itself contains ``` does not break the doc."""
    longest = 0
    run = 0
    for ch in content:
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return "`" * max(3, longest + 1)


def render_techdoc(agent_root: str, cfg: dict, verbatim: list) -> str:
    """Build TECHNICAL_DOCUMENTATION.md: a verbatim, hash-stamped reproduction of
    the agent's constitution, skills, prompts, and policies."""
    name = cfg.get("name", os.path.basename(os.path.abspath(agent_root)))
    goal = cfg.get("goal", "")
    adv = cfg.get("adversarial", {}) or {}
    lines = []
    lines.append("# Technical documentation: %s" % name)
    lines.append("")
    lines.append("> **Word-for-word confirmation.** Every file reproduced below "
                 "is embedded BYTE-FOR-BYTE -- the exact text deployed in the "
                 "agent repo -- with its sha256. Run "
                 "`package_outputs.py --verify <bundle> <repo>` to re-hash the "
                 "live repo and confirm none of it has drifted.")
    lines.append("")
    lines.append("## What this agent is")
    lines.append("")
    lines.append("- **Goal:** %s" % goal)
    lines.append("- **Runtime:** %s" % cfg.get("runtime", "(unset)"))
    lines.append("- **Model:** %s" % cfg.get("model", "(unset)"))
    lines.append("- **Research foundation:** %s"
                 % cfg.get("research_foundation", "(none)"))
    tools = cfg.get("tools", []) or []
    lines.append("- **Tools (MCP):** %s" % (", ".join(tools) if tools else "(none)"))
    lines.append("- **Self-adversarial critic:** %s"
                 % ("on" if adv.get("self_adversarial", True) else "off"))
    lines.append("- **AI-detection publish sublayer:** %s"
                 % ("on" if adv.get("ai_detection", False) else "off"))
    outs = cfg.get("outputs", []) or []
    lines.append("- **Declared outputs:** %s"
                 % (", ".join(map(str, outs)) if outs else "(none)"))
    lines.append("")

    # Integrity index up front -- a scannable list of every hashed surface.
    lines.append("## Integrity index (sha256)")
    lines.append("")
    lines.append("| File | sha256 |")
    lines.append("|---|---|")
    for entry in verbatim:
        lines.append("| `%s` | `%s` |" % (entry["path"], entry["sha256"]))
    lines.append("")

    lines.append("## Verbatim contents")
    lines.append("")
    lines.append("The following are the agent's skills, prompts, policies, and "
                 "constitution, reproduced exactly.")
    lines.append("")
    text = "\n".join(lines) + "\n"
    for entry in verbatim:
        rel = entry["path"]
        content = entry["content"]
        fence = _safe_fence(content)
        lang = _fence_for(rel)
        # Embed `content` BYTE-FOR-BYTE between the fences so the extracted block
        # hashes back to the recorded (live-file) sha256. The opening fence line
        # ends in \n, then content verbatim, then a \n before the closing fence
        # (markdown requires the closer on its own line). _extract_embedded
        # reverses exactly this: it strips the single \n we add before the fence.
        text += "### `%s`\n\nsha256: `%s`\n\n" % (rel, entry["sha256"])
        text += fence + lang + "\n" + content + "\n" + fence + "\n\n"
    return text


# --------------------------------------------------------------------------- #
# powerpoint payload (mirror render_deck.py's Request Shape -- do not re-author)
# --------------------------------------------------------------------------- #

def _load_render_deck():
    """Import diligence-deck/scripts/render_deck.py by walking up to the skills/
    root and across to the sibling skill. No hardcoded absolute paths."""
    d = _HERE
    for _ in range(8):
        cand = os.path.join(d, "diligence-deck", "scripts", "render_deck.py")
        if os.path.exists(cand):
            sk_scripts = os.path.dirname(cand)
            if sk_scripts not in sys.path:
                sys.path.insert(0, sk_scripts)
            import importlib
            return importlib.import_module("render_deck")
        d = os.path.dirname(d)
    return None  # diligence-deck not co-located; we fall back to a local shape


def _agent_summary_slides(cfg: dict, agent_root: str) -> list:
    """Build the render_deck SLIDE SPEC (layout/title/bullets) summarizing the
    agent. render_deck.to_pptagent_payload() then maps these into the documented
    powerpoint Request Shape -- so the contract is render_deck's, not ours."""
    name = cfg.get("name", "agent")
    goal = cfg.get("goal", "")
    adv = cfg.get("adversarial", {}) or {}
    tools = cfg.get("tools", []) or []
    policies = cfg.get("policies", []) or []
    outs = cfg.get("outputs", []) or []

    slides = []
    # Slide 1: title / executive summary (section_id 0 -- render_deck requires it).
    slides.append({
        "layout": "title",
        "title": "Agent: %s" % name,
        "subtitle": "Goal: %s" % goal,
        "bullets": [
            "Runtime: %s" % cfg.get("runtime", "(unset)"),
            "Model: %s" % cfg.get("model", "(unset)"),
            "Research foundation: %s" % cfg.get("research_foundation", "(none)"),
        ],
        "section_id": 0,
    })
    slides.append({
        "layout": "content", "section_id": 1, "title": "Tools (MCP)",
        "bullets": tools or ["(none wired -- minimal attack surface)"],
    })
    slides.append({
        "layout": "content", "section_id": 2, "title": "Policies",
        "bullets": policies or ["(no declared policies)"],
    })
    slides.append({
        "layout": "content", "section_id": 3, "title": "Adversarial hardening",
        "bullets": [
            "Self-adversarial critic: %s"
            % ("on" if adv.get("self_adversarial", True) else "off"),
            "AI-detection publish sublayer: %s"
            % ("on" if adv.get("ai_detection", False) else "off"),
            "Required consecutive clean rounds: %s"
            % adv.get("required_consecutive_passes", 2),
            "Max rounds: %s" % adv.get("max_rounds", 6),
        ],
    })
    slides.append({
        "layout": "content", "section_id": 4, "title": "Outputs (a-la-carte)",
        "bullets": [str(o) for o in outs] or ["(none declared)"],
    })
    return slides


def build_pptx_payload(cfg: dict, agent_root: str) -> dict:
    """Return the powerpoint Request Shape payload summarizing the agent.

    Mirrors render_deck.py: builds a slide spec, then calls render_deck's
    to_pptagent_payload() to conform to the documented contract EXACTLY. Falls
    back to an identical-shaped local builder only if diligence-deck is not
    co-located (keeps the packager runnable in isolation)."""
    title = "Agent summary: %s" % cfg.get("name", "agent")
    slides = _agent_summary_slides(cfg, agent_root)
    prompt = (
        "Render a concise summary deck titled %r of this built agent: its goal, "
        "model, tools, policies, adversarial hardening, and a-la-carte outputs. "
        "Slide 1 (section_id 0) is the title/overview and MUST lead. Use one "
        "slide per entry in `findings`." % title
    )
    rd = _load_render_deck()
    if rd is not None and hasattr(rd, "to_pptagent_payload"):
        # Reuse render_deck's shape mapping + 30-slide cap, then override the
        # prompt: to_pptagent_payload hardcodes a due-diligence prompt that does
        # not describe an agent-summary deck. Same keys -> same contract.
        payload = rd.to_pptagent_payload(slides, title)
        payload["prompt"] = prompt
        return payload
    # Fallback: same keys as render_deck.to_pptagent_payload (no new contract).
    findings = []
    for s in slides:
        entry = {"section_id": s.get("section_id"), "layout": s["layout"],
                 "headline": s["title"], "bullets": s.get("bullets", [])}
        if s.get("subtitle"):
            entry["subtitle"] = s["subtitle"]
        findings.append(entry)
    return {
        "prompt": prompt,
        "slide_count": len(findings),
        "title": title,
        "findings": findings,
        "attachments": [],
        "template_path": None,
        "theme": {},
    }


def pptx_emit_args(payload: dict, filename: str) -> dict:
    """The --emit emit-args path: the argument dict the runtime passes to
    `emit_artifact` after rendering the payload into .pptx bytes.

    Mirrors the powerpoint skill's Artifact Emission contract exactly:
    kind=slides, content_encoding=base64, filename, mime_type, destinations.
    The bytes themselves are produced by the runtime powerpoint tool from
    `request`; we emit the request + the emit_artifact envelope, not the bytes
    (no python-pptx required, just like render_deck --emit payload)."""
    return {
        "tool": "emit_artifact",
        "args": {
            "kind": "slides",
            "content_encoding": "base64",
            "content": None,  # runtime fills with base64(.pptx) from `request`
            "filename": filename,
            "mime_type": ("application/vnd.openxmlformats-officedocument."
                          "presentationml.presentation"),
            "destinations": ["ui", "chat"],
        },
        "request": payload,  # the powerpoint Request Shape to render first
    }


# --------------------------------------------------------------------------- #
# monitoring pointer (B3 hand-off -- reference, do not build)
# --------------------------------------------------------------------------- #

def build_monitoring(cfg: dict) -> tuple:
    """Return (monitoring.json dict, MONITORING.md text): a POINTER to where this
    agent's observability lives. B5 references B3; it does not implement it."""
    name = cfg.get("name", "agent")
    project = "rockie-agent-%s" % name
    mon = {
        "provider": "langfuse",
        "scope": "per-agent",
        "project": project,
        "agent": name,
        "goal": cfg.get("goal", ""),
        "owner": "agent-builder/B3-observability",
        "note": ("Pointer only. Per-agent Langfuse project provisioned by the "
                 "B3 observability hand-off; this bundle references it, it does "
                 "not create the project."),
        "dashboards": {
            "traces": "langfuse://%s/traces" % project,
            "scores": "langfuse://%s/scores" % project,
        },
    }
    md = (
        "# Monitoring: %s\n\n"
        "Ongoing monitoring for this agent is provided by the B3 observability "
        "hand-off as a **per-agent** project. This file is a POINTER; the B5 "
        "packager references B3, it does not build it.\n\n"
        "- **Provider:** Langfuse\n"
        "- **Project:** `%s`\n"
        "- **Traces:** `langfuse://%s/traces`\n"
        "- **Scores:** `langfuse://%s/scores`\n\n"
        "See `monitoring.json` for the machine-readable pointer.\n"
        % (name, project, project, project)
    )
    return mon, md


# --------------------------------------------------------------------------- #
# repo copy + tarball (stdlib tarfile)
# --------------------------------------------------------------------------- #

def _tar_filter(tarinfo):
    base = os.path.basename(tarinfo.name)
    if base in ("__pycache__", ".DS_Store") or base.endswith(".pyc"):
        return None
    return tarinfo


def tar_repo(agent_root: str, out_tar: str) -> None:
    arc = os.path.basename(os.path.abspath(agent_root.rstrip("/"))) or "agent"
    with tarfile.open(out_tar, "w:gz") as tf:
        tf.add(agent_root, arcname=arc, filter=_tar_filter)


# --------------------------------------------------------------------------- #
# the packager
# --------------------------------------------------------------------------- #

def package(agent_root: str, selection: list, out_dir: str) -> dict:
    """Assemble exactly `selection` into out_dir; return the OUTPUTS_MANIFEST."""
    if not os.path.isdir(agent_root):
        raise FileNotFoundError("agent repo not found: %s" % agent_root)
    cfg_path = os.path.join(agent_root, "agent.config.json")
    if not os.path.exists(cfg_path):
        raise FileNotFoundError(
            "%s is not a scaffolded agent (no agent.config.json)" % agent_root)
    cfg = _read_json(cfg_path)
    name = cfg.get("name", os.path.basename(os.path.abspath(agent_root)))
    os.makedirs(out_dir, exist_ok=True)

    artifacts = []  # manifest entries: {output, path(s), sha256, ...}

    def record(output, rel, sha=None):
        full = os.path.join(out_dir, rel)
        artifacts.append({
            "output": output,
            "path": rel,
            "sha256": sha if sha is not None else _sha256_file(full),
        })

    # Build the verbatim surface once -- techdoc embeds it; verify re-checks it.
    verbatim = collect_verbatim(agent_root)

    if "repo" in selection:
        tarname = "%s.repo.tar.gz" % name
        tar_repo(agent_root, os.path.join(out_dir, tarname))
        record("repo", tarname)

    if "techdoc" in selection:
        doc = render_techdoc(agent_root, cfg, verbatim)
        rel = "TECHNICAL_DOCUMENTATION.md"
        _write(os.path.join(out_dir, rel), doc)
        entry = {"output": "techdoc", "path": rel, "sha256": _sha256_text(doc),
                 "verbatim_files": [{"path": v["path"], "sha256": v["sha256"]}
                                    for v in verbatim]}
        artifacts.append(entry)

    if "pptx" in selection:
        payload = build_pptx_payload(cfg, agent_root)
        prel = "%s.pptx.request.json" % name
        ptext = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        _write(os.path.join(out_dir, prel), ptext)
        record("pptx", prel, _sha256_text(ptext))
        emit = pptx_emit_args(payload, "%s.pptx" % name)
        erel = "%s.pptx.emit-args.json" % name
        etext = json.dumps(emit, indent=2, ensure_ascii=False) + "\n"
        _write(os.path.join(out_dir, erel), etext)
        record("pptx", erel, _sha256_text(etext))

    if "af" in selection:
        # REUSE B1's af_export (no duplication).
        af = af_export.export_agent(agent_root)
        af_export.validate_af(af)
        rel = "%s.af" % name
        af_export.write_af(af, os.path.join(out_dir, rel))
        record("af", rel)

    if "policies" in selection:
        pol_dir = os.path.join(out_dir, "policies-bundle")
        os.makedirs(pol_dir, exist_ok=True)
        bundled = []
        for sub in ("policies", os.path.join(".claude", "hooks")):
            src = os.path.join(agent_root, sub)
            if not os.path.isdir(src):
                continue
            for dirpath, _dirs, files in os.walk(src):
                for fn in sorted(files):
                    if fn.endswith(".pyc") or fn == ".DS_Store" or fn == ".gitkeep":
                        continue
                    full = os.path.join(dirpath, fn)
                    rel_in = os.path.relpath(full, agent_root)
                    dest = os.path.join(pol_dir, rel_in)
                    _write(dest, _read(full))
                    bundled.append({"path": os.path.join("policies-bundle", rel_in),
                                    "sha256": _sha256_file(dest)})
        artifacts.append({"output": "policies", "path": "policies-bundle",
                          "files": bundled,
                          "sha256": _sha256_text(json.dumps(bundled, sort_keys=True))})

    if "monitoring" in selection:
        mon, md = build_monitoring(cfg)
        mtext = json.dumps(mon, indent=2) + "\n"
        _write(os.path.join(out_dir, "monitoring.json"), mtext)
        _write(os.path.join(out_dir, "MONITORING.md"), md)
        record("monitoring", "monitoring.json", _sha256_text(mtext))
        record("monitoring", "MONITORING.md", _sha256_text(md))

    manifest = {
        "outputs_manifest_version": "1.0",
        "agent": name,
        "goal": cfg.get("goal", ""),
        "selection": selection,
        "artifacts": artifacts,
    }
    man_text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    _write(os.path.join(out_dir, "OUTPUTS_MANIFEST.json"), man_text)
    return manifest


# --------------------------------------------------------------------------- #
# verify: the word-for-word drift check
# --------------------------------------------------------------------------- #

def verify(bundle_dir: str, agent_root: str) -> list:
    """Re-hash the LIVE repo's verbatim surfaces and assert the techdoc's
    embedded sha256s still match. Returns a list of drift messages (empty == ok).

    Proof of the word-for-word guarantee: the tech doc claims a sha256 per file;
    verify recomputes the hash from the current repo file and from the bytes the
    doc embedded, and fails loudly on any mismatch or missing file."""
    problems = []
    man_path = os.path.join(bundle_dir, "OUTPUTS_MANIFEST.json")
    if not os.path.exists(man_path):
        return ["no OUTPUTS_MANIFEST.json in %s" % bundle_dir]
    manifest = _read_json(man_path)

    techdoc_entry = next(
        (a for a in manifest.get("artifacts", []) if a.get("output") == "techdoc"),
        None)
    if techdoc_entry is None:
        return ["bundle has no techdoc artifact to verify against"]

    recorded = {v["path"]: v["sha256"]
                for v in techdoc_entry.get("verbatim_files", [])}
    if not recorded:
        return ["techdoc manifest entry records no verbatim_files"]

    # 1) live repo must still match the recorded hashes (no drift since packaging)
    live = {v["path"]: v["sha256"] for v in collect_verbatim(agent_root)}
    for rel, sha in recorded.items():
        if rel not in live:
            problems.append("MISSING in live repo: %s" % rel)
        elif live[rel] != sha:
            problems.append("DRIFT: %s (doc=%s live=%s)" % (rel, sha, live[rel]))
    for rel in live:
        if rel not in recorded:
            problems.append("NEW in live repo, absent from doc: %s" % rel)

    # 2) the doc's EMBEDDED copy must itself hash to the recorded value -- proves
    #    the verbatim text in the doc is byte-identical to what was hashed.
    doc_path = os.path.join(bundle_dir, techdoc_entry["path"])
    if not os.path.exists(doc_path):
        problems.append("techdoc file missing: %s" % techdoc_entry["path"])
    else:
        doc = _read(doc_path)
        for rel, sha in recorded.items():
            embedded = _extract_embedded(doc, rel)
            if embedded is None:
                problems.append("doc does not embed: %s" % rel)
            elif _sha256_text(embedded) != sha:
                problems.append("EMBEDDED COPY MISMATCH: %s "
                                "(embedded copy does not hash to recorded sha)" % rel)
    return problems


def _extract_embedded(doc: str, rel: str) -> str:
    r"""Pull the verbatim block for `rel` back out of TECHNICAL_DOCUMENTATION.md.

    The doc writes `### \`<rel>\`` then a fenced block holding the file content
    BYTE-FOR-BYTE, with a single \n inserted before the closing fence (markdown
    needs the closer on its own line). We strip exactly that one \n so the
    extracted text is the file content and re-hashes to the recorded sha."""
    header = "### `%s`" % rel
    idx = doc.find(header)
    if idx < 0:
        return None
    rest = doc[idx + len(header):]
    # find opening fence (a run of >=3 backticks, possibly with a lang hint)
    fence_start = rest.find("```")
    if fence_start < 0:
        return None
    line_end = rest.find("\n", fence_start)
    fence = rest[fence_start:line_end]
    n = 0
    for ch in fence:
        if ch == "`":
            n += 1
        else:
            break
    closing = "\n" + ("`" * n)
    body_start = line_end + 1
    close_idx = rest.find(closing, body_start)
    if close_idx < 0:
        return None
    body = rest[body_start:close_idx]
    # render_techdoc embedded content.rstrip("\n"); compare on that basis.
    return body


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #

def _selftest() -> int:
    import tempfile

    failures = []

    def chk(name, cond):
        print("  [%s] %s" % ("ok" if cond else "FAIL", name))
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as td:
        # Scaffold a representative agent (REUSE the B1 scaffolder).
        cfg = scaffold_agent.default_config(
            "Summarize weekly support tickets into a themed digest")
        cfg["tools"] = ["zendesk-mcp"]
        cfg["policies"] = ["deny:DROP TABLE", "Never email a customer directly"]
        cfg["outputs"] = ["markdown", "slides"]
        repo = os.path.join(td, "agent")
        scaffold_agent.scaffold(cfg, repo)

        # Package a representative selection (one of every deliverable kind).
        out = os.path.join(td, "bundle")
        sel = ALL_OUTPUTS[:]
        manifest = package(repo, sel, out)

        # every selected artifact exists on disk
        chk("repo tarball exists",
            os.path.exists(os.path.join(out, "%s.repo.tar.gz" % cfg["name"])))
        chk("techdoc exists",
            os.path.exists(os.path.join(out, "TECHNICAL_DOCUMENTATION.md")))
        chk("pptx request exists",
            os.path.exists(os.path.join(out, "%s.pptx.request.json" % cfg["name"])))
        chk("pptx emit-args exists",
            os.path.exists(os.path.join(out, "%s.pptx.emit-args.json" % cfg["name"])))
        chk(".af exists", os.path.exists(os.path.join(out, "%s.af" % cfg["name"])))
        chk("policies bundle exists",
            os.path.isdir(os.path.join(out, "policies-bundle")))
        chk("monitoring.json exists",
            os.path.exists(os.path.join(out, "monitoring.json")))
        chk("OUTPUTS_MANIFEST.json exists",
            os.path.exists(os.path.join(out, "OUTPUTS_MANIFEST.json")))

        # manifest is internally consistent: every artifact path resolves + hashes
        consistent = True
        for a in manifest["artifacts"]:
            p = os.path.join(out, a["path"])
            if not os.path.exists(p):
                consistent = False
            elif os.path.isfile(p) and _sha256_file(p) != a["sha256"]:
                consistent = False
        chk("manifest paths + hashes consistent", consistent)
        chk("manifest selection == requested", manifest["selection"] == sel)

        # the .af actually validates (reuse of af_export is real, not faked)
        af = _read_json(os.path.join(out, "%s.af" % cfg["name"]))
        chk(".af validates (af_export reuse)", af.get("af_kind") == af_export.AF_KIND)

        # the pptx payload conforms to the powerpoint Request Shape
        payload = _read_json(os.path.join(out, "%s.pptx.request.json" % cfg["name"]))
        chk("pptx payload has Request-Shape keys",
            set(["prompt", "slide_count", "title", "findings",
                 "attachments", "template_path", "theme"]).issubset(payload))
        chk("pptx slide_count matches findings",
            payload["slide_count"] == len(payload["findings"]))
        emit = _read_json(os.path.join(out, "%s.pptx.emit-args.json" % cfg["name"]))
        chk("emit-args is an emit_artifact call",
            emit.get("tool") == "emit_artifact"
            and emit["args"]["kind"] == "slides"
            and emit["args"]["content_encoding"] == "base64")

        # techdoc embeds the self-critic SKILL verbatim + records its hash
        doc = _read(os.path.join(out, "TECHNICAL_DOCUMENTATION.md"))
        live = {v["path"]: v for v in collect_verbatim(repo)}
        skill_rel = ".claude/skills/self-critic/SKILL.md"
        chk("techdoc covers the self-critic SKILL.md", skill_rel in live)
        # the "prompts" half of the guarantee: a prompt the agent actually runs
        # (the self-critic's domain-critic.md) must be embedded verbatim too.
        prompt_rel = ".claude/skills/self-critic/domain-critic.md"
        chk("techdoc covers the self-critic prompt (domain-critic.md)",
            prompt_rel in live)
        chk("techdoc covers CLAUDE.md", "CLAUDE.md" in live)
        chk("techdoc covers a policy file",
            any(p.startswith("policies/") for p in live))
        if skill_rel in live:
            chk("self-critic SKILL embedded byte-for-byte",
                live[skill_rel]["content"].rstrip("\n") in doc)

        # --verify passes on the freshly packaged, undrifted bundle
        problems = verify(out, repo)
        chk("verify passes on undrifted bundle", problems == [])

        # --verify FAILS LOUDLY when the live repo drifts (the whole point)
        with open(os.path.join(repo, "CLAUDE.md"), "a", encoding="utf-8") as fh:
            fh.write("\n<!-- drift injected by selftest -->\n")
        drift = verify(out, repo)
        chk("verify detects drift after editing CLAUDE.md",
            any("CLAUDE.md" in d and "DRIFT" in d for d in drift))

        # config-driven selection (no --include): "slides"->pptx, "markdown"->techdoc
        out2 = os.path.join(td, "bundle2")
        sel2 = resolve_selection(None, cfg)
        chk("config outputs map slides->pptx", "pptx" in sel2)
        chk("config outputs map markdown->techdoc", "techdoc" in sel2)
        package(repo, sel2, out2)
        chk("config-driven bundle has a manifest",
            os.path.exists(os.path.join(out2, "OUTPUTS_MANIFEST.json")))

    print("\n" + ("ALL SELFTESTS PASSED" if not failures
                  else "SELFTEST FAILURES: %s" % failures))
    return 0 if not failures else 1


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--agent", help="path to a scaffolded agent repo to package")
    p.add_argument("--include",
                   help="comma list of deliverables (%s); "
                        "default = derived from agent.config.json outputs"
                        % ",".join(ALL_OUTPUTS))
    p.add_argument("--out", help="output bundle dir")
    p.add_argument("--verify", nargs=2, metavar=("BUNDLE", "REPO"),
                   help="re-hash the live repo and assert the techdoc has not drifted")
    p.add_argument("--selftest", action="store_true",
                   help="scaffold a sample, package, assert artifacts + manifest, verify")
    args = p.parse_args(argv)

    if args.selftest:
        return _selftest()

    if args.verify:
        bundle, repo = args.verify
        problems = verify(bundle, repo)
        if problems:
            sys.stderr.write("VERIFY FAILED -- word-for-word drift detected:\n")
            for d in problems:
                sys.stderr.write("  - %s\n" % d)
            return 1
        print("VERIFY OK: every documented file matches the live repo byte-for-byte")
        return 0

    if not args.agent:
        p.print_help()
        return 0
    if not args.out:
        p.error("--out <dir> is required when packaging")

    try:
        cfg = _read_json(os.path.join(args.agent, "agent.config.json"))
    except FileNotFoundError:
        p.error("%s is not a scaffolded agent (no agent.config.json)" % args.agent)
    try:
        selection = resolve_selection(args.include, cfg)
    except ValueError as e:
        p.error(str(e))
    if not selection:
        p.error("nothing to package: empty selection (use --include or set "
                "agent.config.json outputs)")

    try:
        manifest = package(args.agent, selection, args.out)
    except (FileNotFoundError, ValueError) as e:
        p.error(str(e))

    print("packaged '%s' -> %s" % (manifest["agent"], os.path.abspath(args.out)))
    print("  selection: %s" % ", ".join(manifest["selection"]))
    print("  artifacts: %d" % len(manifest["artifacts"]))
    print("  manifest:  OUTPUTS_MANIFEST.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
