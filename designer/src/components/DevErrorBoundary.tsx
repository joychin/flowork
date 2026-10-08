/** 开发期防护:Vite 热更新中间态(编辑过程中的瞬时不一致代码)可能令组件树崩溃,
 *  没有边界时表现是整页白屏。有边界时给出「页面状态过期」卡片 + 一键刷新,可自愈。 */

import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { error: Error | null }

export class DevErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[designer] 组件崩溃(热更新中间态或代码缺陷):', error.message, info.componentStack?.split('\n').slice(0, 4).join('\n'))
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="eb-wrap">
        <div className="eb-card">
          <div className="eb-title">页面状态过期</div>
          <div className="eb-desc">
            开发服务器热更新过程中出现异常,页面组件已卸载。
            <code>{this.state.error.message}</code>
            点击刷新即可恢复(未保存的草稿改动在服务器上保留最近一次保存)。
          </div>
          <button className="btn btn-primary" onClick={() => window.location.reload()}>刷新页面</button>
        </div>
      </div>
    )
  }
}
