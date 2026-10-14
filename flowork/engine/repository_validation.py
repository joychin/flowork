"""定义校验面（RepositoryService 的校验方法 Mixin）。

设计时校验（validate）与部署期校验预演（validate_deploy_preview，编辑期前移）
都在这里：前者不落库，后者只读会话跑 L3+L4，同样不落库。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flowork.definitions.definition import ProcessDefinition
from flowork.definitions.validator import ValidateIssue, validate_definition

if TYPE_CHECKING:
    from flowork.engine.engine import ProcessEngine

__all__ = ["RepositoryValidationMixin"]


class RepositoryValidationMixin:
    """RepositoryService 的校验方法组（宿主类提供 _engine 属性）。"""

    _engine: ProcessEngine

    def validate(self, definition: ProcessDefinition) -> list[ValidateIssue]:
        """设计时校验（不落库；组主体×目录、CEL 函数注册表联动）。"""
        return validate_definition(
            definition,
            self._engine.config.node_registry,
            org_configured=self._engine.config.org is not None,
            expression_functions=self._engine.config.expression_functions,
        )

    def validate_deploy_preview(self, definition: ProcessDefinition) -> list[ValidateIssue]:
        """部署期校验预演（编辑期前移）：L3 引用完整性 + L4 目录实体存在性。

        只读、不落库；严格度随 ``EngineConfig.strict_deploy_validation``（缺省
        全部 WARNING——设计器把它们当「发布时会出现的提醒」呈现，不当错误拦编辑）。
        """

        def _command(ctx: Any) -> list[ValidateIssue]:
            from flowork.definitions.validator_deploy import check_deploy_references

            hook = getattr(self._engine.config.org, "subject_exists", None)
            issues = check_deploy_references(
                definition,
                session=ctx.session,
                handlers=self._engine.handlers.names(),
                subject_exists=hook if callable(hook) else None,
                strict=self._engine.config.strict_deploy_validation,
            )
            return list(issues)

        return self._engine.execute(_command)
