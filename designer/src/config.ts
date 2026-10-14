/** 插件配置面(设计器插件计划批 C2):apiBase/mode/definitionCode 是 npm 包的公开接口。
 *
 * 宿主在挂载前调用 configureDesigner(或经 mountFloworkDesigner 选项);模块级
 * 单例在挂载前完成配置即可——多个实例共享一份配置(一个页面嵌一个设计器)。
 */

export type DesignerMode = 'edit' | 'readonly'

type Config = {
  /** 引擎 API 前缀;同域部署留空(走 /api),跨域/网关代理时给完整前缀 */
  apiBase: string
  /** edit=仓库开发态全功能;readonly=宿主嵌入默认(审计面,画布不可编辑) */
  mode: DesignerMode
  /** 深链:挂载后直接打开该定义(审计场景常用);null=定义列表 */
  definitionCode: string | null
}

// 缺省 readonly(全仓 review 2026-10-13 批 2 P0-1):宿主嵌入按 README 三行示例
// 漏传 mode 时必须落在只读审计面——/api 写端点零鉴权(凭证归宿主网关),缺省
// edit 等于无感开放写路径。仓库开发态由 main.tsx 显式传 'edit'。
const config: Config = { apiBase: '', mode: 'readonly', definitionCode: null }

export function configureDesigner(
  opts: { apiBase?: string; mode?: DesignerMode; definitionCode?: string | null },
): void {
  if (typeof opts.apiBase === 'string') config.apiBase = opts.apiBase.replace(/\/+$/, '')
  if (opts.mode === 'edit' || opts.mode === 'readonly') config.mode = opts.mode
  if (opts.definitionCode !== undefined) config.definitionCode = opts.definitionCode
}

export const designerConfig = {
  apiBase: (): string => config.apiBase,
  readonly: (): boolean => config.mode === 'readonly',
  definitionCode: (): string | null => config.definitionCode,
}
