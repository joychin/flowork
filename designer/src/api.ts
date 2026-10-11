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

  /** 整图模拟(主场景=当前草稿,含未保存编辑;引擎真跑但零副作用) */
  simulate: (code: string, spec: Spec, variables: Record<string, unknown>) =>
    request<SimulationReport>(`/api/definitions/${code}/simulate`, {
      method: 'POST',
      body: JSON.stringify({ spec, variables }),
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
