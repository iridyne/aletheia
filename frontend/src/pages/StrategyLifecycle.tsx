import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

const STATES = ['draft', 'research_note', 'strategy_hypothesis', 'paper_candidate', 'paper_running', 'paper_passed', 'live_candidate', 'live_limited', 'retired']

export default function StrategyLifecycle() {
  const { data = [] } = useQuery({ queryKey: ['strategies'], queryFn: api.getStrategies })
  return (
    <div>
      <h2 className="page-title">Strategy Lifecycle</h2>
      <div className="card">
        <div style={{ display: 'flex', gap: '0.25rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
          {STATES.map(s => <span key={s} className="badge badge-blue" style={{ fontSize: '0.65rem' }}>{s}</span>)}
        </div>
        {(data as any[]).length === 0 ? <p style={{ color: 'var(--muted)' }}>No strategies yet.</p> : (
          <table>
            <thead><tr><th>Name</th><th>State</th><th>Created</th></tr></thead>
            <tbody>
              {(data as any[]).map((s: any) => (
                <tr key={s.id}><td>{s.name}</td><td><span className="badge badge-blue">{s.state}</span></td><td>{s.created_at}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
