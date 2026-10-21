/** 未打开任何流程时的画布空态：打开 / 新建 / 拖入。
 *
 * 这里取代了原先独立的「定义列表页」——那个页面在开发态只是把 IDE 已有的文件树
 * 又列一遍，却不回答任何判断性问题。打开入口收进设计器本身，列表按需在对话框里出现。
 */

import { useState } from 'react'
import { useStore } from '../store'
import { designerConfig } from '../config'
import { OpenDialog } from './OpenDialog'

export function EmptyState() {
  const runtime = useStore((s) => s.runtime)
  const openLocalSpec = useStore((s) => s.openLocalSpec)
  const showToast = useStore((s) => s.showToast)
  const [opening, setOpening] = useState(false)
  const [dragging, setDragging] = useState(false)
  const readonly = designerConfig.readonly()

  const drop = async (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    const file = event.dataTransfer.files?.[0]
    if (!file) return
    try {
      const spec = JSON.parse(await file.text())
      if (!spec || typeof spec !== 'object' || !Array.isArray(spec.nodes)) {
        throw new Error('不是流程定义（缺少 nodes 数组）')
      }
      openLocalSpec(spec, file.name)
      showToast(`已打开 ${file.name}（未入库，保存即下载）`)
    } catch (err) {
      showToast(`打开失败：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  return (
    <div
      className="empty-stage"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes('Files')) {
          e.preventDefault()
          setDragging(true)
        }
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => void drop(e)}
    >
      <div className={`empty-card ${dragging ? 'ok' : ''}`}>
        <div className="logo-lg">f</div>
        <h2>flowork 流程设计器</h2>
        <p className="empty-sub">
          {runtime.source === 'files'
            ? `定义目录：${runtime.definitions_dir ?? ''} — 保存直接写回 JSON`
            : '打开一个 JSON 文件开始'}
        </p>
        {!readonly && (
          <div className="empty-actions">
            <button className="btn btn-primary" onClick={() => setOpening(true)}>打开流程</button>
          </div>
        )}
        <p className="empty-sub" style={{ marginTop: 16 }}>
          {readonly ? '只读审计模式' : '也可以把 .json 文件拖到这里'}
        </p>
        {runtime.session_ephemeral && (
          <p className="empty-sub" style={{ marginTop: 10 }} title="设计器不读写你的运行时数据库">
            设计器不碰运行时数据库；试跑痕迹只留在本进程，关掉即消失
          </p>
        )}
        {/* 表达式函数可见性：自定义取人函数没带进本进程时，模拟会报未声明函数——
            这一行让人一眼看出这台设计器认不认自己的函数 */}
        <p className="empty-sub" style={{ marginTop: 6 }}>
          {runtime.vocabulary_functions.length > 0
            ? `表达式函数（${runtime.vocabulary_source}）：${runtime.vocabulary_functions.join(' · ')}`
            : '未装配自定义表达式函数 — 用了 manager_of 等取人词汇的流程跑不起来，'
              + '启动时给 FLOWORK_ORG 或 FLOWORK_VOCABULARY'}
        </p>
      </div>
      {opening && <OpenDialog onClose={() => setOpening(false)} />}
    </div>
  )
}
