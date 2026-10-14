"""随机流程结构生成器（批次 C 起源；测试套件完善批 3/5 词汇扩容）。

块词汇 9 种：task / branch（2 条件臂+默认，臂可 user|cc，汇聚 all|plain——
plain 触发迟到到达吸收）/ cc / service / call（子可含分流）/ countersign
（比例会签）/ mi_call（subjects 多子实例+collect）/ wait（时长|消息）。
两段式：先决策结构（_Block 清单，执行人在决策期定死），再发射（random_emit）
与执行计划——计划是 (结构, n) 的纯函数；驳回注入走「换 reject 步 +
补 start 重报 + 全量重放」（重放复用同一计划，n 不变故路径确定）。
生成闸：每个定义必须过 assert_valid，不过即生成器 bug（非引擎 bug）。
v1 边界：不生成时限（耦合语义由 knot 世界专测）；驳回只落单人多任务
（会签单人否决≠出口，重放假设不成立）；wait 无条件单出边（竞速路由后续）；
会签只投同意票（出口达成后余票 auto_pass 归档，计划按 ceil 截断）；
call 族块至多一个（子定义单一形状）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from flowork.definitions.definition import ProcessDefinition
from flowork.definitions.validator import assert_valid
from flowork.runtime.registry import create_registry
from tests.suite.random_blocks import Step, _Block, _ChildShape, activated_arms
from tests.suite.random_emit import emit_definitions

MAX_BLOCKS = 5
N_CHOICES = [0, 10, 30, 40, 60, 70, 100]
USERS = ["u_dev_wu", "u_dev_zheng", "u_dev_liu", "u_cash", "u_chief_acc", "u_cfo", "u_buyer"]
REJECT_PROBABILITY = 0.25
KIND_WEIGHTS = [
    ("task", 28),
    ("branch", 16),
    ("countersign", 12),
    ("mi_call", 10),
    ("wait", 10),
    ("cc", 8),
    ("service", 8),
    ("call", 8),
]
STARTER = "u_dev_wu"

__all__ = ["STARTER", "Generated", "Step", "generate"]


@dataclass
class Generated:
    seed: int
    n: int
    definitions: dict[str, ProcessDefinition]
    steps: list[Step]
    min_children: int  # 子实例数下限（驳回重放只会更多）
    has_plain_call: bool
    collected_subjects: list[str]  # mi_call collect 断言用（== subjects 序）


def generate(seed: int) -> Generated:
    """按种子生成主定义（及可能的子定义）与确定性执行计划。"""
    gen = _Gen(seed)
    definitions, blocks = gen._build()
    return Generated(
        seed=gen.seed,
        n=gen.n,
        definitions=definitions,
        steps=gen.steps,
        min_children=sum(len(b.subjects) for b in blocks if b.kind == "mi_call")
        + (1 if any(b.kind == "call" for b in blocks) else 0),
        has_plain_call=any(b.kind == "call" for b in blocks),
        collected_subjects=[
            subject for b in blocks if b.kind == "mi_call" for subject in b.subjects
        ],
    )


class _Gen:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.n = self.rng.choice(N_CHOICES)
        self.steps: list[Step] = []
        self.counter = 0
        self.child_shape: _ChildShape | None = None

    def _id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}_{self.counter}"

    def _pick_kind(self, child_used: bool) -> str:
        total = sum(weight for _, weight in KIND_WEIGHTS)
        roll = self.rng.randint(1, total)
        picked = "task"
        for candidate, weight in KIND_WEIGHTS:
            roll -= weight
            if roll <= 0:
                picked = candidate
                break
        return "task" if picked in ("call", "mi_call") and child_used else picked

    def _decide_block(self, kind: str, index: int) -> _Block:
        code = f"n{index + 1}"
        name = f"节点{index + 1}"
        if kind == "task":
            return _Block(
                "task",
                code,
                name,
                actor=self.rng.choice(USERS),
                rejectable=self.rng.random() < 0.35,
            )
        if kind == "branch":
            return _Block(
                "branch",
                code,
                name,
                branch_mode=self.rng.choice(["first_match", "all_match"]),
                sink_all=self.rng.random() < 0.6,
                arm_codes=[self._id("arm") for _ in range(2)],
                arm_kinds=[self.rng.choice(["user", "user", "cc"]) for _ in range(2)],
                arm_actors=[self.rng.choice(USERS) for _ in range(2)],
                default_code=self._id("arm"),
                default_actor=self.rng.choice(USERS),
                join_code=self._id("join"),
                join_actor=self.rng.choice(USERS),
            )
        if kind == "cc":
            return _Block("cc", code, name, actor=self.rng.choice(USERS))
        if kind == "service":
            return _Block("service", code, name)
        if kind == "countersign":
            return _Block(
                "countersign",
                code,
                name,
                members=self.rng.sample(USERS, self.rng.choice([2, 3])),
                ratio=self.rng.choice([50, 60, 100]),
            )
        if kind == "mi_call":
            return _Block(
                "mi_call", code, name, subjects=self.rng.sample(USERS, self.rng.choice([2, 3]))
            )
        if kind == "wait":
            mode = self.rng.choice(["duration", "message"])
            return _Block(
                "wait",
                code,
                name,
                wait_mode=mode,
                after_minutes=self.rng.randint(3, 5) if mode == "duration" else 0,
                message_name=f"m_{code}" if mode == "message" else "",
            )
        return _Block("call", code, name)

    def _decide_child(self) -> _ChildShape:
        if self.rng.random() < 0.4:  # 嵌套分流：子定义空间翻倍
            return _ChildShape(
                branch=True,
                branch_mode=self.rng.choice(["first_match", "all_match"]),
                arm_codes=[self._id("c_arm") for _ in range(2)],
                arm_actors=[self.rng.choice(USERS) for _ in range(2)],
                default_code=self._id("c_arm"),
                default_actor=self.rng.choice(USERS),
                join_code=self._id("c_join"),
                join_actor=self.rng.choice(USERS),
            )
        return _ChildShape(branch=False, actor=self.rng.choice(USERS))

    def _build(self) -> tuple[dict[str, ProcessDefinition], list[_Block]]:
        registry = create_registry()
        count = self.rng.randint(1, MAX_BLOCKS)
        child_used = False
        blocks: list[_Block] = []
        for index in range(count):
            kind = self._pick_kind(child_used)
            child_used = child_used or kind in ("call", "mi_call")
            blocks.append(self._decide_block(kind, index))
        if child_used:
            self.child_shape = self._decide_child()

        definitions = emit_definitions(blocks, self.child_shape, self.seed, self.n)
        for definition in definitions.values():
            assert_valid(definition, registry)  # 生成闸：非法=生成器 bug
        self._plan(blocks)
        self._inject_reject(blocks)
        return definitions, blocks

    # —— 执行计划（(结构, n) 的纯函数）——

    def _plan(self, blocks: list[_Block]) -> None:
        for block in blocks:
            if block.kind == "task":
                self.steps.append(Step("complete", block.actor, block.code))
            elif block.kind == "branch":
                kinds = dict(zip(block.arm_codes, block.arm_kinds, strict=True))
                actors = {
                    **dict(zip(block.arm_codes, block.arm_actors, strict=True)),
                    block.default_code: block.default_actor,
                }
                activated = activated_arms(
                    block.arm_codes, block.default_code, block.branch_mode, self.n
                )
                user_arms = [arm for arm in activated if kinds.get(arm, "user") == "user"]
                if block.sink_all:
                    for arm in user_arms:  # all 汇聚：激活臂逐一办完（cc 臂穿透免步）
                        self.steps.append(Step("complete", actors[arm], arm))
                elif user_arms and not any(kinds.get(arm) == "cc" for arm in activated):
                    # plain 汇聚=any 竞速：首个到达即取消兄弟——有人工臂时第一个人工
                    # 臂夺冠；有 cc 臂时它在扇出内即时夺冠（人工臂任务已被竞速收尾）
                    self.steps.append(Step("complete", actors[user_arms[0]], user_arms[0]))
                self.steps.append(Step("complete", block.join_actor, block.join_code))
            elif block.kind == "countersign":
                quorum = -(-len(block.members) * block.ratio // 100)  # ceil：出口所需票数
                for member in block.members[:quorum]:
                    self.steps.append(Step("complete", member, block.code))
            elif block.kind == "call":
                self.steps.extend(self._child_steps())
            elif block.kind == "mi_call":
                for _subject in block.subjects:
                    self.steps.extend(self._child_steps())
            elif block.kind == "wait":
                if block.wait_mode == "duration":
                    self.steps.append(Step("advance", "", minutes=block.after_minutes))
                else:
                    self.steps.append(Step("publish", "", message=block.message_name))
            # cc/service 不阻塞：无需人工步骤

    def _child_steps(self) -> list[Step]:
        shape = self.child_shape
        assert shape is not None
        if not shape.branch:
            return [Step("complete", shape.actor, "pick", scope="child")]
        steps = [
            Step("complete", actor, arm, scope="child")
            for arm, actor in zip(shape.arm_codes, shape.arm_actors, strict=True)
            if arm in activated_arms(shape.arm_codes, shape.default_code, shape.branch_mode, self.n)
        ]
        default_hit = activated_arms(shape.arm_codes, shape.default_code, shape.branch_mode, self.n)
        if shape.default_code in default_hit:
            steps.append(Step("complete", shape.default_actor, shape.default_code, scope="child"))
        steps.append(Step("complete", shape.join_actor, shape.join_code, scope="child"))
        return steps

    def _inject_reject(self, blocks: list[_Block]) -> None:
        """驳回注入：随机挑一个可驳任务步换成 reject，补 start 重报后全量重放。"""
        if self.rng.random() >= REJECT_PROBABILITY:
            return
        candidates = [
            index
            for index, step in enumerate(self.steps)
            if step.action == "complete"
            and step.scope == "main"
            and self._is_rejectable(blocks, step.node)
        ]
        if not candidates:
            return
        target = self.rng.choice(candidates)
        rejected = self.steps[target]
        replay = [*self.steps]
        self.steps = [
            *self.steps[:target],
            Step("reject", rejected.actor, rejected.node),
            Step("complete", STARTER, "start"),
            *replay,
        ]

    def _is_rejectable(self, blocks: list[_Block], node_code: str) -> bool:
        return any(
            block.kind == "task" and block.rejectable and block.code == node_code
            for block in blocks
        )
