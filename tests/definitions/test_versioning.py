"""P0-4（kernel-v2 spec §6/§16）：major/revision 版本判定测试。

验收：破坏性/兼容性表驱动；同内容重复部署不增版本不发事件；override 带 reason
进事件；维护分支（对老 major 续发 revision）；并发撞车由唯一索引兜底。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable, dump_definition
from flowork.definitions.versioning import (
    TriggeredRule,
    compute_verdict,
    content_hash_of,
)
from flowork.engine.engine import ProcessEngine
from flowork.events.domain import DomainEvent
from flowork.persistence.orm import EventOutboxRow, ProcessDefinitionRow


def _linear(code: str = "ver", assignee: str = "'u_a'") -> ProcessDefinition:
    return (
        FlowBuilder(code)
        .start()
        .user_task("a", assignee=assignee)
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )


def _breaking_variant() -> ProcessDefinition:
    """合法图上的破坏性变体：给 end 增加入边（R4 拓扑语义破坏）。"""
    from flowork.definitions.flow import SequenceFlow

    variant = _linear("ver")
    variant.flows.append(SequenceFlow(source="start", target="end"))
    return variant


def _deployed_events(engine: ProcessEngine, name: str) -> list[dict]:
    with engine.session_factory() as session:
        return [
            json.loads(json.dumps(row.payload))
            for row in session.scalars(select(EventOutboxRow))
            if row.event_name == name
        ]


def _hash(definition) -> str:
    return content_hash_of(dump_definition(definition))


# —— 规则表驱动（R1–R4 破坏 / 兼容对照）——


def _base_def() -> ProcessDefinition:
    definition = _linear("ver")
    definition.input = {
        "type": "object",
        "properties": {"amount": {"type": "number"}},
        "required": ["amount"],
    }
    definition.variables = [
        ProcessVariable(name="amount", type="number", sample=1, searchable=True)
    ]
    return definition


@pytest.mark.parametrize(
    ("mutate", "expected_rules"),
    [
        # 兼容：改名 / 加节点 / 加出边（新节点入边是新节点的，不动既有节点入边集）
        (lambda d: setattr(d, "name", "改名"), []),
        (lambda d: d.nodes.append(type(d.nodes[-1])(code="b", kind="end")), []),
        # R3：删除已发布节点
        (
            lambda d: setattr(d, "nodes", [n for n in d.nodes if n.code != "a"]),
            ["R3_node_identity"],
        ),
        # R4：既有节点入边集合变化（a 增加来自 end 的第二条入边）
        (
            lambda d: d.flows.append(type(d.flows[0])(source="end", target="a")),
            ["R4_topology"],
        ),
        # R1：input required 扩大
        (
            lambda d: d.input and d.input.update(required=["amount", "reason"]),
            ["R1_input_contract"],
        ),
        # R2：searchable 集合变化
        (
            lambda d: d.variables[0].searchable and setattr(d.variables[0], "searchable", False),
            ["R2_searchable_index"],
        ),
    ],
)
def test_breaking_rules_table_driven(mutate, expected_rules):
    base = _base_def()
    target = _base_def()
    mutate(target)
    verdict = compute_verdict(base, target, _hash(target))
    triggered = sorted(r.rule for r in verdict.triggered_rules)
    assert triggered == sorted(expected_rules)
    assert verdict.breaking == bool(expected_rules)


def test_verdict_version_numbers():
    new_code = compute_verdict(None, _linear(), _hash(_linear()))
    assert (new_code.next_major, new_code.next_revision) == (1, 1)
    assert new_code.breaking is False

    compatible = _base_def()  # 同 input/searchable，仅改名
    compatible.name = "兼容变更"
    compat_verdict = compute_verdict(_base_def(), compatible, _hash(compatible))
    assert (compat_verdict.next_major, compat_verdict.next_revision) == (1, 2)

    breaking = compute_verdict(_base_def(), _linear(), _hash(_linear()))
    assert (breaking.next_major, breaking.next_revision) == (2, 1)


def test_verdict_override_requires_reason():
    with pytest.raises(ValueError, match="reason"):
        compute_verdict(_base_def(), _linear(), _hash(_linear()), override_kind="allow_minor")

    forced = compute_verdict(
        _base_def(),
        _linear(),
        _hash(_linear()),
        override_kind="force_major",
        override_reason="上游要求",
    )
    assert forced.breaking and forced.override == {"kind": "force_major", "reason": "上游要求"}
    # 无破坏但强制升 major
    compatible = _base_def()
    compatible.name = "兼容"
    forced_minor = compute_verdict(
        _base_def(),
        compatible,
        _hash(compatible),
        override_kind="force_major",
        override_reason="重整版本线",
    )
    assert (forced_minor.next_major, forced_minor.next_revision) == (2, 1)

    allowed = compute_verdict(
        _base_def(),
        _linear(),
        _hash(_linear()),
        override_kind="allow_minor",
        override_reason="字段已废弃不再写",
    )
    assert not allowed.breaking
    assert (allowed.next_major, allowed.next_revision) == (1, 2)


def test_verdict_identical_dedup():
    digest = _hash(_linear())
    verdict = compute_verdict(_linear(), _linear(), digest, published_hashes={(1, 1): digest})
    assert verdict.identical_to == (1, 1)


# —— deploy 集成 ——


def test_deploy_new_code_then_compatible_revision():
    engine = ProcessEngine()
    v1 = engine.repository.deploy(_linear(), published_by="ci")
    assert (v1.next_major, v1.next_revision) == (1, 1)

    revised = _linear()
    revised.name = "兼容修订"
    v2 = engine.repository.deploy(revised, published_by="ci")
    assert (v2.next_major, v2.next_revision) == (1, 2)

    v3 = engine.repository.deploy(_breaking_variant())
    assert (v3.next_major, v3.next_revision) == (2, 1)
    assert v3.breaking
    assert any(r.rule == "R4_topology" for r in v3.triggered_rules)

    events = _deployed_events(engine, DomainEvent.DEFINITION_DEPLOYED.value)
    assert [e["major"] for e in events] == [1, 1, 2]
    assert events[-1]["breaking"] is True and events[0]["is_new_code"] is True


def test_deploy_idempotent_same_content_no_new_version_no_event():
    engine = ProcessEngine()
    definition = _linear()
    first = engine.repository.deploy(definition)
    again = engine.repository.deploy(definition)  # 同内容重复部署
    assert again.identical_to == (first.next_major, first.next_revision)

    with engine.session_factory() as session:
        rows = session.scalars(select(ProcessDefinitionRow)).all()
        assert len(rows) == 1  # 不产生新版本
    assert len(_deployed_events(engine, DomainEvent.DEFINITION_DEPLOYED.value)) == 1  # 不发事件


def test_deploy_override_reason_in_event():
    engine = ProcessEngine()
    engine.repository.deploy(_linear())
    verdict = engine.repository.deploy(_breaking_variant(), allow_minor_reason="灰度回滚窗口")
    assert (verdict.next_major, verdict.next_revision) == (1, 2)
    event = _deployed_events(engine, DomainEvent.DEFINITION_DEPLOYED.value)[-1]
    assert event["override"] == {"kind": "allow_minor", "reason": "灰度回滚窗口"}


def test_deploy_maintenance_branch_on_old_major():
    engine = ProcessEngine()
    engine.repository.deploy(_linear())
    engine.repository.deploy(_breaking_variant())  # → 2.1

    fix = _linear("ver")
    fix.name = "1.x 维护修订"
    verdict = engine.repository.deploy(fix, target_major=1)  # 老 major 续发
    assert (verdict.next_major, verdict.next_revision) == (1, 2)


def test_start_pins_major_revision():
    engine = ProcessEngine()
    engine.repository.deploy(_linear())
    revised = _linear("ver")
    revised.name = "修订"
    engine.repository.deploy(revised)

    pinned = engine.runtime.start_process("ver", start_user="u_s", major=1, revision=1)
    latest = engine.runtime.start_process("ver", start_user="u_s")
    assert engine.runtime.get_instance(pinned).definition_revision == 1
    assert engine.runtime.get_instance(latest).definition_revision == 2


def test_verdict_preview_does_not_persist():
    engine = ProcessEngine()
    engine.repository.deploy(_linear())
    candidate = _linear("ver")
    candidate.name = "预演"
    verdict = engine.repository.verdict(candidate)
    assert (verdict.next_major, verdict.next_revision) == (1, 2)
    with engine.session_factory() as session:
        assert len(session.scalars(select(ProcessDefinitionRow)).all()) == 1  # 未落库
    assert len(_deployed_events(engine, DomainEvent.DEFINITION_DEPLOYED.value)) == 1  # 未发新事件


_ = TriggeredRule  # 导入占位（契约可见性）


def test_contract_tightening_is_breaking():
    """回归（拍板 4 方案 A）：契约收紧纳入 R1 破坏性——从无到有、关闭多余
    字段容忍，两类「新实例行为断变」强制 major（与 required 扩大同构）。
    修复前均判 revision（平滑升级），按老习惯发请求的发起方被静默打破。"""
    # 从无到有：基线无契约 → 新版整套严格契约
    no_contract = _linear("tighten1")
    with_contract = _linear("tighten1")
    with_contract.input = {
        "type": "object",
        "properties": {"amount": {"type": "number"}},
        "required": ["amount"],
    }
    verdict = compute_verdict(no_contract, with_contract, _hash(with_contract))
    assert verdict.breaking is True, [r.detail for r in verdict.triggered_rules]
    assert any(r.rule == "R1_input_contract" for r in verdict.triggered_rules)

    # 关闭多余字段容忍：additionalProperties → false
    loose = _linear("tighten2")
    loose.input = {"type": "object", "properties": {"amount": {"type": "number"}}}
    strict = _linear("tighten2")
    strict.input = {
        "type": "object",
        "properties": {"amount": {"type": "number"}},
        "additionalProperties": False,
    }
    verdict2 = compute_verdict(loose, strict, _hash(strict))
    assert verdict2.breaking is True, [r.detail for r in verdict2.triggered_rules]
    assert any(r.rule == "R1_input_contract" for r in verdict2.triggered_rules)
