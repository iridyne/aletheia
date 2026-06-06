import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import MetricTile from '../components/shared/MetricTile'
import StatusPill from '../components/shared/StatusPill'
import { useInspector } from '../context/InspectorContext'
import { pnlClass } from '../utils/statusColor'

type Tab = 'positions' | 'trades' | 'pnl'

export default function Portfolio() {
  const [tab, setTab] = useState<Tab>('positions')
  const { setObject } = useInspector()
  const { data: positions = [] } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions })
  const { data: trades = [] } = useQuery({ queryKey: ['trades'], queryFn: api.getTrades })
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })

  const openPositions = (positions as any[]).filter(p => p.status === 'open')

  return (
    <div>
      <h2 className="page-title">Portfolio</h2>

      {portfolio && (
        <div className="card">
          <h3>Account Summary</h3>
          <div className="form-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
            <MetricTile label="Total Value" value={`${portfolio.total_value_usdt ?? '—'} USDT`} />
            <MetricTile label="Today PnL" value={String(portfolio.today_pnl ?? portfolio.unrealized_pnl ?? '—')} color={pnlClass(portfolio.today_pnl) === 'pnl-positive' ? 'var(--green)' : 'var(--red)'} />
            <MetricTile label="Open Positions" value={openPositions.length} />
            <MetricTile label="Total Trades" value={(trades as any[]).length} />
          </div>
        </div>
      )}

      <div className="tab-bar">
        {(['positions', 'trades', 'pnl'] as Tab[]).map(t => (
          <button key={t} className={`tab-btn ${tab === t ? 'tab-active' : ''}`} onClick={() => setTab(t)}>
            {t === 'pnl' ? 'PnL' : t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {tab === 'positions' && (
        <div className="card">
          {openPositions.length === 0 ? (
            <p className="text-muted">No open positions.</p>
          ) : (
            <table>
              <thead><tr><th>Symbol</th><th>Direction</th><th>Qty</th><th>Entry</th><th>Current</th><th>PnL</th><th>Mode</th></tr></thead>
              <tbody>
                {openPositions.map((p: any) => (
                  <tr key={p.id} onClick={() => setObject({ type: 'position', id: p.id, data: p })}>
                    <td>{p.symbol}</td>
                    <td>{p.direction}</td>
                    <td>{p.quantity}</td>
                    <td>{p.entry_price}</td>
                    <td>{p.current_price ?? '—'}</td>
                    <td className={pnlClass(p.unrealized_pnl)}>{p.unrealized_pnl ?? 0}</td>
                    <td><span className="badge badge-blue">{p.execution_mode}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === 'trades' && (
        <div className="card">
          {(trades as any[]).length === 0 ? (
            <p className="text-muted">No trades yet.</p>
          ) : (
            <table>
              <thead><tr><th>Symbol</th><th>Direction</th><th>Qty</th><th>Price</th><th>Status</th><th>Time</th></tr></thead>
              <tbody>
                {(trades as any[]).map((t: any) => (
                  <tr key={t.id} onClick={() => setObject({ type: 'trade', id: t.id, data: t })}>
                    <td>{t.symbol}</td>
                    <td>{t.direction}</td>
                    <td>{t.quantity}</td>
                    <td>{t.price}</td>
                    <td><StatusPill status={t.status} /></td>
                    <td>{t.created_at?.slice(0, 16) || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === 'pnl' && (
        <div className="card">
          <h3>Exposure by Symbol</h3>
          {portfolio?.exposure_by_symbol && Object.keys(portfolio.exposure_by_symbol).length > 0 ? (
            <table>
              <tbody>
                {Object.entries(portfolio.exposure_by_symbol as Record<string, number>).map(([sym, v]) => (
                  <tr key={sym}><th>{sym}</th><td>{v} USDT</td></tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-muted">No exposure data.</p>
          )}
        </div>
      )}
    </div>
  )
}
