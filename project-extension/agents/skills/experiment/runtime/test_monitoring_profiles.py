from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("monitoring_profiles.py")
MONITOR_CONTRACT_PATH = Path(__file__).with_name("monitor_contract.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("experiment_monitoring_profiles", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_monitor_contract_module():
    spec = importlib.util.spec_from_file_location("experiment_monitor_contract", MONITOR_CONTRACT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_resolve_common_fallback_snapshot_includes_required_fields():
    mod = _load_module()

    profile_id, snapshot = mod.resolve_profile_snapshot(
        profile_id=None,
        run_name="generic smoke",
        origin_skill="experiment",
        software=None,
        script="#!/bin/bash\necho hello\n",
    )

    assert profile_id == "common.default.v1"
    assert snapshot["unprofiled"] is True
    assert snapshot["run_name"] == "generic smoke"
    assert snapshot["origin_skill"] == "experiment"
    assert snapshot["duration_class"] == "medium"
    assert snapshot["cadence_bounds"]["cadence_seconds"] == 300
    assert snapshot["metrics"]
    assert snapshot["panels"]
    assert snapshot["red_flags"]
    assert "stop_policy" in snapshot
    assert "safe_to_auto_stop" in snapshot


def test_resolve_physics_adapter_from_software_inference():
    mod = _load_module()

    profile_id, snapshot = mod.resolve_profile_snapshot(
        profile_id=None,
        run_name="md equilibration",
        origin_skill="experiment",
        software=None,
        script="#!/bin/bash\ngmx mdrun -deffnm md\n",
    )

    assert profile_id == "physics.molecular_dynamics.v1"
    assert snapshot["profile_id"] == "physics.molecular_dynamics.v1"
    assert snapshot["family"] == "molecular_dynamics"
    assert snapshot["software"] == "gromacs"


def test_explicit_profile_is_preserved():
    mod = _load_module()

    profile_id, snapshot = mod.resolve_profile_snapshot(
        profile_id="experiment.ml_baseline.v1",
        run_name="finetune run",
        origin_skill="experiment",
        software="pytorch",
        script="#!/bin/bash\ntorchrun train.py\n",
    )

    assert profile_id == "experiment.ml_baseline.v1"
    assert snapshot["requested_profile_id"] == "experiment.ml_baseline.v1"
    assert snapshot["unprofiled"] is False


def test_monitor_contract_marks_missing_utilization_as_telemetry_not_exposed():
    mod = _load_monitor_contract_module()

    envelope = mod.normalize_monitor_envelope(
        {
            "id": "job-123",
            "state": "RUNNING",
            "cost_so_far_cents": 45,
        },
        dashboard_note_id="note:dash-2",
        profile_id="experiment.ml_baseline.v1",
        profile_snapshot_present=True,
    )

    assert envelope["monitor_owner"] == "experiment"
    assert envelope["monitor_target"]["kind"] == "job"
    assert envelope["monitor_target"]["id"] == "job-123"
    assert envelope["state"] == "running"
    assert envelope["utilization"]["state"] == "unavailable"
    assert envelope["utilization"]["reason_code"] == "telemetry_not_exposed"
    assert envelope["spend"]["state"] == "live"
    assert envelope["spend"]["reason_code"] is None
    assert envelope["spend"]["reason"] is None
    assert envelope["artifacts"]["dashboard_note_id"] == "note:dash-2"
    assert envelope["artifacts"]["profile_snapshot_present"] is True


def test_monitor_contract_marks_terminal_cost_actual_only_payload_as_settled_spend():
    mod = _load_monitor_contract_module()

    envelope = mod.normalize_monitor_envelope(
        {
            "id": "job-456",
            "state": "DONE",
            "cost_actual_cents": 123,
            "finished_at": "2026-06-12T12:34:56Z",
        }
    )

    assert envelope["state"] == "done"
    assert envelope["utilization"]["observed_at"] == "2026-06-12T12:34:56Z"
    assert envelope["spend"]["state"] == "settled"
    assert envelope["spend"]["reason_code"] is None
    assert envelope["spend"]["reason"] is not None
    assert envelope["spend"]["cost_so_far_cents"] == 123.0
    assert envelope["spend"]["hourly_rate_cents"] is None
    assert envelope["spend"]["budget_cents"] is None


def test_monitor_contract_treats_budget_only_spend_as_partial_not_live() -> None:
    mod = _load_monitor_contract_module()

    envelope = mod.normalize_monitor_envelope(
        {
            "id": "job-budget-only",
            "state": "RUNNING",
            "budget_cents": 1000,
            "hourly_rate_cents": 25,
        }
    )

    assert envelope["spend"]["cost_so_far_cents"] is None
    assert envelope["spend"]["state"] == "partial"
    assert envelope["spend"]["reason_code"] == "spend_budget_only"
    assert envelope["spend"]["reason"] is not None


def test_monitor_contract_preserves_zero_spend_and_redacts_artifact_path() -> None:
    mod = _load_monitor_contract_module()

    envelope = mod.normalize_monitor_envelope(
        {
            "id": "job-zero",
            "state": "RUNNING",
            "cost_so_far_cents": 0,
            "estimated_hourly_rate_cents": 0,
            "user_budget_cents": 0,
            "artifact_path": "/tmp/tenant-secret-token-abc123/run.json",
        }
    )

    assert envelope["spend"]["cost_so_far_cents"] == 0.0
    assert envelope["spend"]["hourly_rate_cents"] == 0.0
    assert envelope["spend"]["budget_cents"] == 0.0
    assert envelope["spend"]["state"] == "live"
    assert envelope["spend"]["reason_code"] is None
    assert envelope["spend"]["reason"] is None
    assert envelope["artifacts"]["artifact_path_present"] is True
    assert set(envelope["artifacts"]) == {
        "dashboard_note_id",
        "profile_id",
        "profile_snapshot_present",
        "artifact_path_present",
    }
    assert "/tmp/tenant-secret-token-abc123/run.json" not in repr(envelope)


if __name__ == "__main__":
    tests = [
        test_resolve_common_fallback_snapshot_includes_required_fields,
        test_resolve_physics_adapter_from_software_inference,
        test_explicit_profile_is_preserved,
        test_monitor_contract_marks_missing_utilization_as_telemetry_not_exposed,
        test_monitor_contract_marks_terminal_cost_actual_only_payload_as_settled_spend,
        test_monitor_contract_treats_budget_only_spend_as_partial_not_live,
        test_monitor_contract_preserves_zero_spend_and_redacts_artifact_path,
    ]
    for test in tests:
        test()
    print(f"OK - {len(tests)} experiment monitoring profile checks passed")
