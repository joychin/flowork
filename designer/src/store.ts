/** zustand 全局状态:Spec 唯一事实源 + 历史(undo/redo)+ 自动保存/校验防抖 */

import { create } from 'zustand'
import { api } from './api'
import { edgeId, type DefinitionSummary, type NodeKindMeta, type Spec, type ValidateIssue } from './types'

export type Selection = { type: 'node' | 'edge'; id: string } | null
export type RightTab = 'props' | 'json' | 'issues'
export type SaveState = 'idle' | 'saving' | 'saved' | 'error'

const AUTOSAVE_DELAY = 800
const AUTOVALIDATE_DELAY = 500

let saveTimer: ReturnType<typeof setTimeout> | null = null
let validateTimer: ReturnType<typeof setTimeout> | null = null
let toastTimer: ReturnType<typeof setTimeout> | null = null

type StoreState = {
  meta: NodeKindMeta[]
  metaByKind: Record<string, NodeKindMeta>
  listing: DefinitionSummary[]
  listingLoading: boolean
  defInfo: { code: string; name: string; draftVersion: number | null; publishedVersion: number | null } | null
  spec: Spec | null
  selection: Selection
  issues: ValidateIssue[]
  rightTab: RightTab
  saveState: SaveState
  toast: string | null
  pendingConnect: string | null
  publishOpen: boolean
  past: string[]
  future: string[]
}

type StoreActions = {
  bootstrap: () => Promise<void>
  openDefinition: (code: string) => Promise<void>
  createDefinition: (code: string, name: string) => Promise<void>
  backToListing: () => void
  mutate: (fn: (draft: Spec) => void, opts?: { history?: boolean; resave?: boolean }) => void
  addNode: (kind: string, defaults: Record<string, unknown>, pos?: { x: number; y: number }) => void
  connect: (source: string, target: string) => void
  reconnectFlow: (oldSource: string, oldTarget: string, source: string, target: string) => void
  setPendingConnect: (code: string | null) => void
  select: (sel: Selection) => void
  deleteSelection: () => void
  setNodePos: (code: string, x: number, y: number) => void
  setRightTab: (tab: RightTab) => void
  applySpecJson: (json: string) => boolean
  validateNow: () => Promise<void>
  saveNow: () => Promise<void>
  publish: () => Promise<void>
  setPublishOpen: (open: boolean) => void
  undo: () => void
  redo: () => void
  showToast: (msg: string) => void
}

export type Store = StoreState & StoreActions

export const useStore = create<Store>((set, get) => ({
  meta: [],
  metaByKind: {},
  listing: [],
  listingLoading: true,
  defInfo: null,
  spec: null,
  selection: null,
  issues: [],
  rightTab: 'props',
  saveState: 'idle',
  toast: null,
  pendingConnect: null,
  publishOpen: false,
  past: [],
  future: [],

  async bootstrap() {
    const meta = await api.nodeKinds()
    const metaByKind = Object.fromEntries(meta.map((m) => [m.kind, m]))
    set({ meta, metaByKind })
    await reloadListing(set)
  },

  async openDefinition(code) {
    const detail = await api.getDefinition(code)
    const spec = ensureLayout(detail.spec)
    set({
      defInfo: {
        code: detail.code,
        name: detail.name || spec.name,
        draftVersion: detail.draft_version,
        publishedVersion: detail.published_version,
      },
      spec,
      selection: null,
      issues: [],
      past: [],
      future: [],
      saveState: 'idle',
      pendingConnect: null,
    })
    get().validateNow()
  },

  async createDefinition(code, name) {
    const spec: Spec = {
      code,
      name,
      nodes: [
        { code: 'start', name: '发起', kind: 'start' },
        { code: 'end', name: '结束', kind: 'end' },
      ],
      flows: [{ source: 'start', target: 'end' }],
      layout: {},
    }
    const saved = await api.saveDraft(code, ensureLayout(spec))
    await get().openDefinition(code)
    set({ defInfo: get().defInfo ? { ...get().defInfo!, draftVersion: saved.version } : null })
    get().showToast(`已创建「${name || code}」草稿 v${saved.version}`)
  },

  backToListing() {
    set({ defInfo: null, spec: null, selection: null, pendingConnect: null })
  },

  /** 所有写路径的唯一入口:structuredClone 后应用变更,历史/保存/校验自动跟上 */
  mutate(fn, opts = {}) {
    const { spec } = get()
    if (!spec) return
    const { history = true } = opts
    const draft = structuredClone(spec)
    fn(draft)
    inferAdvancePolicies(draft)
    if (history) {
      set((s) => ({ past: [...s.past.slice(-49), JSON.stringify(spec)], future: [] }))
    }
    set({ spec: draft })
    if (opts.resave !== false) {
      scheduleSave(set, get)
      scheduleValidate(get)
    }
  },

  addNode(kind, defaults, pos) {
    const { spec, selection } = get()
    if (!spec) return
    const codes = new Set(spec.nodes.map((n) => n.code))
    let code = kind
    for (let i = 2; codes.has(code); i++) code = `${kind}_${i}`
    // 放置位置:拖放落点 > 选中节点右侧 > 整体最右再往右一格
    let x: number, y: number
    if (pos) {
      x = pos.x; y = pos.y
    } else {
      const anchor = selection?.type === 'node' ? spec.layout[selection.id] : null
      const maxX = Math.max(120, ...spec.nodes.map((n) => spec.layout[n.code]?.x ?? 0))
      x = anchor ? anchor.x + 260 : maxX + 120
      y = anchor ? anchor.y : 200
    }
    get().mutate((draft) => {
      draft.nodes.push({ code, name: '', kind, ...defaults })
      draft.layout[code] = { x, y }
    })
    set({ selection: { type: 'node', id: code }, rightTab: 'props' })
  },

  connect(source, target) {
    const { spec } = get()
    if (!spec) return
    if (source === target) {
      get().showToast('不能连接节点自身')
      return
    }
    if (spec.flows.some((f) => f.source === source && f.target === target)) {
      get().showToast('连线已存在')
      return
    }
    get().mutate((draft) => {
      draft.flows.push({ source, target })
    })
    const after = get().spec?.flows ?? []
    const unconditional = after.filter((f) => f.source === source && !f.condition && !f.is_default)
    if (unconditional.length >= 2) {
      get().showToast(`「${source}」的无条件出边已达 ${unconditional.length} 条：这些分支将同时进行`)
    }
    set({ selection: { type: 'edge', id: edgeId(source, target) }, pendingConnect: null, rightTab: 'props' })
  },

  setPendingConnect(code) {
    set({ pendingConnect: code })
  },

  /** 拖动边的端点改道:替换 source/target(带自环/重复/存在性检查) */
  reconnectFlow(oldSource, oldTarget, source, target) {
    const { spec } = get()
    if (!spec) return
    if (source === target) {
      get().showToast('不能连接节点自身')
      return
    }
    const exists = spec.flows.some((f) => f.source === oldSource && f.target === oldTarget)
    if (!exists) return
    if (spec.flows.some((f) => f.source === source && f.target === target)) {
      get().showToast('连线已存在')
      return
    }
    get().mutate((draft) => {
      const flow = draft.flows.find((f) => f.source === oldSource && f.target === oldTarget)
      if (flow) {
        flow.source = source
        flow.target = target
      }
    })
    set({ selection: { type: 'edge', id: edgeId(source, target) }, pendingConnect: null, rightTab: 'props' })
  },

  select(sel) {
    set({ selection: sel, rightTab: sel ? (get().rightTab === 'issues' ? 'props' : get().rightTab) : get().rightTab })
    if (sel?.type === 'edge') set({ rightTab: 'props' })
  },

  deleteSelection() {
    const { selection, spec } = get()
    if (!selection || !spec) return
    if (selection.type === 'node') {
      const node = spec.nodes.find((n) => n.code === selection.id)
      if (node?.kind === 'start') {
        get().showToast('start 节点不可删除(校验要求有且仅有一个)')
        return
      }
      get().mutate((draft) => {
        draft.nodes = draft.nodes.filter((n) => n.code !== selection.id)
        draft.flows = draft.flows.filter((f) => f.source !== selection.id && f.target !== selection.id)
        delete draft.layout[selection.id]
      })
    } else {
      const { source, target } = { source: selection.id.split('->')[0], target: selection.id.split('->')[1] }
      get().mutate((draft) => {
        draft.flows = draft.flows.filter((f) => !(f.source === source && f.target === target))
      })
    }
    set({ selection: null })
    get().showToast('已删除')
  },

  setNodePos(code, x, y) {
    // 拖拽不进历史(避免刷屏),但要触发保存
    get().mutate((draft) => {
      draft.layout[code] = { x, y }
    }, { history: false })
  },

  setRightTab(tab) {
    set({ rightTab: tab })
  },

  applySpecJson(json) {
    try {
      const next = JSON.parse(json) as Spec
      if (!Array.isArray(next.nodes) || !Array.isArray(next.flows)) throw new Error('缺少 nodes / flows')
      if (get().defInfo) next.code = get().defInfo!.code
      ensureLayout(next)
      get().mutate((draft) => Object.assign(draft, next), { resave: false })
      scheduleSave(set, get)
      get().validateNow()
      get().showToast('已应用 JSON — 画布已从 Spec 重绘')
      return true
    } catch (err) {
      get().showToast(`JSON 解析失败:${err instanceof Error ? err.message : String(err)}`)
      return false
    }
  },

  async validateNow() {
    const { spec } = get()
    if (!spec) return
    try {
      const result = await api.validate(spec)
      set({ issues: result.issues })
    } catch (err) {
      set({ issues: [{ level: 'error', rule: 'spec.invalid', message: String(err), node: null, flow: null }] })
    }
  },

  async saveNow() {
    const { spec, defInfo } = get()
    if (!spec || !defInfo) return
    set({ saveState: 'saving' })
    try {
      const saved = await api.saveDraft(defInfo.code, spec)
      set((s) => ({
        saveState: 'saved',
        defInfo: s.defInfo ? { ...s.defInfo, draftVersion: saved.version, name: spec.name || s.defInfo.name } : null,
      }))
    } catch (err) {
      set({ saveState: 'error' })
      get().showToast(`保存失败:${err instanceof Error ? err.message : String(err)}`)
    }
  },

  async publish() {
    const { defInfo } = get()
    if (!defInfo) return
    try {
      await api.saveDraft(defInfo.code, get().spec!)
      const result = await api.publish(defInfo.code)
      set((s) => ({
        defInfo: s.defInfo ? { ...s.defInfo, publishedVersion: result.version, draftVersion: null } : null,
        publishOpen: false,
      }))
      await reloadListing(set)
      get().showToast(`✅ 已发布 v${result.version} — 运行中实例不受影响,新实例使用新版本`)
    } catch (err) {
      const payload = (err as any).payload
      if (payload && typeof payload === 'object' && Array.isArray(payload.issues)) {
        set({ issues: payload.issues, rightTab: 'issues', publishOpen: false })
        get().showToast('✕ 校验未通过,已禁止发布 — 详见「校验」页')
      } else {
        get().showToast(`发布失败:${err instanceof Error ? err.message : String(err)}`)
      }
    }
  },

  setPublishOpen(open) {
    set({ publishOpen: open })
  },

  undo() {
    const { past, spec } = get()
    if (!past.length || !spec) return
    const prev = past[past.length - 1]
    set((s) => ({ past: s.past.slice(0, -1), future: [JSON.stringify(spec), ...s.future].slice(0, 50), spec: JSON.parse(prev) }))
    scheduleSave(set, get)
    scheduleValidate(get)
  },

  redo() {
    const { future, spec } = get()
    if (!future.length || !spec) return
    const next = future[0]
    set((s) => ({ future: s.future.slice(1), past: [...s.past, JSON.stringify(spec)].slice(-50), spec: JSON.parse(next) }))
    scheduleSave(set, get)
    scheduleValidate(get)
  },

  showToast(msg) {
    set({ toast: msg })
    if (toastTimer) clearTimeout(toastTimer)
    toastTimer = setTimeout(() => set({ toast: null }), 2600)
  },
}))

// ── 内部工具 ──────────────────────────────────────────────

/** 推进策略推断：≥2 入边且未显式设置的节点自动落字段（用户显式选择优先，只填空不覆盖）。
 *  入边全为无条件 → all（并行汇合，等齐）；含条件/默认边 → any（分支合并，任一到达即开始）。 */
function inferAdvancePolicies(spec: Spec) {
  const incoming: Record<string, Spec['flows']> = {}
  for (const f of spec.flows) {
    ;(incoming[f.target] ??= []).push(f)
  }
  for (const node of spec.nodes) {
    const edges = incoming[node.code]
    if (!edges || edges.length < 2) continue
    if (node.advance_policy != null) continue
    node.advance_policy = edges.every((f) => !f.condition && !f.is_default) ? 'all' : 'any'
  }
}

function ensureLayout(spec: Spec): Spec {
  spec.layout = spec.layout ?? {}
  const missing = spec.nodes.filter((n) => !spec.layout[n.code])
  if (!missing.length) return spec
  // BFS 分层自动布局:层级 → x,同层内居中分布 → y
  const byCode = Object.fromEntries(spec.nodes.map((n) => [n.code, n]))
  const outgoing = Object.fromEntries(spec.nodes.map((n) => [n.code, [] as string[]]))
  for (const f of spec.flows) {
    if (outgoing[f.source] && byCode[f.target]) outgoing[f.source].push(f.target)
  }
  const start = spec.nodes.find((n) => n.kind === 'start')
  const levelOf: Record<string, number> = {}
  const queue = start ? [start.code] : spec.nodes.slice(0, 1).map((n) => n.code)
  for (const code of queue) levelOf[code] = 0
  while (queue.length) {
    const cur = queue.shift()!
    for (const nxt of outgoing[cur]) {
      if (levelOf[nxt] === undefined) {
        levelOf[nxt] = levelOf[cur] + 1
        queue.push(nxt)
      }
    }
  }
  // 分层收集后按层布点;不可达节点排最后两层兜底
  const levels = new Map<number, string[]>()
  for (const node of spec.nodes) {
    const level = levelOf[node.code] ?? 0
    if (!levels.has(level)) levels.set(level, [])
    levels.get(level)!.push(node.code)
  }
  const GAP_X = 260
  const GAP_Y = 130
  const sortedLevels = [...levels.keys()].sort((a, b) => a - b)
  for (const level of sortedLevels) {
    const codes = levels.get(level)!
    const height = (codes.length - 1) * GAP_Y
    codes.forEach((code, i) => {
      if (spec.layout[code]) return
      spec.layout[code] = { x: 60 + level * GAP_X, y: 200 - height / 2 + i * GAP_Y }
    })
  }
  return spec
}

async function reloadListing(set: (partial: Partial<StoreState>) => void) {
  set({ listingLoading: true })
  try {
    set({ listing: await api.listDefinitions(), listingLoading: false })
  } catch {
    set({ listingLoading: false })
  }
}

function scheduleSave(set: (partial: Partial<StoreState>) => void, get: () => Store) {
  if (saveTimer) clearTimeout(saveTimer)
  set({ saveState: 'saving' })
  saveTimer = setTimeout(() => void get().saveNow(), AUTOSAVE_DELAY)
}

function scheduleValidate(get: () => Store) {
  if (validateTimer) clearTimeout(validateTimer)
  validateTimer = setTimeout(() => void get().validateNow(), AUTOVALIDATE_DELAY)
}
