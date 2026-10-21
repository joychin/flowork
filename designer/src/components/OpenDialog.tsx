/** 打开流程:一个对话框装三种来源——定义目录里的 JSON / 数据库里的定义 / 本地文件。
 *
 * 文件选择器在嵌入型 webview（含 ZCode 内置浏览器）会被屏蔽——点击既不开窗也无回调，
 * 用户只看到宿主报「无法加载」。所以本地文件另给拖入与粘贴文本两条路，任何环境都成立。
 */

import { useEffect, useState } from 'react'
import { useStore } from '../store'

export function OpenDialog({ onClose }: { onClose: () => void }) {
  const runtime = useStore((s) => s.runtime)
  const files = useStore((s) => s.files)
  const listing = useStore((s) => s.listing)
  const listingLoading = useStore((s) => s.listingLoading)
  const listingError = useStore((s) => s.listingError)
  const refreshListing = useStore((s) => s.refreshListing)
  const openFile = useStore((s) => s.openFile)
  const openDefinition = useStore((s) => s.openDefinition)
  const openLocalSpec = useStore((s) => s.openLocalSpec)
  const createDefinition = useStore((s) => s.createDefinition)
  const createFile = useStore((s) => s.createFile)
  const showToast = useStore((s) => s.showToast)

  const fileMode = runtime.source === 'files'
  const [code, setCode] = useState('')
  const [creating, setCreating] = useState(false)
  const [text, setText] = useState('')
  const [pasting, setPasting] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')

  // 每次打开都刷新一次：目录里可能刚被别人（或 IDE）改了文件
  useEffect(() => {
    void refreshListing()
  }, [refreshListing])

  const loadText = (raw: string, name: string) => {
    try {
      const spec = JSON.parse(raw)
      if (!spec || typeof spec !== 'object' || !Array.isArray(spec.nodes)) {
        throw new Error('不是流程定义（缺少 nodes 数组）')
      }
      openLocalSpec(spec, name)
      showToast(`已打开 ${name}（未入库，保存即下载）`)
      onClose()
    } catch (err) {
      setError(`打开失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const fromFile = async (file: File) => {
    setError('')
    try {
      const spec = JSON.parse(await file.text())
      loadText(JSON.stringify(spec), file.name)
    } catch (err) {
      setError(`打开失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const newDefinition = async () => {
    const trimmed = code.trim()
    if (!/^[a-z][a-z0-9_]*$/.test(trimmed)) return
    try {
      if (fileMode) await createFile(trimmed, '')
      else await createDefinition(trimmed, '')
      onClose()
    } catch (err) {
      setError(`新建失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const broken = files.filter((item) => !item.valid)

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ minWidth: 560 }}>
        <h3>打开流程</h3>
        <p className="modal-sub">
          {fileMode
            ? `定义目录：${runtime.definitions_dir ?? ''} — 打开即可编辑，保存写回该 JSON 文件`
            : '本会话已打开过的定义（只在内存里，关掉即消失）'}
        </p>

        {listingError && <div className="modal-block bad">加载失败：{listingError}</div>}
        {listingLoading && <div className="empty-sub">加载中…</div>}

        {!listingLoading && (
          <div className="def-grid" style={{ maxHeight: 260, overflowY: 'auto' }}>
            {listing.map((def) => {
              const entry = fileMode ? files.find((f) => f.code === def.code) : undefined
              return (
                <button
                  key={def.code}
                  className="def-card"
                  title={entry?.file}
                  onClick={() => {
                    if (fileMode && entry) void openFile(entry.file)
                    else void openDefinition(def.code)
                    onClose()
                  }}
                >
                  <div className="def-card-head">
                    <span className="def-card-name">{def.name || def.code}</span>
                    <span className="def-card-arrow">→</span>
                  </div>
                  <div className="def-card-code">{entry?.file ?? def.code}</div>
                  <div className="def-card-chips">
                    {fileMode ? (
                      <span className="chip">JSON 文件</span>
                    ) : (
                      <>
                        {def.draft_version != null && <span className="chip chip-draft">草稿 v{def.draft_version}</span>}
                        {def.published_version != null && <span className="chip chip-pub">v{def.published_version} 已发布</span>}
                        {def.draft_version == null && def.published_version == null && <span className="chip">空</span>}
                      </>
                    )}
                  </div>
                </button>
              )
            })}
            {/* 坏文件不隐藏：画布上表达不了这类问题，只能在这里点名 */}
            {broken.map((item) => (
              <div key={item.file} className="def-card" style={{ opacity: 0.6, cursor: 'default' }}>
                <div className="def-card-head">
                  <span className="def-card-name">{item.file}</span>
                  <span className="def-card-arrow">✕</span>
                </div>
                <div className="def-card-code">{item.error || '装载失败'}</div>
                <div className="def-card-chips"><span className="chip chip-draft">需要修复</span></div>
              </div>
            ))}
            {!listing.length && !broken.length && (
              <div className="empty-hint">
                {fileMode ? '目录里还没有 *.json' : '还没有流程定义'} — 用下面的「新建」开始
              </div>
            )}
          </div>
        )}

        <div className="field" style={{ marginTop: 14 }}>
          <div className="field-label"><span>新建（code）</span></div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              value={code}
              placeholder="小写字母/数字/下划线，如 travel_expense"
              onChange={(e) => setCode(e.target.value)}
              style={{ flex: 1 }}
            />
            <button
              className="btn"
              disabled={!/^[a-z][a-z0-9_]*$/.test(code.trim())}
              onClick={() => void newDefinition()}
            >
              新建
            </button>
          </div>
        </div>

        <div
          className={`modal-block ${dragging ? 'ok' : ''}`}
          style={{ borderStyle: 'dashed', textAlign: 'center', cursor: 'pointer', marginTop: 12 }}
          onClick={() => setPasting(!pasting)}
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            const file = e.dataTransfer.files?.[0]
            if (file) void fromFile(file)
          }}
        >
          打开本地文件：<b>拖入 .json</b> / 点击展开粘贴框
          <div className="empty-sub" style={{ marginTop: 4 }}>
            本地打开不写入数据库也不进目录——保存即下载
          </div>
        </div>

        {pasting && (
          <div className="field">
            <textarea
              value={text}
              rows={7}
              placeholder='{ "code": "my_flow", "nodes": [...], "flows": [...] }'
              onChange={(e) => setText(e.target.value)}
              style={{ width: '100%', fontFamily: 'monospace', fontSize: 12 }}
            />
            <button
              className="btn btn-primary"
              disabled={!text.trim()}
              onClick={() => loadText(text, '本地定义.json')}
            >
              打开粘贴的内容
            </button>
          </div>
        )}

        {error && <div className="modal-block bad">{error}</div>}

        <div className="modal-actions">
          <button className="btn" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}
