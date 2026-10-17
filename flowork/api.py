"""flowork.api：内核唯一公开门面（稳定 API 面）。

宿主、投影（cli）与插件（contrib，含 web 传输面及未来的 rest/mcp）一律经本门面
使用内核，禁止 import 内部深路径——由 import-linter 反向契约强制
（pyproject [tool.importlinter]「投影/插件只准走公开面」）。

约束（对 AI 与外部宿主同样生效）：
- 内核模块重排（重构刀）只准重排内部件，本门面的路径与符号名冻结；
- 新增符号须同步 tests/test_api_surface.py 快照（快照红=显式扩面）；
- 删除或改名符号为破坏性变更，走 major 版本。

API_VERSION：门面契约版本。增符号=minor（同步
快照即可）；删/改名=major。插件以 REQUIRES_FLOWORK_API 声明依赖的门面版本，
兼容性测试断言插件要求 ≤ 当前值。
"""

from typing import Final

from flowork.clock import utc_now
from flowork.definitions.builder import FlowBuilder
from flowork.definitions.builtin import RouteMode
from flowork.definitions.definition import (
    InvalidDefinitionData,
    ProcessDefinition,
    ProcessVariable,
    load_definition,
    load_definition_file,
)
from flowork.definitions.dry_run import simulate_route
from flowork.definitions.node import FlowNode, MultiInstance
from flowork.definitions.semantic_diff import semantic_diff
from flowork.definitions.validator import (
    InvalidDefinition,
    IssueLevel,
    ValidateIssue,
    validate_definition,
)

# 目录持久化实现：DB 版目录此前仓内仅 tests 可达，宿主
# 走门面无路——DirectoryStore(engine.session_factory) 构造后挂 EngineConfig.org。
from flowork.directory import DirectoryError, DirectoryStore
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.engine.history_service import HistoryService
from flowork.engine.job_service import JobClaim, JobService, JobStateError
from flowork.engine.message_service import MessageService

# 模拟/排练/场景：控制面与运行器（设计器与 CLI 同源；投影面只准经本门面）
from flowork.engine.rehearsal import (
    perform_rehearsal_step,
    rehearsal_state,
    start_rehearsal,
)
from flowork.engine.repository_service import RepositoryService
from flowork.engine.runtime_service import RuntimeService
from flowork.engine.scenario import ScenarioReport, parse_scenario, run_scenario
from flowork.engine.scenario_store import ScenarioStore, scenario_store
from flowork.engine.simulation import SimulationReport
from flowork.engine.simulation_controls import SimulationControls, parse_controls
from flowork.engine.simulation_runner import drive_simulation
from flowork.engine.task_service import TaskService
from flowork.errors import (
    ConditionError,
    DefinitionError,
    DefinitionNotPublishedError,
    DefinitionRetiredError,
    DuplicateStartError,
    FloworkError,
    InstanceNotFoundError,
    NotFoundError,
    TaskNotAvailableError,
    UnknownHandler,
    UnknownNodeKind,
)

# 事件消费帮手与事件名枚举：outbox
# 只写不消费的现状下，宿主消费侧的至少一次分发 + 按 id 幂等去重参考实现；
# DomainEvent＝引擎自发事件全集（「枚举即全集」，按枚举编程
# 的订阅方入口上公开面）。
from flowork.events.consumer import IdempotentSink, OutboxConsumer
from flowork.events.domain import DomainEvent
from flowork.expr.catalog import (
    FunctionEntry,
    engine_functions,
    identifier_entries,
    reserved_identifiers,
)
from flowork.expr.cel import FormulaError, evaluate_formula_value
from flowork.expr.conditions import evaluate_condition
from flowork.expr.context import expression_functions_of

# 目录协议与实现：OrgDirectoryLike＝引擎消费的目录协议（EngineConfig.org 类型，
# 四方法最小面）；StaticOrgDirectory＝participants 的 yaml 静态实现类。
# spi.ports 的同名四方法死协议已删除（与 OrgDirectoryLike 重复且零消费）
# ——公开面不再有同名歧义。
from flowork.participants.org_directory import OrgDirectory as StaticOrgDirectory
from flowork.participants.org_directory import OrgDirectoryLike
from flowork.runtime.behavior import InMemoryNodeContext, NodeBehavior, NodeContext
from flowork.runtime.lifecycle import InstanceState, TokenState
from flowork.runtime.registry import NodeRegistry, create_registry
from flowork.spi.ports import (
    ExpressionFunction,
    ExpressionFunctions,
    FunctionSignature,
)

# 演练/模拟共用的任务步骤机件：CLI 演练台与模拟驱动器共用一份词表与分派
# （find_pending_task 定位待办 + dispatch_task_action 十动作分派）。
from flowork.tasks.action_steps import (
    KNOWN_TASK_ACTIONS,
    dispatch_task_action,
    find_pending_task,
)
from flowork.tasks.models import (
    EventInfo,
    InstanceInfo,
    TaskCompleteResult,
    TaskHistoryInfo,
    TaskInfo,
)

API_VERSION: Final = "1"

__all__ = [
    "API_VERSION",
    "KNOWN_TASK_ACTIONS",
    "ConditionError",
    "DefinitionError",
    "DefinitionNotPublishedError",
    "DefinitionRetiredError",
    "DirectoryError",
    "DirectoryStore",
    "DomainEvent",
    "DuplicateStartError",
    "EngineConfig",
    "EventInfo",
    "ExpressionFunction",
    "ExpressionFunctions",
    "FlowBuilder",
    "FlowNode",
    "FloworkError",
    "FormulaError",
    "FunctionEntry",
    "FunctionSignature",
    "HistoryService",
    "IdempotentSink",
    "InMemoryNodeContext",
    "InstanceInfo",
    "InstanceNotFoundError",
    "InstanceState",
    "InvalidDefinition",
    "InvalidDefinitionData",
    "IssueLevel",
    "JobClaim",
    "JobService",
    "JobStateError",
    "MessageService",
    "MultiInstance",
    "NodeBehavior",
    "NodeContext",
    "NodeRegistry",
    "NotFoundError",
    "OrgDirectoryLike",
    "OutboxConsumer",
    "ProcessDefinition",
    "ProcessEngine",
    "ProcessVariable",
    "RepositoryService",
    "RouteMode",
    "RuntimeService",
    "ScenarioReport",
    "ScenarioStore",
    "SimulationControls",
    "SimulationReport",
    "StaticOrgDirectory",
    "TaskCompleteResult",
    "TaskHistoryInfo",
    "TaskInfo",
    "TaskNotAvailableError",
    "TaskService",
    "TokenState",
    "UnknownHandler",
    "UnknownNodeKind",
    "ValidateIssue",
    "create_registry",
    "dispatch_task_action",
    "drive_simulation",
    "engine_functions",
    "evaluate_condition",
    "evaluate_formula_value",
    "expression_functions_of",
    "find_pending_task",
    "identifier_entries",
    "load_definition",
    "load_definition_file",
    "parse_controls",
    "parse_scenario",
    "perform_rehearsal_step",
    "rehearsal_state",
    "reserved_identifiers",
    "run_scenario",
    "scenario_store",
    "semantic_diff",
    "simulate_route",
    "start_rehearsal",
    "utc_now",
    "validate_definition",
]
