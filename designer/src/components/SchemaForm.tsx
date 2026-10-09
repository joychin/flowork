/**
 * SchemaForm:由 Spec 的 JSON Schema(pydantic 导出)递归生成表单。
 * 引擎侧字段变动,表单零代码跟进 —— 这是「schema 驱动」原则的落地。
 * 分组(form_groups 元数据)+ 必填星号 + 字段级内联错误 = 渐进披露的信息架构。
 */

import { useState } from 'react'
import type { FormGroup, JSONSchema } from '../types'
import { ObjectArrayField } from './ObjectArrayField'

/** 字段中文名(引擎 schema title 为英文时兜底) */
const FIELD_LABELS: Record<string, string> = {
  name: '节点名称',
  route_mode: '匹配方式',
  advance_policy: '开始条件',
  assignee: '审批人(单个)',
  assignees: '审批人(多个)',
  candidate_users: '候选用户',
  candidate_groups: '候选组',
  multi_instance: '多人审批(多实例)',
  mode: '执行模式',
  approve_exit: '同意出口(公式)',
  disapprove_exit: '驳回出口(公式)',
  reject_target: '驳回目标节点',
  auto_pass_originator: '发起人自动通过',
  auto_pass_previous: '上一步办理人自动通过',
  auto_pass_participated: '曾参与人自动通过',
  no_participant_policy: '无参与者策略',
  timeout: '超时策略',
  due_minutes: '超时时间(分钟)',
  strategy: '到期动作',
  deadlines: '多级超时(分钟 → 动作)',
  after_minutes: '时长(分钟)',
  reassign_to: '改派目标(CEL)',
  action: '动作 handler',
  params: '动作参数(JSON)',
  process_code: '子流程定义 code',
  binding: '绑定方式',
  subjects: '参与主体(每项一个 CEL 表达式)',
  completion: '完成公式',
  output_mode: '输出聚合方式',
  is_synchronous: '同步等待子流程',
  input_mappings: '输入映射(JSON)',
  output_mappings: '输出映射(JSON)',
  recipients: '接收人',
  message: '等待消息',
  correlation_key: '关联键(CEL)',
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
  reassign: '改派他人',
  escalate: '升级上报',
  latest: '最新版(运行期解析)',
  deployment: '部署时锁定版本',
  collect: '按序聚合(列表)',
  merge: '逐子合并(仅单子)',
  mark: '仅打标继续跑',
  cancel: '作废实例',
  terminate: '终止实例',
}

/** 按字段语境区分的枚举标签(如 multi_instance.mode 的 parallel 是「并签」而非「并行」) */
const FIELD_ENUM_LABELS: Record<string, Record<string, string>> = {
  mode: { parallel: '并签(全员同时待办)', sequential: '依次(逐人待办)' },
}

function fieldLabel(path: string, name: string, schema: JSONSchema, labels?: Record<string, string>): string {
  // 解析顺序:registry 下发的路径标签(kind 专属,可区分同名字段) → 前端全局字段表 → schema title
  return labels?.[path] ?? FIELD_LABELS[name] ?? schema.title ?? name
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

export type FieldOverride = (ctx: { value: unknown; onChange: (v: unknown) => void }) => React.ReactNode

type Props = {
  schema: JSONSchema
  defs: Record<string, JSONSchema>
  value: Record<string, unknown>
  exclude?: string[]
  /** 按字段名覆盖默认控件(如 process_code → 定义下拉、reject_target → 节点下拉);仅顶层生效 */
  overrides?: Record<string, FieldOverride>
  /** 属性面板分组元数据(registry ui.form_groups);未分组字段归入「其他」组 */
  groups?: FormGroup[] | null
  /** registry 下发的路径标签(kind 专属;键为点路径,如 message.name / deadlines[].action) */
  labels?: Record<string, string>
  /** 嵌套递归时的父路径(顶层空) */
  basePath?: string
  /** 字段级内联错误(key → 错误文案) */
  errors?: Record<string, string>
  onChange: (next: Record<string, unknown>) => void
}

/** 前端轻量必填校验:schema.required 中取值为空(空串/空数组/空对象/null)即报错。 */
export function requiredFieldErrors(
  schema: JSONSchema,
  defs: Record<string, JSONSchema>,
  value: Record<string, unknown>,
  exclude: string[] = [],
): Record<string, string> {
  const { schema: target } = resolve(schema, defs)
  if (!target?.properties) return {}
  const out: Record<string, string> = {}
  for (const key of target.required ?? []) {
    if (exclude.includes(key)) continue
    const v = value[key]
    const empty =
      v == null ||
      v === '' ||
      (Array.isArray(v) && v.length === 0) ||
      (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0)
    if (empty) out[key] = '必填项不能为空'
  }
  return out
}

export function SchemaForm({ schema, defs, value, exclude = [], groups, labels, basePath = '', overrides, errors, onChange }: Props) {
  const resolved = resolve(schema, defs)
  const target = resolved.schema
  if (!target?.properties) return <div className="form-empty">该节点类型无可配置字段</div>

  const required = new Set(target.required ?? [])
  const setField = (key: string, v: unknown) => onChange({ ...value, [key]: v })
  const childPath = (key: string) => (basePath ? `${basePath}.${key}` : key)

  const renderField = (key: string, sub: JSONSchema) => {
    const Override = basePath ? undefined : overrides?.[key]
    if (Override) {
      return (
        <div key={key} className={`field ${errors?.[key] ? 'has-error' : ''}`}>
          <div className="field-label">
            <span>{fieldLabel(childPath(key), key, sub, labels)}</span>
            {!required.has(key) && <span className="opt">可选</span>}
          </div>
          <Override value={value[key]} onChange={(v) => setField(key, v)} />
          {errors?.[key] && <div className="field-error">{errors[key]}</div>}
        </div>
      )
    }
    return (
    <SchemaField
      key={key}
      path={childPath(key)}
      name={key}
      schema={sub}
      defs={defs}
      labels={labels}
      required={required.has(key)}
      error={errors?.[key]}
      value={value[key]}
      onChange={(v) => setField(key, v)}
    />
    )
  }

  // 无分组元数据:保持单列(自定义类型零配置兜底)
  if (!groups?.length) {
    const entries = Object.entries(target.properties).filter(([key]) => !exclude.includes(key))
    return <div className="schema-form">{entries.map(([key, sub]) => renderField(key, sub))}</div>
  }

  // 有分组:按组渲染手风琴;未覆盖字段归入「其他」
  const grouped = new Set(groups.flatMap((g) => g.fields))
  const leftovers = Object.keys(target.properties).filter((key) => !exclude.includes(key) && !grouped.has(key))
  const sections = [...groups]
  if (leftovers.length) sections.push({ title: '其他', fields: leftovers })

  return (
    <div className="schema-form">
      {sections
        .filter((g) => g.fields.some((f) => !exclude.includes(f) && target.properties![f]))
        .map((group) => (
          <FormSection key={group.title} title={group.title} defaultCollapsed={group.collapsed ?? false}>
            {group.fields
              .filter((f) => !exclude.includes(f) && target.properties![f])
              .map((key) => renderField(key, target.properties![key]))}
          </FormSection>
        ))}
    </div>
  )
}

function FormSection({ title, defaultCollapsed, children }: {
  title: string
  defaultCollapsed: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(!defaultCollapsed)
  return (
    <div className={`form-section ${open ? 'open' : 'closed'}`}>
      <button type="button" className="form-section-head" onClick={() => setOpen(!open)}>
        <span className={`chevron ${open ? 'open' : ''}`}>▸</span>
        <span>{title}</span>
      </button>
      {open && <div className="form-section-body">{children}</div>}
    </div>
  )
}

type FieldProps = {
  path: string
  name: string
  schema: JSONSchema
  defs: Record<string, JSONSchema>
  labels?: Record<string, string>
  required: boolean
  error?: string
  value: unknown
  onChange: (v: unknown) => void
}

function SchemaField({ path, name, schema, defs, labels, required, error, value, onChange }: FieldProps) {
  const { schema: target, nullable } = resolve(schema, defs)
  if (!target) return null
  const label = fieldLabel(path, name, target, labels)
  const desc = target.description

  const labelRow = (extra?: React.ReactNode) => (
    <div className="field-label">
      <span>
        {required && <span className="req" title="必填">*</span>}
        {label}
      </span>
      {extra}
    </div>
  )

  const wrap = (inner: React.ReactNode, extra?: React.ReactNode) => (
    <div className={`field ${error ? 'has-error' : ''}`}>
      {labelRow(extra)}
      {inner}
      {error && <div className="field-error">{error}</div>}
    </div>
  )

  // 可空对象(multi_instance / timeout / message):启用开关 + 嵌套表单
  if (target.type === 'object' && nullable && target.properties) {
    const enabled = value != null && typeof value === 'object'
    return wrap(
      enabled && typeof value === 'object' && value != null && (
        <div className="nested">
          <SchemaForm schema={target} defs={defs} labels={labels} basePath={path} value={value as Record<string, unknown>} onChange={(v) => onChange(v)} />
        </div>
      ),
      <button
        type="button"
        className={`switch ${enabled ? 'on' : ''}`}
        onClick={() => onChange(enabled ? null : defaultsOf(target))}
        aria-label={`启用${label}`}
      >
        {enabled ? '已启用' : '未启用'}
      </button>,
    )
  }

  // 固定对象(不可空):直接嵌套
  if (target.type === 'object' && target.properties) {
    return wrap(
      <div className="nested">
        <SchemaForm
          schema={target}
          defs={defs}
          labels={labels}
          basePath={path}
          value={(value ?? {}) as Record<string, unknown>}
          onChange={(v) => onChange(v)}
        />
      </div>,
    )
  }

  // 任意键值对象(params / mappings):JSON 文本域
  if (target.type === 'object') {
    return wrap(<JsonArea value={value ?? {}} onChange={onChange} />)
  }

  // 数组:元素是对象(deadlines 等)走条目子表单;字符串数组走标签输入
  if (target.type === 'array') {
    const { schema: items } = resolve(target.items, defs)
    if (items?.type === 'object' && items.properties) {
      return wrap(
        <ObjectArrayField
          itemsSchema={items}
          defs={defs}
          labels={labels}
          basePath={`${path}[]`}
          value={Array.isArray(value) ? (value as Record<string, unknown>[]) : []}
          onChange={onChange}
        />,
        !required && <span className="opt">可选</span>,
      )
    }
    const stringItems = Array.isArray(value) ? (value as unknown[]).map(String) : []
    return wrap(
      <TagInput values={stringItems} onChange={onChange} placeholder={desc ?? '输入后回车添加,点击标签删除'} />,
      !required && <span className="opt">可选</span>,
    )
  }

  // 布尔:双态按钮
  if (target.type === 'boolean') {
    const on = value === true
    return wrap(
      <div className="bool-row">
        <button type="button" className={`bool-btn ${on ? 'on' : ''}`} onClick={() => onChange(!on)}>
          {on ? '是' : '否'}
        </button>
      </div>,
    )
  }

  // 枚举:下拉
  if (target.enum) {
    return wrap(
      <select value={String(value ?? target.default ?? target.enum[0])} onChange={(e) => onChange(e.target.value)}>
        {target.enum.map((v) => (
          <option key={String(v)} value={String(v)}>{enumLabel(name, v)}</option>
        ))}
      </select>,
    )
  }

  // 数字
  if (target.type === 'integer' || target.type === 'number') {
    return wrap(
      <input
        type="number"
        value={value == null ? '' : String(value)}
        placeholder={desc}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
      />,
    )
  }

  // 可空字符串:输入 + 清空
  if (target.type === 'string') {
    return wrap(
      <input
        type="text"
        value={value == null ? '' : String(value)}
        placeholder={desc ?? (nullable ? '留空表示不设置' : '')}
        onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}
      />,
      !required && <span className="opt">可选</span>,
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
