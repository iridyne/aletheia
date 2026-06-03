import { Routes, Route, NavLink } from 'react-router-dom'
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

const navItems = [
  { to: '/', label: 'Overview' },
  { to: '/agent-runs', label: 'Agent Runs' },
  { to: '/dreaming', label: 'Dreaming / Learning' },
  { to: '/knowledge', label: 'Knowledge Base' },
  { to: '/opportunities', label: 'Opportunities' },
  { to: '/proposals', label: 'Proposals' },
  { to: '/paper-trades', label: 'Paper Trades' },
  { to: '/positions', label: 'Positions' },
  { to: '/strategies', label: 'Strategy Lifecycle' },
  { to: '/charts', label: 'Charts' },
  { to: '/reports', label: 'Reports' },
  { to: '/settings', label: 'Settings' },
]

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>Aletheia</h1>
        <nav>
          {navItems.map(n => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'}>
              {n.label}
            </NavLink>
          ))}
        </nav>
      </aside>
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
  )
}
