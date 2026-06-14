from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from unittest import mock
import contextlib
import io


SUBMIT_PATH = Path(__file__).with_name("submit.py")


def load_submit_mod():
    spec = importlib.util.spec_from_file_location("experiment_submit", SUBMIT_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def rockie_env(**overrides):
    env = {
        "ROCKIELAB_API_URL": "https://platform.test",
        "ROCKIELAB_TENANT_TOKEN": "service-token",
        "ROCKIELAB_TENANT_ID": "t-aaaaaaaaaaaa",
    }
    env.update(overrides)
    return mock.patch.dict(os.environ, env, clear=False)


def test_headers_send_auth_token_and_tenant_id():
    submit_mod = load_submit_mod()
    with mock.patch.dict(
        os.environ,
        {
            "ROCKIELAB_TENANT_TOKEN": "service-token",
            "ROCKIELAB_TENANT_ID": "t-aaaaaaaaaaaa",
        },
        clear=False,
    ):
        assert submit_mod._headers() == {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": submit_mod.ROCKIE_RUNTIME_USER_AGENT,
            "X-Tenant-Token": "service-token",
            "X-Tenant-Id": "t-aaaaaaaaaaaa",
        }


def test_headers_identify_rockie_runtime_not_urllib_default():
    submit_mod = load_submit_mod()
    with mock.patch.dict(
        os.environ,
        {
            "ROCKIELAB_TENANT_TOKEN": "service-token",
            "ROCKIELAB_TENANT_ID": "t-aaaaaaaaaaaa",
        },
        clear=False,
    ):
        user_agent = submit_mod._headers()["User-Agent"]

    assert user_agent.startswith("rockie-runtime/")
    assert "Python-urllib" not in user_agent


def test_headers_tenant_id_override():
    submit_mod = load_submit_mod()
    with mock.patch.dict(
        os.environ,
        {
            "ROCKIELAB_TENANT_TOKEN": "service-token",
            "ROCKIELAB_TENANT_ID": "t-envtenant000",
        },
        clear=False,
    ):
        assert submit_mod._headers("t-override000")["X-Tenant-Id"] == "t-override000"


def test_submit_includes_dashboard_profile_metadata():
    submit_mod = load_submit_mod()
    tmp_dir = Path(os.environ.get("TMPDIR", "/tmp"))
    script_file = tmp_dir / "train_model_submit_test.sh"
    script_file.write_text("#!/bin/bash\naccelerate launch train.py\n", encoding="utf-8")
    term_sheet_file = tmp_dir / "train_model_submit_test.term-sheet.json"
    term_sheet_file.write_text(json.dumps(_approved_term_sheet()), encoding="utf-8")

    captured = {}

    def fake_http_request(method, url, *, headers, body=None):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(body.decode("utf-8"))
        return 200, b'{"job_id":"job-123"}'

    with mock.patch.dict(
        os.environ,
        {
            "ROCKIELAB_API_URL": "https://platform.test",
            "ROCKIELAB_TENANT_TOKEN": "service-token",
            "ROCKIELAB_TENANT_ID": "t-aaaaaaaaaaaa",
            "ROCKIELAB_NOTEBOOK_ID": "notebook:lab-1",
        },
        clear=False,
    ):
        with mock.patch.object(submit_mod, "_http_request", fake_http_request):
            stdout = io.StringIO()
            args = submit_mod.parse_args(
                [
                    "--gpu-type", "A100_80GB",
                    "--gpu-count", "1",
                    "--script-file", str(script_file),
                    "--timeout", "3600",
                    "--term-sheet-json", str(term_sheet_file),
                    "--region", "us",
                    "--tier", "spot",
                    "--origin-skill", "experiment",
                ]
            )
            with contextlib.redirect_stdout(stdout):
                response = submit_mod.submit(args)

    assert response["job_id"] == "job-123"
    dashboard = captured["body"]["dashboard"]
    assert dashboard["notebook_id"] == "notebook:lab-1"
    assert dashboard["origin_skill"] == "experiment"
    assert dashboard["software"] == "pytorch"
    assert dashboard["monitoring_profile_id"] == "experiment.ml_baseline.v1"
    assert dashboard["monitoring_profile_snapshot"]["run_name"] == "train model submit test"
    assert dashboard["monitoring_profile_snapshot"]["cadence_bounds"]["cadence_seconds"] == 240
    assert dashboard["monitoring_profile_snapshot"]["budget_term_sheet"]["quote_id"] == "qts_test"
    assert captured["body"]["budget_cents"] == 27000
    assert captured["body"]["budget_term_sheet"]["quote_id"] == "qts_test"
    telemetry = [line for line in stdout.getvalue().splitlines() if '"event": "budget_term_sheet"' in line]
    assert telemetry


def test_submit_omits_dashboard_without_notebook_context():
    submit_mod = load_submit_mod()
    tmp_dir = Path(os.environ.get("TMPDIR", "/tmp"))
    script_file = tmp_dir / "legacy_submit_test.sh"
    script_file.write_text("#!/bin/bash\necho legacy submit\n", encoding="utf-8")
    term_sheet_file = tmp_dir / "legacy_submit_test.term-sheet.json"
    term_sheet_file.write_text(json.dumps(_approved_term_sheet()), encoding="utf-8")

    captured = {}

    def fake_http_request(method, url, *, headers, body=None):
        captured["body"] = json.loads(body.decode("utf-8"))
        return 200, b'{"job_id":"job-legacy"}'

    with mock.patch.dict(
        os.environ,
        {
            "ROCKIELAB_API_URL": "https://platform.test",
            "ROCKIELAB_TENANT_TOKEN": "service-token",
            "ROCKIELAB_TENANT_ID": "t-aaaaaaaaaaaa",
        },
        clear=True,
    ):
        with mock.patch.object(submit_mod, "_http_request", fake_http_request):
            args = submit_mod.parse_args(
                [
                    "--gpu-type", "A100_80GB",
                    "--gpu-count", "1",
                    "--script-file", str(script_file),
                    "--timeout", "3600",
                    "--term-sheet-json", str(term_sheet_file),
                    "--region", "us",
                    "--tier", "spot",
                ]
            )
            response = submit_mod.submit(args)

    assert response["job_id"] == "job-legacy"
    assert "dashboard" not in captured["body"]
    assert captured["body"]["budget_cents"] == 27000


def test_submit_402_emits_structured_credit_gate_event(tmp_path, capsys):
    submit_mod = load_submit_mod()
    script_file = tmp_path / "credit_gate_submit_test.sh"
    script_file.write_text("#!/bin/bash\necho blocked\n", encoding="utf-8")
    term_sheet_file = tmp_path / "credit_gate_submit_test.term-sheet.json"
    term_sheet_file.write_text(json.dumps(_approved_term_sheet()), encoding="utf-8")

    def fake_http_request(method, url, *, headers, body=None):
        return 402, json.dumps(
            {
                "detail": {
                    "error": {
                        "code": "insufficient_balance",
                        "message": "Tenant has no GPU credit.",
                        "balance_cents": 0,
                        "needed_cents": 27000,
                    }
                }
            }
        ).encode("utf-8")

    with rockie_env():
        with mock.patch.object(submit_mod, "_http_request", fake_http_request):
            args = submit_mod.parse_args(
                [
                    "--gpu-type", "A100_80GB",
                    "--gpu-count", "1",
                    "--script-file", str(script_file),
                    "--timeout", "3600",
                    "--term-sheet-json", str(term_sheet_file),
                    "--region", "us",
                    "--tier", "spot",
                ]
            )
            try:
                submit_mod.submit(args)
            except SystemExit as exc:
                assert exc.code == 4
            else:
                raise AssertionError("submit() should exit on HTTP 402")

    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines()]
    gate = next(event for event in events if event.get("event") == "credit_gate")
    assert gate["code"] == "insufficient_balance"
    assert gate["balance_cents"] == 0
    assert gate["needed_cents"] == 27000
    assert gate["checkout"]["skus"] == [
        "compute:20",
        "compute:50",
        "compute:200",
        "compute:1000",
    ]
    assert "Tenant has no GPU credit." in captured.err


def test_submit_402_redacts_signed_source_ref_from_output(tmp_path, capsys):
    submit_mod = load_submit_mod()
    script_file = tmp_path / "credit_gate_redaction_submit_test.sh"
    script_file.write_text("#!/bin/bash\necho blocked\n", encoding="utf-8")
    term_sheet_file = tmp_path / "credit_gate_redaction_submit_test.term-sheet.json"
    term_sheet_file.write_text(json.dumps(_approved_term_sheet()), encoding="utf-8")

    sensitive_ref = (
        "https://private-bucket.s3.amazonaws.com/paper.pdf?"
        "X-Amz-Credential=AKIASECRET&X-Amz-Signature=abc123&token=tok123"
    )

    def fake_http_request(method, url, *, headers, body=None):
        return 402, json.dumps(
            {
                "detail": {
                    "error": {
                        "code": "insufficient_balance",
                        "message": f"Insufficient credit for source_ref={sensitive_ref}",
                        "balance_cents": 0,
                        "needed_cents": 27000,
                    }
                }
            }
        ).encode("utf-8")

    with rockie_env():
        with mock.patch.object(submit_mod, "_http_request", fake_http_request):
            args = submit_mod.parse_args(
                [
                    "--gpu-type", "A100_80GB",
                    "--gpu-count", "1",
                    "--script-file", str(script_file),
                    "--timeout", "3600",
                    "--term-sheet-json", str(term_sheet_file),
                    "--region", "us",
                    "--tier", "spot",
                ]
            )
            try:
                submit_mod.submit(args)
            except SystemExit as exc:
                assert exc.code == 4
            else:
                raise AssertionError("submit() should exit on HTTP 402")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "abc123" not in combined
    assert "tok123" not in combined
    assert "AKIASECRET" not in combined
    assert "source_ref=" in combined
    assert "[redacted]" in combined


def test_submit_non_402_error_redacts_platform_response(tmp_path, capsys):
    submit_mod = load_submit_mod()
    script_file = tmp_path / "bad_request_redaction_submit_test.sh"
    script_file.write_text("#!/bin/bash\necho blocked\n", encoding="utf-8")
    term_sheet_file = tmp_path / "bad_request_redaction_submit_test.term-sheet.json"
    term_sheet_file.write_text(json.dumps(_approved_term_sheet()), encoding="utf-8")

    def fake_http_request(method, url, *, headers, body=None):
        return 400, json.dumps(
            {
                "error": {
                    "message": (
                        "invalid source_ref=https://example.test/repo.git?"
                        "X-Amz-Signature=abc123&token=tok123"
                    )
                }
            }
        ).encode("utf-8")

    with rockie_env():
        with mock.patch.object(submit_mod, "_http_request", fake_http_request):
            args = submit_mod.parse_args(
                [
                    "--gpu-type", "A100_80GB",
                    "--gpu-count", "1",
                    "--script-file", str(script_file),
                    "--timeout", "3600",
                    "--term-sheet-json", str(term_sheet_file),
                    "--region", "us",
                    "--tier", "spot",
                ]
            )
            try:
                submit_mod.submit(args)
            except SystemExit as exc:
                assert exc.code == 5
            else:
                raise AssertionError("submit() should exit on HTTP 400")

    captured = capsys.readouterr()
    assert "abc123" not in captured.err
    assert "tok123" not in captured.err
    assert "[redacted]" in captured.err


def test_get_job_error_redacts_platform_response(capsys):
    submit_mod = load_submit_mod()

    def fake_http_request(method, url, *, headers, body=None):
        return 500, json.dumps(
            {
                "error": {
                    "message": (
                        "failed log source_ref=https://example.test/paper.pdf?"
                        "X-Amz-Credential=cred456&api_key=key456"
                    )
                }
            }
        ).encode("utf-8")

    with rockie_env():
        with mock.patch.object(submit_mod, "_http_request", fake_http_request):
            try:
                submit_mod.get_job("job-redact")
            except SystemExit as exc:
                assert exc.code == 7
            else:
                raise AssertionError("get_job() should exit on HTTP 500")

    captured = capsys.readouterr()
    assert "cred456" not in captured.err
    assert "key456" not in captured.err
    assert "[redacted]" in captured.err


def test_safe_summary_redacts_signed_url_fields_and_bearer_logs():
    submit_mod = load_submit_mod()
    summary = submit_mod._safe_summary(
        "source_ref=https://example.test/repo.git?X-Amz-Signature=abc123"
        "&X-Amz-Credential=cred456&token=tok789 "
        "Authorization: Bearer bearer-secret "
        "azure=https://b.blob.core.windows.net/c?sig=azsig123&se=2026&sp=r"
    )

    assert "abc123" not in summary
    assert "cred456" not in summary
    assert "tok789" not in summary
    assert "bearer-secret" not in summary
    assert "azsig123" not in summary
    assert summary.count("[redacted]") >= 7


def test_submit_can_infer_physics_profile_from_script():
    submit_mod = load_submit_mod()
    tmp_dir = Path(os.environ.get("TMPDIR", "/tmp"))
    script_file = tmp_dir / "qe_run_submit_test.sh"
    script_file.write_text("#!/bin/bash\npw.x -input silicon.in > silicon.out\n", encoding="utf-8")
    term_sheet_file = tmp_dir / "qe_run_submit_test.term-sheet.json"
    term_sheet_file.write_text(json.dumps(_approved_term_sheet()), encoding="utf-8")

    captured = {}

    def fake_http_request(method, url, *, headers, body=None):
        captured["body"] = json.loads(body.decode("utf-8"))
        return 200, b'{"job_id":"job-456"}'

    with mock.patch.dict(
        os.environ,
        {
            "ROCKIELAB_API_URL": "https://platform.test",
            "ROCKIELAB_TENANT_TOKEN": "service-token",
            "ROCKIELAB_TENANT_ID": "t-aaaaaaaaaaaa",
            "ROCKIELAB_NOTEBOOK_ID": "notebook:physics-1",
        },
        clear=False,
    ):
        with mock.patch.object(submit_mod, "_http_request", fake_http_request):
            args = submit_mod.parse_args(
                [
                    "--gpu-type", "A100_80GB",
                    "--gpu-count", "1",
                    "--script-file", str(script_file),
                    "--timeout", "3600",
                    "--term-sheet-json", str(term_sheet_file),
                    "--region", "us",
                    "--tier", "spot",
                ]
            )
            submit_mod.submit(args)

    dashboard = captured["body"]["dashboard"]
    assert dashboard["software"] == "quantum-espresso"
    assert dashboard["monitoring_profile_id"] == "physics.electronic_structure.v1"


def test_submit_refuses_missing_term_sheet_by_default():
    submit_mod = load_submit_mod()
    tmp_dir = Path(os.environ.get("TMPDIR", "/tmp"))
    script_file = tmp_dir / "missing_term_sheet_submit_test.sh"
    script_file.write_text("#!/bin/bash\necho blocked\n", encoding="utf-8")

    with rockie_env():
        args = submit_mod.parse_args(
            [
                "--gpu-type", "A100_80GB",
                "--gpu-count", "1",
                "--script-file", str(script_file),
                "--timeout", "3600",
            ]
        )
        try:
            submit_mod.submit(args)
        except SystemExit as exc:
            assert exc.code == 9
        else:
            raise AssertionError("submit() should have refused missing --term-sheet-json")


def test_submit_refuses_budget_below_estimate():
    submit_mod = load_submit_mod()
    tmp_dir = Path(os.environ.get("TMPDIR", "/tmp"))
    script_file = tmp_dir / "below_estimate_submit_test.sh"
    script_file.write_text("#!/bin/bash\necho blocked\n", encoding="utf-8")
    term_sheet = _approved_term_sheet()
    term_sheet["user_budget_cents"] = 15000
    term_sheet_file = tmp_dir / "below_estimate_submit_test.term-sheet.json"
    term_sheet_file.write_text(json.dumps(term_sheet), encoding="utf-8")

    with rockie_env():
        args = submit_mod.parse_args(
            [
                "--gpu-type", "A100_80GB",
                "--gpu-count", "1",
                "--script-file", str(script_file),
                "--timeout", "3600",
                "--term-sheet-json", str(term_sheet_file),
                "--region", "us",
                "--tier", "spot",
            ]
        )
        try:
            submit_mod.submit(args)
        except SystemExit as exc:
            assert exc.code == 11
        else:
            raise AssertionError("submit() should have refused a below-estimate budget")


def test_submit_refuses_term_sheet_gpu_type_mismatch(tmp_path):
    submit_mod = load_submit_mod()
    term_sheet = _approved_term_sheet()
    term_sheet["compute"]["gpu_type"] = "H100_SXM"

    _assert_submit_exits(
        submit_mod,
        tmp_path,
        term_sheet,
        ["--gpu-type", "A100_80GB", "--gpu-count", "1", "--timeout", "3600"],
        expected_code=10,
    )


def test_submit_refuses_term_sheet_gpu_count_mismatch(tmp_path):
    submit_mod = load_submit_mod()
    term_sheet = _approved_term_sheet()
    term_sheet["compute"]["gpu_count"] = 2

    _assert_submit_exits(
        submit_mod,
        tmp_path,
        term_sheet,
        ["--gpu-type", "A100_80GB", "--gpu-count", "1", "--timeout", "3600"],
        expected_code=10,
    )


def test_submit_refuses_term_sheet_timeout_mismatch(tmp_path):
    submit_mod = load_submit_mod()
    term_sheet = _approved_term_sheet()

    _assert_submit_exits(
        submit_mod,
        tmp_path,
        term_sheet,
        ["--gpu-type", "A100_80GB", "--gpu-count", "1", "--timeout", "7200"],
        expected_code=10,
    )


def test_submit_refuses_term_sheet_region_mismatch_when_requested(tmp_path):
    submit_mod = load_submit_mod()
    term_sheet = _approved_term_sheet()

    _assert_submit_exits(
        submit_mod,
        tmp_path,
        term_sheet,
        ["--gpu-type", "A100_80GB", "--gpu-count", "1", "--timeout", "3600", "--region", "eu"],
        expected_code=10,
    )


def test_submit_refuses_unavailable_approved_artifact(tmp_path):
    submit_mod = load_submit_mod()
    term_sheet = _approved_term_sheet()
    term_sheet["availability"] = "unavailable"
    term_sheet["status_code"] = "out_of_stock"

    _assert_submit_exits(
        submit_mod,
        tmp_path,
        term_sheet,
        ["--gpu-type", "A100_80GB", "--gpu-count", "1", "--timeout", "3600"],
        expected_code=10,
    )


def test_submit_refuses_missing_user_budget_cents(tmp_path):
    submit_mod = load_submit_mod()
    term_sheet = _approved_term_sheet()
    term_sheet.pop("user_budget_cents")

    _assert_submit_exits(
        submit_mod,
        tmp_path,
        term_sheet,
        [
            "--gpu-type", "A100_80GB",
            "--gpu-count", "1",
            "--timeout", "3600",
            "--region", "us",
            "--tier", "spot",
        ],
        expected_code=11,
    )


def test_submit_refuses_budget_override_mismatch_or_increase(tmp_path):
    submit_mod = load_submit_mod()
    term_sheet = _approved_term_sheet()

    _assert_submit_exits(
        submit_mod,
        tmp_path,
        term_sheet,
        [
            "--gpu-type", "A100_80GB",
            "--gpu-count", "1",
            "--timeout", "3600",
            "--budget-cents", "30000",
            "--region", "us",
            "--tier", "spot",
        ],
        expected_code=11,
    )


def test_submit_refuses_omitted_approved_region(tmp_path):
    submit_mod = load_submit_mod()
    term_sheet = _approved_term_sheet()

    _assert_submit_exits(
        submit_mod,
        tmp_path,
        term_sheet,
        ["--gpu-type", "A100_80GB", "--gpu-count", "1", "--timeout", "3600", "--tier", "spot"],
        expected_code=10,
    )


def test_submit_refuses_omitted_approved_tier(tmp_path):
    submit_mod = load_submit_mod()
    term_sheet = _approved_term_sheet()

    _assert_submit_exits(
        submit_mod,
        tmp_path,
        term_sheet,
        ["--gpu-type", "A100_80GB", "--gpu-count", "1", "--timeout", "3600", "--region", "us"],
        expected_code=10,
    )


def test_submit_refuses_term_sheet_missing_approved_region(tmp_path):
    submit_mod = load_submit_mod()
    term_sheet = _approved_term_sheet()
    term_sheet["compute"].pop("region")

    _assert_submit_exits(
        submit_mod,
        tmp_path,
        term_sheet,
        [
            "--gpu-type", "A100_80GB",
            "--gpu-count", "1",
            "--timeout", "3600",
            "--region", "us",
            "--tier", "spot",
        ],
        expected_code=10,
    )


def test_submit_refuses_term_sheet_missing_approved_tier(tmp_path):
    submit_mod = load_submit_mod()
    term_sheet = _approved_term_sheet()
    term_sheet["compute"].pop("tier")

    _assert_submit_exits(
        submit_mod,
        tmp_path,
        term_sheet,
        [
            "--gpu-type", "A100_80GB",
            "--gpu-count", "1",
            "--timeout", "3600",
            "--region", "us",
            "--tier", "spot",
        ],
        expected_code=10,
    )


def test_submit_refuses_term_sheet_tier_mismatch(tmp_path):
    submit_mod = load_submit_mod()
    term_sheet = _approved_term_sheet()

    _assert_submit_exits(
        submit_mod,
        tmp_path,
        term_sheet,
        [
            "--gpu-type", "A100_80GB",
            "--gpu-count", "1",
            "--timeout", "3600",
            "--region", "us",
            "--tier", "reserved",
        ],
        expected_code=10,
    )


def test_submit_accepts_matching_approved_tier(tmp_path):
    submit_mod = load_submit_mod()
    script_file = tmp_path / "matching_tier_submit_test.sh"
    script_file.write_text("#!/bin/bash\necho submit\n", encoding="utf-8")
    term_sheet_file = tmp_path / "matching_tier_submit_test.term-sheet.json"
    term_sheet_file.write_text(json.dumps(_approved_term_sheet()), encoding="utf-8")
    captured = {}

    def fake_http_request(method, url, *, headers, body=None):
        captured["body"] = json.loads(body.decode("utf-8"))
        return 200, b'{"job_id":"job-tier"}'

    with rockie_env():
        with mock.patch.object(submit_mod, "_http_request", fake_http_request):
            args = submit_mod.parse_args(
                [
                    "--gpu-type", "A100_80GB",
                    "--gpu-count", "1",
                    "--script-file", str(script_file),
                    "--timeout", "3600",
                    "--term-sheet-json", str(term_sheet_file),
                    "--region", "us",
                    "--tier", "spot",
                ]
            )
            response = submit_mod.submit(args)

    assert response["job_id"] == "job-tier"
    assert captured["body"]["spec"]["tier"] == "spot"


def test_main_passes_dashboard_note_id_to_poll(tmp_path):
    submit_mod = load_submit_mod()
    script_file = tmp_path / "dashboard_note_submit_test.sh"
    script_file.write_text("#!/bin/bash\necho submit\n", encoding="utf-8")
    term_sheet_file = tmp_path / "dashboard_note_submit_test.term-sheet.json"
    term_sheet_file.write_text(json.dumps(_approved_term_sheet()), encoding="utf-8")
    captured = {}

    with rockie_env():
        with mock.patch.object(
            submit_mod,
            "submit",
            return_value={"job_id": "job-main", "estimated_cost_cents": 123, "dashboard_note_id": "note:dash/1"},
        ):
            with mock.patch.object(submit_mod, "poll", return_value=0) as poll:
                code = submit_mod.main(
                    [
                        "--gpu-type", "A100_80GB",
                        "--gpu-count", "1",
                        "--script-file", str(script_file),
                        "--timeout", "3600",
                        "--term-sheet-json", str(term_sheet_file),
                    ]
                )
                captured["poll_kwargs"] = poll.call_args.kwargs

    assert code == 0
    assert captured["poll_kwargs"]["dashboard_note_id"] == "note:dash/1"


def test_append_dashboard_url_encodes_note_id_and_uses_writer():
    submit_mod = load_submit_mod()
    captured = {}

    def fake_http_request(method, url, *, headers, body=None):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(body.decode("utf-8"))
        return 200, b'{"ok":true}'

    with rockie_env():
        with mock.patch.object(submit_mod, "_dashboard_http_request", fake_http_request):
            response = submit_mod.append_dashboard(
                "note:dash/1",
                job_id="job-abc",
                api_url="https://platform.test/",
                tenant_id="t-override000",
                status_patch={"job_state": "RUNNING"},
                events=[{"kind": "state", "summary": "Job state is RUNNING."}],
                verdicts=[{"verdict": "continue", "reason_code": "poll_heartbeat", "summary": "Job is active."}],
            )

    assert response == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["url"] == "https://platform.test/api/notes/note%3Adash%2F1/dashboard/append"
    assert captured["headers"]["X-Tenant-Token"] == "service-token"
    assert captured["headers"]["X-Tenant-Id"] == "t-override000"
    assert captured["body"]["writer"] == {"job_backend": "gpu_job", "job_id": "job-abc"}
    assert captured["body"]["status_patch"]["job_state"] == "RUNNING"


def test_append_dashboard_network_error_warns_and_returns_none(capsys):
    submit_mod = load_submit_mod()

    def fake_http_request(method, url, *, headers, body=None):
        return 0, b'{"error":"network_error: dns failed"}'

    with rockie_env():
        with mock.patch.object(submit_mod, "_dashboard_http_request", fake_http_request):
            response = submit_mod.append_dashboard(
                "note:dash/1",
                job_id="job-abc",
                api_url="https://platform.test",
                tenant_id="t-aaaaaaaaaaaa",
            )

    assert response is None
    assert "dashboard append failed (0)" in capsys.readouterr().err


def test_safe_summary_redacts_common_secret_shapes():
    submit_mod = load_submit_mod()
    summary = submit_mod._safe_summary(
        'Authorization: Bearer bearer-secret '
        "api_key: api-secret "
        '{"token": "json-secret"} '
        "OPENAI_API_KEY=env-secret "
        "raw sk-test-secret"
    )

    assert "bearer-secret" not in summary
    assert "api-secret" not in summary
    assert "json-secret" not in summary
    assert "env-secret" not in summary
    assert "sk-test-secret" not in summary
    assert summary.count(submit_mod.SECRET_REDACTION) == 5


def test_safe_summary_truncates_long_logs_after_redaction():
    submit_mod = load_submit_mod()
    summary = submit_mod._safe_summary("token=secret " + ("a" * 700), limit=80)

    assert len(summary) == 80
    assert summary.endswith("...")
    assert "secret" not in summary
    assert submit_mod.SECRET_REDACTION in summary


def test_active_poll_appends_heartbeat_state_log_and_unavailable_metric():
    submit_mod = load_submit_mod()
    appended = []
    finalized = []
    jobs = iter(
        [
            {
                "id": "job-poll",
                "state": "RUNNING",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "cost_so_far_cents": None,
                "last_log_line": "token=sk-test-secret starting work",
            },
            {
                "id": "job-poll",
                "state": "DONE",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "cost_actual_cents": 123,
                "cost_so_far_cents": 123,
                "last_log_line": "finished",
                "finished_at": 123.0,
            },
        ]
    )

    with mock.patch.object(submit_mod, "get_job", side_effect=lambda *args, **kwargs: next(jobs)):
        with mock.patch.object(submit_mod, "append_dashboard", side_effect=lambda *args, **kwargs: appended.append((args, kwargs))):
            with mock.patch.object(submit_mod, "finalize_dashboard", side_effect=lambda *args, **kwargs: finalized.append((args, kwargs))):
                with mock.patch.object(submit_mod.time, "sleep", return_value=None):
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        exit_code = submit_mod.poll(
                            "job-poll",
                            quiet=True,
                            api_url="https://platform.test",
                            tenant_id="t-aaaaaaaaaaaa",
                            dashboard_note_id="note:dash-1",
                        )

    assert exit_code == 0
    assert len(appended) == 1
    assert appended[0][0] == ("note:dash-1",)
    append_kwargs = appended[0][1]
    assert append_kwargs["job_id"] == "job-poll"
    assert append_kwargs["status_patch"]["job_state"] == "RUNNING"
    assert append_kwargs["status_patch"]["monitor_owner"] == "experiment"
    assert append_kwargs["status_patch"]["monitor_target"] == {"kind": "job", "id": "job-poll"}
    assert append_kwargs["status_patch"]["state"] == "running"
    assert append_kwargs["status_patch"]["utilization"]["state"] == "unavailable"
    assert append_kwargs["status_patch"]["utilization"]["reason_code"] == "telemetry_not_exposed"
    assert append_kwargs["status_patch"]["spend"]["state"] == "unavailable"
    assert append_kwargs["status_patch"]["spend"]["reason_code"] == "spend_not_exposed"
    assert "cost_so_far_cents" not in append_kwargs["status_patch"]
    assert append_kwargs["metrics"] == [
        {
            "name": "gpu_utilization",
            "state": "unavailable",
            "reason_code": "telemetry_not_exposed",
            "summary": "GPU utilization telemetry is unavailable from the job status API.",
        }
    ]
    assert append_kwargs["events"][0]["kind"] == "state"
    assert append_kwargs["events"][1]["kind"] == "log"
    assert "sk-test-secret" not in append_kwargs["events"][1]["summary"]
    assert append_kwargs["verdicts"][0]["verdict"] == "continue"
    assert len(finalized) == 1
    assert finalized[0][1]["outcome"] == "success"
    assert finalized[0][1]["status_patch"]["monitor_target"] == {"kind": "job", "id": "job-poll"}
    assert finalized[0][1]["status_patch"]["spend"]["state"] == "live"
    assert finalized[0][1]["status_patch"]["spend"]["reason_code"] is None


def test_active_poll_retries_heartbeat_after_failed_append():
    submit_mod = load_submit_mod()
    appended = []
    jobs = iter(
        [
            {
                "id": "job-retry",
                "state": "RUNNING",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "last_log_line": None,
            },
            {
                "id": "job-retry",
                "state": "RUNNING",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "last_log_line": None,
            },
            {
                "id": "job-retry",
                "state": "DONE",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "cost_actual_cents": 50,
                "last_log_line": "finished",
            },
        ]
    )
    append_results = iter([None, {"ok": True}])

    def fake_append(*args, **kwargs):
        appended.append((args, kwargs))
        return next(append_results)

    with mock.patch.object(submit_mod, "get_job", side_effect=lambda *args, **kwargs: next(jobs)):
        with mock.patch.object(submit_mod, "append_dashboard", side_effect=fake_append):
            with mock.patch.object(submit_mod, "finalize_dashboard", return_value={"ok": True}):
                with mock.patch.object(submit_mod.time, "sleep", return_value=None):
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        exit_code = submit_mod.poll(
                            "job-retry",
                            quiet=True,
                            dashboard_note_id="note:retry",
                        )

    assert exit_code == 0
    assert len(appended) == 2
    assert appended[0][1]["metrics"] == submit_mod._unavailable_telemetry_metrics()
    assert appended[1][1]["metrics"] == submit_mod._unavailable_telemetry_metrics()
    assert appended[1][1]["verdicts"][0]["reason_code"] == "poll_heartbeat"


def test_active_poll_appends_finetune_progress_metrics():
    submit_mod = load_submit_mod()
    appended = []
    jobs = iter(
        [
            {
                "id": "job-finetune",
                "state": "RUNNING",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "last_log_line": 'FINETUNE_PROGRESS {"step":12,"loss":0.42}',
            },
            {
                "id": "job-finetune",
                "state": "DONE",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "cost_actual_cents": 100,
                "last_log_line": "JOB_DONE",
            },
        ]
    )

    with mock.patch.object(submit_mod, "get_job", side_effect=lambda *args, **kwargs: next(jobs)):
        with mock.patch.object(submit_mod, "append_dashboard", side_effect=lambda *args, **kwargs: appended.append((args, kwargs))):
            with mock.patch.object(submit_mod, "finalize_dashboard", return_value={"ok": True}):
                with mock.patch.object(submit_mod.time, "sleep", return_value=None):
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        exit_code = submit_mod.poll(
                            "job-finetune",
                            quiet=True,
                            dashboard_note_id="note:finetune",
                        )

    assert exit_code == 0
    assert len(appended) == 1
    assert {"name": "training.step", "value": 12} in appended[0][1]["metrics"]
    assert {"name": "training.loss", "value": 0.42} in appended[0][1]["metrics"]
    assert "[finetune]" not in stdout.getvalue()


def test_active_poll_prints_finetune_progress_when_not_quiet():
    submit_mod = load_submit_mod()
    jobs = iter(
        [
            {
                "id": "job-finetune-stdout",
                "state": "RUNNING",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "last_log_line": 'FINETUNE_PROGRESS {"step":12,"loss":0.42}',
            },
            {
                "id": "job-finetune-stdout",
                "state": "DONE",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "cost_actual_cents": 100,
                "last_log_line": "JOB_DONE",
            },
        ]
    )

    with mock.patch.object(submit_mod, "get_job", side_effect=lambda *args, **kwargs: next(jobs)):
        with mock.patch.object(submit_mod.time, "sleep", return_value=None):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = submit_mod.poll("job-finetune-stdout", quiet=False)

    assert exit_code == 0
    output = stdout.getvalue()
    assert "[log] FINETUNE_PROGRESS" in output
    assert "[finetune] step=12 loss=0.42" in output


def test_finetune_progress_metrics_ignore_non_finite_numbers_and_bools():
    submit_mod = load_submit_mod()

    assert submit_mod._finetune_progress_metrics(
        'FINETUNE_PROGRESS {"step": NaN, "loss": 0.42}'
    ) == [{"name": "training.loss", "value": 0.42}]
    assert submit_mod._finetune_progress_metrics(
        'FINETUNE_PROGRESS {"step": 12, "loss": Infinity}'
    ) == [{"name": "training.step", "value": 12}]
    assert submit_mod._finetune_progress_metrics(
        'FINETUNE_PROGRESS {"step": -Infinity, "loss": true}'
    ) == []


def test_active_poll_ignores_malformed_finetune_progress_lines():
    submit_mod = load_submit_mod()
    appended = []
    jobs = iter(
        [
            {
                "id": "job-finetune-bad",
                "state": "RUNNING",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "last_log_line": "FINETUNE_PROGRESS {not-json",
            },
            {
                "id": "job-finetune-bad",
                "state": "DONE",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "cost_actual_cents": 100,
                "last_log_line": "JOB_DONE",
            },
        ]
    )

    with mock.patch.object(submit_mod, "get_job", side_effect=lambda *args, **kwargs: next(jobs)):
        with mock.patch.object(submit_mod, "append_dashboard", side_effect=lambda *args, **kwargs: appended.append((args, kwargs))):
            with mock.patch.object(submit_mod, "finalize_dashboard", return_value={"ok": True}):
                with mock.patch.object(submit_mod.time, "sleep", return_value=None):
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        exit_code = submit_mod.poll(
                            "job-finetune-bad",
                            quiet=True,
                            dashboard_note_id="note:finetune-bad",
                        )

    assert exit_code == 0
    assert len(appended) == 1
    assert not any(
        metric.get("name") in {"training.step", "training.loss"}
        for metric in appended[0][1]["metrics"]
    )
    assert "[finetune]" not in stdout.getvalue()


def test_terminal_poll_reports_unavailable_cost_when_absent():
    submit_mod = load_submit_mod()
    job = {
        "id": "job-no-cost",
        "state": "DONE",
        "gpu_count": 1,
        "gpu_type": "A100_80GB",
        "last_log_line": "done",
    }

    with mock.patch.object(submit_mod, "get_job", return_value=job):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = submit_mod.poll("job-no-cost", quiet=True)

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Cost: unavailable" in output
    assert "$0.00" not in output


def test_terminal_poll_with_dashboard_tolerates_unavailable_cost_sentinel():
    submit_mod = load_submit_mod()
    finalized = []
    job = {
        "id": "job-unavailable-cost",
        "state": "FAILED",
        "gpu_count": 1,
        "gpu_type": "A100_80GB",
        "cost_actual_cents": "unavailable",
        "last_log_line": "failed",
        "compute_error": "process exited 1",
    }

    with mock.patch.object(submit_mod, "get_job", return_value=job):
        with mock.patch.object(submit_mod, "append_dashboard") as append:
            with mock.patch.object(submit_mod, "finalize_dashboard", side_effect=lambda *args, **kwargs: finalized.append((args, kwargs))):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    exit_code = submit_mod.poll(
                        "job-unavailable-cost",
                        quiet=True,
                        dashboard_note_id="note:unavailable-cost",
                    )

    assert exit_code == 1
    append.assert_not_called()
    assert len(finalized) == 1
    assert finalized[0][1]["outcome"] == "failed"
    assert "Cost reported: unavailable." in finalized[0][1]["summary"]
    assert "Cost: unavailable" in stdout.getvalue()


def test_poll_finalizes_failed_and_cancelled_once():
    submit_mod = load_submit_mod()

    def run_terminal(state):
        finalized = []
        job = {
            "id": f"job-{state.lower()}",
            "state": state,
            "gpu_count": 1,
            "gpu_type": "A100_80GB",
            "cost_so_far_cents": 0,
            "last_log_line": None,
            "compute_error": "timeout reached" if state == "FAILED" else None,
        }
        with mock.patch.object(submit_mod, "get_job", return_value=job):
            with mock.patch.object(submit_mod, "append_dashboard") as append:
                with mock.patch.object(submit_mod, "finalize_dashboard", side_effect=lambda *args, **kwargs: finalized.append((args, kwargs))):
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        exit_code = submit_mod.poll(
                            job["id"],
                            quiet=True,
                            dashboard_note_id=f"note:{state.lower()}",
                        )
        append.assert_not_called()
        return exit_code, finalized

    failed_code, failed_finalized = run_terminal("FAILED")
    cancelled_code, cancelled_finalized = run_terminal("CANCELLED")

    assert failed_code == 1
    assert len(failed_finalized) == 1
    assert failed_finalized[0][1]["outcome"] == "failed"
    assert failed_finalized[0][1]["termination_outcome"] == "pending_cleanup"
    assert failed_finalized[0][1]["verdict"]["reason_code"] == "timeout_observed"
    assert cancelled_code == 130
    assert len(cancelled_finalized) == 1
    assert cancelled_finalized[0][1]["outcome"] == "cancelled"


def test_poll_without_dashboard_note_id_performs_no_dashboard_mutations():
    submit_mod = load_submit_mod()
    job = {
        "id": "job-no-dashboard",
        "state": "DONE",
        "gpu_count": 1,
        "gpu_type": "A100_80GB",
        "cost_actual_cents": 321,
        "last_log_line": "done",
    }
    with mock.patch.object(submit_mod, "get_job", return_value=job):
        with mock.patch.object(submit_mod, "append_dashboard") as append:
            with mock.patch.object(submit_mod, "finalize_dashboard") as finalize:
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    exit_code = submit_mod.poll("job-no-dashboard", quiet=True)

    assert exit_code == 0
    append.assert_not_called()
    finalize.assert_not_called()


def test_terminal_cost_actual_only_finalize_uses_settled_spend():
    submit_mod = load_submit_mod()
    finalizations = []
    job = {
        "id": "job-settled",
        "state": "DONE",
        "gpu_count": 1,
        "gpu_type": "A100_80GB",
        "cost_actual_cents": 321,
        "last_log_line": "done",
    }

    with mock.patch.object(submit_mod, "get_job", return_value=job):
        with mock.patch.object(submit_mod, "append_dashboard") as append:
            with mock.patch.object(
                submit_mod,
                "finalize_dashboard",
                side_effect=lambda *args, **kwargs: finalizations.append((args, kwargs)),
            ):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    exit_code = submit_mod.poll(
                        "job-settled",
                        quiet=True,
                        dashboard_note_id="note:settled",
                    )

    assert exit_code == 0
    append.assert_not_called()
    assert len(finalizations) == 1
    assert finalizations[0][1]["status_patch"]["spend"]["state"] == "settled"
    assert finalizations[0][1]["status_patch"]["spend"]["reason_code"] is None
    assert finalizations[0][1]["status_patch"]["spend"]["reason"] is not None


def test_dashboard_mutation_failures_warn_without_masking_job_exit(capsys):
    submit_mod = load_submit_mod()
    requests = []
    jobs = iter(
        [
            {
                "id": "job-fail",
                "state": "RUNNING",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "last_log_line": None,
            },
            {
                "id": "job-fail",
                "state": "FAILED",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "cost_so_far_cents": 222,
                "last_log_line": "failed",
                "compute_error": "preempted by capacity event",
            },
        ]
    )

    def fake_http_request(method, url, *, headers, body=None):
        requests.append((method, url, json.loads(body.decode("utf-8"))))
        return 500, b'{"error":"dashboard unavailable"}'

    with rockie_env():
        with mock.patch.object(submit_mod, "get_job", side_effect=lambda *args, **kwargs: next(jobs)):
            with mock.patch.object(submit_mod, "_dashboard_http_request", fake_http_request):
                with mock.patch.object(submit_mod.time, "sleep", return_value=None):
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        exit_code = submit_mod.poll(
                            "job-fail",
                            quiet=True,
                            api_url="https://platform.test",
                            tenant_id="t-aaaaaaaaaaaa",
                            dashboard_note_id="note:fail",
                        )

    assert exit_code == 1
    assert len(requests) == 2
    assert requests[0][1].endswith("/dashboard/append")
    assert requests[1][1].endswith("/dashboard/finalize")
    assert requests[1][2]["outcome"] == "failed"
    assert requests[1][2]["termination_outcome"] == "pending_cleanup"
    assert requests[1][2]["verdict"]["reason_code"] == "preemption_observed"
    stderr = capsys.readouterr().err
    assert "dashboard append failed (500)" in stderr
    assert "dashboard finalize failed (500)" in stderr


def test_dashboard_request_exception_does_not_mask_done_exit(capsys):
    submit_mod = load_submit_mod()
    jobs = iter(
        [
            {
                "id": "job-done",
                "state": "RUNNING",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "last_log_line": "starting",
            },
            {
                "id": "job-done",
                "state": "DONE",
                "gpu_count": 1,
                "gpu_type": "A100_80GB",
                "cost_actual_cents": 123,
                "last_log_line": "done",
            },
        ]
    )

    def broken_http_request(method, url, *, headers, body=None):
        raise RuntimeError("dashboard transport exploded")

    with rockie_env():
        with mock.patch.object(submit_mod, "get_job", side_effect=lambda *args, **kwargs: next(jobs)):
            with mock.patch.object(submit_mod, "_dashboard_http_request", broken_http_request):
                with mock.patch.object(submit_mod.time, "sleep", return_value=None):
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        exit_code = submit_mod.poll(
                            "job-done",
                            quiet=True,
                            api_url="https://platform.test",
                            tenant_id="t-aaaaaaaaaaaa",
                            dashboard_note_id="note:runtime-error",
                        )

    assert exit_code == 0
    stderr = capsys.readouterr().err
    assert "dashboard append failed (0)" in stderr
    assert "dashboard finalize failed (0)" in stderr
    assert "dashboard transport exploded" in stderr


def test_dashboard_lower_transport_exception_does_not_mask_cancelled_exit(capsys):
    submit_mod = load_submit_mod()
    job = {
        "id": "job-cancelled",
        "state": "CANCELLED",
        "gpu_count": 1,
        "gpu_type": "A100_80GB",
        "cost_so_far_cents": 456,
        "last_log_line": "cancelled",
    }

    with rockie_env():
        with mock.patch.object(submit_mod, "get_job", return_value=job):
            with mock.patch.object(submit_mod.urllib.request, "urlopen", side_effect=TimeoutError("read timed out")):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    exit_code = submit_mod.poll(
                        "job-cancelled",
                        quiet=True,
                        api_url="https://platform.test",
                        tenant_id="t-aaaaaaaaaaaa",
                        dashboard_note_id="note:transport-error",
                    )

    assert exit_code == 130
    stderr = capsys.readouterr().err
    assert "dashboard finalize failed (0)" in stderr
    assert "transport_error: read timed out" in stderr


def _assert_submit_exits(submit_mod, tmp_path, term_sheet, submit_args, *, expected_code):
    script_file = tmp_path / "blocked_submit_test.sh"
    script_file.write_text("#!/bin/bash\necho blocked\n", encoding="utf-8")
    term_sheet_file = tmp_path / "blocked_submit_test.term-sheet.json"
    term_sheet_file.write_text(json.dumps(term_sheet), encoding="utf-8")

    with rockie_env():
        args = submit_mod.parse_args(
            submit_args
            + [
                "--script-file", str(script_file),
                "--term-sheet-json", str(term_sheet_file),
            ]
        )
        try:
            submit_mod.submit(args)
        except SystemExit as exc:
            assert exc.code == expected_code
        else:
            raise AssertionError("submit() should have refused the invalid term sheet")


def _approved_term_sheet():
    return {
        "quote_id": "qts_test",
        "job_shape": "lora_finetune",
        "availability": "available",
        "status_code": "available",
        "status_reason": "Matching capacity is available.",
        "quote_source": "fixture",
        "generated_at": "2026-06-05T00:00:00+00:00",
        "compute": {
            "display_provider": "Rockie GPU",
            "gpu_type": "A100_80GB",
            "gpu_count": 1,
            "region": "us",
            "tier": "spot",
        },
        "stages": [
            {"name": "bootstrap", "cents": 900},
            {"name": "data_prep", "cents": 1800},
            {"name": "train_or_run", "cents": 13500},
            {"name": "eval", "cents": 1260},
            {"name": "artifact_storage", "cents": 540},
        ],
        "estimate_cents": 18000,
        "recommended_budget_cents": 27000,
        "user_budget_cents": 27000,
        "wallclock_minutes": 60,
        "confidence_bucket": "high",
        "confidence_statement": "High confidence quote.",
        "decision": {
            "state": "approve",
            "approvable": True,
            "approved_for_submit": True,
        },
    }


if __name__ == "__main__":
    import inspect
    import tempfile
    from types import SimpleNamespace

    class _Capsys:
        def __init__(self, stdout: io.StringIO, stderr: io.StringIO) -> None:
            self._stdout = stdout
            self._stderr = stderr

        def readouterr(self):
            return SimpleNamespace(out=self._stdout.getvalue(), err=self._stderr.getvalue())

    tests = [
        obj
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    ]
    for test in sorted(tests, key=lambda fn: fn.__name__):
        kwargs = {}
        signature = inspect.signature(test)
        stdout = io.StringIO()
        stderr = io.StringIO()
        for name in signature.parameters:
            if name == "tmp_path":
                tempdir = tempfile.TemporaryDirectory()
                kwargs[name] = Path(tempdir.name)
            elif name == "capsys":
                kwargs[name] = _Capsys(stdout, stderr)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            test(**kwargs)
    print(f"OK - {len(tests)} experiment submit checks passed")
