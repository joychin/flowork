/** 顶栏:返回 / 定义信息 / 生命周期状态 / 保存状态 / 校验与发布动作(只读模式裁剪) */

import { useState } from 'react'
import { useStore } from '../store'
import { designerConfig } from '../config'
import { InstancesDialog } from './InstancesDialog'

export function Topbar() {
  const defInfo = useStore((s) => s.defInfo)
  const spec = useStore((s) => s.spec)
  const issues = useStore((s) => s.issues)
  const saveState = useStore((s) => s.saveState)
  const dirty = useStore((s) => s.dirty)
  const saveNow = useStore((s) => s.saveNow)
  const back = useStore((s) => s.backToListing)
  const setPublishOpen = useStore((s) => s.setPublishOpen)
  const setRightTab = useStore((s) => s.setRightTab)
  const undo = useStore((s) => s.undo)
  const redo = useStore((s) => s.redo)
  const relayout = useStore((s) => s.relayout)
  const openDryRun = useStore((s) => s.openDryRun)
  const past = useStore((s) => s.past.length)
  const future = useStore((s) => s.future.length)
  // 审计实例视图(批 B3):本地开合,不入全局状态
  const [instancesOpen, setInstancesOpen] = useState(false)
  // 只读(宿主嵌入):审计面——保存/撤销/重排/校验/发布全部隐藏
  const readonly = designerConfig.readonly()

  if (!defInfo || !spec) return null
  const errorCount = issues.filter((i) => i.level === 'error').length
  const warnCount = issues.filter((i) => i.level === 'warning').length

  return (
    <header className="topbar">
      <button className="icon-btn" onClick={back} title="返回定义列表">←</button>
      <div className="brand">
        <span className="brand-name">flowork</span>
        <span className="brand-sub">流程设计器</span>
      </div>
      <div className="def-info">
        <span className="def-name">{spec.name || defInfo.name || defInfo.code}</span>
        <span className="def-code">{defInfo.code}</span>
        {defInfo.draftVersion != null && <span className="chip chip-draft">草稿 v{defInfo.draftVersion}</span>}
        {defInfo.publishedVersion != null && <span className="chip chip-pub">v{defInfo.publishedVersion} 已发布</span>}
      </div>
      <div className="spacer" />
      {!readonly && (
        <>
          <button className="icon-btn" onClick={undo} disabled={!past} title="撤销 (Ctrl+Z)">↺</button>
          <button className="icon-btn" onClick={redo} disabled={!future} title="重做 (Ctrl+Shift+Z)">↻</button>
          <button className="icon-btn" onClick={relayout} title="按分层重新排布全部节点">⌗</button>
        </>
      )}
      <button className="btn" onClick={openDryRun} title="路由预演 + 完整模拟(草稿可跑,证据先于发布)">▶ 试运行</button>
      <button className="btn" onClick={() => setInstancesOpen(true)} title="实例审计:走到哪、条件为什么这么选">⚖ 实例</button>
      {!readonly && (
        <>
          <button
            className={`btn ${dirty ? 'btn-primary' : ''}`}
            onClick={() => void saveNow()}
            disabled={!dirty || saveState === 'saving'}
            title="保存草稿 (Ctrl+S / Cmd+S)"
          >
            {saveState === 'saving' ? '保存中…' : dirty ? '● 保存' : '已保存'}
          </button>
          <span className={`save-state ${saveState}`}>
            {saveState === 'saving' && '保存中…'}
            {saveState === 'saved' && '✓ 已保存'}
            {saveState === 'error' && '✕ 保存失败'}
            {saveState === 'idle' && ''}
          </span>
          <button className={`btn ${errorCount ? 'btn-danger' : ''}`} onClick={() => { setRightTab('issues'); void useStore.getState().validateNow() }} title="运行设计时校验">
            校验{errorCount ? ` · ${errorCount}错` : ''}{!errorCount && warnCount ? ` · ${warnCount}警` : ''}
          </button>
          <button className="btn btn-primary" onClick={() => setPublishOpen(true)}>
            {defInfo.publishedVersion != null ? `发布 v${defInfo.publishedVersion + 1}` : '发布 v1'}
          </button>
        </>
      )}
      {instancesOpen && <InstancesDialog code={defInfo.code} onClose={() => setInstancesOpen(false)} />}
    </header>
  )
}
