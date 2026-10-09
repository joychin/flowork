"""混用世界①：找人函数 × CEL × 环境变量（取人位）——由简到繁十一档阶梯。

命题：取人表达式里，引擎词汇（contrib 的 manager_of/users_with_role/has_role 或
宿主自注册函数）、CEL 语法（索引/拼接/comprehension 宏/三元/字符串拼接）与
环境变量（流程变量）与判断面事实（prev_task/instance/node()）能自由组合。

阶梯（suite_participant_ladder，站站相连，单一办理人保证 prev_task 确定）：
① 字面量 → ② 单函数 → ③ 函数吃变量 → ④ 函数+CEL 索引 → ⑤ 函数+列表拼接
→ ⑥ 函数+comprehension → ⑦ 谓词+三元 → ⑧ 函数吃上一站事实 → ⑨ 变量×函数×
事实三合一 → ⑩ 函数吃点名节点事实 → ⑪ 纯 CEL 拼变量成名。

两条**实测边界**（本批发现并记录，走查里各有一条测试钉住）：
1. 词汇函数经 contrib 注册面统一抬升返回值到 CEL 值域
   （`flowork/contrib/_registry.py::_lift`）——此前 Python 原生 bool 进不了
   `!has_role(...)` 与 `cond ? a : b`（celpy 按 CEL 类型分派运算符重载）；
2. 事实缺键（`node("查无")` 的人物字段）是 null，直接喂给 `manager_of` 是技术
   故障（CEL 严格类型），必须三元判空——写法样板见
   ``test_null_field_into_people_function_needs_guard``。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowork import contrib
from flowork.definitions.builder import FlowBuilder
from flowork.definitions.definition import ProcessVariable
from flowork.directory.store import DirectoryStore
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.errors import FloworkError
from tests.suite.features import covers, covers_node
from tests.suite.walkthrough import complete_of, expect_position
from tests.suite.world_flows_time import FakeClock
from tests.suite.world_org import build_directory_store

# 阶梯逐站期望：(节点码, 期望办理人...) —— 单一办理人的档位保证下一站 prev_task 确定
LADDER: list[tuple[str, ...]] = [
    ("n_literal", "u_ceo"),  # ① 字面量
    ("n_manager", "u_lead_be"),  # ② manager_of(u_dev_wu)＝后端组主管
    ("n_role_var", "u_chief_acc", "u_cash"),  # ③ users_with_role(duty_role＝财务审批人)
    ("n_index", "u_chief_acc"),  # ④ 取列表首人
    ("n_concat", "u_chief_acc", "u_cash", "u_lead_be"),  # ⑤ 财务审批人 + 上级
    ("n_filter", "u_chief_acc", "u_cash"),  # ⑥ 过滤掉发起人（发起人不在名单内＝原名单）
    ("n_ternary", "u_chief_acc"),  # ⑦ 发起人非 CEO → 取财务首人
    ("n_prev_person", "u_chief_acc"),  # ⑧ manager_of(上一站办理人→会计组长)
    ("n_prev_role", "u_cash"),  # ⑨ 财务审批人减去上一站办理人
    ("n_node_person", "u_lead_be"),  # ⑩ manager_of(node("n_manager").assignee)
    ("n_strcat", "u_ceo"),  # ⑪ 'u_' + role_suffix(＝ceo)
]

LADDER_VARIABLES = {"duty_role": "finance_approver", "role_suffix": "ceo"}


def _start_ladder(engine: ProcessEngine, start_user: str = "u_dev_wu") -> str:
    return engine.runtime.start_process(
        "suite_participant_ladder", start_user=start_user, variables=dict(LADDER_VARIABLES)
    )


def _walk_station(
    engine: ProcessEngine, iid: str, station: str, assignees: tuple[str, ...]
) -> None:
    """断言当前站的办理人集合，并全部办完（推进到下一站）。"""
    expect_position(
        engine,
        iid,
        nodes={station},
        tasks={(station, assignee) for assignee in assignees},
        note=station,
    )
    for assignee in assignees:
        complete_of(engine, iid, assignee)


@covers(
    "participant.assignee_literal",
    "participant.expression_manager_of",
    "participant.expression_users_with_role",
    "participant.expression_mixed",
    "participant.expression_fact_chain",
)
@covers_node(
    "suite_participant_ladder",
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
)
def test_participant_ladder_walks_simple_to_complex(suite_engine: ProcessEngine) -> None:
    """十一档阶梯一次走到底：每站的办理人集合即该档表达式的求值答案。"""
    engine = suite_engine
    iid = _start_ladder(engine)
    for station, *assignees in LADDER:
        _walk_station(engine, iid, station, tuple(assignees))
    expect_position(engine, iid, nodes=set(), state="completed", note="阶梯走完")


@covers("participant.expression_mixed")
def test_ladder_ternary_picks_other_branch_for_ceo_initiator(suite_engine: ProcessEngine) -> None:
    """同一档位两条分支都走：发起人是 CEO 时三元改走 manager_of 边（换人不换图）。

    CEO 走上行后，下游的「事实档」也随之换人——⑩ 点名节点的上级、⑧ 上一站的
    上级全部跟着发起人变；这正是把办事人写进公式（而非写死字面量）的价值，
    也是本档要钉住的口径。
    """
    engine = suite_engine
    iid = _start_ladder(engine, start_user="u_ceo")
    ceo_path = [
        ("n_literal", "u_ceo"),  # ① 字面量：不随发起人变
        ("n_manager", "u_ceo"),  # ② 总经办主管＝其本人
        ("n_role_var", "u_chief_acc", "u_cash"),  # ③ 角色名单不随发起人变
        ("n_index", "u_chief_acc"),  # ④
        ("n_concat", "u_chief_acc", "u_cash", "u_ceo"),  # ⑤ 名单尾变了（上级是 u_ceo）
        ("n_filter", "u_chief_acc", "u_cash"),  # ⑥ 发起人不在名单内＝原名单
        ("n_ternary", "u_ceo"),  # ⑦ 谓词命中 → 走 manager_of 边
        ("n_prev_person", "u_ceo"),  # ⑧ 上一站的上级＝u_ceo
        ("n_prev_role", "u_chief_acc", "u_cash"),  # ⑨ 减去 u_ceo（不在名单内）＝原名单
        ("n_node_person", "u_ceo"),  # ⑩ 点名 n_manager（当年办理人是 u_ceo）
        ("n_strcat", "u_ceo"),  # ⑪ 变量拼名不随发起人变
    ]
    for station, *assignees in ceo_path:
        _walk_station(engine, iid, station, tuple(assignees))
    expect_position(engine, iid, nodes=set(), state="completed", note="CEO 路径走完")


@covers("participant.null_spread", "participant.no_participant_to_admins")
def test_void_expression_falls_back_or_rejects(suite_engine: ProcessEngine) -> None:
    """查无传播：公式求值为 null ＝ 空贡献——有兜底名单则落管理员，无名单则拒绝。

    持久化目录世界的 admin 角色无人持有（admins＝[]）→ 无参与者技术故障
    （挂起＋故障单）；静态目录世界配了管理员 → to_admins 兜底生效（见下一测）。
    """
    engine = suite_engine
    iid = engine.runtime.start_process("suite_participant_void", start_user="u_dev_wu")
    with pytest.raises(FloworkError, match="没有可用参与者"):
        complete_of(engine, iid, "u_dev_wu")  # 前站办完 → manager_of('u_nobody') 求值完成空
    expect_position(engine, iid, nodes={"void_up"}, state="suspended", note="无兜底名单→挂起")
    assert engine.runtime.list_incidents(), "无参与者应产故障单可查"


@covers("participant.null_spread", "participant.no_participant_to_admins")
@covers_node("suite_participant_void", "start", "void_up", "void_ghost", "void_end")
def test_void_expression_lands_on_admins_when_configured(static_engine: ProcessEngine) -> None:
    """同一份定义在配了管理员的目录世界里走通：查无的空贡献由 to_admins 兜住。"""
    engine = static_engine
    iid = engine.runtime.start_process("suite_participant_void", start_user="u_dev_wu")
    complete_of(engine, iid, "u_dev_wu")
    expect_position(
        engine,
        iid,
        nodes={"void_ghost"},
        tasks={("void_ghost", "u_ceo")},
        note="to_admins 兜底（静态目录 admins=[u_ceo]）",
    )
    complete_of(engine, iid, "u_ceo")
    expect_position(engine, iid, nodes=set(), state="completed")


# —— 宿主自定义函数（内核注册表出厂为空：词汇全部来自宿主注册面）——


class _HostFunctions:
    """宿主注册面：contrib 词汇 + 两个自定义函数（同一注册表并存）。

    证明「找人函数」不是内核内置——宿主可以按自己的组织模型命名与实现，
    CEL 里与 contrib 词汇、变量、CEL 语法混用无差别。
    """

    def __init__(self, org: DirectoryStore) -> None:
        from flowork.api import ExpressionFunction, FunctionSignature

        table = dict(contrib.cel_vocabulary(org).functions())
        table["dept_head_of"] = ExpressionFunction(
            FunctionSignature(("string",), "one_user", label="按部门码取主管"),
            lambda code: org.department_head(code),
        )
        table["over_budget"] = ExpressionFunction(
            FunctionSignature(("number", "number"), "bool", label="是否超预算"),
            lambda amount, cap: bool(amount and cap and amount > cap),
        )
        self._table = table

    def functions(self):
        return self._table


def _host_engine(tmp_path: Path) -> ProcessEngine:
    org = build_directory_store(tmp_path / "host_org.db")
    return ProcessEngine(
        EngineConfig(now=FakeClock(), org=org, expression_functions=_HostFunctions(org))
    )


@covers("participant.host_functions", "participant.expression_mixed")
def test_host_functions_mix_with_cel_and_variables(tmp_path: Path) -> None:
    """宿主函数参与取人与条件：dept_head_of 取人、over_budget 与 has_role 同式混用。"""
    engine = _host_engine(tmp_path)
    definition = (
        FlowBuilder("host_mix", "宿主函数混用")
        .start()
        .user_task("fill", "填报", assignee="'u_dev_wu'")
        .branch("g_budget", "预算闸")
        .user_task("cfo_task", "超预算审批", assignee='dept_head_of("fin")')
        .user_task("auto_ok", "常规审批", assignee="manager_of(initiator)")
        .end("host_end", "结束")
        .connect("start", "fill")
        .connect("fill", "g_budget")
        .flow(
            "g_budget",
            "cfo_task",
            condition='over_budget(amount, budget) && !has_role(initiator, "ceo")',
        )
        .flow("g_budget", "auto_ok", default=True)
        .connect("cfo_task", "host_end")
        .connect("auto_ok", "host_end")
        .build()
    )
    definition.variables = [
        ProcessVariable(name="amount", type="number", label="金额", sample=500),
        ProcessVariable(name="budget", type="number", label="预算", sample=100),
    ]
    engine.repository.deploy(definition)

    over = engine.runtime.start_process(
        "host_mix", start_user="u_dev_wu", variables={"amount": 500, "budget": 100}
    )
    complete_of(engine, over, "u_dev_wu")
    expect_position(
        engine,
        over,
        nodes={"cfo_task"},
        tasks={("cfo_task", "u_cfo")},
        note="宿主函数取人：财务部主管（库里 fin_gl 挂 fin，两处口径一致）",
    )

    under = engine.runtime.start_process(
        "host_mix", start_user="u_dev_wu", variables={"amount": 50, "budget": 100}
    )
    complete_of(engine, under, "u_dev_wu")
    expect_position(
        engine,
        under,
        nodes={"auto_ok"},
        tasks={("auto_ok", "u_lead_be")},
        note="未超预算 → 默认臂 → contrib 词汇取人",
    )
    engine.close()


@covers("participant.expression_mixed")
def test_predicate_in_subject_position_rejected_at_design_and_run_time(
    suite_engine: ProcessEngine,
) -> None:
    """谓词函数放取人位：设计期拦（returns 类型对位），运行期同样拒绝。

    设计期不是唯一闸门——`has_role(...)` 求值出 True（不是人），取人管线按
    「须返回人或人列表」拒绝（技术故障）；两道闸门本批都实测。
    """
    from flowork.definitions.validator import IssueLevel, validate_definition

    engine = suite_engine
    definition = (
        FlowBuilder("bad_predicate")
        .start()
        .user_task("x", "取人位用谓词", assignee='has_role(initiator, "ceo")')
        .end()
        .connect("start", "x")
        .connect("x", "end")
        .build()
    )
    definition.variables = [ProcessVariable(name="amount", type="number")]
    issues = validate_definition(
        definition,
        engine.config.node_registry,
        expression_functions=engine.config.expression_functions,
    )
    assert [i.rule for i in issues if i.level == IssueLevel.ERROR] == [
        "expression.function.returns"
    ]

    engine.repository.deploy(definition.model_copy(update={"code": "bad_predicate_rt"}))
    with pytest.raises(FloworkError, match="须返回人或人列表"):
        engine.runtime.start_process("bad_predicate_rt", start_user="u_ceo")


def _two_station(engine: ProcessEngine, code: str, assignee: str) -> None:
    definition = (
        FlowBuilder(code)
        .start()
        .user_task("first", "前站", assignee="'u_dev_wu'")
        .user_task("second", "结算站", assignee=assignee)
        .end()
        .connect("start", "first")
        .connect("first", "second")
        .connect("second", "end")
        .build()
    )
    definition.variables = [
        ProcessVariable(name="duty_role", type="string", label="角色", sample="ceo")
    ]
    engine.repository.deploy(definition)


@covers("participant.expression_fact_chain")
def test_null_field_into_people_function_needs_guard(suite_engine: ProcessEngine) -> None:
    """事实缺键进函数要判空（CEL 严格类型），判空守卫写法则照常取人。

    档位与 ``node()`` 文档口径一致：查无＝固定形状（人物字段 null）——
    ``null`` 直接当人喂给 ``manager_of`` 是**技术故障**（挂起＋故障单，报错
    信息带公式原文）；三元判空后求值是唯一正确写法。宿主把「事实取人」写进
    图纸时必须走守卫，这条测试就是那份写法样板。
    """
    engine = suite_engine
    _two_station(engine, "unguarded_fact", 'manager_of(node("ghost").assignee)')
    iid = engine.runtime.start_process("unguarded_fact", start_user="u_dev_wu")
    with pytest.raises(FloworkError, match="取人公式求值失败"):
        complete_of(engine, iid, "u_dev_wu")
    expect_position(engine, iid, nodes={"first"}, state="suspended", note="无守卫→挂起")
    assert engine.runtime.list_incidents(), "求值错应产故障单"

    _two_station(
        engine,
        "guarded_fact",
        'node("ghost").assignee != null ? manager_of(node("ghost").assignee) : "u_ceo"',
    )
    guarded = engine.runtime.start_process("guarded_fact", start_user="u_dev_wu")
    complete_of(engine, guarded, "u_dev_wu")
    expect_position(
        engine,
        guarded,
        nodes={"second"},
        tasks={("second", "u_ceo")},
        note="判空守卫：查无落兜底人",
    )

    _two_station(
        engine,
        "guarded_hit",
        'node("first").assignee != null ? manager_of(node("first").assignee) : "u_ceo"',
    )
    hit = engine.runtime.start_process("guarded_hit", start_user="u_dev_wu")
    complete_of(engine, hit, "u_dev_wu")
    expect_position(
        engine,
        hit,
        nodes={"second"},
        tasks={("second", "u_lead_be")},
        note="同一条公式：点名节点有事实时取真实上级",
    )
