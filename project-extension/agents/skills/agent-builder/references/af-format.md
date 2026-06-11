# af-format — the `.af` agent-file serialization schema

A `.af` file is a single JSON document that serializes a built agent for handover:
enough to reconstruct or import the agent elsewhere. It is the Letta-style
agent-file format — one self-describing manifest, not a tarball. `scripts/
af_export.py` writes it; `af_export.py --reconstruct --af <file>` rebuilds a repo
from it.

File contents are **embedded**, not merely referenced, so a `.af` is a true
handover artifact: you can rebuild the entire portable repo
(`references/agent-repo-anatomy.md`) from the `.af` alone, with no access to the
original repo or the agent-builder.

## Top-level schema

```json
{
  "af_kind": "letta.agentfile",          // fixed discriminator
  "af_schema_version": "1.0",
  "name": "release-notes-writer",        // from agent.config.json
  "goal": "ONE testable sentence",       // the declared goal, hoisted for quick read
  "config": { ... },                     // the full agent.config.json, verbatim
  "manifest": { ... },                   // the full manifest.json, verbatim (or null)
  "constitution": "<CLAUDE.md contents>",// the constitution, embedded as a string
  "mcp": { "mcpServers": { ... } },      // the full .mcp.json, verbatim
  "policies": ["...", "deny:..."],       // the declared policy statements (from config)
  "skills":       [ {"path": "...", "content": "..."}, ... ],  // .claude/skills/** embedded
  "hooks":        [ {"path": "...", "content": "..."}, ... ],  // .claude/hooks/** embedded
  "policy_files": [ {"path": "...", "content": "..."}, ... ],  // policies/** embedded
  "tests":        [ {"path": "...", "content": "..."}, ... ]   // tests/** embedded
}
```

## Field semantics

- **af_kind** — always `letta.agentfile`. A reader rejects any other value
  (`validate_af`).
- **af_schema_version** — bump on a breaking schema change.
- **name / goal** — hoisted from the config for a quick scan; `config` carries the
  authoritative copies.
- **config** — the complete, verbatim `agent.config.json`. This is the
  config-over-code half of the agent; on reconstruct it is written back unchanged.
- **manifest** — the complete `manifest.json`, or `null` if the source repo had
  none.
- **constitution** — the `CLAUDE.md` text, embedded. On reconstruct it becomes
  `CLAUDE.md`.
- **mcp** — the complete `.mcp.json` (tool wiring), embedded.
- **policies** — the declared policy STATEMENTS (the strings from
  `config.policies`); the rendered `policies/*.md` files live under `policy_files`.
- **skills / hooks / policy_files / tests** — each is a list of
  `{path, content}` pairs. `path` is RELATIVE to the repo root (e.g.
  `.claude/skills/self-critic/critic_loop.py`), so reconstruction recreates the
  identical layout. Binary/junk files (`*.pyc`, `.DS_Store`) are skipped.

## What is embedded vs. referenced

| Captured in the `.af` | How |
|---|---|
| Declared goal | `goal` + `config.goal` |
| Constitution (CLAUDE.md) | `constitution` (string) |
| Config (goal/model/tools/policies/adversarial) | `config` (object) |
| Manifest | `manifest` (object) |
| MCP / tool wiring | `mcp` (object) |
| Policy statements | `policies` (list) |
| Skill files (incl. the self-critic) | `skills` (path+content list) |
| Hook files (safeguards) | `hooks` (path+content list) |
| Policy files | `policy_files` (path+content list) |
| Proven-execution tests | `tests` (path+content list) |

## Round-trip guarantee

`af_export.py --selftest` scaffolds a real sample agent, exports it, reloads the
`.af`, reconstructs a repo from it, and asserts the key files are byte-for-byte
identical — then runs the RECONSTRUCTED agent's shipped self-critic engine
`--selftest` to prove the rebuilt agent is not just present but functional. Export
→ reconstruct → run is the portability proof: the `.af` is a faithful, executable
serialization, not a lossy summary.
