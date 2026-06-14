from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "quote_term_sheet.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("budget_quote_term_sheet", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_quote_from_market_row_uses_marked_up_cost():
    mod = _load_module()
    quote = mod.build_quote(
        {
            "job_shape": "lora_finetune",
            "gpu_type": "A100_80GB",
            "gpu_count": 1,
            "wallclock_minutes": 240,
        },
        market_json_path=str(_fixture("market_available.json")),
        api_url=None,
        allow_heuristic_dry_run=False,
    )

    assert quote["availability"] == "available"
    assert quote["estimate_cents"] == 18000
    assert quote["recommended_budget_cents"] == 27000
    assert sum(stage["cents"] for stage in quote["stages"]) == quote["estimate_cents"]
    assert quote["compute"]["display_provider"] == "Rockie GPU"
    assert quote["compute"]["region"] == "us"
    assert quote["compute"]["tier"] == "spot"


def test_quote_prices_fallback_is_medium_confidence():
    mod = _load_module()
    quote = mod.build_quote(
        {
            "job_shape": "eval_only",
            "gpu_type": "A100_80GB",
            "gpu_count": 2,
            "wallclock_minutes": 60,
        },
        market_json_path=str(_fixture("prices_fallback.json")),
        api_url=None,
        allow_heuristic_dry_run=False,
    )

    assert quote["availability"] == "available"
    assert quote["quote_source"] == "fixture"
    assert quote["estimate_cents"] == 5600
    assert quote["confidence_bucket"] in {"medium", "high"}


def test_quote_market_row_accepts_price_usd_per_hour(tmp_path):
    mod = _load_module()
    market = {
        "rows": [
            {
                "gpu_type": "A100_80GB",
                "gpu_count": 2,
                "region": "us",
                "tier": "spot",
                "available": True,
                "price_usd_per_hour": 28.0,
            }
        ]
    }
    market_path = tmp_path / "market-price-usd-per-hour.json"
    market_path.write_text(json.dumps(market), encoding="utf-8")

    quote = mod.build_quote(
        {
            "job_shape": "eval_only",
            "gpu_type": "A100_80GB",
            "gpu_count": 2,
            "wallclock_minutes": 60,
        },
        market_json_path=str(market_path),
        api_url=None,
        allow_heuristic_dry_run=False,
    )

    assert quote["availability"] == "available"
    assert quote["estimate_cents"] == 5600
    assert quote["confidence_bucket"] == "medium"


def test_quote_unavailable_when_market_has_no_matching_sku():
    mod = _load_module()
    quote = mod.build_quote(
        {
            "job_shape": "synthetic_data_gen",
            "gpu_type": "B200",
            "gpu_count": 1,
            "wallclock_minutes": 180,
        },
        market_json_path=str(_fixture("market_no_sku.json")),
        api_url=None,
        allow_heuristic_dry_run=False,
    )

    assert quote["availability"] == "unavailable"
    assert quote["status_code"] == "no_sku_fits"
    assert quote["recommended_budget_cents"] is None


def test_requested_region_constrains_market_availability(tmp_path):
    mod = _load_module()
    market = {
        "rows": [
            {
                "gpu_type": "A100_80GB",
                "gpu_count": 1,
                "region": "us-east",
                "tier": "spot",
                "available": True,
                "marked_up_cost_cents": 18000,
            },
            {
                "gpu_type": "A100_80GB",
                "gpu_count": 1,
                "region": "us-west",
                "tier": "spot",
                "available": False,
                "marked_up_cost_cents": 17000,
            },
        ]
    }
    market_path = tmp_path / "market-region.json"
    market_path.write_text(json.dumps(market), encoding="utf-8")

    quote = mod.build_quote(
        {
            "job_shape": "eval_only",
            "gpu_type": "A100_80GB",
            "gpu_count": 1,
            "wallclock_minutes": 60,
            "region": "us-west",
            "tier": "spot",
        },
        market_json_path=str(market_path),
        api_url=None,
        allow_heuristic_dry_run=False,
    )

    assert quote["availability"] == "unavailable"
    assert quote["status_code"] == "out_of_stock"
    assert quote["compute"]["region"] == "us-west"


def test_requested_tier_constrains_market_availability(tmp_path):
    mod = _load_module()
    market = {
        "rows": [
            {
                "gpu_type": "A100_80GB",
                "gpu_count": 1,
                "region": "us",
                "tier": "on_demand",
                "available": True,
                "marked_up_cost_cents": 20000,
            },
            {
                "gpu_type": "A100_80GB",
                "gpu_count": 1,
                "region": "us",
                "tier": "spot",
                "available": False,
                "marked_up_cost_cents": 16000,
            },
        ]
    }
    market_path = tmp_path / "market-tier.json"
    market_path.write_text(json.dumps(market), encoding="utf-8")

    quote = mod.build_quote(
        {
            "job_shape": "eval_only",
            "gpu_type": "A100_80GB",
            "gpu_count": 1,
            "wallclock_minutes": 60,
            "region": "us",
            "tier": "spot",
        },
        market_json_path=str(market_path),
        api_url=None,
        allow_heuristic_dry_run=False,
    )

    assert quote["availability"] == "unavailable"
    assert quote["status_code"] == "out_of_stock"
    assert quote["compute"]["tier"] == "spot"


def test_live_quote_requests_identify_rockie_runtime(monkeypatch):
    mod = _load_module()
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"rows":[]}'

    def fake_urlopen(req, timeout):
        captured["headers"] = dict(req.header_items())
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("ROCKIELAB_TENANT_TOKEN", "service-token")
    monkeypatch.setenv("ROCKIELAB_TENANT_ID", "t-aaaaaaaaaaaa")
    with mock.patch.object(mod.urllib.request, "urlopen", fake_urlopen):
        response = mod._http_json("https://api.rockielab.com/api/gpu/market")

    assert response.status == 200
    assert captured["timeout"] == 30
    assert captured["headers"]["User-agent"].startswith("rockie-runtime/")
    assert "Python-urllib" not in captured["headers"]["User-agent"]
    assert captured["headers"]["X-tenant-token"] == "service-token"
    assert captured["headers"]["X-tenant-id"] == "t-aaaaaaaaaaaa"


def test_live_quote_rejects_non_https_before_tenant_auth(monkeypatch):
    mod = _load_module()

    def fake_urlopen(req, timeout):
        raise AssertionError("urlopen must not be called for untrusted api_url")

    monkeypatch.setenv("ROCKIELAB_TENANT_TOKEN", "service-token")
    monkeypatch.setenv("ROCKIELAB_TENANT_ID", "t-aaaaaaaaaaaa")
    with mock.patch.object(mod.urllib.request, "urlopen", fake_urlopen):
        try:
            mod.build_quote(
                {
                    "job_shape": "eval_only",
                    "gpu_type": "A100_80GB",
                    "gpu_count": 1,
                },
                market_json_path=None,
                api_url="http://api.rockielab.com",
                allow_heuristic_dry_run=False,
            )
        except mod.QuoteError as exc:
            assert "must use https" in str(exc)
        else:
            raise AssertionError("expected QuoteError")


def test_live_quote_rejects_non_rockie_host_before_tenant_auth(monkeypatch):
    mod = _load_module()

    def fake_urlopen(req, timeout):
        raise AssertionError("urlopen must not be called for untrusted api_url")

    monkeypatch.setenv("ROCKIELAB_TENANT_TOKEN", "service-token")
    monkeypatch.setenv("ROCKIELAB_TENANT_ID", "t-aaaaaaaaaaaa")
    with mock.patch.object(mod.urllib.request, "urlopen", fake_urlopen):
        try:
            mod.build_quote(
                {
                    "job_shape": "eval_only",
                    "gpu_type": "A100_80GB",
                    "gpu_count": 1,
                },
                market_json_path=None,
                api_url="https://example.invalid",
                allow_heuristic_dry_run=False,
            )
        except mod.QuoteError as exc:
            assert "host is not trusted" in str(exc)
        else:
            raise AssertionError("expected QuoteError")


def _fixture(name: str) -> Path:
    return Path(__file__).with_name("fixtures") / name
