---
name: budget-term-sheet
description: Embedded /experiment subskill that builds a pre-deploy Rockie GPU budget term sheet before any Rockie-originated experiment submit. Trigger through /experiment when the user asks to quote the GPU budget, show the term sheet, modify approval, or submit GPU / torch / triton / training / weight-download work.
---

# /experiment budget-term-sheet subskill

Render the user-facing cost term sheet that Rockie must show before a Rockie-originated
`POST $ROCKIELAB_API_URL/api/jobs/submit` call. This skill is pre-submit UX and
approval gating only. It does not replace platform-context's credit reservation,
day caps, or hard budget enforcement.

## When to invoke

- Before `/experiment` submits a GPU / torch / triton / training / weight-download job.
- When the user asks for a dry-run quote without launching compute.
- When a user changes the approved budget and Rockie needs to re-render the quote.

## Hard rules

- Rockie-originated GPU submits go through this subskill before `runtime/submit.py`.
- Only explicit approval tokens count: `approve`, `approved`, or `I approve`.
- A user-modified budget below `estimate_cents` is non-submittable in this repo.
- Non-available market states never submit.
- Keep the user-facing provider deidentified: `Rockie GPU`, plus SKU/count/region/tier.
- Do not dispatch real compute here. Quote and approval only.

## Procedure

1. Assemble a job spec with `job_shape`, `gpu_type`, `gpu_count`, `region`, `tier`, and either
   `timeout_seconds` or `wallclock_minutes`. Use `region: "us"` and `tier: "spot"` unless the user or workflow requires a different visible capacity slice.
2. Run `${OPENCLAW_SKILLS_DIR}/experiment/subskills/budget-term-sheet/scripts/quote_term_sheet.py` to build the canonical JSON term-sheet object.
   Prefer live quote endpoints when credentials exist; use `--market-json` for tests
   and dry runs; use `--allow-heuristic-dry-run` only when the user explicitly wants
   a heuristic quote.
3. Render the markdown block with `${OPENCLAW_SKILLS_DIR}/experiment/subskills/budget-term-sheet/scripts/render_term_sheet.py`. The block must show:
   total estimate, stage breakdown, recommended budget, wall-clock, Rockie GPU
   compute summary, availability/confidence, and the explicit `Approve / Modify / Cancel`
   prompt.
4. Wait for a user reply. Parse it with `${OPENCLAW_SKILLS_DIR}/experiment/subskills/budget-term-sheet/scripts/parse_approval.py`.
5. Decision handling:
   - `approve`: keep the current budget and mark the term sheet approved.
   - `modify`: re-render with the new `user_budget_cents` and ask again.
   - `modify_then_approve`: re-render with the new budget; only submit if the
     modified budget is still `>= estimate_cents`.
   - `cancel`: abandon the submit.
   - `clarify`: ask again; do not submit.
6. Hand the final approved JSON artifact to `/experiment/runtime/submit.py` through
   `--term-sheet-json`, passing matching `--region` and `--tier`. The submit helper
   refuses artifacts or CLI requests that omit or change the approved region/tier.
   Do not shell raw `curl POST /api/jobs/submit`.

## Commands

Quote JSON:

```bash
python3 ${OPENCLAW_SKILLS_DIR}/experiment/subskills/budget-term-sheet/scripts/quote_term_sheet.py \
  --job-spec-json /tmp/job-spec.json \
  > /tmp/term-sheet.json
```

Render markdown:

```bash
python3 ${OPENCLAW_SKILLS_DIR}/experiment/subskills/budget-term-sheet/scripts/render_term_sheet.py \
  --term-sheet-json /tmp/term-sheet.json
```

Parse the user's reply:

```bash
python3 ${OPENCLAW_SKILLS_DIR}/experiment/subskills/budget-term-sheet/scripts/parse_approval.py \
  --estimate-cents 20000 \
  --reply "cap it at $250 and I approve"
```

## Canonical object

The term-sheet JSON must carry these fields:

- `quote_id`
- `job_shape`
- `availability`
- `status_code`
- `status_reason`
- `quote_source`
- `generated_at`
- `compute`
- `stages`
- `estimate_cents`
- `recommended_budget_cents`
- `user_budget_cents`
- `wallclock_minutes`
- `confidence_bucket`
- `confidence_statement`
- `decision`

Use `references/template.md` as the user-facing block shape.
