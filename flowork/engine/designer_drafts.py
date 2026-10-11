"""设计器工作副本支撑（v2 §6.2）：wf_designer_draft 表的读写与发布衔接。

RepositoryService 经 mixin 组合（engine.repository 入口不变）；编辑态在设计器
本地，独立于引擎定义生命周期表（draft 已取消）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from flowork.engine.ops import CommandContext

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition
    from flowork.definitions.versioning import VersionVerdict
    from flowork.engine.engine import ProcessEngine

__all__ = ["DesignerDraftSupportMixin"]


class DesignerDraftSupportMixin:
    """RepositoryService 的设计器草稿面。"""

    _engine: ProcessEngine

    # —— 设计器支撑：工作副本（wf_designer_draft，独立于引擎定义生命周期）——

    def deploy(self, definition: ProcessDefinition, *, published_by: str = "designer") -> Any:
        """由宿主类提供（RepositoryService.deploy）。"""
        raise NotImplementedError

    def update_draft(self, definition: ProcessDefinition) -> int:
        """保存设计器工作副本：就地覆盖，seq 单调递增。返回草稿序号。"""
        from flowork.definitions.definition import dump_definition
        from flowork.persistence.orm import DesignerDraftRow as _Draft

        def _command(ctx: CommandContext) -> Any:
            content = json.dumps(dump_definition(definition), ensure_ascii=False)
            draft = ctx.session.get(_Draft, definition.code)
            if draft is None:
                ctx.session.add(
                    _Draft(
                        code=definition.code,
                        name=definition.name,
                        content=content,
                        seq=1,
                        open=True,
                    )
                )
                ctx.session.flush()
                return 1
            # 覆盖保存不膨胀序号；发布消费后再存才递增（衔接旧 draft 语义）
            draft.content = content
            draft.name = definition.name
            draft.seq = draft.seq + 1 if not draft.open else draft.seq
            draft.open = True
            ctx.session.flush()
            return draft.seq

        result: int = self._engine.execute(_command)
        return result

    def designer_draft(self, code: str) -> dict[str, Any] | None:
        """取待发布（open）的工作副本：{code, name, seq, spec}；无则 None。"""
        from flowork.persistence.orm import DesignerDraftRow as _Draft

        def _command(ctx: CommandContext) -> Any:
            draft = ctx.session.get(_Draft, code)
            if draft is None or not draft.open:
                return None
            return {
                "code": draft.code,
                "name": draft.name,
                "seq": draft.seq,
                "spec": json.loads(draft.content),
            }

        result: dict[str, Any] | None = self._engine.execute(_command)
        return result

    def consume_draft(self, code: str) -> None:
        """发布后消费工作副本（seq 保留以延续编号）。"""
        from flowork.persistence.orm import DesignerDraftRow as _Draft

        def _command(ctx: CommandContext) -> Any:
            draft = ctx.session.get(_Draft, code)
            if draft is not None:
                draft.open = False

        self._engine.execute(_command)

    def deploy_draft(self, code: str, *, published_by: str = "designer") -> VersionVerdict:
        """发布设计器工作副本（无待发布草稿时报定义不存在）。"""
        from flowork.definitions.definition import load_definition
        from flowork.errors import DefinitionNotPublishedError

        draft = self.designer_draft(code)
        if draft is None:
            raise DefinitionNotPublishedError(f"定义 {code} 无待发布草稿")
        definition = load_definition(draft["spec"], self._engine.config.node_registry)
        verdict: VersionVerdict = self.deploy(definition, published_by=published_by)
        self.consume_draft(code)
        return verdict

    def latest_row(self, code: str) -> dict[str, Any] | None:
        """取某 code 的最新状态摘要（待发布草稿优先）；无则 None。"""
        from sqlalchemy import select

        from flowork.persistence.orm import DesignerDraftRow as _Draft
        from flowork.persistence.orm import ProcessDefinitionRow as _Row

        def _command(ctx: CommandContext) -> Any:
            draft = ctx.session.get(_Draft, code)
            if draft is not None and draft.open:
                return {"version": draft.seq, "status": "draft", "name": draft.name}
            row = ctx.session.scalars(
                select(_Row)
                .where(_Row.code == code)
                .order_by(_Row.major.desc(), _Row.revision.desc())
                .limit(1)
            ).first()
            if row is None:
                return None
            return {"version": row.major, "status": row.status, "name": row.name}

        result: dict[str, Any] | None = self._engine.execute(_command)
        return result

    def published_versions(self, code: str) -> list[dict[str, Any]]:
        """某 code 的已发布版本列表（新→旧；设计器插件计划 A4 版本面数据源）。"""
        from sqlalchemy import select

        from flowork.persistence.orm import ProcessDefinitionRow as _Row

        def _command(ctx: CommandContext) -> Any:
            rows = ctx.session.scalars(
                select(_Row)
                .where(_Row.code == code, _Row.status == "published")
                .order_by(_Row.major.desc(), _Row.revision.desc())
            )
            return [
                {
                    "major": row.major,
                    "revision": row.revision,
                    "name": row.name,
                    "breaking": row.breaking,
                    "published_at": (row.published_at or row.created_at).isoformat(
                        timespec="seconds"
                    ),
                    "published_by": row.published_by,
                }
                for row in rows
            ]

        result: list[dict[str, Any]] = self._engine.execute(_command)
        return result

    def list_summaries(self) -> list[dict[str, Any]]:
        """按 code 汇总定义列表（待发布草稿序号 / 最高已发布 major / 更新时间）。"""
        from sqlalchemy import select

        from flowork.persistence.orm import DesignerDraftRow as _Draft
        from flowork.persistence.orm import ProcessDefinitionRow as _Row

        def _command(ctx: CommandContext) -> Any:
            grouped: dict[str, dict[str, Any]] = {}
            for row in ctx.session.scalars(select(_Row).order_by(_Row.code)):
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
                current = summary["published_version"]
                if current is None or row.major > current:
                    summary["published_version"] = row.major
                    summary["name"] = row.name or summary["name"]
                    summary["updated_at"] = (row.published_at or row.created_at).isoformat(
                        timespec="seconds"
                    )
            for draft in ctx.session.scalars(select(_Draft)):
                summary = grouped.setdefault(
                    draft.code,
                    {
                        "code": draft.code,
                        "name": draft.name,
                        "draft_version": None,
                        "published_version": None,
                        "updated_at": None,
                    },
                )
                if draft.open:
                    summary["draft_version"] = draft.seq
                summary["name"] = draft.name or summary["name"]
                summary["updated_at"] = draft.updated_at.isoformat(timespec="seconds")
            return sorted(grouped.values(), key=lambda s: s["code"])

        result: list[dict[str, Any]] = self._engine.execute(_command)
        return result
