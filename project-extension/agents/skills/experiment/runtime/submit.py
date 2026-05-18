#!/usr/bin/env python3
"""
/experiment skill — job submit + poll helper.

Submits a job to platform-context's ``POST /api/jobs/submit``, polls
``GET /api/jobs/{id}`` every 10 seconds, prints state changes + the
last log line, and exits with the job's exit code (0 for DONE, 1 for
FAILED/CANCELLED).

Reads ``ROCKIELAB_API_URL`` and ``ROCKIELAB_TENANT_TOKEN`` from env.
The tenant ID is read from ``ROCKIELAB_TENANT_ID`` (preferred) or
falls back to the literal string ``self`` if unset (the platform
treats this as the calling token's owning tenant).

Usage::

    submit.py --gpu-type A100_80GB --gpu-count 4 \\
              --script-file path.sh --timeout 14400

CLI surface (argparse):
- ``--gpu-type``    GPU type (A100_80GB | H100_SXM | H200 | B200). Required.
- ``--gpu-count``   Integer >= 1. Required.
- ``--script-file`` Path to the bash script body. Required.
- ``--timeout``     Job timeout in seconds. Required.
- ``--tenant-id``   Override env-driven tenant id.
- ``--region``      Optional RunPod region.
- ``--env``         Repeatable ``KEY=VALUE`` for the job env.
- ``--api-url``     Override ``ROCKIELAB_API_URL``.
- ``--no-poll``     Submit and exit; don't poll.
- ``--quiet``       Print only the final summary.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


TERMINAL_STATES = {"DONE", "FAILED", "CANCELLED"}


def _err(msg: str, *, exit_code: int = 2) -> None:
    sys.stderr.write(f"submit.py: error: {msg}\n")
    sys.exit(exit_code)


def _http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: Optional[bytes] = None,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        _err(f"Network error talking to {url}: {e.reason}", exit_code=3)
        return 0, b""  # unreachable


def _api_url() -> str:
    raw = os.environ.get("ROCKIELAB_API_URL", "").rstrip("/")
    if not raw:
        _err("ROCKIELAB_API_URL is not set in the environment.")
    return raw


def _tenant_token() -> str:
    raw = os.environ.get("ROCKIELAB_TENANT_TOKEN", "")
    if not raw:
        _err("ROCKIELAB_TENANT_TOKEN is not set in the environment.")
    return raw


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Tenant-Token": _tenant_token(),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="submit.py",
        description="Submit and tail a Pebble ML GPU job.",
    )
    p.add_argument("--gpu-type", required=True,
                   choices=["A100_80GB", "H100_SXM", "H200", "B200"])
    p.add_argument("--gpu-count", required=True, type=int)
    p.add_argument("--script-file", required=True,
                   help="Path to the bash script body to run on the pod.")
    p.add_argument("--timeout", required=True, type=int,
                   help="Job timeout in seconds (max 86400).")
    p.add_argument("--tenant-id", default=None)
    p.add_argument("--region", default=None)
    p.add_argument("--env", action="append", default=[],
                   help="Repeatable KEY=VALUE for the job env.")
    p.add_argument("--api-url", default=None,
                   help="Override ROCKIELAB_API_URL.")
    p.add_argument("--no-poll", action="store_true",
                   help="Submit and exit immediately.")
    p.add_argument("--quiet", action="store_true",
                   help="Only print the final summary.")
    return p.parse_args(argv)


def _parse_env_pairs(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for kv in pairs:
        if "=" not in kv:
            _err(f"--env value must be KEY=VALUE, got {kv!r}.")
        k, _, v = kv.partition("=")
        if not k:
            _err(f"--env value has empty key: {kv!r}.")
        out[k] = v
    return out


def submit(args: argparse.Namespace) -> dict[str, Any]:
    if args.gpu_count < 1:
        _err("--gpu-count must be >= 1.")
    if args.timeout < 1:
        _err("--timeout must be >= 1.")
    if not os.path.isfile(args.script_file):
        _err(f"--script-file does not exist: {args.script_file}")

    with open(args.script_file, "r", encoding="utf-8") as fh:
        script = fh.read()

    api = (args.api_url or _api_url()).rstrip("/")
    tenant_id = args.tenant_id or os.environ.get("ROCKIELAB_TENANT_ID", "self")
    env = _parse_env_pairs(args.env)

    spec: dict[str, Any] = {
        "gpu_type": args.gpu_type,
        "gpu_count": args.gpu_count,
    }
    if args.region:
        spec["region"] = args.region

    payload = {
        "tenant_id": tenant_id,
        "spec": spec,
        "script": script,
        "env": env,
        "timeout_seconds": args.timeout,
    }
    code, body = _http_request(
        "POST",
        f"{api}/api/jobs/submit",
        headers=_headers(),
        body=json.dumps(payload).encode("utf-8"),
    )
    parsed = _decode_json(body)
    if code == 402:
        _err(f"Insufficient GPU credit: {parsed}", exit_code=4)
    if code >= 400:
        _err(f"Submit failed ({code}): {parsed}", exit_code=5)
    return parsed


def _decode_json(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body.decode("utf-8", errors="replace")


def get_job(job_id: str, *, api_url: Optional[str] = None) -> dict[str, Any]:
    api = (api_url or _api_url()).rstrip("/")
    code, body = _http_request(
        "GET",
        f"{api}/api/jobs/{urllib.parse.quote(job_id, safe='')}",
        headers=_headers(),
    )
    parsed = _decode_json(body)
    if code == 404:
        _err(f"Job {job_id} not found.", exit_code=6)
    if code >= 400:
        _err(f"GET /jobs/{job_id} failed ({code}): {parsed}", exit_code=7)
    return parsed


def poll(job_id: str, *, quiet: bool, api_url: Optional[str] = None) -> int:
    """Poll the job until it reaches a terminal state. Return exit code."""
    last_state: Optional[str] = None
    last_log: Optional[str] = None
    while True:
        job = get_job(job_id, api_url=api_url)
        state = job.get("state")
        log_line = job.get("last_log_line")
        if state != last_state:
            if not quiet:
                print(
                    f"[{_now_str()}] state -> {state}",
                    file=sys.stdout,
                    flush=True,
                )
            last_state = state
        if log_line and log_line != last_log:
            if not quiet:
                print(f"[log] {log_line}", file=sys.stdout, flush=True)
            last_log = log_line

        if state in TERMINAL_STATES:
            cost = job.get("cost_actual_cents") or job.get("cost_so_far_cents") or 0
            print(
                f"\nJob: {job.get('id')}\n"
                f"Shape: {job.get('gpu_count')}x{job.get('gpu_type')}\n"
                f"Final state: {state}\n"
                f"Cost: ${cost / 100:.2f}\n"
                f"Last log: {log_line or '(none)'}",
                file=sys.stdout,
                flush=True,
            )
            if state == "DONE":
                return 0
            if state == "CANCELLED":
                return 130  # SIGINT-style; caller can distinguish.
            return 1

        time.sleep(10)


def _now_str() -> str:
    return time.strftime("%H:%M:%S", time.localtime())


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    result = submit(args)
    job_id = result.get("job_id")
    if not args.quiet:
        print(
            f"Submitted job {job_id} "
            f"(estimated ${(result.get('estimated_cost_cents') or 0) / 100:.2f})",
            file=sys.stdout,
            flush=True,
        )
    if args.no_poll:
        return 0
    if not job_id:
        _err("Submit response missing job_id.", exit_code=8)
    return poll(
        job_id,
        quiet=args.quiet,
        api_url=args.api_url,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
