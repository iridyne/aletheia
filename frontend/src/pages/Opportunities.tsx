import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import StatusPill from '../components/shared/StatusPill'
import { useInspector } from '../context/InspectorContext'

const FILTERS = ['all', 'new', 'evaluated', 'converted', 'expired'] as const
type Filter = typeof FILTERS[number]

export default function Opportunities() {
  const [filter, setFilter] = useState<Filter>('all')
  const { setObject } = useInspector()
  const { data = [] } = useQuery({ queryKey: ['opportunities'], queryFn: api.getOpportunities })

  const rows = (data as any[]).filter(o => filter === 'all' || o.status === filter)

  return (
    <div>
      <h2 className="page-title">Opportunities</h2>
      <div className="bt-filters">
        {FILTERS.map(f => (
          <button key={f} className={`bt-filter ${filter === f ? 'bt-filter-active' : ''}`} onClick={() => setFilter(f)}>{f}</button>
        ))}
      </div>
      <div className="card">
        {rows.length === 0 ? <p className="text-muted">No opportunities{filter !== 'all' ? ` with status "${filter}"` : ''} yet.</p> : (
          <table>
            <thead><tr><th>Symbol</th><th>Market</th><th>Direction</th><th>Confidence</th><th>Source</th><th>Status</th><th>Created</th></tr></thead>
            <tbody>
              {rows.map((o: any) => (
                <tr key={o.id} onClick={() => setObject({ type: 'opportunity', id: o.id, data: o })}>
                  <td>{o.symbol}</td>
                  <td>{o.market}</td>
                  <td className={o.direction_bias === 'long' ? 'pnl-positive' : o.direction_bias === 'short' ? 'pnl-negative' : ''}>{o.direction_bias}</td>
                  <td className={o.confidence >= 0.7 ? 'pnl-positive' : o.confidence >= 0.4 ? '' : 'text-faint'}>{o.confidence}</td>
                  <td className="text-faint">{o.source || '—'}</td>
                  <td><StatusPill status={o.status} /></td>
                  <td className="text-faint font-sm">{o.created_at?.slice(0, 16) || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
