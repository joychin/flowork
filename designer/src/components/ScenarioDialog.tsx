/** 场景回归：只读运行按钮（脚本的编辑主场在 IDE 与 git）。
 *
 * 场景＝可复跑的审阅资产，存在定义同目录的 <code>.scenario.json</code> —
 * 与 CLI `flowork scenario run` 读同一份。设计器只负责「跑一下看红绿」：
 * 编辑、断言、参数化都在代码/文件里做，那里有 git blame、IDE 跳转和 pytest 的
 * 表达力（令牌/事件/路由边全都能断言，场景 DSL 只能断言五种）。
 */

import { useEffect, useState } from 'react'
import { api, type ScenarioReport } from '../api'
import type { Spec } from '../types'
import { useStore } from '../store'

type Props = { spec: Spec; code: string; onClose: () => void }

export function ScenarioDialog({ spec, code, onClose }: Props) {
  const showToast = useStore((s) => s.showToast)
  const runtime = useStore((s) => s.runtime)
  const [fileName, setFileName] = useState<string | null>(null)
  const [script, setScript] = useState<Record<string, unknown> | null>(null)
  const [report, setReport] = useState<ScenarioReport | null>(null)
  const [busy, setBusy] = useState(false)
  const nodeNames: Record<string, string> = {}
  for (const n of spec.nodes) nodeNames[n.code] = n.name || n.code
  const label = (c: string) => nodeNames[c] ?? c
  const canRead = runtime.source === 'files' && !!runtime.definitions_dir

  useEffect(() => {
    let alive = true
    void (async () => {
      if (!canRead) return
      try {
        const loaded = await api.getScenario(code)
        if (!alive) return
        setFileName(loaded.file)
        setScript(loaded.script)
      } catch {
        if (!alive) return
        setFileName(null)
        setScript(null)
      }
    })()
    return () => {
      alive = false
    }
  }, [code, canRead])

  const run = async () => {
    if (!script) return
    setBusy(true)
    setReport(null)
    try {
      // 当前草稿 spec 直传：跑的是正在编辑的图，不是磁盘上的旧版本
      setReport(await api.runScenario(code, script, spec))
    } catch (err) {
      showToast(`场景运行失败：${err instanceof Error ? err.message : String(err)}`)
    }
    setBusy(false)
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal dryrun-modal scenario-modal" onClick={(e) => e.stopPropagation()}>
        <h3>🧪 场景回归</h3>
        <p className="modal-sub">
          场景脚本是<b>定义同目录的 <code>{code}.scenario.json</code></b>——
          与 CLI <code>flowork scenario run</code> 读同一份，进 git、可在 CI 里跑。
          脚本的编辑在 IDE 里做；这里只负责跑一下看红绿。
        </p>

        <div className="var-row">
          <span className="dryrun-node">文件</span>
          <input
            value={fileName ?? (canRead ? '（当前定义没有场景文件）' : '（无定义目录）')}
            readOnly
            title="场景文件固定在定义目录下，文件名由定义 code 决定"
          />
        </div>

        {!canRead && (
          <div className="dryrun-warning">
            ⚠ 当前没有定义目录（纯内存会话）——启动时给
            <code> FLOWORK_DEFINITIONS_DIR </code> 才能读到场景文件。
          </div>
        )}
        {canRead && !fileName && (
          <div className="dryrun-hint">
            在定义目录下新建 <code>{code}.scenario.json</code>（格式见
            <code> examples/04_expense_scenario.json</code>），刷新本面板即可运行。
          </div>
        )}

        <div className="modal-actions">
          <button className="btn" onClick={onClose}>关闭</button>
          <button className="btn btn-primary" disabled={busy || !script} onClick={() => void run()}>
            {busy ? '运行中…' : '运行场景'}
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
      </div>
    </div>
  )
}
