/** 应用入口:设计器常驻(未打开流程时画布区是空态);全局键盘(撤销/重做/删除)与 toast */

import { useEffect } from 'react'
import { useStore } from './store'
import { designerConfig } from './config'
import { EmptyState } from './components/EmptyState'
import { Topbar } from './components/Topbar'
import { Palette } from './components/Palette'
import { Canvas } from './components/Canvas'
import { RightPanel } from './components/RightPanel'
import { PublishDialog } from './components/PublishDialog'
import { DryRunDialog } from './components/DryRunDialog'

function Toast() {
  const toast = useStore((s) => s.toast)
  if (!toast) return null
  return <div className="toast">{toast}</div>
}

function Designer() {
  const deleteSelection = useStore((s) => s.deleteSelection)
  const undo = useStore((s) => s.undo)
  const redo = useStore((s) => s.redo)
  const spec = useStore((s) => s.spec)
  const readonly = designerConfig.readonly()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (readonly) return // 只读审计:编辑类快捷键全部不响应
      const tag = (e.target as HTMLElement | null)?.tagName
      const typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
      // Ctrl+Z 在 typing 判定之后(review P1-6):输入框内的文本级撤销不得被
      // Spec 级 undo 劫持——修前 JSON Tab/公式框里 Cmd+Z 丢弃输入中内容
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        e.shiftKey ? redo() : undo()
        return
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        void useStore.getState().saveNow()
        return
      }
      if (e.key === 'Escape') {
        useStore.getState().setPendingConnect(null)
        return
      }
      if (typing) return // 文本输入内其余按键不触发画布动作
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        deleteSelection()
      }
    }
    window.addEventListener('keydown', onKey)
    // 手动保存模式:有未保存变更时关页/刷新给一次拦截提示
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      // readonly 无写路径不会 dirty;守卫兜底——审计页不弹「未保存」(review P0-2 连带)
      if (!designerConfig.readonly() && useStore.getState().dirty) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('beforeunload', onBeforeUnload)
    }
  }, [deleteSelection, undo, redo, readonly])

  return (
    <div className="designer">
      <Topbar />
      <div className="designer-main">
        {!spec ? (
          <EmptyState />
        ) : (
          <>
            {!readonly && <Palette />}
            <Canvas />
            {!readonly && <RightPanel />}
          </>
        )}
      </div>
      <PublishDialog />
      <DryRunDialog />
    </div>
  )
}

export default function App() {
  const bootstrap = useStore((s) => s.bootstrap)
  useEffect(() => {
    void bootstrap()
    // 深链:配置了 definitionCode 时挂载后直接打开该定义(审计插件常用入口)
    const code = designerConfig.definitionCode()
    if (code) void useStore.getState().openDefinition(code)
  }, [bootstrap])
  return (
    <>
      <Designer />
      <Toast />
    </>
  )
}
