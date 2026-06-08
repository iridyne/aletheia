import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import PriceChart from '../charts/PriceChart'
import PageHeader from '../components/PageHeader'
import { fmtNum, fmtSigned, useDecimals } from '../lib/format'

// Canonical BTC/USDT format — matches CCXT / backend MarketDataConfig
const SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
const TIMEFRAMES = ['5m', '15m', '1h', '4h', '1d']

function ProviderChip({ provider }: { provider?: string | null }) {
  if (!provider) return null
  const label = provider === 'binance' ? 'Binance · CCXT'
    : provider === 'bybit' ? 'Bybit · CCXT'
    : provider === 'bitget' ? 'Bitget'
    : provider
  return (
    <span style={{
      fontSize: '0.6rem', padding: '0.1rem 0.35rem',
      borderRadius: 3, background: 'rgba(34,211,238,0.1)',
      color: 'var(--accent)', fontFamily: 'monospace', letterSpacing: '0.02em',
    }}>
      {label}
    </span>
  )
}

export default function Charts() {
  const [symbol, setSymbol] = useState('BTC/USDT')
  const [timeframe, setTimeframe] = useState('1h')
  const [selectedOrder, setSelectedOrder] = useState<string | null>(null)
  useDecimals()

  const { data: trades = [] } = useQuery({
    queryKey: ['trades'],
    queryFn: api.getTrades,
  })
  const { data: snapshots = [], dataUpdatedAt } = useQuery({
    queryKey: ['market-snapshots'],
    queryFn: () => api.getMarketSnapshots(undefined, 20),
    refetchInterval: 30_000,   // 5.3: auto-refresh every 30s
  })
  const { data: opportunities = [] } = useQuery({
    queryKey: ['opportunities'],
    queryFn: api.getOpportunities,
    refetchInterval: 60_000,
  })

  // Build snapshot map keyed by both canonical (BTC/USDT) and legacy (BTCUSDT) symbols
  const snapshotMap: Record<string, any> = {}
  for (const s of snapshots as any[]) {
    snapshotMap[s.symbol] = s
    // also index by Bitget format for any legacy snapshots still in DB
    const legacyKey = s.symbol.replace('/', '')
    if (!snapshotMap[legacyKey]) snapshotMap[legacyKey] = s
  }

  // Derive the data provider from the most recent snapshot (all symbols share one provider)
  const anySnap = (snapshots as any[])[0]
  const dataProvider: string | null = anySnap?.provider ?? null

  // Seconds since last data update
  const secondsAgo = dataUpdatedAt ? Math.floor((Date.now() - dataUpdatedAt) / 1000) : null
  const lastUpdatedLabel = secondsAgo === null ? null
    : secondsAgo < 5 ? 'just now'
    : secondsAgo < 60 ? `${secondsAgo}s ago`
    : `${Math.floor(secondsAgo / 60)}m ago`

  const symbolOpportunities = (opportunities as any[]).filter(
    o => o.symbol === symbol && o.status === 'open'
  )

  // Convert canonical symbol for trade matching (trades stored with Bitget format)
  const bitgetSymbol = symbol.replace('/', '')
  const markers = (trades as any[])
    .filter(o => (o.symbol === symbol || o.symbol === bitgetSymbol) && o.filled_at)
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

      {/* Data source + freshness bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
        <span style={{ fontSize: '0.7rem', color: 'var(--muted)' }}>Data source:</span>
        <ProviderChip provider={dataProvider} />
        {!dataProvider && (
          <span style={{ fontSize: '0.7rem', color: 'var(--muted)' }}>loading…</span>
        )}
        {lastUpdatedLabel && (
          <span style={{ fontSize: '0.65rem', color: 'var(--faint)', marginLeft: 'auto' }}>
            updated {lastUpdatedLabel}
          </span>
        )}
      </div>

      {/* Watchlist Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '0.75rem', marginBottom: '1.1rem' }}>
        {SYMBOLS.map(sym => {
          const snap = snapshotMap[sym] ?? snapshotMap[sym.replace('/', '')]
          const isActive = sym === symbol
          const change = snap?.change_pct_24h
          const changeColor = change > 0 ? 'var(--green)' : change < 0 ? 'var(--red)' : 'var(--muted)'
          const displayName = sym  // already canonical BTC/USDT
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
              {/* Symbol + provider */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.25rem' }}>
                <span style={{ fontSize: '0.72rem', color: 'var(--faint)' }}>{displayName}</span>
                <ProviderChip provider={snap?.provider} />
              </div>

              {/* Last price */}
              <div className="mono" style={{ fontSize: '1rem', fontWeight: 600 }}>
                {snap ? fmtNum(snap.price) : '—'}
              </div>

              {/* 24h change + volume */}
              <div className="flex items-center gap-2 mt-2" style={{ justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
                  {change != null && (
                    <span className="mono" style={{ fontSize: '0.72rem', color: changeColor }}>
                      {fmtSigned(change)}%
                    </span>
                  )}
                  {snap?.volume_24h != null && (
                    <span className="text-muted" style={{ fontSize: '0.65rem' }}>
                      Vol {fmtNum(snap.volume_24h / 1e6)}M
                    </span>
                  )}
                </div>
              </div>

              {/* 24h High / Low */}
              {(snap?.high_24h != null || snap?.low_24h != null) && (
                <div style={{ display: 'flex', gap: '0.6rem', marginTop: '0.3rem' }}>
                  {snap.high_24h != null && (
                    <span style={{ fontSize: '0.64rem', color: 'var(--green)' }}>
                      H {fmtNum(snap.high_24h)}
                    </span>
                  )}
                  {snap.low_24h != null && (
                    <span style={{ fontSize: '0.64rem', color: 'var(--red)' }}>
                      L {fmtNum(snap.low_24h)}
                    </span>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Chart */}
      <div className="card">
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.75rem' }}>
          <select value={symbol} onChange={e => setSymbol(e.target.value)}>
            {SYMBOLS.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={timeframe} onChange={e => setTimeframe(e.target.value)}>
            {TIMEFRAMES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <span style={{ marginLeft: 'auto', fontSize: '0.65rem', color: 'var(--faint)' }}>
            {dataProvider ? `via ${dataProvider} · CCXT` : ''}
          </span>
        </div>
        <PriceChart symbol={symbol} timeframe={timeframe} markers={markers} onMarkerClick={setSelectedOrder} />
        <p className="hint">Green/red arrows mark filled paper trades on {symbol}. Click a marker to see trade details.</p>
      </div>

      {/* Linked Opportunities */}
      {symbolOpportunities.length > 0 && (
        <div className="card">
          <h3>Open Opportunities — {symbol}</h3>
          <table>
            <thead>
              <tr><th>Direction</th><th>Confidence</th><th>Score</th><th>Source</th><th>Thesis</th></tr>
            </thead>
            <tbody>
              {symbolOpportunities.slice(0, 5).map((opp: any) => (
                <tr key={opp.id}>
                  <td><span className={`badge ${opp.direction_bias === 'long' ? 'badge-green' : opp.direction_bias === 'short' ? 'badge-red' : 'badge-blue'}`}>{opp.direction_bias}</span></td>
                  <td className="mono">{opp.confidence || '—'}</td>
                  <td className="mono" title="0..100 numeric calibration">
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
