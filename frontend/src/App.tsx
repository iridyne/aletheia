import { Routes, Route, NavLink, useLocation } from 'react-router-dom'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
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

// Navigation grouped by the system's real workflow: observe → research →
// propose → execute → review. Flat 12-item list replaced with 5 semantic groups.
type NavItem = { to: string; label: string }
type NavGroup = { group: string; items: NavItem[] }

const NAV: NavGroup[] = [
  { group: '', items: [{ to: '/', label: 'Overview' }] },
  {
    group: 'Trading',
    items: [
      { to: '/proposals', label: 'Proposals' },
      { to: '/paper-trades', label: 'Paper Trades' },
      { to: '/positions', label: 'Positions' },
      { to: '/charts', label: 'Charts' },
    ],
  },
  {
    group: 'Intelligence',
    items: [
      { to: '/agent-runs', label: 'Agent Runs' },
      { to: '/opportunities', label: 'Opportunities' },
      { to: '/dreaming', label: 'Dreaming' },
      { to: '/knowledge', label: 'Knowledge Base' },
    ],
  },
  {
    group: 'Strategy',
    items: [
      { to: '/strategies', label: 'Strategy Lifecycle' },
      { to: '/reports', label: 'Reports' },
    ],
  },
  {
    group: 'System',
    items: [{ to: '/settings', label: 'Settings' }],
  },
]

const ALL_ITEMS = NAV.flatMap(g => g.items)

function StatusBar() {
  const { data } = useQuery({ queryKey: ['status'], queryFn: api.getStatus, refetchInterval: 10000 })
  const { data: ks } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch, refetchInterval: 10000 })
  if (!data) return <div className="topbar-status"><span className="dot dot-muted" />connecting…</div>

  const running = !!data.gateway_running
  const paused = !!data.paused
  const live = String(data.live_state || 'live_disabled')
  const mode = String(data.exchange_mode || '')
  const perm = String(data.permission_mode || '')
  const killed = !!ks?.active

  return (
    <div className="topbar-status">
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
            <span className="crumb-current">{current?.label || 'Overview'}</span>
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
          </Routes>
        </main>
      </div>
    </div>
  )
}
