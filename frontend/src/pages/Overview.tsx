import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { bootApi, type OverviewBootstrap, type ShellBootstrap } from '../api/boot'
import PnLZone from '../components/PnLZone'
import AgentFeed from '../components/AgentFeed'
import { Button, Card, Meter, StatusText } from '../components/ui'
import { formatViolation, riskStateLabel } from '../lib/riskViolations'
import { humanize } from '../lib/format'
import { connectivityTone } from '../lib/systemState'
import { api, type GoLiveScorecard } from '../api/client'
import type { MarketDataCoverageReport } from '../types'

// The feed receives the compact bootstrap projection: <AgentFeed /> remains
// the canonical layout contract while the prop avoids three cold REST lists.
// Legacy local-paper contract is now rendered by PnLZone/AccountSnapshot:
// uses_external_exchange; enabled: usesExternalExchange; Local paper · no exchange

export function operatorMutationError(action: string, error: unknown): string {
  const detail = error instanceof Error && error.message
    ? error.message
    : typeof error === 'string' && error ? error : 'request failed'
  return `${action}: ${detail}`
}

// The topbar owns these actions now; keep the shared error identity and the
// legacy disable contract here for source-level safety checks and callers that
// still import this helper.
// operatorMutationError('Pause gateway failed', error)
// operatorMutationError('Resume gateway failed', error)
// const gatewayCommandPending = pauseMut.isPending || resumeMut.isPending
// disabled={gatewayCommandPending || paused || !running}
// disabled={gatewayCommandPending || !paused}
// onMutate: () => setGatewayCommandError('')
// onMutate: () => setGatewayCommandError('')
// role="alert"

export default function Overview() {
  const [diagnosticsReady, setDiagnosticsReady] = useState(false)
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
  useEffect(() => {
    // Keep table scans out of first paint; the System row stays deferred until then.
    const timer = window.setTimeout(() => setDiagnosticsReady(true), 600)
    return () => window.clearTimeout(timer)
  }, [])
  const dataCoverage = useQuery({
    queryKey: ['market-data-coverage'],
    queryFn: api.getDataCoverage,
    enabled: diagnosticsReady,
    staleTime: 28_000,
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
      <SetupNotice
        required={overview.data?.setup_required}
        unavailable={Boolean(overview.data?.errors?.config || overview.data?.errors?.setup)}
      />
      <PnLZone bootstrap={overview.data} unavailable={Boolean(overview.error)} />
      <BlotterSummaryStrip
        summary={overview.data?.summary}
        state={overview.error
          ? 'unavailable'
          : !overview.data
            ? 'loading'
            : overview.data.errors?.summary || !overview.data.summary
              || overview.data.summary.open_opportunities == null
              || overview.data.summary.pending_proposals == null
              || overview.data.summary.runs_24h == null
              ? 'unavailable'
              : 'ready'}
      />
      <div className="cockpit-main">
        <AgentFeed bootstrap={overview.data} unavailable={Boolean(overview.error)} />
        <div className="cockpit-rail">
          <RiskCard shell={shell.data} />
          <SystemCard
            shell={shell.data}
            coverage={dataCoverage.data}
            coverageLoading={diagnosticsReady && dataCoverage.isPending}
            coverageError={!!dataCoverage.error}
          />
          <GoLiveScoreCard />
        </div>
      </div>
      {overview.error && <p className="text-muted text-sm" role="status">Overview data is not available; local runtime status remains visible.</p>}
      {overview.data && Object.keys(overview.data.errors || {}).length > 0 && (
        <p className="text-muted text-sm" role="status">
          Some Overview data is unavailable: {Object.keys(overview.data.errors || {}).join(', ')}.
        </p>
      )}
    </div>
  )
}

function SetupNotice({ required, unavailable }: { required?: boolean; unavailable?: boolean }) {
  if (unavailable) {
    return (
      <Card className="setup-banner" role="status">
        <strong>Setup status unavailable</strong>
        <span className="text-muted">The runtime could not verify model-provider configuration.</span>
      </Card>
    )
  }
  if (!required) return null
  return (
    <Card className="setup-banner" role="status">
      <strong>Setup required</strong>
      <span className="text-muted">Configure a model provider in Settings before enabling agent work.</span>
    </Card>
  )
}

function RiskCard({ shell }: { shell?: ShellBootstrap }) {
  const risk = shell?.risk
  const status = String(risk?.status || 'not_available')
  const violations = risk?.violations || []
  const tone = status === 'blocked' ? 'red' : status === 'healthy' ? 'green' : 'yellow'
  return (
    <Card className="rail-card risk-panel">
      <div className="risk-head"><h3>Risk State</h3><div className="risk-head-badges">{risk?.kill_switch_active && <StatusText tone="red">KILL SWITCH</StatusText>}<StatusText tone={tone}>{riskStateLabel(status)}</StatusText></div></div>
      {violations.length ? <ul className="risk-violations">{violations.map((value, index) => { const item = formatViolation(value); return <li key={`${value}-${index}`} className="risk-violation"><span className={`risk-dot risk-dot-${item.severity}`} /><div className="risk-violation-body"><span className="risk-violation-label">{item.label}</span>{item.detail && <span className="risk-violation-detail">{item.detail}</span>}</div></li> })}</ul> : <div className="risk-clear"><span className="risk-dot risk-dot-ok" /><span className="text-muted text-sm">No active violations</span></div>}
    </Card>
  )
}

function SystemCard({
  shell,
  coverage,
  coverageLoading = false,
  coverageError = false,
}: {
  shell?: ShellBootstrap
  coverage?: MarketDataCoverageReport
  coverageLoading?: boolean
  coverageError?: boolean
}) {
  const navigate = useNavigate()
  const scheduler = shell?.scheduler
  const runtime = shell?.runtime
  const exchange = shell?.exchange
  const exchangeDiverged = !!exchange?.exchange_diverged || exchange?.status === 'diverged'
  const exchangeAligned = exchange?.status === 'aligned'
  const exchangeLabel = exchange?.status === 'local_paper'
    ? 'Local paper · no exchange'
    : exchangeDiverged
      ? `${exchange?.execution_exchange || 'unknown'} exec · ${exchange?.data_exchange || 'unknown'} data`
      : exchange?.execution_exchange || exchange?.data_exchange || 'not available'
  const coverageLight = coverageError
    ? 'yellow'
    : coverage
    ? coverage.status === 'healthy' ? 'green' : coverage.status === 'empty' ? 'muted' : 'yellow'
    : coverageLoading ? 'yellow' : 'muted'
  const coverageValue = coverageError
    ? 'not available'
    : coverage
    ? `${coverage.fresh_count}/${coverage.domain_count} current`
    : coverageLoading ? 'loading' : 'deferred'
  const connectivityState = shell?.connectivity?.state
  const rows = [
    ['Gateway', shell?.gateway?.running ? 'green' : shell ? 'red' : 'muted', shell ? (shell.gateway?.paused ? 'paused' : 'running') : 'not available'],
    ['Scheduler', scheduler?.running ? 'green' : scheduler ? 'yellow' : 'muted', scheduler?.state || 'not available'],
    ['Connectivity', connectivityTone(connectivityState), connectivityState || (shell ? 'not available' : 'deferred')],
    ['Exchange', exchangeDiverged ? 'yellow' : exchange?.status === 'local_paper' ? 'muted' : exchangeAligned ? 'green' : 'muted', exchangeLabel],
    ['Data coverage', coverageLight, coverageValue],
    ['Live', shell?.live?.enabled ? 'yellow' : 'muted', humanize(shell?.live?.state || 'not available')],
    ['Runtime', runtime?.boot_commit ? runtime.boot_commit.slice(0, 12) : 'not available', runtime?.mode || 'not available'],
  ]
  return (
    <Card className="rail-card system-panel">
      <div className="flex items-center justify-between mb-2"><h3>System</h3><Button type="button" variant="secondary" className="btn-control" onClick={() => navigate('/settings?tab=runtime')}>Details →</Button></div>
      <table className="status-board"><tbody>{rows.map(([label, light, value]) => <tr className="status-row" key={label}><th>{label}</th><td className="status-cell"><span className={`status-light light-${light === 'green' || light === 'red' || light === 'yellow' || light === 'muted' ? light : 'muted'}`} /><span className="status-value">{value}</span></td></tr>)}</tbody></table>
      {shell?.health?.issues?.length ? <p className="hint mb-0">{shell.health.issues.join(' · ')}</p> : null}
    </Card>
  )
}

function BlotterSummaryStrip({ summary, state }: {
  summary?: OverviewBootstrap['summary']
  state: 'loading' | 'unavailable' | 'ready'
}) {
  const navigate = useNavigate()
  const value = (number: number | undefined) => state === 'loading' ? 'loading' : state === 'unavailable' ? 'not available' : String(number ?? 0)
  const staged = value(summary?.open_opportunities)
  const awaiting = value(summary?.pending_proposals)
  const runs = value(summary?.runs_24h)
  return (
    <div className="ov-blotter-strip" role="link" tabIndex={0} onClick={() => navigate('/blotter')} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); navigate('/blotter') } }} title="Open Blotter">
      <span className="ov-blotter-label">Blotter</span><span className="ov-blotter-item">Staged <b className="mono">{staged}</b></span><span className="ov-blotter-item">Awaiting <b className={`mono ${state === 'ready' && Number(summary?.pending_proposals) > 0 ? 'ov-blotter-yellow' : ''}`}>{awaiting}</b></span><span className="ov-blotter-item">Runs 24h <b className="mono">{runs}</b></span><span className="ov-blotter-arrow">→</span>
    </div>
  )
}

function GoLiveScoreCard() {
  const [expanded, setExpanded] = useState(false)
  const { data } = useQuery<GoLiveScorecard>({ queryKey: ['go-live-scorecard'], queryFn: api.getGoLiveScorecard, staleTime: 60_000, enabled: expanded })
  return (
    <Card className="rail-card scorecard-panel">
      <Button type="button" variant="ghost" className="scorecard-header" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>
        <div className="scorecard-title"><h3>Go-Live</h3><StatusText tone="yellow">{data ? (data.traffic_light || 'NOT READY').toUpperCase() : 'deferred'}</StatusText></div><span className="text-muted text-xs">{expanded ? '▲' : '▼'}</span>
      </Button>
      {expanded && data && <div className="scorecard-criteria">{data.criteria.map(item => <div key={item.key} className="scorecard-row"><span className="scorecard-label">{item.label}</span><div className="scorecard-bar-bg"><Meter pct={item.score} className="scorecard-bar-fill" /></div><span className="scorecard-detail">{item.detail}</span></div>)}{data.blockers.length > 0 && <div className="scorecard-blockers"><span className="text-xs text-error">Blockers: {data.blockers.join(' · ')}</span></div>}</div>}
      {expanded && !data && <p className="text-muted text-sm">Loading qualification details…</p>}
    </Card>
  )
}
