"""结构类校验规则（validator 编排件的规则组一，入口 validate_definition 不变）。

变量声明 / 组主体 / 唯一性 / kind 注册 / start-end 结构 / 端点存在 /
出入边完备 / 可达性 / 分支规则 / 开始条件 / 超时动作。
规则函数统一接 ``(definition, registry, error, warning[, org_configured])``。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from flowork.definitions.builtin import (
    KIND_BRANCH,
    KIND_END,
    KIND_START,
    KIND_USER_TASK,
    KIND_WAIT,
    RouteMode,
)

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition
    from flowork.runtime.registry import NodeRegistry

__all__ = [
    "check_advance_policy",
    "check_branch_rules",
    "check_structure",
    "check_variables_and_org",
    "check_wait_nodes",
]

IssueFn = Callable[..., None]


def check_variables_and_org(
    definition: ProcessDefinition,
    error: IssueFn,
    warning: IssueFn,
    *,
    org_configured: bool | None = None,
) -> None:
    """变量声明检查 + 参与者槽位语义 + 组主体与目录配置的关系（spec §9）。"""

    # —— 槽位组合互斥（code-review 2026-10 P2-13）：候选命中走单任务认领制，
    # —— multi_instance（并行/串行会签语义）被静默忽略（item_count=1）——图纸
    # —— 声明的会签形态全部丢失且零告警。设计期拦下并指路两条正路。
    for node in definition.nodes:
        if node.kind != KIND_USER_TASK:
            continue
        mi = getattr(node, "multi_instance", None)
        has_candidates = bool(
            getattr(node, "candidate_users", None) or getattr(node, "candidate_groups", None)
        )
        if mi is not None and has_candidates:
            error(
                "user_task.mi_with_candidates",
                f"节点 {node.code} 同时声明 multi_instance 与候选池（candidate_users/"
                "candidate_groups）：候选命中走单任务认领制，multi_instance 会被忽略——"
                "多人会签请改用 assignees 表达式，候选认领请移除 multi_instance",
                node=node.code,
            )

    for variable in definition.variables:
        if variable.searchable and variable.type not in ("string", "number", "boolean"):
            error(
                "variables.searchable_type",
                f"searchable 变量仅限 string/number/boolean（{variable.name}: {variable.type}）",
                node=variable.name,
            )

    # —— 人员位组引用（第十八轮）：组引用只属于候选槽（spec §5.5「两个槽不可拍平」）
    # —— subjects/reassign 位写 role:/org: 字面量＝想按角色/部门取人写错了位置，
    # 运行期会变成没人能办的死待办；设计期直接拦（ERROR）。
    from flowork.definitions.validator_deploy import string_literals
    from flowork.definitions.validator_positions import iter_expression_positions

    for kind, where, expression, node_code in iter_expression_positions(definition):
        if kind not in ("subjects", "reassign"):
            continue  # candidates 位组引用合法；条件/映射/出口位不是人员语义
        refs = [lit for lit in string_literals(expression) if lit.startswith(("role:", "org:"))]
        if refs:
            error(
                "participant.group_in_subjects",
                f"人员位 {where} 出现组引用 {refs}——组引用只在候选槽"
                "（candidate_groups）有意义；按角色/部门展开请写 "
                "users_with_role(...) / users_in_org(...)，或改用候选池"
                "（认领时展开，spec §5.5）",
                node=node_code,
            )

    if org_configured is False:
        for node in definition.nodes:
            groups = list(getattr(node, "candidate_groups", None) or [])
            # 候选槽是 CEL 表达式（B 批位点统一）——组引用写在字符串字面量里
            # （"'role:finance'"），取字面量再判前缀；string_literals 宿 validator_deploy，
            # 函数内延迟导入避免 validator → rules → deploy → validator 模块环。
            from flowork.definitions.validator_deploy import string_literals

            references = [lit for expr in groups for lit in string_literals(expr)]
            prefixed = [g for g in references if g.startswith(("role:", "org:"))]
            if prefixed:
                warning(
                    "participant.group_without_directory",
                    f"图纸使用组主体 {prefixed} 而组织目录未配置：组待办将无人能认领"
                    "（EngineConfig.org / 目录见 spec §9）",
                    node=node.code,
                )


def check_structure(
    definition: ProcessDefinition,
    registry: NodeRegistry,
    error: IssueFn,
    warning: IssueFn,
) -> None:
    """唯一性 / kind 注册 / start-end 结构 / 端点存在 / 出入边完备 / 可达性。"""
    nodes = {n.code: n for n in definition.nodes}

    # —— 唯一性与编码 ——
    seen: set[str] = set()
    for node in definition.nodes:
        if not node.code:
            error("node.code.empty", "节点编码为空")
        if node.code in seen:
            error("node.code.duplicate", f"节点编码重复：{node.code}", node=node.code)
        seen.add(node.code)

    # —— kind 注册（G2：未知 kind 设计期拦截）——
    for node in definition.nodes:
        if node.kind not in registry:
            error(
                "node.kind.unregistered",
                f"未注册的节点类型 kind={node.kind!r}（已注册：{', '.join(registry.kinds())}）",
                node=node.code,
            )

    # —— start/end 结构 ——
    starts = [n for n in definition.nodes if n.kind == KIND_START]
    ends = [n for n in definition.nodes if n.kind == KIND_END]
    if len(starts) != 1:
        error("structure.start.count", f"必须有且仅有一个 start 节点，当前 {len(starts)} 个")
    if not ends:
        error("structure.end.missing", "至少需要一个 end 节点")

    for node in definition.nodes:
        if node.kind == KIND_START and definition.incoming(node.code):
            error("structure.start.incoming", "start 节点不允许有入边", node=node.code)
        if node.kind == KIND_END and definition.outgoing(node.code):
            error("structure.end.outgoing", "end 节点不允许有出边", node=node.code)

    # —— 连线端点存在性 ——
    for flow in definition.flows:
        for end, role in ((flow.source, "source"), (flow.target, "target")):
            if end not in nodes:
                error(
                    "flow.endpoint.missing",
                    f"连线 {role} 节点不存在：{end}",
                    flow=flow.flow_code(),
                )

    # —— 入边/出边完备性 ——
    for node in definition.nodes:
        if node.kind == KIND_START:
            continue
        if not definition.incoming(node.code):
            warning("structure.node.unreachable", "节点不可达（无入边）", node=node.code)
        if node.kind != KIND_END and not definition.outgoing(node.code):
            error("structure.node.deadend", "非 end 节点必须有出边", node=node.code)

    # —— 从 start 出发的可达性 ——
    if starts:
        reachable: set[str] = set()
        queue = [starts[0].code]
        while queue:
            current = queue.pop()
            if current in reachable:
                continue
            reachable.add(current)
            queue.extend(f.target for f in definition.outgoing(current) if f.target in nodes)
        for node in definition.nodes:
            if node.code not in reachable:
                warning("structure.node.unreachable", "节点从 start 不可达", node=node.code)

    # —— 会签出口字段错位（第十九轮走查发现）：approve_exit/disapprove_exit 写在
    # —— 节点顶层会被 extra 袋静默收下，而行为只读 multi_instance 内同名字段——
    # —— 出口公式零告警失效、静默回退全票。设计期拦下并指路（可见优于静默）。
    for node in definition.nodes:
        if node.kind != KIND_USER_TASK:
            continue
        for field in ("approve_exit", "disapprove_exit"):
            if getattr(node, field, None) is not None:
                error(
                    "user_task.exit.misplaced",
                    f"{field} 写在节点顶层不生效（行为只读 multi_instance 内同名字段）——"
                    f"请放进 multi_instance",
                    node=node.code,
                )


def check_branch_rules(
    definition: ProcessDefinition,
    error: IssueFn,
    warning: IssueFn,
) -> None:
    """条件分支节点的出边规则（默认边/条件完备）。"""
    for node in definition.nodes:
        if node.kind != KIND_BRANCH:
            continue
        route_mode = getattr(node, "route_mode", RouteMode.FIRST_MATCH)
        outgoing = definition.outgoing(node.code)
        defaults = [f for f in outgoing if f.is_default]
        conditional = [f for f in outgoing if f.condition is not None]
        if len(outgoing) < 2:
            warning("branch.single_outgoing", "分支节点出边少于 2 条，仅作透传", node=node.code)
        if len(defaults) > 1:
            error("branch.default.duplicate", "分支节点最多一条默认边", node=node.code)
        for flow in outgoing:
            if flow.is_default and flow.condition is not None:
                error(
                    "branch.default.has_condition",
                    "默认边不允许再挂条件",
                    node=node.code,
                    flow=flow.flow_code(),
                )
        if route_mode == RouteMode.FIRST_MATCH:
            if len(outgoing) > 1 and not defaults and len(conditional) < len(outgoing):
                error(
                    "branch.first_match.no_default",
                    "存在无条件出边且未设置默认边（条件全不满足时无法路由）",
                    node=node.code,
                )
            if outgoing and not defaults and conditional and len(conditional) == len(outgoing):
                warning(
                    "branch.first_match.all_conditional",
                    "出边全部带条件且无默认边：条件不互补时运行期将无法路由",
                    node=node.code,
                )
        else:  # ALL_MATCH：命中的都走
            for flow in outgoing:
                if flow.condition is None and not flow.is_default:
                    warning(
                        "branch.all_match.unconditional",
                        "无条件出边恒为真（等价并行分支，可考虑直接多条无条件出边）",
                        node=node.code,
                        flow=flow.flow_code(),
                    )


def check_advance_policy(
    definition: ProcessDefinition,
    warning: IssueFn,
) -> None:
    """多入边节点的开始条件提示（spec §2.3）。"""
    for node in definition.nodes:
        if len(definition.incoming(node.code)) < 2:
            continue
        policy = getattr(node, "advance_policy", None)
        if policy is None:
            warning(
                "advance.missing",
                "节点有多条入边但未设置开始条件（缺省按「任一到达即开始」执行）",
                node=node.code,
            )
        # 「all 挂条件分支臂」不再告警：v2 §2.3 可达性放行下未命中臂不阻塞
        # （包容网关映射 = branch + all 汇聚，见 spec §2.4）


def check_wait_nodes(definition: ProcessDefinition, error: IssueFn) -> None:
    """等待节点结构规则（spec §7.2 / wait-node.md §6）。"""
    from flowork.expr.cel import FormulaError, collect_references

    nodes = {n.code: n for n in definition.nodes}
    wait_sources = {n.code for n in definition.nodes if n.kind == KIND_WAIT}
    for flow in definition.flows:
        if not flow.condition or flow.source in wait_sources:
            continue
        try:  # 语法问题已由 condition.formula.invalid 报告，此处只管位点
            idents, _functions = collect_references(flow.condition)
        except FormulaError:
            continue
        if "wait" in idents:
            error(
                "condition.wait_source",
                "wait.* 仅等待节点出边条件可用（spec §7.2）",
                flow=flow.flow_code(),
            )
    from flowork.definitions.wait_node import wait_fields

    for node in definition.nodes:
        if node.kind == KIND_WAIT:
            message, timeout = wait_fields(node)
            if message is None and timeout is None:
                error(
                    "wait.mode.required",
                    "等待节点须声明 message 或 timeout 至少其一（双缺=什么都不等）",
                    node=node.code,
                )
            if message is not None and not message.name.strip():
                error("wait.message.name_empty", "等待消息声明缺 name", node=node.code)
            if not definition.outgoing(node.code):
                error("wait.exit.required", "等待节点必须有出边", node=node.code)
            if any(f.target == node.code for f in definition.outgoing(node.code)):
                # 自环（code-review 2026-10 P2-10 便宜解）：等待收尾（finish_wait）
                # 绕过推进死循环守卫——到时/消息命中即无限重入，每轮都落审计与
                # 事件且无故障单。设计期禁止（合法的循环等待请拆多节点并保证
                # 环上有业务退出条件）。
                error(
                    "wait.self_loop",
                    f"等待节点 {node.code} 的出边指回自身：等待收尾不受推进死循环"
                    "守卫覆盖，到时/消息命中即无限重入——请拆分节点并保证环上有"
                    "业务退出条件（计数变量等）",
                    node=node.code,
                )
        if node.kind == KIND_USER_TASK:
            reject_target = getattr(node, "reject_target", None)
            target_node = nodes.get(reject_target) if reject_target else None
            if target_node is not None and target_node.kind == KIND_WAIT:
                error(
                    "wait.reject_target",
                    f"驳回目标不可为等待节点：{reject_target}（驳回复活到「等」上语义不成立）",
                    node=node.code,
                )
