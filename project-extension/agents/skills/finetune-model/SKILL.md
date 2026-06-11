---
name: finetune-model
description: Track 2 quickstart wrapper for structured fine-tune prompts using a registry model, a registry dataset, Rockie GPU jobs, and inference-loader deployment of the trained artifact.
---

# finetune-model

Run the Quickstart Track 2 fine-tune flow for a selectable registry model and
dataset, then deploy the trained artifact through Rockie's inference loader.
This skill composes existing platform APIs; it must not create a separate
training service or run training locally.

## When to invoke

- Structured Quickstart Track 2 prompts:

  ```text
  Quickstart fine-tune request:
  track: finetune
  model: <registry model slug>
  registry_dataset_id: <registry dataset id>
  compute_target: rockie_gpu
  source: quickstart-picker
  ```

- Equivalent structured lab prompts that explicitly ask to fine-tune a
  selected registry model on a selected registry dataset.

Do not invoke this skill for open-ended model selection, dataset creation,
private tenant data ingestion, or custom training research. Route those to the
appropriate planning or data workflow first.

## Required v1 inputs

- `model`: the registry model slug or id from the picker or user input.
- `registry_dataset_id`: the registry dataset id from the picker or user input.

Both fields are required for v1. If either is missing, stop and ask for that
field. Do not infer a dataset from free text and do not treat a private data reference as selectable.

Explicit refusal: `private_data_ref` is not supported in v1. Refuse v1 requests
that provide `private_data_ref`, even if the prompt says it has been filtered,
until the per-tenant data API from issue #1298 exposes a validated training
handle.

## Runtime auth

All Rockie control-plane calls require both headers:

```bash
-H "X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN" \
-H "X-Tenant-Id: $ROCKIELAB_TENANT_ID"
```

`ROCKIELAB_TENANT_TOKEN` is the control-plane auth token. `ROCKIELAB_TENANT_ID`
is tenant scope/display identity only. Never use `ROCKIELAB_TENANT_ID` as
`X-Tenant-Token`.

## Preflight before GPU spend

Before any job submission, budget approval, or GPU spend:

1. Preflight the model:

   ```bash
   GET $ROCKIELAB_API_URL/api/registry/models/{slug}
   ```

   The model must be selectable for fine-tuning and must be a v1-supported
   `<7B` text-generation base model.

2. Preflight the dataset:

   ```bash
   POST $ROCKIELAB_API_URL/api/registry/datasets/resolve-for-track
   ```

   The request must include `track: "finetune"` and `registry_dataset_id:
   "<id>"`. The resolved dataset must be selectable for Track 2.

If either preflight fails, stop before spend and surface the platform response.

Do not perform launcher-style credit, balance, spend-cap, membership, or local capacity checks. The existing budget-term-sheet gate and platform HTTP 402 path own spend approval and insufficient-credit handling. If the Rockie platform returns HTTP 402 at submit or load time, surface the platform 402 response through the shared redaction/sanitization boundary.

## Local execution is forbidden

This skill must never run model training or model download work locally. Do not import or run `torch`, `triton`, CUDA libraries, `transformers`, `peft`, `datasets`, `accelerate`, bitsandbytes, vLLM, Docker training containers, or any local GPU/training stack on the orchestrator or skill host. Do not download model weights or datasets to the local workspace. Do not start local training, local serving, direct SSH jobs, supplier CLIs, or direct provider APIs.

All GPU training must go through the Rockie job platform using
`skills/experiment/runtime/submit.py`. All serving must go through the Rockie
inference-loader APIs.

## Fine-tune job contract

Generate a single-GPU LoRA/SFT bash script for v1-supported `<7B` models. The
script runs inside the Rockie job pod and may install or import training
libraries there, not locally.

The script must:

- use the preflighted model and resolved `registry_dataset_id`
- save trained outputs under `/workspace/results/fine-tuned-model/`
- archive the trained output as `/workspace/results/fine-tuned-model.tar.gz`
- print structured progress lines exactly as:

  ```text
  FINETUNE_PROGRESS {"step":12,"loss":0.42}
  ```

- print `JOB_DONE` only after the archive is written

Use the `/experiment` helper, not raw `curl`, for job submission:

```bash
python skills/experiment/runtime/submit.py \
  --gpu-type A100_80GB \
  --gpu-count 1 \
  --script-file ./finetune-track2.sh \
  --timeout 14400 \
  --term-sheet-json ./approved-term-sheet.json \
  --region <approved-region> \
  --tier <approved-tier> \
  --origin-skill finetune-model \
  --monitoring-profile-id experiment.ml_baseline.v1
```

The submit helper must remain in the path so the shared budget-term-sheet gate,
dashboard metadata, and platform HTTP 402 handling remain authoritative. When
dashboard metadata is available, pass `origin_skill: "finetune-model"` and a
training monitoring profile.

`skills/experiment/runtime/submit.py` tails job logs. Changed
`job.last_log_line` values beginning `FINETUNE_PROGRESS ` with a JSON suffix
are the progress contract. Valid JSON fields `step` and `loss` are appended to
the dashboard as `training.step` and `training.loss` metrics, and a concise
progress line is printed to stdout. Malformed progress lines are ignored safely.

## Artifact handoff and deployment

After the job reaches `DONE`, fetch artifacts:

```bash
GET $ROCKIELAB_API_URL/api/jobs/{job_id}/artifacts
```

Select `results/fine-tuned-model.tar.gz`. Fail closed if that artifact is absent.
Deploy the trained artifact through the inference loader:

```bash
POST $ROCKIELAB_API_URL/api/inference/loads
```

The load body must use the first-class job artifact URL:

```json
{
  "url": "rockie-job-artifact://<job_id>/results/fine-tuned-model.tar.gz",
  "compute_target": "rockie_gpu",
  "keep_running": true,
  "dashboard": {
    "origin_skill": "finetune-model",
    "run_name": "Quickstart fine-tune deploy: <model>",
    "monitoring_profile_id": "inference.batch_baseline.v1"
  }
}
```

Do not use temporary signed HTTPS artifact URLs for the trained model handoff.
Do not deploy by downloading the artifact locally.

Poll endpoint readiness with:

```bash
GET $ROCKIELAB_API_URL/api/inference/loads/{id}/endpoint/wait
```

Use `GET /api/inference/loads/{id}` and `GET /api/inference/loads/{id}/logs`
only for status and failure inspection.

## Required final lab output

The final response must include all of:

- endpoint URL from the loader endpoint response; use this value directly as
  `ENDPOINT_URL` because it already includes the modality path
- bearer-token handling; if the loader returns a bearer token, show it as the
  value to use in `Authorization: Bearer ...`; if the platform withholds or
  rotates it, explain where the lab/runtime obtains the token without
  inventing one
- copy-paste curl using the endpoint URL and bearer auth header
- `job_id`
- artifact reference:
  `rockie-job-artifact://<job_id>/results/fine-tuned-model.tar.gz`
- `load_id`

Example curl:

```bash
curl "$ENDPOINT_URL" \
  -H "Authorization: Bearer $ROCKIE_INFERENCE_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"fine-tuned-model","messages":[{"role":"user","content":"hello"}]}'
```
