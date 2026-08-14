import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  api, type MarketNewsItem, type MarketOpportunityInstrument, type MarketTrade, type OhlcvResult,
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
import { useDialogFocus } from '../components/ui/useDialogFocus'
import { fmtDelta, fmtNum, fmtSigned, fmtSymbol, fmtTimestamp } from '../lib/format'
import type { MarketSnapshot, Opportunity, Position, PredictionMarket, UniverseMember } from '../types'
import { searchInstruments } from '../charts/instrumentCatalog'

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
      style={pos ? { top: pos.top, left: pos.left, visibility: 'visible' } : { top: -9999, left: -9999, visibility: 'hidden' }}
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
  ema20: 'EMA 20',
  ema50: 'EMA 50',
  bollinger: 'Bollinger Bands 20/2',
}
const SUBCHART_INDICATOR_LABELS: Record<SubchartIndicatorId, string> = {
  rsi: 'RSI 14',
  macd: 'MACD 12/26/9',
  stochastic: 'Stochastic 14/3/3',
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
  const [chartSettings, setChartSettings] = useState<ChartSettings>(hydrateChartSettings)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [dataMeta, setDataMeta] = useState<OhlcvResult['meta'] | null>(null)
  const [timeframeMenuOpen, setTimeframeMenuOpen] = useState(false)
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
    setPickerOpen(false)
  }, [])
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
      <span className="market-context-static mono">{fmtSymbol(active.symbol, active.marketType)}</span>
      <Button variant="ghost" className="market-context-picker" aria-expanded={pickerOpen} aria-controls={dialogId} onClick={() => setPickerOpen(true)}>
        <span>Choose instrument</span><strong>{fmtSymbol(active.symbol, active.marketType)} · {active.marketType}</strong><span aria-hidden="true">⌄</span>
      </Button>
      <strong className="market-context-price mono">{currentSnapshot?.price == null ? 'Price not available' : fmtNum(currentSnapshot.price)}</strong>
      {currentSnapshot?.change_pct_24h != null && <span className={currentSnapshot.change_pct_24h > 0 ? 'pnl-positive' : currentSnapshot.change_pct_24h < 0 ? 'pnl-negative' : 'text-muted'}>{fmtDelta(currentSnapshot.change_pct_24h)}%</span>}
      <span>{active.exchange || 'default exchange'} · {active.marketType}</span>
      <span className="market-context-provenance">{dataMeta ? `Data refreshed ${metaAge}` : 'Data source not available'}</span>
    </div>
    <div ref={shellRef} className="market-workbench-shell">
      <aside className="market-instrument-rail">{navigator}</aside>
      <Card className="market-workbench-chart-card">
        <div id="market-chart-toolbar" className="market-chart-toolbar">
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
          <Button variant="ghost" className="market-chart-settings-button" aria-haspopup="dialog" aria-expanded={settingsOpen} onClick={() => setSettingsOpen(true)}>Chart settings</Button>
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
      { id: 'signals', label: 'Signals', badge: signalCount || undefined, render: () => <SignalsTab onInstrumentSelect={selectFromSignals} /> },
      { id: 'news', label: 'News', render: () => <NewsTab /> },
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
