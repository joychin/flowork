"""端口（Protocol）：引擎内核与外部能力的边界。

全部端口有默认实现（静态配置/内存/no-op），保证内核可裸跑、可单测。
v2.1 定稿（kernel-v2 spec §9）：OrgDirectory 瘦身为引擎必需四方法——
取人（组→人）＝表达式词汇全部宿主注册（见 flowork.contrib），关系判断（人→组）
与身份定格＝引擎自己的行为，走本端口，方向相反、不可互换。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NamedTuple, Protocol, runtime_checkable

__all__ = [
    "Clock",
    "EventSink",
    "ExpressionFunction",
    "ExpressionFunctions",
    "FunctionSignature",
    "Notifier",
    "OrgDirectory",
    "ResourceProvider",
    "WorkCalendar",
]


@runtime_checkable
class Clock(Protocol):
    """时钟端口：注入以支持超时场景的确定性测试。"""

    def now(self) -> datetime: ...


@runtime_checkable
class WorkCalendar(Protocol):
    """工作日历端口：自然时间与工作时间的换算（默认实现=自然时间）。"""

    def duration_minutes(self, start: datetime, end: datetime) -> int: ...


@runtime_checkable
class OrgDirectory(Protocol):
    """组织目录端口（v2.1 定稿：引擎必需四方法）。

    组引用格式：``role:{name}`` / ``org:{code}``；组展开＝直接成员，不含子部门。
    默认实现＝静态目录（空配置时四方法返回 False/[]/None/{}，裸跑不受影响）。
    """

    def is_member(self, user: str, group: str) -> bool:
        """人→组：候选命中判断（引擎查「张三属不属于这个组」）。"""
        ...

    def subjects_in(self, group: str) -> list[str]:
        """组→人：候选列表展开（直接成员，不含子部门）。"""
        ...

    def manager_of(self, user: str) -> str | None:
        """转上级：超时 escalate 的引擎自动行为（吃 org: 返回部门负责人）。"""
        ...

    def snapshot(self, subject: str) -> dict[str, Any]:
        """身份快照：完成事务内定格操作者当时的岗位/角色/部门（默认 {}）。"""
        ...


@dataclass(frozen=True)
class FunctionSignature:
    """表达式函数签名（P0-1 决策：注册必须带签名，喂给 CEL 静态检查）。

    类型词汇（spec §5.5）：one_user / users / groups / mixed / bool，
    另含 CEL 原生标量 string/number/boolean/list/map。
    label 为中文标签（函数目录树下发/设计器函数树，expression-dataface §5）。
    """

    params: tuple[str, ...]
    returns: str
    label: str = ""


class ExpressionFunction(NamedTuple):
    """一个注册项：签名 + 可调用体（纯读，无副作用——路由决策只属于引擎+条件求值）。"""

    signature: FunctionSignature
    fn: Callable[..., object]


@runtime_checkable
class ExpressionFunctions(Protocol):
    """表达式自定义函数来源（spec §4.3/§5.5）——取人词汇全从这里注册。

    内核注册表出厂为空；宿主经本端口注册，须带签名（参数＋返回类型，
    词汇表 one_user/users/groups/mixed/bool）喂给 CEL 静态检查。
    求值链：EngineConfig.expression_functions → routing → expr.cel（functions= 注入）。
    """

    def functions(self) -> Mapping[str, ExpressionFunction]: ...


@runtime_checkable
class ResourceProvider(Protocol):
    """表单/权限等资源类型的注册与获取（M8/M9 接入口）。"""

    def resource_types(self) -> Mapping[str, type]: ...

    def get(self, resource_type: str, resource_id: str) -> Any | None: ...


@runtime_checkable
class EventSink(Protocol):
    """事件外发（默认=outbox 轮询；webhook 实现挂这）。"""

    def emit(self, envelope: dict[str, object]) -> None: ...


@runtime_checkable
class Notifier(Protocol):
    """通知端口：默认 no-op——事件已落 outbox，消息推送是订阅方。"""

    def notify(self, event: str, payload: Mapping[str, object]) -> None: ...
