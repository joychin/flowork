/** 调色板:由 /api/node-kinds 的 ui.palette 生成,点击即添加节点 */

import { useMemo } from 'react'
import { useStore } from '../store'
import { Icon } from './FlowNodes'

export function Palette() {
  const meta = useStore((s) => s.meta)
  const startPaletteDrag = useStore((s) => s.startPaletteDrag)

  const groups = useMemo(() => {
    const out = new Map<string, { kind: string; label: string; defaults: Record<string, unknown>; color: string; icon: string }[]>()
    for (const m of meta) {
      for (const item of m.ui.palette ?? []) {
        const category = m.ui.category ?? '其他'
        if (!out.has(category)) out.set(category, [])
        out.get(category)!.push({
          kind: m.kind,
          label: item.label,
          defaults: item.defaults,
          color: m.ui.color ?? '#2563eb',
          icon: m.ui.icon ?? '',
        })
      }
    }
    return [...out.entries()]
  }, [meta])

  return (
    <aside className="palette">
      <div className="palette-title">节点类型</div>
      {groups.map(([category, items]) => (
        <div key={category}>
          <div className="palette-group">{category}</div>
          {items.map((item, i) => (
            <button
              key={`${item.kind}-${i}`}
              className="palette-item"
              onPointerDown={(e) => {
                // 指针驱动拖拽:Canvas 挂窗口级 pointermove/up;原地松手 = 点击添加
                if (e.button !== 0) return
                e.preventDefault()
                startPaletteDrag({ kind: item.kind, defaults: { ...item.defaults, name: item.label }, label: item.label })
              }}
              title={`拖到画布任意位置,或点击添加「${item.label}」`}
            >
              <span className="palette-ico" style={{ background: `${item.color}1a`, color: item.color }}>
                <Icon name={item.icon} />
              </span>
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      ))}
      <div className="palette-foot">
        ◉ 本面板由引擎 node_registry 元数据生成 — 新增节点类型,前端零代码
      </div>
    </aside>
  )
}
