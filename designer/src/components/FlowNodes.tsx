/** React Flow 自定义节点(矩形/圆)+ 连线,样式与交互(点击连线)在这里挂。
 *  组件全部 memo:配合 adapter 的元素签名缓存,未变化的节点/连线不重渲染。 */

import { memo, useCallback, useRef, useState } from 'react'
import { Handle, Position, BaseEdge, EdgeLabelRenderer, EdgeText, getSmoothStepPath, useReactFlow, useStore as useRFStore } from '@xyflow/react'
import type { EdgeProps, NodeProps } from '@xyflow/react'
import type { NodeData } from '../adapter'
import { useStore } from '../store'

export function Icon({ name, size = 14 }: { name?: string; size?: number }) {
  const common = { width: size, height: size, viewBox: '0 0 16 16', fill: 'none' }
  switch (name) {
    case 'play':
      return <svg {...common}><path d="M4.5 3.2v9.6L12.4 8 4.5 3.2z" fill="currentColor" /></svg>
    case 'stop':
      return <svg {...common}><rect x="4" y="4" width="8" height="8" rx="1.5" fill="currentColor" /></svg>
    case 'user':
      return <svg {...common}><circle cx="8" cy="5.4" r="2.6" fill="currentColor" /><path d="M2.8 13.4c.7-2.6 2.8-3.9 5.2-3.9s4.5 1.3 5.2 3.9" fill="currentColor" /></svg>
    case 'mail':
      return <svg {...common}><rect x="2" y="3.6" width="12" height="8.8" rx="1.5" stroke="currentColor" strokeWidth="1.4" /><path d="m2.6 4.6 5.4 4.4 5.4-4.4" stroke="currentColor" strokeWidth="1.4" /></svg>
    case 'settings':
      return <svg {...common}><circle cx="8" cy="8" r="2.2" stroke="currentColor" strokeWidth="1.4" /><path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6l1.4 1.4M11 11l1.4 1.4M12.4 3.6 11 5M5 11l-1.4 1.4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
    case 'box':
      return <svg {...common}><rect x="2.4" y="4.4" width="11.2" height="9" rx="1.4" stroke="currentColor" strokeWidth="1.4" /><path d="M2.4 7h11.2M6 2.4h4v2H6z" stroke="currentColor" strokeWidth="1.4" /></svg>
    case 'branch':
      return <svg {...common}><path d="M2.5 4h5M2.5 12h5M7.5 4c2.8 0 3.6 2.2 4 4M7.5 12c2.8 0 3.6-2.2 4-4" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" /><circle cx="12.4" cy="8" r="1.3" fill="currentColor" /></svg>
    case 'clock':
      return <svg {...common}><circle cx="8" cy="8" r="5.6" stroke="currentColor" strokeWidth="1.4" fill="none" /><path d="M8 4.8V8l2.3 1.6" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" /></svg>
    default:
      // 未知图标名兜底:实心小方点(带 ui.icon 的节点不再无声消失)
      return <svg {...common}><rect x="5" y="5" width="6" height="6" rx="1.2" fill="currentColor" /></svg>
  }
}

/** 条件分支节点的匹配方式徽章：命中即走 ? / 命中的都走 ≡ */
function BranchModeBadge({ mode }: { mode: string | null }) {
  const all = mode === 'all_match'
  return (
    <span
      className="branch-badge"
      title={all ? '命中的都走(条件为真的分支全部执行)' : '命中即走(按顺序取第一条为真的分支)'}
      style={{
        position: 'absolute', top: -8, right: -8, width: 18, height: 18, borderRadius: 9,
        background: '#d97706', color: '#fff', fontSize: 11, fontWeight: 700,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      {all ? '≡' : '?'}
    </span>
  )
}

function useNodeActions() {
  const select = useStore((s) => s.select)
  const pendingConnect = useStore((s) => s.pendingConnect)
  const setPendingConnect = useStore((s) => s.setPendingConnect)
  const connect = useStore((s) => s.connect)
  return { select, pendingConnect, setPendingConnect, connect }
}

function handles(data: NodeData, actions: ReturnType<typeof useNodeActions>) {
  const onHandleClick = (handleType: 'source' | 'target') => (e: React.MouseEvent) => {
    e.stopPropagation()
    const { pendingConnect, setPendingConnect, connect } = actions
    if (handleType === 'source') {
      // 点出侧圆点:开始拉线(再点自己取消);已有 pending 且不同节点则直接互连
      if (pendingConnect && pendingConnect !== data.code) connect(pendingConnect, data.code)
      else setPendingConnect(pendingConnect === data.code ? null : data.code)
    } else if (pendingConnect && pendingConnect !== data.code) {
      // 点入侧圆点:完成 pending 连接;无 pending 时不做任何事
      connect(pendingConnect, data.code)
    }
  }
  return (
    <>
      <Handle
        type="target"
        position={Position.Left}
        onClick={onHandleClick('target')}
        title="入线口:拉线到此,或先点别的节点出线口再点这里"
        className={actions.pendingConnect && actions.pendingConnect !== data.code ? 'port-hint' : ''}
      />
      <Handle
        type="source"
        position={Position.Right}
        onClick={onHandleClick('source')}
        title="出线口:拖到目标节点连线;或点击后再点目标节点"
        className={actions.pendingConnect === data.code ? 'port-active' : ''}
      />
    </>
  )
}

function issueBadge(level: 'error' | 'warning' | null) {
  if (!level) return null
  return <span className={`node-badge ${level}`} title={level === 'error' ? '存在校验错误' : '存在校验警告'}>{level === 'error' ? '!' : '⚠'}</span>
}

function useNodeClick(data: NodeData) {
  const { select, pendingConnect, setPendingConnect, connect } = useNodeActions()
  return () => {
    if (pendingConnect && pendingConnect !== data.code) {
      connect(pendingConnect, data.code)
      setPendingConnect(null)
    } else {
      select({ type: 'node', id: data.code })
    }
  }
}

// 改道拖点沿路径取点的采样器(隐藏 SVG path;仅选中的 1-2 条边每次渲染算两次,开销可忽略)
let reconnectSampler: SVGPathElement | null = null
function sampleEdgePath(d: string, insetStart: number, insetEnd: number): [{ x: number; y: number }, { x: number; y: number }] {
  if (!reconnectSampler) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    svg.setAttribute('style', 'position:absolute;width:0;height:0;overflow:hidden;pointer-events:none')
    reconnectSampler = document.createElementNS('http://www.w3.org/2000/svg', 'path')
    svg.appendChild(reconnectSampler)
    document.body.appendChild(svg)
  }
  reconnectSampler.setAttribute('d', d)
  const total = reconnectSampler.getTotalLength()
  const a = Math.min(insetStart, total * 0.25)
  const b = Math.min(insetEnd, total * 0.25)
  const p1 = reconnectSampler.getPointAtLength(a)
  const p2 = reconnectSampler.getPointAtLength(Math.max(a + 1, total - b))
  return [{ x: p1.x, y: p1.y }, { x: p2.x, y: p2.y }]
}

function RectNodeImpl({ data, selected }: NodeProps) {
  const d = data as unknown as NodeData
  const color = d.meta?.ui?.color ?? '#2563eb'
  const onClick = useNodeClick(d)
  const actions = useNodeActions()
  return (
    <div className={`fnode rect ${selected ? 'selected' : ''} ${d.pendingConnect ? 'connect-hint' : ''} ${d.hit ? 'hit' : ''}`} style={{ borderColor: color }} onClick={onClick}>
      {handles(d, actions)}
      {issueBadge(d.issueLevel)}
      {d.kind === 'branch' && <BranchModeBadge mode={d.routeMode} />}
      {d.badge && <span className="node-pill" title="节点关键参数(点节点可改)">{d.badge}</span>}
      <span className="fnode-icon" style={{ background: `${color}1a`, color }}>
        <Icon name={d.meta?.ui?.icon} />
      </span>
      <span className="fnode-texts">
        <span className="fnode-name">{d.name}</span>
        <span className="fnode-code">{d.code}</span>
      </span>
    </div>
  )
}

function CircleNodeImpl({ data, selected }: NodeProps) {
  const d = data as unknown as NodeData
  const color = d.meta?.ui?.color ?? '#16a34a'
  const onClick = useNodeClick(d)
  const actions = useNodeActions()
  return (
    <div className="fnode-circle-wrap" onClick={onClick}>
      {handles(d, actions)}
      <div className={`fnode circle ${selected ? 'selected' : ''} ${d.pendingConnect ? 'connect-hint' : ''} ${d.hit ? 'hit' : ''}`} style={{ borderColor: color, background: `${color}1a`, color }}>
        <Icon name={d.meta?.ui?.icon} size={18} />
        {issueBadge(d.issueLevel)}
      </div>
      <div className="fnode-circle-label">{d.name}</div>
    </div>
  )
}

function SmoothEdgeImpl(props: EdgeProps) {
  const { id, source, target, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, label, selected, markerEnd } = props
  const d = props.data as { hit?: boolean; highlight?: boolean; srcOffset?: number; tgtOffset?: number } | undefined
  const hit = Boolean(d?.hit)
  const highlight = Boolean(d?.highlight)
  // 拖拽聚焦:订阅 RF 内部 store 的拖动节点(不经过受控 nodes 管线,不会触发画布级重渲染);
  // 值只在拖动开始/结束各变化一次,期间所有边零开销
  const draggingId = useRFStore((s) => {
    for (const n of s.nodes) if (n.dragging) return n.id
    return null
  })
  const dragFocus = draggingId != null && (draggingId === source || draggingId === target)
  const active = selected || highlight || dragFocus
  const [path, labelX, labelY] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 14 })
  const deleteEdge = useStore((s) => s.deleteSelection)
  const select = useStore((s) => s.select)
  const reconnectFlow = useStore((s) => s.reconnectFlow)
  const showToast = useStore((s) => s.showToast)
  // 改道拖拽:选中后从两端拖点拖到目标节点身上松手;也可直接抓线身拖(按按下点靠近哪端改哪端)。
  // 多线汇入同一节点时拖点按兄弟偏移错开
  const [rehover, setRehover] = useState<string | null>(null)
  // 橡皮筋预览:拖拽中画一条固定端口→鼠标的虚线(与拉新线同款样式),线跟着手走
  const [rubber, setRubber] = useState<{ end: 'source' | 'target'; x: number; y: number } | null>(null)
  const { screenToFlowPosition } = useReactFlow()
  // 橡皮筋路径:改终点→源端口固定不动;改起点→目标端口固定。样式与拉新线(connectionLine)一致
  const rubberPath = rubber
    ? rubber.end === 'target'
      ? getSmoothStepPath({ sourceX, sourceY, sourcePosition, targetX: rubber.x, targetY: rubber.y, targetPosition, borderRadius: 14 })[0]
      : getSmoothStepPath({ sourceX: rubber.x, sourceY: rubber.y, sourcePosition, targetX, targetY, targetPosition, borderRadius: 14 })[0]
    : null
  const rehoverRef = useRef<string | null>(null)
  const movedRef = useRef(false)
  const nodeUnder = useCallback((cx: number, cy: number): string | null => {
    // 命中盒外扩 12 视觉像素:fitView 缩放下节点视觉盒很小(可到 ~60x20),
    // 精确落盒极易 miss;miss 曾静默失败(拖了没反应),是「改接不好用」的主因
    const PAD = 12
    for (const el of document.querySelectorAll('.react-flow__node')) {
      if (el.classList.contains('dragging')) continue
      const r = el.getBoundingClientRect()
      if (cx >= r.x - PAD && cx <= r.x + r.width + PAD && cy >= r.y - PAD && cy <= r.y + r.height + PAD) {
        return el.getAttribute('data-id')
      }
    }
    return null
  }, [])
  const markHover = useCallback((id: string | null) => {
    for (const el of document.querySelectorAll('.fk-rehover')) el.classList.remove('fk-rehover')
    if (id) document.querySelector(`.react-flow__node[data-id="${id}"]`)?.classList.add('fk-rehover')
  }, [])
  const beginReconnect = useCallback(
    (end: 'source' | 'target') => (e: { clientX: number; clientY: number; button?: number; stopPropagation?: () => void; preventDefault?: () => void }) => {
      // 只认主键:右键按下线身/手柄不得静默启动改接(与右键菜单手势隔离)
      if (e.button !== undefined && e.button !== 0) return
      e.stopPropagation?.()
      e.preventDefault?.()
      movedRef.current = false
      const startX = e.clientX
      const startY = e.clientY
      const valid = (a: string, b: string) => {
        const st = useStore.getState().spec
        if (!st || a === b) return false
        const an = st.nodes.find((n) => n.code === a)
        const bn = st.nodes.find((n) => n.code === b)
        if (!an || !bn || an.kind === 'end' || bn.kind === 'start') return false
        return true
      }
      const onMove = (ev: PointerEvent) => {
        if (!movedRef.current && Math.hypot(ev.clientX - startX, ev.clientY - startY) > 3) movedRef.current = true
        // 过 3px 阈值(确认真拖)才画橡皮筋,原地点击不留残影
        if (movedRef.current) setRubber({ end, ...screenToFlowPosition({ x: ev.clientX, y: ev.clientY }) })
        const id2 = nodeUnder(ev.clientX, ev.clientY)
        if (id2 !== rehoverRef.current) {
          rehoverRef.current = id2
          markHover(id2)
          setRehover(id2)
        }
      }
      const finish = (ev: PointerEvent, cancel: boolean) => {
        cleanup()
        if (cancel) return
        if (!movedRef.current) return // 原地点击(无拖动):当作普通选中,不打扰
        const dropId = nodeUnder(ev.clientX, ev.clientY)
        if (!dropId) {
          showToast('改接未落在节点上 — 已取消(把线拖到目标节点身上松手)')
          return
        }
        if (dropId === source || dropId === target) return
        const next = end === 'target' ? { source, target: dropId } : { source: dropId, target }
        if (!valid(next.source, next.target)) {
          showToast('无法改接:end 不可出线 / start 不可入线')
          return
        }
        reconnectFlow(source, target, next.source, next.target)
      }
      const onUp = (ev: PointerEvent) => finish(ev, false)
      const onKey = (ev: KeyboardEvent) => { if (ev.key === 'Escape') finish(ev as unknown as PointerEvent, true) }
      const cleanup = () => {
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
        window.removeEventListener('keydown', onKey)
        rehoverRef.current = null
        markHover(null)
        setRehover(null)
        setRubber(null)
        document.body.classList.remove('fk-dragging')
      }
      document.body.classList.add('fk-dragging')
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
      window.addEventListener('keydown', onKey)
    },
    [source, target, reconnectFlow, showToast, nodeUnder, markHover, screenToFlowPosition],
  )
  // 整条线可拖:选中后在线身按下即开始改接,**恒改终点(箭头端)** ——「把 A→B 的线拖到 C」
  // 语义就是箭头指向 C;要改起点用起点端的拖点手柄。未选中时这次按下只负责选中(上层
  // onClick),下一次按下才拖 —— 与拖点手柄行为一致
  const onEdgeBodyPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (!selected) return
      beginReconnect('target')(e)
    },
    [selected, beginReconnect],
  )
  const srcOff = d?.srcOffset ?? 0
  const tgtOff = d?.tgtOffset ?? 0
  // 拖点沿路径向内缩进 36px:不再压住节点的出线口/入线口(端口保持可拉新线),
  // 同时仍骑在自己的线上;多线兄弟偏移(±18px)保证互相不叠
  const [startPt, endPt] = selected ? sampleEdgePath(path, 36, 36) : [{ x: sourceX, y: sourceY }, { x: targetX, y: targetY }]
  return (
    <g
      className={dragFocus ? 'drag-focus' : undefined}
      onClick={() => select({ type: 'edge', id })}
      onPointerDown={onEdgeBodyPointerDown}
      onContextMenu={(e) => {
        e.preventDefault()
        e.stopPropagation()
        select({ type: 'edge', id })
        useStore.getState().openContextMenu({ x: e.clientX, y: e.clientY, kind: 'edge', id })
      }}
      style={{ cursor: 'pointer' }}
    >
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={{ stroke: active ? '#2f6bff' : hit ? '#16a34a' : '#9aa8bd', strokeWidth: active || hit ? 2.2 : 1.6 }} />
      {rubberPath && (
        <path
          d={rubberPath}
          fill="none"
          stroke="#2f6bff"
          strokeWidth={2.2}
          strokeDasharray="7 5"
          pointerEvents="none"
        />
      )}
      {label ? (
        <EdgeText
          x={labelX}
          y={labelY - 4}
          label={label}
          labelStyle={{ fill: active || hit ? '#fff' : '#475569', fontSize: 11, fontWeight: active || hit ? 600 : 400 }}
          labelShowBg
          labelBgPadding={[6, 3]}
          labelBgBorderRadius={6}
          labelBgStyle={{ fill: active ? '#2f6bff' : hit ? '#16a34a' : '#fff', stroke: active ? '#2f6bff' : hit ? '#16a34a' : '#dbe3ef', strokeWidth: 1 }}
        />
      ) : null}
      {selected && (
        <g onClick={(e) => { e.stopPropagation(); deleteEdge() }}>
          <circle cx={labelX} cy={labelY + 16} r="9" fill="#fff" stroke="#dc2626" strokeWidth="1.2" style={{ cursor: 'pointer' }} />
          <path d={`M${labelX - 3.4},${labelY + 12.6} l6.8,6.8 m0,-6.8 l-6.8,6.8`} stroke="#dc2626" strokeWidth="1.6" strokeLinecap="round" />
        </g>
      )}
      {selected && (
        <EdgeLabelRenderer>
          <div
            className={`fk-reconnect ${rehover ? 'armed' : ''}`}
            style={{ left: startPt.x, top: startPt.y + srcOff, transform: 'translate(-50%, -50%)' }}
            onPointerDown={beginReconnect('source')}
            title="拖到别的节点 = 改接起点(Esc 取消);也可直接抓线身拖"
          />
          <div
            className={`fk-reconnect ${rehover ? 'armed' : ''}`}
            style={{ left: endPt.x, top: endPt.y + tgtOff, transform: 'translate(-50%, -50%)' }}
            onPointerDown={beginReconnect('target')}
            title="拖到别的节点 = 改接终点(Esc 取消);也可直接抓线身拖"
          />
        </EdgeLabelRenderer>
      )}
    </g>
  )
}

export const RectNode = memo(RectNodeImpl)
export const CircleNode = memo(CircleNodeImpl)
export const SmoothEdge = memo(SmoothEdgeImpl)

export const nodeTypes = { fk_rect: RectNode, fk_circle: CircleNode }
export const edgeTypes = { fk_smooth: SmoothEdge }
