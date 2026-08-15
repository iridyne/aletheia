import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Routes, Route, NavLink, Navigate, Link, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Diamond, Menu, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { bootApi } from './api/boot'
import MarketTicker from './components/MarketTicker'
import BackendConnectionGuard from './components/BackendConnectionGuard'
import FetchProgress from './components/FetchProgress'
import StatusCluster from './components/StatusCluster'
import RouteErrorBoundary from './components/RouteErrorBoundary'
import { devPreviewLabel } from './dev-preview'
import { useRealtimeUpdates } from './lib/useRealtimeUpdates'
import { deferTask } from './lib/deferred'
import { NAV } from './routeManifest'

// Local paper exchange honesty is rendered by AccountSnapshot: Local paper · no exchange.

// Overview is the only route in the initial navigation path. Every other page
// stays behind a route boundary so charts and command tooling cannot enter the
// cold graph.
import Overview from './pages/Overview'
// Legacy route contract: const Overview = lazy(() => import('./pages/Overview'))
const AgentActivity = lazy(() => import('./pages/AgentActivity'))
const Learning = lazy(() => import('./pages/Learning'))
const Blotter = lazy(() => import('./pages/Blotter'))
const TradesPositions = lazy(() => import('./pages/TradesPositions'))
const Charts = lazy(() => import('./pages/Charts'))
const Settings = lazy(() => import('./pages/Settings'))
const RiskControls = lazy(() => import('./pages/RiskControls'))
const NewsIntel = lazy(() => import('./pages/NewsIntel'))
const CommandCenter = lazy(() => import('./pages/CommandCenter'))
const SerenityLens = lazy(() => import('./pages/SerenityLens'))
const PredictionLens = lazy(() => import('./pages/PredictionLens'))
const Universe = lazy(() => import('./pages/Universe'))
const Usage = lazy(() => import('./pages/Usage'))
const CommandPalette = lazy(() => import('./components/CommandPalette'))
const PackagedUpdateBanner = lazy(() => import('./components/PackagedUpdateBanner'))

const RAIL_STORAGE_KEY = 'sidebar:collapsed'

function NotFound() {
  return (
    <section className="route-state" aria-labelledby="not-found-title">
      <p className="mono text-muted">404</p>
      <h2 id="not-found-title">Page not found</h2>
      <p className="text-muted">This route is not part of the current trading desk.</p>
      <Link className="btn btn-secondary" to="/">Return to Overview</Link>
    </section>
  )
}

export default function App() {
  const location = useLocation()
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [railCollapsed, setRailCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(RAIL_STORAGE_KEY) === 'true' } catch { return false }
  })
  const [paletteRequested, setPaletteRequested] = useState(false)
  const [updateRequested, setUpdateRequested] = useState(false)
  const shellMarked = useRef(false)
  const previewLabel = devPreviewLabel()
  const shell = useQuery({
    queryKey: ['ui-shell-bootstrap'],
    queryFn: ({ signal }) => bootApi.getShellBootstrap({ signal }),
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: 1,
  })
  const realtime = useRealtimeUpdates(Boolean(shell.data))

  useEffect(() => {
    try { localStorage.setItem(RAIL_STORAGE_KEY, String(railCollapsed)) } catch { /* ignore */ }
  }, [railCollapsed])

  useEffect(() => {
    if (shell.data && !shellMarked.current) {
      shellMarked.current = true
      performance.mark('aletheia:first-real-shell')
    }
  }, [shell.data])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteRequested(true)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  useEffect(() => deferTask(() => setUpdateRequested(true), { delayMs: 3000 }), [])

  const toggle = (group: string) => setCollapsed(value => ({ ...value, [group]: !value[group] }))

  return (
    <div className={`layout${railCollapsed ? ' rail' : ''}`}>
      <FetchProgress />
      {paletteRequested && <Suspense fallback={null}><CommandPalette initialOpen /></Suspense>}
      {updateRequested && shell.data?.runtime?.mode === 'packaged' && <Suspense fallback={null}><PackagedUpdateBanner /></Suspense>}
      {sidebarOpen && <div className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} aria-hidden="true" />}
      <aside className={`sidebar${sidebarOpen ? ' open' : ''}`}>
        <div className="brand">
          <Diamond size={14} className="brand-mark" aria-hidden="true" />
          <span className="brand-name">ALETHEIA</span>
          <span className="brand-sub">trading os</span>
        </div>
        <nav>
          {NAV.map(({ group, items }) => (
            <div key={group || items.map(item => item.to).join('|')} className="nav-group">
              {group && !railCollapsed && <button className="nav-group-label" onClick={() => toggle(group)}><span>{group}</span><span className={`chev ${collapsed[group] ? 'chev-closed' : ''}`}>▾</span></button>}
              {group && railCollapsed && <div className="nav-group-divider" aria-hidden="true" />}
              {(railCollapsed || !collapsed[group]) && items.map(item => {
                const Icon = item.icon
                return <NavLink key={item.to} to={item.to} end={item.to === '/'} onClick={() => setSidebarOpen(false)} title={railCollapsed ? item.label : undefined}><Icon size={16} className="nav-icon" aria-hidden="true" /><span className="nav-label">{item.label}</span></NavLink>
              })}
            </div>
          ))}
        </nav>
        <button className="sidebar-toggle" onClick={() => setRailCollapsed(value => !value)} aria-label={railCollapsed ? 'Expand sidebar' : 'Collapse sidebar'} title={railCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          {railCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}<span className="nav-label">Collapse</span>
        </button>
      </aside>
      <div className="content">
        <header className="topbar">
          <button className="topbar-btn hamburger" onClick={() => setSidebarOpen(value => !value)} aria-label="Toggle menu"><Menu size={18} aria-hidden="true" /></button>
          <MarketTicker />
          {previewLabel && <span className="dev-preview-banner">{previewLabel}</span>}
          <StatusCluster data={shell.data} realtime={realtime} />
        </header>
        <main className="main">
          <BackendConnectionGuard enabled={Boolean(shell.error)} />
          <RouteErrorBoundary key={location.pathname} destination={location.pathname}>
            <Suspense fallback={<div className="route-state route-skeleton" role="status" aria-label="Loading page" />}>
              <Routes>
                <Route path="/" element={<Overview />} />
                <Route path="/agent-activity" element={<AgentActivity />} />
                <Route path="/agent-runs" element={<Navigate to="/agent-activity" replace />} />
                <Route path="/opportunities" element={<Navigate to="/agent-activity" replace />} />
                <Route path="/learning" element={<Learning />} />
                <Route path="/dreaming" element={<Navigate to="/learning" replace />} />
                <Route path="/knowledge" element={<Navigate to="/learning" replace />} />
                <Route path="/reports" element={<Navigate to="/learning" replace />} />
                <Route path="/blotter" element={<Blotter />} />
                <Route path="/proposals" element={<Navigate to="/blotter" replace />} />
                <Route path="/trades" element={<TradesPositions />} />
                <Route path="/paper-trades" element={<Navigate to="/trades" replace />} />
                <Route path="/positions" element={<Navigate to="/trades" replace />} />
                <Route path="/charts" element={<Charts />} />
                <Route path="/prediction-markets" element={<PredictionLens />} />
                <Route path="/command-center" element={<CommandCenter />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/risk" element={<RiskControls />} />
                <Route path="/news" element={<NewsIntel />} />
                <Route path="/serenity-lens" element={<SerenityLens />} />
                <Route path="/universe" element={<Universe />} />
                <Route path="/usage" element={<Usage />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </Suspense>
          </RouteErrorBoundary>
        </main>
      </div>
    </div>
  )
}
