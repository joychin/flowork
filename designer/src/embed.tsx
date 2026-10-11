/** 插件挂载入口(设计器插件计划批 C1):npm 包的公开面。
 *
 * 宿主三行代码嵌入只读审计视图::
 *
 *     import { mountFloworkDesigner } from '@flowork/designer'
 *     import '@flowork/designer/style.css'
 *     mountFloworkDesigner('#audit', { apiBase: '/api', mode: 'readonly', definitionCode: 'leave' })
 */

import { createRoot, type Root } from 'react-dom/client'
import { configureDesigner, type DesignerMode } from './config'
import App from './App'
import './index.css'

export type MountOptions = {
  /** 引擎 API 前缀(同域留空;跨域/网关代理给完整前缀) */
  apiBase?: string
  /** edit(默认,开发态全功能) | readonly(审计面:画布只读+无编辑动作) */
  mode?: DesignerMode
  /** 深链定义 code:挂载后直接打开(缺省=定义列表) */
  definitionCode?: string | null
}

export function mountFloworkDesigner(el: HTMLElement | string, opts: MountOptions = {}): { unmount: () => void } {
  configureDesigner(opts)
  const host = typeof el === 'string' ? document.querySelector<HTMLElement>(el) : el
  if (!host) throw new Error('mountFloworkDesigner: 挂载点不存在')
  const root: Root = createRoot(host)
  root.render(<App />)
  return { unmount: () => root.unmount() }
}

export { configureDesigner }
export type { DesignerMode }
