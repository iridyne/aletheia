import { useEffect, type ReactNode } from 'react'
import { useInspector, type InspectorObject } from '../context/InspectorContext'
import EvidenceStack from './shared/EvidenceStack'

export default function RightInspector() {
  const { object, open, close } = useInspector()

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && open) close()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, close])

  if (!open || !object) return null

  const sections = buildSections(object)

  return (
    <aside className="right-inspector">
      <div className="inspector-header">
        <span className="inspector-type">{formatType(object.type)}</span>
        <button className="inspector-close" onClick={close} title="Close (Esc)">✕</button>
      </div>
      <div className="inspector-body">
        {sections.map((section, i) => (
          <section key={i} className="inspector-section">
            <h4>{section.title}</h4>
            {section.content}
          </section>
        ))}
      </div>
    </aside>
  )
}

type Section = { title: string; content: ReactNode }

function buildSections(obj: InspectorObject): Section[] {
  const d = obj.data
  const sections: Section[] = []

  // Summary
  const summary = d.summary || d.agent_rationale || d.message || d.headline || d.description || d.hypothesis || ''
  if (summary) {
    sections.push({ title: 'Summary', content: <p className="inspector-text">{summary}</p> })
  }

  // Key fields (type-specific)
  const keyFields = getKeyFields(obj)
  if (keyFields.length > 0) {
    sections.push({
      title: 'Details',
      content: (
        <div className="inspector-fields">
          {keyFields.map(([k, v]) => (
            <div key={k} className="inspector-field">
              <span className="inspector-field-key">{k}</span>
              <span className="inspector-field-val">{String(v)}</span>
            </div>
          ))}
        </div>
      ),
    })
  }

  // Evidence
  if (d.evidence || d.key_points || d.reasoning) {
    const ev = d.evidence || d.key_points || d.reasoning
    sections.push({
      title: 'Evidence / Reasoning',
      content: Array.isArray(ev)
        ? <EvidenceStack items={ev.map((item: any) => typeof item === 'string' ? { label: '', value: item } : { label: item.label || '', value: item.value || String(item), source: item.source })} />
        : <p className="inspector-text">{String(ev)}</p>,
    })
  }

  // Risk
  if (d.risk_decision || d.risk_level || d.risk_score) {
    sections.push({
      title: 'Risk',
      content: (
        <div className="inspector-fields">
          {d.risk_decision && <div className="inspector-field"><span className="inspector-field-key">decision</span><span className={`badge ${d.risk_decision === 'approved' ? 'badge-green' : 'badge-red'}`}>{d.risk_decision}</span></div>}
          {d.risk_level && <div className="inspector-field"><span className="inspector-field-key">level</span><span className="inspector-field-val">{d.risk_level}</span></div>}
          {d.risk_score !== undefined && <div className="inspector-field"><span className="inspector-field-key">score</span><span className="inspector-field-val">{d.risk_score}</span></div>}
        </div>
      ),
    })
  }

  // Status
  if (d.status) {
    sections.push({
      title: 'Status',
      content: <span className={`badge ${statusBadge(d.status)}`}>{d.status}</span>,
    })
  }

  // Timeline/timestamps
  const timestamps = getTimestamps(d)
  if (timestamps.length > 0) {
    sections.push({
      title: 'Timeline',
      content: (
        <div className="inspector-fields">
          {timestamps.map(([k, v]) => (
            <div key={k} className="inspector-field">
              <span className="inspector-field-key">{k}</span>
              <span className="inspector-field-val mono">{formatTimestamp(v)}</span>
            </div>
          ))}
        </div>
      ),
    })
  }

  // Linked objects
  const links = getLinkedObjects(d)
  if (links.length > 0) {
    sections.push({
      title: 'Linked Objects',
      content: (
        <div className="inspector-links">
          {links.map(([k, v]) => (
            <div key={k} className="inspector-link-item">
              <span className="inspector-field-key">{k}</span>
              <span className="inspector-field-val">{String(v)}</span>
            </div>
          ))}
        </div>
      ),
    })
  }

  // Raw fallback (collapsed)
  sections.push({
    title: 'Raw Data',
    content: <pre className="inspector-raw">{JSON.stringify(d, null, 2)}</pre>,
  })

  return sections
}

function getKeyFields(obj: InspectorObject): [string, any][] {
  const d = obj.data
  const fields: [string, any][] = []

  switch (obj.type) {
    case 'proposal':
      if (d.symbol) fields.push(['symbol', d.symbol])
      if (d.direction) fields.push(['direction', d.direction])
      if (d.market_type) fields.push(['market', d.market_type])
      if (d.execution_mode) fields.push(['mode', d.execution_mode])
      if (d.confidence) fields.push(['confidence', d.confidence])
      break
    case 'opportunity':
      if (d.symbol) fields.push(['symbol', d.symbol])
      if (d.bias) fields.push(['bias', d.bias])
      if (d.source) fields.push(['source', d.source])
      if (d.confidence) fields.push(['confidence', d.confidence])
      break
    case 'agent_run':
      if (d.agent_name) fields.push(['agent', d.agent_name])
      if (d.trigger_type) fields.push(['trigger', d.trigger_type])
      if (d.duration_sec) fields.push(['duration', `${d.duration_sec}s`])
      break
    case 'strategy':
      if (d.name) fields.push(['name', d.name])
      if (d.state) fields.push(['state', d.state])
      if (d.symbol) fields.push(['symbol', d.symbol])
      break
    case 'trade':
    case 'position':
      if (d.symbol) fields.push(['symbol', d.symbol])
      if (d.side) fields.push(['side', d.side])
      if (d.size) fields.push(['size', d.size])
      if (d.pnl) fields.push(['pnl', d.pnl])
      break
    default:
      if (d.event_type) fields.push(['event_type', d.event_type])
      if (d.source) fields.push(['source', d.source])
      if (d.actor) fields.push(['actor', d.actor])
  }
  return fields
}

function getTimestamps(d: Record<string, any>): [string, string][] {
  const keys = ['created_at', 'started_at', 'ended_at', 'updated_at', 'timestamp', 'published_at']
  return keys.filter(k => d[k]).map(k => [k.replace('_at', '').replace('_', ' '), d[k]])
}

function getLinkedObjects(d: Record<string, any>): [string, any][] {
  const links: [string, any][] = []
  if (d.strategy_id) links.push(['strategy', d.strategy_id])
  if (d.proposal_id) links.push(['proposal', d.proposal_id])
  if (d.opportunity_id) links.push(['opportunity', d.opportunity_id])
  if (d.linked_opportunity_ids?.length) links.push(['opportunities', d.linked_opportunity_ids.join(', ')])
  if (d.linked_strategy_ids?.length) links.push(['strategies', d.linked_strategy_ids.join(', ')])
  return links
}

function formatTimestamp(iso: string): string {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function formatType(type: string): string {
  return type.replace(/_/g, ' ')
}

function statusBadge(status: string): string {
  if (['executed', 'approved', 'running', 'open', 'passed', 'completed'].includes(status)) return 'badge-green'
  if (['rejected', 'failed', 'blocked', 'stopped', 'error'].includes(status)) return 'badge-red'
  if (['pending', 'draft', 'warning', 'paused'].includes(status)) return 'badge-yellow'
  return 'badge-blue'
}
