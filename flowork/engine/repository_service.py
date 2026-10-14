"""RepositoryService：流程定义的部署 / 校验 / 查询（spec §6：deploy 即 publish）。

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
from flowork.definitions.validator import InvalidDefinition
from flowork.definitions.versioning import VersionVerdict, compute_verdict, content_hash_of
from flowork.engine.definition_lifecycle import DefinitionLifecycleMixin
from flowork.engine.designer_drafts import DesignerDraftSupportMixin
from flowork.engine.ops import CommandContext
from flowork.engine.repository_validation import RepositoryValidationMixin
from flowork.errors import DefinitionNotPublishedError
from flowork.events.domain import DomainEvent

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine
    from flowork.persistence.orm import ProcessDefinitionRow

__all__ = ["RepositoryService"]


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex


class RepositoryService(
    DesignerDraftSupportMixin, DefinitionLifecycleMixin, RepositoryValidationMixin
):
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
            # 部署期 L3/L4（spec §6）：引用完整性 + 目录实体存在性
            # ——默认 WARNING 留痕（可见优于静默），strict_deploy_validation 升 ERROR 拦发布。
            from flowork.definitions.validator import IssueLevel
            from flowork.definitions.validator_deploy import check_deploy_references

            hook = getattr(self._engine.config.org, "subject_exists", None)  # L4 钩子
            deploy_issues = check_deploy_references(
                definition,
                session=ctx.session,
                handlers=self._engine.handlers.names(),
                subject_exists=hook if callable(hook) else None,
                strict=self._engine.config.strict_deploy_validation,
            )
            blocking = [i for i in deploy_issues if i.level == IssueLevel.ERROR]
            if blocking:
                raise InvalidDefinition(blocking)

            # 规范化 round-trip：哈希与构造路径无关（bare build 薄形态/registry 全形态一致）
            content = dump_definition(definition)
            canonical = dump_definition(load_definition(content, self._engine.config.node_registry))
            digest = content_hash_of(canonical)
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

            if base is None:
                # 新 code 但既有行（如全部 retired）：恢复部署须越过既有最高版本
                # （spec §6.2 恢复 retired＝重新 deploy 为新版本）
                from dataclasses import replace as _replace

                existing = ctx.session.execute(
                    select(_Row.major, _Row.revision)
                    .where(_Row.code == definition.code)
                    .order_by(_Row.major.desc(), _Row.revision.desc())
                    .limit(1)
                ).first()
                if existing is not None:
                    verdict = _replace(
                        verdict, next_major=existing.major, next_revision=existing.revision + 1
                    )

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

    # —— 查询（校验方法在 repository_validation.Mixin：validate / validate_deploy_preview）——

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
