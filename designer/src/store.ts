/** zustand 全局状态:Spec 唯一事实源 + 历史(undo/redo)+ 自动保存/校验防抖 */

import { create } from 'zustand'
import { api } from './api'
import { designerConfig } from './config'
import {
  edgeId,
  type DefinitionSummary,
  type DiffResult,
  type DryRunResult,
  type NodeKindMeta,
  type Spec,
  type ValidateIssue,
} from './types'

export type Selection = { type: 'node' | 'edge'; id: string } | null
export type RightTab = 'props' | 'json' | 'issues'
export type SaveState = 'idle' | 'saving' | 'saved' | 'error'

const AUTOVALIDATE_DELAY = 500

let validateTimer: ReturnType<typeof setTimeout> | null = null
let toastTimer: ReturnType<typeof setTimeout> | null = null

type StoreState = {
  meta: NodeKindMeta[]
  metaByKind: Record<string, NodeKindMeta>
  listing: DefinitionSummary[]
  listingLoading: boolean
  /** 后端不可达等加载失败（review P1-4）：修前静默吞错呈现成业务空态还引导新建 */
  listingError: string | null
  defInfo: { code: string; name: string; draftVersion: number | null; publishedVersion: number | null } | null
  spec: Spec | null
  selection: Selection
  issues: ValidateIssue[]
  rightTab: RightTab
  saveState: SaveState
  /** 草稿有未保存变更(手动保存模式,无自动保存) */
  dirty: boolean
  toast: string | null
  pendingConnect: string | null
  publishOpen: boolean
  publishDiff: DiffResult | null
  publishDiffLoading: boolean
  dryRunOpen: boolean
  dryRun: DryRunResult | null
  /** 面板拖拽会话:非空 = 正在从调色板拖出一个节点(指针驱动,不走 HTML5 原生拖拽) */
  paletteDrag: { kind: string; defaults: Record<string, unknown>; label: string } | null
  /** 连线拖到空白处弹出的快捷新建菜单(记录落点与来源节点;bx/bw=画布横向范围,用于菜单折返定位) */
  connectMenu: {
    sx: number; sy: number; fx: number; fy: number; source: string; bx: number; bw: number
  } | null
  /** 右键菜单:节点/连线各一组动作,坐标为视口固定定位 */
  ctxMenu: { x: number; y: number; kind: 'node' | 'edge'; id: string } | null
  past: string[]
  future: string[]
}

type StoreActions = {
  bootstrap: () => Promise<void>
  refreshListing: () => Promise<void>
  openDefinition: (code: string) => Promise<void>
  createDefinition: (code: string, name: string) => Promise<void>
  backToListing: () => void
  mutate: (
    fn: (draft: Spec) => void,
    opts?: { history?: boolean; keepDryRun?: boolean },
  ) => void
  addNode: (kind: string, defaults: Record<string, unknown>, pos?: { x: number; y: number }) => void
  /** 把新节点插到一条已有连线中间:原线替换为 source→新节点→target,条件/默认标记随前段走。
   *  仅供调色板拖入(显式插入意图);画布节点拖动不做插线,避免移动节点时误改拓扑。 */
  insertNodeOnFlow: (
    kind: string, defaults: Record<string, unknown>, pos: { x: number; y: number }, source: string, target: string,
  ) => void
  /** 在 pos 处新建节点并立即连一条 source→新节点 的边(连线拖到空白的快捷新建;n8n/Dify 模式) */
  addNodeAndConnect: (kind: string, defaults: Record<string, unknown>, pos: { x: number; y: number }, source: string) => void
  connect: (source: string, target: string) => void
  reconnectFlow: (oldSource: string, oldTarget: string, source: string, target: string) => void
  setPendingConnect: (code: string | null) => void
  startPaletteDrag: (item: { kind: string; defaults: Record<string, unknown>; label: string }) => void
  cancelPaletteDrag: () => void
  openConnectMenu: (m: { sx: number; sy: number; fx: number; fy: number; source: string; bx: number; bw: number }) => void
  closeConnectMenu: () => void
  openContextMenu: (m: { x: number; y: number; kind: 'node' | 'edge'; id: string }) => void
  closeContextMenu: () => void
  /** 复制节点:克隆 kind 与全部字段,code 自动顺延,落点右下偏移,选中新节点 */
  duplicateNode: (code: string) => void
  /** 默认边切换:同源互斥;设为默认时清掉自身的条件(默认边不挂条件) */
  toggleDefaultEdge: (source: string, target: string) => void
  select: (sel: Selection) => void
  deleteSelection: () => void
  setNodePos: (code: string, x: number, y: number) => void
  setRightTab: (tab: RightTab) => void
  applySpecJson: (json: string) => boolean
  validateNow: (deploy?: boolean) => Promise<void>
  saveNow: () => Promise<void>
  publish: () => Promise<void>
  setPublishOpen: (open: boolean) => void
  loadPublishDiff: () => Promise<void>
  openDryRun: () => void
  closeDryRun: () => void
  runDryRun: (variables: Record<string, unknown>, assumptions?: Record<string, string>) => Promise<boolean>
  relayout: () => void
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
  listingError: null,
  defInfo: null,
  spec: null,
  selection: null,
  issues: [],
  rightTab: 'props',
  saveState: 'idle',
  dirty: false,
  toast: null,
  pendingConnect: null,
  publishOpen: false,
  publishDiff: null,
  publishDiffLoading: false,
  dryRunOpen: false,
  dryRun: null,
  paletteDrag: null,
  connectMenu: null,
  ctxMenu: null,
  past: [],
  future: [],

  async bootstrap() {
    try {
      const meta = await api.nodeKinds()
      const metaByKind = Object.fromEntries(meta.map((m) => [m.kind, m]))
      set({ meta, metaByKind })
    } catch (error) {
      // nodeKinds 挂（后端不可达/CORS）修前是 unhandled rejection——列表页永久「加载中」
      set({ listingError: String(error) })
      return
    }
    await reloadListing(set)
  },

  async refreshListing() {
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
      dirty: false,
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

  /** 所有写路径的唯一入口:structuredClone 后应用变更,历史/脏标记/校验自动跟上。
   *  keepDryRun: 纯布局类变更不使预演结果失效（路由拓扑未变）。
   *  注意:mutate 只改内存并标脏,落库靠手动保存(顶栏按钮 / Ctrl+S)或发布前的强制保存。 */
  mutate(fn, opts = {}) {
    const { spec } = get()
    if (!spec) return
    // readonly 单一咽喉（全仓 review 2026-10-13 批 2 P0-2）：修前只读纪律全靠
    // UI 隐藏 + RF 画布级开关两道可绕过的防线（adapter 逐节点 draggable:true
    // 架空画布开关、边交互裸 DOM 直达 store），审计面可被无意改图、模拟拿到
    // 被改过的 spec——证据失真。守卫收口在此，各直写方法另有同款早退。
    if (designerConfig.readonly()) return
    const { history = true, keepDryRun = false } = opts
    const draft = structuredClone(spec)
    fn(draft)
    inferAdvancePolicies(draft)
    if (history) {
      set((s) => ({ past: [...s.past.slice(-49), JSON.stringify(spec)], future: [] }))
    }
    set(keepDryRun ? { spec: draft, dirty: true } : { spec: draft, dryRun: null, dirty: true })
    scheduleValidate(get)
  },

  addNode(kind, defaults, pos) {
    const { spec, selection } = get()
    if (!spec) return
    const code = nextNodeCode(spec, kind)
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

  insertNodeOnFlow(kind, defaults, pos, source, target) {
    const { spec } = get()
    if (!spec) return
    const flow = spec.flows.find((f) => f.source === source && f.target === target)
    if (!flow) return
    const code = nextNodeCode(spec, kind)
    get().mutate((draft) => {
      draft.nodes.push({ code, name: '', kind, ...defaults })
      draft.layout[code] = { x: Math.round(pos.x), y: Math.round(pos.y) }
      draft.flows = draft.flows.flatMap((f) =>
        f.source === source && f.target === target
          ? [{ ...f, source: f.source, target: code }, { source: code, target: f.target }]
          : [f],
      )
    })
    set({ selection: { type: 'node', id: code }, rightTab: 'props' })
    get().showToast(`已把「${String(defaults.name ?? code)}」插入 ${source} → ${target}`)
  },

  addNodeAndConnect(kind, defaults, pos, source) {
    const { spec } = get()
    if (!spec) return
    if (!spec.nodes.some((n) => n.code === source)) return
    const code = nextNodeCode(spec, kind)
    get().mutate((draft) => {
      draft.nodes.push({ code, name: '', kind, ...defaults })
      draft.layout[code] = { x: Math.round(pos.x), y: Math.round(pos.y) }
      draft.flows.push({ source, target: code })
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

  startPaletteDrag(item) {
    set({ paletteDrag: item })
  },

  cancelPaletteDrag() {
    set({ paletteDrag: null })
  },

  openConnectMenu(m) {
    set({ connectMenu: m, pendingConnect: null })
  },

  closeConnectMenu() {
    set({ connectMenu: null })
  },

  openContextMenu(m) {
    set({ ctxMenu: m })
  },

  closeContextMenu() {
    set({ ctxMenu: null })
  },

  duplicateNode(code) {
    const { spec } = get()
    if (!spec) return
    const src = spec.nodes.find((n) => n.code === code)
    if (!src) return
    const newCode = nextNodeCode(spec, src.kind)
    get().mutate((draft) => {
      const pos = draft.layout[code] ?? { x: 0, y: 0 }
      draft.nodes.push({ ...structuredClone(src), code: newCode, name: src.name ? `${src.name} 副本` : '' })
      draft.layout[newCode] = { x: pos.x + 40, y: pos.y + 64 }
    })
    set({ selection: { type: 'node', id: newCode }, rightTab: 'props' })
    get().showToast(`已复制为「${newCode}」— 连线请从出线口拖出`)
  },

  toggleDefaultEdge(source, target) {
    const { spec } = get()
    if (!spec) return
    const flow = spec.flows.find((f) => f.source === source && f.target === target)
    if (!flow) return
    const willBeDefault = !flow.is_default
    // 默认边只在条件分支路由被引擎消费;其他连线设了是死配置(与右键菜单守卫同源)
    if (willBeDefault && spec.nodes.find((n) => n.code === source)?.kind !== 'branch') {
      get().showToast('默认边仅对条件分支的出线有意义 — 已忽略')
      return
    }
    get().mutate((draft) => {
      for (const f of draft.flows) {
        if (f.source === source) f.is_default = false
      }
      const f = draft.flows.find((x) => x.source === source && x.target === target)
      if (!f) return
      if (willBeDefault) {
        f.is_default = true
        f.condition = null // 默认边不挂条件(引擎校验同款约束)
      } else {
        f.is_default = false
      }
    })
    get().showToast(willBeDefault ? `已设 ${source} 的默认边 → ${target}(原条件已清)` : '已取消默认边')
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
    // 一次拖动 = 一条撤销记录(拖乱可 Ctrl+Z 找回);纯布局变更不失效预演
    get().mutate(
      (draft) => {
        draft.layout[code] = { x, y }
      },
      { keepDryRun: true },
    )
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
      get().mutate((draft) => Object.assign(draft, next))
      get().validateNow()
      get().showToast('已应用 JSON — 画布已从 Spec 重绘')
      return true
    } catch (err) {
      get().showToast(`JSON 解析失败:${err instanceof Error ? err.message : String(err)}`)
      return false
    }
  },

  async validateNow(deploy = false) {
    const { spec } = get()
    if (!spec) return
    try {
      const result = await api.validate(spec, deploy)
      set({ issues: result.issues })
    } catch (err) {
      set({ issues: [{ level: 'error', rule: 'spec.invalid', message: String(err), node: null, flow: null }] })
    }
  },

  async saveNow() {
    const { spec, defInfo, saveState } = get()
    if (!spec || !defInfo) return
    if (designerConfig.readonly()) return // 只读审计无保存路径（防快捷键绕过隐藏按钮）
    if (saveState === 'saving') return // 在途防重（review P1-2）：Ctrl+S 绕过按钮 disabled，
    // 两次并发保存各携不同 spec，旧响应后到会把 dirty:false 写回——新改动静默丢失
    set({ saveState: 'saving' })
    try {
      const saved = await api.saveDraft(defInfo.code, spec)
      set((s) => ({
        saveState: 'saved',
        dirty: false,
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
        dirty: false,
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
    if (open) void get().loadPublishDiff()
  },

  async loadPublishDiff() {
    const { defInfo, spec } = get()
    if (!defInfo || !spec) return
    set({ publishDiffLoading: true })
    try {
      const diff = await api.diffDraft(defInfo.code, spec)
      set({ publishDiff: diff, publishDiffLoading: false })
    } catch {
      set({ publishDiff: null, publishDiffLoading: false })
    }
  },

  openDryRun() {
    set({ dryRunOpen: true })
  },

  closeDryRun() {
    // 关闭对话框但保留预演结果:画布高亮供继续对照,编辑配置时由 mutate 失效
    set({ dryRunOpen: false })
  },

  async runDryRun(variables, assumptions) {
    const { spec } = get()
    if (!spec) return false
    try {
      const result = await api.dryRun(spec, variables, assumptions)
      set({ dryRun: result })
      if (result.ok) get().showToast(`预演完成:路径经过 ${result.steps.length} 个节点`)
      else get().showToast('预演发现无法路由的分支 — 详见预演结果')
      return true
    } catch (err) {
      get().showToast(`预演失败:${err instanceof Error ? err.message : String(err)}`)
      return false
    }
  },

  relayout() {
    get().mutate(
      (draft) => {
        layoutSpec(draft, { force: true })
      },
      { keepDryRun: true },
    )
    get().showToast('已按分层重新排布全部节点')
  },

  undo() {
    const { past, spec } = get()
    if (!past.length || !spec) return
    const prev = past[past.length - 1]
    set((s) => ({ past: s.past.slice(0, -1), future: [JSON.stringify(spec), ...s.future].slice(0, 50), spec: JSON.parse(prev), dirty: true }))
    scheduleValidate(get)
  },

  redo() {
    const { future, spec } = get()
    if (!future.length || !spec) return
    const next = future[0]
    set((s) => ({ future: s.future.slice(1), past: [...s.past, JSON.stringify(spec)].slice(-50), spec: JSON.parse(next), dirty: true }))
    scheduleValidate(get)
  },

  showToast(msg) {
    set({ toast: msg })
    if (toastTimer) clearTimeout(toastTimer)
    toastTimer = setTimeout(() => set({ toast: null }), 2600)
  },
}))

// ── 内部工具 ──────────────────────────────────────────────

/** 未占用的节点编码:kind, kind_2, kind_3 … */
function nextNodeCode(spec: Spec, kind: string): string {
  const codes = new Set(spec.nodes.map((n) => n.code))
  let code = kind
  for (let i = 2; codes.has(code); i++) code = `${kind}_${i}`
  return code
}

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
  layoutSpec(spec, { force: false })
  return spec
}

/** BFS 分层布局:层级 → x,同层内居中分布 → y。
 *  force=false 只补缺失坐标(打开定义时);force=true 全部重排(「整理布局」按钮)。 */
function layoutSpec(spec: Spec, { force }: { force: boolean }) {
  if (!force && spec.nodes.every((n) => spec.layout[n.code])) return
  if (force) spec.layout = {}
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
  // 长链蛇形折行(压测观察①):层数超过 8 层的一维图会宽到 fitView 失效(minZoom 卡底)、
  // 两端节点无法同屏 —— 自动按 8 列换行成二维网格,奇数行反向行走
  const MAX_COLS = 8
  if (sortedLevels.length > MAX_COLS) {
    for (const level of sortedLevels) {
      const codes = levels.get(level)!
      const row = Math.floor(level / MAX_COLS)
      const col0 = level % MAX_COLS
      const col = row % 2 === 1 ? MAX_COLS - 1 - col0 : col0
      codes.forEach((code, i) => {
        spec.layout[code] = { x: 60 + col * GAP_X, y: 120 + row * (GAP_Y + 40) + i * 40 }
      })
    }
    return
  }
  for (const level of sortedLevels) {
    const codes = levels.get(level)!
    const height = (codes.length - 1) * GAP_Y
    codes.forEach((code, i) => {
      spec.layout[code] = { x: 60 + level * GAP_X, y: 200 - height / 2 + i * GAP_Y }
    })
  }
}

async function reloadListing(set: (partial: Partial<StoreState>) => void) {
  set({ listingLoading: true, listingError: null })
  try {
    set({ listing: await api.listDefinitions(), listingLoading: false })
  } catch (error) {
    // 失败≠空态（review P1-4）：修前静默吞错、列表呈现「还没有流程定义」
    // 引导用户去新建——后端不可达被伪装成业务空态
    set({ listingLoading: false, listingError: String(error) })
  }
}

function scheduleValidate(get: () => Store) {
  if (validateTimer) clearTimeout(validateTimer)
  validateTimer = setTimeout(() => void get().validateNow(), AUTOVALIDATE_DELAY)
}
