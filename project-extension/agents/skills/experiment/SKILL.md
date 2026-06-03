---
name: experiment
description: Run a materials-science / ML compute job on Pebble ML's GPU fleet. Trigger words "run experiment", "submit job", "use the experiment skill". Picks the right GPU type and count from a natural-language description (DFT for QE/VASP/ABINIT, MD for GROMACS/LAMMPS/OpenMM, training for PyTorch/JAX), generates the script, posts it to `POST $ROCKIELAB_API_URL/api/jobs/submit`, polls status, streams logs, and surfaces the final artifacts. Use this for anything that needs a GPU — single A100 up to multi-pod B200 clusters.
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

## First-experiment GPU-mode disclosure

Before submitting the user's FIRST experiment in a workspace (i.e.
neither `.codex/gpu-custom.md` exists nor
`$ROCKIE_GPU_MODE` is set), the agent emits **one neutral sentence**
before proceeding:

> Your options are Rockie GPU (Rockie provisions the pod and the
> per-hour price it quotes already includes everything) or your own
> hardware (`ROCKIE_GPU_MODE=custom` then ask Codex to run
> gpu-custom-setup). Default is Rockie GPU — proceeding with that
> unless you say otherwise.

That's the entire disclosure. Do NOT:
- repeat it on subsequent experiments
- pitch Rockie GPU with adjectives like "easy" or "best"
- compare to specific competitors by name
- nag if the user is quiet — just proceed with Rockie GPU

The user can opt into custom mode at any time by setting
`ROCKIE_GPU_MODE=custom`; the next experiment run will see the env
and trigger gpu-custom-setup for the one-time flow audit.

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

Call the helper at `runtime/submit.py` (don't shell out to curl
directly — the helper handles the credit-balance pre-check, the
state-machine polling, and the SSE log tail):

```bash
python3 ${SKILL_DIR}/runtime/submit.py \
    --gpu-type A100_80GB \
    --gpu-count 1 \
    --script-file /tmp/experiment.sh \
    --timeout 14400
```

Required env: `ROCKIELAB_API_URL` (e.g. `https://api.rockielab.com`)
and `ROCKIELAB_TENANT_TOKEN` (per-tenant). The submit helper exits
with the job's exit code (0 = DONE, non-zero = FAILED/CANCELLED).

## Surfacing results

After `submit.py` returns, fetch the artifact list:

```bash
curl -s "$ROCKIELAB_API_URL/api/jobs/${JOB_ID}/artifacts" \
    -H "X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN"
```

Each artifact has a `signed_url` (1h TTL). Surface them to the user
with sizes; do NOT auto-download large files unless asked.

## Cost-awareness

Before submitting, check the credit balance:

```bash
curl -s "$ROCKIELAB_API_URL/api/jobs/credit-balance?tenant_id=$TID" \
    -H "X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN"
```

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
    --script-file /tmp/dft.sh --timeout 3600
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
    --script-file /tmp/md.sh --timeout 7200
```
