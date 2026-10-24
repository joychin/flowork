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
    KIND_USER_TASK,
    RouteMode,
)

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition

__all__ = [
    "check_advance_policy",
    "check_branch_rules",
    "check_structure",
    "check_variables_and_org",
    "check_wait_nodes",
]

from flowork.definitions.validator_structure import check_structure as check_structure
from flowork.definitions.validator_wait_rules import check_wait_nodes as check_wait_nodes

IssueFn = Callable[..., None]


def check_variables_and_org(
    definition: ProcessDefinition,
    error: IssueFn,
    warning: IssueFn,
    *,
    org_configured: bool | None = None,
) -> None:
    """变量声明检查 + 参与者槽位语义 + 组主体与目录配置的关系。"""

    # —— 槽位组合互斥：候选命中走单任务认领制，
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

    # —— 人员位组引用：组引用只属于候选槽（「两个槽不可拍平」）
    # —— subjects/reassign 位写 role:/org: 字面量＝想按角色/部门取人写错了位置，
    # 运行期会变成没人能办的死待办；设计期直接拦（ERROR）。
    from flowork.definitions.validator_deploy import string_literals
    from flowork.definitions.validator_positions import iter_expression_positions

    for kind, where, expression, node_code in iter_expression_positions(definition):
        if kind not in ("subjects", "reassign"):
            continue  # candidates 位组引用合法；条件/映射/出口位不是人员语义
        # manager_of 的参数位放行：词汇契约明文「吃 org: 返回部门负责人」
        # （人员位按部门负责人取人唯一写法）；展开型词汇参数带前缀仍是错写，照拦。
        refs = [
            lit
            for lit in string_literals(expression, outside_calls=("manager_of",))
            if lit.startswith(("role:", "org:"))
        ]
        if refs:
            error(
                "participant.group_in_subjects",
                f"人员位 {where} 出现组引用 {refs}——组引用只在候选槽"
                "（candidate_groups）有意义；按角色/部门展开请写 "
                "users_with_role(...) / users_in_org(...)，或改用候选池"
                "（认领时展开）",
                node=node_code,
            )

    # —— 候选组必须显式前缀：判定侧
    # —— 裸名只按人解释，组槽位写裸组名会静默失效（无人能认领）——设计期拦下。
    from flowork.definitions.validator_deploy import string_literals as _sl

    for node in definition.nodes:
        groups = list(getattr(node, "candidate_groups", None) or [])
        references = [lit for expr in groups for lit in _sl(expr)]
        bare = [g for g in references if not g.startswith(("role:", "org:"))]
        if bare:
            error(
                "candidate_groups.bare_reference",
                f"candidate_groups 的组引用必须带 role:/org: 前缀：{bare}"
                "（裸名只按人解释——「角色就是角色，人名就是人名」；"
                "函数返回的组引用同样须自带前缀）",
                node=node.code,
            )

    if org_configured is False:
        for node in definition.nodes:
            groups = list(getattr(node, "candidate_groups", None) or [])
            # 候选槽是 CEL 表达式——组引用写在字符串字面量里
            # （"'role:finance'"），取字面量再判前缀；string_literals 宿 validator_deploy，
            # 函数内延迟导入避免 validator → rules → deploy → validator 模块环。
            from flowork.definitions.validator_deploy import string_literals

            references = [lit for expr in groups for lit in string_literals(expr)]
            prefixed = [g for g in references if g.startswith(("role:", "org:"))]
            if prefixed:
                warning(
                    "participant.group_without_directory",
                    f"图纸使用组主体 {prefixed} 而组织目录未配置：组待办将无人能认领"
                    "（配置 EngineConfig.org）",
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
    """多入边节点的开始条件提示。"""
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
        # 「all 挂条件分支臂」不再告警：可达性放行下未命中臂不阻塞
        # （包容网关映射 = branch + all 汇聚）
