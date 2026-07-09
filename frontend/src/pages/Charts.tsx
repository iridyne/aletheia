import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import PriceChart, { type TradeMarker } from '../charts/PriceChart'
import MacroBar from '../components/MacroBar'
import PageHeader from '../components/PageHeader'
import Tabs from '../components/Tabs'
import type { TabDef } from '../components/Tabs'
import { Badge, Button, Card, CardHeader, TableWrap } from '../components/ui'
import { fmtNum, fmtSigned, fmtDelta, fmtTimestamp, useDecimals, fmtSymbol } from '../lib/format'
import type { PredictionMarket } from '../types'

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

// ─── Symbol Strip ─────────────────────────────────────────────────────────────
// Compact horizontal scrollable row of symbol chips — replaces the card grid.

function SymbolStrip({
  symbols, snapshotMap, flashes, activeSymbol, onSelect, opportunityMap, marketType,
}: {
  symbols: string[]
  snapshotMap: Record<string, any>
  flashes: Record<string, 'up' | 'down'>
  activeSymbol: string
  onSelect: (sym: string) => void
  opportunityMap: Record<string, any>
  marketType?: string | null
}) {
  return (
    <div className="symbol-strip">
      {symbols.map(sym => {
        const snap = snapshotMap[sym] ?? snapshotMap[sym.replace('/', '')]
        const isActive = sym === activeSymbol
        const change = snap?.change_pct_24h
        const opp = opportunityMap[sym]
        const flashClass = flashes[sym] ? ` tick-flash-${flashes[sym]}` : ''
        return (
          <Button
            key={sym}
            className={`symbol-chip${isActive ? ' active' : ''}`}
            onClick={() => onSelect(sym)}
          >
            {/* #170 fix: prefer per-snapshot market_type over the board's
                asset-class label (same reason as the mtFor() helper below). */}
            <span className="symbol-chip-name">{fmtSymbol(sym, snap?.market_type || marketType || undefined)}</span>
            {opp && (
              <span className={`symbol-chip-bias bias-dot-${opp.direction_bias}`} title={`AI: ${opp.direction_bias}`} />
            )}
            {snap && (
              <span className={`symbol-chip-price mono${flashClass}`}>{fmtNum(snap.price)}</span>
            )}
            {change != null && (
              <span className={`symbol-chip-change mono ${change > 0 ? 'pnl-positive' : change < 0 ? 'pnl-negative' : 'text-muted'}`}>
                {fmtDelta(change)}%
              </span>
            )}
          </Button>
        )
      })}
    </div>
  )
}

// ─── Chart Panel (always visible at top) ────────────────────────────────────

function ChartPanel({
  symbol, symbols, onSymbolChange, snapshotMap, dataProvider, lastUpdatedLabel, marketType,
}: {
  symbol: string; symbols: string[]; onSymbolChange: (s: string) => void
  snapshotMap: Record<string, any>; dataProvider: string | null; lastUpdatedLabel: string | null
  marketType?: string | null
}) {
  // #170 fix: reversed the fallback order. Previously `marketType ?? snap.market_type`
  // — the board-level `marketType` (which can be an asset-class label like 'crypto')
  // ALWAYS won, so a per-symbol snapshot that said 'perp' was ignored and the perp
  // rendered with a slash (spot convention). Now per-snapshot market_type is the
  // authoritative source; the board's marketType is only used when the snapshot
  // lookup is missing.
  const mtFor = (s: string) => {
    const snap = snapshotMap[s] ?? snapshotMap[s.replace('/', '')]
    return snap?.market_type || marketType || undefined
  }
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
  const tradeMarkers: TradeMarker[] = (trades as any[])
    .filter(o => (o.symbol === symbol || o.symbol === bitgetSymbol) && o.filled_at)
    .map(o => {
      const dir = (o.direction || '').toLowerCase()
      const isClose = !!o.reduce_only || o.order_type === 'stop' || o.order_type === 'take_profit'
      let kind: TradeMarker['kind']
      if (isClose) {
        kind = (dir === 'sell' || dir === 'short') ? 'stop_long' : 'stop_short'
      } else {
        kind = (dir === 'long' || dir === 'buy') ? 'long' : 'short'
      }
      return {
        time: Math.floor(new Date(
          o.filled_at.endsWith('Z') || o.filled_at.includes('+') ? o.filled_at : o.filled_at + 'Z'
        ).getTime() / 1000),
        kind,
        price: o.price || 0,
        id: o.id,
      }
    })
    .filter(m => !Number.isNaN(m.time))

  const signalMarkers: TradeMarker[] = symbolOpportunities
    .filter((o: any) => o.created_at)
    .map((o: any) => {
      const ts = o.created_at.endsWith('Z') || o.created_at.includes('+') ? o.created_at : o.created_at + 'Z'
      return {
        time: Math.floor(new Date(ts).getTime() / 1000),
        kind: (o.direction_bias === 'long' || o.direction_bias === 'bullish' ? 'long' : 'short') as TradeMarker['kind'],
        price: 0,
        id: `signal-${o.id}`,
      }
    })
    .filter((m: any) => !Number.isNaN(m.time))

  const markers = [...tradeMarkers, ...signalMarkers]
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
            {symbols.map(s => <option key={s} value={s}>{fmtSymbol(s, mtFor(s))}</option>)}
          </select>
          <div className="seg-toggle">
            {TIMEFRAMES.map(t => (
              <Button key={t} variant="ghost" className={`seg-btn${timeframe === t ? ' active' : ''}`} onClick={() => setTimeframe(t)}>
                {t}
              </Button>
            ))}
          </div>
        </div>
        {/* #115 fix: PriceChart now reads marketType so a perp instrument's
            chart pulls perp bars, not spot. mtFor(symbol) resolves via prop
            override → snapshot lookup → 'spot' fallback. */}
        <PriceChart symbol={symbol} timeframe={timeframe} marketType={mtFor(symbol) || 'spot'} markers={markers} onMarkerClick={setSelectedOrder} />
      </Card>

      {symbolOpportunities.length > 0 && (
        <Card>
          <CardHeader title={`Open Opportunities — ${fmtSymbol(symbol, mtFor(symbol))}`} />
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
                <td className="mono">{fmtSymbol(opp.symbol, opp.market)}</td>
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
  const { data: markets = [], isLoading } = useQuery<PredictionMarket[]>({
    queryKey: ['prediction-markets'],
    queryFn: api.getPredictionMarkets,
    refetchInterval: 120_000,
  })

  if (isLoading) return <p className="text-muted text-sm">Loading prediction markets…</p>
  if (markets.length === 0) return (
    <p className="hint">Prediction observer not running or no active markets configured.</p>
  )

  // #169 fix: this card used to read m.question and m.volume_usd — the
  // PredictionMarket type carries event_title and volume_24h. The old field
  // names always resolved to undefined, so the card showed a blank question
  // and dropped the volume line entirely. Restore the typed PredictionMarket
  // reference so future field renames trip tsc instead of silently blanking
  // the card again.
  return (
    <div className="pred-grid">
      {markets.slice(0, 20).map((m) => {
        const prob = m.yes_probability ?? null
        const change = m.probability_change_24h ?? null
        const tone = change == null ? '' : change > 0 ? 'pnl-positive' : change < 0 ? 'pnl-negative' : ''
        return (
          <Card key={m.id} className="pred-card">
            <div className="pred-card-question">{m.event_title}</div>
            <div className="pred-card-stats">
              {prob != null && (
                <span className="pred-card-prob mono">{(prob * 100).toFixed(0)}%</span>
              )}
              {change != null && (
                <span className={`pred-card-change mono ${tone}`}>{fmtSigned(change * 100)}%</span>
              )}
              {m.volume_24h != null && (
                <span className="pred-card-vol text-muted">${fmtNum(m.volume_24h)}</span>
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
  // #116 fix (Charts side): pick up ?symbol=... &market_type=... from deep
  // links (TradesPositions "View on Chart" passes them). Falls back to the
  // BTC/USDT default when the caller didn't specify.
  const [searchParams, setSearchParams] = useSearchParams()
  const initialSymbol = searchParams.get('symbol') || 'BTC/USDT'
  const initialMarketType = searchParams.get('market_type') || 'spot'
  const [symbol, setSymbol] = useState(initialSymbol)
  const [marketType, setMarketType] = useState(initialMarketType)
  const [activeTab, setActiveTab] = useState('signals')
  useDecimals()
  // Keep the URL in sync when the user picks a different symbol — deep links
  // stay copy-pasteable and the browser back button behaves.
  useEffect(() => {
    const params: Record<string, string> = { symbol }
    if (marketType && marketType !== 'spot') params.market_type = marketType
    setSearchParams(params, { replace: true })
  }, [symbol, marketType, setSearchParams])

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

  const boardMarketType: string | null | undefined = (universe as any)?.snapshot?.market_type

  const watchlistSymbols = useMemo<string[]>(() => {
    const members = (universe as any)?.members
    if (Array.isArray(members) && members.length > 0) {
      return members.slice(0, 20).map((m: any) => m.symbol)
    }
    return FALLBACK_SYMBOLS
  }, [universe])

  const snapshotMap = useMemo(() => {
    const m: Record<string, any> = {}
    for (const s of snapshots as any[]) {
      m[s.symbol] = s
      const lk = s.symbol.replace('/', '')
      if (!m[lk]) m[lk] = s
    }
    return m
  }, [snapshots])

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
    window.scrollTo({ top: 0, behavior: 'smooth' })
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

      {/* Compact symbol selector strip */}
      <SymbolStrip
        symbols={watchlistSymbols}
        snapshotMap={snapshotMap}
        flashes={flashes}
        activeSymbol={symbol}
        onSelect={setSymbol}
        opportunityMap={opportunityMap}
        marketType={boardMarketType}
      />

      {/* Chart always at top */}
      <ChartPanel
        symbol={symbol}
        symbols={watchlistSymbols}
        onSymbolChange={setSymbol}
        snapshotMap={snapshotMap}
        dataProvider={dataProvider}
        lastUpdatedLabel={lastUpdatedLabel}
        // #116 fix continuation: prefer the URL-provided market_type (from
        // TradesPositions "View on Chart") over the board's market_type. A
        // deep link that says market_type=perp shouldn't be overridden by
        // the parent board's asset class (which may be a broader label like
        // 'crypto'). Falls back to the board's value when the URL didn't say.
        marketType={marketType !== 'spot' ? marketType : boardMarketType}
      />

      {/* Intelligence panels below the chart */}
      <Tabs
        value={activeTab}
        onChange={(id) => setActiveTab(id as typeof activeTab)}
        tabs={([
          {
            id: 'signals',
            label: 'Signals',
            badge: signalCount > 0 ? signalCount : undefined,
            render: () => <SignalsTab onSymbolSelect={handleSignalSymbolSelect} />,
          },
          { id: 'news', label: 'News', render: () => <NewsTab /> },
          { id: 'predictions', label: 'Predictions', render: () => <PredictionsTab /> },
        ]) as TabDef[]}
      />
    </div>
  )
}
