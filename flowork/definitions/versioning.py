"""版本判定（kernel-v2 spec §6.3）：破坏性规则、VersionVerdict、内容哈希。

- 每条规则＝注册式检查器（同节点注册表哲学），默认四条（R1–R4）；
- deploy 可 force_major/allow_minor 覆盖（必须带 reason，进事件）；
- 输出 ``VersionVerdict``：进 deploy 响应、事件载荷、日志，AI 可先跑预演（只判定不落库）；
- 内容哈希＝去除生命周期字段（major/revision/status）与画布（layout）后的规范 JSON
  SHA-256——发布不可变 ⇒ 同哈希即同内容（幂等去重依据）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition

__all__ = [
    "BREAKING_RULES",
    "BreakingRule",
    "TriggeredRule",
    "VersionVerdict",
    "compute_verdict",
    "content_hash_of",
]

# 生命周期与展示字段不参与内容身份
_NON_SEMANTIC_KEYS = ("major", "revision", "status", "layout")


@dataclass(frozen=True)
class TriggeredRule:
    """一条被触发的破坏性规则（可解释输出）。"""

    rule: str
    detail: str


@dataclass(frozen=True)
class VersionVerdict:
    """deploy 的版本判定报告（spec §6.3 输出契约）。"""

    next_major: int
    next_revision: int
    breaking: bool
    triggered_rules: list[TriggeredRule] = field(default_factory=list)
    content_hash: str = ""
    identical_to: tuple[int, int] | None = None  # (major, revision)：内容与该已发布版本相同
    override: dict[str, str] | None = None  # {kind: force_major|allow_minor, reason}

    def as_event_payload(self) -> dict[str, Any]:
        return {
            "next_major": self.next_major,
            "next_revision": self.next_revision,
            "breaking": self.breaking,
            "triggered_rules": [{"rule": r.rule, "detail": r.detail} for r in self.triggered_rules],
            "content_hash": self.content_hash,
            "identical_to": list(self.identical_to) if self.identical_to else None,
            "override": self.override,
        }


BreakingRule = Callable[["ProcessDefinition", "ProcessDefinition"], list[str]]
"""检查器：返回违例详情列表（空＝未违例）。base＝已发布基线，target＝待发布。"""


def _rule_input_contract(base: ProcessDefinition, target: ProcessDefinition) -> list[str]:
    """R1：input 契约破坏＝required 扩大、既有字段类型变化（含收窄、enum 缩小）、
    契约从无到有、关闭多余字段容忍（拍板 4 方案 A：两类收紧同属「新实例行为
    断变」——与 required 扩大同构，强制 major）。"""
    base_schema = base.input or {}
    target_schema = target.input or {}
    violations: list[str] = []
    if not base_schema and target_schema.get("properties"):
        # 从无到有：基线什么都收，新版开始拒收不合契约的载荷——发起方行为断变
        violations.append("新增输入契约：基线无契约（任意载荷放行），新版开始校验拒收")
    if not base_schema:
        return violations
    base_required = set(base_schema.get("required") or [])
    target_required = set(target_schema.get("required") or [])
    added = sorted(target_required - base_required)
    if added:
        violations.append(f"required 扩大：{added}")
    if (
        base_schema.get("additionalProperties") is not False
        and target_schema.get("additionalProperties") is False
    ):
        violations.append(
            "关闭多余字段容忍：additionalProperties → false（携带附加字段的既有请求被拒）"
        )
    base_props = base_schema.get("properties") or {}
    target_props = target_schema.get("properties") or {}
    for name in sorted(base_props.keys() & target_props.keys()):
        old_type, new_type = base_props[name].get("type"), target_props[name].get("type")
        if old_type != new_type:
            violations.append(f"字段 {name} 类型变化：{old_type} → {new_type}")
        old_enum, new_enum = base_props[name].get("enum"), target_props[name].get("enum")
        if old_enum and new_enum and set(new_enum) < set(old_enum):
            missing = sorted(set(old_enum) - set(new_enum))
            violations.append(f"字段 {name} enum 收窄：移除 {missing}")
    return violations


def _rule_searchable_index(base: ProcessDefinition, target: ProcessDefinition) -> list[str]:
    """R2：索引契约破坏＝searchable 变量集合变化（宿主检索/报表依赖）。"""
    base_set = {v.name for v in base.variables if v.searchable}
    target_set = {v.name for v in target.variables if v.searchable}
    if base_set == target_set:
        return []
    removed = sorted(base_set - target_set)
    return (
        [f"searchable 集合变化：移除 {removed}"]
        if removed
        else [f"searchable 集合变化：{sorted(target_set - base_set)}"]
    )


def _rule_node_identity(base: ProcessDefinition, target: ProcessDefinition) -> list[str]:
    """R3：节点身份破坏＝已发布节点 code 被删或同 code 节点 kind 改变。"""
    violations: list[str] = []
    base_kinds = {n.code: n.kind for n in base.nodes}
    target_kinds = {n.code: n.kind for n in target.nodes}
    removed = sorted(base_kinds.keys() - target_kinds.keys())
    if removed:
        violations.append(f"已发布节点被删除：{removed}")
    changed = sorted(
        code
        for code in base_kinds.keys() & target_kinds.keys()
        if base_kinds[code] != target_kinds[code]
    )
    if changed:
        violations.append(f"同 code 节点 kind 改变：{changed}")
    return violations


def _rule_topology(base: ProcessDefinition, target: ProcessDefinition) -> list[str]:
    """R4：拓扑语义破坏＝任一节点入边集合变化。"""
    violations: list[str] = []
    codes = sorted({n.code for n in base.nodes} | {n.code for n in target.nodes})
    for code in codes:
        base_in = {f.source for f in base.incoming(code)}
        target_in = {f.source for f in target.incoming(code)}
        if base_in != target_in:
            violations.append(f"节点 {code} 入边变化：{sorted(base_in)} → {sorted(target_in)}")
    return violations


BREAKING_RULES: dict[str, BreakingRule] = {
    "R1_input_contract": _rule_input_contract,
    "R2_searchable_index": _rule_searchable_index,
    "R3_node_identity": _rule_node_identity,
    "R4_topology": _rule_topology,
}


def content_hash_of(content: dict[str, Any]) -> str:
    """内容哈希：剥离非语义字段后的规范 JSON（sort_keys）SHA-256。"""
    semantic = {k: v for k, v in content.items() if k not in _NON_SEMANTIC_KEYS}
    canonical = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_verdict(
    base: ProcessDefinition | None,
    target: ProcessDefinition,
    content_hash: str,
    *,
    published_hashes: dict[tuple[int, int], str] | None = None,
    override_kind: str | None = None,
    override_reason: str = "",
) -> VersionVerdict:
    """计算版本判定（纯函数，不落库——预演与 deploy 共用）。

    Args:
        base: 同 code 的已发布基线（维护分支时为该 major 的最新 revision）；None＝新 code。
        content_hash: 待发布内容哈希。
        published_hashes: {(major, revision): hash}——命中即幂等去重（identical_to）。
        override_kind: force_major / allow_minor（必须带 reason）。
    """
    identical_to = next(
        (
            version
            for version, published in (published_hashes or {}).items()
            if published == content_hash
        ),
        None,
    )
    if identical_to is not None:
        return VersionVerdict(
            next_major=identical_to[0],
            next_revision=identical_to[1],
            breaking=False,
            content_hash=content_hash,
            identical_to=identical_to,
        )

    triggered: list[TriggeredRule] = []
    if base is not None:
        for name, rule in BREAKING_RULES.items():
            for detail in rule(base, target):
                triggered.append(TriggeredRule(rule=name, detail=detail))

    breaking = bool(triggered)
    override: dict[str, str] | None = None
    if override_kind:
        if not override_reason:
            raise ValueError("覆盖破坏性判定必须带 reason（spec §6.3）")
        override = {"kind": override_kind, "reason": override_reason}
        if override_kind == "force_major":
            breaking = True
        elif override_kind == "allow_minor":
            breaking = False

    if base is None:
        next_major, next_revision = 1, 1
    elif breaking:
        next_major, next_revision = base.major + 1, 1
    else:
        next_major, next_revision = base.major, base.revision + 1
    return VersionVerdict(
        next_major=next_major,
        next_revision=next_revision,
        breaking=breaking,
        triggered_rules=triggered,
        content_hash=content_hash,
        override=override,
    )
