"""随机流程结构生成器（批次 C）：Zeebe RandomProcessGenerator 的 flowork 版。

有界随机：序列 ≤5 块、臂内一层、分支 2 条件臂+默认边。
两段式：先决策结构（_Block 清单，执行人在决策期定死），再发射 FlowBuilder
与执行计划——计划是 (结构, n) 的纯函数；驳回注入走「换 reject 步 +
补 start 重报 + 全量重放」（重放复用同一计划，n 不变故路径确定）。
生成闸：每个定义必须过 assert_valid，不过即生成器 bug（非引擎 bug）。
v1 边界：不生成时限（耦合语义由 knot 世界专测）；驳回只落单人多任务
（会签单人否决≠出口，重放假设不成立）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable
from flowork.definitions.validator import assert_valid
from flowork.runtime.registry import create_registry

MAX_BLOCKS = 5
ARMS = [(60, "金"), (30, "银")]  # 条件臂阈值（n >= 阈值），默认边恒有
N_CHOICES = [0, 10, 30, 40, 60, 70, 100]
USERS = ["u_dev_wu", "u_dev_zheng", "u_dev_liu", "u_cash", "u_chief_acc", "u_cfo", "u_buyer"]
REJECT_PROBABILITY = 0.25
KIND_WEIGHTS = [("task", 45), ("branch", 25), ("cc", 10), ("service", 10), ("call", 10)]
STARTER = "u_dev_wu"


@dataclass
class Step:
    """执行计划步：complete=按人办理；reject=驳回（节点必带 reject_target=start）。"""

    action: str  # complete / reject
    actor: str
    node: str = ""


@dataclass
class Generated:
    seed: int
    n: int
    definitions: dict[str, ProcessDefinition]
    steps: list[Step]
    expects_child: bool


@dataclass
class _Block:
    kind: str
    code: str
    name: str
    actor: str = ""
    rejectable: bool = False
    branch_mode: str = ""
    arm_codes: list[str] = field(default_factory=list)
    arm_actors: list[str] = field(default_factory=list)
    default_code: str = ""
    default_actor: str = ""
    join_code: str = ""
    join_actor: str = ""


def generate(seed: int) -> Generated:
    """按种子生成主定义（及可能的子定义）与确定性执行计划。"""
    gen = _Gen(seed)
    definitions, blocks = gen._build()
    return Generated(
        seed=seed,
        n=gen.n,
        definitions=definitions,
        steps=gen.steps,
        expects_child=any(block.kind == "call" for block in blocks),
    )


def activated_arms(block: _Block, n: int) -> list[str]:
    """分支激活臂（含默认臂兜底）：first_match 取首个命中，all_match 取全部命中。"""
    hits = [
        code
        for (threshold, _name), code in zip(ARMS, block.arm_codes, strict=True)
        if n >= threshold
    ]
    if block.branch_mode == "first_match":
        return hits[:1] or [block.default_code]
    return hits or [block.default_code]


class _Gen:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.n = self.rng.choice(N_CHOICES)
        self.steps: list[Step] = []
        self.counter = 0

    def _id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}_{self.counter}"

    def _pick_kind(self, call_used: bool) -> str:
        total = sum(weight for _, weight in KIND_WEIGHTS)
        roll = self.rng.randint(1, total)
        picked = "task"
        for candidate, weight in KIND_WEIGHTS:
            roll -= weight
            if roll <= 0:
                picked = candidate
                break
        return "task" if picked == "call" and call_used else picked

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
                arm_codes=[self._id("arm") for _ in ARMS],
                arm_actors=[self.rng.choice(USERS) for _ in ARMS],
                default_code=self._id("arm"),
                default_actor=self.rng.choice(USERS),
                join_code=self._id("join"),
                join_actor=self.rng.choice(USERS),
            )
        if kind == "cc":
            return _Block("cc", code, name, actor=self.rng.choice(USERS))
        if kind == "service":
            return _Block("service", code, name)
        return _Block("call", code, name)

    def _build(self) -> tuple[dict[str, ProcessDefinition], list[_Block]]:
        registry = create_registry()
        count = self.rng.randint(1, MAX_BLOCKS)
        call_used = False
        blocks: list[_Block] = []
        for index in range(count):
            kind = self._pick_kind(call_used)
            call_used = call_used or kind == "call"
            blocks.append(self._decide_block(kind, index))

        definitions: dict[str, ProcessDefinition] = {"rand_main": self._emit_main(blocks)}
        if call_used:
            definitions["rand_child"] = self._emit_child()
        for definition in definitions.values():
            assert_valid(definition, registry)  # 生成闸：非法=生成器 bug
        self._plan(blocks)
        self._inject_reject(blocks)
        return definitions, blocks

    # —— 发射 ——

    def _emit_main(self, blocks: list[_Block]) -> ProcessDefinition:
        builder = FlowBuilder("rand_main", f"随机流程 {self.seed}").start("start")
        variables = [ProcessVariable(name="n", type="number", label="档位", sample=self.n)]
        for block in blocks:
            self._emit_block(builder, block)
            if block.kind == "call":
                variables.append(
                    ProcessVariable(name="echo_back", type="number", label="回传", sample=0)
                )
        builder.end("the_end", "结束")
        self._wire(builder, blocks)
        return builder.build().model_copy(update={"variables": variables})

    def _emit_block(self, builder: FlowBuilder, block: _Block) -> None:
        if block.kind == "task":
            extra: dict[str, object] = {"reject_target": "start"} if block.rejectable else {}
            builder.user_task(block.code, block.name, assignee=f"'{block.actor}'", **extra)
        elif block.kind == "branch":
            builder.branch(block.code, block.name, route_mode=block.branch_mode)
            for arm_code, arm_actor in zip(block.arm_codes, block.arm_actors, strict=True):
                builder.user_task(arm_code, f"{block.name}臂", assignee=f"'{arm_actor}'")
            builder.user_task(
                block.default_code, f"{block.name}默认臂", assignee=f"'{block.default_actor}'"
            )
            builder.user_task(
                block.join_code,
                f"{block.name}汇聚签",
                assignee=f"'{block.join_actor}'",
                advance_policy="all",
            )
        elif block.kind == "cc":
            builder.cc_task(block.code, block.name, recipients=[f"'{block.actor}'"])
        elif block.kind == "service":
            builder.service_task(block.code, block.name, action="echo_paid")
        else:
            builder.call_activity(
                block.code,
                block.name,
                process_code="rand_child",
                input_mappings={"child_note": "n"},
                output_mappings={"echo_back": "child_note"},
            )

    def _emit_child(self) -> ProcessDefinition:
        child_actor = self.rng.choice(USERS)
        self.child_actor = child_actor
        return (
            FlowBuilder("rand_child", f"随机子流程 {self.seed}")
            .start()
            .user_task("pick", "子流程办理", assignee=f"'{child_actor}'")
            .end("child_end", "子结束")
            .flow("start", "pick")
            .flow("pick", "child_end")
            .build()
            .model_copy(
                update={
                    "variables": [
                        ProcessVariable(name="child_note", type="number", label="入参", sample=0)
                    ]
                }
            )
        )

    def _wire(self, builder: FlowBuilder, blocks: list[_Block]) -> None:
        previous: list[str] = ["start"]
        for block in blocks:
            if block.kind == "branch":
                for source in previous:
                    builder.flow(source, block.code)
                builder.flow(block.code, block.arm_codes[0], condition=f"n >= {ARMS[0][0]}")
                builder.flow(block.code, block.arm_codes[1], condition=f"n >= {ARMS[1][0]}")
                builder.flow(block.code, block.default_code, default=True)
                for arm in [*block.arm_codes, block.default_code]:
                    builder.flow(arm, block.join_code)
                previous = [block.join_code]
            else:
                for source in previous:
                    builder.connect(source, block.code)
                previous = [block.code]
        for source in previous:
            builder.connect(source, "the_end")

    # —— 执行计划 ——

    def _plan(self, blocks: list[_Block]) -> None:
        self.child_actor = getattr(self, "child_actor", self.rng.choice(USERS))
        for block in blocks:
            if block.kind == "branch":
                for arm_code in activated_arms(block, self.n):
                    actor = self._arm_actor(block, arm_code)
                    self.steps.append(Step("complete", actor, arm_code))
                self.steps.append(Step("complete", block.join_actor, block.join_code))
            elif block.kind == "task":
                self.steps.append(Step("complete", block.actor, block.code))
            elif block.kind == "call":
                self.steps.append(Step("complete", self.child_actor, "pick"))
            # cc 不阻塞、service 进场即完成：无需人工步骤

    def _arm_actor(self, block: _Block, node_code: str) -> str:
        for code, actor in zip(block.arm_codes, block.arm_actors, strict=False):
            if code == node_code:
                return actor
        return block.default_actor

    def _inject_reject(self, blocks: list[_Block]) -> None:
        """驳回注入：随机挑一个可驳任务步换成 reject，补 start 重报后全量重放。"""
        if self.rng.random() >= REJECT_PROBABILITY:
            return
        candidates = [
            index
            for index, step in enumerate(self.steps)
            if step.action == "complete" and self._is_rejectable(blocks, step.node)
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
