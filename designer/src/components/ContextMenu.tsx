/** 右键菜单:节点(拉线/复制/编辑/删除)与连线(编辑/默认边/删除)各一组动作。
 *  打开即挂窗口级关闭器(点击任意处 / Esc / 滚动画布),动作执行前先关菜单。 */

import { useEffect } from 'react'
import { useStore } from '../store'

export function ContextMenu() {
  const menu = useStore((s) => s.ctxMenu)
  const spec = useStore((s) => s.spec)
  const close = useStore((s) => s.closeContextMenu)

  useEffect(() => {
    if (!menu) return
    const onDown = (e: PointerEvent) => {
      // 菜单内部的按下不关(否则点菜单项的那一下就把自己拆了,click 永远落空)
      if ((e.target as HTMLElement)?.closest?.('.ctx-menu')) return
      close()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    // capture:抢在画布的 pointerdown 处理(选空/取消拉线)之前把菜单关掉,一次点击只做一件事
    window.addEventListener('pointerdown', onDown, true)
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.removeEventListener('pointerdown', onDown, true)
      window.removeEventListener('keydown', onKey, true)
    }
  }, [menu, close])

  if (!menu || !spec) return null

  const node = menu.kind === 'node' ? spec.nodes.find((n) => n.code === menu.id) : null
  const flow =
    menu.kind === 'edge'
      ? spec.flows.find((f) => `${f.source}->${f.target}` === menu.id)
      : null
  if (!node && !flow) return null

  type Item = { label: string; hint?: string; danger?: boolean; disabled?: boolean; run: () => void }
  const items: Item[] = []

  if (node) {
    const select = useStore.getState().select
    const deletable = node.kind !== 'start'
    // start 有且仅有一个(校验 structure.start.count),且删除守卫硬拦一切 start——
    // 放行复制会产出一个删不掉的副本,流程从此带 ERROR
    const duplicatable = node.kind !== 'start'
    items.push(
      {
        label: '从这里拉线',
        hint: '点目标节点完成',
        disabled: node.kind === 'end',
        run: () => {
          select({ type: 'node', id: node.code })
          useStore.getState().setPendingConnect(node.code)
        },
      },
      {
        label: '复制节点',
        hint: duplicatable ? '连配置一起克隆' : 'start 有且仅有一个',
        disabled: !duplicatable,
        run: () => useStore.getState().duplicateNode(node.code),
      },
      { label: '编辑属性', run: () => select({ type: 'node', id: node.code }) },
      {
        label: '删除节点',
        danger: true,
        disabled: !deletable,
        hint: deletable ? undefined : 'start 不可删除',
        run: () => {
          select({ type: 'node', id: node.code })
          useStore.getState().deleteSelection()
        },
      },
    )
  }

  if (flow) {
    const select = useStore.getState().select
    const edgeId = `${flow.source}->${flow.target}`
    // 默认边只在条件分支的路由里被引擎消费(routing/validator 均只认 branch 出边),
    // 其他连线设了是发布不拦、运行不用的死配置——菜单直接拦在门口
    const defaultEligible = spec.nodes.find((n) => n.code === flow.source)?.kind === 'branch'
    items.push(
      { label: '编辑连线', hint: flow.condition ? '含条件公式' : '无条件', run: () => select({ type: 'edge', id: edgeId }) },
      {
        label: flow.is_default ? '取消默认边' : '设为默认边',
        hint: flow.is_default ? undefined : defaultEligible ? '同源互斥;原条件清空' : '仅条件分支的出线可设',
        disabled: !flow.is_default && !defaultEligible,
        run: () => useStore.getState().toggleDefaultEdge(flow.source, flow.target),
      },
      {
        label: '删除连线',
        danger: true,
        run: () => {
          select({ type: 'edge', id: edgeId })
          useStore.getState().deleteSelection()
        },
      },
    )
  }

  // 视口折返:靠右/靠下时往回弹,避免菜单出屏(W = min-width 176 + 边框 2)
  const W = 178
  const H = items.length * 34 + 12
  const x = Math.min(menu.x, window.innerWidth - W - 8)
  const y = Math.min(menu.y, window.innerHeight - H - 8)

  const run = (item: Item) => {
    close()
    item.run()
  }

  return (
    <div className="ctx-menu" style={{ left: x, top: y }} onContextMenu={(e) => e.preventDefault()}>
      <div className="ctx-menu-title">{node ? `节点 · ${node.code}` : `连线 · ${menu.id}`}</div>
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          className={`ctx-item ${item.danger ? 'danger' : ''}`}
          disabled={item.disabled}
          title={item.hint}
          // 动作挂 pointerdown:菜单项在 click 之前就可能因外部关闭器重渲染,pointerdown 最稳
          onPointerDown={() => run(item)}
        >
          <span>{item.label}</span>
          {item.hint && <span className="ctx-hint">{item.hint}</span>}
        </button>
      ))}
    </div>
  )
}
