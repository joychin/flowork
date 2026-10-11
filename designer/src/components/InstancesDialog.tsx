/** 实例审计视图(批 B3):定义 → 实例列表(按状态过滤) → 实例详情三面板(时间线/路由决策/变量历史) */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { InstanceDetail, InstanceInfo, RouteDecision, TimelineEntry, VariableChange } from '../types'

const STATE_LABELS: Record<string, string> = {
  running: '运行中',
  paused: '已暂停',
  suspended: '已挂起',
  completed: '已完成',
  terminated: '已终止',
  cancelled: '已作废',
  failed: '故障',
}

const TABS: [string, string][] = [
  ['timeline', '时间线'],
  ['routes', '路由决策'],
  ['variables', '变量历史'],
]

const fmt = (iso: string | null) => (iso ? iso.replace('T', ' ').slice(0, 19) : '-')

export function InstancesDialog({ code, onClose }: { code: string; onClose: () => void }) {
  const [stateFilter, setStateFilter] = useState('')
  const [includeSim, setIncludeSim] = useState(false)
  const [list, setList] = useState<InstanceInfo[] | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (selected) return
    setList(null)
    api
      .instances({ state: stateFilter || undefined, definition_code: code, include_simulated: includeSim })
      .then(setList)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [code, stateFilter, includeSim, selected])

  if (selected) {
    return <InstanceView id={selected} onBack={() => setSelected(null)} onClose={onClose} />
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal dryrun-modal" onClick={(e) => e.stopPropagation()}>
        <h3>审计实例 · {code}</h3>
        <p className="modal-sub">
          运行态审计:谁发起了、走到哪、条件为什么这么选。模拟实例默认隔离(勾选后才可见)。
        </p>
        <div className="inst-filters">
          <select value={stateFilter} onChange={(e) => setStateFilter(e.target.value)}>
            <option value="">全部状态</option>
            {Object.entries(STATE_LABELS).map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
          <label className="inst-sim-toggle">
            <input type="checkbox" checked={includeSim} onChange={(e) => setIncludeSim(e.target.checked)} />
            含模拟实例(试跑留痕)
          </label>
        </div>

        {error && <div className="field-error">{error}</div>}
        {!error && list === null && <div className="empty-sub">加载中…</div>}
        {list !== null && list.length === 0 && <div className="empty-sub">暂无实例</div>}
        {list !== null && list.length > 0 && (
          <div className="inst-list">
            {list.map((inst) => (
              <button key={inst.id} className="inst-row" onClick={() => setSelected(inst.id)}>
                <span className={`chip inst-state ${inst.state}`}>{STATE_LABELS[inst.state] ?? inst.state}</span>
                <span className="inst-id" title={inst.id}>{inst.id.slice(0, 8)}</span>
                <span className="inst-meta">
                  v{inst.definition_major}.{inst.definition_revision} · {fmt(inst.started_at)} · {inst.start_user}
                  {inst.business_key ? ` · ${inst.business_key}` : ''}
                </span>
                {inst.simulated && <span className="chip chip-draft">模拟</span>}
              </button>
            ))}
          </div>
        )}

        <div className="modal-actions">
          <button className="btn" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}

/** 实例详情:概要 + 令牌 + 三面板(时间线/路由决策/变量历史) */
function InstanceView({ id, onBack, onClose }: { id: string; onBack: () => void; onClose: () => void }) {
  const [detail, setDetail] = useState<InstanceDetail | null>(null)
  const [timeline, setTimeline] = useState<TimelineEntry[] | null>(null)
  const [decisions, setDecisions] = useState<RouteDecision[] | null>(null)
  const [varHistory, setVarHistory] = useState<VariableChange[] | null>(null)
  const [tab, setTab] = useState('timeline')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([
      api.instanceDetail(id),
      api.timeline(id).catch(() => null),
      api.routeDecisions(id).catch(() => null),
      api.variableHistory(id).catch(() => null),
    ])
      .then(([d, tl, rd, vh]) => {
        setDetail(d)
        setTimeline(tl)
        setDecisions(rd)
        setVarHistory(vh)
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [id])

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal dryrun-modal" onClick={(e) => e.stopPropagation()}>
        <button className="icon-btn inst-back" onClick={onBack} title="返回实例列表">←</button>
        <h3>
          实例 {id.slice(0, 8)}…{' '}
          {detail && (
            <span className={`chip inst-state ${detail.instance.state}`}>
              {STATE_LABELS[detail.instance.state] ?? detail.instance.state}
            </span>
          )}
          {detail?.instance.simulated && <span className="chip chip-draft">模拟</span>}
        </h3>
        {detail && (
          <div className="inst-kv">
            <span>v{detail.instance.definition_major}.{detail.instance.definition_revision}</span>
            <span>发起 {fmt(detail.instance.started_at)} · {detail.instance.start_user}</span>
            {detail.instance.finished_at && <span>完结 {fmt(detail.instance.finished_at)}</span>}
            {detail.instance.business_key && <span>业务键 {detail.instance.business_key}</span>}
          </div>
        )}
        {error && <div className="field-error">{error}</div>}
        {!error && !detail && <div className="empty-sub">加载中…</div>}

        {detail && (
          <>
            <div className="inst-tokens">
              {detail.tokens.map((t) => (
                <span key={t.id} className={`chip token-chip ${t.state}`} title={`#${t.token_no} 由 ${t.source_activity_code ?? 'start'}`}>
                  {t.activity_code} · {t.state}
                </span>
              ))}
              {detail.tokens.length === 0 && <span className="empty-sub">无令牌</span>}
            </div>

            <div className="dialog-tabs">
              {TABS.map(([v, label]) => (
                <button key={v} className={`tab-btn ${tab === v ? 'active' : ''}`} onClick={() => setTab(v)}>
                  {label}
                </button>
              ))}
            </div>

            {tab === 'timeline' && (
              <ul className="inst-timeline">
                {(timeline ?? []).map((entry, i) => (
                  <li key={i} className={`tl-entry tl-${entry.kind}`}>
                    <span className="tl-time">{fmt(entry.time)}</span>
                    <span className="tl-kind">{entry.kind === 'token' ? '令牌' : entry.kind === 'task' ? '办理' : '事件'}</span>
                    <span className="tl-body">
                      {entry.title}
                      {entry.detail && <span className="tl-detail">{entry.detail}</span>}
                    </span>
                  </li>
                ))}
                {(timeline ?? []).length === 0 && <li className="empty-sub">无轨迹</li>}
              </ul>
            )}

            {tab === 'routes' && (
              <ul className="inst-timeline">
                {(decisions ?? []).map((d) => (
                  <li key={d.id} className="tl-entry tl-route">
                    <span className="tl-time">{fmt(d.created_at)}</span>
                    <span className="tl-kind">选路</span>
                    <span className="tl-body">
                      <b>{d.source_activity}</b>
                      {d.edges.map((e, i) => (
                        <span key={i} className="tl-edge">
                          {e.formula ?? '(无条件)'} → {String(e.result)}
                        </span>
                      ))}
                      <span className="tl-detail">选中:{d.selected.join('、') || '(无)'}</span>
                    </span>
                  </li>
                ))}
                {(decisions ?? []).length === 0 && <li className="empty-sub">无路由记录(实例尚未选路)</li>}
              </ul>
            )}

            {tab === 'variables' && (
              <ul className="inst-timeline">
                {(varHistory ?? []).map((v, i) => (
                  <li key={i} className="tl-entry tl-var">
                    <span className="tl-time">{fmt(v.created_at)}</span>
                    <span className="tl-kind">变量</span>
                    <span className="tl-body">
                      <b>{v.name}</b>
                      <span className="tl-edge">
                        {JSON.stringify(v.old_value) ?? '∅'} → {JSON.stringify(v.new_value)}
                      </span>
                      <span className="tl-detail">
                        写方 {v.source}
                        {v.acted_by ? ` · ${v.acted_by}` : ''}
                      </span>
                    </span>
                  </li>
                ))}
                {(varHistory ?? []).length === 0 && <li className="empty-sub">无变量变更</li>}
              </ul>
            )}
          </>
        )}

        <div className="modal-actions">
          <button className="btn" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}
