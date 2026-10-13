"""声明式钩子接线（spec §2.6 层 1；2026-10-13 内核收尾批 2）。

背景（走查实证）：``FlowNode.hooks`` 此前被 pydantic 收下但引擎零读取——
图纸声明静默失效、零告警（注入 hooks 后 handler 调用 0 次）；连线 on_take
裸 resolve 直调（未注册＝UnknownHandler 冒泡零留痕；模拟试跑真执行宿主
handler，违反 A10 隔离承诺）。本批接线 on_enter/on_exit/on_cancel 三时机
并统一进 engine/declared_hooks.py 咽喉。

拍板（2026-10-13）：未注册 handler＝部署期 WARNING（strict 升 ERROR，规则
node.hooks/flow.on_take.handler_unknown）+ 运行期技术故障挂起+故障单；
on_fail 挂账错误归因专题，本批不接。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable
from flowork.definitions.validator import InvalidDefinition
from flowork.definitions.validator_deploy import check_deploy_references
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.engine.kernel import EngineExecutionError

_HOOK = {"handler": "stamp", "params": {"mark": 1}}


def _session(engine: ProcessEngine):
    return engine.session_factory()


# —— 三时机：调用、params 透传、次序 ——


def test_on_enter_write_feeds_condition():
    """on_enter 先于行为与路由：钩子写的变量被条件边真求值消费。"""
    engine = ProcessEngine()
    calls: list[tuple[str, int]] = []

    def stamp(ctx, params):
        calls.append((ctx.node.code, params["mark"]))
        ctx.variables["amount"] = 200

    engine.register_handler("stamp", stamp)
    spec = (
        FlowBuilder("hk1")
        .start()
        .service_task("w", hooks={"on_enter": _HOOK})
        .end("big")
        .end("small")
        .connect("start", "w")
        .flow("w", "big", condition="amount > 100")
        .flow("w", "small", default=True)
        .build(variables=[ProcessVariable(name="amount", type="number")])
    )
    engine.repository.deploy(spec)
    iid = engine.runtime.start_process("hk1", start_user="u", variables={"amount": 1})
    assert calls == [("w", 1)]  # params 透传
    assert engine.runtime.get_instance(iid).state == "completed"  # 走了 big 侧


def _stamp(calls: list[tuple[str, int]]):
    def handler(ctx, params):
        calls.append((ctx.node.code, params["mark"]))

    return handler


def test_on_exit_fires_after_task_completion():
    engine = ProcessEngine()
    calls: list[tuple[str, int]] = []
    engine.register_handler("stamp", _stamp(calls))
    spec = (
        FlowBuilder("hk2")
        .start()
        .user_task(
            "a", assignee="'u_a'", hooks={"on_exit": {"handler": "stamp", "params": {"mark": 2}}}
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(spec)
    iid = engine.runtime.start_process("hk2", start_user="u")
    task = engine.task.list_tasks(instance_id=iid)[0]
    engine.task.complete(task.id, actor="u_a")
    assert calls == [("a", 2)]
    assert engine.runtime.get_instance(iid).state == "completed"


def test_on_cancel_fires_on_terminate():
    engine = ProcessEngine()
    calls: list[tuple[str, int]] = []
    engine.register_handler("stamp", _stamp(calls))
    spec = (
        FlowBuilder("hk3")
        .start()
        .user_task(
            "a",
            assignee="'u_a'",
            hooks={"on_cancel": {"handler": "stamp", "params": {"mark": 3}}},
        )
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(spec)
    iid = engine.runtime.start_process("hk3", start_user="u")
    engine.runtime.terminate_process(iid, actor="admin", reason="收队")
    assert calls == [("a", 3)]  # 终止砍树 → cancel_token → 声明式先于行为


# —— 未注册 / handler 异常：技术故障挂起 + 故障单 ——


def test_unregistered_node_hook_midflow_suspends_with_incident():
    engine = ProcessEngine()
    spec = (
        FlowBuilder("hk4")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'", hooks={"on_enter": {"handler": "ghost"}})
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    engine.repository.deploy(spec)  # 部署期 WARNING 留痕不拦（先部署后注册合法）
    iid = engine.runtime.start_process("hk4", start_user="u")
    task = engine.task.list_tasks(instance_id=iid)[0]
    with pytest.raises(EngineExecutionError, match="未注册的 handler 'ghost'"):
        engine.task.complete(task.id, actor="u_a")
    assert engine.runtime.get_instance(iid).state == "suspended"
    incidents = engine.runtime.list_incidents(instance_id=iid)
    assert incidents and incidents[0]["state"] == "open"
    assert "ghost" in incidents[0]["message"]


def test_unregistered_on_take_hook_suspends_with_incident():
    engine = ProcessEngine()
    spec = (
        FlowBuilder("hk5")
        .start()
        .user_task("a", assignee="'u_a'")
        .user_task("b", assignee="'u_b'")
        .end()
        .connect("start", "a")
        .flow("a", "b", on_take={"handler": "ghost"})
        .connect("b", "end")
        .build()
    )
    engine.repository.deploy(spec)
    iid = engine.runtime.start_process("hk5", start_user="u")
    task = engine.task.list_tasks(instance_id=iid)[0]
    with pytest.raises(EngineExecutionError, match="未注册的 handler 'ghost'"):
        engine.task.complete(task.id, actor="u_a")
    assert engine.runtime.get_instance(iid).state == "suspended"
    assert engine.runtime.list_incidents(instance_id=iid)


def test_handler_exception_wrapped_as_incident():
    engine = ProcessEngine()

    def boom(ctx, params):
        raise ValueError("炸了")

    engine.register_handler("boom", boom)
    spec = (
        FlowBuilder("hk6")
        .start()
        .user_task("a", assignee="'u_a'", hooks={"on_enter": {"handler": "boom"}})
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    engine.repository.deploy(spec)
    with pytest.raises(EngineExecutionError, match="执行失败") as excinfo:
        engine.runtime.start_process("hk6", start_user="u")
    assert "炸了" in str(excinfo.value)


# —— 模拟试跑隔离（A10：绝不执行宿主 handler）——


def test_simulate_skips_declared_hooks_and_on_take():
    engine = ProcessEngine()
    calls: list[str] = []
    engine.register_handler("stamp", lambda ctx, params: calls.append(ctx.node.code))
    spec = (
        FlowBuilder("hksim")
        .start()
        .service_task("w", hooks={"on_enter": _HOOK})
        .end()
        .flow("start", "w", on_take={"handler": "stamp"})
        .connect("w", "end")
        .build()
    )
    engine.repository.deploy(spec)
    report = engine.runtime.simulate("hksim")
    assert calls == []  # 模拟零执行（on_take 此前无守卫会真跑——隔离漏随本批修复）
    assert report.final_state == "completed"


# —— 部署期 L3：node.hooks / flow.on_take handler_unknown ——


def _hooked_flow(code: str):
    return (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u_a'", hooks={"on_enter": {"handler": "ghost"}})
        .end()
        .flow("start", "a", on_take={"handler": "ghost2"})
        .connect("a", "end")
    )


def test_deploy_warns_on_unknown_hook_handlers():
    engine = ProcessEngine()
    engine.repository.deploy(_hooked_flow("hkdep").build())  # WARNING 不拦发布
    issues = check_deploy_references(
        _hooked_flow("hkdep").build(), session=_session(engine), handlers=()
    )
    assert sorted(i.rule for i in issues) == [
        "flow.on_take.handler_unknown",
        "node.hooks.handler_unknown",
    ]
    assert all(i.level.value == "warning" for i in issues)


def test_deploy_strict_blocks_unknown_hook_handlers():
    engine = ProcessEngine(EngineConfig(strict_deploy_validation=True))
    with pytest.raises(InvalidDefinition, match="handler_unknown"):
        engine.repository.deploy(_hooked_flow("hkdep2").build())


def test_registered_hook_handlers_pass_deploy_check():
    engine = ProcessEngine()
    engine.register_handler("stamp", lambda ctx, params: None)
    spec = (
        FlowBuilder("hkdep3")
        .start()
        .user_task("a", assignee="'u_a'", hooks={"on_enter": _HOOK})
        .end()
        .flow("start", "a", on_take={"handler": "stamp"})
        .connect("a", "end")
        .build()
    )
    issues = check_deploy_references(
        spec, session=_session(engine), handlers=engine.handlers.names()
    )
    assert issues == []


# —— 复核批（2026-10-13 事件 review 复核批）：三处手工完成路径的 on_exit ——
# 修前 user_task 全员自动通过 / wait 三路收口 / 驳回流转均自行 finish_token，
# 静默跳过声明式 on_exit（契约：每次完成都跑，declared_hooks.py 模块头）。


def test_on_exit_runs_on_auto_pass_finish_now():
    """全员自动通过（_finish_now）路径跑声明式 on_exit——修前静默跳过。"""
    engine = ProcessEngine()
    calls: list[str] = []

    def stamp(ctx, params):
        calls.append(ctx.node.code)

    engine.register_handler("stamp", stamp)
    engine.repository.deploy(
        FlowBuilder("hk_autopass")
        .start()
        .user_task("a", assignee="'u_s'", auto_pass_originator=True, hooks={"on_exit": _HOOK})
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    iid = engine.runtime.start_process("hk_autopass", start_user="u_s")
    assert engine.runtime.get_instance(iid).state == "completed"
    assert calls == ["a"]  # 修前 calls == []
    engine.close()


def test_on_exit_runs_on_wait_finish():
    """等待节点消息命中收口跑声明式 on_exit——修前静默跳过。"""
    engine = ProcessEngine()
    calls: list[str] = []

    def stamp(ctx, params):
        calls.append(ctx.node.code)

    engine.register_handler("stamp", stamp)
    engine.repository.deploy(
        FlowBuilder("hk_wait")
        .start()
        .wait("w", message={"name": "m1"}, hooks={"on_exit": _HOOK})
        .end()
        .connect("start", "w")
        .connect("w", "end")
        .build()
    )
    iid = engine.runtime.start_process("hk_wait", start_user="u_s")
    engine.messages.publish_message("m1", payload={"a": 1}, message_id="hk1")
    assert engine.runtime.get_instance(iid).state == "completed"
    assert calls == ["w"]  # 修前 calls == []
    engine.close()


def test_on_exit_runs_on_reject_flow():
    """驳回流转跑声明式 on_exit——修前静默跳过。"""
    engine = ProcessEngine()
    calls: list[str] = []

    def stamp(ctx, params):
        calls.append(ctx.node.code)

    engine.register_handler("stamp", stamp)
    engine.repository.deploy(
        FlowBuilder("hk_reject")
        .start()
        .user_task("a", assignee="'u1'", hooks={"on_exit": _HOOK})
        .user_task("b", assignee="'u2'")
        .end()
        .connect("start", "a")
        .connect("a", "b")
        .connect("b", "end")
        .build()
    )
    iid = engine.runtime.start_process("hk_reject", start_user="u_s")
    task = engine.task.list_tasks(instance_id=iid)[0]
    engine.task.reject(task.id, actor="u1")
    assert calls == ["a"]  # 修前 calls == []
    engine.close()
