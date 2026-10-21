"""RepositoryService：流程定义的部署 / 校验 / 查询（deploy 即 publish）。

发布即不可变；实例钉快照；默认不迁移。deploy 事务内、写库前完成破坏性判定
（R1–R4）；内容哈希与已发布版本相同 → 幂等去重（不产生新版本、不发事件）；
并发撞车由 (code, major, revision) 唯一索引兜底。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError
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
from flowork.engine.repository_read import RepositoryReadMixin
from flowork.engine.repository_validation import RepositoryValidationMixin
from flowork.errors import FloworkError
from flowork.events.domain import DomainEvent

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["RepositoryService"]


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex


class RepositoryService(
    DesignerDraftSupportMixin,
    DefinitionLifecycleMixin,
    RepositoryValidationMixin,
    RepositoryReadMixin,
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
            target_major: 维护分支——在该 major 的最新 revision 上续发。
            force_major_reason / allow_minor_reason: 覆盖破坏性判定，必须带 reason（进事件）。
        """

        def _command(ctx: CommandContext) -> VersionVerdict:
            from sqlalchemy import select

            from flowork.definitions.validator import assert_valid
            from flowork.persistence.orm import ProcessDefinitionRow as _Row

            assert_valid(
                definition, self._engine.config.node_registry
            )  # org_configured 联动在 validate()（deploy 保持强校验语义）
            # 部署期 L3/L4：引用完整性 + 目录实体存在性
            # ——默认 WARNING 留痕（可见优于静默），strict_deploy_validation 升 ERROR 拦发布。
            from flowork.definitions.validator import IssueLevel
            from flowork.definitions.validator_deploy import check_deploy_references

            # L4 钩子：优先 subject_exists（设计名），回落 user_exists
            # （DirectoryStore 的实名——2026-10-21 review P2：修前只探前者，
            # 两个内置目录都没实现，L4 检查全程空转）
            hook = getattr(self._engine.config.org, "subject_exists", None) or getattr(
                self._engine.config.org, "user_exists", None
            )
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
            # 哈希按语义字段计算；落库内容补上判定出的版本身份
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
                return verdict  # 幂等去重：不落库、不发事件（降噪）

            if base is None:
                # 新 code 但既有行（如全部 retired）：恢复部署须越过既有最高版本
                # 恢复 retired＝重新 deploy 为新版本
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
            # 事件载荷：code, major, revision, breaking, triggered_rules,
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

        try:
            result: VersionVerdict = self._engine.execute(_command)
        except IntegrityError as exc:
            # 并发撞 (code, major, revision) 唯一索引（两副本同刻 deploy）：
            # 归一为可编程处理的冲突错误——内容相同的重试会命中幂等去重
            # （identical_to verdict），不同内容则重判版本
            from flowork.errors import DefinitionConflictError

            raise DefinitionConflictError(
                f"并发部署冲突（{definition.code} 版本唯一索引撞车，请重试）：{exc.orig}"
            ) from exc
        return result

    def _baseline(
        self, session: Session, code: str, target_major: int | None
    ) -> ProcessDefinition | None:
        """比较基线：默认最高 published；维护分支＝该 major 的最新 revision。

        显式 target_major 但该 major 从未发布 → 报错（不静默退到全局最高版本）。
        """
        from sqlalchemy import select

        from flowork.persistence.orm import ProcessDefinitionRow as _Row

        stmt = select(_Row).where(_Row.code == code, _Row.status == "published")
        if target_major is None:
            stmt = stmt.order_by(_Row.major.desc(), _Row.revision.desc()).limit(1)
        else:
            stmt = stmt.where(_Row.major == target_major).order_by(_Row.revision.desc()).limit(1)
        row = session.scalars(stmt).first()
        if row is None:
            if target_major is not None:
                raise FloworkError(
                    f"target_major={target_major} 下没有已发布版本："
                    f"维护分支须指向定义 {code} 已发布过的 major"
                )
            return None
        return load_definition(json.loads(row.content), self._engine.config.node_registry)
