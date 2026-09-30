/** Spec 与 API 类型(与引擎 pydantic 模型同构) */

export type NodePosition = { x: number; y: number }

export type ConditionItem = {
  left: string
  operator: string
  right: unknown
}

export type FlowCondition = {
  groups: ConditionItem[][] // 组间 OR、组内 AND
  formula?: string | null
}

export type SequenceFlow = {
  source: string
  target: string
  name?: string
  is_default?: boolean
  condition?: FlowCondition | null
}

export type SpecNode = Record<string, unknown> & {
  code: string
  name?: string
  kind: string
}

export type Spec = {
  code: string
  name: string
  version?: number
  status?: string
  nodes: SpecNode[]
  flows: SequenceFlow[]
  layout: Record<string, NodePosition>
}

export type ValidateIssue = {
  level: 'error' | 'warning'
  rule: string
  message: string
  node: string | null
  flow: string | null
}

export type DefinitionSummary = {
  code: string
  name: string
  draft_version: number | null
  published_version: number | null
  updated_at: string | null
}

export type DefinitionDetail = {
  code: string
  name: string
  draft_version: number | null
  published_version: number | null
  spec: Spec
}

/** /api/node-kinds 返回的节点类型元数据 */
export type NodeKindMeta = {
  kind: string
  ui: {
    name?: string
    category?: string
    shape?: 'rect' | 'circle' | 'diamond'
    color?: string
    icon?: string
    palette?: { label: string; defaults: Record<string, unknown> }[]
  }
  form_exclude: string[]
  schema: JSONSchema
}

export type JSONSchema = {
  type?: string
  properties?: Record<string, JSONSchema>
  required?: string[]
  enum?: unknown[]
  title?: string
  description?: string
  default?: unknown
  $ref?: string
  anyOf?: JSONSchema[]
  items?: JSONSchema
  $defs?: Record<string, JSONSchema>
  additionalProperties?: boolean | JSONSchema
  format?: string
}

export const edgeId = (source: string, target: string) => `${source}->${target}`

export const parseEdgeId = (id: string) => {
  const [source, target] = id.split('->')
  return { source, target }
}
