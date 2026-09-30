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
from flowork.errors import DefinitionNotPublishedError, NotFoundError

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


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex


_ = NotFoundError  # 导出占位（保持导入整洁）
