import { useState, useEffect, useLayoutEffect, useRef, forwardRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { GoLiveScorecard } from '../api/client'
import SetupBanner from '../components/SetupBanner'
import PnLZone from '../components/PnLZone'
import AgentFeed from '../components/AgentFeed'
import CollectorHealthDetails from '../components/CollectorHealthDetails'
import { Button, Card, StatusText } from '../components/ui'
import { formatViolation, riskStateLabel } from '../lib/riskViolations'
import { humanize, fmtTimestamp } from '../lib/format'

/**
 * Cockpit / Overview (OVERVIEW_REDESIGN.md Decision 3).
 *
 * Reading order: money first (PnL zone) -> what the AI is doing (agent feed, main width)
 * -> what needs YOU (right rail: pending proposals = the one human-in-loop control point,
 * risk state, system health). Bloomberg dense zoning + OpenAlice agent-narrative-primary.
 */
export default function Overview() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [collectorsExpanded, setCollectorsExpanded] = useState(false)
  const [coverageExpanded, setCoverageExpanded] = useState(false)
  const [modeExpanded, setModeExpanded] = useState(false)
  const chdRef = useRef<HTMLDivElement>(null)
  const coverageRef = useRef<HTMLDivElement>(null)
  const modeRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (collectorsExpanded) requestAnimationFrame(() => chdRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }))
  }, [collectorsExpanded])
  useEffect(() => {
    if (coverageExpanded) requestAnimationFrame(() => coverageRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }))
  }, [coverageExpanded])
  useEffect(() => {
    if (modeExpanded) requestAnimationFrame(() => modeRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }))
  }, [modeExpanded])
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus, staleTime: 8000 })
  const { data: killSwitch } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch, staleTime: 30000, refetchInterval: 30000 })
  const { data: riskStatus } = useQuery({ queryKey: ['risk-status'], queryFn: api.getRiskStatus, refetchInterval: 15000, staleTime: 14000 })
  const { data: modelConfig } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig, staleTime: 60000, refetchInterval: 60000 })
  const { data: modelHealth = [] } = useQuery({ queryKey: ['model-health'], queryFn: api.getModelHealth, refetchInterval: 30000, staleTime: 28000 })
  const { data: collectorHealth } = useQuery({
    queryKey: ['collector-health'],
    queryFn: api.getCollectorHealth,
    refetchInterval: 30000,
    staleTime: 28000,
  })
  const { data: dataCoverage } = useQuery({
    queryKey: ['market-data-coverage'],
    queryFn: api.getDataCoverage,
    refetchInterval: 30000,
    staleTime: 28000,
  })
  const exchangeMode = String((status as any)?.exchange_mode || '')
  const usesExternalExchange = !!(status as any)?.uses_external_exchange
  // #171 fix: pick the exchange-test endpoint by the CONFIGURED exchange, not
  // always Bitget. Previously an OKX/Binance deployment saw a stale Bitget
  // probe (false-red if bitget creds absent, false-green if bitget was up
  // while the real exchange was down).
  const exchangeName = String(
    (status as any)?.execution_exchange || (status as any)?.exchange || 'bitget',
  ).toLowerCase()
  const testExchangeConnection =
    exchangeName === 'okx' ? api.testOkx :
    exchangeName === 'binance' ? api.testBinance :
    api.testBitget
  const { data: exchangeHealth } = useQuery({
    queryKey: ['exchange-test', exchangeName, exchangeMode],
    queryFn: () => testExchangeConnection(exchangeMode),
    refetchInterval: 15000,
    staleTime: 14000,
    enabled: usesExternalExchange,
  })
  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })

  const s = (status as any) ?? {}
  const running = !!s.gateway_running
  const paused = !!s.paused
  const killed = !!killSwitch?.active

  // Risk
  const riskState = String(riskStatus?.status || 'not_available')
  const riskTone = riskState === 'blocked' ? 'red'
    : (riskState === 'warning' || riskState === 'not_available' || riskState === 'not_evaluated') ? 'yellow'
    : 'green'
  const violations = (riskStatus?.violations as string[]) || []
  const latestRiskDecision = (riskStatus as any)?.latest_risk_decision

  // System state
  const liveState = String(s.live_state || 'live_disabled')
  const defaultProvider = String(modelConfig?.default_provider || '')
  const dph = (modelHealth as any[]).find(h => h.provider === defaultProvider)

  return (
    <div>
      <h2 className="page-title">Overview</h2>
      <SetupBanner />

      {/* Anchor 1 — PnL / account zone (full width) */}
      <PnLZone />

      {/* Anchor 1.5 — Blotter summary strip. The four decision-flow cards
          (EntryStatusCard / HumanApprovalCard / OpportunityFunnelCard /
          DecisionHistoryCard) migrated to /blotter as of 2026-07-18; this
          strip preserves at-a-glance visibility of the pipeline state
          without hosting the full workflow.  See
          docs/design/frontend/blotter_design.md. */}
      <BlotterSummaryStrip />

      {/* Anchor 2 — agent feed (main) + right rail */}
      <div className="cockpit-main">
        <AgentFeed />

        <div className="cockpit-rail">
          {/* Risk state */}
          <Card className="rail-card risk-panel">
            <div className="risk-head">
              <h3>Risk State</h3>
              <div className="risk-head-badges">
                {killed && <StatusText tone="red">KILL SWITCH</StatusText>}
                <StatusText tone={riskTone}>{riskStateLabel(riskState)}</StatusText>
              </div>
            </div>
            {violations.length > 0 ? (
              <ul className="risk-violations">
                {violations.map((v, i) => {
                  const f = formatViolation(v)
                  return (
                    <li key={i} className="risk-violation">
                      <span className={`risk-dot risk-dot-${f.severity}`} />
                      <div className="risk-violation-body">
                        <span className="risk-violation-label">{f.label}</span>
                        {f.detail && <span className="risk-violation-detail">{f.detail}</span>}
                      </div>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <div className="risk-clear">
                <span className="risk-dot risk-dot-ok" />
                <span className="text-muted text-sm">No active violations</span>
              </div>
            )}
            {latestRiskDecision && !latestRiskDecision.approved && (
              <p className="hint mb-0">
                Latest check rejected {fmtTimestamp(latestRiskDecision.created_at)}. Historical decisions remain available in Risk &amp; Controls.
              </p>
            )}
          </Card>

          {/* System / connections — tabular status board (old-school console feel) */}
          <Card className="rail-card system-panel">
            {!status ? (
              <div className="skeleton"><div className="skeleton-bar" /><div className="skeleton-bar" /><div className="skeleton-bar" /></div>
            ) : (<>
            <div className="flex items-center justify-between mb-2">
              <h3>System</h3>
              <div className="flex gap-2">
                <Button className="btn-control" onClick={() => pauseMut.mutate()} disabled={paused || !running}>⏸</Button>
                <Button className="btn-control" variant="secondary" onClick={() => resumeMut.mutate()} disabled={!paused}>▶</Button>
              </div>
            </div>
            <table className="status-board">
              <tbody>
                <StatusRow label="Gateway" light={running ? 'green' : 'red'} value={running ? 'up' : 'down'} />
                {/* #118 fix: previously the Scheduler light was green whenever
                    !paused, ignoring `scheduler_running` and `scheduler_state`
                    from /status — so a scheduler that had actually stopped
                    (state != 'running') still read green. Prefer the real
                    running flag when the backend supplies it, falling back to
                    the paused heuristic only when it's absent. */}
                {(() => {
                  const hasReal = typeof s?.scheduler_running === 'boolean'
                  const isRunning = hasReal ? !!s.scheduler_running : !paused
                  const state = String(s?.scheduler_state || (paused ? 'paused' : (isRunning ? 'active' : 'stopped')))
                  const light = paused ? 'yellow' : (isRunning ? 'green' : 'red')
                  return <StatusRow label="Scheduler" light={light} value={state} />
                })()}
                <StatusRow
                  label="Exchange"
                  light={
                    !usesExternalExchange ? 'muted' :
                    s.exchange_diverged ? 'yellow' :
                    exchangeHealth?.connected ? 'green' : exchangeHealth ? 'red' : 'yellow'
                  }
                  value={
                    !usesExternalExchange ? 'Local paper · no exchange' :
                    s.exchange_diverged
                      ? `${String(s.execution_exchange)} exec · ${String(s.market_data_exchange)} data`
                      : `${String(s.execution_exchange || s.exchange)} · ${exchangeMode}`
                  }
                  title={s.exchange_diverged
                    ? 'Order execution and market analysis use different exchanges. This split was explicitly configured.'
                    : undefined}
                  onClick={() => navigate('/settings?tab=execution')}
                  externalLink
                />
                <StatusRow
                  label="Model"
                  light={dph?.available ? 'green' : dph ? 'red' : 'yellow'}
                  value={defaultProvider || '—'}
                  onClick={() => navigate('/settings?tab=agent')}
                  externalLink
                />
                <StatusRow
                  label="Collectors"
                  light={
                    !collectorHealth ? 'yellow' :
                    collectorHealth.status === 'healthy' ? 'green' :
                    collectorHealth.status === 'initializing' ? 'muted' :
                    collectorHealth.status === 'degraded' ? 'red' : 'yellow'
                  }
                  value={
                    !collectorHealth ? 'loading' :
                    collectorHealth.status === 'initializing' ? 'initializing' :
                    collectorHealth.status === 'degraded'
                      ? `${collectorHealth.degraded_count} degraded · ${collectorHealth.recorded_count}/${collectorHealth.domain_count} reporting`
                      : collectorHealth.status === 'attention'
                        ? `${collectorHealth.transient_problem_count} transient · ${collectorHealth.recorded_count}/${collectorHealth.domain_count} reporting`
                        : `${collectorHealth.recorded_count}/${collectorHealth.domain_count} reporting`
                  }
                  onClick={collectorHealth?.domains?.length ? () => setCollectorsExpanded(x => !x) : undefined}
                  expanded={collectorsExpanded}
                />
                <StatusRow
                  label="Data coverage"
                  light={
                    !dataCoverage ? 'yellow' :
                    dataCoverage.status === 'empty' ? 'muted' :
                    dataCoverage.critical_problem_count > 0 ? 'yellow' : 'green'
                  }
                  value={
                    !dataCoverage ? 'loading' :
                    `${dataCoverage.fresh_count}/${dataCoverage.domain_count} current`
                  }
                  onClick={dataCoverage?.domains?.length ? () => setCoverageExpanded(x => !x) : undefined}
                  expanded={coverageExpanded}
                />
                {/* Live + Permission + Kill collapsed into one row to keep the System
                    card height short — open positions moved to the PnL Exposure tile. */}
                <StatusRow
                  label="Mode"
                  light={killed ? 'red' : liveState === 'live_disabled' ? 'muted' : 'yellow'}
                  value={`Live: ${humanize(liveState)} · ${humanize(String(s.permission_mode))}${killed ? ' · KILL' : ''}`}
                  onClick={() => setModeExpanded(x => !x)}
                  expanded={modeExpanded}
                />
              </tbody>
            </table>
            {collectorHealth?.domains?.length ? (
              <CollapsibleSection ref={chdRef} open={collectorsExpanded}>
                <CollectorHealthDetails health={collectorHealth} mode="full" />
              </CollapsibleSection>
            ) : null}
            {dataCoverage?.domains?.length ? (
              <CollapsibleSection ref={coverageRef} open={coverageExpanded}>
                <DataCoverageDetail coverage={dataCoverage} />
              </CollapsibleSection>
            ) : null}
            <CollapsibleSection ref={modeRef} open={modeExpanded}>
              <ModeDetail s={s} paused={paused} killed={killed} liveState={liveState} riskState={riskState} />
            </CollapsibleSection>
            </>)}
          </Card>

          {/* Go-Live Scorecard */}
          <GoLiveScoreCard />
        </div>
      </div>
    </div>
  )
}

// ── Blotter summary strip ────────────────────────────────────────────────────
// Owner-approved 2026-07-18: the four decision-flow cards
// (EntryStatusCard / HumanApprovalCard / OpportunityFunnelCard /
// DecisionHistoryCard) plus their helpers, tone tables, and reason
// dictionaries all migrated to /blotter. Only this shortcut strip remains
// on the Overview so at-a-glance visibility of the pipeline is preserved.
// See docs/design/frontend/blotter_design.md.

function BlotterSummaryStrip() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  // Match the Blotter page's cache keys + staleTime so the Overview→Blotter
  // transition is warm: react-query returns cached data instantly and only
  // background-refreshes if past staleTime. Uses the fast /current-snapshot
  // endpoint (perf item 1) — the summary strip only needs `current.*`.
  const { data: funnel } = useQuery({
    queryKey: ['opportunity-funnel-snapshot', 24],
    queryFn: () => api.getOpportunityFunnelCurrentSnapshot(24),
    refetchInterval: 30000,
    staleTime: 20000,
  })
  const { data: proposals = [] } = useQuery({
    queryKey: ['proposals'],
    queryFn: api.getProposals,
    staleTime: 10000,
  })
  const { data: decisions } = useQuery({
    queryKey: ['operator-decisions', 24],
    queryFn: () => api.getOperatorDecisions(24, 50),
    refetchInterval: 60000,
    staleTime: 20000,
  })

  // Perf item 4: hover on the strip preloads whatever Blotter would fetch
  // that this component isn't already fetching. `funnel`, `proposals`,
  // `operator-decisions` all share exact queryKey + queryFn with the Blotter
  // page, so their caches carry over. react-query's prefetchQuery honours the
  // shared staleTime — if data is fresh, prefetch is a no-op. Fires on
  // pointer AND keyboard focus so the tab-order path is equally warm.
  const prefetchBlotter = () => {
    qc.prefetchQuery({
      queryKey: ['opportunity-funnel-snapshot', 24],
      queryFn: () => api.getOpportunityFunnelCurrentSnapshot(24),
      staleTime: 20000,
    })
    qc.prefetchQuery({
      queryKey: ['operator-decisions', 24],
      queryFn: () => api.getOperatorDecisions(24, 50),
      staleTime: 20000,
    })
    qc.prefetchQuery({
      queryKey: ['proposals'],
      queryFn: api.getProposals,
      staleTime: 10000,
    })
  }

  const staged = funnel?.current?.counts?.open_opportunities ?? 0
  const awaiting = (proposals as any[]).filter((p: any) => p.status === 'pending').length
  const filledToday = decisions?.counts?.trade ?? 0
  const c = decisions?.counts
  const rejected24h = c
    ? (c.operator_skip ?? 0) + (c.user_reject ?? 0) + (c.auto_reject ?? 0) + (c.reject_unknown ?? 0)
    : 0

  const openBlotter = () => navigate('/blotter')
  return (
    <div
      className="ov-blotter-strip"
      role="link"
      tabIndex={0}
      onClick={openBlotter}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openBlotter() } }}
      onMouseEnter={prefetchBlotter}
      onFocus={prefetchBlotter}
      title="Open Blotter — every candidate, sign-off, and terminal outcome"
    >
      <span className="ov-blotter-label">Blotter</span>
      <span className="ov-blotter-item">
        Staged <b className="mono">{staged}</b>
      </span>
      <span className="ov-blotter-item">
        Awaiting <b className={`mono ${awaiting > 0 ? 'ov-blotter-yellow' : ''}`}>{awaiting}</b>
      </span>
      <span className="ov-blotter-item">
        Filled today <b className={`mono ${filledToday > 0 ? 'ov-blotter-green' : ''}`}>{filledToday}</b>
      </span>
      <span className="ov-blotter-item">
        Rejected 24h <b className={`mono ${rejected24h > 0 ? 'ov-blotter-red' : ''}`}>{rejected24h}</b>
      </span>
      <span className="ov-blotter-arrow">→</span>
    </div>
  )
}

// ── Go-Live Scorecard Card ───────────────────────────────────────────────────
function GoLiveScoreCard() {
  const [expanded, setExpanded] = useState(false)
  const { data } = useQuery<GoLiveScorecard>({
    queryKey: ['go-live-scorecard'],
    queryFn: api.getGoLiveScorecard,
    staleTime: 60_000,
  })
  if (!data) return null

  const color = data.traffic_light === 'green' ? 'var(--green)'
    : data.traffic_light === 'yellow' ? 'var(--yellow)' : 'var(--red)'
  const label = data.traffic_light === 'green' ? 'READY'
    : data.traffic_light === 'yellow' ? 'CAUTION' : 'NOT READY'

  const circumference = 2 * Math.PI * 36
  const offset = circumference * (1 - data.composite_score / 100)

  return (
    <Card className="rail-card scorecard-panel">
      <div className="scorecard-header" onClick={() => setExpanded(!expanded)}>
        <svg width="56" height="56" viewBox="0 0 80 80">
          <circle cx="40" cy="40" r="36" fill="none" stroke="var(--border)" strokeWidth="6" />
          <circle cx="40" cy="40" r="36" fill="none" stroke={color} strokeWidth="6"
            strokeDasharray={circumference} strokeDashoffset={offset}
            strokeLinecap="round" transform="rotate(-90 40 40)" />
          <text x="40" y="44" textAnchor="middle" fill={color}
            fontSize="16" fontWeight="700" fontFamily="var(--font-mono)">
            {Math.round(data.composite_score)}
          </text>
        </svg>
        <div className="scorecard-title">
          <h3>Go-Live</h3>
          <StatusText tone={data.traffic_light === 'green' ? 'green' : data.traffic_light === 'yellow' ? 'yellow' : 'red'}>
            {label}
          </StatusText>
        </div>
        <span className="text-muted text-xs">{expanded ? '▲' : '▼'}</span>
      </div>
      {expanded && (
        <div className="scorecard-criteria">
          {data.criteria.map(c => {
            const barFill = { width: `${c.score}%`, background: c.status === 'pass' ? 'var(--green)' : c.status === 'warning' ? 'var(--yellow)' : 'var(--red)' }
            return (
              <div key={c.key} className="scorecard-row">
                <span className="scorecard-label">{c.label}</span>
                <div className="scorecard-bar-bg">
                  <div className="scorecard-bar-fill" style={barFill} />
                </div>
                <span className="scorecard-detail">{c.detail}</span>
              </div>
            )
          })}
          {data.blockers.length > 0 && (
            <div className="scorecard-blockers">
              <span className="text-xs text-error">Blockers:</span>
              {data.blockers.map((b, i) => <span key={i} className="text-xs text-error"> · {b}</span>)}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

type Light = 'green' | 'red' | 'yellow' | 'blue' | 'muted'

/** One row of the System status board: label · status light · value. */
function StatusRow({
  label, light, value, title, onClick, expanded, externalLink,
}: { label: string; light: Light; value: string; title?: string; onClick?: () => void; expanded?: boolean; externalLink?: boolean }) {
  return (
    <tr className={`status-row${onClick ? ' status-row-clickable' : ''}`} onClick={onClick}>
      <th>{label}</th>
      <td className="status-cell">
        <span className={`status-light light-${light}`} />
        <span className="status-value" title={title ?? value}>{value}</span>
        {onClick && (
          <span className="status-row-chevron" aria-hidden="true">
            {/* Large triangles (U+25B6 / U+25BC) instead of the "small
                triangle" variants (▸ ▾, U+25B8 / U+25BE) so the expand
                affordance carries the same visual weight as the external
                nav arrow → in a proportional font. */}
            {externalLink ? '→' : (expanded ? '▼' : '▶')}
          </span>
        )}
      </td>
    </tr>
  )
}

// ── Collapsible panel — explicit pixel heights to avoid fr-unit jump on close ──
/**
 * Shadcn/Radix pattern: measure the inner div's scrollHeight before each
 * transition so the browser always has a concrete "from" and "to" pixel value.
 * grid-template-rows: 0fr → 1fr can't reliably capture the "from" value at the
 * very first frame on close, which produces the visible jump at scrollTop = 0.
 */
const CollapsibleSection = forwardRef<HTMLDivElement, { open: boolean; children: React.ReactNode }>(
  ({ open, children }, forwardedRef) => {
    const outerRef = useRef<HTMLDivElement>(null)
    const innerRef = useRef<HTMLDivElement>(null)
    const committedOpen = useRef(open)

    const mergeRef = (el: HTMLDivElement | null) => {
      outerRef.current = el
      if (!forwardedRef) return
      if (typeof forwardedRef === 'function') forwardedRef(el)
      else (forwardedRef as React.MutableRefObject<HTMLDivElement | null>).current = el
    }

    useLayoutEffect(() => {
      const outer = outerRef.current
      const inner = innerRef.current
      if (!outer || !inner || open === committedOpen.current) return
      committedOpen.current = open

      if (open) {
        const h = inner.scrollHeight
        outer.style.overflow = 'hidden'
        outer.style.height = '0px'
        outer.style.opacity = '0'
        void outer.offsetHeight                          // force reflow — capture start state
        outer.style.transition = 'height .22s cubic-bezier(.4,0,.2,1), opacity .16s ease-out .06s'
        outer.style.height = `${h}px`
        outer.style.opacity = '1'
        const onEnd = (e: TransitionEvent) => {
          if (e.propertyName !== 'height') return
          outer.style.height = 'auto'
          outer.style.overflow = ''
          outer.style.transition = ''
        }
        outer.addEventListener('transitionend', onEnd)
        return () => outer.removeEventListener('transitionend', onEnd)
      } else {
        const h = outer.offsetHeight                     // snapshot current pixel height
        outer.style.overflow = 'hidden'
        outer.style.height = `${h}px`
        void outer.offsetHeight                          // force reflow — capture start state
        outer.style.transition = 'height .22s cubic-bezier(.4,0,.2,1), opacity .14s ease-out'
        outer.style.height = '0px'
        outer.style.opacity = '0'
        const onEnd = (e: TransitionEvent) => {
          if (e.propertyName !== 'height') return
          outer.style.transition = ''
        }
        outer.addEventListener('transitionend', onEnd)
        return () => outer.removeEventListener('transitionend', onEnd)
      }
    }, [open])

    return (
      <div ref={mergeRef} style={{ overflow: 'hidden', height: '0px', opacity: 0 }}>
        <div ref={innerRef} style={{ paddingTop: 'var(--space-2)' }}>
          {children}
        </div>
      </div>
    )
  }
)
CollapsibleSection.displayName = 'CollapsibleSection'

// ── Inline detail panels ──────────────────────────────────────────────────────

function fmtAgeSec(sec: number): string {
  if (sec < 60) return `${Math.round(sec)}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m`
  return `${Math.floor(sec / 3600)}h${Math.floor((sec % 3600) / 60)}m`
}

function fmtMaxAge(sec: number): string {
  if (sec < 60) return `${sec}s`
  if (sec % 3600 === 0) return `${sec / 3600}h`
  return `${Math.floor(sec / 60)}m`
}

const COVERAGE_LIGHT: Record<string, string> = {
  fresh: 'green', stale: 'yellow', empty: 'muted', invalid: 'red', idle: 'muted',
}
const COVERAGE_RANK: Record<string, number> = {
  invalid: 0, stale: 1, empty: 2, idle: 3, fresh: 4,
}

function DataCoverageDetail({ coverage }: { coverage: { domains: any[] } }) {
  const sorted = [...coverage.domains].sort(
    (a, b) => (COVERAGE_RANK[a.state] ?? 5) - (COVERAGE_RANK[b.state] ?? 5),
  )
  return (
    <div className="chd-panel">
      <div className="chd-title">Data coverage</div>
      {sorted.map((d: any) => {
        const light = COVERAGE_LIGHT[d.state] ?? 'muted'
        return (
          <div key={d.domain} className="chd-domain">
            <div className="chd-domain-head">
              <span className={`status-light light-${light}`} />
              <span className="chd-domain-name">{(d.label || d.domain).toUpperCase()}</span>
              <span className={`chd-status chd-status-${light}`}>{d.state}</span>
            </div>
            <div className="chd-domain-meta">
              <span>{d.symbol_count} symbols · {(d.row_count as number).toLocaleString()} rows</span>
              {d.age_sec != null && (
                <span>{fmtAgeSec(d.age_sec)} old{d.max_age_sec ? ` · max ${fmtMaxAge(d.max_age_sec)}` : ''}</span>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function ModeDetail({ s, paused, killed, liveState, riskState }: {
  s: any; paused: boolean; killed: boolean; liveState: string; riskState: string
}) {
  type ModeItem = { label: string; value: string; light: Light }
  const items: ModeItem[] = [
    {
      label: 'LIVE MODE',
      value: humanize(liveState),
      light: liveState === 'live_full' ? 'green' : liveState === 'live_disabled' ? 'muted' : 'yellow',
    },
    {
      label: 'PERMISSION',
      value: humanize(String(s.permission_mode || '—')),
      light: s.permission_mode === 'Full' ? 'yellow' : 'muted',
    },
    {
      label: 'KILL SWITCH',
      value: killed ? 'active' : 'inactive',
      light: killed ? 'red' : 'muted',
    },
    {
      label: 'SCHEDULER',
      value: paused ? 'paused' : (typeof s.scheduler_running === 'boolean'
        ? (s.scheduler_running ? 'running' : 'stopped')
        : String(s.scheduler_state || '—')),
      light: paused ? 'yellow' : (s.scheduler_running === false ? 'red' : 'green'),
    },
  ]
  if (riskState && riskState !== 'not_available' && riskState !== 'not_evaluated') {
    items.push({
      label: 'RISK',
      value: riskStateLabel(riskState),
      light: riskState === 'blocked' ? 'red' : riskState === 'warning' ? 'yellow' : 'green',
    })
  }
  return (
    <div className="chd-panel">
      <div className="chd-title">Mode &amp; permissions</div>
      {items.map(({ label, value, light }) => (
        <div key={label} className="chd-domain">
          <div className="chd-domain-head">
            <span className={`status-light light-${light}`} />
            <span className="chd-domain-name">{label}</span>
            <span className={`chd-status chd-status-${light}`}>{value}</span>
          </div>
        </div>
      ))}
    </div>
  )
}
