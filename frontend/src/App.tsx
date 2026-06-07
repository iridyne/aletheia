import { Routes, Route, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './api/client'
import Overview from './pages/Overview'
import AgentRuns from './pages/AgentRuns'
import Dreaming from './pages/Dreaming'
import KnowledgeBase from './pages/KnowledgeBase'
import Opportunities from './pages/Opportunities'
import Proposals from './pages/Proposals'
import PaperTrades from './pages/PaperTrades'
import Positions from './pages/Positions'
import StrategyLifecycle from './pages/StrategyLifecycle'
import Charts from './pages/Charts'
import Reports from './pages/Reports'
import Settings from './pages/Settings'
import RiskControls from './pages/RiskControls'
import NewsIntel from './pages/NewsIntel'
import ActivityTimeline from './components/ActivityTimeline'
import CommandPalette from './components/CommandPalette'

type NavItem = { to: string; label: string }
type NavGroup = { group: string; items: NavItem[] }

const NAV: NavGroup[] = [
  { group: '', items: [{ to: '/', label: 'Cockpit' }] },
  {
    group: 'Markets',
    items: [
      { to: '/opportunities', label: 'Opportunities' },
      { to: '/charts', label: 'Charts' },
      { to: '/news', label: 'News Intel' },
    ],
  },
  {
    group: 'Trading',
    items: [
      { to: '/proposals', label: 'Proposals' },
      { to: '/paper-trades', label: 'Paper Trades' },
      { to: '/positions', label: 'Positions' },
    ],
  },
  {
    group: 'Intelligence',
    items: [
      { to: '/agent-runs', label: 'Agent Runs' },
      { to: '/strategies', label: 'Strategies' },
      { to: '/knowledge', label: 'Knowledge Base' },
      { to: '/dreaming', label: 'Dreaming' },
    ],
  },
  {
    group: 'Operations',
    items: [
      { to: '/risk', label: 'Risk & Controls' },
      { to: '/reports', label: 'Reports' },
      { to: '/settings', label: 'Settings' },
    ],
  },
]

const ALL_ITEMS = NAV.flatMap(g => g.items)

function StatusBar() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['status'], queryFn: api.getStatus, refetchInterval: 10000 })
  const { data: ks } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch, refetchInterval: 10000 })
  const { data: runs = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns, refetchInterval: 10000 })

  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })

  if (!data) return <div className="topbar-status"><span className="dot dot-muted" />connecting…</div>

  const running = !!(data as any).gateway_running
  const paused = !!(data as any).paused
  const live = String((data as any).live_state || 'live_disabled')
  const mode = String((data as any).exchange_mode || '')
  const perm = String((data as any).permission_mode || '')
  const killed = !!ks?.active

  const latestRun = (runs as any[])[0]
  let heartbeatText = ''
  if (latestRun?.ended_at) {
    const mins = Math.floor((Date.now() - new Date(latestRun.ended_at).getTime()) / 60000)
    heartbeatText = mins < 1 ? '<1m' : `${mins}m`
  }

  return (
    <div className="topbar-status">
      {heartbeatText && (
        <span className="pill pill-muted" title={`Last agent run: ${latestRun?.agent_name}`}>
          <span className="dot dot-green heartbeat" />
          {heartbeatText}
        </span>
      )}
      <span className={`pill ${running ? (paused ? 'pill-yellow' : 'pill-green') : 'pill-red'}`}>
        <span className={`dot ${running ? (paused ? 'dot-yellow' : 'dot-green') : 'dot-red'}`} />
        {running ? (paused ? 'Paused' : 'Running') : 'Stopped'}
      </span>
      <span className="pill pill-muted">{mode || '—'}</span>
      <span className={`pill ${live === 'live_disabled' ? 'pill-muted' : 'pill-yellow'}`}>
        {live.replace('live_', '')}
      </span>
      <span className="pill pill-blue">{perm}</span>
      {killed && <span className="pill pill-red"><span className="dot dot-red" />kill switch</span>}
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
  const location = useLocation()
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const current = ALL_ITEMS.find(i => i.to === location.pathname)
    || (location.pathname === '/' ? ALL_ITEMS[0] : undefined)
  const currentGroup = NAV.find(g => g.items.some(i => i.to === location.pathname))?.group

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
          <div className="breadcrumb">
            {currentGroup && <span className="crumb-group">{currentGroup}</span>}
            {currentGroup && <span className="crumb-sep">/</span>}
            <span className="crumb-current">{current?.label || 'Cockpit'}</span>
          </div>
          <StatusBar />
        </header>
        <main className="main">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/agent-runs" element={<AgentRuns />} />
            <Route path="/dreaming" element={<Dreaming />} />
            <Route path="/knowledge" element={<KnowledgeBase />} />
            <Route path="/opportunities" element={<Opportunities />} />
            <Route path="/proposals" element={<Proposals />} />
            <Route path="/paper-trades" element={<PaperTrades />} />
            <Route path="/positions" element={<Positions />} />
            <Route path="/strategies" element={<StrategyLifecycle />} />
            <Route path="/charts" element={<Charts />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/risk" element={<RiskControls />} />
            <Route path="/news" element={<NewsIntel />} />
          </Routes>
        </main>
        <ActivityTimeline />
      </div>
    </div>
  )
}
