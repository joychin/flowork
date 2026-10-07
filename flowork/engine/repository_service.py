"""RepositoryService：流程定义的部署 / 校验 / 查询（v2 §6：deploy 即 publish）。

发布即不可变；实例钉快照；默认不迁移。deploy 事务内、写库前完成破坏性判定
（R1–R4）；内容哈希与已发布版本相同 → 幂等去重（不产生新版本、不发事件）；
并发撞车由 (code, major, revision) 唯一索引兜底。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from flowork.definitions.definition import (
    ProcessDefinition,
    dump_definition,
    load_definition,
)
from flowork.definitions.validator import ValidateIssue, validate_definition
from flowork.definitions.versioning import VersionVerdict, compute_verdict, content_hash_of
from flowork.engine.ops import CommandContext
from flowork.errors import DefinitionNotPublishedError
from flowork.events.domain import DomainEvent

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessDefinitionRow

__all__ = ["RepositoryService"]


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex


class RepositoryService:
    """定义仓库服务：deploy（校验＋版本判定＋落库＋事件）/ 查询。"""

    def __init__(self, engine: ProcessEngine) -> None:
        self._engine = engine

    # —— 部署 ——

    def deploy(
        self,
        definition: ProcessDefinition,
        *,
        published_by: str = "system",
        target_major: int | None = None,
        force_major_reason: str = "",
        allow_minor_reason: str = "",
    ) -> VersionVerdict:
        """部署定义（新增与更新同一操作；纯数据写入，秒级生效）。

        Args:
            target_major: 维护分支——在该 major 的最新 revision 上续发（spec §6.2）。
            force_major_reason / allow_minor_reason: 覆盖破坏性判定，必须带 reason（进事件）。
        """

        def _command(ctx: CommandContext) -> VersionVerdict:
            from sqlalchemy import select

            from flowork.definitions.validator import assert_valid
            from flowork.persistence.orm import ProcessDefinitionRow as _Row

            assert_valid(
                definition, self._engine.config.node_registry
            )  # org_configured 联动在 validate()（deploy 保持强校验语义）

            content = dump_definition(definition)
            digest = content_hash_of(content)
            # 哈希按语义字段计算；落库内容补上判定出的版本身份（spec §6.6 审计）
            published = {
                (row.major, row.revision): row.content_hash
                for row in ctx.session.scalars(
                    select(_Row).where(_Row.code == definition.code, _Row.status == "published")
                )
            }

            base = self._baseline(ctx.session, definition.code, target_major)
            if force_major_reason and allow_minor_reason:
                raise ValueError("force_major 与 allow_minor 不可同时指定")
            override_kind = (
                "force_major"
                if force_major_reason
                else "allow_minor"
                if allow_minor_reason
                else None
            )
            verdict = compute_verdict(
                base,
                definition,
                digest,
                published_hashes=published,
                override_kind=override_kind,
                override_reason=force_major_reason or allow_minor_reason,
            )
            if verdict.identical_to is not None:
                return verdict  # 幂等去重：不落库、不发事件（降噪，spec §6.4）

            stored = dict(content)
            stored["major"] = verdict.next_major
            stored["revision"] = verdict.next_revision
            stored["status"] = "published"
            row = _Row(
                id=_new_id(),
                code=definition.code,
                major=verdict.next_major,
                revision=verdict.next_revision,
                status="published",
                breaking=verdict.breaking,
                name=definition.name,
                content=json.dumps(stored, ensure_ascii=False),
                content_hash=digest,
                published_by=published_by,
                published_at=self._engine.config.now(),
            )
            ctx.session.add(row)
            # 事件载荷（spec §6.4）：code, major, revision, breaking, triggered_rules,
            # content_hash, published_by, is_new_code, override?
            payload = {
                "code": definition.code,
                "major": verdict.next_major,
                "revision": verdict.next_revision,
                "breaking": verdict.breaking,
                "triggered_rules": [
                    {"rule": r.rule, "detail": r.detail} for r in verdict.triggered_rules
                ],
                "content_hash": digest,
                "published_by": published_by,
                "is_new_code": base is None,
                "override": verdict.override,
            }
            self._engine.kernel_for(ctx).record_event(
                DomainEvent.DEFINITION_DEPLOYED, payload, None
            )
            ctx.session.flush()
            return verdict

        result: VersionVerdict = self._engine.execute(_command)
        return result

    def verdict(
        self, definition: ProcessDefinition, *, target_major: int | None = None
    ) -> VersionVerdict:
        """版本判定预演（只判定不落库，spec §6.3——AI/CI 先看后果再 deploy）。"""

        def _command(ctx: CommandContext) -> VersionVerdict:
            from sqlalchemy import select

            from flowork.persistence.orm import ProcessDefinitionRow as _Row

            content = dump_definition(definition)
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

    def _baseline(
        self, session: Session, code: str, target_major: int | None
    ) -> ProcessDefinition | None:
        """比较基线：默认最高 published；维护分支＝该 major 的最新 revision。"""
        from sqlalchemy import select

        from flowork.persistence.orm import ProcessDefinitionRow as _Row

        stmt = select(_Row).where(_Row.code == code, _Row.status == "published")
        if target_major is None:
            stmt = stmt.order_by(_Row.major.desc(), _Row.revision.desc()).limit(1)
        else:
            stmt = stmt.where(_Row.major == target_major).order_by(_Row.revision.desc()).limit(1)
        row = session.scalars(stmt).first()
        if row is None:
            return None
        return load_definition(json.loads(row.content), self._engine.config.node_registry)

    # —— 校验 / 查询 ——

    def validate(self, definition: ProcessDefinition) -> list[ValidateIssue]:
        """设计时校验（不落库；组主体×目录状态联动检查，spec §9）。"""
        return validate_definition(
            definition,
            self._engine.config.node_registry,
            org_configured=self._engine.config.org is not None,
        )

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
            raise DefinitionNotPublishedError(
                f"已发布定义 {code}（major={major}, revision={revision}）不存在"
            )
        assert row is not None
        return row

    # —— 设计器支撑：工作副本（wf_designer_draft，独立于引擎定义生命周期）——

    def update_draft(self, definition: ProcessDefinition) -> int:
        """保存设计器工作副本：就地覆盖，seq 单调递增。返回草稿序号。"""
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
        draft = self.designer_draft(code)
        if draft is None:
            raise DefinitionNotPublishedError(f"定义 {code} 无待发布草稿")
        definition = load_definition(draft["spec"], self._engine.config.node_registry)
        verdict = self.deploy(definition, published_by=published_by)
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
