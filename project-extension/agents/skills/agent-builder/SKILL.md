---
name: agent-builder
description: Build any goal-declared agent end to end. Turns one declared goal into a portable, self-hardening, publishable agent repo on the Claude-Code harness. Declare goal, pick runtime/tools and write policies/hooks/safeguards, scaffold a portable repo (config-over-code split), build, run a fresh no-memory adversarial gauntlet, iterate with the clean-pass counter resetting on any failed round, publish ONLY after passing twice with zero CRITICALs, then export a .af agent-file. Biased to emit agents that contain their own internal adversarial critic loop. Triggers on "build an agent", "make me an agent that", "agent builder", "scaffold an agent", "publish this agent", "export to .af", "/agent-builder".
---

# agent-builder — build any goal-declared agent

This skill is the meta-system that generalizes the proven diligence agent
("Atlas") into a reusable pipeline: declare a goal, and out comes a portable,
self-hardening, publishable agent. Atlas shipped to prod using the patterns this
skill reuses; agent-builder turns those patterns from "build one diligence agent"
into "build any agent". You build the builder, not the agent's output.

## Explicit goal

Take a user's declared goal ("an agent that does X") and produce a **portable git
repo** on the OpenClaw/Claude-Code harness that achieves it, ships its own
adversarial safeguards by default, survives a fresh no-memory publish gauntlet
twice with zero CRITICALs before it is allowed to ship, and serializes to a `.af`
agent-file for handover. Refuse to declare an agent publishable until the gate
says so.

## Lifecycle

```
[1] DECLARE   — resolve the goal to ONE testable sentence. Ask 2-3 clarifying Qs
                only if it is too vague to test. Write it to agent.config.json.
[2] CHOOSE    — pick runtime/model/tools + write policies/hooks/safeguards.
                The decision table below drives every choice from the goal.
[3] SCAFFOLD  — scripts/scaffold_agent.py emits the portable repo (CLAUDE.md,
                .claude/skills/ incl. the self-adversarial critic BY DEFAULT,
                .claude/hooks/, .mcp.json, policies/, tests/, manifest).
[4] BUILD     — flesh out the domain skills/prompts so it achieves the goal;
                keep the self-critic wired.
[5] RUN       — exercise the agent; produce a sample deliverable.
[6] GATE      — scripts/publish_gate.py: a fresh no-memory adversarial gauntlet.
                quality sublayer ALWAYS; ai-detection sublayer when opted in.
                Counter resets on any failed round.
[7] ITERATE   — apply the cited fixes; re-run. One pass never unlocks.
[8] PUBLISH   — ONLY after two consecutive zero-CRITICAL rounds (publishable:true).
[9] EXPORT    — scripts/af_export.py emits the .af handover artifact.
```

The publish gate (step 6) is the conceptual heart. It reuses the `paper` skill's
adversarial gauntlet mechanics (Attack to Defense to Rebuttal to Style to Format;
CRITICAL blocks termination; two consecutive clean rounds; degraded
single-process fallback with hard context reset) by IMPORTING the proven engine
from `diligence-deck/scripts/critic_loop.py` rather than re-authoring it. See
`references/publish-gate.md` and `../paper/references/adversarial-gauntlet.md`; do
not duplicate that engine here.

## When to invoke

- "Build me an agent that does X" / "scaffold an agent for X" — run the full
  lifecycle.
- "Publish this agent" / "is this agent ready to ship" — run the gate (step 6) on
  an existing scaffolded repo and report the verdict.
- "Export this agent to a .af" — run the export (step 9).

When NOT to invoke: when the user wants the TASK done (run diligence, write a
paper), reach for that domain skill. agent-builder builds the agent that would do
the task; it does not do the task.

## The routing / decision table

The builder turns the declared goal into config choices. Defaults are the common
case; deviate only with a reason.

| Decision | Default | Deviate when |
|---|---|---|
| **runtime** | `fly-machine:kind=agent` (a 2nd Fly machine of kind=agent, per spec clarification #2) | The declarer names another harness. |
| **model** | `claude-opus-4-8` | A narrow mechanical goal can use a cheaper model. |
| **research foundation** | `gpt-researcher` | The goal needs deep multi-source research, wire GPT-Researcher; a shallow goal wires none. |
| **tools (MCP)** | none | Wire exactly the MCP servers the goal requires. Over-wiring is attack surface. |
| **policies** | none | Encode the declarer's must-nevers; `deny:<substring>` for blockable rules, prose for judgment rules. |
| **self_adversarial** | **true** | Leave ON unless the declarer explicitly opts out. The builder is biased to emit agents that contain their own critic loop. |
| **ai_detection** (publish sublayer) | **false** | Turn ON only when the declarer cares the output reads as human-authored. SKIP when output is understood to be AI-generated. |
| **outputs** | none | Add the a-la-carte deliverable kinds the declarer asked for (e.g. `slides`, `markdown`, `report`). |
| **battleground hand-off** | not invoked | The declarer wants the model picked by a bake-off, hand off to `diligence-deck/scripts/battleground.py` semantics; record the winner in `model`. |

## The portable repo (config-over-code)

The scaffold emits a stable HARNESS plus a versioned per-customer CONFIG, the
LangGraph-Assistants split: the harness (`CLAUDE.md`, `.claude/skills/`,
`.claude/hooks/`, `tests/`) is shared code; `agent.config.json` (goal, model,
tools, policies, adversarial settings, outputs) is what differs per customer. To
re-target an agent you change the config and re-scaffold, never rewrite the
harness. Full layout and schema: `references/agent-repo-anatomy.md`.

## Biased to emit self-adversarial agents

By DEFAULT the scaffold wires the generated agent with its OWN internal
adversarial critic loop, exactly like Atlas's partner-critic: every emitted agent
ships `.claude/skills/self-critic/` containing a `critic_loop.py` (a self-contained
copy of the proven engine, lineage noted in its docstring) plus a goal-scoped
`domain-critic.md`. The agent hardens its own deliverables (two consecutive clean
rounds) before returning them. The declarer opts out with `--no-self-adversarial`;
then the repo shape stays stable but no self-critic ships. This is the spec's bias
toward emitting agents that contain their own adversarial flows.

## The publish gate contract

> IF the built agent has not passed a fresh, no-memory adversarial gauntlet
> **twice with zero CRITICALs**, the system SHALL refuse to mark it publishable.

`scripts/publish_gate.py` enforces it and emits a verdict JSON
(`publishable`, `converged`, `rounds`, `consecutive_clean`, `sublayers_run`,
`reason`, `round_log`). It is **publishable iff `publishable: true`**. The gate is
**double-layered**:

- **quality** sublayer ALWAYS runs: does the agent achieve its declared goal, are
  its tools/prompts/policies sound, is its reasoning trace defensible?
  (`prompts/quality-critic.md`).
- **ai-detection** sublayer is OPT-IN: only when the declarer sets
  `adversarial.ai_detection: true`. Judges whether output reads human-authored;
  skipped when output is understood to be AI-generated
  (`prompts/ai-detection-critic.md`).

Each sublayer runs each round as a fresh no-memory critic; a round passes only if
every enabled sublayer is CRITICAL-clean. The clean-pass counter resets to zero on
any failed round; convergence needs two consecutive clean rounds; the cap is
`MAX_ROUNDS = 6` and a non-converging gate FAILS LOUDLY for human review. Both
subagent and single-process modes are supported, identical termination rules. Full
contract: `references/publish-gate.md`.

## `.af` export

Once publishable, `scripts/af_export.py` serializes the repo to a `.af`
(Letta-style agent-file): a single JSON manifest embedding the constitution,
config, skills, hooks, MCP wiring, and policy declarations, enough to reconstruct
or import the agent elsewhere. The format and the round-trip guarantee are in
`references/af-format.md`.

## A-la-carte outputs

The same step [9] EXPORT also packages the agent's **a-la-carte deliverables**:
the customer picks any SUBSET and `scripts/package_outputs.py` assembles exactly
that subset into a bundle dir, each artifact content-hashed in a top-level
`OUTPUTS_MANIFEST.json`. The catalog (pick any subset):

| Output | Artifact(s) | Notes |
|---|---|---|
| `repo` | `<name>.repo.tar.gz` | portable tarball of the whole repo (stdlib `tarfile`). |
| `techdoc` | `TECHNICAL_DOCUMENTATION.md` | skills/prompts/policies/CLAUDE.md **word-for-word** + a sha256 each. |
| `pptx` | `*.pptx.request.json` + `*.pptx.emit-args.json` | powerpoint Request Shape + `emit_artifact` call; mirrors `diligence-deck/render_deck.py`. |
| `af` | `<name>.af` | the `.af`, produced by **calling** `af_export.py` (not re-authored). |
| `policies` | `policies-bundle/` | the agent's `policies/` + `.claude/hooks/`. |
| `monitoring` | `monitoring.json` + `MONITORING.md` | POINTER to the agent's per-agent Langfuse/observability (the B3 hand-off). |

```
scripts/package_outputs.py --agent <repo> --include repo,techdoc,pptx,af,policies,monitoring --out <dir>
scripts/package_outputs.py --agent <repo> --out <dir>   # selection from agent.config.json "outputs"
scripts/package_outputs.py --verify <bundle> <repo>     # word-for-word drift check
```

Absent `--include`, the selection is derived from the agent's `agent.config.json`
`outputs` list. The packager REUSES, never duplicates: it imports `af_export.py`
for the `.af`, and mirrors render_deck's documented powerpoint Request Shape
`{prompt, slide_count, title, findings, attachments, template_path, theme}` plus
the `emit_artifact` contract (`kind: "slides"`, `content_encoding: "base64"`, …).

### Word-for-word confirmation guarantee

The technical documentation reproduces the agent's skills and prompts
**byte-for-byte** — the SAME text deployed in the repo — so the customer sees
precisely what their agent is made of. Every `.claude/skills/**/SKILL.md`,
`prompts/**/*.md`, `policies/**`, and `CLAUDE.md` is embedded verbatim in fenced
blocks AND a sha256 of each is recorded. `package_outputs.py --verify <bundle>
<repo>` re-hashes the live repo files AND the doc's embedded copies and asserts
both still match the recorded hashes — failing loudly (exit 1, named files) on any
drift. Full contract: `references/outputs-catalog.md`.

## Routing

| Command | Intent | What it does |
|---|---|---|
| `/agent-builder` | "build an agent that X" | Full lifecycle: declare to export. |
| `/agent-builder scaffold` | "scaffold an agent for X" | `scripts/scaffold_agent.py` emits the portable repo. |
| `/agent-builder gate` | "is this agent ready", "publish this agent" | `scripts/publish_gate.py` runs the double-layered gauntlet; reports the verdict. |
| `/agent-builder export` | "export this agent to a .af" | `scripts/af_export.py` emits the `.af`. |
| `/agent-builder package` | "package these outputs", "give me the deliverables" | `scripts/package_outputs.py` assembles the selected a-la-carte deliverables. |

## Proven execution

Every script runs and self-tests deterministically (no model call, stdlib only):

```
scripts/scaffold_agent.py --selftest   # scaffolds into a temp dir, asserts the repo shape,
                                        # runs the shipped self-critic engine + tests
scripts/publish_gate.py    --selftest   # proves the gate logic: pass-twice, reset-on-fail,
                                        # ai-detection sublayer gating, opt-in skip, the cap
scripts/af_export.py       --selftest   # scaffolds a sample, exports, round-trips byte-for-byte,
                                        # runs the reconstructed agent's engine
scripts/package_outputs.py --selftest   # scaffolds a sample, packages every deliverable, asserts
                                        # each artifact + a consistent manifest, then --verify proves
                                        # the word-for-word doc matches (and detects injected drift)
```

`examples/` ships a curated sample: `agent.config.json` for a small real goal
(a release-notes writer), the scaffolded repo it produces, its `.af` export, and a
packaged a-la-carte bundle (`release-notes-writer.bundle/`).

## Rules

1. **Build the agent, do not do its job.** The deliverable is a repo + a passing
   gate verdict + a `.af`, not the agent's output.
2. **Bias to self-adversarial.** Ship the internal critic loop by default; opt out
   only on explicit request.
3. **Opt-in AI-detection only.** Never add the AI-detection sublayer unless the
   declarer asked for human-reading output.
4. **The gate is the ship gate.** No `.af`, no "done", until `publishable: true`.
   Never override a non-converging gate.
5. **Config over code.** Goal/model/tool/policy choices live in
   `agent.config.json`, not hardcoded in the harness.
6. **No invented capability.** Wire only tools the goal needs; declare only
   policies the declarer stated.
7. **Reuse, do not re-author the gauntlet.** The publish gate imports the proven
   `critic_loop` engine; only the emitted agent gets a self-contained copy
   (because it must be portable).

## File map

- `scripts/scaffold_agent.py` — declare a goal to a portable agent repo (CLAUDE.md,
  .claude/skills incl. the self-adversarial critic by default, .claude/hooks,
  .mcp.json, policies, tests, manifest). `--selftest` asserts the structure.
- `scripts/publish_gate.py` — the pass-twice double-layered publish gauntlet;
  imports the proven `critic_loop` engine (DRY). `--selftest` proves the logic.
- `scripts/af_export.py` — serialize a scaffolded repo to a `.af` agent-file;
  `--reconstruct` rebuilds a repo from one. `--selftest` round-trips a sample.
- `scripts/package_outputs.py` — assemble the a-la-carte deliverables (repo,
  techdoc, pptx, af, policies, monitoring) into a hashed bundle; reuses
  `af_export.py` + render_deck's powerpoint contract. `--verify` enforces the
  word-for-word guarantee; `--selftest` packages a sample and proves it.
- `prompts/builder.md` — the orchestrator: declared goal to runtime/tool/policy
  choices to scaffold to gate to export.
- `prompts/quality-critic.md` — the always-on quality adversary (goal achievement,
  tools, prompts, policies, reasoning trace).
- `prompts/ai-detection-critic.md` — the opt-in AI-content detector sublayer.
- `references/agent-repo-anatomy.md` — the portable-repo layout + config-over-code
  split + the agent.config.json schema.
- `references/publish-gate.md` — the pass-twice contract, counter-reset rule,
  sublayer semantics, and how it reuses the paper / diligence gauntlet.
- `references/af-format.md` — the `.af` schema + round-trip guarantee.
- `references/outputs-catalog.md` — the a-la-carte deliverable catalog, each
  output's shape, and the word-for-word verbatim-confirmation contract.
- `examples/agent.config.json` — a minimal real declared goal.
- `examples/release-notes-writer/` — the scaffolded repo that config produces.
- `examples/release-notes-writer.af` — its `.af` export.
- `examples/release-notes-writer.bundle/` — a packaged sample (techdoc, pptx, af,
  policies, monitoring + `OUTPUTS_MANIFEST.json`); `--verify`-clean against the repo.
