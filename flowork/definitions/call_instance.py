"""多实例子流程配置。

Zeebe inputCollection/outputCollection 契约：subjects 逐个解析＝每人一个子实例；
输出按 index 对齐聚合（未写 null 占位）；空集合＝节点立即完成（等价 skip）。
completion 只做「提前收工」（公式命中终止剩余 running 子），不做失败机制。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["CallInstanceConfig"]


class CallInstanceConfig(BaseModel):
    """call_activity.multi_instance。"""

    model_config = ConfigDict(extra="forbid")

    # 取人表达式（CEL，复用 resolver：查无传播/上限护栏）；每人一个子实例
    subjects: list[str] = Field(default_factory=list)
    # "all"（缺省＝全员结算）| CEL 布尔公式（注入 child_total/child_completed/child_failed）
    completion: str = "all"
    # collect＝index 对齐聚合 list（缺省）/ merge＝逐子覆写（仅限单子，validator 拦 MI+merge）
    output_mode: str = "collect"
