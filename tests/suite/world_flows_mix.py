"""套件·混用世界族：找人函数 × CEL × 环境变量（两条命题主线）。

用户命题的落点：① 找人函数与 CEL、环境变量混用；② 连线规则与找人函数、
环境变量混用。三个定义按「由简到繁」组织，与 `test_participant_mix.py` /
`test_route_mix.py` 的走查一一对应。

- ``suite_participant_ladder``：取人位复杂度阶梯——字面量 → 单函数 → 函数吃
  变量 → 索引/拼接/comprehension/三元 → 吃上一站与点名节点事实 → 纯 CEL 拼接。
  十一站站站相连（每站单一办理人，保证 prev_task 确定），一次走到底。
- ``suite_participant_void``：查无传播与无参与者兜底（`manager_of` 查无 →
  null → 空贡献；to_admins 有名单则兜底、无名单则拒绝）。
- ``suite_route_mix``：连线规则 × 找人函数 × 环境变量——两级闸门，第一级
  first_match 三臂（事实+函数+变量三方合一 / 函数列表成员+实例事实 / 默认臂吃
  变量），第二级 all_match 双臂（函数取人 + 事实条件）汇入 all 汇聚。

人设沿用 `world_org`（华信智造）：u_dev_wu（后端组）/ u_lead_be（后端组主管）/
u_cto（研发中心主管，dept_manager）/ u_purch_head（采购主管，dept_manager）/
u_chief_acc·u_cash（财务审批人）/ u_ceo（CEO）。
"""

from __future__ import annotations

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessDefinition, ProcessVariable

# 阶梯定义引用的变量（role_suffix 供纯 CEL 拼接档）
_LADDER_VARIABLES = [
    ProcessVariable(name="duty_role", type="string", label="经办角色", sample="finance_approver"),
    ProcessVariable(name="role_suffix", type="string", label="角色后缀", sample="ceo"),
]

# 路由世界的变量：金额参与连线条件与事实臂，duty_role 只在取人位用
_ROUTE_VARIABLES = [
    ProcessVariable(name="amount", type="number", label="金额", sample=9000),
    ProcessVariable(name="duty_role", type="string", label="经办角色", sample="ceo"),
]


def participant_ladder_definition() -> ProcessDefinition:
    """取人位复杂度阶梯：十一站，站站相连（表达式的复杂度只往前走）。"""
    builder = (
        FlowBuilder("suite_participant_ladder", "套件·取人阶梯")
        .start()
        # 1 字面量（基线）
        .user_task("n_literal", "① 字面量", assignee="'u_ceo'")
        # 2 单函数
        .user_task("n_manager", "② 上级函数", assignee="manager_of(initiator)")
        # 3 函数吃变量（环境变量 → 人）
        .user_task("n_role_var", "③ 角色取自变量", assignee="users_with_role(duty_role)")
        # 4 函数结果 + CEL 索引
        .user_task("n_index", "④ 索引取首人", assignee='users_with_role("finance_approver")[0]')
        # 5 函数结果 + 列表拼接（去重保序）
        .user_task(
            "n_concat",
            "⑤ 拼接上级",
            assignee='users_with_role("finance_approver") + [manager_of(initiator)]',
        )
        # 6 函数结果 + comprehension 宏
        .user_task(
            "n_filter",
            "⑥ 过滤发起人",
            assignee='users_with_role("finance_approver").filter(u, u != initiator)',
        )
        # 7 谓词函数 + 三元（两边各一个函数）
        .user_task(
            "n_ternary",
            "⑦ 谓词三元",
            assignee=(
                'has_role(initiator, "ceo") == true '
                '? manager_of(initiator) : users_with_role("finance_approver")[0]'
            ),
        )
        # 8 函数吃上一站事实
        .user_task("n_prev_person", "⑧ 上一站的上级", assignee="manager_of(prev_task.assignee)")
        # 9 变量 × 函数 × 事实 三合一
        .user_task(
            "n_prev_role",
            "⑨ 角色减去上一站",
            assignee="users_with_role(duty_role).filter(u, u != prev_task.assignee)",
        )
        # 10 函数吃点名节点事实
        .user_task(
            "n_node_person",
            "⑩ 点名节点的上级",
            assignee='manager_of(node("n_manager").assignee)',
        )
        # 11 纯 CEL 字符串拼接（变量拼人名）
        .user_task("n_strcat", "⑪ 拼接变量成名", assignee="'u_' + role_suffix")
        .end("ladder_end", "阶梯走完")
    )
    chain = [
        "start",
        "n_literal",
        "n_manager",
        "n_role_var",
        "n_index",
        "n_concat",
        "n_filter",
        "n_ternary",
        "n_prev_person",
        "n_prev_role",
        "n_node_person",
        "n_strcat",
        "ladder_end",
    ]
    for index in range(len(chain) - 1):
        builder.connect(chain[index], chain[index + 1])
    return builder.build().model_copy(update={"variables": _LADDER_VARIABLES})


def participant_void_definition() -> ProcessDefinition:
    """查无传播与兜底：前站办完 → 取人公式查无 → 空贡献（兜底名单决定生死）。"""
    return (
        FlowBuilder("suite_participant_void", "套件·取人查无")
        .start()
        .user_task("void_up", "前站", assignee="'u_dev_wu'")
        .user_task(
            "void_ghost",
            "查无兜底",
            assignee="manager_of('u_nobody')",
            no_participant_policy="to_admins",
        )
        .end("void_end", "结束")
        .connect("start", "void_up")
        .connect("void_up", "void_ghost")
        .connect("void_ghost", "void_end")
        .build()
    )


def route_mix_definition() -> ProcessDefinition:
    """连线规则 × 找人函数 × 环境变量：两级闸门，一级择一、二级汇聚。

    第一级（first_match）三臂——条件里同时出现事实（prev_task/instance）、
    函数（has_role/users_with_role）与变量（amount）；三臂的取人也各不相同
    （函数吃发起人 / 函数列表 / 函数吃变量）。
    第二级（all_match）两条件臂 + 默认臂——条件建立在函数、变量、点名节点事实
    （node("intake").runs）与实例事实上；两条件臂都命中即并行开工，
    sink（advance_policy=all）等齐收口，都不命中落默认臂直连终点。
    """
    builder = (
        FlowBuilder("suite_route_mix", "套件·连线混用")
        .start()
        .user_task("intake", "收件", assignee="'u_purch_head'")
        .branch("g_stage1", "一级闸门")
        # 臂一：事实 + 函数 + 变量 三方合一 → 取人＝函数吃发起人
        .user_task(
            "arm_fact",
            "事实臂",
            assignee="manager_of(initiator)",
            reject_target="start",
        )
        # 臂二：函数列表成员 + 实例事实 → 取人＝函数列表
        .user_task("arm_role", "角色臂", assignee='users_with_role("finance_approver")')
        # 臂三：默认臂 → 取人＝函数吃变量
        .user_task("arm_var", "变量臂", assignee="users_with_role(duty_role)")
        .branch("g_stage2", "二级闸门", route_mode="all_match")
        # 二级臂一：谓词函数 + 变量 + 点名节点事实
        .user_task(
            "arm_by_role",
            "二级角色臂",
            assignee='users_with_role("finance_approver").filter(u, u != initiator)',
        )
        # 二级臂二：实例事实 + 函数 + 变量
        .user_task(
            "arm_by_fact",
            "二级事实臂",
            assignee="manager_of(initiator)",
        )
        .service_task("sink", "收口", action="echo_paid", advance_policy="all")
        .end("route_end", "流程结束")
        .connect("start", "intake")
        .connect("intake", "g_stage1")
        .flow(
            "g_stage1",
            "arm_fact",
            condition='has_role(prev_task.assignee, "dept_manager") == true && amount >= 5000',
        )
        .flow(
            "g_stage1",
            "arm_role",
            condition=(
                'initiator in users_with_role("dept_manager") || instance.business_key == "VIP"'
            ),
        )
        .flow("g_stage1", "arm_var", default=True)
        .connect("arm_fact", "g_stage2")
        .connect("arm_role", "g_stage2")
        .connect("arm_var", "g_stage2")
        .flow(
            "g_stage2",
            "arm_by_role",
            condition=(
                'has_role(initiator, "dept_manager") == true && amount >= 5000 '
                '&& node("intake").runs >= 1'
            ),
        )
        .flow(
            "g_stage2",
            "arm_by_fact",
            condition=('instance.business_key != null && size(users_with_role("ceo")) > 0'),
        )
        .flow("g_stage2", "arm_var", default=True)
        .connect("arm_by_role", "sink")
        .connect("arm_by_fact", "sink")
        .connect("sink", "route_end")
        .connect("arm_var", "route_end")
    )
    return builder.build().model_copy(update={"variables": _ROUTE_VARIABLES})
