import { Routes, Route, NavLink } from 'react-router-dom'
import { useState, useEffect } from 'react'
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
// ActivityTimeline temporarily hidden — content overlapped Overview's Agent Activity
// card (runs duplicated) and the global-shell strip lacked viewport-follow + click
// drill-down. To revive: restore the import + the <ActivityTimeline /> mount below,
// and rework as a viewport-fixed audit-only ticker (kill_switch / risk_blocked /
// config events; agent runs already covered by Agent Activity).
// import ActivityTimeline from './components/ActivityTimeline'
import CommandPalette from './components/CommandPalette'
import MarketTicker from './components/MarketTicker'

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
    ],
  },
  {
    group: 'Operate',
    items: [
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
const PERMISSION_MODES = ['ReadOnly', 'Research', 'PaperAuto', 'AllowEditing', 'Auto'] as const

function StatusBar() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['status'], queryFn: api.getStatus, refetchInterval: 10000 })
  const { data: ks } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch, refetchInterval: 10000 })
  const { data: runs = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns, refetchInterval: 10000 })

  // Tick every minute so heartbeat age re-renders even without new data
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 60000)
    return () => clearInterval(id)
  }, [])

  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const permMut = useMutation({
    mutationFn: (mode: string) => api.setPermissionMode(mode),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })

  if (!data) return <div className="topbar-status"><span className="dot dot-muted" />connecting…</div>

  const running = !!(data as any).gateway_running
  const paused = !!(data as any).paused
  const live = String((data as any).live_state || 'live_disabled')
  const liveOn = live !== 'live_disabled'
  const mode = String((data as any).exchange_mode || '')
  const exch = String((data as any).exchange || '')
  const perm = String((data as any).permission_mode || '')
  const modelProv = String((data as any).model_provider || '')
  const killed = !!ks?.active

  const latestRun = (runs as any[])[0]
  let heartbeatText = ''
  if (latestRun?.ended_at) {
    const mins = Math.floor((Date.now() - new Date(latestRun.ended_at).getTime()) / 60000)
    heartbeatText = mins < 1 ? '<1m' : `${mins}m`
  }

  // Three lights — left to right by criticality.
  const gwLight = running ? (paused ? 'yellow' : 'green') : 'red'
  const liveLight = liveOn ? 'yellow' : 'muted'
  const killLight = killed ? 'red' : 'muted'

  // Top-line state word + tone.
  const stateWord = killed ? 'KILL' : !running ? 'Stopped' : paused ? 'Paused' : 'Running'
  const stateTone = killed ? 'red' : !running ? 'red' : paused ? 'yellow' : 'green'

  return (
    <div className="topbar-status">
      {/* Block 1 — Status cluster (lights + heartbeat + state word). Hover → popover. */}
      <div className="status-cluster" tabIndex={0} aria-label="System status">
        <span className="status-cluster-lights" aria-hidden="true">
          <span className={`dot dot-${gwLight}`} />
          <span className={`dot dot-${liveLight}`} />
          <span className={`dot dot-${killLight}`} />
        </span>
        {heartbeatText && (
          <span className="status-cluster-hb mono" title="Time since last agent run">
            <span className="dot dot-green heartbeat" />{heartbeatText}
          </span>
        )}
        <span className={`status-cluster-state status-cluster-${stateTone}`}>{stateWord}</span>

        {/* Hover/focus popover — full status detail. */}
        <div className="status-popover" role="tooltip">
          <div className="status-popover-title">System Status</div>
          <table className="status-popover-table">
            <tbody>
              <tr>
                <th>Gateway</th>
                <td><span className={`dot dot-${gwLight}`} /> {running ? (paused ? 'paused' : 'running') : 'stopped'}</td>
              </tr>
              <tr>
                <th>Scheduler</th>
                <td><span className={`dot dot-${paused ? 'yellow' : 'green'}`} /> {paused ? 'paused' : 'active'}</td>
              </tr>
              <tr>
                <th>Exchange</th>
                <td className="mono">{exch || '—'}{mode ? ` · ${mode}` : ''}</td>
              </tr>
              {modelProv && (
                <tr>
                  <th>Model</th>
                  <td className="mono">{modelProv}</td>
                </tr>
              )}
              <tr>
                <th>Live</th>
                <td><span className={`dot dot-${liveLight}`} /> {live.replace('live_', '')}</td>
              </tr>
              <tr>
                <th>Permission</th>
                <td className="mono">{perm || '—'}</td>
              </tr>
              <tr>
                <th>Kill Switch</th>
                <td><span className={`dot dot-${killLight}`} /> {killed ? 'engaged' : 'inactive'}</td>
              </tr>
              {latestRun && (
                <tr>
                  <th>Last Run</th>
                  <td className="mono">{latestRun.agent_name}{heartbeatText ? ` · ${heartbeatText} ago` : ''}</td>
                </tr>
              )}
            </tbody>
          </table>
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
        onClick={() => paused ? resumeMut.mutate() : pauseMut.mutate()}
        title={paused ? 'Resume agents' : 'Pause agents'}
      >
        {paused ? '▶' : '⏸'}
      </button>
    </div>
  )
}

export default function App() {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const toggle = (g: string) => setCollapsed(c => ({ ...c, [g]: !c[g] }))

  return (
    <div className="layout">
      <CommandPalette />
      <aside className="sidebar">
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
                <NavLink key={n.to} to={n.to} end={n.to === '/'}>
                  {n.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <div className="content">
        <header className="topbar">
          {/* Bloomberg-style ticker replaces the breadcrumb (which duplicated brand+sidebar
              "you-are-here" cues). The active sidebar item + each page's PageHeader title
              cover navigation context; the topbar now adds market-state context instead. */}
          <MarketTicker />
          <StatusBar />
        </header>
        <main className="main">
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
            <Route path="/settings" element={<Settings />} />
            <Route path="/risk" element={<RiskControls />} />
            <Route path="/news" element={<NewsIntel />} />
          </Routes>
        </main>
        {/* <ActivityTimeline /> — temporarily hidden, see import comment above */}
      </div>
    </div>
  )
}
