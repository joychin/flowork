/** 应用入口:定义列表 ⇄ 设计器 两态;全局键盘(撤销/重做/删除)与 toast */

import { useEffect } from 'react'
import { useStore } from './store'
import { DefinitionList } from './components/DefinitionList'
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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName
      const typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
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
      if (typing) return
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        deleteSelection()
      }
    }
    window.addEventListener('keydown', onKey)
    // 手动保存模式:有未保存变更时关页/刷新给一次拦截提示
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (useStore.getState().dirty) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('beforeunload', onBeforeUnload)
    }
  }, [deleteSelection, undo, redo])

  return (
    <div className="designer">
      <Topbar />
      <div className="designer-main">
        <Palette />
        <Canvas />
        <RightPanel />
      </div>
      <PublishDialog />
      <DryRunDialog />
    </div>
  )
}

export default function App() {
  const spec = useStore((s) => s.spec)
  const bootstrap = useStore((s) => s.bootstrap)
  useEffect(() => { void bootstrap() }, [bootstrap])
  return (
    <>
      {spec ? <Designer /> : <DefinitionList />}
      <Toast />
    </>
  )
}
