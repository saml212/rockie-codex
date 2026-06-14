#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Optional

APPROVAL_PATTERN = re.compile(r"\b(?:approve|approved)\b", re.IGNORECASE)

CANCEL_PATTERNS = (
    re.compile(r"\bcancel\b", re.IGNORECASE),
    re.compile(r"\bdon't do it\b", re.IGNORECASE),
    re.compile(r"\bdo not do it\b", re.IGNORECASE),
    re.compile(r"\bnever mind\b", re.IGNORECASE),
    re.compile(r"\bstop\b", re.IGNORECASE),
)

NEGATED_APPROVAL = re.compile(
    r"""
    (?:
        \b(?:don't|do not|did not|not|never|can't|cannot|won't|wouldn't|isn't|is not)\s+
        (?:think\s+)?(?:i\s+|we\s+|you\s+)?(?:approve|approved|approving|approval)\b
      |
        \b(?:don't|do not|did not|not|never|can't|cannot|won't|wouldn't|isn't|is not)\s+
        think\s+(?:i\s+|we\s+|you\s+)?(?:can\s+|should\s+|would\s+)?(?:approve|approved)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
CONDITIONAL_APPROVAL = re.compile(
    r"""
    (?:
        ^\s*(?:if|unless|once|when|provided|assuming|as\s+long\s+as)\b[\s\S]*\b(?:approve|approved)\b
      |
        \b(?:do|should|can|could|would)\s+(?:you|i|we)\s+(?:approve|approved)\b
      |
        \b(?:approve|approved)\b\s*
        (?:
            \?
          | [,;:]?\s*(?:right|ok|okay|yes|no|do\s+i|don't\s+i|dont\s+i|should\s+i|can\s+i)\s*\?
          | [,;:]?\s*(?:only\s+)?if\b
          | unless\b
          | (?:only\s+)?when\b
          | once\b
          | provided(?:\s+that)?\b
          | assuming\b
          | as\s+long\s+as\b
          | so\s+long\s+as\b
          | subject\s+to\b
          | after\b
        )
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
AMOUNT_PATTERN = re.compile(
    r"""
    (?:
        \$\s*(?P<dollar_amount>\d+(?:\.\d{1,2})?)
      |
        (?P<word_amount>\d+(?:\.\d{1,2})?)\s*dollars?\b
      |
        \bcap\s+(?:it\s+)?at\s+(?P<cap_amount>\d+(?:\.\d{1,2})?)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="parse_approval.py",
        description="Parse a term-sheet approval reply.",
    )
    parser.add_argument("--reply", required=True, help="User reply text.")
    parser.add_argument("--estimate-cents", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    result = parse_reply(args.reply, estimate_cents=args.estimate_cents)
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def parse_reply(reply: str, *, estimate_cents: Optional[int] = None) -> dict[str, Any]:
    text = reply.strip()
    lowered = text.lower()

    if not text:
        return _result("clarify", reason="Empty reply. Ask for approve, modify, or cancel.")

    if NEGATED_APPROVAL.search(text):
        return _result("cancel", reason="Reply explicitly negated approval.")
    if CONDITIONAL_APPROVAL.search(text):
        return _result("clarify", reason="Approval was phrased as a question or condition.")

    cancel = any(pattern.search(text) for pattern in CANCEL_PATTERNS)
    approval = _has_strict_approval(text)
    amount_matches = list(AMOUNT_PATTERN.finditer(text))
    if len(amount_matches) > 1:
        return _result("clarify", reason="Multiple budget amounts found.")

    ambiguous_tokens = ("looks good", "yes go ahead", "sure", "ok", "okay", "wait", "hmm")
    if not approval and not cancel and any(token in lowered for token in ambiguous_tokens):
        return _result("clarify", reason="Reply is ambiguous and does not contain an explicit approval token.")

    budget_cents = None
    if amount_matches:
        budget_cents = _amount_match_to_cents(amount_matches[0])
        if budget_cents is None:
            return _result("clarify", reason="Could not parse the budget amount.")
        if _contains_negated_budget(text):
            return _result("clarify", reason="Budget change is hedged or negated.")

    if cancel and approval:
        return _result("clarify", reason="Reply contains both approval and cancellation.")
    if cancel:
        return _result("cancel", budget_cents=budget_cents, reason="Reply cancelled the submit.")
    if approval and budget_cents is not None:
        result = _result(
            "modify_then_approve",
            budget_cents=budget_cents,
            reason="Reply approved with a modified budget.",
        )
        if estimate_cents is not None and budget_cents < estimate_cents:
            result["requires_requote"] = True
            result["approved_for_submit"] = False
            result["reason"] = (
                "Modified budget is below the estimate; re-render a non-submittable term sheet "
                "and ask for a budget at or above the estimate or a cancel."
            )
        return result
    if approval:
        return _result("approve", reason="Explicit approval token matched.")
    if budget_cents is not None:
        result = _result("modify", budget_cents=budget_cents, reason="Budget change requested.")
        if estimate_cents is not None and budget_cents < estimate_cents:
            result["requires_requote"] = True
            result["reason"] = (
                "Modified budget is below the estimate; re-render a non-submittable term sheet "
                "and ask for a budget at or above the estimate or a cancel."
            )
        return result

    return _result("clarify", reason="Reply did not contain an explicit approval, modification, or cancel signal.")


def _contains_negated_budget(text: str) -> bool:
    lowered = text.lower()
    hedges = ("maybe $", "around $", "about $", "roughly $", "not $")
    return any(token in lowered for token in hedges)


def _has_strict_approval(text: str) -> bool:
    for match in APPROVAL_PATTERN.finditer(text):
        suffix = text[match.end():]
        if re.match(r"\s*\?", suffix):
            continue
        if re.match(r"\s*[,;:]?\s*(?:(?:only\s+)?if|unless|(?:only\s+)?when|once|provided(?:\s+that)?|assuming|as\s+long\s+as|so\s+long\s+as|subject\s+to|after)\b", suffix, re.IGNORECASE):
            continue
        if re.match(r"\s*[,;:]?\s*(?:right|ok|okay|yes|no|do\s+i|don't\s+i|dont\s+i|should\s+i|can\s+i)\s*\?", suffix, re.IGNORECASE):
            continue
        prefix = text[max(0, match.start() - 24):match.start()]
        if re.search(r"\b(?:don't|do not|did not|not|never|can't|cannot|won't|wouldn't|isn't|is not)\s+$", prefix, re.IGNORECASE):
            continue
        if re.search(r"\b(?:do|should|can|could|would)\s+(?:you|i|we)\s+$", prefix, re.IGNORECASE):
            continue
        return True
    return False


def _amount_match_to_cents(match: re.Match[str]) -> Optional[int]:
    amount_str = match.group("dollar_amount") or match.group("word_amount") or match.group("cap_amount")
    if amount_str is None:
        return None
    return int(round(float(amount_str) * 100))


def _result(decision: str, *, budget_cents: Optional[int] = None, reason: str) -> dict[str, Any]:
    return {
        "decision": decision,
        "budget_cents": budget_cents,
        "reason": reason,
        "approved_for_submit": decision in {"approve", "modify_then_approve"},
    }


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
