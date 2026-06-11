---
name: upstream-contribute
description: Scan the current Codex session for harness-level patterns that would be useful to other rockie-codex users — pruning fixes, small skill improvements, new hooks, cross-discipline-useful capabilities, memory-schema upgrades — strip the project-specific specificity, and dispatch a writer sub-agent that forks `saml212/rockie-codex`, applies the generalized change on a `contrib/<slug>` branch, runs `tests/smoke-test.sh`, and opens a PR. Never auto-merges; the maintainers review. Triggers when the user says "upstream this", "contribute back", "anything generalizable here", or after `$clean` emits its post-audit nudge. Sibling to `$propose-harness-change`, but scoped at the public upstream rather than a private fork.
---

# $upstream-contribute — meta-loop: rockie users improve rockie

`$propose-harness-change` packages improvements against the user's
**own** local clone of rockie-codex. `$upstream-contribute` is the
public counterpart: it dispatches a PR against `saml212/rockie-codex`
so the next release ships the pattern to everyone.

This is the meta-loop. Researchers using rockie-codex in their own
work uncover patterns that generalize — a tighter `[DEAD-END]` query,
a hook that catches a stuck-detector edge case, a skill that helps
across disciplines. The skill captures one of those, generalizes it,
and opens a reviewable PR. The maintainers merge; the next user
benefits.

## When to run

- The user says "upstream this", "contribute back", "anything
  generalizable here", "open-source this".
- `$clean` finishes an audit and emits the post-audit nudge (see
  `clean/SKILL.md` → "Post-audit hook").
- A `[LEARN harness-upstream]` or `[LEARN cross-discipline]` block
  was written this session and the user wants to act on it.

Run **at most once** per session, near the end. Don't dispatch the
writer sub-agent during active work — the user wants to review the
candidates before any forking happens.

Codex doesn't expose repo-defined slash commands the way Claude
Code does, so the practical invocation path is `$upstream-contribute`
or natural language ("contribute back upstream").

## Method

The skill mirrors the **Generator / Verifier / Updater** rigor of
`$propose-harness-change` and adds a **Scout** step in front (since
the upstream PR is a more public artifact than a local patch).

### 1. Scout (this skill, in-session)

Read recent context to identify candidates:

```bash
# [LEARN] rows added this session
/usr/bin/sqlite3 ${OPENCLAW_WORKSPACE_DIR}/memory/workflow.db "
  SELECT category, rule, mistake, correction
  FROM learnings
  WHERE created_at >= datetime('now','-6 hours')
  ORDER BY created_at DESC
"

# Files changed in the last N commits
git log --since='6 hours ago' --name-only --pretty=format:'%h %s'

# Any new skill / hook / script files
git diff --name-only HEAD~5..HEAD -- '${OPENCLAW_SKILLS_DIR}/' '${OPENCLAW_WORKSPACE_DIR}/hooks/' '${OPENCLAW_WORKSPACE_DIR}/scripts/' \
  | grep -E '\.(md|sh|py)$' || true
```

For each candidate, score (yes/no/no — keep only "yes"):

| Question | Why it matters |
|---|---|
| Would this help another rockie-codex user across a different research domain? | Cross-discipline generalizability is the bar for upstream. |
| Is the pattern small and self-contained (one file, one function, one rule)? | Big architectural changes go through `$propose-harness-change` first. |
| Can it be described without naming any internal project, person, or dataset? | Leak-protection. |
| Does it survive without `(your-project)`-specific configuration? | If it doesn't, it's a fork patch, not an upstream patch. |

Drop anything that fails any of the four. Then surface the survivors
to the user with one sentence each:

> Found 3 candidates. Take any of them upstream?
> 1. `[LEARN]` row "fts5: strip hyphens from MATCH" → would generalize as a fix in `load-relevant-rules.sh`.
> 2. New script `.codex/scripts/queue_audit.py` → would generalize as a small skill or as part of `$queue-refill`.
> 3. Tighter `[DEAD-END]` query in `load-relevant-deadends.sh` → would generalize as a hook fix.
>
> Reply with the numbers (e.g. "1, 3") to dispatch.

### 2. Strip project-specific specificity

For each chosen candidate, the skill **rewrites** before sending to
the writer sub-agent. Examples:

- "in our matrix-token research we found X" → "for any iterative ML
  reasoning research, we found X"
- "we noticed during the autopilot run on dataset Y that Z" → "we
  noticed during a multi-day autopilot run that Z" (and never mention
  the project or dataset name)
- "this fixed the hang we had during the Tuesday kickoff" → "this
  fixes a hang that occurs when the queue is empty at start"

Concrete project names, dataset names, internal repo paths, and
collaborator names get stripped. The agent does NOT proceed if it
can't generalize the change — it returns the candidate to the user
with "this looks too project-specific to upstream cleanly."

### 3. Dispatch a writer sub-agent (Generator)

Codex CLI's sub-agent dispatch is the natural place for this; the
Generator runs with no prior session context. Pass:

- The generalized change description (text)
- The list of files to touch (paths only)
- `target_repo=saml212/rockie-codex`
- `branch=contrib/<short-slug>`

The Generator must:

1. `gh repo fork saml212/rockie-codex --clone --remote` (if not
   already forked).
2. `cd rockie-codex && git checkout -b contrib/<slug>`.
3. Apply the change. Each file edit must compose with existing
   differentiators (taste corpus, modes, pre-run audit, `[LEARN]`
   DB, waterfall, journal tree, experiment-runs/, `$deploy-team`,
   pre-commit sentinel). Duplicates of existing capability get
   rejected by the maintainers and waste review cycles — abort if
   the change would duplicate.
4. Run `bash tests/smoke-test.sh`. Must be green. The Codex smoke
   suite covers 75 assertions including installer idempotency,
   FTS5 retrieval, and fake-provider GPU routing.
5. `git add -p` (deliberate hunks only; never `git add -A`).
6. Commit with Conventional Commit prefix (`feat:`, `fix:`, `docs:`,
   `chore:`, `port:`). Sign-off if upstream `CONTRIBUTING.md`
   requires it. **No AI-generated co-author trailers** — that's
   stated explicitly in upstream `CONTRIBUTING.md` and the writer
   sub-agent must respect it.
7. `git push -u origin contrib/<slug>`.
8. `gh pr create` with the body template below.

### 4. Verify (fresh-context audit, optional)

For changes touching hooks, schema, or anything that runs on every
prompt — dispatch a fresh-context Verifier (same shape as
`$propose-harness-change`) to read the diff and the four
verifier-questions BEFORE the PR is opened. Store the verdict in
the PR body. For docs-only or one-skill changes, skip this; the
maintainers' PR review is enough.

### 5. Updater (the human, asynchronous)

The PR sits open. The user reviews the body, runs the smoke test
locally if they want extra confidence, and merges (or doesn't).
The skill is done once the PR URL is reported back.

## PR body template

```markdown
## What

<one-line description of the generalized change>

## Why this is generalizable

<2-3 sentences explaining how this helps users across research
domains, not just one project>

## What this isn't

<1-2 sentences explicitly noting what this PR deliberately does NOT
do — scope discipline, so the maintainers don't have to ask>

## Smoke-test status

`bash tests/smoke-test.sh` → 75 assertions, all green
(local run on <date>; CI will re-run on push).

## Composition with existing differentiators

<one sentence per relevant pillar — taste corpus / adversarial
gauntlets / cheap autonomy / honesty — confirming this composes
rather than duplicates>

## Acknowledgements

This pattern was uncovered by an external rockie-codex user during
their own research work. They asked for upstreaming via
`$upstream-contribute`. Crediting the contributor by GitHub handle
(if they opted in): @<handle>.
```

The user's GitHub handle is included **only** if they explicitly
opt in during the scout step. Default is no attribution — leak
protection.

## What qualifies as upstream-worthy

Same bar as `$propose-harness-change`, plus a stronger
generalizability filter:

**Yes:**
- Bug fixes in hooks/scripts that affect any user (regardless of
  domain).
- New smoke-test assertions catching general-purpose regressions.
- New skill that's useful across disciplines (e.g. `$scheduled-notes`
  works for any research project; `$matrix-decomposition-helper`
  doesn't).
- Memory-schema upgrades with migrations (`memory/migrations/NNN_*.sql`).
- Cross-discipline-useful capabilities (e.g. an FTS5 retrieval
  tweak; a new `[DEAD-END]` matcher).
- README / docs improvements that clarify install or usage.

**No:**
- Domain-specific changes (ML-preset rules that don't generalize
  beyond ML, NLP-specific patterns that don't help other research,
  etc.). Keep these in the user's own fork.
- Anything depending on private config or internal infra.
- Reformatting / "nice to have" cosmetic changes without a clear win.
- Schema changes without a migration file.
- Anything that would require the user to reveal internal project
  context to make sense.

## Refusal paths

The skill must REFUSE to dispatch a Generator if:

- The candidate change can't be generalized without leaking project
  context. (Surface the candidate back to the user with a "looks
  project-specific" note; they can rewrite manually.)
- The change touches `memory/schema.sql` without a companion migration.
- The change modifies the Verifier's own definition or this skill's
  own SKILL.md (canonical self-improvement footgun — those go through
  ordinary maintainer review, not via the skill).
- The change adds a network dependency that isn't already in upstream
  `NOTICE`.
- `bash tests/smoke-test.sh` fails on the writer sub-agent's branch.
- The user hasn't reviewed and approved at least one candidate.

## Codex runtime caveats

A few deltas vs the rockie-claude version of this skill:

- The Generator runs in a Codex sub-agent context, not a Claude
  Agent-tool subprocess. Capability is equivalent for the purposes
  of this skill (file edits + shell + `gh`), but tool naming differs.
- Codex has no `PreCompact` event, so any `[LEARN harness-upstream]`
  rows must already be persisted to `workflow.db` by `Stop` before
  the Scout runs. Don't rely on a backstop scan during compaction.
- The "AI co-author trailer" prohibition in upstream `CONTRIBUTING.md`
  applies even more strictly here than in the Claude port — Codex
  output sometimes auto-suggests a `Co-Authored-By: openai-codex`
  trailer; the Generator strips it before commit.

## Composition with other skills

- **`$clean`** — emits the post-audit nudge that surfaces this skill.
  Runs after the audit sentinel is written; never blocks the commit.
- **`$propose-harness-change`** — for changes scoped at the user's
  OWN local rockie-codex clone. If the user wants the change in their
  fork first (and only later upstream), they run that skill instead.
- **`$post-run-review`** — populates `[LEARN]` rows that this skill
  will scan for candidates.
- **autopilot** — autopilot writes `[LEARN]` blocks during long runs;
  those become candidates here.

## Open questions (roadmap)

- The Generator's `gh pr create` step is the first place this skill
  pushes to a public remote. Until we've seen 10+ real proposals and
  reviewed the false-approve rate, the skill should pause for explicit
  user "y" before the push step.
- The Verifier panel is single-agent today. A bias-probe panel (two
  Verifiers with opposite priors + a meta-Verifier) is future work,
  same as in `$propose-harness-change`.
- A "draft PR" mode that opens the PR with `--draft` and lets the
  user re-edit the body before un-drafting is on the roadmap.
