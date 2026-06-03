import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

const STATES = ['draft', 'research_note', 'strategy_hypothesis', 'paper_candidate', 'paper_running', 'paper_passed', 'live_candidate', 'live_limited', 'retired']

// Next forward state for the promote button (mirrors backend FORWARD_TRANSITIONS happy path)
const NEXT_STATE: Record<string, string> = {
  draft: 'research_note',
  research_note: 'strategy_hypothesis',
  strategy_hypothesis: 'paper_candidate',
  paper_candidate: 'paper_running',
  paper_running: 'paper_passed',
  paper_passed: 'live_candidate',
  live_candidate: 'live_limited',
}

export default function StrategyLifecycle() {
  const qc = useQueryClient()
  const { data = [] } = useQuery({ queryKey: ['strategies'], queryFn: api.getStrategies })
  const promote = useMutation({
    mutationFn: ({ id, target }: { id: string; target: string }) => api.promoteStrategy(id, target),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
    onError: (e: any) => alert(e.message),
  })
  const retire = useMutation({
    mutationFn: (id: string) => api.retireStrategy(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
  })

  const rows = data as any[]
  return (
    <div>
      <h2 className="page-title">Strategy Lifecycle</h2>
      <div className="card">
        <div style={{ display: 'flex', gap: '0.25rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
          {STATES.map(s => <span key={s} className="badge badge-blue" style={{ fontSize: '0.65rem' }}>{s}</span>)}
        </div>
        {rows.length === 0 ? <p style={{ color: 'var(--muted)' }}>No strategies yet.</p> : (
          <table>
            <thead><tr><th>Name</th><th>State</th><th>Created</th><th>Actions</th></tr></thead>
            <tbody>
              {rows.map((s: any) => {
                const next = NEXT_STATE[s.state]
                const terminal = s.state === 'retired' || s.state === 'deprecated'
                return (
                  <tr key={s.id}>
                    <td>{s.name}</td>
                    <td><span className="badge badge-blue">{s.state}</span></td>
                    <td>{s.created_at?.slice(0, 16)}</td>
                    <td>
                      <div style={{ display: 'flex', gap: '0.25rem' }}>
                        {next && <button onClick={() => promote.mutate({ id: s.id, target: next })}>Promote → {next}</button>}
                        {!terminal && <button className="secondary" onClick={() => retire.mutate(s.id)}>Retire</button>}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
