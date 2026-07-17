import { useState, useEffect, useLayoutEffect, useRef, forwardRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { GoLiveScorecard } from '../api/client'
import SetupBanner from '../components/SetupBanner'
import PnLZone from '../components/PnLZone'
import AgentFeed from '../components/AgentFeed'
import CollectorHealthDetails from '../components/CollectorHealthDetails'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import { Badge, Button, Card, CardHeader, StatusText } from '../components/ui'
import { formatViolation, riskStateLabel } from '../lib/riskViolations'
import { humanize, fmtTimestamp, fmtSymbol } from '../lib/format'

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
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: killSwitch } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch })
  const { data: riskStatus } = useQuery({ queryKey: ['risk-status'], queryFn: api.getRiskStatus, refetchInterval: 15000 })
  const { data: proposals = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  const { data: modelConfig } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig })
  const { data: modelHealth = [] } = useQuery({ queryKey: ['model-health'], queryFn: api.getModelHealth, refetchInterval: 30000 })
  const { data: collectorHealth } = useQuery({
    queryKey: ['collector-health'],
    queryFn: api.getCollectorHealth,
    refetchInterval: 30000,
  })
  const { data: dataCoverage } = useQuery({
    queryKey: ['market-data-coverage'],
    queryFn: api.getDataCoverage,
    refetchInterval: 30000,
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
    enabled: usesExternalExchange,
  })
  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })

  if (!status) return <div className="skeleton"><div className="skeleton-bar" /><div className="skeleton-bar" /><div className="skeleton-bar" /></div>

  const s = status as any
  const running = !!s.gateway_running
  const paused = !!s.paused
  const killed = !!killSwitch?.active
  const pendingProposals = (proposals as any[]).filter(p => p.status === 'pending')

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

      {/* Anchor 2 — agent feed (main) + right rail */}
      <div className="cockpit-main">
        <AgentFeed />

        <div className="cockpit-rail">
          {/* Approval Desk — the human-in-loop control point + recent decisions history */}
          <ApprovalDeskCard pendingProposals={pendingProposals} />


          {/* Funnel — the counterpart to the Approval Desk: why nothing (or few) reached it */}
          <OpportunityFunnelCard />

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
          </Card>

          {/* Go-Live Scorecard */}
          <GoLiveScoreCard />
        </div>
      </div>
    </div>
  )
}

// ── Approval Desk Card (pending + recent decisions history) ─────────────────
// The Approval Desk's "action queue" role stays intact at the top; a folded
// Recent decisions section beneath it records every terminal outcome for the
// same instrument stream — trade / operator_skip / user_reject / auto_reject —
// with the operator's own reasoning as the one-line summary. Clicking a row
// opens the full lifecycle trace drawer (emit → filter_trace → pi_decision →
// risk_decision → proposal → order → close). This closes the loop on "why
// didn't this reach me?" once a candidate crosses INTO the operator stage —
// the Funnel card covers everything BEFORE that boundary.

const OUTCOME_LABEL: Record<string, string> = {
  trade: 'trade',
  operator_skip: 'operator skip',
  operator_watch: 'operator watch',
  user_reject: 'you rejected',
  auto_reject: 'auto rejected',
  reject_unknown: 'Reject attribution unavailable',
}

const OUTCOME_ICON: Record<string, string> = {
  trade: '✓',
  operator_skip: '✗',
  operator_watch: '◔',
  user_reject: '⊘',
  auto_reject: '⊗',
  reject_unknown: '?',
}

const OUTCOME_TONE: Record<string, 'green' | 'yellow' | 'red' | 'muted'> = {
  trade: 'green',
  operator_skip: 'muted',
  operator_watch: 'yellow',
  user_reject: 'red',
  auto_reject: 'red',
  reject_unknown: 'yellow',
}

function ApprovalDeskCard({ pendingProposals }: { pendingProposals: any[] }) {
  const navigate = useNavigate()
  const [historyOpen, setHistoryOpen] = useState(false)
  const [selectedOppId, setSelectedOppId] = useState<string | null>(null)
  const historyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (historyOpen) requestAnimationFrame(() =>
      historyRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }))
  }, [historyOpen])

  const { data: decisions } = useQuery({
    queryKey: ['operator-decisions'],
    queryFn: () => api.getOperatorDecisions(24, 20),
    refetchInterval: 60000,
  })

  const counts = decisions?.counts
  const items = decisions?.items || []
  const historyTotal = decisions?.total_count || 0

  return (
    <Card className="rail-card desk-panel">
      <CardHeader title="Approval Desk" />
      {pendingProposals.length > 0 ? (
        <>
          <Badge tone="yellow">{pendingProposals.length}</Badge>
          <ul className="approval-list">
            {pendingProposals.slice(0, 4).map((p: any) => (
              <li key={p.id}>
                <span><span className="mono">{fmtSymbol(p.symbol, p.market_type)}</span> {humanize(p.direction)}</span>
                <span className="ts mono">{fmtTimestamp(p.created_at)}</span>
              </li>
            ))}
          </ul>
          <Button className="btn-control mt-3" onClick={() => navigate('/proposals')}>Review →</Button>
        </>
      ) : (
        <p className="text-muted text-sm">No pending actions</p>
      )}

      {/* Recent decisions — collapsible history of terminal operator outcomes */}
      {decisions && (
        <>
          <table className="status-board decisions-toggle">
            <tbody>
              <tr
                className={`status-row status-row-clickable`}
                onClick={() => setHistoryOpen(x => !x)}
              >
                <th>Recent</th>
                <td className="status-cell">
                  <span className="status-value text-xs">
                    {historyTotal > 0
                      ? <RecentCountsInline counts={counts!} />
                      : <span className="text-muted">no decisions in 24h</span>}
                  </span>
                  <span className="status-row-chevron">{historyOpen ? '▾' : '▸'}</span>
                </td>
              </tr>
            </tbody>
          </table>
          <CollapsibleSection ref={historyRef} open={historyOpen}>
            <div className="decisions-panel">
              <div className="decisions-title">Last {decisions.window_hours}h · {items.length} shown</div>
              {items.length === 0 && (
                <p className="text-muted text-sm mb-0">No operator decisions yet.</p>
              )}
              {items.map((it) => {
                const tone = OUTCOME_TONE[it.outcome] || 'muted'
                return (
                  <button
                    key={`${it.decision_id || it.proposal_id || it.opportunity_id}-${it.decided_at}`}
                    type="button"
                    className={`decisions-row decisions-row-${tone}`}
                    onClick={() => it.opportunity_id && setSelectedOppId(it.opportunity_id)}
                    disabled={!it.opportunity_id}
                  >
                    <span className={`decisions-icon decisions-icon-${tone}`}>{OUTCOME_ICON[it.outcome] || '·'}</span>
                    <div className="decisions-body">
                      <div className="decisions-head">
                        <span className="mono">{fmtSymbol(it.symbol || '', it.market_type || undefined)}</span>
                        {it.direction && <span className="text-xs"> {humanize(it.direction)}</span>}
                        <span className="text-xs text-muted"> · {OUTCOME_LABEL[it.outcome] || it.outcome}</span>
                        <span className="ts mono text-xs">{fmtTimestamp(it.decided_at)}</span>
                      </div>
                      {it.one_line_reason && (
                        <div className="decisions-reason text-xs">{it.one_line_reason}</div>
                      )}
                    </div>
                    {it.opportunity_id && <span className="decisions-chev">▸</span>}
                  </button>
                )
              })}
            </div>
          </CollapsibleSection>
        </>
      )}

      <DecisionTraceDrawer
        opportunityId={selectedOppId}
        onClose={() => setSelectedOppId(null)}
      />
    </Card>
  )
}

function RecentCountsInline({ counts }: { counts: {
  trade: number; operator_skip: number; operator_watch: number
  user_reject: number; auto_reject: number; reject_unknown: number
} }) {
  return (
    <span className="decisions-counts">
      {counts.trade > 0 && <span className="decisions-count decisions-count-green">✓ {counts.trade}</span>}
      {counts.operator_skip > 0 && <span className="decisions-count decisions-count-muted">✗ {counts.operator_skip}</span>}
      {counts.user_reject > 0 && <span className="decisions-count decisions-count-red">⊘ {counts.user_reject}</span>}
      {counts.auto_reject > 0 && <span className="decisions-count decisions-count-red">⊗ {counts.auto_reject}</span>}
      {counts.reject_unknown > 0 && <span className="decisions-count decisions-count-yellow">? {counts.reject_unknown}</span>}
      {counts.operator_watch > 0 && <span className="decisions-count decisions-count-yellow">◔ {counts.operator_watch}</span>}
    </span>
  )
}

function DecisionTraceDrawer({ opportunityId, onClose }: {
  opportunityId: string | null; onClose: () => void
}) {
  const { data } = useQuery({
    queryKey: ['opportunity-trace', opportunityId],
    queryFn: () => api.getOpportunityTrace(opportunityId!),
    enabled: !!opportunityId,
  })
  const title = data?.symbol
    ? `${fmtSymbol(data.symbol, data.market || undefined)}  ·  ${humanize(data.direction || '')}`
    : 'Decision trace'
  return (
    <DetailDrawer open={!!opportunityId} onClose={onClose} title={title}>
      {!data && opportunityId && <p className="text-muted text-sm">Loading trace…</p>}
      {data && data.found === false && (
        <p className="text-muted text-sm">{data.final_reason || 'Opportunity trace not found.'}</p>
      )}
      {data && data.found !== false && (
        <>
          <DrawerSection label="Summary">
            <DrawerField label="Final state" value={data.final_state || '—'} />
            <DrawerField label="Final stage" value={data.final_stage || '—'} />
            <DrawerField label="Final reason" value={data.final_reason || '—'} />
            <DrawerField label="Scanner family" value={data.scanner_family || '—'} />
            <DrawerField label="Emitted at" value={data.created_at ? fmtTimestamp(data.created_at) : '—'} />
          </DrawerSection>
          {data.timeline && data.timeline.length > 0 && (
            <DrawerSection label={`Timeline (${data.timeline.length})`}>
              <ul className="trace-timeline">
                {data.timeline.map((ev, i) => (
                  <li key={i} className="trace-event">
                    <span className="trace-event-ts mono text-xs">{fmtTimestamp(ev.at)}</span>
                    {ev.stage && <span className="trace-event-stage text-xs">{ev.stage}</span>}
                    {(ev.summary || ev.reason) && (
                      <div className="trace-event-summary">{ev.summary || ev.reason}</div>
                    )}
                    {ev.details && Object.keys(ev.details).length > 0 && (
                      <pre className="trace-event-details">{JSON.stringify(ev.details, null, 2)}</pre>
                    )}
                  </li>
                ))}
              </ul>
            </DrawerSection>
          )}
        </>
      )}
    </DetailDrawer>
  )
}

// ── Opportunity Funnel Card ──────────────────────────────────────────────────
// Answers "why didn't we open a position?" in three sections:
//   1. Top diagnosis headline — one sentence, honestly derived from counts
//   2. 24h cumulative funnel — Discovered / Reviewed / Passed / Proposed, bar-lengths
//      show ratio to Discovered
//   3. Working-set snapshot (now) — accepted vs rejected, then rejection reasons
//      with dot/hollow-dot for count>0/=0, TTL examples annotated with
//      "expired X min ago · ttl was N min"
// Footer surfaces min_confidence_to_trade (clickable) + universe snapshot age.
//
// Time-scale honesty: cumulative counts explicitly labeled "last N h"; snapshot
// counts explicitly labeled "now". No fabricated causal join.
const REASON_LABEL: Record<string, string> = {
  confidence_below: 'confidence below threshold',
  guard_blocked: 'guard blocked',
  ttl_expired: 'signal TTL expired',
  unsupported_demo_symbol: 'demo venue unsupported',
  universe_not_eligible: 'not in universe',
  neutral_bias: 'no directional bias',
  factor_hard_blocked: 'factor blocked',
  execution_eligible_false: 'execution disabled',
}

// Reasons we always show a row for (even when count=0), so the reader can see
// "not this one" as an affirmative signal, not a silent omission.
const ALWAYS_SHOWN_REASONS = ['confidence_below', 'ttl_expired', 'guard_blocked']

const REASON_SUBTITLE: Record<string, string> = {
  ttl_expired: 'family window elapsed before ranker scan',
  confidence_below: 'ranker confidence gate rejected',
  guard_blocked: 'active guard suppressed entry',
  unsupported_demo_symbol: 'demo venue does not list this pair',
  universe_not_eligible: 'universe member marked ineligible',
  neutral_bias: 'scanner did not pick a side',
  execution_eligible_false: 'no live price at emit time',
}

function fmtReason(reason: string): string {
  return REASON_LABEL[reason] || reason.replace(/_/g, ' ')
}

function fmtDurMin(sec: number): string {
  const m = Math.floor(sec / 60)
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  const mm = m % 60
  return mm ? `${h}h ${mm}m` : `${h}h`
}

type FunnelData = ReturnType<typeof api.getOpportunityFunnel> extends Promise<infer R> ? R : never

function buildDiagnosis(data: FunnelData): { text: string; tone: 'green' | 'yellow' | 'red' | 'muted' } {
  const snap = data.snapshot
  const excluded = snap.excluded_by_reason || []
  const rejectedTotal = excluded.reduce((s, r) => s + (r.count || 0), 0)
  const executable = snap.executable_count

  if (data.snapshot.freshness === 'missing') {
    return { text: 'No operator eval yet in this session. Snapshot appears after the first candidate refresh.', tone: 'muted' }
  }
  if (data.snapshot.freshness === 'unavailable') {
    return { text: 'Operator snapshot time is not available; current supply cannot be verified.', tone: 'yellow' }
  }
  if (data.snapshot.freshness === 'stale') {
    const age = data.snapshot.age_sec != null ? ` (${fmtDurMin(data.snapshot.age_sec)} old)` : ''
    return { text: `Snapshot stale${age}; current candidate supply is not verified.`, tone: 'yellow' }
  }
  if (executable === 0 && rejectedTotal === 0) {
    return { text: `Idle: no candidates in ranker view (${data.still_open_count} awaiting review).`, tone: 'muted' }
  }
  if (executable === 0 && snap.main_reason === 'ttl_expired') {
    return { text: `Idle: all ${rejectedTotal} open signals expired before review.`, tone: 'red' }
  }
  if (executable === 0 && snap.main_reason === 'confidence_below') {
    return { text: `Idle: confidence gate rejected all ${rejectedTotal} candidates (threshold=${data.config.min_confidence_to_trade}).`, tone: 'red' }
  }
  if (executable === 0 && snap.main_reason) {
    return { text: `Idle: ${fmtReason(snap.main_reason)} blocked all ${rejectedTotal} candidates.`, tone: 'red' }
  }
  if (snap.below_floor) {
    return { text: `Low supply: ${executable} executable candidate${executable === 1 ? '' : 's'} (floor ${snap.floor}).`, tone: 'yellow' }
  }
  return { text: `Current snapshot: ${executable} executable candidate${executable === 1 ? '' : 's'} in ranker view.`, tone: 'green' }
}

function FunnelBar({ label, count, denom, hint, tone = 'muted' }: {
  label: string; count: number; denom: number; hint?: string
  tone?: 'green' | 'yellow' | 'red' | 'muted' | 'accent'
}) {
  const pct = denom > 0 ? Math.max(0, Math.min(100, (count / denom) * 100)) : 0
  return (
    <div className="funnel-bar-row">
      <span className="funnel-bar-label">{label}</span>
      <span className="funnel-bar-count mono">{count}</span>
      <div className="funnel-bar-track" aria-hidden>
        <div className={`funnel-bar-fill funnel-bar-fill-${tone}`} style={{ width: `${pct}%` }} />
      </div>
      {hint && <span className="funnel-bar-hint text-xs text-muted">{hint}</span>}
    </div>
  )
}

function OpportunityFunnelCard() {
  const navigate = useNavigate()
  const [openReason, setOpenReason] = useState<string | null>(null)
  const { data } = useQuery({
    queryKey: ['opportunity-funnel'],
    queryFn: () => api.getOpportunityFunnel(24),
    refetchInterval: 30000,
  })
  if (!data) return null

  const snap = data.snapshot
  const excluded = snap.excluded_by_reason || []
  const rejectedTotal = excluded.reduce((s, r) => s + (r.count || 0), 0)
  const executable = snap.executable_count
  const hasSnapshot = snap.freshness === 'fresh'

  const diag = buildDiagnosis(data)

  // Merge always-shown reasons with any others that appeared this snapshot
  const excludedByReason = new Map(excluded.map(r => [r.reason, r]))
  const reasonRows = [
    ...ALWAYS_SHOWN_REASONS.map(k => excludedByReason.get(k) || { reason: k, count: 0, examples: [] }),
    ...excluded.filter(r => !ALWAYS_SHOWN_REASONS.includes(r.reason)),
  ]

  const proposalConversionPct = data.emitted_count > 0
    ? (data.proposed_count / data.emitted_count) * 100
    : null

  return (
    <Card className="rail-card funnel-panel">
      <div className="funnel-head">
        <h3>Funnel</h3>
        <span className="text-xs text-muted">Market Observer → Operator</span>
      </div>

      {/* Section 1: Diagnosis */}
      <div className={`funnel-diagnosis funnel-diag-${diag.tone}`}>
        <span className={`funnel-diag-dot funnel-diag-dot-${diag.tone}`} />
        <span className="funnel-diag-text">{diag.text}</span>
      </div>

      {/* Section 2: 24h cumulative funnel */}
      <div className="funnel-section">
        <div className="funnel-section-title">
          <span>Last {data.window_hours} h</span>
          <span className="text-xs text-muted">cumulative</span>
        </div>
        <FunnelBar label="Discovered" count={data.emitted_count} denom={data.emitted_count} tone="muted" />
        <FunnelBar label="Awaiting ranker" count={data.still_open_count} denom={data.emitted_count} tone="muted" />
        <FunnelBar label="Queued for Operator" count={data.queued_count} denom={data.emitted_count} tone="accent" />
        <FunnelBar label="Reviewed" count={data.reviewed_count} denom={data.emitted_count} tone="muted"
          hint="terminal operator review" />
        <FunnelBar label="Expired / invalidated" count={data.expired_count} denom={data.emitted_count} tone="red" />
        <FunnelBar label="Proposed" count={data.proposed_count} denom={data.emitted_count}
          tone={data.proposed_count > 0 ? 'green' : 'muted'} hint="same emitted cohort" />
        {proposalConversionPct != null && (
          <div className="funnel-accept-rate">
            <span className="funnel-bar-label">proposal conversion</span>
            <span className="mono">{proposalConversionPct.toFixed(1)}%</span>
          </div>
        )}
      </div>

      {/* Section 3: Working set now */}
      {hasSnapshot && (
        <div className="funnel-section">
          <div className="funnel-section-title">
            <span>Working set (fresh snapshot)</span>
            <span className="text-xs text-muted">{executable + rejectedTotal} in ranker view</span>
          </div>
          <div className="funnel-workingset">
            <span className="funnel-ws-item">
              <span className="funnel-ws-mark funnel-ws-accepted">✓</span>
              accepted <span className="mono">{executable}</span>
            </span>
            <span className="funnel-ws-item">
              <span className="funnel-ws-mark funnel-ws-rejected">✗</span>
              rejected <span className="mono">{rejectedTotal}</span>
            </span>
          </div>

          {(rejectedTotal > 0 || reasonRows.some(r => r.count > 0)) && (
            <div className="funnel-reasons">
              <div className="funnel-reasons-title text-xs text-muted">Rejection reasons</div>
              {reasonRows.map((row) => {
                const isMain = row.reason === snap.main_reason && row.count > 0
                const hasCount = (row.count || 0) > 0
                const isOpen = openReason === row.reason
                const canExpand = hasCount && (row.examples?.length || 0) > 0
                return (
                  <div key={row.reason} className="funnel-reason">
                    <div
                      className={`funnel-reason-head${canExpand ? ' status-row-clickable' : ''}`}
                      onClick={canExpand ? () => setOpenReason(isOpen ? null : row.reason) : undefined}
                    >
                      <span className={`funnel-reason-dot${hasCount ? (isMain ? ' funnel-reason-dot-main' : ' funnel-reason-dot-active') : ' funnel-reason-dot-empty'}`}>
                        {hasCount ? '●' : '○'}
                      </span>
                      <span className={`funnel-reason-name${hasCount ? '' : ' funnel-reason-name-muted'}`}>
                        {fmtReason(row.reason)}
                      </span>
                      <span className={`funnel-reason-count mono${hasCount ? '' : ' funnel-reason-count-muted'}`}>{row.count}</span>
                      <span className="status-row-chevron">{canExpand ? (isOpen ? '▾' : '▸') : ''}</span>
                    </div>
                    {isOpen && (
                      <>
                        {REASON_SUBTITLE[row.reason] && (
                          <div className="funnel-reason-subtitle">{REASON_SUBTITLE[row.reason]}</div>
                        )}
                        {row.examples && row.examples.length > 0 && (
                          <ul className="funnel-examples">
                            {row.examples.slice(0, 4).map((ex, i) => {
                              const familyTtl = ex.scanner_family
                                ? data.family_ttl_sec[ex.scanner_family]
                                : undefined
                              return (
                                <li key={i}>
                                  <div className="funnel-example-head">
                                    <span className="mono">{fmtSymbol(ex.symbol || '', undefined)}</span>
                                    {ex.direction && <span className="text-xs"> {ex.direction}</span>}
                                    {ex.scanner_family && <span className="text-xs text-muted"> · {ex.scanner_family}</span>}
                                  </div>
                                  {row.reason === 'ttl_expired' && ex.expired_seconds != null && (
                                    <div className="funnel-example-hint">
                                      expired {fmtDurMin(ex.expired_seconds)} ago
                                      {familyTtl != null && ` · ttl was ${fmtDurMin(familyTtl)}`}
                                    </div>
                                  )}
                                </li>
                              )
                            })}
                          </ul>
                        )}
                      </>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Footer — thresholds & context */}
      <div className="funnel-footer">
        <div className="funnel-footer-row">
          <span className="text-xs text-muted">Confidence threshold</span>
          <button
            type="button"
            className="funnel-link mono"
            onClick={() => navigate('/settings?tab=trading')}
          >
            {data.config.min_confidence_to_trade}  ⚙
          </button>
        </div>
        {data.universe_snapshot_age_sec != null && (
          <div className="funnel-footer-row">
            <span className="text-xs text-muted">universe snapshot age</span>
            <span className="mono text-xs">{fmtDurMin(data.universe_snapshot_age_sec)}</span>
          </div>
        )}
      </div>
    </Card>
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
        <span className="status-value" title={title}>{value}</span>
        {onClick && (
          <span className="status-row-chevron">
            {externalLink ? '→' : (expanded ? '▾' : '▸')}
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
