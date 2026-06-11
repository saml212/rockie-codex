from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "render_term_sheet.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("budget_render_term_sheet", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_render_available_term_sheet_contains_stage_breakdown():
    mod = _load_module()
    rendered = mod.render_term_sheet(
        {
            "quote_id": "qts_demo",
            "job_shape": "lora_finetune",
            "availability": "available",
            "status_code": "available",
            "status_reason": "Matching capacity is available.",
            "compute": {
                "display_provider": "Rockie GPU",
                "gpu_type": "A100_80GB",
                "gpu_count": 1,
                "region": "us",
                "tier": "spot",
            },
            "estimate_cents": 18000,
            "recommended_budget_cents": 27000,
            "user_budget_cents": 27000,
            "wallclock_minutes": 240,
            "confidence_bucket": "high",
            "confidence_statement": "High confidence quote.",
            "stages": [
                {"name": "bootstrap", "cents": 900},
                {"name": "data_prep", "cents": 1800},
                {"name": "train_or_run", "cents": 13500},
                {"name": "eval", "cents": 1260},
                {"name": "artifact_storage", "cents": 540},
            ],
            "decision": {"state": "pending", "approvable": True, "approved_for_submit": False},
        }
    )

    assert "# Rockie GPU Budget Term Sheet" in rendered
    assert "Rockie GPU, 1xA100_80GB, region=us, tier=spot" in rendered
    assert "Estimated total: $180.00 (18000 cents)" in rendered
    assert "- `train_or_run`: $135.00 (13500 cents)" in rendered
    assert "Reply with exactly one of:" in rendered


def test_render_unavailable_term_sheet_marks_non_approvable_state():
    mod = _load_module()
    rendered = mod.render_term_sheet(
        {
            "quote_id": "qts_unavail",
            "job_shape": "full_finetune",
            "availability": "unavailable",
            "status_code": "out_of_stock",
            "status_reason": "Matching capacity is out of stock. Try another region/tier or wait.",
            "compute": {
                "display_provider": "Rockie GPU",
                "gpu_type": "H100_SXM",
                "gpu_count": 4,
            },
            "estimate_cents": None,
            "recommended_budget_cents": None,
            "user_budget_cents": None,
            "wallclock_minutes": 720,
            "confidence_bucket": "n/a",
            "confidence_statement": "No approvable quote.",
            "stages": [],
            "decision": {"state": "pending", "approvable": False, "approved_for_submit": False},
        }
    )

    assert "Availability: unavailable (out_of_stock)" in rendered
    assert "Recommended budget: n/a" in rendered
    assert "Decision state: pending (approvable=no, approved_for_submit=no)" in rendered
