/** Spec ↔ React Flow 元素适配:spec 是唯一事实源,RF 元素全部派生 */

import { edgeId, type NodeKindMeta, type Spec, type ValidateIssue } from './types'

export type NodeData = {
  code: string
  name: string
  kind: string
  routeMode: string | null
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

const SIZE: Record<string, { w: number; h: number }> = {
  rect: { w: 164, h: 56 },
  circle: { w: 56, h: 56 },
}

export const nodeSize = (meta?: NodeKindMeta) => SIZE[meta?.ui?.shape ?? 'rect']

/** 条件文本摘要:结构化 groups → "amount > 10000 && x == 1 / …";默认边 → 默认 */
export function conditionSummary(flow: Spec['flows'][number]): string {
  const parts: string[] = []
  if (flow.is_default) parts.push('默认')
  const cond = flow.condition
  if (cond?.groups?.length) {
    const groupTexts = cond.groups.map((group) =>
      group.map((item) => `${item.left} ${OPERATOR_LABEL[item.operator] ?? item.operator} ${formatRight(item)}`).join(' 且 '),
    )
    parts.push(groupTexts.join(' 或 '))
  }
  if (cond?.formula) parts.push(cond.formula)
  return parts.join(' · ') || ''
}

function formatRight(item: { operator: string; right: unknown }): string {
  const right = item.right
  if (item.operator === 'is_empty' || item.operator === 'is_not_empty') return ''
  if (Array.isArray(right)) return `[${right.map(String).join(', ')}]`
  return typeof right === 'string' ? `"${right}"` : String(right ?? '')
}

export const OPERATOR_LABEL: Record<string, string> = {
  eq: '==', ne: '!=', gt: '>', ge: '>=', lt: '<', le: '<=',
  in: '属于', not_in: '不属于', contains: '包含',
  starts_with: '前缀', is_empty: '为空', is_not_empty: '非空',
}

export const OPERATOR_OPTIONS: [string, string][] = [
  ['eq', '等于 =='], ['ne', '不等于 !='], ['gt', '大于 >'], ['ge', '大于等于 >='],
  ['lt', '小于 <'], ['le', '小于等于 <='], ['in', '属于(列表)'], ['not_in', '不属于(列表)'],
  ['contains', '包含'], ['starts_with', '前缀匹配'], ['is_empty', '为空'], ['is_not_empty', '非空'],
]

export function toFlowElements(
  spec: Spec,
  metaByKind: Record<string, NodeKindMeta>,
  issues: ValidateIssue[],
  selected: { type: 'node' | 'edge'; id: string } | null,
  pendingConnect: string | null,
  hit?: DryRunHit | null,
) {
  const nodeIssues = new Map<string, 'error' | 'warning'>()
  for (const issue of issues) {
    if (!issue.node) continue
    const prev = nodeIssues.get(issue.node)
    if (issue.level === 'error' || (!prev && issue.level === 'warning')) {
      nodeIssues.set(issue.node, issue.level)
    }
  }

  const nodes = spec.nodes.map((n) => {
    const meta = metaByKind[n.kind]
    const size = nodeSize(meta)
    const pos = spec.layout[n.code] ?? { x: 0, y: 0 }
    return {
      id: n.code,
      type: `fk_${meta?.ui?.shape ?? 'rect'}`,
      position: { x: pos.x, y: pos.y },
      selected: selected?.type === 'node' && selected.id === n.code,
      data: {
        code: n.code,
        name: n.name || n.code,
        kind: n.kind,
        routeMode: typeof n.route_mode === 'string' ? n.route_mode : null,
        meta,
        issueLevel: nodeIssues.get(n.code) ?? null,
        pendingConnect: pendingConnect === n.code,
        hit: hit?.nodes.has(n.code) ?? false,
      } satisfies NodeData,
      style: { width: size.w, height: size.h },
      draggable: true,
      selectable: true,
    }
  })

  const edges = spec.flows.map((f) => {
    const id = edgeId(f.source, f.target)
    const summary = conditionSummary(f)
    const isSelected = selected?.type === 'edge' && selected.id === id
    const isHit = hit?.edges.has(id) ?? false
    return {
      id,
      source: f.source,
      target: f.target,
      type: 'fk_smooth',
      selected: isSelected,
      reconnectable: true,
      // 命中带略窄于默认 20:减少多条边交汇时改道手柄被相邻边命中带遮挡
      interactionWidth: 14,
      label: summary || undefined,
      labelShowBg: true,
      labelBgPadding: [6, 3] as [number, number],
      labelBgBorderRadius: 6,
      labelBgStyle: {
        fill: isSelected ? '#2f6bff' : isHit ? '#16a34a' : '#ffffff',
        fillOpacity: 0.95,
        stroke: isSelected ? '#2f6bff' : isHit ? '#16a34a' : '#dbe3ef',
        strokeWidth: 1,
      },
      labelStyle: {
        fill: isSelected || isHit ? '#ffffff' : '#475569',
        fontSize: 11,
        fontWeight: isSelected || isHit ? 600 : 400,
      },
      style: {
        stroke: isSelected ? '#2f6bff' : isHit ? '#16a34a' : '#9aa8bd',
        strokeWidth: isSelected || isHit ? 2.2 : 1.6,
      },
      markerEnd: {
        type: 'arrowclosed' as const,
        width: 16,
        height: 16,
        color: isSelected ? '#2f6bff' : isHit ? '#16a34a' : '#9aa8bd',
      },
      data: { flow: f, hit: isHit },
    }
  })

  return { nodes, edges }
}
