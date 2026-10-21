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

/** 消息启动声明(spec §7.1;表达式求值于消息上下文 payload.* + now) */
export type StartMessageDecl = {
  name: string
  correlation_key?: string
  input_mappings?: Record<string, string>
  start_user?: string
  business_key?: string
}

export type Spec = {
  code: string
  name: string
  version?: number
  status?: string
  nodes: SpecNode[]
  flows: SequenceFlow[]
  variables?: ProcessVariable[]
  /** 入参契约(JSON Schema 2020-12);发起/任务提交/节点输出三边界按它校验 */
  input?: Record<string, unknown> | null
  /** 实例时限:整单最大存活时间,到点按 strategy 处置 */
  deadline?: { after_minutes: number; strategy: 'mark' | 'cancel' | 'terminate' } | null
  /** 消息启动声明(随定义发布生效) */
  start_on_message?: StartMessageDecl[]
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

/** 运行形态（GET /api/runtime）：files=定义来自目录；db=来自设计器自己的会话库 */
export type RuntimeInfo = {
  source: 'db' | 'files'
  definitions_dir: string | null
  /** 会话库是否是临时的（内存）——设计器不碰用户运行时库，痕迹关掉就没了 */
  session_ephemeral: boolean
  /** 表达式函数来源说明（宿主自定义取人函数靠它带进设计器进程） */
  vocabulary_source: string
  /** 当前进程认识哪些函数——空数组＝没装配，用了自定义函数的流程跑不起来 */
  vocabulary_functions: string[]
}

/** 文件清单项（GET /api/files）：valid=false 时 error 说明为何装不进来 */
export type DefinitionFile = {
  file: string
  code: string | null
  name: string
  valid: boolean
  error: string
  updated_at: string | null
}

/** 定义来源：db=数据库定义；file=目录里的 JSON；local=浏览器里打开的本地文件（未入库） */
export type DefinitionSource = 'db' | 'file' | 'local'

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
    /** 单例标记:画布上已有该 kind 时添加入口置灰(校验要求恰好一个的类型,如 start) */
    singleton?: boolean
    palette?: { label: string; defaults: Record<string, unknown> }[]
    form_groups?: FormGroup[]
    /** 路径标签(点路径 → 中文),供 schema 表单按「父对象感知」显示字段名 */
    field_labels?: Record<string, string>
    /** 画布徽章/摘要声明(表达式按节点字段取值),供画布节点渲染关键参数 */
    badges?: { mode: string; field: string; values?: Record<string, string> }[]
    summary?: string
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

/** /api/catalog 返回：表达式词汇目录(F4,与 validator 同源) */
export type CatalogEntry = {
  name: string
  type?: string
  params?: string[]
  returns?: string
  label: string
  positions: string[]
  note: string
}

export type Catalog = { identifiers: CatalogEntry[]; functions: CatalogEntry[] }

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

/** /api/definitions/{code}/publish 返回：版本判定全貌(批 A3;破坏性明细在 triggered_rules) */
export type PublishResult = {
  code: string
  version: number
  revision: number
  breaking: boolean
  triggered_rules: { rule: string; detail: string }[]
  identical_to: [number, number] | null
  status: string
}

/** 版本判定预演（POST .../verdict）：与 publish 同一份判定，但只读不落库 */
export type VerdictPreview = {
  next_major: number
  next_revision: number
  breaking: boolean
  triggered_rules: { rule: string; detail: string }[]
  content_hash: string
  identical_to: [number, number] | null
  override: Record<string, string> | null
}

/** /api/definitions/{code}/simulate 返回:整图模拟报告(引擎 SimulationReport) */
export type SimulationReport = {
  instance_id: string
  definition_code: string
  final_state: string
  visited: string[]
  stubbed: string[]
  stalled_at: string | null
  route_decisions: { node: string; selected: string[] }[]
}

/** /api/instances 返回:实例概要 */
export type TokenInfo = {
  id: string
  token_no: number
  activity_code: string
  source_activity_code: string | null
  state: string
  item_count: number
  approve_count: number
  disapprove_count: number
  reject_flag: boolean
  approval_exit: string | null
  created_at: string | null
  finished_at: string | null
}

/** /api/directory/summary 返回:组织目录只读摘要(批 D1;未配置=无目录降级) */
export type DirectorySummary = {
  configured: boolean
  users: { id: string; department: string | null; roles: string[]; manager: string | null }[]
  departments: { code: string; name: string; parent: string | null }[]
  roles: string[]
}

export const edgeId = (source: string, target: string) => `${source}->${target}`

export const parseEdgeId = (id: string) => {
  const [source, target] = id.split('->')
  return { source, target }
}
