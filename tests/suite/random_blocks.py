"""随机结构共享件：块/子形状/计划步数据类 + 条件臂常量（gen 与 emit 分居的共同依赖）。"""

from __future__ import annotations

from dataclasses import dataclass, field

# 条件臂阈值（n >= 阈值激活；默认边兜底恒有）
ARMS = [(60, "金"), (30, "银")]


@dataclass
class Step:
    """执行计划步：complete/reject 按人办理；advance 推钟+tick；publish 发消息。

    scope：main=主实例；child=任一运行中子实例（按节点码找待办——MI 多子
    并行时顺序无关，只保收敛不保指派）。
    """

    action: str
    actor: str
    node: str = ""
    scope: str = "main"
    minutes: int = 0  # advance：推进分钟数
    message: str = ""  # publish：消息名


@dataclass
class _Block:
    kind: str
    code: str
    name: str
    actor: str = ""
    rejectable: bool = False
    # branch：2 条件臂+默认臂；臂内容 user|cc；汇聚 all|plain（plain 触发迟到到达吸收）
    branch_mode: str = ""
    sink_all: bool = True
    arm_codes: list[str] = field(default_factory=list)
    arm_kinds: list[str] = field(default_factory=list)
    arm_actors: list[str] = field(default_factory=list)
    default_code: str = ""
    default_actor: str = ""
    join_code: str = ""
    join_actor: str = ""
    # countersign：比例会签（计划只投同意票，余票出口达成后 auto_pass）
    members: list[str] = field(default_factory=list)
    ratio: int = 100
    # mi_call：subjects 多子实例 + collect 聚合
    subjects: list[str] = field(default_factory=list)
    # wait：时长模式推钟，消息模式发布
    wait_mode: str = ""
    after_minutes: int = 0
    message_name: str = ""


@dataclass
class _ChildShape:
    """子定义形状：平铺 pick 或 first_match 分流（复用主流程阈值）。"""

    branch: bool
    actor: str = ""
    branch_mode: str = ""
    arm_codes: list[str] = field(default_factory=list)
    arm_actors: list[str] = field(default_factory=list)
    default_code: str = ""
    default_actor: str = ""
    join_code: str = ""
    join_actor: str = ""


def activated_arms(arm_codes: list[str], default_code: str, branch_mode: str, n: int) -> list[str]:
    """分支激活臂（含默认臂兜底）：first_match 取首个命中，all_match 取全部命中。"""
    hits = [
        code for (threshold, _name), code in zip(ARMS, arm_codes, strict=True) if n >= threshold
    ]
    if branch_mode == "first_match":
        return hits[:1] or [default_code]
    return hits or [default_code]
