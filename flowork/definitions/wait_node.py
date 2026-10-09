"""等待节点定义（spec §7.2，wait-node.md v3）：等消息 / 等时长 / 两者竞速。

从 builtin.py 拆出独立模块（单文件 ≤300 行红线）。三模式合一的行业先例是
Conductor WAIT 任务（定长延迟 / 等到某时刻 / 等外部信号）；等待与关联对标
Zeebe receive task（订阅式关联），竞速的超时侧对标 Step Functions 回调 +
TimeoutSeconds。周期性触发明确不做（属定时启动调度域，用户 2026-10-09 裁定）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from flowork.definitions.node import FlowNode

if TYPE_CHECKING:
    from flowork.runtime.registry import NodeRegistry

__all__ = [
    "KIND_WAIT",
    "WAIT_UI",
    "WaitMessage",
    "WaitSpec",
    "WaitTimeout",
    "register_wait_node",
    "wait_fields",
]

KIND_WAIT: Literal["wait"] = "wait"


class WaitMessage(BaseModel):
    """订阅声明：name=消息契约名（静态，权威在生产方）；correlation_key 为
    CEL 表达式，进入节点时求值一次并快照（订阅期内变量变化不更新，Zeebe 同
    语义），结果须 string；缺省=仅按消息名匹配。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    correlation_key: str | None = None


class WaitTimeout(BaseModel):
    """时间侧：进入起算的相对时长（工作日历折算，暂停冻结恢复顺延）。
    纯延时模式它就是等待时长本身；与 message 同时声明即二元竞速。
    「等到指定时刻」（绝对时间/CEL 日期表达式）后置；周期形态不支持。"""

    model_config = ConfigDict(extra="forbid")

    after_minutes: int = Field(gt=0)


class WaitSpec(FlowNode):
    """等待节点（内置第八种 kind）：先到者决定走向，结果词汇 wait.*（仅出边条件）。"""

    kind: Literal["wait"] = KIND_WAIT
    message: WaitMessage | None = None
    # 载荷→变量映射（spec §7 原文「与人工任务完成的写入路径同一条」）：
    # 右侧表达式于消息上下文（payload.* + now）求值，缺键得 None；
    # 未声明 → 全量合并兜底（Zeebe 默认同款）。
    output_mappings: dict[str, str] = Field(default_factory=dict)
    timeout: WaitTimeout | None = None


# 设计器 UI 元数据（与内置七种同款结构；palette 两预设=三模式中的两常用形态）
WAIT_UI: dict[str, Any] = {
    "name": "等待",
    "category": "事件",
    "shape": "rect",
    "color": "#0284c7",
    "icon": "clock",
    "palette": [
        {"label": "等待消息", "defaults": {"message": {"name": ""}}},
        {"label": "延时等待", "defaults": {"timeout": {"after_minutes": 60}}},
    ],
    "form_groups": [
        {"title": "基础", "fields": ["name", "message", "output_mappings"]},
        {"title": "时间", "fields": ["timeout"], "collapsed": True},
    ],
}


def register_wait_node(registry: NodeRegistry) -> None:
    """注册等待节点（由 register_builtin_nodes 统一调用）。"""
    registry.register(KIND_WAIT, WaitSpec, ui=WAIT_UI)


def wait_fields(node: Any) -> tuple[WaitMessage | None, WaitTimeout | None]:
    """从节点取 (message, timeout)——兼容 bare FlowNode 的 dict 薄形态。

    FlowBuilder 不带 registry 的 build 产出基类节点（部署规范化 round-trip 前），
    校验器在该形态下也要给出准确规则，故此处统一升格为模型再判。
    """
    raw_message = getattr(node, "message", None)
    raw_timeout = getattr(node, "timeout", None)
    message = (
        WaitMessage.model_validate(raw_message) if isinstance(raw_message, dict) else raw_message
    )
    timeout = (
        WaitTimeout.model_validate(raw_timeout) if isinstance(raw_timeout, dict) else raw_timeout
    )
    return message, timeout
