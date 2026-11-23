"""定义仓库只读查询面：verdict 预演与已发布定义读取。

RepositoryService 经 mixin 组合（engine.repository 入口不变）；拆出守
300 行红线。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from flowork.engine.ops import CommandContext

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from flowork.definitions.definition import ProcessDefinition
    from flowork.definitions.versioning import VersionVerdict
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessDefinitionRow

from flowork.definitions.definition import dump_definition, load_definition
from flowork.definitions.versioning import compute_verdict, content_hash_of
from flowork.errors import DefinitionNotPublishedError

__all__ = ["RepositoryReadMixin"]


class RepositoryReadMixin:
    """verdict（版本判定预演）与已发布定义读取。"""

    _engine: ProcessEngine

    def _baseline(self, session: Session, code: str, target_major: int | None) -> Any:
        """比较基线由 RepositoryService 提供。"""
        raise NotImplementedError

    def verdict(
        self, definition: ProcessDefinition, *, target_major: int | None = None
    ) -> VersionVerdict:
        """版本判定预演（只判定不落库——AI/CI 先看后果再 deploy）。"""

        def _command(ctx: CommandContext) -> VersionVerdict:
            from sqlalchemy import select

            from flowork.persistence.orm import ProcessDefinitionRow as _Row

            content = dump_definition(
                load_definition(dump_definition(definition), self._engine.config.node_registry)
            )
            published = {
                (row.major, row.revision): row.content_hash
                for row in ctx.session.scalars(
                    select(_Row).where(_Row.code == definition.code, _Row.status == "published")
                )
            }
            return compute_verdict(
                self._baseline(ctx.session, definition.code, target_major),
                definition,
                content_hash_of(content),
                published_hashes=published,
            )

        result: VersionVerdict = self._engine.execute(_command)
        return result

    def get_definition(
        self, code: str, major: int | None = None, revision: int | None = None
    ) -> ProcessDefinition:
        """取已发布定义（默认最高 published；可显式钉 major/revision）。"""

        def _command(ctx: CommandContext) -> Any:
            row = self._published_row(ctx.session, code, major, revision)
            return load_definition(json.loads(row.content), self._engine.config.node_registry)

        result: ProcessDefinition = self._engine.execute(_command)
        return result

    def get_definition_json(
        self, code: str, major: int | None = None, revision: int | None = None
    ) -> dict[str, Any]:
        """取已发布定义的原始 JSON（快照用）。"""

        def _command(ctx: CommandContext) -> Any:
            row = self._published_row(ctx.session, code, major, revision)
            return json.loads(row.content)

        result: dict[str, Any] = self._engine.execute(_command)
        return result

    def _published_row(
        self, session: Session, code: str, major: int | None, revision: int | None
    ) -> ProcessDefinitionRow:
        from sqlalchemy import select

        from flowork.persistence.orm import ProcessDefinitionRow as _Row

        stmt = select(_Row).where(_Row.code == code, _Row.status == "published")
        if major is None and revision is None:
            stmt = stmt.order_by(_Row.major.desc(), _Row.revision.desc()).limit(1)
        else:
            if major is not None:
                stmt = stmt.where(_Row.major == major)
            if revision is not None:
                stmt = stmt.where(_Row.revision == revision)
        found = session.scalars(stmt).first()
        row: ProcessDefinitionRow | None = found
        if row is None:
            from flowork.errors import DefinitionRetiredError

            retired = session.scalars(
                select(_Row.id).where(_Row.code == code, _Row.status == "retired")
            ).first()
            if retired is not None:
                raise DefinitionRetiredError(
                    f"定义 {code} 已 retired，不接新实例（重新 deploy 可恢复）"
                )
            raise DefinitionNotPublishedError(
                f"已发布定义 {code}（major={major}, revision={revision}）不存在"
            )
        assert row is not None
        return row
