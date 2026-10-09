"""套件共享夹具：双组织世界（持久化目录 / 静态目录）+ 双引擎 + 场景定义全家桶。

隔离保证：引擎用默认内存 SQLite（每测试全新库）；目录库是 tmp_path 下的独立
文件库，与引擎库完全分离——组织数据从不落进引擎库。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowork import contrib
from flowork.directory.store import DirectoryStore
from flowork.engine.config import EngineConfig
from flowork.engine.engine import ProcessEngine
from flowork.participants.org_directory import OrgDirectory
from tests.suite.world_catalog import STATIC_WORLD_CODES, STORE_WORLD_CODES, build_definition
from tests.suite.world_flows_time import FakeClock
from tests.suite.world_org import build_directory_store, build_static_org


def register_world_actions(engine: ProcessEngine) -> None:
    """世界定义引用的系统动作（service_task handler）。"""

    def make_payment(ctx, params):
        ctx.variables["paid"] = True
        ctx.variables["paid_amount"] = ctx.variables.get("amount") or ctx.variables.get("budget")
        ctx.emit("payment_executed", {"paid_amount": ctx.variables["paid_amount"]})

    def echo_paid(ctx, params):
        ctx.variables["erped"] = True

    def explode(ctx, params):
        raise RuntimeError("外部接口炸了")

    def sap_check(ctx, params):
        """物料世界：SAP 数据检查（测试变量 mock_check_ok 模拟外部结果）。"""
        ctx.variables["sap_check_ok"] = ctx.variables.get("mock_check_ok", True)

    def sap_submit(ctx, params):
        """物料世界：提交 SAP（mock_sap_result 模拟 S/E；S 时回填物料编码）。"""
        result = ctx.variables.get("mock_sap_result", "S")
        ctx.variables["sap_code"] = result
        if result == "S":
            ctx.variables["material_code"] = "MAT-7001"

    def fetch_roster(ctx, params):
        """任务发布：获取未参保人员清单（外部档案系统模拟）。"""
        ctx.variables["roster_count"] = 3

    def esign_send(ctx, params):
        """合同签订：E签宝对接签订（外部签署系统模拟）。"""
        ctx.variables["esign_status"] = "signed"

    engine.register_handler("make_payment", make_payment)
    engine.register_handler("echo_paid", echo_paid)
    engine.register_handler("explode", explode)
    engine.register_handler("sap_check", sap_check)
    engine.register_handler("sap_submit", sap_submit)
    engine.register_handler("fetch_roster", fetch_roster)
    engine.register_handler("esign_send", esign_send)


def deploy_world(engine: ProcessEngine, codes: list[str]) -> None:
    for code in codes:
        engine.repository.deploy(build_definition(code))


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def org_store(tmp_path: Path) -> DirectoryStore:
    """持久化组织目录（独立文件库）。"""
    return build_directory_store(tmp_path / "suite_org.db")


@pytest.fixture
def suite_engine(clock: FakeClock, org_store: DirectoryStore) -> ProcessEngine:
    """持久化目录世界引擎：组织 CRUD/角色/Agent 全量可用。"""
    engine = ProcessEngine(
        EngineConfig(
            now=clock,
            org=org_store,
            expression_functions=contrib.cel_vocabulary(org_store),
        )
    )
    deploy_world(engine, STORE_WORLD_CODES)
    register_world_actions(engine)
    return engine


@pytest.fixture
def static_engine(clock: FakeClock) -> ProcessEngine:
    """静态目录世界引擎：委托与管理员（to_admins 兜底）可用。"""
    org: OrgDirectory = build_static_org()
    engine = ProcessEngine(
        EngineConfig(
            now=clock,
            org=org,
            expression_functions=contrib.cel_vocabulary(org),
        )
    )
    deploy_world(engine, STATIC_WORLD_CODES)
    register_world_actions(engine)
    return engine
