#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = SCRIPT_DIR.parent / "references" / "template.md"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="render_term_sheet.py",
        description="Render a budget term-sheet JSON object into markdown.",
    )
    parser.add_argument("--term-sheet-json", required=True, help="Path to the term-sheet JSON.")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    term_sheet = json.loads(Path(args.term_sheet_json).read_text(encoding="utf-8"))
    rendered = render_term_sheet(term_sheet)
    sys.stdout.write(rendered)
    if not rendered.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def render_term_sheet(term_sheet: dict[str, Any]) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    estimate_cents = term_sheet.get("estimate_cents")
    recommended_cents = term_sheet.get("recommended_budget_cents")
    user_budget_cents = term_sheet.get("user_budget_cents")
    compute = term_sheet.get("compute") or {}
    decision = term_sheet.get("decision") or {}

    stage_lines = "\n".join(
        f"- `{stage['name']}`: {_format_usd(stage['cents'])}"
        for stage in term_sheet.get("stages") or []
    ) or "- No stage breakdown available."

    availability_line = f"{term_sheet.get('availability')} ({term_sheet.get('status_code')}): {term_sheet.get('status_reason')}"
    compute_parts = [
        compute.get("display_provider", "Rockie GPU"),
        f"{compute.get('gpu_count', '?')}x{compute.get('gpu_type', 'unknown')}",
    ]
    if compute.get("region"):
        compute_parts.append(f"region={compute['region']}")
    if compute.get("tier"):
        compute_parts.append(f"tier={compute['tier']}")

    values = {
        "quote_id": str(term_sheet.get("quote_id", "")),
        "job_shape": str(term_sheet.get("job_shape", "")),
        "compute_line": ", ".join(part for part in compute_parts if part),
        "availability_line": availability_line,
        "wallclock_line": _format_wallclock(term_sheet.get("wallclock_minutes")),
        "estimate_line": _format_usd(estimate_cents),
        "recommended_budget_line": _format_usd_or_na(recommended_cents),
        "user_budget_line": _format_usd_or_na(user_budget_cents),
        "stage_lines": stage_lines,
        "confidence_line": (
            f"{term_sheet.get('confidence_bucket')}: {term_sheet.get('confidence_statement')}"
        ),
        "decision_line": _decision_line(decision),
    }

    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{ {key} }}}}", value)
    return rendered


def _format_usd(cents: Optional[int]) -> str:
    if cents is None:
        return "n/a"
    return f"${cents / 100:.2f} ({cents} cents)"


def _format_usd_or_na(cents: Optional[int]) -> str:
    return _format_usd(cents)


def _format_wallclock(minutes: Any) -> str:
    if minutes is None:
        return "unknown"
    minutes_int = int(minutes)
    hours, remainder = divmod(minutes_int, 60)
    if hours and remainder:
        return f"{hours}h {remainder}m"
    if hours:
        return f"{hours}h"
    return f"{remainder}m"


def _decision_line(decision: dict[str, Any]) -> str:
    state = decision.get("state", "pending")
    approvable = "yes" if decision.get("approvable") else "no"
    approved = "yes" if decision.get("approved_for_submit") else "no"
    return f"{state} (approvable={approvable}, approved_for_submit={approved})"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
