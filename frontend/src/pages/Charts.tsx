import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import PriceChart from '../charts/PriceChart'
import PageHeader from '../components/PageHeader'
import { Badge, Button, Card, CardHeader, TableWrap } from '../components/ui'
import { fmtNum, fmtSigned, fmtTimestamp, useDecimals } from '../lib/format'

// Canonical BTC/USDT format — matches CCXT / backend MarketDataConfig
const SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
const TIMEFRAMES = ['5m', '15m', '1h', '4h', '1d']

function ProviderChip({ provider }: { provider?: string | null }) {
  if (!provider) return null
  const label = provider === 'binance' ? 'Binance · CCXT'
    : provider === 'bybit' ? 'Bybit · CCXT'
    : provider === 'bitget' ? 'Bitget'
    : provider
  return <span className="provider-chip">{label}</span>
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
      time: Math.floor(new Date(o.filled_at.endsWith('Z') || o.filled_at.includes('+') ? o.filled_at : o.filled_at + 'Z').getTime() / 1000),
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
      <div className="charts-source-bar">
        <span className="charts-source-label">Data source:</span>
        <ProviderChip provider={dataProvider} />
        {!dataProvider && <span className="charts-source-label">loading…</span>}
        {lastUpdatedLabel && (
          <span className="charts-updated">updated {lastUpdatedLabel}</span>
        )}
      </div>

      {/* Watchlist Grid */}
      <div className="charts-watchlist">
        {SYMBOLS.map(sym => {
          const snap = snapshotMap[sym] ?? snapshotMap[sym.replace('/', '')]
          const isActive = sym === symbol
          const change = snap?.change_pct_24h
          return (
            <Card
              key={sym}
              className={`charts-watchlist-card${isActive ? ' active' : ''}`}
              onClick={() => setSymbol(sym)}
            >
              <div className="charts-card-head">
                <span className="charts-card-symbol">{sym}</span>
                <ProviderChip provider={snap?.provider} />
              </div>
              <div className="mono charts-card-price">
                {snap ? fmtNum(snap.price) : '—'}
              </div>
              <div className="charts-card-meta">
                <div className="charts-card-change">
                  {change != null && (
                    <span className={`mono ${change > 0 ? 'pnl-positive' : change < 0 ? 'pnl-negative' : 'text-muted'}`}>
                      {fmtSigned(change)}%
                    </span>
                  )}
                  {snap?.volume_24h != null && (
                    <span className="charts-card-vol">Vol {fmtNum(snap.volume_24h / 1e6)}M</span>
                  )}
                </div>
              </div>
              {(snap?.high_24h != null || snap?.low_24h != null) && (
                <div className="charts-card-hl">
                  {snap.high_24h != null && (
                    <span className="charts-card-high">H {fmtNum(snap.high_24h)}</span>
                  )}
                  {snap.low_24h != null && (
                    <span className="charts-card-low">L {fmtNum(snap.low_24h)}</span>
                  )}
                </div>
              )}
            </Card>
          )
        })}
      </div>

      {/* Chart */}
      <Card>
        <div className="charts-controls">
          <select value={symbol} onChange={e => setSymbol(e.target.value)}>
            {SYMBOLS.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={timeframe} onChange={e => setTimeframe(e.target.value)}>
            {TIMEFRAMES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <PriceChart symbol={symbol} timeframe={timeframe} markers={markers} onMarkerClick={setSelectedOrder} />
        <p className="hint">Green/red arrows mark filled paper trades on {symbol}. Click a marker to see trade details.</p>
      </Card>

      {/* Linked Opportunities */}
      {symbolOpportunities.length > 0 && (
        <Card>
          <CardHeader title={`Open Opportunities — ${symbol}`} />
          <TableWrap>
          <table>
            <thead>
              <tr><th>Direction</th><th>Confidence</th><th>Score</th><th>Source</th><th>Thesis</th></tr>
            </thead>
            <tbody>
              {symbolOpportunities.slice(0, 5).map((opp: any) => (
                <tr key={opp.id}>
                  <td><Badge tone={opp.direction_bias === 'long' ? 'green' : opp.direction_bias === 'short' ? 'red' : 'blue'}>{opp.direction_bias}</Badge></td>
                  <td className="mono">{opp.confidence || '—'}</td>
                  <td className="mono" title="0..100 numeric calibration">
                    {opp.confidence_score != null ? `${opp.confidence_score}/100` : '—'}
                  </td>
                  <td>{opp.source_agent || '—'}</td>
                  <td className="td-ellipsis" title={opp.thesis || ''}>{opp.thesis || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </TableWrap>
        </Card>
      )}

      {/* Trade Detail */}
      {selectedTrade && (
        <Card>
          <CardHeader title="Trade Detail" />
          <TableWrap>
          <table>
            <tbody>
              <tr><th>Order ID</th><td className="mono">{selectedTrade.id}</td></tr>
              <tr><th>Direction</th><td>{selectedTrade.direction}</td></tr>
              <tr><th>Quantity</th><td className="mono">{fmtNum(selectedTrade.quantity)}</td></tr>
              <tr><th>Price</th><td className="mono">{fmtNum(selectedTrade.price)} USDT</td></tr>
              <tr><th>Filled</th><td className="mono">{fmtTimestamp(selectedTrade.filled_at)}</td></tr>
              <tr><th>Strategy</th><td className="mono">{selectedTrade.strategy_id || '—'}</td></tr>
            </tbody>
          </table>
          </TableWrap>
          <Button variant="secondary" onClick={() => setSelectedOrder(null)}>Close</Button>
        </Card>
      )}
    </div>
  )
}
