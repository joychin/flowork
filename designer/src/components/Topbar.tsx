/** 顶栏:返回 / 定义信息 / 生命周期状态 / 保存状态 / 校验与发布动作 */

import { useStore } from '../store'

export function Topbar() {
  const defInfo = useStore((s) => s.defInfo)
  const spec = useStore((s) => s.spec)
  const issues = useStore((s) => s.issues)
  const saveState = useStore((s) => s.saveState)
  const back = useStore((s) => s.backToListing)
  const setPublishOpen = useStore((s) => s.setPublishOpen)
  const setRightTab = useStore((s) => s.setRightTab)
  const undo = useStore((s) => s.undo)
  const redo = useStore((s) => s.redo)
  const relayout = useStore((s) => s.relayout)
  const openDryRun = useStore((s) => s.openDryRun)
  const past = useStore((s) => s.past.length)
  const future = useStore((s) => s.future.length)

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
      <button className="icon-btn" onClick={undo} disabled={!past} title="撤销 (Ctrl+Z)">↺</button>
      <button className="icon-btn" onClick={redo} disabled={!future} title="重做 (Ctrl+Shift+Z)">↻</button>
      <button className="icon-btn" onClick={relayout} title="按分层重新排布全部节点">⌗</button>
      <button className="btn" onClick={openDryRun} title="按流程变量样例值预演路由,画布高亮路径">▶ 试运行</button>
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
    </header>
  )
}
