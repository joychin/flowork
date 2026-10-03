"""目录模块（M-A）：组织/用户持久化与 OrgDirectory 查询面。

竖切范围见 docs/product/engine-v1/01-身份与组织模型.md；角色、Agent、委托随 M-A 全量补充。
"""

from flowork.directory.orm import OrgUnitRow, OrgUserRow
from flowork.directory.store import DirectoryError, DirectoryStore, OrgUnitInfo, UserInfo

__all__ = [
    "DirectoryError",
    "DirectoryStore",
    "OrgUnitInfo",
    "OrgUnitRow",
    "OrgUserRow",
    "UserInfo",
]
