---
name: autoresearch
description: Canonical around-the-clock research loop. Defines the agent's outer loop — read taste corpus + queue, pick the next experiment, mutate the explicitly-declared mutation surface, run the experiment under a hard time budget against a frozen metric, score, codify, repeat. Augmented with Karpathy's sharp primitives (frozen metric, time cap, explicit mutation surface).
---

# autoresearch

The canonical loop that distinguishes Rockie from a chat assistant.
This is what runs around-the-clock on the tenant's Fly machine.

The bones of this skill predate Karpathy's autoresearch (rockie's
existing autopilot + queue + post-run-review primitives are richer than
Karpathy's narrow loop). Phase 7 augmented those bones with three
sharp Karpathy primitives — frozen metric, time-budget per experiment,
explicit mutation surface — without stripping the broader system.

## When to invoke

- The autopilot daemon invokes this skill on every queue dequeue.
- A researcher invokes manually for a one-off experiment outside the
  queue (`/autoresearch run-once …`).
- The post-run-review skill calls back here with a follow-up experiment
  derived from a [LEARN] block.

## The canonical loop

```
┌─ taste corpus (immutable for the run) ────────────┐
│  SOUL / STYLE / METHODOLOGY / DISMISSALS / MEMORY │
│  + program.md (Karpathy convention) if present    │
└────────────────────┬───────────────────────────────┘
                     │
            ┌────────▼────────────┐
            │ PICK from queue or  │   ← queue-refill skill keeps this stocked
            │ from a [LEARN] tip  │
            └────────┬────────────┘
                     │
                     ▼
       declare mutation_surface[]  ← explicit; every other path is read-only
                     │
                     ▼
         lock metric (metric_locked=1)  ← Karpathy: no goalpost-shifting
                     │
                     ▼
         set time_budget_seconds         ← Karpathy: hard cap, default 300s
                     │
                     ▼
        ┌────────────────────────┐
        │ propose change to ANY  │
        │ file in mutation_surface│   (everything else is immutable)
        └────────────┬────────────┘
                     │
                     ▼
              run the experiment
                     │
                     ▼
        evaluate against the frozen metric
                     │
              ┌──────┴──────┐
        improvement?     no improvement?
              │              │
              ▼              ▼
         git commit     git revert
              │              │
              ▼              ▼
       post-run-review (always)
              │
              ▼
      [LEARN] / [DEAD-END] capture
              │
              ▼
       calibration row (predicted vs actual delta)
              │
              ▼
       [if best-so-far] code-pool admit
              │
              └──→ next iteration
```

## Karpathy primitives — what they buy us

### 1. Frozen metric (`metric_locked = 1`)

Once an experiment starts, the metric is locked. No mid-run redefinition,
no "we should also track X" feature creep. Every iteration in the run
is comparable to every other iteration of the same run. Goalpost-shifting
is the failure mode this prevents — agents are tempted to add metrics
that flatter their changes.

**Convention:** the `metric_name` and `lower_is_better` fields on the
experiments row are set at queue-claim time. Toggling `metric_locked = 1`
means: until this run is `closed_at`, those two fields are immutable.

### 2. Per-experiment time cap (`time_budget_seconds`)

Karpathy's repo defaults to 300s (5 min). Rockie defaults to the same;
override per experiment based on workload (DFT might be 1800s; a quick
hyperparameter check might be 60s). The cap is HARD — once exceeded,
the runner kills the process and records a `failure_class = 'timeout'`.

This forces fast iteration. Slow experiments hide under "still running"
forever; a hard cap makes them visible.

### 3. Explicit mutation surface (`mutation_surface[]`)

Each experiment declares which file paths are mutable for THIS run.
Everything else is immutable for the duration. The runner enforces by
reverting any out-of-surface changes before evaluation.

Karpathy's convention: just `train.py`. Rockie's mutation_surface is a
list — most experiments only mutate one file, but model-architecture
sweeps may need to mutate `model.py` AND `train.py`. The point is
EXPLICIT declaration, not the count.

This makes diffs interpretable, prevents accidental config edits, and
gives downstream code-pool admission a clean "what changed" delta.

## What rockie has that Karpathy doesn't (don't lose this)

These are the broader primitives the taste corpus + memory schema
already provide:

- `dead_ends` registry — directions tried + rejected, with reasons.
  Future agents won't redo your failed work.
- `hypothesis_calibration` — predicted vs actual metric delta. Tracks
  whether your hypothesis-quality is improving over time.
- `code_pool` — best-K archive across runs. Cherry-pick winning ideas
  forward.
- `experiments` tree — parent/child journal lets you bisect your own
  research lineage.
- Multi-provider GPU procurement (RunPod, Vast, Datacrunch, Prime).
  Karpathy assumes one GPU on the desk.

## Procedure

```
# 1. Pick an experiment (queue-refill keeps the queue stocked).
#    The autopilot daemon does this; manual: pick a `pending` row from
#    experiment_queue ORDER BY priority, created_at LIMIT 1.

# 2. Lock metric + budget + surface from the queue row:
EXP_ID=$(.claude/skills/autoresearch/scripts/start.sh QUEUE_ID)

# 3. Mutate. The agent edits files in mutation_surface. Anything else
#    triggers an immediate revert.

# 4. Run the experiment under the budget:
.claude/skills/autoresearch/scripts/run.sh "$EXP_ID"
# (kills the process when time_budget_seconds elapses; records timeout
# as failure_class=timeout)

# 5. Evaluate. The runner reads the metric_value and compares against
#    the prior best for the metric_name (with lower_is_better in mind).

# 6. Commit if improved, revert if not. Either way, post-run-review
#    runs and emits [LEARN]/[DEAD-END]. The autopilot loop continues.
```

## Anti-patterns

- **Unlocking the metric mid-run** to "also track X" — defeats the
  purpose. Lock it; if you genuinely need a different metric, that's a
  new run.
- **Skipping the time cap** — slow experiments creep into "always
  running". Better to hit the cap and record `timeout` than to silently
  block the queue.
- **Defaulting mutation_surface to "the whole repo"** — that's no
  surface at all. Pick one to three files. If you need more, you may
  be conflating two experiments.
- **Optimising without taste** — `program.md` (Karpathy convention) or
  the SOUL/STYLE/METHODOLOGY corpus (rockie convention) MUST be loaded
  into the agent's context every iteration. Without taste, you get
  technically-correct-but-research-garbage output.

## Schema additions (migration 005)

See `platform-skills/memory/migrations/005_karpathy_primitives.sql`:

- `experiments.metric_locked` (0/1)
- `experiments.time_budget_seconds` (int, NULL = no cap)
- `experiments.mutation_surface` (TEXT, JSON-encoded list of paths)
- Same three columns on `experiment_queue` for pre-population.

## References

- karpathy/autoresearch (March 2026) — vendored at `rockie-workspace/references/karpathy-autoresearch/` (Phase 7 follow-up — not yet pulled).
- Sakana AI Scientist (sakana.ai/ai-scientist) — broader-scope alternative; rockie's primitives are closer to Sakana shape than Karpathy's narrow loop.
- DeepMind FunSearch (arxiv.org/abs/2302.12588) — function-discovery flavor; not the Rockie pattern.

## Sequencing

The autoresearch loop runs WITHIN a runtime variant — rockie-claude /
rockie-codex / rockie-byok. The Phase 6 alignment agent keeps the
overlay copies of this skill in sync across the three. The post-run-review
+ queue-refill + autopilot skills are the loop's neighbors and must
stay coherent with the conventions documented here.
