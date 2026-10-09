"""flowork.api：内核唯一公开门面（稳定 API 面）。

宿主、投影（cli）与插件（contrib，含 web 传输面及未来的 rest/mcp）一律经本门面
使用内核，禁止 import 内部深路径——由 import-linter 反向契约强制
（pyproject [tool.importlinter]「投影/插件只准走公开面」）。

约束（对 AI 与外部宿主同样生效）：
- 内核模块重排（spec §15 重构刀）只准重排内部件，本门面的路径与符号名冻结；
- 新增符号须同步 tests/test_api_surface.py 快照（快照红=显式扩面）；
- 删除或改名符号为破坏性变更，走 major 版本（spec P0-4）。
"""

from flowork.clock import utc_now
from flowork.definitions.builder import FlowBuilder
from flowork.definitions.builtin import RouteMode
from flowork.definitions.definition import (
    InvalidDefinitionData,
    ProcessDefinition,
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
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.engine.history_service import HistoryService
from flowork.engine.job_service import JobClaim, JobService, JobStateError
from flowork.engine.message_service import MessageService
from flowork.engine.repository_service import RepositoryService
from flowork.engine.runtime_service import RuntimeService
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
from flowork.expr.catalog import (
    FunctionEntry,
    engine_functions,
    identifier_entries,
    reserved_identifiers,
)
from flowork.expr.cel import FormulaError, evaluate_formula_value
from flowork.expr.conditions import evaluate_condition
from flowork.expr.context import expression_functions_of

# StaticOrgDirectory：participants 的 yaml 静态目录实现类（OrgDirectory.from_yaml）。
# 与 spi.ports.OrgDirectory（四方法端口 Protocol）同名，公开面以别名消歧——
# 端口用 OrgDirectory，装配实现用 StaticOrgDirectory。
from flowork.participants.org_directory import OrgDirectory as StaticOrgDirectory
from flowork.participants.org_directory import OrgDirectoryLike
from flowork.runtime.behavior import InMemoryNodeContext, NodeBehavior, NodeContext
from flowork.runtime.lifecycle import InstanceState, NodeLifecycle, TokenState
from flowork.runtime.registry import NodeRegistry, create_registry
from flowork.spi.ports import (
    Clock,
    EventSink,
    ExpressionFunction,
    ExpressionFunctions,
    FunctionSignature,
    OrgDirectory,
)
from flowork.tasks.models import (
    EventInfo,
    InstanceInfo,
    TaskCompleteResult,
    TaskHistoryInfo,
    TaskInfo,
)

__all__ = [
    "Clock",
    "ConditionError",
    "DefinitionError",
    "DefinitionNotPublishedError",
    "DefinitionRetiredError",
    "DuplicateStartError",
    "EngineConfig",
    "EventInfo",
    "EventSink",
    "ExpressionFunction",
    "ExpressionFunctions",
    "FlowBuilder",
    "FlowNode",
    "FloworkError",
    "FormulaError",
    "FunctionEntry",
    "FunctionSignature",
    "HistoryService",
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
    "NodeLifecycle",
    "NodeRegistry",
    "NotFoundError",
    "OrgDirectory",
    "OrgDirectoryLike",
    "ProcessDefinition",
    "ProcessEngine",
    "RepositoryService",
    "RouteMode",
    "RuntimeService",
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
    "engine_functions",
    "evaluate_condition",
    "evaluate_formula_value",
    "expression_functions_of",
    "identifier_entries",
    "load_definition",
    "load_definition_file",
    "reserved_identifiers",
    "semantic_diff",
    "simulate_route",
    "utc_now",
    "validate_definition",
]
