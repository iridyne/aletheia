import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { connectivityTone, semanticTone, type SemanticState } from '../lib/systemState'
import { bootApi, type OverviewBootstrap, type ShellBootstrap } from '../api/boot'
import PnLZone from '../components/PnLZone'
import AgentFeed from '../components/AgentFeed'
import AnimatedSection from '../components/AnimatedSection'
import CollectorHealthDetails from '../components/CollectorHealthDetails'
import { Button, Card, Meter, StatusText } from '../components/ui'
import { formatViolation, riskStateLabel } from '../lib/riskViolations'
import { fmtTimestamp, humanize } from '../lib/format'
import { api, type GoLiveScorecard } from '../api/client'
import type { MarketDataCoverageReport } from '../types'
import { parseAllIssues, sortedIssues, type ParsedIssue } from '../components/health-issues'
import type { CollectorHealthReport, MarketDataCoverageDomain } from '../types'

export function operatorMutationError(action: string, error: unknown): string {
  const detail = error instanceof Error && error.message
    ? error.message
    : typeof error === 'string' && error ? error : 'request failed'
  return `${action}: ${detail}`
}


export default function Overview() {
  const shell = useQuery({
    queryKey: ['ui-shell-bootstrap'],
    queryFn: ({ signal }) => bootApi.getShellBootstrap({ signal }),
    staleTime: 5_000,
    refetchInterval: 10_000,
  })
  const overview = useQuery({
    queryKey: ['ui-overview-bootstrap'],
    queryFn: ({ signal }) => bootApi.getOverviewBootstrap({ signal }),
    staleTime: 5_000,
    refetchInterval: 15_000,
  })
  const coverage = useQuery<MarketDataCoverageReport>({
    queryKey: ['market-data-coverage'],
    queryFn: api.getDataCoverage,
    staleTime: 30_000,
    refetchInterval: 30_000,
  })
  const overviewMarked = useRef(false)

  useEffect(() => {
    if (overview.data && !overviewMarked.current) {
      overviewMarked.current = true
      performance.mark('aletheia:first-real-overview')
    }
  }, [overview.data])

  return (
    <div>
      <h2 className="page-title">Overview</h2>
      <SetupNotice required={overview.data?.setup_required} />
      <PnLZone bootstrap={overview.data} />
      <EntrySummaryStrip summary={overview.data?.summary} />
      <div className="cockpit-main">
        <AgentFeed bootstrap={overview.data} />
        <div className="cockpit-rail">
          <RiskCard shell={shell.data} shellError={shell.isError} />
          <SystemCard shell={shell.data} coverage={coverage.data} coverageError={coverage.isError} />
          <GoLiveScoreCard />
        </div>
      </div>
      {overview.error && <p className="text-muted text-sm" role="status">Overview data is not available; local runtime status remains visible.</p>}
    </div>
  )
}

function SetupNotice({ required }: { required?: boolean }) {
  const navigate = useNavigate()
  if (!required) return null
  return (
    <Card className="setup-banner" role="status">
      <strong className="mono text-xs tracking-widest">NO MODEL CONFIGURED</strong>
      <button className="btn btn-sm secondary" onClick={() => navigate('/settings?tab=agent')}>Configure Model Provider →</button>
    </Card>
  )
}

function RiskCard({ shell, shellError }: { shell?: ShellBootstrap; shellError?: boolean }) {
  const risk = shell?.risk
  const status = String(risk?.status || 'not_available')
  const violations = risk?.violations || []
  const unavailable = shellError || !shell || !risk
  const tone = unavailable
    ? semanticTone('unknown')
    : semanticTone(status === 'blocked' ? 'blocked' : status === 'healthy' ? 'active' : status === 'warning' ? 'degraded' : 'unknown')
  return (
    <Card className="rail-card risk-panel">
      <div className="risk-head"><h3>Risk State</h3><div className="risk-head-badges">{risk?.kill_switch_active && <StatusText tone="red">KILL SWITCH</StatusText>}<StatusText tone={tone}>{riskStateLabel(status)}</StatusText></div></div>
      {unavailable ? <p className="text-muted text-sm" role="status">Risk state not available</p> : violations.length ? <ul className="risk-violations">{violations.map((value, index) => { const item = formatViolation(value); return <li key={`${value}-${index}`} className="risk-violation"><span className={`risk-dot risk-dot-${item.severity}`} /><div className="risk-violation-body"><span className="risk-violation-label">{item.label}</span>{item.detail && <span className="risk-violation-detail">{item.detail}</span>}</div></li> })}</ul> : <div className="risk-clear"><span className="risk-dot risk-dot-ok" /><span className="text-muted text-sm">No active violations</span></div>}
    </Card>
  )
}

type SystemDetail = 'collectors' | 'coverage' | 'mode' | null

function SystemCard({ shell, coverage, coverageError }: { shell?: ShellBootstrap; coverage?: MarketDataCoverageReport; coverageError?: boolean }) {
  const navigate = useNavigate()
  const [expanded, setExpanded] = useState<SystemDetail>(null)
  const exchange = shell?.exchange
  const exchangeDiverged = !!exchange?.exchange_diverged || exchange?.status === 'diverged'
  const exchangeAligned = exchange?.status === 'aligned'
  const exchangeLabel = exchange?.status === 'local_paper'
    ? 'Local paper · no exchange'
    : exchangeDiverged
      ? `${exchange?.execution_exchange || 'unknown'} exec · ${exchange?.data_exchange || 'unknown'} data`
      : exchange?.execution_exchange || exchange?.data_exchange || 'not available'
  const collectorHealthQuery = useQuery<CollectorHealthReport>({
    queryKey: ['collector-health'],
    queryFn: api.getCollectorHealth,
    staleTime: 28_000,
    refetchInterval: 30_000,
    retry: 1,
  })
  const collectorHealth = collectorHealthQuery.data
  const coverageReport = coverage
  const collectorIncident = collectorHealth?.infrastructure_incident
  const collectorCounts = new Map<string, number>()
  for (const domain of collectorHealth?.domains ?? []) {
    const status = domain.overdue ? 'overdue' : domain.degraded ? 'degraded' : domain.last_status
    const label = status === 'not_configured' ? 'unconfigured' : status === 'never_run' ? 'unobserved' : status.replace(/_/g, ' ')
    collectorCounts.set(label, (collectorCounts.get(label) ?? 0) + 1)
  }
  const collectorSummary = [...collectorCounts].map(([status, count]) => `${count} ${status}`).join(' · ')
  const collectorState: SemanticState = !collectorHealth
    ? collectorHealthQuery.isPending ? 'loading' : 'unknown'
    : collectorIncident?.status === 'active' || collectorIncident?.status === 'recovering'
      ? 'degraded'
      : collectorHealth.status === 'healthy' ? collectorCounts.has('success') ? 'active' : 'disabled'
        : collectorHealth.status === 'degraded' || collectorHealth.status === 'attention' ? 'degraded' : 'unknown'
  const collectorLight = semanticTone(collectorState)
  const collectorValue = !collectorHealth
    ? collectorHealthQuery.isPending ? 'loading' : 'not available'
    : collectorIncident?.status === 'active'
      ? `${collectorIncident.affected_domains.length} blocked by connectivity`
      : collectorIncident?.status === 'recovering'
        ? `${collectorIncident.affected_domains.length} retrying after recovery`
        : collectorHealth.status === 'initializing' ? `initializing · ${collectorSummary}` : collectorSummary
  const coverageHealthy = coverage?.status === 'healthy'
  const coverageLight = coverageReport
    ? coverage?.status === 'empty' ? 'muted' : coverageReport.critical_problem_count > 0 ? 'yellow' : coverageHealthy ? 'green' : 'yellow'
    : 'muted'
  const coverageValue = coverageReport
    ? `${coverageReport.fresh_count}/${coverageReport.domain_count} current`
    : coverageError ? 'not available' : 'deferred'
  const healthIssues = sortedIssues(parseAllIssues(shell?.health?.issues || []))
  const toggle = (detail: Exclude<SystemDetail, null>) => setExpanded(value => value === detail ? null : detail)
  const pipelineTone: Light = coverageError || !collectorHealth
    ? 'muted'
    : collectorState === 'degraded' || (coverageReport && coverageReport.critical_problem_count > 0)
      ? 'yellow'
      : collectorHealth.status === 'attention' || collectorHealth.status === 'initializing'
        ? 'yellow'
        : 'green'
  const pipelineLabel = coverageError || !collectorHealth
    ? 'UNAVAILABLE'
    : collectorState === 'degraded' || (coverageReport && coverageReport.critical_problem_count > 0)
      ? 'DEGRADED'
      : collectorHealth.status === 'attention'
        ? 'ATTENTION'
        : collectorHealth.status === 'initializing'
          ? 'INITIALIZING'
          : 'OPERATIONAL'
  return (
    <Card className="rail-card system-panel data-pipeline-panel">
      <div className="pipeline-head">
        <h3>Data Pipeline</h3>
        <StatusText tone={pipelineTone}>{pipelineLabel}</StatusText>
      </div>
      <table className="status-board"><tbody>
        <StatusRow label="Exchange" light={exchangeDiverged ? 'yellow' : exchange?.status === 'local_paper' ? 'muted' : exchangeAligned ? 'green' : 'muted'} value={exchangeLabel} onClick={() => navigate('/settings?tab=execution')} title={exchangeDiverged ? 'Execution and market-data exchanges are intentionally split.' : exchangeLabel} externalLink />
        <StatusRow label="Collectors" light={collectorLight} value={collectorValue} onClick={() => toggle('collectors')} expanded={expanded === 'collectors'} />
        {collectorHealth && collectorHealth.status !== 'healthy' && expanded !== 'collectors' && <tr className="status-detail-row"><td colSpan={2}><div className="system-inline-detail"><CollectorHealthDetails health={collectorHealth} mode="compact" /></div></td></tr>}
        <StatusRow label="Data coverage" light={coverageLight} value={coverageValue} onClick={() => toggle('coverage')} expanded={expanded === 'coverage'} />
        <StatusRow label="Execution" light={shell?.kill_switch?.active ? 'red' : shell?.live?.enabled ? 'yellow' : 'muted'} value={shell?.kill_switch?.active ? 'KILL SWITCH' : shell?.live?.enabled ? `Live: ${humanize(shell?.live?.state || 'active')}` : humanize(shell?.permission?.mode || 'Paper')} onClick={() => toggle('mode')} expanded={expanded === 'mode'} title={`Live: ${humanize(shell?.live?.state || 'not available')} · ${humanize(shell?.permission?.mode || 'not available')}${shell?.kill_switch?.active ? ' · KILL' : ''}`} />
      </tbody></table>
      <AnimatedSection open={expanded === 'collectors'}>
        {collectorHealth?.domains?.length ? <CollectorHealthDetails health={collectorHealth} mode="full" /> : <p className="text-muted text-sm" role="status">Collector diagnostics not available.</p>}
      </AnimatedSection>
      <AnimatedSection open={expanded === 'coverage'}>
        {coverageReport?.domains?.length ? <DataCoverageDetail coverage={coverageReport} /> : <p className="text-muted text-sm" role="status">Data coverage details {coverageError ? 'not available.' : 'loading…'}</p>}
      </AnimatedSection>
      <AnimatedSection open={expanded === 'mode'}>
        <ModeDetail shell={shell} />
      </AnimatedSection>
      {healthIssues.length > 0 && <HealthIssueList issues={healthIssues} />}
    </Card>
  )
}

type Light = 'green' | 'red' | 'yellow' | 'blue' | 'muted'

function StatusRow({
  label, light, value, title, onClick, expanded, externalLink,
}: { label: string; light: Light; value: string; title?: string; onClick?: () => void; expanded?: boolean; externalLink?: boolean }) {
  const clickable = Boolean(onClick)
  return (
    <tr
      className={`status-row${clickable ? ' status-row-clickable' : ''}`}
      onClick={onClick}
      onKeyDown={event => { if (clickable && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); onClick?.() } }}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      aria-expanded={clickable && expanded != null ? expanded : undefined}
    >
      <th>{label}</th>
      <td className="status-cell">
        <span className={`status-light light-${light}`} />
        <span className="status-value" title={title ?? value}>{value}</span>
        {clickable && <span className="status-row-chevron" aria-hidden="true">{externalLink ? '→' : expanded ? '▼' : '▶'}</span>}
      </td>
    </tr>
  )
}

function HealthIssueList({ issues }: { issues: ParsedIssue[] }) {
  return (
    <div className="sp-issues overview-health-issues" aria-label="System health issues">
      {issues.map(issue => (
        <div key={issue.key} className="sp-issue-row" title={issue.key} aria-label={`${issue.label}: ${issue.detail || issue.hint}`}>
          <span className={`sp-issue-icon sp-issue-${issue.severity}`}>⚠</span>
          <div className="sp-issue-body">
            <div className="sp-issue-title"><span className="sp-issue-label">{issue.label}</span>{issue.value && <span className="sp-issue-value">{issue.value}</span>}</div>
            <span className="sp-issue-hint">{issue.hint}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

const COVERAGE_LIGHT: Record<string, Light> = { fresh: 'green', stale: 'yellow', empty: 'muted', invalid: 'red', idle: 'muted' }
const COVERAGE_RANK: Record<string, number> = { invalid: 0, stale: 1, empty: 2, idle: 3, fresh: 4 }

function fmtCoverageAge(sec: number | null): string {
  if (sec == null) return 'never'
  if (sec < 60) return `${Math.round(sec)}s old`
  if (sec < 3600) return `${Math.floor(sec / 60)}m old`
  return `${Math.floor(sec / 3600)}h${Math.floor((sec % 3600) / 60)}m old`
}

function fmtCoverageMaxAge(sec: number): string {
  if (sec < 60) return `${sec}s`
  if (sec % 3600 === 0) return `${sec / 3600}h`
  return `${Math.floor(sec / 60)}m`
}

function DataCoverageDetail({ coverage }: { coverage: MarketDataCoverageReport }) {
  const domains = [...coverage.domains].sort((a, b) => (COVERAGE_RANK[a.state] ?? 5) - (COVERAGE_RANK[b.state] ?? 5))
  return (
    <div className="chd-panel">
      <div className="chd-title">Current data coverage</div>
      {domains.map((domain: MarketDataCoverageDomain) => {
        const light = COVERAGE_LIGHT[domain.state] || 'muted'
        return (
          <div key={domain.domain} className="chd-domain">
            <div className="chd-domain-head"><span className={`status-light light-${light}`} /><span className="chd-domain-name">{(domain.label || domain.domain).toUpperCase()}</span><span className={`chd-status chd-status-${light}`}>{domain.state}</span></div>
            <div className="chd-domain-meta">
              <span>{domain.scope === 'current_targets' ? `${domain.target_count} current targets · ${domain.missing_target_count} missing targets` : 'Global scope'}</span>
              <span>{domain.symbol_count} symbols · {domain.row_count.toLocaleString()} current rows · {domain.fresh_row_count} fresh · {domain.stale_row_count} stale · {domain.unavailable_row_count} unavailable</span>
              <span>{fmtCoverageAge(domain.age_sec)}{domain.max_age_sec ? ` · max ${fmtCoverageMaxAge(domain.max_age_sec)}` : ''}</span>
            </div>
            <div className="chd-domain-meta">
              <span>Historical inventory (all stored rows): {domain.inventory.state}</span>
              <span>{domain.inventory.symbol_count} symbols · {domain.inventory.row_count.toLocaleString()} rows · {domain.inventory.fresh_row_count} fresh · {domain.inventory.stale_row_count} stale · {domain.inventory.unavailable_row_count} unavailable</span>
              <span>{fmtCoverageAge(domain.inventory.age_sec)}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function ModeDetail({ shell }: { shell?: ShellBootstrap }) {
  const scheduler = shell?.scheduler
  const risk = shell?.risk?.status
  const killed = !!shell?.kill_switch?.active
  const items: Array<{ label: string; value: string; light: Light }> = [
    { label: 'LIVE MODE', value: humanize(shell?.live?.state || 'not available'), light: shell?.live?.state === 'live_full' ? 'green' : shell?.live?.enabled ? 'yellow' : 'muted' },
    { label: 'PERMISSION', value: humanize(shell?.permission?.mode || 'not available'), light: shell?.permission?.mode === 'Full' ? 'yellow' : 'muted' },
    { label: 'KILL SWITCH', value: killed ? 'active' : 'inactive', light: killed ? 'red' : 'muted' },
    { label: 'SCHEDULER', value: scheduler?.state || 'not available', light: shell?.gateway?.paused ? 'yellow' : scheduler?.running === false ? 'red' : scheduler ? 'green' : 'muted' },
  ]
  if (risk && risk !== 'not_available' && risk !== 'not_evaluated') items.push({ label: 'RISK', value: riskStateLabel(risk), light: risk === 'blocked' ? 'red' : risk === 'warning' ? 'yellow' : 'green' })
  return <div className="chd-panel"><div className="chd-title">Mode &amp; permissions</div>{items.map(item => <div key={item.label} className="chd-domain"><div className="chd-domain-head"><span className={`status-light light-${item.light}`} /><span className="chd-domain-name">{item.label}</span><span className={`chd-status chd-status-${item.light}`}>{item.value}</span></div></div>)}</div>
}

function EntrySummaryStrip({ summary }: { summary?: OverviewBootstrap['summary'] }) {
  const navigate = useNavigate()
  const active = summary?.active_entry_plans
  const needsPlanning = summary?.needs_planning
  const expiring = summary?.expiring_soon
  const dest = needsPlanning && needsPlanning > 0 ? '/universe?tab=candidates' : '/universe'
  return (
    <div className="ov-blotter-strip" role="link" tabIndex={0} onClick={() => navigate(dest)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); navigate(dest) } }} title="Open Entry Workbench">
      <span className="ov-blotter-label">Entry Workbench</span><span className="ov-blotter-item">Active Plans <b className="mono">{active ?? '—'}</b></span><span className="ov-blotter-item">Needs Planning <b className={`mono ${needsPlanning ? 'ov-blotter-yellow' : ''}`}>{needsPlanning ?? '—'}</b></span><span className="ov-blotter-item">Expiring Soon <b className={`mono ${expiring ? 'ov-blotter-yellow' : ''}`}>{expiring ?? '—'}</b></span><span className="ov-blotter-arrow">→</span>
    </div>
  )
}

export function isGoLiveScorecard(value: unknown): value is GoLiveScorecard {
  if (!value || typeof value !== 'object') return false
  const scorecard = value as Partial<GoLiveScorecard>
  if (typeof scorecard.composite_score !== 'number' || !Number.isFinite(scorecard.composite_score)) return false
  if (scorecard.traffic_light !== 'red' && scorecard.traffic_light !== 'yellow' && scorecard.traffic_light !== 'green') return false
  if (!Array.isArray(scorecard.criteria) || !scorecard.criteria.every(item => {
    if (!item || typeof item !== 'object') return false
    const criterion = item as Partial<GoLiveScorecard['criteria'][number]>
    return typeof criterion.key === 'string'
      && typeof criterion.label === 'string'
      && typeof criterion.detail === 'string'
      && typeof criterion.score === 'number'
      && Number.isFinite(criterion.score)
      && (criterion.status === 'pass' || criterion.status === 'warning' || criterion.status === 'fail')
  })) return false
  return Array.isArray(scorecard.blockers)
    && scorecard.blockers.every(blocker => typeof blocker === 'string')
    && typeof scorecard.computed_at === 'string'
    && scorecard.computed_at.length > 0
}

function GoLiveScoreCard() {
  const [expanded, setExpanded] = useState(false)
  const scorecardQuery = useQuery<GoLiveScorecard>({
    queryKey: ['go-live-scorecard'],
    queryFn: async () => {
      const value = await api.getGoLiveScorecard()
      if (!isGoLiveScorecard(value)) throw new Error('invalid scorecard response')
      return value
    },
    staleTime: 60_000,
    retry: 1,
    enabled: expanded,
  })
  const { data } = scorecardQuery
  const stale = Boolean(data && scorecardQuery.isRefetchError)
  const dataTone = data ? data.traffic_light : 'muted'
  const label = data
    ? stale ? 'STALE' : data.traffic_light === 'green' ? 'READY' : data.traffic_light === 'yellow' ? 'CAUTION' : 'NOT READY'
    : scorecardQuery.isError ? 'UNAVAILABLE' : scorecardQuery.isPending ? 'DEFERRED' : 'DEFERRED'
  return (
    <Card className="rail-card scorecard-panel">
      <Button type="button" variant="ghost" className="scorecard-header" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>
        <div className="scorecard-title"><h3>Go-Live</h3><StatusText tone={stale ? 'yellow' : dataTone}>{label}</StatusText></div><span className="text-muted text-xs">{expanded ? '▼' : '▶'}</span>
      </Button>
      {expanded && scorecardQuery.isPending && !data && <p className="text-muted text-sm" role="status">Loading qualification details…</p>}
      {expanded && scorecardQuery.isError && !data && <div className="scorecard-unavailable" role="status"><p className="text-muted text-sm">Qualification details are unavailable.</p><Button type="button" variant="secondary" onClick={() => void scorecardQuery.refetch()}>Retry</Button></div>}
      {expanded && data && <div className="scorecard-criteria">{stale && <p className="scorecard-stale" role="status">Refresh failed; showing the last computed scorecard. Last computed {fmtTimestamp(data.computed_at)}.</p>}{data.criteria.map(item => <div key={item.key} className="scorecard-row"><span className="scorecard-label">{item.label}</span><div className="scorecard-bar-bg"><Meter pct={item.score} className="scorecard-bar-fill" /></div><span className="scorecard-detail">{item.detail}</span></div>)}{data.blockers.length > 0 && <div className="scorecard-blockers"><span className="text-xs text-error">Blockers: {data.blockers.join(' · ')}</span></div>}{!stale && <p className="scorecard-meta">Last computed {fmtTimestamp(data.computed_at)}</p>}</div>}
    </Card>
  )
}
