/** 右侧面板:属性(schema 驱动、分组)/ Spec JSON / 校验 三个 Tab;空选态 = 流程设置 */

import { useEffect, useMemo, useState } from 'react'
import { conditionSummary } from '../adapter'
import { parseEdgeId, type ProcessVariable, type Spec } from '../types'
import { sampleVariables } from '../api'
import { api } from '../api'
import { useStore } from '../store'
import { SchemaForm, requiredFieldErrors } from './SchemaForm'
import { Icon } from './FlowNodes'

export function RightPanel() {
  const tab = useStore((s) => s.rightTab)
  const setTab = useStore((s) => s.setRightTab)
  return (
    <aside className="right-panel">
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'props'} className={tab === 'props' ? 'on' : ''} onClick={() => setTab('props')}>属性</button>
        <button role="tab" aria-selected={tab === 'json'} className={tab === 'json' ? 'on' : ''} onClick={() => setTab('json')}>Spec JSON</button>
        <button role="tab" aria-selected={tab === 'issues'} className={tab === 'issues' ? 'on' : ''} onClick={() => setTab('issues')}>校验<IssueCount /></button>
      </div>
      <div className="panel-body">
        {tab === 'props' && <PropsTab />}
        {tab === 'json' && <JsonTab />}
        {tab === 'issues' && <IssuesTab />}
      </div>
    </aside>
  )
}

function IssueCount() {
  const issues = useStore((s) => s.issues)
  const errors = issues.filter((i) => i.level === 'error').length
  if (!issues.length) return null
  return <span className={`issue-count ${errors ? 'bad' : ''}`}>{issues.length}</span>
}

/* ── 属性 Tab ─────────────────────────────────────────── */

function PropsTab() {
  const selection = useStore((s) => s.selection)
  const spec = useStore((s) => s.spec)
  const metaByKind = useStore((s) => s.metaByKind)
  const issues = useStore((s) => s.issues)
  const mutate = useStore((s) => s.mutate)
  const deleteSelection = useStore((s) => s.deleteSelection)
  const setRightTab = useStore((s) => s.setRightTab)

  if (!spec) return null
  if (!selection) {
    return <ProcessSettings spec={spec} />
  }

  if (selection.type === 'edge') {
    const { source, target } = parseEdgeId(selection.id)
    const flow = spec.flows.find((f) => f.source === source && f.target === target)
    if (!flow) return null
    const edgeIssues = issues.filter((i) => i.flow === `${source}->${target}`)
    return (
      <div>
        <PanelHead
          icon="↝"
          title={`连线 · ${source} → ${target}`}
          code={conditionSummary(flow) || '无条件'}
          onDelete={deleteSelection}
          deleteLabel="删除连线"
        />
        <IssueInline issues={edgeIssues} onOpen={() => setRightTab('issues')} />
        <EdgeForm
          key={selection.id}
          flow={flow}
          onChange={(patch) => mutate((draft) => {
            const f = draft.flows.find((x) => x.source === source && x.target === target)
            if (f) Object.assign(f, patch)
          })}
        />
        <div className="panel-note">
          条件挂在出边。条件分支按条件取路,全部不满足走「默认」边。
        </div>
      </div>
    )
  }

  const node = spec.nodes.find((n) => n.code === selection.id)
  if (!node) return null
  const meta = metaByKind[node.kind]
  const schema = meta?.schema
  const exclude = [...(meta?.form_exclude ?? ['code', 'kind', 'hooks', 'properties']), 'advance_policy']
  const incoming = spec.flows.filter((f) => f.target === node.code)
  const nodeIssues = issues.filter((i) => i.node === node.code)

  // 字段级内联错误(必填空值),与引擎校验同源的 schema 计算
  const fieldErrors = schema ? requiredFieldErrors(schema, schema.$defs ?? {}, node, exclude) : {}

  return (
    <div>
      <PanelHead
        icon={meta?.ui?.icon}
        color={meta?.ui?.color}
        title={node.name || node.code}
        code={`${node.code} · kind: ${node.kind}`}
        onDelete={deleteSelection}
        deleteLabel="删除节点"
      />
      <IssueInline issues={nodeIssues} onOpen={() => setRightTab('issues')} />
      <div className="field">
        <div className="field-label"><span>activity_code</span></div>
        <input value={node.code} disabled />
      </div>
      {incoming.length >= 2 && (
        <AdvancePolicyField
          node={node}
          disabled={node.kind === 'cc_task'}
          onChange={(patch) => mutate((draft) => {
            const target = draft.nodes.find((n) => n.code === node.code)
            if (target) Object.assign(target, patch)
          })}
        />
      )}
      {schema ? (
        <SchemaForm
          schema={schema}
          defs={schema.$defs ?? {}}
          exclude={exclude}
          groups={meta?.ui?.form_groups ?? null}
          errors={fieldErrors}
          value={node}
          onChange={(next) => mutate((draft) => {
            const target = draft.nodes.find((n) => n.code === node.code)
            if (target) Object.assign(target, next)
          })}
        />
      ) : (
        <div className="form-empty">未注册的节点类型:{node.kind}</div>
      )}
      <div className="panel-note">◉ 表单由 <b>{node.kind}Spec</b> 的 JSON Schema 自动生成 — 字段与引擎校验同源。</div>
    </div>
  )
}

/* ── 校验问题内联(字段级反馈的节点/连线入口,可跳校验页) ── */

function IssueInline({ issues, onOpen }: {
  issues: { level: 'error' | 'warning'; rule: string; message: string }[]
  onOpen: () => void
}) {
  if (!issues.length) return null
  return (
    <div className={`issue-inline ${issues.some((i) => i.level === 'error') ? 'bad' : 'warn'}`}>
      {issues.slice(0, 3).map((issue, i) => (
        <div key={i} className="issue-inline-row">
          <span className="issue-badge">{issue.level === 'error' ? '!' : '⚠'}</span>
          <span>{issue.message}</span>
        </div>
      ))}
      {issues.length > 3 && (
        <button className="issue-inline-more" onClick={onOpen}>还有 {issues.length - 3} 条,查看全部</button>
      )}
    </div>
  )
}

/* ── 流程设置(空选态):流程名称 + 流程变量声明 ─────────── */

const VAR_TYPE_LABELS: Record<ProcessVariable['type'], string> = {
  string: '文本',
  number: '数字',
  boolean: '是/否',
  list: '列表',
}

function ProcessSettings({ spec }: { spec: Spec }) {
  const mutate = useStore((s) => s.mutate)
  const openDryRun = useStore((s) => s.openDryRun)
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
    <div>
      <PanelHead icon="⌂" title="流程设置" code={spec.code} onDelete={() => {}} deleteLabel="" />
      <div className="field">
        <div className="field-label"><span>流程名称</span></div>
        <input
          value={spec.name}
          placeholder="给流程起个名字"
          onChange={(e) => mutate((draft) => { draft.name = e.target.value })}
        />
      </div>
      <div className="field">
        <div className="field-label">
          <span>流程变量</span>
          <span className="opt">条件/试运行/变量选择器的数据源</span>
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
      <button className="btn btn-block" onClick={openDryRun}>▶ 试运行(按样例值预演路由)</button>
      <div className="panel-note">
        点击画布中的节点或连线编辑其属性;变量声明随 Spec 保存,与 Spec JSON Tab 同源。
      </div>
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

/* ── 开始条件：仅多条入边节点显示（advance_policy any/all）── */

function AdvancePolicyField({ node, disabled, onChange }: {
  node: Spec['nodes'][number]
  disabled: boolean
  onChange: (patch: Partial<Spec['nodes'][number]>) => void
}) {
  const value = (node.advance_policy as string | null | undefined) ?? 'any'
  const options: [string, string, string][] = [
    ['any', '任一前置分支到达即开始', '竞速：先到的分支开始本节点，其余在办分支自动取消'],
    ['all', '所有前置分支到齐再开始', '等齐：本节点等待全部入边来源完成后才激活（并行汇合）'],
  ]
  return (
    <div className="field advance-field">
      <div className="field-label">
        <span>开始条件</span>
        <span className="opt">{disabled ? '传阅节点恒按「任一到达」执行' : '多条入边时的推进策略'}</span>
      </div>
      <div className="advance-options">
        {options.map(([mode, title, desc]) => (
          <button
            key={mode}
            type="button"
            disabled={disabled}
            className={`advance-option ${value === mode ? 'on' : ''}`}
            onClick={() => onChange({ advance_policy: mode })}
          >
            <span className="advance-title">{title}</span>
            <span className="advance-desc">{desc}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

function PanelHead({ icon, color, title, code, onDelete, deleteLabel }: {
  icon?: string; color?: string; title: string; code: string; onDelete: () => void; deleteLabel: string
}) {
  return (
    <div className="panel-head">
      <div className="panel-head-row">
        <span className="panel-ico" style={color ? { background: `${color}1a`, color } : undefined}>
          {icon ? <Icon name={icon} /> : '↝'}
        </span>
        <span className="panel-title" title={title}>{title}</span>
      </div>
      <div className="panel-code">{code}</div>
      {deleteLabel && <button className="btn btn-delete" onClick={onDelete}>{deleteLabel}</button>}
    </div>
  )
}

/* ── 连线编辑:name / 默认边 / CEL 公式(单轨) ────────── */

function EdgeForm({ flow, onChange }: {
  flow: Spec['flows'][number]
  onChange: (patch: Partial<Spec['flows'][number]>) => void
}) {
  const spec = useStore((s) => s.spec)
  const showToast = useStore((s) => s.showToast)
  const formula = typeof flow.condition === 'string' ? flow.condition : ''
  const [preview, setPreview] = useState<{ ok: boolean; text: string } | null>(null)

  const setFormula = (text: string) => {
    onChange({ condition: text.trim() ? text : null })
  }

  const runPreview = async () => {
    if (!formula.trim()) {
      setPreview({ ok: false, text: '先填写公式' })
      return
    }
    try {
      const result = await api.formulaPreview(formula, sampleVariables(spec?.variables))
      setPreview(
        result.ok
          ? { ok: true, text: `按样例值试算结果:${result.result}` }
          : { ok: false, text: result.error ?? '试算失败' },
      )
    } catch (err) {
      setPreview({ ok: false, text: `试算失败:${err instanceof Error ? err.message : String(err)}` })
    }
    showToast('公式试算完成')
  }

  return (
    <div className="edge-form">
      <div className="field">
        <div className="field-label"><span>连线名称</span></div>
        <input value={flow.name ?? ''} onChange={(e) => onChange({ name: e.target.value })} placeholder="可选" />
      </div>
      <div className="field">
        <div className="field-label">
          <span>默认边</span>
          <button
            type="button"
            className={`switch ${flow.is_default ? 'on' : ''}`}
            onClick={() => onChange({ is_default: !flow.is_default })}
          >
            {flow.is_default ? '是默认边' : '否'}
          </button>
        </div>
        <div className="field-desc">条件分支的条件全不满足时走默认边;默认边不能再挂条件。</div>
      </div>

      <div className="field">
        <div className="field-label"><span>条件公式(CEL)</span></div>
        <div className="formula-area">
          <div className="field-desc">
            变量直接写名字;逻辑用 && / || / !,布尔 true/false,列表 in [1, 2],长度 x.size();
            发起人内置标识符 initiator。
          </div>
          <textarea
            className="json-area"
            rows={3}
            spellCheck={false}
            value={formula}
            placeholder={'如 amount > 10000 && level == "L2"'}
            onChange={(e) => setFormula(e.target.value)}
          />
          <div className="formula-actions">
            <button className="mini-btn" type="button" onClick={() => void runPreview()}>▶ 按样例值试算</button>
          </div>
          {preview && (
            <div className={`formula-preview ${preview.ok ? 'ok' : 'bad'}`}>{preview.text}</div>
          )}
        </div>
      </div>
    </div>
  )
}

/* ── Spec JSON Tab ────────────────────────────────────── */

function JsonTab() {
  const spec = useStore((s) => s.spec)
  const applySpecJson = useStore((s) => s.applySpecJson)
  const [text, setText] = useState('')

  const serialized = useMemo(() => (spec ? JSON.stringify(spec, null, 2) : ''), [spec])
  useEffect(() => { setText(serialized) }, [serialized])

  if (!spec) return null
  return (
    <div className="json-tab">
      <div className="json-actions">
        <span className="json-hint">唯一事实源 — 直接改这份 JSON 点「应用」,画布随之重绘(CLI/AI 写路径与你看到的同构)</span>
        <button className="btn btn-sm" onClick={() => setText(serialized)}>重置</button>
        <button className="btn btn-sm btn-primary" onClick={() => applySpecJson(text)}>应用变更</button>
      </div>
      <textarea className="json-editor" value={text} spellCheck={false} onChange={(e) => setText(e.target.value)} />
    </div>
  )
}

/* ── 校验 Tab ─────────────────────────────────────────── */

function IssuesTab() {
  const issues = useStore((s) => s.issues)
  const spec = useStore((s) => s.spec)
  const select = useStore((s) => s.select)
  const validateNow = useStore((s) => s.validateNow)
  const pending = useStore((s) => s.saveState)

  if (!spec) return null
  return (
    <div className="issues-tab">
      <div className="issues-toolbar">
        <span className={`issues-verdict ${issues.every((i) => i.level !== 'error') ? 'ok' : 'bad'}`}>
          {issues.length === 0 ? '✓ 校验通过,无问题' : issues.every((i) => i.level !== 'error') ? '○ 无错误,有改进建议' : `✕ ${issues.filter((i) => i.level === 'error').length} 个错误,禁止发布`}
        </span>
        <button className="btn btn-sm" onClick={() => void validateNow()} disabled={pending === 'saving'}>重新校验</button>
      </div>
      {issues.map((issue, i) => (
        <button
          key={i}
          className={`issue-item ${issue.level}`}
          onClick={() => {
            if (issue.node) select({ type: 'node', id: issue.node })
            else if (issue.flow) select({ type: 'edge', id: issue.flow })
          }}
        >
          <span className="issue-badge">{issue.level === 'error' ? '!' : '⚠'}</span>
          <span className="issue-texts">
            <span className="issue-msg">{issue.message}</span>
            <span className="issue-meta">{issue.rule}{issue.node ? ` · 节点 ${issue.node}` : ''}{issue.flow ? ` · 连线 ${issue.flow}` : ''}</span>
          </span>
        </button>
      ))}
      {issues.length === 0 && <div className="empty-sub">修改任意配置后会自动重新校验(引擎侧 repository.validate)。</div>}
    </div>
  )
}
