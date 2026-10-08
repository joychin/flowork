"""旧结构化条件组（groups）→ CEL 的 loader shim 翻译（条件单轨化兼容，§4.1）。

覆盖：单条件操作符全表、组内 AND/组间 OR、formula 直取、空条件归 None、
不可翻译场景明确报错。shim 是设计器「点选生成公式」将共用的同一张映射。
"""

from __future__ import annotations

import pytest

from flowork.definitions.definition import InvalidDefinitionData, load_definition
from flowork.definitions.flow import SequenceFlow


def _flow_condition_of(data: dict, index: int = 1) -> str | None:
    """flows[1] 是挂条件的边（flows[0]=start→gate 无条件）。"""
    registry = __import__("flowork.runtime.registry", fromlist=["create_registry"])
    definition = load_definition(data, registry.create_registry())
    return definition.flows[index].condition


def _spec_with(condition: object) -> dict:
    return {
        "code": "shim",
        "name": "shim",
        "nodes": [
            {"code": "start", "kind": "start"},
            {"code": "gate", "kind": "branch"},
            {"code": "a", "kind": "user_task", "assignee": "u1"},
            {"code": "end", "kind": "end"},
        ],
        "flows": [
            {"source": "start", "target": "gate"},
            {"source": "gate", "target": "a", "condition": condition},
            {"source": "gate", "target": "end", "is_default": True},
            {"source": "a", "target": "end"},
        ],
    }


def _item(left: str, op: str, right: object = None) -> dict:
    return {"left": left, "operator": op, "right": right}


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (_item("amount", "eq", 1000), "amount == 1000"),
        (_item("amount", "ne", 1000), "amount != 1000"),
        (_item("amount", "gt", 10000), "amount > 10000"),
        (_item("amount", "ge", 1000), "amount >= 1000"),
        (_item("amount", "lt", 5), "amount < 5"),
        (_item("amount", "le", 5), "amount <= 5"),
        (_item("tag", "in", ["a", "b"]), 'tag in ["a", "b"]'),
        (_item("tag", "not_in", ["a"]), '!(tag in ["a"])'),
        (_item("memo", "contains", "pay"), 'memo.contains("pay")'),
        (_item("code", "starts_with", "PRJ"), 'code.startsWith("PRJ")'),
        (_item("x", "is_empty"), '(x == null || x == "" || x.size() == 0)'),
        (_item("x", "is_not_empty"), '!(x == null || x == "" || x.size() == 0)'),
        (_item("urgent", "eq", True), "urgent == true"),
        (_item("level", "eq", None), "level == null"),
        (_item("level", "eq", "L2"), 'level == "L2"'),
    ],
)
def test_shim_translates_operators(item, expected):
    assert _flow_condition_of(_spec_with({"groups": [[item]]})) == expected


def test_shim_group_and_or():
    groups = [
        [_item("amount", "gt", 1), _item("urgent", "eq", True)],  # 组内 AND
        [_item("level", "eq", "L2")],  # 组间 OR
    ]
    assert (
        _flow_condition_of(_spec_with({"groups": groups}))
        == '(amount > 1 && urgent == true) || level == "L2"'
    )


def test_shim_formula_passthrough_and_empty():
    assert _flow_condition_of(_spec_with({"groups": [], "formula": "amount > 1"})) == "amount > 1"
    assert _flow_condition_of(_spec_with({"groups": [], "formula": None})) is None
    assert _flow_condition_of(_spec_with("amount > 1")) == "amount > 1"  # 新格式字符串原样


def test_shim_string_literal_escaping():
    definition_condition = _flow_condition_of(
        _spec_with({"groups": [[_item("q", "eq", 'say "hi"')]]})
    )
    assert definition_condition == 'q == "say \\"hi\\""'


def test_shim_untranslatable_operator_fails_loudly():
    with pytest.raises(InvalidDefinitionData, match="手工迁移"):
        _flow_condition_of(_spec_with({"groups": [[_item("x", "no_such_op", 1)]]}))
    with pytest.raises(InvalidDefinitionData, match="非数值"):
        _flow_condition_of(_spec_with({"groups": [[_item("x", "gt", "big")]]}))


def test_shim_translated_condition_evaluates():
    """翻译产物是可执行 CEL（不是死字符串）：加载即求值全链路。"""
    from flowork.expr.conditions import evaluate_condition

    condition = _flow_condition_of(
        _spec_with({"groups": [[_item("amount", "ge", 1000), _item("urgent", "eq", True)]]})
    )
    assert evaluate_condition(condition, {"amount": 1500, "urgent": True}) is True
    assert evaluate_condition(condition, {"amount": 500, "urgent": True}) is False


def test_sequence_flow_condition_is_plain_string():
    """模型收敛：condition 就是 str|None（不再有 FlowCondition 包装）。"""
    flow = SequenceFlow(source="a", target="b", condition="amount > 1")
    assert flow.condition == "amount > 1"
