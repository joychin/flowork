/** React Flow 自定义节点(矩形/圆)+ 连线,样式与交互(点击连线)在这里挂 */

import { Handle, Position, BaseEdge, EdgeText, getSmoothStepPath } from '@xyflow/react'
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
    default:
      return null
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

export function RectNode({ data, selected }: NodeProps) {
  const d = data as unknown as NodeData
  const color = d.meta?.ui?.color ?? '#2563eb'
  const onClick = useNodeClick(d)
  const actions = useNodeActions()
  return (
    <div className={`fnode rect ${selected ? 'selected' : ''} ${d.pendingConnect ? 'connect-hint' : ''}`} style={{ borderColor: color }} onClick={onClick}>
      {handles(d, actions)}
      {issueBadge(d.issueLevel)}
      {d.kind === 'branch' && <BranchModeBadge mode={d.routeMode} />}
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

export function CircleNode({ data, selected }: NodeProps) {
  const d = data as unknown as NodeData
  const color = d.meta?.ui?.color ?? '#16a34a'
  const onClick = useNodeClick(d)
  const actions = useNodeActions()
  return (
    <div className="fnode-circle-wrap" onClick={onClick}>
      {handles(d, actions)}
      <div className={`fnode circle ${selected ? 'selected' : ''} ${d.pendingConnect ? 'connect-hint' : ''}`} style={{ borderColor: color, background: `${color}1a`, color }}>
        <Icon name={d.meta?.ui?.icon} size={18} />
        {issueBadge(d.issueLevel)}
      </div>
      <div className="fnode-circle-label">{d.name}</div>
    </div>
  )
}

export function SmoothEdge(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, label, selected, markerEnd } = props
  const [path, labelX, labelY] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 14 })
  const deleteEdge = useStore((s) => s.deleteSelection)
  const select = useStore((s) => s.select)
  return (
    <g onClick={() => select({ type: 'edge', id })} style={{ cursor: 'pointer' }}>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={{ stroke: selected ? '#2f6bff' : '#9aa8bd', strokeWidth: selected ? 2.2 : 1.6 }} />
      {label ? (
        <EdgeText
          x={labelX}
          y={labelY - 4}
          label={label}
          labelStyle={{ fill: selected ? '#fff' : '#475569', fontSize: 11, fontWeight: selected ? 600 : 400 }}
          labelShowBg
          labelBgPadding={[6, 3]}
          labelBgBorderRadius={6}
          labelBgStyle={{ fill: selected ? '#2f6bff' : '#fff', stroke: selected ? '#2f6bff' : '#dbe3ef', strokeWidth: 1 }}
        />
      ) : null}
      {selected && (
        <g onClick={(e) => { e.stopPropagation(); deleteEdge() }}>
          <circle cx={labelX} cy={labelY + 16} r="9" fill="#fff" stroke="#dc2626" strokeWidth="1.2" style={{ cursor: 'pointer' }} />
          <path d={`M${labelX - 3.4},${labelY + 12.6} l6.8,6.8 m0,-6.8 l-6.8,6.8`} stroke="#dc2626" strokeWidth="1.6" strokeLinecap="round" />
        </g>
      )}
    </g>
  )
}

export const nodeTypes = { fk_rect: RectNode, fk_circle: CircleNode }
export const edgeTypes = { fk_smooth: SmoothEdge }
