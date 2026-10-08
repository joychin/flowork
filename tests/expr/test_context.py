"""expr/context.py：表达式求值上下文构造（kernel-v2 §4.3/C.4）。

全表达式位共用的唯一构造入口：变量副本 + ``initiator`` 保留标识符 + 可选
``task`` 快照——显式同名变量优先（不覆盖声明值）。
"""

from __future__ import annotations

from flowork.expr.context import expression_context


def test_returns_copy_not_same_dict():
    variables = {"amount": 1}
    payload = expression_context(variables)
    payload["amount"] = 2
    assert variables["amount"] == 1


def test_initiator_injected_from_start_user():
    payload = expression_context({"amount": 1}, start_user="user:zhang")
    assert payload["initiator"] == "user:zhang"


def test_no_start_user_no_initiator():
    payload = expression_context({"amount": 1})
    assert "initiator" not in payload


def test_explicit_initiator_variable_wins():
    """显式同名变量优先（spec §4.3：声明为发起人语义时以声明为准）。"""
    payload = expression_context({"initiator": "user:declared"}, start_user="user:zhang")
    assert payload["initiator"] == "user:declared"


def test_task_snapshot_injected():
    snapshot = {"id": "t1", "assignee": "user:li", "outcome": None}
    payload = expression_context({"amount": 1}, task=snapshot)
    assert payload["task"] == {"id": "t1", "assignee": "user:li", "outcome": None}


def test_task_snapshot_copied_and_explicit_wins():
    snapshot = {"id": "t1", "assignee": "user:li", "outcome": None}
    payload = expression_context({}, task=snapshot)
    snapshot["id"] = "t2"  # 快照后被改不影响上下文（防御性副本）
    assert payload["task"]["id"] == "t1"

    payload2 = expression_context({"task": {"id": "declared"}}, task=snapshot)
    assert payload2["task"] == {"id": "declared"}
