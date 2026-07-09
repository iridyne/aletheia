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

function toUtc(ts: string): string {
  return ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z'
}

function formatTime(ts: string): string {
  // #173 fix: previously rendered HH:MM only, so a stale timeline (last event
  // from 3 days ago) looked as if it happened at that clock time today. When
  // the event is from a past date, prefix the month/day so staleness is
  // visible at a glance; same-day events keep the tight HH:MM to avoid noise.
  try {
    const d = new Date(toUtc(ts))
    const now = new Date()
    const sameDay =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate()
    if (sameDay) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    }
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) +
      ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch { return '—' }
}

function statusColor(status?: string): string {
  if (!status) return ''
  if (status === 'completed') return 'var(--green)'
  if (status === 'error' || status === 'failed') return 'var(--red)'
  if (status === 'running') return 'var(--yellow)'
  return ''
}

const COLLAPSE_KEY = 'aletheia.activityCollapsed'

export default function ActivityTimeline() {
  // Collapsed by default so the main area isn't starved of vertical space (C3).
  // Persist the user's preference so expanding it sticks across reloads.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(COLLAPSE_KEY) !== 'false' } catch { return true }
  })
  const [filter, setFilter] = useState<Filter>('all')

  function toggleCollapsed() {
    setCollapsed(c => {
      const next = !c
      try { localStorage.setItem(COLLAPSE_KEY, String(next)) } catch { /* ignore */ }
      return next
    })
  }

  const { data: runs = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns })
  const { data: events = [] } = useQuery({ queryKey: ['audit-events'], queryFn: () => api.getAuditEvents(30) })

  const all = [...normalizeRuns(runs.slice(0, 20)), ...normalizeEvents(events)]
    .sort((a, b) => new Date(toUtc(b.timestamp)).getTime() - new Date(toUtc(a.timestamp)).getTime())

  const items = filterItems(all, filter)
  const FILTERS: Filter[] = ['all', 'agent', 'risk', 'trade', 'system']

  return (
    <div className={`activity-timeline ${collapsed ? 'collapsed' : ''}`}>
      <div className="activity-timeline-header" onClick={toggleCollapsed}>
        <div className="flex items-center gap-2">
          <span className="at-chev">{collapsed ? '▶' : '▼'}</span>
          <span className="at-title">Activity</span>
          <span className="badge badge-blue at-count">{items.length}</span>
        </div>
        <div className="flex items-center gap-2" onClick={e => e.stopPropagation()}>
          {!collapsed && FILTERS.map(f => (
            <button
              key={f}
              className={`at-filter ${f === filter ? '' : 'secondary'}`}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>
      </div>
      {!collapsed && (
        <div className="activity-timeline-body">
          {items.length === 0 && (
            <div className="at-empty">No activity yet</div>
          )}
          {items.map(item => (
            <div key={item.id} className="timeline-event">
              <span className="timeline-ts">{formatTime(item.timestamp)}</span>
              <span className="at-icon" style={{ color: statusColor(item.status) || 'var(--accent)' }}>{item.icon}</span>
              <span className="at-event-title">{item.title}</span>
              {item.subtitle && <span className="at-event-sub">{item.subtitle}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
