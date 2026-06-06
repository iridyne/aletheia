import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useInspector } from '../context/InspectorContext'

type FilterCategory = 'all' | 'agent' | 'risk' | 'trade' | 'system'

export default function BottomTimeline() {
  const [collapsed, setCollapsed] = useState(false)
  const [filter, setFilter] = useState<FilterCategory>('all')
  const { setObject } = useInspector()
  const { data: events = [] } = useQuery({
    queryKey: ['activity-feed'],
    queryFn: () => api.getActivityFeed(30),
    refetchInterval: 10000,
  })

  const filtered = filter === 'all'
    ? events
    : (events as any[]).filter(e => e.category === filter)

  return (
    <div className={`bottom-timeline ${collapsed ? 'bt-collapsed' : ''}`}>
      <div className="bt-header">
        <div className="bt-left">
          <span className="bt-title">Activity Timeline</span>
          {!collapsed && (
            <div className="bt-filters">
              {(['all', 'agent', 'risk', 'trade', 'system'] as FilterCategory[]).map(cat => (
                <button
                  key={cat}
                  className={`bt-filter ${filter === cat ? 'bt-filter-active' : ''}`}
                  onClick={() => setFilter(cat)}
                >
                  {cat}
                </button>
              ))}
            </div>
          )}
        </div>
        <button className="bt-toggle" onClick={() => setCollapsed(c => !c)}>
          {collapsed ? '▲' : '▼'}
        </button>
      </div>
      {!collapsed && (
        <div className="bt-body">
          {filtered.length === 0 ? (
            <p className="bt-placeholder">No activity yet.</p>
          ) : (
            <div className="bt-events">
              {(filtered as any[]).map((evt: any, i: number) => (
                <div
                  key={evt.id || i}
                  className="bt-event"
                  onClick={() => setObject({
                    type: evt.type === 'agent' ? 'agent_run' : 'activity',
                    id: evt.id,
                    data: evt.data || evt,
                  })}
                >
                  <span className="bt-time">{formatTime(evt.timestamp)}</span>
                  <span className={`bt-cat bt-cat-${evt.category}`}>{catIcon(evt.category)}</span>
                  <span className="bt-summary">{evt.summary}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function formatTime(iso: string): string {
  if (!iso) return '--:--'
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return '--:--'
  }
}

function catIcon(category: string): string {
  switch (category) {
    case 'agent': return '◈'
    case 'risk': return '⚠'
    case 'trade': return '◆'
    case 'system': return '⚙'
    default: return '●'
  }
}
