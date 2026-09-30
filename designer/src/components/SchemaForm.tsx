/**
 * SchemaForm:由 Spec 的 JSON Schema(pydantic 导出)递归生成表单。
 * 引擎侧字段变动,表单零代码跟进 —— 这是「schema 驱动」原则的落地。
 */

import { useState } from 'react'
import type { JSONSchema } from '../types'

/** 字段中文名(引擎 schema title 为英文时兜底) */
const FIELD_LABELS: Record<string, string> = {
  name: '节点名称',
  gateway_kind: '匹配方式',
  assignee: '审批人(单个)',
  assignees: '审批人(多个)',
  candidate_users: '候选用户',
  candidate_groups: '候选组',
  multi_instance: '多人审批(多实例)',
  mode: '执行模式',
  approve_exit: '同意出口(比例)',
  disapprove_exit: '驳回出口(比例)',
  reject_target: '驳回目标节点',
  auto_pass_originator: '发起人自动通过',
  auto_pass_previous: '上一步办理人自动通过',
  auto_pass_participated: '曾参与人自动通过',
  no_participant_policy: '无参与者策略',
  timeout: '超时策略',
  due_minutes: '超时时间(分钟)',
  strategy: '到期动作',
  action: '动作 handler',
  params: '动作参数(JSON)',
  process_code: '子流程定义 code',
  is_synchronous: '同步等待子流程',
  input_mappings: '输入映射(JSON)',
  output_mappings: '输出映射(JSON)',
  recipients: '接收人',
}

const ENUM_LABELS: Record<string, string> = {
  exclusive: '命中即走(按顺序取第一条)',
  parallel: '并行(全走)',
  inclusive: '命中的都走(高级)',
  notify: '仅催办通知',
  auto_approve: '自动通过',
  auto_reject: '自动驳回',
  raise: '报错拦截',
  to_admins: '转交管理员',
}

/** 按字段语境区分的枚举标签(如 multi_instance.mode 的 parallel 是「并签」而非「并行」) */
const FIELD_ENUM_LABELS: Record<string, Record<string, string>> = {
  mode: { parallel: '并签(全员同时待办)', sequential: '依次(逐人待办)' },
}

function fieldLabel(name: string, schema: JSONSchema): string {
  return FIELD_LABELS[name] ?? schema.title ?? name
}

function enumLabel(fieldName: string, value: unknown): string {
  const byField = FIELD_ENUM_LABELS[fieldName]
  if (byField && byField[String(value)]) return byField[String(value)]
  return ENUM_LABELS[String(value)] ?? String(value)
}

function resolve(schema: JSONSchema | undefined, defs: Record<string, JSONSchema>): { schema: JSONSchema | null; nullable: boolean } {
  if (!schema) return { schema: null, nullable: false }
  if (schema.$ref) {
    const name = schema.$ref.replace('#/$defs/', '')
    return { schema: defs[name] ?? null, nullable: false }
  }
  if (schema.anyOf) {
    const nonNull = schema.anyOf.filter((s) => s.type !== 'null' && !s.$ref?.includes('null'))
    const hasNull = schema.anyOf.some((s) => s.type === 'null')
    const target = nonNull[0]
    if (target?.$ref) {
      const name = target.$ref.replace('#/$defs/', '')
      return { schema: defs[name] ?? null, nullable: hasNull }
    }
    return { schema: target ?? null, nullable: hasNull }
  }
  return { schema, nullable: false }
}

type Props = {
  schema: JSONSchema
  defs: Record<string, JSONSchema>
  value: Record<string, unknown>
  exclude?: string[]
  onChange: (next: Record<string, unknown>) => void
}

export function SchemaForm({ schema, defs, value, exclude = [], onChange }: Props) {
  const resolved = resolve(schema, defs)
  const target = resolved.schema
  if (!target?.properties) return <div className="form-empty">该节点类型无可配置字段</div>

  const entries = Object.entries(target.properties).filter(([key]) => !exclude.includes(key))
  const required = new Set(target.required ?? [])

  const setField = (key: string, v: unknown) => onChange({ ...value, [key]: v })

  return (
    <div className="schema-form">
      {entries.map(([key, sub]) => (
        <SchemaField
          key={key}
          name={key}
          schema={sub}
          defs={defs}
          required={required.has(key)}
          value={value[key]}
          onChange={(v) => setField(key, v)}
        />
      ))}
    </div>
  )
}

type FieldProps = {
  name: string
  schema: JSONSchema
  defs: Record<string, JSONSchema>
  required: boolean
  value: unknown
  onChange: (v: unknown) => void
}

function SchemaField({ name, schema, defs, required, value, onChange }: FieldProps) {
  const { schema: target, nullable } = resolve(schema, defs)
  if (!target) return null
  const label = fieldLabel(name, target)
  const desc = target.description

  // 可空对象(multi_instance / timeout):启用开关 + 嵌套表单
  if (target.type === 'object' && nullable && target.properties) {
    const enabled = value != null && typeof value === 'object'
    return (
      <div className="field">
        <div className="field-label">
          <span>{label}</span>
          <button
            type="button"
            className={`switch ${enabled ? 'on' : ''}`}
            onClick={() => onChange(enabled ? null : defaultsOf(target))}
            aria-label={`启用${label}`}
          >
            {enabled ? '已启用' : '未启用'}
          </button>
        </div>
        {enabled && typeof value === 'object' && value != null && (
          <div className="nested">
            <SchemaForm schema={target} defs={defs} value={value as Record<string, unknown>} onChange={(v) => onChange(v)} />
          </div>
        )}
      </div>
    )
  }

  // 固定对象(不可空):直接嵌套
  if (target.type === 'object' && target.properties) {
    return (
      <div className="field">
        <div className="field-label"><span>{label}</span></div>
        <div className="nested">
          <SchemaForm
            schema={target}
            defs={defs}
            value={(value ?? {}) as Record<string, unknown>}
            onChange={(v) => onChange(v)}
          />
        </div>
      </div>
    )
  }

  // 任意键值对象(params / mappings):JSON 文本域
  if (target.type === 'object') {
    return (
      <div className="field">
        <div className="field-label"><span>{label}</span></div>
        <JsonArea value={value ?? {}} onChange={onChange} />
      </div>
    )
  }

  // 数组:字符串标签输入
  if (target.type === 'array') {
    const items = Array.isArray(value) ? (value as unknown[]).map(String) : []
    return (
      <div className="field">
        <div className="field-label">
          <span>{label}</span>
          {!required && <span className="opt">可选</span>}
        </div>
        <TagInput values={items} onChange={onChange} placeholder={desc ?? '输入后回车添加,点击标签删除'} />
      </div>
    )
  }

  // 布尔:双态按钮
  if (target.type === 'boolean') {
    const on = value === true
    return (
      <div className="field">
        <div className="field-label"><span>{label}</span></div>
        <div className="bool-row">
          <button type="button" className={`bool-btn ${on ? 'on' : ''}`} onClick={() => onChange(!on)}>
            {on ? '是' : '否'}
          </button>
        </div>
      </div>
    )
  }

  // 枚举:下拉
  if (target.enum) {
    return (
      <div className="field">
        <div className="field-label"><span>{label}</span></div>
        <select value={String(value ?? target.default ?? target.enum[0])} onChange={(e) => onChange(e.target.value)}>
          {target.enum.map((v) => (
            <option key={String(v)} value={String(v)}>{enumLabel(name, v)}</option>
          ))}
        </select>
      </div>
    )
  }

  // 数字
  if (target.type === 'integer' || target.type === 'number') {
    return (
      <div className="field">
        <div className="field-label"><span>{label}</span></div>
        <input
          type="number"
          value={value == null ? '' : String(value)}
          placeholder={desc}
          onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
        />
      </div>
    )
  }

  // 可空字符串:输入 + 清空
  if (target.type === 'string') {
    return (
      <div className="field">
        <div className="field-label">
          <span>{label}</span>
          {!required && <span className="opt">可选</span>}
        </div>
        <input
          type="text"
          value={value == null ? '' : String(value)}
          placeholder={desc ?? (nullable ? '留空表示不设置' : '')}
          onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}
        />
      </div>
    )
  }

  return null
}

function defaultsOf(schema: JSONSchema): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, sub] of Object.entries(schema.properties ?? {})) {
    if (sub.default !== undefined) out[key] = sub.default
  }
  return out
}

function TagInput({ values, onChange, placeholder }: { values: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const v = draft.trim()
    if (!v || values.includes(v)) return setDraft('')
    onChange([...values, v])
    setDraft('')
  }
  return (
    <div className="tag-input">
      {values.map((v) => (
        <span key={v} className="tag" onClick={() => onChange(values.filter((x) => x !== v))} title="点击删除">
          {v} ×
        </span>
      ))}
      <input
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); add() }
          if (e.key === 'Backspace' && !draft && values.length) onChange(values.slice(0, -1))
        }}
        onBlur={add}
      />
    </div>
  )
}

function JsonArea({ value, onChange }: { value: unknown; onChange: (v: unknown) => void }) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2))
  const [bad, setBad] = useState(false)
  return (
    <>
      <textarea
        className={`json-area ${bad ? 'bad' : ''}`}
        value={text}
        rows={4}
        spellCheck={false}
        onChange={(e) => {
          const t = e.target.value
          setText(t)
          try {
            onChange(JSON.parse(t))
            setBad(false)
          } catch {
            setBad(true)
          }
        }}
      />
      {bad && <div className="json-bad">JSON 格式有误,修正后自动生效</div>}
    </>
  )
}
