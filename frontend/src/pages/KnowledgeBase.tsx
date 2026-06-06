import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import StatusPill from '../components/shared/StatusPill'
import { useInspector } from '../context/InspectorContext'

const TABS = [
  { key: 'research', label: 'Research Notes', fn: () => api.getKnowledge() },
  { key: 'strategy', label: 'Strategy Memory', fn: () => api.getStrategyMemory() },
  { key: 'reviews', label: 'Daily Reviews', fn: () => api.getKnowledge('daily_review') },
  { key: 'dreaming', label: 'Dreaming Logs', fn: () => api.getDreamingLogs() },
] as const

export default function KnowledgeBase() {
  const [tab, setTab] = useState<typeof TABS[number]['key']>('research')
  const { setObject } = useInspector()
  const active = TABS.find(t => t.key === tab)!
  const { data = [] } = useQuery({ queryKey: ['knowledge', tab], queryFn: active.fn })
  const rows = data as any[]

  return (
    <div>
      <h2 className="page-title">Knowledge Base</h2>
      <div className="tab-bar">
        {TABS.map(t => (
          <button key={t.key} className={`tab-btn ${tab === t.key ? 'tab-active' : ''}`} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>
      <div className="card">
        {rows.length === 0 ? <p className="text-muted">No {active.label.toLowerCase()} yet.</p> : (
          <table>
            <thead><tr><th>Title</th><th>Confidence</th><th>Status</th><th>Created</th></tr></thead>
            <tbody>
              {rows.map((k: any) => (
                <tr key={k.id} onClick={() => setObject({ type: 'research_note', id: k.id, data: k })}>
                  <td>{k.title}</td>
                  <td>{k.confidence}</td>
                  <td><StatusPill status={k.status || 'active'} /></td>
                  <td className="text-faint font-sm">{k.created_at?.slice(0, 16)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
