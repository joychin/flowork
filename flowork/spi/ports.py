"""端口（Protocol）：引擎内核与外部能力的边界。

全部端口有默认实现（静态配置/内存/no-op），保证内核可裸跑、可单测。
引擎消费的目录查询面＝``participants.org_directory.OrgDirectoryLike``（前四个
方法为引擎必需最小面；spec §9 曾另行声明的四方法 OrgDirectory 协议全仓零消费，
随 2026-10-12 死符号收口批删除）。

**死符号收口（2026-10-12）**：Clock 删除——可注入时钟由 `EngineConfig.now`
（裸 `Callable[[], datetime]`）承载，全仓零实现零注解消费；EventSink 删除——
outbox 消费者接收裸 `Callable[[envelope], None]`，webhook 落地时按实际形状重声明；
OrgDirectory（四方法协议）删除——与 OrgDirectoryLike 概念重复且零消费。
历史决策见 docs/product/kernel-v2-plan.md、spec §9。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import NamedTuple, Protocol, runtime_checkable

__all__ = [
    "ExpressionFunction",
    "ExpressionFunctions",
    "FunctionSignature",
]


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
