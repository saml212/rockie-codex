from __future__ import annotations

from typing import Any, Optional


STATE_MAP = {
    "QUEUED": "queued",
    "CLONING": "running",
    "INSTALLING": "running",
    "LOADING": "running",
    "LOADED": "loaded",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
}


def normalize_monitor_envelope(
    *,
    load_status: Optional[dict[str, Any]] = None,
    endpoint_status: Optional[dict[str, Any]] = None,
    dashboard_metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    load_status = load_status or {}
    endpoint_status = endpoint_status or {}
    dashboard_metadata = dashboard_metadata or {}
    target_id = (
        load_status.get("id")
        or endpoint_status.get("load_id")
        or dashboard_metadata.get("load_id")
        or "unknown"
    )
    state = _normalize_state(
        load_status.get("state")
        or endpoint_status.get("state")
        or dashboard_metadata.get("state")
    )
    utilization = {
        "gpu_percent": _number(load_status.get("gpu_percent")),
        "cpu_percent": _number(load_status.get("cpu_percent")),
        "memory_gb": _number(load_status.get("memory_gb")),
        "gpu_memory_gb": _number(load_status.get("gpu_memory_gb")),
        "state": "live",
        "reason_code": None,
        "reason": None,
        "observed_at": (
            load_status.get("observed_at")
            or endpoint_status.get("observed_at")
            or dashboard_metadata.get("observed_at")
        ),
    }
    if not any(utilization[key] is not None for key in ("gpu_percent", "cpu_percent", "memory_gb", "gpu_memory_gb")):
        utilization.update(
            {
                "state": "unavailable",
                "reason_code": "telemetry_not_exposed",
                "reason": "Inference load monitoring does not expose live utilization telemetry.",
            }
        )

    spend = {
        "cost_so_far_cents": _number(load_status.get("cost_so_far_cents")),
        "hourly_rate_cents": _number(load_status.get("hourly_rate_cents")),
        "budget_cents": _number(load_status.get("spend_cap_cents")),
        "state": "unavailable",
        "reason_code": "spend_not_exposed",
        "reason": "Inference load monitoring does not expose live spend details.",
    }
    if spend["cost_so_far_cents"] is not None:
        spend["state"] = "live"
        spend["reason_code"] = None
        spend["reason"] = None
    elif spend["hourly_rate_cents"] is not None or spend["budget_cents"] is not None:
        spend["state"] = "partial"
        spend["reason_code"] = "spend_budget_only"
        spend["reason"] = "Only budget or rate guidance is available; no observed spend has been reported yet."

    return {
        "monitor_owner": "inference-engineer",
        "monitor_target": {
            "kind": "load",
            "id": target_id,
        },
        "state": state,
        "utilization": utilization,
        "spend": spend,
        "artifacts": {
            "dashboard_note_id": dashboard_metadata.get("dashboard_note_id"),
            "profile_id": dashboard_metadata.get("monitoring_profile_id"),
            "profile_snapshot_present": bool(dashboard_metadata.get("monitoring_profile_snapshot")),
            "endpoint_url_present": bool(endpoint_status.get("url")),
        },
    }


def _normalize_state(raw_state: Any) -> str:
    if not isinstance(raw_state, str):
        return "unknown"
    return STATE_MAP.get(raw_state.upper(), raw_state.lower())


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
