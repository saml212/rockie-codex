from __future__ import annotations

from typing import Any, Optional


TERMINAL_STATES = {"done", "failed", "cancelled"}
STATE_MAP = {
    "QUEUED": "queued",
    "PENDING": "queued",
    "RUNNING": "running",
    "LOADING": "running",
    "DONE": "done",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
    "LOADED": "loaded",
}


def normalize_monitor_envelope(
    job: dict[str, Any],
    *,
    dashboard_note_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    profile_snapshot_present: bool = False,
) -> dict[str, Any]:
    state = _normalize_state(job.get("state"))
    observed_at = _first_present(
        job,
        "observed_at",
        "updated_at",
        "finished_at",
        "started_at",
    )
    target_id = job.get("id") or "unknown"

    utilization = {
        "gpu_percent": _number(job.get("gpu_percent")),
        "cpu_percent": _number(job.get("cpu_percent")),
        "memory_gb": _number(job.get("memory_gb")),
        "gpu_memory_gb": _number(job.get("gpu_memory_gb")),
        "state": "live",
        "reason_code": None,
        "reason": None,
        "observed_at": observed_at,
    }
    if not any(utilization[key] is not None for key in ("gpu_percent", "cpu_percent", "memory_gb", "gpu_memory_gb")):
        utilization.update(
            {
                "state": "unavailable",
                "reason_code": "telemetry_not_exposed",
                "reason": "Live utilization telemetry is unavailable from the job status API.",
            }
        )

    cost_so_far = _number(job.get("cost_so_far_cents"))
    cost_actual = _number(job.get("cost_actual_cents"))
    hourly_rate = _first_number(job.get("hourly_rate_cents"), job.get("estimated_hourly_rate_cents"))
    budget = _first_number(job.get("budget_cents"), job.get("user_budget_cents"))
    observed_spend = cost_so_far if cost_so_far is not None else cost_actual
    spend = {
        "cost_so_far_cents": observed_spend,
        "hourly_rate_cents": hourly_rate,
        "budget_cents": budget,
        "state": "unavailable",
        "reason_code": "spend_not_exposed",
        "reason": "Live spend details are unavailable from the job status API.",
    }
    if observed_spend is not None:
        spend["reason_code"] = None
        spend["reason"] = None
        if cost_so_far is not None:
            spend["state"] = "live"
        elif cost_actual is not None:
            if state in TERMINAL_STATES:
                spend["state"] = "settled"
                spend["reason"] = "Observed terminal spend is available but no longer live."
            else:
                spend["state"] = "partial"
                spend["reason"] = "Observed spend is available, but the job is not terminal so it is not live."
    elif hourly_rate is not None or budget is not None:
        spend["state"] = "partial"
        spend["reason_code"] = "spend_budget_only"
        spend["reason"] = "Only budget or rate guidance is available; no observed spend has been reported yet."

    return {
        "monitor_owner": "experiment",
        "monitor_target": {
            "kind": "job",
            "id": target_id,
        },
        "state": state,
        "utilization": utilization,
        "spend": spend,
        "artifacts": {
            "dashboard_note_id": dashboard_note_id,
            "profile_id": profile_id,
            "profile_snapshot_present": profile_snapshot_present,
            "artifact_path_present": job.get("artifact_path") is not None,
        },
    }


def _normalize_state(raw_state: Any) -> str:
    if not isinstance(raw_state, str):
        return "unknown"
    return STATE_MAP.get(raw_state.upper(), raw_state.lower())


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _first_number(*values: Any) -> Optional[float]:
    for value in values:
        numeric = _number(value)
        if numeric is not None:
            return numeric
    return None
