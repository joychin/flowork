/** 流程设置(空选态):流程名称 / 变量声明 / 入参契约 / 实例时限 / 消息启动。
 *  从 RightPanel 拆出(该文件超 500 行);三块新配置都是 ProcessDefinition 一等字段,
 *  PUT 草稿直接落库,后端零改动。 */

import { useState } from 'react'
import type { ProcessVariable, Spec, StartMessageDecl } from '../types'
import { useStore } from '../store'

export function ProcessSettings({ spec }: { spec: Spec }) {
  const openDryRun = useStore((s) => s.openDryRun)
  return (
    <div>
      <PanelHead code={spec.code} />
      <NameField spec={spec} />
      <VariablesSection spec={spec} />
      <InputContractSection spec={spec} />
      <InstanceDeadlineSection spec={spec} />
      <StartOnMessageSection spec={spec} />
      <button className="btn btn-block" onClick={openDryRun}>▶ 试运行(按样例值预演路由)</button>
      <div className="panel-note">
        点击画布中的节点或连线编辑其属性;流程级配置随 Spec 保存,与 Spec JSON Tab 同源。
      </div>
    </div>
  )
}

function PanelHead({ code }: { code: string }) {
  return (
    <div className="panel-head">
      <div className="panel-head-row">
        <span className="panel-ico">⌂</span>
        <span className="panel-title">流程设置</span>
      </div>
      <div className="panel-code">{code}</div>
    </div>
  )
}

function NameField({ spec }: { spec: Spec }) {
  const mutate = useStore((s) => s.mutate)
  return (
    <div className="field">
      <div className="field-label"><span>流程名称</span></div>
      <input
        value={spec.name}
        placeholder="给流程起个名字"
        onChange={(e) => mutate((draft) => { draft.name = e.target.value })}
      />
    </div>
  )
}

/* ── 变量声明(条件/试运行/词汇点选的数据源) ─────────────────── */

const VAR_TYPE_LABELS: Record<ProcessVariable['type'], string> = {
  string: '文本',
  number: '数字',
  boolean: '是/否',
  list: '列表',
}

function VariablesSection({ spec }: { spec: Spec }) {
  const mutate = useStore((s) => s.mutate)
  const variables = spec.variables ?? []
  const setVar = (index: number, patch: Partial<ProcessVariable>) => {
    mutate((draft) => {
      draft.variables = [...(draft.variables ?? [])]
      draft.variables[index] = { ...draft.variables[index], ...patch }
    })
  }
  const removeVar = (index: number) => {
    mutate((draft) => {
      draft.variables = (draft.variables ?? []).filter((_, i) => i !== index)
    })
  }
  const addVar = () => {
    mutate((draft) => {
      draft.variables = [...(draft.variables ?? []), { name: '', type: 'string', label: '' }]
    })
  }
  return (
    <div className="field">
      <div className="field-label">
        <span>流程变量</span>
        <span className="opt">条件/试运行/词汇点选的数据源</span>
      </div>
      {variables.length === 0 && (
        <div className="empty-sub">
          未声明变量。声明后:连线条件可直接点选变量,「试运行」用样例值预演路由。
        </div>
      )}
      {variables.map((v, i) => (
        <div key={i} className="var-row">
          <input
            className="var-name"
            value={v.name}
            placeholder="变量名(如 amount)"
            onChange={(e) => setVar(i, { name: e.target.value.trim() })}
          />
          <select
            value={v.type}
            onChange={(e) => setVar(i, { type: e.target.value as ProcessVariable['type'] })}
          >
            {(Object.keys(VAR_TYPE_LABELS) as ProcessVariable['type'][]).map((t) => (
              <option key={t} value={t}>{VAR_TYPE_LABELS[t]}</option>
            ))}
          </select>
          <input
            className="var-label"
            value={v.label ?? ''}
            placeholder="显示名(可选)"
            onChange={(e) => setVar(i, { label: e.target.value })}
          />
          <VariableSampleInput value={v} onChange={(sample) => setVar(i, { sample })} />
          <button className="mini-btn" title="删除变量" onClick={() => removeVar(i)}>×</button>
        </div>
      ))}
      <button className="add-group" onClick={addVar}>＋ 添加变量</button>
    </div>
  )
}

/** 样例值输入:按变量类型给对应控件(string 文本 / number 数字 / boolean 下拉 / list JSON) */
function VariableSampleInput({ value, onChange }: {
  value: ProcessVariable
  onChange: (sample: unknown) => void
}) {
  if (value.type === 'boolean') {
    return (
      <select
        className="var-sample"
        value={value.sample === true ? 'true' : 'false'}
        onChange={(e) => onChange(e.target.value === 'true')}
        title="试运行样例值"
      >
        <option value="false">否</option>
        <option value="true">是</option>
      </select>
    )
  }
  if (value.type === 'number') {
    return (
      <input
        className="var-sample"
        type="number"
        value={value.sample == null ? '' : String(value.sample)}
        placeholder="样例值"
        onChange={(e) => onChange(e.target.value === '' ? undefined : Number(e.target.value))}
      />
    )
  }
  if (value.type === 'list') {
    return (
      <input
        className="var-sample"
        value={Array.isArray(value.sample) ? JSON.stringify(value.sample) : ''}
        placeholder='样例值,如 ["a","b"]'
        onChange={(e) => {
          try {
            const parsed = JSON.parse(e.target.value)
            onChange(Array.isArray(parsed) ? parsed : undefined)
          } catch {
            onChange(undefined)
          }
        }}
      />
    )
  }
  return (
    <input
      className="var-sample"
      value={value.sample == null ? '' : String(value.sample)}
      placeholder="样例值"
      onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value)}
    />
  )
}

/* ── 折叠节(共用外壳) ─────────────────────────────────────── */

function Collapsible({ title, hint, defaultOpen = false, children }: {
  title: string
  hint?: string
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="ps-section">
      <button type="button" className="ps-section-head" onClick={() => setOpen(!open)}>
        <span className={`chevron ${open ? 'open' : ''}`}>▸</span>
        <span>{title}</span>
        {hint && <span className="opt">{hint}</span>}
      </button>
      {open && <div className="ps-section-body">{children}</div>}
    </div>
  )
}

/* ── 入参契约(input JSON Schema;发起/提交/节点输出三边界按它校验) ── */

const VAR_TYPE_TO_JSON: Record<ProcessVariable['type'], string> = {
  string: 'string',
  number: 'number',
  boolean: 'boolean',
  list: 'array',
}

function InputContractSection({ spec }: { spec: Spec }) {
  const mutate = useStore((s) => s.mutate)
  const showToast = useStore((s) => s.showToast)
  const text = spec.input ? JSON.stringify(spec.input, null, 2) : ''
  const [draft, setDraft] = useState(text)
  const [bad, setBad] = useState(false)
  const apply = (value: string) => {
    setDraft(value)
    if (!value.trim()) {
      mutate((d) => { d.input = null })
      setBad(false)
      return
    }
    try {
      const parsed = JSON.parse(value)
      mutate((d) => { d.input = parsed })
      setBad(false)
    } catch {
      setBad(true)
    }
  }
  const skeleton = () => {
    const props: Record<string, unknown> = {}
    for (const v of spec.variables ?? []) {
      if (v.name) props[v.name] = { type: VAR_TYPE_TO_JSON[v.type] }
    }
    const obj = { type: 'object', properties: props }
    apply(JSON.stringify(obj, null, 2))
    showToast('已按变量声明生成契约骨架')
  }
  return (
    <Collapsible title="入参契约" hint={spec.input ? '已配置' : '未配置'}>
      <div className="field-desc">
        JSON Schema:发起时按它校验入参(三边界之一)。留空 = 不校验,随意传变量。
      </div>
      <textarea
        className={`json-area ${bad ? 'bad' : ''}`}
        rows={5}
        spellCheck={false}
        value={draft}
        placeholder='{"type":"object","properties":{"amount":{"type":"number"}}}'
        onChange={(e) => apply(e.target.value)}
      />
      {bad && <div className="json-bad">JSON 格式有误,修正后自动生效</div>}
      <button className="mini-btn" type="button" onClick={skeleton}>按变量声明生成骨架</button>
    </Collapsible>
  )
}

/* ── 实例时限(整单最大存活时间;到点按策略处置) ─────────────── */

const DEADLINE_STRATEGIES: [string, string][] = [
  ['mark', '仅打标继续跑'],
  ['cancel', '作废实例'],
  ['terminate', '终止实例'],
]

function InstanceDeadlineSection({ spec }: { spec: Spec }) {
  const mutate = useStore((s) => s.mutate)
  const deadline = spec.deadline ?? null
  const enabled = deadline != null
  return (
    <Collapsible title="实例时限" hint={enabled ? `启用 · ${deadline!.after_minutes} 分钟` : '未启用'}>
      <div className="field">
        <div className="field-label">
          <span>启用时限</span>
          <button
            type="button"
            className={`switch ${enabled ? 'on' : ''}`}
            onClick={() => mutate((d) => {
              d.deadline = enabled ? null : { after_minutes: 1440, strategy: 'mark' }
            })}
          >
            {enabled ? '已启用' : '未启用'}
          </button>
        </div>
        <div className="field-desc">
          发起时按分钟数物化截止时刻,到点按策略处置(暂停期间时钟冻结)。
        </div>
        {deadline && (
          <div className="var-row">
            <input
              type="number"
              min={1}
              value={deadline.after_minutes}
              onChange={(e) => {
                const v = Number(e.target.value)
                mutate((d) => { if (d.deadline) d.deadline.after_minutes = v > 0 ? v : 1 })
              }}
            />
            <select
              value={deadline.strategy}
              onChange={(e) => mutate((d) => {
                if (d.deadline) d.deadline.strategy = e.target.value as 'mark' | 'cancel' | 'terminate'
              })}
            >
              {DEADLINE_STRATEGIES.map(([v, label]) => (
                <option key={v} value={v}>{label}</option>
              ))}
            </select>
          </div>
        )}
      </div>
    </Collapsible>
  )
}

/* ── 消息启动(发布同名消息即发起实例;声明随定义发布生效) ────── */

function StartOnMessageSection({ spec }: { spec: Spec }) {
  const mutate = useStore((s) => s.mutate)
  const decls = spec.start_on_message ?? []
  const setDecl = (index: number, patch: Partial<StartMessageDecl>) => {
    mutate((draft) => {
      draft.start_on_message = [...(draft.start_on_message ?? [])]
      draft.start_on_message[index] = { ...draft.start_on_message[index], ...patch }
    })
  }
  const removeDecl = (index: number) => {
    mutate((draft) => {
      draft.start_on_message = (draft.start_on_message ?? []).filter((_, i) => i !== index)
    })
  }
  const addDecl = () => {
    mutate((draft) => {
      draft.start_on_message = [...(draft.start_on_message ?? []), { name: '' }]
    })
  }
  return (
    <Collapsible title="消息启动" hint={decls.length ? `${decls.length} 条声明` : '未声明'}>
      <div className="field-desc">
        发布该名消息即自动发起实例(如「报销单已提交」)。表达式求值于消息载荷(payload.*),实例尚不存在。
      </div>
      {decls.map((d, i) => (
        <div key={i} className="obj-array-item">
          <div className="obj-array-head">
            <span className="obj-array-no">#{i + 1}</span>
            <button className="mini-btn" title="删除声明" onClick={() => removeDecl(i)}>× 删除</button>
          </div>
          <div className="field">
            <div className="field-label"><span>消息名 *</span></div>
            <input
              value={d.name}
              placeholder="如 expense_submitted"
              onChange={(e) => setDecl(i, { name: e.target.value })}
            />
          </div>
          <div className="field">
            <div className="field-label"><span>关联键</span><span className="opt">可选,防串单</span></div>
            <input
              value={d.correlation_key ?? ''}
              placeholder="如 payload.order_no(载荷求值键==发布携带的 correlation_key 才命中)"
              onChange={(e) => setDecl(i, { correlation_key: e.target.value })}
            />
          </div>
          <div className="var-row">
            <input
              value={d.start_user ?? ''}
              placeholder="发起人(CEL,可选;缺省 message:消息名)"
              onChange={(e) => setDecl(i, { start_user: e.target.value })}
            />
            <input
              value={d.business_key ?? ''}
              placeholder="业务键(CEL,可选)"
              onChange={(e) => setDecl(i, { business_key: e.target.value })}
            />
          </div>
          <JsonMappingField
            label="输入映射(载荷 → 启动变量)"
            value={d.input_mappings ?? {}}
            onChange={(m) => setDecl(i, { input_mappings: m })}
          />
        </div>
      ))}
      <button className="add-group" onClick={addDecl}>＋ 添加消息启动声明</button>
    </Collapsible>
  )
}

function JsonMappingField({ label, value, onChange }: {
  label: string
  value: Record<string, string>
  onChange: (next: Record<string, string>) => void
}) {
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2))
  const [bad, setBad] = useState(false)
  return (
    <div className="field">
      <div className="field-label"><span>{label}</span></div>
      <textarea
        className={`json-area ${bad ? 'bad' : ''}`}
        rows={3}
        spellCheck={false}
        value={text}
        onChange={(e) => {
          const t = e.target.value
          setText(t)
          try {
            const parsed = JSON.parse(t || '{}')
            onChange(parsed)
            setBad(false)
          } catch {
            setBad(true)
          }
        }}
      />
      {bad && <div className="json-bad">JSON 格式有误,修正后自动生效</div>}
    </div>
  )
}
