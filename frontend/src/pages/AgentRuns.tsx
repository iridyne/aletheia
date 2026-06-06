import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useInspector } from '../context/InspectorContext'

const AGENTS = [
  'market_observer', 'research_agent', 'strategy_designer',
  'risk_critic', 'portfolio_agent', 'post_trade_reviewer', 'dreaming_agent',
]

export default function AgentRuns() {
  const qc = useQueryClient()
  const { setObject } = useInspector()
  const { data = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns })
  const run = useMutation({
    mutationFn: (name: string) => api.startAgentRun(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-runs'] }),
  })
  const rows = data as any[]

  return (
    <div>
      <h2 className="page-title">Agent Runs</h2>
      <div className="card">
        <h3>Run an agent now</h3>
        <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap' }}>
          {AGENTS.map(a => (
            <button key={a} className="secondary" onClick={() => run.mutate(a)} disabled={run.isPending}>{a}</button>
          ))}
        </div>
      </div>
      <div className="card">
        {rows.length === 0 ? <p style={{ color: 'var(--muted)' }}>No agent runs yet.</p> : (
          <table>
            <thead><tr><th>Agent</th><th>Status</th><th>Trigger</th><th>Provider</th><th>Outputs</th><th>Started</th></tr></thead>
            <tbody>
              {rows.map((r: any) => {
                let outputs = 0
                try { outputs = JSON.parse(r.outputs_created || '[]').length } catch { /* ignore */ }
                return (
                  <tr key={r.id} onClick={() => setObject({ type: 'agent_run', id: r.id, data: r })}>
                    <td>{r.agent_name}</td>
                    <td><span className={`badge ${r.status === 'completed' ? 'badge-green' : r.status?.includes('error') ? 'badge-yellow' : 'badge-blue'}`}>{r.status}</span></td>
                    <td>{r.trigger_type}</td>
                    <td>{r.model_provider || '—'}</td>
                    <td>{outputs}</td>
                    <td>{r.started_at?.slice(0, 16)}</td>
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
