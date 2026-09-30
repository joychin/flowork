import type {
  DefinitionDetail,
  DefinitionSummary,
  DiffResult,
  DryRunResult,
  NodeKindMeta,
  ProcessVariable,
  Spec,
  ValidateIssue,
} from './types'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
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
    request<{ code: string; version: number; status: string }>(
      `/api/definitions/${code}/publish`,
      { method: 'POST' },
    ),

  validate: (spec: Spec) =>
    request<{ ok: boolean; issues: ValidateIssue[] }>('/api/validate', {
      method: 'POST',
      body: JSON.stringify(spec),
    }),

  dryRun: (spec: Spec, variables: Record<string, unknown>) =>
    request<DryRunResult>('/api/dry-run', {
      method: 'POST',
      body: JSON.stringify({ spec, variables }),
    }),

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
