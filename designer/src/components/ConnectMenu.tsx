/** 连线拖到空白处的快捷新建菜单(n8n/Dify 模式):
 *  松手位置弹出节点类型选择,点选即在该处建节点并自动连上来源节点。 */

import { useEffect, useMemo, useRef } from 'react'
import { useStore } from '../store'
import { nodeSize } from '../adapter'
import { Icon } from './FlowNodes'

export function ConnectMenu() {
  const menu = useStore((s) => s.connectMenu)
  const meta = useStore((s) => s.meta)
  const addNodeAndConnect = useStore((s) => s.addNodeAndConnect)
  const close = useStore((s) => s.closeConnectMenu)
  const rootRef = useRef<HTMLDivElement | null>(null)

  const items = useMemo(() => {
    const out: { kind: string; label: string; defaults: Record<string, unknown>; color: string; icon: string }[] = []
    for (const m of meta) {
      for (const p of m.ui.palette ?? []) {
        out.push({ kind: m.kind, label: p.label, defaults: { ...p.defaults, name: p.label }, color: m.ui.color ?? '#2563eb', icon: m.ui.icon ?? '' })
      }
    }
    return out
  }, [meta])

  // 点击菜单外 / Esc 关闭
  useEffect(() => {
    if (!menu) return
    const onDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) close()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('pointerdown', onDown, true)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('pointerdown', onDown, true)
      window.removeEventListener('keydown', onKey)
    }
  }, [menu, close])

  if (!menu) return null
  const width = 208
  // 折返定位以画布为基准(拖线甩出画布时菜单仍弹回画布内,不盖右栏)
  const left = Math.max(menu.bx + 8, Math.min(menu.sx + 10, menu.bx + menu.bw - width - 12))
  const top = Math.max(8, Math.min(menu.sy - 12, window.innerHeight - 380))

  const pick = (item: { kind: string; defaults: Record<string, unknown>; label: string; color: string; icon: string }) => {
    // 节点以落点为中心放置
    const meta = useStore.getState().metaByKind[item.kind]
    const size = nodeSize(meta)
    addNodeAndConnect(item.kind, item.defaults, { x: menu.fx - size.w / 2, y: menu.fy - size.h / 2 }, menu.source)
  }

  return (
    <div ref={rootRef} className="connect-menu" style={{ left, top, width }}>
      <div className="connect-menu-head">选择要连接的节点</div>
      <div className="connect-menu-list">
        {items.map((item, i) => (
          <button key={`${item.kind}-${i}`} className="connect-menu-item" onClick={() => { pick(item); close() }}>
            <span className="palette-ico" style={{ background: `${item.color}1a`, color: item.color }}>
              <Icon name={item.icon} />
            </span>
            <span>{item.label}</span>
          </button>
        ))}
      </div>
      <div className="connect-menu-foot">Esc 取消</div>
    </div>
  )
}
