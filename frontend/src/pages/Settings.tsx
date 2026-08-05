import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useSearchParams, Link } from 'react-router-dom'
import { api } from '../api/client'
import ConnectionStatus, { ConnState } from '../components/ConnectionStatus'
import { GuardAuthorityNotice } from '../components/GuardAuthorityNotice'
import PageHeader from '../components/PageHeader'
import {
  CollapsibleSection as Collapsible,
  SaveMessage as SaveMsg,
  SettingsRow as Row,
  SettingsSection,
  Switch as Toggle,
} from '../components/ui'
import { fmtNum, humanize, humanizeBool } from '../lib/format'
import { isUnsavedEdit, shouldBlockNavigation } from '../features/settings/dirtyGuard'
import { executionExchangeFromPaperMode } from '../features/settings/execution/exchangeMode'
import { ModelProvidersSection } from '../features/settings/model-providers/ModelProvidersSection'
import { MODEL_PROVIDERS_SECTION } from '../features/settings/model-providers/modelProviderState'
import { HEALTH_MONITORING_SECTION, WEBHOOK_SECTION } from '../features/settings/health/healthState'
import { HealthNotificationsTab } from '../features/settings/tabs/HealthNotificationsTab'
import { AdvancedTab, ADVANCED_SECTION } from '../features/settings/tabs/AdvancedTab'
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

const PERMISSION_MODES = ['Observe', 'Paper', 'Full']
const PROVIDER_LABELS: Record<string, string> = {
  openai_compatible: 'API',
  databricks: 'Databricks',
  anthropic_native: 'Claude OAuth (Experimental)',
  openai_native: 'OpenAI OAuth (Experimental)',
  codex_cli: 'Codex CLI',
  claude_cli: 'Claude Code CLI',
}
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
  { title: 'Permission Mode',          tab: 'agent', slug: 'permission-mode', alt: ['autonomy', 'observe', 'paper', 'full'] },
  { title: 'Learning',                 tab: 'agent', slug: 'learning', alt: ['dreaming', 'research', 'pi daemon', 'paper learning', 'experiments', 'contribution', 'lifecycle', 'guard', 'lesson guard', 'auto promote', 'guard candidate', 'controller', 'shadow', 'filter tuning', 'opportunity funnel'] },
  { title: 'Regime Detection',         tab: 'agent', slug: 'regime-detection', alt: ['regime', 'bias', 'short enabled', 'bear', 'trend', 'direction'] },
  { title: 'Serenity Research',        tab: 'agent', slug: 'serenity-research', alt: ['serenity', 'auto promote', 'pi evaluation', 'long horizon', 'industrial chain'] },
  { title: 'Scheduler',                tab: 'agent', slug: 'scheduler', alt: ['jobs', 'cron', 'autostart agents'] },
  { title: MODEL_PROVIDERS_SECTION.title, tab: 'agent', slug: MODEL_PROVIDERS_SECTION.id, alt: [...MODEL_PROVIDERS_SECTION.searchTerms] },
  { title: 'Inference Admission',      tab: 'agent', slug: 'inference-admission', alt: ['broker', 'rate limit', 'rpm', 'tpm', 'concurrency', 'queue', 'inference limits', 'admission control', 'timeout'] },
  { title: 'LLM Cost & Usage',         tab: 'agent', slug: 'llm-cost-usage', alt: ['tokenomics', 'spend', 'budget', 'economics', 'cooldown', 'broker', 'inference'] },
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

function AgentModelsTab({ status, settings, qc }: any) {
  const agent = settings?.agent as any || {}
  const runtime = settings?.runtime as any || {}
  // #164 fix: previously these mutations had onSuccess only — a rejected save
  // (validation, permission, backend error) failed silently. Toggle appears to
  // work, config never persists. Route every failure through a shared local
  // banner so the operator sees the real reason.
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const onSaveError = (label: string) => (e: any) =>
    setSaveErr(`${label}: ${e?.message || 'save failed'}`)

  const permMut = useMutation({
    mutationFn: (mode: string) => api.setPermissionMode(mode),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['status'] }); qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Permission mode'),
  })
  const autoLearnMut = useMutation({
    mutationFn: (enabled: boolean) => api.setAutonomousLearning(enabled),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Autonomous learning'),
  })
  const bypassDistressMut = useMutation({
    mutationFn: (enabled: boolean) => api.setBypassDistressGate(enabled),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Bypass distress gate'),
  })
  const runtimeMut = useMutation({
    mutationFn: (body: any) => api.updateRuntimeConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Runtime config'),
  })
  const operatorMut = useMutation({
    mutationFn: (body: any) => api.updateOperatorConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Operator config'),
  })
  const piDaemonMut = useMutation({
    mutationFn: (body: any) => api.updatePiDaemonConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('System Operator daemon'),
  })
  const serenityMut = useMutation({
    mutationFn: (body: any) => api.updateSerenityConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Serenity config'),
  })
  const controllerMut = useMutation({
    mutationFn: (body: any) => api.updateControllerConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Filter controller config'),
  })
  const learningMut = useMutation({
    mutationFn: (body: any) => api.updateLearningConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Paper Learning config'),
  })

  const perm = String(status.permission_mode || agent.permission_mode || '')
  const autoLearn = !!agent.autonomous_learning
  const bypassDistressGate = !!agent.bypass_distress_gate
  const schedulerEnabled = runtime.scheduler_enabled !== false
  const operatorLoopEnabled = settings?.operator?.loop_enabled !== false
  const piDaemon = (settings?.pi_daemon ?? {}) as any
  const piDaemonEnabled = piDaemon.enabled !== false
  const serenity = (settings?.serenity ?? {}) as any
  const serenityEnabled = !!serenity.enabled
  const serenityAutoPromote = !!serenity.auto_promote
  const serenityMinScore = Number(serenity.min_score_for_pi ?? 72)
  const serenityStopLossPct = Number(serenity.stop_loss_pct ?? 8.0)
  const serenityMaxHoldDays = Number(serenity.max_hold_days ?? 90)
  const controllerCfg = (settings?.controller ?? {}) as any
  const controllerShadowEnabled = controllerCfg.shadow_enabled !== false
  const controllerObserveHours = Number(controllerCfg.observe_interval_hours ?? 6)
  const controllerMinSamples = Number(controllerCfg.min_samples_gate ?? 20)
  const learningCfg = (settings?.learning ?? {}) as any
  const contributionEvaluationEnabled = learningCfg.contribution_evaluation_enabled !== false
  const lifecycleAutomationEnabled = !!learningCfg.lifecycle_automation_enabled
  const paperInfluenceEnabled = !!learningCfg.paper_influence_enabled
  const paperInfluencePaths = Array.isArray(learningCfg.paper_influence_paths) ? learningCfg.paper_influence_paths : []
  const learningPathOptions = Array.isArray(settings?.learning_path_options) ? settings.learning_path_options as Array<{
    path_id: string
    label: string
    supported: boolean
    status: 'supported' | 'consumer_unwired' | 'measurement_unwired' | 'authority_unwired'
  }> : []
  const knownLearningPathIds = new Set(learningPathOptions.map(option => option.path_id))
  const unknownPaperInfluencePaths = paperInfluencePaths.filter(
    (path: string) => !knownLearningPathIds.has(path),
  )
  const autoRefreeze = learningCfg.auto_refreeze !== false
  const evaluationInterval = Number(learningCfg.evaluation_interval_minutes ?? 60)
  const paperActiveAllocation = Number(learningCfg.paper_active_allocation_pct ?? 0.80)
  const guardCfg = (settings?.guard ?? {}) as any
  const legacyGuardAutoPromote = !!guardCfg.auto_promote_enabled

  const guardMut = useMutation({
    mutationFn: (body: any) => api.updateGuardConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Guard promotion'),
  })

  const regimeCfg = (settings?.regime ?? {}) as any
  const regimeMut = useMutation({
    mutationFn: (body: any) => api.updateRegimeConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: onSaveError('Regime config'),
  })

  return (
    <div>
      {saveErr && (
        <div className="save-err-banner" role="alert">
          Save failed — {saveErr}
        </div>
      )}
      <Section
        title="Permission Mode"
        description="Controls how much autonomy agents have over trading decisions. Changes are audited."
      >
        <Row label="Permission mode" hint="Observe → Paper → Full. Increasing autonomy at each step.">
          <select value={perm} onChange={e => {
            const next = e.target.value
            if (!confirm(`Change permission mode to "${next}"? This affects agent autonomy and is audited.`)) return
            permMut.mutate(next)
          }} className="w-auto">
            {PERMISSION_MODES.map(m => <option key={m}>{m}</option>)}
          </select>
        </Row>
      </Section>

      <Section title="Learning" description="Controls whether agents autonomously generate research, knowledge, and strategy ideas.">
        <Row label="Autonomous learning" hint="When ON, agents run dreaming/research cycles unprompted. When OFF, only human-triggered runs execute.">
          <Toggle checked={autoLearn} onChange={v => autoLearnMut.mutate(v)} disabled={autoLearnMut.isPending} />
        </Row>
        <Row label="Bypass distress gate" hint="When ON, historical win-rate/PnL stats are hidden from the System Operator prompt so poor past performance cannot trigger self-protection. Use during demo/test phases only — disable before evaluating real results.">
          <Toggle checked={bypassDistressGate} onChange={v => bypassDistressMut.mutate(v)} disabled={bypassDistressMut.isPending} />
        </Row>
        <Collapsible title="Paper Learning Experiments">
          <p className="hint mb-2">Contribution measurement remains read-only. Lifecycle automation and paper influence are separately default-off, and never affect live trading.</p>
          <Row label="Contribution measurement" hint="Evaluate already-recorded shadow exposures and outcomes. This does not grant any influence.">
            <Toggle checked={contributionEvaluationEnabled} onChange={v => learningMut.mutate({ contribution_evaluation_enabled: v })} disabled={learningMut.isPending} />
          </Row>
          <Row label="Lifecycle automation" hint="Allow stage recommendations to be recorded and, only with the gates below, move one paper experiment stage at a time. Default OFF.">
            <Toggle checked={lifecycleAutomationEnabled} onChange={v => {
              if (v && !confirm('Enable paper-experiment lifecycle automation? Paper influence remains separately disabled until you turn it on.')) return
              learningMut.mutate({ lifecycle_automation_enabled: v })
            }} disabled={learningMut.isPending} />
          </Row>
          <Row label="Paper influence" hint="Allow a fully gated paper experiment to use its treatment allocation. Default OFF; this never enables live trading.">
            <Toggle checked={paperInfluenceEnabled} onChange={v => {
              if (v && !confirm('Enable paper Learning influence? Only explicitly allowlisted paths can use paper treatment.')) return
              learningMut.mutate({ paper_influence_enabled: v })
            }} disabled={learningMut.isPending} />
          </Row>
          <Row label="Allowed paper paths" hint="Only paths with a wired consumer, prospective measurement, and authority adapter can be selected. Unsupported design entries remain visible but disabled.">
            <div style={{ display: 'grid', gap: 6, minWidth: 320 }}>
              {learningPathOptions.map(option => (
                <label key={option.path_id} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input
                    type="checkbox"
                    checked={paperInfluencePaths.includes(option.path_id)}
                    disabled={learningMut.isPending || !option.supported}
                    onChange={e => {
                      const configuredSupported = paperInfluencePaths.filter(
                        (path: string) => learningPathOptions.some(
                          candidate => candidate.path_id === path && candidate.supported,
                        ),
                      )
                      const next = e.currentTarget.checked
                        ? [...new Set([...configuredSupported, option.path_id])]
                        : configuredSupported.filter((path: string) => path !== option.path_id)
                      learningMut.mutate({ paper_influence_paths: next })
                    }}
                  />
                  <span>{option.label}</span>
                  <code>{option.path_id}</code>
                  <span style={{ color: option.status === 'supported' ? 'var(--green)' : 'var(--muted)' }}>
                    {option.status === 'supported' ? 'Supported' : option.status === 'consumer_unwired' ? 'Consumer unwired' : option.status === 'measurement_unwired' ? 'Measurement unwired' : 'Authority unwired'}
                  </span>
                </label>
              ))}
              {unknownPaperInfluencePaths.map((path: string) => (
                <div key={path} style={{ color: 'var(--yellow)' }}>
                  Unsupported saved path: <code>{path}</code>. Change any supported selection to remove it.
                </div>
              ))}
            </div>
          </Row>
          <Row label="Auto-refreeze" hint="Immediately closes a paper experiment after a negative or invalid measurement verdict. Default ON.">
            <Toggle checked={autoRefreeze} onChange={v => learningMut.mutate({ auto_refreeze: v })} disabled={learningMut.isPending} />
          </Row>
          <Row label="Evaluation interval (minutes)" hint="How often read-only measurement and lifecycle checks run. Default 60; allowed range 15–1440.">
            <input type="number" min={15} max={1440} step={5} defaultValue={evaluationInterval} onBlur={e => {
              const v = Math.round(Number(e.currentTarget.value))
              if (Number.isFinite(v) && v >= 15 && v <= 1440 && v !== evaluationInterval) learningMut.mutate({ evaluation_interval_minutes: v })
            }} disabled={learningMut.isPending} className="w-24" />
          </Row>
          <Row label="Active-stage treatment allocation" hint="Maximum paper treatment share at the final stage. A control holdout is mandatory; hard maximum 90%.">
            <input type="number" min={0.01} max={0.90} step={0.05} defaultValue={paperActiveAllocation} onBlur={e => {
              const v = Number(e.currentTarget.value)
              if (Number.isFinite(v) && v > 0 && v <= 0.90 && v !== paperActiveAllocation) learningMut.mutate({ paper_active_allocation_pct: v })
            }} disabled={learningMut.isPending} className="w-24" />
          </Row>
        </Collapsible>
        <Collapsible title="Trading Guards">
          <GuardAuthorityNotice legacyAutoPromoteEnabled={legacyGuardAutoPromote} />
          <Row
            label="Guards enabled"
            hint="Master evaluation switch. When OFF, guards do not block or subtract score in the paper ranker. Candidate creation and expiry maintenance are separate."
          >
            <Toggle
              checked={guardCfg.enabled !== false}
              onChange={v => guardMut.mutate({ enabled: v })}
              disabled={guardMut.isPending}
            />
          </Row>
          <Row
            label="Enforce in paper ranker"
            hint="When ON, active guards subtract score in the paper trader's ranking pass. When OFF, guards are advisory only (kept for observation but do not affect trade selection)."
          >
            <Toggle
              checked={guardCfg.enforce_in_paper_ranker !== false}
              onChange={v => guardMut.mutate({ enforce_in_paper_ranker: v })}
              disabled={guardMut.isPending}
            />
          </Row>
          <Row
            label="Max score penalty"
            hint="Maximum score points a single guard can subtract in the ranker. Caps runaway penalties from broad-matching guards."
          >
            <input
              type="number"
              min={0}
              max={100}
              step={5}
              value={Number(guardCfg.max_score_penalty ?? 40)}
              onChange={e => {
                const n = Math.max(0, Math.min(100, Number(e.target.value) || 0))
                guardMut.mutate({ max_score_penalty: n })
              }}
              disabled={guardMut.isPending}
              style={{ width: 80 }}
            />
          </Row>
        </Collapsible>
        <Collapsible title="Filter Feedback Controller">
          <p className="hint mb-2">Phase 1 (SHADOW): records whether each opportunity filter has enough causal evidence for a future review. It NEVER changes trading config in this phase.</p>
          <Row
            label="Shadow observe job enabled"
            hint="When ON, the controller records read-only evidence-availability rows. It writes no trading config and does not issue threshold changes until passed/dropped cohorts are recorded."
          >
            <Toggle
              checked={controllerShadowEnabled}
              onChange={v => controllerMut.mutate({ shadow_enabled: v })}
              disabled={controllerMut.isPending}
            />
          </Row>
          <Row
            label="Observe interval (hours)"
            hint="How often the shadow observe job runs. Verdicts use multi-hour forward-return horizons, so frequent runs add no signal. Default 6."
          >
            <input
              type="number"
              min={1}
              max={168}
              step={1}
              defaultValue={controllerObserveHours}
              onBlur={e => {
                const v = parseInt(e.currentTarget.value, 10)
                if (!Number.isFinite(v) || v < 1 || v > 168 || v === controllerObserveHours) return
                controllerMut.mutate({ observe_interval_hours: v })
              }}
              className="w-24"
              disabled={controllerMut.isPending}
            />
          </Row>
          <Row
            label="Min samples gate"
            hint="Reserved for future filter-specific evidence. Aggregate family samples never produce a filter threshold recommendation. Default 20."
          >
            <input
              type="number"
              min={5}
              max={500}
              step={1}
              defaultValue={controllerMinSamples}
              onBlur={e => {
                const v = parseInt(e.currentTarget.value, 10)
                if (!Number.isFinite(v) || v < 5 || v > 500 || v === controllerMinSamples) return
                controllerMut.mutate({ min_samples_gate: v })
              }}
              className="w-24"
              disabled={controllerMut.isPending}
            />
          </Row>
        </Collapsible>
      </Section>

      <Section
        title="Regime Detection"
        description="Market regime classifier that adjusts trade direction bias. Identifies trending/ranging/bear conditions to guide the System Operator's direction preference."
      >
        <Row label="Regime detection enabled" hint="When ON, regime detector classifies market conditions and injects bias into System Operator context.">
          <Toggle checked={regimeCfg.enabled !== false} onChange={v => regimeMut.mutate({ enabled: v })} disabled={regimeMut.isPending} />
        </Row>
        <Row label="Short trades enabled" hint="Allow short direction trades when regime is bearish. Disable to restrict to long-only.">
          <Toggle checked={regimeCfg.short_enabled !== false} onChange={v => regimeMut.mutate({ short_enabled: v })} disabled={regimeMut.isPending} />
        </Row>
        <Row label="Bias threshold" hint="Minimum score change required to flip regime bias (0.0–1.0). Higher = more stable, slower to react.">
          <input
            type="number" step="0.05" min="0" max="1"
            value={Number(regimeCfg.bias_threshold ?? 0.30)}
            onChange={e => regimeMut.mutate({ bias_threshold: Number(e.target.value) })}
            className="input-sm"
          />
        </Row>
        <Row label="Min hold periods" hint="Minimum bars the regime must hold before a bias flip is accepted. Prevents whipsawing (1–20).">
          <input
            type="number" step="1" min="1" max="20"
            value={Number(regimeCfg.min_hold_periods ?? 3)}
            onChange={e => regimeMut.mutate({ min_hold_periods: Number(e.target.value) })}
            className="input-sm"
          />
        </Row>
      </Section>

      <Section
        title="Serenity Research"
        description="Long-horizon industrial-chain analyst lens. Promoting a theme enqueues its opportunities into the System Operator evaluation queue (the System Operator remains sole trade-decision maker)."
      >
        <Row
          label="Serenity Lens enabled"
          hint="Master switch. When OFF, the scheduler skips all Serenity research runs (evidence collection, theme clustering, opportunity generation). Existing data is preserved."
        >
          <Toggle
            checked={serenityEnabled}
            onChange={v => serenityMut.mutate({ enabled: v })}
            disabled={serenityMut.isPending}
          />
        </Row>
        <Row
          label="Auto-promote candidate themes"
          hint="When ON, research_worker auto-promotes themes that produce a 'candidate_position' bucket opportunity (score ≥ min below, 0 fail gates). When OFF, promotion is manual-only."
        >
          <Toggle
            checked={serenityAutoPromote}
            onChange={v => {
              if (v && !confirm('Enable auto-promote? Themes that meet the threshold will be queued to the System Operator without human review.')) return
              serenityMut.mutate({ auto_promote: v })
            }}
            disabled={serenityMut.isPending}
          />
        </Row>
        <Row
          label="Min score for System Operator"
          hint="Minimum aggregate research score (0-100) required before an opportunity is forwarded to the System Operator. Default 72."
        >
          <input
            type="number"
            min={0}
            max={100}
            step={1}
            defaultValue={serenityMinScore}
            onBlur={e => {
              const v = parseInt(e.currentTarget.value, 10)
              if (!Number.isFinite(v) || v < 0 || v > 100 || v === serenityMinScore) return
              serenityMut.mutate({ min_score_for_pi: v })
            }}
            className="w-24"
            disabled={serenityMut.isPending}
          />
        </Row>
        <Row
          label="Stop-loss % (serenity school)"
          hint="Wider stop applied to serenity-school positions (long-horizon thesis). Default 8.0%. Short-term positions use the global Trading default."
        >
          <input
            type="number"
            min={0.1}
            max={50}
            step={0.1}
            defaultValue={serenityStopLossPct}
            onBlur={e => {
              const v = parseFloat(e.currentTarget.value)
              if (!Number.isFinite(v) || v < 0.1 || v > 50 || v === serenityStopLossPct) return
              serenityMut.mutate({ stop_loss_pct: v })
            }}
            className="w-24"
            disabled={serenityMut.isPending}
          />
        </Row>
        <Row
          label="Max hold days (serenity school)"
          hint="Maximum holding period for serenity-school positions before time-based exit. Default 90 days."
        >
          <input
            type="number"
            min={1}
            max={365}
            step={1}
            defaultValue={serenityMaxHoldDays}
            onBlur={e => {
              const v = parseInt(e.currentTarget.value, 10)
              if (!Number.isFinite(v) || v < 1 || v > 365 || v === serenityMaxHoldDays) return
              serenityMut.mutate({ max_hold_days: v })
            }}
            className="w-24"
            disabled={serenityMut.isPending}
          />
        </Row>
      </Section>

      <Section title="Scheduler" description="Controls background scheduling of all agent jobs.">
        <Row
          label="Scheduler enabled"
          hint="When disabled, no agent jobs fire automatically. The gateway stays up; runs can still be triggered manually."
        >
          <Toggle
            checked={schedulerEnabled}
            onChange={v => {
              if (!v && !confirm('Disable the scheduler? All agent jobs will stop firing automatically.')) return
              runtimeMut.mutate({ scheduler_enabled: v })
            }}
            disabled={runtimeMut.isPending}
          />
        </Row>
        <Row
          label="Operator loop"
          hint="When enabled, the System Operator runs system-level assessments every 4h and takes Level 1 actions automatically."
        >
          <Toggle
            checked={operatorLoopEnabled}
            onChange={v => {
              if (!v && !confirm('Disable the operator loop? Automated system governance will stop.')) return
              operatorMut.mutate({ loop_enabled: v })
            }}
            disabled={operatorMut.isPending}
          />
        </Row>
        <Row
          label="System Operator daemon (event-driven)"
          hint="When ON, System Operator wakes instantly on signal arrival instead of polling every 15 min. Disable to revert to legacy polling."
        >
          <Toggle
            checked={piDaemonEnabled}
            onChange={v => {
              if (!v && !confirm('Disable the System Operator daemon? The agent will revert to 15-minute polling intervals.')) return
              piDaemonMut.mutate({ enabled: v })
            }}
            disabled={piDaemonMut.isPending}
          />
        </Row>
        {piDaemonEnabled && (
          <>
            <Row label="Debounce (s)" hint="How long to wait after a signal before evaluating (collects burst signals). Range 0–60.">
              <input
                type="number" min={0} max={60} step={1}
                defaultValue={piDaemon.debounce_sec ?? 8}
                className="w-20"
                onBlur={e => {
                  const v = parseFloat(e.target.value)
                  if (!isNaN(v)) piDaemonMut.mutate({ debounce_sec: v })
                }}
              />
            </Row>
            <Row label="Cooldown (s)" hint="Minimum gap between two evaluations. Range 0–600.">
              <input
                type="number" min={0} max={600} step={5}
                defaultValue={piDaemon.cooldown_sec ?? 60}
                className="w-20"
                onBlur={e => {
                  const v = parseFloat(e.target.value)
                  if (!isNaN(v)) piDaemonMut.mutate({ cooldown_sec: v })
                }}
              />
            </Row>
            <Row label="Heartbeat (s)" hint="Fallback polling interval when no signals arrive. Range 60–3600.">
              <input
                type="number" min={60} max={3600} step={60}
                defaultValue={piDaemon.heartbeat_sec ?? 300}
                className="w-20"
                onBlur={e => {
                  const v = parseFloat(e.target.value)
                  if (!isNaN(v)) piDaemonMut.mutate({ heartbeat_sec: v })
                }}
              />
            </Row>
          </>
        )}
      </Section>

      <ModelProvidersSection qc={qc} />

      <Section title="Inference Admission" description="Per-provider rate limits, concurrency, and queue admission. Every LLM call routed through a provider is governed by these ceilings before it reaches the model. Independent of the provider's identity/model settings above.">
        <InferenceAdmissionCard qc={qc} />
      </Section>

      <Section title="LLM Cost & Usage" description="Enable default-off budget shadow observation. The read-only usage dashboard (token spend, provider trends, budgets, cooldowns) lives on the Usage page.">
        <EconomicsSnapshotCard economics={settings?.economics} qc={qc} />
      </Section>
    </div>
  )
}



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





// ─────────────────────────────────────────────────────────────────────────────
function EconomicsSnapshotCard({ economics, qc }: { economics: any; qc: any }) {
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const economicsEnabled = economics?.enabled === true
  const shadowEnabled = economics?.budgets_shadow_enabled === true
  const guardrailDetectionEnabled = economics?.prompt_guardrail_detection_enabled === true
  const economicsMut = useMutation({
    mutationFn: (body: { enabled?: boolean; budgets_shadow_enabled?: boolean; prompt_guardrail_detection_enabled?: boolean }) => api.updateEconomicsConfig(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setSaveErr(null) },
    onError: (error: any) => setSaveErr(`Economics controls: ${error?.message || 'save failed'}`),
  })
  const { data: status } = useQuery({
    queryKey: ['economics-status'],
    queryFn: api.getEconomicsStatus,
    refetchInterval: 30_000,
  })

  return (
    <div>
      {saveErr && <div className="settings-save-msg err">{saveErr}</div>}
      <Row
        label="Enable economics harness"
        hint="Keeps LLM budget controls dormant until explicitly enabled. This does not block or reroute any model call."
      >
        <Toggle
          checked={economicsEnabled}
          onChange={enabled => economicsMut.mutate({ enabled })}
          disabled={economicsMut.isPending}
        />
      </Row>
      <Row
        label="Budget shadow observation"
        hint="Records calls that would exceed declared budgets. Calls still proceed; collect this evidence for two weeks before considering enforcement."
      >
        <Toggle
          checked={shadowEnabled}
          onChange={budgets_shadow_enabled => economicsMut.mutate({ budgets_shadow_enabled })}
          disabled={!economicsEnabled || economicsMut.isPending}
        />
      </Row>
      <Row
        label="Prompt secret-shape detection (observe only)"
        hint="Records only stable rule IDs and counts; it never stores prompt text or blocks a model call."
      >
        <Toggle
          checked={guardrailDetectionEnabled}
          onChange={prompt_guardrail_detection_enabled => economicsMut.mutate({ prompt_guardrail_detection_enabled })}
          disabled={!economicsEnabled || economicsMut.isPending}
        />
      </Row>
      {status && (status.tracing_enabled === false || status.audit_enabled === false) && (
        <p className="hint" style={{ marginBottom: '0.75rem' }}>
          {status.tracing_enabled === false && 'Cost tracing disabled (ALETHEIA_COST_TRACING=0). '}
          {status.audit_enabled === false && 'Viability audit disabled (ALETHEIA_VIABILITY_AUDIT=0).'}
        </p>
      )}
      <p className="hint">
        Read-only spend, token, budget, and cooldown views live on the{' '}
        <Link to="/usage">Usage page</Link>.
      </p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Inference Admission — sibling Section to AI Model Providers.
// The broker's rate limit / concurrency / queue policy is a control-plane
// concern separate from provider identity. Rendered as its own Section so
// the semantic decoupling shows up in the operator's UI, not just the code.
// ─────────────────────────────────────────────────────────────────────────────

function InferenceAdmissionCard({ qc }: { qc: any }) {
  const { data } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig })
  const { data: broker } = useQuery({
    queryKey: ['model-broker'],
    queryFn: api.getInferenceBroker,
    refetchInterval: 10_000,
  })

  if (!data) return <p className="hint">Loading provider list…</p>

  const providers = Object.entries((data as any).providers ?? {}) as Array<[string, any]>
  const enabled = providers.filter(([, cfg]) => cfg?.enabled === true)

  if (enabled.length === 0) {
    return (
      <p className="hint">
        No providers enabled. Enable a provider under "AI Model Providers" above
        to configure its inference admission.
      </p>
    )
  }

  const running = broker?.running ?? false

  return (
    <div className="flex flex-col gap-3">
      <div className="hint mono" style={{ fontSize: '0.72rem' }}>
        Broker: {running ? 'running' : 'not yet started'}
        {broker && ` · ${Object.keys(broker.providers ?? {}).length} lane(s) tracked`}
        · 0 RPM / 0 TPM means unlimited
      </div>
      {enabled.map(([name, cfg]) => {
        const metrics = broker?.providers?.[name]?.metrics
        const label = PROVIDER_LABELS[name] || name
        const compactModel = cfg?.model && cfg.model !== 'default' ? ` · model: ${cfg.model}` : ''
        return (
          <div
            key={name}
            style={{
              border: '1px solid var(--border)',
              borderRadius: 'var(--r)',
              padding: '0.7rem 0.9rem',
              background: 'var(--surface)',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                marginBottom: '0.5rem',
              }}
            >
              <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>{label}</span>
              <span className="hint mono" style={{ fontSize: '0.72rem', color: 'var(--muted)' }}>
                {name}
                {compactModel}
              </span>
              {metrics && (
                <span
                  className="hint mono"
                  style={{ marginLeft: 'auto', fontSize: '0.72rem' }}
                >
                  active {metrics.active ?? 0} · queued {metrics.queued ?? 0} · wait avg{' '}
                  {Math.round((metrics.queue_wait_avg_ms as number) ?? 0)}ms
                </span>
              )}
            </div>
            <InferenceLimitsForm name={name} config={cfg} metrics={metrics} qc={qc} />
          </div>
        )
      })}
    </div>
  )
}

function InferenceLimitsForm({ name, config, metrics, qc }: {
  name: string; config: any; metrics?: Record<string, number>; qc: any
}) {
  const [maxConcurrency, setMaxConcurrency] = useState(config?.inference_max_concurrency ?? 2)
  const [rpm, setRpm] = useState(config?.inference_requests_per_minute ?? 60)
  const [tpm, setTpm] = useState(config?.inference_tpm ?? 120000)
  const [queueCapacity, setQueueCapacity] = useState(config?.inference_queue_capacity ?? 64)
  const [reservedSlots, setReservedSlots] = useState(config?.inference_reserved_high_priority_slots ?? 8)
  const [timeoutSec, setTimeoutSec] = useState(config?.inference_timeout_sec ?? 300)
  const save = useMutation({
    mutationFn: () => {
      const payload = {
        inference_max_concurrency: Number(maxConcurrency),
        inference_requests_per_minute: Number(rpm),
        inference_tpm: Number(tpm),
        inference_queue_capacity: Number(queueCapacity),
        inference_reserved_high_priority_slots: Number(reservedSlots),
        inference_timeout_sec: Number(timeoutSec),
      }
      return config?.kind === 'api'
        ? api.updateModelProfile(name, payload)
        : api.updateModelIntegration(name, payload)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['model'] })
      qc.invalidateQueries({ queryKey: ['model-broker'] })
    },
  })

  return (
    <div style={{ marginTop: '0.75rem' }}>
      {metrics && (
        <div className="hint mono" style={{ marginBottom: '0.5rem', fontSize: '0.72rem' }}>
          active {metrics.active ?? 0} · queued {metrics.queued ?? 0} · wait avg {Math.round(metrics.queue_wait_avg_ms ?? 0)}ms
        </div>
      )}
      <div className="form-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
        <label>Max concurrency<input type="number" min={1} max={16} value={maxConcurrency} onChange={e => setMaxConcurrency(Number(e.target.value))} /></label>
        <label>Requests/min<input type="number" min={0} max={10000} value={rpm} onChange={e => setRpm(Number(e.target.value))} /></label>
        <label>Tokens/min<input type="number" min={0} max={100000000} value={tpm} onChange={e => setTpm(Number(e.target.value))} /></label>
        <label>Queue capacity<input type="number" min={1} max={1000} value={queueCapacity} onChange={e => setQueueCapacity(Number(e.target.value))} /></label>
        <label>Reserved priority slots<input type="number" min={0} max={queueCapacity} value={reservedSlots} onChange={e => setReservedSlots(Number(e.target.value))} /></label>
        <label>Total timeout (sec)<input type="number" min={1} max={1800} value={timeoutSec} onChange={e => setTimeoutSec(Number(e.target.value))} /></label>
      </div>
      <div className="flex items-center gap-2 mt-2">
        <button className="secondary" onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save inference limits'}
        </button>
        <span className="hint">0 RPM/TPM means unlimited. Priority slots are reserved for interactive/trading work.</span>
      </div>
      {save.isSuccess && <div className="settings-save-msg ok">Inference limits saved.</div>}
      {save.isError && <div className="settings-save-msg err">{(save.error as any)?.message || 'Save failed'}</div>}
    </div>
  )
}
