from __future__ import annotations

from pathlib import Path


SKILL = Path(__file__).with_name("SKILL.md")


def read_skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_documents_structured_track2_prompt_and_required_inputs() -> None:
    text = read_skill()

    required = [
        "Quickstart fine-tune request:",
        "track: finetune",
        "model: <registry model slug>",
        "registry_dataset_id: <registry dataset id>",
        "`model`: the registry model slug or id",
        "`registry_dataset_id`: the registry dataset id",
        "Both fields are required for v1",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing structured prompt contract: {missing!r}"


def test_refuses_private_data_ref_v1() -> None:
    text = read_skill()

    required = [
        "Explicit refusal: `private_data_ref` is not supported in v1",
        "Refuse v1 requests",
        "that provide `private_data_ref`",
        "until the per-tenant data API from issue #1298 exposes a validated",
        "do not treat a private data reference as selectable",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing private data refusal: {missing!r}"


def test_documents_auth_and_preflight_before_spend() -> None:
    text = read_skill()

    required = [
        'X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN',
        'X-Tenant-Id: $ROCKIELAB_TENANT_ID',
        "`ROCKIELAB_TENANT_TOKEN` is the control-plane auth token",
        "Preflight before GPU spend",
        "GET $ROCKIELAB_API_URL/api/registry/models/{slug}",
        "POST $ROCKIELAB_API_URL/api/registry/datasets/resolve-for-track",
        'track: "finetune"',
        'registry_dataset_id:\n   "<id>"',
        "If either preflight fails, stop before spend",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing auth/preflight contract: {missing!r}"


def test_forbids_local_gpu_training_and_downloads() -> None:
    text = read_skill()

    required = [
        "Local execution is forbidden",
        "must never run model training or model download work locally",
        "Do not import or run",
        "`torch`",
        "`triton`",
        "CUDA libraries",
        "`transformers`",
        "`peft`",
        "Do not download model weights or datasets to the local workspace",
        "Do not start local training",
        "All GPU training must go through the Rockie job platform",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing local execution guard: {missing!r}"


def test_documents_submit_helper_budget_gate_progress_and_artifact() -> None:
    text = read_skill()

    required = [
        "single-GPU LoRA/SFT",
        "`<7B` models",
        "/workspace/results/fine-tuned-model/",
        "/workspace/results/fine-tuned-model.tar.gz",
        'FINETUNE_PROGRESS {"step":12,"loss":0.42}',
        "`JOB_DONE`",
        "python skills/experiment/runtime/submit.py",
        "--term-sheet-json ./approved-term-sheet.json",
        "--origin-skill finetune-model",
        "--monitoring-profile-id experiment.ml_baseline.v1",
        "shared budget-term-sheet gate",
        'origin_skill: "finetune-model"',
        "training.step",
        "training.loss",
        "Malformed progress lines are ignored safely",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing submit/progress contract: {missing!r}"


def test_documents_artifact_handoff_and_loader_endpoint_wait() -> None:
    text = read_skill()

    required = [
        "GET $ROCKIELAB_API_URL/api/jobs/{job_id}/artifacts",
        "Select `results/fine-tuned-model.tar.gz`",
        "Fail closed if that artifact is absent",
        "POST $ROCKIELAB_API_URL/api/inference/loads",
        '"url": "rockie-job-artifact://<job_id>/results/fine-tuned-model.tar.gz"',
        "Do not use temporary signed HTTPS artifact URLs",
        "GET $ROCKIELAB_API_URL/api/inference/loads/{id}/endpoint/wait",
        "GET /api/inference/loads/{id}/logs",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing deploy handoff contract: {missing!r}"


def test_surfaces_platform_402_without_custom_credit_gate() -> None:
    text = read_skill()

    required = [
        "If the Rockie platform returns HTTP 402",
        "surface the platform 402 response through the shared redaction/sanitization boundary",
        "Do not perform launcher-style credit, balance, spend-cap, membership, or local",
        "existing budget-term-sheet gate and platform HTTP 402",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing 402 passthrough contract: {missing!r}"


def test_requires_endpoint_bearer_curl_job_artifact_and_load_output() -> None:
    text = read_skill()

    required = [
        "endpoint URL from the loader endpoint response",
        "use this value directly as\n  `ENDPOINT_URL`",
        "bearer-token handling",
        "Authorization: Bearer",
        "copy-paste curl",
        'curl "$ENDPOINT_URL"',
        "`job_id`",
        "`rockie-job-artifact://<job_id>/results/fine-tuned-model.tar.gz`",
        "`load_id`",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing final output contract: {missing!r}"
    assert "$ENDPOINT_URL/v1/chat/completions" not in text
    assert "rockie-job-artifact://<job_id>/fine-tuned-model.tar.gz" not in text


if __name__ == "__main__":
    tests = [
        test_documents_structured_track2_prompt_and_required_inputs,
        test_refuses_private_data_ref_v1,
        test_documents_auth_and_preflight_before_spend,
        test_forbids_local_gpu_training_and_downloads,
        test_documents_submit_helper_budget_gate_progress_and_artifact,
        test_documents_artifact_handoff_and_loader_endpoint_wait,
        test_surfaces_platform_402_without_custom_credit_gate,
        test_requires_endpoint_bearer_curl_job_artifact_and_load_output,
    ]
    for test in tests:
        test()
    print(f"OK - {len(tests)} finetune-model contract checks passed")
