/** 发布确认对话框:语义 diff 预览 / 错误拦截 / 警告提示 / 版本判定全貌呈现(批 B2) */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { PublishResult, VerdictPreview } from '../types'
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
  const issues = useStore((s) => s.issues)
  const defInfo = useStore((s) => s.defInfo)
  const spec = useStore((s) => s.spec)
  const diff = useStore((s) => s.publishDiff)
  const diffLoading = useStore((s) => s.publishDiffLoading)
  const loadVerdict = useStore((s) => s.loadVerdict)
  // 发布由对话框本地编排(保存草稿→发布→呈现判定);store 只在成功后更新状态
  const [verdict, setVerdict] = useState<PublishResult | null>(null)
  const [publishing, setPublishing] = useState(false)
  // 版本判定预演：与真发布同一份判定，只读不落库——先看后果再按确认
  const [preview, setPreview] = useState<VerdictPreview | null>(null)

  useEffect(() => {
    if (!open) {
      setPreview(null)
      return
    }
    let alive = true
    void loadVerdict().then((result) => {
      if (alive) setPreview(result)
    })
    return () => {
      alive = false
    }
  }, [open, loadVerdict])

  if (!open || !defInfo || !spec) return null
  const errors = issues.filter((i) => i.level === 'error')
  const warnings = issues.filter((i) => i.level === 'warning')
  // 版号以引擎判定为准：兼容变更走 revision 续发（v1.1），破坏性才升 major。
  // 修前用 publishedVersion + 1 猜主版本，标题写「发布 v2」而引擎实际发 v1.1。
  const nextVersion = preview
    ? `${preview.next_major}.${preview.next_revision}`
    : defInfo.publishedVersion != null
      ? `${defInfo.publishedVersion}.?`
      : '1.1'

  const publishNow = async () => {
    setPublishing(true)
    try {
      await useStore.getState().saveNow()
      const result = await api.publish(defInfo.code)
      setVerdict(result)
      useStore.setState((s) => ({
        defInfo: s.defInfo
          ? { ...s.defInfo, publishedVersion: result.version, draftVersion: null }
          : s.defInfo,
        dirty: false,
      }))
      void useStore.getState().refreshListing()
    } catch (err) {
      const payload = (err as { payload?: { issues?: unknown } }).payload
      if (payload && typeof payload === 'object' && Array.isArray(payload.issues)) {
        useStore.setState({
          issues: payload.issues as typeof issues,
          rightTab: 'issues',
          publishOpen: false,
        })
        useStore.getState().showToast('✕ 校验未通过,已禁止发布 — 详见「校验」页')
      } else {
        useStore.getState().showToast(`发布失败:${err instanceof Error ? err.message : String(err)}`)
      }
    }
    setPublishing(false)
  }

  const close = () => {
    setVerdict(null)
    setOpen(false)
  }

  // 发布完成:呈现判定全貌(版本/是否破坏性/明细/无变化提示)
  if (verdict) {
    return (
      <div className="modal-mask" onClick={close}>
        <div className="modal" onClick={(e) => e.stopPropagation()}>
          <h3>✅ 已发布 v{verdict.version}.{verdict.revision}</h3>
          <p className="modal-sub">
            该版本内容<b>不可变</b>;运行中实例继续用旧版本,新实例使用新版本。
          </p>
          {verdict.identical_to != null && (
            <div className="modal-block warn">
              内容与已发布 v{verdict.identical_to[0]}.{verdict.identical_to[1]} 完全一致(引擎幂等去重,未产生新版本)。
            </div>
          )}
          {verdict.breaking && (
            <div className="modal-block bad">
              ⚠ 本次发布含<b>破坏性变更</b>(详见下方规则明细)— 在途实例不受影响,但迁移与兼容需要关注。
            </div>
          )}
          {verdict.triggered_rules.length > 0 && (
            <div className="diff-block">
              <div className="diff-head">版本判定规则明细</div>
              <ul className="diff-list">
                {verdict.triggered_rules.map((r, i) => (
                  <li key={i} className={`diff-item ${verdict.breaking ? 'changed' : 'info'}`}>
                    <span className={`diff-mark ${verdict.breaking ? 'changed' : 'info'}`}>
                      {verdict.breaking ? '!' : '·'}
                    </span>
                    <span className="diff-type">{r.rule}</span>
                    <span className="diff-detail">{r.detail}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {verdict.triggered_rules.length === 0 && !verdict.breaking && verdict.identical_to == null && (
            <div className="diff-summary ok">无破坏性变更(判定未触发任何版本规则)。</div>
          )}
          <div className="modal-actions">
            <button className="btn btn-primary" onClick={close}>完成</button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="modal-mask" onClick={() => { setVerdict(null); setOpen(false) }}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>发布 v{nextVersion}</h3>
        <p className="modal-sub">
          发布后该版本内容<b>不可变</b>;运行中实例继续使用 v{defInfo.publishedVersion ?? '-'},新实例使用 v{nextVersion}。
          发布前引擎会执行 <code>assert_valid</code> 强制校验。
        </p>

        {/* 版本判定预演：与真发布同一份判定（含破坏性规则与幂等去重），只读不落库 */}
        {preview && (
          <div className={`modal-block ${preview.breaking ? 'bad' : 'ok'}`}>
            <b>版本判定预演</b>：将发布为 v{preview.next_major}.{preview.next_revision}
            {preview.breaking ? '（含破坏性变更，见规则明细）' : '（兼容变更）'}
            {preview.identical_to != null &&
              `；内容与 v${preview.identical_to[0]}.${preview.identical_to[1]} 完全相同，引擎会幂等去重`}
            {preview.triggered_rules.length > 0 && (
              <ul className="diff-list" style={{ marginTop: 6 }}>
                {preview.triggered_rules.map((r, i) => (
                  <li key={i} className="diff-item changed">
                    <span className="diff-mark changed">!</span>
                    <span className="diff-type">{r.rule}</span>
                    <span className="diff-detail">{r.detail}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

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
            <div className="diff-summary ok">
              {preview?.identical_to != null
                ? `与已发布 v${diff.base_version} 内容一致——引擎会幂等去重，不会产生新版本。`
                : `与已发布 v${diff.base_version} 内容一致（发布将产生同内容的新版本）。`}
            </div>
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
          <button className="btn" onClick={close}>取消</button>
          <button
            className="btn btn-primary"
            disabled={errors.length > 0 || publishing}
            onClick={() => void publishNow()}
          >
            {publishing
              ? '发布中…'
              : preview?.identical_to != null
                ? '确认（内容相同，将被幂等跳过）'
                : `确认发布 v${nextVersion}`}
          </button>
        </div>
      </div>
    </div>
  )
}
