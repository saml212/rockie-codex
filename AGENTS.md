# How to work in rockie-codex

Local overlay for the official `codex` binary. Customizes the runtime in a per-tenant Fly machine when `MODE=subscription` and `BINARY=codex`.

For *what this repo does for you as a researcher*, see `README.md`. For *cross-repo orientation* (the four-harness model + alignment agent), see `../docs/architecture.md` in the workspace meta-repo.

## First reads (after the workspace `docs/` reads)

1. `README.md` — researcher-facing overview.
2. `project-extension/` — repo-installed extension layer.
3. `user-extension/` — user-global extension layer.
4. `agents-md/` — AGENTS.md templates that get rendered into tenant projects.

## ⚠️ This repo is a LOCAL OVERLAY, not the canonical source

Skills, hooks, memory schema, scripts → **canonical source is `../platform-skills/`**.

The runtime image's `assemble-skills.sh` pulls from `platform-skills/` (NOT from this repo) and writes to `~/.codex/skills/` inside the per-tenant machine. This repo is a local copy used for development reference.

The Phase 6 **alignment agent** propagates changes from `platform-skills/` into this repo's mirror copies. **Don't edit skills here directly** — edit them in `platform-skills/` and let the alignment agent push.

The exceptions are intentional divergences (in `.claude/skills/align-overlays/intentional-divergences.yaml` in the meta-repo): codex-side conventions like `agents-md/` templates, natural-language invocation style (vs claude's `claude-md/` and slash commands), and `hooks.json` (vs claude's `hooks/` directory).

## Process for changes here

1. **Is this a skill/hook/memory change?** Edit `platform-skills/` instead. Don't fork here.
2. **Is this an agents-md template change?** Edit `agents-md/` here. The alignment agent will mirror to `rockie-claude/claude-md/` with translation (AGENTS.md ↔ CLAUDE.md, NL invocation ↔ slash commands).
3. **Is this a structural change to project-extension/ or user-extension/?** Likely affects rockie-claude too — flag in the PR description.
4. **Tests:** `pnpm test` (codex-extension's vitest config, lint via tests.yml).
5. **Commit:** Sam Larson author, no AI trailers, Conventional Commits.

## Skills you'll reach for

From the meta-repo (`../.claude/skills/` — note: skills live on the claude-side of the dev harness; this codex repo doesn't have its own dev-skill loadout):
- `/backpressure` — required pre-commit on substantial changes.
- `/clean` — periodic AI-slop reduction.
- `/align-overlays` — when you need to verify/sync with platform-skills before a substantial change.

## Things to be aware of

- **No fork; overlay-only.** The Codex CLI carve-out is gray (no Boris-equivalent statement from OpenAI), so codex is shipped as a beta runtime variant. Our policy is: run the official `codex` binary unmodified, with our customizations layered via `~/.codex/`.
- **Token theft is a security concern.** OAuth credentials live on the tenant Fly volume; never leave it. See `../platform-context/SECURITY.md` §4.
- **rockie-claude parity.** When you add a new skill or change template content, the claude side may need an analog. The alignment agent flags drift; honor its PRs.
- **Hook format differs from claude-side.** Codex uses `hooks.json` (single file), claude uses `hooks/` (directory). The alignment agent translates.

## When in doubt

- For **rockie-codex internal** decisions: `README.md` + the relevant subdir.
- For **Rockie-platform** decisions (alignment policy, four-harness model, runtime variant differences): `../docs/decisions.md` + `../docs/architecture.md` in the workspace meta-repo.
