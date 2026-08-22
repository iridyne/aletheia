import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import { Badge, Button, Card, StatusText, useConfirm } from '../components/ui'
import { DataTable } from '../components/DataTable'
import { DecisionTraceDrawer } from '../components/DecisionTraceDrawer'
import { humanize, fmtSymbol, fmtAge, fmtTimestamp } from '../lib/format'
import { positionKeys } from '../lib/queryKeys'

type OperatorDecisionRow = Awaited<ReturnType<typeof api.getOperatorDecisions>>['items'][number]

/**
 * Blotter — the merged trade-decision workstation (docs/design/frontend/blotter_design.md).
 *
 * Vertical reading order (owner-requested 2026-07-18):
 *   1. STATUS TOKEN STRIP   — five uppercase state tokens with numeric counts.
 *   2. CANDIDATE LEDGER     — full-width filterable table of every terminal
 *                             outcome (Filled / Skip / Watch / Reject / …).
 *                             Named "Ledger" not "Order History" because most
 *                             rows never became an order — they're the history
 *                             of the opportunity-scoring game itself.
 *   3. MIDDLE BAND          — Candidates (7/12) + Awaiting Sign-off (5/12) as
 *                             the current-state working panels below the ledger.
 *
 * V1 batches:
 *   Batch 1 — skeleton + top strip + route/nav (done).
 *   Batch 2 — Candidates panel + Awaiting Sign-off panel (done).
 *   Batch 3 (this update) — Candidate Ledger (was Order History; owner renamed
 *           2026-07-18 because there is no real order — it's the ledger of
 *           opportunity-scoring outcomes) and reordered above the middle band.
 *   Batch 4 — backend outcome/symbol filter params.
 *   Batch 5 — Overview clean-up + summary strip.
 */

// ─────────────────────────────────────────────────────────────────────────────
// Helper functions (mirrored from Overview.tsx pending final relocation to lib/)
// ─────────────────────────────────────────────────────────────────────────────

function fmtCountdown(seconds: number | null): string {
  if (seconds == null) return 'expiry unavailable'
  if (seconds <= 0) return 'expired'
  const minutes = Math.ceil(seconds / 60)
  return minutes < 60 ? `expires in ${minutes}m` : `expires in ${Math.ceil(minutes / 60)}h`
}

function fmtRetry(seconds: number | null): string | null {
  if (seconds == null) return null
  if (seconds <= 0) return 'retry due now'
  const minutes = Math.ceil(seconds / 60)
  return minutes < 60 ? `retry in ${minutes}m` : `retry in ${Math.ceil(minutes / 60)}h`
}

function candidateStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    deferred: 'waiting for facts',
    queued: 'operator queue',
    ranker_pending: 'ranker pending',
    ranker_cooldown: 'signal cooldown',
    ranker_backpressure: 'queue full',
    ranker_batch_limit: 'batch limit',
    ranker_position_duplicate: 'position exists',
    ranker_duplicate: 'duplicate symbol',
    ranker_already_evaluated: 'already evaluated',
    ranker_filter_wait: 'filter blocked',
    ranker_evidence_unavailable: 'evidence unavailable',
  }
  return labels[status] || humanize(status)
}

function entryTone(state: string): 'green' | 'yellow' | 'red' | 'muted' {
  if (state === 'position_open') return 'green'
  if (state === 'human_approval_required' || state === 'operator_deferred' || state === 'operator_queued') return 'yellow'
  if (state.startsWith('ranker_')) return 'yellow'
  if (state === 'no_active_candidate') return 'red'
  return 'muted'
}

// Direction pill helper (L / S) — replaces the more verbose humanize(direction).
function sideChip(direction: string | null | undefined): string {
  const d = String(direction || '').toLowerCase()
  if (d === 'long' || d === 'buy') return 'L'
  if (d === 'short' || d === 'sell') return 'S'
  return '—'
}

// ─────────────────────────────────────────────────────────────────────────────
// Top status token strip
// ─────────────────────────────────────────────────────────────────────────────

type TokenTone = 'muted' | 'accent' | 'green' | 'yellow' | 'red'

function StatusToken({ label, count, tone = 'muted', hint }: {
  label: string
  count: number | string | null
  tone?: TokenTone
  hint?: string
}) {
  return (
    <div className={`bl-token bl-token-${count == null ? 'muted' : tone}`} title={hint}>
      <div className="bl-token-label">{label}</div>
      <div className="bl-token-count mono">{count ?? '—'}</div>
    </div>
  )
}

export function StatusTokenStrip() {
  const { data: funnel, isPending: funnelPending, isError: funnelError } = useQuery({
    // Perf item 1: switch to the fast /current-snapshot endpoint so the
    // Blotter no longer pulls the heavy queued/reviewed/expired counts + the
    // candidate-supply snapshot payload just to render its top strip. Shared
    // queryKey with CandidatesPanel keeps them on a single deduped fetch.
    queryKey: ['opportunity-funnel-snapshot', 24],
    queryFn: () => api.getOpportunityFunnelCurrentSnapshot(24),
    refetchInterval: 30000,
    staleTime: 20000,
  })
  const { data: proposals, isPending: proposalsPending, isError: proposalsError } = useQuery({
    queryKey: ['proposals'],
    queryFn: api.getProposals,
    refetchInterval: 15000,
    staleTime: 10000,
  })
  const { data: decisions, isPending: decisionsPending, isError: decisionsError } = useQuery({
    queryKey: ['operator-decisions', 24],
    queryFn: () => api.getOperatorDecisions(24, 200),
    refetchInterval: 30000,
    staleTime: 20000,
  })

  const funnelAvailable = !funnelPending && !funnelError && funnel != null
  const proposalsAvailable = !proposalsPending && !proposalsError && proposals != null
  const decisionsAvailable = !decisionsPending && !decisionsError && decisions != null

  const counts = useMemo(() => {
    // still_open used to be a top-level field on the full funnel response;
    // it's identical to current.counts.open_opportunities, which the fast
    // /current-snapshot endpoint returns as part of `current`.
    const staged = funnelAvailable ? (funnel.current?.counts?.open_opportunities ?? 0) : null
    const awaitingSignoff = proposalsAvailable
      ? (proposals as any[]).filter(p => p.status === 'pending').length
      : null
    const working = funnelAvailable ? (funnel.current?.ranker?.counts?.carrier_active ?? 0) : null
    const filledToday = decisionsAvailable ? (decisions.counts?.trade ?? 0) : null
    const c = decisions?.counts
    const rejected24h = decisionsAvailable && c
      ? (c.operator_skip ?? 0) + (c.portfolio_guard_reject ?? 0) + (c.user_reject ?? 0) + (c.auto_reject ?? 0) + (c.reject_unknown ?? 0)
      : null
    return { staged, awaitingSignoff, working, filledToday, rejected24h }
  }, [funnel, funnelAvailable, proposals, proposalsAvailable, decisions, decisionsAvailable])

  return (
    <div className="bl-token-strip">
      <StatusToken label="Staged"            count={counts.staged}          tone={counts.staged          != null && counts.staged          > 0 ? 'accent' : 'muted'} hint="Candidates emitted, awaiting ranker review" />
      <StatusToken label="Awaiting Sign-off" count={counts.awaitingSignoff} tone={counts.awaitingSignoff != null && counts.awaitingSignoff > 0 ? 'yellow' : 'muted'} hint="Proposals pending your approval" />
      <StatusToken label="Working"           count={counts.working}         tone={counts.working         != null && counts.working         > 0 ? 'accent' : 'muted'} hint="Active carriers held by the ranker" />
      <StatusToken label="Filled (today)"    count={counts.filledToday}     tone={counts.filledToday     != null && counts.filledToday     > 0 ? 'green'  : 'muted'} hint="Trades filled in the last 24h" />
      <StatusToken label="Rejected (24h)"    count={counts.rejected24h}     tone={counts.rejected24h     != null && counts.rejected24h     > 0 ? 'red'    : 'muted'} hint="Skips + rejects in the last 24h" />
    </div>
  )
}

// Loading skeleton for the Candidates table — shaped like the real rows so
// perceived latency stays low while the (heavy) funnel endpoint is in flight.
function CandidatesSkeleton() {
  return (
    <div className="bl-cand-skeleton" aria-hidden="true">
      <div className="bl-cand-skeleton-diag" />
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="bl-cand-skeleton-row" />
      ))}
      <div className="bl-cand-skeleton-footer" />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Candidates panel — migrated from EntryStatusCard
// ─────────────────────────────────────────────────────────────────────────────

function CandidatesPanel() {
  // Shares queryKey with StatusTokenStrip so react-query dedupes to a single
  // in-flight request + warm cache. Uses the fast /current-snapshot endpoint
  // introduced in perf item 1 (docs/design/frontend/blotter_design.md
  // #performance-backlog) — the full-window counts + snapshot payload of the
  // parent /opportunity-funnel endpoint are unnecessary for this panel.
  const { data } = useQuery({
    queryKey: ['opportunity-funnel-snapshot', 24],
    queryFn: () => api.getOpportunityFunnelCurrentSnapshot(24),
    refetchInterval: 30000,
    staleTime: 20000,
  })

  if (!data) {
    return (
      <Card className="bl-panel bl-panel-candidates">
        <div className="bl-panel-head">
          <h3>Candidates</h3>
          <span className="text-muted text-xs">Loading…</span>
        </div>
        <CandidatesSkeleton />
      </Card>
    )
  }

  const current = data.current
  const tone = entryTone(current.state)
  const candidates = current.candidates || []
  const ranker = current.ranker

  // Layer-1 observability tooltip: when the diagnosis is defer-flavoured,
  // surface the reason-class breakdown + oldest live-defer age + 12h rate on
  // hover. The counts come straight from
  // /opportunity-funnel/current-snapshot's `counts` block (backed by
  // pipeline_progress._active_defer_stats). Rendered as a native `title` so
  // there is no new UI state — the yellow chip already exists.  Null defer fields
  // mean the backing query failed — render N/A, never zero.
  const c = current.counts
  const deferFieldsAvailable =
    c.market_quiet_defers != null &&
    c.candidate_specific_defers != null &&
    c.defer_rate != null
  const showDeferTip =
    (current.counts.deferred_candidates > 0 ||
      (c.market_quiet_defers != null && c.market_quiet_defers > 0) ||
      (c.candidate_specific_defers != null && c.candidate_specific_defers > 0))
  const oldestMin =
    c.oldest_defer_age_seconds != null
      ? Math.round(c.oldest_defer_age_seconds / 60)
      : null
  const deferTipLines = showDeferTip && deferFieldsAvailable
    ? [
        `Live defers · market-quiet ${c.market_quiet_defers}`
          + ` / candidate-specific ${c.candidate_specific_defers}`,
        oldestMin != null ? `Oldest live defer · ${oldestMin}m ago` : null,
        `12h defer rate · ${(c.defer_rate! * 100).toFixed(0)}%`,
        c.queue_capacity != null
          ? `Queue capacity · ${current.counts.queued_candidates}/${c.queue_capacity}`
          : null,
      ]
        .filter(Boolean)
        .join('\n')
    : !deferFieldsAvailable
    ? 'Defer diagnostics unavailable — the backing query failed.'
    : ''

  return (
    <Card className="bl-panel bl-panel-candidates">
      <div className="bl-panel-head">
        <h3>Candidates</h3>
        <span className="text-muted text-xs mono" title={fmtTimestamp(current.as_of)}>
          {humanize(current.primary_stage)} · updated {fmtAge(current.as_of)}
        </span>
      </div>

      {/* Diagnosis line — a single row, not a banner sentence. Dot + reason. */}
      <div
        className={`bl-diagnosis bl-diagnosis-${tone}`}
        title={showDeferTip ? deferTipLines : undefined}
      >
        <span className={`bl-diagnosis-dot bl-diagnosis-dot-${tone}`} />
        <span className="bl-diagnosis-text">{current.reason || current.headline}</span>
      </div>

      {/* Candidates table — Symbol / Side / Status / Countdown / Gate (with popover).
          # column dropped — for short candidate lists the index adds no information.
          Side chip is wrapped in a <span> inside the <td> so the cell keeps
          table-cell layout (the earlier version put `.bl-side` directly on the
          td, which forced display:inline-block and broke row-baseline alignment). */}
      {candidates.length > 0 ? (
        <table className="bl-cand-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th className="bl-cell-side">Side</th>
              <th>Status</th>
              <th>Countdown</th>
              <th>Gate</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c, i) => {
              const passed = c.passed_filter_count ?? 0
              const blocker = c.blocking_filter || c.reason_code
              const gateOk = !blocker
              return (
                <tr key={c.opportunity_id || `${c.symbol}-${i}`}>
                  <td className="mono">
                    {fmtSymbol(c.symbol || '', c.market_type || undefined)}
                    {c.source === 'ranker_preview' && (
                      <span className="bl-cand-source-tag">preview</span>
                    )}
                  </td>
                  <td className="bl-cell-side">
                    <span className={`bl-side bl-side-${sideChip(c.direction).toLowerCase()}`}>{sideChip(c.direction)}</span>
                  </td>
                  <td>
                    <span className={`bl-cand-status bl-cand-status-${c.status}`}>
                      {candidateStatusLabel(c.status)}
                    </span>
                  </td>
                  <td className="mono">
                    {fmtRetry(c.retry_in_sec) || fmtCountdown(c.expires_in_sec)}
                  </td>
                  <td>
                    <span className={`bl-gate bl-gate-${gateOk ? 'ok' : 'block'}`}>
                      <span className="bl-gate-label">
                        {gateOk ? 'PASS' : humanize(blocker || 'blocked')}
                      </span>
                      <span className="bl-gate-tip" role="tooltip">
                        <div><strong>Stage</strong> · {humanize(c.stage)}</div>
                        <div><strong>Passed filters</strong> · {passed}</div>
                        <div><strong>Blocker</strong> · {humanize(blocker || 'none')}</div>
                        {c.reason && <div className="bl-gate-tip-reason">{c.reason}</div>}
                      </span>
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      ) : (
        <p className="text-muted text-sm mb-2">
          No candidates in the ranker view. See the diagnosis above.
        </p>
      )}

      {/* Ranker footer — single mono line, no big evidence card */}
      {ranker && (
        <div className="bl-cand-footer mono text-xs">
          <span>open {ranker.counts.open}</span>
          <span>· pending {ranker.counts.not_evaluated}</span>
          <span>· cooldown {ranker.counts.cooldown}</span>
          <span>· backpressure {ranker.counts.backpressure}</span>
          <span>· carrier {ranker.counts.carrier_active}</span>
          {ranker.last_run && (
            <span className="bl-cand-footer-sep">
              · last run {ranker.last_run.status} · {humanize(ranker.last_run.trigger_type)}
            </span>
          )}
        </div>
      )}
    </Card>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Awaiting Sign-off panel — migrated from HumanApprovalCard, with inline actions
// ─────────────────────────────────────────────────────────────────────────────

function canApprovePaper(p: any): boolean {
  return p?.status === 'pending' && p?.execution_mode === 'paper_internal'
}

/**
 * Parse the conviction embedded in `agent_rationale` as a `\n---\n{JSON}` tail,
 * mirroring Proposals.tsx `parseDecisionMeta`. Returns null when absent or invalid.
 */
function parseConviction(rationale: string | null | undefined): number | null {
  if (!rationale) return null
  const sep = rationale.indexOf('\n---\n')
  if (sep === -1) return null
  try {
    const meta = JSON.parse(rationale.slice(sep + 5))
    return typeof meta?.conviction === 'number' ? meta.conviction : null
  } catch {
    return null
  }
}

function SignoffPanel() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const { data = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  const pending = (data as any[]).filter(p => p.status === 'pending')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [errById, setErrById] = useState<Record<string, string>>({})
  const [selectedOppId, setSelectedOppId] = useState<string | null>(null)
  const setErr = (id: string, msg: string) => setErrById(prev => ({ ...prev, [id]: msg }))
  const clearErr = (id: string) => setErrById(prev => { const n = { ...prev }; delete n[id]; return n })

  const approve = useMutation({
    mutationFn: (id: string) => api.approveProposal(id).then(r => ({ id, r })),
    onSuccess: ({ id, r }) => {
      // Match Proposals.tsx #113 fix — 200 responses can still be execution
      // failures; inspect the body before clearing errors.
      const execStatus = String((r as any)?.execution?.status || '').toLowerCase()
      if (execStatus === 'error' || execStatus === 'rejected') {
        setErr(id, String((r as any)?.execution?.message || `execution ${execStatus}`))
        return
      }
      clearErr(id)
      qc.invalidateQueries({ queryKey: ['proposals'] })
      qc.invalidateQueries({ queryKey: positionKeys.all })
    },
    onError: (e: any, id) => setErr(id, String(e?.message || 'approve failed')),
    onSettled: () => setBusyId(null),
  })
  const reject = useMutation({
    mutationFn: (id: string) => api.rejectProposal(id).then(r => ({ id, r })),
    onSuccess: ({ id }) => { clearErr(id); qc.invalidateQueries({ queryKey: ['proposals'] }) },
    onError: (e: any, id) => setErr(id, String(e?.message || 'reject failed')),
    onSettled: () => setBusyId(null),
  })

  async function doApprove(p: any) {
    if (!canApprovePaper(p)) {
      setErr(p.id, 'Only a pending proposal stored as local paper can be approved here.')
      return
    }
    if (!(await confirm({
      title: 'Approve local paper proposal?',
      description: `${p.direction.toUpperCase()} ${fmtSymbol(p.symbol, p.market_type)}`,
      detail: 'Local simulation; no exchange order placed.',
      confirmLabel: 'Approve',
    }))) return
    setBusyId(p.id)
    approve.mutate(p.id)
  }
  async function doReject(p: any) {
    if (!(await confirm({
      title: 'Reject proposal?',
      description: `${p.direction.toUpperCase()} ${fmtSymbol(p.symbol, p.market_type)}`,
      confirmLabel: 'Reject',
      variant: 'warning',
    }))) return
    setBusyId(p.id)
    reject.mutate(p.id)
  }

  return (
    <Card className="bl-panel bl-panel-signoff">
      <div className="bl-panel-head">
        <h3>Awaiting Sign-off</h3>
        {pending.length > 0
          ? <Badge tone="yellow">{pending.length}</Badge>
          : <StatusText tone="green">clear</StatusText>}
      </div>

      {pending.length === 0 ? (
        <p className="text-muted text-sm mb-0">
          No proposals awaiting decision. Automated candidates and waits are shown to the left.
        </p>
      ) : (
        <ul className="bl-signoff-list">
          {pending.map(p => {
            const conv = parseConviction(p.agent_rationale)
            const err = errById[p.id]
            const busy = busyId === p.id
            const paperEligible = canApprovePaper(p)
            const traceable = !!p.opportunity_id
            const openTrace = () => { if (traceable) setSelectedOppId(p.opportunity_id) }
            return (
              <li
                key={p.id}
                className={`bl-signoff-row${traceable ? ' bl-signoff-row-traceable' : ''}`}
                onClick={openTrace}
                onKeyDown={traceable ? (e) => {
                  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openTrace() }
                } : undefined}
                role={traceable ? 'button' : undefined}
                tabIndex={traceable ? 0 : undefined}
                title={traceable ? 'Click to open the full opportunity trace' : undefined}
              >
                <div className="bl-signoff-head">
                  <span className="mono bl-signoff-sym">{fmtSymbol(p.symbol, p.market_type)}</span>
                  <span className={`bl-side bl-side-${sideChip(p.direction).toLowerCase()}`}>{sideChip(p.direction)}</span>
                  {conv != null && <span className="bl-signoff-conv mono" title="Conviction">conv {conv}</span>}
                  <span className="text-muted text-xs mono" title={fmtTimestamp(p.created_at)}>
                    {fmtAge(p.created_at)}
                  </span>
                </div>
                <div className="bl-signoff-meta text-xs text-muted">
                  {humanize(p.execution_mode)} · {humanize(p.order_type)}
                  {p.leverage ? ` · lev ${p.leverage}x` : ''}
                  {p.risk_reward != null ? ` · r:r ${p.risk_reward}:1` : ''}
                </div>
                {/* Actions row stops click bubbling so pressing a button
                    doesn't also fire the row's openTrace handler. */}
                <div className="bl-signoff-actions" onClick={e => e.stopPropagation()}>
                  {paperEligible ? (
                    <Button className="btn-sm" onClick={() => doApprove(p)} disabled={busy}>
                      {busy && approve.isPending ? 'Approving…' : 'Approve'}
                    </Button>
                  ) : (
                    <StatusText tone="yellow" className="text-xs">
                      Paper approval unavailable for this stored mode
                    </StatusText>
                  )}
                  <Button className="btn-sm" variant="secondary" onClick={() => doReject(p)} disabled={busy}>
                    {busy && reject.isPending ? 'Rejecting…' : 'Reject'}
                  </Button>
                </div>
                {err && <div className="bl-signoff-err" role="alert">✗ {err}</div>}
              </li>
            )
          })}
        </ul>
      )}
      <DecisionTraceDrawer
        opportunityId={selectedOppId}
        onClose={() => setSelectedOppId(null)}
      />
    </Card>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Candidate Ledger — full-width table above the middle band (Batch 3)
// ─────────────────────────────────────────────────────────────────────────────

// Compact table-friendly labels (Bloomberg style: short tokens, mixed case).
const OUTCOME_ORDER = ['trade', 'operator_skip', 'operator_watch', 'portfolio_guard_reject', 'user_reject', 'auto_reject', 'reject_unknown'] as const
type Outcome = typeof OUTCOME_ORDER[number]

const OUTCOME_LABEL: Record<Outcome, string> = {
  trade: 'Filled',
  operator_skip: 'Op Skip',
  operator_watch: 'Watch',
  portfolio_guard_reject: 'Guard Reject',
  user_reject: 'User Reject',
  auto_reject: 'Auto Reject',
  reject_unknown: 'Reject (?)',
}

const OUTCOME_TONE: Record<Outcome, 'green' | 'yellow' | 'red' | 'muted'> = {
  trade: 'green',
  operator_skip: 'muted',
  operator_watch: 'yellow',
  portfolio_guard_reject: 'red',
  user_reject: 'red',
  auto_reject: 'red',
  reject_unknown: 'yellow',
}

const TIME_WINDOWS: { hours: number; label: string }[] = [
  { hours: 1,   label: '1h' },
  { hours: 6,   label: '6h' },
  { hours: 24,  label: '24h' },
  { hours: 168, label: '7D' },
]

const col = createColumnHelper<OperatorDecisionRow>()

export function CandidateLedger() {
  const [hours, setHours] = useState(24)
  const [outcomeFilter, setOutcomeFilter] = useState<Set<Outcome>>(new Set())
  const [sideFilter, setSideFilter] = useState<'all' | 'l' | 's'>('all')
  const [search, setSearch] = useState('')
  const [selectedOppId, setSelectedOppId] = useState<string | null>(null)

  // Perf item 6: `outcome` filter is pushed down to the server so that when
  // total history exceeds the 200-row limit, we still get the most recent 200
  // rows OF THE SELECTED OUTCOME instead of accidentally clipping older
  // Filled rows behind a wall of recent Skips. `side` and `search` stay
  // client-side: side matches on direction (~50% of rows either way so
  // scanning 200 covers both), and search is a symbol+reason substring
  // matcher, not something the backend's exact-symbol param can express.
  //
  // Counts on the facet chips remain drawn from the full-window response;
  // backend contract guarantees `counts` and `total_count` ignore filters.
  const outcomeParam = useMemo(() => {
    if (outcomeFilter.size === 0) return undefined
    return Array.from(outcomeFilter).join(',')
  }, [outcomeFilter])

  const { data, isPending, isError } = useQuery({
    queryKey: ['operator-decisions', hours, outcomeParam || 'all'],
    queryFn: () => api.getOperatorDecisions(hours, 200, { outcome: outcomeParam }),
    refetchInterval: 60000,
    staleTime: 20000,
  })

  const items = data?.items ?? []
  const counts = data?.counts

  // Map query state → DataTable.state; initial error/no data → unavailable, success empty → empty, error with cached data → stale
  const tableState = isPending && items.length === 0
    ? 'loading'
    : isError && items.length === 0
      ? 'unavailable'
      : isError && items.length > 0
        ? 'stale'
        : undefined

  const filteredItems = useMemo(() => {
    return items.filter(it => {
      // outcome is now server-side; kept a defensive check in case a stale
      // cache is served across a queryKey change.
      if (outcomeFilter.size > 0 && !outcomeFilter.has(it.outcome as Outcome)) return false
      if (sideFilter !== 'all') {
        const d = String(it.direction || '').toLowerCase()
        const isL = d === 'long' || d === 'buy'
        const isS = d === 'short' || d === 'sell'
        if (sideFilter === 'l' && !isL) return false
        if (sideFilter === 's' && !isS) return false
      }
      if (search.trim()) {
        const s = search.trim().toLowerCase()
        const sym = String(it.symbol || '').toLowerCase()
        const reason = String(it.one_line_reason || '').toLowerCase()
        if (!sym.includes(s) && !reason.includes(s)) return false
      }
      return true
    })
  }, [items, outcomeFilter, sideFilter, search])

  const toggleOutcome = (o: Outcome) => {
    setOutcomeFilter(prev => {
      const n = new Set(prev)
      if (n.has(o)) n.delete(o); else n.add(o)
      return n
    })
  }

  const hasFilters = outcomeFilter.size > 0 || sideFilter !== 'all' || search.trim().length > 0
  const reset = () => { setOutcomeFilter(new Set()); setSideFilter('all'); setSearch('') }

  const columns = useMemo(() => [
    col.accessor('decided_at', {
      header: 'Time',
      cell: info => (
        <span className="mono text-xs" title={fmtTimestamp(info.getValue())}>
          {fmtAge(info.getValue())}
        </span>
      ),
    }),
    col.accessor('symbol', {
      header: 'Symbol',
      cell: info => (
        <span className="mono">
          {fmtSymbol(info.getValue() || '', info.row.original.market_type || undefined)}
        </span>
      ),
    }),
    col.accessor('direction', {
      header: 'Side',
      cell: info => {
        const s = sideChip(info.getValue())
        return <span className={`bl-side bl-side-${s.toLowerCase()}`}>{s}</span>
      },
    }),
    col.accessor('outcome', {
      header: 'Outcome',
      cell: info => {
        const o = info.getValue() as Outcome
        return (
          <span className={`bl-outcome bl-outcome-${OUTCOME_TONE[o] || 'muted'}`}>
            {OUTCOME_LABEL[o] || humanize(o)}
          </span>
        )
      },
    }),
    col.accessor('conviction', {
      header: 'Conv',
      cell: info => {
        const v = info.getValue()
        return v != null
          ? <span className="mono">{v}</span>
          : <span className="text-muted">—</span>
      },
    }),
    col.accessor('one_line_reason', {
      header: 'Reason',
      cell: info => <span className="text-sm">{info.getValue() || '—'}</span>,
      enableSorting: false,
    }),
  ], [])

  return (
    <Card className="bl-panel bl-panel-ledger">
      <div className="bl-panel-head">
        <h3>Candidate Ledger</h3>
        <span className="text-muted text-xs">
          {filteredItems.length}
          {hasFilters ? ` of ${items.length}` : ''}
          {' '}row{filteredItems.length === 1 ? '' : 's'}
          {' · last '}{hours < 24 ? `${hours}h` : `${hours / 24}D`}
          {' · every terminal outcome'}
        </span>
      </div>

      <div className="bl-toolbar">
        <input
          className="bl-toolbar-search"
          type="search"
          placeholder="Search symbol or reason…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          aria-label="Search Order History"
        />

        {/* Outcome facets — every terminal outcome as an equal citizen.
            Root cause of the earlier "only trades are visible" perception was
            the collapsed pulse UI; here every outcome is on the toolbar with
            its full-window count, so a reject is as legible as a fill. */}
        <div className="bl-toolbar-facets" role="group" aria-label="Filter by outcome">
          {OUTCOME_ORDER.map(o => {
            const n = counts?.[o] ?? 0
            const active = outcomeFilter.has(o)
            return (
              <Button
                key={o}
                type="button"
                className={`bl-facet bl-facet-${OUTCOME_TONE[o]}${active ? ' active' : ''}`}
                onClick={() => toggleOutcome(o)}
                title={active ? 'Click to remove filter' : `Filter to ${OUTCOME_LABEL[o]}`}
                aria-pressed={active}
              >
                {OUTCOME_LABEL[o]}
                <span className="bl-facet-count mono">{n}</span>
              </Button>
            )
          })}
        </div>

        <div className="bl-toolbar-side" role="group" aria-label="Filter by side">
          {(['all', 'l', 's'] as const).map(s => (
            <Button
              key={s}
              type="button"
              className={`bl-side-btn${sideFilter === s ? ' active' : ''}`}
              onClick={() => setSideFilter(s)}
              aria-pressed={sideFilter === s}
            >
              {s === 'all' ? 'All' : s.toUpperCase()}
            </Button>
          ))}
        </div>

        <div className="bl-toolbar-time" role="group" aria-label="Time window">
          {TIME_WINDOWS.map(w => (
            <Button
              key={w.hours}
              type="button"
              className={`bl-time-btn${hours === w.hours ? ' active' : ''}`}
              onClick={() => setHours(w.hours)}
              aria-pressed={hours === w.hours}
            >
              {w.label}
            </Button>
          ))}
        </div>

        {hasFilters && (
          <Button type="button" className="bl-reset-btn" onClick={reset}>
            ✕ Reset
          </Button>
        )}
      </div>

      <DataTable
        data={filteredItems}
        columns={columns}
        pageSize={20}
        onRowClick={(row) => row.opportunity_id && setSelectedOppId(row.opportunity_id)}
        state={tableState}
        emptyMessage={
          hasFilters
            ? 'No rows match the current filters.'
            : `No terminal decisions in the last ${hours < 24 ? `${hours}h` : `${hours / 24}D`}.`
        }
      />

      <DecisionTraceDrawer
        opportunityId={selectedOppId}
        onClose={() => setSelectedOppId(null)}
      />
    </Card>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────

export default function Blotter() {
  return (
    <div>
      <PageHeader
        title="Blotter"
        role="Decide · the merged trade-decision workstation. Every candidate, every pending proposal, and every terminal outcome on a single grid. Sell-side EMS terminology (blotter = pending + working + filled + rejected as columns, not separate pages)."
      />

      <StatusTokenStrip />

      {/* Ledger first — historical mass is the higher-information surface;
          the current-state panels sit below as the working area. */}
      <CandidateLedger />

      <div className="bl-mid-band">
        <div className="bl-mid-candidates">
          <CandidatesPanel />
        </div>
        <div className="bl-mid-signoff">
          <SignoffPanel />
        </div>
      </div>
    </div>
  )
}
