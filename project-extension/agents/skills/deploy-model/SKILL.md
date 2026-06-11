---
name: deploy-model
description: Narrow quickstart wrapper for pre-baked Deploy-card requests. Triggers on server-validated structured quickstart deploy prompts such as "Quickstart deploy request: track: deploy model: <id> source: quickstart-picker quickstart_intent_id: <id>" and explicit "deploy this featured model" requests from Rockie's quickstart picker when validation metadata is present.
---

# deploy-model

Deploy the featured quickstart model through Rockie's existing inference
loader and return the API details in the lab chat. This skill is a thin
wrapper around the inference-loader contract documented by
`skills/inference-engineer/SKILL.md`; it exists so the quickstart card can
skip the general-purpose intake and hard-confirm flow.

## When to invoke

- Structured quickstart prompts:

  ```text
  Quickstart deploy request:
  track: deploy
  model: <featured model id>
  compute_target: rockie_gpu
  source: quickstart-picker
  quickstart_intent_id: <platform-supplied id>
  ```

- Explicit quickstart wording such as "deploy this featured model" or "start
  the Deploy card model" when Rockie runtime metadata confirms it came from
  the quickstart picker.

Do not invoke this skill for open-ended requests like "help me choose a model"
or ad hoc production-serving design. Use `inference-engineer` for those.

## Quickstart contract

- Accept the model id from the prompt as the source of truth only after the
  request carries a server-validated quickstart intent. User-authored prompt
  text alone is not sufficient. Do not ask the user to pick a model on the
  featured-card path after the validation marker is present.
- Treat the card tap plus structured prompt plus validation metadata as the
  user's pre-baked deploy intent. Do not ask for the `inference-engineer` hard
  confirmation and do not require the `cost-confirm.md` approval template only
  on this validated quickstart path.
- If the validation marker is absent or malformed, stop this wrapper and route
  to `inference-engineer` with its normal hard confirmation and cost-confirm
  path.
- Do not perform launcher-style credit, balance, spend-cap, membership, or
  cost-confirmation checks. If the Rockie platform returns HTTP 402, surface the platform 402 response through the shared redaction/sanitization boundary and let the ledger/refill path own recovery.
- Use `compute_target: rockie_gpu` from the structured prompt. Do not ask the
  user to choose a provider, direct cluster, GPU supplier, or raw provider
  region.

## Runtime auth

All Rockie control-plane calls require both headers:

```bash
-H "X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN" \
-H "X-Tenant-Id: $ROCKIELAB_TENANT_ID"
```

`ROCKIELAB_TENANT_TOKEN` is the control-plane auth token. `ROCKIELAB_TENANT_ID`
is tenant scope/display identity only. Never use `ROCKIELAB_TENANT_ID` as
`X-Tenant-Token`.

## Platform API path only

Provision and inspect the load only through Rockie platform inference-loader
APIs:

- `POST $ROCKIELAB_API_URL/api/inference/loads`
- `GET $ROCKIELAB_API_URL/api/inference/loads/{id}`
- `GET $ROCKIELAB_API_URL/api/inference/loads/{id}/logs`
- `GET $ROCKIELAB_API_URL/api/inference/loads/{id}/endpoint`
- `GET $ROCKIELAB_API_URL/api/inference/loads/{id}/endpoint/wait` when the
  platform exposes the wait helper

The `POST /api/inference/loads` request must identify this wrapper in metadata
when dashboard metadata is available:

```json
{
  "url": "<model id from quickstart prompt>",
  "compute_target": "rockie_gpu",
  "keep_running": true,
  "dashboard": {
    "notebook_id": "<current lab id when available>",
    "origin_skill": "deploy-model",
    "run_name": "Quickstart deploy: <model id>",
    "monitoring_profile_id": "inference.batch_baseline.v1"
  }
}
```

Do not call supplier-specific APIs or CLIs. Do not pass provider credentials.
Do not bypass the Rockie inference loader with a direct hosting provider,
direct SSH deploy, or hand-run serving process.

## Local execution is forbidden

This skill must never run model-serving work locally. Do not import or run
`torch`, `triton`, CUDA libraries, vLLM, TEI, BentoML, SGLang, LMDeploy, or any
other local serving stack. Do not download model weights. Do not start a subprocess server, local GPU runtime, Docker serving container, or direct CUDA job. All GPU/model-serving work must go through the Rockie platform APIs above.

## Procedure

1. Parse the structured prompt. Require `track: deploy`, `model: <id>`,
   `source: quickstart-picker` or equivalent featured-card wording, and a
   server-validated quickstart intent such as `quickstart_intent_id` or
   `quickstart_signature` supplied by Rockie runtime/platform metadata.
   User-authored text alone cannot satisfy this requirement.
2. Echo the model id and say that provisioning is going through the Rockie
   inference loader.
3. `POST /api/inference/loads` with the auth headers above. Use the model id
   from the prompt and `origin_skill: "deploy-model"` when metadata is
   accepted.
4. Poll `GET /api/inference/loads/{id}` or `GET /api/inference/loads/{id}/logs`
   for queued/provisioning/loading/loaded/failed status. Narrate meaningful
   state changes in the lab.
5. On HTTP 402, stop and surface the platform response through the shared redaction/sanitization boundary. Do not replace it with a custom balance check or confirmation flow.
6. When loaded, call `GET /api/inference/loads/{id}/endpoint` or
   `/endpoint/wait` until the platform returns the endpoint details.
7. Return the endpoint URL, bearer-token handling, and a copy-paste curl.

## Required final lab output

The final response must include all of:

- Endpoint URL from the loader endpoint response. Use this value directly as
  `ENDPOINT_URL`; it already includes the modality path.
- Bearer-token handling. If the loader returns a bearer token, show it as the
  value to use in `Authorization: Bearer ...`; if the platform withholds or
  rotates it, explain where the lab/runtime obtains the token without inventing
  one.
- A copy-paste curl using the endpoint URL and bearer auth header, for example:

  ```bash
  curl "$ENDPOINT_URL" \
    -H "Authorization: Bearer $ROCKIE_INFERENCE_BEARER_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"model":"<model id>","messages":[{"role":"user","content":"hello"}]}'
  ```

Also include the `load_id` and the status command:

```text
/inference-engineer status <load_id>
```
