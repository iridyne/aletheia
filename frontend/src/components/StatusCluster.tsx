import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import CollectorHealthDetails from './CollectorHealthDetails'
import type { RealtimeStatus } from '../lib/useRealtimeUpdates'
import type { ShellBootstrap } from '../api/boot'
import type { InfraEvent } from '../types'
import { deriveMasterState, getOperatorDisplayStatus } from '../lib/systemState'
import { safeErrorText } from '../lib/mutationErrors'
import { parseHealthIssue } from './health-issues'
import { useConfirm } from './ui'

const PERMISSION_MODES = ['Observe', 'Paper', 'Full'] as const

function eventTone(event: InfraEvent): 'yellow' | 'red' {
  return event.severity === 'critical' ? 'red' : 'yellow'
}

function eventPriority(event: InfraEvent, connectivityState: string): number {
  if (event.code === 'external_connectivity_unavailable') return connectivityState === 'unavailable' ? 0 : 5
  if (event.severity === 'critical') return 1
  if (event.code === 'external_connectivity_restored') return 2
  if (event.severity === 'warning') return 3
  return 4
}

function fmtEventTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

function heartbeatAge(operator: any, fallback?: ShellBootstrap['operator']): string {
  const age = operator?.heartbeat_age_sec ?? fallback?.heartbeat_age_sec
  if (typeof age === 'number' && Number.isFinite(age)) {
    const minutes = Math.floor(age / 60)
    return minutes < 1 ? '<1m' : `${minutes}m`
  }
  const raw = operator?.last_seen_at ?? fallback?.last_seen_at
  if (!raw) return ''
  const timestamp = String(raw)
  const parsed = new Date(timestamp.endsWith('Z') || timestamp.includes('+') ? timestamp : `${timestamp}Z`).getTime()
  if (Number.isNaN(parsed)) return ''
  const minutes = Math.floor((Date.now() - parsed) / 60000)
  return minutes < 1 ? '<1m' : `${minutes}m`
}

function exchangeLabel(data: ShellBootstrap, status: any): string {
  if (status) {
    const mode = String(status.exchange_mode || '')
    const exchange = String(status.exchange || '')
    return status.uses_external_exchange ? `${exchange || '—'}${mode ? ` · ${mode}` : ''}` : 'Local paper · no exchange'
  }
  const exchange = data.exchange?.execution_exchange || data.exchange?.data_exchange
  if (exchange) return `${exchange}${data.exchange?.paper_execution_mode ? ` · ${data.exchange.paper_execution_mode}` : ''}`
  return data.exchange?.paper_execution_mode ? `Local paper · ${data.exchange.paper_execution_mode}` : 'Local paper · no exchange'
}

function statusLight(state: string | undefined): 'green' | 'yellow' | 'red' | 'muted' {
  return state === 'reachable' ? 'green' : state === 'unavailable' ? 'red' : state === 'recovering' ? 'yellow' : 'muted'
}

export default function StatusCluster({
  data,
  realtime,
}: {
  data?: ShellBootstrap
  realtime: RealtimeStatus
}) {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [detailsRequested, setDetailsRequested] = useState(false)
  const [error, setError] = useState('')
  const paused = !!data?.gateway?.paused
  const running = !!data?.gateway?.running
  const permission = String(data?.permission?.mode || '')

  // Keep the cold graph small: the old popover's detail fan-out starts only
  // when an operator actually hovers or focuses the status cluster.
  const detailOptions = { enabled: detailsRequested, staleTime: 15_000 }
  const statusQuery = useQuery({ queryKey: ['status'], queryFn: api.getStatus, ...detailOptions })
  const operatorQuery = useQuery({ queryKey: ['operator-status'], queryFn: api.getOperatorStatus, ...detailOptions })
  const killSwitchQuery = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch, ...detailOptions })
  const runsQuery = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns, ...detailOptions })
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: api.getHealth, ...detailOptions })
  const infraEventsQuery = useQuery({ queryKey: ['infra-events'], queryFn: api.getInfraEvents, ...detailOptions })
  const collectorHealthQuery = useQuery({ queryKey: ['collector-health'], queryFn: api.getCollectorHealth, ...detailOptions })
  const riskQuery = useQuery({ queryKey: ['risk-status'], queryFn: api.getRiskStatus, ...detailOptions })

  const status = statusQuery.data
  const operator = operatorQuery.data ?? data?.operator
  const killed = killSwitchQuery.data?.active ?? data?.kill_switch?.active ?? false
  const live = String(data?.live?.state || 'live_disabled')
  const liveOn = data?.live?.enabled ?? live !== 'live_disabled'
  const connectivityState = status?.external_connectivity?.state ?? data?.connectivity?.state ?? 'not_available'
  const schedulerRunning = data?.scheduler?.running ?? (!paused && !!data?.scheduler?.enabled)
  const schedulerState = data?.scheduler?.state || (paused ? 'paused' : data?.scheduler?.enabled ? 'configured' : 'disabled')
  const healthIssues = (healthQuery.data?.issues ?? data?.health?.issues ?? []).filter(issue => !issue.startsWith('code_updated_pending_restart:'))
  const driftIssue = (healthQuery.data?.issues ?? data?.health?.issues ?? []).find(issue => issue.startsWith('code_updated_pending_restart:')) || ''
  const driftRange = driftIssue.replace('code_updated_pending_restart:', '')
  const recentInfraEvents = [...(infraEventsQuery.data?.events ?? status?.infra_events ?? [])]
    .sort((a, b) => eventPriority(a, connectivityState) - eventPriority(b, connectivityState) || Date.parse(b.created_at) - Date.parse(a.created_at))
    .slice(0, 5)
  const heartbeatText = heartbeatAge(operator, data?.operator)
  const operatorStatus = String(operator?.status || data?.operator?.status || 'offline')
  const operatorMode = String(operator?.mode || data?.operator?.mode || (paused ? 'Paused' : 'Observing'))
  const riskState = String(riskQuery.data?.status || data?.risk?.status || '')
  const master = deriveMasterState({
    gatewayRunning: running,
    killed,
    paused,
    riskState,
    operatorStatus,
    operatorMode,
  })
  const opDisplay = getOperatorDisplayStatus(operator as any)
  const realtimeTone = realtime.state === 'connected'
    ? 'green'
    : realtime.state === 'polling_only' || realtime.state === 'reconnecting'
      ? 'yellow'
      : 'muted'
  const realtimeLabel = realtime.state === 'connected'
    ? 'push'
    : realtime.state === 'polling_only'
      ? 'poll'
      : realtime.state === 'reconnecting'
        ? 'retry'
        : realtime.state === 'connecting'
          ? 'ws…'
          : 'off'
  const realtimeTitle = `Realtime transport: ${realtime.state}; ${realtime.malformedFrameCount} malformed; ${realtime.unknownCategoryCount} unknown categories`
  const connectivityLight = statusLight(connectivityState)
  const gwLight = running ? (paused ? 'yellow' : 'green') : 'red'
  const liveLight = liveOn ? 'yellow' : 'muted'
  const killLight = killed ? 'red' : 'muted'
  const latestRun = (runsQuery.data || [])[0]

  const pause = useMutation({
    mutationFn: paused ? api.resume : api.pause,
    onMutate: () => setError(''),
    onSuccess: async () => {
      await qc.refetchQueries({ queryKey: ['ui-shell-bootstrap'] }, { throwOnError: true })
      setError('')
    },
    onError: (e: Error) => setError(safeErrorText(e)),
  })
  const setMode = useMutation({
    mutationFn: (mode: string) => api.setPermissionMode(mode),
    onMutate: () => setError(''),
    onSuccess: async () => {
      await qc.refetchQueries({ queryKey: ['ui-shell-bootstrap'] }, { throwOnError: true })
      setError('')
    },
    onError: (e: Error) => setError(safeErrorText(e)),
  })
  const restart = useMutation({
    mutationFn: api.restartGateway,
    onMutate: () => setError(''),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['status'] })
      void qc.refetchQueries({ queryKey: ['ui-shell-bootstrap'] })
    },
    onError: (e: Error) => setError(safeErrorText(e)),
  })
  const nextMode = async () => {
    const index = PERMISSION_MODES.indexOf(permission as typeof PERMISSION_MODES[number])
    if (index < 0) return
    const next = PERMISSION_MODES[(index + 1) % PERMISSION_MODES.length]
    if (next === 'Full' && !(await confirm({
      title: 'Switch to Full permission mode?',
      description: 'This enables live order execution.',
      confirmLabel: 'Switch to Full',
      variant: 'danger',
    }))) return
    setMode.mutate(next)
  }
  const requestDetails = () => setDetailsRequested(true)

  if (!data) {
    return (
      <div className="topbar-status">
        <span className="dot dot-muted" />connecting…
        <span className="realtime-state mono" title={realtimeTitle}>
          <span className={`dot dot-${realtimeTone}`} />{realtimeLabel}
        </span>
      </div>
    )
  }

  return (
    <div className="topbar-status">
      <div className="status-cluster" tabIndex={0} aria-label="System status" onMouseEnter={requestDetails} onFocus={requestDetails}>
        <span className="status-cluster-lights" aria-hidden="true">
          <span className={`dot dot-${gwLight}`} />
          <span className={`dot dot-${connectivityLight}`} />
          <span className={`dot dot-${liveLight}`} />
          <span className={`dot dot-${killLight}`} />
          {driftIssue && <span className="dot dot-yellow" title={`Code drift: ${driftRange} — restart needed`} />}
        </span>
        {heartbeatText && (
          <span className="status-cluster-hb mono" title="Time since last operator heartbeat">
            <span className="dot dot-green heartbeat" />{heartbeatText}
          </span>
        )}
        <span className="realtime-state mono" aria-label={`Realtime transport: ${realtime.state}`} title={realtimeTitle}>
          <span className={`dot dot-${realtimeTone}`} />{realtimeLabel}
        </span>
        <span className={`status-cluster-state status-cluster-${master.tone}`}>{master.word}</span>

        <div className="status-popover" role="tooltip">
          <div className="status-popover-title">System Status</div>

          <div className="sp-section">
            <div className="sp-row"><span className={`dot dot-${gwLight}`} /><span className="sp-label">Gateway</span><span className="sp-value">{running ? (paused ? 'paused' : 'running') : 'stopped'}</span></div>
            <div className="sp-row"><span className={`dot dot-${schedulerRunning ? 'green' : paused ? 'yellow' : 'red'}`} /><span className="sp-label">Scheduler</span><span className="sp-value">{schedulerState}</span></div>
            <div className="sp-row"><span className={`dot dot-${liveLight}`} /><span className="sp-label">Live</span><span className="sp-value">{live.replace('live_', '')}</span></div>
            <div className="sp-row"><span className={`dot dot-${killLight}`} /><span className="sp-label">Kill Switch</span><span className="sp-value">{killed ? 'engaged' : 'inactive'}</span></div>
            {healthIssues.length > 0 && (
              <div className="sp-issues">
                {healthIssues.map(issue => {
                  const parsed = parseHealthIssue(issue)
                  return (
                    <div key={issue} className="sp-issue-row" title={parsed.key} aria-label={`${parsed.label}: ${parsed.detail || parsed.hint}`}>
                      <span className={`sp-issue-icon sp-issue-${parsed.severity}`}>⚠</span>
                      <div className="sp-issue-body">
                        <div className="sp-issue-title"><span className="sp-issue-label">{parsed.label}</span>{parsed.value && <span className="sp-issue-value">{parsed.value}</span>}</div>
                        {parsed.hint && <span className="sp-issue-hint">{parsed.hint}</span>}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          <div className="sp-section">
            <div className="sp-section-label">Infrastructure</div>
            <div className="sp-row"><span className={`dot dot-${connectivityLight}`} /><span className="sp-label">Connectivity</span><span className="sp-value">{connectivityState}</span></div>
            <div className="sp-row"><span className={`dot dot-${realtimeTone}`} /><span className="sp-label">Realtime transport</span><span className="sp-value">{realtime.state}{realtime.lastFrameAt && ` · last ${fmtEventTime(new Date(realtime.lastFrameAt).toISOString())}`}{(realtime.malformedFrameCount > 0 || realtime.unknownCategoryCount > 0) && ` · ${realtime.malformedFrameCount} malformed · ${realtime.unknownCategoryCount} unknown`}</span></div>
            <div className="sp-row"><span className="sp-label">Exchange</span><span className="sp-value">{exchangeLabel(data, status)}</span></div>
            <div className="sp-row"><span className="sp-label">Permission</span><span className="sp-value">{permission || '—'}</span></div>
            {(status?.model_provider || operator?.model_provider) && <div className="sp-row"><span className="sp-label">Model</span><span className="sp-value">{status?.model_provider || operator?.model_provider}</span></div>}
            {collectorHealthQuery.data && <CollectorHealthDetails health={collectorHealthQuery.data} mode="compact" />}
            {detailsRequested && statusQuery.isPending && <div className="sp-row"><span className="sp-label">Diagnostics</span><span className="sp-value">loading…</span></div>}
            {detailsRequested && statusQuery.isError && <div className="sp-row"><span className="sp-label">Diagnostics</span><span className="sp-value">not available</span></div>}
          </div>

          {recentInfraEvents.length > 0 && (
            <div className="sp-section">
              <div className="sp-section-label">Infra Events</div>
              <div className="sp-events">
                {recentInfraEvents.map((event, index) => (
                  <div key={`${event.created_at}-${event.code}-${index}`} className="sp-event-row">
                    <span className={`sp-issue-icon sp-issue-${eventTone(event)}`}>⚠</span>
                    <div className="sp-event-main">
                      <div className="sp-event-header"><span className="sp-event-code">{event.code}</span>{event.created_at && <span className="sp-event-ts">{fmtEventTime(event.created_at)}</span>}</div>
                      <span className="sp-event-message">{event.message}</span>
                      {event.action && <span className="sp-event-action">{event.action}</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="sp-section">
            <div className="sp-section-label">Operator</div>
            <div className="sp-row"><span className="sp-label">Status</span><span className="sp-value">{opDisplay.state}{operatorStatus === 'stale' && <span className="sp-stale-hint"> — heartbeat delayed; usually self-recovers in 1–2 min. If stuck, restart the gateway.</span>}</span></div>
            {opDisplay.blockedDetail && <div className="sp-blocked-detail">{opDisplay.blockedDetail}</div>}
            {operator?.current_intent && <div className="sp-row"><span className="sp-label">Intent</span><span className="sp-value sp-value-wrap">{operator.current_intent}</span></div>}
            <div className="sp-row sp-row-debug"><span className="sp-label sp-label-muted">Mode</span><span className="sp-value sp-value-muted">{opDisplay.debugMode}</span></div>
            {operator?.session_id && <div className="sp-row"><span className="sp-label">Session</span><span className="sp-value">{operator.session_id}</span></div>}
            {heartbeatText && <div className="sp-row"><span className="sp-label">Heartbeat</span><span className="sp-value">{heartbeatText} ago</span></div>}
            {latestRun && <div className="sp-row"><span className="sp-label">Last Run</span><span className="sp-value">{latestRun.agent_name}</span></div>}
            {operator?.last_error && <div className="sp-row"><span className="sp-issue-icon sp-issue-red">⚠</span><span className="sp-issue-label">{operator.last_error}</span></div>}
          </div>

          {driftIssue && (
            <div className="sp-drift">
              <div className="sp-section-label sp-drift-label">⚠ Code Drift</div>
              <span className="sp-value">{driftRange}</span>
              <button className="topbar-btn secondary" onClick={() => restart.mutate()} disabled={restart.isPending} title="Kill the current gateway process and cold-restart from disk. Use when git pull brought new commits that --reload didn't pick up, or when the process is in a bad state.">
                {restart.isPending ? 'restarting…' : '↻ Cold Restart'}
              </button>
              <p className="sp-drift-hint">Hot-reload missed these commits. Cold restart loads the new code.</p>
            </div>
          )}
        </div>
      </div>

      <button className="perm-mode-btn mono" onClick={nextMode} disabled={!permission || setMode.isPending} title={`Permission mode: ${permission} — click to cycle`}>
        {permission || '—'}
      </button>
      <button className="topbar-btn secondary" onClick={() => pause.mutate()} disabled={!running || pause.isPending} title={paused ? 'Resume agents' : 'Pause agents'}>
        {pause.isPending ? '…' : paused ? '▶' : '⏸'}
      </button>
      {error && <span className="topbar-err" title={error}>{error}</span>}
    </div>
  )
}
