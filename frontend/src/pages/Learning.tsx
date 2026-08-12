import { useDeferredValue, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { api, type DreamingSummary, type KnowledgeCatalogResponse, type LearningContributionProjection } from '../api/client'
import type { KnowledgeItem } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import Tabs from '../components/Tabs'
import PageHeader from '../components/PageHeader'
import { MeasuredPathSelector } from '../components/MeasuredPathSelector'
import { Badge, Button, Card, CardHeader, ListRowButton, Meter, StatusText } from '../components/ui'
import type { StatusTone } from '../components/ui'
import { fmtNum, fmtSigned, humanize, fmtTimestamp } from '../lib/format'
import { guardActivationConfirmation, guardEffectText, type GuardRow } from '../lib/guards'
import { authorityLabel, contributionPartialCopy, deriveContributionViewState, measuredContributionPathIds } from '../lib/learningContribution'
import { learningPulseValue, learningReadState, optionalBooleanLabel, type LearningReadState } from '../lib/learningWorkbench'

/**
 * Learning — measured contribution first, then the memory & evolution surfaces.
 *
 * Overview answers status, change, impact and attention first. Impact, Runs, Library and
 * Operations provide the evidence and controls behind those answers.
 *
 * Honesty (CLAUDE.md): the §4 taxonomy is preserved — each Knowledge sub-tab queries its real
 * item_type, no laundering. Every empty/missing/file-not-found state is carried verbatim.
 * Enum values (confidence, severity, status) are humanized for display but never relabeled.
 */

export default function Learning() {
  const [tab, setTab] = useState('overview')
  return (
    <div>
      <PageHeader
        title="Learning"
        role="See what changed, whether it helped, and what needs review."
      />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'overview', label: 'Overview', render: () => <LearningOverview onNavigate={setTab} /> },
          { id: 'contribution', label: 'Impact', render: () => <ContributionTab /> },
          { id: 'dreaming', label: 'Runs', render: () => <DreamingTab /> },
          { id: 'knowledge', label: 'Library', render: () => <KnowledgeTab /> },
          { id: 'operations', label: 'Operations', render: () => <ReportsTab /> },
        ]}
      />
    </div>
  )
}

export function LearningOverview({ onNavigate }: { onNavigate: (tab: string) => void }) {
  const [selectedKnowledge, setSelectedKnowledge] = useState<KnowledgeItem | null>(null)
  const summaryQuery = useQuery({ queryKey: ['dreaming-summary'], queryFn: api.getDreamingSummary })
  const contributionQuery = useQuery({ queryKey: ['learning-contribution', 'lco-v1'], queryFn: api.getLearningContribution, staleTime: 30_000 })
  const funnelQuery = useQuery({ queryKey: ['learning-funnel'], queryFn: api.getLearningFunnel, staleTime: 120_000 })
  const controllerQuery = useQuery({ queryKey: ['filter-controller'], queryFn: api.getFilterController, staleTime: 60_000 })
  const summary = summaryQuery.data
  const experiment = contributionQuery.data?.items?.[0]
  const proposedLessons = funnelQuery.data
    ? funnelQuery.data.ps_lessons.availability === 'available'
      ? funnelQuery.data.ps_lessons.by_status?.find(item => item.status === 'proposed')?.count ?? 0
      : null
    : null
  const guardCandidates = funnelQuery.data
    ? funnelQuery.data.knowledge_items_by_type.find(item => item.item_type === 'guard_candidate')?.count ?? 0
    : null
  const contradictions = funnelQuery.data?.contradictions.checked ? funnelQuery.data.contradictions.count ?? 0 : null
  const controllerSignals = controllerQuery.data ? controllerQuery.data.persistent_recommendations.length : null
  const attentionValues = [proposedLessons, guardCandidates, contradictions, controllerSignals]
  const knownAttentionTotal = attentionValues.reduce<number>((total, value) => total + (value ?? 0), 0)
  const attentionComplete = attentionValues.every(value => value != null)
  const recent = summary?.recent_knowledge_items.slice(0, 5) ?? []
  const job = summary?.dreaming_job
  const lastRun = summary?.recent_dreaming_runs[0]
  const runState = learningReadState(summaryQuery.isLoading, summaryQuery.isError, summary != null)
  const impactState = learningReadState(contributionQuery.isLoading, contributionQuery.isError, contributionQuery.data != null)
  const changesMeta = runState === 'loading' ? 'Loading'
    : runState === 'unavailable' ? 'Unavailable'
    : `${summary?.recent_knowledge_total ?? 0} created in 24h; ${summary?.deprecated_memories.length ?? 0} deprecated in 7d${runState === 'stale' ? '; stale' : ''}`
  const attentionMeta = attentionComplete
    ? knownAttentionTotal ? `${knownAttentionTotal.toLocaleString()} open items` : 'No open items'
    : `${knownAttentionTotal.toLocaleString()} known open items; some counts unavailable`

  return (
    <section className="learning-home" aria-label="Learning overview">
      <div className="learn-home-status">
        <ContributionFact label="Learning job" value={learningPulseValue(runState, humanize(job?.status || lastRun?.status || 'not recorded'))} note={`Last success ${fmtTimestamp(job?.last_succeeded_at || lastRun?.ended_at)}${job?.next_due_at ? `; next ${fmtTimestamp(job.next_due_at)}` : ''}`} />
        <ContributionFact label="New knowledge" value={learningPulseValue(runState, summary?.recent_knowledge_total ?? 0)} note="Created in the last 24 hours" />
        <ContributionFact label="Impact verdict" value={learningPulseValue(impactState, experiment ? humanize(experiment.measurement.state) : 'Not measured')} note={humanize(experiment?.measurement.reason || 'No current experiment')} />
        <ContributionFact label="Needs review" value={attentionComplete ? knownAttentionTotal : 'Partial'} note={`${proposedLessons ?? 'unknown'} lessons; ${guardCandidates ?? 'unknown'} guard candidates; ${contradictions ?? 'unknown'} contradictions`} />
      </div>

      <div className="learning-home-grid">
        <Card>
          <CardHeader title="What changed" meta={changesMeta} actions={<Button size="sm" variant="secondary" onClick={() => onNavigate('knowledge')}>Open library</Button>} />
          <ItemList state={runState} items={recent} onSelect={setSelectedKnowledge} empty="No knowledge was created in the last 24 hours." />
          {summary?.recent_knowledge_truncated && <p className="text-muted text-sm">Showing 5 recent items from {summary.recent_knowledge_total.toLocaleString()} created in the last 24 hours.</p>}
        </Card>

        <Card>
          <CardHeader title="Measured impact" actions={<Button size="sm" variant="secondary" onClick={() => onNavigate('contribution')}>View evidence</Button>} />
          {experiment ? <ImpactSummary item={experiment} /> : impactState === 'available'
            ? <p className="text-muted text-sm">No current measured experiment.</p>
            : <LearningReadMessage state={impactState} subject="Measured impact" />}
        </Card>
      </div>

      <Card>
        <CardHeader title="Needs attention" meta={attentionMeta} actions={<Button size="sm" variant="secondary" onClick={() => onNavigate('operations')}>Open operations</Button>} />
        <div className="learning-attention-grid">
          <AttentionItem label="Proposed lessons" value={proposedLessons} detail="Review before application" onClick={() => onNavigate('knowledge')} />
          <AttentionItem label="Guard candidates" value={guardCandidates} detail="Human review required" onClick={() => onNavigate('knowledge')} />
          <AttentionItem label="Contradictions" value={contradictions} detail="Resolve conflicting knowledge" onClick={() => onNavigate('operations')} />
          <AttentionItem label="Persistent filter signals" value={controllerSignals} detail="Review only; no automatic change" onClick={() => onNavigate('operations')} />
        </div>
      </Card>
      <DetailDrawer open={!!selectedKnowledge} onClose={() => setSelectedKnowledge(null)} title={selectedKnowledge?.title || 'Knowledge Item'}>
        {selectedKnowledge && <>
          <DrawerSection label="Details">
            <DrawerField label="Type" value={knowledgeTypeLabel(selectedKnowledge.item_type)} />
            <DrawerField label="Status" value={<StatusBadge status={selectedKnowledge.status || 'active'} />} />
            <DrawerField label="Produced by run" value={selectedKnowledge.producer_run_id || 'Not recorded'} mono />
            <DrawerField label="Created" value={fmtTimestamp(selectedKnowledge.created_at)} mono />
          </DrawerSection>
          <DrawerSection label="Content"><MarkdownContent itemId={selectedKnowledge.id} /></DrawerSection>
        </>}
      </DetailDrawer>
    </section>
  )
}

function ImpactSummary({ item }: { item: LearningContributionProjection['items'][number] }) {
  const remaining = Math.max(0, item.samples.required_per_arm - item.samples.treatment_assignment_units)
  return <div className="lco-facts">
    <ContributionRow label="Verdict" value={humanize(item.measurement.state)} />
    <ContributionRow label="Next action" value={humanize(item.lifecycle.recommended_action)} />
    <ContributionRow label="Observed change" value={fmtSigned(item.measurement.delta)} />
    <ContributionRow label="Previous verdict" value={humanize(item.latest_historical_measurement?.state || 'none recorded')} />
    <ContributionRow label="Evidence needed" value={remaining ? `${remaining} more treatment units` : 'Sample threshold met'} />
    <ContributionRow label="Last evaluated" value={fmtTimestamp(item.measurement.evaluated_at)} />
  </div>
}

function AttentionItem({ label, value, detail, onClick }: { label: string; value: number | null; detail: string; onClick: () => void }) {
  return <Button type="button" className="learning-attention-item" onClick={onClick}>
    <strong>{value == null ? 'Unavailable' : value.toLocaleString()}</strong>
    <span>{label}</span>
    <small>{detail}</small>
  </Button>
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
    return <ContributionMessage tone="blue" title="Loading measured contribution" detail="Reading saved experiment evidence." />
  }
  if (query.isError && !query.data) {
    return <ContributionMessage tone="red" title="Contribution unavailable" detail="Experiment evidence could not be read." />
  }

  const data = query.data
  if (!data || data.availability === 'unavailable') {
    return <ContributionMessage tone="red" title="Contribution unavailable" detail={humanize(data?.unavailable_reason || 'projection unavailable')} />
  }
  const pathIds = measuredContributionPathIds(data)
  if (pathIds.length === 0) {
    return <ContributionMessage tone="muted" title="No measurable Learning path" detail="No current Learning path has a measurement adapter." />
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
  const treatmentRemaining = Math.max(0, item.samples.required_per_arm - item.samples.treatment_assignment_units)
  const controlRemaining = Math.max(0, item.samples.required_per_arm - item.samples.control_assignment_units)
  const coverage = measurement.coverage
  const daysRemaining = coverage?.required_consecutive_days == null || coverage.current_consecutive_market_days == null
    ? null
    : Math.max(0, coverage.required_consecutive_days - coverage.current_consecutive_market_days)
  const evidenceGap = [
    treatmentRemaining ? `Treatment needs ${treatmentRemaining} more complete units` : null,
    controlRemaining ? `Control needs ${controlRemaining} more complete units` : null,
    daysRemaining ? `Continuity needs ${daysRemaining} more market days` : null,
  ].filter(Boolean).join('; ') || 'Sample and continuity gates are met'
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
          meta={`${humanize(item.stage)} experiment, ${item.experiment_id}`}
          actions={<StatusText tone={measurementTone(measurement.state)}>{humanize(measurement.state)}</StatusText>}
        />
        <div className="lco-summary-grid">
          <ContributionFact label="Verdict" value={humanize(measurement.state)} note={humanize(measurement.reason || 'not available')} />
          <ContributionFact label="Next action" value={humanize(lifecycle.recommended_action)} note={humanize(lifecycle.recommendation_reason || 'No recommendation reason recorded')} />
          <ContributionFact label="Evidence gap" value={evidenceGap} note={`${item.samples.treatment_assignment_units}/${item.samples.required_per_arm} treatment; ${item.samples.control_assignment_units}/${item.samples.required_per_arm} control`} />
          <ContributionFact label="Observed utility delta" value={fmtSigned(measurement.delta)} note="Preliminary until evidence gates pass" />
          <ContributionFact
            label="Current identity"
            value={item.current_identity.matches_running_cohort ? 'Matched' : 'Not matched'}
            note={humanize(item.current_identity.mismatch_reason || item.current_identity.policy_epoch || 'current')}
          />
        </div>
      </Card>

      <div className="lco-grid">
        <Card>
          <CardHeader title="Cohort maturity" />
          <CohortProgress label="Treatment" value={item.samples.treatment_assignment_units} required={item.samples.required_per_arm} pct={treatmentPct} />
          <CohortProgress label="Control" value={item.samples.control_assignment_units} required={item.samples.required_per_arm} pct={controlPct} />
          <div className="lco-facts">
            <ContributionRow label="Raw exposures" value={item.samples.raw_exposures} />
            <ContributionRow label="Complete assignment units" value={item.samples.complete_assignment_units} />
            <ContributionRow label="Cluster members" value={item.samples.cluster_members} />
            <ContributionRow label="Selected members" value={item.samples.selected_members} />
            <ContributionRow label="Treatment selected / members" value={armSelectionLabel(item.samples.treatment_selected_members, item.samples.treatment_members)} />
            <ContributionRow label="Control selected / members" value={armSelectionLabel(item.samples.control_selected_members, item.samples.control_members)} />
          </div>
        </Card>

        <Card>
          <CardHeader title="Evidence integrity" />
          <div className="lco-facts">
            <ContributionRow label="Excluded" value={excluded} />
            <ContributionRow label="Invalid" value={invalid} />
            <ContributionRow label="Proven conflicts" value={integrity.conflict_count} />
            <ContributionRow label="Matching mirror rows" value={integrity.matching_mirror_rows} />
            <ContributionRow label="Harm boundary" value={humanize(integrity.harm_state)} />
            <ContributionRow label="Metric version" value={humanize(item.current_identity.metric_version)} />
          </div>
          {(excluded > 0 || invalid > 0 || integrity.conflict_count > 0) && (
            <p className="lco-note">
              Exclusions: {formatCounts(integrity.exclusion_reasons)}; invalid: {formatCounts(integrity.invalid_reasons)}
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
          <CardHeader title="Evaluator continuity" meta="Schedule and failures" />
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
          <CardHeader title="Lifecycle" meta="Recommendation and authority" />
          <div className="lco-facts">
            <ContributionRow label="Recommendation" value={humanize(lifecycle.recommended_action)} />
            <ContributionRow label="Paper influence" value={operations == null ? 'Unavailable' : operations.config.paper_influence_enabled ? 'Enabled' : 'Disabled'} />
            <ContributionRow label="Authority" value={authorityLabel(lifecycle.authority_enabled, lifecycle.authority_block_reason)} />
            <ContributionRow label="Authority reason" value={humanize(lifecycle.authority_block_reason || 'authority granted')} />
            <ContributionRow label="Mutation available" value={lifecycle.mutation_available ? 'Yes' : 'No'} />
          </div>
          <p className="lco-note">
            {lifecycle.mutation_available
              ? 'Owner admission is available.'
              : humanize(lifecycle.mutation_unavailable_reason || 'admission unavailable')}
          </p>
        </Card>
      </div>
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

function armSelectionLabel(selected: number | null, members: number | null) {
  return selected == null || members == null ? 'Unavailable' : `${selected} / ${members}`
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

/* ============================ DREAMING ============================ */
export function DreamingTab() {
  const summaryQuery = useQuery({ queryKey: ['dreaming-summary'], queryFn: api.getDreamingSummary })
  const logsQuery = useQuery({ queryKey: ['dreaming'], queryFn: api.getDreamingLogs })
  const [selectedItem, setSelectedItem] = useState<KnowledgeItem | null>(null)
  const summaryState = learningReadState(summaryQuery.isLoading, summaryQuery.isError, summaryQuery.data != null)
  const logsState = learningReadState(logsQuery.isLoading, logsQuery.isError, logsQuery.data != null)
  const summary = summaryQuery.data
  const allLogs = logsQuery.data ?? []
  const dreamingJob = summary?.dreaming_job

  const runs = summary?.recent_dreaming_runs || []
  const research = summary?.recent_research_notes || []
  const hypotheses = summary?.recent_strategy_hypotheses || []
  const deprecated = summary?.deprecated_memories || []
  const otherRecent = (summary?.recent_knowledge_items || []).filter(
    item => !['research_note', 'strategy_memory', 'dreaming_log', 'daily_review'].includes(item.item_type),
  )
  const logs = summary?.recent_dreaming_logs?.length ? summary.recent_dreaming_logs : allLogs.slice(0, 10)

  return (
    <>
      {/* Signature — dreaming runs as cognition sessions */}
      <Card>
        <CardHeader title="Dreaming sessions" meta="Most recent first" />
        {summaryState === 'loading' || summaryState === 'unavailable' ? (
          <LearningReadMessage state={summaryState} subject="Dreaming sessions" />
        ) : (
          <>
            {summaryState !== 'available' && <LearningReadMessage state={summaryState} subject="Dreaming sessions" />}
            {runs.length === 0 ? (
              dreamingJob?.last_started_at ? (
                <div className="lco-facts">
                  <ContributionRow label="Scheduled consolidation" value={fmtTimestamp(dreamingJob.last_started_at)} />
                  <ContributionRow label="Job status" value={humanize(dreamingJob.status || 'unknown')} />
                  <ContributionRow label="Last success" value={fmtTimestamp(dreamingJob.last_succeeded_at)} />
                  <ContributionRow label="Next run" value={fmtTimestamp(dreamingJob.next_due_at)} />
                  <ContributionRow label="Backlog" value={dreamingJob.backlog_count ?? 'Unavailable'} />
                  <p className="lco-note">The scheduler has a run record, but no Dreaming Agent session is recorded in the last 24 hours.</p>
                </div>
              ) : <p className="text-muted text-sm">No dreaming run or scheduled consolidation has been recorded.</p>
            ) : (
              <div className="dream-sessions">
                {runs.map(run => <DreamSession key={run.id} run={run} />)}
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
          <Card>
            <CardHeader title="Other knowledge" meta={summary?.recent_knowledge_truncated ? `24h, showing ${summary.recent_knowledge_returned} of ${summary.recent_knowledge_total}` : `24h, ${summary?.recent_knowledge_total ?? 0} total`} />
            <ItemList state={summaryState} items={otherRecent} onSelect={setSelectedItem} empty="No other knowledge types were produced in the last 24 hours." />
            {summary?.recent_knowledge_truncated && <p className="lco-note">This glance view is bounded. Open Knowledge Base to browse the complete catalog.</p>}
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
              <DrawerField label="Produced by run" value={selectedItem.producer_run_id || 'Not recorded'} mono />
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
function DreamSession({ run }: { run: DreamingSummary['recent_dreaming_runs'][number] }) {
  const tools = parseJsonArray(run.tools_called)
  const outputs = parseJsonArray(run.outputs_created)
  const errors = parseJsonArray(run.errors)
  const artifacts = run.artifacts ?? []
  const dur = fmtDuration(run.started_at, run.ended_at)

  return (
    <div className="dream-session">
      <div className="dream-session-head">
        <StatusText tone={runTone(run.status)}>{humanize(run.status)}</StatusText>
        <span className="dream-session-trigger">{humanize(run.trigger_type)}{run.model_provider ? `, ${humanize(run.model_provider)}` : ''}</span>
        <span className="dream-session-time mono">{fmtTimestamp(run.started_at)}{dur ? `, ${dur}` : ''}</span>
      </div>
      {tools.length > 0 && (
        <div className="dream-tools">
          {tools.map((t, i) => <span key={i} className="dream-chip mono">{t}</span>)}
        </div>
      )}
      <div className="dream-session-out">
        <span>{artifacts.length || outputs.length} output{(artifacts.length || outputs.length) === 1 ? '' : 's'} recorded</span>
        {errors.length > 0 && <span className="text-error">; {errors.length} error{errors.length === 1 ? '' : 's'}</span>}
      </div>
      {artifacts.length > 0 ? (
        <div className="dream-artifacts">
          {artifacts.map(artifact => <span key={`${artifact.artifact_type}:${artifact.artifact_id}`}><Badge tone="blue">{knowledgeTypeLabel(artifact.artifact_type)}</Badge> <span className="mono">{artifact.artifact_id}</span></span>)}
        </div>
      ) : outputs.length === 0 && errors.length === 0 ? (
        <p className="lco-note">The run completed without a recorded knowledge artifact.</p>
      ) : null}
    </div>
  )
}

/** Compact clickable list of knowledge items — title + timestamp (type is the group header). */
function ItemList({ state = 'available', items, onSelect, empty }: { state?: LearningReadState; items: KnowledgeItem[]; onSelect: (item: KnowledgeItem) => void; empty: string }) {
  if (state === 'loading' || state === 'unavailable') return <LearningReadMessage state={state} subject="Items" />
  const warning = state !== 'available' ? <LearningReadMessage state={state} subject="Items" /> : null
  if (!items || items.length === 0) return <>{warning}<p className="text-muted text-sm">{empty}</p></>
  return (
    <>
      {warning}
      <ul className="learn-items">
        {items.map(item => (
          <li key={item.id} className="learn-item">
            <ListRowButton type="button" className="learn-item-button" onClick={() => onSelect(item)}>
              <span className="learn-item-title">{item.title || '(untitled)'}</span>
              <span className="learn-item-context"><Badge tone="blue">{knowledgeTypeLabel(item.item_type)}</Badge><span className="learn-item-meta mono">{fmtTimestamp(item.created_at)}</span></span>
            </ListRowButton>
          </li>
        ))}
      </ul>
    </>
  )
}

/* ============================ KNOWLEDGE BASE ============================ */
// §4 taxonomy preserved: each sub-tab queries its real item_type (no laundering).
const KB_TABS = [
  { key: 'all', label: 'All Knowledge' },
  { key: 'research', label: 'Research Notes' },
  { key: 'strategy', label: 'Strategy Memory' },
  { key: 'reviews', label: 'Daily Reviews' },
  { key: 'dreaming', label: 'Dreaming Logs' },
  { key: 'lessons', label: 'Post-Trade Lessons' },
  { key: 'guardCandidates', label: 'Guard Candidates' },
  { key: 'guards', label: 'Active Guards' },
] as const

type KnowledgeTabKey = typeof KB_TABS[number]['key']
const KNOWLEDGE_PAGE_SIZE = 100
type KnowledgeTabData = KnowledgeCatalogResponse | KnowledgeItem[] | LessonRow[] | GuardRow[]

async function fetchKnowledgeTab(tab: KnowledgeTabKey, offset: number, search: string, status: string): Promise<KnowledgeTabData> {
  if (tab === 'all') return api.getAllKnowledge(offset, KNOWLEDGE_PAGE_SIZE, search, status)
  if (tab === 'research') return api.getKnowledge()
  if (tab === 'strategy') return api.getStrategyMemory()
  if (tab === 'reviews') return api.getKnowledge('daily_review')
  if (tab === 'dreaming') return api.getDreamingLogs()
  if (tab === 'lessons') return api.getLessons(100)
  if (tab === 'guardCandidates') return api.getGuardCandidates()
  return api.getGuards()
}

const kbCol = createColumnHelper<KnowledgeItem>()
const kbColumns = [
  kbCol.accessor('title', { header: 'Title' }),
  kbCol.accessor('item_type', { header: 'Type', cell: info => <Badge tone="blue">{humanize(info.getValue() || 'unknown')}</Badge> }),
  kbCol.accessor('confidence', { header: 'Confidence', cell: info => <ConfidenceBadge value={info.getValue()} /> }),
  kbCol.accessor('status', { header: 'Status', cell: info => <StatusBadge status={info.getValue()} /> }),
  kbCol.accessor('producer_run_id', { header: 'Producer', cell: info => <span className="mono">{info.getValue() ? String(info.getValue()).slice(0, 12) : 'Not recorded'}</span> }),
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
  const [tab, setTab] = useState<KnowledgeTabKey>('all')
  const [catalogOffset, setCatalogOffset] = useState(0)
  const [catalogSearch, setCatalogSearch] = useState('')
  const [catalogStatus, setCatalogStatus] = useState('active')
  const deferredSearch = useDeferredValue(catalogSearch)
  const [selected, setSelected] = useState<KnowledgeItem | null>(null)
  const [selectedLesson, setSelectedLesson] = useState<LessonRow | null>(null)
  const [selectedGuard, setSelectedGuard] = useState<GuardRow | null>(null)
  const active = KB_TABS.find(t => t.key === tab)!
  const activeQuery = useQuery<KnowledgeTabData>({
    queryKey: ['knowledge', tab, tab === 'all' ? catalogOffset : 0, tab === 'all' ? deferredSearch : '', tab === 'all' ? catalogStatus : 'active'],
    queryFn: () => fetchKnowledgeTab(tab, catalogOffset, deferredSearch, catalogStatus),
  })
  const funnelQuery = useQuery({ queryKey: ['learning-funnel'], queryFn: api.getLearningFunnel, staleTime: 120_000 })
  const catalog = tab === 'all' && activeQuery.data && !Array.isArray(activeQuery.data) ? activeQuery.data : null
  const data = catalog?.items ?? (Array.isArray(activeQuery.data) ? activeQuery.data : [])
  const activeState = learningReadState(activeQuery.isLoading, activeQuery.isError, activeQuery.data != null)
  const isLessons = tab === 'lessons'
  const isGuards = tab === 'guards'
  const isGuardCandidates = tab === 'guardCandidates'
  const isGuardRows = isGuards || isGuardCandidates
  const rows = isLessons ? (data as unknown as LessonRow[]) : isGuardRows ? (data as unknown as GuardRow[]) : (data as KnowledgeItem[])
  const tableState = activeState === 'available' && rows.length === 0 ? 'empty' : activeState

  const typeCounts = Object.fromEntries((funnelQuery.data?.knowledge_items_by_type ?? []).map(item => [item.item_type, item.count]))
  const totalKnowledge = (funnelQuery.data?.knowledge_items_by_type ?? []).reduce((total, item) => total + item.count, 0)
  const counts: Record<KnowledgeTabKey, number | null> = {
    all: funnelQuery.data ? totalKnowledge : null,
    research: typeCounts.research_note ?? 0,
    strategy: typeCounts.strategy_memory ?? 0,
    reviews: typeCounts.daily_review ?? 0,
    dreaming: typeCounts.dreaming_log ?? 0,
    lessons: funnelQuery.data?.ps_lessons.total ?? null,
    guardCandidates: typeCounts.guard_candidate ?? 0,
    guards: funnelQuery.data?.active_guards_total ?? null,
  }
  const countState = learningReadState(funnelQuery.isLoading, funnelQuery.isError, funnelQuery.data != null, !!funnelQuery.data && Object.keys(funnelQuery.data.query_errors).length > 0)
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
      <div className="kb-subtabs" role="tablist" aria-label="Knowledge categories">
        {KB_TABS.map(t => (
          <Button
            key={t.key}
            id={`knowledge-tab-${t.key}`}
            role="tab"
            aria-selected={tab === t.key}
            aria-controls="knowledge-tabpanel"
            tabIndex={tab === t.key ? 0 : -1}
            variant={tab === t.key ? 'primary' : 'secondary'}
            size="sm"
            onClick={() => { setTab(t.key); if (t.key !== 'all') setCatalogOffset(0) }}
            onKeyDown={handleTabKeyDown}
          >
            {t.label}
            {countState === 'loading' && <span className="tab-badge" aria-label={`${t.label} count loading`}>…</span>}
            {countState === 'unavailable' && <span className="tab-badge" aria-label={`${t.label} count unavailable`}>!</span>}
            {counts[t.key] != null && <span className="tab-badge">{counts[t.key]}{countState === 'stale' ? '~' : ''}</span>}
          </Button>
        ))}
      </div>
      <Card id="knowledge-tabpanel" role="tabpanel" aria-labelledby={`knowledge-tab-${tab}`}>
        {tab === 'all' && <div className="kb-toolbar">
          <label>
            <span>Search knowledge</span>
            <input type="search" value={catalogSearch} placeholder="Title, tag, or type" onChange={event => { setCatalogSearch(event.target.value); setCatalogOffset(0) }} />
          </label>
          <label>
            <span>Status</span>
            <select value={catalogStatus} onChange={event => { setCatalogStatus(event.target.value); setCatalogOffset(0) }}>
              <option value="active">Active</option>
              <option value="archived">Archived</option>
              <option value="deprecated">Deprecated</option>
              <option value="all">All statuses</option>
            </select>
          </label>
        </div>}
        {catalog && <p className="kb-catalog-meta">Showing {catalog.total === 0 ? 0 : catalog.offset + 1}–{catalog.offset + catalog.returned} of {catalog.total.toLocaleString()} matching knowledge items</p>}
        {isLessons ? (
          <DataTable state={tableState} data={rows as LessonRow[]} columns={lessonColumns} onRowClick={setSelectedLesson} emptyMessage="No lessons yet." pageSize={100} />
        ) : isGuardRows ? (
          <DataTable
            state={tableState}
            data={rows as GuardRow[]}
            columns={guardColumns}
            onRowClick={setSelectedGuard}
            pageSize={100}
            emptyMessage={isGuardCandidates ? 'No guard candidates awaiting review.' : 'No active guards yet.'}
          />
        ) : (
          <DataTable state={tableState} data={rows as KnowledgeItem[]} columns={kbColumns} onRowClick={setSelected} emptyMessage={`No ${active.label.toLowerCase()} yet.`} pageSize={100} />
        )}
        {catalog && catalog.total > catalog.limit && (
          <div className="kb-pagination" aria-label="Knowledge catalog pagination">
            <Button size="sm" variant="secondary" disabled={catalog.offset === 0} onClick={() => setCatalogOffset(Math.max(0, catalog.offset - catalog.limit))}>Previous</Button>
            <span>Page {Math.floor(catalog.offset / catalog.limit) + 1} of {Math.ceil(catalog.total / catalog.limit)}</span>
            <Button size="sm" variant="secondary" disabled={!catalog.has_more} onClick={() => setCatalogOffset(catalog.offset + catalog.limit)}>Next</Button>
          </div>
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
              <DrawerField label="Produced by run" value={selected.producer_run_id ? <span className="mono">{selected.producer_run_id}</span> : 'Not recorded'} />
              <DrawerField label="Item-level consumption" value="Not recorded" />
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
  return (
    <>
      <LearningFunnelPanel />

      <FilterControllerPersistentCard />
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
  const schedule = data.schedule
  const meta = `${schedule.enabled ? `scheduled every ${schedule.observe_interval_hours}h` : 'schedule disabled'}, ${data.runs_available} runs, last ${fmtTimestamp(data.last_run_at)}`
  const emptyCopy = data.empty_reason === 'controller_disabled'
    ? 'Scheduled observation is disabled; persisted history remains read-only.'
    : data.empty_reason === 'no_controller_runs'
    ? 'No controller run has been persisted yet.'
    : data.empty_reason === 'insufficient_run_history'
      ? `Only ${data.runs_available} runs are available; ${cfg.min_consecutive_runs} are required to judge persistence.`
      : 'No persistent overrestrictive signals across the available run history.'

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
        <p className="text-muted text-sm">{emptyCopy}</p>
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

function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  const tabs = Array.from(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? [])
  const current = tabs.indexOf(event.currentTarget)
  if (current < 0 || tabs.length === 0) return
  event.preventDefault()
  const next = event.key === 'Home' ? 0
    : event.key === 'End' ? tabs.length - 1
    : event.key === 'ArrowRight' ? (current + 1) % tabs.length
    : (current - 1 + tabs.length) % tabs.length
  tabs[next].focus()
  tabs[next].click()
}

/** UTC-safe parse (mirrors lib/format normalizeIso) → epoch ms, or NaN. */
function isoMs(iso?: string | null): number {
  if (!iso) return NaN
  const norm = iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`
  return new Date(norm).getTime()
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

function knowledgeTypeLabel(itemType: string): string {
  return ITEM_TYPE_LABEL[itemType] ?? `Unknown type: ${itemType}`
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
