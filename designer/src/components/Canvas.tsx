/** 画布:React Flow 受控渲染;调色板拖放入画布、节点拖拽调位、拖拽/点击两种连线 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  BackgroundVariant,
  ConnectionLineType,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeMouseHandler,
  type EdgeTypes,
  type Node as RFNode,
  type NodeMouseHandler,
  type OnConnectEnd,
  type OnNodeDrag,
  type OnReconnect,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { nodeSize, toFlowElements } from '../adapter'
import { useStore } from '../store'
import { edgeTypes, nodeTypes } from './FlowNodes'

export function Canvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  )
}

function CanvasInner() {
  const spec = useStore((s) => s.spec)
  const metaByKind = useStore((s) => s.metaByKind)
  const issues = useStore((s) => s.issues)
  const selection = useStore((s) => s.selection)
  const pendingConnect = useStore((s) => s.pendingConnect)
  const select = useStore((s) => s.select)
  const setNodePos = useStore((s) => s.setNodePos)
  const connect = useStore((s) => s.connect)
  const setPendingConnect = useStore((s) => s.setPendingConnect)
  const addNode = useStore((s) => s.addNode)
  const reconnectFlow = useStore((s) => s.reconnectFlow)
  const showToast = useStore((s) => s.showToast)
  const rf = useReactFlow()
  const [hintOpen, setHintOpen] = useState(true)
  const [dropReady, setDropReady] = useState(false)

  // 拖拽中的实时位置:自动校验/保存等重渲染发生时,用实时位置覆盖派生位置,避免节点回弹
  const livePositions = useRef<Record<string, { x: number; y: number }>>({})
  useEffect(() => { livePositions.current = {} }, [spec])

  // 端点改道拖拽标记:onReconnectStart/onReconnectEnd 之间,onConnectEnd 需跳过(两者都由同一次松手触发)
  const reconnectDrag = useRef<{ active: boolean; handledAt: number }>({ active: false, handledAt: 0 })

  const elements = useMemo(() => {
    if (!spec) return { nodes: [] as RFNode[], edges: [] }
    const { nodes, edges } = toFlowElements(spec, metaByKind, issues, selection, pendingConnect)
    return {
      nodes: nodes.map((n) => {
        const live = livePositions.current[n.id]
        return live ? { ...n, position: live } : n
      }),
      edges,
    }
  }, [spec, metaByKind, issues, selection, pendingConnect])

  const onNodeDrag = useCallback<OnNodeDrag>(
    (_event, node) => {
      livePositions.current[node.id] = node.position
    },
    [],
  )

  const onNodeDragStop = useCallback<OnNodeDrag>(
    (_event, node) => {
      setNodePos(node.id, Math.round(node.position.x), Math.round(node.position.y))
    },
    [setNodePos],
  )

  const onNodeClick = useCallback<NodeMouseHandler>(
    (_event, node) => {
      if (pendingConnect && pendingConnect !== node.id) connect(pendingConnect, node.id)
      else select({ type: 'node', id: node.id })
    },
    [pendingConnect, connect, select],
  )

  const onEdgeClick = useCallback<EdgeMouseHandler>(
    (_event, edge) => select({ type: 'edge', id: edge.id }),
    [select],
  )

  const onConnect = useCallback(
    (connection: Connection) => {
      if (connection.source && connection.target) {
        connect(connection.source, connection.target)
        setPendingConnect(null) // 拖拽连线成功后,清理目标 handle 上误触发的 pending 状态
      }
    },
    [connect, setPendingConnect],
  )

  // 拖线过程中的实时校验:非法目标不吸附、不落线(end 无出线 / start 无入线 / 自环)
  const isValidConnection = useCallback(
    (conn: Connection | Edge) => {
      if (!spec) return false
      const { source, target } = conn
      if (!source || !target || source === target) return false
      const sourceNode = spec.nodes.find((n) => n.code === source)
      const targetNode = spec.nodes.find((n) => n.code === target)
      if (!sourceNode || !targetNode) return false
      if (sourceNode.kind === 'end' || targetNode.kind === 'start') return false
      return true
    },
    [spec],
  )

  // 拖动边的端点改道
  const onReconnect = useCallback<OnReconnect>(
    (oldEdge, connection) => {
      if (connection.source && connection.target) {
        reconnectFlow(oldEdge.source, oldEdge.target, connection.source, connection.target)
      }
    },
    [reconnectFlow],
  )

  // 兜底:拖线/改道松手时若落在「节点身上」(而非端口附近),视为连接该节点 —— 符合直觉,无需瞄准小圆点
  const nodeUnderCursor = (event: MouseEvent | TouchEvent | PointerEvent): string | null => {
    const me = event as MouseEvent
    const el = document.elementFromPoint(me.clientX, me.clientY)
    return el?.closest('.react-flow__node')?.getAttribute('data-id') ?? null
  }

  const onConnectEnd = useCallback<OnConnectEnd>(
    (event, state) => {
      // 边端点改道拖拽会同时触发 onConnectEnd —— 已由 onReconnectEnd 处理,这里跳过
      if (reconnectDrag.current.active || Date.now() - reconnectDrag.current.handledAt < 150) return
      if (!state?.fromNode || state.isValid === true) return
      const targetId = nodeUnderCursor(event)
      if (!targetId || targetId === state.fromNode.id) return
      const source = state.fromNode.id
      if (!isValidConnection({ source, target: targetId, sourceHandle: null, targetHandle: null })) return
      connect(source, targetId)
    },
    [isValidConnection, connect],
  )

  const onReconnectStart = useCallback(() => {
    reconnectDrag.current.active = true
  }, [])

  const onReconnectEnd = useCallback(
    (
      event: MouseEvent | TouchEvent,
      edge: Edge,
      handleType: 'source' | 'target',
      state: { isValid: boolean | null } | null,
    ) => {
      reconnectDrag.current.handledAt = Date.now()
      reconnectDrag.current.active = false
      if (state?.isValid === true) return
      const targetId = nodeUnderCursor(event)
      if (!targetId) return
      // RF 语义:handleType 是「固定端」——拖 source 端时传 'target',反之亦然
      const next =
        handleType === 'target'
          ? { source: targetId, target: edge.target }
          : { source: edge.source, target: targetId }
      if (!isValidConnection({ ...next, sourceHandle: null, targetHandle: null })) return
      reconnectFlow(edge.source, edge.target, next.source, next.target)
    },
    [isValidConnection, reconnectFlow],
  )

  // ── 调色板拖放入画布(带"可放置"光晕;节点以光标为中心落下) ──
  const isNodeDrag = (e: React.DragEvent) => e.dataTransfer.types.includes('application/flowork-node')

  const onDragEnter = useCallback((e: React.DragEvent) => {
    if (isNodeDrag(e)) {
      e.preventDefault()
      setDropReady(true)
    }
  }, [])

  const onDragOver = useCallback((e: React.DragEvent) => {
    if (isNodeDrag(e)) {
      e.preventDefault()
      e.dataTransfer.dropEffect = 'copy'
    }
  }, [])

  const onDragLeave = useCallback((e: React.DragEvent) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDropReady(false)
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      setDropReady(false)
      const raw = e.dataTransfer.getData('application/flowork-node')
      if (!raw) return
      e.preventDefault()
      try {
        const { kind, defaults } = JSON.parse(raw) as { kind: string; defaults: Record<string, unknown> }
        const pos = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY })
        const size = nodeSize(metaByKind[kind])
        addNode(kind, defaults, { x: Math.round(pos.x - size.w / 2), y: Math.round(pos.y - size.h / 2) })
      } catch {
        /* 忽略非法拖拽数据 */
      }
    },
    [rf, metaByKind, addNode],
  )

  if (!spec) return null

  return (
    <div
      className={`canvas-wrap ${dropReady ? 'drop-ready' : ''}`}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <ReactFlow
        key={spec.code}
        nodes={elements.nodes}
        edges={elements.edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes as EdgeTypes}
        onNodeDrag={onNodeDrag}
        onNodeDragStop={onNodeDragStop}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        onReconnect={onReconnect}
        onReconnectStart={onReconnectStart}
        onReconnectEnd={onReconnectEnd}
        onConnect={onConnect}
        onConnectEnd={onConnectEnd}
        onPaneClick={() => { select(null); setPendingConnect(null) }}
        isValidConnection={isValidConnection}
        connectionRadius={28}
        connectionLineType={ConnectionLineType.SmoothStep}
        connectionLineStyle={{ stroke: '#2f6bff', strokeWidth: 2.2, strokeDasharray: '7 5' }}
        reconnectRadius={12}
        zoomOnDoubleClick={false}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.3}
        maxZoom={1.8}
        deleteKeyCode={null}
        multiSelectionKeyCode={null}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.6} color="#d7dee9" />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeStrokeWidth={3} className="fk-minimap" />
      </ReactFlow>
      {pendingConnect && (
        <div className="connect-banner">
          正在从 <b>{pendingConnect}</b> 拉线 — 点击目标节点完成连接,点击空白处取消
        </div>
      )}
      {hintOpen && !pendingConnect && (
        <div className="canvas-hint-banner">
          <span>
            <b>加节点</b>:把左侧类型拖进画布(或点击) · <b>连线</b>:从节点右侧圆点<b>拖到</b>目标节点,或点圆点后点目标 ·
            <b> 调位置</b>:直接拖动节点
          </span>
          <button className="hint-close" onClick={() => setHintOpen(false)} title="关闭提示">×</button>
        </div>
      )}
    </div>
  )
}
