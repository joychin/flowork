"""日志面守卫（宿主日志接入批）。

内核日志＝stdlib logging 层级命名空间（logger 名＝模块全路径），宿主经
``logging.getLogger("flowork")`` 挂 handler 接管全部输出——内核不设日志端口、
不做 basicConfig（死端口先例：Notifier/EventSink/WorkCalendar/VariableStore
均按零消费删除，日志端口没有理由成为第五个）。本文件钉住三件事：

1. 包根 NullHandler（logging HOWTO 库礼仪）：宿主未配置日志时引擎完全
   静默且不警告；
2. 命名纪律（AST 扫描）：flowork/ 内所有 ``getLogger`` 只允许 ``__name__``
   字面量——层级名由模块路径自动成立，防硬编码游离名漂移；
3. 传播契约（宿主视角）：在 ``flowork`` 命名空间挂 handler 即可收到引擎
   全部输出，record.name 为模块全路径（宿主可按子系统细粒度调级）。
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from flowork.definitions.builder import FlowBuilder
from flowork.definitions.validator import IssueLevel
from flowork.definitions.validator_deploy import check_deploy_references
from flowork.engine.engine import ProcessEngine

REPO_ROOT = Path(__file__).resolve().parent.parent


# —— 1. 包根 NullHandler ——


def test_package_root_carries_null_handler():
    """导入 flowork 即挂 NullHandler：未配置日志时静默、无「No handlers」警告。

    本文件顶部 import flowork.* 已触发包初始化（NullHandler 在包 import 时挂上）。
    """
    handlers = logging.getLogger("flowork").handlers
    assert any(isinstance(h, logging.NullHandler) for h in handlers)


def test_alembic_fileconfig_keeps_existing_loggers_enabled():
    """回归钉子（本批实证）：fileConfig 缺省 disable_existing_loggers=True，
    同进程先 import flowork 再跑 Alembic 升级＝整个 flowork 命名空间被静默
    禁用（全量套件中 test_alembic 先跑，本文件的传播测试随即收空——宿主
    「CLI 编程式升级后引擎日志无声消失」同根）。env.py 必须显式关掉缺省。
    """
    src = (REPO_ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "disable_existing_loggers=False" in src


# —— 2. 命名纪律 ——


def _get_logger_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted((REPO_ROOT / "flowork").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(REPO_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            via_logging = isinstance(func, ast.Attribute) and func.attr == "getLogger"
            via_import = isinstance(func, ast.Name) and func.id == "getLogger"
            if not (via_logging or via_import):
                continue
            arg = node.args[0] if node.args else None
            if not (isinstance(arg, ast.Name) and arg.id == "__name__"):
                violations.append(
                    f"{rel}:{node.lineno} getLogger 实参必须是 __name__ 字面量"
                    "（层级名＝模块全路径；游离名让宿主无法按子系统调级）"
                )
    return violations


def test_every_get_logger_uses_dunder_name():
    violations = _get_logger_violations()
    assert not violations, "\n".join(violations)


# —— 3. 传播契约 ——


def test_host_handler_receives_deploy_warning():
    """宿主在 ``flowork`` 层挂 handler：部署校验告警可达，记录者名＝模块全路径。"""
    engine = ProcessEngine()
    flow = (
        FlowBuilder("log_probe")
        .start()
        .user_task("a", assignee="'u_a'")
        .service_task("s", action="ghost_handler")
        .end()
        .connect("start", "a")
        .connect("a", "s")
        .connect("s", "end")
        .build()
    )
    received: list[logging.LogRecord] = []

    class _HostHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            received.append(record)

    host_handler = _HostHandler(level=logging.WARNING)
    flowork_logger = logging.getLogger("flowork")
    flowork_logger.addHandler(host_handler)
    try:
        issues = check_deploy_references(flow, session=engine.session_factory(), handlers=())
    finally:
        flowork_logger.removeHandler(host_handler)

    assert [(i.rule, i.level) for i in issues] == [
        ("service_task.handler_unknown", IssueLevel.WARNING)
    ]
    assert received, "宿主 handler 未收到引擎输出"
    assert all(r.name == "flowork.definitions.validator_deploy" for r in received)
    assert any("deploy 校验" in r.getMessage() for r in received)


def test_worker_dead_letter_warns_with_module_logger_name(caplog):
    """worker Job 失败两分支接声（与内部 timer 路径 _mark_failure 对称）。"""
    engine = ProcessEngine()
    engine.repository.deploy(
        FlowBuilder("job_flow")
        .start()
        .user_task("a", assignee="'u_a'")
        .end()
        .connect("start", "a")
        .connect("a", "end")
        .build()
    )
    instance_id = engine.runtime.start_process("job_flow", start_user="u_s")
    job_id = engine.worker_jobs.create_worker_job(
        job_type="http_call", instance_id=instance_id, payload={"url": "https://x"}
    )
    engine.worker_jobs.activate_jobs(["http_call"], worker_id="w-1")

    with caplog.at_level(logging.WARNING, logger="flowork"):
        outcome = engine.worker_jobs.fail_job(job_id, worker_id="w-1", error="彻底失败", retries=0)

    assert outcome == "dead_letter"
    records = [r for r in caplog.records if r.name == "flowork.engine.job_service"]
    assert any("dead-letter" in r.getMessage() for r in records)
