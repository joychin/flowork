"""试运行对等待节点的支持（2026-10-09 走查补齐批）。

走查发现：wait.* 未注入模拟上下文，任何含等待节点的流程试运行必然
「无法路由」——设计器最重要的验证工具在等待节点面前等于坏的。
修复：等待出边按「假设」求值，缺省 auto＝消息到达 / 超时两面都预演。
"""

from __future__ import annotations

from flowork.api import EngineConfig, load_definition, simulate_route

SPEC = {
    "code": "wait_demo",
    "name": "等待演示",
    "nodes": [
        {"code": "start", "kind": "start", "name": "开始"},
        {
            "code": "wait_pay",
            "kind": "wait",
            "name": "等回款",
            "message": {"name": "payment_arrived", "correlation_key": "order_no"},
            "timeout": {"after_minutes": 1440},
        },
        {"code": "ok", "kind": "end", "name": "到账"},
        {"code": "late", "kind": "end", "name": "超时"},
    ],
    "variables": [{"name": "order_no", "type": "string", "sample": "SO-1"}],
    "flows": [
        {"source": "start", "target": "wait_pay"},
        {"source": "wait_pay", "target": "ok", "condition": 'wait.result == "matched"'},
        {"source": "wait_pay", "target": "late", "condition": 'wait.result == "elapsed"'},
    ],
    "layout": {},
}


def _run(**kwargs):
    config = EngineConfig(db_file=":memory:")
    definition = load_definition(SPEC, config.node_registry)
    return simulate_route(definition, {"order_no": "SO-1"}, **kwargs)


def test_wait_dry_run_defaults_to_both_branches():
    """缺省 auto：消息到达 / 超时两面都预演，不再 blocked，reason 标注假设。"""
    result = _run()
    assert result.ok, result.blocked
    reached = [s.node for s in result.steps]
    assert "ok" in reached and "late" in reached
    wait_step = next(s for s in result.steps if s.node == "wait_pay")
    labels = [s.via_reason for s in result.steps if s.via_source == "wait_pay"]
    assert any("假设消息到达" in r for r in labels)
    assert any("假设超时" in r for r in labels)
    assert wait_step.via_source == "start"


def test_wait_dry_run_explicit_assumption_single_branch():
    """显式假设只推一面：matched 只到「到账」，elapsed 只到「超时」。"""
    matched = _run(assumptions={"wait_pay": "matched"})
    assert [s.node for s in matched.steps if s.via_source == "wait_pay"] == ["ok"]
    elapsed = _run(assumptions={"wait_pay": "elapsed"})
    assert [s.node for s in elapsed.steps if s.via_source == "wait_pay"] == ["late"]


def test_wait_dry_run_exposes_message_name_in_shape():
    """wait.message_name 来自节点声明（signal 广播场景可引用）。"""
    result = _run(assumptions={"wait_pay": "matched"})
    assert result.ok
    # 消息名进公式：改一条条件引用 message_name 再跑
    spec = {
        **SPEC,
        "flows": [
            {"source": "start", "target": "wait_pay"},
            {
                "source": "wait_pay",
                "target": "ok",
                "condition": 'wait.message_name == "payment_arrived"',
            },
        ],
    }
    config = EngineConfig(db_file=":memory:")
    definition = load_definition(spec, config.node_registry)
    result2 = simulate_route(definition, {}, assumptions={"wait_pay": "matched"})
    assert result2.ok, result2.blocked


def test_wait_dry_run_default_edge_when_neither_matches():
    """两种假设下条件都不满足 → 走默认边（不误报无法路由）。"""
    spec = {
        **SPEC,
        "flows": [
            {"source": "start", "target": "wait_pay"},
            {"source": "wait_pay", "target": "ok", "condition": 'wait.result == "never"'},
            {"source": "wait_pay", "target": "late", "is_default": True},
        ],
    }
    config = EngineConfig(db_file=":memory:")
    definition = load_definition(spec, config.node_registry)
    result = simulate_route(definition, {})
    assert result.ok, result.blocked
    assert [s.node for s in result.steps if s.via_source == "wait_pay"] == ["late"]
