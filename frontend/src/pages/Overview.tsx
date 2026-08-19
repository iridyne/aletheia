import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { bootApi, type OverviewBootstrap, type ShellBootstrap } from '../api/boot'
import PnLZone from '../components/PnLZone'
import AgentFeed from '../components/AgentFeed'
import { Button, Card, Meter, StatusText } from '../components/ui'
import { formatViolation, riskStateLabel } from '../lib/riskViolations'
import { humanize } from '../lib/format'
import { api, type GoLiveScorecard } from '../api/client'

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
      <BlotterSummaryStrip summary={overview.data?.summary} />
      <div className="cockpit-main">
        <AgentFeed bootstrap={overview.data} />
        <div className="cockpit-rail">
          <RiskCard shell={shell.data} />
          <SystemCard shell={shell.data} coverage={overview.data?.coverage} />
          <GoLiveScoreCard />
        </div>
      </div>
      {overview.error && <p className="text-muted text-sm" role="status">Overview data is not available; local runtime status remains visible.</p>}
    </div>
  )
}

function SetupNotice({ required }: { required?: boolean }) {
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
  const tone = !shell ? 'muted' : status === 'blocked' ? 'red' : status === 'healthy' ? 'green' : 'yellow'
  return (
    <Card className="rail-card risk-panel">
      <div className="risk-head"><h3>Risk State</h3><div className="risk-head-badges">{risk?.kill_switch_active && <StatusText tone="red">KILL SWITCH</StatusText>}<StatusText tone={tone}>{riskStateLabel(status)}</StatusText></div></div>
      {violations.length ? <ul className="risk-violations">{violations.map((value, index) => { const item = formatViolation(value); return <li key={`${value}-${index}`} className="risk-violation"><span className={`risk-dot risk-dot-${item.severity}`} /><div className="risk-violation-body"><span className="risk-violation-label">{item.label}</span>{item.detail && <span className="risk-violation-detail">{item.detail}</span>}</div></li> })}</ul> : <div className="risk-clear"><span className="risk-dot risk-dot-ok" /><span className="text-muted text-sm">No active violations</span></div>}
    </Card>
  )
}

function SystemCard({ shell, coverage }: { shell?: ShellBootstrap; coverage?: OverviewBootstrap['coverage'] }) {
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
  const coverageAvailable = coverage?.availability === 'available'
  const rows = [
    ['Gateway', shell?.gateway?.running ? 'green' : shell ? 'red' : 'muted', shell ? (shell.gateway?.paused ? 'paused' : 'running') : 'not available'],
    ['Scheduler', scheduler?.running ? 'green' : scheduler ? 'yellow' : 'muted', scheduler?.state || 'not available'],
    ['Connectivity', shell?.connectivity?.state === 'reachable' ? 'green' : 'muted', shell?.connectivity?.state || 'deferred'],
    ['Exchange', exchangeDiverged ? 'yellow' : exchange?.status === 'local_paper' ? 'muted' : exchangeAligned ? 'green' : 'muted', exchangeLabel],
    ['Data coverage', coverageAvailable ? 'green' : coverage ? 'yellow' : 'muted', coverageAvailable ? 'available' : coverage ? 'not available' : 'deferred'],
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

function BlotterSummaryStrip({ summary }: { summary?: OverviewBootstrap['summary'] }) {
  const navigate = useNavigate()
  const staged = summary?.open_opportunities
  const awaiting = summary?.pending_proposals
  const runs = summary?.runs_24h
  return (
    <div className="ov-blotter-strip" role="link" tabIndex={0} onClick={() => navigate('/blotter')} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); navigate('/blotter') } }} title="Open Blotter">
      <span className="ov-blotter-label">Blotter</span><span className="ov-blotter-item">Staged <b className="mono">{staged ?? '—'}</b></span><span className="ov-blotter-item">Awaiting <b className={`mono ${awaiting ? 'ov-blotter-yellow' : ''}`}>{awaiting ?? '—'}</b></span><span className="ov-blotter-item">Runs 24h <b className="mono">{runs ?? '—'}</b></span><span className="ov-blotter-arrow">→</span>
    </div>
  )
}

function GoLiveScoreCard() {
  const [expanded, setExpanded] = useState(false)
  const { data } = useQuery<GoLiveScorecard>({ queryKey: ['go-live-scorecard'], queryFn: api.getGoLiveScorecard, staleTime: 60_000, enabled: expanded })
  return (
    <Card className="rail-card scorecard-panel">
      <Button type="button" variant="ghost" className="scorecard-header" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>
        <div className="scorecard-title"><h3>Go-Live</h3><StatusText tone={data ? data.traffic_light : 'muted'}>{data ? (data.traffic_light || 'NOT READY').toUpperCase() : 'deferred'}</StatusText></div><span className="text-muted text-xs">{expanded ? '▲' : '▼'}</span>
      </Button>
      {expanded && data && <div className="scorecard-criteria">{data.criteria.map(item => <div key={item.key} className="scorecard-row"><span className="scorecard-label">{item.label}</span><div className="scorecard-bar-bg"><Meter pct={item.score} className="scorecard-bar-fill" /></div><span className="scorecard-detail">{item.detail}</span></div>)}{data.blockers.length > 0 && <div className="scorecard-blockers"><span className="text-xs text-error">Blockers: {data.blockers.join(' · ')}</span></div>}</div>}
      {expanded && !data && <p className="text-muted text-sm">Loading qualification details…</p>}
    </Card>
  )
}
