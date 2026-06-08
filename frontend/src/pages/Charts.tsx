import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import PriceChart from '../charts/PriceChart'
import PageHeader from '../components/PageHeader'
import { fmtNum, fmtSigned, useDecimals } from '../lib/format'

const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
const TIMEFRAMES = ['5m', '15m', '1h', '4h', '1d']

export default function Charts() {
  const [symbol, setSymbol] = useState('BTCUSDT')
  const [timeframe, setTimeframe] = useState('1h')
  const [selectedOrder, setSelectedOrder] = useState<string | null>(null)
  useDecimals() // re-render when the display-decimals setting changes
  const { data: trades = [] } = useQuery({ queryKey: ['trades'], queryFn: api.getTrades })
  const { data: snapshots = [] } = useQuery({ queryKey: ['market-snapshots'], queryFn: () => api.getMarketSnapshots(undefined, 20) })
  const { data: opportunities = [] } = useQuery({ queryKey: ['opportunities'], queryFn: api.getOpportunities })

  const snapshotMap: Record<string, any> = {}
  for (const s of snapshots as any[]) {
    if (!snapshotMap[s.symbol]) snapshotMap[s.symbol] = s
  }

  const symbolOpportunities = (opportunities as any[]).filter(o => o.symbol === symbol && o.status === 'open')

  const markers = (trades as any[])
    .filter(o => o.symbol === symbol && o.filled_at)
    .map(o => ({
      time: Math.floor(new Date(o.filled_at + 'Z').getTime() / 1000),
      side: (o.direction === 'long' || o.direction === 'buy' ? 'buy' : 'sell') as 'buy' | 'sell',
      price: o.price || 0,
      id: o.id,
    }))
    .filter(m => !Number.isNaN(m.time))

  const selectedTrade = selectedOrder ? (trades as any[]).find(t => t.id === selectedOrder) : null

  return (
    <div>
      <PageHeader
        title="Markets"
        role="Observe · price action for the traded universe (BTC/ETH/SOL) with filled paper trades marked on-chart and open opportunities linked per symbol."
      />

      {/* Watchlist Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '0.75rem', marginBottom: '1.1rem' }}>
        {SYMBOLS.map(sym => {
          const snap = snapshotMap[sym]
          const isActive = sym === symbol
          const change = snap?.change_pct_24h
          const color = change > 0 ? 'var(--green)' : change < 0 ? 'var(--red)' : 'var(--muted)'
          return (
            <div
              key={sym}
              className="card"
              style={{
                cursor: 'pointer', padding: '0.7rem 0.9rem', marginBottom: 0,
                borderColor: isActive ? 'var(--accent)' : undefined,
                background: isActive ? 'rgba(34,211,238,0.05)' : undefined,
              }}
              onClick={() => setSymbol(sym)}
            >
              <div style={{ fontSize: '0.72rem', color: 'var(--faint)', marginBottom: '0.2rem' }}>{sym.replace('USDT', '')}/USDT</div>
              <div className="mono" style={{ fontSize: '1rem', fontWeight: 600 }}>
                {snap ? fmtNum(snap.price) : '—'}
              </div>
              <div className="flex items-center gap-2 mt-2">
                {change != null && (
                  <span className="mono" style={{ fontSize: '0.72rem', color }}>{fmtSigned(change)}%</span>
                )}
                {snap?.volume_24h != null && (
                  <span className="text-muted" style={{ fontSize: '0.65rem' }}>Vol: {fmtNum(snap.volume_24h / 1e6)}M</span>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Chart */}
      <div className="card">
        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem' }}>
          <select value={symbol} onChange={e => setSymbol(e.target.value)}>
            {SYMBOLS.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={timeframe} onChange={e => setTimeframe(e.target.value)}>
            {TIMEFRAMES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <PriceChart symbol={symbol} timeframe={timeframe} markers={markers} onMarkerClick={setSelectedOrder} />
        <p className="hint">Green/red arrows mark filled paper trades on {symbol}. Click a marker to see trade details.</p>
      </div>

      {/* Linked Opportunities */}
      {symbolOpportunities.length > 0 && (
        <div className="card">
          <h3>Open Opportunities — {symbol.replace('USDT', '')}</h3>
          <table>
            <thead>
              <tr><th>Direction</th><th>Confidence</th><th>Score</th><th>Source</th><th>Thesis</th></tr>
            </thead>
            <tbody>
              {symbolOpportunities.slice(0, 5).map((opp: any) => (
                <tr key={opp.id}>
                  <td><span className={`badge ${opp.direction_bias === 'long' ? 'badge-green' : opp.direction_bias === 'short' ? 'badge-red' : 'badge-blue'}`}>{opp.direction_bias}</span></td>
                  <td className="mono">{opp.confidence || '—'}</td>
                  <td className="mono" title="0..100 numeric calibration the LLM emits alongside the categorical confidence. Empty = model didn't supply one (parse failure or older row).">
                    {opp.confidence_score != null ? `${opp.confidence_score}/100` : '—'}
                  </td>
                  <td>{opp.source_agent || '—'}</td>
                  <td style={{ maxWidth: '30ch', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={opp.thesis || ''}>{opp.thesis || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Trade Detail */}
      {selectedTrade && (
        <div className="card">
          <h3>Trade Detail</h3>
          <table>
            <tbody>
              <tr><th>Order ID</th><td className="mono">{selectedTrade.id}</td></tr>
              <tr><th>Direction</th><td>{selectedTrade.direction}</td></tr>
              <tr><th>Quantity</th><td>{selectedTrade.quantity}</td></tr>
              <tr><th>Price</th><td>{selectedTrade.price}</td></tr>
              <tr><th>Filled</th><td>{selectedTrade.filled_at?.slice(0, 16)}</td></tr>
              <tr><th>Strategy</th><td className="mono">{selectedTrade.strategy_id || '—'}</td></tr>
            </tbody>
          </table>
          <button className="secondary" style={{ marginTop: '0.4rem' }} onClick={() => setSelectedOrder(null)}>Close</button>
        </div>
      )}
    </div>
  )
}
