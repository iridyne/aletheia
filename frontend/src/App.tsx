import { Routes, Route, NavLink, Navigate } from 'react-router-dom'
import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './api/client'
import Overview from './pages/Overview'
import AgentActivity from './pages/AgentActivity'
import Learning from './pages/Learning'
import Blotter from './pages/Blotter'
import TradesPositions from './pages/TradesPositions'
import Charts from './pages/Charts'
import Settings from './pages/Settings'
import RiskControls from './pages/RiskControls'
import NewsIntel from './pages/NewsIntel'
import CommandCenter from './pages/CommandCenter'
import SerenityLens from './pages/SerenityLens'
import PredictionLens from './pages/PredictionLens'
import Universe from './pages/Universe'
// ActivityTimeline temporarily hidden — content overlapped Overview's Agent Activity
// card (runs duplicated) and the global-shell strip lacked viewport-follow + click
// drill-down. To revive: restore the import + the <ActivityTimeline /> mount below,
// and rework as a viewport-fixed audit-only ticker (kill_switch / risk_blocked /
// config events; agent runs already covered by Agent Activity).
// import ActivityTimeline from './components/ActivityTimeline'
import CommandPalette from './components/CommandPalette'
import MarketTicker from './components/MarketTicker'
import BackendConnectionGuard from './components/BackendConnectionGuard'
import FetchProgress from './components/FetchProgress'
import CollectorHealthDetails from './components/CollectorHealthDetails'
import type { InfraEvent } from './types'
import { deriveMasterState, getOperatorDisplayStatus } from './lib/systemState'
import { devPreviewLabel } from './dev-preview'
import { useRealtimeUpdates } from './lib/useRealtimeUpdates'

export type NavItem = { to: string; label: string }
export type NavGroup = { group: string; items: NavItem[] }

// Nav follows the owner-approved trading-desk IA:
// global entries -> Research -> Decision -> Portfolio -> global entries.
// Keep this list honest: items must point only at implemented routes.
export const NAV: NavGroup[] = [
  {
    group: '',
    items: [
      { to: '/', label: 'Overview' },
      { to: '/command-center', label: 'Command Center' },
    ],
  },
  {
    group: 'Research',
    items: [
      { to: '/universe', label: 'Universe Board' },
      { to: '/charts', label: 'Markets' },
      { to: '/prediction-markets', label: 'Prediction Markets' },
      // Serenity Lens is a supply-chain research/discovery path (a way to observe
      // the world for opportunities), not a learning/evolution loop — belongs here.
      { to: '/serenity-lens', label: 'Serenity Lens' },
      { to: '/news', label: 'News Intel' },
    ],
  },
  {
    group: 'Decision',
    items: [
      // Blotter (BLOTTER_DESIGN.md) merges the four Overview decision cards +
      // the legacy /proposals page into one sell-side-style workstation. The
      // /proposals route is preserved below as a legacy redirect target.
      { to: '/blotter', label: 'Blotter' },
      { to: '/agent-activity', label: 'Agent Activity' },
    ],
  },
  {
    group: 'Portfolio',
    items: [
      { to: '/trades', label: 'Trades & Positions' },
      { to: '/risk', label: 'Risk & Controls' },
    ],
  },
  {
    group: '',
    items: [
      { to: '/learning', label: 'Learning' },
      { to: '/settings', label: 'Settings' },
    ],
  },
]


/**
 * Topbar StatusBar — Bloomberg-style "instrument cluster" replacing the prior 5-pill row.
 *
 * Three semantic blocks (left → right):
 *   1. STATUS CLUSTER  — three dots (gateway / live / kill) + heartbeat + state word.
 *      Hover → popover with the full status detail (gateway, scheduler, exchange, model,
 *      live-state, permission, kill-switch, last-run age). The popover replaces the
 *      former pills' diffuse hover-titles with one focused panel.
 *   2. SYSTEM PLATE    — small mono gray line: `bitget · demo · Research` (low-priority
 *      context; hidden under 1200px).
 *   3. ACTION BUTTON   — Pause / Resume.
 *
 * Information density goal: same data as before, ~75px narrower, far less visual noise.
 */
const PERMISSION_MODES = ['Observe', 'Paper', 'Full'] as const
const STATUS_POLL_INTERVAL_ACTIVE_MS = 2000
const STATUS_POLL_INTERVAL_IDLE_MS = 15000
const RISK_POLL_INTERVAL_ACTIVE_MS = 3000
const RISK_POLL_INTERVAL_IDLE_MS = 30000

function adaptiveStatusPollMs(isRestarting: boolean): number {
  return isRestarting ? STATUS_POLL_INTERVAL_ACTIVE_MS : STATUS_POLL_INTERVAL_IDLE_MS
}

function adaptiveRiskPollMs(isRestarting: boolean): number {
  return isRestarting ? RISK_POLL_INTERVAL_ACTIVE_MS : RISK_POLL_INTERVAL_IDLE_MS
}

function extractResumeError(e: any): string {
  return String(e?.message || 'failed')
}

function parseIssue(raw: string): { label: string; value: string; tone: 'yellow' | 'red'; hint: string } {
  if (raw.startsWith('market_data_stale:')) return { label: 'market_data_stale', value: raw.split(':')[1], tone: 'yellow', hint: 'Feed will refresh automatically on next 5-min cycle. If stuck >30 min, restart the gateway.' }
  if (raw.startsWith('agent_failure_ratio:')) return { label: 'agent_failure_ratio', value: raw.split(':')[1], tone: 'red', hint: 'Multiple agents failing. Check gateway logs for errors.' }
  if (raw === 'kill_switch_active') return { label: 'kill_switch', value: 'active', tone: 'red', hint: 'All trading blocked. Go to Risk & Controls → Kill Switch to release.' }
  if (raw === 'trading_paused') return { label: 'trading', value: 'paused', tone: 'yellow', hint: 'Click the Resume button in the top bar to restart the trading loop.' }
  if (raw.startsWith('llm_unavailable:')) return { label: 'llm_unavailable', value: raw.split(':')[1], tone: 'red', hint: 'LLM provider unreachable. Check Settings → Model Provider, then click Resume.' }
  return { label: raw, value: '', tone: 'yellow', hint: '' }
}

function eventTone(e: InfraEvent): 'yellow' | 'red' {
  return e.severity === 'critical' ? 'red' : 'yellow'
}

function fmtEventTime(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

function StatusBar() {
  const qc = useQueryClient()
  const [restarting, setRestarting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const { data } = useQuery({
    queryKey: ['status'],
    queryFn: api.getStatus,
    refetchInterval: () => adaptiveStatusPollMs(restarting),
  })
  const { data: operator } = useQuery({
    queryKey: ['operator-status'],
    queryFn: api.getOperatorStatus,
    refetchInterval: () => adaptiveStatusPollMs(restarting),
  })
  const { data: ks } = useQuery({
    queryKey: ['kill-switch'],
    queryFn: api.getKillSwitch,
    refetchInterval: () => adaptiveStatusPollMs(restarting),
  })
  const { data: runs = [] } = useQuery({
    queryKey: ['agent-runs'],
    queryFn: api.getAgentRuns,
    refetchInterval: () => adaptiveStatusPollMs(restarting),
  })
  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: api.getHealth,
    refetchInterval: () => adaptiveStatusPollMs(restarting),
  })
  const { data: infraEvents } = useQuery({
    queryKey: ['infra-events'],
    queryFn: api.getInfraEvents,
    refetchInterval: () => adaptiveStatusPollMs(restarting),
  })
  const { data: collectorHealth } = useQuery({
    queryKey: ['collector-health'],
    queryFn: api.getCollectorHealth,
    refetchInterval: () => adaptiveStatusPollMs(restarting),
  })
  // #174 fix: /status doesn't carry risk_status — the topbar used to read
  // `data?.risk_status || data?.risk?.status` and always got '', so
  // deriveMasterState never saw CAUTION/OPERATIONAL and defaulted to
  // STANDBY-with-green even when risk was actively warning. Mirror
  // RiskControls.tsx:83-86 and query getRiskStatus directly.
  const { data: risk } = useQuery({
    queryKey: ['risk-status'],
    queryFn: api.getRiskStatus,
    refetchInterval: () => adaptiveRiskPollMs(restarting),
  })

  // Code drift = disk HEAD has moved past the boot commit. Surfaces the
  // "code updated, restart needed" signal that build_info.code_drift_issue
  // emits via /api/health.issues. Empty string when no drift.
  const driftIssue = (health?.issues || []).find(i => i.startsWith('code_updated_pending_restart:')) || ''
  const driftRange = driftIssue ? driftIssue.replace('code_updated_pending_restart:', '') : ''
  const healthIssues = (health?.issues || []).filter(i => !i.startsWith('code_updated_pending_restart:'))
  const recentInfraEvents = (infraEvents?.events || data?.infra_events || []).slice(0, 5)

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [])

  const startHealthPoll = (initialDelay: number) => {
    if (pollRef.current) clearInterval(pollRef.current)
    if (timeoutRef.current) clearTimeout(timeoutRef.current)
    timeoutRef.current = setTimeout(() => {
      pollRef.current = setInterval(async () => {
        try {
          const r = await fetch('/api/health')
          if (r.ok) {
            if (pollRef.current) clearInterval(pollRef.current)
            setRestarting(false)
            // Brief pause so the backend finishes loading heavy subsystems
            // (APScheduler, DB pool, etc.) before we fire all active queries.
            // Without this, the first request burst hits a warming backend and
            // the loading bar can stay stuck until the 15s fetch timeout fires.
            setTimeout(() => qc.invalidateQueries(), 1500)
          }
        } catch { /* still down */ }
      }, 2000)
      timeoutRef.current = setTimeout(() => {
        if (pollRef.current) clearInterval(pollRef.current)
        setRestarting(false)
      }, 30000)
    }, initialDelay)
  }

  const restartMut = useMutation({
    mutationFn: api.restartGateway,
    onMutate: () => setRestarting(true),
    onSuccess: (data: any) => {
      // Warm reload: worker stays alive ~2s before uvicorn kills it.
      // Cold restart: process dies 0.5s after response.
      const delay = data?.mode === 'warm' ? 3000 : 1000
      startHealthPoll(delay)
    },
    onError: () => {
      // fetch failed (process died before response) — poll immediately
      startHealthPoll(0)
    },
  })

  // Tick every minute so heartbeat age re-renders even without new data
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 60000)
    return () => clearInterval(id)
  }, [])

  const [pauseErr, setPauseErr] = useState('')
  const pauseMut = useMutation({
    mutationFn: api.pause,
    onSuccess: () => { setPauseErr(''); qc.invalidateQueries({ queryKey: ['status'] }) },
    onError: (e: any) => setPauseErr(extractResumeError(e)),
  })
  const resumeMut = useMutation({
    mutationFn: api.resume,
    onSuccess: () => { setPauseErr(''); qc.invalidateQueries({ queryKey: ['status'] }) },
    onError: (e: any) => setPauseErr(extractResumeError(e)),
  })
  const permMut = useMutation({
    mutationFn: (mode: string) => api.setPermissionMode(mode),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })

  if (!data) return <div className="topbar-status"><span className="dot dot-muted" />connecting…</div>

  const running = !!data.gateway_running
  const paused = !!data.paused
  const live = String(data.live_state || 'live_disabled')
  const liveOn = live !== 'live_disabled'
  const mode = String(data.exchange_mode || '')
  const exch = String(data.exchange || '')
  const usesExternalExchange = !!data.uses_external_exchange
  const exchangeLabel = usesExternalExchange ? `${exch || '—'}${mode ? ` · ${mode}` : ''}` : 'Local paper · no exchange'
  const perm = String(data.permission_mode || '')
  const modelProv = String(data.model_provider || '')
  const hasSchedulerActual = typeof data.scheduler_running === 'boolean'
  const schedulerState = hasSchedulerActual
    ? String(data.scheduler_state || 'unknown')
    : (paused ? 'paused' : data.scheduler_enabled ? 'configured' : 'disabled')
  const schedulerRunning = hasSchedulerActual
    ? !!data.scheduler_running
    : (!paused && !!data.scheduler_enabled)
  const killed = !!ks?.active

  const latestRun = (runs as any[])[0]
  let heartbeatText = ''
  if ((operator as any)?.last_seen_at) {
    const ts = String((operator as any).last_seen_at)
    const t = new Date(ts.endsWith('Z') || ts.includes('+') ? ts : ts + 'Z').getTime()
    const mins = Math.floor((Date.now() - t) / 60000)
    heartbeatText = mins < 1 ? '<1m' : `${mins}m`
  }

  // Three lights — left to right by criticality.
  const gwLight = running ? (paused ? 'yellow' : 'green') : 'red'
  const liveLight = liveOn ? 'yellow' : 'muted'
  const killLight = killed ? 'red' : 'muted'

  // Top-line state word + tone — single source via lib/systemState so this matches
  // the Risk console exactly (no more "Overview says yellow, Risk says blue" mismatches).
  // Risk verdict is fetched lazily (see riskStatus query above? — topbar doesn't query it).
  // We DO query risk-status in App's StatusBar via the popover content above; reuse it here.
  // (If absent the helper falls through to STANDBY, which matches old behavior at boot.)
  const operatorStatus = String((operator as any)?.status || 'offline')
  const operatorMode = String((operator as any)?.mode || (paused ? 'Paused' : 'Observing'))
  // #174 fix: pull real risk state from the dedicated /risk/status query
  // (matches RiskControls.tsx). Fall back to any inline value on /status
  // for backwards-compat, else '' so deriveMasterState reads STANDBY at boot.
  const riskState = String(
    (risk as any)?.status
      || (data as any)?.risk_status
      || (data as any)?.risk?.status
      || ''
  )
  const opDisplay = getOperatorDisplayStatus(operator as any)
  const master = deriveMasterState({
    gatewayRunning: running, killed, paused, riskState, operatorStatus,
    operatorMode,
  })
  const stateWord = master.word
  const stateTone = master.tone

  return (
    <div className="topbar-status">
      {/* Block 1 — Status cluster (lights + heartbeat + state word). Hover → popover. */}
      <div className="status-cluster" tabIndex={0} aria-label="System status">
        <span className="status-cluster-lights" aria-hidden="true">
          <span className={`dot dot-${gwLight}`} />
          <span className={`dot dot-${liveLight}`} />
          <span className={`dot dot-${killLight}`} />
          {driftIssue && <span className="dot dot-yellow" title={`Code drift: ${driftRange} — restart needed`} />}
        </span>
        {heartbeatText && (
          <span className="status-cluster-hb mono" title="Time since last operator heartbeat">
            <span className="dot dot-green heartbeat" />{heartbeatText}
          </span>
        )}
        <span className={`status-cluster-state status-cluster-${stateTone}`}>{stateWord}</span>

        {/* Hover/focus popover — full status detail, sectioned layout. */}
        <div className="status-popover" role="tooltip">
          <div className="status-popover-title">System Status</div>

          {/* Section 1: Health signals */}
          <div className="sp-section">
            <div className="sp-row">
              <span className={`dot dot-${gwLight}`} />
              <span className="sp-label">Gateway</span>
              <span className="sp-value">{running ? (paused ? 'paused' : 'running') : 'stopped'}</span>
            </div>
            <div className="sp-row">
              <span className={`dot dot-${schedulerRunning ? 'green' : paused ? 'yellow' : 'red'}`} />
              <span className="sp-label">Scheduler</span>
              <span className="sp-value">{schedulerState}</span>
            </div>
            <div className="sp-row">
              <span className={`dot dot-${liveLight}`} />
              <span className="sp-label">Live</span>
              <span className="sp-value">{live.replace('live_', '')}</span>
            </div>
            <div className="sp-row">
              <span className={`dot dot-${killLight}`} />
              <span className="sp-label">Kill Switch</span>
              <span className="sp-value">{killed ? 'engaged' : 'inactive'}</span>
            </div>
            {healthIssues.length > 0 && (
              <div className="sp-issues">
                {healthIssues.map(i => {
                  const { label, value, tone, hint } = parseIssue(i)
                  return (
                    <div key={i} className="sp-issue-row">
                      <span className={`sp-issue-icon sp-issue-${tone}`}>⚠</span>
                      <div className="sp-issue-body">
                        <div className="sp-issue-title">
                          <span className="sp-issue-label">{label}</span>
                          {value && <span className="sp-issue-value">{value}</span>}
                        </div>
                        {hint && <span className="sp-issue-hint">{hint}</span>}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* Section 2: Infrastructure */}
          <div className="sp-section">
            <div className="sp-section-label">Infrastructure</div>
            <div className="sp-row">
              <span className="sp-label">Exchange</span>
              <span className="sp-value">{exchangeLabel}</span>
            </div>
            <div className="sp-row">
              <span className="sp-label">Permission</span>
              <span className="sp-value">{perm || '—'}</span>
            </div>
            {modelProv && (
              <div className="sp-row">
                <span className="sp-label">Model</span>
                <span className="sp-value">{modelProv}</span>
              </div>
            )}
            {collectorHealth && (
              <CollectorHealthDetails health={collectorHealth} mode="compact" />
            )}
          </div>

          {recentInfraEvents.length > 0 && (
            <div className="sp-section">
              <div className="sp-section-label">Infra Events</div>
              <div className="sp-events">
                {recentInfraEvents.map((e, idx) => (
                  <div key={`${e.created_at}-${e.code}-${idx}`} className="sp-event-row">
                    <span className={`sp-issue-icon sp-issue-${eventTone(e)}`}>⚠</span>
                    <div className="sp-event-main">
                      <div className="sp-event-header">
                        <span className="sp-event-code">{e.code}</span>
                        {e.created_at && <span className="sp-event-ts">{fmtEventTime(e.created_at)}</span>}
                      </div>
                      <span className="sp-event-message">{e.message}</span>
                      {e.action && <span className="sp-event-action">{e.action}</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Section 3: Operator */}
          <div className="sp-section">
            <div className="sp-section-label">Operator</div>
            <div className="sp-row">
              <span className="sp-label">Status</span>
              <span className="sp-value">
                {opDisplay.state}
                {operatorStatus === 'stale' && <span className="sp-stale-hint"> — heartbeat delayed; usually self-recovers in 1–2 min. If stuck, restart the gateway.</span>}
              </span>
            </div>
            {opDisplay.blockedDetail && (
              <div className="sp-blocked-detail">
                {opDisplay.blockedDetail}
              </div>
            )}
            {(operator as any)?.current_intent && (
              <div className="sp-row">
                <span className="sp-label">Intent</span>
                <span className="sp-value sp-value-wrap">{(operator as any).current_intent}</span>
              </div>
            )}
            <div className="sp-row sp-row-debug">
              <span className="sp-label sp-label-muted">Mode</span>
              <span className="sp-value sp-value-muted">{opDisplay.debugMode}</span>
            </div>
            {(operator as any)?.session_id && (
              <div className="sp-row">
                <span className="sp-label">Session</span>
                <span className="sp-value">{(operator as any).session_id}</span>
              </div>
            )}
            {heartbeatText && (
              <div className="sp-row">
                <span className="sp-label">Heartbeat</span>
                <span className="sp-value">{heartbeatText} ago</span>
              </div>
            )}
            {latestRun && (
              <div className="sp-row">
                <span className="sp-label">Last Run</span>
                <span className="sp-value">{latestRun.agent_name}</span>
              </div>
            )}
            {(operator as any)?.last_error && (
              <div className="sp-row">
                <span className={`sp-issue-icon sp-issue-red`}>⚠</span>
                <span className="sp-issue-label">{(operator as any).last_error}</span>
              </div>
            )}
          </div>

          {/* Section 4: Code drift warning (conditional) */}
          {driftIssue && (
            <div className="sp-drift">
              <div className="sp-section-label sp-drift-label">⚠ Code Drift</div>
              <span className="sp-value">{driftRange}</span>
              <button
                className="topbar-btn secondary"
                onClick={() => restartMut.mutate()}
                disabled={restarting}
                title="Kill the current gateway process and cold-restart from disk. Use when git pull brought new commits that --reload didn't pick up, or when the process is in a bad state."
              >
                {restarting ? 'restarting…' : '↻ Cold Restart'}
              </button>
              <p className="sp-drift-hint">Hot-reload missed these commits. Cold restart loads the new code.</p>
            </div>
          )}
        </div>
      </div>

      {/* Block 2 — Permission cycle button (exchange/mode visible in popover). */}
      <button
        className="perm-mode-btn mono"
        onClick={() => {
          const idx = PERMISSION_MODES.indexOf(perm as any)
          if (idx < 0) return
          const next = PERMISSION_MODES[(idx + 1) % PERMISSION_MODES.length]
          if (next === 'Full' && !confirm('Switch to Full permission mode? This enables live order execution.')) return
          permMut.mutate(next)
        }}
        title={`Permission mode: ${perm} — click to cycle`}
        disabled={permMut.isPending}
      >
        {perm || '—'}
      </button>

      {/* Block 3 — Pause/Resume action. */}
      <button
        className="topbar-btn secondary"
        onClick={() => { setPauseErr(''); if (paused) { resumeMut.mutate() } else { pauseMut.mutate() } }}
        disabled={pauseMut.isPending || resumeMut.isPending}
        title={paused ? 'Resume agents' : 'Pause agents'}
      >
        {(pauseMut.isPending || resumeMut.isPending) ? '…' : paused ? '▶' : '⏸'}
      </button>
      {pauseErr && <span className="topbar-err" title={pauseErr}>{pauseErr}</span>}
    </div>
  )
}

export default function App() {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const toggle = (g: string) => setCollapsed(c => ({ ...c, [g]: !c[g] }))
  const previewLabel = devPreviewLabel()

  useRealtimeUpdates()

  return (
    <div className="layout">
      <FetchProgress />
      <CommandPalette />
      <aside className={`sidebar${sidebarOpen ? ' open' : ''}`}>
        <div className="brand">
          <span className="brand-mark">◆</span>
          <span className="brand-name">ALETHEIA</span>
          <span className="brand-sub">trading os</span>
        </div>
        <nav>
          {NAV.map(({ group, items }) => (
            <div key={group || items.map(i => i.to).join('|')} className="nav-group">
              {group && (
                <button className="nav-group-label" onClick={() => toggle(group)}>
                  <span>{group}</span>
                  <span className={`chev ${collapsed[group] ? 'chev-closed' : ''}`}>▾</span>
                </button>
              )}
              {!collapsed[group] && items.map(n => (
                <NavLink key={n.to} to={n.to} end={n.to === '/'} onClick={() => setSidebarOpen(false)}>
                  {n.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <div className="content">
        <header className="topbar">
          {/* Hamburger for mobile sidebar toggle (L1) */}
          <button className="topbar-btn hamburger" onClick={() => setSidebarOpen(o => !o)} aria-label="Toggle menu">☰</button>
          {/* Bloomberg-style ticker replaces the breadcrumb (which duplicated brand+sidebar
              "you-are-here" cues). The active sidebar item + each page's PageHeader title
              cover navigation context; the topbar now adds market-state context instead. */}
          <MarketTicker />
          {previewLabel && <span className="dev-preview-banner">{previewLabel}</span>}
          <StatusBar />
        </header>
        <main className="main">
          <BackendConnectionGuard />
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/agent-activity" element={<AgentActivity />} />
            {/* Legacy paths kept as redirects so existing cross-links/bookmarks resolve. */}
            <Route path="/agent-runs" element={<AgentActivity />} />
            <Route path="/opportunities" element={<AgentActivity />} />
            <Route path="/learning" element={<Learning />} />
            <Route path="/dreaming" element={<Learning />} />
            <Route path="/knowledge" element={<Learning />} />
            <Route path="/reports" element={<Learning />} />
            <Route path="/blotter" element={<Blotter />} />
            {/* /proposals fully replaced by /blotter (2026-07-18). Legacy
                external links, bookmarks, and any lingering in-app navigate()
                calls redirect transparently. The old pages/Proposals.tsx has
                been deleted; its only outward-facing export
                (ProposalRationaleDetail) moved to
                components/ProposalRationaleDetail.tsx. */}
            <Route path="/proposals" element={<Navigate to="/blotter" replace />} />
            <Route path="/trades" element={<TradesPositions />} />
            {/* Legacy paths kept as redirects so existing cross-links/bookmarks resolve. */}
            <Route path="/paper-trades" element={<TradesPositions />} />
            <Route path="/positions" element={<TradesPositions />} />
            <Route path="/charts" element={<Charts />} />
            <Route path="/prediction-markets" element={<PredictionLens />} />
            <Route path="/command-center" element={<CommandCenter />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/risk" element={<RiskControls />} />
            <Route path="/news" element={<NewsIntel />} />
            <Route path="/serenity-lens" element={<SerenityLens />} />
            <Route path="/universe" element={<Universe />} />
          </Routes>
        </main>
        {/* <ActivityTimeline /> — temporarily hidden, see import comment above */}
      </div>
    </div>
  )
}
