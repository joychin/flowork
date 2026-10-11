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
  // 完整模拟(本地状态,不入全局——报告只属于本次试跑)
  const [sim, setSim] = useState<SimulationReport | null>(null)
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
    setRunning(true)
    setSim(null)
    try {
      // 主场景=当前草稿(含未保存编辑):证据先于发布,无需先保存
      const report = await api.simulate(defInfo.code, spec, variables)
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
            引擎真跑整图:user_task 自动通过、service_task 以桩替代、等待节点冻结为卡点 — <b>零副作用</b>(不发事件、
            不产生待办、不进实例列表)。条件按样例值<b>真求值</b>,当前草稿即跑,<b>无需先发布</b>。
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

/** 完整模拟报告:终态结论 / 节点时间线 / 桩清单 / 卡点 / 路由轨迹 */
function SimulationView({ report, names }: { report: SimulationReport; names: Record<string, string> }) {
  const label = (code: string) => names[code] ?? code
  // selected 存的是连线 id("source->target"):展示按目标节点名读
  const edgeLabel = (flow: string) => label(flow.split('->').pop() ?? flow)
  const stalled = report.final_state === 'running'
  return (
    <div className="dryrun-result">
      <div className={`dryrun-verdict ${report.final_state === 'failed' ? 'bad' : stalled ? 'warn' : 'ok'}`}>
        {STATE_LABELS[report.final_state] ??
          (stalled ? `⏸ 卡在「${label(report.stalled_at ?? '')}」— 等待类节点是发现,不是错误` : `终态:${report.final_state}`)}
      </div>

      <div className="sim-section-title">节点时间线({report.visited.length} 站)</div>
      <ol className="dryrun-steps">
        {report.visited.map((code, i) => (
          <li key={`${code}-${i}`}>
            <span className="dryrun-node">{label(code)}</span>
            {report.stubbed.includes(code) && <span className="sim-stub">桩(handler 未执行)</span>}
          </li>
        ))}
      </ol>

      {report.stubbed.length > 0 && (
        <div className="dryrun-warning">
          ⚠ 服务任务以桩替代:{report.stubbed.map(label).join('、')} — 真实运行会执行注册的 handler
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

      <div className="dryrun-hint">
        模拟实例已隔离留存({report.instance_id.slice(0, 8)}…),不出现在实例列表;发布后可用同参数对照线上行为。
      </div>
    </div>
  )
}
