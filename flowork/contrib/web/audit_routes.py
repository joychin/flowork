"""审计数据面：整图模拟 / 版本面。

``register_audit_routes(app,
engine)`` 挂端点，无独立状态。资源命名与参数风格对齐已定稿
的 /v2 面（``GET /definitions/{code}?major=&revision=`` 同义）；全部只读（除模拟
外零副作用），模拟走内核 simulated 实例。

实例只读面已删除：设计器不连运行时数据库，会话库里的实例只是本次模拟的残留，
拿到界面上展示只会假装在看生产轨迹。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import HTTPException

from flowork.api import FloworkError, IssueLevel
from flowork.contrib.web._helpers import issues_payload, parse_spec

__all__ = ["register_audit_routes"]


def register_audit_routes(app: Any, engine: Any) -> None:
    """把模拟 / 版本端点挂到应用（engine 为 ProcessEngine）。"""

    @app.post("/api/definitions/{code}/simulate")
    def simulate(
        code: str, body: dict[str, Any], major: int | None = None, revision: int | None = None
    ) -> dict[str, Any]:
        """整图模拟（草稿主场景）：证据先于发布。

        body：``{"spec": 定义 JSON（可选——缺省取已保存草稿）, "variables": 样例,
        "controls": 模拟控制面（可选）}``。携带 spec 或取草稿时走**草稿直通**
        （不落定义表，模拟前强制设计期校验，ERROR 拦 422）；``?major=&revision=``
        则模拟已发布版本（对照线上行为）。controls 三键全可选：
        ``decisions``（全量十词表按序扮演办理人，未覆盖的待办停为「待决策点」）、
        ``stubs``（service_task 桩返回值）、``waits``（等待节点 matched/elapsed 假设）；
        缺省＝机器人全通过。
        """
        raw_spec = body.get("spec")
        raw_vars = body.get("variables")
        raw_controls = body.get("controls")
        # 发起人：initiator 是取人公式最常用的输入（manager_of(initiator) 是
        # 文档标准写法）。缺省 simulator 不在组织目录里，取人解析会空——
        # 想验自定义取人函数就必须能指定发起人。
        raw_user = body.get("start_user")
        if raw_user is not None and not isinstance(raw_user, str):
            raise HTTPException(status_code=422, detail="start_user 必须是字符串")
        start_user = raw_user or "simulator"
        if raw_spec is not None and not isinstance(raw_spec, dict):
            # 修前非 dict（字符串/列表）静默回退旧草稿——「当前草稿
            # 含未保存编辑」是本端点主场景，据以拍板发布的报告可能不是送审内容
            raise HTTPException(status_code=422, detail="spec 必须是流程定义 JSON 对象")
        if raw_vars is not None and not isinstance(raw_vars, dict):
            raise HTTPException(status_code=422, detail="variables 必须是对象")
        if raw_controls is not None and not isinstance(raw_controls, dict):
            raise HTTPException(status_code=422, detail="controls 必须是对象")
        variables = raw_vars if isinstance(raw_vars, dict) else None
        if raw_spec is not None or (major is None and revision is None):
            spec = raw_spec if isinstance(raw_spec, dict) else _saved_draft(engine, code)
            definition = parse_spec(engine, spec)
            issues = engine.repository.validate(definition)
            errors = [i for i in issues if i.level == IssueLevel.ERROR]
            if errors:
                raise HTTPException(
                    status_code=422,
                    detail={"message": "校验未通过，禁止模拟", "issues": issues_payload(errors)},
                )
            report = engine.runtime.simulate(
                code,
                spec=spec,
                variables=variables,
                controls=raw_controls,
                start_user=start_user,
            )
        else:
            report = engine.runtime.simulate(
                code,
                variables=variables,
                major=major,
                revision=revision,
                controls=raw_controls,
                start_user=start_user,
            )
        return asdict(report)

    @app.get("/api/definitions/{code}/versions")
    def versions(code: str) -> list[dict[str, Any]]:
        """已发布版本列表（新→旧）：版本号 / 破坏性标记 / 发布时间与归因。"""
        rows: list[dict[str, Any]] = engine.repository.published_versions(code)
        if not rows:
            raise HTTPException(status_code=404, detail=f"定义 {code} 无已发布版本")
        return rows

    @app.get("/api/definitions/{code}/versions/{major}")
    def version_detail(code: str, major: int, revision: int | None = None) -> dict[str, Any]:
        """按版本取已发布 spec（与 ``GET /definitions/{code}?major=`` 同义；
        只读模式没有草稿，本端点是 readonly 视图的数据前提）。"""
        try:
            spec: dict[str, Any] = engine.repository.get_definition_json(code, major, revision)
        except FloworkError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return spec


def _saved_draft(engine: Any, code: str) -> dict[str, Any]:
    """取已保存草稿；无草稿且无已发布版本时报 404（有发布版则提示给 major）。"""
    draft: dict[str, Any] | None = engine.repository.designer_draft(code)
    if draft is not None:
        spec: dict[str, Any] = draft["spec"]
        return spec
    try:
        engine.repository.get_definition_json(code)
    except FloworkError as exc:
        raise HTTPException(status_code=404, detail=f"定义 {code} 不存在") from exc
    raise HTTPException(
        status_code=404,
        detail=f"定义 {code} 无待发布草稿（模拟已发布版本请携带 ?major= 参数）",
    )
