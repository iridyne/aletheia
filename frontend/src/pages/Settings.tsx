import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import ConnectionStatus, { ConnState } from '../components/ConnectionStatus'
import PageHeader from '../components/PageHeader'
import {
  CollapsibleSection as Collapsible,
  SaveMessage as SaveMsg,
  SettingsRow as Row,
  SettingsSection,
  Switch as Toggle,
} from '../components/ui'
import { humanize } from '../lib/format'
import { isUnsavedEdit, shouldBlockNavigation } from '../features/settings/dirtyGuard'
import { executionExchangeFromPaperMode } from '../features/settings/execution/exchangeMode'
import { MODEL_PROVIDERS_SECTION } from '../features/settings/model-providers/modelProviderState'
import { HEALTH_MONITORING_SECTION, WEBHOOK_SECTION } from '../features/settings/health/healthState'
import { HealthNotificationsTab } from '../features/settings/tabs/HealthNotificationsTab'
import { AdvancedTab, ADVANCED_SECTION } from '../features/settings/tabs/AdvancedTab'
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

const CONFIDENCE_LEVELS = ['high', 'medium', 'speculative'] as const

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
  { title: 'Exchange Connection',      tab: 'execution', slug: 'exchange-connection', alt: ['bitget', 'okx', 'binance', 'api key'] },
  { title: 'Trading Mode — Advanced',  tab: 'execution', slug: 'trading-mode', alt: ['demo', 'live unlock', 'paper', 'trading mode'] },
  { title: 'Paper Trading Parameters', tab: 'execution', slug: 'paper-trading-parameters', alt: ['stop loss', 'take profit', 'hold time', 'confidence'] },
  { title: 'Capital Events',           tab: 'execution', slug: 'capital-events', alt: ['deposit', 'withdrawal', 'pnl baseline'] },
  { title: 'Local Paper Account',      tab: 'execution', slug: 'local-paper-account', alt: ['no exchange', 'paper cash', 'available balance', 'close positions'] },
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

/** Anchor id for a registered section title, or undefined when unregistered. */
export function sectionSlug(title: string): string | undefined {
  return SETTINGS_INDEX.find(it => it.title === title)?.slug
}

/** Registry export for the drift test — see settings.test.ts. */
export const SETTINGS_SECTION_TITLES: readonly string[] = SETTINGS_INDEX.map(it => it.title)

/**
 * Settings-local wrapper that resolves the anchor id from SETTINGS_INDEX instead
 * of requiring each of the 30 call sites to repeat it. Registering a section in
 * one place is what makes `?section=<slug>` and ⌘K land on it; before #632 only
 * one call site passed an id, so 29 deep links silently scrolled nowhere.
 */
function Section(props: { title: string; description?: string; children: React.ReactNode }) {
  return <SettingsSection {...props} id={sectionSlug(props.title)} />
}

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
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: settings, dataUpdatedAt: settingsUpdatedAt } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
  const [navWidth, setNavWidth] = useState(SETTINGS_NAV_DEFAULT)
  const [isNavCollapsed, setIsNavCollapsed] = useState(false)
  const navExpandedWidthRef = useRef(SETTINGS_NAV_DEFAULT)
  const navDragRef = useRef<{ startX: number; startWidth: number } | null>(null)
  const [tab, setTab] = useState<Tab>(() => {
    return parseTab(searchParams.get('tab')) ?? 'agent'
  })
  const dirtyRef = useRef<Set<string>>(new Set())
  const registerDirty = (id: string, isDirty: boolean) => {
    if (isDirty) dirtyRef.current.add(id)
    else dirtyRef.current.delete(id)
  }
  // Delegated fallback for the 26 sections that do not call registerDirty.
  // See features/settings/dirtyGuard.ts for why this is a shell-level listener.
  const touchedRef = useRef(false)
  const [isTouched, setIsTouched] = useState(false)
  const markTouched = (e: React.FormEvent<HTMLDivElement>) => {
    if (touchedRef.current) return
    if (!isUnsavedEdit(e.target as HTMLElement | null)) return
    touchedRef.current = true
    setIsTouched(true)   // state (not just ref) so the beforeunload effect re-runs
  }
  const clearDirty = () => {
    dirtyRef.current.clear()
    touchedRef.current = false
    setIsTouched(false)
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

  // Every section's save calls invalidateQueries(['settings']), so a refetch of
  // `settings` is the shell-visible signal that a save landed. Without this the
  // operator would be warned about edits they already saved, which teaches them
  // to dismiss the prompt — the same failure mode as not warning at all.
  // Skipped on first load: refs start clean, so there is nothing to reset.
  const lastSettledRef = useRef(settingsUpdatedAt)
  useEffect(() => {
    if (settingsUpdatedAt === lastSettledRef.current) return
    lastSettledRef.current = settingsUpdatedAt
    clearDirty()
  }, [settingsUpdatedAt])

  // Warn before a hard reload / tab close / cross-origin navigation. react-router's
  // useBlocker cannot cover this case ("does not handle hard-reloads or cross-origin
  // navigations") and additionally requires a data router, which this app does not use.
  useEffect(() => {
    if (!shouldBlockNavigation(dirtyRef.current.size, isTouched)) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [isTouched])

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

  if (!status || !settings) return (
    <div className="skeleton" style={{ maxWidth: '600px', paddingTop: 'var(--space-6)' }}>
      <div className="skeleton-bar" />
      <div className="skeleton-bar" />
      <div className="skeleton-bar" />
      <div className="skeleton-bar" style={{ width: '38%' }} />
      <div className="skeleton-bar" style={{ width: '62%', marginTop: 'var(--space-4)' }} />
      <div className="skeleton-bar" />
    </div>
  )

  return (
    <div>
      <PageHeader
        title="Settings"
        role="Operate · Configure AI agent behaviour, exchange connections, risk limits, and system infrastructure. Secrets are write-only and never displayed."
      />

      <SettingsSearch onPick={selectTab} />

      <div
        className={`settings-shell ${isNavCollapsed ? 'is-collapsed' : ''}`}
        style={{ '--settings-nav-width': `${navWidth}px` } as React.CSSProperties}
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
        <div className="settings-panel">
          {tab === 'agent'     && <AgentModelsTab        status={status} settings={settings} qc={qc} />}
          {tab === 'execution' && <ExecutionTab          status={status} settings={settings} qc={qc} onDirty={registerDirty} />}
          {tab === 'risk'      && <RiskTab               settings={settings} qc={qc} onDirty={registerDirty} />}
          {tab === 'health'    && <HealthNotificationsTab settings={settings} qc={qc} />}
          {tab === 'runtime'   && <RuntimeSystemTab      status={status} settings={settings} qc={qc} />}
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

// ─────────────────────────────────────────────────────────────────────────────
// INTELLIGENCE TAB — agent behaviour, permission, autonomy, scheduler
// ─────────────────────────────────────────────────────────────────────────────




// ─────────────────────────────────────────────────────────────────────────────
// EXECUTION TAB — exchange, credentials, mode, markets, live unlock, trading params
// ─────────────────────────────────────────────────────────────────────────────


function ExecutionTab({ status, settings, qc, onDirty }: any) {
  const currentPaperMode = String((settings?.exchange as any)?.paper_execution_mode || '')
  const currentExecutionExchange = executionExchangeFromPaperMode(
    currentPaperMode, String((settings?.exchange as any)?.name || ''),
  )
  const currentDataExchange = String((settings?.market_data as any)?.ccxt_exchange || '')
  const currentDiverged = currentExecutionExchange !== null
    && currentExecutionExchange !== currentDataExchange
  return (
    <div>
      {currentDiverged && (
        <div className="settings-save-msg err mb-3">
          Exchange split active: orders execute on <strong>{currentExecutionExchange}</strong>,
          while market analysis uses <strong>{currentDataExchange}</strong>. Keep this only if intentional.
        </div>
      )}
      <Section
        title="Exchange Connection"
        description="Select primary exchange to unify data source, paper execution, and risk allowlist. Then configure API credentials below."
      >
        <PrimaryExchangeCard settings={settings} qc={qc} />
        <ExchangeConnectionCard qc={qc} />
      </Section>

      <Section title="Trading Mode — Advanced" description="Configure demo execution or inspect the experimental Live readiness controls. Live Unlock currently records an authority prerequisite; it does not route new proposals away from the paper/demo execution mode selected below.">
        <ExchangeModeRow status={status} qc={qc} settings={settings} />
        <ExchangeMarketsRow settings={settings} qc={qc} onDirty={onDirty} />
        <div className="danger-zone">
          <div className="danger-zone-header">Danger Zone</div>
          <LiveStateRow status={status} qc={qc} />
        </div>
      </Section>

      <Section
        title="Paper Trading Parameters"
        description="Default exit rules applied when the paper position manager opens a new trade."
      >
        <TradingParamsCard settings={settings} qc={qc} onDirty={onDirty} />
      </Section>

      <Section
        title="Capital Events"
        description="Record deposits and withdrawals as audited cash-flow history. Account Equity remains visible as a raw balance series; when events fall inside the visible window, Overview also renders a separate cash-flow-adjusted series. Cumulative PnL remains verified trading PnL."
      >
        <CapitalEventsCard />
      </Section>

      {String((settings?.exchange as any)?.paper_execution_mode) === 'paper_internal' && (
        <Section
          title="Local Paper Account"
          description="No exchange only. Manage local simulated cash and positions; every change is recorded for audit and shown to the trading agent."
        >
          <PaperAccountCard qc={qc} />
        </Section>
      )}

    </div>
  )
}

function PaperAccountCard({ qc }: { qc: ReturnType<typeof useQueryClient> }) {
  const { data: account, isLoading } = useQuery({ queryKey: ['paper-account'], queryFn: api.getPaperAccount })
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['paper-account'] })
    qc.invalidateQueries({ queryKey: ['positions'] })
    qc.invalidateQueries({ queryKey: ['portfolio'] })
  }
  const mutate = useMutation({
    mutationFn: ({ action, value }: { action: 'credit' | 'debit' | 'set'; value: number }) => {
      if (action === 'credit') return api.creditPaperAccount(value, note)
      if (action === 'debit') return api.debitPaperAccount(value, note)
      return api.setPaperAccountBalance(value, note)
    },
    onSuccess: () => { setMsg('Saved local paper account change'); setAmount(''); setNote(''); refresh() },
    onError: (error: any) => setMsg(error.message || 'Account change failed'),
  })
  const closeMutation = useMutation({
    mutationFn: (spotOnly: boolean) => spotOnly ? api.closePaperSpotPositions() : api.closeAllPaperPositions(),
    onSuccess: (result) => { setMsg(`Closed ${result.closed_position_ids?.length ?? 0} local position(s)`); refresh() },
    onError: (error: any) => setMsg(error.message || 'Could not close positions'),
  })
  const value = Number(amount)
  const valid = Number.isFinite(value) && value >= 0

  if (isLoading) return <p className="hint">Loading local paper account…</p>
  return (
    <div>
      <div className="settings-number-grid" style={{ marginBottom: '1rem' }}>
        {[
          ['Cash balance', account?.cash_balance_usdt],
          ['Reserved margin', account?.reserved_margin_usdt],
          ['Available balance', account?.available_balance_usdt],
          ['Equity', account?.equity_usdt],
        ].map(([label, raw]) => (
          <label className="settings-field" key={String(label)}>
            <span>{label}</span>
            <input
              readOnly
              value={raw == null ? 'unavailable' : `${Number(raw).toFixed(2)} USDT`}
              className="mono"
            />
          </label>
        ))}
      </div>
      {account?.economics_complete === false && (
        <p className="hint" style={{ marginBottom: '1rem' }}>
          Account economics unavailable: {account.unsettled_closed_positions_count} closed position(s) still lack complete funding or close costs. New local-paper capacity is held at zero until reconciled.
        </p>
      )}
      <Row label="Amount" hint="Add, remove, or set local simulated cash. Setting cash to 0 is preserved and blocks new local paper orders until funded again.">
        <input type="number" min="0" step="0.01" value={amount} onChange={e => { setAmount(e.target.value); setMsg(null) }} placeholder="200" />
        <input value={note} onChange={e => setNote(e.target.value)} placeholder="Reason (shown to agent)" style={{ minWidth: '180px' }} />
        <button disabled={!valid || mutate.isPending} onClick={() => mutate.mutate({ action: 'credit', value })}>Add funds</button>
        <button className="secondary" disabled={!valid || value <= 0 || mutate.isPending} onClick={() => mutate.mutate({ action: 'debit', value })}>Remove funds</button>
        <button className="secondary" disabled={!valid || mutate.isPending} onClick={() => {
          if (confirm(`Set local paper cash balance to ${value.toFixed(2)} USDT?`)) mutate.mutate({ action: 'set', value })
        }}>Set balance</button>
      </Row>
      <div className="danger-zone" style={{ marginTop: '1rem' }}>
        <div className="danger-zone-header">Position Management</div>
        <Row label="Close local spot positions" hint="Closes every open local paper spot position at its last stored market price. Trade history remains intact.">
          <button className="secondary" disabled={closeMutation.isPending} onClick={() => {
            if (confirm('Close all local paper spot positions?')) closeMutation.mutate(true)
          }}>Close local spot positions</button>
        </Row>
        <Row label="Close all local positions" hint="Closes every open local paper position, including futures, at its last stored market price.">
          <button disabled={closeMutation.isPending} onClick={() => {
            if (confirm('Close every open local paper position?')) closeMutation.mutate(false)
          }}>Close all local positions</button>
        </Row>
      </div>
      {msg && <p className="hint" style={{ marginTop: '0.75rem' }}>{msg}</p>}
      {(account?.events || []).length > 0 && (
        <div style={{ marginTop: '1rem' }}>
          <p className="hint">Recent local account activity</p>
          {(account.events as any[]).slice(0, 5).map(event => (
            <p className="hint mono" key={event.id} style={{ margin: '0.2rem 0' }}>
              {event.created_at} · {event.action} · {Number(event.amount_usdt).toFixed(2)} USDT {event.note ? `· ${event.note}` : ''}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

function CapitalEventsCard() {
  const qc = useQueryClient()
  const { data: events = [] } = useQuery({ queryKey: ['capital-events'], queryFn: api.getCapitalEvents })
  const { data: netData } = useQuery({ queryKey: ['capital-net-deposits'], queryFn: api.getNetDeposits })
  const [brokerId, setBrokerId] = useState('')
  const [eventType, setEventType] = useState<'deposit' | 'withdraw'>('deposit')
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [msg, setMsg] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () => api.createCapitalEvent({
      broker_id: brokerId || 'manual',
      event_type: eventType,
      amount_usdt: parseFloat(amount),
      note: note || undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['capital-events'] })
      qc.invalidateQueries({ queryKey: ['capital-net-deposits'] })
      qc.invalidateQueries({ queryKey: ['account-equity-history'] })
      setAmount('')
      setNote('')
      setMsg('Recorded')
      setTimeout(() => setMsg(null), 3000)
    },
    onError: (e: any) => setMsg(e.message || 'Failed'),
  })

  const netDeposits = netData?.net_deposits_usdt ?? 0

  return (
    <div>
      <p className="text-muted text-sm mb-2">Net deposits: <strong>{netDeposits.toFixed(2)} USDT</strong></p>
      <div className="capital-form">
        <select value={eventType} onChange={e => setEventType(e.target.value as any)}>
          <option value="deposit">Deposit</option>
          <option value="withdraw">Withdraw</option>
        </select>
        <input type="text" placeholder="Broker ID (e.g. bitget, okx)" value={brokerId} onChange={e => setBrokerId(e.target.value)} />
        <input type="number" placeholder="Amount (USDT)" min={0} step={0.01} value={amount} onChange={e => setAmount(e.target.value)} />
        <input type="text" placeholder="Note (optional)" value={note} onChange={e => setNote(e.target.value)} />
        <button className="btn btn-primary" disabled={!amount || parseFloat(amount) <= 0} onClick={() => mutation.mutate()}>Record</button>
      </div>
      {msg && <p className="save-msg mt-1">{msg}</p>}
      {(events as any[]).length > 0 && (
        <table className="mini-table mt-2">
          <thead><tr><th>Date</th><th>Type</th><th>Broker</th><th>Amount</th><th>Note</th></tr></thead>
          <tbody>
            {(events as any[]).slice(0, 20).map((e: any) => (
              <tr key={e.id}>
                <td>{(e.detected_at || '').slice(0, 10)}</td>
                <td>{e.event_type}</td>
                <td>{e.broker_id}</td>
                <td>{Number(e.amount_usdt).toFixed(2)}</td>
                <td>{e.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}


const PRIMARY_EXCHANGES = [
  { id: 'okx',           label: 'OKX',             desc: 'Data + paper execution via OKX Demo' },
  { id: 'bitget',        label: 'Bitget',           desc: 'Data + paper execution via Bitget Demo' },
  { id: 'binance',       label: 'Binance',          desc: 'Data + paper execution via Binance Testnet' },
  { id: 'paper_internal',label: 'Paper (internal)', desc: 'No exchange — local fill simulation only' },
] as const

function PrimaryExchangeCard({ settings, qc }: any) {
  const exchangeName = String((settings?.exchange as any)?.name || 'bitget')
  const paperMode = String((settings?.exchange as any)?.paper_execution_mode || 'bitget_demo')
  const effective = paperMode === 'paper_internal' ? 'paper_internal' : exchangeName

  const [err, setErr] = useState<string | null>(null)
  const mut = useMutation({
    mutationFn: (id: string) => api.setPrimaryExchange(id),
    onSuccess: () => { setErr(null); qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
    onError: (e: any) => setErr(e.message || 'Failed to switch exchange'),
  })

  return (
    <div>
      <Row
        label="Primary exchange"
        hint="Atomically updates market data source, paper execution mode, and risk allowlist. Paper (internal) skips exchange connectivity entirely."
      >
        <select
          value={effective}
          onChange={e => {
            const id = e.target.value
            if (id === effective) return
            const label = PRIMARY_EXCHANGES.find(ex => ex.id === id)?.label ?? id
            if (!confirm(`Switch primary exchange to ${label}?\n\nThis atomically updates data source, paper execution mode, and risk allowlist.`)) return
            mut.mutate(id)
          }}
          disabled={mut.isPending}
          className="w-auto"
        >
          {PRIMARY_EXCHANGES.map(ex => (
            <option key={ex.id} value={ex.id}>{ex.label}</option>
          ))}
        </select>
      </Row>
      {err && <div className="settings-save-msg err mt-2">{err}</div>}
    </div>
  )
}

function ExchangeModeRow({ status, qc, settings }: any) {
  const paperMode = String((settings?.exchange as any)?.paper_execution_mode || 'bitget_demo')
  const modeLabel: Record<string, string> = {
    okx_demo: 'OKX Demo', bitget_demo: 'Bitget Demo',
    binance_testnet: 'Binance Testnet', paper_internal: 'Paper (internal)',
  }
  const mut = useMutation({
    mutationFn: (mode: string) => api.setExchangeMode(mode),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })
  return (
    <Row label="Exchange mode" hint={`demo uses ${modeLabel[paperMode] ?? paperMode} for paper trading. live is an experimental readiness intent only; new proposals still use ${modeLabel[paperMode] ?? paperMode}.`}>
      <select value={String(status.exchange_mode)} onChange={e => {
        const next = e.target.value
        if (next === 'live' && !confirm('Select experimental live readiness? This does not route new proposals to real-fund execution.')) return
        mut.mutate(next)
      }} className="w-auto">
        <option value="demo">demo</option>
        <option value="live">live</option>
      </select>
    </Row>
  )
}

function ExchangeMarketsRow({ settings, qc, onDirty }: any) {
  const markets = (settings?.exchange as any)?.enabled_markets || { spot: true, usdt_futures: true }
  const serverPaperMode = String((settings?.exchange as any)?.paper_execution_mode || 'bitget_demo')
  const [spot, setSpot] = useState(!!markets.spot)
  const [futures, setFutures] = useState(!!markets.usdt_futures)
  const [paperMode, setPaperMode] = useState(serverPaperMode)
  const [confirmExchangeDivergence, setConfirmExchangeDivergence] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    setSpot(!!markets.spot)
    setFutures(!!markets.usdt_futures)
    setPaperMode(serverPaperMode)
    setConfirmExchangeDivergence(false)
  }, [settings])

  const dataExchange = String((settings?.market_data as any)?.ccxt_exchange || '')
  const executionExchange = executionExchangeFromPaperMode(
    paperMode, String((settings?.exchange as any)?.name || ''),
  )
  const createsDivergence = paperMode !== serverPaperMode
    && executionExchange !== null
    && executionExchange !== dataExchange

  const mut = useMutation({
    mutationFn: () => api.setExchangeMarkets({
      spot,
      usdt_futures: futures,
      paper_execution_mode: paperMode,
      confirm_exchange_divergence: confirmExchangeDivergence,
    }),
    onSuccess: () => { setConfirmExchangeDivergence(false); setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const dirty = spot !== !!markets.spot || futures !== !!markets.usdt_futures
    || paperMode !== serverPaperMode

  useEffect(() => { onDirty?.('exchange-markets', dirty) }, [dirty])

  return (
    <>
      <Row label="Enabled markets" hint="Which Bitget market types agents can place orders in. At least one must be enabled.">
        <Toggle checked={spot} label="Spot" onChange={v => { setSpot(v); setMsg(null) }} />
        <Toggle checked={futures} label="USDT Futures" onChange={v => { setFutures(v); setMsg(null) }} />
      </Row>
      <Row label="Paper execution mode" hint="Where autonomous paper orders execute. okx_demo = OKX simulated trading (broad coverage); binance_testnet = Binance Futures Testnet (556 pairs); bitget_demo = Bitget demo API (42 pairs); paper_internal = local simulator (no exchange).">
        <select value={paperMode} onChange={e => { setPaperMode(e.target.value); setConfirmExchangeDivergence(false); setMsg(null) }} className="w-auto">
          <option value="okx_demo">OKX Demo</option>
          <option value="binance_testnet">Binance Testnet</option>
          <option value="bitget_demo">Bitget Demo</option>
          <option value="paper_internal">Paper (internal)</option>
        </select>
        {createsDivergence && (
          <Toggle
            checked={confirmExchangeDivergence}
            onChange={setConfirmExchangeDivergence}
            label={`Confirm ${executionExchange} execution with ${dataExchange} market data`}
          />
        )}
        {dirty && (
          <button className="secondary" onClick={() => mut.mutate()} disabled={mut.isPending || (!spot && !futures) || (createsDivergence && !confirmExchangeDivergence)}>
            {mut.isPending ? 'Saving…' : 'Save exchange config'}
          </button>
        )}
      </Row>
      <SaveMsg msg={msg} />
    </>
  )
}

// Live unlock warning wall — §4b of live_unlock_contract.md.
// This is an experimental authority-state prerequisite, not proof that the
// new-Proposal path can select a live execution mode (EX-04 / GitHub #418).
function LiveUnlockWarningWall({ credError, onConfirm, onCancel }: {
  credError?: string
  onConfirm: () => void
  onCancel: () => void
}) {
  // Styling only: this wall previously hardcoded `var(--color-danger-bg, #fff5f5)`,
  // and neither --color-danger nor --color-danger-bg is declared in index.css, so
  // the fallback painted a near-white panel on this app's near-black --bg #0a0e12.
  // Now uses the shared .danger-zone classes.
  //
  // COPY IS CONTRACT-BOUND — DO NOT REWORD OR TRANSLATE.
  // docs/design/contracts/live_unlock_contract.md §4b mandates these three
  // honest-boundary elements, and tests/specs/spec_live_unlock.py::
  // test_ui_warning_wall_three_elements (claim LU-S03) asserts the literal
  // strings. Changing the wording requires updating the contract and its
  // executable spec in the same change.
  return (
    <div className="danger-zone" role="alertdialog" aria-label="实验性 Live 权限准备确认">
      <div className="danger-zone-header">实验性 Live 权限准备 — 请仔细阅读</div>
      <ul className="settings-danger-list">
        <li>此操作只记录 live_limited 权限前置状态，不会把新 Proposal 路由到实盘</li>
        <li>当前自主 Proposal 仍使用 Paper execution mode 中选择的模拟执行通道</li>
        <li>只有未来独立完成并验证 live Proposal 路由后，真实资金风险才会出现</li>
      </ul>
      {credError && (
        <p className="settings-danger-error">
          ⚠ 凭证预检失败：{credError}。建议先修复凭证再解锁。
        </p>
      )}
      <div className="settings-action-row settings-action-row-start">
        <button onClick={onConfirm}>确认记录实验性权限状态</button>
        <button className="secondary" onClick={onCancel}>取消</button>
      </div>
    </div>
  )
}

function LiveStateRow({ status, qc }: any) {
  const unlock = useMutation({ mutationFn: api.liveUnlock, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const lock = useMutation({ mutationFn: api.liveLock, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const isDisabled = status.live_state === 'live_disabled'
  const [warning, setWarning] = useState('')
  const [showWall, setShowWall] = useState(false)
  const [credError, setCredError] = useState<string | undefined>(undefined)

  const preflightUnlock = async () => {
    setWarning('')
    setCredError(undefined)
    try {
      const r = await api.testBitget('live')
      if (!r.connected) {
        setCredError(r.error || 'unknown')
      }
    } catch {
      // Intentional: credential pre-flight is advisory only. The backend enforces its
      // own gate (state must be live_disabled + confirm=true). A network error here
      // means we cannot verify credentials, but the user still sees the warning wall
      // and can decide. The backend will catch any real credential issue when the
      // first live order is attempted.
    }
    setShowWall(true)
  }

  const handleConfirmUnlock = () => {
    setShowWall(false)
    unlock.mutate()
  }

  return (
    <div>
      <Row label="Experimental Live authority state" hint="Disabled by default. Unlocking runs a credential pre-flight, requires confirmation, and is audited, but does not change new-Proposal routing. New autonomous proposals remain paper/demo until EX-04 is implemented and verified." danger={!isDisabled}>
        <span className={`badge ${isDisabled ? 'badge-muted' : 'badge-yellow'}`}>{humanize(String(status.live_state))}</span>
        <button onClick={preflightUnlock} disabled={!isDisabled}>Unlock Live-Limited</button>
        <button className="secondary" onClick={() => lock.mutate()} disabled={isDisabled}>Lock</button>
      </Row>
      {showWall && (
        <LiveUnlockWarningWall
          credError={credError}
          onConfirm={handleConfirmUnlock}
          onCancel={() => setShowWall(false)}
        />
      )}
      {warning && <div className="settings-save-msg err">⚠ {warning}</div>}
    </div>
  )
}

// ─── Exchange credentials ───

// ─── Unified Exchange Connection Card ───

const EXCHANGE_CONFIG = [
  { id: 'bitget', name: 'Bitget', profiles: ['demo', 'live'] as const, hasPassphrase: true },
  { id: 'binance', name: 'Binance', profiles: ['testnet', 'live'] as const, hasPassphrase: false },
  { id: 'okx', name: 'OKX', profiles: ['demo', 'live'] as const, hasPassphrase: true },
] as const

type ExchangeId = typeof EXCHANGE_CONFIG[number]['id']

function ExchangeConnectionCard({ qc }: any) {
  const [exchange, setExchange] = useState<ExchangeId>('bitget')
  const exCfg = EXCHANGE_CONFIG.find(e => e.id === exchange)!
  const [profile, setProfile] = useState<string>(exCfg.profiles[0])
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [passphrase, setPassphrase] = useState('')
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null)

  // Reset form when switching exchange/profile
  useEffect(() => {
    setApiKey(''); setApiSecret(''); setPassphrase(''); setResult(null)
  }, [exchange, profile])

  // Ensure profile is valid for the selected exchange
  useEffect(() => {
    const cfg = EXCHANGE_CONFIG.find(e => e.id === exchange)!
    if (!cfg.profiles.includes(profile as any)) {
      setProfile(cfg.profiles[0])
    }
  }, [exchange, profile])

  // Credential status queries
  const bitgetCreds = useQuery({ queryKey: ['bitgetCreds'], queryFn: api.getBitgetCredStatus })
  const binanceCreds = useQuery({ queryKey: ['binanceCreds'], queryFn: api.getBinanceCredStatus })
  const okxCreds = useQuery({ queryKey: ['okxCreds'], queryFn: api.getOkxCredStatus })

  const getCredData = () => {
    if (exchange === 'bitget') return bitgetCreds.data
    if (exchange === 'binance') return binanceCreds.data
    return okxCreds.data
  }

  const credData = getCredData()
  const cur = credData?.[profile as keyof typeof credData] as any

  // Connection test queries for all profiles of current exchange
  const bitgetDemoTest = useQuery({
    queryKey: ['exchange-test', 'bitget', 'demo'],
    queryFn: () => api.testBitget('demo'),
    refetchInterval: 12000,
    enabled: !!bitgetCreds.data?.demo?.api_key?.configured,
  })
  const bitgetLiveTest = useQuery({
    queryKey: ['exchange-test', 'bitget', 'live'],
    queryFn: () => api.testBitget('live'),
    refetchInterval: 12000,
    enabled: !!bitgetCreds.data?.live?.api_key?.configured,
  })
  const binanceTestnetTest = useQuery({
    queryKey: ['exchange-test', 'binance', 'testnet'],
    queryFn: () => api.testBinance('testnet'),
    refetchInterval: 15000,
    enabled: !!binanceCreds.data?.testnet?.api_key?.configured,
  })
  const binanceLiveTest = useQuery({
    queryKey: ['exchange-test', 'binance', 'live'],
    queryFn: () => api.testBinance('live'),
    refetchInterval: 15000,
    enabled: !!binanceCreds.data?.live?.api_key?.configured,
  })
  const okxDemoTest = useQuery({
    queryKey: ['exchange-test', 'okx', 'demo'],
    queryFn: () => api.testOkx('demo'),
    refetchInterval: 15000,
    enabled: !!okxCreds.data?.demo?.api_key?.configured,
  })
  const okxLiveTest = useQuery({
    queryKey: ['exchange-test', 'okx', 'live'],
    queryFn: () => api.testOkx('live'),
    refetchInterval: 15000,
    enabled: !!okxCreds.data?.live?.api_key?.configured,
  })

  const getTestResult = (exId: string, prof: string) => {
    const map: Record<string, any> = {
      'bitget:demo': bitgetDemoTest, 'bitget:live': bitgetLiveTest,
      'binance:testnet': binanceTestnetTest, 'binance:live': binanceLiveTest,
      'okx:demo': okxDemoTest, 'okx:live': okxLiveTest,
    }
    return map[`${exId}:${prof}`]
  }

  const getConnStatus = (exId: string, prof: string): { state: ConnState; text: string } => {
    const cData = exId === 'bitget' ? bitgetCreds.data : exId === 'binance' ? binanceCreds.data : okxCreds.data
    const profData = cData?.[prof as keyof typeof cData] as any
    const configured = !!profData?.api_key?.configured
    if (!configured) return { state: 'muted', text: 'not configured' }
    const testQ = getTestResult(exId, prof)
    if (!testQ?.data) return { state: 'yellow', text: 'checking…' }
    if (testQ.data.connected) return { state: 'green', text: 'connected' }
    return { state: 'red', text: `✗ ${testQ.data.error || 'unreachable'}` }
  }

  // Verify & Save
  const verifyAndSave = useMutation({
    mutationFn: async () => {
      setResult(null)
      const hasAllRequired = exCfg.hasPassphrase
        ? !!(apiKey && apiSecret && passphrase)
        : !!(apiKey && apiSecret)

      if (hasAllRequired) {
        // Test candidate before saving
        let testResult: any
        if (exchange === 'bitget') {
          testResult = await api.testBitgetCandidate({ profile, api_key: apiKey, api_secret: apiSecret, passphrase })
        } else if (exchange === 'binance') {
          testResult = await api.testBinanceCandidate({ mode: profile, api_key: apiKey, api_secret: apiSecret })
        } else {
          testResult = await api.testOkxCandidate({ mode: profile, api_key: apiKey, api_secret: apiSecret, passphrase })
        }
        if (!testResult.connected) throw new Error(testResult.error || 'connection failed')
      }

      // Save credentials
      if (exchange === 'bitget') {
        await api.setBitgetCredentials({ profile, api_key: apiKey || undefined, api_secret: apiSecret || undefined, passphrase: passphrase || undefined })
      } else if (exchange === 'binance') {
        await api.setBinanceCredentials({ profile, api_key: apiKey || undefined, api_secret: apiSecret || undefined })
      } else {
        await api.setOkxCredentials({ profile, api_key: apiKey || undefined, api_secret: apiSecret || undefined, passphrase: passphrase || undefined })
      }

      return hasAllRequired
    },
    onSuccess: (tested) => {
      setApiKey(''); setApiSecret(''); setPassphrase('')
      setResult({ ok: true, msg: tested ? '✓ verified & saved' : '✓ saved (partial)' })
      qc.invalidateQueries({ queryKey: ['bitgetCreds'] })
      qc.invalidateQueries({ queryKey: ['binanceCreds'] })
      qc.invalidateQueries({ queryKey: ['okxCreds'] })
      qc.invalidateQueries({ queryKey: ['exchange-test', exchange, profile] })
    },
    onError: (e: any) => setResult({ ok: false, msg: `✗ ${e.message} — not saved` }),
  })

  const dirty = exCfg.hasPassphrase ? !!(apiKey || apiSecret || passphrase) : !!(apiKey || apiSecret)

  return (
    <div className="card mb-0">
      {/* Exchange selector */}
      <div className="flex gap-2 mb-3">
        <select value={exchange} onChange={e => setExchange(e.target.value as ExchangeId)} style={{ flex: 1 }}>
          {EXCHANGE_CONFIG.map(ex => (
            <option key={ex.id} value={ex.id}>{ex.name}</option>
          ))}
        </select>
      </div>

      {/* Profile tabs */}
      <div className="flex gap-2 mb-3">
        {exCfg.profiles.map(p => (
          <button key={p} className={profile === p ? '' : 'secondary'} onClick={() => setProfile(p)}>
            {p === 'testnet' ? 'Testnet' : p === 'demo' ? 'Demo' : 'Live'}
          </button>
        ))}
      </div>

      {/* Connection status for all profiles of this exchange */}
      <div className="mb-3">
        {exCfg.profiles.map(p => {
          const { state, text } = getConnStatus(exchange, p)
          return <ConnectionStatus key={p} label={`${exCfg.name} ${p}`} state={state} text={text} />
        })}
      </div>

      {/* Credential fields */}
      <div className="form-grid">
        <label>
          <span>API Key{cur?.api_key?.configured ? ` (··${cur.api_key.last4})` : ''}</span>
          <input type="password" value={apiKey} placeholder={cur?.api_key?.configured ? '•••••• (keep)' : 'paste'} onChange={e => setApiKey(e.target.value)} />
        </label>
        <label>
          <span>API Secret{cur?.api_secret?.configured ? ' (set)' : ''}</span>
          <input type="password" value={apiSecret} placeholder={cur?.api_secret?.configured ? '•••••• (keep)' : 'paste'} onChange={e => setApiSecret(e.target.value)} />
        </label>
        {exCfg.hasPassphrase && (
          <label>
            <span>Passphrase{cur?.passphrase?.configured ? ' (set)' : ''}</span>
            <input type="password" value={passphrase} placeholder={cur?.passphrase?.configured ? '•••••• (keep)' : 'paste'} onChange={e => setPassphrase(e.target.value)} />
          </label>
        )}
      </div>

      {/* Save button */}
      <div className="flex gap-2 mt-3">
        <button onClick={() => verifyAndSave.mutate()} disabled={verifyAndSave.isPending || !dirty}>
          {verifyAndSave.isPending ? 'Verifying…' : `Verify & Save`}
        </button>
      </div>
      {result && <div className={`settings-save-msg ${result.ok ? 'ok' : 'err'}`}>{result.msg}</div>}
      <p className="hint">
        {profile === 'live'
          ? 'Live credentials connect to the real exchange. Ensure risk limits are configured before trading.'
          : exchange === 'binance'
            ? 'Get testnet credentials from testnet.binancefuture.com.'
            : `${exCfg.name} ${profile} uses simulated trading — no real funds at risk.`
        }
        {' '}Credentials are write-only.
      </p>
    </div>
  )
}

// ─── Trading parameters ───

function TradingParamsCard({ settings, qc, onDirty }: any) {
  const trading = settings?.trading as any
  const [stopLoss, setStopLoss] = useState('')
  const [takeProfit, setTakeProfit] = useState('')
  const [holdHours, setHoldHours] = useState('')
  const [slippageBps, setSlippageBps] = useState('')
  const [executionFeePct, setExecutionFeePct] = useState('')
  const [confidence, setConfidence] = useState('medium')
  const [autoAdopt, setAutoAdopt] = useState(true)
  const [trailEnabled, setTrailEnabled] = useState(false)
  const [exchangeTpslEnabled, setExchangeTpslEnabled] = useState(false)
  const [trailAtrMult, setTrailAtrMult] = useState('1.5')
  const [trailPct, setTrailPct] = useState('1.0')
  const [breakevenTriggerPct, setBreakevenTriggerPct] = useState('1.5')
  const [volSizingEnabled, setVolSizingEnabled] = useState(true)
  const [defaultNotional, setDefaultNotional] = useState('25')
  const [volRiskUsdt, setVolRiskUsdt] = useState('25')
  const [volTargetPct, setVolTargetPct] = useState('1.5')
  const [volKellyCap, setVolKellyCap] = useState('0.25')
  const [maeEnabled, setMaeEnabled] = useState(false)
  const [maeUrgentScore, setMaeUrgentScore] = useState('80')
  const [maeTightenScore, setMaeTightenScore] = useState('60')
  const [maeCollapseScore, setMaeCollapseScore] = useState('30')
  const [maeTightenAtrMult, setMaeTightenAtrMult] = useState('0.8')
  const [personaEnabled, setPersonaEnabled] = useState(false)
  const [activePersona, setActivePersona] = useState('conservative')
  const [personaLearningEnabled, setPersonaLearningEnabled] = useState(false)
  const [personaLearningMinSamples, setPersonaLearningMinSamples] = useState('15')
  const [personaUnlockEnabled, setPersonaUnlockEnabled] = useState(false)
  const [personaUnlockStep, setPersonaUnlockStep] = useState('25')
  const [personaPyramidEnabled, setPersonaPyramidEnabled] = useState(false)
  const [personaPyramidTrigger, setPersonaPyramidTrigger] = useState('1.0')
  const [personaAutoAdvance, setPersonaAutoAdvance] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!trading) return
    setStopLoss(String(trading.default_stop_loss_pct ?? 2))
    setTakeProfit(String(trading.default_take_profit_pct ?? 4))
    setHoldHours(String(trading.max_position_hold_hours ?? 72))
    setSlippageBps(String(trading.slippage_bps ?? 5))
    setExecutionFeePct(String(trading.execution_fee_pct ?? 0.1))
    setConfidence(trading.min_confidence_to_trade ?? 'medium')
    setAutoAdopt(trading.auto_adopt_enabled !== false)
    setTrailEnabled(!!trading.trail_enabled)
    setExchangeTpslEnabled(!!trading.exchange_tpsl_enabled)
    const mae = trading.market_aware_exits || {}
    setMaeEnabled(!!mae.enabled)
    setMaeUrgentScore(String(mae.urgent_exit_score ?? 80))
    setMaeTightenScore(String(mae.tighten_stop_score ?? 60))
    setMaeCollapseScore(String(mae.confidence_collapse_score ?? 30))
    setMaeTightenAtrMult(String(mae.tighten_atr_mult ?? 0.8))
    setTrailAtrMult(String(trading.trail_atr_mult ?? 1.5))
    setTrailPct(String(trading.trail_pct ?? 1.0))
    setBreakevenTriggerPct(String(trading.breakeven_trigger_pct ?? 1.5))
    setVolSizingEnabled(trading.vol_sizing_enabled !== false)
    setDefaultNotional(String(trading.default_notional_usdt ?? 25))
    setVolRiskUsdt(String(trading.vol_target_risk_usdt ?? 25))
    setVolTargetPct(String(trading.vol_target_pct ?? 1.5))
    setVolKellyCap(String(trading.vol_kelly_cap_fraction ?? 0.25))
    setPersonaEnabled(!!trading.persona_sizing_enabled)
    setActivePersona(trading.active_persona ?? 'conservative')
    setPersonaLearningEnabled(!!trading.persona_learning_enabled)
    setPersonaLearningMinSamples(String(trading.persona_learning_min_samples ?? 15))
    setPersonaUnlockEnabled(!!trading.persona_progressive_unlock_enabled)
    setPersonaUnlockStep(String(trading.persona_unlock_step_samples ?? 25))
    setPersonaPyramidEnabled(!!trading.persona_pyramid_enabled)
    setPersonaPyramidTrigger(String(trading.persona_pyramid_add_trigger_pct ?? 1.0))
    setPersonaAutoAdvance(!!trading.persona_auto_advance_enabled)
  }, [settings])

  const mut = useMutation({
    mutationFn: () => api.updateTradingConfig({
      default_stop_loss_pct: parseFloat(stopLoss),
      default_take_profit_pct: parseFloat(takeProfit),
      max_position_hold_hours: parseFloat(holdHours),
      slippage_bps: parseFloat(slippageBps),
      execution_fee_pct: parseFloat(executionFeePct),
      min_confidence_to_trade: confidence,
      auto_adopt_enabled: autoAdopt,
      trail_enabled: trailEnabled,
      exchange_tpsl_enabled: exchangeTpslEnabled,
      trail_atr_mult: parseFloat(trailAtrMult),
      trail_pct: parseFloat(trailPct),
      breakeven_trigger_pct: parseFloat(breakevenTriggerPct),
      vol_sizing_enabled: volSizingEnabled,
      default_notional_usdt: parseFloat(defaultNotional),
      vol_target_risk_usdt: parseFloat(volRiskUsdt),
      vol_target_pct: parseFloat(volTargetPct),
      vol_kelly_cap_fraction: parseFloat(volKellyCap),
      persona_sizing_enabled: personaEnabled,
      active_persona: activePersona,
      persona_learning_enabled: personaLearningEnabled,
      persona_learning_min_samples: parseInt(personaLearningMinSamples),
      persona_progressive_unlock_enabled: personaUnlockEnabled,
      persona_unlock_step_samples: parseInt(personaUnlockStep),
      persona_pyramid_enabled: personaPyramidEnabled,
      persona_pyramid_add_trigger_pct: parseFloat(personaPyramidTrigger),
      persona_auto_advance_enabled: personaAutoAdvance,
      market_aware_exits: {
        enabled: maeEnabled,
        urgent_exit_score: parseInt(maeUrgentScore),
        tighten_stop_score: parseInt(maeTightenScore),
        confidence_collapse_score: parseInt(maeCollapseScore),
        tighten_atr_mult: parseFloat(maeTightenAtrMult),
      },
    }),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })
  const dirty = !!trading && (stopLoss !== String(trading.default_stop_loss_pct ?? 2) || takeProfit !== String(trading.default_take_profit_pct ?? 4) || holdHours !== String(trading.max_position_hold_hours ?? 72) || slippageBps !== String(trading.slippage_bps ?? 5) || executionFeePct !== String(trading.execution_fee_pct ?? 0.1) || confidence !== (trading.min_confidence_to_trade ?? 'medium') || autoAdopt !== (trading.auto_adopt_enabled !== false) || trailEnabled !== !!trading.trail_enabled)
  useEffect(() => { onDirty?.('trading-params', dirty) }, [dirty])

  if (!trading) return null

  return (
    <div className="card mb-0">
      <div className="form-grid mb-3">
        <label>
          <span>Default stop-loss (%)</span>
          <input type="number" min={0.1} max={50} step={0.1} value={stopLoss}
            onChange={e => { setStopLoss(e.target.value); setMsg(null) }}
            className="mono" />
        </label>
        <label>
          <span>Default take-profit (%)</span>
          <input type="number" min={0.1} max={100} step={0.1} value={takeProfit}
            onChange={e => { setTakeProfit(e.target.value); setMsg(null) }}
            className="mono" />
        </label>
        <label>
          <span>Max hold time (hours)</span>
          <input type="number" min={1} max={8760} step={1} value={holdHours}
            onChange={e => { setHoldHours(e.target.value); setMsg(null) }}
            className="mono" />
        </label>
        <label>
          <span>Paper slippage (bps)</span>
          <input type="number" min={0} max={200} step={0.5} value={slippageBps}
            onChange={e => { setSlippageBps(e.target.value); setMsg(null) }}
            className="mono" />
        </label>
        <label>
          <span>Execution fee (%)</span>
          <input type="number" min={0} max={5} step={0.01} value={executionFeePct}
            onChange={e => { setExecutionFeePct(e.target.value); setMsg(null) }}
            className="mono" />
        </label>
        <label>
          <span>Min confidence to trade</span>
          <select value={confidence} onChange={e => { setConfidence(e.target.value); setMsg(null) }}>
            {CONFIDENCE_LEVELS.map(c => <option key={c}>{c}</option>)}
          </select>
        </label>
      </div>
      <div className="settings-inline-note">
        <Toggle
          checked={autoAdopt}
          label="Auto-adopt external demo positions into local management"
          onChange={v => { setAutoAdopt(v); setMsg(null) }}
        />
        <p className="hint mt-1">
          When ON, demo positions found on the exchange can be brought under Aletheia management and may receive default exits.
          Live positions are never auto-adopted.
        </p>
      </div>
      <Collapsible title="Trailing Stop & Breakeven">
        <div className="settings-inline-note">
          <Toggle
            checked={trailEnabled}
            label="Trailing stop + breakeven move (paper positions)"
            onChange={v => { setTrailEnabled(v); setMsg(null) }}
          />
          <p className="hint mt-1">
            Moves stop-loss up as price rises. Breakeven trigger: profit ≥ threshold → floor stop at entry price. Default off — enable only after verifying stop parameters.
          </p>
        </div>
        {trailEnabled && (
          <div className="form-grid mb-3">
            <label>
              <span>Trail ATR multiplier</span>
              <input type="number" min={0.5} max={5} step={0.1} value={trailAtrMult}
                onChange={e => { setTrailAtrMult(e.target.value); setMsg(null) }} className="mono" />
            </label>
            <label>
              <span>Trail % (fallback, no ATR)</span>
              <input type="number" min={0.1} max={10} step={0.1} value={trailPct}
                onChange={e => { setTrailPct(e.target.value); setMsg(null) }} className="mono" />
            </label>
            <label>
              <span>Breakeven trigger (%)</span>
              <input type="number" min={0.1} max={20} step={0.1} value={breakevenTriggerPct}
                onChange={e => { setBreakevenTriggerPct(e.target.value); setMsg(null) }} className="mono" />
            </label>
          </div>
        )}
      </Collapsible>
      <Collapsible title="Position Sizing">
        <div className="settings-inline-note">
          <Toggle
            checked={volSizingEnabled}
            label="Volatility-target sizing (ATR-normalized)"
            onChange={v => { setVolSizingEnabled(v); setMsg(null) }}
          />
          <p className="hint mt-1">
            Normalize position size so 1 ATR move ≈ risk_budget. High-vol coins get smaller positions, low-vol get larger.
          </p>
        </div>
        <div className="form-grid mb-3">
          <label>
            <span>Base notional (USDT)</span>
            <input type="number" min={5} max={1000} step={5} value={defaultNotional}
              onChange={e => { setDefaultNotional(e.target.value); setMsg(null) }} className="mono" />
          </label>
          {volSizingEnabled && (<>
            <label>
              <span>Risk budget (USDT)</span>
              <input type="number" min={5} max={500} step={5} value={volRiskUsdt}
                onChange={e => { setVolRiskUsdt(e.target.value); setMsg(null) }} className="mono" />
            </label>
            <label>
              <span>ATR target (%)</span>
              <input type="number" min={0.5} max={10} step={0.1} value={volTargetPct}
                onChange={e => { setVolTargetPct(e.target.value); setMsg(null) }} className="mono" />
            </label>
            <label>
              <span>Kelly cap fraction</span>
              <input type="number" min={0.05} max={1} step={0.05} value={volKellyCap}
                onChange={e => { setVolKellyCap(e.target.value); setMsg(null) }} className="mono" />
            </label>
          </>)}
        </div>
        <div className="settings-inline-note">
          <Toggle
            checked={exchangeTpslEnabled}
            label="Exchange-native TPSL bracket (Bitget demo/live)"
            onChange={v => { setExchangeTpslEnabled(v); setMsg(null) }}
          />
          <p className="hint mt-1">
            When ON, stop-loss and take-profit orders are placed directly on Bitget at position open.
            The local polling loop remains active as a fallback. Enable only after verifying demo behavior.
          </p>
        </div>
      </Collapsible>
      <Collapsible title="Persona-Based Sizing">
        <div className="settings-inline-note">
          <Toggle
            checked={personaEnabled}
            label="Persona-based sizing (动态 sizing 蓝本)"
            onChange={v => { setPersonaEnabled(v); setMsg(null) }}
          />
          <p className="hint mt-1">
            When ON, entry sizing reads the active persona's notional and Kelly bounds instead of
            the flat fields above. Other displayed ranges are reference inputs for the separately
            gated unlock/pyramid experiments below; they do not currently change base entry leverage,
            stops, or add orders. Default OFF = legacy sizing (zero change). Risk-engine hard caps
            always bound the final size regardless of persona.
          </p>
        {personaEnabled && (
          <>
            <label className="mt-2">
              <span>Active persona</span>
              <select value={activePersona} className="w-auto"
                onChange={e => { setActivePersona(e.target.value); setMsg(null) }}>
                {Object.keys(trading.personas || {}).map((name: string) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </label>
            {(() => {
              const p = (trading.personas || {})[activePersona]
              if (!p) return null
              return (
                <div className="mt-2">
                  <span className="settings-field-label">{p.description || activePersona} — 参数区间 (min / init / max)</span>
                  <div className="settings-field-grid">
                    {Object.entries(p.params || {}).map(([k, r]: any) => (
                      <div key={k} className="mono text-sm">{k}: {r.min} / <b>{r.init}</b> / {r.max}</div>
                    ))}
                  </div>
                  <p className="hint mt-1">
                    Active entry consumers: <code>notional_usdt</code> and <code>kelly_fraction</code>.
                    Leverage, stop, and add parameters remain experimental/reference-only.
                  </p>
                </div>
              )
            })()}
            <div className="mt-2">
              <Toggle
                checked={personaLearningEnabled}
                label="Persona learning (按 regime 干净窗奖励学习 sizing)"
                onChange={v => { setPersonaLearningEnabled(v); setMsg(null) }}
              />
              <p className="hint mt-1">
                P3: nudges risk-appetite by each regime bucket's clean-window reward (synthetic/thrash
                closes excluded). Dormant until a bucket has enough clean samples; bounded ±0.3; self-
                refreezes on thin/degrading data. Watch <code>loop.sizing_policy</code> in doctor.
              </p>
              {personaLearningEnabled && (
                <label className="mt-2">
                  <span>Min clean samples per bucket</span>
                  <input type="number" min={3} max={500} step={1} value={personaLearningMinSamples}
                    onChange={e => { setPersonaLearningMinSamples(e.target.value); setMsg(null) }}
                    className="mono w-auto" />
                </label>
              )}
            </div>
            <div className="mt-2">
              <Toggle
                checked={personaUnlockEnabled}
                label="Progressive unlock (逐个解冻 operator-decidable 参数)"
                onChange={v => { setPersonaUnlockEnabled(v); setMsg(null) }}
              />
              <p className="hint mt-1">
                Experimental: only active entry parameters (<code>notional_usdt</code> and{' '}
                <code>kelly_fraction</code>) can unlock. The current gate counts eligible historical
                closes globally; it does not establish a fresh Persona-specific evidence window.
                Account distress refreezes all. Default OFF.
              </p>
              {personaUnlockEnabled && (
                <label className="mt-2">
                  <span>Clean closes per unlock step</span>
                  <input type="number" min={1} max={1000} step={1} value={personaUnlockStep}
                    onChange={e => { setPersonaUnlockStep(e.target.value); setMsg(null) }}
                    className="mono w-auto" />
                </label>
              )}
              <div className="mt-2">
                <Toggle
                  checked={personaAutoAdvance}
                  disabled={!personaAutoAdvance}
                  label="Auto-advance rollout (Experimental safety hold)"
                  onChange={v => { if (!v) setPersonaAutoAdvance(false); setMsg(null) }}
                />
                <p className="hint mt-1">
                  The 8-hour job is currently read-only and does not change Persona settings.
                  Time and broad health checks are not a prospective Persona/policy step verdict.
                  Existing enabled configurations can be switched off; new enablement is blocked.
                </p>
              </div>
            </div>
            <div className="mt-2">
              <Toggle
                checked={personaPyramidEnabled}
                disabled={!personaPyramidEnabled}
                label="Pyramid add-on policy helper (no runtime trigger)"
                onChange={v => { if (!v) setPersonaPyramidEnabled(false); setMsg(null) }}
              />
              <p className="hint mt-1">
                The helper can calculate a bounded add plan, but no Scheduler or Operator path calls it
                and it does not create a paper, demo, or live order. Existing enabled configurations can
                be switched off; new enablement is blocked.
              </p>
              {personaPyramidEnabled && (
                <label className="mt-2">
                  <span>Add trigger (unrealized profit %)</span>
                  <input type="number" min={0.1} max={50} step={0.1} value={personaPyramidTrigger}
                    onChange={e => { setPersonaPyramidTrigger(e.target.value); setMsg(null) }}
                    className="mono w-auto" />
                </label>
              )}
            </div>
          </>
        )}
      </div>
      </Collapsible>
      <Collapsible title="Market-Aware Position Review">
        <div className="settings-inline-note">
          <Toggle
            checked={maeEnabled}
            label="Market-aware review (urgent exit / tighten stop suggestions)"
            onChange={v => { setMaeEnabled(v); setMsg(null) }}
          />
          <p className="hint mt-1">
            When ON, position_manager evaluates adverse market signals and wakes the System Operator
            with an urgent-exit or tighten-stop suggestion. It does not close positions or mutate stops
            until a separate authorized decision is made.
          </p>
        </div>
        {maeEnabled && (
          <div className="form-grid mb-3">
            <label>
              <span>Urgent exit score</span>
              <input type="number" min={50} max={100} step={5} value={maeUrgentScore}
                onChange={e => { setMaeUrgentScore(e.target.value); setMsg(null) }} className="mono" />
            </label>
            <label>
              <span>Tighten stop score</span>
              <input type="number" min={30} max={90} step={5} value={maeTightenScore}
                onChange={e => { setMaeTightenScore(e.target.value); setMsg(null) }} className="mono" />
            </label>
            <label>
              <span>Confidence collapse score</span>
              <input type="number" min={10} max={50} step={5} value={maeCollapseScore}
                onChange={e => { setMaeCollapseScore(e.target.value); setMsg(null) }} className="mono" />
            </label>
            <label>
              <span>Tighten ATR multiplier</span>
              <input type="number" min={0.3} max={2.0} step={0.1} value={maeTightenAtrMult}
                onChange={e => { setMaeTightenAtrMult(e.target.value); setMsg(null) }} className="mono" />
            </label>
          </div>
        )}
      </Collapsible>
      <div className="flex gap-2">
        <button onClick={() => {
          const checks: [string, string][] = [
            ['Stop-loss', stopLoss], ['Take-profit', takeProfit],
            ['Hold hours', holdHours], ['Slippage bps', slippageBps],
            ['Execution fee', executionFeePct],
          ]
          for (const [label, val] of checks) {
            if (!val || !Number.isFinite(parseFloat(val))) {
              setMsg({ ok: false, text: `${label} must be a valid number` })
              return
            }
          }
          mut.mutate()
        }} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save'}</button>
      </div>
      <SaveMsg msg={msg} />
      <p className="hint">Stop-loss / take-profit apply to new positions only. Existing positions keep their original exits.</p>
    </div>
  )
}
