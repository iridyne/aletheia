import { useState, useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import { hasUncontrolledDraft, isFallbackDraftControl, shouldBlockNavigation } from '../features/settings/dirtyGuard'
import { MODEL_PROVIDERS_SECTION } from '../features/settings/model-providers/modelProviderState'
import { HEALTH_MONITORING_SECTION, WEBHOOK_SECTION } from '../features/settings/health/healthState'
import { HealthNotificationsTab } from '../features/settings/tabs/HealthNotificationsTab'
import { AdvancedTab, ADVANCED_SECTION } from '../features/settings/tabs/AdvancedTab'
import {
  CAPITAL_EVENTS_SECTION, EXCHANGE_CONNECTION_SECTION, ExecutionTab,
  PAPER_ACCOUNT_SECTION, PAPER_PARAMETERS_SECTION, TRADING_MODE_SECTION,
} from '../features/settings/tabs/ExecutionTab'
import {
  AgentModelsTab, INFERENCE_ADMISSION_SECTION, LEARNING_SECTION, LLM_COST_SECTION,
  PERMISSION_SECTION, REGIME_SECTION, SCHEDULER_SECTION, SERENITY_SECTION,
} from '../features/settings/tabs/AgentModelsTab'
import {
  APP_SECTION, DATA_EXPORT_SECTION, DATA_RETENTION_SECTION, NETWORK_PROXY_SECTION,
  RuntimeSystemTab, SOFTWARE_UPDATES_SECTION,
} from '../features/settings/tabs/RuntimeSystemTab'
import {
  EQUITIES_SECTION, MACRO_SIGNALS_SECTION, MARKET_DATA_SECTION, NEWS_FEEDS_SECTION,
  PREDICTION_MARKETS_SECTION, SignalsTab,
} from '../features/settings/tabs/SignalsTab'
import {
  EVENT_TRIGGERS_SECTION, RISK_LIMITS_SECTION, RiskTab,
  TRADABLE_SYMBOLS_SECTION, UNIVERSE_SECTION,
} from '../features/settings/tabs/RiskTab'
import { TELEGRAM_SECTION } from '../features/settings/telegram/telegramState'

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

type Tab = 'agent' | 'execution' | 'risk' | 'health' | 'runtime' | 'signals' | 'advanced'
const TABS: { id: Tab; label: string; description: string }[] = [
  { id: 'agent',     label: 'Agent & Models',      description: 'Autonomy, scheduler, and model providers' },
  { id: 'execution', label: 'Execution',           description: 'Exchange, credentials, live state, and paper fills' },
  { id: 'risk',      label: 'Risk & Universe',     description: 'Risk limits, tradable symbols, and universe board' },
  { id: 'health',    label: 'Health & Alerts',     description: 'Self-heal, startup gates, and notifications' },
  { id: 'runtime',   label: 'Runtime & System',    description: 'App shell, updates, retention, and network' },
  { id: 'signals',   label: 'Signals',             description: 'Market data sources, news feeds, macro signals, and observed assets' },
  { id: 'advanced',  label: 'Advanced',            description: 'Read-only configuration coverage and diagnostics' },
]

const TAB_ALIASES: Record<string, Tab> = {
  intelligence: 'agent',
  infrastructure: 'runtime',
}

const SETTINGS_NAV_KEY = 'aletheia.settings.navLayout.v1'
const SETTINGS_NAV_DEFAULT = 248
const SETTINGS_NAV_MIN = 216
const SETTINGS_NAV_MAX = 320
const SETTINGS_NAV_COLLAPSED = 52

type SettingsNavState = {
  width: number
  collapsed: boolean
}

function clampSettingsNavWidth(value: number): number {
  if (!Number.isFinite(value)) return SETTINGS_NAV_DEFAULT
  return Math.min(SETTINGS_NAV_MAX, Math.max(SETTINGS_NAV_MIN, Math.round(value)))
}

function clampSettingsNavResize(value: number): number {
  return clampSettingsNavWidth(value)
}

function SettingsNavIcon({ tab }: { tab: Tab }) {
  const props = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  switch (tab) {
    case 'agent':
      return <svg viewBox="0 0 24 24" aria-hidden="true" {...props}><circle cx="12" cy="12" r="3" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" /></svg>
    case 'execution':
      return <svg viewBox="0 0 24 24" aria-hidden="true" {...props}><path d="M5 12h13M14 7l5 5-5 5M5 6v12" /></svg>
    case 'risk':
      return <svg viewBox="0 0 24 24" aria-hidden="true" {...props}><path d="M12 3 20 6v5c0 5-3.4 8.4-8 10-4.6-1.6-8-5-8-10V6l8-3Z" /><path d="m9 12 2 2 4-4" /></svg>
    case 'health':
      return <svg viewBox="0 0 24 24" aria-hidden="true" {...props}><path d="M3 12h4l2-5 4 10 2-5h6" /></svg>
    case 'runtime':
      return <svg viewBox="0 0 24 24" aria-hidden="true" {...props}><path d="M4 7h16M4 17h16M8 4v6M16 14v6" /></svg>
    case 'signals':
      return <svg viewBox="0 0 24 24" aria-hidden="true" {...props}><path d="M3 17 8 12l4 3 7-8" /><path d="M17 7h2v2" /></svg>
    case 'advanced':
      return <svg viewBox="0 0 24 24" aria-hidden="true" {...props}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.1 2.1-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-3v-.2a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1-2.1-2.1.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H5.2v-3h.2a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1 2.1-2.1.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.6v-.2h3v.2a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1 2.1 2.1-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v3h-.2a1.7 1.7 0 0 0-1.6 1Z" /></svg>
  }
}

/**
 * Inline-search index — fuzzy-find a Section across all 6 tabs.
 *
 * Settings is 3500+ lines and split into 6 tabs, so the operator has no fast way
 * to jump to e.g. "Risk Limits → Margin warning ratio". This index lets a search
 * box at the top of the page match any Section by title, returning the tab it
 * lives on; clicking a result switches tabs (and the existing ?section= deep-link
 * mechanism takes care of scrolling).
 *
 * MAINTAIN INVARIANT: every local `<Section>` and extracted feature section must
 * render metadata listed below. Local `Section` resolves its anchor id from this
 * registry; extracted features use the same descriptor for their wrapper and
 * registry row. The invariant is enforced by a test rather than this comment,
 * which had already drifted three times (#632).
 */
const SETTINGS_INDEX: { title: string; tab: Tab; slug: string; alt?: string[] }[] = [
  // Agent & Models
  { title: PERMISSION_SECTION.title,          tab: PERMISSION_SECTION.tab, slug: PERMISSION_SECTION.id, alt: [...PERMISSION_SECTION.searchTerms] },
  { title: LEARNING_SECTION.title,            tab: LEARNING_SECTION.tab, slug: LEARNING_SECTION.id, alt: [...LEARNING_SECTION.searchTerms] },
  { title: REGIME_SECTION.title,              tab: REGIME_SECTION.tab, slug: REGIME_SECTION.id, alt: [...REGIME_SECTION.searchTerms] },
  { title: SERENITY_SECTION.title,            tab: SERENITY_SECTION.tab, slug: SERENITY_SECTION.id, alt: [...SERENITY_SECTION.searchTerms] },
  { title: SCHEDULER_SECTION.title,           tab: SCHEDULER_SECTION.tab, slug: SCHEDULER_SECTION.id, alt: [...SCHEDULER_SECTION.searchTerms] },
  { title: MODEL_PROVIDERS_SECTION.title,     tab: MODEL_PROVIDERS_SECTION.tab, slug: MODEL_PROVIDERS_SECTION.id, alt: [...MODEL_PROVIDERS_SECTION.searchTerms] },
  { title: INFERENCE_ADMISSION_SECTION.title, tab: INFERENCE_ADMISSION_SECTION.tab, slug: INFERENCE_ADMISSION_SECTION.id, alt: [...INFERENCE_ADMISSION_SECTION.searchTerms] },
  { title: LLM_COST_SECTION.title,            tab: LLM_COST_SECTION.tab, slug: LLM_COST_SECTION.id, alt: [...LLM_COST_SECTION.searchTerms] },
  // Execution
  { title: EXCHANGE_CONNECTION_SECTION.title, tab: EXCHANGE_CONNECTION_SECTION.tab, slug: EXCHANGE_CONNECTION_SECTION.id, alt: [...EXCHANGE_CONNECTION_SECTION.searchTerms] },
  { title: TRADING_MODE_SECTION.title,        tab: TRADING_MODE_SECTION.tab, slug: TRADING_MODE_SECTION.id, alt: [...TRADING_MODE_SECTION.searchTerms] },
  { title: PAPER_PARAMETERS_SECTION.title,    tab: PAPER_PARAMETERS_SECTION.tab, slug: PAPER_PARAMETERS_SECTION.id, alt: [...PAPER_PARAMETERS_SECTION.searchTerms] },
  { title: CAPITAL_EVENTS_SECTION.title,      tab: CAPITAL_EVENTS_SECTION.tab, slug: CAPITAL_EVENTS_SECTION.id, alt: [...CAPITAL_EVENTS_SECTION.searchTerms] },
  { title: PAPER_ACCOUNT_SECTION.title,       tab: PAPER_ACCOUNT_SECTION.tab, slug: PAPER_ACCOUNT_SECTION.id, alt: [...PAPER_ACCOUNT_SECTION.searchTerms] },
  // Risk & Universe
  { title: UNIVERSE_SECTION.title,         tab: UNIVERSE_SECTION.tab, slug: UNIVERSE_SECTION.id, alt: [...UNIVERSE_SECTION.searchTerms] },
  { title: EVENT_TRIGGERS_SECTION.title,   tab: EVENT_TRIGGERS_SECTION.tab, slug: EVENT_TRIGGERS_SECTION.id, alt: [...EVENT_TRIGGERS_SECTION.searchTerms] },
  { title: TRADABLE_SYMBOLS_SECTION.title, tab: TRADABLE_SYMBOLS_SECTION.tab, slug: TRADABLE_SYMBOLS_SECTION.id, alt: [...TRADABLE_SYMBOLS_SECTION.searchTerms] },
  { title: RISK_LIMITS_SECTION.title,      tab: RISK_LIMITS_SECTION.tab, slug: RISK_LIMITS_SECTION.id, alt: [...RISK_LIMITS_SECTION.searchTerms] },
  // Health & Alerts
  { title: HEALTH_MONITORING_SECTION.title, tab: HEALTH_MONITORING_SECTION.tab, slug: HEALTH_MONITORING_SECTION.id, alt: [...HEALTH_MONITORING_SECTION.searchTerms] },
  { title: TELEGRAM_SECTION.title,          tab: TELEGRAM_SECTION.tab, slug: TELEGRAM_SECTION.id, alt: [...TELEGRAM_SECTION.searchTerms] },
  { title: WEBHOOK_SECTION.title,           tab: WEBHOOK_SECTION.tab, slug: WEBHOOK_SECTION.id, alt: [...WEBHOOK_SECTION.searchTerms] },
  // Runtime & System
  { title: APP_SECTION.title,              tab: APP_SECTION.tab, slug: APP_SECTION.id, alt: [...APP_SECTION.searchTerms] },
  { title: DATA_RETENTION_SECTION.title,   tab: DATA_RETENTION_SECTION.tab, slug: DATA_RETENTION_SECTION.id, alt: [...DATA_RETENTION_SECTION.searchTerms] },
  { title: DATA_EXPORT_SECTION.title,      tab: DATA_EXPORT_SECTION.tab, slug: DATA_EXPORT_SECTION.id, alt: [...DATA_EXPORT_SECTION.searchTerms] },
  { title: SOFTWARE_UPDATES_SECTION.title, tab: SOFTWARE_UPDATES_SECTION.tab, slug: SOFTWARE_UPDATES_SECTION.id, alt: [...SOFTWARE_UPDATES_SECTION.searchTerms] },
  { title: NETWORK_PROXY_SECTION.title,    tab: NETWORK_PROXY_SECTION.tab, slug: NETWORK_PROXY_SECTION.id, alt: [...NETWORK_PROXY_SECTION.searchTerms] },
  // Signals
  { title: MARKET_DATA_SECTION.title,        tab: MARKET_DATA_SECTION.tab, slug: MARKET_DATA_SECTION.id, alt: [...MARKET_DATA_SECTION.searchTerms] },
  { title: MACRO_SIGNALS_SECTION.title,      tab: MACRO_SIGNALS_SECTION.tab, slug: MACRO_SIGNALS_SECTION.id, alt: [...MACRO_SIGNALS_SECTION.searchTerms] },
  { title: NEWS_FEEDS_SECTION.title,         tab: NEWS_FEEDS_SECTION.tab, slug: NEWS_FEEDS_SECTION.id, alt: [...NEWS_FEEDS_SECTION.searchTerms] },
  { title: PREDICTION_MARKETS_SECTION.title, tab: PREDICTION_MARKETS_SECTION.tab, slug: PREDICTION_MARKETS_SECTION.id, alt: [...PREDICTION_MARKETS_SECTION.searchTerms] },
  { title: EQUITIES_SECTION.title,           tab: EQUITIES_SECTION.tab, slug: EQUITIES_SECTION.id, alt: [...EQUITIES_SECTION.searchTerms] },
  // Advanced
  { title: ADVANCED_SECTION.title,     tab: ADVANCED_SECTION.tab, slug: ADVANCED_SECTION.id, alt: [...ADVANCED_SECTION.searchTerms] },
]

function matchesSearch(item: typeof SETTINGS_INDEX[number], q: string): boolean {
  const needle = q.toLowerCase().trim()
  if (!needle) return false
  if (item.title.toLowerCase().includes(needle)) return true
  return (item.alt || []).some(a => a.toLowerCase().includes(needle))
}

function parseTab(value: string | null): Tab | null {
  if (!value) return null
  if (TABS.some(t => t.id === value)) return value as Tab
  return TAB_ALIASES[value] ?? null
}


// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────

export default function Settings() {
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: status, isPending: statusPending, refetch: refetchStatus } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: settings, dataUpdatedAt: settingsUpdatedAt, isPending: settingsPending, refetch: refetchSettings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
  })
  const [navWidth, setNavWidth] = useState(SETTINGS_NAV_DEFAULT)
  const [isNavCollapsed, setIsNavCollapsed] = useState(false)
  const navExpandedWidthRef = useRef(SETTINGS_NAV_DEFAULT)
  const navDragRef = useRef<{ startX: number; startWidth: number } | null>(null)
  const [tab, setTab] = useState<Tab>(() => {
    return parseTab(searchParams.get('tab')) ?? 'agent'
  })
  const dirtyRef = useRef<Set<string>>(new Set())
  const touchedRef = useRef(false)
  const touchedControlsRef = useRef<Set<HTMLElement>>(new Set())
  const [isTouched, setIsTouched] = useState(false)
  const [preciseDirtyCount, setPreciseDirtyCount] = useState(0)
  const [draftEpoch, setDraftEpoch] = useState(0)
  const registerDirty = (id: string, isDirty: boolean) => {
    if (isDirty) dirtyRef.current.add(id)
    else {
      dirtyRef.current.delete(id)
      if (dirtyRef.current.size === 0) {
        const fallbackDraftRemains = [...touchedControlsRef.current].some(hasUncontrolledDraft)
        if (!fallbackDraftRemains) {
          touchedControlsRef.current.clear()
          touchedRef.current = false
          setIsTouched(false)
        }
      }
    }
    setPreciseDirtyCount(dirtyRef.current.size)
  }
  // Delegated fallback for editors that do not expose precise local state.
  // See features/settings/dirtyGuard.ts for why this is a shell-level listener.
  const markTouched = (e: React.FormEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement | null
    if (!isFallbackDraftControl(target)) return
    if (target) touchedControlsRef.current.add(target)
    if (touchedRef.current) return
    touchedRef.current = true
    setIsTouched(true)   // state (not just ref) so the beforeunload effect re-runs
  }
  const clearDirty = () => {
    dirtyRef.current.clear()
    touchedControlsRef.current.clear()
    touchedRef.current = false
    setIsTouched(false)
    setPreciseDirtyCount(0)
  }
  const discardDrafts = () => {
    clearDirty()
    // Remount the active tab so every staged editor adopts the last server
    // snapshot through its existing draft lifecycle. Immediate controls are
    // already server-controlled and therefore settle to the same snapshot.
    setDraftEpoch(epoch => epoch + 1)
  }

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(SETTINGS_NAV_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw) as SettingsNavState
      if (typeof parsed.width === 'number') {
        if (parsed.collapsed) {
          setIsNavCollapsed(true)
          setNavWidth(SETTINGS_NAV_COLLAPSED)
          navExpandedWidthRef.current = clampSettingsNavWidth(parsed.width)
        } else {
          const width = clampSettingsNavWidth(parsed.width)
          setIsNavCollapsed(false)
          setNavWidth(width)
          navExpandedWidthRef.current = width
        }
      }
      if (typeof parsed.collapsed === 'boolean' && typeof parsed.width !== 'number') {
        setIsNavCollapsed(parsed.collapsed)
      }
    } catch {
      // ignore stale/corrupt localStorage data
    }
  }, [])

  useEffect(() => {
    window.localStorage.setItem(SETTINGS_NAV_KEY, JSON.stringify({
      width: isNavCollapsed ? navExpandedWidthRef.current : navWidth,
      collapsed: isNavCollapsed,
    }))
  }, [navWidth, isNavCollapsed])

  const toggleNav = () => {
    if (isNavCollapsed) {
      setIsNavCollapsed(false)
      setNavWidth(clampSettingsNavWidth(navExpandedWidthRef.current))
      return
    }
    navExpandedWidthRef.current = navWidth
    setIsNavCollapsed(true)
    setNavWidth(SETTINGS_NAV_COLLAPSED)
  }

  const onResizePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    if (isNavCollapsed) return
    navDragRef.current = { startX: e.clientX, startWidth: navWidth }
    const moveHandler = (ev: PointerEvent) => {
      if (!navDragRef.current) return
      const next = navDragRef.current.startWidth + (ev.clientX - navDragRef.current.startX)
      const clamped = clampSettingsNavResize(next)
      navExpandedWidthRef.current = clamped
      setNavWidth(clamped)
    }
    const stopHandler = () => {
      navDragRef.current = null
      document.body.style.userSelect = ''
      window.removeEventListener('pointermove', moveHandler)
      window.removeEventListener('pointerup', stopHandler)
      window.removeEventListener('pointercancel', stopHandler)
    }
    document.body.style.userSelect = 'none'
    window.addEventListener('pointermove', moveHandler)
    window.addEventListener('pointerup', stopHandler)
    window.addEventListener('pointercancel', stopHandler)
  }

  useEffect(() => {
    const requestedTab = parseTab(searchParams.get('tab'))
    if (requestedTab && requestedTab !== tab) setTab(requestedTab)
  }, [searchParams, tab])

  useEffect(() => {
    const section = searchParams.get('section')
    if (!section) return
    const timer = window.setTimeout(() => {
      document.getElementById(`settings-section-${section}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [searchParams, tab, settings])

  // A settings refetch is only a save acknowledgement for the delegated
  // fallback when no browser-managed draft remains. Uncontrolled inputs keep
  // their DOM value across React renders, so a sibling save must not clear the
  // warning while that value still differs from its server default.
  const lastSettledRef = useRef(settingsUpdatedAt)
  useEffect(() => {
    if (settingsUpdatedAt === lastSettledRef.current) return
    lastSettledRef.current = settingsUpdatedAt
    const fallbackDraftRemains = [...touchedControlsRef.current].some(hasUncontrolledDraft)
    if (fallbackDraftRemains) return
    touchedControlsRef.current.clear()
    touchedRef.current = false
    setIsTouched(false)
  }, [settingsUpdatedAt])

  // Warn before a hard reload / tab close / cross-origin navigation. react-router's
  // useBlocker cannot cover this case ("does not handle hard-reloads or cross-origin
  // navigations") and additionally requires a data router, which this app does not use.
  useEffect(() => {
    if (!shouldBlockNavigation(dirtyRef.current.size, isTouched)) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [isTouched, preciseDirtyCount])

  // `slug` is set when navigation came from a search result, so the deep-link
  // effect above can scroll to that section instead of leaving the operator at
  // the top of an 11-section tab.
  const selectTab = (next: Tab, slug?: string) => {
    if (shouldBlockNavigation(dirtyRef.current.size, touchedRef.current)) {
      if (!confirm('You have unsaved changes on this tab. Switch tabs and discard them?')) return
    }
    clearDirty()
    setTab(next)
    const nextParams = new URLSearchParams(searchParams)
    nextParams.set('tab', next)
    if (slug) nextParams.set('section', slug)
    else nextParams.delete('section')
    setSearchParams(nextParams, { replace: true })
  }

  // Auto-select a numeric field's value on focus so typing replaces it instead
  // of appending (e.g. editing "1" → "3" no longer forces deleting the 1 first).
  // Delegated on the shell: React focus events bubble through child tab components,
  // so this covers every <input type="number"> without touching each one.
  const handleNumericFocus = (e: React.FocusEvent<HTMLDivElement>) => {
    const el = e.target as HTMLElement
    if (el instanceof HTMLInputElement && el.type === 'number') el.select()
  }
  // Stop blur-save editors from committing when focus moves to Discard. The
  // capture phase runs before a child input's onBlur, while the button remains
  // keyboard- and pointer-activatable normally.
  const suppressDiscardBlur = (e: React.FocusEvent<HTMLDivElement>) => {
    const next = e.relatedTarget as HTMLElement | null
    if (next?.dataset.settingsDiscard === 'true') e.stopPropagation()
  }

  if (statusPending || settingsPending) return (
    <div className="skeleton" style={{ maxWidth: '600px', paddingTop: 'var(--space-6)' }}>
      <div className="skeleton-bar" />
      <div className="skeleton-bar" />
      <div className="skeleton-bar" />
      <div className="skeleton-bar" style={{ width: '38%' }} />
      <div className="skeleton-bar" style={{ width: '62%', marginTop: 'var(--space-4)' }} />
      <div className="skeleton-bar" />
    </div>
  )

  if (!status || !settings) {
    const failedAuthority = !status ? 'System status' : 'Settings configuration'
    return (
      <div className="settings-load-error" role="alert" style={{ maxWidth: '600px', paddingTop: 'var(--space-6)' }}>
        <p style={{ color: 'var(--red)', marginBottom: 'var(--space-3)' }}>
          Failed to load {failedAuthority}. The operator cannot configure or diagnose the system.
        </p>
        <button
          type="button"
          className="secondary"
          onClick={() => { void refetchStatus(); void refetchSettings() }}
        >
          Retry
        </button>
      </div>
    )
  }

  return (
    <div>
      <PageHeader
        title="Settings"
        role="Operate · Configure AI agent behaviour, exchange connections, risk limits, and system infrastructure. Secrets are write-only and never displayed."
      />

      <SettingsSearch onPick={selectTab} />

      {shouldBlockNavigation(dirtyRef.current.size, isTouched) && (
        <div className="settings-draft-banner" role="status">
          <span>Unsaved staged changes — Save them in their section or discard this draft.</span>
          <button type="button" className="secondary" data-settings-discard="true" onClick={discardDrafts}>
            Discard changes
          </button>
        </div>
      )}

      <div
        className={`settings-shell ${isNavCollapsed ? 'is-collapsed' : ''}`}
        style={{ '--settings-nav-width': `${navWidth}px` } as React.CSSProperties}
        onBlurCapture={suppressDiscardBlur}
        onFocus={handleNumericFocus}
        onInput={markTouched}
        onChange={markTouched}
      >
        <aside className={`settings-nav ${isNavCollapsed ? 'is-collapsed' : ''}`} aria-label="Settings sections">
          <div className="settings-nav-header">
            <span className="settings-nav-heading">Settings</span>
            <button
              type="button"
              className="settings-nav-toggle"
              aria-label={isNavCollapsed ? 'Expand Settings navigation' : 'Collapse Settings navigation'}
              aria-expanded={!isNavCollapsed}
              title={isNavCollapsed ? 'Expand Settings navigation' : 'Collapse Settings navigation'}
              onClick={toggleNav}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d={isNavCollapsed ? 'm9 18 6-6-6-6' : 'm15 18-6-6 6-6'} /></svg>
            </button>
          </div>
          {TABS.map(t => (
            <button
              key={t.id}
              className={`settings-nav-item ${tab === t.id ? 'active' : ''}`}
              aria-label={t.label}
              aria-current={tab === t.id ? 'page' : undefined}
              title={isNavCollapsed ? `${t.label}: ${t.description}` : undefined}
              onClick={() => selectTab(t.id)}
            >
              <span className="settings-nav-icon"><SettingsNavIcon tab={t.id} /></span>
              <span className="settings-nav-copy">
                <span className="settings-nav-label">{t.label}</span>
                <span className="settings-nav-desc">{t.description}</span>
              </span>
            </button>
          ))}
        </aside>
        <div
          className="settings-nav-resize-handle"
          aria-hidden="true"
          onPointerDown={onResizePointerDown}
        />
        <div className="settings-panel" key={draftEpoch}>
          {tab === 'agent'     && <AgentModelsTab        status={status} settings={settings} qc={qc} onDirty={registerDirty} />}
          {tab === 'execution' && <ExecutionTab          status={status} settings={settings} qc={qc} onDirty={registerDirty} />}
          {tab === 'risk'      && <RiskTab               settings={settings} qc={qc} onDirty={registerDirty} />}
          {tab === 'health'    && <HealthNotificationsTab settings={settings} qc={qc} onDirty={registerDirty} />}
          {tab === 'runtime'   && <RuntimeSystemTab      status={status} settings={settings} qc={qc} onDirty={registerDirty} />}
          {tab === 'signals'   && <SignalsTab            settings={settings} qc={qc} onDirty={registerDirty} />}
          {tab === 'advanced'  && <AdvancedTab           settings={settings} />}
        </div>
      </div>
    </div>
  )
}

/**
 * Inline fuzzy search across all Settings sections. ⌘/Ctrl + K to focus, Esc to close.
 * Results are tab- AND section-aware: picking a result switches tab and passes the
 * registry slug so the ?section= effect scrolls to the exact section (#632).
 */
function SettingsSearch({ onPick }: { onPick: (tab: Tab, slug?: string) => void }) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  // Cmd/Ctrl+K from anywhere on the page → focus search.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  const results = q.trim() ? SETTINGS_INDEX.filter(it => matchesSearch(it, q)) : []
  const pick = (it: typeof SETTINGS_INDEX[number]) => {
    onPick(it.tab, it.slug)
    setQ(''); setOpen(false); setActiveIdx(0)
    inputRef.current?.blur()
  }
  return (
    <div className="settings-search">
      <input
        ref={inputRef}
        type="search"
        className="settings-search-input"
        placeholder="Search settings…  (⌘K)"
        value={q}
        onChange={e => { setQ(e.target.value); setOpen(true); setActiveIdx(0) }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={e => {
          if (e.key === 'Escape') { setQ(''); setOpen(false); inputRef.current?.blur() }
          else if (e.key === 'ArrowDown') { e.preventDefault(); setActiveIdx(i => Math.min(i + 1, results.length - 1)) }
          else if (e.key === 'ArrowUp')   { e.preventDefault(); setActiveIdx(i => Math.max(i - 1, 0)) }
          else if (e.key === 'Enter' && results[activeIdx]) { e.preventDefault(); pick(results[activeIdx]) }
        }}
        aria-label="Search settings"
        aria-autocomplete="list"
        aria-expanded={open && results.length > 0}
      />
      {open && q.trim() && (
        <div className="settings-search-results" role="listbox">
          {results.length === 0 ? (
            <div className="settings-search-empty">No matches.</div>
          ) : results.map((it, i) => (
            <button
              key={it.title}
              type="button"
              role="option"
              aria-selected={i === activeIdx}
              className={`settings-search-result${i === activeIdx ? ' active' : ''}`}
              // Use onMouseDown so the parent input's onBlur (which closes the dropdown)
              // doesn't fire BEFORE our click — onClick on a child that's about to be
              // unmounted by the blur would never run.
              onMouseDown={(e) => { e.preventDefault(); pick(it) }}
              onMouseEnter={() => setActiveIdx(i)}
            >
              <span className="settings-search-title">{it.title}</span>
              <span className="settings-search-tab">{TABS.find(t => t.id === it.tab)?.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
