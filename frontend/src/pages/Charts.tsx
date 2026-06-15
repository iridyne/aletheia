import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import PriceChart from '../charts/PriceChart'
import Sparkline from '../components/Sparkline'
import MacroBar from '../components/MacroBar'
import PageHeader from '../components/PageHeader'
import { Badge, Button, Card, CardHeader, TableWrap } from '../components/ui'
import { fmtNum, fmtSigned, fmtTimestamp, useDecimals } from '../lib/format'

const FALLBACK_SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
const TIMEFRAMES = ['5m', '15m', '1h', '4h', '1d']

function ProviderChip({ provider }: { provider?: string | null }) {
  if (!provider) return null
  const label = provider === 'binance' ? 'Binance · CCXT'
    : provider === 'bybit' ? 'Bybit · CCXT'
    : provider === 'bitget' ? 'Bitget'
    : provider
  return <span className="provider-chip">{label}</span>
}

// ─── Watchlist Grid ─────────────────────────────────────────────────────────

function WatchlistGrid({
  symbols, snapshotMap, flashes, activeSymbol, onSelect, opportunityMap,
}: {
  symbols: string[]
  snapshotMap: Record<string, any>
  flashes: Record<string, 'up' | 'down'>
  activeSymbol: string
  onSelect: (sym: string) => void
  opportunityMap: Record<string, any>
}) {
  return (
    <div className="charts-watchlist">
      {symbols.map(sym => {
        const snap = snapshotMap[sym] ?? snapshotMap[sym.replace('/', '')]
        const isActive = sym === activeSymbol
        const change = snap?.change_pct_24h
        const opp = opportunityMap[sym]
        return (
          <Card
            key={sym}
            className={`charts-watchlist-card${isActive ? ' active' : ''}`}
            onClick={() => onSelect(sym)}
          >
            <div className="charts-card-head">
              <span className="charts-card-symbol">{sym}</span>
              {opp && (
                <span className={`charts-bias-dot bias-${opp.direction_bias}`} title={`AI: ${opp.direction_bias} (${opp.confidence_score ?? opp.confidence})`} />
              )}
            </div>
            <div className={`mono charts-card-price${flashes[sym] ? ` tick-flash-${flashes[sym]}` : ''}`}>
              {snap ? fmtNum(snap.price) : '—'}
            </div>
            <Sparkline symbol={sym} width={100} height={20} />
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
                {snap.high_24h != null && <span className="charts-card-high">H {fmtNum(snap.high_24h)}</span>}
                {snap.low_24h != null && <span className="charts-card-low">L {fmtNum(snap.low_24h)}</span>}
              </div>
            )}
          </Card>
        )
      })}
    </div>
  )
}

// ─── Tab: Chart ──────────────────────────────────────────────────────────────

function ChartTab({
  symbol, symbols, onSymbolChange, snapshotMap, dataProvider, lastUpdatedLabel,
}: {
  symbol: string; symbols: string[]; onSymbolChange: (s: string) => void
  snapshotMap: Record<string, any>; dataProvider: string | null; lastUpdatedLabel: string | null
}) {
  const [timeframe, setTimeframe] = useState('1h')
  const [selectedOrder, setSelectedOrder] = useState<string | null>(null)

  const { data: trades = [] } = useQuery({ queryKey: ['trades'], queryFn: api.getTrades })
  const { data: opportunities = [] } = useQuery({
    queryKey: ['opportunities'],
    queryFn: api.getOpportunities,
    refetchInterval: 60_000,
  })

  const symbolOpportunities = (opportunities as any[]).filter(
    o => o.symbol === symbol && o.status === 'open'
  )

  const bitgetSymbol = symbol.replace('/', '')
  const markers = (trades as any[])
    .filter(o => (o.symbol === symbol || o.symbol === bitgetSymbol) && o.filled_at)
    .map(o => ({
      time: Math.floor(new Date(
        o.filled_at.endsWith('Z') || o.filled_at.includes('+') ? o.filled_at : o.filled_at + 'Z'
      ).getTime() / 1000),
      side: (o.direction === 'long' || o.direction === 'buy' ? 'buy' : 'sell') as 'buy' | 'sell',
      price: o.price || 0,
      id: o.id,
    }))
    .filter(m => !Number.isNaN(m.time))

  const selectedTrade = selectedOrder ? (trades as any[]).find(t => t.id === selectedOrder) : null

  return (
    <>
      <div className="charts-source-bar">
        <span className="charts-source-label">Data source:</span>
        <ProviderChip provider={dataProvider} />
        {!dataProvider && <span className="charts-source-label">loading…</span>}
        {lastUpdatedLabel && <span className="charts-updated">updated {lastUpdatedLabel}</span>}
      </div>

      <Card>
        <div className="charts-controls">
          <select value={symbol} onChange={e => onSymbolChange(e.target.value)}>
            {symbols.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <div className="seg-toggle">
            {TIMEFRAMES.map(t => (
              <button key={t} className={`seg-btn${timeframe === t ? ' active' : ''}`} onClick={() => setTimeframe(t)}>
                {t}
              </button>
            ))}
          </div>
        </div>
        <PriceChart symbol={symbol} timeframe={timeframe} markers={markers} onMarkerClick={setSelectedOrder} />
      </Card>

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
                    <td className="mono">{opp.confidence_score != null ? `${opp.confidence_score}/100` : '—'}</td>
                    <td>{opp.source_agent || '—'}</td>
                    <td className="td-ellipsis" title={opp.thesis || ''}>{opp.thesis || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Card>
      )}

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
    </>
  )
}

// ─── Tab: Signals ────────────────────────────────────────────────────────────

function SignalsTab({ onSymbolSelect }: { onSymbolSelect: (sym: string) => void }) {
  const { data: opportunities = [], isLoading } = useQuery({
    queryKey: ['opportunities'],
    queryFn: api.getOpportunities,
    refetchInterval: 60_000,
  })

  const rows = (opportunities as any[]).filter(
    o => o.asset_class === 'crypto' || !o.asset_class
  ).sort((a, b) => (b.confidence_score ?? 0) - (a.confidence_score ?? 0))

  if (isLoading) return <p className="text-muted text-sm">Loading signals…</p>
  if (rows.length === 0) return (
    <p className="hint">No active signals — market observer has not run yet or no opportunities are open.</p>
  )

  return (
    <Card>
      <CardHeader title="AI Market Signals" />
      <TableWrap>
        <table>
          <thead>
            <tr><th>Symbol</th><th>Direction</th><th>Score</th><th>Confidence</th><th>Source</th><th>Thesis</th></tr>
          </thead>
          <tbody>
            {rows.slice(0, 30).map((opp: any) => (
              <tr
                key={opp.id}
                className={`clickable signals-row-${opp.direction_bias}`}
                onClick={() => onSymbolSelect(opp.symbol)}
              >
                <td className="mono">{opp.symbol}</td>
                <td>
                  <Badge tone={opp.direction_bias === 'long' ? 'green' : opp.direction_bias === 'short' ? 'red' : 'blue'}>
                    {opp.direction_bias}
                  </Badge>
                </td>
                <td className="mono">{opp.confidence_score != null ? `${opp.confidence_score}/100` : '—'}</td>
                <td className="mono">{opp.confidence || '—'}</td>
                <td className="text-muted">{opp.source_agent || '—'}</td>
                <td className="td-ellipsis" title={opp.thesis || ''}>{opp.thesis || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </Card>
  )
}

// ─── Tab: News ───────────────────────────────────────────────────────────────

function NewsTab() {
  const { data: news = [], isLoading } = useQuery({
    queryKey: ['news'],
    queryFn: () => api.getNews(20),
    refetchInterval: 120_000,
  })

  if (isLoading) return <p className="text-muted text-sm">Loading news…</p>
  if ((news as any[]).length === 0) return (
    <p className="hint">News ingestion not configured — add news sources in Settings or create items manually.</p>
  )

  return (
    <div className="news-feed">
      {(news as any[]).map((item: any) => {
        const sentiment = item.sentiment || 'neutral'
        const tone = sentiment === 'positive' ? 'green' : sentiment === 'negative' ? 'red' : 'blue'
        return (
          <div key={item.id} className="news-item">
            <div className="news-item-head">
              <span className="news-item-title">{item.title}</span>
              <Badge tone={tone}>{sentiment}</Badge>
            </div>
            <div className="news-item-meta">
              {item.source && <span className="news-item-source">{item.source}</span>}
              {item.published_at && <span className="mono news-item-ts">{fmtTimestamp(item.published_at)}</span>}
              {item.symbols_mentioned?.length > 0 && (
                <span className="news-item-symbols">
                  {item.symbols_mentioned.slice(0, 4).map((s: string) => (
                    <span key={s} className="news-sym-chip">{s}</span>
                  ))}
                </span>
              )}
            </div>
            {item.summary && <p className="news-item-summary">{item.summary}</p>}
          </div>
        )
      })}
    </div>
  )
}

// ─── Tab: Predictions ────────────────────────────────────────────────────────

function PredictionsTab() {
  const { data: markets = [], isLoading } = useQuery({
    queryKey: ['prediction-markets'],
    queryFn: api.getPredictionMarkets,
    refetchInterval: 120_000,
  })

  if (isLoading) return <p className="text-muted text-sm">Loading prediction markets…</p>
  if ((markets as any[]).length === 0) return (
    <p className="hint">Prediction observer not running or no active markets configured.</p>
  )

  return (
    <div className="pred-grid">
      {(markets as any[]).slice(0, 20).map((m: any) => {
        const prob = m.yes_probability ?? null
        const change = m.probability_change_24h ?? null
        const tone = change == null ? '' : change > 0 ? 'pnl-positive' : change < 0 ? 'pnl-negative' : ''
        return (
          <Card key={m.id} className="pred-card">
            <div className="pred-card-question">{m.question}</div>
            <div className="pred-card-stats">
              {prob != null && (
                <span className="pred-card-prob mono">{(prob * 100).toFixed(0)}%</span>
              )}
              {change != null && (
                <span className={`pred-card-change mono ${tone}`}>{fmtSigned(change * 100)}%</span>
              )}
              {m.volume_usd != null && (
                <span className="pred-card-vol text-muted">${fmtNum(m.volume_usd)}</span>
              )}
            </div>
          </Card>
        )
      })}
    </div>
  )
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export default function Charts() {
  const [symbol, setSymbol] = useState('BTC/USDT')
  const [activeTab, setActiveTab] = useState('chart')
  useDecimals()

  const { data: universe } = useQuery({
    queryKey: ['universe-current'],
    queryFn: api.getUniverseCurrent,
    refetchInterval: 120_000,
  })

  const { data: snapshots = [], dataUpdatedAt } = useQuery({
    queryKey: ['market-snapshots'],
    queryFn: () => api.getMarketSnapshots(undefined, 50),
    refetchInterval: 30_000,
  })

  const { data: opportunities = [] } = useQuery({
    queryKey: ['opportunities'],
    queryFn: api.getOpportunities,
    refetchInterval: 60_000,
  })

  // Dynamic watchlist: universe members (ranked) or fallback
  const watchlistSymbols = useMemo<string[]>(() => {
    const members = (universe as any)?.members
    if (Array.isArray(members) && members.length > 0) {
      return members.slice(0, 12).map((m: any) => m.symbol)
    }
    return FALLBACK_SYMBOLS
  }, [universe])

  // Snapshot map keyed by canonical and legacy Bitget format
  const snapshotMap = useMemo(() => {
    const m: Record<string, any> = {}
    for (const s of snapshots as any[]) {
      m[s.symbol] = s
      const lk = s.symbol.replace('/', '')
      if (!m[lk]) m[lk] = s
    }
    return m
  }, [snapshots])

  // Best opportunity per symbol (highest confidence_score, open)
  const opportunityMap = useMemo(() => {
    const m: Record<string, any> = {}
    for (const o of opportunities as any[]) {
      if (o.status !== 'open') continue
      const existing = m[o.symbol]
      if (!existing || (o.confidence_score ?? 0) > (existing.confidence_score ?? 0)) {
        m[o.symbol] = o
      }
    }
    return m
  }, [opportunities])

  // Tick flash tracking
  const prevPrices = useRef<Record<string, number>>({})
  const [flashes, setFlashes] = useState<Record<string, 'up' | 'down'>>({})
  useEffect(() => {
    const next: Record<string, 'up' | 'down'> = {}
    for (const sym of watchlistSymbols) {
      const price = (snapshotMap[sym] ?? snapshotMap[sym.replace('/', '')])?.price
      const prev = prevPrices.current[sym]
      if (price != null && prev != null && price !== prev) {
        next[sym] = price > prev ? 'up' : 'down'
      }
      if (price != null) prevPrices.current[sym] = price
    }
    if (Object.keys(next).length > 0) {
      setFlashes(next)
      const t = setTimeout(() => setFlashes({}), 650)
      return () => clearTimeout(t)
    }
  }, [snapshots, watchlistSymbols])

  const anySnap = (snapshots as any[])[0]
  const dataProvider: string | null = anySnap?.provider ?? null
  const secondsAgo = dataUpdatedAt ? Math.floor((Date.now() - dataUpdatedAt) / 1000) : null
  const lastUpdatedLabel = secondsAgo === null ? null
    : secondsAgo < 5 ? 'just now'
    : secondsAgo < 60 ? `${secondsAgo}s ago`
    : `${Math.floor(secondsAgo / 60)}m ago`

  const handleSignalSymbolSelect = useCallback((sym: string) => {
    setSymbol(sym)
    setActiveTab('chart')
  }, [])

  const signalCount = (opportunities as any[]).filter(
    o => o.status === 'open' && (!o.asset_class || o.asset_class === 'crypto')
  ).length

  return (
    <div>
      <PageHeader
        title="Markets"
        role="Observe · global market intelligence — macro regime, price action, AI signals, news, and prediction markets."
      />

      {/* Macro indicators bar */}
      <MacroBar />

      {/* Dynamic watchlist — universe-driven, falls back to BTC/ETH/SOL */}
      <WatchlistGrid
        symbols={watchlistSymbols}
        snapshotMap={snapshotMap}
        flashes={flashes}
        activeSymbol={symbol}
        onSelect={s => { setSymbol(s); setActiveTab('chart') }}
        opportunityMap={opportunityMap}
      />

      {/* Lower panel: tab-switched detail areas */}
      <div className="tab-bar" role="tablist">
        {(['chart', 'signals', 'news', 'predictions'] as const).map(id => (
          <button
            key={id}
            role="tab"
            aria-selected={activeTab === id}
            className={`tab${activeTab === id ? ' tab-active' : ''}`}
            onClick={() => setActiveTab(id)}
          >
            {id === 'chart' ? 'Chart'
              : id === 'signals' ? <>Signals{signalCount > 0 && <span className="tab-badge">{signalCount}</span>}</>
              : id === 'news' ? 'News'
              : 'Predictions'}
          </button>
        ))}
      </div>
      <div className="tab-panel">
        {activeTab === 'chart' && (
          <ChartTab
            symbol={symbol}
            symbols={watchlistSymbols}
            onSymbolChange={setSymbol}
            snapshotMap={snapshotMap}
            dataProvider={dataProvider}
            lastUpdatedLabel={lastUpdatedLabel}
          />
        )}
        {activeTab === 'signals' && <SignalsTab onSymbolSelect={handleSignalSymbolSelect} />}
        {activeTab === 'news' && <NewsTab />}
        {activeTab === 'predictions' && <PredictionsTab />}
      </div>
    </div>
  )
}
