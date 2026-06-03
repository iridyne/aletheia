import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export default function PaperTrades() {
  const { data = [] } = useQuery({ queryKey: ['trades'], queryFn: api.getTrades })
  return (
    <div>
      <h2 className="page-title">Paper Trades</h2>
      <div className="card">
        {(data as any[]).length === 0 ? <p style={{ color: 'var(--muted)' }}>No paper trades yet.</p> : (
          <table>
            <thead><tr><th>Symbol</th><th>Direction</th><th>Qty</th><th>Price</th><th>Status</th></tr></thead>
            <tbody>
              {(data as any[]).map((t: any) => (
                <tr key={t.id}>
                  <td>{t.symbol}</td><td>{t.direction}</td><td>{t.quantity}</td>
                  <td>{t.price}</td><td>{t.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
