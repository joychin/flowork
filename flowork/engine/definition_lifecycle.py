"""定义生命周期：retire/purge/list/definition_drift。

RepositoryService 经 mixin 组合（engine.repository 入口不变）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from flowork.engine.ops import CommandContext
from flowork.errors import DefinitionError, DefinitionNotPublishedError
from flowork.events.domain import DomainEvent

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["DefinitionLifecycleMixin"]


class DefinitionLifecycleMixin:
    """RepositoryService 的定义生命周期面。"""

    _engine: ProcessEngine

    # —— 生命周期——

    def retire(self, code: str, actor: str = "admin", reason: str = "") -> int:
        """retire：该 code 全部 published 行转 retired（在跑跑完，行保留）。"""

        def _command(ctx: CommandContext) -> int:
            from sqlalchemy import update

            from flowork.persistence.orm import ProcessDefinitionRow as _Row

            result = ctx.session.execute(
                update(_Row)
                .where(_Row.code == code, _Row.status == "published")
                .values(status="retired")
            )
            count = int(getattr(result, "rowcount", 0) or 0)
            if count == 0:
                raise DefinitionNotPublishedError(f"定义 {code} 无已发布版本可 retire")
            self._engine.kernel_for(ctx).record_event(
                DomainEvent.DEFINITION_RETIRED,
                {"code": code, "actor": actor, "reason": reason},
                None,
            )
            return count

        return self._engine.execute(_command)

    def purge(self, code: str, actor: str = "admin", *, force: bool = False) -> int:
        """purge：物理删除定义行（仅 CLI --force；有实例引用该定义则拒绝）。"""

        def _command(ctx: CommandContext) -> int:
            from sqlalchemy import delete, select

            from flowork.persistence.orm import ProcessDefinitionRow as _Row
            from flowork.persistence.orm import ProcessInstanceRow

            if not force:
                raise ValueError("purge 需要 --force（物理删除不可逆）")
            referenced = (
                ctx.session.scalars(
                    select(ProcessInstanceRow.id)
                    .where(ProcessInstanceRow.definition_code == code)
                    .limit(1)
                ).first()
                is not None
            )
            if referenced:
                raise DefinitionNotPublishedError(
                    f"定义 {code} 被在途/历史实例引用，拒绝 purge（先核对实例清单）"
                )
            stale_ids = ctx.session.scalars(select(_Row.id).where(_Row.code == code)).all()
            if stale_ids:
                ctx.session.execute(delete(_Row).where(_Row.id.in_(stale_ids)))
            self._engine.kernel_for(ctx).record_event(
                DomainEvent.DEFINITION_PURGED, {"code": code, "actor": actor}, None
            )
            return len(stale_ids)

        return self._engine.execute(_command)

    def list_definitions(
        self, *, code_prefix: str = "", latest_only: bool = True
    ) -> list[dict[str, Any]]:
        """定义清单（默认每 code 只列最高 published；含 retired 标记）。"""

        def _command(ctx: CommandContext) -> Any:
            from sqlalchemy import select

            from flowork.persistence.orm import ProcessDefinitionRow as _Row

            stmt = select(_Row).order_by(_Row.code, _Row.major.desc(), _Row.revision.desc())
            if code_prefix:
                stmt = stmt.where(_Row.code.like(f"{code_prefix}%"))
            rows = ctx.session.scalars(stmt).all()
            if not latest_only:
                return [
                    {
                        "code": row.code,
                        "name": row.name,
                        "major": row.major,
                        "revision": row.revision,
                        "breaking": row.breaking,
                        "status": row.status,
                    }
                    for row in rows
                ]
            grouped: dict[str, dict[str, Any]] = {}
            for row in rows:
                summary = grouped.setdefault(
                    row.code,
                    {
                        "code": row.code,
                        "name": row.name,
                        "major": None,
                        "revision": None,
                        "breaking": False,
                        "status": "published",
                        "versions": 0,
                    },
                )
                summary["versions"] += 1
                if summary["major"] is None:
                    summary.update(
                        major=row.major,
                        revision=row.revision,
                        breaking=row.breaking,
                        status=row.status,
                        name=row.name or summary["name"],
                    )
                elif row.status == "published" and summary["status"] == "retired":
                    summary["status"] = "published"
            return list(grouped.values())

        return self._engine.execute(_command)

    def definition_drift(self, directory: str | Path) -> dict[str, list[dict[str, Any]]]:
        """git 目录与库对账：未部署/库多余/库落后 三张清单。"""
        import json as _json
        from pathlib import Path as _Path

        from flowork.definitions.definition import dump_definition, load_definition
        from flowork.definitions.versioning import content_hash_of

        def _command(ctx: CommandContext) -> Any:
            from sqlalchemy import select

            from flowork.persistence.orm import ProcessDefinitionRow as _Row

            file_hashes: dict[str, str] = {}
            for path in sorted(_Path(directory).glob("*.json")):
                try:
                    definition = load_definition(
                        _json.loads(path.read_text(encoding="utf-8")),
                        self._engine.config.node_registry,
                    )
                except (ValueError, DefinitionError):
                    continue
                file_hashes[definition.code] = content_hash_of(dump_definition(definition))
            # 每 code 取最高 published 版本的哈希
            db_latest: dict[str, str] = {}
            for row in ctx.session.scalars(
                select(_Row)
                .where(_Row.status == "published")
                .order_by(_Row.code, _Row.major.desc(), _Row.revision.desc())
            ):
                db_latest.setdefault(row.code, row.content_hash)

            undeployed = [
                {"code": code, "file": f"{code}.json"}
                for code in sorted(file_hashes.keys() - db_latest.keys())
            ]
            extra = [{"code": code} for code in sorted(db_latest.keys() - file_hashes.keys())]
            stale = [
                {"code": code, "file": f"{code}.json", "db_hash": db_latest[code][:12]}
                for code in sorted(file_hashes.keys() & db_latest.keys())
                if file_hashes[code] != db_latest[code]
            ]
            return {"undeployed": undeployed, "extra_in_db": extra, "stale_in_db": stale}

        return self._engine.execute(_command)
