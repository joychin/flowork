/** 发布确认对话框:错误拦截 / 警告提示 / 不可变说明 */

import { useStore } from '../store'

export function PublishDialog() {
  const open = useStore((s) => s.publishOpen)
  const setOpen = useStore((s) => s.setPublishOpen)
  const publish = useStore((s) => s.publish)
  const issues = useStore((s) => s.issues)
  const defInfo = useStore((s) => s.defInfo)

  if (!open || !defInfo) return null
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
