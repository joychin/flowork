"""expr/catalog.py + F1 落地联动（expression-dataface §2.1/§5）。

目录 sanity、函数签名 label、路由求值时刻钉死（验收 E5）、取人位 now 可用。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from flowork.definitions.flow import SequenceFlow
from flowork.engine.routing import evaluation_variables, select_outgoing
from flowork.expr.catalog import identifier_entries, positions_of, reserved_identifiers
from flowork.expr.cel import evaluate_formula_value
from flowork.expr.context import expression_context
from flowork.spi.ports import FunctionSignature


def test_catalog_reserved_identifiers_and_positions():
    assert reserved_identifiers() == frozenset(
        {
            "initiator",
            "instance",
            "now",
            "prev_task",
            "task",
            "wait",
            "subject",  # A9 多实例子流程（v2.3 第四批）
            "child_total",
            "child_completed",
            "child_failed",
        }
    )
    assert positions_of("subject") == frozenset({"mapping_input"})
    assert positions_of("child_total") == frozenset({"completion"})
    assert positions_of("task") == frozenset({"reassign", "exit"})
    assert positions_of("prev_task") == frozenset({"condition", "subjects", "wait_correlation"})
    assert positions_of("wait") == frozenset({"condition"})  # 仅等待节点出边条件（§7.2）
    assert positions_of("initiator") == positions_of("now")
    assert positions_of("instance") == positions_of("now")  # 全位点全局词
    assert positions_of("amount") == frozenset()  # 非保留词


def test_engine_function_catalog():
    """F2：引擎注入函数目录（node）——validator 静态检查的对照源。"""
    from flowork.expr.catalog import engine_functions

    entry = engine_functions()["node"]
    assert entry.params == ("string",) and entry.returns == "map"
    assert entry.positions == frozenset({"condition", "subjects", "wait_correlation"})


def test_catalog_entries_carry_chinese_labels():
    labels = {entry.name: entry.label for entry in identifier_entries()}
    assert labels == {
        "initiator": "发起人",
        "instance": "当前实例",
        "now": "求值时刻",
        "prev_task": "上一站",
        "task": "当前任务快照",
        "wait": "等待结果",
        "subject": "当前迭代主体",  # A9 多实例子流程（v2.3 第四批）
        "child_total": "子实例总数",
        "child_completed": "已完成子实例数",
        "child_failed": "失败子实例数",
    }


def test_function_signature_label_defaults_empty():
    assert FunctionSignature(("string",), "bool").label == ""


def test_contrib_vocabulary_signatures_carry_labels():
    """F0：注册面签名带中文标签（函数目录树下发的原料，expression-dataface §5）。"""
    from flowork.contrib import cel_vocabulary

    table = cel_vocabulary(None).functions()  # type: ignore[arg-type]
    labels = {name: registered.signature.label for name, registered in table.items()}
    assert labels == {
        "manager_of": "取上级",
        "users_with_role": "按角色取人",
        "has_role": "判断角色成员",
    }


def test_now_usable_in_subject_expression():
    """取人位公式可引用 now（三元返回 subject，值位语义）。"""
    context = expression_context({}, start_user="u_start")
    assert (
        evaluate_formula_value("now > timestamp('2000-01-01T00:00:00Z') ? 'u_a' : 'u_b'", context)
        == "u_a"
    )


def test_routing_pins_one_moment(monkeypatch):
    """验收 E5：first_match 组内多条条件共用装配时刻的 now，不逐条取钟。

    时钟序列 [T1, T2, T3]：装配吃 T1 后，"now==T2" 的首边不中、"now==T1" 的
    次边中；若实现退化为逐条取钟，首边会吃到 T2 先命中（走向 late）。
    """
    t1 = datetime(2026, 1, 1, 0, 0, 0)
    t2 = datetime(2026, 1, 2, 0, 0, 0)
    times = iter([t1, t2, datetime(2026, 1, 3)])
    monkeypatch.setattr("flowork.expr.context.utc_now", lambda: next(times))
    instance = SimpleNamespace(start_user="u_start", id="i_e5", started_at=t1, business_key=None)
    flows = [
        SequenceFlow(
            source="gate", target="late", condition="now == timestamp('2026-01-02T00:00:00Z')"
        ),
        SequenceFlow(
            source="gate", target="on_time", condition="now == timestamp('2026-01-01T00:00:00Z')"
        ),
    ]
    picked = select_outgoing(flows, evaluation_variables(instance, {}), mode="first_match")
    assert [flow.target for flow in picked] == ["on_time"]
