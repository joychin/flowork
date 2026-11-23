"""真服务器冒烟（needs_http）：uvicorn 实进程跑 REST /v2。

进程内 TestClient 不经过真实 ASGI 生命周期/线程池/套接字——本轮 review
的三个 REST 缺口（统一错误体破口、业务键形状、CORS 头）都是真服务器上
才现形的。本文件以线程起 uvicorn（tmp 库），钉住最小证据面。
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import uvicorn

pytestmark = pytest.mark.needs_http


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def base_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    port = _free_port()
    # 显式 tmp 库：REST 独立进程缺省落 CWD 的 flowork.db——仓库根目录的残留
    # 库会让 create_all 静默沿用旧 schema（真实踩坑：agent 列删除前的库让
    # 任务插行撞 NOT NULL）
    monkeypatch.setenv("FLOWORK_DB", str(tmp_path / "rest.db"))
    config = uvicorn.Config(
        "flowork.contrib.rest.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    config.load()

    class _Server(uvicorn.Server):
        def override_signal(self, slot: int) -> None:
            pass  # 禁止 uvicorn 挂系统信号处理器（线程形态必需）

    server = _Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            httpx.get(f"{url}/v2/health", timeout=2)
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        pytest.fail("uvicorn 5 秒内未就绪")
    yield url
    server.should_exit = True
    thread.join(15)


def _deploy_approval(base: str) -> None:
    spec_path = Path(__file__).parent.parent / "examples/01_simple_approval.json"
    spec = json.loads(spec_path.read_text())
    response = httpx.post(
        f"{base}/v2/definitions/deploy", json={"spec": spec, "actor": "ci"}, timeout=30
    )
    assert response.status_code == 200, response.text


def test_real_server_health_cors_and_concurrency(base_url: str) -> None:
    base = base_url
    _deploy_approval(base)

    # 生命周期：真 uvicorn 的 lifespan 跑过（startup 开了内置调度）——健康检查可用即证据
    assert httpx.get(f"{base}/v2/health", timeout=10).status_code == 200

    # CORS 缺省不挂：带 Origin 头也不发 access-control-*（任意网页不可跨域写文件面）
    probe = httpx.get(f"{base}/v2/health", headers={"Origin": "http://evil.example"}, timeout=10)
    assert not any(key.startswith("access-control-") for key in probe.headers)

    # 并发办结同一任务：恰一次 200、其余领域拒绝、零 5xx
    instance_id = httpx.post(
        f"{base}/v2/instances", json={"code": "simple_approval", "start_user": "u0"}, timeout=30
    ).json()["instance_id"]
    task_id = httpx.post(
        f"{base}/v2/tasks/search", json={"filter": {"instance_id": instance_id}}, timeout=30
    ).json()["items"][0]["id"]

    def complete_once(i: int) -> int:
        payload = {"actor": "u_manager", "comment": str(i)}
        response = httpx.post(f"{base}/v2/tasks/{task_id}/complete", json=payload, timeout=30)
        return response.status_code

    with ThreadPoolExecutor(32) as pool:
        statuses = list(pool.map(complete_once, range(40)))
    counts = Counter(statuses)
    assert counts[200] == 1
    assert counts[409] == len(statuses) - 1
    assert not any(code >= 500 for code in counts)


def test_real_server_error_contract(base_url: str) -> None:
    base = base_url
    # 路由级 404 与未处理异常都回统一错误体（TestClient 形态已覆盖，此处钉真服务器）
    missing = httpx.get(f"{base}/v2/nope", timeout=10)
    assert missing.status_code == 404
    assert "error" in missing.json()

    bad_key = httpx.post(
        f"{base}/v2/instances",
        json={"code": "simple_approval", "start_user": "u0", "business_key": {"x": 1}},
        timeout=30,
    )
    assert bad_key.status_code == 422
    assert "business_key" in bad_key.json()["error"]["message"]
