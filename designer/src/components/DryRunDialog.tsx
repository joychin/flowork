/** 试运行对话框:按流程变量样例值预演路由(静态模拟,不执行节点行为),画布高亮路径 */

import { useEffect, useState } from 'react'
import { sampleVariables } from '../api'
import { useStore } from '../store'

export function DryRunDialog() {
  const open = useStore((s) => s.dryRunOpen)
  const result = useStore((s) => s.dryRun)
  const close = useStore((s) => s.closeDryRun)
  const run = useStore((s) => s.runDryRun)
  const spec = useStore((s) => s.spec)
  const [varsText, setVarsText] = useState('{}')
  const [running, setRunning] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)

  // 打开时用声明变量的样例值预填
  useEffect(() => {
    if (open && spec) {
      setVarsText(JSON.stringify(sampleVariables(spec.variables), null, 2))
      setParseError(null)
    }
  }, [open, spec])

  if (!open || !spec) return null

  const declared = spec.variables ?? []
  const runNow = async () => {
    let variables: Record<string, unknown>
    try {
      variables = JSON.parse(varsText || '{}')
      if (variables === null || typeof variables !== 'object' || Array.isArray(variables)) {
        throw new Error('变量必须是 JSON 对象')
      }
      setParseError(null)
    } catch (err) {
      setParseError(err instanceof Error ? err.message : String(err))
      return
    }
    setRunning(true)
    await run(variables)
    setRunning(false)
  }

  const nodeNames: Record<string, string> = {}
  for (const n of spec.nodes) nodeNames[n.code] = n.name || n.code

  return (
    <div className="modal-mask" onClick={close}>
      <div className="modal dryrun-modal" onClick={(e) => e.stopPropagation()}>
        <h3>▶ 试运行 · 路由预演</h3>
        <p className="modal-sub">
          静态模拟条件路由:<b>不执行</b>节点动作、不产生待办。样例值来自「流程设置」的变量声明,可临时修改;
          修改配置后预演结果自动失效。
        </p>

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

        <div className="modal-actions">
          <button className="btn" onClick={close}>关闭</button>
          <button className="btn btn-primary" disabled={running || !!parseError} onClick={() => void runNow()}>
            {running ? '预演中…' : '开始预演'}
          </button>
        </div>

        {result && (
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
      </div>
    </div>
  )
}
