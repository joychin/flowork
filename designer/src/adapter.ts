/** Spec ↔ React Flow 元素适配:spec 是唯一事实源,RF 元素全部派生。
 *  元素对象带内容签名缓存:未变化的节点/连线复用旧对象,React Flow 据此跳过重渲染 ——
 *  拖拽中校验/保存响应到达、点击选中、连线高亮等只重绘真正受影响的元素。 */

import type { Edge, Node as RFNode } from '@xyflow/react'
import { edgeId, type NodeKindMeta, type Spec, type ValidateIssue } from './types'

export type NodeData = {
  code: string
  name: string
  kind: string
  routeMode: string | null
  badge: string | null
  meta: NodeKindMeta | undefined
  issueLevel: 'error' | 'warning' | null
  pendingConnect: boolean
  hit: boolean
}

/** dry-run 预演高亮:命中的节点与连线(由 store.dryRun 派生) */
export type DryRunHit = { nodes: Set<string>; edges: Set<string> }

export function hitFromSteps(steps: { node: string; via_source: string | null }[]): DryRunHit {
  const nodes = new Set(steps.map((s) => s.node))
  const edges = new Set(
    steps.filter((s) => s.via_source).map((s) => edgeId(s.via_source!, s.node)),
  )
  return { nodes, edges }
}

/** 节点关键参数徽章:等待(等什么/等多久/竞速)、子流程(被调 code)、服务任务(action)。
    画布上一眼可辨,不必点开属性面板 —— 值来自节点字段,纯派生。 */
function badgeOf(n: Spec['nodes'][number]): string | null {
  const clip = (s: string, n = 14) => (s.length > n ? `${s.slice(0, n)}…` : s)
  if (n.kind === 'wait') {
    const msg = (n.message as { name?: string } | null | undefined)?.name
    const mins = (n.timeout as { after_minutes?: number } | null | undefined)?.after_minutes
    if (msg && mins != null) return `⚡ ${clip(String(msg))}·${mins}分`
    if (msg) return `✉ ${clip(String(msg))}`
    if (mins != null) return `⏱ ${mins}分`
    return '⚠ 未配置等待'
  }
  if (n.kind === 'call_activity') {
    const pc = typeof n.process_code === 'string' ? n.process_code : ''
    if (!pc) return '⚠ 未选子流程'
    const mi = n.multi_instance as { subjects?: unknown[] } | null | undefined
    return mi?.subjects?.length ? `↳ ${clip(pc)} ×${mi.subjects.length}` : `↳ ${clip(pc)}`
  }
  if (n.kind === 'service_task') {
    const a = typeof n.action === 'string' ? n.action : ''
    return a ? `⚙ ${clip(a)}` : '⚠ 未选动作'
  }
  return null
}

const SIZE: Record<string, { w: number; h: number }> = {
  rect: { w: 164, h: 56 },
  circle: { w: 56, h: 56 },
}

export const nodeSize = (meta?: NodeKindMeta) => SIZE[meta?.ui?.shape ?? 'rect']

/** 条件文本摘要（单轨 CEL）：公式原文截断；默认边 → 默认 */
export function conditionSummary(flow: Spec['flows'][number]): string {
  const parts: string[] = []
  if (flow.is_default) parts.push('默认')
  const cond = flow.condition
  if (typeof cond === 'string' && cond.trim()) {
    parts.push(cond.length > 40 ? `${cond.slice(0, 40)}…` : cond)
  }
  return parts.join(' · ') || ''
}

type Cached<T> = { sig: string; value: T }

/** 元素对象缓存:按定义 code 分代;Canvas 侧 useRef 持有 */
export type ElementsCache = {
  code: string
  nodes: Map<string, Cached<RFNode>>
  edges: Map<string, Cached<Edge>>
}

export function newElementsCache(): ElementsCache {
  return { code: '', nodes: new Map(), edges: new Map() }
}

export type Selection = { type: 'node' | 'edge'; id: string } | null

export function toFlowElements(
  spec: Spec,
  metaByKind: Record<string, NodeKindMeta>,
  issues: ValidateIssue[],
  selected: Selection,
  pendingConnect: string | null,
  hit: DryRunHit | null | undefined,
  cache: ElementsCache,
  splitTarget: string | null = null,
) {
  if (cache.code !== spec.code) {
    cache.nodes.clear()
    cache.edges.clear()
    cache.code = spec.code
  }

  const nodeIssues = new Map<string, 'error' | 'warning'>()
  for (const issue of issues) {
    if (!issue.node) continue
    const prev = nodeIssues.get(issue.node)
    if (issue.level === 'error' || (!prev && issue.level === 'warning')) {
      nodeIssues.set(issue.node, issue.level)
    }
  }

  // ui 元数据签名按 kind 缓存,避免逐节点 stringify
  const uiSigs = new Map<string, string>()
  const uiSig = (kind: string) => {
    let v = uiSigs.get(kind)
    if (v === undefined) {
      v = JSON.stringify(metaByKind[kind]?.ui ?? null)
      uiSigs.set(kind, v)
    }
    return v
  }

  const nodes = spec.nodes.map((n) => {
    const meta = metaByKind[n.kind]
    const size = nodeSize(meta)
    const pos = spec.layout[n.code] ?? { x: 0, y: 0 }
    const badge = badgeOf(n)
    const sig = [
      pos.x, pos.y, n.kind, n.name ?? '', typeof n.route_mode === 'string' ? n.route_mode : '',
      badge ?? '', nodeIssues.get(n.code) ?? '', pendingConnect === n.code ? 1 : 0,
      hit?.nodes.has(n.code) ? 1 : 0,
      selected?.type === 'node' && selected.id === n.code ? 1 : 0,
      uiSig(n.kind),
    ].join('|')
    const cached = cache.nodes.get(n.code)
    if (cached && cached.sig === sig) return cached.value
    const node: RFNode = {
      id: n.code,
      type: `fk_${meta?.ui?.shape ?? 'rect'}`,
      position: { x: pos.x, y: pos.y },
      width: size.w,
      height: size.h,
      // v12 受控模式:measured 只从用户 node 的 measured 字段 adopt(不回退 width/height)。
      // 节点尺寸由 SIZE 表确定,显式声明 → 打开定义后的测量窗口期(ResizeObserver 回填前)
      // 拖动不再是"未初始化"(error#015)死状态
      measured: { width: size.w, height: size.h },
      selected: selected?.type === 'node' && selected.id === n.code,
      data: {
        code: n.code,
        name: n.name || n.code,
        kind: n.kind,
        routeMode: typeof n.route_mode === 'string' ? n.route_mode : null,
        badge,
        meta,
        issueLevel: nodeIssues.get(n.code) ?? null,
        pendingConnect: pendingConnect === n.code,
        hit: hit?.nodes.has(n.code) ?? false,
      } satisfies NodeData,
      style: { width: size.w, height: size.h },
      // draggable/selectable 留 undefined 跟随画布级开关(nodesDraggable 等)——
      // RF v12 per-node 显式 true 会架空 readonly 的画布开关(review P0-2)
    }
    cache.nodes.set(n.code, { sig, value: node })
    return node
  })

  // 同端兄弟偏移:同一节点的多条入边(按源排序)/多条出边(按目标排序)端点垂直错开,
  // 否则它们的端点与改道拖点完全重叠,多线汇入时无法分辨要拖哪条
  const byTarget = new Map<string, string[]>() // target → 排序后的 source 列表
  const bySource = new Map<string, string[]>() // source → 排序后的 target 列表
  for (const f of spec.flows) {
    if (!byTarget.has(f.target)) byTarget.set(f.target, [])
    if (!bySource.has(f.source)) bySource.set(f.source, [])
    byTarget.get(f.target)!.push(f.source)
    bySource.get(f.source)!.push(f.target)
  }
  for (const list of byTarget.values()) list.sort()
  for (const list of bySource.values()) list.sort()
  const spread = (list: string[] | undefined, key: string) => {
    if (!list || list.length < 2) return 0
    const i = list.indexOf(key)
    return (i - (list.length - 1) / 2) * 18
  }

  const edges = spec.flows.map((f) => {
    const id = edgeId(f.source, f.target)
    const summary = conditionSummary(f)
    const isSelected = selected?.type === 'edge' && selected.id === id
    const isHighlighted = isSelected || splitTarget === id
    const isHit = hit?.edges.has(id) ?? false
    const srcOffset = spread(bySource.get(f.source), f.target)
    const tgtOffset = spread(byTarget.get(f.target), f.source)
    const sig = [f.source, f.target, summary, isHighlighted ? 1 : 0, isHit ? 1 : 0, srcOffset, tgtOffset, JSON.stringify(f)].join('|')
    const cached = cache.edges.get(id)
    if (cached && cached.sig === sig) return cached.value
    const edge: Edge = {
      id,
      source: f.source,
      target: f.target,
      type: 'fk_smooth',
      selected: isSelected,
      interactionWidth: 14,
      label: summary || undefined,
      labelShowBg: true,
      labelBgPadding: [6, 3] as [number, number],
      labelBgBorderRadius: 6,
      labelBgStyle: {
        fill: isHighlighted ? '#2f6bff' : isHit ? '#16a34a' : '#ffffff',
        fillOpacity: 0.95,
        stroke: isHighlighted ? '#2f6bff' : isHit ? '#16a34a' : '#dbe3ef',
        strokeWidth: 1,
      },
      labelStyle: {
        fill: isHighlighted || isHit ? '#ffffff' : '#475569',
        fontSize: 11,
        fontWeight: isHighlighted || isHit ? 600 : 400,
      },
      style: {
        stroke: isHighlighted ? '#2f6bff' : isHit ? '#16a34a' : '#9aa8bd',
        strokeWidth: isHighlighted || isHit ? 2.2 : 1.6,
      },
      markerEnd: {
        type: 'arrowclosed' as const,
        width: 16,
        height: 16,
        color: isHighlighted ? '#2f6bff' : isHit ? '#16a34a' : '#9aa8bd',
      },
      data: { flow: f, hit: isHit, highlight: isHighlighted, srcOffset, tgtOffset },
    }
    cache.edges.set(id, { sig, value: edge })
    return edge
  })

  return { nodes, edges }
}
