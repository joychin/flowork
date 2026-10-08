"""all 汇聚放行门（kernel-v2 spec §2.3 可达性语义）。

等价总判据：**除已停在本节点的令牌外，没有任何未完令牌能沿图到达本节点** →
全部入边满足，放行。可达性是结构判定（不评条件）——条件为假的在途分支最终会
落定，落定后即不再阻塞；已落定（finished/cancelled/revoked/failed）的旧令牌
一律不算数，循环迭代里上一轮的 settled 令牌不能让本轮提前放行。

对齐 Flowable InclusiveGatewayActivityBehavior（静态可达性＋活跃执行扫描）；
驳回归零、管理员 move_token 等直调 ``activate`` 的路径不经本门（语义保持）。
遍历实现下沉 :mod:`flowork.definitions.graph`（能力补足 P0-3）——设计时图级
lint 与本门同源，杜绝镜像漂移；定义不可变，若 profile 需要可再按定义内容键
缓存（spec §2.3 实现提示）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flowork.definitions.graph import upstream_basin

if TYPE_CHECKING:
    from flowork.definitions.definition import ProcessDefinition

__all__ = ["should_release"]


def should_release(
    definition: ProcessDefinition, unfinished_elsewhere: set[str], node_code: str
) -> bool:
    """all 汇聚是否放行。

    Args:
        definition: 实例钉住的流程定义（不可变）。
        unfinished_elsewhere: 停在本节点**以外**的未完令牌所在节点编码集合
            （停靠本节点者由调用方剔除——它们正是等待放行的既有到达）。
        node_code: 汇聚节点编码。
    """
    return not (unfinished_elsewhere & upstream_basin(definition, node_code))
