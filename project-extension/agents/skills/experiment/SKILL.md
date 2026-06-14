---
name: experiment
description: Run a materials-science / ML compute job on Rockie GPU capacity. Trigger words "run experiment", "submit job", "use the experiment skill", or requests to quote/approve GPU spend before an experiment. Picks the right GPU type and count from a natural-language description (DFT for QE/VASP/ABINIT, MD for GROMACS/LAMMPS/OpenMM, training for PyTorch/JAX), generates the script, routes Rockie-originated submits through the embedded budget-term-sheet gate plus `runtime/submit.py`, polls status, streams logs, and surfaces the final artifacts. Use this for anything that needs a GPU — single A100 up to multi-pod B200 clusters.
---

# experiment — submit a GPU job

Wraps the Phase 5 job runner. The agent decides the GPU shape, writes
the script, hands it to platform-context, and tails the run.

## When to invoke

- User asks to "run an experiment", "submit a job", "kick off a
  calculation", "train this model".
- User describes a calculation that obviously needs a GPU (DFT,
  AIMD, large-scale MD with PME, model fine-tuning, inference batch).
- User asks to use the experiment skill directly.

If the task is local-only (pre-processing, data wrangling, plotting),
do NOT use the experiment skill — run it inline. The skill is for
GPU-bound work that the user is willing to pay GPU credit for.

## First-experiment GPU disclosure

Before submitting the user's FIRST experiment in a workspace, the agent
emits **one neutral sentence** before proceeding:

> Rockie provisions the GPU pod for this experiment, and the per-hour price
> it quotes already includes the platform overhead; proceeding unless you
> want to change the scope.

That's the entire disclosure. Do NOT:
- repeat it on subsequent experiments
- pitch Rockie GPU with adjectives like "easy" or "best"
- compare to specific competitors by name
- offer BYO mode, user-provided GPU mode, or a router bypass
- nag if the user is quiet — just proceed with Rockie GPU

## Picking GPU shape

| Workload | Default | Notes |
|---|---|---|
| QE / VASP / ABINIT DFT, single SCF | 1x A100_80GB | Most DFT fits in 80GB. |
| Large-cell DFT (>500 atoms) | 4x A100_80GB | Needs MPI, use Instant Cluster. |
| AIMD (BOMD with QE/CP2K) | 2x A100_80GB | I/O-bound; 2 pods is cheaper than 1xH100. |
| GROMACS / LAMMPS / OpenMM | 1x A100_80GB | Single GPU saturates most MD. |
| PyTorch fine-tune (<7B params) | 1x A100_80GB | |
| PyTorch fine-tune (7-70B params) | 4x H100_SXM | Tensor parallel; H100 SXM has NVLink. |
| Frontier model training | 8x H200 / B200 | Reach for B200 only when the user explicitly asks for it (it's the priciest SKU per GPU-hour). |

When in doubt, ask the user once: "1 GPU or 4? A100 or H100?" Then
commit. Don't ping-pong.

## Generating the script

The script is a bash file that runs end-to-end on the pod's
`/workspace`. Pre-fab templates by domain:

- **DFT (QE)**: pull cif/poscar inputs, run `pw.x < input.in > output.out`, dump to `/workspace/results/`.
- **MD (LAMMPS)**: stage the input deck under `/workspace/run/`, `mpirun -np $GPU_COUNT lmp -i in.lammps`.
- **PyTorch training**: `accelerate launch --num_processes=$GPU_COUNT train.py ...` with the dataset path threaded in via `env`.

Always end the script with: tar the results dir to `/workspace/results.tar.gz`, then `echo "JOB_DONE"` so the platform's stdout watcher can detect completion.

## Submitting

Before any Rockie-originated submit, invoke the embedded budget-term-sheet
subskill, render the quote, and wait for explicit user approval. Hand the
approved JSON artifact to `runtime/submit.py` (don't shell out to curl
directly — the helper handles the credit-balance pre-check, the
state-machine polling, the SSE log tail, the budget-term-sheet gate, and
the dashboard Note metadata/profile snapshot packaging):

```bash
python3 ${SKILL_DIR}/runtime/submit.py \
    --gpu-type A100_80GB \
    --gpu-count 1 \
    --region us \
    --tier spot \
    --script-file /tmp/experiment.sh \
    --timeout 14400 \
    --term-sheet-json /tmp/term-sheet.approved.json
```

Required env: `ROCKIELAB_API_URL` (e.g. `https://api.rockielab.com`),
`ROCKIELAB_TENANT_ID`, and `ROCKIELAB_TENANT_TOKEN`. The token
authenticates the request; it is not tenant identity, and the helper
does not fall back to an implicit tenant. The submit helper exits with
the job's exit code (0 = DONE, non-zero = FAILED/CANCELLED).

Budget gate contract:

- `--term-sheet-json` is required by default for Rockie-authored submits.
- The helper only accepts final term-sheet decisions `approve` or
  `modify_then_approve` when the term sheet is available, approvable,
  and explicitly approved for submit.
- The approved term sheet must include and match submit GPU type/count,
  `compute.region`, `compute.tier`, and quoted wallclock. Pass matching
  `--region` and `--tier`; omitting or changing either field refuses
  submit locally.
- If the final budget is below `estimate_cents`, the helper refuses
  locally before the HTTP submit.
- Approved term sheets must include `user_budget_cents`; the helper
  refuses to fall back to `recommended_budget_cents` for submit.
- `--allow-ungated-submit` exists only for legacy/manual paths; do not
  use it in normal Rockie skill flows.
- Optional `--budget-cents` is only an assertion and must exactly equal
  the approved term sheet's `user_budget_cents`.

### Budget-term-sheet subskill

The pre-submit quote/approval flow lives at
`${SKILL_DIR}/subskills/budget-term-sheet/` instead of as a top-level
skill. Use its scripts directly when the user asks for a dry-run quote,
changes a budget, or is about to submit Rockie-authored GPU work:

```bash
python3 ${SKILL_DIR}/subskills/budget-term-sheet/scripts/quote_term_sheet.py \
    --job-spec-json /tmp/job-spec.json \
    > /tmp/term-sheet.json
python3 ${SKILL_DIR}/subskills/budget-term-sheet/scripts/render_term_sheet.py \
    --term-sheet-json /tmp/term-sheet.json
python3 ${SKILL_DIR}/subskills/budget-term-sheet/scripts/parse_approval.py \
    --estimate-cents 20000 \
    --reply "I approve"
```

Dashboard Note contract:

- Pass `--notebook-id <notebook:...>` when the run belongs to a lab note flow.
- When a notebook id is present, the helper sends a `dashboard` payload
  with `run_name`, `origin_skill`, `software`, `monitoring_profile_id`,
  and a frozen `monitoring_profile_snapshot`. Runs without notebook
  context remain legacy job submissions and omit the dashboard block.
- Default `origin_skill` is `experiment`.
- If you do not set `--monitoring-profile-id`, the helper infers one from
  the script/software when possible and otherwise falls back to
  `common.default.v1` with `unprofiled=true` recorded in the snapshot.
- For PyTorch/JAX training and generic ML experiments, prefer
  `experiment.ml_baseline.v1`.
- For physics plans, pass the exact physics profile id already attached
  by the physics router; the helper can also infer representative
  adapters such as `physics.molecular_dynamics.v1` for GROMACS/LAMMPS or
  `physics.electronic_structure.v1` for QE/CP2K/ABINIT scripts.

## Surfacing results

After `submit.py` returns, fetch the artifact list:

```bash
curl -s "$ROCKIELAB_API_URL/api/jobs/${JOB_ID}/artifacts" \
    -H "User-Agent: rockie-runtime/1.0 (+https://api.rockielab.com)" \
    -H "X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN" \
    -H "X-Tenant-Id: $ROCKIELAB_TENANT_ID"
```

Each artifact has a `signed_url` (1h TTL). Surface them to the user
with sizes; do NOT auto-download large files unless asked.

## Parent-owned monitor contract

The experiment skill owns monitoring for the GPU jobs it submits. Status
summaries and dashboard patches should normalize to this shared contract:

```json
{
  "monitor_owner": "experiment",
  "monitor_target": {"kind": "job", "id": "job-123"},
  "state": "queued|running|loaded|done|failed|cancelled|unknown",
  "utilization": {
    "state": "live|unavailable|not_applicable",
    "reason_code": "telemetry_not_exposed|null"
  },
  "spend": {
    "state": "live|settled|partial|unavailable",
    "reason_code": "spend_not_exposed|spend_budget_only|null"
  },
  "artifacts": {
    "dashboard_note_id": "note:dash/1",
    "profile_id": "experiment.ml_baseline.v1",
    "profile_snapshot_present": true
  }
}
```

Keep spend and utilization independent. When the job status API does not
expose live utilization, return `utilization.state: "unavailable"` with
`reason_code: "telemetry_not_exposed"` rather than fake zeroes. Queue,
running, and terminal polls should preserve the same envelope and keep
dashboard metadata attached when it exists, but do not forward raw
sensitive `artifact_path` values in the outward monitor envelope. Treat
`cost_so_far_cents` or `cost_actual_cents` as observed spend. Budget or
hourly-rate guidance without observed spend must never look live; return
`spend.state: "partial"` with `reason_code: "spend_budget_only"` until
observed spend arrives. When only terminal `cost_actual_cents` is
present, return `spend.state: "settled"` with a null `reason_code`
instead of claiming the spend is live. Preserve legitimate zero values.

The shared runtime may still emit upstream `LOADED` for a running job;
the monitor contract normalizes that to `loaded` so downstream consumers
see a stable lowercase state.

## Cost-awareness

Before submitting, check the credit balance:

```bash
curl -s "$ROCKIELAB_API_URL/api/jobs/credit-balance?tenant_id=$ROCKIELAB_TENANT_ID" \
    -H "User-Agent: rockie-runtime/1.0 (+https://api.rockielab.com)" \
    -H "X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN" \
    -H "X-Tenant-Id: $ROCKIELAB_TENANT_ID"
```

Do not infer the tenant from the token or use an implicit self tenant.

If the projected cost (timeout × marked-up rate × gpu_count + overhead)
exceeds the balance, tell the user and offer to top up via Stripe
Checkout (the `/compute` page has the buttons).

## Output template

Keep the user-facing summary to ~5 lines:

```
Job: <job_id>
Shape: <gpu_count>x<gpu_type>
Estimated cost: $<X.YY>
State: <STATE>
Artifacts: <N file(s)>, signed URLs valid for 1h
```

## Worked example: DFT (Quantum Espresso pw.x, silicon BCC)

Single-A100 SCF on a 2-atom silicon cell. Most DFT defaults to 1 GPU;
escalate to 4 only when the user is explicit (large unit cell or AIMD).

```bash
cat > /tmp/dft.sh <<'SH'
#!/bin/bash
set -euo pipefail
mkdir -p /workspace/results
mpirun -np ${GPU_COUNT:-1} pw.x \
    -input /workspace/inputs/silicon.in \
    > /workspace/results/silicon.out 2>&1
tar -czf /workspace/results.tar.gz -C /workspace results
echo "JOB_DONE"
SH
python3 ${SKILL_DIR}/runtime/submit.py \
    --gpu-type A100_80GB --gpu-count 1 \
    --region us --tier spot \
    --script-file /tmp/dft.sh --timeout 3600 \
    --term-sheet-json /tmp/dft.term-sheet.approved.json
```

The artifact list will surface `silicon.out` (the SCF energy is on the
`!    total energy` line). Pull it before the 1h URL TTL elapses.

## Worked example: MD (GROMACS, lysozyme in water)

Standard equilibration step on a single A100 — GROMACS saturates one
80GB card for systems up to ~100k atoms. The trajectory file `md.xtc`
is the artifact you want to pull back; the energy log goes via
`stdout.log` over SSE.

```bash
cat > /tmp/md.sh <<'SH'
#!/bin/bash
set -euo pipefail
cd /workspace/run
gmx grompp -f md.mdp -c npt.gro -p topol.top -o md.tpr
gmx mdrun -deffnm md -nb gpu -bonded gpu -update gpu -ntmpi 1
mkdir -p /workspace/results
cp md.xtc md.edr md.log /workspace/results/
tar -czf /workspace/results.tar.gz -C /workspace results
echo "JOB_DONE"
SH
python3 ${SKILL_DIR}/runtime/submit.py \
    --gpu-type A100_80GB --gpu-count 1 \
    --region us --tier spot \
    --script-file /tmp/md.sh --timeout 7200 \
    --term-sheet-json /tmp/md.term-sheet.approved.json
```
