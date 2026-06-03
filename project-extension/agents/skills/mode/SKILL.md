---
name: mode
description: Manage swappable operational overlays on top of the central taste corpus. A mode is a small TOML file capturing how *this session's* policy differs from the researcher's stable identity — hardware preferences, budget ceilings, reading focus, scope-lock, subagent model policy, output register, deadlines. The active mode is loaded into every SessionStart alongside taste/INDEX.md. Triggers on `/mode show`, `/mode list`, `/mode switch <name>`, `/mode new <name>`, `/mode edit <name>`, `/mode diff <a> <b>`.
---

# /mode — operational overlays on the taste corpus

Identity is centralized in `.rockie/taste/`. Modes carry the small,
context-dependent policy bits that flip between sessions:
paper-deadline vs. exploratory vs. dogfooding vs. teaching.

A mode is a TOML file at `.rockie/taste/modes/<name>.toml`. The
active mode is named in `.rockie/taste/modes/_active` (one line).
SessionStart loads it and surfaces a compact summary alongside the
researcher identity from INDEX.md.

## When to use

- A new context (deadline, area, register) where overrides apply.
- The harness behavior shouldn't change *who you are*, just what you
  optimize for this session.

## Subcommands

```bash
python3 .agents/skills/mode/runtime/mode.py show            # active mode + diff vs central
python3 .agents/skills/mode/runtime/mode.py list            # available modes
python3 .agents/skills/mode/runtime/mode.py switch <name>   # atomic flip _active
python3 .agents/skills/mode/runtime/mode.py new <name> [--from <template>]
python3 .agents/skills/mode/runtime/mode.py edit <name>     # opens in $EDITOR
python3 .agents/skills/mode/runtime/mode.py diff <a> <b>    # compare two modes
python3 .agents/skills/mode/runtime/mode.py active          # print just the active name
```

## Built-in templates

Shipped in `<rockie>/project-harness/skills/mode/templates/`. The
installer copies them into `.rockie/taste/modes/` on first run if
that directory doesn't exist yet.

| Template | When |
|---|---|
| `default` | No overrides — central corpus is canonical |
| `paper-crunch` | Deadline-locked: scope_lock, formal register, smoke gates, Opus on attack/review |
| `exploratory` | No scope lock, broad reading window, sonnet-first, register=lab-notebook |
| `dogfooding` | Harness self-modification or first-run testing — tight budgets, fast cycles |
| `learning` | Educational sessions: register=teaching, gates relaxed, Opus on explanations |

## Schema (every field optional)

```toml
name = "<slug>"
description = "<one sentence>"

[hardware]
hardware_type = "H100" | "A100" | "H200"
spot_only = true
on_demand_allowed = false
gpu_budget_dollars_per_session = 50

[reading]
focus_arxiv_categories = ["cs.LG"]
exclude_categories = ["q-bio.NC"]
recency_window_months = 6
prefer_venues = ["NeurIPS", "ICML"]

[methodology]
success_criteria_override = "<prose>"
required_ablations = ["param-matched-flat"]
allow_negative_result = true

[risk]
sanity_check_above_dollars = 10
session_max_dollars = 80
require_smoke_before_real_run = true

[output]
register = "formal-paper" | "lab-notebook" | "teaching" | "code-comment"
default_target = "<paper / blog / tinker>"

[workflow]
scope_lock = true
require_clean_before_commit = true
require_audit_subagent_before_train = true
prefer_opus_for = ["attack", "novelty-check"]
prefer_sonnet_for = ["fix-list", "research-collation"]

[dismissals]
active_categories = ["ml-architectural"]   # which DISMISSALS.md tags
                                            # are load-bearing this mode

[deadline]
target_date = "YYYY-MM-DD"
scope_lock = true
```

## Override semantics

Layered look-up, not merge-and-rewrite. When agent code or a hook
needs a value:

1. Active mode has the field → use it.
2. Central corpus (INDEX.md, METHODOLOGY.md) has the field → use it.
3. Else fall back to harness default.

Disk corpus is never modified by mode switches. Switching is cheap.

## Hard rules

- `_active` is rewritten atomically (write to `.tmp`, fsync, rename).
- Mode names must match `^[a-z][a-z0-9-]{1,30}$` — short slugs.
- An invalid TOML in modes/ does not silently disable the mode; the
  CLI errors with the parse failure and refuses to switch to it.
- `/mode switch` to a non-existent mode lists available modes,
  exit 2.
- A mode file must have at minimum `name = "..."`. Empty modes are
  legal (the `default` template is empty).

## Failure modes

- Mode TOML has typo in section header (`[hardare]` instead of
  `[hardware]`) — section is ignored silently. Run `/mode show` to
  see what got loaded; mismatches between expected schema and parsed
  shape are flagged with WARN.
- Mode sets `spot_only = true` but the requested SKU has no spot
  capacity right now — SessionStart conflict-detection warns; the
  agent should re-check availability via `rockie-gpu market` or relax
  `spot_only` before relying on it.
- Mode says `spot_only = true` but `ROCKIE_GPU_MODE = none` —
  WARN: the Rockie GPU layer is disabled.

## Agent invocation protocol

When the user says `/mode <subcommand>` or asks to switch modes, the
agent:

1. Run `python3 .agents/skills/mode/runtime/mode.py <subcommand>`.
2. Echo the output to the user.
3. If the action was `switch`, suggest `/clear` so the next session
   sees the new mode injected at SessionStart.

The mode name does NOT auto-inject mid-session; the agent already
has the prior session's mode in context. To reflect the swap
immediately, the user starts a fresh session.
