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
import { MeasuredPathSelector } from '../components/MeasuredPathSelector'
import { Badge, Button, Card, CardHeader, Meter, StatusText } from '../components/ui'
import type { StatusTone } from '../components/ui'
import { fmtNum, fmtSigned, humanize, fmtTimestamp } from '../lib/format'
import { guardActivationConfirmation, guardEffectText, type GuardRow } from '../lib/guards'
import { authorityLabel, contributionPartialCopy, deriveContributionViewState, measuredContributionPathIds } from '../lib/learningContribution'
import { PORTFOLIO_CONTEXT_LABEL, learningPulseValue, learningReadState, optionalBooleanLabel, type LearningReadState } from '../lib/learningWorkbench'

/**
 * Learning — measured contribution first, then the memory & evolution surfaces.
 *
 * The page answers four human questions, one per tab:
 *   CONTRIBUTION — "Has Learning measurably improved a current experiment?"
 *   DREAMING  — "What did the AI just figure out?"  Each dreaming run is rendered as a
 *               cognition session (tools it called, how long it thought, what it produced),
 *               then this cycle's outputs grouped by kind. This narrative IS the signature.
 *   KNOWLEDGE — "What does it know?"  The §4 type taxonomy as sub-tabs, searchable library.
 *   REPORTS   — "What context surrounds Learning?" Portfolio PnL is account context, while
 *               reviews and diagnostics remain activity rather than contribution evidence.
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
// Activity-only read-outs. They never claim measured contribution and failed reads stay unavailable.
export function LearningPulse() {
  const summaryQuery = useQuery({ queryKey: ['dreaming-summary'], queryFn: api.getDreamingSummary })
  const strategiesQuery = useQuery({ queryKey: ['strategies'], queryFn: api.getStrategies })
  const lessonsQuery = useQuery({ queryKey: ['lessons-all'], queryFn: () => api.getLessons(100) })
  const summaryState = learningReadState(summaryQuery.isLoading, summaryQuery.isError, summaryQuery.data != null)
  const strategiesState = learningReadState(strategiesQuery.isLoading, strategiesQuery.isError, strategiesQuery.data != null)
  const lessonsState = learningReadState(lessonsQuery.isLoading, lessonsQuery.isError, lessonsQuery.data != null)
  const summary = summaryQuery.data
  const strategies = strategiesQuery.data ?? []
  const lessons = lessonsQuery.data ?? []

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
        value={learningPulseValue(summaryState, lastRun ? fmtRelative(lastRun.started_at) : 'Never')}
        tone={summaryState === 'unavailable' ? 'red' : runTone(lastRun?.status)}
      />
      <PulseStat label="Activity · new knowledge 24h" value={learningPulseValue(summaryState, new24h)} tone={summaryState === 'unavailable' ? 'red' : new24h > 0 ? 'blue' : 'muted'} />
      <PulseStat label="Activity · strategies in paper" value={learningPulseValue(strategiesState, active.length)} tone={strategiesState === 'unavailable' ? 'red' : active.length > 0 ? 'blue' : 'muted'} />
      <PulseStat label="Activity · lessons logged" value={learningPulseValue(lessonsState, lessonCount)} tone={lessonsState === 'unavailable' ? 'red' : lessonCount > 0 ? 'yellow' : 'muted'} />
    </section>
  )
}

/* ======================= MEASURED CONTRIBUTION ======================= */
function ContributionTab() {
  const [selectedPath, setSelectedPath] = useState('')
  const query = useQuery({
    queryKey: ['learning-contribution', 'lco-v1'],
    queryFn: api.getLearningContribution,
    staleTime: 30_000,
    refetchInterval: 30_000,
  })
  if (query.isLoading) {
    return <ContributionMessage tone="blue" title="Loading measured contribution" detail="Reading persisted experiment evidence. No evaluation or lifecycle action is being run." />
  }
  if (query.isError && !query.data) {
    return <ContributionMessage tone="red" title="Contribution unavailable" detail="The contribution read failed. This is not an empty cohort or a zero result." />
  }

  const data = query.data
  if (!data || data.availability === 'unavailable') {
    return <ContributionMessage tone="red" title="Contribution unavailable" detail={humanize(data?.unavailable_reason || 'projection unavailable')} />
  }
  const pathIds = measuredContributionPathIds(data)
  if (pathIds.length === 0) {
    return <ContributionMessage tone="muted" title="No measurable Learning path" detail="The projection succeeded, but no path currently has a measurement adapter." />
  }
  const defaultPath = pathIds.includes('universe_family_calibration')
    ? 'universe_family_calibration'
    : pathIds[0]
  const activePath = pathIds.includes(selectedPath) ? selectedPath : defaultPath
  const activePathState = data.path_states.find(state => state.path_id === activePath)
  const item = data.items.find(candidate => candidate.path_id === activePath)
  const viewState = deriveContributionViewState({
    loading: false,
    failed: query.isError,
    data,
    pathId: activePath,
  })
  const pathSelector = (
    <MeasuredPathSelector
      pathIds={pathIds}
      activePath={activePath}
      generatedLabel={fmtTimestamp(data.generated_at)}
      onChange={setSelectedPath}
    />
  )
  if (viewState === 'unavailable') {
    return (
      <section className="lco-stack" aria-label="Learning measured contribution">
        {pathSelector}
        <ContributionMessage
          tone="red"
          title="Contribution unavailable for this path"
          detail={humanize(activePathState?.unavailable_reason || 'path projection unavailable')}
        />
      </section>
    )
  }
  if (!item) {
    const stale = viewState === 'stale'
    return (
      <section className="lco-stack" aria-label="Learning measured contribution">
        {pathSelector}
        {data.availability === 'partial' && (
          <ContributionMessage
            tone="yellow"
            title="Projection is partial"
            detail="The selected path has no current experiment, and one or more other projection sections are unavailable."
          />
        )}
        <ContributionMessage
          tone={stale ? 'yellow' : 'muted'}
          title={stale ? 'Measurement is stale' : 'No current experiment for this path'}
          detail={stale
            ? 'The last successful projection had no current experiment, and the latest refresh failed.'
            : 'This measurable path is available, but the projection returned no open experiment for it.'}
        />
      </section>
    )
  }
  const measurement = item.measurement
  const integrity = item.integrity
  const lifecycle = item.lifecycle
  const operations = data.operations
  const evaluator = operations?.evaluator
  const required = Math.max(item.samples.required_per_arm, 1)
  const treatmentPct = item.samples.treatment_assignment_units / required * 100
  const controlPct = item.samples.control_assignment_units / required * 100
  const excluded = sumCounts(integrity.exclusion_reasons)
  const invalid = sumCounts(integrity.invalid_reasons)
  const envelopePartial = data.availability === 'partial'
  const partial = envelopePartial || viewState === 'partial' || viewState === 'stale'
  const partialCopy = contributionPartialCopy({
    fetchFailed: query.isError,
    itemStale: item.data_freshness.state === 'stale',
    selectedPathPartial: item.availability === 'partial' || activePathState?.availability === 'partial',
    operationsFailed: data.unavailable_reason === 'operations_projection_failed',
  })

  return (
    <section className="lco-stack" aria-label="Learning measured contribution">
      {pathSelector}
      {partial && (
        <ContributionMessage
          tone="yellow"
          title={partialCopy.title}
          detail={partialCopy.detail}
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
            <ContributionRow label="Harm boundary" value={humanize(integrity.harm_state)} />
          </div>
          {(excluded > 0 || invalid > 0 || integrity.conflict_count > 0) && (
            <p className="lco-note">
              Exclusions: {formatCounts(integrity.exclusion_reasons)} · Invalid: {formatCounts(integrity.invalid_reasons)}
            </p>
          )}
          {integrity.harm_state === 'indeterminate' && (
            <p className="lco-note">Harm is not yet measurable: {humanize(integrity.harm_indeterminate_reason || 'insufficient evidence')}.</p>
          )}
          {integrity.evidence_conflicts.map((conflict) => (
            <p className="lco-note" key={`${conflict.reason}:${conflict.outcome_candidate_key}`}>
              Conflict {conflict.outcome_candidate_key}: {Object.keys(conflict.differing_fields).map((field) => humanize(field)).join(', ') || 'proven row disagreement'} · canonical {conflict.canonical ? `${conflict.canonical.source}#${conflict.canonical.row_id ?? 'unknown'}` : 'not designated'} · mirrors {conflict.mirrors?.map((row) => `${row.source}#${row.row_id ?? 'unknown'}`).join(', ') || 'not applicable'}{conflict.row_provenance_truncated ? ` · showing 20 of ${conflict.row_count} rows` : ''}
            </p>
          ))}
          {integrity.evidence_conflict_provenance_truncated && (
            <p className="lco-note">Conflict provenance is truncated; the conflict count remains complete.</p>
          )}
        </Card>
      </div>

      <div className="lco-grid">
        <Card>
          <CardHeader title="Evaluator continuity" meta="Operational health is not effectiveness" />
          {evaluator ? (
            <div className="lco-facts">
              <ContributionRow label="Current status" value={humanize(evaluator.current_status)} />
              <ContributionRow label="Last success" value={fmtTimestamp(evaluator.last_succeeded_at)} />
              <ContributionRow label="Next due" value={fmtTimestamp(evaluator.next_due_at)} />
              <ContributionRow label="Backlog" value={evaluator.backlog_count} />
              <ContributionRow label="Catch-up due" value={optionalBooleanLabel(evaluator.catchup_due)} />
              <ContributionRow label="Last failure" value={humanize(evaluator.last_failure_code || 'none recorded')} />
              <ContributionRow label="Audit mirror" value={humanize(operations?.lifecycle_audit?.current_status || 'unavailable')} />
            </div>
          ) : (
            <LearningReadMessage state="unavailable" subject="Evaluator continuity" />
          )}
        </Card>

        <Card>
          <CardHeader title="Lifecycle boundary" meta="Read-only inspection · treatment authority shown separately" />
          <div className="lco-facts">
            <ContributionRow label="Recommendation" value={humanize(lifecycle.recommended_action)} />
            <ContributionRow label="Paper influence" value={operations == null ? 'Unavailable' : operations.config.paper_influence_enabled ? 'Enabled' : 'Disabled'} />
            <ContributionRow label="Authority" value={authorityLabel(lifecycle.authority_enabled, lifecycle.authority_block_reason)} />
            <ContributionRow label="Authority reason" value={humanize(lifecycle.authority_block_reason || 'authority granted')} />
            <ContributionRow label="Mutation available" value={lifecycle.mutation_available ? 'Yes' : 'No'} />
          </div>
          <p className="lco-note">
            {lifecycle.mutation_available
              ? 'The protected owner admission route is ready. This observability page remains read only.'
              : `${humanize(lifecycle.mutation_unavailable_reason || 'admission unavailable')}. A recommendation cannot change allocation or admit a trial from this page.`}
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
export function DreamingTab() {
  const summaryQuery = useQuery({ queryKey: ['dreaming-summary'], queryFn: api.getDreamingSummary })
  const logsQuery = useQuery({ queryKey: ['dreaming'], queryFn: api.getDreamingLogs })
  const [selectedItem, setSelectedItem] = useState<any>(null)
  const summaryState = learningReadState(summaryQuery.isLoading, summaryQuery.isError, summaryQuery.data != null)
  const logsState = learningReadState(logsQuery.isLoading, logsQuery.isError, logsQuery.data != null)
  const summary = summaryQuery.data
  const allLogs = logsQuery.data ?? []

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
        {summaryState === 'loading' || summaryState === 'unavailable' ? (
          <LearningReadMessage state={summaryState} subject="Dreaming sessions" />
        ) : (
          <>
            {summaryState !== 'available' && <LearningReadMessage state={summaryState} subject="Dreaming sessions" />}
            {runs.length === 0 ? (
              <p className="text-muted text-sm">No dreaming runs in the last 24 hours.</p>
            ) : (
              <div className="dream-sessions">
                {runs.map((r: any) => <DreamSession key={r.id} run={r} />)}
              </div>
            )}
          </>
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
            <ItemList state={summaryState} items={research} onSelect={setSelectedItem} empty="No new research notes in the last 24 hours." />
          </Card>
          <Card>
            <CardHeader title="Strategy hypotheses" meta="24h" />
            <ItemList state={summaryState} items={hypotheses} onSelect={setSelectedItem} empty="No new strategy hypotheses in the last 24 hours." />
          </Card>
        </div>
        <div className="learn-col-aside">
          <Card>
            <CardHeader title="Consolidations" />
            <ItemList state={summary?.recent_dreaming_logs?.length ? summaryState : logsState} items={logs} onSelect={setSelectedItem} empty="No consolidations yet." />
          </Card>
          <Card>
            <CardHeader title="Deprecated memories" meta="7d" />
            <ItemList state={summaryState} items={deprecated} onSelect={setSelectedItem} empty="No memories deprecated in the last 7 days." />
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
function ItemList({ state = 'available', items, onSelect, empty }: { state?: LearningReadState; items: any[]; onSelect: (item: any) => void; empty: string }) {
  if (state === 'loading' || state === 'unavailable') return <LearningReadMessage state={state} subject="Items" />
  const warning = state !== 'available' ? <LearningReadMessage state={state} subject="Items" /> : null
  if (!items || items.length === 0) return <>{warning}<p className="text-muted text-sm">{empty}</p></>
  return (
    <>
      {warning}
      <ul className="learn-items">
        {items.map((item: any) => (
          <li key={item.id} className="learn-item clickable" onClick={() => onSelect(item)}>
            <span className="learn-item-title">{item.title || '(untitled)'}</span>
            <span className="learn-item-meta mono">{fmtTimestamp(item.created_at)}</span>
          </li>
        ))}
      </ul>
    </>
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
  guardCol.accessor('lifecycle_status', { header: 'State', cell: info => <StatusBadge status={info.getValue() || ''} /> }),
  guardCol.accessor('scope', { header: 'Scope', cell: info => <Badge tone="blue">{humanize(info.getValue() || '—')}</Badge> }),
  guardCol.accessor(row => row.actual_effect?.score_penalty, { id: 'baselinePenalty', header: 'Baseline Penalty', cell: info => { const v = info.getValue(); return <span className="mono">{v ? `-${v}` : '—'}</span> } }),
]

export function KnowledgeTab() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<typeof KB_TABS[number]['key']>('research')
  const [selected, setSelected] = useState<KnowledgeItem | null>(null)
  const [selectedLesson, setSelectedLesson] = useState<LessonRow | null>(null)
  const [selectedGuard, setSelectedGuard] = useState<GuardRow | null>(null)
  const active = KB_TABS.find(t => t.key === tab)!
  const activeQuery = useQuery({ queryKey: ['knowledge', tab], queryFn: active.fn })
  const data = activeQuery.data ?? []
  const activeState = learningReadState(activeQuery.isLoading, activeQuery.isError, activeQuery.data != null)
  const isLessons = tab === 'lessons'
  const isGuards = tab === 'guards'
  const isGuardCandidates = tab === 'guardCandidates'
  const isGuardRows = isGuards || isGuardCandidates
  const rows = isLessons ? (data as unknown as LessonRow[]) : isGuardRows ? (data as unknown as GuardRow[]) : (data as KnowledgeItem[])
  const tableState = activeState === 'available' && rows.length === 0 ? 'empty' : activeState

  const q0 = useQuery({ queryKey: ['knowledge', KB_TABS[0].key], queryFn: KB_TABS[0].fn, staleTime: 60_000 })
  const q1 = useQuery({ queryKey: ['knowledge', KB_TABS[1].key], queryFn: KB_TABS[1].fn, staleTime: 60_000 })
  const q2 = useQuery({ queryKey: ['knowledge', KB_TABS[2].key], queryFn: KB_TABS[2].fn, staleTime: 60_000 })
  const q3 = useQuery({ queryKey: ['knowledge', KB_TABS[3].key], queryFn: KB_TABS[3].fn, staleTime: 60_000 })
  const q4 = useQuery({ queryKey: ['knowledge', KB_TABS[4].key], queryFn: KB_TABS[4].fn, staleTime: 60_000 })
  const q5 = useQuery({ queryKey: ['knowledge', KB_TABS[5].key], queryFn: KB_TABS[5].fn, staleTime: 60_000 })
  const q6 = useQuery({ queryKey: ['knowledge', KB_TABS[6].key], queryFn: KB_TABS[6].fn, staleTime: 60_000 })
  const countQueries = [q0, q1, q2, q3, q4, q5, q6]
  const counts: Record<string, number | undefined> = {
    [KB_TABS[0].key]: Array.isArray(q0.data) ? q0.data.length : undefined,
    [KB_TABS[1].key]: Array.isArray(q1.data) ? q1.data.length : undefined,
    [KB_TABS[2].key]: Array.isArray(q2.data) ? q2.data.length : undefined,
    [KB_TABS[3].key]: Array.isArray(q3.data) ? q3.data.length : undefined,
    [KB_TABS[4].key]: Array.isArray(q4.data) ? q4.data.length : undefined,
    [KB_TABS[5].key]: Array.isArray(q5.data) ? q5.data.length : undefined,
    [KB_TABS[6].key]: Array.isArray(q6.data) ? q6.data.length : undefined,
  }
  const countStates = Object.fromEntries(KB_TABS.map((item, index) => {
    const query = countQueries[index]
    return [item.key, learningReadState(query.isLoading, query.isError, query.data != null)]
  })) as Record<typeof KB_TABS[number]['key'], LearningReadState>
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
            {t.label}
            {countStates[t.key] === 'loading' && <span className="tab-badge" aria-label={`${t.label} count loading`}>…</span>}
            {countStates[t.key] === 'unavailable' && <span className="tab-badge" aria-label={`${t.label} count unavailable`}>!</span>}
            {counts[t.key] != null && counts[t.key]! > 0 && <span className="tab-badge">{counts[t.key]}{countStates[t.key] === 'stale' ? '~' : ''}</span>}
          </Button>
        ))}
      </div>
      <Card>
        {isLessons ? (
          <DataTable state={tableState} data={rows as LessonRow[]} columns={lessonColumns} onRowClick={setSelectedLesson} emptyMessage="No lessons yet." />
        ) : isGuardRows ? (
          <DataTable
            state={tableState}
            data={rows as GuardRow[]}
            columns={guardColumns}
            onRowClick={setSelectedGuard}
            emptyMessage={isGuardCandidates ? 'No guard candidates awaiting review.' : 'No active guards yet.'}
          />
        ) : (
          <DataTable state={tableState} data={rows as KnowledgeItem[]} columns={kbColumns} onRowClick={setSelected} emptyMessage={`No ${active.label.toLowerCase()} yet.`} />
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
                  onClick={() => {
                    if (!confirm(guardActivationConfirmation(selectedGuard))) return
                    guardAction.mutate(selectedGuard.guard_id)
                  }}
                >
                  {selectedGuard.origin === 'learned' && selectedGuard.shadow_only ? 'Promote to Active Observation' : 'Activate Guard'}
                </Button>
                {guardAction.error && <p className="text-error text-sm">{guardAction.error.message}</p>}
              </DrawerSection>
            )}
            <DrawerSection label="Properties">
              <DrawerField label="Guard ID" value={selectedGuard.guard_id} mono />
              <DrawerField label="Lifecycle State" value={<StatusBadge status={selectedGuard.lifecycle_status || selectedGuard.status || ''} />} />
              <DrawerField label="Origin" value={humanize(selectedGuard.origin || '—')} />
              <DrawerField label="Severity" value={<Badge tone={severityTone(selectedGuard.severity)}>{humanize(selectedGuard.severity || '—')}</Badge>} />
              <DrawerField label="Stored Enforcement" value={humanize(selectedGuard.enforcement || '—')} />
              <DrawerField label="Shadow Only" value={selectedGuard.shadow_only ? 'Yes' : 'No'} />
              <DrawerField label="Authority" value={selectedGuard.authority_granted ? 'Granted at guard level' : humanize(selectedGuard.authority_scope || 'none')} />
              <DrawerField label="Ranker Evaluation" value={selectedGuard.evaluation_enabled ? 'Enabled' : 'Disabled'} />
              <DrawerField label="Proposed Effect" value={guardEffectText(selectedGuard.proposed_effect)} />
              <DrawerField label="Activation Effect" value={guardEffectText(selectedGuard.activation_effect)} />
              <DrawerField label="Current Baseline Effect" value={guardEffectText(selectedGuard.actual_effect)} />
              <DrawerField label="Experiment" value={selectedGuard.experiment_id || 'Not registered'} mono={!!selectedGuard.experiment_id} />
              <DrawerField label="Experiment Stage" value={humanize(selectedGuard.experiment_stage || '—')} />
              <DrawerField label="Experiment Status" value={humanize(selectedGuard.experiment_status || '—')} />
              <DrawerField label="Assignment" value={selectedGuard.assignment ? humanize(selectedGuard.assignment) : selectedGuard.authority_scope === 'unsupported' ? 'Not available; measurement unsupported' : 'Determined per trading candidate'} />
              <DrawerField label="Scope" value={<Badge tone="blue">{humanize(selectedGuard.scope || '—')}</Badge>} />
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
export function ReportsTab() {
  const dreamingQuery = useQuery({ queryKey: ['dreaming'], queryFn: api.getDreamingLogs })
  const reviewsQuery = useQuery({ queryKey: ['reviews'], queryFn: () => api.getKnowledge('daily_review') })
  const postTradeReviewsQuery = useQuery({ queryKey: ['post-trade-reviews'], queryFn: api.getPostTradeReviews })
  const dreaming = dreamingQuery.data ?? []
  const reviews = reviewsQuery.data ?? []
  const postTradeReviews = postTradeReviewsQuery.data ?? []

  return (
    <>
      <Card>
        <CardHeader title="Portfolio PnL" meta={PORTFOLIO_CONTEXT_LABEL} />
        <PnLChart />
      </Card>

      <LearningFunnelPanel />

      <FilterControllerPersistentCard />

      <div className="learn-grid">
        <Card>
          <CardHeader title="Daily reviews" />
          <ReviewList state={learningReadState(reviewsQuery.isLoading, reviewsQuery.isError, reviewsQuery.data != null)} items={reviews as any[]} empty="No daily reviews yet — generated by the dreaming cycle." />
        </Card>
        <Card>
          <CardHeader title="Post-trade reviews" />
          <ReviewList state={learningReadState(postTradeReviewsQuery.isLoading, postTradeReviewsQuery.isError, postTradeReviewsQuery.data != null)} items={postTradeReviews as any[]} empty="No post-trade reviews yet." />
        </Card>
        <Card>
          <CardHeader title="Recent consolidations" />
          <ReviewList state={learningReadState(dreamingQuery.isLoading, dreamingQuery.isError, dreamingQuery.data != null)} items={(dreaming as any[]).slice(0, 10)} empty="No consolidations yet." />
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
  if (!data) {
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
      {isError && <LearningReadMessage state="stale" subject="Filter controller signals" />}
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
function ReviewList({ state = 'available', items, empty }: { state?: LearningReadState; items: any[]; empty: string }) {
  if (state === 'loading' || state === 'unavailable') return <LearningReadMessage state={state} subject="Reviews" />
  const warning = state !== 'available' ? <LearningReadMessage state={state} subject="Reviews" /> : null
  if (!items || items.length === 0) return <>{warning}<p className="text-muted text-sm">{empty}</p></>
  return (
    <>
      {warning}
      <ul className="learn-items">
        {items.slice(0, 10).map((r: any) => (
          <li key={r.id} className="learn-item">
            <span className="learn-item-title">{r.title || '(untitled)'}</span>
            <span className="learn-item-meta mono">{fmtTimestamp(r.created_at)}</span>
          </li>
        ))}
      </ul>
    </>
  )
}

/* ============================ SHARED ============================ */
function ConfidenceBadge({ value }: { value: unknown }) {
  if (value == null || value === '') return <span className="text-faint">—</span>
  return <Badge tone={confidenceTone(value)}>{confidenceLabel(value)}</Badge>
}

// shared markdown content (read from /knowledge/{id}/content)
export function MarkdownContent({ itemId }: { itemId: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['knowledge-content', itemId],
    queryFn: () => api.getKnowledgeContent(itemId),
    enabled: !!itemId,
  })
  if (isLoading) return <div className="skeleton"><div className="skeleton-bar" /><div className="skeleton-bar" /><div className="skeleton-bar" /></div>
  if (isError && !data) return <LearningReadMessage state="unavailable" subject="Knowledge content" />
  const warning = isError ? <LearningReadMessage state="stale" subject="Knowledge content" /> : null
  if (!data || data.error) return <>{warning}<p className="text-muted text-sm">{data?.error === 'file_not_found' ? 'File not found on disk.' : 'No content available.'}</p></>
  if (!data.content) return <>{warning}<p className="text-muted text-sm">Empty file.</p></>
  return <>
    {warning}
    <div className="markdown-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(data.content) }} />
  </>
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

function LearningReadMessage({ state, subject }: { state: Exclude<LearningReadState, 'available'>; subject: string }) {
  if (state === 'loading') return <p className="text-muted text-sm">Loading {subject.toLowerCase()}...</p>
  if (state === 'stale') return <p role="status" className="text-warning text-sm">{subject} is stale. Retained data may not reflect the latest read.</p>
  if (state === 'partial') return <p role="status" className="text-warning text-sm">{subject} is partial. Available sections remain visible.</p>
  return <p role="alert" className="text-error text-sm">{subject} unavailable. The read failed; this is not an empty result.</p>
}

export function LearningFunnelPanel() {
  const { data, isLoading, isError } = useQuery({ queryKey: ['learning-funnel'], queryFn: api.getLearningFunnel, staleTime: 120000 })

  if (isLoading) return (
    <Card>
      <CardHeader title="Knowledge Funnel" />
      <div className="skeleton"><div className="skeleton-bar" /><div className="skeleton-bar" /></div>
    </Card>
  )
  if ((isError && !data) || !data) return (
    <Card>
      <CardHeader title="Knowledge Funnel" />
      <LearningReadMessage state="unavailable" subject="Knowledge funnel" />
    </Card>
  )

  const totalItems = data.knowledge_items_by_type.reduce((s, x) => s + x.count, 0)
  const visibility = data.pi_actual_visibility
  const ps = data.ps_lessons
  const lessonApplied = ps.availability === 'available' ? ps.by_status?.find(x => x.status === 'applied')?.count ?? 0 : null
  const lessonProposed = ps.availability === 'available' ? ps.by_status?.find(x => x.status === 'proposed')?.count ?? 0 : null
  const contradictionCount = data.contradictions?.count
  const funnelState = learningReadState(false, isError, true, Object.keys(data.query_errors).length > 0)

  return (
    <Card>
      <CardHeader title="Knowledge Funnel" meta={`${totalItems.toLocaleString()} items`} />
      {funnelState !== 'available' && <LearningReadMessage state={funnelState} subject="Knowledge funnel" />}
      {Object.keys(data.query_errors).length > 0 && (
        <p className="text-warning text-sm">Unavailable sections: {Object.keys(data.query_errors).map(key => humanize(key)).join(', ')}.</p>
      )}
      <div className="lf-meta-row">
        <span className="lf-meta-pill">System Operator lessons (case studies): <strong>{visibility.layer3_lesson_hits}</strong></span>
        <span className="lf-meta-pill">Guards available: <strong>{visibility.layer3_guards_available}</strong></span>
        <span className="lf-meta-pill">Lessons applied: <strong>{lessonApplied ?? 'Unavailable'}</strong> / proposed: <strong>{lessonProposed ?? 'Unavailable'}</strong></span>
        <span className="lf-meta-pill">Serenity active: <strong>{data.serenity_opportunities_active ?? 'Unavailable'}</strong></span>
        <span className="lf-meta-pill">Contradictions needing review: <strong>{data.contradictions.checked ? contradictionCount ?? 0 : 'Unavailable'}</strong></span>
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
