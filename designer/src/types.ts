/** Spec 与 API 类型(与引擎 pydantic 模型同构) */

export type NodePosition = { x: number; y: number }

export type SequenceFlow = {
  source: string
  target: string
  name?: string
  is_default?: boolean
  /** CEL 公式（条件单轨化，spec §4.1）；null＝无条件 */
  condition?: string | null
}

export type SpecNode = Record<string, unknown> & {
  code: string
  name?: string
  kind: string
}

/** 流程变量声明（流程设置面板编辑；dry-run 取样 / 变量选择器 / 校验预警的数据源） */
export type ProcessVariable = {
  name: string
  type: 'string' | 'number' | 'boolean' | 'list'
  label?: string
  sample?: unknown
}

export type Spec = {
  code: string
  name: string
  version?: number
  status?: string
  nodes: SpecNode[]
  flows: SequenceFlow[]
  variables?: ProcessVariable[]
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
export type FormGroup = { title: string; fields: string[]; collapsed?: boolean }

export type NodeKindMeta = {
  kind: string
  ui: {
    name?: string
    category?: string
    shape?: 'rect' | 'circle'
    color?: string
    icon?: string
    palette?: { label: string; defaults: Record<string, unknown> }[]
    form_groups?: FormGroup[]
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

/** /api/dry-run 返回：路由预演路径 */
export type DryRunStep = {
  node: string
  via_source: string | null
  via_reason: string
  note: string
}

export type DryRunResult = {
  ok: boolean
  steps: DryRunStep[]
  warnings: string[]
  blocked: string[]
}

/** /api/definitions/{code}/diff 返回：发布前语义变更清单 */
export type DiffChange = {
  type: 'meta' | 'node' | 'flow' | 'variable'
  action: 'added' | 'removed' | 'changed' | 'info'
  key: string
  detail: string
}

export type DiffResult = {
  base_version: number | null
  first_publish: boolean
  has_changes: boolean
  changes: DiffChange[]
}

export const edgeId = (source: string, target: string) => `${source}->${target}`

export const parseEdgeId = (id: string) => {
  const [source, target] = id.split('->')
  return { source, target }
}
