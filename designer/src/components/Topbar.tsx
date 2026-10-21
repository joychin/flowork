/** 顶栏:返回 / 定义信息 / 生命周期状态 / 保存状态 / 校验与发布动作(只读模式裁剪) */

import { useState } from 'react'
import { useStore } from '../store'
import { designerConfig } from '../config'
import { ScenarioDialog } from './ScenarioDialog'

export function Topbar() {
  const defInfo = useStore((s) => s.defInfo)
  const spec = useStore((s) => s.spec)
  const issues = useStore((s) => s.issues)
  const saveState = useStore((s) => s.saveState)
  const dirty = useStore((s) => s.dirty)
  const saveNow = useStore((s) => s.saveNow)
  const back = useStore((s) => s.closeDefinition)
  const setPublishOpen = useStore((s) => s.setPublishOpen)
  const setRightTab = useStore((s) => s.setRightTab)
  const undo = useStore((s) => s.undo)
  const redo = useStore((s) => s.redo)
  const relayout = useStore((s) => s.relayout)
  const openDryRun = useStore((s) => s.openDryRun)
  const exportSpec = useStore((s) => s.exportSpec)
  const runtime = useStore((s) => s.runtime)
  const past = useStore((s) => s.past.length)
  const future = useStore((s) => s.future.length)
  // 场景(审阅动作,只读模式同样可用(零副作用))
  const [scenarioOpen, setScenarioOpen] = useState(false)
  // 只读(宿主嵌入):审计面——保存/撤销/重排/校验/发布全部隐藏
  const readonly = designerConfig.readonly()

  // 未打开流程:只留品牌条,打开入口在画布空态里
  if (!defInfo || !spec) {
    return (
      <header className="topbar">
        <div className="brand">
          <span className="brand-name">flowork</span>
          <span className="brand-sub">流程设计器</span>
        </div>
      </header>
    )
  }
  const errorCount = issues.filter((i) => i.level === 'error').length
  const warnCount = issues.filter((i) => i.level === 'warning').length

  return (
    <header className="topbar">
      <button className="icon-btn" onClick={back} title="关闭当前流程">←</button>
      <div className="brand">
        <span className="brand-name">flowork</span>
        <span className="brand-sub">流程设计器</span>
      </div>
      <div className="def-info">
        <span className="def-name">{spec.name || defInfo.name || defInfo.code}</span>
        <span className="def-code">{defInfo.fileName ?? defInfo.code}</span>
        {/* 来源：文件=目录里的 JSON（保存写回它）；local=浏览器本地文件（未入库） */}
        {defInfo.source === 'file' && <span className="chip" title="保存直接写回这个 JSON 文件">JSON 文件</span>}
        {defInfo.source === 'local' && (
          <span className="chip chip-draft" title="浏览器里打开的本地文件——保存即下载，未入库">未入库</span>
        )}
        {defInfo.source !== 'local' && defInfo.draftVersion != null && (
          <span className="chip chip-draft">草稿 v{defInfo.draftVersion}</span>
        )}
        {defInfo.source !== 'local' && defInfo.publishedVersion != null && (
          <span className="chip chip-pub">v{defInfo.publishedVersion} 已发布</span>
        )}
        {defInfo.source === 'file' && defInfo.publishedVersion == null && (
          <span className="chip" title="该 code 尚无已发布版本——对比线上版与版本判定预演不可用">未发布</span>
        )}
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
      <button className="btn" onClick={() => setScenarioOpen(true)} title="场景回归:可复跑脚本 + 断言(与 CI 同一份文件)">🧪 场景</button>
      {!readonly && (
        <>
          <button
            className={`btn ${dirty ? 'btn-primary' : ''}`}
            onClick={() => void saveNow()}
            // 本地会话的保存＝导出（没有落盘通道），所以不看 dirty——随时可导出
            disabled={defInfo.source !== 'local' && (!dirty || saveState === 'saving')}
            title={
              defInfo.source === 'file'
                ? '保存并写回 JSON 文件 (Ctrl+S / Cmd+S)'
                : defInfo.source === 'local'
                  ? '导出为 JSON 文件 (Ctrl+S / Cmd+S)——本地会话没有其他落盘通道'
                  : '保存草稿 (Ctrl+S / Cmd+S)'
            }
          >
            {saveState === 'saving'
              ? '保存中…'
              : dirty
                ? defInfo.source === 'local' ? '● 导出' : '● 保存'
                : defInfo.source === 'local' ? '导出' : '已保存'}
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
          {/* 本地会话没有「已保存」这个概念（保存即下载），独立导出按钮只对入库来源有意义 */}
          {defInfo.source !== 'local' && (
            <button
              className="btn"
              onClick={() => void exportSpec()}
              title="导出为 JSON 文件（不含画布坐标）——提交 git 后由流水线 deploy"
            >
              导出
            </button>
          )}
          {/* 发布只存在于数据库形态：文件形态下进生产的那一步是 CI/CD 的 deploy */}
          {runtime.source === 'db' && (
            <button className="btn btn-primary" onClick={() => setPublishOpen(true)}>
              {defInfo.publishedVersion != null ? `发布 v${defInfo.publishedVersion + 1}` : '发布 v1'}
            </button>
          )}
        </>
      )}
      {scenarioOpen && (
        <ScenarioDialog spec={spec} code={defInfo.code} onClose={() => setScenarioOpen(false)} />
      )}
    </header>
  )
}
