from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "parse_approval.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("budget_parse_approval", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_explicit_approve_is_submittable():
    mod = _load_module()
    result = mod.parse_reply("I approve", estimate_cents=20000)
    assert result["decision"] == "approve"
    assert result["approved_for_submit"] is True


def test_modify_then_approve_parses_dollar_budget():
    mod = _load_module()
    result = mod.parse_reply("cap it at $250 and approved", estimate_cents=20000)
    assert result["decision"] == "modify_then_approve"
    assert result["budget_cents"] == 25000
    assert result["approved_for_submit"] is True


def test_modify_below_estimate_requires_requote():
    mod = _load_module()
    result = mod.parse_reply("actually make it $150", estimate_cents=20000)
    assert result["decision"] == "modify"
    assert result["budget_cents"] == 15000
    assert result["requires_requote"] is True
    assert result["approved_for_submit"] is False


def test_modify_then_approve_below_estimate_requires_non_submittable_requote():
    mod = _load_module()
    result = mod.parse_reply("cap it at $150 and approved", estimate_cents=20000)
    assert result["decision"] == "modify_then_approve"
    assert result["budget_cents"] == 15000
    assert result["requires_requote"] is True
    assert result["approved_for_submit"] is False


def test_negative_reply_cancels():
    mod = _load_module()
    result = mod.parse_reply("no, don't do it", estimate_cents=20000)
    assert result["decision"] == "cancel"
    assert result["approved_for_submit"] is False


def test_ambiguous_replies_do_not_count_as_approval():
    mod = _load_module()
    for reply in ("yes go ahead", "looks good", "wait", "hmm"):
        result = mod.parse_reply(reply, estimate_cents=20000)
        assert result["decision"] == "clarify"
        assert result["approved_for_submit"] is False


def test_negated_approval_forms_do_not_approve():
    mod = _load_module()
    for reply in (
        "not approved",
        "do not approve",
        "I cannot approve this",
        "not approving",
        "I don't think I approve",
    ):
        result = mod.parse_reply(reply, estimate_cents=20000)
        assert result["approved_for_submit"] is False
        assert result["decision"] in {"cancel", "clarify"}


def test_question_and_conditional_approval_forms_do_not_approve():
    mod = _load_module()
    for reply in (
        "approve?",
        "approved?",
        "approved if the logs look good",
        "approve if cost stays low",
        "I approve, if cost stays low",
        "Do you approve",
        "Should I approve",
    ):
        result = mod.parse_reply(reply, estimate_cents=20000)
        assert result["decision"] == "clarify"
        assert result["approved_for_submit"] is False


def test_leading_conditional_approval_forms_do_not_approve():
    mod = _load_module()
    for reply in (
        "If the cost is under $200, approve",
        "unless the queue is long, approve",
        "once the data is staged, approve",
        "when capacity is available, approve",
        "provided the run uses A100s, approve",
        "assuming the quote is current, approve",
        "as long as it stays under budget, approve",
    ):
        result = mod.parse_reply(reply, estimate_cents=20000)
        assert result["decision"] == "clarify"
        assert result["approved_for_submit"] is False


def test_qualified_approval_forms_do_not_approve():
    mod = _load_module()
    for reply in (
        "I approve only if the cost is under $200",
        "I approve only when capacity is confirmed",
        "approved provided that capacity is confirmed",
        "I approve subject to staying under $250",
        "I approve so long as it stays under $250",
        "approve after you confirm stock",
    ):
        result = mod.parse_reply(reply, estimate_cents=20000)
        assert result["decision"] == "clarify"
        assert result["approved_for_submit"] is False


def test_tag_question_approval_forms_do_not_approve():
    mod = _load_module()
    for reply in ("I approve, right?", "I approve, don't I?", "approve, ok?", "approved?"):
        result = mod.parse_reply(reply, estimate_cents=20000)
        assert result["decision"] == "clarify"
        assert result["approved_for_submit"] is False
