/** 排练对话框:人在环逐步驱动 simulated 实例 —— 每步一张步单(待办/已办/路由/变量/等待)。
 *
 * 与「试运行」的分工：试运行=机器人一次跑完(冒烟)；排练=人扮演办理人逐步走
 * (看合不合理)。两者同一基座(simulated 实例+真实任务行+真实动作面)。
 */

import { useEffect, useState } from 'react'
import { api, sampleVariables, type RehearsalState, type RehearsalTask } from '../api'
import type { Spec } from '../types'
import { useStore } from '../store'

const ACTION_LABELS: Record<string, string> = {
  complete: '同意',
  reject: '驳回',
  transfer: '转办',
  assist: '协办',
  consult: '咨询',
  claim: '认领',
  unclaim: '取消认领',
  add_countersigner: '加签',
  complete_assist: '办协办',
  complete_consult: '交意见',
}

type Props = { spec: Spec; code: string; onClose: () => void }

export function RehearsalDialog({ spec, code, onClose }: Props) {
  const showToast = useStore((s) => s.showToast)
  const [startUser, setStartUser] = useState('u_zhang')
  const [varsText, setVarsText] = useState('{}')
  const [state, setState] = useState<RehearsalState | null>(null)
  const [busy, setBusy] = useState(false)
  const [comment, setComment] = useState('')
  const [rejectTarget, setRejectTarget] = useState('')
  const nodeNames: Record<string, string> = {}
  for (const n of spec.nodes) nodeNames[n.code] = n.name || n.code

  useEffect(() => {
    setVarsText(JSON.stringify(sampleVariables(spec.variables), null, 2))
  }, [spec])

  const start = async () => {
    let variables: Record<string, unknown>
    try {
      variables = JSON.parse(varsText || '{}')
    } catch (err) {
      showToast(`变量 JSON 无效:${err instanceof Error ? err.message : String(err)}`)
      return
    }
    setBusy(true)
    try {
      setState(await api.startRehearsal(code, spec, variables, startUser))
    } catch (err) {
      showToast(`排练启动失败:${err instanceof Error ? err.message : String(err)}`)
    }
    setBusy(false)
  }

  const act = async (task: RehearsalTask, action: string) => {
    if (!state) return
    const step: Record<string, unknown> = { task_id: task.id, action, comment: comment || undefined }
    if (action === 'complete' || action === 'reject') {
      const parsed = variablesOf(comment)
      if (parsed) step.variables = parsed
    }
    if (action === 'reject' && rejectTarget) step.target = rejectTarget
    if (action === 'transfer' || action === 'assist' || action === 'consult' || action === 'add_countersigner') {
      const to = window.prompt(`${ACTION_LABELS[action]}给谁(逗号分隔多人)`, '')
      if (!to) return
      step.to = to
    }
    setBusy(true)
    try {
      setState(await api.rehearsalStep(state.instance_id, step))
      setComment('')
      setRejectTarget('')
    } catch (err) {
      showToast(`动作被拒绝:${err instanceof Error ? err.message : String(err)}`)
    }
    setBusy(false)
  }

  const resolveWait = async (node: string, result: 'matched' | 'elapsed') => {
    if (!state) return
    setBusy(true)
    try {
      setState(await api.rehearsalStep(state.instance_id, { kind: 'wait', node, result }))
    } catch (err) {
      showToast(`等待兑現失败:${err instanceof Error ? err.message : String(err)}`)
    }
    setBusy(false)
  }

  const label = (code: string) => nodeNames[code] ?? code
  const finished = state && state.instance.state !== 'running'

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal dryrun-modal" onClick={(e) => e.stopPropagation()}>
        <h3>🎭 排练（人扮演办理人，逐步走）</h3>
        {!state && (
          <>
            <p className="modal-sub">
              发起一个<b>排练实例</b>（与真实实例同语义、零副作用：不发事件、不进实例列表），
              然后你以各节点办理人的身份一步步办理——每一步都能看到待办变化、路由选择与变量血缘。
            </p>
            <div className="var-row">
              <span className="dryrun-node">发起人</span>
              <input value={startUser} onChange={(e) => setStartUser(e.target.value)} />
            </div>
            <div className="var-row" style={{ alignItems: 'flex-start', flexDirection: 'column', gap: 4 }}>
              <span className="dryrun-node">起始变量(JSON)</span>
              <textarea
                className="json-area"
                rows={4}
                spellCheck={false}
                value={varsText}
                onChange={(e) => setVarsText(e.target.value)}
              />
            </div>
            <div className="modal-actions">
              <button className="btn" onClick={onClose}>取消</button>
              <button className="btn btn-primary" disabled={busy} onClick={() => void start()}>
                {busy ? '启动中…' : '开始排练'}
              </button>
            </div>
          </>
        )}

        {state && (
          <div className="dryrun-result">
            <div className={`dryrun-verdict ${finished ? 'ok' : 'warn'}`}>
              {finished
                ? `✓ 排练完成（终态 ${state.instance.state}）`
                : `▶ 进行中 — 待办 ${state.tasks.length} 条（${state.tasks.map((t) => label(t.activity_code)).join('、')}）`}
            </div>

            {!finished && (
              <div className="rehearsal-actions">
                {state.tasks.map((task) => (
                  <div key={task.id} className="rehearsal-task">
                    <div className="rehearsal-task-head">
                      <b>{label(task.activity_code)}</b>
                      <span className="dryrun-note">{task.assignee || '（候选池）'}</span>
                      {task.task_type !== 'approval' && (
                        <span className="sim-stub">{task.task_type}</span>
                      )}
                    </div>
                    <input
                      className="rehearsal-comment"
                      placeholder="意见 / 变量 JSON(办理时可用)"
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                    />
                    {task.available_actions.includes('reject') && (
                      <input
                        className="rehearsal-comment"
                        placeholder="驳回落点节点 code(留空走默认)"
                        value={rejectTarget}
                        onChange={(e) => setRejectTarget(e.target.value)}
                      />
                    )}
                    <div className="rehearsal-buttons">
                      {task.available_actions.map((action) => (
                        <button
                          key={action}
                          className={`btn ${action === 'complete' ? 'btn-primary' : ''} ${action === 'reject' ? 'btn-danger' : ''}`}
                          disabled={busy}
                          onClick={() => void act(task, action)}
                        >
                          {ACTION_LABELS[action] ?? action}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {state.waiting.length > 0 && (
              <div className="dryrun-assumptions">
                <div className="dryrun-assumptions-title">等待中的节点（选一个结果继续）</div>
                {state.waiting.map((w) => (
                  <div key={w.node} className="var-row">
                    <span className="dryrun-node">{label(w.node)}</span>
                    <button className="btn" disabled={busy} onClick={() => void resolveWait(w.node, 'matched')}>
                      消息到达
                    </button>
                    <button className="btn" disabled={busy} onClick={() => void resolveWait(w.node, 'elapsed')}>
                      超时
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div className="sim-section-title">步单 · 已办（{state.task_history.length} 步）</div>
            <ol className="dryrun-steps">
              {state.task_history.map((h, i) => (
                <li key={i}>
                  <span className="dryrun-node">{label(h.node)}</span>
                  <span className="sim-selected">{h.outcome} · {h.acted_by}</span>
                  {h.comment && <span className="dryrun-note">「{h.comment}」</span>}
                </li>
              ))}
            </ol>

            <div className="sim-section-title">步单 · 路由（{state.routes.length} 次选路）</div>
            <ul className="sim-routes">
              {state.routes.map((r, i) => (
                <li key={i}>
                  <span className="dryrun-node">{label(r.node)}</span>
                  <span className="sim-selected">→ {r.selected.map((e) => label(String(e).split('->').pop() ?? '')).join('、') || '(无出边)'}</span>
                </li>
              ))}
            </ul>

            {Object.keys(state.variables).length > 0 && (
              <>
                <div className="sim-section-title">步单 · 变量</div>
                <pre className="json-area" style={{ maxHeight: 140, overflow: 'auto' }}>
                  {JSON.stringify(state.variables, null, 2)}
                </pre>
              </>
            )}

            {state.variable_history.length > 0 && (
              <>
                <div className="sim-section-title">步单 · 变量血缘</div>
                <ul className="sim-routes">
                  {state.variable_history.map((v, i) => (
                    <li key={i}>
                      <span className="dryrun-node">{v.name}</span>
                      <span className="sim-selected">
                        {JSON.stringify(v.old)} → {JSON.stringify(v.new)}
                      </span>
                      <span className="dryrun-note">{v.source} · {v.acted_by ?? '-'}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}

            <div className="modal-actions">
              <button className="btn" onClick={onClose}>关闭</button>
              <button className="btn" onClick={() => void start()}>重开一次</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/** 意见框里粘 JSON 对象＝本次办理要写的变量（与演练台同一习惯）。 */
function variablesOf(text: string): Record<string, unknown> | null {
  const trimmed = text.trim()
  if (!trimmed.startsWith('{')) return null
  try {
    const parsed = JSON.parse(trimmed)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
}
