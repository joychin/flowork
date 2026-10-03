"""目录共享件：错误与校验（store/roles 共用，避免环导）。"""

from __future__ import annotations

import re

from flowork.errors import FloworkError

__all__ = ["DirectoryError"]

_CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class DirectoryError(FloworkError):
    """目录操作非法。"""


def _validate_code(kind: str, code: str) -> None:
    if not _CODE_PATTERN.match(code):
        raise DirectoryError(f"{kind}标识 {code!r} 非法（限字母数字与 _ -，长度 1-64）")


def _like_prefix(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
