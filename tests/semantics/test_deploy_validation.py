"""部署期引用校验（v2.3 第三批 A11）：L3 定义内引用 + L4 目录实体存在性。

依据 deploy-validation-design.md / spec §6（第十轮）：
- L3a：call_activity 目标未发布——binding=latest（缺省）→ WARNING（子流程可独立
  部署）；binding=deployment → ERROR 拦发布（Zeebe deployment binding 同义）；
  自引用（process_code==自身）合法跳过。
- L3b：service_task handler 未注册 → WARNING（合法模式：先 deploy 后 register，
  tests/semantics/test_incident_resolution 即此）；strict 升 ERROR。
- L4：取人位字面量经 subject_exists 钩子——False → WARNING（strict ERROR）；
  None（目录未实现）→ 零噪音跳过；无字面量表达式零误报。
- 纪律：可见优于静默（WARNING 留痕不拦发布）；strict_deploy_validation 全升 ERROR。
"""

from __future__ import annotations

import pytest

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.validator import InvalidDefinition, IssueLevel, validate_definition
from flowork.definitions.validator_deploy import check_deploy_references, string_literals
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine


def _call_flow(code: str, target: str, binding: str | None = None):
    fields: dict = {"process_code": target}
    if binding:
        fields["binding"] = binding
    return (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee="'u_a'")
        .call_activity("sub", **fields)
        .end()
        .connect("start", "a")
        .connect("a", "sub")
        .connect("sub", "end")
    )


# —— string_literals 单元 ——


def test_string_literals_extracts_quoted_only():
    assert string_literals("'u_a'") == ["u_a"]
    assert string_literals("'u_' + level") == ["u_"]
    assert sorted(string_literals("['u1','u2']")) == ["u1", "u2"]
    assert string_literals("manager_of(initiator)") == []  # 无字面量零误报
    assert string_literals("amount > 100") == []


# —— L3a：call_activity 目标 ——


def test_call_target_unpublished_warns_on_latest_binding():
    engine = ProcessEngine()
    engine.repository.deploy(_call_flow("parent1", "child_missing").build())  # 不拦
    issues = check_deploy_references(
        _call_flow("parent1", "child_missing").build(), session=_session(engine), handlers=()
    )
    assert [(i.rule, i.level) for i in issues] == [
        ("call_activity.target_unpublished", IssueLevel.WARNING)
    ]


def test_call_target_missing_blocks_on_deployment_binding():
    engine = ProcessEngine()
    flow = _call_flow("parent2", "child_missing", binding="deployment")
    with pytest.raises(InvalidDefinition, match="target_missing"):
        engine.repository.deploy(flow.build())


def test_call_target_published_and_self_reference_pass():
    engine = ProcessEngine()
    # 子先独立部署（合法路径），父后部署 → 无 issue
    engine.repository.deploy(FlowBuilder("child_ok").start().end().connect("start", "end").build())
    issues = check_deploy_references(
        _call_flow("parent3", "child_ok").build(), session=_session(engine), handlers=()
    )
    assert issues == []
    # 自引用（递归流程首版）合法
    issues2 = check_deploy_references(
        _call_flow("rec", "rec").build(), session=_session(engine), handlers=()
    )
    assert issues2 == []


def test_strict_config_lifts_warnings_to_errors():
    engine = ProcessEngine(EngineConfig(strict_deploy_validation=True))
    with pytest.raises(InvalidDefinition, match="target_unpublished"):
        engine.repository.deploy(_call_flow("parent4", "child_missing").build())


# —— L3b：handler 注册 ——


def test_unknown_handler_warns_but_allows_late_registration():
    engine = ProcessEngine()
    flow = (
        FlowBuilder("svc1")
        .start()
        .service_task("boom", action="explode")
        .end()
        .connect("start", "boom")
        .connect("boom", "end")
    )
    engine.repository.deploy(flow.build())  # 先 deploy（WARNING 留痕不拦）
    engine.register_handler("explode", lambda ctx, params: None)  # 后注册合法
    issues = check_deploy_references(flow.build(), session=_session(engine), handlers=())
    assert [(i.rule, i.level) for i in issues] == [
        ("service_task.handler_unknown", IssueLevel.WARNING)
    ]
    # strict 下拦
    strict_engine = ProcessEngine(EngineConfig(strict_deploy_validation=True))
    with pytest.raises(InvalidDefinition, match="handler_unknown"):
        strict_engine.repository.deploy(flow.build())


# —— L4：目录实体存在性 ——


class _Org:
    """最小目录钩子宿主：subject_exists 返回预置判定。"""

    def __init__(self, known: set[str]) -> None:
        self._known = known

    def subject_exists(self, subject: str) -> bool:
        return subject in self._known


def _session(engine: ProcessEngine):
    return engine.session_factory()


def test_subject_literal_unknown_warns_and_strict_blocks():
    flow = (
        FlowBuilder("l4a")
        .start()
        .user_task("a", assignee="'u_zhang'")
        .cc_task("notify", recipients=["'u_li'", "manager_of(initiator)"])
        .end()
        .connect("start", "a")
        .connect("a", "notify")
        .connect("notify", "end")
    ).build()
    engine = ProcessEngine(EngineConfig(org=_Org(known={"u_zhang", "u_boss"})))
    issues = check_deploy_references(
        flow,
        session=_session(engine),
        handlers=(),
        subject_exists=engine.config.org.subject_exists,  # type: ignore[union-attr]
    )
    # 'u_li' 不在目录 → WARNING；'u_zhang' 在册、函数式取人无字面量 → 零误报
    assert [(i.rule, i.node) for i in issues] == [("participant.subject_unknown", "notify")]

    strict_engine = ProcessEngine(
        EngineConfig(org=_Org(known=set()), strict_deploy_validation=True)
    )
    with pytest.raises(InvalidDefinition, match="subject_unknown"):
        strict_engine.repository.deploy(flow)


def test_subject_hook_none_is_silent():
    """目录未实现 subject_exists（None/缺方法）→ 零噪音（不降级 WARNING）。"""
    flow = (
        FlowBuilder("l4b")
        .start()
        .user_task("a", assignee="'u_ghost'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
    ).build()
    engine = ProcessEngine()  # 裸引擎（org=None）→ hook 缺方法 → deploy 不问
    engine.repository.deploy(flow)  # 不拦、不告警
    issues = check_deploy_references(
        flow, session=_session(engine), handlers=(), subject_exists=None
    )
    assert issues == []


def test_pure_validate_definition_unchanged_by_l4():
    """设计时 validate()（无 I/O）不跑 L3/L4——纯校验面不受影响。"""
    flow = _call_flow("pure", "never_published").build()
    issues = validate_definition(flow, ProcessEngine().config.node_registry)
    assert all(i.rule != "call_activity.target_unpublished" for i in issues)
