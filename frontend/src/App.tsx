import { Routes, Route, NavLink } from 'react-router-dom'
import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './api/client'
import Overview from './pages/Overview'
import AgentActivity from './pages/AgentActivity'
import Learning from './pages/Learning'
import Proposals from './pages/Proposals'
import TradesPositions from './pages/TradesPositions'
import StrategyLifecycle from './pages/StrategyLifecycle'
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

export type NavItem = { to: string; label: string }
export type NavGroup = { group: string; items: NavItem[] }

// Nav grouped by the 5 workflow phases (docs/WEBUI_PAGE_MAP.md target IA):
// Observe -> Decide -> Execute -> Evolve -> Operate.
// NOTE: page MERGES (Positions+PaperTrades, AgentRuns+Opportunities, Dreaming+KB+Reports)
// are later build steps — until those merged pages exist, nav keeps the existing routes
// under their target phase rather than pointing at not-yet-built destinations.
export const NAV: NavGroup[] = [
  { group: '', items: [{ to: '/', label: 'Overview' }] },
  {
    group: 'Observe',
    items: [
      { to: '/charts', label: 'Markets' },
      { to: '/prediction-markets', label: 'Prediction Markets' },
      { to: '/universe', label: 'Universe' },
    ],
  },
  {
    group: 'Decide',
    items: [
      { to: '/proposals', label: 'Approval Desk' },
      { to: '/agent-activity', label: 'Agent Activity' },
    ],
  },
  {
    group: 'Execute',
    items: [
      { to: '/trades', label: 'Trades & Positions' },
    ],
  },
  {
    group: 'Evolve',
    items: [
      { to: '/strategies', label: 'Strategies' },
      { to: '/learning', label: 'Learning' },
      { to: '/serenity-lens', label: 'Serenity Lens' },
    ],
  },
  {
    group: 'Operate',
    items: [
      { to: '/command-center', label: 'Command Center' },
      { to: '/risk', label: 'Risk & Controls' },
      { to: '/settings', label: 'Settings' },
      { to: '/news', label: 'News Intel' },
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

function extractResumeError(e: any): string {
  return String(e?.message || 'failed')
}

function StatusBar() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['status'], queryFn: api.getStatus, refetchInterval: 10000 })
  const { data: operator } = useQuery({ queryKey: ['operator-status'], queryFn: api.getOperatorStatus, refetchInterval: 10000 })
  const { data: ks } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch, refetchInterval: 10000 })
  const { data: runs = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns, refetchInterval: 10000 })
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: api.getHealth, refetchInterval: 10000 })

  // Code drift = disk HEAD has moved past the boot commit. Surfaces the
  // "code updated, restart needed" signal that build_info.code_drift_issue
  // emits via /api/health.issues. Empty string when no drift.
  const driftIssue = (health?.issues || []).find(i => i.startsWith('code_updated_pending_restart:')) || ''
  const driftRange = driftIssue ? driftIssue.replace('code_updated_pending_restart:', '') : ''

  const [restarting, setRestarting] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

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
            qc.invalidateQueries()
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
  const perm = String(data.permission_mode || '')
  const modelProv = String(data.model_provider || '')
  const schedulerState = String(data.scheduler_state || (paused ? 'paused' : 'unknown'))
  const schedulerRunning = !!data.scheduler_running
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

  // Top-line state word + tone.
  const operatorStatus = String((operator as any)?.status || 'offline')
  const operatorMode = String((operator as any)?.mode || (paused ? 'Paused' : 'Observing'))
  const stateWord = killed
    ? 'KILL'
    : !running
      ? 'Stopped'
      : operatorStatus === 'stale'
        ? 'Stale'
        : paused
          ? 'Paused'
          : operatorMode
  const stateTone = killed
    ? 'red'
    : !running
      ? 'red'
      : operatorStatus === 'stale'
        ? 'yellow'
        : operatorStatus === 'offline'
          ? 'red'
          : paused
            ? 'yellow'
            : 'green'

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
          </div>

          {/* Section 2: Infrastructure */}
          <div className="sp-section">
            <div className="sp-section-label">Infrastructure</div>
            <div className="sp-row">
              <span className="sp-label">Exchange</span>
              <span className="sp-value">{exch || '—'}{mode ? ` · ${mode}` : ''}</span>
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
          </div>

          {/* Section 3: Operator */}
          <div className="sp-section">
            <div className="sp-section-label">Operator</div>
            <div className="sp-row">
              <span className="sp-label">Status</span>
              <span className="sp-value">{operatorStatus}</span>
            </div>
            <div className="sp-row">
              <span className="sp-label">Mode</span>
              <span className="sp-value">{operatorMode}</span>
            </div>
            {operatorMode === 'Working' && (operator as any)?.current_intent && (
              <div className="sp-row">
                <span className="sp-label">Intent</span>
                <span className="sp-value sp-value-wrap">{(operator as any).current_intent}</span>
              </div>
            )}
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

      {/* Block 2 — System plate (low-priority context, hides on narrow). */}
      <div className="system-plate mono" title="Exchange · Mode · Permission">
        {(exch || '—')}{mode ? `·${mode}` : ''}
        <button
          className="perm-mode-btn mono"
          onClick={() => {
            const idx = PERMISSION_MODES.indexOf(perm as any)
            const next = PERMISSION_MODES[(idx + 1) % PERMISSION_MODES.length]
            permMut.mutate(next)
          }}
          title={`Permission mode: ${perm} — click to cycle`}
          disabled={permMut.isPending}
        >
          {perm || '—'}
        </button>
      </div>

      {/* Block 3 — Pause/Resume action. */}
      <button
        className="topbar-btn secondary"
        onClick={() => { setPauseErr(''); paused ? resumeMut.mutate() : pauseMut.mutate() }}
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

  return (
    <div className="layout">
      <CommandPalette />
      <aside className={`sidebar${sidebarOpen ? ' open' : ''}`}>
        <div className="brand">
          <span className="brand-mark">◆</span>
          <span className="brand-name">ALETHEIA</span>
          <span className="brand-sub">trading os</span>
        </div>
        <nav>
          {NAV.map(({ group, items }) => (
            <div key={group || 'home'} className="nav-group">
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
            <Route path="/proposals" element={<Proposals />} />
            <Route path="/trades" element={<TradesPositions />} />
            {/* Legacy paths kept as redirects so existing cross-links/bookmarks resolve. */}
            <Route path="/paper-trades" element={<TradesPositions />} />
            <Route path="/positions" element={<TradesPositions />} />
            <Route path="/strategies" element={<StrategyLifecycle />} />
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
