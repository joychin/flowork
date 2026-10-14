/** 定义列表页:所有 ProcessDefinition 汇总 + 新建入口 */

import { useState } from 'react'
import { useStore } from '../store'
import { designerConfig } from '../config'

export function DefinitionList() {
  const listing = useStore((s) => s.listing)
  const loading = useStore((s) => s.listingLoading)
  const listingError = useStore((s) => s.listingError)
  const refreshListing = useStore((s) => s.refreshListing)
  const openDefinition = useStore((s) => s.openDefinition)
  const createDefinition = useStore((s) => s.createDefinition)
  const [creating, setCreating] = useState(false)
  const [code, setCode] = useState('')
  const [name, setName] = useState('')

  const submit = async () => {
    const trimmed = code.trim()
    if (!/^[a-z][a-z0-9_]*$/.test(trimmed)) return
    await createDefinition(trimmed, name.trim())
    setCreating(false)
    setCode('')
    setName('')
  }

  return (
    <div className="list-page">
      <header className="list-header">
        <div className="logo-lg">f</div>
        <div>
          <h1>flowork · 流程定义</h1>
          <p className="list-sub">流程定义(ProcessDefinition)草稿 → 校验 → 发布;发布版本不可变。</p>
        </div>
        <div className="spacer" />
        {!designerConfig.readonly() && (
          <button className="btn btn-primary" onClick={() => setCreating(true)}>＋ 新建流程</button>
        )}
      </header>

      {loading ? (
        <div className="empty-hint">加载中…</div>
      ) : (
        <div className="def-grid">
          {listing.map((def) => (
            <button key={def.code} className="def-card" onClick={() => void openDefinition(def.code)}>
              <div className="def-card-head">
                <span className="def-card-name">{def.name || def.code}</span>
                <span className="def-card-arrow">→</span>
              </div>
              <div className="def-card-code">{def.code}</div>
              <div className="def-card-chips">
                {def.draft_version != null && <span className="chip chip-draft">草稿 v{def.draft_version}</span>}
                {def.published_version != null && <span className="chip chip-pub">v{def.published_version} 已发布</span>}
                {def.draft_version == null && def.published_version == null && <span className="chip">空</span>}
              </div>
              <div className="def-card-time">{def.updated_at ? `更新于 ${def.updated_at.replace('T', ' ')}` : ''}</div>
            </button>
          ))}
          {listingError && !loading && (
            <div className="empty-hint">
              后端不可达或请求失败：{listingError}{' '}
              <button className="btn" onClick={() => void refreshListing()}>重试</button>
            </div>
          )}
          {!listingError && !listing.length && (
            <div className="empty-hint">还没有流程定义 — 点右上角「新建流程」。</div>
          )}
        </div>
      )}

      {creating && (
        <div className="modal-mask" onClick={() => setCreating(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>新建流程定义</h3>
            <p className="modal-sub">创建含「发起 → 结束」最小结构的草稿,进入设计器继续搭建。</p>
            <div className="field">
              <div className="field-label"><span>code</span></div>
              <input value={code} placeholder="小写字母/数字/下划线,如 travel_expense" onChange={(e) => setCode(e.target.value)} autoFocus />
              {code && !/^[a-z][a-z0-9_]*$/.test(code) && <div className="json-bad">code 需以小写字母开头,仅含小写字母/数字/下划线</div>}
            </div>
            <div className="field">
              <div className="field-label"><span>名称</span></div>
              <input value={name} placeholder="如 差旅报销审批" onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void submit()} />
            </div>
            <div className="modal-actions">
              <button className="btn" onClick={() => setCreating(false)}>取消</button>
              <button className="btn btn-primary" disabled={!/^[a-z][a-z0-9_]*$/.test(code)} onClick={() => void submit()}>创建并打开</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
