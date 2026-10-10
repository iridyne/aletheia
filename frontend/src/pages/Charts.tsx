import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  api, type MarketOpportunityInstrument, type MarketTrade, type OhlcvResult,
} from '../api/client'
import PriceChart, {
  DEFAULT_CHART_SETTINGS, MAIN_INDICATOR_IDS, MAX_INDICATORS_PER_ZONE, SUBCHART_INDICATOR_IDS,
  type ChartMarker, type ChartSettings, type MainIndicatorId, type PositionLevel, type SubchartIndicatorId,
} from '../charts/PriceChart'
import MacroBar from '../components/MacroBar'
import PageHeader from '../components/PageHeader'
import Tabs from '../components/Tabs'
import type { TabDef } from '../components/Tabs'
import { DetailDrawer, DrawerField, DrawerSection } from '../components/DetailDrawer'
import { Badge, Button, Card, CardHeader, ListRowButton, TableWrap } from '../components/ui'
import { DataStateMessage } from '../components/DataState'
import { useDialogFocus } from '../components/ui/useDialogFocus'
import { fmtCompact, fmtDelta, fmtNum, fmtSigned, fmtSymbol, fmtTimestamp, humanize } from '../lib/format'
import type { Candidate, CandidateDetail, EntryPlan, MarketMicrostructure, MarketSnapshot, Opportunity, Position, PredictionMarket } from '../types'
import { searchInstruments } from '../charts/instrumentCatalog'
import R20IndicatorWorkbench from '../charts/R20IndicatorWorkbench'

const PINNED_TIMEFRAMES = ['5m', '15m', '1h', '4h', '1d']
const OVERFLOW_TIMEFRAME_GROUPS: Array<{ title: string; items: string[] }> = [
  { title: 'Minutes', items: ['1m', '3m', '5m', '15m', '30m'] },
  { title: 'Hours', items: ['1h', '2h', '4h', '6h', '8h', '12h'] },
  { title: 'Days', items: ['1d', '3d', '1w'] },
]



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
function InstrumentNavigator({ groups, active, search, onSearch, onSelect, defaultExchange, collapsedGroupIds, onToggleGroup }: {
  groups: InstrumentGroup[]
  active: MarketInstrument
  search: string
  onSearch: (value: string) => void
  onSelect: (instrument: MarketInstrument) => void
  defaultExchange: string
  collapsedGroupIds: ReadonlySet<string>
  onToggleGroup: (id: string) => void
}) {
  const term = search.trim()
  const results = useMemo(() => (term ? searchInstruments(term, defaultExchange, 20) : []), [term, defaultExchange])
  const bodyIdBase = useId()
  const renderRow = (instrument: MarketInstrument, primary: string, secondary: string, key: string, direction?: 'positive' | 'negative') =>
    <ListRowButton
      key={key}
      className="market-instrument-row"
      selected={instrumentKey(instrument) === instrumentKey(active)}
      aria-pressed={instrumentKey(instrument) === instrumentKey(active)}
      onClick={() => { onSearch(''); onSelect(instrument) }}
    >
      <span className="market-instrument-primary">{primary}</span>
      <span className={`market-instrument-secondary${direction ? ` ${direction}` : ''}`}>{secondary}</span>
    </ListRowButton>
  return <div className="market-instrument-navigator">
    <label className="market-instrument-search">
      <span>Find instrument</span>
      <input type="search" value={search} placeholder="Search symbol…" onChange={event => onSearch(event.target.value)} />
    </label>
    <div className="market-instrument-scroll">
      {term ? <section className="market-instrument-group" key="search">
        <div className="market-instrument-group-title"><span>Search results</span><Badge>{results.length}</Badge></div>
        {results.length ? results.map((entry, index) => {
          const instrument: MarketInstrument = { symbol: entry.symbol, exchange: entry.exchange, marketType: entry.marketType }
          const primary = `${fmtSymbol(entry.symbol.split(':')[0], entry.marketType)} · ${entry.marketType}`
          const secondary = `${entry.displayName} · ${entry.exchange}`
          return renderRow(instrument, primary, secondary, `${instrumentKey(instrument)}|${index}`)
        }) : <p className="market-instrument-empty">No instruments matched "{term}"</p>}
      </section> : groups.map(group => {
        const collapsed = collapsedGroupIds.has(group.id)
        const bodyId = `${bodyIdBase}-${group.id}`
        return <section className="market-instrument-group" key={group.id}>
          <Button
            type="button"
            variant="ghost"
            className="market-instrument-group-title"
            aria-expanded={!collapsed}
            aria-controls={bodyId}
            onClick={() => onToggleGroup(group.id)}
          >
            <span>{group.title}</span>
            <span className="market-instrument-group-title-meta">
              <Badge>{group.items.length}</Badge>
              <span className="market-instrument-group-chevron" aria-hidden="true">{collapsed ? '›' : '⌄'}</span>
            </span>
          </Button>
          <div id={bodyId} hidden={collapsed}>
            {group.items.length ? group.items.map((item, index) =>
              renderRow(item.instrument, item.primary, item.secondary, `${instrumentKey(item.instrument)}|${index}`, item.direction)
            ) : <p className="market-instrument-empty">{group.empty}</p>}
          </div>
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

function StructureTab({
  active,
  snapshot,
  microstructure,
  candidateDetail,
  entryPlan,
  position,
}: {
  active: MarketInstrument
  snapshot?: MarketSnapshot | null
  microstructure?: MarketMicrostructure | null
  candidateDetail?: CandidateDetail | null
  entryPlan?: EntryPlan | null
  position?: Position | null
}) {
  const member = candidateDetail?.member
  const attention = candidateDetail?.attention
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Card>
          <CardHeader title="Market Telemetry" meta={active.exchange || 'Default exchange'} />
          <div className="flex flex-col gap-1 text-sm">
            <div className="flex justify-between"><span className="text-muted">Symbol</span><span className="mono font-bold">{fmtSymbol(active.symbol, active.marketType)}</span></div>
            <div className="flex justify-between"><span className="text-muted">Market Type</span><span className="mono uppercase">{active.marketType}</span></div>
            <div className="flex justify-between"><span className="text-muted">Price</span><span className="mono">{snapshot?.price != null ? fmtNum(snapshot.price) : '—'}</span></div>
            <div className="flex justify-between"><span className="text-muted">24h Change</span><span className={`mono ${snapshot?.change_pct_24h != null && snapshot.change_pct_24h > 0 ? 'pnl-positive' : snapshot?.change_pct_24h != null && snapshot.change_pct_24h < 0 ? 'pnl-negative' : 'text-muted'}`}>{snapshot?.change_pct_24h != null ? `${fmtSigned(snapshot.change_pct_24h)}%` : '—'}</span></div>
            <div className="flex justify-between"><span className="text-muted">24h Volume</span><span className="mono">{snapshot?.volume_24h != null ? `$${fmtCompact(snapshot.volume_24h)}` : '—'}</span></div>
            {microstructure?.spread_bps != null && (
              <div className="flex justify-between"><span className="text-muted">Spread</span><span className="mono">{microstructure.spread_bps} bps</span></div>
            )}
            {microstructure?.depth_10bps_usd != null && (
              <div className="flex justify-between"><span className="text-muted">Depth (10bps)</span><span className="mono">${fmtCompact(microstructure.depth_10bps_usd)}</span></div>
            )}
            {microstructure?.funding_rate != null && (
              <div className="flex justify-between"><span className="text-muted">Funding Rate</span><span className="mono">{fmtSigned(microstructure.funding_rate * 100)}%</span></div>
            )}
            {microstructure?.open_interest_usd != null && (
              <div className="flex justify-between"><span className="text-muted">Open Interest</span><span className="mono">${fmtCompact(microstructure.open_interest_usd)}</span></div>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader title="Trade & Risk Geometry" meta={position ? 'Active position' : entryPlan ? 'Entry plan' : 'No open trade'} />
          <div className="flex flex-col gap-1 text-sm">
            {position ? (
              <>
                <div className="flex justify-between"><span className="text-muted">Direction</span><Badge tone={position.direction === 'long' ? 'green' : 'red'}>{humanize(position.direction)}</Badge></div>
                <div className="flex justify-between"><span className="text-muted">Entry Price</span><span className="mono">{fmtNum(position.entry_price)}</span></div>
                <div className="flex justify-between"><span className="text-muted">Quantity</span><span className="mono">{fmtNum(position.quantity)}</span></div>
                <div className="flex justify-between"><span className="text-muted">uPnL</span><span className="mono">{position.unrealized_pnl != null ? `${fmtSigned(position.unrealized_pnl)} USDT` : '—'}</span></div>
                <div className="flex justify-between"><span className="text-muted">Bracket (SL/TP)</span><span className="mono">{fmtNum(position.stop_price)} / {fmtNum(position.take_profit_price)}</span></div>
              </>
            ) : entryPlan ? (
              <>
                <div className="flex justify-between"><span className="text-muted">Plan Status</span><Badge tone="green">{humanize(entryPlan.status)}</Badge></div>
                <div className="flex justify-between"><span className="text-muted">Direction</span><span className="mono uppercase">{entryPlan.direction}</span></div>
                <div className="flex justify-between"><span className="text-muted">Mode</span><span className="mono">{humanize(entryPlan.execution_mode)}</span></div>
                <div className="flex justify-between"><span className="text-muted">Stop / Target</span><span className="mono">{fmtNum(entryPlan.plan?.stop_price)} / {fmtNum(entryPlan.plan?.target_price)}</span></div>
              </>
            ) : (
              <p className="text-muted text-sm m-0">No active position or planned entry for this instrument.</p>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader title="Candidate Selection Evidence" meta={candidateDetail ? 'Observed member' : 'Not in candidate pool'} />
          <div className="flex flex-col gap-1 text-sm">
            {candidateDetail ? (
              <>
                <div className="flex justify-between"><span className="text-muted">Rank</span><span className="mono font-bold">#{member?.rank ?? '—'}</span></div>
                <div className="flex justify-between"><span className="text-muted">Family</span><span>{humanize(attention?.candidate_family ?? member?.scanner_family ?? '—')}</span></div>
                <div className="flex justify-between"><span className="text-muted">{member?.admission_basis === 'startup' ? '冷启动：信号强度' : '收益排序：预期净收益'}</span><span className="mono">{member?.admission_basis === 'startup' ? (member.pessimistic_strength == null ? '未知' : fmtNum(member.pessimistic_strength)) : member?.candidate_selection_utility_pct != null ? `${fmtNum(member.candidate_selection_utility_pct)}%` : '未知'}</span></div>
                {member?.admission_basis === 'startup' && <p className="hint">预期净收益未知 · {({ no_history: '暂无历史样本', parent_sample_insufficient: '父组样本不足', child_sample_insufficient: '子组样本不足' } as Record<string, string>)[member.startup_reason ?? ''] ?? member.startup_reason ?? '原因未知'}</p>}
                <div className="flex justify-between"><span className="text-muted">Spread</span><span className="mono">{member?.spread_bps != null ? `${member.spread_bps} bps` : '—'}</span></div>
              </>
            ) : (
              <p className="text-muted text-sm m-0">Instrument selected directly via navigator (not linked from Candidate pool).</p>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}

export function SignalsTab({ onInstrumentSelect }: { onInstrumentSelect: (instrument: MarketInstrument) => void }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['opportunities'], queryFn: api.getOpportunities, refetchInterval: 60_000,
  })
  const opportunities = (data ?? []) as Opportunity[]
  const rows = opportunities.filter(opportunity => opportunity.asset_class === 'crypto' || !opportunity.asset_class)
    .sort((a, b) => (b.confidence_score ?? 0) - (a.confidence_score ?? 0))
  if (isLoading && !data) return <DataStateMessage state="loading" message="Loading signals…" />
  if (isError && !data) return <DataStateMessage state="unavailable" message="Market signals unavailable. The read failed; this is not an empty result." />
  if (isError && data && !rows.length) return <DataStateMessage state="stale" message="Market signals are stale. The failed latest read cannot confirm an empty market." />
  return <>
    {isError && data && <DataStateMessage state="stale" message="Market signals are stale. Retained rows may not reflect the latest read." />}
    {!rows.length ? <p className="hint">No active signals — market observer has not run yet or no opportunities are open.</p> : <Card><CardHeader title="AI Market Signals" /><TableWrap><table>
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
  </table></TableWrap></Card>}
  </>
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

function TimeframeMenu({ open, anchorRef, current, onSelect, onClose }: {
  open: boolean
  anchorRef: React.RefObject<HTMLButtonElement | null>
  current: string
  onSelect: (value: string) => void
  onClose: () => void
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  useDialogFocus(open, panelRef, onClose)
  useLayoutEffect(() => {
    if (!open || !anchorRef.current || !panelRef.current) return
    const anchor = anchorRef.current.getBoundingClientRect()
    const panel = panelRef.current
    const width = panel.offsetWidth
    const preferredLeft = anchor.right - width
    const left = Math.max(8, Math.min(preferredLeft, window.innerWidth - width - 8))
    setPos({ top: anchor.bottom + 4, left })
  }, [open, anchorRef])
  if (!open) return null
  return <>
    <div className="market-tf-backdrop" onMouseDown={onClose} />
    <div
      ref={panelRef}
      role="menu"
      tabIndex={-1}
      aria-label="More timeframes"
      className="market-tf-menu"
      style={pos ? { top: pos.top, left: pos.left, opacity: 1 } : { top: 0, left: 0, visibility: 'hidden' }}
      onMouseDown={event => event.stopPropagation()}
    >
      {OVERFLOW_TIMEFRAME_GROUPS.map(group => <div className="market-tf-menu-group" key={group.title}>
        <div className="market-tf-menu-group-title" role="presentation">{group.title}</div>
        <div className="market-tf-menu-grid">
          {group.items.map(value => <Button
            key={value}
            type="button"
            variant="ghost"
            role="menuitem"
            aria-checked={value === current}
            className="market-tf-menu-item"
            onClick={() => { onSelect(value); onClose() }}
          >{value}</Button>)}
        </div>
      </div>)}
    </div>
  </>
}


const CHART_SETTINGS_STORAGE_KEY = 'aletheia.charts.settings.v1'

const MAIN_INDICATOR_LABELS: Record<MainIndicatorId, string> = {
  vwap: 'VWAP',
  ma: 'MA 5/10/20',
  ema20: 'EMA 20',
  ema50: 'EMA 50',
  bollinger: 'Bollinger Bands 20/2',
  sar: 'SAR',
  bbi: 'BBI',
}
const SUBCHART_INDICATOR_LABELS: Record<SubchartIndicatorId, string> = {
  vol: 'Volume',
  macd: 'MACD 12/26/9',
  rsi: 'RSI 14',
  stochastic: 'Stochastic 14/3/3',
  obv: 'OBV',
  wr: 'Williams %R 14',
  atr: 'ATR 14',
}

function sanitizeIndicatorArray<T extends string>(values: unknown, allowed: readonly T[]): T[] {
  if (!Array.isArray(values)) return []
  const seen = new Set<T>()
  for (const value of values) {
    if (typeof value === 'string' && (allowed as readonly string[]).includes(value)) {
      seen.add(value as T)
    }
    if (seen.size >= MAX_INDICATORS_PER_ZONE) break
  }
  return [...seen]
}

function hydrateChartSettings(): ChartSettings {
  if (typeof window === 'undefined') return DEFAULT_CHART_SETTINGS
  try {
    const raw = window.localStorage.getItem(CHART_SETTINGS_STORAGE_KEY)
    if (!raw) return DEFAULT_CHART_SETTINGS
    const parsed = JSON.parse(raw) as
      | (Partial<Omit<ChartSettings, 'indicators'>> & {
          indicators?: {
            main?: unknown
            subcharts?: unknown
            rsi?: { enabled?: unknown }
          }
        })
      | null
    if (!parsed || typeof parsed !== 'object') return DEFAULT_CHART_SETTINGS
    const legacyRsi = parsed.indicators?.rsi
    const hasNewShape = parsed.indicators
      && (Array.isArray(parsed.indicators.main) || Array.isArray(parsed.indicators.subcharts))
    const indicators: ChartSettings['indicators'] = hasNewShape
      ? {
          main: sanitizeIndicatorArray(parsed.indicators?.main, MAIN_INDICATOR_IDS),
          subcharts: sanitizeIndicatorArray(parsed.indicators?.subcharts, SUBCHART_INDICATOR_IDS),
        }
      : {
          main: [],
          subcharts: legacyRsi && legacyRsi.enabled === false ? [] : ['rsi'],
        }
    return {
      view: {
        chartType: 'candlestick',
        crosshair: parsed.view?.crosshair ?? DEFAULT_CHART_SETTINGS.view.crosshair,
        grid: {
          horizontal: parsed.view?.grid?.horizontal ?? DEFAULT_CHART_SETTINGS.view.grid.horizontal,
          vertical: parsed.view?.grid?.vertical ?? DEFAULT_CHART_SETTINGS.view.grid.vertical,
        },
        timezone: parsed.view?.timezone === 'local' ? 'local' : 'UTC',
      },
      scale: {
        mode: parsed.scale?.mode ?? DEFAULT_CHART_SETTINGS.scale.mode,
        autoScale: parsed.scale?.autoScale ?? DEFAULT_CHART_SETTINGS.scale.autoScale,
        priceLabels: parsed.scale?.priceLabels ?? DEFAULT_CHART_SETTINGS.scale.priceLabels,
      },
      overlays: {
        volume: parsed.overlays?.volume ?? DEFAULT_CHART_SETTINGS.overlays.volume,
        profile: parsed.overlays?.profile ?? DEFAULT_CHART_SETTINGS.overlays.profile,
        levels: parsed.overlays?.levels ?? DEFAULT_CHART_SETTINGS.overlays.levels,
        signals: parsed.overlays?.signals ?? DEFAULT_CHART_SETTINGS.overlays.signals,
      },
      indicators,
    }
  } catch {
    return DEFAULT_CHART_SETTINGS
  }
}

function ChartSettings({ open, scope, settings, onChange, onReset, onClose }: {
  open: boolean
  scope: string
  settings: ChartSettings
  onChange: (patch: (prev: ChartSettings) => ChartSettings) => void
  onReset: () => void
  onClose: () => void
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const titleId = useId()
  useDialogFocus(open, panelRef, onClose)
  if (!open) return null
  return <div className="chart-settings-backdrop" onMouseDown={onClose}>
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabIndex={-1}
      ref={panelRef}
      className="chart-settings-modal"
      onMouseDown={event => event.stopPropagation()}
    >
      <div className="chart-settings-header">
        <h3 id={titleId}>Chart settings</h3>
        <span className="chart-settings-scope">{scope}</span>
        <Button variant="secondary" onClick={onClose} aria-label="Close chart settings">Close</Button>
      </div>
      <div className="chart-settings-body">
        <section className="chart-settings-section">
          <div className="chart-settings-section-title">View</div>
          <div className="chart-settings-row">
            <span>Chart type</span>
            <span className="chart-settings-static mono">Candlestick</span>
          </div>
          <label className="chart-settings-row">
            <span>Crosshair</span>
            <select value={settings.view.crosshair} onChange={event => onChange(prev => ({ ...prev, view: { ...prev.view, crosshair: event.target.value as ChartSettings['view']['crosshair'] } }))}>
              <option value="magnet">Magnet</option>
              <option value="normal">Normal</option>
              <option value="hidden">Hidden</option>
            </select>
          </label>
          <label className="chart-settings-row">
            <span>Horizontal grid</span>
            <input type="checkbox" checked={settings.view.grid.horizontal} onChange={event => onChange(prev => ({ ...prev, view: { ...prev.view, grid: { ...prev.view.grid, horizontal: event.target.checked } } }))} />
          </label>
          <label className="chart-settings-row">
            <span>Vertical grid</span>
            <input type="checkbox" checked={settings.view.grid.vertical} onChange={event => onChange(prev => ({ ...prev, view: { ...prev.view, grid: { ...prev.view.grid, vertical: event.target.checked } } }))} />
          </label>
          <label className="chart-settings-row">
            <span>Timezone</span>
            <select value={settings.view.timezone} onChange={event => onChange(prev => ({ ...prev, view: { ...prev.view, timezone: event.target.value as ChartSettings['view']['timezone'] } }))}>
              <option value="UTC">UTC</option>
              <option value="local">Local</option>
            </select>
          </label>
          <p className="chart-settings-row-hint">Formats time axis labels only. Candle aggregation remains UTC.</p>
        </section>
        <section className="chart-settings-section">
          <div className="chart-settings-section-title">Price scale</div>
          <label className="chart-settings-row">
            <span>Scale</span>
            <select value={settings.scale.mode} onChange={event => onChange(prev => ({ ...prev, scale: { ...prev.scale, mode: event.target.value as ChartSettings['scale']['mode'] } }))}>
              <option value="normal">Normal</option>
              <option value="logarithmic">Logarithmic</option>
              <option value="percent">Percent</option>
              <option value="indexed100">Indexed to 100</option>
            </select>
          </label>
          <label className="chart-settings-row">
            <span>Auto scale</span>
            <input type="checkbox" checked={settings.scale.autoScale} onChange={event => onChange(prev => ({ ...prev, scale: { ...prev.scale, autoScale: event.target.checked } }))} />
          </label>
          <label className="chart-settings-row">
            <span>Show right-axis labels</span>
            <input type="checkbox" checked={settings.scale.priceLabels} onChange={event => onChange(prev => ({ ...prev, scale: { ...prev.scale, priceLabels: event.target.checked } }))} />
          </label>
        </section>
        <section className="chart-settings-section">
          <div className="chart-settings-section-title">Overlays</div>
          <label className="chart-settings-row">
            <span>Volume</span>
            <input type="checkbox" checked={settings.overlays.volume} onChange={event => onChange(prev => ({ ...prev, overlays: { ...prev.overlays, volume: event.target.checked } }))} />
          </label>
          <label className="chart-settings-row">
            <span>Estimated profile</span>
            <input type="checkbox" checked={settings.overlays.profile} onChange={event => onChange(prev => ({ ...prev, overlays: { ...prev.overlays, profile: event.target.checked } }))} />
          </label>
          <p className="chart-settings-row-hint">Visible-range estimate · not exchange-provided.</p>
          <label className="chart-settings-row">
            <span>Position levels</span>
            <input type="checkbox" checked={settings.overlays.levels} onChange={event => onChange(prev => ({ ...prev, overlays: { ...prev.overlays, levels: event.target.checked } }))} />
          </label>
          <label className="chart-settings-row">
            <span>Signals</span>
            <input type="checkbox" checked={settings.overlays.signals} onChange={event => onChange(prev => ({ ...prev, overlays: { ...prev.overlays, signals: event.target.checked } }))} />
          </label>
        </section>
        <section className="chart-settings-section">
          <div className="chart-settings-section-title">
            Main chart indicators · {settings.indicators.main.length}/{MAX_INDICATORS_PER_ZONE}
          </div>
          {MAIN_INDICATOR_IDS.map(id => {
            const active = settings.indicators.main.includes(id)
            const full = settings.indicators.main.length >= MAX_INDICATORS_PER_ZONE
            return <label className="chart-settings-row" key={id}>
              <span>{MAIN_INDICATOR_LABELS[id]}</span>
              <input
                type="checkbox"
                checked={active}
                disabled={!active && full}
                onChange={event => onChange(prev => {
                  const current = prev.indicators.main
                  if (event.target.checked) {
                    if (current.includes(id) || current.length >= MAX_INDICATORS_PER_ZONE) return prev
                    return { ...prev, indicators: { ...prev.indicators, main: [...current, id] } }
                  }
                  return { ...prev, indicators: { ...prev.indicators, main: current.filter(entry => entry !== id) } }
                })}
              />
            </label>
          })}
          {settings.indicators.main.length >= MAX_INDICATORS_PER_ZONE
            && <p className="chart-settings-row-hint">Remove one indicator to add another.</p>}
        </section>
        <section className="chart-settings-section">
          <div className="chart-settings-section-title">
            Subcharts · {settings.indicators.subcharts.length}/{MAX_INDICATORS_PER_ZONE}
          </div>
          {SUBCHART_INDICATOR_IDS.map(id => {
            const active = settings.indicators.subcharts.includes(id)
            const full = settings.indicators.subcharts.length >= MAX_INDICATORS_PER_ZONE
            return <label className="chart-settings-row" key={id}>
              <span>{SUBCHART_INDICATOR_LABELS[id]}</span>
              <input
                type="checkbox"
                checked={active}
                disabled={!active && full}
                onChange={event => onChange(prev => {
                  const current = prev.indicators.subcharts
                  if (event.target.checked) {
                    if (current.includes(id) || current.length >= MAX_INDICATORS_PER_ZONE) return prev
                    return { ...prev, indicators: { ...prev.indicators, subcharts: [...current, id] } }
                  }
                  return { ...prev, indicators: { ...prev.indicators, subcharts: current.filter(entry => entry !== id) } }
                })}
              />
            </label>
          })}
          {settings.indicators.subcharts.length >= MAX_INDICATORS_PER_ZONE
            && <p className="chart-settings-row-hint">Remove one indicator to add another.</p>}
        </section>
      </div>
      <div className="chart-settings-footer">
        <Button variant="ghost" onClick={onReset}>Restore defaults</Button>
        <Button variant="secondary" onClick={onClose}>Close</Button>
      </div>
    </div>
  </div>
}


export default function Charts() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const entryPlanId = searchParams.get('entry_plan_id')
  const candidateId = searchParams.get('candidate_id')
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
  const [chartSettings, setChartSettings] = useState<ChartSettings>(hydrateChartSettings)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [dataMeta, setDataMeta] = useState<OhlcvResult['meta'] | null>(null)
  const [timeframeMenuOpen, setTimeframeMenuOpen] = useState(false)
  const [simMode, setSimMode] = useState(false)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const clearEntryPlanOnNextSync = useRef(false)
  const [collapsedGroupIds, setCollapsedGroupIds] = useState<ReadonlySet<string>>(() => new Set<string>())
  const toggleGroupCollapse = useCallback((id: string) => {
    setCollapsedGroupIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])
  const timeframeOverflowRef = useRef<HTMLButtonElement>(null)
  const shellRef = useRef<HTMLDivElement>(null)
  const { data: defaultExchangeConfig } = useQuery({
    queryKey: ['config-default-exchange'],
    queryFn: api.getDefaultExchange,
    staleTime: 5 * 60_000,
  })
  const defaultExchange = (defaultExchangeConfig?.exchange || 'bitget').toLowerCase()
  const dialogId = useId()
  const { data: entryPlan, isError: entryPlanUnavailable } = useQuery<EntryPlan>({
    queryKey: ['entry-plan', entryPlanId],
    queryFn: () => api.getEntryPlan(entryPlanId!),
    enabled: Boolean(entryPlanId),
  })
  const { data: candidateDetail } = useQuery({
    queryKey: ['candidate-charts', candidateId],
    queryFn: () => api.getCandidate(candidateId!),
    enabled: Boolean(candidateId),
  })

  useEffect(() => {
    if (!candidateId || !candidateDetail) return
    const m = candidateDetail.attention ?? candidateDetail.member
    if (m?.symbol) {
      setActive({
        symbol: m.symbol,
        exchange: ('venue' in m && typeof m.venue === 'string' && m.venue) || defaultExchange,
        marketType: ('market_type' in m && typeof m.market_type === 'string' && m.market_type) || 'spot',
      })
    }
  }, [candidateDetail, candidateId, defaultExchange])

  useEffect(() => {
    if (!entryPlanId || !entryPlan) return
    setActive({ symbol: entryPlan.symbol, exchange: entryPlan.venue, marketType: entryPlan.market_type })
  }, [entryPlan, entryPlanId])

  useEffect(() => {
    const params: Record<string, string> = { symbol: active.symbol }
    if (active.exchange) params.exchange = active.exchange
    if (active.marketType !== 'spot') params.market_type = active.marketType
    if (entryPlanId && !clearEntryPlanOnNextSync.current) params.entry_plan_id = entryPlanId
    if (candidateId) params.candidate_id = candidateId
    clearEntryPlanOnNextSync.current = false
    setSearchParams(params, { replace: true })
  }, [active, candidateId, entryPlanId, setSearchParams])
  const { data: universe } = useQuery({ queryKey: ['universe-current'], queryFn: api.getUniverseCurrent, refetchInterval: 120_000 })
  const { data: snapshots = [] } = useQuery({ queryKey: ['market-snapshots'], queryFn: () => api.getMarketSnapshots(undefined, 500), refetchInterval: 30_000 })
  const { data: positions = [] } = useQuery({ queryKey: ['positions'], queryFn: () => api.getPositions(), refetchInterval: 30_000 })
  const { data: opportunityInstruments = [] } = useQuery({
    queryKey: ['market-opportunity-instruments'], queryFn: api.getMarketOpportunityInstruments, refetchInterval: 60_000,
  })
  const { data: opportunities = [] } = useQuery({
    queryKey: ['market-opportunities', active.symbol, active.marketType],
    queryFn: () => api.getMarketOpportunities(active.symbol, active.marketType), refetchInterval: 60_000,
  })
  const { data: trades = [] } = useQuery({ queryKey: ['trades'], queryFn: () => api.getTrades(), refetchInterval: 30_000 })
  const { data: microstructure } = useQuery({
    queryKey: ['market-microstructure', active.symbol, active.exchange, active.marketType],
    queryFn: () => api.getMarketMicrostructure(active.symbol, active.exchange, active.marketType),
    refetchInterval: 15_000,
  })

  const snapshotMap = useMemo(() => {
    const map = new Map<string, MarketSnapshot>()
    for (const snapshot of snapshots) map.set(`${snapshot.symbol}|${snapshot.market_type || 'spot'}`, snapshot)
    return map
  }, [snapshots])
  const snapshotFor = useCallback((instrument: MarketInstrument) => snapshotMap.get(`${instrument.symbol}|${instrument.marketType}`)
    ?? snapshotMap.get(`${instrument.symbol.replace('/', '')}|${instrument.marketType}`), [snapshotMap])

  const groups = useMemo<InstrumentGroup[]>(() => {
    const universeMarket = universe?.snapshot?.market_type || 'spot'
    const universeItems = (universe?.members || []).map((member: Candidate) => {
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
  const entryPlanMatchesActive = Boolean(entryPlanId && entryPlan
    && entryPlan.venue === active.exchange
    && entryPlan.symbol === active.symbol
    && entryPlan.market_type === active.marketType)
  const positionLevels = useMemo<PositionLevel[]>(() => {
    const planLevels: PositionLevel[] = entryPlanMatchesActive && entryPlan?.plan ? [
      ...entryPlan.plan.entry_zone.map((price, index) => ({ id: `${entryPlan.id}-entry-${index}`, kind: 'entry' as const, price, title: `Entry Plan zone ${index + 1}` })),
      { id: `${entryPlan.id}-invalidation`, kind: 'stop' as const, price: entryPlan.plan.invalidation_price, title: 'Entry Plan invalidation' },
      { id: `${entryPlan.id}-stop`, kind: 'stop' as const, price: entryPlan.plan.stop_price, title: 'Entry Plan stop' },
      { id: `${entryPlan.id}-target`, kind: 'take_profit' as const, price: entryPlan.plan.target_price, title: 'Entry Plan target' },
      ...(entryPlan.plan.limit_price ? [{ id: `${entryPlan.id}-limit`, kind: 'entry' as const, price: entryPlan.plan.limit_price, title: 'Entry Plan resting limit' }] : []),
      ...Object.entries(entryPlan.plan.trigger_params ?? {})
        .filter(([key, value]) => (key.includes('level') || key.includes('price')) && typeof value === 'number')
        .map(([key, price]) => ({ id: `${entryPlan.id}-trigger-${key}`, kind: 'entry' as const, price: Number(price), title: `Trigger ${key.replaceAll('_', ' ')}` })),
    ] : []
    const openPositionLevels = matchingPositions.flatMap(position => {
    const direction = position.direction || 'Position'
    return [
      { id: `${position.id}-entry`, kind: 'entry' as const, price: position.entry_price, title: `Position entry · ${direction}` },
      { id: `${position.id}-stop`, kind: 'stop' as const, price: position.stop_price ?? 0, title: `Stop · ${direction}` },
      { id: `${position.id}-tp`, kind: 'take_profit' as const, price: position.take_profit_price ?? 0, title: `Take profit · ${direction}` },
      { id: `${position.id}-liq`, kind: 'liquidation' as const, price: position.liquidation_price ?? 0, title: `Estimated liquidation · ${direction}` },
    ]
    })
    return [...planLevels, ...openPositionLevels].filter(level => Number.isFinite(level.price) && level.price > 0)
  }, [entryPlan, entryPlanMatchesActive, matchingPositions])

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

  const quickSymbols = useMemo(() => {
    const list: Array<{
      symbol: string
      exchange: string
      marketType: string
      label: string
      badge?: string
      tone?: 'green' | 'red' | 'blue'
    }> = []
    const seen = new Set<string>()

    for (const pos of positions) {
      if (pos.status !== 'open' || seen.has(pos.symbol)) continue
      seen.add(pos.symbol)
      const pnl = pos.unrealized_pnl ?? 0
      list.push({
        symbol: pos.symbol,
        exchange: active.exchange || defaultExchange,
        marketType: pos.market_type || 'perp',
        label: fmtSymbol(pos.symbol, pos.market_type),
        badge: pnl !== 0 ? fmtSigned(pnl) : pos.direction,
        tone: pnl > 0 ? 'green' : pnl < 0 ? 'red' : 'blue',
      })
    }

    for (const member of (universe?.members || []).slice(0, 6)) {
      if (seen.has(member.symbol)) continue
      seen.add(member.symbol)
      const mType = universe?.snapshot?.market_type || 'spot'
      list.push({
        symbol: member.symbol,
        exchange: defaultExchange,
        marketType: mType,
        label: fmtSymbol(member.symbol, mType),
        badge: `#${member.rank}`,
      })
    }

    const defaults = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'DOGE/USDT']
    for (const sym of defaults) {
      if (seen.has(sym)) continue
      seen.add(sym)
      list.push({
        symbol: sym,
        exchange: defaultExchange,
        marketType: 'perp',
        label: fmtSymbol(sym, 'perp'),
      })
      if (list.length >= 8) break
    }

    return list.slice(0, 8)
  }, [positions, universe, defaultExchange, active.exchange])

  const toggleQuickIndicator = useCallback((kind: 'ma' | 'boll') => {
    setChartSettings(prev => {
      const current = prev.indicators.main
      let next: MainIndicatorId[]
      if (kind === 'ma') {
        const hasMa = current.includes('ema20') || current.includes('ema50')
        next = hasMa
          ? current.filter(id => id !== 'ema20' && id !== 'ema50')
          : [...current.filter(id => id !== 'ema20' && id !== 'ema50'), 'ema20', 'ema50'].slice(0, 3) as MainIndicatorId[]
      } else {
        const hasBoll = current.includes('bollinger')
        next = hasBoll
          ? current.filter(id => id !== 'bollinger')
          : [...current, 'bollinger'].slice(0, 3) as MainIndicatorId[]
      }
      return { ...prev, indicators: { ...prev.indicators, main: next } }
    })
  }, [])
  const activeR20Indicators = useMemo<Record<string, boolean>>(() => {
    const main = chartSettings.indicators.main
    const sub = chartSettings.indicators.subcharts
    return {
      VWAP: main.includes('vwap'),
      MA: main.includes('ma'),
      EMA: main.includes('ema20') || main.includes('ema50'),
      BOLL: main.includes('bollinger'),
      SAR: main.includes('sar'),
      BBI: main.includes('bbi'),
      VOL: chartSettings.overlays.volume || sub.includes('vol'),
      MACD: sub.includes('macd'),
      RSI: sub.includes('rsi'),
      KDJ: sub.includes('stochastic'),
      OBV: sub.includes('obv'),
      WR: sub.includes('wr'),
    }
  }, [chartSettings])

  const handleToggleR20Indicator = useCallback((key: string) => {
    setChartSettings(prev => {
      const main = [...prev.indicators.main]
      const sub = [...prev.indicators.subcharts]
      const overlays = { ...prev.overlays }

      if (key === 'VWAP') {
        const idx = main.indexOf('vwap')
        if (idx >= 0) main.splice(idx, 1)
        else if (main.length < MAX_INDICATORS_PER_ZONE) main.push('vwap')
      } else if (key === 'MA') {
        const idx = main.indexOf('ma')
        if (idx >= 0) main.splice(idx, 1)
        else if (main.length < MAX_INDICATORS_PER_ZONE) main.push('ma')
      } else if (key === 'EMA') {
        const has = main.includes('ema20') || main.includes('ema50')
        if (has) {
          const m1 = main.filter(id => id !== 'ema20' && id !== 'ema50')
          return { ...prev, indicators: { ...prev.indicators, main: m1 } }
        } else if (main.length < MAX_INDICATORS_PER_ZONE) {
          main.push('ema20')
        }
      } else if (key === 'BOLL') {
        const idx = main.indexOf('bollinger')
        if (idx >= 0) main.splice(idx, 1)
        else if (main.length < MAX_INDICATORS_PER_ZONE) main.push('bollinger')
      } else if (key === 'SAR') {
        const idx = main.indexOf('sar')
        if (idx >= 0) main.splice(idx, 1)
        else if (main.length < MAX_INDICATORS_PER_ZONE) main.push('sar')
      } else if (key === 'BBI') {
        const idx = main.indexOf('bbi')
        if (idx >= 0) main.splice(idx, 1)
        else if (main.length < MAX_INDICATORS_PER_ZONE) main.push('bbi')
      } else if (key === 'VOL') {
        overlays.volume = !overlays.volume
      } else if (key === 'MACD') {
        const idx = sub.indexOf('macd')
        if (idx >= 0) sub.splice(idx, 1)
        else if (sub.length < MAX_INDICATORS_PER_ZONE) sub.push('macd')
      } else if (key === 'RSI') {
        const idx = sub.indexOf('rsi')
        if (idx >= 0) sub.splice(idx, 1)
        else if (sub.length < MAX_INDICATORS_PER_ZONE) sub.push('rsi')
      } else if (key === 'KDJ') {
        const idx = sub.indexOf('stochastic')
        if (idx >= 0) sub.splice(idx, 1)
        else if (sub.length < MAX_INDICATORS_PER_ZONE) sub.push('stochastic')
      } else if (key === 'OBV') {
        const idx = sub.indexOf('obv')
        if (idx >= 0) sub.splice(idx, 1)
        else if (sub.length < MAX_INDICATORS_PER_ZONE) sub.push('obv')
      } else if (key === 'WR') {
        const idx = sub.indexOf('wr')
        if (idx >= 0) sub.splice(idx, 1)
        else if (sub.length < MAX_INDICATORS_PER_ZONE) sub.push('wr')
      }

      return {
        ...prev,
        overlays,
        indicators: { main, subcharts: sub },
      }
    })
  }, [])

  const handleRefresh = useCallback(() => {
    setIsRefreshing(true)
    setTimeout(() => setIsRefreshing(false), 800)
  }, [])

  const currentSnapshot = snapshotFor(active)
  const selectInstrument = useCallback((instrument: MarketInstrument) => {
    if (entryPlanId && (!entryPlan
      || entryPlan.venue !== instrument.exchange
      || entryPlan.symbol !== instrument.symbol
      || entryPlan.market_type !== instrument.marketType)) {
      clearEntryPlanOnNextSync.current = true
    }
    setSelectedEvents([])
    setActive(instrument)
    setPickerOpen(false)
  }, [entryPlan, entryPlanId])
  const selectFromSignals = useCallback((instrument: MarketInstrument) => {
    selectInstrument(instrument)
    document.getElementById('market-chart-toolbar')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [selectInstrument])
  const signalCount = opportunities.filter(opportunity => opportunity.status === 'open').length
  const metaAge = dataMeta?.ageMs == null ? 'age not available' : dataMeta.ageMs < 60_000 ? `${Math.floor(dataMeta.ageMs / 1000)}s ago` : `${Math.floor(dataMeta.ageMs / 60_000)}m ago`
  const navigator = <InstrumentNavigator
    groups={groups}
    active={active}
    search={search}
    onSearch={setSearch}
    onSelect={selectInstrument}
    defaultExchange={defaultExchange}
    collapsedGroupIds={collapsedGroupIds}
    onToggleGroup={toggleGroupCollapse}
  />
  useEffect(() => {
    if (typeof window === 'undefined') return
    try { window.localStorage.setItem(CHART_SETTINGS_STORAGE_KEY, JSON.stringify(chartSettings)) } catch { /* localStorage unavailable */ }
  }, [chartSettings])
  const resetChartSettings = useCallback(() => setChartSettings(DEFAULT_CHART_SETTINGS), [])

  // The chart card + rail must fit inside the initial viewport with no page
  // scroll: bind their height to `viewport - shell.top - bottom-gutter` so both
  // columns end at the visible viewport edge. Recompute on resize; PriceChart's
  // ResizeObserver reflows the canvas.
  useLayoutEffect(() => {
    if (typeof window === 'undefined') return
    const shell = shellRef.current
    if (!shell) return
    const apply = () => {
      const top = shell.getBoundingClientRect().top
      const height = Math.max(360, window.innerHeight - top - 16)
      shell.style.setProperty('--charts-shell-height', `${height}px`)
    }
    apply()
    window.addEventListener('resize', apply)
    return () => window.removeEventListener('resize', apply)
  }, [])

  return <div className="markets-page">
    <PageHeader
      title="Markets"
      role="Exact-instrument market workbench · price, volume, structure, positions, and observed signals."
      actions={<span className="market-header-note">Exact instrument · observed market data</span>}
    />

    <div className="market-workbench-context">
      <Button variant="ghost" className="market-context-picker" aria-expanded={pickerOpen} aria-controls={dialogId} onClick={() => setPickerOpen(true)}>
        <strong className="mono">{fmtSymbol(active.symbol, active.marketType).toUpperCase()} {active.marketType === 'perp' ? 'PERP' : active.marketType === 'futures' ? 'FUT' : ''}</strong>
        <span className="text-muted mono">· {(active.exchange || 'exchange').toUpperCase()}</span>
        <span aria-hidden="true" className="text-muted">⌄</span>
      </Button>
      <strong className="market-context-price mono">{currentSnapshot?.price == null ? '—' : fmtNum(currentSnapshot.price)}</strong>
      {currentSnapshot?.change_pct_24h != null && (
        <span className={`mono ${currentSnapshot.change_pct_24h > 0 ? 'pnl-positive' : currentSnapshot.change_pct_24h < 0 ? 'pnl-negative' : 'text-muted'}`}>
          {currentSnapshot.change_pct_24h > 0 ? '▲' : currentSnapshot.change_pct_24h < 0 ? '▼' : ''}{fmtDelta(currentSnapshot.change_pct_24h)}%
        </span>
      )}
      {currentSnapshot?.volume_24h != null && (
        <span className="mono text-muted">VOL ${fmtCompact(currentSnapshot.volume_24h)}</span>
      )}
      {microstructure?.funding_rate != null && (
        <span className="mono text-muted">FR {(microstructure.funding_rate * 100).toFixed(4)}%</span>
      )}
      {microstructure?.spread_bps != null && (
        <span className="mono text-muted">SPREAD {microstructure.spread_bps} bp</span>
      )}
      {dataMeta?.ageMs != null && (
        <span className="mono text-muted market-context-age">{metaAge}</span>
      )}
    </div>
    <div className="market-quick-pills-bar" aria-label="Quick instrument switcher">
      <span className="text-2xs text-muted mono uppercase font-semibold mr-1">Watch:</span>
      <div className="flex items-center gap-1 overflow-x-auto py-0.5">
        {quickSymbols.map(item => {
          const isSelected = active.symbol === item.symbol && active.marketType === item.marketType
          return (
            <button
              key={`${item.symbol}-${item.marketType}`}
              type="button"
              className={`market-quick-pill${isSelected ? ' active' : ''}`}
              onClick={() => selectInstrument({ symbol: item.symbol, exchange: item.exchange, marketType: item.marketType })}
            >
              <span className="mono font-semibold">{item.label}</span>
              {item.badge && <span className={`market-quick-pill-badge ${item.tone ?? 'neutral'}`}>{item.badge}</span>}
            </button>
          )
        })}
      </div>
    </div>
    {entryPlanId && <Card className="entry-workbench-notice">
      {entryPlanUnavailable ? 'Exact Entry Plan overlay is unavailable.' : entryPlan ? `Entry Plan ${entryPlan.id.slice(0, 8)} · ${humanize(entryPlan.status)} · immutable geometry overlay` : 'Loading exact Entry Plan overlay…'}
    </Card>}
    {candidateId && !entryPlanId && <Card className="entry-workbench-notice">
      Candidate {candidateId.slice(0, 8)} · {candidateDetail?.attention?.candidate_family ?? candidateDetail?.member?.scanner_family ?? 'observed'} · {candidateDetail?.member?.admission_basis === 'startup' ? '冷启动：信号强度' : candidateDetail?.member?.candidate_selection_policy === 'candidate_selection_utility_v1' ? '收益排序：预期净收益' : '历史 Candidate'}
    </Card>}
    <div ref={shellRef} className="market-workbench-shell">
      <aside className="market-instrument-rail">{navigator}</aside>
      <Card className="market-workbench-chart-card">
        <div id="market-chart-toolbar" className="market-chart-toolbar">
          <div className="flex items-center gap-2 flex-wrap">
            <div className="seg-toggle" aria-label="Timeframe">
              {PINNED_TIMEFRAMES.map(value => <Button key={value} variant="ghost" className={`seg-btn${timeframe === value ? ' active' : ''}`} aria-pressed={timeframe === value} onClick={() => setTimeframe(value)}>{value}</Button>)}
              <Button
                ref={timeframeOverflowRef}
                type="button"
                variant="ghost"
                className={`seg-btn market-tf-overflow${!PINNED_TIMEFRAMES.includes(timeframe) ? ' active' : ''}`}
                aria-haspopup="menu"
                aria-expanded={timeframeMenuOpen}
                aria-label="More timeframes"
                onClick={() => setTimeframeMenuOpen(open => !open)}
              >{PINNED_TIMEFRAMES.includes(timeframe) ? '⋯' : timeframe}</Button>
            </div>

            <R20IndicatorWorkbench
              timeframe={timeframe}
              activeIndicators={activeR20Indicators}
              onToggleIndicator={handleToggleR20Indicator}
              onRefresh={handleRefresh}
              isRefreshing={isRefreshing}
              currentPrice={currentSnapshot?.price ?? 0}
              currentAtr={microstructure?.spread_bps ? (currentSnapshot?.price ?? 0) * 0.015 : 0}
              symbol={active.symbol}
              simMode={simMode}
              onToggleSimMode={setSimMode}
              onResetSimulation={() => {}}
            />
          </div>

          <div className="flex items-center gap-1.5">
            {matchingPositions.length > 0 && (
              <Button
                type="button"
                variant="secondary"
                className="btn-xs"
                onClick={() => navigate(`/trades?symbol=${encodeURIComponent(active.symbol)}`)}
                title="View in Blotter"
              >
                Blotter ↗
              </Button>
            )}
            <Button
              type="button"
              variant="secondary"
              className="btn-xs"
              onClick={() => navigate(`/universe?tab=candidates&symbol=${encodeURIComponent(active.symbol)}`)}
              title="Open in Entry Workbench"
            >
              + Plan ↗
            </Button>
            <Button variant="ghost" className="market-chart-settings-button" aria-haspopup="dialog" aria-expanded={settingsOpen} onClick={() => setSettingsOpen(true)}>Chart settings</Button>
          </div>
        </div>
        <PriceChart
          symbol={active.symbol}
          exchange={active.exchange}
          marketType={active.marketType}
          timeframe={timeframe}
          markers={markers}
          positionLevels={positionLevels}
          settings={chartSettings}
          onMarkerClick={setSelectedEvents}
          onMetaChange={setDataMeta}
        />
      </Card>
    </div>

    <InstrumentDialog open={pickerOpen} id={dialogId} onClose={() => setPickerOpen(false)}>{navigator}</InstrumentDialog>
    <MacroBar />
    <Tabs value={activeTab} onChange={id => setActiveTab(id)} tabs={([
      { id: 'structure', label: 'Structure & Profile', render: () => (
        <StructureTab
          active={active}
          snapshot={currentSnapshot}
          microstructure={microstructure}
          candidateDetail={candidateDetail}
          entryPlan={entryPlan}
          position={positions.find(p => p.symbol === active.symbol && p.status === 'open') ?? null}
        />
      )},
      { id: 'signals', label: 'Signals', badge: signalCount || undefined, render: () => <SignalsTab onInstrumentSelect={selectFromSignals} /> },
      { id: 'predictions', label: 'Predictions', render: () => <PredictionsTab /> },
    ]) as TabDef[]} />
    <ChartSettings
      open={settingsOpen}
      scope={`${fmtSymbol(active.symbol, active.marketType)} · ${active.marketType}`}
      settings={chartSettings}
      onChange={setChartSettings}
      onReset={resetChartSettings}
      onClose={() => setSettingsOpen(false)}
    />
    <EventDrawer events={selectedEvents} trades={trades} opportunities={opportunities} symbol={active.symbol} onClose={() => setSelectedEvents([])} />
    <TimeframeMenu
      open={timeframeMenuOpen}
      anchorRef={timeframeOverflowRef}
      current={timeframe}
      onSelect={value => { setTimeframe(value); setTimeframeMenuOpen(false) }}
      onClose={() => setTimeframeMenuOpen(false)}
    />
  </div>
}
