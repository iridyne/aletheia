import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { api, type LearningContributionProjection } from '../api/client'
import type { KnowledgeItem, StrategyRecord } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import Tabs from '../components/Tabs'
import PnLChart from '../charts/PnLChart'
import PageHeader from '../components/PageHeader'
import { Badge, Button, Card, CardHeader, Meter, StatusText } from '../components/ui'
import type { StatusTone } from '../components/ui'
import { fmtNum, fmtSigned, humanize, fmtTimestamp } from '../lib/format'
import { deriveContributionViewState } from '../lib/learningContribution'

/**
 * Learning — measured contribution first, then the memory & evolution surfaces.
 *
 * The page answers four human questions, one per tab:
 *   CONTRIBUTION — "Has Learning measurably improved a current experiment?"
 *   DREAMING  — "What did the AI just figure out?"  Each dreaming run is rendered as a
 *               cognition session (tools it called, how long it thought, what it produced),
 *               then this cycle's outputs grouped by kind. This narrative IS the signature.
 *   KNOWLEDGE — "What does it know?"  The §4 type taxonomy as sub-tabs, searchable library.
 *   REPORTS   — "Is it getting better?"  Portfolio PnL + a strategy-promotion board showing
 *               each paper strategy's distance to its next lifecycle gate.
 *
 * Honesty (CLAUDE.md): the §4 taxonomy is preserved — each Knowledge sub-tab queries its real
 * item_type, no laundering. Every empty/missing/file-not-found state is carried verbatim.
 * Enum values (confidence, severity, status) are humanized for display but never relabeled.
 */

export default function Learning() {
  return (
    <div>
      <PageHeader
        title="Learning"
        role="Measure first, then evolve. Contribution separates current experiment evidence from Learning activity, memory and portfolio performance."
      />
      <LearningPulse />
      <Tabs
        tabs={[
          { id: 'contribution', label: 'Measured contribution', render: () => <ContributionTab /> },
          { id: 'dreaming', label: 'Dreaming', render: () => <DreamingTab /> },
          { id: 'knowledge', label: 'Knowledge Base', render: () => <KnowledgeTab /> },
          { id: 'reports', label: 'Reports', render: () => <ReportsTab /> },
        ]}
      />
    </div>
  )
}

/* ============================ PULSE BAND ============================ */
// A quiet read-out strip — four live numbers that answer "is the system still learning,
// and how much?" at a glance. Deliberately NOT a hero state-word band (that's the Risk
// page's signature); here the four metrics are the point.
function LearningPulse() {
  const { data: summary } = useQuery({ queryKey: ['dreaming-summary'], queryFn: api.getDreamingSummary })
  const { data: strategies = [] } = useQuery({ queryKey: ['strategies'], queryFn: api.getStrategies })
  const { data: lessons = [] } = useQuery({ queryKey: ['lessons-all'], queryFn: () => api.getLessons(100) })

  const runs = (summary?.recent_dreaming_runs as any[]) || []
  const lastRun = runs[0]
  const new24h =
    (summary?.recent_research_notes?.length || 0) +
    (summary?.recent_strategy_hypotheses?.length || 0) +
    (summary?.recent_dreaming_logs?.length || 0) +
    ((summary as any)?.recent_daily_reviews?.length || 0)
  const active = (strategies as StrategyRecord[]).filter(s => s.state !== 'draft' && s.state !== 'retired')
  const lessonCount = Array.isArray(lessons) ? lessons.length : 0

  return (
    <section className="learn-pulse">
      <PulseStat
        label="Activity · last consolidation"
        value={lastRun ? fmtRelative(lastRun.started_at) : 'Never'}
        tone={runTone(lastRun?.status)}
      />
      <PulseStat label="Activity · new knowledge 24h" value={new24h} tone={new24h > 0 ? 'blue' : 'muted'} />
      <PulseStat label="Activity · strategies in paper" value={active.length} tone={active.length > 0 ? 'blue' : 'muted'} />
      <PulseStat label="Activity · lessons logged" value={lessonCount} tone={lessonCount > 0 ? 'yellow' : 'muted'} />
    </section>
  )
}

/* ======================= MEASURED CONTRIBUTION ======================= */
function ContributionTab() {
  const query = useQuery({
    queryKey: ['learning-contribution', 'lco-v1'],
    queryFn: api.getLearningContribution,
  })
  const viewState = deriveContributionViewState({
    loading: query.isLoading,
    failed: query.isError,
    data: query.data,
  })

  if (viewState === 'loading') {
    return <ContributionMessage tone="blue" title="Loading measured contribution" detail="Reading persisted experiment evidence. No evaluation or lifecycle action is being run." />
  }
  if (viewState === 'unavailable' && !query.data) {
    return <ContributionMessage tone="red" title="Contribution unavailable" detail="The contribution read failed. This is not an empty cohort or a zero result." />
  }

  const data = query.data
  if (!data || viewState === 'unavailable') {
    return <ContributionMessage tone="red" title="Contribution unavailable" detail={humanize(data?.unavailable_reason || 'projection unavailable')} />
  }
  if (viewState === 'empty') {
    return <ContributionMessage tone="muted" title="No open Learning experiment" detail="The projection succeeded, but there is no current experiment to measure." />
  }

  const item =
    data.items.find(candidate => candidate.path_id === 'universe_family_calibration') ??
    data.items[0]
  const freshness = data.data_freshness
  const measurement = item.measurement
  const integrity = item.integrity
  const lifecycle = item.lifecycle
  const evaluator = data.operations?.evaluator
  const required = Math.max(item.samples.required_per_arm, 1)
  const treatmentPct = item.samples.treatment_assignment_units / required * 100
  const controlPct = item.samples.control_assignment_units / required * 100
  const excluded = sumCounts(integrity.exclusion_reasons)
  const invalid = sumCounts(integrity.invalid_reasons)
  const partial = viewState === 'partial' || viewState === 'stale'

  return (
    <section className="lco-stack" aria-label="Learning measured contribution">
      {partial && (
        <ContributionMessage
          tone="yellow"
          title={freshness.state === 'stale' ? 'Measurement is stale' : 'Measurement is partial'}
          detail="The evidence below is preserved, but it must not be treated as a current effectiveness claim."
        />
      )}

      <Card>
        <CardHeader
          title={humanize(item.path_id)}
          meta={`${humanize(item.stage)} experiment · ${item.experiment_id}`}
          actions={<StatusText tone={measurementTone(measurement.state)}>{humanize(measurement.state)}</StatusText>}
        />
        <div className="lco-summary-grid">
          <ContributionFact label="Measured delta" value={fmtSigned(measurement.delta)} note="Backend verdict fact; not portfolio PnL" />
          <ContributionFact label="Treatment" value={`${item.samples.treatment_assignment_units} / ${item.samples.required_per_arm}`} note="Complete assignment units" />
          <ContributionFact label="Control" value={`${item.samples.control_assignment_units} / ${item.samples.required_per_arm}`} note="Complete assignment units" />
          <ContributionFact
            label="Current identity"
            value={item.current_identity.matches_running_cohort ? 'Matched' : 'Not matched'}
            note={humanize(item.current_identity.mismatch_reason || item.current_identity.policy_epoch || 'current')}
          />
        </div>
        <p className="lco-reason">
          <strong>Verdict reason:</strong> {humanize(measurement.reason || 'not available')}
        </p>
      </Card>

      <div className="lco-grid">
        <Card>
          <CardHeader title="Cohort maturity" meta="Treatment and control remain separate" />
          <CohortProgress label="Treatment" value={item.samples.treatment_assignment_units} required={item.samples.required_per_arm} pct={treatmentPct} />
          <CohortProgress label="Control" value={item.samples.control_assignment_units} required={item.samples.required_per_arm} pct={controlPct} />
          <div className="lco-facts">
            <ContributionRow label="Raw exposures" value={item.samples.raw_exposures} />
            <ContributionRow label="Complete assignment units" value={item.samples.complete_assignment_units} />
            <ContributionRow label="Cluster members" value={item.samples.cluster_members} />
            <ContributionRow label="Selected members" value={item.samples.selected_members} />
          </div>
        </Card>

        <Card>
          <CardHeader title="Evidence integrity" meta="No silent zero or conflict collapse" />
          <div className="lco-facts">
            <ContributionRow label="Excluded" value={excluded} />
            <ContributionRow label="Invalid" value={invalid} />
            <ContributionRow label="Proven conflicts" value={integrity.conflict_count} />
            <ContributionRow label="Matching mirror rows" value={integrity.matching_mirror_rows} />
            <ContributionRow label="Harm boundary" value={integrity.harm_breached ? 'Breached' : 'Not breached'} />
          </div>
          {(excluded > 0 || invalid > 0 || integrity.conflict_count > 0) && (
            <p className="lco-note">
              Exclusions: {formatCounts(integrity.exclusion_reasons)} · Invalid: {formatCounts(integrity.invalid_reasons)}
            </p>
          )}
        </Card>
      </div>

      <div className="lco-grid">
        <Card>
          <CardHeader title="Evaluator continuity" meta="Operational health is not effectiveness" />
          <div className="lco-facts">
            <ContributionRow label="Current status" value={humanize(evaluator?.current_status || 'not available')} />
            <ContributionRow label="Last success" value={fmtTimestamp(evaluator?.last_succeeded_at)} />
            <ContributionRow label="Next due" value={fmtTimestamp(evaluator?.next_due_at)} />
            <ContributionRow label="Backlog" value={evaluator?.backlog_count ?? '—'} />
            <ContributionRow label="Catch-up due" value={evaluator?.catchup_due == null ? '—' : evaluator.catchup_due ? 'Yes' : 'No'} />
            <ContributionRow label="Last failure" value={humanize(evaluator?.last_failure_code || 'none recorded')} />
          </div>
        </Card>

        <Card>
          <CardHeader title="Lifecycle boundary" meta="Recommendation only · no mutation authority" />
          <div className="lco-facts">
            <ContributionRow label="Recommendation" value={humanize(lifecycle.recommended_action)} />
            <ContributionRow label="Paper influence" value={data.operations?.config.paper_influence_enabled ? 'Enabled' : 'Disabled'} />
            <ContributionRow label="Authority" value={lifecycle.authority_enabled ? 'Enabled' : 'Read only'} />
            <ContributionRow label="Mutation available" value={lifecycle.mutation_available ? 'Yes' : 'No'} />
          </div>
          <p className="lco-note">
            {humanize(lifecycle.mutation_unavailable_reason)}. A recommendation cannot change allocation or admit a trial from this page.
          </p>
        </Card>
      </div>

      <p className="lco-footnote">
        Activity counts above show that Learning is producing artifacts. Only this identity-matched treatment/control measurement can claim contribution.
      </p>
    </section>
  )
}

function ContributionMessage({ tone, title, detail }: { tone: StatusTone; title: string; detail: string }) {
  return (
    <Card className="lco-message" role="status">
      <StatusText tone={tone}>{title}</StatusText>
      <p>{detail}</p>
    </Card>
  )
}

function ContributionFact({ label, value, note }: { label: string; value: ReactNode; note: string }) {
  return (
    <div className="lco-summary-fact">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  )
}

function ContributionRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="lco-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function CohortProgress({ label, value, required, pct }: { label: string; value: number; required: number; pct: number }) {
  return (
    <div className="lco-progress">
      <div><span>{label}</span><strong>{value} / {required}</strong></div>
      <div className="lco-progress-track"><Meter pct={pct} className="lco-progress-fill" /></div>
    </div>
  )
}

function measurementTone(state: LearningContributionProjection['items'][number]['measurement']['state']): StatusTone {
  if (state === 'positive') return 'green'
  if (state === 'negative' || state === 'invalid') return 'red'
  if (state === 'insufficient_data') return 'yellow'
  if (state === 'neutral') return 'blue'
  return 'muted'
}

function sumCounts(counts: Record<string, number>): number {
  return Object.values(counts).reduce((total, value) => total + value, 0)
}

function formatCounts(counts: Record<string, number>): string {
  const entries = Object.entries(counts)
  return entries.length === 0
    ? 'none'
    : entries.map(([reason, count]) => `${humanize(reason)} ${fmtNum(count)}`).join(', ')
}

/** Pulse-band read-out: label over a toned value. Uses the shared .stat CSS structure. */
function PulseStat({ label, value, tone }: { label: string; value: ReactNode; tone?: StatusTone }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value${tone ? ` stat-value-${tone}` : ''}`}>{value}</span>
    </div>
  )
}

/* ============================ DREAMING ============================ */
function DreamingTab() {
  const { data: summary } = useQuery({ queryKey: ['dreaming-summary'], queryFn: api.getDreamingSummary })
  const { data: allLogs = [] } = useQuery({ queryKey: ['dreaming'], queryFn: api.getDreamingLogs })
  const [selectedItem, setSelectedItem] = useState<any>(null)

  const runs = (summary?.recent_dreaming_runs as any[]) || []
  const research = (summary?.recent_research_notes as any[]) || []
  const hypotheses = (summary?.recent_strategy_hypotheses as any[]) || []
  const deprecated = (summary?.deprecated_memories as any[]) || []
  const logs = (summary?.recent_dreaming_logs?.length ? summary.recent_dreaming_logs : (allLogs as any[]).slice(0, 10)) as any[]

  return (
    <>
      {/* Signature — dreaming runs as cognition sessions */}
      <Card>
        <CardHeader title="Dreaming sessions" meta="What the agent consolidated · most recent first" />
        {runs.length === 0 ? (
          <p className="text-muted text-sm">No dreaming runs in the last 24 hours.</p>
        ) : (
          <div className="dream-sessions">
            {runs.map((r: any) => <DreamSession key={r.id} run={r} />)}
          </div>
        )}
      </Card>

      {/*
        This cycle's output, grouped by kind. Layout: asymmetric 2fr/1fr Bento split.
        Research notes can have 20+ items (~800px tall) while Consolidations/Deprecated
        often have 0-1 (~90px). A 1fr/1fr grid leaves ~500px of empty alignment gap
        beside the long column. The 2fr/1fr split + manual column grouping turns that
        gap into intentional visual hierarchy: the main outputs (Research / Hypotheses)
        are wide on the left; the meta/decay info (Consolidations / Deprecated) is
        narrow on the right.
      */}
      <div className="learn-grid asym">
        <div className="learn-col-main">
          <Card>
            <CardHeader title="Research notes" meta="24h" />
            <ItemList items={research} onSelect={setSelectedItem} empty="No new research notes in the last 24 hours." />
          </Card>
          <Card>
            <CardHeader title="Strategy hypotheses" meta="24h" />
            <ItemList items={hypotheses} onSelect={setSelectedItem} empty="No new strategy hypotheses in the last 24 hours." />
          </Card>
        </div>
        <div className="learn-col-aside">
          <Card>
            <CardHeader title="Consolidations" />
            <ItemList items={logs} onSelect={setSelectedItem} empty="No consolidations yet." />
          </Card>
          <Card>
            <CardHeader title="Deprecated memories" meta="7d" />
            <ItemList items={deprecated} onSelect={setSelectedItem} empty="No memories deprecated in the last 7 days." />
          </Card>
        </div>
      </div>

      <DetailDrawer open={!!selectedItem} onClose={() => setSelectedItem(null)} title={selectedItem?.title || 'Knowledge Item'}>
        {selectedItem && (
          <>
            <DrawerSection label="Details">
              <DrawerField label="Type" value={humanize(selectedItem.item_type)} />
              <DrawerField label="Confidence" value={<ConfidenceBadge value={selectedItem.confidence} />} />
              <DrawerField label="Status" value={<StatusBadge status={selectedItem.status || 'active'} />} />
              <DrawerField label="Created" value={fmtTimestamp(selectedItem.created_at)} mono />
              <DrawerField label="Strategy" value={selectedItem.strategy_id} mono />
            </DrawerSection>
            <DrawerSection label="Content">
              <MarkdownContent itemId={selectedItem.id} />
            </DrawerSection>
          </>
        )}
      </DetailDrawer>
    </>
  )
}

/** One dreaming run rendered as a cognition session: status · trigger · timing · tools · output. */
function DreamSession({ run }: { run: any }) {
  const tools = parseJsonArray(run.tools_called)
  const outputs = parseJsonArray(run.outputs_created)
  const errors = parseJsonArray(run.errors)
  const dur = fmtDuration(run.started_at, run.ended_at)

  return (
    <div className="dream-session">
      <div className="dream-session-head">
        <StatusText tone={runTone(run.status)}>{humanize(run.status)}</StatusText>
        <span className="dream-session-trigger">{humanize(run.trigger_type)}{run.model_provider ? ` · ${humanize(run.model_provider)}` : ''}</span>
        <span className="dream-session-time mono">{fmtTimestamp(run.started_at)}{dur ? ` · ${dur}` : ''}</span>
      </div>
      {tools.length > 0 && (
        <div className="dream-tools">
          {tools.map((t, i) => <span key={i} className="dream-chip mono">{t}</span>)}
        </div>
      )}
      <div className="dream-session-out">
        <span>{outputs.length} output{outputs.length === 1 ? '' : 's'} created</span>
        {errors.length > 0 && <span className="text-error"> · {errors.length} error{errors.length === 1 ? '' : 's'}</span>}
      </div>
    </div>
  )
}

/** Compact clickable list of knowledge items — title + timestamp (type is the group header). */
function ItemList({ items, onSelect, empty }: { items: any[]; onSelect: (item: any) => void; empty: string }) {
  if (!items || items.length === 0) return <p className="text-muted text-sm">{empty}</p>
  return (
    <ul className="learn-items">
      {items.map((item: any) => (
        <li key={item.id} className="learn-item clickable" onClick={() => onSelect(item)}>
          <span className="learn-item-title">{item.title || '(untitled)'}</span>
          <span className="learn-item-meta mono">{fmtTimestamp(item.created_at)}</span>
        </li>
      ))}
    </ul>
  )
}

/* ============================ KNOWLEDGE BASE ============================ */
// §4 taxonomy preserved: each sub-tab queries its real item_type (no laundering).
const KB_TABS = [
  { key: 'research', label: 'Research Notes', fn: () => api.getKnowledge() },
  { key: 'strategy', label: 'Strategy Memory', fn: () => api.getStrategyMemory() },
  { key: 'reviews', label: 'Daily Reviews', fn: () => api.getKnowledge('daily_review') },
  { key: 'dreaming', label: 'Dreaming Logs', fn: () => api.getDreamingLogs() },
  { key: 'lessons', label: 'Post-Trade Lessons', fn: () => api.getLessons(100) },
  { key: 'guardCandidates', label: 'Guard Candidates', fn: () => api.getGuardCandidates() },
  { key: 'guards', label: 'Active Guards', fn: () => api.getGuards() },
] as const

const kbCol = createColumnHelper<KnowledgeItem>()
const kbColumns = [
  kbCol.accessor('title', { header: 'Title' }),
  kbCol.accessor('confidence', { header: 'Confidence', cell: info => <ConfidenceBadge value={info.getValue()} /> }),
  kbCol.accessor('status', { header: 'Status', cell: info => <StatusBadge status={info.getValue()} /> }),
  kbCol.accessor('created_at', { header: 'Created', cell: info => <span className="mono">{fmtTimestamp(info.getValue())}</span> }),
]

// Lesson row shape (matches /api/persistence/lessons output).
type LessonRow = {
  lesson_id: string; source_type?: string; source_id?: string
  category?: string; severity?: string; summary?: string
  status?: string; created_at?: string
  activated_at?: string; dry_run_until?: string; expires_at?: string
}

// Guard row shape (matches /api/knowledge/guards output).
type GuardRow = {
  guard_id: string; title: string; description?: string
  status?: string; scope?: string; enforcement?: string; severity?: string
  score_penalty?: number; block_candidate?: boolean; tags?: string[]
}
const lessonCol = createColumnHelper<LessonRow>()
const lessonColumns = [
  lessonCol.accessor('summary', { header: 'Summary', cell: info => <span>{(info.getValue() || '').slice(0, 120)}{(info.getValue() || '').length > 120 ? '…' : ''}</span> }),
  lessonCol.accessor('category', { header: 'Category', cell: info => <Badge tone="blue">{humanize(info.getValue() || '—')}</Badge> }),
  // severity enum is warning/critical/info (NOT high/medium/low) — map by meaning, not literal.
  lessonCol.accessor('severity', { header: 'Severity', cell: info => <Badge tone={severityTone(info.getValue())}>{humanize(info.getValue() || '—')}</Badge> }),
  lessonCol.accessor('status', { header: 'Status', cell: info => <StatusBadge status={info.getValue() || ''} /> }),
  lessonCol.accessor('created_at', { header: 'Created', cell: info => <span className="mono">{fmtTimestamp(info.getValue())}</span> }),
]

const guardCol = createColumnHelper<GuardRow>()
const guardColumns = [
  guardCol.accessor('title', { header: 'Guard', cell: info => <span>{info.getValue()}</span> }),
  guardCol.accessor('severity', { header: 'Severity', cell: info => <Badge tone={severityTone(info.getValue())}>{humanize(info.getValue() || '—')}</Badge> }),
  guardCol.accessor('enforcement', { header: 'Mode', cell: info => <Badge tone={info.getValue() === 'blocking' ? 'red' : 'yellow'}>{humanize(info.getValue() || '—')}</Badge> }),
  guardCol.accessor('scope', { header: 'Scope', cell: info => <Badge tone="blue">{humanize(info.getValue() || '—')}</Badge> }),
  guardCol.accessor('score_penalty', { header: 'Penalty', cell: info => { const v = info.getValue(); return <span className="mono">{v ? `-${v}` : '—'}</span> } }),
]

function KnowledgeTab() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<typeof KB_TABS[number]['key']>('research')
  const [selected, setSelected] = useState<KnowledgeItem | null>(null)
  const [selectedLesson, setSelectedLesson] = useState<LessonRow | null>(null)
  const [selectedGuard, setSelectedGuard] = useState<GuardRow | null>(null)
  const active = KB_TABS.find(t => t.key === tab)!
  const { data = [] } = useQuery({ queryKey: ['knowledge', tab], queryFn: active.fn })
  const isLessons = tab === 'lessons'
  const isGuards = tab === 'guards'
  const isGuardCandidates = tab === 'guardCandidates'
  const isGuardRows = isGuards || isGuardCandidates
  const rows = isLessons ? (data as unknown as LessonRow[]) : isGuardRows ? (data as unknown as GuardRow[]) : (data as KnowledgeItem[])

  const { data: d0 } = useQuery({ queryKey: ['knowledge', KB_TABS[0].key], queryFn: KB_TABS[0].fn, staleTime: 60_000 })
  const { data: d1 } = useQuery({ queryKey: ['knowledge', KB_TABS[1].key], queryFn: KB_TABS[1].fn, staleTime: 60_000 })
  const { data: d2 } = useQuery({ queryKey: ['knowledge', KB_TABS[2].key], queryFn: KB_TABS[2].fn, staleTime: 60_000 })
  const { data: d3 } = useQuery({ queryKey: ['knowledge', KB_TABS[3].key], queryFn: KB_TABS[3].fn, staleTime: 60_000 })
  const { data: d4 } = useQuery({ queryKey: ['knowledge', KB_TABS[4].key], queryFn: KB_TABS[4].fn, staleTime: 60_000 })
  const { data: d5 } = useQuery({ queryKey: ['knowledge', KB_TABS[5].key], queryFn: KB_TABS[5].fn, staleTime: 60_000 })
  const { data: d6 } = useQuery({ queryKey: ['knowledge', KB_TABS[6].key], queryFn: KB_TABS[6].fn, staleTime: 60_000 })
  const counts: Record<string, number | undefined> = {
    [KB_TABS[0].key]: Array.isArray(d0) ? d0.length : undefined,
    [KB_TABS[1].key]: Array.isArray(d1) ? d1.length : undefined,
    [KB_TABS[2].key]: Array.isArray(d2) ? d2.length : undefined,
    [KB_TABS[3].key]: Array.isArray(d3) ? d3.length : undefined,
    [KB_TABS[4].key]: Array.isArray(d4) ? d4.length : undefined,
    [KB_TABS[5].key]: Array.isArray(d5) ? d5.length : undefined,
    [KB_TABS[6].key]: Array.isArray(d6) ? d6.length : undefined,
  }
  const lessonAction = useMutation({
    mutationFn: ({ lessonId, action }: { lessonId: string; action: 'accept' | 'reject' | 'archive' }) => {
      if (action === 'accept') return api.acceptLesson(lessonId)
      if (action === 'reject') return api.rejectLesson(lessonId)
      return api.archiveLesson(lessonId)
    },
    onSuccess: (updated: LessonRow) => {
      setSelectedLesson(updated)
      queryClient.invalidateQueries({ queryKey: ['knowledge'] })
      queryClient.invalidateQueries({ queryKey: ['lessons-all'] })
      queryClient.invalidateQueries({ queryKey: ['learning-funnel'] })
    },
  })
  const guardAction = useMutation({
    mutationFn: (guardId: string) => api.promoteGuardCandidate(guardId),
    onSuccess: (updated: GuardRow) => {
      setSelectedGuard(updated)
      queryClient.invalidateQueries({ queryKey: ['knowledge'] })
      queryClient.invalidateQueries({ queryKey: ['learning-funnel'] })
    },
  })

  return (
    <>
      <div className="kb-subtabs">
        {KB_TABS.map(t => (
          <Button key={t.key} variant={tab === t.key ? 'primary' : 'secondary'} size="sm" onClick={() => setTab(t.key)}>
            {t.label}{counts[t.key] != null && counts[t.key]! > 0 && <span className="tab-badge">{counts[t.key]}</span>}
          </Button>
        ))}
      </div>
      <Card>
        {isLessons ? (
          <DataTable data={rows as LessonRow[]} columns={lessonColumns} onRowClick={setSelectedLesson} emptyMessage="No lessons yet." />
        ) : isGuardRows ? (
          <DataTable
            data={rows as GuardRow[]}
            columns={guardColumns}
            onRowClick={setSelectedGuard}
            emptyMessage={isGuardCandidates ? 'No guard candidates awaiting review.' : 'No active guards yet.'}
          />
        ) : (
          <DataTable data={rows as KnowledgeItem[]} columns={kbColumns} onRowClick={setSelected} emptyMessage={`No ${active.label.toLowerCase()} yet.`} />
        )}
      </Card>

      <DetailDrawer open={!!selectedLesson} onClose={() => setSelectedLesson(null)} title="Post-Trade Lesson">
        {selectedLesson && (
          <>
            <DrawerSection label="Summary">
              <p className="text-sm text-pre-wrap">{selectedLesson.summary || '—'}</p>
            </DrawerSection>
            <DrawerSection label="Actions">
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={lessonAction.isPending || selectedLesson.status === 'accepted' || selectedLesson.status === 'applied'}
                  onClick={() => lessonAction.mutate({ lessonId: selectedLesson.lesson_id, action: 'accept' })}
                >
                  Accept
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={lessonAction.isPending || selectedLesson.status === 'rejected'}
                  onClick={() => lessonAction.mutate({ lessonId: selectedLesson.lesson_id, action: 'reject' })}
                >
                  Reject
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  disabled={lessonAction.isPending || selectedLesson.status === 'archived'}
                  onClick={() => lessonAction.mutate({ lessonId: selectedLesson.lesson_id, action: 'archive' })}
                >
                  Archive
                </Button>
              </div>
              {lessonAction.error && <p className="text-error text-sm">{lessonAction.error.message}</p>}
            </DrawerSection>
            <DrawerSection label="Metadata">
              <DrawerField label="Category" value={<Badge tone="blue">{humanize(selectedLesson.category || '—')}</Badge>} />
              <DrawerField label="Severity" value={<Badge tone={severityTone(selectedLesson.severity)}>{humanize(selectedLesson.severity || '—')}</Badge>} />
              <DrawerField label="Status" value={<StatusBadge status={selectedLesson.status || ''} />} />
              <DrawerField label="Source Type" value={humanize(selectedLesson.source_type || '—')} />
              <DrawerField label="Source ID" value={selectedLesson.source_id} mono />
              <DrawerField label="Created" value={fmtTimestamp(selectedLesson.created_at)} mono />
              {selectedLesson.activated_at && <DrawerField label="Activated" value={fmtTimestamp(selectedLesson.activated_at)} mono />}
              {selectedLesson.dry_run_until && <DrawerField label="Dry-run Until" value={fmtTimestamp(selectedLesson.dry_run_until)} mono />}
              {selectedLesson.expires_at && <DrawerField label="Expires" value={fmtTimestamp(selectedLesson.expires_at)} mono />}
            </DrawerSection>
          </>
        )}
      </DetailDrawer>

      <DetailDrawer open={!!selectedGuard} onClose={() => setSelectedGuard(null)} title={selectedGuard?.title || 'Guard'}>
        {selectedGuard && (
          <>
            <DrawerSection label="Description">
              <p className="text-sm text-pre-wrap">{selectedGuard.description || '—'}</p>
            </DrawerSection>
            {isGuardCandidates && (
              <DrawerSection label="Actions">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={guardAction.isPending}
                  onClick={() => guardAction.mutate(selectedGuard.guard_id)}
                >
                  Promote
                </Button>
                {guardAction.error && <p className="text-error text-sm">{guardAction.error.message}</p>}
              </DrawerSection>
            )}
            <DrawerSection label="Properties">
              <DrawerField label="Guard ID" value={selectedGuard.guard_id} mono />
              <DrawerField label="Status" value={<StatusBadge status={selectedGuard.status || ''} />} />
              <DrawerField label="Severity" value={<Badge tone={severityTone(selectedGuard.severity)}>{humanize(selectedGuard.severity || '—')}</Badge>} />
              <DrawerField label="Enforcement" value={<Badge tone={selectedGuard.enforcement === 'blocking' ? 'red' : 'yellow'}>{humanize(selectedGuard.enforcement || '—')}</Badge>} />
              <DrawerField label="Scope" value={<Badge tone="blue">{humanize(selectedGuard.scope || '—')}</Badge>} />
              <DrawerField label="Score Penalty" value={selectedGuard.score_penalty ? `-${selectedGuard.score_penalty}` : '—'} />
              <DrawerField label="Blocks Candidates" value={selectedGuard.block_candidate ? 'Yes' : 'No'} />
            </DrawerSection>
            {selectedGuard.tags && selectedGuard.tags.length > 0 && (
              <DrawerSection label="Tags">
                <div className="flex flex-wrap gap-1">{selectedGuard.tags.map(t => <Badge key={t} tone="blue">{t}</Badge>)}</div>
              </DrawerSection>
            )}
          </>
        )}
      </DetailDrawer>

      <DetailDrawer open={!!selected && !isLessons && !isGuardRows} onClose={() => setSelected(null)} title={selected?.title || 'Knowledge Item'}>
        {selected && (
          <>
            <DrawerSection label="Metadata">
              <DrawerField label="Type" value={humanize(selected.item_type)} />
              <DrawerField label="Confidence" value={<ConfidenceBadge value={selected.confidence} />} />
              <DrawerField label="Status" value={<StatusBadge status={selected.status} />} />
              <DrawerField label="Strategy ID" value={selected.strategy_id} mono />
              <DrawerField label="Created" value={fmtTimestamp(selected.created_at)} mono />
              <DrawerField label="Updated" value={fmtTimestamp(selected.updated_at)} mono />
            </DrawerSection>
            <DrawerSection label="Tags">
              {(() => {
                const tags = parseJsonArray(selected.tags)
                return tags.length > 0
                  ? <div className="flex flex-wrap gap-1">{tags.map(t => <Badge key={t} tone="blue">{t}</Badge>)}</div>
                  : <span className="text-muted text-sm">No tags</span>
              })()}
            </DrawerSection>
            <DrawerSection label="Content">
              <MarkdownContent itemId={selected.id} />
            </DrawerSection>
          </>
        )}
      </DetailDrawer>
    </>
  )
}

/* ============================ REPORTS ============================ */
function ReportsTab() {
  const { data: dreaming = [] } = useQuery({ queryKey: ['dreaming'], queryFn: api.getDreamingLogs })
  const { data: reviews = [] } = useQuery({ queryKey: ['reviews'], queryFn: () => api.getKnowledge('daily_review') })
  const { data: postTradeReviews = [] } = useQuery({ queryKey: ['post-trade-reviews'], queryFn: api.getPostTradeReviews })

  return (
    <>
      <Card>
        <CardHeader title="Portfolio PnL" />
        <PnLChart />
      </Card>

      <LearningFunnelPanel />

      <FilterControllerPersistentCard />

      <div className="learn-grid">
        <Card>
          <CardHeader title="Daily reviews" />
          <ReviewList items={reviews as any[]} empty="No daily reviews yet — generated by the dreaming cycle." />
        </Card>
        <Card>
          <CardHeader title="Post-trade reviews" />
          <ReviewList items={postTradeReviews as any[]} empty="No post-trade reviews yet." />
        </Card>
        <Card>
          <CardHeader title="Recent consolidations" />
          <ReviewList items={(dreaming as any[]).slice(0, 10)} empty="No consolidations yet." />
        </Card>
      </div>
    </>
  )
}

/**
 * Persistent filter controller signals — cells whose overrestrictive_suspect
 * verdict has held across N+ consecutive runs. This is an OBSERVATIONAL
 * diagnostic for human review, not a Learning A/B hypothesis. filter_controller
 * signals live in a different action space than any live learning path
 * (see PR #145 for the boundary reasoning). Any config change flagged here
 * must be applied through the normal change-control channel.
 */
function FilterControllerPersistentCard() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['filter-controller'],
    queryFn: api.getFilterController,
    staleTime: 60_000,
  })

  if (isLoading) {
    return (
      <Card>
        <CardHeader title="Filter controller — persistent signals" />
        <div className="skeleton"><div className="skeleton-bar" /><div className="skeleton-bar" /></div>
      </Card>
    )
  }
  if (isError || !data) {
    return (
      <Card>
        <CardHeader title="Filter controller — persistent signals" />
        <p className="text-muted text-sm">Signal source unavailable.</p>
      </Card>
    )
  }

  const persistent = data.persistent_recommendations || []
  const cfg = data.persistent_config || { min_consecutive_runs: 3, max_runs_lookback: 10 }
  const meta = `>= ${cfg.min_consecutive_runs} of last ${cfg.max_runs_lookback} runs`

  return (
    <Card>
      <CardHeader
        title="Filter controller — persistent signals"
        meta={meta}
      />
      <p className="text-muted text-sm mt-0 mb-2">
        Cells whose observational verdict has held across multiple consecutive
        controller runs. Human-review signal only — no live learning path
        consumes this. Any parameter change requires the normal change-control path.
      </p>
      {persistent.length === 0 ? (
        <p className="text-muted text-sm">
          No persistent overrestrictive_suspect signals — every cell in the recent
          runs held, was single-run only, or was insufficient_data.
        </p>
      ) : (
        <ul className="learn-items">
          {persistent.map(p => {
            const cellParts = [
              p.direction || '—',
              p.market_type || '—',
              p.horizon || '—',
            ].join(' · ')
            const signal = p.latest_error_signal
            const signalTone: StatusTone =
              signal == null ? 'muted' : Math.abs(signal) >= 0.15 ? 'yellow' : 'blue'
            return (
              <li key={p.latest_run_id + p.filter_name + p.scanner_family} className="learn-item">
                <div className="flex flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <span className="learn-item-title">
                      {p.filter_name} · {p.scanner_family}
                    </span>
                    <Badge tone="yellow">
                      streak {p.consecutive_suspect_runs}/{p.runs_analyzed}
                    </Badge>
                    {signal != null && (
                      <StatusText tone={signalTone}>
                        signal {signal >= 0 ? '+' : ''}{signal.toFixed(4)}
                      </StatusText>
                    )}
                  </div>
                  <span className="learn-item-meta mono text-xs">
                    {cellParts}
                    {p.filter_version ? ` · v=${p.filter_version}` : ''}
                    {' · last '}
                    {fmtTimestamp(p.last_suspect_at)}
                  </span>
                  {p.latest_reasoning && (
                    <span className="text-muted text-sm mt-0.5">
                      {p.latest_reasoning}
                    </span>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}

/** Compact title + timestamp list for periodic reviews (read-only). */
function ReviewList({ items, empty }: { items: any[]; empty: string }) {
  if (!items || items.length === 0) return <p className="text-muted text-sm">{empty}</p>
  return (
    <ul className="learn-items">
      {items.slice(0, 10).map((r: any) => (
        <li key={r.id} className="learn-item">
          <span className="learn-item-title">{r.title || '(untitled)'}</span>
          <span className="learn-item-meta mono">{fmtTimestamp(r.created_at)}</span>
        </li>
      ))}
    </ul>
  )
}

/* ============================ SHARED ============================ */
function ConfidenceBadge({ value }: { value: unknown }) {
  if (value == null || value === '') return <span className="text-faint">—</span>
  return <Badge tone={confidenceTone(value)}>{confidenceLabel(value)}</Badge>
}

// shared markdown content (read from /knowledge/{id}/content)
function MarkdownContent({ itemId }: { itemId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['knowledge-content', itemId],
    queryFn: () => api.getKnowledgeContent(itemId),
    enabled: !!itemId,
  })
  if (isLoading) return <div className="skeleton"><div className="skeleton-bar" /><div className="skeleton-bar" /><div className="skeleton-bar" /></div>
  if (!data || data.error) return <p className="text-muted text-sm">{data?.error === 'file_not_found' ? 'File not found on disk.' : 'No content available.'}</p>
  if (!data.content) return <p className="text-muted text-sm">Empty file.</p>
  return <div className="markdown-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(data.content) }} />
}

// Minimal markdown→HTML (escapes first, then a small subset).
function renderMarkdown(src: string): string {
  return src
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')
    .replace(/^---$/gm, '<hr/>')
    .replace(/\n{2,}/g, '</p><p>')
    .replace(/^(?!<[hluop])(.+)$/gm, '<p>$1</p>')
}

/* ---- small format/derivation helpers ---- */

/** Parse a JSON-array string column (tools_called, tags, outputs_created) defensively. */
function parseJsonArray(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw as string[]
  if (typeof raw !== 'string' || !raw) return []
  try { const v = JSON.parse(raw); return Array.isArray(v) ? v.map(String) : [] } catch { return [] }
}

/** UTC-safe parse (mirrors lib/format normalizeIso) → epoch ms, or NaN. */
function isoMs(iso?: string | null): number {
  if (!iso) return NaN
  const norm = iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`
  return new Date(norm).getTime()
}

/** "6h ago" relative time from an ISO timestamp. */
function fmtRelative(iso?: string | null): string {
  const t = isoMs(iso)
  if (!Number.isFinite(t)) return '—'
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000))
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60); if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

/** Elapsed time between two ISO stamps, e.g. "20s" / "3m 12s". Null if unknown. */
function fmtDuration(start?: string | null, end?: string | null): string | null {
  const a = isoMs(start); const b = isoMs(end)
  if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return null
  const s = Math.round((b - a) / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60); const r = s % 60
  return r ? `${m}m ${r}s` : `${m}m`
}

/** Format a value that may be a 0–1 ratio or an already-scaled percent. */
function pctMaybeRatio(v: number): string {
  const pct = Math.abs(v) <= 1 ? v * 100 : v
  return `${pct.toFixed(1)}%`
}

function runTone(status?: string): StatusTone {
  const v = String(status || '').toLowerCase()
  if (v === 'completed' || v === 'completed_empty') return 'green'
  if (v.includes('error') || v === 'failed') return 'red'
  if (v === 'running' || v === 'in_progress') return 'yellow'
  return 'muted'
}

function confidenceTone(c: unknown): StatusTone {
  const v = String(c ?? '').toLowerCase()
  if (v.includes('high')) return 'green'
  if (v.includes('medium') || v.includes('moderate')) return 'yellow'
  if (v.includes('low')) return 'muted'
  // numeric confidence (0–1): high if strong
  const n = Number(c)
  if (Number.isFinite(n)) return n >= 0.66 ? 'green' : n >= 0.33 ? 'yellow' : 'muted'
  return 'blue'
}

/** "high_confidence" → "High"; numeric 0–1 → "0.80". */
function confidenceLabel(c: unknown): string {
  if (typeof c === 'number') return fmtNum(c)
  const s = String(c).trim()
  const stripped = s.replace(/_?confidence$/i, '')
  return humanize(stripped || s)
}

function severityTone(s: unknown): StatusTone {
  const v = String(s ?? '').toLowerCase()
  if (v === 'critical' || v === 'high' || v === 'error') return 'red'
  if (v === 'warning' || v === 'medium' || v === 'warn') return 'yellow'
  if (v === 'info' || v === 'low') return 'blue'
  return 'muted'
}

const ITEM_TYPE_LABEL: Record<string, string> = {
  system_action: 'System Action',
  post_trade_review: 'Post-Trade Review',
  trade_case_study: 'Case Study',
  research_note: 'Research Note',
  guard_candidate: 'Guard Candidate',
  dreaming_log: 'Dreaming Log',
  strategy_memory: 'Strategy Memory',
  operator_assessment: 'Operator Assessment',
  daily_review: 'Daily Review',
  reasoning_feedback: 'Reasoning Feedback',
  trading_memo: 'Trading Memo',
  calibration_report: 'Calibration Report',
}

function LearningFunnelPanel() {
  const { data, isLoading } = useQuery({ queryKey: ['learning-funnel'], queryFn: api.getLearningFunnel, staleTime: 120000 })

  if (isLoading) return (
    <Card>
      <CardHeader title="Knowledge Funnel" />
      <div className="skeleton"><div className="skeleton-bar" /><div className="skeleton-bar" /></div>
    </Card>
  )
  if (!data) return null

  const totalItems = data.knowledge_items_by_type.reduce((s, x) => s + x.count, 0)
  const visibility = data.pi_actual_visibility
  const ps = data.ps_lessons
  const lessonApplied = ps.by_status.find(x => x.status === 'applied')?.count ?? 0
  const lessonProposed = ps.by_status.find(x => x.status === 'proposed')?.count ?? 0
  const contradictionCount = data.contradictions?.count ?? 0

  return (
    <Card>
      <CardHeader title="Knowledge Funnel" meta={`${totalItems.toLocaleString()} items`} />
      <div className="lf-meta-row">
        <span className="lf-meta-pill">System Operator lessons (case studies): <strong>{visibility.layer3_lesson_hits}</strong></span>
        <span className="lf-meta-pill">Guards available: <strong>{visibility.layer3_guards_available}</strong></span>
        <span className="lf-meta-pill">Lessons applied: <strong>{lessonApplied}</strong> / proposed: <strong>{lessonProposed}</strong></span>
        <span className="lf-meta-pill">Serenity active: <strong>{data.serenity_opportunities_active}</strong></span>
        <span className="lf-meta-pill">Contradictions needing review: <strong>{contradictionCount}</strong></span>
      </div>
      <div className="lf-bars">
        {data.knowledge_items_by_type.map(({ item_type, count }) => {
          const pct = totalItems > 0 ? (count / totalItems) * 100 : 0
          return (
            <div key={item_type} className="lf-bar-row">
              <span className="lf-bar-label">{ITEM_TYPE_LABEL[item_type] ?? humanize(item_type)}</span>
              <div className="lf-bar-track">
                <Meter pct={pct} className="lf-bar-fill" />
              </div>
              <span className="lf-bar-count mono">{count.toLocaleString()}</span>
            </div>
          )
        })}
      </div>
    </Card>
  )
}
