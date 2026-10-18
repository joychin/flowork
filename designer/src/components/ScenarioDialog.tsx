/** 场景面板:可复跑脚本(步骤+断言)的列表/编辑/运行/红绿 —— CI 与设计器同一份文件。
 *
 * 脚本格式与演练剧本同构(steps=任务/等待/推钟三类步;assertions=业务效果断言);
 * 运行跑 simulated 实例(草稿可跑、零副作用),报告含断言明细与覆盖照明。
 */

import { useEffect, useState } from 'react'
import { api, type ScenarioCompare, type ScenarioReport, type ScenarioSummary } from '../api'
import type { Spec } from '../types'
import { designerConfig } from '../config'
import { useStore } from '../store'

const TEMPLATE = `{
  "name": "新场景",
  "start_user": "u_zhang",
  "variables": {"amount": 100},
  "steps": [
    {"node": "submit", "by": "u_zhang", "action": "complete"},
    {"node": "manager", "by": "u_manager", "action": "complete"}
  ],
  "assertions": [
    {"type": "final_state", "equals": "completed"},
    {"type": "pending", "empty": true}
  ]
}`

type Props = { spec: Spec; code: string; onClose: () => void }

export function ScenarioDialog({ spec, code, onClose }: Props) {
  const showToast = useStore((s) => s.showToast)
  const [rows, setRows] = useState<ScenarioSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [scriptText, setScriptText] = useState(TEMPLATE)
  const [report, setReport] = useState<ScenarioReport | null>(null)
  const [compare, setCompare] = useState<ScenarioCompare | null>(null)
  const [busy, setBusy] = useState(false)
  const nodeNames: Record<string, string> = {}
  for (const n of spec.nodes) nodeNames[n.code] = n.name || n.code
  const label = (c: string) => nodeNames[c] ?? c
  // 只读嵌入：场景行是持久化写面——隐藏新建/保存/删除，保留运行（零副作用）
  const readonly = designerConfig.readonly()

  const refresh = async () => {
    try {
      setRows(await api.listScenarios(code))
    } catch (err) {
      showToast(`场景列表加载失败:${err instanceof Error ? err.message : String(err)}`)
    }
  }

  useEffect(() => {
    void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code])

  const openRow = async (row: ScenarioSummary) => {
    try {
      const detail = await api.getScenario(row.id)
      setSelected(row.id)
      setName(detail.name)
      setScriptText(JSON.stringify(detail.script, null, 2))
      setReport(null)
      setCompare(null)
    } catch (err) {
      showToast(`场景读取失败:${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const save = async () => {
    let script: Record<string, unknown>
    try {
      script = JSON.parse(scriptText)
    } catch (err) {
      showToast(`脚本 JSON 无效:${err instanceof Error ? err.message : String(err)}`)
      return
    }
    setBusy(true)
    try {
      const saved = await api.saveScenario(code, {
        name: name || String(script.name || '未命名场景'),
        script,
        id: selected ?? undefined,
      })
      setSelected(saved.id)
      await refresh()
      showToast('场景已保存')
    } catch (err) {
      showToast(`保存失败:${err instanceof Error ? err.message : String(err)}`)
    }
    setBusy(false)
  }

  const run = async () => {
    if (!selected) {
      showToast('先保存场景再运行（左侧选一个已保存的场景）')
      return
    }
    setBusy(true)
    setReport(null)
    try {
      // 主场景=当前草稿(含未保存编辑):与 simulate 同口径
      setReport(await api.runScenario(selected, spec))
      setCompare(null)
      await refresh()
    } catch (err) {
      showToast(`场景运行失败:${err instanceof Error ? err.message : String(err)}`)
    }
    setBusy(false)
  }

  const runCompare = async () => {
    if (!selected) {
      showToast('先保存场景再对比(对比跑同一份脚本：草稿 vs 最新发布版)')
      return
    }
    setBusy(true)
    setCompare(null)
    try {
      setCompare(await api.compareScenario(selected, spec))
    } catch (err) {
      showToast(`对比失败:${err instanceof Error ? err.message : String(err)}`)
    }
    setBusy(false)
  }

  const remove = async (row: ScenarioSummary) => {
    if (!window.confirm(`删除场景「${row.name}」？`)) return
    try {
      await api.deleteScenario(row.id)
      if (selected === row.id) setSelected(null)
      await refresh()
    } catch (err) {
      showToast(`删除失败:${err instanceof Error ? err.message : String(err)}`)
    }
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal dryrun-modal scenario-modal" onClick={(e) => e.stopPropagation()}>
        <h3>🧪 场景回归（可复跑脚本 · 与 CI 同一份文件）</h3>
        <p className="modal-sub">
          场景＝步骤 + 断言。运行在<b>模拟实例</b>上（当前草稿即跑、零副作用），
          断言失败即红；同一份 JSON 可在 CI 里用 <code>flowork scenario run</code> 执行。
        </p>

        <div className="scenario-layout">
          <div className="scenario-list">
            {!readonly && (
              <button className="btn" disabled={busy} onClick={() => { setSelected(null); setName(''); setScriptText(TEMPLATE); setReport(null); setCompare(null) }}>
                ＋ 新建场景
              </button>
            )}
            {rows.map((row) => (
              <div
                key={row.id}
                className={`scenario-row ${selected === row.id ? 'active' : ''}`}
                onClick={() => void openRow(row)}
              >
                <div className="scenario-row-head">
                  <span className={`scenario-dot ${row.last_ok === true ? 'ok' : row.last_ok === false ? 'bad' : ''}`}>
                    {row.last_ok === true ? '✓' : row.last_ok === false ? '✕' : '·'}
                  </span>
                  <b>{row.name || '(未命名)'}</b>
                </div>
                <div className="dryrun-note">
                  {row.step_count} 步 · {row.assertion_count} 断言
                  {row.last_run_at ? ` · 上次 ${row.last_run_at}` : ' · 未运行'}
                </div>
                {row.last_summary && <div className="dryrun-note">{row.last_summary}</div>}
                {!readonly && (
                  <button
                    className="icon-btn scenario-del"
                    title="删除"
                    onClick={(e) => { e.stopPropagation(); void remove(row) }}
                  >
                    ✕
                  </button>
                )}
              </div>
            ))}
            {rows.length === 0 && <div className="dryrun-hint">还没有场景 — 点上面的「新建场景」</div>}
          </div>

          <div className="scenario-editor">
            <div className="var-row">
              <span className="dryrun-node">名称</span>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="场景名" />
            </div>
            <textarea
              className="json-area"
              rows={14}
              spellCheck={false}
              value={scriptText}
              onChange={(e) => setScriptText(e.target.value)}
            />
            <div className="modal-actions">
              {!readonly && (
              <button className="btn" disabled={busy} onClick={() => void save()}>保存</button>
            )}
              <button className="btn btn-primary" disabled={busy || !selected} onClick={() => void run()}>
                {busy ? '运行中…' : '运行(草稿)'}
              </button>
              <button className="btn" disabled={busy || !selected} onClick={() => void runCompare()}>
                对比线上版
              </button>
            </div>

            {report && (
              <div className="dryrun-result">
                <div className={`dryrun-verdict ${report.ok ? 'ok' : 'bad'}`}>
                  {report.ok
                    ? `✓ 全绿（${report.assertions.length} 条断言，终态 ${report.final_state}）`
                    : `✕ ${report.assertions.filter((a) => !a.ok).length}/${report.assertions.length} 条断言失败（终态 ${report.final_state}）`}
                </div>
                <table className="scenario-table">
                  <thead>
                    <tr><th>结果</th><th>类型</th><th>期望</th><th>实际</th></tr>
                  </thead>
                  <tbody>
                    {report.assertions.map((a, i) => (
                      <tr key={i} className={a.ok ? '' : 'bad'}>
                        <td>{a.ok ? '✓' : '✕'}</td>
                        <td>{a.type}</td>
                        <td><code>{JSON.stringify(a.expected)}</code></td>
                        <td><code>{JSON.stringify(a.actual)}</code></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {report.coverage.nodes && (
                  <div className="dryrun-hint">
                    覆盖照明：节点 {report.coverage.nodes.covered.length}/{report.coverage.nodes.total}
                    {report.coverage.nodes.missing.length > 0 &&
                      `（未覆盖 ${report.coverage.nodes.missing.map(label).join('、')}）`}
                  </div>
                )}
                {report.notes.map((n, i) => (
                  <div key={i} className="dryrun-warning">⚠ {n}</div>
                ))}
              </div>
            )}

            {compare && (
              <div className="dryrun-result">
                <div className="sim-section-title">
                  两版对比（同一脚本 · 草稿 vs {compare.published_version ?? '无已发布版'}）
                </div>
                <table className="scenario-table">
                  <thead>
                    <tr><th>版本</th><th>结论</th><th>失败断言</th><th>终态</th></tr>
                  </thead>
                  <tbody>
                    <tr className={compare.draft?.ok ? '' : 'bad'}>
                      <td>草稿</td>
                      <td>{compare.draft ? (compare.draft.ok ? '✓ 全绿' : '✕ 有失败') : '（无草稿）'}</td>
                      <td>{compare.draft ? compare.draft.assertions.filter((a) => !a.ok).length : '-'}</td>
                      <td>{compare.draft?.final_state ?? '-'}</td>
                    </tr>
                    <tr className={compare.published?.ok ? '' : 'bad'}>
                      <td>{compare.published_version ?? '线上'}</td>
                      <td>{compare.published ? (compare.published.ok ? '✓ 全绿' : '✕ 有失败') : '（无已发布版）'}</td>
                      <td>{compare.published ? compare.published.assertions.filter((a) => !a.ok).length : '-'}</td>
                      <td>{compare.published?.final_state ?? '-'}</td>
                    </tr>
                  </tbody>
                </table>
                {compare.draft && compare.published && compare.draft.ok !== compare.published.ok && (
                  <div className="dryrun-warning">
                    ⚠ 两版结论不一致 — 本次改动改变了脚本行为（红=会失败的那一版）
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="modal-actions">
          <button className="btn" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}
