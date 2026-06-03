import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export default function Positions() {
  const { data = [] } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions })
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })
  const rows = (data as any[]).filter(p => p.status === 'open')

  const groups: Record<string, any[]> = {}
  for (const p of rows) {
    const key = `${p.execution_mode} · ${p.market_type}`
    ;(groups[key] ||= []).push(p)
  }

  return (
    <div>
      <h2 className="page-title">Positions</h2>

      {portfolio?.exposure_by_symbol && Object.keys(portfolio.exposure_by_symbol).length > 0 && (
        <div className="card">
          <h3>Exposure by Symbol</h3>
          <table>
            <tbody>
              {Object.entries(portfolio.exposure_by_symbol as Record<string, number>).map(([sym, v]) => (
                <tr key={sym}><th>{sym}</th><td>{v} USDT</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rows.length === 0 ? (
        <div className="card"><p style={{ color: 'var(--muted)' }}>No open positions.</p></div>
      ) : (
        Object.entries(groups).map(([group, items]) => (
          <div className="card" key={group}>
            <h3>{group}</h3>
            <table>
              <thead><tr><th>Symbol</th><th>Direction</th><th>Qty</th><th>Entry</th><th>Current</th><th>Unrealized PnL</th></tr></thead>
              <tbody>
                {items.map((p: any) => (
                  <tr key={p.id}>
                    <td>{p.symbol}</td>
                    <td>{p.direction}</td>
                    <td>{p.quantity}</td>
                    <td>{p.entry_price}</td>
                    <td>{p.current_price ?? '—'}</td>
                    <td style={{ color: (p.unrealized_pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>{p.unrealized_pnl ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))
      )}
    </div>
  )
}
