"""web /api 面快照守卫（设计器插件计划批 C3，模式对齐 tests/test_api_surface.py）。

设计器以插件交付后，它消费的 /api 面从「内部实现细节」升级为「对外契约」——
宿主升级插件时不能随便断。本测试把端点清单钉死：增删路径或方法=红灯=显式动作
（同步改本清单并说明理由）。web 层代码一律经 flowork.api（投影纪律另测）。
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from flowork.contrib.web.app import create_app

# 冻结清单（与 app 路由一一对应；格式 "METHODS path"，METHODS 升序逗号连接）
EXPECTED: set[str] = {
    "GET /api/health",
    "GET /api/node-kinds",
    "GET /api/definitions",
    "GET /api/definitions/{code}",
    "PUT /api/definitions/{code}",
    "POST /api/definitions/{code}/publish",
    "POST /api/validate",
    "POST /api/definitions/{code}/diff",
    # 版本判定预演（只判定不落库）：编辑期把发布的后果前移
    "POST /api/definitions/{code}/verdict",
    # 运行形态下发：db（会话库） vs files（开发形态的文件事实源）
    "GET /api/runtime",
    # 文件事实源：仅配置 FLOWORK_DEFINITIONS_DIR 时注册
    "GET /api/files",
    "GET /api/files/{file_name}",
    "PUT /api/files/{file_name}",
    "POST /api/dry-run",
    "POST /api/formula/preview",
    "GET /api/catalog",
    "POST /api/definitions/{code}/simulate",
    "GET /api/definitions/{code}/versions",
    "GET /api/definitions/{code}/versions/{major}",
    "GET /api/handlers",
    "GET /api/directory/summary",
    # 场景回归：脚本随定义同住目录（<code>.scenario.json），与 CI 同一份文件
    "GET /api/definitions/{code}/scenario",
    "PUT /api/definitions/{code}/scenario",
    "DELETE /api/definitions/{code}/scenario",
    "POST /api/definitions/{code}/scenarios/run",
}


@pytest.fixture(scope="module")
def route_set(tmp_path_factory) -> set[str]:
    # 文件面与场景文件面只在配置了定义目录时注册——契约快照取完整形态
    app = create_app(db_file=":memory:", definitions_dir=tmp_path_factory.mktemp("defs"))
    out: set[str] = set()
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api"):
            # 只冻 /api 面——插件契约;/designer-demo 等宿主便利面按 dist-lib
            # 存在与否条件注册,不属于契约(也避免快照随构建产物漂移)
            methods = ",".join(sorted(route.methods - {"HEAD", "OPTIONS"}))
            out.add(f"{methods} {route.path}")
    return out


def test_api_surface_exactly_frozen(route_set: set[str]) -> None:
    assert route_set == EXPECTED, (
        "web /api 面与冻结清单不一致——插件契约变更须显式同步本清单: "
        f"多出={sorted(route_set - EXPECTED)} 缺失={sorted(EXPECTED - route_set)}"
    )
