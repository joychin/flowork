/** 试运行对话框:双 Tab——路由预演(静态模拟条件路由) / 完整模拟(引擎真跑,草稿主场景) */

import { useEffect, useState } from 'react'
import { api, sampleVariables } from '../api'
import type { SimulationReport } from '../types'
import { useStore } from '../store'

const WAIT_ASSUMPTIONS: [string, string][] = [
  ['auto', '自动(消息到达与超时两面都试)'],
  ['matched', '假设消息到达'],
  ['elapsed', '假设超时'],
]

const SIM_WAIT_OPTIONS: [string, string][] = [
  ['none', '不设(冻结为卡点)'],
  ['matched', '假设消息到达'],
  ['elapsed', '假设超时'],
]

const ACTION_WORDS = [
  'complete', 'reject', 'transfer', 'assist', 'consult', 'claim', 'unclaim',
  'add_countersigner', 'complete_assist', 'complete_consult',
]

const DECISIONS_PLACEHOLDER = `[
  {"node": "manager", "by": "u_manager", "action": "reject", "comment": "退回补充"},
  {"node": "submit", "by": "u_zhang", "action": "complete", "variables": {"amount": 18000}}
]`

const STUBS_PLACEHOLDER = `{"score": {"score_val": 99}}`

const OUTCOME_BADGES: Record<string, string> = {
  agree: '✓', reject: '✕', transfer: '⇄', auto_pass: '↷', withdrawn: '↩', assist_cancel: '−',
}

/** 报告扩展字段(决策/待决策/桩应用/注记);主体 SimulationReport 在 types.ts */
type SimReport = SimulationReport & {
  notes?: string[]
  decisions?: { node: string; assignee: string; acted_by: string; outcome: string; comment: string }[]
  pending?: { task_id: string; node: string; assignee: string; task_type: string }[]
  stubs_applied?: string[]
}

const STATE_LABELS: Record<string, string> = {
  completed: '✓ 全图跑通,正常完结',
  failed: '✕ 技术故障中断 — 真实运行到此会挂起并出故障单',
}

export function DryRunDialog() {
  const open = useStore((s) => s.dryRunOpen)
  const result = useStore((s) => s.dryRun)
  const close = useStore((s) => s.closeDryRun)
  const run = useStore((s) => s.runDryRun)
  const spec = useStore((s) => s.spec)
  const defInfo = useStore((s) => s.defInfo)
  const showToast = useStore((s) => s.showToast)
  const [tab, setTab] = useState<'route' | 'sim'>('route')
  const [varsText, setVarsText] = useState('{}')
  const [running, setRunning] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)
  // 等待节点预演假设(逐节点;缺省 auto=两面都试,reason 会标注假设)
  const [assumptions, setAssumptions] = useState<Record<string, string>>({})
  // 完整模拟控制面:等待假设(逐节点 none/matched/elapsed) + 决策清单/桩(JSON)
  const [simWaits, setSimWaits] = useState<Record<string, string>>({})
  const [decisionsText, setDecisionsText] = useState('')
  const [stubsText, setStubsText] = useState('')
  // 完整模拟(本地状态,不入全局——报告只属于本次试跑)
  const [sim, setSim] = useState<SimReport | null>(null)
  const waitNodes = (spec?.nodes ?? []).filter((n) => n.kind === 'wait')

  // 打开时用声明变量的样例值预填
  useEffect(() => {
    if (open && spec) {
      setVarsText(JSON.stringify(sampleVariables(spec.variables), null, 2))
      setParseError(null)
    }
  }, [open, spec])

  if (!open || !spec || !defInfo) return null

  const declared = spec.variables ?? []
  const parseVars = (): Record<string, unknown> | null => {
    try {
      const variables = JSON.parse(varsText || '{}')
      if (variables === null || typeof variables !== 'object' || Array.isArray(variables)) {
        throw new Error('变量必须是 JSON 对象')
      }
      setParseError(null)
      return variables
    } catch (err) {
      setParseError(err instanceof Error ? err.message : String(err))
      return null
    }
  }

  const nodeNames: Record<string, string> = {}
  for (const n of spec.nodes) nodeNames[n.code] = n.name || n.code

  const runRoute = async () => {
    const variables = parseVars()
    if (!variables) return
    setRunning(true)
    await run(variables, assumptions)
    setRunning(false)
  }

  const runSim = async () => {
    const variables = parseVars()
    if (!variables) return
    // 控制面三件(全可选):决策清单/桩=JSON 文本,等待假设=逐节点下拉
    let decisions: unknown
    let stubs: unknown
    try {
      decisions = decisionsText.trim() ? JSON.parse(decisionsText) : undefined
      stubs = stubsText.trim() ? JSON.parse(stubsText) : undefined
    } catch (err) {
      showToast(`控制面 JSON 无效:${err instanceof Error ? err.message : String(err)}`)
      return
    }
    const waits: Record<string, string> = {}
    for (const [node, value] of Object.entries(simWaits)) {
      if (value === 'matched' || value === 'elapsed') waits[node] = value
    }
    const hasControls = decisions !== undefined || stubs !== undefined || Object.keys(waits).length > 0
    const controls = hasControls
      ? { ...(decisions !== undefined ? { decisions } : {}), ...(stubs !== undefined ? { stubs } : {}), ...(Object.keys(waits).length > 0 ? { waits } : {}) }
      : undefined
    setRunning(true)
    setSim(null)
    try {
      // 主场景=当前草稿(含未保存编辑):证据先于发布,无需先保存
      const report = await api.simulate(defInfo.code, spec, variables, controls)
      setSim(report)
    } catch (err) {
      showToast(`模拟失败:${err instanceof Error ? err.message : String(err)}`)
    }
    setRunning(false)
  }

  return (
    <div className="modal-mask" onClick={close}>
      <div className="modal dryrun-modal" onClick={(e) => e.stopPropagation()}>
        <h3>▶ 试运行</h3>
        <div className="dialog-tabs">
          <button className={`tab-btn ${tab === 'route' ? 'active' : ''}`} onClick={() => setTab('route')}>
            路由预演(静态)
          </button>
          <button className={`tab-btn ${tab === 'sim' ? 'active' : ''}`} onClick={() => setTab('sim')}>
            完整模拟(引擎真跑)
          </button>
        </div>

        {tab === 'route' && (
          <p className="modal-sub">
            静态模拟条件路由:<b>不执行</b>节点动作、不产生待办。样例值来自「流程设置」的变量声明,可临时修改;
            修改配置后预演结果自动失效。
          </p>
        )}
        {tab === 'sim' && (
          <p className="modal-sub">
            引擎真跑整图:待办由<b>机器人全通过</b>(或按下方决策清单扮演办理人)、service_task 以桩替代、
            等待节点可按假设兑现 — <b>零副作用</b>(不发事件、不进实例列表)。条件按样例值<b>真求值</b>,
            当前草稿即跑,<b>无需先发布</b>。
          </p>
        )}

        {declared.length > 0 && (
          <div className="dryrun-vars-hint">
            已声明变量:{declared.map((v) => `${v.name}(${v.label || v.type})`).join(' · ')}
          </div>
        )}
        {declared.length === 0 && (
          <div className="dryrun-vars-hint warn">
            未在「流程设置」声明变量 — 条件将按样例值缺失处理(不满足)。仍可在下方手填变量试算。
          </div>
        )}

        <textarea
          className="json-area dryrun-vars"
          rows={Math.min(10, 3 + varsText.split('\n').length)}
          spellCheck={false}
          value={varsText}
          onChange={(e) => setVarsText(e.target.value)}
        />
        {parseError && <div className="field-error">{parseError}</div>}

        {tab === 'route' && waitNodes.length > 0 && (
          <div className="dryrun-assumptions">
            <div className="dryrun-assumptions-title">等待节点假设(预演不是运行:先到者决定走向,这里把两面都画出来)</div>
            {waitNodes.map((n) => (
              <div key={n.code} className="var-row">
                <span className="dryrun-node">{n.name || n.code}</span>
                <select
                  value={assumptions[n.code] ?? 'auto'}
                  onChange={(e) => setAssumptions((prev) => ({ ...prev, [n.code]: e.target.value }))}
                >
                  {WAIT_ASSUMPTIONS.map(([v, label]) => (
                    <option key={v} value={v}>{label}</option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        )}

        {tab === 'sim' && (
          <div className="dryrun-assumptions">
            <div className="dryrun-assumptions-title">模拟控制(可选):给了决策清单即禁用机器人,未覆盖的待办停在「待决策点」</div>
            {waitNodes.length > 0 && (
              <div className="dryrun-assumptions-title" style={{ fontWeight: 400 }}>
                等待节点假设(不设=冻结为卡点)
              </div>
            )}
            {waitNodes.map((n) => (
              <div key={n.code} className="var-row">
                <span className="dryrun-node">{n.name || n.code}</span>
                <select
                  value={simWaits[n.code] ?? 'none'}
                  onChange={(e) => setSimWaits((prev) => ({ ...prev, [n.code]: e.target.value }))}
                >
                  {SIM_WAIT_OPTIONS.map(([v, label]) => (
                    <option key={v} value={v}>{label}</option>
                  ))}
                </select>
              </div>
            ))}
            <div className="var-row" style={{ alignItems: 'flex-start', flexDirection: 'column', gap: 4 }}>
              <span className="dryrun-node">决策清单(JSON,按序扮演;动作词表:{ACTION_WORDS.join('/')} )</span>
              <textarea
                className="json-area"
                rows={4}
                spellCheck={false}
                placeholder={DECISIONS_PLACEHOLDER}
                value={decisionsText}
                onChange={(e) => setDecisionsText(e.target.value)}
              />
            </div>
            <div className="var-row" style={{ alignItems: 'flex-start', flexDirection: 'column', gap: 4 }}>
              <span className="dryrun-node">桩返回值(JSON:{"{节点: 变量对象}"},注入后下游条件真求值)</span>
              <textarea
                className="json-area"
                rows={3}
                spellCheck={false}
                placeholder={STUBS_PLACEHOLDER}
                value={stubsText}
                onChange={(e) => setStubsText(e.target.value)}
              />
            </div>
          </div>
        )}

        <div className="modal-actions">
          <button className="btn" onClick={close}>关闭</button>
          {tab === 'route' && (
            <button className="btn btn-primary" disabled={running || !!parseError} onClick={() => void runRoute()}>
              {running ? '预演中…' : '开始预演'}
            </button>
          )}
          {tab === 'sim' && (
            <button className="btn btn-primary" disabled={running || !!parseError} onClick={() => void runSim()}>
              {running ? '模拟中…' : '开始模拟'}
            </button>
          )}
        </div>

        {tab === 'route' && result && (
          <div className="dryrun-result">
            <div className={`dryrun-verdict ${result.ok ? 'ok' : 'bad'}`}>
              {result.ok ? `✓ 路由可达 — 路径经过 ${result.steps.length} 个节点` : '✕ 存在无法路由的位置'}
            </div>
            <ol className="dryrun-steps">
              {result.steps.map((step, i) => (
                <li key={i}>
                  <span className="dryrun-node">{nodeNames[step.node] ?? step.node}</span>
                  {step.via_reason && <span className="dryrun-reason">{step.via_reason}</span>}
                  {step.note && <span className="dryrun-note">{step.note}</span>}
                </li>
              ))}
            </ol>
            {result.warnings.map((w, i) => (
              <div key={`w${i}`} className="dryrun-warning">⚠ {w}</div>
            ))}
            {result.blocked.map((b, i) => (
              <div key={`b${i}`} className="dryrun-blocked">✕ {b}</div>
            ))}
            <div className="dryrun-hint">路径已用绿色高亮在画布上标出;修改任意配置后高亮自动清除。</div>
          </div>
        )}

        {tab === 'sim' && sim && <SimulationView report={sim} names={nodeNames} />}
      </div>
    </div>
  )
}

/** 完整模拟报告:终态结论 / 节点时间线 / 办理记录 / 桩清单 / 待决策点 / 路由轨迹 */
function SimulationView({ report, names }: { report: SimReport; names: Record<string, string> }) {
  const label = (code: string) => names[code] ?? code
  // selected 存的是连线 id("source->target"):展示按目标节点名读
  const edgeLabel = (flow: string) => label(flow.split('->').pop() ?? flow)
  const stalled = report.final_state === 'running'
  const decisions = report.decisions ?? []
  const pending = report.pending ?? []
  const notes = report.notes ?? []
  const stubsApplied = report.stubs_applied ?? []
  return (
    <div className="dryrun-result">
      <div className={`dryrun-verdict ${report.final_state === 'failed' ? 'bad' : stalled ? 'warn' : 'ok'}`}>
        {STATE_LABELS[report.final_state] ??
          (pending.length > 0
            ? `⏸ 待决策 — 停在 ${pending.map((p) => label(p.node)).join('、')}(决策清单未覆盖,机器人已禁用)`
            : stalled
              ? `⏸ 卡在「${label(report.stalled_at ?? '')}」— 等待类节点是发现,不是错误`
              : `终态:${report.final_state}`)}
      </div>

      <div className="sim-section-title">节点时间线({report.visited.length} 站)</div>
      <ol className="dryrun-steps">
        {report.visited.map((code, i) => (
          <li key={`${code}-${i}`}>
            <span className="dryrun-node">{label(code)}</span>
            {stubsApplied.includes(code) && <span className="sim-stub">桩(注入了给定返回值)</span>}
            {!stubsApplied.includes(code) && report.stubbed.includes(code) && (
              <span className="sim-stub">桩(handler 未执行)</span>
            )}
          </li>
        ))}
      </ol>

      {decisions.length > 0 && (
        <>
          <div className="sim-section-title">办理记录({decisions.length} 步)</div>
          <ol className="dryrun-steps">
            {decisions.map((d, i) => (
              <li key={i}>
                <span className="dryrun-node">{label(d.node)}</span>
                <span className="sim-selected">
                  {OUTCOME_BADGES[d.outcome] ?? d.outcome} {d.outcome} · {d.acted_by}
                </span>
                {d.comment && <span className="dryrun-note">「{d.comment}」</span>}
              </li>
            ))}
          </ol>
        </>
      )}

      {report.stubbed.length > 0 && (
        <div className="dryrun-warning">
          ⚠ 服务任务以桩替代:{report.stubbed.map(label).join('、')}
          {stubsApplied.length > 0 && `(其中 ${stubsApplied.map(label).join('、')} 注入了给定返回值)`} — 真实运行会执行注册的 handler
        </div>
      )}

      {report.route_decisions.length > 0 && (
        <>
          <div className="sim-section-title">路由轨迹({report.route_decisions.length} 次选路)</div>
          <ul className="sim-routes">
            {report.route_decisions.map((d, i) => (
              <li key={i}>
                <span className="dryrun-node">{label(d.node)}</span>
                <span className="sim-selected">→ {d.selected.map(edgeLabel).join('、') || '(无出边)'}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {notes.map((n, i) => (
        <div key={`n${i}`} className="dryrun-warning">⚠ {n}</div>
      ))}

      <div className="dryrun-hint">
        模拟实例已隔离留存({report.instance_id.slice(0, 8)}…),不出现在实例列表;发布后可用同参数对照线上行为。
      </div>
    </div>
  )
}
