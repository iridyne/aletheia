import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export default function Opportunities() {
  const { data = [] } = useQuery({ queryKey: ['opportunities'], queryFn: api.getOpportunities })
  return (
    <div>
      <h2 className="page-title">Opportunities</h2>
      <div className="card">
        {(data as any[]).length === 0 ? <p style={{ color: 'var(--muted)' }}>No opportunities yet.</p> : (
          <table>
            <thead><tr><th>Symbol</th><th>Market</th><th>Direction</th><th>Confidence</th><th>Status</th></tr></thead>
            <tbody>
              {(data as any[]).map((o: any) => (
                <tr key={o.id}>
                  <td>{o.symbol}</td><td>{o.market}</td><td>{o.direction_bias}</td>
                  <td>{o.confidence}</td><td><span className="badge badge-blue">{o.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
