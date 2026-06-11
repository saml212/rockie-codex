---
name: inference-engineer
description: Productize an open-source model into a hosted inference endpoint the researcher (or their agent) can call. Picks the right hardware, the right serving stack (vLLM / Triton / TEI / BentoML), wraps it in an OpenAI-compatible gateway (LiteLLM) with per-tenant auth, exposes it as an MCP tool in chat, and runs a quality + latency + cost probe so the user knows what they actually shipped. Triggers on `/inference-engineer [model-url]`, or on natural intent like "host this model", "serve inference for X", "deploy model Y", "get me an API for Z", "I want to reproduce paper P on a small GPU", "what do I do with this trained model".
---

# inference-engineer

The packaging skill that turns "here is a model" into "here is an API your agent can call." Companion to `/autoresearch` (Rockie's R&D half): autoresearch *produces* models and findings, inference-engineer *operates* them.

## When to invoke

- **Explicit:** `/inference-engineer <model-url-or-HF-repo>` (URL optional; agent will ask for it).
- **Intent-triggered:** any user message expressing one of —
  - *"I want to host / serve / deploy / productize / run inference on / get an API for / make available <some model>"*
  - *"I trained / fine-tuned a model — what do I do with it?"*
  - *"How do I serve this for my agent / app / customers / a batch of N requests?"*
  - *"Reproduce paper / blog / GitHub repo X on a small GPU"*
- **Cascade entry from sub-skills:** the eval / kernel / gateway sub-skills can call back here to re-provision when a user asks to swap hardware or change serving config.

## The two researcher motivations this skill serves

1. **"I found a model — productize it."** A researcher has a HF repo / GitHub repo / paper-with-code link. They want to host it themselves (cost, privacy, latency, customization). They need: right hardware, right serving stack, an API, MCP exposure to their chat agent, quality + cost numbers.
2. **"I trained a model — now what?"** Autoresearch produced a checkpoint. The researcher wants to share it, evaluate it head-to-head against the baseline, or wire it into a downstream agent. They need the same productization path but starting from local weights instead of a public repo.

Both routes converge on the same 8-step orchestration below.

If this skill needs Rockie-managed training, eval, synthetic-data, or
other GPU job execution outside `POST /api/inference/loads`, route that
work through `/experiment` and its `/budget-term-sheet` approval gate.
Do not introduce raw `/api/jobs/submit` calls here.

## Read this before doing anything

The skill reasons from a checked-in research corpus, not from re-Googling. **Before step 1 of any run, read all of:**

- `${OPENCLAW_SKILLS_DIR}/inference-engineer/research/serving-stacks.md` — when to pick vLLM vs Triton vs TEI vs BentoML vs SGLang vs LMDeploy vs Dynamo
- `${OPENCLAW_SKILLS_DIR}/inference-engineer/research/api-gateways.md` — why LiteLLM-in-front-of-everything is the 2026 default; alternatives
- `${OPENCLAW_SKILLS_DIR}/inference-engineer/research/hardware-workload.md` — GPU SKU pricing + decision tree (model class × workload → SKU)
- `${OPENCLAW_SKILLS_DIR}/inference-engineer/research/evaluation.md` — lm-eval / MTEB / MMMU / GenAI-Perf — which tool for which modality
- `${OPENCLAW_SKILLS_DIR}/inference-engineer/research/kernel-engineering.md` — what `/inference-kernel-engineer` will do later (read for context; it's a post-load optimization, not Day-1)

The research corpus is the brain. If a research file looks stale (`Last verified` >30 days ago), invoke `/inference-research-refresh` first.

## Runtime auth

All control-plane calls require both headers:

```bash
-H "X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN" \
-H "X-Tenant-Id: $ROCKIELAB_TENANT_ID"
```

`ROCKIELAB_TENANT_TOKEN` is the stable control-plane auth token.
`ROCKIELAB_TENANT_ID` is display/scoping identity only. Never guess that
`ROCKIELAB_TENANT_ID` can be used as `X-Tenant-Token`; live prod rejects
that with `invalid_tenant_token`.

## The 8 steps (procedural; the agent walks these in order, no skipping)

### Step 1 — Intake

If the user invoked with no model URL: ask for one. If they gave one, echo it back and confirm.

Then ask the workload + reliability intent questions from `${OPENCLAW_SKILLS_DIR}/inference-engineer/prompts/intake-clarify.md`:

1. *Who hits this — just you/a small team, an app with real users, or a queue of jobs to grind through?*
2. *Does latency matter, or just total time?*
3. *Roughly how many requests/day, and how long is the typical input?*
4. *Are you OK with the endpoint dying mid-request and auto-reprovisioning, or does this need to be live and survive the next 60 minutes?*
5. When latency matters: *Where will the customers calling this endpoint be located?* (`US`, `Europe`, or `any region`)

Map the first three answers to one of the canonical workload shapes documented in `research/hardware-workload.md` § Workload taxonomy: `interactive` | `production-api` | `batch` | `embedding-throughput` | `vision-segmentation` | `scientific`.

Do not invent a sixth shape. If the user's situation doesn't fit, ask one more question to disambiguate, then map.

Map the reliability answer to the provisioning tier:

- Cannot die mid-request / must survive the next hour / no interruption → `tier: "on_demand"`.
- OK with dying mid-request / can retry / overnight batch / async / retryable one-off / cron → `tier: "spot"`.

Do not pick spot for a live demo or active endpoint merely because it is cheaper. If the user says "make it cheap" while describing a live workload, keep `on_demand` unless they explicitly accept mid-request death and reprovisioning. If they explicitly accept preemption for any workload, use `spot` and warn that live/customer-facing endpoints may disappear during a request.

Map the latency-region answer to coarse region intent only:

- US / North America / US East / US West → `region: "us"`.
- Europe / EU / UK → `region: "eu"`.
- Any region / wherever is cheapest / latency not important → `region: "any"`.

Never expose or ask for an underlying provider. Region is customer-visible latency intent; provider identity and raw provider regions remain Rockie-internal.

### Step 2 — Classify source + preflight access

This step is zero-spend. Do **not** call `POST /api/inference/loads` here unless the backend exposes an explicit dry-run/classify flag that cannot provision a GPU. If no dry-run endpoint exists, classify from the model URL/repo metadata and proceed to cost planning only after the checks below pass.

Run the safe source-access preflight before cost estimate or provisioning:

- For a GitHub repo, verify the repo metadata is readable with a read-only API call. Do not clone yet.
- For a Hugging Face repo, read model metadata first. Inspect `gated`, `pipeline_tag`, `library_name`, and config availability.
- If `gated` is truthy, perform a non-weight access check such as `HEAD /<repo>/resolve/main/config.json` using the connected HF credential. Never print token values. Never download weights during preflight.
- If gated config access fails, stop the requested-model path. Tell the user the exact approval action needed on Hugging Face and offer a verified public fallback when one exists.

The detector/preflight returns: `source_type` (`hf-repo` / `github-repo` / `direct-file` / `container-image`), `modality` (`text-embedding` / `text-generation` / `chat` / `image-segmentation` / `multimodal`), `vram_requirement_mb`, `dependency_strategy`, `backend`, `embedding_dimension`, `confidence`, and any access blocker/fallback.

Echo the classification to the user in one line: *"Detected: Mistral-7B-Instruct (HF repo, chat LLM, ~14 GB VRAM, vLLM backend, confidence 0.9)."*

**If the requested model is gated and access is missing:** stop before cost estimate. Say that access is blocked, link/name the gated model page, and offer the verified fallback as a `different model` option. Do not proceed to cost confirm for the gated model.

**SAM-family fallback rule:** if the user asks for `facebook/sam3`/SAM3 and HF access is not approved, recommend:

- GitHub repo: `facebookresearch/segment-anything`
- Hugging Face model: `facebook/sam-vit-base`
- HF facts to report: `gated=false`, `pipeline_tag=mask-generation`, `library_name=transformers`, `config.json` accessible
- Serving plan: Transformers `pipeline("mask-generation", model="facebook/sam-vit-base")`
- Workload shape: `vision-segmentation`

Do not claim that `facebook/sam-vit-base` is deployed unless the backend accepts and serves that backend. If the current `/api/inference/loads` catalog still rejects it or only serves `/embed`, present it as the verified fallback **plan** and ask whether to proceed once support is available or file/track the backend catalog/serving follow-up.

**If the detector returns `unsupported_source`:** stop. Tell the user the model is outside the supported catalog today and offer to file an issue against `inference_source_plans.py` to add it. Do not proceed. If you file that issue, dispatch it immediately; do not leave new issue debt idle.

### Step 3 — Cost estimate

Pick the GPU SKU from the workload shape × `vram_requirement_mb`, using the decision tree in `research/hardware-workload.md` § Decision tree. Use the tier selected from Step 1: `on_demand` when the endpoint cannot die mid-request or must survive the next 60 minutes, and `spot` when the owner explicitly accepts preemption and reprovisioning, including overnight/batch/async workloads.

Get current Rockie-GPU market pricing through the deidentified CLI:

```
rockie-gpu market --gpu <sku> --spot <true|false> --region <us|eu|any> --json
```

`rockie-gpu market` is the single GPU-shopping surface — it returns ranked, Rockie-priced options (markup already baked in) without ever naming the underlying compute supplier or exposing a supplier API key. Read the cheapest `$/hr` from its JSON for the SKU/tier/region you picked. Never call a supplier price endpoint or per-supplier CLI directly.

For Day-1, default to a **1-hour spend cap** and a **3-minute bootstrap budget** baked into the estimate. Show the user:

- Picked SKU + why (1 sentence reasoning from the decision tree)
- $/hr (spot or on-demand) + why that tier matches the reliability answer
- Coarse region (`us`, `eu`, or `any`) when latency matters
- Estimated bootstrap time (`hf-repo` ~3 min, `github-repo` ~8 min, `container-image` ~90 sec)
- Spend cap if they say yes ($/hr × 1 hr by default)
- The serving stack that will be used (e.g. *"vLLM with continuous batching, OpenAI-compatible API"*)

### Step 4 — HARD confirm (load-bearing)

**Use `${OPENCLAW_SKILLS_DIR}/inference-engineer/prompts/cost-confirm.md` verbatim.** Do not paraphrase. Do not skip. Wait for an unambiguous **yes** from the user. Anything else — silence, "maybe", "what about a smaller GPU first", "hmm" — is **not** consent. Re-ask or revise the plan based on their response.

This is the *only* hard gate in the skill. Bypassing it without explicit user yes is a critical bug. The `cost-confirm.md` template includes the exact dollar figure, the SKU, the spend cap, and the "yes / no / smaller / cheaper / different model" branches so the agent doesn't improvise vague language at the load-bearing moment.

### Step 5 — Provision

On confirmed yes:

```
POST $ROCKIELAB_API_URL/api/inference/loads
-H "X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN"
-H "X-Tenant-Id: $ROCKIELAB_TENANT_ID"
{
  "url": "<source-url>",
  "gpu_type": "<sku>",
  "spend_cap_cents": <cap from step 3>,
  "keep_running": true,
  "tier": "<spot|on_demand>",
  "region": "<us|eu|any>",
  "dashboard": {
    "notebook_id": "<notebook:... when this launch belongs to a lab note flow>",
    "run_name": "<human-readable load name>",
    "origin_skill": "inference-engineer",
    "software": "<vllm|triton|tei|bentoml|sglang|lmdeploy|dynamo>",
    "monitoring_profile_id": "inference.batch_baseline.v1",
    "monitoring_profile_snapshot": "<frozen snapshot with run_name, software, duration_class, cadence_bounds, metrics, panels, red_flags, stop_policy, safe_to_auto_stop>"
  }
}
```

The load endpoint acquires the GPU through `rockie-gpu provision --best --budget-cents <spend_cap_cents>` under the hood — the same deidentified broker `rockie-gpu market` quoted in step 3. The skill never selects a compute supplier, never passes a supplier name, and never handles a supplier API key; the only credentials it sends are the tenant token + tenant id above. If you ever find yourself reaching for a supplier-specific provisioning path, stop — `rockie-gpu` is the sole surface.

`keep_running: true` means a successfully loaded served endpoint should remain available after bootstrap until either the user explicitly stops it or the spend cap is reached. When the spend cap is reached, the budget watchdog tears down the GPU and removes the endpoint registration and bearer cache while preserving the load record for audit/status. If `keep_running` is omitted or false, the server uses the default non-preserved behavior for loaded endpoints, including shutdown cleanup.

Dashboard Note boundary:

- This repo does not currently ship a local `inference-engineer` runtime
  submit helper analogous to `/experiment`.
- Until that helper exists, the skill author must thread the dashboard
  block above through the server-side `POST /api/inference/loads` path or
  the nearest control-plane writer and freeze the snapshot at launch time.
- Default to `inference.batch_baseline.v1` unless a more specific domain
  profile is attached by the parent research skill.

Returns `{ id: <load_id>, state: "queued", ... }`. Echo the load_id to the user and tell them they can stop at any time with `/inference-engineer stop <load_id>` (which translates to a `POST /api/inference/loads/<id>/cancel`). Explicit stop/cancel is final for the served endpoint: it tears down the GPU, Cloudflare tunnel, DNS record, endpoint registration, and bearer cache even when the load was created with `keep_running: true`.

### Step 6 — Stream progress

Poll every 3 seconds:

```
GET $ROCKIELAB_API_URL/api/inference/loads/<id>/logs
-H "X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN"
-H "X-Tenant-Id: $ROCKIELAB_TENANT_ID"
```

Returns `{ id, state, last_log_tail }`. State transitions to forward to the user:

- `queued` → *"queued, waiting for GPU..."*
- `cloning` → *"cloning source repo..."*
- `installing` → *"installing dependencies..."*
- `loading` → *"loading model weights..."*
- `loaded` → *"loaded — serving on the pod"*
- `failed` → surface the error from `last_log_tail`, offer to cancel + retry with a different SKU

On `failed`, do not silently retry. Show the user the error, propose the fix (smaller model, bigger GPU, different stack), get confirmation, then re-enter at step 3.

### Step 7 — Endpoint + MCP

Once state is `loaded`:

```
GET $ROCKIELAB_API_URL/api/inference/loads/<id>/endpoint
-H "X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN"
-H "X-Tenant-Id: $ROCKIELAB_TENANT_ID"
```

Returns the Cloudflare-tunnel hostname + bearer token (4-outcome matrix: 404 = no load, 425 + Retry-After = not-ready, 200 + bearer = cache hit, 200 + null bearer = cache miss).

The MCP tool registers **automatically** — `inference_tool_registry.list_active_inference_tools(tenant)` is polled by the mcp-rockie bridge, which emits `notifications/tools/list_changed` to the chat agent. The skill's job is to **confirm the tool surfaced**: wait up to 30s for `inference_<load_id>` to appear in the agent's MCP tool list.

Before reporting success, render the output in the lab:

1. Resolve the current lab id. Use the runtime-provided `PLATFORM_LAB_ID` when it is configured, or the explicit lab/notebook id from the current chat context. Do not invent one.
2. Run a bounded smoke inference through `inference_<load_id>` and pass `notebook_id` explicitly for every preview-producing modality. The dynamic inference tool strips this platform-only field before calling the model endpoint, but it needs the lab id to spool SAM-sized masks, images, audio, long generations, or large vectors into `output_assets`.
3. Call `visualize_inference_result` with `notebook_id`, `load_id`, detected `modality`, bounded `response_body`, the original `request_body`, and any `output_assets` returned by `inference_<load_id>`.
4. Report success only after `visualize_inference_result` returns a Note id. Tell the user the preview is in the lab Notes pane; do not make terminal JSON decoding the happy path.

Smoke-call shapes, based on `modality`:

- `text-generation` / `chat`: call `inference_<id>` with `{notebook_id: "<notebook:...>", prompt: "haiku about rocks", max_tokens: 80}`, then call `visualize_inference_result`.
- `text-embedding`: call `inference_<id>` with `{notebook_id: "<notebook:...>", input: "hello world"}`, then render vector stats via `visualize_inference_result`.
- `image-segmentation`: call `inference_<id>` with `{notebook_id: "<notebook:...>", image_base64: "<bounded-base64-png>", input_points: [[x, y]]}`. Use the returned `output_assets` for mask blobs; never paste raw SAM mask base64 into chat. Then call `visualize_inference_result` so the mask overlay appears as an `inference_preview` Note.
- Unsupported or future modalities: still call `visualize_inference_result`; it creates a fallback raw-JSON preview Note with backend redaction.

### Step 8 — Affordances

Tell the user the three commands they have:

- **stop:** `/inference-engineer stop <load_id>` → `POST /api/inference/loads/<id>/cancel` (always with `X-Operator-Tenant-Id: $ROCKIELAB_OPERATOR_TENANT_ID` when set) → explicit DELETE/cancel semantics: tears down pod, Cloudflare tunnel, DNS record, endpoint registration, and bearer cache; this overrides `keep_running: true`. **Teardown response is 200 (or 204 on pre-#1217 deploys) — see the Teardown protocol in Hard rules.** A 404 is NEVER teardown success; the body's `error.code` distinguishes `load_not_yours_or_missing` from `load_not_found` (#1218).
- **extend cap:** `/inference-engineer extend <load_id> <new-cap-cents>` → `PATCH /api/inference/loads/<id>` (if the route exists; if not, file an issue and tell the user it's a coming-soon).
- **status:** `/inference-engineer status <load_id>` → `GET /api/inference/loads/<id>` → current state + spend so far + endpoint URL.

Then **offer the post-load optimization sub-skills** as opt-in:

- *"Want to evaluate quality (lm-eval / MTEB / etc)?"* → `/inference-eval <load_id>`
- *"Want to try the kernel optimizer for a speedup?"* → `/inference-kernel-engineer <load_id>` (warn: takes ~30 min + an extra $1-3 in GPU spend; not all models benefit)

## Sub-skills you'll reach for

- `/inference-hardware-picker` — standalone version of step 3 (workload + model → SKU + stack). The parent skill inlines this; useful when the user is just shopping for a SKU.
- `/inference-eval` — quality + perf + cost probe on a loaded endpoint. Modality-aware (lm-eval for LLM, MTEB for embedding, ADE20K for SAM, MMMU-Pro for multimodal).
- `/inference-kernel-engineer` — post-load profile + Triton kernel iteration (GEAK-style 4-agent loop with the Sakana-lesson human checkpoint before integration).
- `/inference-serving-gateway` — standalone LiteLLM-sidecar wrapping for a model already loaded outside this skill (e.g. via a manual ssh pod).
- `/inference-research-refresh` — re-runs the 5 research subagents (sonnet, not opus) to refresh the corpus in `research/`. Invoke when a research file's `Last verified` is >30 days old.

## Hard rules

- **Step 4 is a hard gate.** Never provision without explicit user yes. The `cost-confirm.md` template is non-negotiable.
- **Read the research corpus before reasoning.** Don't make up SKU/stack/eval choices from nothing — the research files have the decision tree, the pricing, and the rationale. If you're about to write a sentence that contradicts a research file, stop and re-read.
- **No silent retries on `failed`.** Always surface the error and the proposed fix; let the user accept before re-provisioning.
- **No improvising at step 4.** Use the prompt template exactly. Improvised cost-confirm wording has historically been the place where agents accidentally pretend the user said yes.
- **Always surface the three affordances at step 8.** Even if the user seems happy. They need to know how to stop / extend / check status.
- **Never use tenant identity as auth.** `ROCKIELAB_TENANT_ID` must only
  be sent as `X-Tenant-Id`. `X-Tenant-Token` must come from
  `ROCKIELAB_TENANT_TOKEN`.
- **`rockie-gpu` is the sole GPU surface.** All GPU shopping, provisioning,
  and spend goes through `rockie-gpu market` / `rockie-gpu provision` /
  `rockie-gpu audit-open-spend`. Never name a compute supplier, never call a
  supplier-specific endpoint or CLI, never read or pass a supplier API key.
  The only credentials this skill sends are the tenant token + tenant id.
- **Preflight before money.** GitHub/HF access checks happen before cost estimate and before Step 4. Never discover gated-model access by starting a GPU load.
- **Don't load unsupported sources.** If the detector says `unsupported_source`, stop — don't paper over it with a "let me try anyway."
- **Don't turn a planning miss into a runtime miss.** Adding a fallback to a catalog is not enough unless the backend can actually serve that modality. If serving support is missing, say so and track that implementation instead of provisioning.
- **One model per load.** Multi-model serving (one pod hosting N models) is out of scope today. If the user asks, file an issue and serve one model per pod for now.
- **Teardown success requires a positive signal (#1217, #1218).** Only `200` with `outcome ∈ {"cancelled","already_terminated"}` (post-#1217 deploy) or `204 No Content` (legacy pre-#1217 deploy) confirms termination. **`404` is NEVER teardown success** — it means "this request was ambiguous, you didn't send attestation" (`load_not_yours_or_missing`) or "the load id is bogus" (`load_not_found`). Both branches require user input, not a confident "torn down." Follow the Teardown protocol, which now uses typed error codes (#1218) instead of branching on the bare 404 status.

### Teardown protocol (when the user asks you to stop a load)

1. Send `POST /api/inference/loads/<id>/cancel` with `X-Tenant-Token` + `X-Tenant-Id`, AND `X-Operator-Tenant-Id: $ROCKIELAB_OPERATOR_TENANT_ID` when that env var is set (broker-child runtime injects it; lab UI does not).
2. **On 200** → read `outcome` from the body. Tell the user `"torn down (was <previous_state>)"` for `cancelled` or `"already terminal (was <previous_state>); no-op confirmed"` for `already_terminated`. If `resolved_via` is `operator_attestation`, append "(via operator identity, tenant <id>)" — surfaces that the load was created from a different runtime identity than yours.
3. **On 204** (legacy pre-#1217 deploy) → treat as `cancelled`; tell the user "torn down." Once every prod env is on the post-#1217 image, this branch goes away.
4. **On 404 with `error.code = "load_not_yours_or_missing"` (#1218)** → DO NOT claim teardown. Your request was ambiguous: you didn't send `X-Operator-Tenant-Id`, or the operator tenant you sent doesn't bind to the user's record. Retry once with `X-Operator-Tenant-Id: $ROCKIELAB_OPERATOR_TENANT_ID` if you haven't already; if you already did, escalate to the user — "the load id might be stale, or it belongs to a different account; can you confirm via `GET /api/inference/loads` from your lab UI?"
5. **On 404 with `error.code = "load_not_found"` (legacy or no-attestation path)** → the id truly doesn't exist in your tenant. Ask the user to double-check; offer `GET /api/inference/loads` to list.
6. **On 403 with `error.code = "load_not_owned_by_attested_operator"` (#1218)** → the load exists but the operator tenant you asserted doesn't own it either. Surface the id mismatch to the user verbatim ("the operator identity you're running under doesn't own load <id>"); do NOT retry. This is the explicit "wrong-tenant" signal — the user needs to fix the operator id on their end.
7. **On 5xx / network error** → surface the response verbatim; ask whether to retry once.
8. **Never** infer teardown from "absence of a row" — a follow-up `GET .../loads/<id>` returning 404 is the same ambiguity as steps 4–6.

## Failure modes the skill handles

- **HF gated model:** preflight should catch this before cost estimate by checking model metadata and a non-weight config resource. Tell the user, point them at the HF page to request access, and offer a verified fallback if available.
- **Fallback accepted by preflight but unsupported by loader:** do not call `/api/inference/loads`. Explain that the fallback is verified as public/readable but still needs backend catalog/serving support, then file and immediately dispatch the implementation issue.
- **VRAM underestimate:** load fails with OOM. Re-enter step 3 with the next-larger SKU.
- **CF tunnel provisioning timeout:** rare; surface the error, offer cancel + retry.
- **MCP tool doesn't surface within 30s:** check `inference_tool_registry.list_active_inference_tools(tenant)` manually via a debug endpoint; if missing, file an issue against tool_registry.
- **Spend cap hit mid-load:** `gpu_credit_service` enforces; load auto-cancels with `__CLEANUP__:budget_exhausted`. Tell the user what was spent and offer to extend cap + re-provision.

## Outputs

The skill itself doesn't write files — its outputs are:

- A live MCP tool (`inference_<load_id>`) the chat agent can call
- A live HTTPS endpoint at `https://inference-<load_id>.rockielab.com` (bearer-auth, OpenAI-compatible for LLMs)
- A `pod_state` row in SurrealDB with the load metadata
- An entry in `gpu_credit_service` reflecting the spend
- (Optional, if user opts in at step 8) A quality / perf / cost probe report from `/inference-eval`

## Agent invocation (what the agent literally does)

```
1. read research/*.md (5 files; cache in memory for this turn)
2. read prompts/intake-clarify.md, prompts/cost-confirm.md
3. step 1 — intake; ask 3 workload questions; classify shape
4. step 2 — classify source + run GitHub/HF access preflight (dry-run only; no GPU; no weight downloads)
5. step 3 — cost estimate from `rockie-gpu market` (deidentified GPU broker)
6. step 4 — HARD confirm using cost-confirm.md verbatim; gate on unambiguous yes
7. step 5 — POST /api/inference/loads
8. step 6 — poll /logs every 3s; forward state to user
9. step 7 — GET /endpoint; wait up to 30s for MCP tool to appear; show example invocation
10. step 8 — surface stop / extend / status affordances + offer sub-skills
```

## When you (the agent) are uncertain

Ask the user. Don't speculate-edit the provisioning request. The cost-confirm step is the last line of defense before real money gets spent — if you're not sure the cost or the SKU is right, surface the uncertainty in the cost-confirm message itself ("I'm not sure if this model needs more VRAM than I estimated; we could provision a bigger GPU as a safety margin for $X more — yes / no?").

<!-- dispatch re-trigger 2026-05-29T21:55Z (PAT got Contents:write) -->
