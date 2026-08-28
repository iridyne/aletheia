import { useState } from 'react'
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
import { fmtSymbol, fmtTimestamp, humanize } from '../lib/format'

const planCol = createColumnHelper<EntryPlan>()
const candidateCol = createColumnHelper<CandidateAttention>()
const historyCol = createColumnHelper<PlanningHistoryResponse['items'][number]>()

// Calibration compatibility: by_cohort_horizon rows remain isolated by
// row.direction and row.market_type across the owner-facing projection.
const planColumns = [
  planCol.accessor('symbol', {
    header: 'Entry Plan',
    cell: info => <strong className="mono">{fmtSymbol(info.getValue(), info.row.original.market_type)}</strong>,
  }),
  planCol.accessor('direction', { header: 'Side', cell: info => humanize(info.getValue()) }),
  planCol.accessor('execution_mode', { header: 'Execution', cell: info => humanize(info.getValue()) }),
  planCol.accessor('status', { header: 'Status', cell: info => <Badge tone="green">{humanize(info.getValue())}</Badge> }),
  planCol.accessor(row => row.plan?.entry_zone, {
    id: 'entry_zone',
    header: 'Entry zone',
    cell: info => info.getValue()?.map(value => value.toLocaleString()).join(' – ') ?? '—',
  }),
  planCol.accessor(row => row.plan?.expires_at, { id: 'expires_at', header: 'Plan expiry', cell: info => fmtTimestamp(info.getValue()) }),
]

const candidateColumns = [
  candidateCol.accessor('symbol', {
    header: 'Candidate',
    cell: info => <strong className="mono">{fmtSymbol(info.getValue(), info.row.original.market_type)}</strong>,
  }),
  candidateCol.accessor('candidate_family', { header: 'Family', cell: info => humanize(info.getValue()) }),
  candidateCol.accessor('status', { header: 'Planning', cell: info => humanize(info.getValue()) }),
  candidateCol.accessor('attention_started_at', { header: 'Attention started', cell: info => fmtTimestamp(info.getValue()) }),
  candidateCol.accessor('last_requalified_at', { header: 'Last requalified', cell: info => fmtTimestamp(info.getValue()) }),
  candidateCol.accessor('soft_expires_at', { header: 'Soft deadline', cell: info => fmtTimestamp(info.getValue()) }),
  candidateCol.accessor('hard_expires_at', { header: 'Hard deadline', cell: info => fmtTimestamp(info.getValue()) }),
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
  const summary = data?.summary
  const plans = data?.entry_plans
  const candidates = data?.candidate_attention
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
      : universeStatus === 'no_active_snapshot'
        ? 'No active snapshot'
        : 'Not available'

  const tabs = [
    {
      id: 'plans',
      label: 'Entry Plans',
      badge: plans?.length,
      render: () => <ReadSection available={data?.availability.entry_plans?.available} loading={workbench.isPending} label="Entry Plans">
        <DataTable columns={planColumns} data={plans ?? []} onRowClick={openPlan} emptyMessage="No active Entry Plans." tableId="entry-workbench-plans" />
      </ReadSection>,
    },
    {
      id: 'candidates',
      label: 'Candidates',
      badge: candidates?.length,
      render: () => <ReadSection available={data?.availability.candidate_attention?.available} loading={workbench.isPending} label="Candidate attention">
        <DataTable columns={candidateColumns} data={candidates ?? []} onRowClick={openCandidate} emptyMessage="No active Candidate attention." tableId="entry-workbench-candidates" />
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
      <DrawerSection label="Identity">
        <DrawerField label="Plan ID" value={plan.id} mono />
        <DrawerField label="Candidate ID" value={plan.candidate_id} mono />
        <DrawerField label="Contract" value={plan.contract_valid ? 'Validated' : 'Invalid'} />
        <DrawerField label="Status" value={humanize(plan.status)} />
      </DrawerSection>
      <DrawerSection label="Immutable plan geometry">
        <DrawerField label="Thesis" value={plan.plan?.thesis} />
        <DrawerField label="Scenario" value={plan.plan?.scenario} />
        <DrawerField label="Entry zone" value={plan.plan?.entry_zone?.join(' – ')} mono />
        <DrawerField label="Invalidation" value={plan.plan?.invalidation_price} mono />
        <DrawerField label="Stop" value={plan.plan?.stop_price} mono />
        <DrawerField label="Target" value={plan.plan?.target_price} mono />
        <DrawerField label="Expiry" value={fmtTimestamp(plan.plan?.expires_at)} />
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
        <DrawerField label="Rank" value={detail.data?.member.rank} />
        <DrawerField label="Score" value={detail.data?.member.total_score} />
        <DrawerField label="Candidate expiry" value={fmtTimestamp(detail.data?.member.expires_at)} />
      </DrawerSection>
    </>}
  </DetailDrawer>
}
