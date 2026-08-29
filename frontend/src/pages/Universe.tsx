import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type {
  CandidateAttention,
  EntryPlan,
  EntryWorkbenchResponse,
  PlanningHistoryResponse,
} from '../types'
import { DataTable } from '../components/DataTable'
import { DetailDrawer, DrawerField, DrawerSection } from '../components/DetailDrawer'
import PageHeader from '../components/PageHeader'
import MetricTile from '../components/MetricTile'
import Tabs from '../components/Tabs'
import { Badge, Button, Card } from '../components/ui'
import { fmtNum, fmtSymbol, fmtTimestamp, humanize } from '../lib/format'

const planCol = createColumnHelper<EntryPlan>()
const candidateCol = createColumnHelper<CandidateAttention>()
const historyCol = createColumnHelper<PlanningHistoryResponse['items'][number]>()

function entryLabel(plan: EntryPlan) {
  if (!plan.plan) return 'Not available'
  if (plan.execution_mode === 'RESTING_MAKER') return `Limit ${fmtNum(plan.plan.limit_price)}`
  return `Zone ${plan.plan.entry_zone.map(value => fmtNum(value)).join(' – ')}`
}

function triggerLabel(plan: EntryPlan) {
  if (plan.execution_mode === 'RESTING_MAKER') return 'Not required'
  return plan.plan?.trigger_spec_id
    ? `${humanize(plan.plan.trigger_spec_id)} · ${plan.plan.trigger_spec_version ?? 'version unavailable'}`
    : 'Unavailable'
}

function riskReward(plan: EntryPlan) {
  if (!plan.plan) return '—'
  const entry = plan.plan.limit_price ?? (plan.plan.entry_zone[0] + plan.plan.entry_zone[1]) / 2
  const risk = Math.abs(entry - plan.plan.stop_price)
  return risk > 0 ? (Math.abs(plan.plan.target_price - entry) / risk).toFixed(2) : '—'
}

function tacticLabel(plan: EntryPlan) {
  return plan.plan?.order_tactic === 'POST_ONLY_LIMIT'
    ? 'Post-Only limit'
    : plan.plan?.order_tactic === 'OPPOSING_BBO_IOC'
      ? 'Opposing-BBO IOC'
      : 'Unavailable'
}

function expiryLabel(value?: string) {
  if (!value) return 'Not recorded'
  const remaining = new Date(value).getTime() - Date.now()
  if (!Number.isFinite(remaining)) return 'Unavailable'
  if (remaining <= 0) return `${fmtTimestamp(value)} · expired`
  const minutes = Math.ceil(remaining / 60_000)
  return `${fmtTimestamp(value)} · ${minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m`} left`
}

function evidenceLabel(value?: string | null) {
  if (!value) return 'Not recorded'
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) && parsed.length ? parsed.join(', ') : 'Not recorded'
  } catch {
    return value
  }
}

// Calibration compatibility: by_cohort_horizon rows remain isolated by
// row.direction and row.market_type across the owner-facing projection.
const planColumns = [
  planCol.accessor('symbol', {
    header: 'Exact instrument',
    cell: info => <><strong className="mono">{fmtSymbol(info.getValue(), info.row.original.market_type)}</strong><br /><span>{info.row.original.venue} · {info.row.original.market_type}</span></>,
  }),
  planCol.accessor('direction', { header: 'Side', cell: info => humanize(info.getValue()) }),
  planCol.accessor(row => entryLabel(row), { id: 'entry', header: 'Entry', cell: info => <span className="mono">{info.getValue()}</span> }),
  planCol.accessor(row => triggerLabel(row), { id: 'trigger', header: 'TriggerSpec' }),
  planCol.accessor(row => `${fmtNum(row.plan?.stop_price)} / ${fmtNum(row.plan?.target_price)} / ${riskReward(row)}`, {
    id: 'risk_geometry', header: 'Stop / Target / R:R', cell: info => <span className="mono">{info.getValue()}</span>,
  }),
  planCol.accessor(row => `${humanize(row.execution_mode)} · ${tacticLabel(row)}`, { id: 'execution', header: 'Execution / tactic' }),
  planCol.accessor('status', { header: 'Status', cell: info => <Badge tone="green">{humanize(info.getValue())}</Badge> }),
  planCol.accessor(row => expiryLabel(row.plan?.expires_at), { id: 'expires_at', header: 'Expiry / countdown' }),
]

const candidateColumns = [
  candidateCol.accessor('symbol', {
    header: 'Candidate',
    cell: info => <strong className="mono">{fmtSymbol(info.getValue(), info.row.original.market_type)}</strong>,
  }),
  candidateCol.accessor(row => `${humanize(row.candidate_family)} · ${evidenceLabel(row.inclusion_reason)}`, { id: 'why', header: 'Family / why selected' }),
  candidateCol.accessor(row => `Rank ${row.rank ?? '—'} · Score ${row.total_score ?? '—'} · Liquidity ${row.liquidity_score ?? '—'} · Spread ${row.spread_bps ?? '—'} bps`, { id: 'quality', header: 'Score / market quality' }),
  candidateCol.accessor(row => `${humanize(row.status)} · ${humanize(row.attention_status)}`, { id: 'state', header: 'Planning / attention' }),
  candidateCol.accessor('last_requalified_at', { header: 'Last requalified', cell: info => fmtTimestamp(info.getValue()) }),
  candidateCol.accessor(row => `${fmtTimestamp(row.soft_expires_at)} / ${fmtTimestamp(row.hard_expires_at)}`, { id: 'deadlines', header: 'Soft / hard deadline' }),
  candidateCol.accessor('attention_terminal_reason', { header: 'Terminal reason', cell: info => info.getValue() ? humanize(info.getValue()!) : 'Active' }),
]

const historyColumns = [
  historyCol.accessor('symbol', { header: 'Instrument', cell: info => <span className="mono">{fmtSymbol(info.getValue(), info.row.original.market_type)}</span> }),
  historyCol.accessor('updated_at', { header: 'Updated', cell: info => fmtTimestamp(info.getValue()) }),
  historyCol.accessor('status', { header: 'Planning result', cell: info => humanize(info.getValue()) }),
  historyCol.accessor('attention_terminal_reason', { header: 'Attention result', cell: info => info.getValue() ? humanize(info.getValue()!) : 'Active' }),
  historyCol.accessor('entry_plan_status', { header: 'Entry Plan', cell: info => info.getValue() ? humanize(info.getValue()!) : '—' }),
]

export default function Universe() {
  const navigate = useNavigate()
  const [search, setSearch] = useSearchParams()
  const [historyBefore, setHistoryBefore] = useState<string | null>(null)
  const [candidateFilter, setCandidateFilter] = useState('needs_planning')
  const tab = search.get('tab') || 'plans'
  const selectedPlanId = search.get('plan_id')
  const selectedCandidateId = search.get('candidate_id')
  const workbench = useQuery<EntryWorkbenchResponse>({
    queryKey: ['entry-workbench'],
    queryFn: api.getEntryWorkbench,
    refetchInterval: 30_000,
  })
  const planDetail = useQuery<EntryPlan>({
    queryKey: ['entry-plan', selectedPlanId],
    queryFn: () => api.getEntryPlan(selectedPlanId!),
    enabled: Boolean(selectedPlanId),
  })
  const history = useQuery<PlanningHistoryResponse>({
    queryKey: ['planning-history', historyBefore],
    queryFn: () => api.getPlanningHistory(50, historyBefore),
    enabled: tab === 'history',
  })

  const data = workbench.data
  const workbenchUnavailable = workbench.isError && !data
  const summary = data?.summary
  const plans = data?.entry_plans
  const candidates = data?.candidate_attention
  const filteredCandidates = useMemo(() => {
    const rows = candidates ?? []
    const now = Date.now()
    if (candidateFilter === 'all_active') return rows
    if (candidateFilter === 'expiring') return rows.filter(row => Math.min(
      new Date(row.soft_expires_at).getTime(),
      new Date(row.hard_expires_at).getTime(),
    ) - now <= 5 * 60_000)
    if (candidateFilter === 'flagged') return rows.filter(row => row.execution_eligible === 0 || evidenceLabel(row.risk_flags) !== 'Not recorded')
    if (candidateFilter === 'stale') return rows.filter(row => row.candidate_expires_at && new Date(row.candidate_expires_at).getTime() <= now)
    return rows.filter(row => ['pending', 'planning', 'unavailable'].includes(row.status))
  }, [candidateFilter, candidates])
  const setTab = (next: string) => {
    const params = new URLSearchParams(search)
    params.set('tab', next)
    params.delete('plan_id')
    params.delete('candidate_id')
    setSearch(params)
  }
  const openPlan = (plan: EntryPlan) => {
    const params = new URLSearchParams(search)
    params.set('tab', 'plans')
    params.set('plan_id', plan.id)
    setSearch(params)
  }
  const openCandidateId = (candidateId: string) => {
    const params = new URLSearchParams(search)
    params.set('tab', 'candidates')
    params.set('candidate_id', candidateId)
    setSearch(params)
  }
  const openCandidate = (candidate: CandidateAttention) => openCandidateId(candidate.last_candidate_id)
  const closeDrawer = () => {
    const params = new URLSearchParams(search)
    params.delete('plan_id')
    params.delete('candidate_id')
    setSearch(params)
  }
  const universeStatus = data?.universe.status
  const statusLabel = universeStatus === 'active'
    ? 'Active'
    : universeStatus === 'disabled'
      ? 'Universe disabled'
      : universeStatus === 'stale_snapshot'
        ? 'Stale snapshot'
      : universeStatus === 'no_active_snapshot'
        ? 'No active snapshot'
        : 'Not available'

  const tabs = [
    {
      id: 'plans',
      label: 'Entry Plans',
      badge: plans?.length,
      render: () => <ReadSection available={workbenchUnavailable ? false : data?.availability.entry_plans?.available} loading={workbench.isPending} label="Entry Plans">
        <DataTable columns={planColumns} data={plans ?? []} onRowClick={openPlan} emptyMessage="No active Entry Plans." tableId="entry-workbench-plans" />
      </ReadSection>,
    },
    {
      id: 'candidates',
      label: 'Candidates',
      badge: candidates?.length,
      render: () => <ReadSection available={workbenchUnavailable ? false : data?.availability.candidate_attention?.available} loading={workbench.isPending} label="Candidate attention">
        <label className="entry-workbench-filter">Candidate view
          <select value={candidateFilter} onChange={event => setCandidateFilter(event.target.value)}>
            <option value="needs_planning">Needs Planning</option>
            <option value="all_active">All Active</option>
            <option value="expiring">Expiring</option>
            <option value="flagged">Flagged</option>
            <option value="stale">Stale</option>
          </select>
        </label>
        <DataTable columns={candidateColumns} data={filteredCandidates} onRowClick={openCandidate} emptyMessage={`No Candidates in ${humanize(candidateFilter)}.`} tableId="entry-workbench-candidates" />
      </ReadSection>,
    },
    {
      id: 'history',
      label: 'Planning History',
      render: () => <ReadSection available={!history.isError} loading={history.isPending} label="Planning history">
        <DataTable columns={historyColumns} data={history.data?.items ?? []} emptyMessage="No planning history." tableId="entry-workbench-history" />
        {historyBefore && <Button variant="secondary" onClick={() => setHistoryBefore(null)}>Newest</Button>}
        {history.data?.next_before && <Button variant="secondary" onClick={() => setHistoryBefore(history.data!.next_before)}>Next</Button>}
      </ReadSection>,
    },
  ]

  return <div className="entry-workbench">
    <PageHeader
      title="Entry Workbench"
      role="Candidate attention → Entry Plan → Execution. Plans remain visible after Candidate attention ends."
      actions={<Button variant="secondary" onClick={() => navigate('/settings?tab=risk&section=universe')}>Settings</Button>}
    />

    <section className="entry-workbench-summary" aria-label="Entry summary">
      <MetricTile label="Active Entry Plans" value={summary?.active_entry_plans ?? '—'} context="waiting through partially filled" />
      <MetricTile label="Needs Planning" value={summary?.needs_planning ?? '—'} context="active attention, retryable work" />
      <MetricTile label="Expiring Soon" value={summary?.expiring_soon ?? '—'} context="within 5 minutes" tone={summary?.expiring_soon ? 'warning' : 'neutral'} />
      <MetricTile label="Universe Status" value={statusLabel} context={data?.universe.snapshot ? `snapshot ${data.universe.snapshot.id.slice(0, 8)}` : 'no Candidate facts fabricated'} />
    </section>

    {workbenchUnavailable ? <Card role="alert">Entry Workbench is unavailable. Summary counts and rows are not inferred.</Card> : null}

    {data?.universe.fallback_instruments.length ? <Card className="entry-workbench-notice">
      <strong>{statusLabel}.</strong> Fallback instruments available: {data.universe.fallback_instruments.join(', ')}. They are not durable Candidates.
    </Card> : null}

    <Tabs value={tab} onChange={setTab} tabs={tabs} />

    {selectedPlanId && <PlanDrawer plan={planDetail.data} unavailable={planDetail.isError} onClose={closeDrawer} onCandidate={() => planDetail.data && openCandidateId(planDetail.data.candidate_id)} onTrades={() => navigate(`/trades?tab=blotter&entry_plan_id=${encodeURIComponent(selectedPlanId)}`)} onCharts={() => planDetail.data && navigate(`/charts?symbol=${encodeURIComponent(planDetail.data.symbol)}&exchange=${encodeURIComponent(planDetail.data.venue)}&market_type=${encodeURIComponent(planDetail.data.market_type)}&entry_plan_id=${encodeURIComponent(selectedPlanId)}`)} />}
    {selectedCandidateId && <CandidateDrawer candidateId={selectedCandidateId} candidate={candidates?.find(item => item.last_candidate_id === selectedCandidateId)} onClose={closeDrawer} />}
  </div>
}

function ReadSection({ available, loading, label, children }: { available?: boolean; loading: boolean; label: string; children: React.ReactNode }) {
  if (loading) return <Card role="status">Loading {label}…</Card>
  if (available === false) return <Card role="alert">{label} is unavailable. Counts and rows are not inferred.</Card>
  return <>{children}</>
}

function PlanDrawer({ plan, unavailable, onClose, onCandidate, onTrades, onCharts }: { plan?: EntryPlan; unavailable: boolean; onClose: () => void; onCandidate: () => void; onTrades: () => void; onCharts: () => void }) {
  return <DetailDrawer open title={plan ? `${plan.symbol} Entry Plan` : 'Entry Plan'} onClose={onClose}>
    {unavailable ? <p>Entry Plan detail is unavailable.</p> : !plan ? <p>Loading exact plan…</p> : <>
      <DrawerSection label="Plan Summary">
        <DrawerField label="Plan ID" value={plan.id} mono />
        <DrawerField label="Candidate ID" value={plan.candidate_id} mono />
        <DrawerField label="Exact instrument" value={`${plan.venue} · ${fmtSymbol(plan.symbol, plan.market_type)} · ${plan.market_type}`} mono />
        <DrawerField label="Side" value={humanize(plan.direction)} />
        <DrawerField label="Contract" value={plan.contract_valid ? 'Validated' : 'Invalid'} />
        <DrawerField label="Status" value={humanize(plan.status)} />
        <DrawerField label="Expiry / countdown" value={expiryLabel(plan.plan?.expires_at)} />
      </DrawerSection>
      <DrawerSection label="Entry Geometry">
        <DrawerField label="Entry" value={entryLabel(plan)} mono />
        <DrawerField label="Entry zone" value={plan.plan?.entry_zone?.map(value => fmtNum(value)).join(' – ')} mono />
        <DrawerField label="Invalidation" value={plan.plan?.invalidation_price} mono />
        <DrawerField label="Stop" value={plan.plan?.stop_price} mono />
        <DrawerField label="Target" value={plan.plan?.target_price} mono />
        <DrawerField label="R:R" value={riskReward(plan)} mono />
      </DrawerSection>
      <DrawerSection label="Trigger & Execution">
        <DrawerField label="TriggerSpec" value={triggerLabel(plan)} />
        <DrawerField label="Execution mode" value={humanize(plan.execution_mode)} />
        <DrawerField label="Order tactic" value={tacticLabel(plan)} />
        <DrawerField label="TIF / price rule" value={`${plan.plan?.time_in_force ?? 'Unavailable'} · ${humanize(plan.plan?.price_rule)}`} />
        <DrawerField label="BBO reference" value={plan.plan?.bbo_reference ?? 'Not required'} />
        <DrawerField label="Max wait" value={plan.plan?.max_wait_sec == null ? 'Not recorded' : `${plan.plan.max_wait_sec}s`} />
        <DrawerField label="Partial fill" value={humanize(plan.plan?.partial_fill_policy)} />
      </DrawerSection>
      <DrawerSection label="Thesis & Evidence">
        <DrawerField label="Thesis" value={plan.plan?.thesis} />
        <DrawerField label="Scenario" value={plan.plan?.scenario} />
        <DrawerField label="Evidence" value={plan.plan?.evidence_refs?.length ? plan.plan.evidence_refs.join(', ') : 'Not recorded'} mono />
      </DrawerSection>
      <DrawerSection label="Risk Envelope">
        <DrawerField label="Max notional" value={fmtNum(plan.plan?.max_notional)} mono />
        <DrawerField label="Max risk" value={plan.plan?.max_risk_pct == null ? 'Not recorded' : `${plan.plan.max_risk_pct}%`} mono />
        <DrawerField label="Max leverage" value={plan.plan?.max_leverage == null ? 'Not recorded' : `${plan.plan.max_leverage}×`} mono />
        <DrawerField label="Expected horizon" value={plan.plan?.expected_horizon_hours == null ? 'Not recorded' : `${plan.plan.expected_horizon_hours}h`} />
        <DrawerField label="Permission / account" value={`${plan.plan?.required_permission ?? 'Unavailable'} · ${plan.plan?.execution_account_mode ?? 'Unavailable'}`} />
        <DrawerField label="Risk policy" value={plan.plan?.risk_policy_id ? `${plan.plan.risk_policy_id} · rev ${plan.plan.risk_policy_revision ?? 'unavailable'}` : 'Unavailable'} mono />
      </DrawerSection>
      <DrawerSection label="Lineage / exact execution">
        <DrawerField label="Order" value={plan.lineage?.order ? `${plan.lineage.order.id} · ${humanize(plan.lineage.order.status)}` : 'Not submitted'} mono />
        <DrawerField label="Exchange order" value={plan.lineage?.order?.exchange_order_id ?? 'Not recorded'} mono />
        <DrawerField label="Fills" value={plan.lineage?.fills ? `${plan.lineage.fills.count} · quantity ${fmtNum(plan.lineage.fills.quantity)} · latest ${fmtTimestamp(plan.lineage.fills.latest_at)}` : 'Not submitted'} />
        <DrawerField label="Position" value={plan.lineage?.position ? `${plan.lineage.position.id} · ${humanize(plan.lineage.position.status)} · quantity ${fmtNum(plan.lineage.position.quantity)} @ ${fmtNum(plan.lineage.position.entry_price)}` : 'Not opened'} mono />
      </DrawerSection>
      <div className="flex gap-2 flex-wrap"><Button variant="secondary" onClick={onCandidate}>Open source Candidate</Button><Button onClick={onTrades}>Open exact execution</Button><Button variant="secondary" onClick={onCharts}>Open chart overlay</Button></div>
    </>}
  </DetailDrawer>
}

function CandidateDrawer({ candidateId, candidate, onClose }: { candidateId: string; candidate?: CandidateAttention; onClose: () => void }) {
  const detail = useQuery({
    queryKey: ['universe-candidate', candidateId],
    queryFn: () => api.getCandidate(candidateId),
  })
  const attention = candidate ?? detail.data?.attention
  return <DetailDrawer open title={`${candidate?.symbol ?? 'Candidate'} attention`} onClose={onClose}>
    {detail.isError ? <p>Candidate detail is unavailable.</p> : <>
      <DrawerSection label="Attention">
        <DrawerField label="Candidate ID" value={candidateId} mono />
        <DrawerField label="Started" value={fmtTimestamp(attention?.attention_started_at)} />
        <DrawerField label="Last requalified" value={fmtTimestamp(attention?.last_requalified_at)} />
        <DrawerField label="Soft deadline" value={fmtTimestamp(attention?.soft_expires_at)} />
        <DrawerField label="Hard deadline" value={fmtTimestamp(attention?.hard_expires_at)} />
        <DrawerField label="Planning" value={attention?.status ? humanize(attention.status) : '—'} />
        <DrawerField label="Terminal reason" value={attention?.attention_terminal_reason ? humanize(attention.attention_terminal_reason) : attention?.attention_status === 'active' ? 'Active' : '—'} />
      </DrawerSection>
      <DrawerSection label="Candidate evidence">
        <DrawerField label="Family / why selected" value={`${humanize(attention?.candidate_family)} · ${evidenceLabel(detail.data?.member.inclusion_reason)}`} />
        <DrawerField label="Rank" value={detail.data?.member.rank} />
        <DrawerField label="Score" value={detail.data?.member.total_score} />
        <DrawerField label="Liquidity / spread" value={`${detail.data?.member.liquidity_score ?? '—'} · ${detail.data?.member.spread_bps ?? '—'} bps`} />
        <DrawerField label="Execution eligible" value={detail.data?.member.execution_eligible == null ? 'Unavailable' : detail.data.member.execution_eligible ? 'Yes' : 'No'} />
        <DrawerField label="Risk flags" value={evidenceLabel(detail.data?.member.risk_flags)} />
        <DrawerField label="Candidate expiry" value={fmtTimestamp(detail.data?.member.expires_at)} />
      </DrawerSection>
    </>}
  </DetailDrawer>
}
