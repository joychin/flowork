import type { DefinitionDetail, DefinitionSummary, NodeKindMeta, Spec, ValidateIssue } from './types'

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
}
