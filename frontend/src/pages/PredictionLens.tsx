import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { PredictionMarket, PredictionStatus, PredictionObserverRun, PredictionClaim } from '../types'
import PageHeader from '../components/PageHeader'
import Tabs from '../components/Tabs'
import type { TabDef } from '../components/Tabs'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import { StatusBadge } from '../components/DataTable'
import { DataStateMessage } from '../components/DataState'
import { Badge, Button, Card, ListRowButton, StatusText, TableWrap, useToast, type StatusTone } from '../components/ui'
import ProbabilityChart from '../charts/ProbabilityChart'
import { fmtScore100, fmtTimestamp } from '../lib/format'

type Tab = 'radar' | 'movers' | 'runs' | 'preferences'

export type PredictionStatusState = 'loading' | 'enabled' | 'disabled' | 'unavailable' | 'stale'

export function resolvePredictionStatusState(
  status: PredictionStatus | undefined,
  isPending: boolean,
  isError: boolean,
  cachedEnabled: boolean | undefined,
): { state: PredictionStatusState; enabled: boolean | undefined } {
  if (status) {
    return isError
      ? { state: 'stale', enabled: status.enabled }
      : { state: status.enabled ? 'enabled' : 'disabled', enabled: status.enabled }
  }
  if (isError) return cachedEnabled == null ? { state: 'unavailable', enabled: undefined } : { state: 'stale', enabled: cachedEnabled }
  if (isPending) return cachedEnabled == null ? { state: 'loading', enabled: undefined } : { state: 'stale', enabled: cachedEnabled }
  return cachedEnabled == null ? { state: 'unavailable', enabled: undefined } : { state: 'stale', enabled: cachedEnabled }
}

export function resolvePredictionDataState<T>(data: T[] | undefined, isPending: boolean, isError: boolean): PredictionDataState {
  if (isError && data != null) return 'stale'
  if (isError) return 'unavailable'
  if (isPending && data == null) return 'loading'
  if (data == null) return 'loading'
  return data.length > 0 ? 'available' : 'empty'
}

type PredictionDataState = 'loading' | 'available' | 'empty' | 'stale' | 'unavailable' | 'not-configured'

function PredictionDataNotice({ state, message }: { state: PredictionDataState; message?: string }) {
  if (state === 'available') return null
  return <DataStateMessage state={state} message={message} />
}

function shortDate(s: string | null | undefined) {
  return fmtTimestamp(s)
}

function pctFmt(v: number | null | undefined, decimals = 1) {
  if (v == null) return '—'
  return `${(v * 100).toFixed(decimals)}%`
}

function deltaFmt(v: number | null | undefined) {
  if (v == null) return '—'
  const pct = v * 100
  const sign = pct >= 0 ? '+' : ''
  return `${sign}${pct.toFixed(1)}pp`
}

function volFmt(v: number | null | undefined) {
  if (v == null) return '—'
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`
  return `$${v.toFixed(0)}`
}

function freshnessLabel(dateStr: string | null | undefined): { label: string; tone: StatusTone } {
  if (!dateStr) return { label: 'unknown', tone: 'muted' }
  const age = (Date.now() - new Date(dateStr).getTime()) / 60000
  if (age < 15) return { label: 'fresh', tone: 'green' }
  if (age < 60) return { label: 'stale', tone: 'muted' }
  return { label: 'outdated', tone: 'red' }
}

function DirectionBadge({ direction }: { direction: string | null }) {
  if (!direction) return <StatusText tone="muted">—</StatusText>
  const tone = direction === 'long' ? 'green' : direction === 'short' ? 'red' : 'yellow'
  const label = direction === 'long' ? 'YES rising' : direction === 'short' ? 'YES falling' : 'stable'
  return <StatusText tone={tone}>{label}</StatusText>
}

const CLAIM_TYPE_TONE: Record<string, StatusTone> = {
  observation: 'blue',
  volume_spike: 'green',
  probability_momentum: 'yellow',
  resolution_risk: 'red',
  liquidity_warning: 'red',
}

function ClaimBadge({ type }: { type: string }) {
  return <StatusText tone={CLAIM_TYPE_TONE[type] || 'blue'}>{type.replace(/_/g, ' ')}</StatusText>
}

// --- Observer Status Card ---
function ObserverStatusCard({ status, state }: { status: PredictionStatus | undefined; state: PredictionStatusState }) {
  if (!status) return null
  const freshness = freshnessLabel(status.last_run?.completed_at)

  return (
    <Card className="pred-status-card">
      <div className="pred-status-mode">
        <span className="pred-status-label">Observer mode</span>
        <StatusText tone={status.enabled ? 'green' : 'muted'}>{status.enabled ? 'enabled' : 'disabled'}</StatusText>
        {state === 'stale' && <StatusText tone="yellow">stale status</StatusText>}
      </div>
      <div className="pred-status-grid">
        <div className="pred-status-item">
          <span className="pred-status-label">Last Run</span>
          <span className="pred-status-value">
            {status.last_run ? shortDate(status.last_run.completed_at) : 'Never'}
          </span>
        </div>
        <div className="pred-status-item">
          <span className="pred-status-label">Freshness</span>
          <span className="pred-status-value"><StatusText tone={freshness.tone}>{freshness.label}</StatusText></span>
        </div>
        <div className="pred-status-item">
          <span className="pred-status-label">Markets Scanned</span>
          <span className="pred-status-value">{status.last_run?.markets_scanned ?? 0}</span>
        </div>
        <div className="pred-status-item">
          <span className="pred-status-label">Open Signals</span>
          <span className="pred-status-value">{status.open_opportunities}</span>
        </div>
        <div className="pred-status-item">
          <span className="pred-status-label">Interval</span>
          <span className="pred-status-value">every {status.observer_interval_minutes}m</span>
        </div>
        <div className="pred-status-item">
          <span className="pred-status-label">Categories</span>
          <span className="pred-status-value">{status.categories.join(', ') || 'none'}</span>
        </div>
      </div>
    </Card>
  )
}

// --- Market Card ---
function MarketCard({ market, onClick }: { market: PredictionMarket; onClick: () => void }) {
  return (
    <ListRowButton
      type="button"
      className="pred-market-card"
      aria-label={`Open details for ${market.event_title}`}
      onClick={onClick}
    >
      <div className="pred-card-header">
        <h4 className="pred-card-title">{market.event_title}</h4>
        {market.category && <Badge tone="blue">{market.category}</Badge>}
      </div>
      <div className="pred-card-stats">
        <div className="pred-stat">
          <span className="pred-stat-label">YES</span>
          <span className="pred-stat-value pred-yes-value">{pctFmt(market.yes_probability)}</span>
        </div>
        <div className="pred-stat">
          <span className="pred-stat-label">24h</span>
          <span className={`pred-stat-value ${(market.probability_change_24h ?? 0) > 0 ? 'pnl-positive' : (market.probability_change_24h ?? 0) < 0 ? 'pnl-negative' : ''}`}>
            {deltaFmt(market.probability_change_24h)}
          </span>
        </div>
        <div className="pred-stat">
          <span className="pred-stat-label">Volume</span>
          <span className="pred-stat-value">{volFmt(market.volume_24h)}</span>
        </div>
        <div className="pred-stat">
          <span className="pred-stat-label">Liquidity</span>
          <span className="pred-stat-value">{volFmt(market.liquidity)}</span>
        </div>
      </div>
      <div className="pred-card-footer">
        <DirectionBadge direction={market.direction_bias} />
        {market.resolution_at && (
          <span className="text-muted text-sm">Resolves: {fmtTimestamp(market.resolution_at, { dateOnly: true })}</span>
        )}
        <span className="text-muted text-sm">{shortDate(market.created_at)}</span>
      </div>
    </ListRowButton>
  )
}

// --- Market Radar Tab ---
function MarketRadarTab({ markets, state, onSelect }: { markets: PredictionMarket[]; state: PredictionDataState; onSelect: (m: PredictionMarket) => void }) {
  const [sort, setSort] = useState<'recent' | 'movers' | 'volume'>('recent')

  if (state === 'loading' || state === 'unavailable' || state === 'not-configured') {
    return <PredictionDataNotice state={state} message={state === 'unavailable' ? 'Prediction markets unavailable. The read failed; this is not an empty result.' : undefined} />
  }

  const sorted = [...markets].sort((a, b) => {
    if (sort === 'movers') return Math.abs(b.probability_change_24h ?? 0) - Math.abs(a.probability_change_24h ?? 0)
    if (sort === 'volume') return (b.volume_24h ?? 0) - (a.volume_24h ?? 0)
    return (b.created_at ?? '').localeCompare(a.created_at ?? '')
  })

  return (
    <div>
      <PredictionDataNotice state={state} message="Prediction market data is stale. Retained cards may not reflect the latest read." />
      <div className="pred-sort-bar">
        <Button variant="secondary" className={sort === 'recent' ? 'active' : ''} onClick={() => setSort('recent')}>Recent</Button>
        <Button variant="secondary" className={sort === 'movers' ? 'active' : ''} onClick={() => setSort('movers')}>Biggest Movers</Button>
        <Button variant="secondary" className={sort === 'volume' ? 'active' : ''} onClick={() => setSort('volume')}>Highest Volume</Button>
      </div>
      {sorted.length === 0 && state === 'empty' ? (
        <div className="pred-empty">
          <p>No prediction market observations yet.</p>
          <p className="text-muted">The prediction observer runs every 30 minutes when configured.</p>
        </div>
      ) : sorted.length > 0 ? (
        <div className="pred-grid">
          {sorted.map(m => <MarketCard key={m.id} market={m} onClick={() => onSelect(m)} />)}
        </div>
      ) : null}
    </div>
  )
}

// --- Probability Movers Tab ---
function ProbabilityMoversTab({ markets, state, onSelect }: { markets: PredictionMarket[]; state: PredictionDataState; onSelect: (m: PredictionMarket) => void }) {
  if (state === 'loading' || state === 'unavailable' || state === 'not-configured') {
    return <PredictionDataNotice state={state} message={state === 'unavailable' ? 'Probability movements unavailable. The read failed; this is not an empty result.' : undefined} />
  }
  const movers = [...markets]
    .filter(m => m.probability_change_24h != null)
    .sort((a, b) => Math.abs(b.probability_change_24h!) - Math.abs(a.probability_change_24h!))

  if (movers.length === 0 && state === 'empty') {
    return <PredictionDataNotice state="empty" message="No probability movements recorded yet." />
  }

  return (
    <>
      <PredictionDataNotice state={state} message="Probability movement data is stale. Retained rows may not reflect the latest read." />
      {movers.length > 0 && <Card>
        <TableWrap>
          <table>
          <thead>
            <tr>
              <th>Event</th>
              <th>YES Now</th>
              <th>24h Change</th>
              <th>Volume</th>
              <th>Liquidity</th>
              <th>Signal</th>
            </tr>
          </thead>
          <tbody>
            {movers.map(m => (
              <tr
                key={m.id}
                className="clickable"
                role="button"
                tabIndex={0}
                aria-label={`Open details for ${m.event_title}`}
                onClick={() => onSelect(m)}
                onKeyDown={event => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onSelect(m)
                  }
                }}
              >
                <td className="pred-event-cell td-ellipsis">{m.event_title}</td>
                <td className="mono">{pctFmt(m.yes_probability)}</td>
                <td className={`mono ${(m.probability_change_24h ?? 0) > 0 ? 'pnl-positive' : (m.probability_change_24h ?? 0) < 0 ? 'pnl-negative' : ''}`}>
                  {deltaFmt(m.probability_change_24h)}
                </td>
                <td className="mono">{volFmt(m.volume_24h)}</td>
                <td className="mono">{volFmt(m.liquidity)}</td>
                <td><DirectionBadge direction={m.direction_bias} /></td>
              </tr>
            ))}
          </tbody>
          </table>
        </TableWrap>
      </Card>}
    </>
  )
}

// --- Observer Runs Tab ---
function ObserverRunsTab({ runs, state }: { runs: PredictionObserverRun[]; state: PredictionDataState }) {
  if (state === 'loading' || state === 'unavailable' || state === 'not-configured') {
    return <PredictionDataNotice state={state} message={state === 'unavailable' ? 'Observer runs unavailable. The read failed; this is not an empty result.' : undefined} />
  }
  if (runs.length === 0 && state === 'empty') {
    return <PredictionDataNotice state="empty" message="No observer runs recorded yet." />
  }

  return (
    <>
      <PredictionDataNotice state={state} message="Observer run data is stale. Retained rows may not reflect the latest read." />
      {runs.length > 0 && <Card>
        <TableWrap>
          <table>
          <thead>
            <tr>
              <th>Started</th>
              <th>Status</th>
              <th>Markets</th>
              <th>Opps Created</th>
              <th>Completed</th>
              <th>Errors</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(r => (
              <tr key={r.id}>
                <td>{shortDate(r.started_at)}</td>
                <td><StatusBadge status={r.status} /></td>
                <td>{r.markets_scanned}</td>
                <td>{r.opportunities_created}</td>
                <td>{shortDate(r.completed_at)}</td>
                <td>{r.errors.length > 0 ? <StatusText tone="red">{r.errors.length}</StatusText> : '—'}</td>
              </tr>
            ))}
          </tbody>
          </table>
        </TableWrap>
      </Card>}
    </>
  )
}

// --- Preferences Tab ---
function PreferencesTab() {
  const qc = useQueryClient()
  const { data: prefs } = useQuery({
    queryKey: ['prediction-preferences'],
    queryFn: api.getPredictionPreferences,
  })
  const [cats, setCats] = useState('')
  const { toast } = useToast()
  const updateMut = useMutation({
    mutationFn: (categories: string[]) => api.updatePredictionPreferences({ categories }),
    onSuccess: () => {
      toast('success', 'Preferences saved')
      qc.invalidateQueries({ queryKey: ['prediction-preferences'] })
      qc.invalidateQueries({ queryKey: ['prediction-status'] })
    },
    onError: (e: any) => toast('error', `Save failed: ${e?.message || 'request failed'}`),
  })

  const currentCats = prefs?.categories ?? []

  return (
    <Card>
      <h3>Observer Preferences</h3>
      <div className="pred-pref-section">
        <label className="pred-pref-label">Observed Categories</label>
        <p className="text-muted text-sm">Keywords that filter which Polymarket events the observer tracks.</p>
        <div className="pred-pref-tags">
          {currentCats.map(c => (
            <Badge key={c} tone="blue">{c}</Badge>
          ))}
        </div>
        <div className="flex gap-1 mt-1">
          <input
            className="input"
            placeholder="Add categories (comma-separated)"
            value={cats}
            onChange={e => setCats(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && cats.trim()) {
                const newCats = [...currentCats, ...cats.split(',').map(s => s.trim()).filter(Boolean)]
                updateMut.mutate([...new Set(newCats)])
                setCats('')
              }
            }}
          />
          <Button
            variant="secondary"
            disabled={!cats.trim() || updateMut.isPending}
            onClick={() => {
              const newCats = [...currentCats, ...cats.split(',').map(s => s.trim()).filter(Boolean)]
              updateMut.mutate([...new Set(newCats)])
              setCats('')
            }}
          >
            Add
          </Button>
        </div>
        {currentCats.length > 0 && (
          <div className="mt-1">
            <Button
              variant="secondary"
              className="text-sm"
              onClick={() => updateMut.mutate([])}
              disabled={updateMut.isPending}
            >
              Clear all categories
            </Button>
          </div>
        )}
      </div>
      <div className="pred-pref-section mt-2">
        <label className="pred-pref-label">Observer Interval</label>
        <p className="text-muted text-sm">
          The prediction observer runs every {prefs?.observer_interval_minutes ?? 30} minutes (configured in scheduler).
        </p>
      </div>
    </Card>
  )
}

// --- Serenity Cross-Links ---
function SerenityLinksSection({ marketId }: { marketId: string }) {
  const evidenceQuery = useQuery({
    queryKey: ['serenity-evidence-all'],
    queryFn: () => api.getSerenityEvidence(),
    staleTime: 60000,
  })
  const evidenceState = resolvePredictionDataState(evidenceQuery.data, evidenceQuery.isPending, evidenceQuery.isError)
  const allEvidence = evidenceQuery.data ?? []
  const linked = allEvidence.filter((e: any) => e.source === 'prediction_market' && e.source_ref === marketId)
  if (evidenceState === 'available' && !linked.length) return null

  return (
    <DrawerSection label="Linked Serenity Themes">
      {evidenceState === 'loading' && <PredictionDataNotice state="loading" message="Linked Serenity evidence is loading…" />}
      {evidenceState === 'unavailable' && <PredictionDataNotice state="unavailable" message="Linked Serenity evidence unavailable. The read failed; no conclusion is implied." />}
      {evidenceState === 'stale' && <PredictionDataNotice state="stale" message="Linked Serenity evidence is stale. Retained links may not reflect the latest read." />}
      {linked.map((e: any) => (
        <div key={e.id} className="pred-serenity-link">
          <Badge tone="green">evidence</Badge>
          <span>{e.title}</span>
          <a href="/serenity-lens" className="lens-cross-link">→ View in Serenity Lens</a>
        </div>
      ))}
    </DrawerSection>
  )
}

// --- Market Detail Drawer Content ---
function MarketDetailContent({ market }: { market: PredictionMarket }) {
  const historyQuery = useQuery({
    queryKey: ['prediction-history', market.market_id],
    queryFn: () => api.getPredictionMarketHistory(market.market_id),
    enabled: !!market.market_id,
  })
  const claimsQuery = useQuery({
    queryKey: ['prediction-claims', market.market_id],
    queryFn: () => api.getPredictionMarketClaims(market.market_id),
    enabled: !!market.market_id,
  })
  const historyState = resolvePredictionDataState(historyQuery.data?.points, historyQuery.isPending, historyQuery.isError)
  const claimsState = resolvePredictionDataState(claimsQuery.data, claimsQuery.isPending, claimsQuery.isError)
  const historyData = historyQuery.data
  const claims = claimsQuery.data ?? []

  return (
    <>
      <DrawerSection label="Event">
        <DrawerField label="Title" value={market.event_title} />
        <DrawerField label="Category" value={market.category || '—'} />
        <DrawerField label="Source" value={market.source} />
        <DrawerField label="Market ID" value={market.market_id} mono />
      </DrawerSection>

      <DrawerSection label="Probability History">
        {historyState === 'loading' && <PredictionDataNotice state="loading" message="Probability history is loading…" />}
        {historyState === 'unavailable' && <PredictionDataNotice state="unavailable" message="Probability history unavailable. The read failed; no empty history is implied." />}
        {historyState === 'stale' && <PredictionDataNotice state="stale" message="Probability history is stale. Retained points may not reflect the latest read." />}
        {historyState !== 'loading' && historyState !== 'unavailable' && (
          <ProbabilityChart data={historyData?.points ?? []} height={180} />
        )}
      </DrawerSection>

      <DrawerSection label="Probability">
        <DrawerField label="YES Probability" value={pctFmt(market.yes_probability)} />
        <DrawerField label="24h Change" value={deltaFmt(market.probability_change_24h)} />
        <DrawerField label="Signal" value={<DirectionBadge direction={market.direction_bias} />} />
        <DrawerField label="Confidence score" value={market.confidence_score != null ? fmtScore100(market.confidence_score) : market.confidence ?? '—'} />
      </DrawerSection>

      <DrawerSection label="Market Data">
        <DrawerField label="Volume 24h" value={volFmt(market.volume_24h)} />
        <DrawerField label="Liquidity" value={volFmt(market.liquidity)} />
        <DrawerField label="Order Book" value={market.enable_orderbook ? 'Enabled' : 'Disabled'} />
        <DrawerField label="Resolution" value={fmtTimestamp(market.resolution_at, { dateOnly: true })} />
      </DrawerSection>

      {(claimsState !== 'available' || claims.length > 0) && (
        <DrawerSection label={claims.length > 0 ? `Move Explanations (${claims.length})` : 'Move Explanations'}>
          {claimsState === 'loading' && <PredictionDataNotice state="loading" message="Move explanations are loading…" />}
          {claimsState === 'unavailable' && <PredictionDataNotice state="unavailable" message="Move explanations unavailable. The read failed; no claims are implied." />}
          {claimsState === 'stale' && <PredictionDataNotice state="stale" message="Move explanations are stale. Retained claims may not reflect the latest read." />}
          {claims.length > 0 && <div className="pred-claims-list">
            {claims.map((c: PredictionClaim) => (
              <div key={c.id} className="pred-claim-item">
                <ClaimBadge type={c.claim_type} />
                <span className="pred-claim-text">{c.claim_text}</span>
                {c.direction && <DirectionBadge direction={c.direction} />}
              </div>
            ))}
          </div>}
        </DrawerSection>
      )}

      <SerenityLinksSection marketId={market.market_id} />

      <DrawerSection label="Observation Status">
        <p className="text-muted text-sm">
          This prediction market observation is informational only and will not be routed to any execution pathway.
        </p>
      </DrawerSection>
    </>
  )
}

export function PredictionStatusNotice({ state, enabled }: { state: PredictionStatusState; enabled: boolean | undefined }) {
  if (state === 'enabled') return null
  if (state === 'disabled') {
    return (
      <div className="pred-state-notice pred-state-disabled" role="status">
        <strong>Prediction Markets disabled</strong>
        <span>The observer is not running. Preferences below are configuration-only; no observation action is available.</span>
      </div>
    )
  }
  if (state === 'stale') {
    return (
      <DataStateMessage
        state="stale"
        message={enabled === true
          ? 'Prediction status is stale. Retained observations are shown for context; refresh is disabled until live status is confirmed.'
          : 'Prediction status is stale and the last known mode was disabled. Live availability is not confirmed.'}
      />
    )
  }
  return <DataStateMessage state={state === 'loading' ? 'loading' : 'unavailable'} message={state === 'loading' ? 'Loading Prediction Markets status…' : 'Prediction Markets unavailable. The status read failed; availability is not known.'} />
}

// Last-known enabled flag is only a stale-status hint; it never authorizes a
// refresh or treats an unreadable live status as current.
const PRED_ENABLED_CACHE = 'aletheia.prediction.enabled'
function readCachedEnabled(): boolean | undefined {
  try {
    const v = localStorage.getItem(PRED_ENABLED_CACHE)
    return v == null ? undefined : v === 'true'
  } catch {
    return undefined
  }
}

// --- Main Page ---
export default function PredictionLens() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('radar')
  const [selected, setSelected] = useState<PredictionMarket | null>(null)

  const statusQuery = useQuery({
    queryKey: ['prediction-status'],
    queryFn: api.getPredictionStatus,
    refetchInterval: 30000,
  })

  const cachedEnabled = readCachedEnabled()
  const statusView = resolvePredictionStatusState(statusQuery.data, statusQuery.isPending, statusQuery.isError, cachedEnabled)
  const isDisabled = statusView.state === 'disabled'
  const showOperationalTabs = !isDisabled && !(statusView.state === 'stale' && statusView.enabled === false)

  useEffect(() => {
    if (statusQuery.data?.enabled != null) {
      try { localStorage.setItem(PRED_ENABLED_CACHE, String(statusQuery.data.enabled)) } catch { /* ignore */ }
    }
  }, [statusQuery.data?.enabled])

  const marketsQuery = useQuery({
    queryKey: ['prediction-markets'],
    queryFn: api.getPredictionMarkets,
    refetchInterval: 60000,
    enabled: statusView.enabled === true,
  })
  const runsQuery = useQuery({
    queryKey: ['prediction-runs'],
    queryFn: api.getPredictionRuns,
    enabled: statusView.enabled === true,
  })
  const markets = marketsQuery.data ?? []
  const runs = runsQuery.data ?? []
  const marketsState = statusView.state === 'unavailable'
    ? 'unavailable'
    : statusView.state === 'loading'
      ? 'loading'
    : resolvePredictionDataState(marketsQuery.data, marketsQuery.isPending, marketsQuery.isError)
  const runsState = statusView.state === 'unavailable'
    ? 'unavailable'
    : statusView.state === 'loading'
      ? 'loading'
    : resolvePredictionDataState(runsQuery.data, runsQuery.isPending, runsQuery.isError)
  const activeTab = showOperationalTabs ? tab : 'preferences'
  const preferenceLabel = isDisabled || (statusView.state === 'stale' && statusView.enabled === false)
    ? 'Preferences (configuration only)'
    : 'Preferences'

  const { toast } = useToast()
  const refresh = useMutation({
    mutationFn: api.refreshPrediction,
    onSuccess: () => {
      toast('success', 'Prediction refresh triggered')
      qc.invalidateQueries({ queryKey: ['prediction-status'] })
      qc.invalidateQueries({ queryKey: ['prediction-markets'] })
      qc.invalidateQueries({ queryKey: ['prediction-runs'] })
    },
    onError: (e: any) => toast('error', `Refresh failed: ${e?.message || 'request failed'}`),
  })

  return (
    <div className="pred-page-wrapper">
      <PageHeader
        title="Prediction Markets"
        role="Observe · prediction market probability trends from Polymarket. Observation-only — not routed to Paper Trader or Live Execution."
        actions={
          <Button variant="secondary" onClick={() => refresh.mutate(undefined as any)} disabled={refresh.isPending || statusView.state !== 'enabled'}>
            {refresh.isPending ? 'Refreshing…' : 'Refresh Now'}
          </Button>
        }
      />

      <PredictionStatusNotice state={statusView.state} enabled={statusView.enabled} />
      <div className="pred-obs-banner">
        Prediction markets are observation-only. They are not eligible for Paper Trader or Live Execution.
      </div>

      <ObserverStatusCard status={statusQuery.data} state={statusView.state} />

      <Tabs
        value={activeTab}
        onChange={(id) => setTab(id as Tab)}
        tabs={([
          ...(showOperationalTabs ? [
            { id: 'radar', label: 'Market Radar', badge: markets.length > 0 ? markets.length : undefined,
              render: () => <MarketRadarTab markets={markets} state={marketsState} onSelect={setSelected} /> },
            { id: 'movers', label: 'Probability Movers',
              render: () => <ProbabilityMoversTab markets={markets} state={marketsState} onSelect={setSelected} /> },
            { id: 'runs', label: 'Observer Runs', badge: runs.length > 0 ? runs.length : undefined,
              render: () => <ObserverRunsTab runs={runs} state={runsState} /> },
          ] : []),
          { id: 'preferences', label: preferenceLabel, render: () => <PreferencesTab /> },
        ]) as TabDef[]}
      />

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title="Prediction Market Detail">
        {selected && <MarketDetailContent market={selected} />}
      </DetailDrawer>
    </div>
  )
}
