/** 右侧面板:属性(schema 驱动)/ Spec JSON / 校验 三个 Tab */

import { useEffect, useMemo, useState } from 'react'
import { OPERATOR_OPTIONS, conditionSummary } from '../adapter'
import { parseEdgeId, type ConditionItem, type Spec } from '../types'
import { useStore } from '../store'
import { SchemaForm } from './SchemaForm'
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
  const mutate = useStore((s) => s.mutate)
  const deleteSelection = useStore((s) => s.deleteSelection)

  if (!spec) return null
  if (!selection) {
    return (
      <div className="empty-hint">
        <div className="empty-big">⬡</div>
        点击画布中的节点或连线编辑属性。
        <div className="empty-sub">
          添加节点:点左侧调色板 · 建连线:点击节点右侧圆点,再点击目标节点
        </div>
      </div>
    )
  }

  if (selection.type === 'edge') {
    const { source, target } = parseEdgeId(selection.id)
    const flow = spec.flows.find((f) => f.source === source && f.target === target)
    if (!flow) return null
    return (
      <div>
        <PanelHead
          icon="↝"
          title={`连线 · ${source} → ${target}`}
          code={conditionSummary(flow) || '无条件'}
          onDelete={deleteSelection}
          deleteLabel="删除连线"
        />
        <EdgeForm
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
      <button className="btn btn-delete" onClick={onDelete}>{deleteLabel}</button>
    </div>
  )
}

/* ── 连线编辑:name / 默认边 / 条件组 / 公式 ───────────── */

function EdgeForm({ flow, onChange }: {
  flow: Spec['flows'][number]
  onChange: (patch: Partial<Spec['flows'][number]>) => void
}) {
  const condition = flow.condition ?? { groups: [] }
  const groups = condition.groups ?? []

  const setGroups = (next: ConditionItem[][]) => {
    onChange({ condition: next.length ? { groups: next } : null })
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
        <div className="field-label">
          <span>条件组</span>
          <span className="opt">组间 OR · 组内 AND</span>
        </div>
        {groups.map((group, gi) => (
          <div key={gi} className="cond-group">
            <div className="cond-group-head">
              <span>组 {gi + 1}</span>
              <button className="mini-btn" onClick={() => setGroups(groups.filter((_, i) => i !== gi))}>删除组</button>
            </div>
            {group.map((item, ii) => (
              <div key={ii} className="cond-row">
                <input
                  className="cond-left"
                  value={item.left}
                  placeholder="变量,如 amount"
                  onChange={(e) => {
                    const next = groups.map((g, i) => i !== gi ? g : g.map((it, j) => j !== ii ? it : { ...it, left: e.target.value }))
                    setGroups(next)
                  }}
                />
                <select
                  value={item.operator}
                  onChange={(e) => {
                    const next = groups.map((g, i) => i !== gi ? g : g.map((it, j) => j !== ii ? it : { ...it, operator: e.target.value }))
                    setGroups(next)
                  }}
                >
                  {OPERATOR_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
                {!['is_empty', 'is_not_empty'].includes(item.operator) && (
                  <input
                    className="cond-right"
                    value={Array.isArray(item.right) ? (item.right as unknown[]).join(',') : String(item.right ?? '')}
                    placeholder="值(逗号分隔列表)"
                    onChange={(e) => {
                      const raw = e.target.value
                      const isList = ['in', 'not_in'].includes(item.operator)
                      const right = isList ? raw.split(',').map((s) => s.trim()).filter(Boolean) : raw
                      const asNum = !isList && raw !== '' && !Number.isNaN(Number(raw)) ? Number(raw) : right
                      const next = groups.map((g, i) => i !== gi ? g : g.map((it, j) => j !== ii ? it : { ...it, right: asNum }))
                      setGroups(next)
                    }}
                  />
                )}
                <button className="mini-btn" title="删除该条件" onClick={() => {
                  const nextGroup = group.filter((_, j) => j !== ii)
                  setGroups(groups.map((g, i) => i !== gi ? g : nextGroup).filter((g) => g.length))
                }}>×</button>
              </div>
            ))}
            <button className="add-cond" onClick={() => {
              const next = groups.map((g, i) => i !== gi ? g : [...g, { left: '', operator: 'eq', right: '' }])
              setGroups(next)
            }}>＋ 条件(AND)</button>
          </div>
        ))}
        <button className="add-group" onClick={() => setGroups([...groups, [{ left: '', operator: 'gt', right: '' }]])}>
          ＋ 添加条件组(OR)
        </button>
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
