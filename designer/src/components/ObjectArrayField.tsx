/** 对象数组编辑器:元素是嵌套对象(list[TaskDeadline] 等)的可增删条目子表单。
 *  此前这类字段被数组分支无差别按字符串标签处理,对象被 String() 成
 *  「[object Object]」,既看不懂也改不了 —— 多级超时(deadlines)因此在界面上不可配置。 */

import type { JSONSchema } from '../types'
import { SchemaForm } from './SchemaForm'

type Props = {
  itemsSchema: JSONSchema
  defs: Record<string, JSONSchema>
  labels?: Record<string, string>
  /** 条目内字段的前缀(如 deadlines[].after_minutes),供路径标签解析 */
  basePath?: string
  value: Record<string, unknown>[]
  onChange: (next: Record<string, unknown>[]) => void
}

export function ObjectArrayField({ itemsSchema, defs, labels, basePath = '', value, onChange }: Props) {
  const setItem = (index: number, next: Record<string, unknown>) => {
    onChange(value.map((item, i) => (i === index ? next : item)))
  }
  const remove = (index: number) => onChange(value.filter((_, i) => i !== index))
  const add = () => onChange([...value, defaultsOf(itemsSchema)])

  return (
    <div className="obj-array">
      {value.length === 0 && <div className="empty-sub">暂无条目 — 点「添加」新建</div>}
      {value.map((item, i) => (
        <div key={i} className="obj-array-item">
          <div className="obj-array-head">
            <span className="obj-array-no">#{i + 1}</span>
            <button
              type="button"
              className="mini-btn"
              title="删除此条目"
              onClick={() => remove(i)}
            >
              × 删除
            </button>
          </div>
          <SchemaForm
            schema={itemsSchema}
            defs={defs}
            labels={labels}
            basePath={basePath}
            value={item}
            onChange={(next) => setItem(i, next)}
          />
        </div>
      ))}
      <button type="button" className="add-group" onClick={add}>＋ 添加条目</button>
    </div>
  )
}

/** 新条目的安全默认:取 schema 默认值;无默认的必填数字给 1(schema 约束 gt 0),避免「加了就红」 */
function defaultsOf(schema: JSONSchema): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, sub] of Object.entries(schema.properties ?? {})) {
    if (sub.default !== undefined) {
      out[key] = sub.default
    } else if ((schema.required ?? []).includes(key) && (sub.type === 'integer' || sub.type === 'number')) {
      out[key] = 1
    }
  }
  return out
}
