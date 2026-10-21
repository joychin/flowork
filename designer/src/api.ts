import type {
  Catalog,
  DefinitionDetail,
  DefinitionFile,
  DefinitionSummary,
  DiffResult,
  DirectorySummary,
  DryRunResult,
  NodeKindMeta,
  ProcessVariable,
  PublishResult,
  RuntimeInfo,
  SimulationReport,
  Spec,
  ValidateIssue,
  VerdictPreview,
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

  /** 运行形态：files=开发形态（列表/保存走目录里的 JSON）；db=托管/演示 */
  runtime: () => request<RuntimeInfo>('/api/runtime'),

  // —— 文件事实源（仅 --definitions-dir 形态注册；未配置时 404） ——

  listFiles: () => request<{ dir: string; files: DefinitionFile[] }>('/api/files'),

  getFile: (fileName: string) =>
    request<{ file: string; spec: Spec; dir: string }>(
      `/api/files/${encodeURIComponent(fileName)}`,
    ),

  saveFile: (fileName: string, spec: Spec) =>
    request<{ file: string; code: string; issues: ValidateIssue[] }>(
      `/api/files/${encodeURIComponent(fileName)}`,
      { method: 'PUT', body: JSON.stringify(spec) },
    ),

  listDefinitions: () => request<DefinitionSummary[]>('/api/definitions'),

  /** 取定义内容。source='published' 取已发布版——导出给 git 的唯一正确来源 */
  getDefinition: (code: string, source: 'auto' | 'published' = 'auto') =>
    request<DefinitionDetail>(
      `/api/definitions/${code}${source === 'published' ? '?source=published' : ''}`,
    ),

  /** 版本判定预演（只判定不落库）：下一版号 / 破坏性规则 / 幂等去重 */
  verdict: (code: string, spec: Spec) =>
    request<VerdictPreview>(`/api/definitions/${code}/verdict`, {
      method: 'POST',
      body: JSON.stringify(spec),
    }),

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
    startUser?: string,
  ) =>
    request<SimulationReport>(`/api/definitions/${code}/simulate`, {
      method: 'POST',
      body: JSON.stringify({
        spec,
        variables,
        ...(controls ? { controls } : {}),
        ...(startUser ? { start_user: startUser } : {}),
      }),
    }),

  // —— 元数据下发(批 D:下拉辅助数据源) ——

  handlers: () => request<{ handlers: string[] }>('/api/handlers'),

  directorySummary: () =>
    request<DirectorySummary>('/api/directory/summary'),

  // —— 场景回归:脚本随定义同住目录(<code>.scenario.json),CI 跑同一份文件 ——
  // 设计器只读运行;编辑与断言在 IDE/git 里做（见 ScenarioDialog 头注释）

  getScenario: (code: string) =>
    request<{ file: string; name: string; script: Record<string, unknown> }>(
      `/api/definitions/${encodeURIComponent(code)}/scenario`,
    ),

  runScenario: (code: string, script: Record<string, unknown>, spec?: Spec) =>
    request<ScenarioReport>(`/api/definitions/${encodeURIComponent(code)}/scenarios/run`, {
      method: 'POST',
      body: JSON.stringify(spec ? { script, spec } : { script }),
    }),
}

/** 场景运行报告（engine ScenarioReport 同构） */
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
