import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  api, type MarketNewsItem, type MarketOpportunityInstrument, type MarketTrade, type OhlcvResult,
} from '../api/client'
import PriceChart, {
  type ChartMarker, type PositionLevel, type VisibleVolumeProfile,
} from '../charts/PriceChart'
import MacroBar from '../components/MacroBar'
import PageHeader from '../components/PageHeader'
import Tabs from '../components/Tabs'
import type { TabDef } from '../components/Tabs'
import { DetailDrawer, DrawerField, DrawerSection } from '../components/DetailDrawer'
import { Badge, Button, Card, CardHeader, ListRowButton, TableWrap } from '../components/ui'
import { useDialogFocus } from '../components/ui/useDialogFocus'
import { fmtDelta, fmtNum, fmtSigned, fmtSymbol, fmtTimestamp } from '../lib/format'
import type { MarketSnapshot, Opportunity, Position, PredictionMarket, UniverseMember } from '../types'

const TIMEFRAMES = ['5m', '15m', '1h', '4h', '1d']
const MARKET_TYPES = ['spot', 'perp', 'futures']

export interface MarketInstrument { symbol: string; exchange: string; marketType: string }
interface InstrumentItem {
  instrument: MarketInstrument
  primary: string
  secondary: string
  search: string
  direction?: 'positive' | 'negative'
}
interface InstrumentGroup { id: string; title: string; empty: string; items: InstrumentItem[] }

const instrumentKey = ({ symbol, exchange, marketType }: MarketInstrument) => `${exchange}|${symbol}|${marketType}`
const utcSeconds = (value: string) => Math.floor(new Date(value.endsWith('Z') || value.includes('+') ? value : `${value}Z`).getTime() / 1000)

function ProviderChip({ provider }: { provider?: string | null }) {
  if (!provider) return null
  const label = provider === 'binance' ? 'Binance · CCXT'
    : provider === 'bybit' ? 'Bybit · CCXT'
    : provider === 'bitget' ? 'Bitget'
    : provider
  return <span className="provider-chip">{label}</span>
}

function InstrumentNavigator({ groups, active, search, onSearch, onSelect }: {
  groups: InstrumentGroup[]
  active: MarketInstrument
  search: string
  onSearch: (value: string) => void
  onSelect: (instrument: MarketInstrument) => void
}) {
  const normalized = search.trim().toLowerCase()
  return <div className="market-instrument-navigator">
    <label className="market-instrument-search">
      <span>Find instrument</span>
      <input type="search" value={search} placeholder="Search symbol…" onChange={event => onSearch(event.target.value)} />
    </label>
    <div className="market-instrument-scroll">
      {groups.map(group => {
        const items = normalized
          ? group.items.filter(item => normalized.split(/\s+/).every(token => item.search.includes(token)))
          : group.items
        return <section className="market-instrument-group" key={group.id}>
          <div className="market-instrument-group-title"><span>{group.title}</span><Badge>{items.length}</Badge></div>
          {items.length ? items.map((item, index) => <ListRowButton
            key={`${instrumentKey(item.instrument)}|${index}`}
            className="market-instrument-row"
            selected={instrumentKey(item.instrument) === instrumentKey(active)}
            aria-pressed={instrumentKey(item.instrument) === instrumentKey(active)}
            onClick={() => onSelect(item.instrument)}
          >
            <span className="market-instrument-primary">{item.primary}</span>
            <span className={`market-instrument-secondary${item.direction ? ` ${item.direction}` : ''}`}>{item.secondary}</span>
          </ListRowButton>) : <p className="market-instrument-empty">{normalized ? `No matching ${group.title.toLowerCase()}` : group.empty}</p>}
        </section>
      })}
    </div>
  </div>
}

function InstrumentDialog({ open, id, children, onClose }: {
  open: boolean; id: string; children: React.ReactNode; onClose: () => void
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  useDialogFocus(open, panelRef, onClose)
  if (!open) return null
  return <div className="market-instrument-backdrop" onClick={onClose}>
    <div
      id={id}
      className="market-instrument-dialog"
      role="dialog"
      aria-modal="true"
      aria-label="Choose instrument"
      tabIndex={-1}
      ref={panelRef}
      onClick={event => event.stopPropagation()}
    >
      <div className="market-instrument-dialog-header"><h3>Choose instrument</h3><Button variant="secondary" onClick={onClose}>Close</Button></div>
      {children}
    </div>
  </div>
}

function SignalsTab({ onInstrumentSelect }: { onInstrumentSelect: (instrument: MarketInstrument) => void }) {
  const { data: opportunities = [], isLoading } = useQuery({
    queryKey: ['opportunities'], queryFn: api.getOpportunities, refetchInterval: 60_000,
  })
  const rows = opportunities.filter(opportunity => opportunity.asset_class === 'crypto' || !opportunity.asset_class)
    .sort((a, b) => (b.confidence_score ?? 0) - (a.confidence_score ?? 0))
  if (isLoading) return <p className="text-muted text-sm">Loading signals…</p>
  if (!rows.length) return <p className="hint">No active signals — market observer has not run yet or no opportunities are open.</p>
  return <Card><CardHeader title="AI Market Signals" /><TableWrap><table>
    <thead><tr><th>Symbol</th><th>Direction</th><th>Score</th><th>Confidence</th><th>Source</th><th>Thesis</th></tr></thead>
    <tbody>{rows.slice(0, 30).map(opportunity => <tr
      key={opportunity.id}
      className={`clickable signals-row-${opportunity.direction_bias}`}
      onClick={() => onInstrumentSelect({ symbol: opportunity.symbol, exchange: '', marketType: opportunity.market })}
    >
      <td className="mono">{fmtSymbol(opportunity.symbol, opportunity.market)}</td>
      <td><Badge tone={opportunity.direction_bias === 'long' ? 'green' : opportunity.direction_bias === 'short' ? 'red' : 'blue'}>{opportunity.direction_bias}</Badge></td>
      <td className="mono">{opportunity.confidence_score != null ? `${opportunity.confidence_score}/100` : '—'}</td>
      <td className="mono">{opportunity.confidence || '—'}</td>
      <td className="text-muted">{opportunity.source_agent || '—'}</td>
      <td className="td-ellipsis" title={opportunity.thesis || ''}>{opportunity.thesis || '—'}</td>
    </tr>)}</tbody>
  </table></TableWrap></Card>
}

function NewsTab() {
  const { data: news = [], isLoading } = useQuery({ queryKey: ['news'], queryFn: () => api.getNews(20), refetchInterval: 120_000 })
  if (isLoading) return <p className="text-muted text-sm">Loading news…</p>
  if (!news.length) return <p className="hint">News ingestion not configured — add news sources in Settings or create items manually.</p>
  return <div className="news-feed">{news.map((item: MarketNewsItem) => {
    const sentiment = item.sentiment || 'neutral'
    return <div key={item.id} className="news-item">
      <div className="news-item-head"><span className="news-item-title">{item.title}</span><Badge tone={sentiment === 'positive' ? 'green' : sentiment === 'negative' ? 'red' : 'blue'}>{sentiment}</Badge></div>
      <div className="news-item-meta">{item.source && <span className="news-item-source">{item.source}</span>}{item.published_at && <span className="mono news-item-ts">{fmtTimestamp(item.published_at)}</span>}</div>
      {item.summary && <p className="news-item-summary">{item.summary}</p>}
    </div>
  })}</div>
}

function PredictionsTab() {
  const { data: markets = [], isLoading } = useQuery<PredictionMarket[]>({
    queryKey: ['prediction-markets'], queryFn: api.getPredictionMarkets, refetchInterval: 120_000,
  })
  if (isLoading) return <p className="text-muted text-sm">Loading prediction markets…</p>
  if (!markets.length) return <p className="hint">Prediction observer not running or no active markets configured.</p>
  return <div className="pred-grid">{markets.slice(0, 20).map(market => <Card key={market.id} className="pred-card">
    <div className="pred-card-question">{market.event_title}</div>
    <div className="pred-card-stats">
      {market.yes_probability != null && <span className="pred-card-prob mono">{(market.yes_probability * 100).toFixed(0)}%</span>}
      {market.probability_change_24h != null && <span className={`pred-card-change mono ${market.probability_change_24h > 0 ? 'pnl-positive' : market.probability_change_24h < 0 ? 'pnl-negative' : ''}`}>{fmtSigned(market.probability_change_24h * 100)}%</span>}
      {market.volume_24h != null && <span className="pred-card-vol text-muted">${fmtNum(market.volume_24h)}</span>}
    </div>
  </Card>)}</div>
}

function EventDrawer({ events, trades, opportunities, symbol, onClose }: {
  events: ChartMarker[]; trades: MarketTrade[]; opportunities: Opportunity[]; symbol: string; onClose: () => void
}) {
  return <DetailDrawer open={events.length > 0} onClose={onClose} title={`Chart events · ${fmtSymbol(symbol)}`}>
    {events.map(event => {
      if (event.entity === 'opportunity') {
        const detail = opportunities.find(opportunity => String(opportunity.id) === event.id)
        return <DrawerSection key={`${event.entity}-${event.id}`} label={`${event.entity} · ${event.kind}`}>
          <DrawerField label="Occurred" value={fmtTimestamp(detail?.created_at)} mono />
          <DrawerField label="Status" value={detail?.status} />
          <DrawerField label="Source" value={detail?.source_agent} />
          <DrawerField label="Confidence / score" value={`${detail?.confidence || '—'} · ${detail?.confidence_score ?? '—'}`} />
          <DrawerField label="Thesis" value={detail?.thesis} />
        </DrawerSection>
      }
      const detail = trades.find(trade => String(trade.id) === event.id)
      return <DrawerSection key={`${event.entity}-${event.id}`} label={`${event.entity} · ${event.kind}`}>
        <DrawerField label="Occurred" value={fmtTimestamp(detail?.filled_at)} mono />
        <DrawerField label="Quantity" value={fmtNum(detail?.quantity)} mono />
        <DrawerField label="Fill price" value={fmtNum(detail?.price)} mono />
        <DrawerField label="Strategy" value={detail?.strategy_id} mono />
      </DrawerSection>
    })}
  </DetailDrawer>
}

export default function Charts() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [active, setActive] = useState<MarketInstrument>({
    symbol: searchParams.get('symbol') || 'BTC/USDT',
    exchange: searchParams.get('exchange') || '',
    marketType: searchParams.get('market_type') || 'spot',
  })
  const [timeframe, setTimeframe] = useState('1h')
  const [activeTab, setActiveTab] = useState('signals')
  const [search, setSearch] = useState('')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [selectedEvents, setSelectedEvents] = useState<ChartMarker[]>([])
  const [overlays, setOverlays] = useState({ volume: true, profile: true, levels: true, signals: true })
  const [profile, setProfile] = useState<VisibleVolumeProfile | null>(null)
  const [dataMeta, setDataMeta] = useState<OhlcvResult['meta'] | null>(null)
  const [formSymbol, setFormSymbol] = useState(active.symbol)
  const [formExchange, setFormExchange] = useState(active.exchange)
  const [formMarket, setFormMarket] = useState(active.marketType)
  const [formError, setFormError] = useState('')
  const dialogId = useId()

  useEffect(() => {
    const params: Record<string, string> = { symbol: active.symbol }
    if (active.exchange) params.exchange = active.exchange
    if (active.marketType !== 'spot') params.market_type = active.marketType
    setSearchParams(params, { replace: true })
  }, [active, setSearchParams])

  const { data: universe } = useQuery({ queryKey: ['universe-current'], queryFn: api.getUniverseCurrent, refetchInterval: 120_000 })
  const { data: snapshots = [] } = useQuery({ queryKey: ['market-snapshots'], queryFn: () => api.getMarketSnapshots(undefined, 500), refetchInterval: 30_000 })
  const { data: positions = [] } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions, refetchInterval: 30_000 })
  const { data: opportunityInstruments = [] } = useQuery({
    queryKey: ['market-opportunity-instruments'], queryFn: api.getMarketOpportunityInstruments, refetchInterval: 60_000,
  })
  const { data: opportunities = [] } = useQuery({
    queryKey: ['market-opportunities', active.symbol, active.marketType],
    queryFn: () => api.getMarketOpportunities(active.symbol, active.marketType), refetchInterval: 60_000,
  })
  const { data: trades = [] } = useQuery({ queryKey: ['trades'], queryFn: api.getTrades, refetchInterval: 30_000 })

  const snapshotMap = useMemo(() => {
    const map = new Map<string, MarketSnapshot>()
    for (const snapshot of snapshots) map.set(`${snapshot.symbol}|${snapshot.market_type || 'spot'}`, snapshot)
    return map
  }, [snapshots])
  const snapshotFor = useCallback((instrument: MarketInstrument) => snapshotMap.get(`${instrument.symbol}|${instrument.marketType}`)
    ?? snapshotMap.get(`${instrument.symbol.replace('/', '')}|${instrument.marketType}`), [snapshotMap])

  const groups = useMemo<InstrumentGroup[]>(() => {
    const universeMarket = universe?.snapshot?.market_type || 'spot'
    const universeItems = (universe?.members || []).map((member: UniverseMember) => {
      const instrument = { symbol: member.symbol, exchange: '', marketType: universeMarket }
      const snapshot = snapshotFor(instrument)
      const secondary = `#${member.rank} · ${member.scanner_family || 'Unclassified'}${snapshot?.price != null ? ` · ${fmtNum(snapshot.price)}` : ''}`
      return { instrument, primary: `${fmtSymbol(member.symbol, universeMarket)} · ${universeMarket}`, secondary, search: `${member.symbol} ${fmtSymbol(member.symbol, universeMarket)} ${universeMarket} universe ${member.scanner_family || ''}`.toLowerCase() }
    })
    const positionItems = positions.filter(position => position.status === 'open').map((position: Position) => {
      const instrument = { symbol: position.symbol, exchange: '', marketType: position.market_type || 'spot' }
      const pnl = position.unrealized_pnl == null ? 'PnL not available' : `${fmtSigned(position.unrealized_pnl)} PnL`
      const secondary = `${position.direction} · ${pnl}`
      return { instrument, primary: `${fmtSymbol(position.symbol, instrument.marketType)} · ${instrument.marketType}`, secondary, search: `${position.symbol} ${fmtSymbol(position.symbol, instrument.marketType)} ${instrument.marketType} position ${position.direction} ${pnl}`.toLowerCase(), direction: position.direction.toLowerCase() === 'long' ? 'positive' as const : 'negative' as const }
    })
    const opportunityItems = opportunityInstruments.map((item: MarketOpportunityInstrument) => {
      const instrument = { symbol: item.symbol, exchange: '', marketType: item.market }
      const direction = item.latest_direction_bias || 'neutral'
      const secondary = `${direction} · ${item.latest_status}`
      return { instrument, primary: `${fmtSymbol(item.symbol, item.market)} · ${item.market}`, secondary, search: `${item.symbol} ${fmtSymbol(item.symbol, item.market)} ${item.market} opportunity ${direction} ${item.latest_status}`.toLowerCase(), direction: direction === 'long' || direction === 'bullish' ? 'positive' as const : direction === 'short' || direction === 'bearish' ? 'negative' as const : undefined }
    })
    return [
      { id: 'universe', title: 'Universe', empty: 'No active universe', items: universeItems },
      { id: 'positions', title: 'Positions', empty: 'No active positions', items: positionItems },
      { id: 'opportunities', title: 'Opportunities', empty: 'No active opportunities', items: opportunityItems },
    ]
  }, [opportunityInstruments, positions, snapshotFor, universe])

  const matchingPositions = useMemo(() => positions.filter(position => position.status === 'open'
    && position.symbol === active.symbol && (position.market_type || 'spot') === active.marketType), [active, positions])
  const positionLevels = useMemo<PositionLevel[]>(() => matchingPositions.flatMap(position => {
    const direction = position.direction || 'Position'
    return [
      { id: `${position.id}-entry`, kind: 'entry' as const, price: position.entry_price, title: `Position entry · ${direction}` },
      { id: `${position.id}-stop`, kind: 'stop' as const, price: position.stop_price ?? 0, title: `Stop · ${direction}` },
      { id: `${position.id}-tp`, kind: 'take_profit' as const, price: position.take_profit_price ?? 0, title: `Take profit · ${direction}` },
      { id: `${position.id}-liq`, kind: 'liquidation' as const, price: position.liquidation_price ?? 0, title: `Estimated liquidation · ${direction}` },
    ]
  }).filter(level => Number.isFinite(level.price) && level.price > 0), [matchingPositions])

  const markers = useMemo<ChartMarker[]>(() => {
    const tradeMarkers: ChartMarker[] = trades.flatMap(trade => {
      const symbolMatches = trade.symbol === active.symbol || trade.symbol === active.symbol.replace('/', '')
      if (!symbolMatches || (trade.market_type && trade.market_type !== active.marketType) || !trade.filled_at) return []
      const direction = String(trade.direction || '').toLowerCase()
      const close = Boolean(trade.reduce_only) || trade.order_type === 'stop' || trade.order_type === 'take_profit'
      const kind: ChartMarker['kind'] = close
        ? (direction === 'sell' || direction === 'short' ? 'stop_long' : 'stop_short')
        : (direction === 'long' || direction === 'buy' ? 'long' : 'short')
      return [{ id: String(trade.id), entity: 'trade', time: utcSeconds(trade.filled_at), kind, label: kind.startsWith('stop') ? 'Stop' : kind === 'long' ? 'Long' : 'Short' }]
    })
    const opportunityMarkers: ChartMarker[] = opportunities.flatMap(opportunity => {
      if (!opportunity.created_at || !opportunity.direction_bias
        || !['long', 'bullish', 'short', 'bearish'].includes(opportunity.direction_bias)) return []
      const long = opportunity.direction_bias === 'long' || opportunity.direction_bias === 'bullish'
      return [{ id: String(opportunity.id), entity: 'opportunity', time: utcSeconds(opportunity.created_at), kind: long ? 'long' : 'short', label: long ? 'Long' : 'Short' }]
    })
    return [...tradeMarkers, ...opportunityMarkers].filter(marker => Number.isFinite(marker.time))
  }, [active, opportunities, trades])

  const currentSnapshot = snapshotFor(active)
  const selectInstrument = useCallback((instrument: MarketInstrument) => {
    setSelectedEvents([])
    setActive(instrument)
    setFormSymbol(instrument.symbol)
    setFormExchange(instrument.exchange)
    setFormMarket(instrument.marketType)
    setPickerOpen(false)
  }, [])
  const submitInstrument = (event: React.FormEvent) => {
    event.preventDefault()
    const symbol = formSymbol.trim().toUpperCase()
    if (!/^[^/\s]+\/[^/\s]+$/.test(symbol)) { setFormError('Use canonical BASE/QUOTE format.'); return }
    if (!MARKET_TYPES.includes(formMarket)) { setFormError('Choose spot, perp, or futures.'); return }
    setFormError('')
    selectInstrument({ symbol, exchange: formExchange.trim(), marketType: formMarket })
  }
  const selectFromSignals = useCallback((instrument: MarketInstrument) => {
    selectInstrument(instrument)
    document.getElementById('market-chart-toolbar')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [selectInstrument])
  const signalCount = opportunities.filter(opportunity => opportunity.status === 'open').length
  const metaAge = dataMeta?.ageMs == null ? 'age not available' : dataMeta.ageMs < 60_000 ? `${Math.floor(dataMeta.ageMs / 1000)}s ago` : `${Math.floor(dataMeta.ageMs / 60_000)}m ago`
  const navigator = <InstrumentNavigator groups={groups} active={active} search={search} onSearch={setSearch} onSelect={selectInstrument} />

  return <div className="markets-page">
    <PageHeader
      title="Markets"
      role="Exact-instrument market workbench · price, volume, structure, positions, and observed signals."
      actions={<form className="market-instrument-form" onSubmit={submitInstrument}>
        <label><span>Symbol</span><input value={formSymbol} onChange={event => setFormSymbol(event.target.value)} placeholder="BTC/USDT" /></label>
        <label><span>Exchange</span><input value={formExchange} onChange={event => setFormExchange(event.target.value)} placeholder="default" /></label>
        <label><span>Market</span><select value={formMarket} onChange={event => setFormMarket(event.target.value)}>{MARKET_TYPES.map(type => <option key={type} value={type}>{type}</option>)}</select></label>
        <Button type="submit" disabled={!formSymbol.trim() || !MARKET_TYPES.includes(formMarket)}>Load</Button>
        {formError && <span className="market-instrument-form-error" role="alert">{formError}</span>}
      </form>}
    />

    <div className="market-workbench-context">
      <span className="market-context-static mono">{fmtSymbol(active.symbol, active.marketType)}</span>
      <button className="market-context-picker" aria-expanded={pickerOpen} aria-controls={dialogId} onClick={() => setPickerOpen(true)}>
        <span>Choose instrument</span><strong>{fmtSymbol(active.symbol, active.marketType)} · {active.marketType}</strong><span aria-hidden="true">⌄</span>
      </button>
      <strong className="market-context-price mono">{currentSnapshot?.price == null ? 'Price not available' : fmtNum(currentSnapshot.price)}</strong>
      {currentSnapshot?.change_pct_24h != null && <span className={currentSnapshot.change_pct_24h > 0 ? 'pnl-positive' : currentSnapshot.change_pct_24h < 0 ? 'pnl-negative' : 'text-muted'}>{fmtDelta(currentSnapshot.change_pct_24h)}%</span>}
      <span>{active.exchange || 'default exchange'} · {active.marketType}</span>
      <span className="market-context-provenance"><ProviderChip provider={dataMeta?.source} />{dataMeta ? `${dataMeta.instrument || fmtSymbol(active.symbol, active.marketType)} · ${metaAge}` : 'source not available'}</span>
    </div>

    <div className="market-workbench-shell">
      <aside className="market-instrument-rail">{navigator}</aside>
      <Card className="market-workbench-chart-card">
        <div id="market-chart-toolbar" className="market-chart-toolbar">
          <div className="seg-toggle">{TIMEFRAMES.map(value => <Button key={value} variant="ghost" className={`seg-btn${timeframe === value ? ' active' : ''}`} aria-pressed={timeframe === value} onClick={() => setTimeframe(value)}>{value}</Button>)}</div>
          <div className="market-overlay-controls">{([
            ['volume', 'Volume'], ['profile', 'Est. profile'], ['levels', 'Position levels'], ['signals', 'Signals'],
          ] as const).map(([key, label]) => <label key={key}><input type="checkbox" checked={overlays[key]} aria-controls="market-price-chart" onChange={event => setOverlays(value => ({ ...value, [key]: event.target.checked }))} />{label}</label>)}</div>
        </div>
        <PriceChart
          symbol={active.symbol}
          exchange={active.exchange}
          marketType={active.marketType}
          timeframe={timeframe}
          markers={markers}
          positionLevels={positionLevels}
          overlays={overlays}
          onMarkerClick={setSelectedEvents}
          onMetaChange={setDataMeta}
          onProfileChange={setProfile}
        />
        <div className="market-chart-legend">
          <span>■ Candle</span><span>▥ Volume</span><span>━ Est. POC</span><span>┄ Est. VAH / VAL</span>
          <span className="market-chart-profile-values">{profile ? `POC ${fmtNum(profile.poc)} · VAH ${fmtNum(profile.vah)} · VAL ${fmtNum(profile.val)}` : 'Estimated profile unavailable'}</span>
          <small>Estimated from visible OHLCV · 70% value area</small>
        </div>
      </Card>
    </div>

    <InstrumentDialog open={pickerOpen} id={dialogId} onClose={() => setPickerOpen(false)}>{navigator}</InstrumentDialog>
    <MacroBar />
    <Tabs value={activeTab} onChange={id => setActiveTab(id)} tabs={([
      { id: 'signals', label: 'Signals', badge: signalCount || undefined, render: () => <SignalsTab onInstrumentSelect={selectFromSignals} /> },
      { id: 'news', label: 'News', render: () => <NewsTab /> },
      { id: 'predictions', label: 'Predictions', render: () => <PredictionsTab /> },
    ]) as TabDef[]} />
    <EventDrawer events={selectedEvents} trades={trades} opportunities={opportunities} symbol={active.symbol} onClose={() => setSelectedEvents([])} />
  </div>
}
