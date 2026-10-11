/** 元数据下发缓存(设计器插件计划批 D):handler 名录 + 目录摘要。
 *
 * SchemaForm 的取人/动作字段据此渲染 datalist——把「未注册 handler/拼错人」
 * 消灭在选择时。未配置目录时按无目录降级(只建议 handler 与空列表)。
 * 取人位是 CEL 公式:用户建议值带引号(`` 'u_zhang' ``)才是可填形态;
 * candidate_groups 是合法的组引用位(`` 'role:finance' ``)。
 */

import { useEffect, useState } from 'react'
import { api } from './api'

export type Suggestion = { value: string; label: string }

type Options = {
  handlers: string[]
  users: { id: string; department: string | null; roles: string[] }[]
  departments: { code: string; name: string; parent: string | null }[]
  roles: string[]
  configured: boolean
}

let cache: Promise<Options> | null = null

function loadOptions(): Promise<Options> {
  cache ??= (async () => {
    try {
      const [h, d] = await Promise.all([api.handlers(), api.directorySummary()])
      return {
        handlers: h.handlers,
        users: d.users,
        departments: d.departments,
        roles: d.roles,
        configured: d.configured,
      }
    } catch {
      return { handlers: [], users: [], departments: [], roles: [], configured: false }
    }
  })()
  return cache
}

/** 取人位建议:用户字面量(带 CEL 引号),label 附部门/角色翻译 */
async function userSuggestions(): Promise<Suggestion[]> {
  const opts = await loadOptions()
  const deptName = (code: string | null) =>
    opts.departments.find((d) => d.code === code)?.name ?? code
  return opts.users.map((u) => ({
    value: `'${u.id}'`,
    label: `${u.id} · ${deptName(u.department)}${u.roles.length ? `(${u.roles.join('/')})` : ''}`,
  }))
}

/** candidate_groups 位建议:role:/org: 组引用(该位合法的组语义) */
async function groupSuggestions(): Promise<Suggestion[]> {
  const opts = await loadOptions()
  if (!opts.configured) return []
  const roleItems = opts.roles.map((r) => ({ value: `'role:${r}'`, label: `${r} · 角色` }))
  const orgItems = opts.departments.map((d) => ({ value: `'org:${d.code}'`, label: `${d.name} · 部门` }))
  return [...roleItems, ...orgItems]
}

function handlerSuggestions(): Promise<Suggestion[]> {
  return loadOptions().then((opts) => opts.handlers.map((h) => ({ value: h, label: h })))
}

const EMPTY: Suggestion[] = []

/** 字段建议(按字段名分发;非建议字段恒空,不发请求) */
export async function suggestionsFor(field: string): Promise<Suggestion[]> {
  switch (field) {
    case 'action':
      return handlerSuggestions()
    case 'assignee':
    case 'assignees':
    case 'recipients':
    case 'subjects':
    case 'candidate_users':
    case 'reassign_to':
      return userSuggestions()
    case 'candidate_groups':
      return groupSuggestions()
    default:
      return EMPTY
  }
}

/** 建议钩子:SchemaField 内取当前字段的 datalist 项(异步到达,缓存共享) */
export function useSuggestions(field: string): Suggestion[] {
  const [items, setItems] = useState<Suggestion[]>(EMPTY)
  useEffect(() => {
    let alive = true
    void suggestionsFor(field).then((s) => {
      if (alive && s.length) setItems(s)
    })
    return () => {
      alive = false
    }
  }, [field])
  return items
}
