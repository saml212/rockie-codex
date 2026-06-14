#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_WALLCLOCK_MINUTES = {
    "lora_finetune": 240,
    "full_finetune": 720,
    "eval_only": 60,
    "inference_deploy": 120,
    "synthetic_data_gen": 180,
}

HEURISTIC_MARKED_UP_HOURLY_CENTS = {
    "A40_48GB": 950,
    "A100_80GB": 2800,
    "H100_SXM": 5200,
    "H200": 6900,
    "B200": 11500,
}

STAGE_WEIGHTS = (
    ("bootstrap", 5),
    ("data_prep", 10),
    ("train_or_run", 75),
    ("eval", 7),
    ("artifact_storage", 3),
)
ROCKIE_RUNTIME_USER_AGENT = "rockie-runtime/1.0 (+https://api.rockielab.com)"
TRUSTED_ROCKIE_API_HOST = "api.rockielab.com"


class QuoteError(RuntimeError):
    pass


class Response:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self.body = body


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="quote_term_sheet.py",
        description="Build a budget term-sheet JSON object for a Rockie GPU job.",
    )
    parser.add_argument("--job-spec-json", default=None, help="Path to the job spec JSON.")
    parser.add_argument("--market-json", default=None, help="Path to a market/prices fixture JSON.")
    parser.add_argument("--api-url", default=None, help="Override ROCKIELAB_API_URL.")
    parser.add_argument(
        "--allow-heuristic-dry-run",
        action="store_true",
        help="Allow the local heuristic table when live pricing is unavailable.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        job_spec = _load_json_input(args.job_spec_json)
        quote = build_quote(
            job_spec,
            market_json_path=args.market_json,
            api_url=args.api_url,
            allow_heuristic_dry_run=args.allow_heuristic_dry_run,
        )
    except QuoteError as exc:
        sys.stderr.write(f"quote_term_sheet.py: error: {exc}\n")
        return 2

    json.dump(quote, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def build_quote(
    job_spec: dict[str, Any],
    *,
    market_json_path: Optional[str],
    api_url: Optional[str],
    allow_heuristic_dry_run: bool,
) -> dict[str, Any]:
    normalized_spec = _normalize_job_spec(job_spec)
    duration_hours = normalized_spec["wallclock_minutes"] / 60.0

    response, quote_source = _select_quote_source(
        normalized_spec,
        market_json_path=market_json_path,
        api_url=api_url,
        allow_heuristic_dry_run=allow_heuristic_dry_run,
        duration_hours=duration_hours,
    )
    quote = _normalize_quote_response(
        normalized_spec,
        response=response,
        quote_source=quote_source,
        duration_hours=duration_hours,
    )
    return quote


def _load_json_input(path: Optional[str]) -> dict[str, Any]:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    raw = sys.stdin.read().strip()
    if not raw:
        raise QuoteError("job spec JSON is required via stdin or --job-spec-json.")
    return json.loads(raw)


def _normalize_job_spec(job_spec: dict[str, Any]) -> dict[str, Any]:
    gpu_type = str(job_spec.get("gpu_type") or "").strip()
    if not gpu_type:
        raise QuoteError("job spec missing gpu_type.")
    gpu_count = int(job_spec.get("gpu_count") or 0)
    if gpu_count < 1:
        raise QuoteError("job spec gpu_count must be >= 1.")

    job_shape = str(job_spec.get("job_shape") or "").strip()
    wallclock_minutes = job_spec.get("wallclock_minutes")
    if wallclock_minutes is not None:
        wallclock_minutes = int(math.ceil(float(wallclock_minutes)))
    elif job_spec.get("timeout_seconds") is not None:
        wallclock_minutes = int(math.ceil(float(job_spec["timeout_seconds"]) / 60.0))
    elif job_shape in DEFAULT_WALLCLOCK_MINUTES:
        wallclock_minutes = DEFAULT_WALLCLOCK_MINUTES[job_shape]
    else:
        raise QuoteError(
            "unknown job_shape without explicit wallclock_minutes or timeout_seconds."
        )
    if wallclock_minutes < 1:
        raise QuoteError("wallclock_minutes must be >= 1.")

    normalized = {
        "job_shape": job_shape or "custom",
        "gpu_type": gpu_type,
        "gpu_count": gpu_count,
        "wallclock_minutes": wallclock_minutes,
        "region": job_spec.get("region") or "us",
        "tier": job_spec.get("tier") or "spot",
        "dataset_size_gb": job_spec.get("dataset_size_gb"),
        "artifact_size_gb": job_spec.get("artifact_size_gb"),
        "quote_source": job_spec.get("quote_source"),
    }
    return normalized


def _select_quote_source(
    job_spec: dict[str, Any],
    *,
    market_json_path: Optional[str],
    api_url: Optional[str],
    allow_heuristic_dry_run: bool,
    duration_hours: float,
) -> tuple[Response, str]:
    if market_json_path:
        data = json.loads(Path(market_json_path).read_text(encoding="utf-8"))
        return Response(status=200, body=data), "fixture"

    resolved_api = (api_url or os.environ.get("ROCKIELAB_API_URL", "")).rstrip("/")
    if resolved_api and os.environ.get("ROCKIELAB_TENANT_TOKEN") and os.environ.get("ROCKIELAB_TENANT_ID"):
        _validate_trusted_rockie_api_url(resolved_api)
        market_response = _get_market_response(resolved_api, job_spec, duration_hours)
        if market_response.status == 200:
            return market_response, "market"
        if _is_route_missing_market(market_response):
            prices_response = _get_prices_response(resolved_api)
            if prices_response.status == 200:
                return prices_response, "prices"
            return prices_response, "prices"
        return market_response, "market"

    if allow_heuristic_dry_run:
        heuristic = {
            "source": "heuristic",
            "gpu_type": job_spec["gpu_type"],
            "marked_up_hourly_cents": HEURISTIC_MARKED_UP_HOURLY_CENTS.get(job_spec["gpu_type"]),
        }
        if job_spec.get("region"):
            heuristic["region"] = job_spec["region"]
        if job_spec.get("tier"):
            heuristic["tier"] = job_spec["tier"]
        return Response(status=200, body=heuristic), "heuristic"

    raise QuoteError(
        "no market quote available. Provide --market-json, configure Rockie API env, "
        "or opt into --allow-heuristic-dry-run."
    )


def _validate_trusted_rockie_api_url(api_url: str) -> None:
    parsed = urllib.parse.urlparse(api_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https":
        raise QuoteError("Rockie API URL must use https before tenant auth is attached.")
    if host != TRUSTED_ROCKIE_API_HOST and not host.endswith(f".{TRUSTED_ROCKIE_API_HOST}"):
        raise QuoteError("Rockie API URL host is not trusted for tenant auth.")


def _get_market_response(api_url: str, job_spec: dict[str, Any], duration_hours: float) -> Response:
    query = {
        "gpu_type": job_spec["gpu_type"],
        "gpu_count": str(job_spec["gpu_count"]),
        "duration_hours": f"{duration_hours:.2f}",
    }
    if job_spec.get("region"):
        query["region"] = str(job_spec["region"])
    if job_spec.get("tier"):
        query["tier"] = str(job_spec["tier"])
    url = f"{api_url}/api/gpu/market?{urllib.parse.urlencode(query)}"
    return _http_json(url)


def _get_prices_response(api_url: str) -> Response:
    return _http_json(f"{api_url}/api/gpu/prices")


def _http_json(url: str) -> Response:
    _validate_trusted_rockie_api_url(url)
    headers = {
        "Accept": "application/json",
        "User-Agent": ROCKIE_RUNTIME_USER_AGENT,
        "X-Tenant-Token": os.environ.get("ROCKIELAB_TENANT_TOKEN", ""),
        "X-Tenant-Id": os.environ.get("ROCKIELAB_TENANT_ID", ""),
    }
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read()
            return Response(status=response.status, body=_decode_json(body))
    except urllib.error.HTTPError as exc:
        return Response(status=exc.code, body=_decode_json(exc.read()))
    except urllib.error.URLError as exc:
        return Response(status=502, body={"error": str(exc.reason)})


def _decode_json(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body.decode("utf-8", errors="replace")


def _is_route_missing_market(response: Response) -> bool:
    if response.status != 404:
        return False
    body = response.body
    text = json.dumps(body).lower() if isinstance(body, (dict, list)) else str(body).lower()
    if "no_sku_fits" in text:
        return False
    route_markers = ("route", "endpoint", "not implemented", "not found", "/api/gpu/market")
    return any(marker in text for marker in route_markers)


def _normalize_quote_response(
    job_spec: dict[str, Any],
    *,
    response: Response,
    quote_source: str,
    duration_hours: float,
) -> dict[str, Any]:
    body = response.body

    if response.status == 402:
        return _build_unavailable_quote(
            job_spec,
            quote_source=quote_source,
            status_code="insufficient_credit",
            status_reason="Quote source rejected the request for insufficient credit. Top up or lower the duration / SKU.",
        )

    if response.status in (502, 503) or _all_adapters_failed(body):
        return _build_failed_quote(
            job_spec,
            quote_source=quote_source,
            status_code="quote_source_failed" if response.status == 502 else "out_of_stock",
            status_reason=(
                "Quote source failed. Retry later; do not submit."
                if response.status == 502
                else "Matching capacity is out of stock. Try another region/tier or wait."
            ),
        )

    selected_row = _select_available_market_row(body, job_spec)
    if response.status == 200 and selected_row is not None:
        estimate_cents, source_confidence = _estimate_from_market_row(
            row=selected_row,
            body=body,
            gpu_count=job_spec["gpu_count"],
            duration_hours=duration_hours,
        )
        confidence_bucket = _confidence_bucket(job_spec, quote_source, source_confidence)
        recommended_budget_cents = _recommended_budget_cents(estimate_cents, confidence_bucket)
        return _build_available_quote(
            job_spec,
            quote_source=quote_source,
            estimate_cents=estimate_cents,
            recommended_budget_cents=recommended_budget_cents,
            confidence_bucket=confidence_bucket,
            status_reason="Matching capacity is available.",
        )

    prices_estimate = _estimate_from_prices_map(body, job_spec, duration_hours)
    if prices_estimate is not None:
        estimate_cents = prices_estimate
        confidence_bucket = _confidence_bucket(job_spec, quote_source, "medium")
        recommended_budget_cents = _recommended_budget_cents(estimate_cents, confidence_bucket)
        return _build_available_quote(
            job_spec,
            quote_source=quote_source,
            estimate_cents=estimate_cents,
            recommended_budget_cents=recommended_budget_cents,
            confidence_bucket=confidence_bucket,
            status_reason="Compatibility pricing fallback matched the requested SKU.",
        )

    if quote_source == "heuristic":
        hourly = HEURISTIC_MARKED_UP_HOURLY_CENTS.get(job_spec["gpu_type"])
        if hourly is None:
            return _build_unavailable_quote(
                job_spec,
                quote_source=quote_source,
                status_code="no_sku_fits",
                status_reason="The heuristic table has no rate for the requested GPU. Choose a different SKU.",
            )
        estimate_cents = int(math.ceil(hourly * job_spec["gpu_count"] * duration_hours))
        confidence_bucket = _confidence_bucket(job_spec, quote_source, "low")
        recommended_budget_cents = _recommended_budget_cents(estimate_cents, confidence_bucket)
        return _build_available_quote(
            job_spec,
            quote_source=quote_source,
            estimate_cents=estimate_cents,
            recommended_budget_cents=recommended_budget_cents,
            confidence_bucket=confidence_bucket,
            status_reason="Heuristic dry-run quote only; confirm with live pricing before submission.",
        )

    status_code = "out_of_stock" if response.status == 503 or _matching_rows_unavailable(body, job_spec) else "no_sku_fits"
    status_reason = (
        "Matching capacity is out of stock. Try another region/tier or wait."
        if status_code == "out_of_stock"
        else "No matching SKU fit the request. Choose a smaller or different GPU."
    )
    return _build_unavailable_quote(
        job_spec,
        quote_source=quote_source,
        status_code=status_code,
        status_reason=status_reason,
    )


def _all_adapters_failed(body: Any) -> bool:
    text = json.dumps(body).lower() if isinstance(body, (dict, list)) else str(body).lower()
    return "adapter" in text and "failed" in text


def _market_rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, dict):
        if isinstance(body.get("rows"), list):
            return [row for row in body["rows"] if isinstance(row, dict)]
        if isinstance(body.get("options"), list):
            return [row for row in body["options"] if isinstance(row, dict)]
        if (
            body.get("gpu_type")
            or body.get("usd_per_hour")
            or body.get("price_usd_per_hour")
            or body.get("marked_up_cost_cents")
        ):
            return [body]
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    return []


def _select_available_market_row(body: Any, job_spec: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows = _market_rows(body)
    matching: list[dict[str, Any]] = []
    for row in rows:
        if not _row_matches_requested_dimensions(row, job_spec):
            continue
        matching.append(row)
    if not matching:
        return None
    for row in matching:
        if _row_available(row):
            return row
    return None


def _matching_rows_unavailable(body: Any, job_spec: dict[str, Any]) -> bool:
    rows = _market_rows(body)
    saw_match = False
    for row in rows:
        if not _row_matches_requested_dimensions(row, job_spec):
            continue
        saw_match = True
    return saw_match


def _row_matches_requested_dimensions(row: dict[str, Any], job_spec: dict[str, Any]) -> bool:
    row_gpu = str(row.get("gpu_type") or row.get("sku") or row.get("name") or "").strip()
    if row_gpu and row_gpu != job_spec["gpu_type"]:
        return False
    row_count = row.get("gpu_count") or row.get("count")
    if row_count is not None and int(row_count) != job_spec["gpu_count"]:
        return False
    requested_region = job_spec.get("region")
    if requested_region is not None:
        row_region = row.get("region") or "us"
        if str(row_region) != str(requested_region):
            return False
    requested_tier = job_spec.get("tier")
    if requested_tier is not None:
        row_tier = row.get("tier") or "spot"
        if str(row_tier) != str(requested_tier):
            return False
    return True


def _row_available(row: dict[str, Any]) -> bool:
    for key in ("available", "is_available", "in_stock"):
        value = row.get(key)
        if isinstance(value, bool):
            return value
    status = str(row.get("status") or "").lower()
    if status:
        return status in {"available", "ok", "ready"}
    return True


def _estimate_from_market_row(
    *,
    row: dict[str, Any],
    body: Any,
    gpu_count: int,
    duration_hours: float,
) -> tuple[int, str]:
    if isinstance(body, dict) and body.get("recommended_total_cost_cents") is not None:
        return int(body["recommended_total_cost_cents"]), "high"
    if row.get("marked_up_cost_cents") is not None:
        return int(row["marked_up_cost_cents"]), "high"
    if row.get("recommended_total_cost_cents") is not None:
        return int(row["recommended_total_cost_cents"]), "high"
    if row.get("marked_up_hourly_cents") is not None:
        return int(math.ceil(float(row["marked_up_hourly_cents"]) * gpu_count * duration_hours)), "high"
    if row.get("usd_per_hour") is not None:
        return int(math.ceil(float(row["usd_per_hour"]) * 100 * gpu_count * duration_hours)), "medium"
    if row.get("price_usd_per_hour") is not None:
        return int(math.ceil(float(row["price_usd_per_hour"]) * 100 * gpu_count * duration_hours)), "medium"
    raise QuoteError("market row matched but exposed no usable price field.")


def _estimate_from_prices_map(body: Any, job_spec: dict[str, Any], duration_hours: float) -> Optional[int]:
    if isinstance(body, dict):
        if isinstance(body.get("prices"), dict):
            price = body["prices"].get(job_spec["gpu_type"])
            if price is not None:
                return int(math.ceil(float(price) * 100 * job_spec["gpu_count"] * duration_hours))
        if isinstance(body.get("prices"), list):
            body = body["prices"]
        elif body.get(job_spec["gpu_type"]) is not None:
            return int(math.ceil(float(body[job_spec["gpu_type"]]) * 100 * job_spec["gpu_count"] * duration_hours))
    if isinstance(body, list):
        for row in body:
            if not isinstance(row, dict):
                continue
            if not _row_matches_requested_dimensions(row, job_spec):
                continue
            if row.get("usd_per_hour") is not None:
                return int(math.ceil(float(row["usd_per_hour"]) * 100 * job_spec["gpu_count"] * duration_hours))
            if row.get("price_usd_per_hour") is not None:
                return int(math.ceil(float(row["price_usd_per_hour"]) * 100 * job_spec["gpu_count"] * duration_hours))
    return None


def _confidence_bucket(job_spec: dict[str, Any], quote_source: str, source_confidence: str) -> str:
    explicit_duration = job_spec["job_shape"] in DEFAULT_WALLCLOCK_MINUTES
    inferred_dimensions = 0 if explicit_duration else 1
    if quote_source == "heuristic":
        return "low"
    if quote_source == "prices":
        return "medium"
    if source_confidence == "high" and inferred_dimensions == 0 and job_spec["job_shape"] in DEFAULT_WALLCLOCK_MINUTES:
        return "high"
    if inferred_dimensions <= 1:
        return "medium"
    return "low"


def _recommended_budget_cents(estimate_cents: int, confidence_bucket: str) -> int:
    if confidence_bucket == "low":
        return max(int(math.ceil(estimate_cents * 2.0)), estimate_cents + 5000)
    return max(int(math.ceil(estimate_cents * 1.5)), estimate_cents + 5000)


def _build_available_quote(
    job_spec: dict[str, Any],
    *,
    quote_source: str,
    estimate_cents: int,
    recommended_budget_cents: int,
    confidence_bucket: str,
    status_reason: str,
) -> dict[str, Any]:
    return {
        "quote_id": f"qts_{uuid.uuid4().hex[:12]}",
        "job_shape": job_spec["job_shape"],
        "availability": "available",
        "status_code": "available",
        "status_reason": status_reason,
        "quote_source": quote_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compute": _build_compute(job_spec),
        "stages": _allocate_stages(estimate_cents),
        "estimate_cents": estimate_cents,
        "recommended_budget_cents": recommended_budget_cents,
        "user_budget_cents": recommended_budget_cents,
        "wallclock_minutes": job_spec["wallclock_minutes"],
        "confidence_bucket": confidence_bucket,
        "confidence_statement": _confidence_statement(confidence_bucket, quote_source),
        "decision": _pending_decision(True),
    }


def _build_unavailable_quote(
    job_spec: dict[str, Any],
    *,
    quote_source: str,
    status_code: str,
    status_reason: str,
) -> dict[str, Any]:
    return {
        "quote_id": f"qts_{uuid.uuid4().hex[:12]}",
        "job_shape": job_spec["job_shape"],
        "availability": "unavailable",
        "status_code": status_code,
        "status_reason": status_reason,
        "quote_source": quote_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "compute": _build_compute(job_spec),
        "stages": [],
        "estimate_cents": None,
        "recommended_budget_cents": None,
        "user_budget_cents": None,
        "wallclock_minutes": job_spec["wallclock_minutes"],
        "confidence_bucket": "n/a",
        "confidence_statement": "No approvable quote. Adjust the job shape or retry the market lookup.",
        "decision": _pending_decision(False),
    }


def _build_failed_quote(
    job_spec: dict[str, Any],
    *,
    quote_source: str,
    status_code: str,
    status_reason: str,
) -> dict[str, Any]:
    payload = _build_unavailable_quote(
        job_spec,
        quote_source=quote_source,
        status_code=status_code,
        status_reason=status_reason,
    )
    payload["availability"] = "quote_failed"
    payload["decision"] = _pending_decision(False)
    return payload


def _build_compute(job_spec: dict[str, Any]) -> dict[str, Any]:
    compute = {
        "display_provider": "Rockie GPU",
        "gpu_type": job_spec["gpu_type"],
        "gpu_count": job_spec["gpu_count"],
    }
    if job_spec.get("region"):
        compute["region"] = job_spec["region"]
    if job_spec.get("tier"):
        compute["tier"] = job_spec["tier"]
    return compute


def _allocate_stages(estimate_cents: int) -> list[dict[str, Any]]:
    remaining = estimate_cents
    stages: list[dict[str, Any]] = []
    for index, (name, percent) in enumerate(STAGE_WEIGHTS):
        if index == len(STAGE_WEIGHTS) - 1:
            cents = remaining
        else:
            cents = int(round(estimate_cents * percent / 100.0))
            remaining -= cents
        stages.append({"name": name, "cents": cents, "usd": cents / 100.0})
    return stages


def _confidence_statement(confidence_bucket: str, quote_source: str) -> str:
    if confidence_bucket == "high":
        return f"High confidence: live {quote_source} pricing with a standard job shape and explicit duration."
    if confidence_bucket == "medium":
        return f"Medium confidence: quote normalized from {quote_source} data with one inferred dimension or a compatibility fallback."
    return f"Low confidence: heuristic or weakly specified quote from {quote_source}; treat the budget as a conservative cap."


def _pending_decision(approvable: bool) -> dict[str, Any]:
    return {
        "state": "pending",
        "approvable": approvable,
        "approved_for_submit": False,
    }


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
