# outputs-catalog — the a-la-carte deliverables + the verbatim-confirmation contract

Step [9] EXPORT of the lifecycle is **a-la-carte outputs**: once an agent repo is
scaffolded (and ideally publish-gate-passed), the customer picks any SUBSET of
deliverables and `scripts/package_outputs.py` assembles exactly that subset into a
bundle dir, each artifact content-hashed in a top-level `OUTPUTS_MANIFEST.json`.

```
package_outputs.py --agent <repo> --include repo,techdoc,pptx,af,policies,monitoring --out <dir>
package_outputs.py --agent <repo> --out <dir>     # selection derived from agent.config.json "outputs"
package_outputs.py --verify <bundle> <repo>       # word-for-word drift check
package_outputs.py --selftest
```

The selection comes from `--include` (wins when present) or, absent that, is
derived from the agent's `agent.config.json` `outputs` list
(`slides`/`deck`->`pptx`, `markdown`/`report`->`techdoc`, etc.); `techdoc` is
always added so every bundle records what it contains.

## The deliverable catalog

| Output | Artifact(s) | Shape |
|---|---|---|
| `repo` | `<name>.repo.tar.gz` | A portable tarball of the whole agent repo (stdlib `tarfile`, `__pycache__`/`*.pyc`/`.DS_Store` filtered). |
| `techdoc` | `TECHNICAL_DOCUMENTATION.md` | The agent's CLAUDE.md, skills, prompts, and policies reproduced **word-for-word** with a sha256 per file. See the contract below. |
| `pptx` | `<name>.pptx.request.json` + `<name>.pptx.emit-args.json` | A powerpoint Request Shape payload summarizing the agent, plus an `emit_artifact` call for the runtime. Mirrors `diligence-deck/render_deck.py`. |
| `af` | `<name>.af` | The Letta-style agent-file. Produced by **importing** B1's `af_export.py` — not re-authored. |
| `policies` | `policies-bundle/` | The agent's `policies/` + `.claude/hooks/` copied verbatim (the safeguards bundle). |
| `monitoring` | `monitoring.json` + `MONITORING.md` | A POINTER to where this agent's per-agent Langfuse/observability lives (the B3 hand-off). References B3; builds nothing. |
| _(always)_ | `OUTPUTS_MANIFEST.json` | Lists `selection`, and every artifact with its `sha256` (the techdoc entry also lists each verbatim file + its hash). |

## The PowerPoint deliverable (reuse, not reinvention)

The `pptx` output does NOT invent a deck format. It builds a render_deck-style
slide spec (title/overview slide + one slide each for tools, policies, adversarial
hardening, outputs) and hands it to `render_deck.to_pptagent_payload()`, so the
emitted `*.pptx.request.json` conforms EXACTLY to the documented `powerpoint` skill
Request Shape: `{prompt, slide_count, title, findings, attachments, template_path,
theme}` (the 30-slide cap is render_deck's too). Only the `prompt` string is
overridden, because render_deck's hardcoded prompt describes a due-diligence deck,
not an agent summary — the KEYS (the contract) are unchanged.

`*.pptx.emit-args.json` is the `--emit emit-args` path: the argument dict the
runtime passes to `emit_artifact` after rendering the request into `.pptx` bytes.
It mirrors the powerpoint skill's Artifact Emission contract exactly —
`kind: "slides"`, `content_encoding: "base64"`, `filename`, `mime_type`
(`application/vnd.openxmlformats-officedocument.presentationml.presentation`),
`destinations: ["ui", "chat"]` — with `content: null` for the runtime to fill from
`request`. No `python-pptx` is required (just like render_deck `--emit payload`):
the packager emits the request + the emission envelope, the runtime renders the
bytes.

## The word-for-word verbatim confirmation contract (Sam-critical)

> The technical documentation reproduces the agent's skills and prompts
> **byte-for-byte** — the SAME text deployed in the agent repo — so the customer
> sees precisely what their agent is made of.

**The verbatim surface.** `TECHNICAL_DOCUMENTATION.md` embeds, verbatim, the
contents of every:

- `CLAUDE.md` (the constitution),
- `.claude/skills/**/*.md` (every skill's SKILL.md AND the prompts it ships,
  e.g. the self-critic's `domain-critic.md` — in the config-over-code layout an
  agent's prompts live inside its skills),
- `prompts/**/*.md` (any top-level goal prompts the repo ships),
- `policies/**` (every declared policy file).

Each file's exact contents are placed inside a fenced block (the fence length is
auto-chosen longer than any backtick run in the content, so a file that itself
contains ``` cannot break the doc), and a `sha256` of the file is recorded both
inline in the doc and in `OUTPUTS_MANIFEST.json` (`techdoc.verbatim_files`).

**The integrity check (`--verify`).** `package_outputs.py --verify <bundle> <repo>`
proves the guarantee in two independent ways and **fails loudly** (non-zero exit,
itemized drift list) if either breaks:

1. **No drift since packaging.** It re-hashes the LIVE repo's verbatim surfaces and
   asserts each still matches the sha recorded in the manifest. A `MISSING`,
   `DRIFT`, or `NEW` line is emitted for any file that disappeared, changed, or
   appeared since the bundle was built.
2. **The embedded copy is faithful.** It re-extracts each file's block back out of
   `TECHNICAL_DOCUMENTATION.md` and asserts THAT text hashes to the recorded sha.
   This proves the words printed in the doc are byte-identical to the words that
   were hashed (the doc cannot silently misquote the agent).

Because both the live file and the doc's embedded copy must hash to the same
recorded value, "the doc says exactly what the agent is" is a checkable invariant,
not a claim. Edit one byte of any skill/prompt/policy/CLAUDE.md after packaging and
`--verify` exits 1 naming the file.

## OUTPUTS_MANIFEST.json

```json
{
  "outputs_manifest_version": "1.0",
  "agent": "release-notes-writer",
  "goal": "…",
  "selection": ["techdoc", "pptx", "af", "policies", "monitoring"],
  "artifacts": [
    {"output": "techdoc", "path": "TECHNICAL_DOCUMENTATION.md", "sha256": "…",
     "verbatim_files": [{"path": "CLAUDE.md", "sha256": "…"}, …]},
    {"output": "pptx", "path": "release-notes-writer.pptx.request.json", "sha256": "…"},
    {"output": "af",   "path": "release-notes-writer.af", "sha256": "…"},
    …
  ]
}
```

Every artifact path resolves inside the bundle and re-hashes to its recorded
`sha256` (the `--selftest` asserts this consistency).
