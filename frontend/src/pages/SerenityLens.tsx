import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  api,
  SerenityEvidence,
  SerenityOpportunity,
  SerenityResearchCycle,
  SerenityRiskGate,
  SerenityTheme,
  SerenityThemeOverview,
  SerenityTimelinePoint,
} from '../api/client'
import PageHeader from '../components/PageHeader'

type ScoreBucket = 'watch' | 'research_priority' | 'candidate_position'
type PipelineStepStatus = 'pending' | 'active' | 'done'
type ThemeOverviewState = SerenityThemeOverview | null

const PIPELINE_STEPS = [
  'research',
  'validation',
  'demand_wave',
  'supply_chain',
  'ramp',
  'order_night',
  'candidate_position',
] as const
type PipelineStep = (typeof PIPELINE_STEPS)[number]

const MODULE_CARDS = [
  {
    title: 'Theme Discovery',
    body: 'Track demand-wave shifts and map which themes are currently under stress.',
  },
  {
    title: 'Chokepoint Layer',
    body: 'Split evidence into disclosed / high-confidence / likely / speculative before any thesis enters priority flow.',
  },
  {
    title: 'Average Engine',
    body: 'Expose DCA ladder and historical-regime mismatch modes as explicit research flags.',
  },
  {
    title: 'Risk Gate',
    body: 'Only let research candidates leave this page when gates are in a pass-ready posture.',
  },
]

const STEP_LABELS: Record<string, string> = {
  research: 'Research',
  validation: 'Validation',
  demand_wave: 'Demand Wave',
  supply_chain: 'Supply Chain',
  ramp: 'Ramp',
  order_night: 'Order Night',
  candidate_position: 'Candidate',
}

function safePipelineStep(value: string): PipelineStep {
  return (PIPELINE_STEPS as readonly string[]).includes(value) ? (value as PipelineStep) : 'research'
}

function statusBadge(type: ScoreBucket) {
  if (type === 'candidate_position') return <span className="badge badge-green">candidate_position</span>
  if (type === 'research_priority') return <span className="badge badge-blue">research_priority</span>
  return <span className="badge badge-yellow">watch</span>
}

function shortDate(s: string | null | undefined) {
  if (!s) return '—'
  return s.slice(0, 16)
}

function parseJsonArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(v => String(v))
  }
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      if (Array.isArray(parsed)) return parsed.map(v => String(v))
    } catch {
      return []
    }
  }
  return []
}

function normalizeStage(stage: string | null | undefined): string {
  const value = (stage || '').toLowerCase().trim()
  if (value === 'sample' || value === 'designin' || value === 'design-in') return 'supply_chain'
  if (value === 'order' || value === 'order-night') return 'order_night'
  if (value === 'candidate') return 'candidate_position'
  return PIPELINE_STEPS.includes(value as never) ? value : 'research'
}

function pipelineStepState(step: string, activeStage: string): PipelineStepStatus {
  const active = PIPELINE_STEPS.indexOf(activeStage as PipelineStep)
  const current = PIPELINE_STEPS.indexOf(step as PipelineStep)
  if (current < 0 || active < 0) return 'pending'
  if (current < active) return 'done'
  if (current === active) return 'active'
  return 'pending'
}

function isBucket(value: unknown): value is ScoreBucket {
  return value === 'watch' || value === 'research_priority' || value === 'candidate_position'
}

function normalizeRow(op: SerenityOpportunity): SerenityOpportunity & { serenity_score: { bucket: ScoreBucket; score: number } } {
  const scoreBucket = isBucket(op.serenity_score?.bucket) ? op.serenity_score!.bucket : 'watch'
  return { ...op, serenity_score: { ...op.serenity_score, bucket: scoreBucket, score: op.serenity_score?.score ?? 0 } as any }
}

function formatSigned(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'
  return value > 0 ? `+${value}` : `${value}`
}

type PostMortemRow = {
  id: string
  outcome: string
  summary: string
  failure_bucket?: string | null
  score_delta?: number | null
  created_at: string
}

export default function SerenityLens() {
  const qc = useQueryClient()
  const { data: themes = [] as SerenityTheme[] } = useQuery({
    queryKey: ['serenity-themes'],
    queryFn: api.getSerenityThemes,
  })

  const [selectedThemeId, setSelectedThemeId] = useState<string | ''>('')
  const [selectedOpp, setSelectedOpp] = useState<SerenityOpportunity | null>(null)
  const [search, setSearch] = useState('')
  const [isSavingReplay, setIsSavingReplay] = useState(false)
  const [timelineCursor, setTimelineCursor] = useState(0)

  const snapshotMut = useMutation({
    mutationFn: async ({ id, asOf }: { id: string; asOf: string }) => {
      return api.createSerenityReplaySnapshot(id, { as_of: asOf })
    },
    onSuccess: () => {
      qc.invalidateQueries()
      setIsSavingReplay(false)
    },
  })

  useEffect(() => {
    if (!selectedThemeId && themes.length > 0) {
      setSelectedThemeId(themes[0].id)
    }
  }, [themes, selectedThemeId])

  const themesLoaded = themes.length > 0
  const selectedTheme = themes.find(t => t.id === selectedThemeId) || null

  const { data: opportunities = [] as (SerenityOpportunity & { serenity_score: { bucket: ScoreBucket; score: number } })[] } = useQuery({
    queryKey: ['serenity-opportunities', selectedThemeId],
    queryFn: () => api.getSerenityOpportunities(selectedThemeId || undefined),
    enabled: themesLoaded && !!selectedThemeId,
  })

  const { data: evidence = [] as SerenityEvidence[] } = useQuery({
    queryKey: ['serenity-evidence', selectedThemeId],
    queryFn: () => api.getSerenityEvidence(selectedThemeId || undefined),
    enabled: themesLoaded && !!selectedThemeId,
  })

  const { data: themeOverview = null as ThemeOverviewState } = useQuery({
    queryKey: ['serenity-theme-overview', selectedThemeId],
    queryFn: () => api.getSerenityThemeOverview(selectedThemeId),
    enabled: themesLoaded && !!selectedThemeId,
  })

  const { data: gates = [] as SerenityRiskGate[] } = useQuery({
    queryKey: ['serenity-risk-gates', selectedOpp?.id],
    queryFn: () => api.getSerenityOpportunityRiskGates(selectedOpp!.id),
    enabled: !!selectedOpp?.id,
  })

  const { data: researchCycle = null as SerenityResearchCycle | null } = useQuery({
    queryKey: ['serenity-research-cycle', selectedOpp?.id],
    queryFn: () => api.getSerenityResearchCycle(selectedOpp!.id),
    enabled: !!selectedOpp?.id,
  })

  const timeline = useMemo<SerenityTimelinePoint[]>(() => {
    if (!researchCycle?.timeline?.length) return []
    return [...researchCycle.timeline]
      .sort((a, b) => new Date(a.as_of).getTime() - new Date(b.as_of).getTime())
  }, [researchCycle?.timeline])

  useEffect(() => {
    if (!timeline.length) {
      setTimelineCursor(0)
      return
    }
    setTimelineCursor(previous => {
      const max = timeline.length - 1
      if (previous > max) return max
      if (previous < 0) return 0
      return previous
    })
  }, [timeline.length])

  const stageCounts = themeOverview?.metrics.stage_counts || {}
  const averageModeCounts = themeOverview?.metrics.average_mode_counts || {}

  const enriched = useMemo(
    () => opportunities.map(normalizeRow)
      .map(op => ({ ...op, riskFlags: parseJsonArray(op.risk_flags) }))
      .filter(op => {
        if (!search) return true
        const s = search.toLowerCase()
        return op.symbol.toLowerCase().includes(s)
          || (op.thesis || '').toLowerCase().includes(s)
          || op.stage.toLowerCase().includes(s)
      }),
    [opportunities, search],
  )

  const ordered = useMemo(
    () => [...enriched].sort((a, b) => (b.serenity_score?.score || 0) - (a.serenity_score?.score || 0)),
    [enriched],
  )

  const topEvidence = useMemo(
    () => [...evidence]
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .slice(0, 8),
    [evidence],
  )

  const scoreSummary = useMemo(() => {
    const buckets: Record<ScoreBucket, number> = {
      watch: 0,
      research_priority: 0,
      candidate_position: 0,
    }
    for (const opp of ordered) {
      buckets[opp.serenity_score?.bucket || 'watch'] += 1
    }
    return buckets
  }, [ordered])

  useEffect(() => {
    setTimelineCursor(0)
  }, [selectedOpp?.id])

  if (themes.length === 0) {
    return (
      <div>
        <PageHeader
          title="Serenity Lens"
          role="Research-first workbench: map themes -> chokepoints -> evidence -> time stage -> risk gate."
        />
        <section className="card">
          <h3>Serenity Lens</h3>
          <p>No research themes exist yet. Open this endpoint pair from backend jobs to hydrate themes and opportunities.</p>
          <p>Current page intentionally reads read-only; it displays only persisted research states.</p>
        </section>
      </div>
    )
  }

  const safeSelected = selectedOpp ? selectedOpp : null
  const replayPoint = timeline[timelineCursor]
  const activeReplayStage = normalizeStage(replayPoint?.stage || safeSelected?.stage)
  const postMortems = (researchCycle?.post_mortems || []) as PostMortemRow[]
  const hasTimeline = timeline.length > 0

  return (
    <div>
      <PageHeader
        title="Serenity Lens"
        role="Research-first workbench: map themes -> chokepoints -> evidence -> time stage -> risk gate."
      />
      <div className="notice-banner">
        <span className="notice-icon">!</span>
        <div className="notice-text">
          <b>Honest scope note:</b> this panel is research-first.
          It summarizes local evidence and scores; it does not create orders directly.
        </div>
      </div>

      <div className="lens-grid">
        {MODULE_CARDS.map(card => (
          <section className="card lens-module" key={card.title}>
            <h3>{card.title}</h3>
            <p>{card.body}</p>
          </section>
        ))}
      </div>

      <section className="card">
        <div className="card-head">
          <h3>Theme Context</h3>
          <div className="lens-controls">
            <label htmlFor="serenity-theme">Theme</label>
            <select
              id="serenity-theme"
              value={selectedThemeId}
              onChange={e => {
                setSelectedThemeId(e.target.value)
                setSelectedOpp(null)
              }}
            >
              {themes.map(t => (
                <option value={t.id} key={t.id}>{t.name}</option>
              ))}
            </select>
          </div>
        </div>
        <div>
          {selectedTheme ? (
            <div>
              <p><b>Demand wave:</b> {selectedTheme.demand_wave || '—'}</p>
              <p><b>Objective:</b> {selectedTheme.objective || '—'}</p>
              {themeOverview ? (
                <>
                  <p><b>Opportunity count:</b> {themeOverview.metrics.opportunity_count}</p>
                  <p><b>Evidence count:</b> {themeOverview.metrics.evidence_count}</p>
                <div className="serenity-divider" />
                  <h4>Pipeline distribution</h4>
                  <div className="lens-distribution">
                    {themeOverview.metrics.pipeline_order.length
                      ? themeOverview.metrics.pipeline_order.map(rawStep => {
                        const step = safePipelineStep(rawStep)
                        const label = STEP_LABELS[step] || step
                        const count = stageCounts[step as string] || 0
                        return (
                          <span key={step} className="pill">
                            {label}: {count}
                          </span>
                        )
                      })
                      : <span className="pill">No pipeline stages available</span>
                    }
                  </div>
                  <h4>Average mode distribution</h4>
                  <div className="lens-distribution">
                    {Object.keys(averageModeCounts).length ? Object.entries(averageModeCounts)
                      .sort((a, b) => b[1] - a[1])
                      .map(([mode, count]) => (
                        <span key={mode} className="pill">
                          {mode}: {count}
                        </span>
                      ))
                      : <span className="pill">No average mode data</span>
                    }
                  </div>
                </>
              ) : <p>Theme overview loading…</p>}
            </div>
          ) : <p>No theme selected.</p>}
        </div>
      </section>

      <section className="card">
        <div className="card-head card-head-space">
          <h3>Evidence Desk</h3>
          <span>{topEvidence.length ? `${topEvidence.length} evidence item(s)` : 'No evidence yet'}</span>
        </div>
        <div className="lens-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Title</th>
                <th>Tier</th>
                <th>Source</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {topEvidence.length > 0 ? topEvidence.map(item => (
                <tr key={item.id}>
                  <td>{item.symbol || '—'}</td>
                  <td>{item.title}</td>
                  <td>{item.tier}</td>
                  <td>{item.source}</td>
                  <td>{item.confidence}</td>
                </tr>
              )) : (
                <tr><td colSpan={5}>No evidence yet for this theme.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <div className="card-head card-head-space">
          <h3>Opportunity Lens</h3>
          <div className="lens-controls">
            <span className="pill pill-blue">watch: {scoreSummary.watch}</span>
            <span className="pill pill-yellow">research: {scoreSummary.research_priority}</span>
            <span className="pill pill-green">candidate: {scoreSummary.candidate_position}</span>
          </div>
          <div className="lens-controls">
            <label htmlFor="lens-search">Filter</label>
            <input
              id="lens-search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="symbol / thesis / stage"
              className="lens-search"
            />
          </div>
        </div>

        <div className="lens-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Direction</th>
                <th>Evidence</th>
                <th>Stage</th>
                <th>Action</th>
                <th>Score</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {ordered.length > 0 ? ordered.map(op => (
                <tr key={op.id} onClick={() => setSelectedOpp(op)} className="clickable">
                  <td>{op.symbol}</td>
                  <td>{op.direction}</td>
                  <td>{op.evidence_tier}</td>
                  <td>{op.stage}</td>
                  <td>{statusBadge(op.serenity_score?.bucket || 'watch')}</td>
                  <td>{op.serenity_score?.score ?? 0}</td>
                  <td>{shortDate(op.created_at)}</td>
                </tr>
              )) : (
                <tr><td colSpan={7}>No opportunities for this theme.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {safeSelected && (
        <section className="card">
          <div className="card-head card-head-space">
            <h3>Selected thesis</h3>
            <button
              className="secondary"
              onClick={() => {
                setIsSavingReplay(true)
                const asOf = new Date().toISOString()
                snapshotMut.mutate({ id: safeSelected.id, asOf }, { onSettled: () => setIsSavingReplay(false) })
              }}
              disabled={isSavingReplay}
            >
              {isSavingReplay ? 'Saving snapshot…' : 'Capture replay snapshot'}
            </button>
          </div>
          <p><b>Symbol:</b> {safeSelected.symbol}</p>
          <p><b>Direction:</b> {safeSelected.direction}</p>
          <p><b>Thesis:</b> {safeSelected.thesis || '—'}</p>
          <p><b>Risk intent:</b> {safeSelected.action_intent}</p>
          <p><b>Evidence tier / average mode:</b> {safeSelected.evidence_tier} / {safeSelected.average_mode}</p>
          <p><b>Risk flags:</b> {parseJsonArray(safeSelected.risk_flags).join(', ') || '—'}</p>

          <div className="serenity-divider" />
          <h4>Reasoning score</h4>
          <div>
            <p><b>Total:</b> {safeSelected.serenity_score?.score ?? 0}</p>
            <p><b>Evidence score:</b> {safeSelected.serenity_score?.evidence_score ?? 0}</p>
            <p><b>Stage score:</b> {safeSelected.serenity_score?.stage_score ?? 0}</p>
            <p><b>Average score:</b> {safeSelected.serenity_score?.average_score ?? 0}</p>
            <p><b>Confidence component:</b> {safeSelected.serenity_score?.confidence_component ?? 0}</p>
            <p><b>Stage index:</b> {safeSelected.serenity_score?.stage_index ?? 0}</p>
            {safeSelected.serenity_score?.average_engine ? (
              <div>
                <p><b>Average mode:</b> {safeSelected.serenity_score.average_engine.mode}</p>
                <p><b>Average engine active:</b> {safeSelected.serenity_score.average_engine.active ? 'yes' : 'no'}</p>
                {safeSelected.serenity_score.average_engine.signals ? (
                  <p>
                    <b>Signals:</b> dca=
                    {(safeSelected.serenity_score.average_engine.signals as { has_dca?: boolean }).has_dca ? 'yes' : 'no'}
                    , regime_gap=
                    {(safeSelected.serenity_score.average_engine.signals as { has_regime_gap?: boolean }).has_regime_gap ? 'yes' : 'no'}
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>

          <div className="serenity-divider" />
          <h4>Pipeline state (from selected replay point)</h4>
          <div className="pipeline-strip">
            {PIPELINE_STEPS.map(step => {
              const state = pipelineStepState(step, activeReplayStage)
              return (
                <span key={step} className={`pill pill-${state === 'active' ? 'blue' : state === 'done' ? 'green' : 'yellow'}`}>
                  {STEP_LABELS[step]}: {state}
                </span>
              )
            })}
          </div>
          {hasTimeline ? (
            <div>
              <label htmlFor="timeline-slider" className="notice-text">
                Time Replay: {shortDate(replayPoint?.as_of || safeSelected.created_at)}
              </label>
              <input
                id="timeline-slider"
                type="range"
                min={0}
                max={timeline.length - 1}
                step={1}
                value={timelineCursor}
                onChange={e => setTimelineCursor(Number(e.target.value))}
              />
              <p><b>Replay index:</b> {timelineCursor + 1}/{timeline.length}</p>
              <p><b>Event:</b> {replayPoint?.event_title || 'snapshot'} ({replayPoint?.event_type || 'snapshot'})</p>
              <p><b>Score:</b> {replayPoint?.score_total ?? safeSelected.serenity_score?.score ?? 0}</p>
              <p><b>Stage:</b> {replayPoint?.stage || safeSelected.stage}</p>
              <p><b>Bucket:</b> {replayPoint?.bucket || safeSelected.serenity_score?.bucket || 'watch'}</p>
            </div>
          ) : (
            <p>No replay timeline yet. Capture snapshots to build a timeline.</p>
          )}

          <div className="serenity-divider" />
          <h4>Risk gates</h4>
          <div className="lens-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Gate</th>
                  <th>Status</th>
                  <th>Metric</th>
                  <th>Observed / threshold</th>
                </tr>
              </thead>
              <tbody>
                {gates.length > 0 ? gates.map(g => (
                  <tr key={g.id}>
                    <td>{g.gate_name}</td>
                    <td>{g.status}</td>
                    <td>{g.metric}</td>
                    <td>{String(g.observed ?? '—')} / {String(g.threshold ?? '—')}</td>
                  </tr>
                )) : <tr><td colSpan={4}>No gates yet.</td></tr>}
              </tbody>
            </table>
            {researchCycle ? (
              <div>
                <div className="serenity-divider" />
                <h5>Risk summary</h5>
                {Object.keys(researchCycle.risk_gate_summary).length
                  ? (
                    <div className="lens-distribution">
                      {Object.entries(researchCycle.risk_gate_summary).map(([k, v]) => (
                        <span key={k} className="pill">
                          {k}: {v}
                        </span>
                      ))}
                    </div>
                  )
                  : <p>No risk summary yet.</p>
                }
              </div>
            ) : null}
          </div>

          <div className="serenity-divider" />
          <h4>Post-mortem feedback</h4>
          <div className="lens-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>At</th>
                  <th>Outcome</th>
                  <th>Failure bucket</th>
                  <th>Score delta</th>
                  <th>Summary</th>
                </tr>
              </thead>
              <tbody>
                {postMortems.length > 0 ? postMortems.map(pm => (
                  <tr key={pm.id}>
                    <td>{shortDate(pm.created_at)}</td>
                    <td>{pm.outcome}</td>
                    <td>{pm.failure_bucket || '—'}</td>
                    <td>{formatSigned(pm.score_delta ?? null)}</td>
                    <td>{pm.summary}</td>
                  </tr>
                )) : (
                  <tr><td colSpan={5}>No post-mortems yet.</td></tr>
                )}
              </tbody>
            </table>
            {researchCycle ? (
              <p>
                <b>Post-mortem count:</b>
                {' '}
                {researchCycle.post_mortem_summary.count}
              </p>
            ) : null}
          </div>
        </section>
      )}
    </div>
  )
}
