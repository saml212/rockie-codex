from __future__ import annotations

from pathlib import Path


def test_inference_engineer_documents_auth_and_identity_headers():
    src = Path(__file__).with_name("SKILL.md").read_text(encoding="utf-8")

    assert 'X-Tenant-Token: $ROCKIELAB_TENANT_TOKEN' in src
    assert 'X-Tenant-Id: $ROCKIELAB_TENANT_ID' in src
    assert "Never use tenant identity as auth" in src


def test_step_7_renders_inference_preview_before_reporting_success():
    src = Path(__file__).with_name("SKILL.md").read_text(encoding="utf-8")

    assert "visualize_inference_result" in src
    assert "notebook_id" in src
    assert "output_assets" in src
    assert "Report success only after `visualize_inference_result` returns a Note id" in src
    assert "never paste raw SAM mask base64 into chat" in src
