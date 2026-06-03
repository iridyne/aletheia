import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export default function Proposals() {
  const { data = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  return (
    <div>
      <h2 className="page-title">Proposals</h2>
      <div className="card">
        {(data as any[]).length === 0 ? <p style={{ color: 'var(--muted)' }}>No proposals yet.</p> : (
          <table>
            <thead><tr><th>Symbol</th><th>Direction</th><th>Mode</th><th>Status</th><th>Created</th></tr></thead>
            <tbody>
              {(data as any[]).map((p: any) => (
                <tr key={p.id}>
                  <td>{p.symbol}</td><td>{p.direction}</td><td>{p.execution_mode}</td>
                  <td><span className="badge badge-yellow">{p.status}</span></td><td>{p.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
