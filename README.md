# rockie-codex

**An autonomous AI research harness for OpenAI Codex CLI.**

Inspired by Project Hail Mary's Rocky — the alien research partner you
couldn't have built the answer without.

> **Looking for the Claude Code version?** See
> [`saml212/rockie-claude`](https://github.com/saml212/rockie-claude).
> Same patterns (`[LEARN]`, taste corpus, autopilot, gauntlets) on
> Anthropic's runtime.

---

## What rockie-codex does for you

Four jobs, run continuously, until you tell it to stop:

### 1. Captures your research taste — and iterates on it

A 5-minute first-run interview compiles your worldview, methodology,
dismissals, and voice into a durable `.rockie/taste/` corpus. Every
future session loads it automatically. Your agent knows what *you*
think a good result looks like, what dead ends you've sworn off, and
what register you want it to write in. Refresh anytime with the
`onboard` skill (`$onboard --section <name>`).

### 2. Bulletproofs every step with adversarial subagent networks

Plan → Research → Build → Audit → Run → Assess → Codify. Each step has
adversarial review built in:

- **`$deploy-team`** — gauntlets (brainstorm / research / attack /
  validate), pre-launch audits, post-run analysis. A team of agents
  fight over every important call. Repo-local Python/Codex workflow.
- **`$deploy-team-dashboard`** — user-global Node dashboard
  orchestrator. Per-agent git worktrees, JSONL intervention log,
  live thread view.
- **`$clean`** — pre-commit anti-slop audit gates `git commit` until
  debug artifacts and stale claims are gone.
- **`$propose-harness-change`** — Generator / Verifier / Updater split.
  The agent never auto-pushes.
- **stuck-detector + dead-end registry + budget-gate** —
  background services that nudge the agent when it's spinning, when
  it's about to re-propose a dead idea, or when it's about to cross
  a ceiling.

### 3. Cheap, resource-efficient autonomy — indefinitely

- **Local-first.** SQLite + FTS5 `[LEARN]` memory. No vector DB. No
  service except Codex itself.
- **Token-aware.** Prompts/wallclock/tool-calls auto-tracked but
  uncapped (they cost nothing). Only GPU dollars get enforced
  ceilings.
- **Spot-first GPU policy.** Min-bid defaults. Provider-hop on
  preemption (RunPod / Vast / Prime / Verda) before ever bumping a
  bid. On-demand last resort, gated.
- **Modes** — `$mode switch paper-crunch` for deadline-locked
  scope-lock; `$mode switch exploratory` for broad reading + fast
  iteration; build your own. Swap operational policy without changing
  your identity.

### 4. Stays honest

- Catches bugs *before* you burn GPU time (separate auditor agent
  reads shapes/gradients/stability pre-launch).
- Notices when it's stuck (4 semantic-loop types, periods 2/3/4).
- Tracks whether predictions were right (`predicted_delta` vs
  `actual_delta` per experiment).
- Classifies every failure: `bug | bad-hyperparam | bad-hypothesis`.
  Routes `[LEARN]` and `[DEAD-END]` accordingly.

> Status: **alpha / pre-launch.** Codex-native port of the patterns
> running on a multi-GPU autonomous research project. Breaking changes
> until `v0.1`.

---

## What you and your agent will go through

A chronological walkthrough of the first week. Each phase below is
what actually happens; the rest of this README explains the machinery.

**Hour 0 — Install + onboard.** Run `./install.sh ~/your-research-project`.
Open Codex CLI in that project; the SessionStart hook spots that no
taste corpus exists and prompts the `onboard` skill. Five to seven
questions, ~5 minutes. Output: a six-file taste corpus committed to
`<project>/.rockie/taste/` (SOUL, STYLE, METHODOLOGY, DISMISSALS,
MEMORY, INDEX). `INDEX.md` is auto-injected into every future session
via the `SessionStart` hook in `.codex/hooks.json`.

**Day 1 — Plan + first experiment.** You talk to Codex. Subagents
verify novelty and check for re-proposing dead ends already logged
in the registry. Before any GPU dollars are spent, the pre-launch
audit agent reads shapes, gradients, and stability of the proposed
code in a separate context. Only then does the first training run
launch. Every prediction (`predicted_delta`) is recorded alongside
the hypothesis.

**Day 1+ — Continuous loop.** `$autopilot` takes over. The Zero-Cost
Monitor polls training logs without LLM calls, so a stable run costs
nothing while it churns. ntfy push notifications wake you only on
anomalies, ceiling crosses, or genuine decisions — never on routine
heartbeats. Every run produces a `[LEARN]` block on completion; the
next prompt's `UserPromptSubmit` hook auto-injects the top-5 relevant
rules via FTS5 BM25 search.

**Week 1+ — Iteration compounds.** Predicted-vs-actual deltas roll up
per experiment so calibration becomes visible. Failures are classified
`bug | bad-hyperparam | bad-hypothesis` and route `[LEARN]` or
`[DEAD-END]` accordingly. The dead-end registry prevents new subagents
from re-proposing what the team already ruled out. When your standards
shift, refresh the relevant slice with `$onboard --section <name>` —
identity drift gets an audit trail, not a silent overwrite.

---

## The loop

```
                  ┌─ Plan ─────────── you talk to Codex
                  │
                  ├─ Research ─────── subagents verify, check novelty
                  │
                  ├─ Build ────────── write code, clean, comment the non-obvious
                  │
                  ├─ Audit ────────── SEPARATE agent reviews shapes/gradients/stability
                  │                   (the pre-run gate nobody else has)
                  │
                  ├─ Run ──────────── execute; ntfy push on preemption / block / win
                  │
                  ├─ Assess ───────── post-run review emits {is_bug, bad-hyperparam, bad-hypothesis}
                  │
                  └─ Codify ───────── [LEARN] block → workflow.db (FTS5)
                                      next prompt auto-injects relevant rules
```

Every cycle should make the next cycle better.

---

## Install

```bash
git clone https://github.com/saml212/rockie-codex.git ~/rockie-codex
cd ~/rockie-codex
./install.sh ~/path/to/your/research-project
```

The installer:

1. Copies `project-extension/codex/` → `<your-project>/.codex/`
2. Copies `project-extension/agents/skills/` → `<your-project>/.agents/skills/`
3. Copies `user-extension/codex/` → `~/.codex/` (unless `--project-only`)
4. Initializes `workflow.db` (FTS5 required — pinned to `/usr/bin/sqlite3`)
5. Seeds harness rules + 5 mode templates (default, paper-crunch,
   exploratory, dogfooding, learning)
6. Prints an `AGENTS.md` template path to drop into your repo root.

**On first session:** the `SessionStart` hook prompts you to use the
`onboard` skill — 5–7 questions, ~5 minutes. Produces your taste
corpus.

**Verify the install:** `bash tests/smoke-test.sh` runs 75 assertions
(hooks fire, FTS5 search, atomic queue claim, installer idempotency,
path-traversal refusal, budget-ceiling enforcement, autopilot end-to-end
with mock launcher, schema migrations, autopilot.conf safe parser, GPU
router with fake providers). CI runs the same on every push. ~10
seconds, no API key.

See [docs/install.md](docs/install.md) for manual install,
[docs/quickstart.md](docs/quickstart.md) for first-session walkthrough,
[docs/EVENT-MAPPING.md](docs/EVENT-MAPPING.md) for the runtime contract,
[docs/ntfy-setup.md](docs/ntfy-setup.md) for push notifications
(optional).

---

## The skills you'll invoke

Codex doesn't expose repo-defined slash commands, so the practical
invocation path is `$<skill-name>` or natural language ("use onboard").
Historical slash names from `rockie-claude` are retained as docs
shorthand only.

| Skill | What |
|---|---|
| `$onboard` | researcher-taste interview → six-file `taste/` corpus that auto-loads every session |
| `$mode` | swap operational overlays (paper-crunch / exploratory / dogfooding / learning / your own) |
| `$deploy-team` | dispatch adversarial subagent gauntlets — repo-local Python/Codex workflow |
| `$deploy-team-dashboard` | user-global Node dashboard orchestrator with worktrees |
| `$clean` | pre-commit anti-slop audit + sentinel; gates `git commit` |
| `$propose-harness-change` | package an upstream-back patch with Generator/Verifier/Updater review |
| `$upstream-contribute` | scan the session for generalizable patterns; open a PR against `saml212/rockie-codex` |
| `$queue-refill` | brainstorm 3–5 new high-quality experiments when the queue runs dry |
| `$post-run-review` | structured review after every training/eval run; emits `[LEARN]` or `[DEAD-END]` |
| `$autopilot` | continuous-operation mode for days-long autonomous work |

---

## The `[LEARN]` protocol

When Codex learns something durable mid-session, it emits:

```
[LEARN] <category>: <one-line rule>
Mistake: <what went wrong>
Correction: <what the right approach is>
```

The `Stop` hook parses, dedupes by `(project, category, rule)`, inserts
into `.codex/memory/workflow.db`. On the next prompt, the
`UserPromptSubmit` hook tokenizes the prompt, runs an FTS5 BM25 search
over the learnings, and injects the top-5 relevant rules — but only
if the best match is genuinely strong (BM25 score < -4). No noise.

Codex has no `PreCompact` event, so the user-global
`memory-pre-compact.sh` runs as a `Stop`-registered backstop instead.
Details in [docs/EVENT-MAPPING.md](docs/EVENT-MAPPING.md).

---

## Use it on an existing project

If you already have a research repo and just want rockie-codex's
hooks, skills, and memory layered on top:

```bash
git clone https://github.com/saml212/rockie-codex.git ~/rockie-codex
~/rockie-codex/install.sh ~/your-research-project
```

What gets written:

- `<your-project>/.codex/` — the per-project Codex runtime: hooks,
  scripts, memory schema, `hooks.json`, project_id stamp, sentinels
  dir.
- `<your-project>/.agents/skills/` — the repo-local Codex skills.
- `~/.codex/` — the user-global runtime: cross-project memory lib,
  user-global hooks, the `deploy-team-dashboard` Node orchestrator.

What stays untouched:

- All existing source code in your repo. The installer never edits
  anything outside `.codex/`, `.agents/`, and `.gitignore` (it adds
  a managed block between `# BEGIN rockie-codex` / `# END rockie-codex`
  markers; rules outside the markers are never touched).
- An existing `AGENTS.md` is preserved. If absent, the installer
  prints the template path and you copy it in deliberately.
- Your existing `.env` is left alone.

First time you open Codex CLI in that project, the `SessionStart`
hook spots the missing `.rockie/taste/INDEX.md` and prompts the
`onboard` skill (~5 minutes). After that, normal Codex CLI workflow
+ harness intercepts.

## Use it on a new project

Starting fresh:

```bash
git clone https://github.com/saml212/rockie-codex.git ~/rockie-codex
mkdir ~/your-research-project && cd ~/your-research-project
git init
~/rockie-codex/install.sh .
```

What `install.sh` creates:

- Skeleton `.codex/` (hooks, scripts, memory dir, hooks.json) and
  `.agents/skills/`
- `workflow.db` initialized + seeded with the harness rules
- A managed `.gitignore` block (so `workflow.db`, sentinels, and
  `.codex/sessions` stay out of git)
- A printed pointer to the `AGENTS.md` template — copy it, edit the
  Project section, commit.

First session walkthrough:

1. Open Codex CLI in the new project.
2. `SessionStart` hook prompts the `onboard` skill (e.g. "use $onboard").
   Five to seven questions, ~5 minutes. Output: a six-file
   `.rockie/taste/` corpus committed to your repo.
3. Talk to Codex. Subagents verify novelty; the pre-launch audit
   reads shapes/gradients before any GPU dollars are spent.
4. After the first run completes, `$post-run-review` emits a `[LEARN]`
   block and the next prompt's `UserPromptSubmit` hook auto-injects
   relevant rules. The loop is now closed.

## Bring your own GPU (bypass the spot-procurement flow)

rockie-codex's default GPU layer is a cross-provider router (RunPod /
Vast / Prime / Verda) with min-bid spot defaults and provider-hop on
preemption. That's the right choice if you have nothing pre-configured.

If you already have GPU infra — a university cluster, on-prem H100s,
your own AWS account, an SSH-tunneled workstation, or credentials at
a provider we don't route to — rockie-codex steps out of the way.

**One-time setup:**

```bash
# in your-research-project/
echo 'ROCKIE_GPU_MODE=custom' >> .env
```

Then in your first agent session, ask Codex about GPUs. The agent
runs `$gpu-custom-setup` — a Q&A that captures your auth, provision,
connect, monitor, and terminate flow, then writes
`.codex/gpu-custom.md`. Subsequent sessions read that file when
GPUs come up.

**Minimal `autopilot.conf` for an SSH-based launcher:**

```ini
LAUNCHER_CMD=/usr/local/bin/my-launch.sh
ROCKIE_GPU_MODE=custom
```

`my-launch.sh` is whatever you already use to dispatch a training
run (sbatch + sshfs, ssh + nohup, terraform apply, etc.). The
autopilot loop, the Zero-Cost Monitor, the budget gate, the
post-run review, and the dead-end registry all keep working — only
the `gpu.py` cross-provider router is bypassed.

After that, `$gpu-custom` is the agent's runtime gateway:
provision / connect / status / cost / terminate are all routed
through your captured flow, not through rockie-codex's router. The
terminate command is run **verbatim** from your setup file —
never improvised — because cost-sensitive teardown deserves
zero ambiguity.

See `project-extension/agents/skills/gpu-custom-setup/SKILL.md` for
the full Q&A spec and `project-extension/agents/skills/gpu-custom/SKILL.md`
for the runtime routing rules.

## Contributing back upstream

`$upstream-contribute` is the meta-loop: rockie-codex users improve
rockie-codex itself as they work. After `$clean` finishes an audit,
the skill surfaces a nudge — *"Anything in this session worth
upstreaming?"* — and on opt-in, scans the session for generalizable
patterns (pruning fixes, small skill improvements, new hooks,
cross-discipline capabilities, memory-schema upgrades), strips
project-specific specificity, and dispatches a writer sub-agent that
forks `saml212/rockie-codex`, applies the change on a `contrib/<slug>`
branch, runs the smoke test, and opens a PR. The agent never
auto-merges; the maintainers review; the next release ships the
pattern to everyone.

The bar is generalizability. Domain-specific changes stay in your
fork via `$propose-harness-change`. Anything that would require
revealing internal project context to make sense gets refused.

The Codex Generator strips any `Co-Authored-By: openai-codex` /
auto-suggested AI co-author trailers before commit — upstream
`CONTRIBUTING.md` is explicit about that.

See `project-extension/agents/skills/upstream-contribute/SKILL.md`
for the full Scout / Generator / Verifier / Updater flow and the
leak-protection rules. The end-to-end paper-trace lives at
[docs/UPSTREAM-CONTRIBUTE-EXAMPLE.md](docs/UPSTREAM-CONTRIBUTE-EXAMPLE.md).

---

## Repo layout

```text
project-extension/
  codex/          # repo-installed hooks, scripts, memory schema, hooks.json
  agents/skills/  # repo-installed Codex skills
user-extension/
  codex/          # home-installed hooks, scripts, teams, user skills
agents-md/        # AGENTS.md templates
docs/             # public architecture, install, mapping, paper trace
tests/            # smoke suite + fakes
```

## Honest Codex deltas

- Project hooks live in `.codex/hooks.json`; user-global hooks live in
  `~/.codex/hooks.json`.
- Project skills live in `.agents/skills/`; user-global skills live in
  `~/.codex/skills/`.
- `AGENTS.md` replaces `CLAUDE.md`.
- Historical slash names like `/onboard`, `/mode`, and `/clean` are
  retained as shorthand in docs, but in Codex you typically invoke the
  skill via `$onboard`, `$mode`, `$clean`, or natural language.
- Codex has no `PreCompact` hook event. The legacy
  `memory-pre-compact.sh` backstop is shipped and mapped to `Stop`;
  details are in [docs/EVENT-MAPPING.md](docs/EVENT-MAPPING.md).
- The dashboard orchestrator accepts legacy model aliases and maps
  them to Codex models:
  - `haiku` → `gpt-5.4-mini`
  - `sonnet` → `gpt-5.4`
  - `opus` → `gpt-5.5`

---

## Licensing

Apache-2.0. See [LICENSE](LICENSE).

This is a Codex-native port of patterns from
[`saml212/rockie-claude`](https://github.com/saml212/rockie-claude).
Cited explicitly because rockie-claude is the upstream reference for
the Generator/Verifier/Updater discipline, the `[LEARN]` protocol,
and the taste-corpus design.

## Contributing

- Every port must cite source file + line range.
- Every new feature must compose with existing differentiators (taste
  corpus, modes, pre-run audit, `[LEARN]` DB, waterfall, journal tree,
  experiment-runs/, `$deploy-team`, pre-commit sentinel). Duplicates
  get rejected.
- Run `$clean` before committing — the pre-commit-gate hook enforces
  it.
- All commits must be signed off by the contributor (`git commit -s`);
  do not include any AI-generated co-author trailers in commit
  messages.

**Upstream-back from agents — two paths.** If a Codex agent using
rockie-codex in your own project discovers a harness-level
improvement, it can emit `[LEARN harness-upstream] …` mid-session.

- `$propose-harness-change` — reviewed/verified patch against your
  OWN local rockie-codex clone. The Generator/Verifier/Updater split
  keeps the agent from auto-committing; the human pushes when ready.
- `$upstream-contribute` — the public meta-loop. Scans the session,
  strips project-specific specificity, dispatches a writer sub-agent
  to fork `saml212/rockie-codex`, applies the generalized change,
  runs the smoke test, and opens a PR. Maintainers review; the next
  release ships the pattern to everyone.

The agent never auto-pushes in either path.

---

## Further reading

**For users:**

- [docs/quickstart.md](docs/quickstart.md) — 5-minute install + first commands
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — event flow + storage model
- [docs/EVENT-MAPPING.md](docs/EVENT-MAPPING.md) — Claude Code event → Codex CLI event, honest gaps
- [docs/budgets.md](docs/budgets.md) — 4-dimension auto-tracking
- [docs/environment.md](docs/environment.md) — `.env` + rotation
- [docs/ntfy-setup.md](docs/ntfy-setup.md) — push notifications
- [docs/PORTS.md](docs/PORTS.md) — what was ported, what was adapted
- [docs/UPSTREAM-CONTRIBUTE-EXAMPLE.md](docs/UPSTREAM-CONTRIBUTE-EXAMPLE.md) — paper trace of the meta-loop
- [SECURITY.md](SECURITY.md) — threat model + risk surfaces

---

## Related projects

- [`saml212/rockie-claude`](https://github.com/saml212/rockie-claude) —
  Anthropic Claude Code sibling. Same patterns, different runtime.

## Acknowledgements

This harness is a Codex-native port of patterns extracted from
research originally driven on a learned-representations workspace; see
[pebbleml.com](https://www.pebbleml.com) for the kind of project a
researcher might run rockie-codex on.
