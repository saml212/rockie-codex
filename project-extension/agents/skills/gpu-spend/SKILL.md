---
name: gpu-spend
description: When the user (or you) needs to know GPU spend — "what's my burn rate?", "how much have I spent this week?", "is anything still running?", "am I close to budget?", "what's running idle?" — invoke this. Wraps `rockie-gpu spent --json` (the deidentified Rockie-GPU spend surface) for accurate, reconciled numbers, then summarizes for the user. `rockie-gpu` is the single GPU surface: it never names the underlying compute supplier and never exposes a supplier API key. **Custom-mode users:** if `ROCKIE_GPU_MODE=custom`, invoke `/gpu-custom` instead — `rockie-gpu` is bypassed in that mode.
---

# /gpu-spend — Rockie-GPU spend snapshot

The single source of truth for "what is the agent costing me right now?"
`rockie-gpu spent` reconciles live state, sums project spend, and prints
both the LLM-readable JSON and a human view — all under Rockie's own
deidentified billing surface, with no supplier names and no supplier
billing URLs leaking through.

## When to invoke

- User asks anything about cost, spend, rate, burn, budget, or pods running.
- Before any decision to spin up a new GPU (read state before adding load).
- After terminating a pod, to confirm the bleed has stopped.
- Periodically during long autonomous runs (the budget-reconcile.sh hook
  fires this on every UserPromptSubmit, but the agent should also check
  on entering the Codify phase).

Do NOT invoke if the user asked a non-cost question and you'd just be
showing off. The hook keeps the budget honest in the background.

## What the skill does

Runs:
```bash
rockie-gpu spent --json
```

Output shape (LLM-ergonomic, deidentified — Rockie-priced, no supplier
names):
```json
{
  "project": "...",
  "compute_per_hr": 0.0,
  "storage_per_hr": 0.011,
  "running_pods": 0,
  "idle_volume_gb": 80,
  "grand_total_per_hr": 0.011,
  "grand_cumulative_usd": 2.20
}
```

Read the JSON. Synthesize a 2–3 line summary for the user that:
- Names the top spend driver (compute or storage).
- Calls out idle storage if `running_pods=0` but `idle_volume_gb > 0`
  (user is paying for nothing — recommend tearing the pod down).
- Compares `grand_cumulative_usd` to budget ceiling if known
  (`rockie-gpu spent --json` reports the ceiling when one is configured).

## Composition

- **`rockie-gpu spent`** reconciles implicitly, so numbers are always
  fresh (within seconds of the call). No separate reconcile step.
- **`rockie-gpu audit-open-spend --demo-safe`** is the line-item audit
  when the user wants the full breakdown of where the dollars went — it
  is the only drill-down surface. There is no per-supplier CLI to reach
  for; `rockie-gpu` sees every dollar.

## Agent invocation template

```
Run `rockie-gpu spent --json`. Read the JSON.
Summarize in 2-3 lines:
1. Top spend driver (compute or storage).
2. Idle storage warning if any.
3. Cumulative-vs-ceiling status.
For a full breakdown, run `rockie-gpu audit-open-spend --demo-safe`.
No more than 6 lines total.
```

## Why JSON, not the human table

The human `rockie-gpu spent` (no flag) is for users; LLMs should always
read `--json` because:
- Stable shape across versions; the table prettifies and may break parsers.
- Rates as floats, not strings — you can compare directly.
- `grand_cumulative_usd` and the rate fields are first-class, not
  buried in formatted output.
