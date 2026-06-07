import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

type ActivityItem = {
  id: string
  timestamp: string
  type: 'agent_run' | 'audit_event'
  icon: string
  title: string
  subtitle?: string
  status?: string
}

type Filter = 'all' | 'agent' | 'risk' | 'trade' | 'system'

function normalizeRuns(runs: any[]): ActivityItem[] {
  return runs.map(r => ({
    id: `run-${r.id}`,
    timestamp: r.started_at,
    type: 'agent_run' as const,
    icon: '◈',
    title: `${r.agent_name} ${r.status === 'completed' ? 'completed' : r.status}`,
    subtitle: r.outputs_created ? `outputs: ${JSON.parse(r.outputs_created || '[]').length}` : undefined,
    status: r.status,
  }))
}

function normalizeEvents(events: any[]): ActivityItem[] {
  return events.map(e => ({
    id: `evt-${e.id}`,
    timestamp: e.created_at,
    type: 'audit_event' as const,
    icon: e.event_type?.includes('risk') ? '⚠' : e.event_type?.includes('propos') ? '◆' : '•',
    title: (e.event_type || '').replace(/_/g, ' '),
    subtitle: e.details || e.entity_type,
    status: undefined,
  }))
}

function filterItems(items: ActivityItem[], filter: Filter): ActivityItem[] {
  if (filter === 'all') return items
  if (filter === 'agent') return items.filter(i => i.type === 'agent_run')
  if (filter === 'risk') return items.filter(i => i.title.includes('risk') || i.title.includes('kill'))
  if (filter === 'trade') return items.filter(i => i.title.includes('propos') || i.title.includes('order') || i.title.includes('trade'))
  return items.filter(i => i.type === 'audit_event')
}

function formatTime(ts: string): string {
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch { return '—' }
}

function statusColor(status?: string): string {
  if (!status) return ''
  if (status === 'completed') return 'var(--green)'
  if (status === 'error' || status === 'failed') return 'var(--red)'
  if (status === 'running') return 'var(--yellow)'
  return ''
}

export default function ActivityTimeline() {
  const [collapsed, setCollapsed] = useState(false)
  const [filter, setFilter] = useState<Filter>('all')

  const { data: runs = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns })
  const { data: events = [] } = useQuery({ queryKey: ['audit-events'], queryFn: () => api.getAuditEvents(30) })

  const all = [...normalizeRuns(runs.slice(0, 20)), ...normalizeEvents(events)]
    .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())

  const items = filterItems(all, filter)
  const FILTERS: Filter[] = ['all', 'agent', 'risk', 'trade', 'system']

  return (
    <div className={`activity-timeline ${collapsed ? 'collapsed' : ''}`}>
      <div className="activity-timeline-header" onClick={() => setCollapsed(c => !c)}>
        <div className="flex items-center gap-2">
          <span style={{ fontSize: '0.7rem', color: 'var(--accent)' }}>▶</span>
          <span style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--muted)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
            Activity
          </span>
          <span className="badge badge-blue" style={{ fontSize: '0.6rem', padding: '0.05rem 0.35rem' }}>
            {items.length}
          </span>
        </div>
        <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
          {!collapsed && FILTERS.map(f => (
            <button
              key={f}
              className={f === filter ? '' : 'secondary'}
              style={{ padding: '0.15rem 0.4rem', fontSize: '0.6rem' }}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
          <span style={{ fontSize: '0.75rem', color: 'var(--faint)', cursor: 'pointer' }}>
            {collapsed ? '▲' : '▼'}
          </span>
        </div>
      </div>
      {!collapsed && (
        <div className="activity-timeline-body">
          {items.length === 0 && (
            <div style={{ padding: '1rem', color: 'var(--faint)', fontSize: '0.78rem', textAlign: 'center' }}>
              No activity yet
            </div>
          )}
          {items.map(item => (
            <div key={item.id} className="timeline-event">
              <span className="timeline-ts">{formatTime(item.timestamp)}</span>
              <span style={{ color: statusColor(item.status) || 'var(--accent)', fontSize: '0.75rem' }}>{item.icon}</span>
              <span style={{ flex: 1, fontSize: '0.78rem' }}>{item.title}</span>
              {item.subtitle && <span style={{ fontSize: '0.7rem', color: 'var(--faint)' }}>{item.subtitle}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
