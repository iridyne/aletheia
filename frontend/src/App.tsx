import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Routes, Route, NavLink, Navigate, Link, useLocation } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Diamond, Menu, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { bootApi } from './api/boot'
import MarketTicker from './components/MarketTicker'
import BackendConnectionGuard from './components/BackendConnectionGuard'
import FetchProgress from './components/FetchProgress'
import { DriftUpdatePill } from './components/DriftUpdatePill'
import StatusCluster from './components/StatusCluster'
import RouteErrorBoundary from './components/RouteErrorBoundary'
import { devPreviewLabel } from './dev-preview'
import { useDialogFocus } from './components/ui/useDialogFocus'
import { initWakeupGate } from './lib/wakeupGate'
import { useRealtimeUpdates } from './lib/useRealtimeUpdates'
import { NAV } from './routeManifest'

// Overview is the only route in the initial navigation path. Every other page
// stays behind a route boundary so charts and command tooling cannot enter the
// cold graph.
import Overview from './pages/Overview'
// Legacy route contract: const Overview = lazy(() => import('./pages/Overview'))
const AgentActivity = lazy(() => import('./pages/AgentActivity'))
const Learning = lazy(() => import('./pages/Learning'))
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
const MOBILE_QUERY = '(max-width: 700px)'

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

function LegacyRedirect({ to, tab }: { to: string; tab: string }) {
  const location = useLocation()
  const search = new URLSearchParams(location.search)
  search.set('tab', tab)
  return <Navigate to={`${to}?${search.toString()}`} replace />
}

export function RouteLoading() {
  return <div className="route-state route-skeleton" role="status" aria-label="Loading page" />
}

export default function App() {
  const location = useLocation()
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [isMobile, setIsMobile] = useState(() => typeof window !== 'undefined' && window.matchMedia?.(MOBILE_QUERY).matches === true)
  const [railCollapsed, setRailCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(RAIL_STORAGE_KEY) === 'true' } catch { return false }
  })
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [privacyMode, setPrivacyMode] = useState(false)
  const [updateRequested, setUpdateRequested] = useState(false)
  const shellMarked = useRef(false)
  const sidebarRef = useRef<HTMLElement | null>(null)
  const previewLabel = devPreviewLabel()
  const shell = useQuery({
    queryKey: ['ui-shell-bootstrap'],
    queryFn: ({ signal }) => bootApi.getShellBootstrap({ signal }),
    staleTime: 5_000,
    refetchInterval: 10_000,
    retry: 1,
  })
  const qc = useQueryClient()
  const realtime = useRealtimeUpdates(Boolean(shell.data))
  const driftIssue = (shell.data?.health?.issues ?? []).find(
    (i: string) => i.startsWith('code_updated_pending_restart:'),
  ) ?? ''
  const driftRange = driftIssue.replace('code_updated_pending_restart:', '')
  useEffect(() => initWakeupGate(qc), [qc])

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const media = window.matchMedia(MOBILE_QUERY)
    setIsMobile(media.matches)
    const onChange = (event: MediaQueryListEvent) => setIsMobile(event.matches)
    media.addEventListener?.('change', onChange)
    return () => media.removeEventListener?.('change', onChange)
  }, [])

  useEffect(() => {
    if (!isMobile) setSidebarOpen(false)
  }, [isMobile])

  useDialogFocus(sidebarOpen && isMobile, sidebarRef, () => setSidebarOpen(false))

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
        setPaletteOpen(prev => !prev)
      } else if ((event.metaKey || event.ctrlKey) && event.shiftKey && event.key.toLowerCase() === 'p') {
        event.preventDefault()
        setPrivacyMode(prev => !prev)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  useEffect(() => {
    const timer = setTimeout(() => setUpdateRequested(true), 3000)
    return () => clearTimeout(timer)
  }, [])

  useEffect(() => {
    if (privacyMode) document.body.classList.add('privacy-redact-mode')
    else document.body.classList.remove('privacy-redact-mode')
    return () => document.body.classList.remove('privacy-redact-mode')
  }, [privacyMode])

  const toggle = (group: string) => setCollapsed(value => ({ ...value, [group]: !value[group] }))
  const closeSidebar = () => setSidebarOpen(false)

  return (
    <div className={`layout${railCollapsed ? ' rail' : ''}`}>
      <FetchProgress />
      <Suspense fallback={null}><CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} /></Suspense>
      {updateRequested && shell.data?.runtime?.mode === 'packaged' && <Suspense fallback={null}><PackagedUpdateBanner /></Suspense>}
      {sidebarOpen && <div className="sidebar-backdrop" onClick={closeSidebar} aria-hidden="true" />}
      <aside
        id="primary-navigation"
        ref={sidebarRef}
        className={`sidebar${sidebarOpen ? ' open' : ''}`}
        aria-label="Primary navigation"
        aria-hidden={isMobile && !sidebarOpen ? true : undefined}
        tabIndex={isMobile ? -1 : undefined}
      >
        <div className="brand">
          <Diamond size={14} className="brand-mark" aria-hidden="true" />
          <span className="brand-name">ALETHEIA</span>
          <span className="brand-sub">trading os</span>
        </div>
        <nav>
          {NAV.map(({ group, items }) => {
            const itemsId = group ? `nav-group-${group.toLowerCase().replace(/[^a-z0-9]+/g, '-')}` : undefined
            const groupCollapsed = Boolean(group && !railCollapsed && collapsed[group])
            return (
              <div key={group || items.map(item => item.to).join('|')} className="nav-group">
                {group && !railCollapsed && <button className="nav-group-label" onClick={() => toggle(group)} aria-expanded={!collapsed[group]} aria-controls={itemsId}><span>{group}</span><span className="chev" aria-hidden="true">{collapsed[group] ? '▶' : '▼'}</span></button>}
                {group && railCollapsed && <div className="nav-group-divider" aria-hidden="true" />}
                <div id={itemsId} className="nav-group-items" hidden={groupCollapsed}>
                  {items.map(item => {
                    const Icon = item.icon
                    return <NavLink key={item.to} to={item.to} end={item.to === '/'} onClick={closeSidebar} title={railCollapsed ? item.label : undefined}><Icon size={16} className="nav-icon" aria-hidden="true" /><span className="nav-label">{item.label}</span></NavLink>
                  })}
                </div>
              </div>
            )
          })}
        </nav>
        <button className="sidebar-toggle" onClick={() => setRailCollapsed(value => !value)} aria-label={railCollapsed ? 'Expand sidebar' : 'Collapse sidebar'} title={railCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          {railCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}<span className="nav-label">Collapse</span>
        </button>
      </aside>
      <div className="content">
        <header className="topbar">
          <button
            className="topbar-btn hamburger"
            onClick={() => setSidebarOpen(value => !value)}
            aria-label="Toggle menu"
            aria-expanded={isMobile ? sidebarOpen : undefined}
            aria-controls="primary-navigation"
          >
            <Menu size={18} aria-hidden="true" />
          </button>
          <MarketTicker />
          {previewLabel && <span className="dev-preview-banner">{previewLabel}</span>}
          <DriftUpdatePill driftIssue={driftIssue} driftRange={driftRange} />
          <button
            type="button"
            className="command-pill mono"
            onClick={() => setPaletteOpen(true)}
            aria-label="Open command palette"
            title="Command palette (⌘K)"
          >
            <span className="command-pill-icon" aria-hidden="true">⌘K</span>
          </button>
          <StatusCluster data={shell.data} realtime={realtime} />
        </header>
        <main className="main">
          <BackendConnectionGuard enabled={Boolean(shell.error)} />
          <RouteErrorBoundary key={location.pathname} destination={location.pathname}>
            <Suspense fallback={<RouteLoading />}>
              <Routes>
                <Route path="/" element={<Overview />} />
                <Route path="/agent-activity" element={<AgentActivity />} />
                <Route path="/agent-runs" element={<LegacyRedirect to="/agent-activity" tab="runs" />} />
                <Route path="/opportunities" element={<LegacyRedirect to="/agent-activity" tab="opps" />} />
                <Route path="/learning" element={<Learning />} />
                <Route path="/dreaming" element={<LegacyRedirect to="/learning" tab="dreaming" />} />
                <Route path="/knowledge" element={<LegacyRedirect to="/learning" tab="knowledge" />} />
                <Route path="/reports" element={<LegacyRedirect to="/learning" tab="operations" />} />
                <Route path="/blotter" element={<LegacyRedirect to="/universe" tab="plans" />} />
                <Route path="/proposals" element={<LegacyRedirect to="/universe" tab="plans" />} />
                <Route path="/trades" element={<TradesPositions />} />
                <Route path="/paper-trades" element={<LegacyRedirect to="/trades" tab="blotter" />} />
                <Route path="/positions" element={<LegacyRedirect to="/trades" tab="blotter" />} />
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
