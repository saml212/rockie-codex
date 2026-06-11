from __future__ import annotations

from pathlib import Path


SKILL = Path(__file__).with_name("SKILL.md")


def read_skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_documents_structured_track3_prompt_and_required_input() -> None:
    text = read_skill()

    required = [
        "name: sota-delta",
        "description: Track 3 quickstart wrapper",
        "Quickstart SOTA+Delta request:",
        "track: sota-delta",
        "source_ref: <paper URL | arXiv | PDF | GitHub repo>",
        "compute_target: rockie_gpu",
        "source: quickstart-picker",
        "`source_ref`: the paper URL, PDF URL/file handle, arXiv reference, or GitHub",
        "`source_ref` is required for v1",
        "Accept\npaper URL, PDF URL/file handle wording, arXiv, or GitHub repo inputs",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing structured prompt/input contract: {missing!r}"


def test_documents_pre_spend_source_classification_and_plan() -> None:
    text = read_skill()

    required = [
        "Before any budget approval, job submission, or GPU spend",
        "Classify the source",
        "paper URL, PDF URL/file handle, arXiv, or GitHub repo",
        "Extract method + claimed results",
        "task, dataset, model or architecture, training/evaluation recipe, reported",
        "Present a reproduction plan before GPU spend",
        "proposed baseline target and success criteria",
        "risk that the source may not reproduce the claimed result",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing pre-spend workflow contract: {missing!r}"


def test_source_ref_sanitization_redaction_contract() -> None:
    text = read_skill()

    required = [
        "Source reference sanitization",
        "Treat `source_ref` as potentially sensitive input",
        "derive a sanitized display/source ref",
        "preserves enough provenance",
        "Signed URLs, embedded credentials, private repo tokens, provider identifiers if",
        "query-string secrets, credentials, cookies, API keys, bearer tokens",
        "must not be written to job scripts, logs, artifacts, term",
        "Use the sanitized",
        "display/source ref in job scripts, logs, artifacts, and final responses",
        "raw sensitive references out of persisted files and command lines",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing source_ref sanitization contract: {missing!r}"


def test_forbids_local_gpu_training_downloads_and_provider_paths() -> None:
    text = read_skill()

    required = [
        "Local execution is forbidden",
        "must never run local GPU/training work",
        "model downloads",
        "local large repo/weights/dataset downloads",
        "supplier CLIs",
        "direct SSH",
        "provider APIs",
        "custom runners",
        "Do not import or run `torch`, `triton`, CUDA libraries",
        "Do not download\nmodel weights or datasets to the local workspace",
        "Do not clone or download\nlarge repositories, weights, or datasets locally",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing local execution guard: {missing!r}"


def test_documents_submit_helper_budget_gate_and_monitoring_profile() -> None:
    text = read_skill()

    required = [
        "python skills/experiment/runtime/submit.py",
        "--term-sheet-json ./approved-term-sheet.json",
        "--origin-skill sota-delta",
        "--monitoring-profile-id experiment.ml_baseline.v1",
        "The compute path must use `python skills/experiment/runtime/submit.py` with",
        "`--term-sheet-json ./approved-term-sheet.json`, `--origin-skill sota-delta`, and",
        "`--monitoring-profile-id experiment.ml_baseline.v1`",
        "The budget-term-sheet and submit helper are authoritative",
        "Do not shell raw `curl",
        "supplier CLIs",
        "direct SSH",
        "provider APIs",
        "custom runner",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing submit helper contract: {missing!r}"


def test_requires_explicit_approval_and_forbids_ungated_submit() -> None:
    text = read_skill()

    required = [
        "Wait for explicit user approval through the budget-term-sheet flow",
        "obtain explicit user approval immediately before every submit command",
        "Never submit based on implied approval, prior discussion, or default settings",
        "Do not use or document `--allow-ungated-submit`",
        "ungated submit is forbidden for",
        "both baseline and delta runs",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing explicit approval/ungated-submit guard: {missing!r}"


def test_surfaces_platform_402_without_custom_credit_gate() -> None:
    text = read_skill()

    required = [
        "If the Rockie platform returns HTTP 402",
        "surface the platform HTTP 402 through",
        "the shared redaction/sanitization boundary",
        "Do not perform custom credit",
        "balance, spend-cap, membership, capacity, or launcher-style checks",
        "launcher-style checks",
        "rely on the approved term sheet",
        "submit helper, and platform response",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing 402 passthrough contract: {missing!r}"


def test_documents_honest_reproduction_vs_claim_output() -> None:
    text = read_skill()

    required = [
        "honest reproduction-vs-claim output",
        "source claim and citation/location",
        "reproduced metric and artifact/log reference",
        "absolute and relative gap",
        "If mismatch, surface gap honestly",
        "Do not relabel a mismatch as success because",
        "failed to reproduce",
        "could not be judged because the source omitted critical details",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing reproduction honesty contract: {missing!r}"


def test_completed_job_without_usable_metrics_or_artifacts_is_failure() -> None:
    text = read_skill()

    required = [
        "If a completed job does not produce usable metrics or required artifacts",
        "metrics/artifacts are missing, unparseable, empty, or not comparable",
        "classify the run as `could not be judged` or failure",
        "A finished",
        "job without usable evidence is not success",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing missing/unparseable evidence failure contract: {missing!r}"


def test_documents_delta_prompt_experiment_and_post_run_review_delegation() -> None:
    text = read_skill()

    required = [
        'After reproduction completes, prompt user for delta ("what do you want to add?")',
        "Do not invent a delta silently",
        "The delta extension runs via `/experiment`",
        "Use the same local-execution",
        "restrictions, source_ref sanitization/redaction contract, submit helper",
        "monitoring profile, explicit pre-submit user approval, and budget-term-sheet",
        "approval flow as the baseline",
        "The delta run must not run local GPU/training",
        "supplier CLIs, direct SSH, provider APIs, custom runners",
        "Baseline-vs-delta assessment delegates to `/post-run-review`",
        "compare baseline and delta metrics",
        "the follow-up prompt: `what do you want to add?`",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing delta workflow contract: {missing!r}"


def test_documents_out_of_scope_no_runtime_frontend_api_or_gpu_tests() -> None:
    text = read_skill()

    required = [
        "Explicit out of scope",
        "This PR does not implement runtime/frontend/API changes",
        "No runtime\nimplementation, frontend implementation, API implementation",
        "No GPU jobs/tests are part of this PR",
        "Contract tests may inspect this Markdown",
        "they must not submit GPU jobs, download models, clone large repos, or",
        "run training",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing out-of-scope contract: {missing!r}"


if __name__ == "__main__":
    tests = [
        test_documents_structured_track3_prompt_and_required_input,
        test_documents_pre_spend_source_classification_and_plan,
        test_source_ref_sanitization_redaction_contract,
        test_forbids_local_gpu_training_downloads_and_provider_paths,
        test_documents_submit_helper_budget_gate_and_monitoring_profile,
        test_requires_explicit_approval_and_forbids_ungated_submit,
        test_surfaces_platform_402_without_custom_credit_gate,
        test_documents_honest_reproduction_vs_claim_output,
        test_completed_job_without_usable_metrics_or_artifacts_is_failure,
        test_documents_delta_prompt_experiment_and_post_run_review_delegation,
        test_documents_out_of_scope_no_runtime_frontend_api_or_gpu_tests,
    ]
    for test in tests:
        test()
    print(f"OK - {len(tests)} sota-delta contract checks passed")
