"""RepositoryService：流程定义的保存 / 发布 / 查询 / 校验。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from flowork.definitions.definition import (
    ProcessDefinition,
    dump_definition,
    load_definition,
)
from flowork.definitions.validator import ValidateIssue, assert_valid, validate_definition
from flowork.engine.ops import CommandContext
from flowork.errors import DefinitionNotPublishedError

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessDefinitionRow

__all__ = ["RepositoryService"]


class RepositoryService:
    """定义仓库服务：草稿 → 校验 → 发布（发布后内容不可变）。"""

    def __init__(self, engine: ProcessEngine) -> None:
        self._engine = engine

    def save_definition(self, definition: ProcessDefinition) -> int:
        """保存草稿（同 code 自动递增版本）。返回版本号。"""
        from flowork.persistence.orm import ProcessDefinitionRow as _Row

        def _command(ctx: CommandContext) -> Any:
            from sqlalchemy import func, select

            max_version = ctx.session.scalar(
                select(func.max(_Row.version)).where(_Row.code == definition.code)
            )
            row = _Row(
                id=_new_id(),
                code=definition.code,
                version=(max_version or 0) + 1,
                status="draft",
                name=definition.name,
                content=json.dumps(dump_definition(definition), ensure_ascii=False),
            )
            ctx.session.add(row)
            ctx.session.flush()
            return row.version

        result: int = self._engine.execute(_command)
        return result

    def publish(self, code: str, version: int | None = None) -> int:
        """发布定义（默认发布最新草稿；发布前强制校验）。返回发布版本。"""
        from flowork.persistence.orm import ProcessDefinitionRow as _Row

        def _command(ctx: CommandContext) -> Any:
            from sqlalchemy import select

            stmt = select(_Row).where(_Row.code == code)
            if version is None:
                stmt = stmt.order_by(_Row.version.desc()).limit(1)
            else:
                stmt = stmt.where(_Row.version == version)
            row = ctx.session.scalars(stmt).first()
            if row is None:
                raise DefinitionNotPublishedError(f"定义 {code}（version={version}）不存在")
            definition = load_definition(json.loads(row.content), self._engine.config.node_registry)
            assert_valid(definition, self._engine.config.node_registry)
            row.status = "published"
            ctx.session.flush()
            return row.version

        result: int = self._engine.execute(_command)
        return result

    def get_definition(self, code: str, version: int | None = None) -> ProcessDefinition:
        """取已发布定义（默认最高已发布版本）。"""

        def _command(ctx: CommandContext) -> Any:
            row = self._published_row(ctx.session, code, version)
            return load_definition(json.loads(row.content), self._engine.config.node_registry)

        result: ProcessDefinition = self._engine.execute(_command)
        return result

    def _published_row(
        self, session: Session, code: str, version: int | None
    ) -> ProcessDefinitionRow:
        from sqlalchemy import select

        from flowork.persistence.orm import ProcessDefinitionRow as _Row

        stmt = select(_Row).where(_Row.code == code, _Row.status == "published")
        if version is None:
            stmt = stmt.order_by(_Row.version.desc()).limit(1)
        else:
            stmt = stmt.where(_Row.version == version)
        found = session.scalars(stmt).first()
        row: ProcessDefinitionRow | None = found
        if row is None:
            raise DefinitionNotPublishedError(f"已发布定义 {code}（version={version}）不存在")
        assert row is not None
        return row

    def validate(self, definition: ProcessDefinition) -> list[ValidateIssue]:
        """设计时校验（不落库）。"""
        return validate_definition(definition, self._engine.config.node_registry)

    def get_definition_json(self, code: str, version: int | None = None) -> dict[str, Any]:
        """取已发布定义的原始 JSON（快照用）。"""

        def _command(ctx: CommandContext) -> Any:
            row = self._published_row(ctx.session, code, version)
            return json.loads(row.content)

        result: dict[str, Any] = self._engine.execute(_command)
        return result

    # —— 设计器支撑：草稿就地更新 / 最新行 / 汇总列表 ——

    def update_draft(self, definition: ProcessDefinition) -> int:
        """保存草稿：最新版本已是草稿则就地覆盖，否则新建草稿版本。返回草稿版本号。"""
        from flowork.persistence.orm import ProcessDefinitionRow as _Row

        def _command(ctx: CommandContext) -> Any:
            from sqlalchemy import select

            latest = ctx.session.scalars(
                select(_Row)
                .where(_Row.code == definition.code)
                .order_by(_Row.version.desc())
                .limit(1)
            ).first()
            content = json.dumps(dump_definition(definition), ensure_ascii=False)
            if latest is not None and latest.status == "draft":
                latest.content = content
                latest.name = definition.name
                ctx.session.flush()
                return latest.version
            row = _Row(
                id=_new_id(),
                code=definition.code,
                version=(latest.version + 1) if latest is not None else 1,
                status="draft",
                name=definition.name,
                content=content,
            )
            ctx.session.add(row)
            ctx.session.flush()
            return row.version

        result: int = self._engine.execute(_command)
        return result

    def latest_row(self, code: str) -> dict[str, Any] | None:
        """取某 code 的最新定义行摘要（任意状态）；无则 None。"""
        from flowork.persistence.orm import ProcessDefinitionRow as _Row

        def _command(ctx: CommandContext) -> Any:
            from sqlalchemy import select

            row = ctx.session.scalars(
                select(_Row).where(_Row.code == code).order_by(_Row.version.desc()).limit(1)
            ).first()
            if row is None:
                return None
            return {"version": row.version, "status": row.status, "name": row.name}

        result: dict[str, Any] | None = self._engine.execute(_command)
        return result

    def list_summaries(self) -> list[dict[str, Any]]:
        """按 code 汇总定义列表（最新草稿版本 / 最高已发布版本 / 更新时间）。"""
        from flowork.persistence.orm import ProcessDefinitionRow as _Row

        def _command(ctx: CommandContext) -> Any:
            from sqlalchemy import select

            rows = ctx.session.scalars(select(_Row).order_by(_Row.code, _Row.version)).all()
            grouped: dict[str, dict[str, Any]] = {}
            for row in rows:
                summary = grouped.setdefault(
                    row.code,
                    {
                        "code": row.code,
                        "name": row.name,
                        "draft_version": None,
                        "published_version": None,
                        "updated_at": None,
                    },
                )
                if row.status == "draft":
                    summary["draft_version"] = row.version
                else:
                    summary["published_version"] = row.version
                summary["name"] = row.name or summary["name"]
                summary["updated_at"] = row.created_at.isoformat(timespec="seconds")
            return sorted(grouped.values(), key=lambda s: s["code"])

        result: list[dict[str, Any]] = self._engine.execute(_command)
        return result


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex
