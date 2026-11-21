"""flowork：可嵌入的 BPM 流程引擎。

内核语义以 Token 令牌模型为基础，命名对齐 BPMN 词汇。
"""

import logging

__version__ = "0.1.0"

# 库礼仪（logging HOWTO）：未配置 handler 时静默且不警告——「要不要出声、
# 用什么日志后端」的决定权留给宿主（经 logging.getLogger("flowork") 挂
# handler / 设 level 接管，接入食谱见 docs/integration.md「接入你的日志体系」）。
# 内核永远不做 basicConfig。命名纪律（logger 名 = 模块全路径）由
# tests/test_logging_surface.py 静态守卫。
logging.getLogger(__name__).addHandler(logging.NullHandler())
