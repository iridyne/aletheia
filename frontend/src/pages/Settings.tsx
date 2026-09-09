import { useState, useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useConfirm } from '../components/ui'
import PageHeader from '../components/PageHeader'
import Tabs, { type TabDef } from '../components/Tabs'
import { hasUncontrolledDraft, isFallbackDraftControl, shouldBlockNavigation } from '../features/settings/dirtyGuard'
import { MODEL_PROVIDERS_SECTION } from '../features/settings/model-providers/modelProviderState'
import { HEALTH_MONITORING_SECTION, WEBHOOK_SECTION } from '../features/settings/health/healthState'
import { HealthNotificationsTab } from '../features/settings/tabs/HealthNotificationsTab'
import { AdvancedTab, ADVANCED_SECTION } from '../features/settings/tabs/AdvancedTab'
import {
  CAPITAL_EVENTS_SECTION, EXCHANGE_CONNECTION_SECTION, ExecutionTab,
  PAPER_PARAMETERS_SECTION, TRADING_MODE_SECTION,
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

function getSectionSummary(id: string, settings: any, status: any): string | null {
  switch (id) {
    case 'exchange-connection': {
      const name = String(settings?.exchange?.name || 'bitget')
      return name.toUpperCase()
    }
    case 'trading-mode':
      return status?.exchange_mode ? `Mode: ${status.exchange_mode}` : null
    case 'paper-trading-parameters':
      return 'Configured'
    case 'capital-events':
      return 'Audit log active'
    case 'permission-mode':
      return settings?.agent?.permission_mode ? `Mode: ${settings.agent.permission_mode}` : 'Configured'
    case 'learning':
      return settings?.learning?.enabled !== false ? 'Enabled' : 'Disabled'
    case 'regime-detection':
      return settings?.regime?.enabled !== false ? 'Enabled' : 'Disabled'
    case 'serenity-research':
      return settings?.serenity?.enabled ? 'Active' : 'Disabled'
    case 'scheduler':
      return settings?.runtime?.scheduler_enabled !== false ? 'Active' : 'Paused'
    case 'ai-model-providers':
      return 'Models & keys'
    case 'inference-admission':
      return 'Rate & concurrency limits'
    case 'llm-cost-usage':
      return settings?.economics?.budgets_shadow_enabled ? 'Shadow enabled' : 'Configured'
    case 'universe':
      return settings?.universe?.enabled ? 'Active' : 'Configured'
    case 'event-triggers':
      return 'Thresholds active'
    case 'tradable-symbols': {
      const mode = settings?.risk?.symbol_filter_mode ?? 'blacklist'
      const count = (settings?.risk?.symbol_list ?? []).length
      return `${mode}: ${count} symbol${count === 1 ? '' : 's'}`
    }
    case 'risk-limits':
      return 'Hard risk caps'
    case 'health-monitoring':
      return 'Self-heal & probes'
    case 'telegram':
      return 'Alert channel'
    case 'webhook':
      return settings?.webhook?.url ? 'Configured' : 'Not configured'
    case 'app':
      return settings?.app?.theme ? `Theme: ${settings.app.theme}` : 'Dashboard'
    case 'data-retention':
      return 'Storage & retention'
    case 'data-export':
      return 'CSV export'
    case 'software-updates':
      return 'Manual update'
    case 'network-proxy':
      return settings?.network?.proxy_host ? 'Configured' : 'Direct'
    case 'market-data-sources':
      return settings?.market_data?.ccxt_exchange ? String(settings.market_data.ccxt_exchange).toUpperCase() : 'Default'
    case 'macro-sentiment-signals':
      return 'Macro & sentiment'
    case 'news-feeds':
      return 'RSS ingestion'
    case 'prediction-markets':
      return 'Polymarket observer'
    case 'observed-us-equities': {
      const count = (settings?.market_data?.observed_equities ?? []).length
      return `${count} ticker${count === 1 ? '' : 's'}`
    }
    case 'configuration-matrix':
      return 'Read-only audit'
    default:
      return null
  }
}

function CategoryHub({
  tab,
  settings,
  status,
  dirtyIds,
  onSelectSection,
}: {
  tab: Tab
  settings: any
  status: any
  dirtyIds: Set<string>
  onSelectSection: (slug: string) => void
}) {
  const sections = SETTINGS_INDEX.filter(it => it.tab === tab)
  return (
    <div className="settings-hub-grid" role="region" aria-label={`${TABS.find(t => t.id === tab)?.label} sections`}>
      {sections.map(s => {
        const isDirty = dirtyIds.has(s.slug)
        const summary = getSectionSummary(s.slug, settings, status)
        return (
          <button
            key={s.slug}
            type="button"
            className="settings-hub-card"
            onClick={() => onSelectSection(s.slug)}
            aria-label={`Configure ${s.title}`}
          >
            <div className="settings-hub-card-header">
              <h3 className="settings-hub-card-title">{s.title}</h3>
              {isDirty && <span className="settings-hub-draft-dot" title="Unsaved changes" />}
            </div>
            <p className="settings-hub-card-desc">{s.description}</p>
            <div className="settings-hub-card-footer">
              {summary && <span className="settings-hub-card-badge">{summary}</span>}
              <span className="settings-hub-card-action">Configure →</span>
            </div>
          </button>
        )
      })}
    </div>
  )
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
const SETTINGS_INDEX: { title: string; tab: Tab; slug: string; description: string; alt?: string[] }[] = [
  // Agent & Models
  { title: PERMISSION_SECTION.title,          tab: PERMISSION_SECTION.tab, slug: PERMISSION_SECTION.id, description: PERMISSION_SECTION.description, alt: [...PERMISSION_SECTION.searchTerms] },
  { title: LEARNING_SECTION.title,            tab: LEARNING_SECTION.tab, slug: LEARNING_SECTION.id, description: LEARNING_SECTION.description, alt: [...LEARNING_SECTION.searchTerms] },
  { title: REGIME_SECTION.title,              tab: REGIME_SECTION.tab, slug: REGIME_SECTION.id, description: REGIME_SECTION.description, alt: [...REGIME_SECTION.searchTerms] },
  { title: SERENITY_SECTION.title,            tab: SERENITY_SECTION.tab, slug: SERENITY_SECTION.id, description: SERENITY_SECTION.description, alt: [...SERENITY_SECTION.searchTerms] },
  { title: SCHEDULER_SECTION.title,           tab: SCHEDULER_SECTION.tab, slug: SCHEDULER_SECTION.id, description: SCHEDULER_SECTION.description, alt: [...SCHEDULER_SECTION.searchTerms] },
  { title: MODEL_PROVIDERS_SECTION.title,     tab: MODEL_PROVIDERS_SECTION.tab, slug: MODEL_PROVIDERS_SECTION.id, description: MODEL_PROVIDERS_SECTION.description, alt: [...MODEL_PROVIDERS_SECTION.searchTerms] },
  { title: INFERENCE_ADMISSION_SECTION.title, tab: INFERENCE_ADMISSION_SECTION.tab, slug: INFERENCE_ADMISSION_SECTION.id, description: INFERENCE_ADMISSION_SECTION.description, alt: [...INFERENCE_ADMISSION_SECTION.searchTerms] },
  { title: LLM_COST_SECTION.title,            tab: LLM_COST_SECTION.tab, slug: LLM_COST_SECTION.id, description: LLM_COST_SECTION.description, alt: [...LLM_COST_SECTION.searchTerms] },
  // Execution
  { title: EXCHANGE_CONNECTION_SECTION.title, tab: EXCHANGE_CONNECTION_SECTION.tab, slug: EXCHANGE_CONNECTION_SECTION.id, description: EXCHANGE_CONNECTION_SECTION.description, alt: [...EXCHANGE_CONNECTION_SECTION.searchTerms] },
  { title: TRADING_MODE_SECTION.title,        tab: TRADING_MODE_SECTION.tab, slug: TRADING_MODE_SECTION.id, description: TRADING_MODE_SECTION.description, alt: [...TRADING_MODE_SECTION.searchTerms] },
  { title: PAPER_PARAMETERS_SECTION.title,    tab: PAPER_PARAMETERS_SECTION.tab, slug: PAPER_PARAMETERS_SECTION.id, description: PAPER_PARAMETERS_SECTION.description, alt: [...PAPER_PARAMETERS_SECTION.searchTerms] },
  { title: CAPITAL_EVENTS_SECTION.title,      tab: CAPITAL_EVENTS_SECTION.tab, slug: CAPITAL_EVENTS_SECTION.id, description: CAPITAL_EVENTS_SECTION.description, alt: [...CAPITAL_EVENTS_SECTION.searchTerms] },
  // Risk & Universe
  { title: UNIVERSE_SECTION.title,         tab: UNIVERSE_SECTION.tab, slug: UNIVERSE_SECTION.id, description: UNIVERSE_SECTION.description, alt: [...UNIVERSE_SECTION.searchTerms] },
  { title: EVENT_TRIGGERS_SECTION.title,   tab: EVENT_TRIGGERS_SECTION.tab, slug: EVENT_TRIGGERS_SECTION.id, description: EVENT_TRIGGERS_SECTION.description, alt: [...EVENT_TRIGGERS_SECTION.searchTerms] },
  { title: TRADABLE_SYMBOLS_SECTION.title, tab: TRADABLE_SYMBOLS_SECTION.tab, slug: TRADABLE_SYMBOLS_SECTION.id, description: TRADABLE_SYMBOLS_SECTION.description, alt: [...TRADABLE_SYMBOLS_SECTION.searchTerms] },
  { title: RISK_LIMITS_SECTION.title,      tab: RISK_LIMITS_SECTION.tab, slug: RISK_LIMITS_SECTION.id, description: RISK_LIMITS_SECTION.description, alt: [...RISK_LIMITS_SECTION.searchTerms] },
  // Health & Alerts
  { title: HEALTH_MONITORING_SECTION.title, tab: HEALTH_MONITORING_SECTION.tab, slug: HEALTH_MONITORING_SECTION.id, description: HEALTH_MONITORING_SECTION.description, alt: [...HEALTH_MONITORING_SECTION.searchTerms] },
  { title: TELEGRAM_SECTION.title,          tab: TELEGRAM_SECTION.tab, slug: TELEGRAM_SECTION.id, description: TELEGRAM_SECTION.description, alt: [...TELEGRAM_SECTION.searchTerms] },
  { title: WEBHOOK_SECTION.title,           tab: WEBHOOK_SECTION.tab, slug: WEBHOOK_SECTION.id, description: WEBHOOK_SECTION.description, alt: [...WEBHOOK_SECTION.searchTerms] },
  // Runtime & System
  { title: APP_SECTION.title,              tab: APP_SECTION.tab, slug: APP_SECTION.id, description: APP_SECTION.description, alt: [...APP_SECTION.searchTerms] },
  { title: DATA_RETENTION_SECTION.title,   tab: DATA_RETENTION_SECTION.tab, slug: DATA_RETENTION_SECTION.id, description: DATA_RETENTION_SECTION.description, alt: [...DATA_RETENTION_SECTION.searchTerms] },
  { title: DATA_EXPORT_SECTION.title,      tab: DATA_EXPORT_SECTION.tab, slug: DATA_EXPORT_SECTION.id, description: DATA_EXPORT_SECTION.description, alt: [...DATA_EXPORT_SECTION.searchTerms] },
  { title: SOFTWARE_UPDATES_SECTION.title, tab: SOFTWARE_UPDATES_SECTION.tab, slug: SOFTWARE_UPDATES_SECTION.id, description: SOFTWARE_UPDATES_SECTION.description, alt: [...SOFTWARE_UPDATES_SECTION.searchTerms] },
  { title: NETWORK_PROXY_SECTION.title,    tab: NETWORK_PROXY_SECTION.tab, slug: NETWORK_PROXY_SECTION.id, description: NETWORK_PROXY_SECTION.description, alt: [...NETWORK_PROXY_SECTION.searchTerms] },
  // Signals
  { title: MARKET_DATA_SECTION.title,        tab: MARKET_DATA_SECTION.tab, slug: MARKET_DATA_SECTION.id, description: MARKET_DATA_SECTION.description, alt: [...MARKET_DATA_SECTION.searchTerms] },
  { title: MACRO_SIGNALS_SECTION.title,      tab: MACRO_SIGNALS_SECTION.tab, slug: MACRO_SIGNALS_SECTION.id, description: MACRO_SIGNALS_SECTION.description, alt: [...MACRO_SIGNALS_SECTION.searchTerms] },
  { title: NEWS_FEEDS_SECTION.title,         tab: NEWS_FEEDS_SECTION.tab, slug: NEWS_FEEDS_SECTION.id, description: NEWS_FEEDS_SECTION.description, alt: [...NEWS_FEEDS_SECTION.searchTerms] },
  { title: PREDICTION_MARKETS_SECTION.title, tab: PREDICTION_MARKETS_SECTION.tab, slug: PREDICTION_MARKETS_SECTION.id, description: PREDICTION_MARKETS_SECTION.description, alt: [...PREDICTION_MARKETS_SECTION.searchTerms] },
  { title: EQUITIES_SECTION.title,           tab: EQUITIES_SECTION.tab, slug: EQUITIES_SECTION.id, description: EQUITIES_SECTION.description, alt: [...EQUITIES_SECTION.searchTerms] },
  // Advanced
  { title: ADVANCED_SECTION.title,     tab: ADVANCED_SECTION.tab, slug: ADVANCED_SECTION.id, description: ADVANCED_SECTION.description, alt: [...ADVANCED_SECTION.searchTerms] },
]

const SECTION_BY_SLUG = new Map<string, typeof SETTINGS_INDEX[number]>(
  SETTINGS_INDEX.map(item => [item.slug, item]),
)

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
  const confirm = useConfirm()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: status, isPending: statusPending, refetch: refetchStatus } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: settings, dataUpdatedAt: settingsUpdatedAt, isPending: settingsPending, refetch: refetchSettings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
  })
  const [tab, setTab] = useState<Tab>(() => {
    return parseTab(searchParams.get('tab')) ?? 'agent'
  })
  const dirtyRef = useRef<Set<string>>(new Set())
  const touchedRef = useRef(false)
  const touchedControlsRef = useRef<Set<HTMLElement>>(new Set())
  const navigationConfirmPending = useRef(false)
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

  // Warn before a hard reload / tab close / cross-origin navigation.
  // The in-app link guard below cannot cover hard reloads or tab close.
  useEffect(() => {
    if (!shouldBlockNavigation(dirtyRef.current.size, isTouched)) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [isTouched, preciseDirtyCount])

  // BrowserRouter has no data-router blocker. Capture ordinary same-origin link
  // navigation before React Router handles it, then continue through useNavigate
  // only after the existing application confirmation resolves.
  useEffect(() => {
    if (!shouldBlockNavigation(dirtyRef.current.size, isTouched)) return
    const onDocumentClick = (event: MouseEvent) => {
      if (
        event.defaultPrevented || event.button !== 0 || event.metaKey
        || event.ctrlKey || event.shiftKey || event.altKey
      ) return
      const target = event.target instanceof Element ? event.target : null
      const link = target?.closest<HTMLAnchorElement>('a[href]')
      if (!link || link.target === '_blank' || link.hasAttribute('download')) return
      const destination = new URL(link.href, window.location.href)
      if (
        destination.origin !== window.location.origin
        || destination.href === window.location.href
      ) return
      event.preventDefault()
      event.stopPropagation()
      if (navigationConfirmPending.current) return
      navigationConfirmPending.current = true
      void confirm({
        title: 'Discard unsaved changes?',
        description: 'You have unsaved changes. Leave Settings and discard them?',
        confirmLabel: 'Leave and discard',
        variant: 'warning',
      }).then(approved => {
        navigationConfirmPending.current = false
        if (!approved) return
        clearDirty()
        navigate(`${destination.pathname}${destination.search}${destination.hash}`)
      })
    }
    document.addEventListener('click', onDocumentClick, true)
    return () => document.removeEventListener('click', onDocumentClick, true)
  }, [confirm, isTouched, navigate, preciseDirtyCount])

  // `slug` is set when navigation came from a search result, so the deep-link
  // effect above can scroll to that section instead of leaving the operator at
  // the top of an 11-section tab.
  const selectTab = async (next: Tab, slug?: string) => {
    const currentSection = searchParams.get('section')
    if (next === tab && slug === (currentSection ?? undefined)) return
    if (shouldBlockNavigation(dirtyRef.current.size, touchedRef.current)) {
      if (!(await confirm({
        title: 'Discard unsaved changes?',
        description: 'You have unsaved changes on this tab. Switch tabs and discard them?',
        confirmLabel: 'Switch and discard',
        variant: 'warning',
      }))) return
    }
    clearDirty()
    setTab(next)
    const nextParams = new URLSearchParams(searchParams)
    nextParams.set('tab', next)
    if (slug) nextParams.set('section', slug)
    else nextParams.delete('section')
    setSearchParams(nextParams, { replace: true })
  }

  const selectSection = async (nextSection: string) => {
    const currentSection = searchParams.get('section')
    if (nextSection === currentSection) return
    if (shouldBlockNavigation(dirtyRef.current.size, touchedRef.current)) {
      if (!(await confirm({
        title: 'Discard unsaved changes?',
        description: 'You have unsaved changes in this section. Switch sections and discard them?',
        confirmLabel: 'Switch and discard',
        variant: 'warning',
      }))) return
    }
    clearDirty()
    const nextParams = new URLSearchParams(searchParams)
    nextParams.set('tab', tab)
    nextParams.set('section', nextSection)
    setSearchParams(nextParams, { replace: true })
  }

  const backToHub = async () => {
    if (shouldBlockNavigation(dirtyRef.current.size, touchedRef.current)) {
      if (!(await confirm({
        title: 'Discard unsaved changes?',
        description: 'You have unsaved changes in this section. Leave this section and discard them?',
        confirmLabel: 'Leave and discard',
        variant: 'warning',
      }))) return
    }
    clearDirty()
    const nextParams = new URLSearchParams(searchParams)
    nextParams.set('tab', tab)
    nextParams.delete('section')
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

  const section = searchParams.get('section')
  const tabSections = SETTINGS_INDEX.filter(it => it.tab === tab)
  const activeSectionDef = section ? SECTION_BY_SLUG.get(section) : undefined
  const isFocusedView = Boolean(section && activeSectionDef && activeSectionDef.tab === tab)

  const tabsDef: TabDef[] = TABS.map(t => ({
    id: t.id,
    label: t.label,
    render: () => (
      <div
        className="settings-shell"
        onBlurCapture={suppressDiscardBlur}
        onFocus={handleNumericFocus}
        onInput={markTouched}
        onChange={markTouched}
      >
        <div className="settings-panel" key={draftEpoch}>
          {!isFocusedView ? (
            <CategoryHub
              tab={t.id}
              settings={settings}
              status={status}
              dirtyIds={dirtyRef.current}
              onSelectSection={selectSection}
            />
          ) : (
            <>
              <div className="settings-breadcrumb-bar">
                <nav className="settings-breadcrumb" aria-label="Settings section breadcrumb">
                  <button type="button" className="settings-breadcrumb-back" onClick={backToHub}>
                    ← Back to {t.label}
                  </button>
                  <span className="settings-breadcrumb-separator" aria-hidden="true">/</span>
                  <span className="settings-breadcrumb-current">{activeSectionDef?.title}</span>
                </nav>
                <div className="settings-sibling-switcher" role="navigation" aria-label="Sections in this category">
                  {tabSections.map(s => (
                    <button
                      key={s.slug}
                      type="button"
                      className={`settings-pill ${s.slug === section ? 'active' : ''}`}
                      onClick={() => selectSection(s.slug)}
                      aria-current={s.slug === section ? 'page' : undefined}
                    >
                      <span>{s.title}</span>
                      {dirtyRef.current.has(s.slug) && <span className="settings-pill-draft-dot" title="Unsaved changes" />}
                    </button>
                  ))}
                </div>
              </div>
              {t.id === 'agent'     && <AgentModelsTab        status={status} settings={settings} qc={qc} onDirty={registerDirty} sectionId={section ?? undefined} />}
              {t.id === 'execution' && <ExecutionTab          status={status} settings={settings} qc={qc} onDirty={registerDirty} sectionId={section ?? undefined} />}
              {t.id === 'risk'      && <RiskTab               settings={settings} qc={qc} onDirty={registerDirty} sectionId={section ?? undefined} />}
              {t.id === 'health'    && <HealthNotificationsTab settings={settings} qc={qc} onDirty={registerDirty} sectionId={section ?? undefined} />}
              {t.id === 'runtime'   && <RuntimeSystemTab      status={status} settings={settings} qc={qc} onDirty={registerDirty} sectionId={section ?? undefined} />}
              {t.id === 'signals'   && <SignalsTab            settings={settings} qc={qc} onDirty={registerDirty} sectionId={section ?? undefined} />}
              {t.id === 'advanced'  && <AdvancedTab           settings={settings} sectionId={section ?? undefined} />}
            </>
          )}
        </div>
      </div>
    ),
  }))

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

      <Tabs tabs={tabsDef} value={tab} onChange={id => selectTab(id as Tab)} />
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
