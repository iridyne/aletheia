import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { PredictionMarket, PredictionStatus, PredictionObserverRun, PredictionClaim } from '../types'
import PageHeader from '../components/PageHeader'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import { StatusBadge } from '../components/DataTable'
import ProbabilityChart from '../charts/ProbabilityChart'

type Tab = 'radar' | 'movers' | 'runs' | 'preferences'

function shortDate(s: string | null | undefined) {
  if (!s) return '—'
  return s.slice(0, 16).replace('T', ' ')
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

function freshnessLabel(dateStr: string | null | undefined): { label: string; tone: string } {
  if (!dateStr) return { label: 'unknown', tone: 'text-muted' }
  const age = (Date.now() - new Date(dateStr).getTime()) / 60000
  if (age < 15) return { label: 'fresh', tone: 'pnl-positive' }
  if (age < 60) return { label: 'stale', tone: 'text-muted' }
  return { label: 'outdated', tone: 'pnl-negative' }
}

function DirectionBadge({ direction }: { direction: string | null }) {
  if (!direction) return <span className="badge">—</span>
  const cls = direction === 'long' ? 'badge-green' : direction === 'short' ? 'badge-red' : 'badge-yellow'
  const label = direction === 'long' ? 'YES rising' : direction === 'short' ? 'YES falling' : 'stable'
  return <span className={`badge ${cls}`}>{label}</span>
}

const CLAIM_TYPE_BADGE: Record<string, string> = {
  observation: 'badge-blue',
  volume_spike: 'badge-green',
  probability_momentum: 'badge-yellow',
  resolution_risk: 'badge-red',
  liquidity_warning: 'badge-red',
}

function ClaimBadge({ type }: { type: string }) {
  const cls = CLAIM_TYPE_BADGE[type] || 'badge-blue'
  return <span className={`badge ${cls}`}>{type.replace(/_/g, ' ')}</span>
}

// --- Observer Status Card ---
function ObserverStatusCard({ status }: { status: PredictionStatus | undefined }) {
  if (!status) return <div className="pred-status-card card">Loading status…</div>
  const freshness = freshnessLabel(status.last_run?.completed_at)

  return (
    <div className="pred-status-card card">
      <div className="pred-status-grid">
        <div className="pred-status-item">
          <span className="pred-status-label">Last Run</span>
          <span className="pred-status-value">
            {status.last_run ? shortDate(status.last_run.completed_at) : 'Never'}
          </span>
        </div>
        <div className="pred-status-item">
          <span className="pred-status-label">Freshness</span>
          <span className={`pred-status-value ${freshness.tone}`}>{freshness.label}</span>
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
    </div>
  )
}

// --- Market Card ---
function MarketCard({ market, onClick }: { market: PredictionMarket; onClick: () => void }) {
  return (
    <div className="pred-market-card card" onClick={onClick}>
      <div className="pred-card-header">
        <h4 className="pred-card-title">{market.event_title}</h4>
        {market.category && <span className="badge badge-blue">{market.category}</span>}
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
          <span className="text-muted text-sm">Resolves: {market.resolution_at.slice(0, 10)}</span>
        )}
        <span className="text-muted text-sm">{shortDate(market.created_at)}</span>
      </div>
    </div>
  )
}

// --- Market Radar Tab ---
function MarketRadarTab({ markets, onSelect }: { markets: PredictionMarket[]; onSelect: (m: PredictionMarket) => void }) {
  const [sort, setSort] = useState<'recent' | 'movers' | 'volume'>('recent')

  const sorted = [...markets].sort((a, b) => {
    if (sort === 'movers') return Math.abs(b.probability_change_24h ?? 0) - Math.abs(a.probability_change_24h ?? 0)
    if (sort === 'volume') return (b.volume_24h ?? 0) - (a.volume_24h ?? 0)
    return (b.created_at ?? '').localeCompare(a.created_at ?? '')
  })

  return (
    <div>
      <div className="pred-sort-bar">
        <button className={`secondary ${sort === 'recent' ? 'active' : ''}`} onClick={() => setSort('recent')}>Recent</button>
        <button className={`secondary ${sort === 'movers' ? 'active' : ''}`} onClick={() => setSort('movers')}>Biggest Movers</button>
        <button className={`secondary ${sort === 'volume' ? 'active' : ''}`} onClick={() => setSort('volume')}>Highest Volume</button>
      </div>
      {sorted.length === 0 ? (
        <div className="pred-empty">
          <p>No prediction market observations yet.</p>
          <p className="text-muted">The prediction observer runs every 30 minutes when configured.</p>
        </div>
      ) : (
        <div className="pred-grid">
          {sorted.map(m => <MarketCard key={m.id} market={m} onClick={() => onSelect(m)} />)}
        </div>
      )}
    </div>
  )
}

// --- Probability Movers Tab ---
function ProbabilityMoversTab({ markets, onSelect }: { markets: PredictionMarket[]; onSelect: (m: PredictionMarket) => void }) {
  const movers = [...markets]
    .filter(m => m.probability_change_24h != null)
    .sort((a, b) => Math.abs(b.probability_change_24h!) - Math.abs(a.probability_change_24h!))

  if (movers.length === 0) {
    return <p className="text-muted">No probability movements recorded yet.</p>
  }

  return (
    <div className="card">
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
            <tr key={m.id} onClick={() => onSelect(m)} style={{ cursor: 'pointer' }}>
              <td className="pred-event-cell">{m.event_title.length > 60 ? m.event_title.slice(0, 57) + '…' : m.event_title}</td>
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
    </div>
  )
}

// --- Observer Runs Tab ---
function ObserverRunsTab({ runs }: { runs: PredictionObserverRun[] }) {
  if (runs.length === 0) {
    return <p className="text-muted">No observer runs recorded yet.</p>
  }

  return (
    <div className="card">
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
              <td>{r.errors.length > 0 ? <span className="badge badge-red">{r.errors.length}</span> : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
  const updateMut = useMutation({
    mutationFn: (categories: string[]) => api.updatePredictionPreferences({ categories }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['prediction-preferences'] })
      qc.invalidateQueries({ queryKey: ['prediction-status'] })
    },
  })

  const currentCats = prefs?.categories ?? []

  return (
    <div className="card">
      <h3>Observer Preferences</h3>
      <div className="pred-pref-section">
        <label className="pred-pref-label">Observed Categories</label>
        <p className="text-muted text-sm">Keywords that filter which Polymarket events the observer tracks.</p>
        <div className="pred-pref-tags">
          {currentCats.map(c => (
            <span key={c} className="badge badge-blue">{c}</span>
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
          <button
            className="secondary"
            disabled={!cats.trim() || updateMut.isPending}
            onClick={() => {
              const newCats = [...currentCats, ...cats.split(',').map(s => s.trim()).filter(Boolean)]
              updateMut.mutate([...new Set(newCats)])
              setCats('')
            }}
          >
            Add
          </button>
        </div>
        {currentCats.length > 0 && (
          <div className="mt-1">
            <button
              className="secondary text-sm"
              onClick={() => updateMut.mutate([])}
              disabled={updateMut.isPending}
            >
              Clear all categories
            </button>
          </div>
        )}
      </div>
      <div className="pred-pref-section mt-2">
        <label className="pred-pref-label">Observer Interval</label>
        <p className="text-muted text-sm">
          The prediction observer runs every {prefs?.observer_interval_minutes ?? 30} minutes (configured in scheduler).
        </p>
      </div>
    </div>
  )
}

// --- Serenity Cross-Links ---
function SerenityLinksSection({ marketId }: { marketId: string }) {
  const { data: allEvidence = [] } = useQuery({
    queryKey: ['serenity-evidence-all'],
    queryFn: () => api.getSerenityEvidence(),
    staleTime: 60000,
  })
  const linked = allEvidence.filter((e: any) => e.source === 'prediction_market' && e.source_ref === marketId)
  if (!linked.length) return null

  return (
    <DrawerSection label="Linked Serenity Themes">
      {linked.map((e: any) => (
        <div key={e.id} className="pred-serenity-link">
          <span className="badge badge-green">evidence</span>
          <span>{e.title}</span>
          <a href="/serenity" className="lens-cross-link">→ View in Serenity Lens</a>
        </div>
      ))}
    </DrawerSection>
  )
}

// --- Market Detail Drawer Content ---
function MarketDetailContent({ market }: { market: PredictionMarket }) {
  const { data: historyData } = useQuery({
    queryKey: ['prediction-history', market.market_id],
    queryFn: () => api.getPredictionMarketHistory(market.market_id),
    enabled: !!market.market_id,
  })
  const { data: claims = [] } = useQuery({
    queryKey: ['prediction-claims', market.market_id],
    queryFn: () => api.getPredictionMarketClaims(market.market_id),
    enabled: !!market.market_id,
  })

  return (
    <>
      <DrawerSection label="Event">
        <DrawerField label="Title" value={market.event_title} />
        <DrawerField label="Category" value={market.category || '—'} />
        <DrawerField label="Source" value={market.source} />
        <DrawerField label="Market ID" value={market.market_id} mono />
      </DrawerSection>

      <DrawerSection label="Probability History">
        <ProbabilityChart data={historyData?.points ?? []} height={180} />
      </DrawerSection>

      <DrawerSection label="Probability">
        <DrawerField label="YES Probability" value={pctFmt(market.yes_probability)} />
        <DrawerField label="24h Change" value={deltaFmt(market.probability_change_24h)} />
        <DrawerField label="Signal" value={<DirectionBadge direction={market.direction_bias} />} />
        <DrawerField label="Confidence" value={market.confidence_score != null ? `${market.confidence_score}%` : market.confidence ?? '—'} />
      </DrawerSection>

      <DrawerSection label="Market Data">
        <DrawerField label="Volume 24h" value={volFmt(market.volume_24h)} />
        <DrawerField label="Liquidity" value={volFmt(market.liquidity)} />
        <DrawerField label="Order Book" value={market.enable_orderbook ? 'Enabled' : 'Disabled'} />
        <DrawerField label="Resolution" value={market.resolution_at?.slice(0, 10) ?? '—'} />
      </DrawerSection>

      {claims.length > 0 && (
        <DrawerSection label={`Move Explanations (${claims.length})`}>
          <div className="pred-claims-list">
            {claims.map((c: PredictionClaim) => (
              <div key={c.id} className="pred-claim-item">
                <ClaimBadge type={c.claim_type} />
                <span className="pred-claim-text">{c.claim_text}</span>
                {c.direction && <DirectionBadge direction={c.direction} />}
              </div>
            ))}
          </div>
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

// --- Main Page ---
export default function PredictionLens() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('radar')
  const [selected, setSelected] = useState<PredictionMarket | null>(null)

  const { data: status } = useQuery({
    queryKey: ['prediction-status'],
    queryFn: api.getPredictionStatus,
    refetchInterval: 30000,
  })
  const { data: markets = [] } = useQuery({
    queryKey: ['prediction-markets'],
    queryFn: api.getPredictionMarkets,
    refetchInterval: 60000,
  })
  const { data: runs = [] } = useQuery({
    queryKey: ['prediction-runs'],
    queryFn: api.getPredictionRuns,
  })

  const refresh = useMutation({
    mutationFn: api.refreshPrediction,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['prediction-status'] })
      qc.invalidateQueries({ queryKey: ['prediction-markets'] })
      qc.invalidateQueries({ queryKey: ['prediction-runs'] })
    },
  })

  return (
    <div>
      <PageHeader
        title="Prediction Markets"
        role="Observe · prediction market probability trends from Polymarket. Observation-only — not routed to Paper Trader or Live Execution."
        actions={
          <button className="secondary" onClick={() => refresh.mutate(undefined as any)} disabled={refresh.isPending}>
            {refresh.isPending ? 'Refreshing…' : 'Refresh Now'}
          </button>
        }
      />

      <div className="pred-obs-banner">
        Prediction markets are observation-only. They are not eligible for Paper Trader or Live Execution.
      </div>

      <ObserverStatusCard status={status} />

      <div className="pred-tabs">
        <button className={`pred-tab ${tab === 'radar' ? 'pred-tab-active' : ''}`} onClick={() => setTab('radar')}>
          Market Radar {markets.length > 0 && <span className="tab-badge">{markets.length}</span>}
        </button>
        <button className={`pred-tab ${tab === 'movers' ? 'pred-tab-active' : ''}`} onClick={() => setTab('movers')}>
          Probability Movers
        </button>
        <button className={`pred-tab ${tab === 'runs' ? 'pred-tab-active' : ''}`} onClick={() => setTab('runs')}>
          Observer Runs {runs.length > 0 && <span className="tab-badge">{runs.length}</span>}
        </button>
        <button className={`pred-tab ${tab === 'preferences' ? 'pred-tab-active' : ''}`} onClick={() => setTab('preferences')}>
          Preferences
        </button>
      </div>

      <div className="pred-tab-content">
        {tab === 'radar' && <MarketRadarTab markets={markets} onSelect={setSelected} />}
        {tab === 'movers' && <ProbabilityMoversTab markets={markets} onSelect={setSelected} />}
        {tab === 'runs' && <ObserverRunsTab runs={runs} />}
        {tab === 'preferences' && <PreferencesTab />}
      </div>

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title="Prediction Market Detail">
        {selected && <MarketDetailContent market={selected} />}
      </DetailDrawer>
    </div>
  )
}
