import type {
  Catalog,
  DefinitionDetail,
  DefinitionSummary,
  DiffResult,
  DirectorySummary,
  DryRunResult,
  InstanceDetail,
  InstanceInfo,
  NodeKindMeta,
  ProcessVariable,
  PublishResult,
  RouteDecision,
  SimulationReport,
  Spec,
  TimelineEntry,
  ValidateIssue,
  VariableChange,
} from './types'
import { designerConfig } from './config'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${designerConfig.apiBase()}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  const text = await resp.text()
  const body = text ? JSON.parse(text) : null
  if (!resp.ok) {
    const detail = body?.detail
    const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail ?? resp.statusText))
    ;(err as any).payload = detail
    ;(err as any).status = resp.status
    throw err
  }
  return body as T
}

export const api = {
  nodeKinds: () => request<NodeKindMeta[]>('/api/node-kinds'),

  listDefinitions: () => request<DefinitionSummary[]>('/api/definitions'),

  getDefinition: (code: string) => request<DefinitionDetail>(`/api/definitions/${code}`),

  saveDraft: (code: string, spec: Spec) =>
    request<{ code: string; version: number; status: string }>(`/api/definitions/${code}`, {
      method: 'PUT',
      body: JSON.stringify(spec),
    }),

  publish: (code: string) =>
    request<PublishResult>(`/api/definitions/${code}/publish`, { method: 'POST' }),

  validate: (spec: Spec, deploy = false) =>
    request<{ ok: boolean; issues: ValidateIssue[] }>(
      `/api/validate${deploy ? '?deploy=true' : ''}`,
      { method: 'POST', body: JSON.stringify(spec) },
    ),

  dryRun: (
    spec: Spec,
    variables: Record<string, unknown>,
    assumptions?: Record<string, string>,
  ) =>
    request<DryRunResult>('/api/dry-run', {
      method: 'POST',
      body: JSON.stringify({ spec, variables, assumptions }),
    }),

  catalog: () => request<Catalog>('/api/catalog'),

  formulaPreview: (formula: string, variables: Record<string, unknown>) =>
    request<{ ok: boolean; result?: boolean; error?: string }>('/api/formula/preview', {
      method: 'POST',
      body: JSON.stringify({ formula, variables }),
    }),

  diffDraft: (code: string, spec: Spec) =>
    request<DiffResult>(`/api/definitions/${code}/diff`, {
      method: 'POST',
      body: JSON.stringify(spec),
    }),

  // —— 审计数据面(设计器插件计划批 A/批 B 消费) ——

  /** 整图模拟(主场景=当前草稿,含未保存编辑;引擎真跑但零副作用;controls=决策/桩/等待假设,可选) */
  simulate: (
    code: string,
    spec: Spec,
    variables: Record<string, unknown>,
    controls?: Record<string, unknown>,
  ) =>
    request<SimulationReport>(`/api/definitions/${code}/simulate`, {
      method: 'POST',
      body: JSON.stringify(controls ? { spec, variables, controls } : { spec, variables }),
    }),

  instances: (params: { state?: string; definition_code?: string; include_simulated?: boolean }) => {
    const qs = new URLSearchParams()
    if (params.state) qs.set('state', params.state)
    if (params.definition_code) qs.set('definition_code', params.definition_code)
    if (params.include_simulated) qs.set('include_simulated', 'true')
    const query = qs.toString()
    return request<InstanceInfo[]>(`/api/instances${query ? `?${query}` : ''}`)
  },

  instanceDetail: (id: string) => request<InstanceDetail>(`/api/instances/${id}`),

  timeline: (id: string) => request<TimelineEntry[]>(`/api/instances/${id}/timeline`),

  routeDecisions: (id: string) => request<RouteDecision[]>(`/api/instances/${id}/route-decisions`),

  variableHistory: (id: string) => request<VariableChange[]>(`/api/instances/${id}/variable-history`),

  // —— 元数据下发(批 D:下拉辅助数据源) ——

  handlers: () => request<{ handlers: string[] }>('/api/handlers'),

  directorySummary: () =>
    request<DirectorySummary>('/api/directory/summary'),

  // —— 排练(批 3):simulated 实例的人点驱动 ——

  startRehearsal: (
    code: string,
    spec: Spec,
    variables: Record<string, unknown>,
    startUser: string,
  ) =>
    request<RehearsalState>(`/api/definitions/${code}/rehearsal`, {
      method: 'POST',
      body: JSON.stringify({ spec, variables, start_user: startUser }),
    }),

  rehearsalStep: (instanceId: string, step: Record<string, unknown>) =>
    request<RehearsalState>(`/api/rehearsals/${instanceId}/step`, {
      method: 'POST',
      body: JSON.stringify(step),
    }),

  rehearsalState: (instanceId: string) =>
    request<RehearsalState>(`/api/rehearsals/${instanceId}`),

  // —— 场景回归(批 4):脚本 CRUD + 运行 ——

  listScenarios: (code: string) =>
    request<ScenarioSummary[]>(`/api/definitions/${code}/scenarios`),

  saveScenario: (code: string, body: Record<string, unknown>) =>
    request<ScenarioSummary>(`/api/definitions/${code}/scenarios`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getScenario: (id: string) => request<ScenarioDetail>(`/api/scenarios/${id}`),

  deleteScenario: (id: string) =>
    request<{ deleted: string }>(`/api/scenarios/${id}`, { method: 'DELETE' }),

  runScenario: (id: string, spec?: Spec) =>
    request<ScenarioReport>(`/api/scenarios/${id}/run`, {
      method: 'POST',
      body: JSON.stringify(spec ? { spec } : {}),
    }),
}

/** 排练步单(与后端 rehearsal_state 同构;报告/任务行字段按需读) */
export type RehearsalTask = {
  id: string
  instance_id: string
  activity_code: string
  name: string
  assignee: string
  task_type: string
  state: string
  available_actions: string[]
}

export type RehearsalState = {
  instance_id: string
  instance: { id: string; state: string; definition_code: string; simulated?: boolean }
  tasks: RehearsalTask[]
  task_history: {
    node: string
    assignee: string
    acted_by: string
    outcome: string
    comment: string
    created_at: string
  }[]
  routes: { node: string; selected: string[]; edges: unknown[]; snapshot: unknown }[]
  variables: Record<string, unknown>
  variable_history: {
    name: string
    old: unknown
    new: unknown
    source: string
    acted_by: string | null
    changed_at: string
  }[]
  waiting: { node: string; instance_id: string }[]
}

export type ScenarioSummary = {
  id: string
  definition_code: string
  name: string
  description: string
  step_count: number
  assertion_count: number
  last_ok: boolean | null
  last_run_at: string | null
  last_summary: string
  updated_at: string
}

export type ScenarioDetail = ScenarioSummary & { script: Record<string, unknown> }

export type ScenarioReport = {
  scenario: string
  instance_id: string
  final_state: string
  ok: boolean
  steps: { index: number; kind: string; node?: string; action?: string; detail?: unknown }[]
  assertions: {
    type: string
    ok: boolean
    actual: unknown
    expected: Record<string, unknown>
    message: string
  }[]
  coverage: {
    nodes?: { covered: string[]; missing: string[]; total: number }
    edges?: { covered: string[]; missing: string[]; total: number }
  }
  notes: string[]
  advanced_minutes: number
}

/** 流程变量 → dry-run / 公式试算用的样例值表（sample 优先，否则按类型给安全默认） */
export function sampleVariables(variables: ProcessVariable[] | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const v of variables ?? []) {
    if (v.sample !== undefined && v.sample !== null) {
      out[v.name] = v.sample
      continue
    }
    out[v.name] = v.type === 'number' ? 0 : v.type === 'boolean' ? false : v.type === 'list' ? [] : ''
  }
  return out
}
