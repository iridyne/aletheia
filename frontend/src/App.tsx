import { Routes, Route, Navigate } from 'react-router-dom'
import { useState, useEffect, useCallback } from 'react'
import { InspectorProvider } from './context/InspectorContext'
import TopStatusBar from './components/TopStatusBar'
import LeftNav from './components/LeftNav'
import RightInspector from './components/RightInspector'
import BottomTimeline from './components/BottomTimeline'
import CommandCenter from './components/CommandCenter'

import Overview from './pages/Overview'
import AgentRuns from './pages/AgentRuns'
import Dreaming from './pages/Dreaming'
import KnowledgeBase from './pages/KnowledgeBase'
import Opportunities from './pages/Opportunities'
import Proposals from './pages/Proposals'
import StrategyLifecycle from './pages/StrategyLifecycle'
import Charts from './pages/Charts'
import Reports from './pages/Reports'
import Settings from './pages/Settings'
import CommandCenterPage from './pages/CommandCenterPage'
import NewsIntelligence from './pages/NewsIntelligence'
import RiskControls from './pages/RiskControls'
import Portfolio from './pages/Portfolio'

export default function App() {
  const [cmdOpen, setCmdOpen] = useState(false)
  const [navOpen, setNavOpen] = useState(false)

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault()
      setCmdOpen(o => !o)
    }
    if (e.key === 'Escape') {
      setCmdOpen(false)
      setNavOpen(false)
    }
  }, [])

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])

  return (
    <InspectorProvider>
      <div className={`app-shell${navOpen ? ' nav-open' : ''}`}>
        <TopStatusBar onCommandOpen={() => setCmdOpen(true)} onNavToggle={() => setNavOpen(o => !o)} />
        <LeftNav />
        <main className="main-workspace">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/command" element={<CommandCenterPage />} />
            <Route path="/risk" element={<RiskControls />} />
            <Route path="/news" element={<NewsIntelligence />} />
            <Route path="/charts" element={<Charts />} />
            <Route path="/opportunities" element={<Opportunities />} />
            <Route path="/dreaming" element={<Dreaming />} />
            <Route path="/knowledge" element={<KnowledgeBase />} />
            <Route path="/proposals" element={<Proposals />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="/strategies" element={<StrategyLifecycle />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/agent-runs" element={<AgentRuns />} />
            {/* Legacy redirects */}
            <Route path="/paper-trades" element={<Navigate to="/portfolio" replace />} />
            <Route path="/positions" element={<Navigate to="/portfolio" replace />} />
          </Routes>
        </main>
        <RightInspector />
        <BottomTimeline />
      </div>

      {cmdOpen && (
        <div className="cmd-overlay" onClick={() => setCmdOpen(false)}>
          <div className="cmd-dialog" onClick={e => e.stopPropagation()}>
            <CommandCenter />
          </div>
        </div>
      )}
    </InspectorProvider>
  )
}
