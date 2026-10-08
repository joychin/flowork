/** 「把新节点插到连线中间」的几何判定:纯数学最近距离,无 DOM/SVG 采样。
 *  smoothstep(右出左入)近似为三段正交线段:源水平段 / 中间垂直段 / 入目标水平段 ——
 *  对“落点是否在线上”的判定足够精确,且开销为微秒级,拖拽/拖放帧路径零负担。
 *  仅用于调色板拖入(显式插入意图);画布节点拖动不做插线检测,避免移动节点时误改拓扑。 */

import { nodeSize } from './adapter'
import type { NodeKindMeta, Spec } from './types'

function distToSegment(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax
  const dy = by - ay
  const len2 = dx * dx + dy * dy
  let t = len2 === 0 ? 0 : ((px - ax) * dx + (py - ay) * dy) / len2
  t = Math.max(0, Math.min(1, t))
  return Math.hypot(ax + t * dx - px, ay + t * dy - py)
}

export type EdgeHit = { id: string; source: string; target: string }

/** 找距 point 最近的连线;maxDist 判定半径收紧到 45 —— 需要视觉上“落在线上”才触发插入 */
export function findEdgeNear(
  spec: Spec,
  metaByKind: Record<string, NodeKindMeta>,
  point: { x: number; y: number },
  { maxDist = 45 }: { maxDist?: number } = {},
): EdgeHit | null {
  let best: EdgeHit | null = null
  let bestDist = maxDist
  for (const f of spec.flows) {
    const sPos = spec.layout[f.source]
    const tPos = spec.layout[f.target]
    if (!sPos || !tPos) continue
    const sNode = spec.nodes.find((n) => n.code === f.source)
    const tNode = spec.nodes.find((n) => n.code === f.target)
    if (!sNode || !tNode) continue
    const sSize = nodeSize(metaByKind[sNode.kind])
    const tSize = nodeSize(metaByKind[tNode.kind])
    // 出线口 = 源节点右缘中点,入线口 = 目标左缘中点(与 Handle 布局一致)
    const sx = sPos.x + sSize.w
    const sy = sPos.y + sSize.h / 2
    const tx = tPos.x
    const ty = tPos.y + tSize.h / 2
    const midX = (sx + tx) / 2
    const d = Math.min(
      distToSegment(point.x, point.y, sx, sy, midX, sy),
      distToSegment(point.x, point.y, midX, sy, midX, ty),
      distToSegment(point.x, point.y, midX, ty, tx, ty),
    )
    if (d < bestDist) {
      bestDist = d
      best = { id: `${f.source}->${f.target}`, source: f.source, target: f.target }
    }
  }
  return best
}
