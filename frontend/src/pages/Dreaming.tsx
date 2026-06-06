import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import StatusPill from '../components/shared/StatusPill'
import { useInspector } from '../context/InspectorContext'

export default function Dreaming() {
  const { setObject } = useInspector()
  const { data = [] } = useQuery({ queryKey: ['dreaming'], queryFn: api.getDreamingLogs })
  const rows = data as any[]

  return (
    <div>
      <h2 className="page-title">Dreaming / Learning</h2>
      <div className="card">
        {rows.length === 0 ? (
          <div>
            <p className="text-muted">No dreaming sessions yet.</p>
            <p className="hint">The dreaming agent consolidates trading lessons, strategy evaluations, and market observations into long-term memory during scheduled sessions.</p>
          </div>
        ) : (
          <table>
            <thead><tr><th>Title</th><th>Status</th><th>Insights</th><th>Created</th></tr></thead>
            <tbody>
              {rows.map((d: any) => (
                <tr key={d.id} onClick={() => setObject({ type: 'research_note', id: d.id, data: d })}>
                  <td>{d.title}</td>
                  <td><StatusPill status={d.status || 'completed'} /></td>
                  <td className="text-faint">{d.insights_count ?? d.content?.length ?? '—'}</td>
                  <td className="text-faint font-sm">{d.created_at?.slice(0, 16) || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
