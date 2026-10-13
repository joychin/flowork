"""插件兼容声明守卫（收尾批 5，openface-plugin-design §4）。

每个官方插件位（rest/mcp/web + 两个词汇包）以 ``REQUIRES_FLOWORK_API``
声明依赖的门面契约版本，本测试断言 ≤ ``flowork.api.API_VERSION``——
门面升 major（删/改名符号）时红灯的插件须逐一升级声明并验证。
"""

from __future__ import annotations

import flowork.api
from flowork import contrib
from flowork.contrib import mcp as mcp_plugin
from flowork.contrib import numeric as numeric_plugin
from flowork.contrib import rest, web


def test_all_plugin_slots_declare_compatible_api_version() -> None:
    slots = {
        "contrib.rest": rest.REQUIRES_FLOWORK_API,
        "contrib.mcp": mcp_plugin.REQUIRES_FLOWORK_API,
        "contrib.web": web.REQUIRES_FLOWORK_API,
        "contrib（词汇包 cel_vocabulary）": contrib.REQUIRES_FLOWORK_API,
        "contrib.numeric_vocabulary": numeric_plugin.REQUIRES_FLOWORK_API,
    }
    for name, required in slots.items():
        assert required <= flowork.api.API_VERSION, (
            f"{name} 声明 REQUIRES_FLOWORK_API={required} > "
            f"API_VERSION={flowork.api.API_VERSION}——门面升 major 后须同步升级声明"
        )
