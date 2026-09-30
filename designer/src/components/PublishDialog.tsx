/** 发布确认对话框:语义 diff 预览 / 错误拦截 / 警告提示 / 不可变说明 */

import { useStore } from '../store'

const ACTION_LABELS: Record<string, string> = { added: '+', removed: '−', changed: '~', info: '·' }
const TYPE_LABELS: Record<string, string> = {
  meta: '流程',
  node: '节点',
  flow: '连线',
  variable: '变量',
}

export function PublishDialog() {
  const open = useStore((s) => s.publishOpen)
  const setOpen = useStore((s) => s.setPublishOpen)
  const publish = useStore((s) => s.publish)
  const issues = useStore((s) => s.issues)
  const defInfo = useStore((s) => s.defInfo)
  const spec = useStore((s) => s.spec)
  const diff = useStore((s) => s.publishDiff)
  const diffLoading = useStore((s) => s.publishDiffLoading)

  if (!open || !defInfo || !spec) return null
  const errors = issues.filter((i) => i.level === 'error')
  const warnings = issues.filter((i) => i.level === 'warning')
  const nextVersion = defInfo.publishedVersion != null ? defInfo.publishedVersion + 1 : 1

  return (
    <div className="modal-mask" onClick={() => setOpen(false)}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>发布 v{nextVersion}</h3>
        <p className="modal-sub">
          发布后该版本内容<b>不可变</b>;运行中实例继续使用 v{defInfo.publishedVersion ?? '-'},新实例使用 v{nextVersion}。
          发布前引擎会执行 <code>assert_valid</code> 强制校验。
        </p>

        {errors.length > 0 && (
          <div className="modal-block bad">
            ✕ 存在 {errors.length} 个校验错误,需先修复:{errors[0].message}
            {errors.length > 1 && ' 等'}
          </div>
        )}
        {errors.length === 0 && warnings.length > 0 && (
          <div className="modal-block warn">⚠ 有 {warnings.length} 条警告(不阻塞发布):{warnings[0].message}</div>
        )}

        {/* 发布前 diff:与最新已发布版本的语义变更清单 */}
        <div className="diff-block">
          <div className="diff-head">
            {diff?.first_publish ? '首次发布 — 将发布以下内容' : '本次发布相对已发布版本的变更'}
          </div>
          {diffLoading && <div className="empty-sub">正在比对…</div>}
          {!diffLoading && diff?.first_publish && (
            <div className="diff-summary">
              全新发布:{spec.nodes.length} 个节点 · {spec.flows.length} 条连线
              {(spec.variables?.length ?? 0) > 0 && ` · ${spec.variables!.length} 个流程变量`}
            </div>
          )}
          {!diffLoading && diff && !diff.first_publish && diff.changes.length === 0 && (
            <div className="diff-summary ok">与已发布 v{diff.base_version} 内容一致(发布将产生同内容的新版本)。</div>
          )}
          {!diffLoading && diff && diff.changes.length > 0 && (
            <ul className="diff-list">
              {diff.changes.map((c, i) => (
                <li key={i} className={`diff-item ${c.action}`}>
                  <span className={`diff-mark ${c.action}`}>{ACTION_LABELS[c.action] ?? '·'}</span>
                  <span className="diff-type">{TYPE_LABELS[c.type] ?? c.type}</span>
                  <span className="diff-detail">{c.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="modal-actions">
          <button className="btn" onClick={() => setOpen(false)}>取消</button>
          <button className="btn btn-primary" disabled={errors.length > 0} onClick={() => void publish()}>
            确认发布 v{nextVersion}
          </button>
        </div>
      </div>
    </div>
  )
}
