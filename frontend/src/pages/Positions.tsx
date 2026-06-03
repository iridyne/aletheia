import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export default function Positions() {
  const { data = [] } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions })
  return (
    <div>
      <h2 className="page-title">Positions</h2>
      <div className="card">
        {(data as any[]).length === 0 ? <p style={{ color: 'var(--muted)' }}>No positions yet.</p> : (
          <table>
            <thead><tr><th>Symbol</th><th>Direction</th><th>Qty</th><th>Entry</th><th>PnL</th><th>Status</th></tr></thead>
            <tbody>
              {(data as any[]).map((p: any) => (
                <tr key={p.id}>
                  <td>{p.symbol}</td><td>{p.direction}</td><td>{p.quantity}</td>
                  <td>{p.entry_price}</td><td>{p.unrealized_pnl}</td>
                  <td><span className={`badge ${p.status === 'open' ? 'badge-green' : 'badge-red'}`}>{p.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
