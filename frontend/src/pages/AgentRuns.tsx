import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export default function AgentRuns() {
  const { data = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns })
  return (
    <div>
      <h2 className="page-title">Agent Runs</h2>
      <div className="card">
        {(data as any[]).length === 0 ? <p style={{ color: 'var(--muted)' }}>No agent runs yet.</p> : (
          <table>
            <thead><tr><th>Agent</th><th>Status</th><th>Trigger</th><th>Started</th></tr></thead>
            <tbody>
              {(data as any[]).map((r: any) => (
                <tr key={r.id}>
                  <td>{r.agent_name}</td>
                  <td><span className={`badge ${r.status === 'completed' ? 'badge-green' : 'badge-yellow'}`}>{r.status}</span></td>
                  <td>{r.trigger_type}</td>
                  <td>{r.started_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
