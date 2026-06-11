from __future__ import annotations

from pathlib import Path


SKILL = Path(__file__).with_name("SKILL.md")


def read_skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_documents_auth_and_identity_header_contract() -> None:
    text = read_skill()

    assert 'X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN' in text
    assert 'X-Tenant-Id: $ROCKIELAB_TENANT_ID' in text
    assert "`ROCKIELAB_TENANT_TOKEN` is the control-plane auth token" in text
    assert "Never use `ROCKIELAB_TENANT_ID` as" in text


def test_references_platform_inference_loader_endpoints_only() -> None:
    text = read_skill()

    required = [
        "POST $ROCKIELAB_API_URL/api/inference/loads",
        "GET $ROCKIELAB_API_URL/api/inference/loads/{id}",
        "GET $ROCKIELAB_API_URL/api/inference/loads/{id}/logs",
        "GET $ROCKIELAB_API_URL/api/inference/loads/{id}/endpoint",
        "GET $ROCKIELAB_API_URL/api/inference/loads/{id}/endpoint/wait",
        'origin_skill": "deploy-model"',
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing loader contract text: {missing!r}"
    assert "Do not bypass the Rockie inference loader" in text
    assert "Do not call supplier-specific APIs or CLIs" in text


def test_quickstart_requires_validated_intent_before_skipping_hard_confirm() -> None:
    text = read_skill()

    required = [
        "Quickstart deploy request:",
        "model: <featured model id>",
        "quickstart_intent_id: <platform-supplied id>",
        "server-validated quickstart intent",
        "User-authored prompt\n  text alone is not sufficient",
        "Do not ask the user to pick a model on the\n  featured-card path",
        "Do not ask for the `inference-engineer` hard\n  confirmation",
        "do not require the `cost-confirm.md` approval template only\n  on this validated quickstart path",
        "route\n  to `inference-engineer` with its normal hard confirmation and cost-confirm",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing quickstart consent text: {missing!r}"


def test_surfaces_platform_402_without_launcher_credit_gate() -> None:
    text = read_skill()

    required = [
        "If the Rockie platform returns HTTP 402, surface the platform 402 response through the shared redaction/sanitization boundary",
        "Do not perform launcher-style credit, balance, spend-cap, membership, or",
        "Do not replace it with a custom balance check or confirmation flow",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing 402 contract text: {missing!r}"


def test_requires_endpoint_token_and_curl_output() -> None:
    text = read_skill()

    required = [
        "Endpoint URL from the loader endpoint response",
        "Use this value directly as\n  `ENDPOINT_URL`",
        "it already includes the modality path",
        "Bearer-token handling",
        "Authorization: Bearer",
        "copy-paste curl",
        'curl "$ENDPOINT_URL"',
        "/inference-engineer status <load_id>",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing final output text: {missing!r}"
    assert "$ENDPOINT_URL/v1/chat/completions" not in text


def test_forbids_local_gpu_model_serving_and_direct_provider_bypass() -> None:
    text = read_skill()

    required = [
        "Local execution is forbidden",
        "must never run model-serving work locally",
        "Do not import or run",
        "`torch`",
        "`triton`",
        "CUDA libraries",
        "Do not download model weights",
        "Do not start a subprocess server",
        "direct CUDA job",
        "All GPU/model-serving work must go through the Rockie platform APIs above",
        "Do not pass provider credentials",
        "direct SSH deploy",
    ]
    missing = [snippet for snippet in required if snippet not in text]
    assert not missing, f"SKILL.md missing local execution guard: {missing!r}"


if __name__ == "__main__":
    tests = [
        test_documents_auth_and_identity_header_contract,
        test_references_platform_inference_loader_endpoints_only,
        test_quickstart_requires_validated_intent_before_skipping_hard_confirm,
        test_surfaces_platform_402_without_launcher_credit_gate,
        test_requires_endpoint_token_and_curl_output,
        test_forbids_local_gpu_model_serving_and_direct_provider_bypass,
    ]
    for test in tests:
        test()
    print(f"OK - {len(tests)} deploy-model contract checks passed")
