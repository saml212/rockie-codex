# agent-repo-anatomy — the portable agent repo + config-over-code split

A built agent is a **portable git repo** on the OpenClaw/Claude-Code harness.
`scripts/scaffold_agent.py` emits exactly this layout; `scripts/af_export.py`
serializes it; the gate in `scripts/publish_gate.py` decides if it ships.

## The layout (word-for-word reproducible)

```
<agent>/
  CLAUDE.md                       constitution: the declared GOAL + runtime + policies + discipline
  agent.config.json               the versioned, per-customer config (see below)
  manifest.json                   agent manifest: name, goal, capabilities, adversarial, provenance
  .mcp.json                       MCP / tool wiring (one server entry per wired tool)
  .claude/
    skills/
      self-critic/                shipped BY DEFAULT (self-adversarial bias)
        SKILL.md                  how the agent hardens its own deliverables
        critic_loop.py            the agent's OWN gauntlet engine (lineage: diligence-deck)
        domain-critic.md          a fresh no-memory domain adversary, scoped to the goal
    hooks/
      policy_gate.py              PreToolUse safeguard: blocks declared deny: policies
  policies/
    policy-01.md ...              one file per declared policy statement
  tests/
    test_agent.py                 proven-execution tests (repo shape + the self-critic engine runs)
```

When the declarer opts OUT of self-adversarial (`--no-self-adversarial`),
`.claude/skills/` is kept present (a `.gitkeep`) so the repo shape stays stable,
and `manifest.json` lists no skills. Everything else is identical.

## The config-over-code split (LangGraph-Assistants pattern)

The repo separates a **stable harness** from a **versioned per-customer config**:

- **Harness (stable, shared):** `CLAUDE.md`, `.claude/skills/`, `.claude/hooks/`,
  `tests/`. This is the code. It changes when the agent-builder itself improves.
- **Config (versioned, per-customer):** `agent.config.json`. This is what differs
  between two agents built for two customers — the goal, the model, the tool list,
  the policies, the adversarial settings, the requested outputs.

The harness reads the config; it does not hardcode goal/model/tool choices that
belong in the config. To re-target an agent at a new goal you change the config and
re-scaffold — you do not rewrite the harness.

### agent.config.json schema

```json
{
  "schema_version": "1.0",
  "name": "release-notes-writer",            // slug; derived from goal if omitted
  "goal": "ONE testable sentence: what this agent is FOR",
  "runtime": "fly-machine:kind=agent",       // a 2nd Fly machine, kind=agent (spec)
  "model": "claude-opus-4-8",
  "research_foundation": "gpt-researcher",    // deep-research foundation when needed
  "tools": ["github-mcp"],                    // MCP servers to wire into .mcp.json
  "policies": [                               // declared rules; deny:<s> are blockable
    "Never invent a change not backed by a merged PR.",
    "deny:internal-only"
  ],
  "adversarial": {
    "self_adversarial": true,                 // ship the internal critic loop (DEFAULT)
    "ai_detection": false,                    // opt-in publish sublayer (default off)
    "max_rounds": 6,
    "required_consecutive_passes": 2
  },
  "outputs": ["markdown"]                     // a-la-carte deliverable kinds
}
```

## Field semantics

- **goal** — the single load-bearing field. Everything else serves it. The scaffold
  embeds it verbatim into `CLAUDE.md`, `manifest.json`, the self-critic, and the
  proven-execution test.
- **runtime / model / research_foundation** — the runtime defaults to a 2nd Fly
  machine of `kind=agent` (per spec clarification #2); the deep-research foundation
  is GPT-Researcher, wired only when the goal needs multi-source research.
- **tools** — each becomes a server entry in `.mcp.json` with a placeholder launch
  command the wiring step fills in. Wire only what the goal needs.
- **policies** — `deny:<substring>` policies are enforced mechanically by
  `policy_gate.py` (it blocks any tool call whose input contains the substring);
  all other policy strings are advisory and enforced by the agent's judgment, and
  each gets a `policies/policy-NN.md`.
- **adversarial.self_adversarial** — DEFAULT TRUE. The scaffold is biased to emit
  agents that contain their own adversarial critic loop.
- **adversarial.ai_detection** — DEFAULT FALSE. Opt-in; only flips on the
  publish-gate AI-detection sublayer when the output must read human-authored.
- **outputs** — the deliverable kinds the declarer wants (a-la-carte).

## Why portable

Because every dependency the agent needs to run and to harden itself is INSIDE the
repo — its constitution, its skills (including a self-contained copy of the critic
engine), its hooks, its tool wiring, its policies, its tests — the repo can be
moved, cloned, or reconstructed from a `.af` (`references/af-format.md`) without
reaching back to the agent-builder. The self-critic engine is COPIED into the agent
(not imported) for exactly this reason; the agent-builder's own `publish_gate.py`
imports the canonical engine because it always runs next to diligence-deck.
