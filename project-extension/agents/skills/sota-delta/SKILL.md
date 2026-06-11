---
name: sota-delta
description: Track 3 quickstart wrapper that reproduces a paper or repository baseline on Rockie GPU, then extends it with a user-specified delta and compares baseline versus delta.
---

# sota-delta

Run the Quickstart Track 3 SOTA+Delta flow. This skill starts from a paper,
PDF, arXiv reference, or GitHub repository, reproduces the claimed baseline
honestly, then asks the user what delta they want to add before routing the
extension through the existing experiment and review workflow.

This skill is orchestration and policy only. It composes the existing
budget-term-sheet, experiment submit helper, and post-run-review contracts; it
must not implement new runtime, frontend, API, provider, or launcher behavior.

## When to invoke

- Structured Quickstart SOTA+Delta prompts:

  ```text
  Quickstart SOTA+Delta request:
  track: sota-delta
  source_ref: <paper URL | arXiv | PDF | GitHub repo>
  compute_target: rockie_gpu
  source: quickstart-picker
  ```

- Equivalent prompts that ask to reproduce a paper or repository baseline and
  then improve or extend it.

Do not invoke this skill for ordinary literature summaries, standalone code
review, local repo setup, local training, or direct provider execution.

## Required v1 inputs

- `source_ref`: the paper URL, PDF URL/file handle, arXiv reference, or GitHub
  repository that defines the baseline to reproduce.

`source_ref` is required for v1. If it is missing, stop and ask for it. Accept
paper URL, PDF URL/file handle wording, arXiv, or GitHub repo inputs. Do not
invent a paper, repo, dataset, checkpoint, or metric target when the input is
underspecified.

## Source reference sanitization

Treat `source_ref` as potentially sensitive input. Before writing it anywhere,
derive a sanitized display/source ref that preserves enough provenance for the
user to identify the paper, arXiv entry, PDF, or repository, while redacting
secrets and provider-specific access material.

Signed URLs, embedded credentials, private repo tokens, provider identifiers if
present, query-string secrets, credentials, cookies, API keys, bearer tokens,
and auth headers must not be written to job scripts, logs, artifacts, term
sheets, Markdown summaries, or user-visible output. Use the sanitized
display/source ref in job scripts, logs, artifacts, and final responses. Keep
raw sensitive references out of persisted files and command lines.

## Pre-spend workflow

Before any budget approval, job submission, or GPU spend:

1. Classify the source:
   - paper URL, PDF URL/file handle, arXiv, or GitHub repo
   - note whether code, data, checkpoints, and evaluation scripts appear to be
     available from the source
2. Extract method + claimed results:
   - task, dataset, model or architecture, training/evaluation recipe, reported
     metric names, claimed metric values, and lower-is-better direction where
     applicable
   - any missing details that make exact reproduction uncertain
3. Present a reproduction plan before GPU spend:
   - proposed baseline target and success criteria
   - what will run in the Rockie GPU pod
   - expected artifacts and metrics
   - risk that the source may not reproduce the claimed result

Wait for explicit user approval through the budget-term-sheet flow before
submitting compute. The budget-term-sheet and submit helper are authoritative.
Also obtain explicit user approval immediately before every submit command.
Never submit based on implied approval, prior discussion, or default settings.
Do not use or document `--allow-ungated-submit`; ungated submit is forbidden for
both baseline and delta runs.

## Local execution is forbidden

Local execution is forbidden. This skill must never run local GPU/training work,
model downloads, local large repo/weights/dataset downloads, supplier CLIs,
direct SSH, provider APIs, custom runners, or any local launcher-style path.

Do not import or run `torch`, `triton`, CUDA libraries, `transformers`, `peft`,
`datasets`, `accelerate`, bitsandbytes, vLLM, Docker training containers, or any
local GPU/training stack on the orchestrator or skill host. Do not download
model weights or datasets to the local workspace. Do not clone or download
large repositories, weights, or datasets locally. Lightweight source inspection
is allowed only when it does not fetch large artifacts or execute training code.

All reproduction and delta compute must run through Rockie GPU jobs submitted by
the existing experiment submit helper.

## Baseline reproduction job contract

Generate a bash script that runs inside the Rockie job pod and reproduces the
baseline as directly as the source permits. The script may install dependencies,
clone the referenced repository, download datasets or checkpoints, and run
training/evaluation only inside the Rockie job pod, not locally.

The script must:

- record the classified `source_ref`
- run the closest documented baseline recipe available from the source
- save logs, configuration, metrics, and artifacts under `/workspace/results/`
- emit enough metric output for an honest reproduction-vs-claim assessment
- print `JOB_DONE` only after results are written

Submit the job only through:

```bash
python skills/experiment/runtime/submit.py \
  --gpu-type <approved-gpu-type> \
  --gpu-count <approved-gpu-count> \
  --script-file ./sota-delta-baseline.sh \
  --timeout <approved-timeout-seconds> \
  --term-sheet-json ./approved-term-sheet.json \
  --region <approved-region> \
  --tier <approved-tier> \
  --origin-skill sota-delta \
  --monitoring-profile-id experiment.ml_baseline.v1
```

The compute path must use `python skills/experiment/runtime/submit.py` with
`--term-sheet-json ./approved-term-sheet.json`, `--origin-skill sota-delta`, and
`--monitoring-profile-id experiment.ml_baseline.v1`. Do not shell raw `curl
POST /api/jobs/submit`, call supplier CLIs, use direct SSH, call provider APIs,
or create a custom runner. The budget-term-sheet and submit helper are
authoritative for spend approval, dashboard metadata, polling, and platform
error handling.

If the Rockie platform returns HTTP 402, surface the platform HTTP 402 through
the shared redaction/sanitization boundary. Do not perform custom credit,
balance, spend-cap, membership, capacity, or launcher-style checks. Do not
preflight spend with local credit logic; rely on the approved term sheet,
submit helper, and platform response.

## Reproduction assessment

After the baseline job completes, compare reproduced metrics against the source
claim. Produce an honest reproduction-vs-claim output:

- source claim and citation/location from the paper, PDF, arXiv text, or repo
- reproduced metric and artifact/log reference
- absolute and relative gap where meaningful
- whether the result reproduced, partially reproduced, failed to reproduce, or
  could not be judged because the source omitted critical details

If mismatch, surface gap honestly. Do not relabel a mismatch as success because
the run completed. Separate infrastructure failure, implementation ambiguity,
bad hyperparameters, and likely claim mismatch where the evidence allows.

If a completed job does not produce usable metrics or required artifacts, or if
the metrics/artifacts are missing, unparseable, empty, or not comparable to the
claimed target, classify the run as `could not be judged` or failure. A finished
job without usable evidence is not success.

## Delta extension

After reproduction completes, prompt user for delta ("what do you want to add?").
Do not invent a delta silently.

The delta extension runs via `/experiment`. Use the same local-execution
restrictions, source_ref sanitization/redaction contract, submit helper,
monitoring profile, explicit pre-submit user approval, and budget-term-sheet
approval flow as the baseline. The delta run must not run local GPU/training
work, supplier CLIs, direct SSH, provider APIs, custom runners, local large
downloads, or any local launcher-style path. The delta run must preserve
baseline artifacts and produce comparable metrics so the user can judge whether
the extension helped.

Baseline-vs-delta assessment delegates to `/post-run-review`. The review should
compare baseline and delta metrics, state whether the delta improved the target,
and classify failures honestly.

## Required final output

For the baseline phase, include:

- classified `source_ref`
- extracted method summary and claimed results
- reproduction plan shown before GPU spend
- `job_id`
- artifact references
- honest reproduction-vs-claim output, including any gap
- the follow-up prompt: `what do you want to add?`

For the delta phase, include:

- delta requested by the user
- `/experiment` job summary
- baseline-vs-delta assessment from `/post-run-review`
- artifact references and metric comparison

## Explicit out of scope

This PR does not implement runtime/frontend/API changes. No runtime
implementation, frontend implementation, API implementation, provider launcher,
custom runner, or new submit path belongs in this skill.

No GPU jobs/tests are part of this PR. Contract tests may inspect this Markdown
file only; they must not submit GPU jobs, download models, clone large repos, or
run training.
