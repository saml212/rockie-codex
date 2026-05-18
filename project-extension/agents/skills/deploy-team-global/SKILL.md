---
name: deploy-team
description: Deploy a multi-agent team on a hard, multi-dimensional problem where several aspects need simultaneous optimization and trade-offs between them, or to shape the thinking behind a non-obvious architectural choice, or to examine work you've already done from multiple distinct perspectives. This is for throwing compute at genuinely hard problems — not for parallelism. Each agent has its own motivation, runs in an isolated git worktree, coordinates with the others via a shared thread, and is observable through a live dashboard the developer can intervene in. NOT for fire-and-forget parallelism — use the native Agent tool when one specialist can answer.
---

# /deploy-team — multi-agent team on a hard problem

The tool is installed at `$HOME/.openclaw/workspace/teams/`. It operates on the repository where the main agent is currently working (resolved via `git rev-parse --show-toplevel`), not where the tool itself is installed.

## When to summon

Only deploy a team when ALL THREE hold:

1. **Multi-dimensional problem.** The question has several aspects that must be optimized simultaneously and traded off against each other — not one question with one best answer.
2. **Cross-pollination matters.** You want the agents to read each other's findings mid-work and adjust. If they could produce their answers in isolation, use the Agent tool instead.
3. **Observability is useful.** The developer should be able to watch the work unfold and intervene (post to the thread, DM an agent, pause, stop).

If any one is missing, use a specialist subagent via the Agent tool.

## When NOT to summon

- One question with one right answer (find all uses of X, fix this bug, audit this function) — use the appropriate subagent
- Time-critical work — teams have 30+ second spin-up
- Fire-and-forget parallel dispatch — the Agent tool already does that
- Anything where you already know the answer and just need it typed — use a specialist

## Flow

1. Compose a team config (JSON). Shape is authoritative at `$HOME/.openclaw/workspace/teams/schema.json` — follow it rather than inventing fields.
2. Show the config to the developer for approval before spending the compute.
3. Invoke from the repo you're working in:
   ```bash
   node "$HOME/.openclaw/workspace/teams/orchestrator/index.js" <path/to/config.json>
   ```
4. The orchestrator validates the config, stages any referenced context files, creates a git worktree per agent, starts the dashboard, opens it in Chrome, runs the team, and writes `result.json` when done.
5. When agents finish (all `AGENT_DONE`, deadline hits, or `stop_team` fires), read `result.json` and `thread.md` to synthesize findings back to the developer.

## Config (authoritative at `$HOME/.openclaw/workspace/teams/schema.json`)

Required: `name`, `purpose`, `agents[]`. Each agent requires `name`, `motivation`, `prompt`. Optional: per-team `context_files`, `target.base`, `max_iterations_per_agent`, `max_total_minutes`, `parallel`; per-agent `model` (haiku/sonnet/opus), `context_files`, `permissions`.

The schema enforces team and agent names against `^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$` and requires agent names to be unique within a team.

## Prompt placeholders (use these — do NOT invent relative paths)

Agent prompts run in a worktree that's a peer of the run-dir, not a parent — so `./context/...` and `./thread.md` do NOT resolve. Use these placeholders in your agent prompts and the orchestrator will expand them to correct absolute paths:

| Placeholder | Expands to |
|---|---|
| `{{context}}` or `{{context_dir}}` | Absolute path of `<run-dir>/context/` (where `context_files` are staged) |
| `{{worktree}}` | Absolute path of the agent's git worktree |
| `{{post_thread}}` | Full Bash command prefix: `bash <post-script-path> <agent-name>` |
| `{{agent_name}}` | The agent's own name |

Example agent prompt:
```
Read {{context}}/position-papers/agent-a.md and critique it from your lens. To
post your critique to the shared thread, run: {{post_thread}} "your critique here"
```

## Hard constraints

- **`context_files` are mandatory** for any file an agent needs to read outside its worktree. Claude Code sandboxes agents to their worktree + `<run-dir>/context/`. The orchestrator copies listed files into `<run-dir>/context/` before agents start; reference the repo-relative path via `{{context}}/<repo-relative-path>`.
- **Thread access** — agents cannot Read/Edit the thread file directly (sandbox-protected). They post via the `post-thread` helper (use `{{post_thread}}` placeholder). Thread content reaches them via the "Thread Updates" section of each iteration's prompt.
- **Run state lives in `.team-runs/<run-id>/`** in the invoking repo (gitignored). Worktrees, per-agent state, events, and the shared thread are all there.
- **Worktrees are preserved after the run** — diffs each agent produced are browsable at `.team-runs/<run-id>/worktrees/<agent>/`. Remove with `git worktree remove <path>` when done.
- **`AGENT_DONE` must appear on its own line** outside fenced code blocks. Agents that quote or discuss the sentinel in prose don't false-terminate.

## `AGENT_DONE` ≠ terminated (chat-mode)

When an agent emits `AGENT_DONE`, it enters the `awaiting_input` state — **not done forever**. The agent sits waiting for:
- A **DM** sent from the dashboard (per-agent input box) → wakes that agent only
- A **thread post** sent from the dashboard (bottom-right textarea) → wakes ALL awaiting agents

The team ends when:
- The developer clicks **Stop Team** on the dashboard, OR
- `max_total_minutes` expires, OR
- Every agent has been explicitly stopped

**If all agents show `awaiting_input` and nothing is happening, the team isn't frozen — it's waiting for you.** Post to the thread or DM an agent to resume.

## Cognitive burden

The developer never types `/deploy-team`. The main agent decides to summon based on the "when to summon" rubric, composes the config, shows it for approval, then synthesizes results back. If uncertain whether to deploy, use the specialist path first — upgrading to a team later is cheaper than spinning down a team that shouldn't have run.

## Dependencies

- Node.js ≥ 20
- `express` pre-installed at `$HOME/.openclaw/workspace/teams/orchestrator/node_modules/`
- macOS `open` or Linux `xdg-open` to auto-launch Chrome (falls back to logging the URL)
