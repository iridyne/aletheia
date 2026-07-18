import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import ConnectionStatus, { ConnState } from '../components/ConnectionStatus'
import PageHeader from '../components/PageHeader'
import {
  CollapsibleSection as Collapsible,
  NumberFieldGrid as NumGrid,
  SaveMessage as SaveMsg,
  SettingsRow as Row,
  SettingsSection as Section,
  Switch as Toggle,
  SwitchGrid as ToggleGrid,
  type BoolField,
  type NumField,
} from '../components/ui'
import { useDecimals, setDecimals, fmtNum, DECIMAL_BOUNDS, humanize, humanizeBool } from '../lib/format'

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const PERMISSION_MODES = ['Observe', 'Paper', 'Full']
const ALL_PROVIDERS = ['openai_compatible', 'anthropic_native', 'openai_native', 'claude_cli', 'codex_cli'] as const
const HTTP_PROVIDERS = new Set(['openai_compatible', 'anthropic_compatible', 'anthropic_native', 'openai_native'])
const NATIVE_PROVIDERS = new Set(['anthropic_native', 'openai_native'])
const PROVIDER_LABELS: Record<string, string> = {
  openai_compatible: 'API',
  anthropic_native: 'Claude OAuth (Experimental)',
  openai_native: 'OpenAI OAuth (Experimental)',
  codex_cli: 'Codex CLI',
  claude_cli: 'Claude Code CLI',
}
const OAUTH_PROVIDERS = new Set(['anthropic_native', 'openai_native'])
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
 * MAINTAIN INVARIANT: every `<Section title="X">` in this file must have a
 * matching entry below. Drift means the search will miss that section.
 */
const SETTINGS_INDEX: { title: string; tab: Tab; alt?: string[] }[] = [
  // Agent & Models
  { title: 'Permission Mode',          tab: 'agent', alt: ['autonomy', 'observe', 'paper', 'full'] },
  { title: 'Learning',                 tab: 'agent', alt: ['dreaming', 'research', 'pi daemon', 'paper learning', 'experiments', 'contribution', 'lifecycle', 'guard', 'lesson guard', 'auto promote', 'guard candidate', 'controller', 'shadow', 'filter tuning', 'opportunity funnel'] },
  { title: 'Regime Detection',         tab: 'agent', alt: ['regime', 'bias', 'short enabled', 'bear', 'trend', 'direction'] },
  { title: 'Serenity Research',        tab: 'agent', alt: ['serenity', 'auto promote', 'pi evaluation', 'long horizon', 'industrial chain'] },
  { title: 'Scheduler',                tab: 'agent', alt: ['jobs', 'cron', 'autostart agents'] },
  { title: 'AI Model Providers',       tab: 'agent', alt: ['llm', 'openai', 'anthropic', 'claude', 'codex'] },
  // Execution
  { title: 'Exchange Connection',      tab: 'execution', alt: ['bitget', 'okx', 'binance', 'api key'] },
  { title: 'Trading Mode',             tab: 'execution', alt: ['demo', 'live unlock', 'paper'] },
  { title: 'Paper Trading Parameters', tab: 'execution', alt: ['stop loss', 'take profit', 'hold time', 'confidence'] },
  { title: 'Capital Events',           tab: 'execution', alt: ['deposit', 'withdrawal', 'pnl baseline'] },
  { title: 'Local Paper Account',      tab: 'execution', alt: ['no exchange', 'paper cash', 'available balance', 'close positions'] },
  // Risk & Universe
  { title: 'Universe Board',           tab: 'risk', alt: ['scanner', 'tradable universe'] },
  { title: 'Strategy Factory',         tab: 'risk', alt: ['auto-strategy', 'designer'] },
  { title: 'Event Triggers',           tab: 'risk', alt: ['threshold', 'fire conditions'] },
  { title: 'Tradable Symbols',         tab: 'risk', alt: ['allowlist', 'symbol filter'] },
  { title: 'Risk Limits',              tab: 'risk', alt: ['notional cap', 'leverage', 'daily loss', 'margin ratio', 'kill switch', 'force close'] },
  // Health & Alerts
  { title: 'Health Monitoring',        tab: 'health', alt: ['alert thresholds', 'self-heal'] },
  { title: 'Telegram',                 tab: 'health', alt: ['bot', 'chat id', 'notification'] },
  { title: 'Webhook',                  tab: 'health', alt: ['slack', 'discord', 'n8n', 'hmac'] },
  // Runtime & System
  { title: 'Display & App',            tab: 'runtime', alt: ['decimals', 'theme', 'dashboard', 'shell', 'auto open browser', 'pnl mode'] },
  { title: 'Data & Retention',         tab: 'runtime', alt: ['backfill', 'ohlcv storage', 'backtest', 'prune', 'cleanup', 'telemetry'] },
  { title: 'Software Updates',         tab: 'runtime', alt: ['git pull', 'upgrade', 'self-update'] },
  // Signals
  { title: 'Market Data Sources',      tab: 'signals', alt: ['ohlcv', 'data provider', 'override', 'long short', 'lsr'] },
  { title: 'Macro & Sentiment Signals',tab: 'signals', alt: ['fear greed', 'sentiment', 'coingecko', 'top movers', 'macro'] },
  { title: 'News Feeds',               tab: 'signals', alt: ['rss', 'ingestion'] },
  { title: 'Prediction Markets',       tab: 'signals', alt: ['polymarket', 'categories'] },
  { title: 'Observed US Equities',     tab: 'signals', alt: ['xstocks', 'equity'] },
  // Advanced
  { title: 'Configuration Coverage',   tab: 'advanced', alt: ['coverage matrix', 'config audit'] },
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

function parseCsvList(raw: string): string[] {
  return raw.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
}

function listToCsv(value: unknown): string {
  return Array.isArray(value) ? value.join(', ') : ''
}

function formatConfigValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  return JSON.stringify(value)
}

function flattenConfig(value: unknown, prefix = ''): Array<{ key: string; value: unknown }> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return prefix ? [{ key: prefix, value }] : []
  const rows: Array<{ key: string; value: unknown }> = []
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (child && typeof child === 'object' && !Array.isArray(child)) rows.push(...flattenConfig(child, path))
    else rows.push({ key: path, value: child })
  }
  return rows
}

// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────

export default function Settings() {
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
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

  const selectTab = (next: Tab) => {
    if (dirtyRef.current.size > 0) {
      if (!confirm('You have unsaved changes on this tab. Switch tabs and discard them?')) return
    }
    dirtyRef.current.clear()
    setTab(next)
    const nextParams = new URLSearchParams(searchParams)
    nextParams.set('tab', next)
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
          {tab === 'runtime'   && <RuntimeSystemTab      settings={settings} qc={qc} onDirty={registerDirty} />}
          {tab === 'signals'   && <SignalsTab            settings={settings} qc={qc} onDirty={registerDirty} />}
          {tab === 'advanced'  && <AdvancedTab           settings={settings} />}
        </div>
      </div>
    </div>
  )
}

/**
 * Inline fuzzy search across all Settings sections. ⌘/Ctrl + K to focus, Esc to close.
 * Results are tab-aware: clicking a result switches the current tab and lets the
 * existing ?section= deep-link mechanism handle scroll-to-section.
 */
function SettingsSearch({ onPick }: { onPick: (tab: Tab) => void }) {
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
    onPick(it.tab)
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
    status: 'supported' | 'consumer_unwired' | 'authority_unwired'
  }> : []
  const knownLearningPathIds = new Set(learningPathOptions.map(option => option.path_id))
  const unknownPaperInfluencePaths = paperInfluencePaths.filter(
    (path: string) => !knownLearningPathIds.has(path),
  )
  const autoRefreeze = learningCfg.auto_refreeze !== false
  const evaluationInterval = Number(learningCfg.evaluation_interval_minutes ?? 60)
  const paperActiveAllocation = Number(learningCfg.paper_active_allocation_pct ?? 0.80)
  const guardCfg = (settings?.guard ?? {}) as any
  const guardAutoPromote = guardCfg.auto_promote_enabled !== false

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
        <div className="save-err-banner" role="alert" style={{
          background: 'var(--red-bg, rgba(255,0,0,0.08))',
          color: 'var(--red)',
          padding: '0.6em 0.8em',
          borderRadius: 4,
          marginBottom: 12,
        }}>
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
          <Row label="Allowed paper paths" hint="Only paths with a wired consumer and prospective authority adapter can be selected. Unsupported design entries remain visible but disabled.">
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
                  <span style={{ color: option.supported ? 'var(--green)' : 'var(--muted)' }}>
                    {option.supported ? 'Supported' : 'Not connected'}
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
        <Collapsible title="Guard Promotion">
          <p className="hint mb-2">Post-trade lesson guards learned by the dreaming agent. Candidates that accumulate enough matching trades are promoted to active guards that block future trades.</p>
          {/* #36 fix: previously only `auto_promote_enabled` was visible even though the
              backend GuardConfig carries `enabled` / `enforce_in_paper_ranker` /
              `min_matching_trades` / `max_score_penalty`. The API accepts all four,
              so hiding them left the settings page mismatched with backend truth. */}
          <Row
            label="Guards enabled"
            hint="Master switch. When OFF, guards do nothing — no scoring penalty, no auto-promotion, no candidate accumulation."
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
            label="Auto-promote guard candidates"
            hint="When ON, the lesson_reviewer promotes guard_candidate entries to active trading guards automatically. When OFF, guards are promoted manually only."
          >
            <Toggle
              checked={guardAutoPromote}
              onChange={v => {
                if (v && !confirm('Enable auto-promote for lesson guards? Candidates that accumulate enough matching trades will block future trades without human review.')) return
                guardMut.mutate({ auto_promote_enabled: v })
              }}
              disabled={guardMut.isPending}
            />
          </Row>
          <Row
            label="Min matching trades before promotion"
            hint="A candidate guard needs at least this many matching trades before auto-promotion considers it. Higher = more evidence required, slower promotions."
          >
            <input
              type="number"
              min={1}
              max={20}
              value={Number(guardCfg.min_matching_trades ?? 3)}
              onChange={e => {
                const n = Math.max(1, Math.min(20, Math.round(Number(e.target.value) || 0)))
                guardMut.mutate({ min_matching_trades: n })
              }}
              disabled={guardMut.isPending}
              style={{ width: 80 }}
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

      <Section title="AI Model Providers" description="LLM endpoints used by all agents. Verify & Save tests the connection before persisting. API keys are write-only.">
        <ModelProviderCard qc={qc} />
      </Section>
    </div>
  )
}

// ─── Strategy Factory config ───

function StrategyFactoryCard({ settings, qc }: any) {
  const sf = settings?.strategy_factory as any
  const [vals, setVals] = useState<Record<string, unknown>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!sf) return
    setVals({
      backtest_gate_enabled: sf.backtest_gate_enabled ?? true,
      sweep_enabled: sf.sweep_enabled ?? true,
      sweep_max_variants: sf.sweep_max_variants ?? 100,
      sweep_use_optuna: sf.sweep_use_optuna ?? false,
      sweep_n_trials: sf.sweep_n_trials ?? 60,
      sweep_optuna_timeout_sec: sf.sweep_optuna_timeout_sec ?? 120,
      sweep_top_k: sf.sweep_top_k ?? 10,
      event_driven_enabled: sf.event_driven_enabled ?? true,
      event_cooldown_minutes: sf.event_cooldown_minutes ?? 30,
      event_min_score: sf.event_min_score ?? 60,
      walk_forward_enabled: sf.walk_forward_enabled ?? true,
      walk_forward_folds: sf.walk_forward_folds ?? 3,
      walk_forward_min_bars: sf.walk_forward_min_bars ?? 150,
      high_trade_count_threshold: sf.high_trade_count_threshold ?? 30,
      min_runtime_days_relaxed: sf.min_runtime_days_relaxed ?? 3,
    })
  }, [sf])

  const { mutate: save } = useMutation({
    mutationFn: () => api.updateStrategyFactoryConfig(vals as any),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setMsg({ ok: true, text: 'Saved' }) },
    onError: (e: any) => setMsg({ ok: false, text: e.message }),
  })

  if (!sf) return null

  const boolFields: BoolField[] = [
    { key: 'backtest_gate_enabled', label: 'Backtest gate' },
    { key: 'sweep_enabled', label: 'Parameter sweep' },
    { key: 'sweep_use_optuna', label: 'Optuna (Bayesian)' },
    { key: 'event_driven_enabled', label: 'Event-driven design' },
    { key: 'walk_forward_enabled', label: 'Walk-forward validation' },
  ]

  const numFields: NumField[] = [
    { key: 'sweep_max_variants', label: 'Sweep max variants', unit: '', min: 10, max: 500, step: 10, isInt: true },
    { key: 'sweep_n_trials', label: 'Optuna trials', unit: '', min: 10, max: 300, step: 10, isInt: true },
    { key: 'sweep_optuna_timeout_sec', label: 'Optuna timeout', unit: 's', min: 30, max: 600, step: 10, isInt: false },
    { key: 'sweep_top_k', label: 'Optuna top-K', unit: '', min: 3, max: 30, step: 1, isInt: true },
    { key: 'event_cooldown_minutes', label: 'Event cooldown', unit: 'min', min: 5, max: 120, step: 1, isInt: true },
    { key: 'event_min_score', label: 'Event min score', unit: '', min: 45, max: 80, step: 1, isInt: true },
    { key: 'walk_forward_folds', label: 'Walk-forward folds', unit: '', min: 2, max: 10, step: 1, isInt: true },
    { key: 'walk_forward_min_bars', label: 'Walk-forward min bars', unit: '', min: 60, max: 1000, step: 10, isInt: true },
    { key: 'high_trade_count_threshold', label: 'High-trade threshold', unit: '', min: 10, max: 100, step: 1, isInt: true },
    { key: 'min_runtime_days_relaxed', label: 'Min runtime days (relaxed)', unit: 'days', min: 1, max: 14, step: 1, isInt: true },
  ]

  const setBool = (key: string, value: boolean) => setVals(v => ({ ...v, [key]: value }))
  const setNum = (key: string, value: number) => setVals(v => ({ ...v, [key]: value }))

  return (
    <div className="space-y-4">
      <ToggleGrid fields={boolFields} vals={vals} set={setBool} />
      <NumGrid fields={numFields} vals={vals} set={setNum} />
      <button onClick={() => save()} className="btn btn-primary btn-sm mt-2">Save Strategy Factory</button>
      <SaveMsg msg={msg} />
    </div>
  )
}

// ─── Historical Data config ───

function HistoricalDataCard({ settings, qc }: any) {
  const hd = settings?.historical_data as any
  const [vals, setVals] = useState<Record<string, unknown>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!hd) return
    setVals({
      enabled: hd.enabled ?? true,
      max_bars_per_pair: hd.max_bars_per_pair ?? 50000,
      default_months: hd.default_months ?? 6,
      nightly_backfill_enabled: hd.nightly_backfill_enabled ?? true,
      nightly_backfill_hour_cst: hd.nightly_backfill_hour_cst ?? 3,
      min_bars_for_backtest: hd.min_bars_for_backtest ?? 200,
      active_universe_backfill_enabled: hd.active_universe_backfill_enabled ?? true,
      active_universe_backfill_interval_minutes: hd.active_universe_backfill_interval_minutes ?? 10,
      active_universe_backfill_max_pairs_per_run: hd.active_universe_backfill_max_pairs_per_run ?? 3,
    })
  }, [hd])

  const { mutate: save } = useMutation({
    mutationFn: () => api.updateHistoricalDataConfig(vals as any),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setMsg({ ok: true, text: 'Saved' }) },
    onError: (e: any) => setMsg({ ok: false, text: e.message }),
  })

  if (!hd) return null

  const boolFields: BoolField[] = [
    { key: 'enabled', label: 'Enabled' },
    { key: 'nightly_backfill_enabled', label: 'Nightly backfill' },
    { key: 'active_universe_backfill_enabled', label: 'Active Universe catch-up' },
  ]
  const numFields: NumField[] = [
    { key: 'max_bars_per_pair', label: 'Max bars/pair', unit: '', min: 1000, max: 200000, step: 5000, isInt: true },
    { key: 'default_months', label: 'Default months', unit: 'mo', min: 1, max: 24, step: 1, isInt: true },
    { key: 'nightly_backfill_hour_cst', label: 'Backfill hour (CST)', unit: 'h', min: 0, max: 23, step: 1, isInt: true },
    { key: 'min_bars_for_backtest', label: 'Min bars for backtest', unit: '', min: 30, max: 5000, step: 10, isInt: true },
    { key: 'active_universe_backfill_interval_minutes', label: 'Active catch-up interval', unit: 'min', min: 5, max: 60, step: 5, isInt: true },
    { key: 'active_universe_backfill_max_pairs_per_run', label: 'Active catch-up pairs/run', unit: '', min: 1, max: 10, step: 1, isInt: true },
  ]

  const setBool = (key: string, value: boolean) => setVals(v => ({ ...v, [key]: value }))
  const setNum = (key: string, value: number) => setVals(v => ({ ...v, [key]: value }))

  return (
    <div className="space-y-4">
      <ToggleGrid fields={boolFields} vals={vals} set={setBool} />
      <NumGrid fields={numFields} vals={vals} set={setNum} />
      <button onClick={() => save()} className="btn btn-primary btn-sm mt-2">Save Historical Data</button>
      <SaveMsg msg={msg} />
    </div>
  )
}

// ─── Event Triggers config ───

function EventTriggerCard({ settings, qc }: any) {
  const et = settings?.event_triggers as any
  const [vals, setVals] = useState<Record<string, unknown>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!et) return
    setVals({
      enabled: et.enabled ?? true,
      price_move_pct_threshold: et.price_move_pct_threshold ?? 3.0,
      volume_surge_multiplier: et.volume_surge_multiplier ?? 3.0,
      watch_universe_members: et.watch_universe_members ?? true,
      trigger_strategy_design: et.trigger_strategy_design ?? true,
      strategy_design_min_move_pct: et.strategy_design_min_move_pct ?? 5.0,
    })
  }, [et])

  const { mutate: save } = useMutation({
    mutationFn: () => api.updateEventTriggerConfig(vals as any),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setMsg({ ok: true, text: 'Saved' }) },
    onError: (e: any) => setMsg({ ok: false, text: e.message }),
  })

  if (!et) return null

  const boolFields: BoolField[] = [
    { key: 'enabled', label: 'Enabled' },
    { key: 'watch_universe_members', label: 'Watch universe' },
    { key: 'trigger_strategy_design', label: 'Trigger design' },
  ]
  const numFields: NumField[] = [
    { key: 'price_move_pct_threshold', label: 'Price move threshold', unit: '%', min: 0.5, max: 20, step: 0.5, isInt: false },
    { key: 'volume_surge_multiplier', label: 'Volume surge multiplier', unit: '×', min: 1.5, max: 20, step: 0.5, isInt: false },
    { key: 'strategy_design_min_move_pct', label: 'Design trigger min move', unit: '%', min: 1, max: 30, step: 0.5, isInt: false },
  ]

  const setBool = (key: string, value: boolean) => setVals(v => ({ ...v, [key]: value }))
  const setNum = (key: string, value: number) => setVals(v => ({ ...v, [key]: value }))

  return (
    <div className="space-y-4">
      <ToggleGrid fields={boolFields} vals={vals} set={setBool} />
      <NumGrid fields={numFields} vals={vals} set={setNum} />
      <button onClick={() => save()} className="btn btn-primary btn-sm mt-2">Save Event Triggers</button>
      <SaveMsg msg={msg} />
    </div>
  )
}

// ─── Universe config ───

function UniverseConfigCard({ settings, qc, onDirty }: any) {
  const universe = settings?.universe as any
  const [enabled, setEnabled] = useState(false)
  const [refreshMin, setRefreshMin] = useState('10')
  const [maxCandidates, setMaxCandidates] = useState('30')
  const [corePool, setCorePool] = useState('10')
  const [minVolume, setMinVolume] = useState('5000000')
  const [maxSpread, setMaxSpread] = useState('25')
  const [minDepth, setMinDepth] = useState('25000')
  const [exploreRatio, setExploreRatio] = useState('0.2')
  const [snapshotTtlEnabled, setSnapshotTtlEnabled] = useState(true)
  const [snapshotTtlHours, setSnapshotTtlHours] = useState('24')
  const [blacklist, setBlacklist] = useState('')
  const [fallbackSymbols, setFallbackSymbols] = useState('BTC/USDT, ETH/USDT, SOL/USDT')
  const [marketType, setMarketType] = useState('crypto')
  const [familyQuota, setFamilyQuota] = useState<Record<string, string>>({})
  const [familyQuotaByRegime, setFamilyQuotaByRegime] = useState<Record<string, Record<string, string>>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [changed, setChanged] = useState(false)

  useEffect(() => {
    if (!universe) return
    setEnabled(!!universe.enabled)
    setRefreshMin(String(universe.refresh_interval_min ?? 10))
    setMaxCandidates(String(universe.max_candidates ?? 30))
    setCorePool(String(universe.core_pool_size ?? 10))
    setMinVolume(String(universe.min_volume_24h_usdt ?? 5000000))
    setMaxSpread(String(universe.max_spread_bps ?? 25))
    setMinDepth(String(universe.min_depth_10bps_usd ?? 25000))
    setExploreRatio(String(universe.explore_ratio ?? 0.2))
    setSnapshotTtlEnabled(universe.snapshot_ttl_enabled ?? true)
    setSnapshotTtlHours(String(universe.snapshot_ttl_hours ?? 24))
    setBlacklist(listToCsv(universe.blacklist))
    setFallbackSymbols(listToCsv(universe.fallback_symbols ?? ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']))
    setMarketType(universe.market_type || 'crypto')
    const fq = universe.family_quota || { momentum: 0.3, mean_reversion: 0.2, derivatives: 0.3, event: 0.2 }
    const stringified: Record<string, string> = {}
    for (const [k, v] of Object.entries(fq)) stringified[k] = String(v)
    setFamilyQuota(stringified)
    const fqr = (universe.family_quota_by_regime || {}) as Record<string, Record<string, number>>
    const fqrStr: Record<string, Record<string, string>> = {}
    for (const [regime, quota] of Object.entries(fqr)) {
      fqrStr[regime] = {}
      for (const [fam, v] of Object.entries(quota)) fqrStr[regime][fam] = String(v)
    }
    setFamilyQuotaByRegime(fqrStr)
  }, [settings])

  const mut = useMutation({
    mutationFn: () => {
      const fqParsed: Record<string, number> = {}
      for (const [k, v] of Object.entries(familyQuota)) fqParsed[k] = parseFloat(v) || 0
      const fqrParsed: Record<string, Record<string, number>> = {}
      for (const [regime, quota] of Object.entries(familyQuotaByRegime)) {
        fqrParsed[regime] = {}
        for (const [fam, v] of Object.entries(quota)) fqrParsed[regime][fam] = parseFloat(v) || 0
      }
      return api.updateUniverseConfig({
        family_quota_by_regime: fqrParsed,
        enabled,
        refresh_interval_min: parseInt(refreshMin),
        max_candidates: parseInt(maxCandidates),
        core_pool_size: parseInt(corePool),
        min_volume_24h_usdt: parseFloat(minVolume),
        max_spread_bps: parseFloat(maxSpread),
        min_depth_10bps_usd: parseFloat(minDepth),
        explore_ratio: parseFloat(exploreRatio),
        snapshot_ttl_enabled: snapshotTtlEnabled,
        snapshot_ttl_hours: parseFloat(snapshotTtlHours),
        blacklist: parseCsvList(blacklist),
        fallback_symbols: parseCsvList(fallbackSymbols),
        market_type: marketType,
        family_quota: fqParsed,
      })
    },
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); setChanged(false); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  useEffect(() => {
    onDirty?.('universe-config', changed)
    return () => onDirty?.('universe-config', false)
  }, [changed])

  if (!universe) return null

  return (
    <div className="card mb-0">
      <Row
        label="Enable Universe Scanner"
        hint="When on, dynamically discovers and scores USDT pairs each refresh cycle. When off, agents fall back to the static symbol list (BTC/ETH/SOL)."
      >
        <Toggle checked={enabled} onChange={v => { setEnabled(v); setChanged(true); setMsg(null) }} />
      </Row>
      <div className="settings-field-grid">
        <label className="settings-field">
          <span>Refresh interval (min)</span>
          <input type="number" min={1} max={60} step={1} value={refreshMin}
            onChange={e => { setRefreshMin(e.target.value); setChanged(true); setMsg(null) }} />
        </label>
        <label className="settings-field">
          <span>Max candidates</span>
          <input type="number" min={5} max={100} step={1} value={maxCandidates}
            onChange={e => { setMaxCandidates(e.target.value); setChanged(true); setMsg(null) }} />
        </label>
        <label className="settings-field">
          <span>Core pool size</span>
          <input type="number" min={1} max={50} step={1} value={corePool}
            onChange={e => { setCorePool(e.target.value); setChanged(true); setMsg(null) }} />
        </label>
        <label className="settings-field">
          <span>Min 24h volume (USDT)</span>
          <input type="number" min={100000} max={500000000} step={100000} value={minVolume}
            onChange={e => { setMinVolume(e.target.value); setChanged(true); setMsg(null) }} />
        </label>
        <label className="settings-field">
          <span>Max spread (bps)</span>
          <input type="number" min={1} max={200} step={1} value={maxSpread}
            onChange={e => { setMaxSpread(e.target.value); setChanged(true); setMsg(null) }} />
        </label>
        <label className="settings-field">
          <span>Min depth @ 10bps (USD)</span>
          <input type="number" min={1000} max={10000000} step={1000} value={minDepth}
            onChange={e => { setMinDepth(e.target.value); setChanged(true); setMsg(null) }} />
        </label>
        <label className="settings-field">
          <span>Explore ratio</span>
          <input type="number" min={0} max={0.5} step={0.05} value={exploreRatio}
            onChange={e => { setExploreRatio(e.target.value); setChanged(true); setMsg(null) }} />
        </label>
        <label className="settings-field">
          <span>Snapshot TTL</span>
          <div className="flex items-center gap-2">
            <Toggle checked={snapshotTtlEnabled} onChange={v => { setSnapshotTtlEnabled(v); setChanged(true); setMsg(null) }} />
            <input type="number" min={1} max={72} step={1} value={snapshotTtlHours}
              disabled={!snapshotTtlEnabled}
              onChange={e => { setSnapshotTtlHours(e.target.value); setChanged(true); setMsg(null) }} />
            <span className="hint m-0">hrs{!snapshotTtlEnabled ? ' (disabled — toggle on to edit)' : ''}</span>
          </div>
        </label>
      </div>
      <div className="form-grid mb-3">
        <label>
          <span>Universe blacklist</span>
          <input value={blacklist} placeholder="DOGE/USDT, PEPE/USDT"
            onChange={e => { setBlacklist(e.target.value); setChanged(true); setMsg(null) }}
            className="mono" />
        </label>
        <label>
          <span>Fallback symbols</span>
          <input value={fallbackSymbols} placeholder="BTC/USDT, ETH/USDT, SOL/USDT"
            onChange={e => { setFallbackSymbols(e.target.value); setChanged(true); setMsg(null) }}
            className="mono" />
        </label>
      </div>
      <div className="form-grid mb-3">
        <label>
          <span>Market type</span>
          <select value={marketType} onChange={e => { setMarketType(e.target.value); setChanged(true); setMsg(null) }} className="w-auto">
            <option value="crypto">crypto</option>
            <option value="stocks">stocks</option>
            <option value="mixed">mixed</option>
          </select>
        </label>
      </div>
      <div className="mb-3">
        <span className="settings-field-label">Family quota (must sum to 1.0)</span>
        <div className="settings-field-grid">
          {Object.entries(familyQuota).map(([family, val]) => (
            <label className="settings-field" key={family}>
              <span>{humanize(family)}</span>
              <input type="number" min={0} max={1} step={0.05} value={val}
                onChange={e => { setFamilyQuota(prev => ({ ...prev, [family]: e.target.value })); setChanged(true); setMsg(null) }} />
            </label>
          ))}
        </div>
        {(() => {
          const total = Object.values(familyQuota).reduce((s, v) => s + (parseFloat(v) || 0), 0)
          const ok = Math.abs(total - 1.0) < 0.001
          return <div style={{ fontSize: '0.72rem', color: ok ? 'var(--green)' : 'var(--red)', marginTop: 'var(--space-1)' }}>Total: {total.toFixed(3)}{ok ? ' ✓' : ' — must equal 1.0'}</div>
        })()}
      </div>
      <div className="mb-3">
        <span className="settings-field-label">Per-regime family quota (2A+.9 — overrides the static quota above when that regime is active; each row must sum to 1.0)</span>
        {Object.entries(familyQuotaByRegime).map(([regime, quota]) => {
          const rt = Object.values(quota).reduce((s, v) => s + (parseFloat(v) || 0), 0)
          const ok = Math.abs(rt - 1.0) < 0.001
          return (
            <div key={regime} className="mb-2">
              <span className="settings-field-label">{humanize(regime)}</span>
              <div className="settings-field-grid">
                {Object.entries(quota).map(([family, val]) => (
                  <label className="settings-field" key={family}>
                    <span>{humanize(family)}</span>
                    <input type="number" min={0} max={1} step={0.05} value={val}
                      onChange={e => {
                        const nv = e.target.value
                        setFamilyQuotaByRegime(prev => ({ ...prev, [regime]: { ...prev[regime], [family]: nv } }))
                        setChanged(true); setMsg(null)
                      }} />
                  </label>
                ))}
              </div>
              <div style={{ fontSize: '0.72rem', color: ok ? 'var(--green)' : 'var(--red)', marginTop: 'var(--space-1)' }}>Total: {rt.toFixed(3)}{ok ? ' ✓' : ' — must equal 1.0'}</div>
            </div>
          )
        })}
      </div>
      <div className="flex items-center gap-2">
        <button onClick={() => {
          const total = Object.values(familyQuota).reduce((s, v) => s + (parseFloat(v) || 0), 0)
          if (Math.abs(total - 1.0) > 0.001) {
            setMsg({ ok: false, text: `Family quota must sum to 1.0 (currently ${total.toFixed(3)})` })
            return
          }
          for (const [regime, quota] of Object.entries(familyQuotaByRegime)) {
            const rt = Object.values(quota).reduce((s, v) => s + (parseFloat(v) || 0), 0)
            if (Math.abs(rt - 1.0) > 0.001) {
              setMsg({ ok: false, text: `${humanize(regime)} regime quota must sum to 1.0 (currently ${rt.toFixed(3)})` })
              return
            }
          }
          mut.mutate()
        }} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save'}</button>
      </div>
      <SaveMsg msg={msg} />
      {universe?.scanner_weights && Object.keys(universe.scanner_weights).length > 0 && (
        <div className="mb-3">
          <span className="settings-field-label">Scanner weights (operator-adjusted, read-only)</span>
          <div className="settings-field-grid">
            {Object.entries(universe.scanner_weights as Record<string, number>).map(([family, w]) => (
              <label className="settings-field" key={family}>
                <span>{humanize(family)}</span>
                <input type="number" value={w} readOnly className="mono" />
              </label>
            ))}
          </div>
          <p className="hint m-0">Adjusted autonomously by the System Operator. Reset via <code>aletheia config set universe.scanner_weights &#123;&#125;</code>.</p>
        </div>
      )}
      {universe?.scanner_regime_scoring && Object.keys(universe.scanner_regime_scoring).length > 0 && (
        <div className="mb-3">
          <span className="settings-field-label">Regime scoring deltas (soft, read-only)</span>
          <div className="settings-field-grid">
            {Object.entries(universe.scanner_regime_scoring as Record<string, number>).map(([k, v]) => (
              <label className="settings-field" key={k}>
                <span>{humanize(k)}</span>
                <input type="number" value={v} readOnly className="mono" />
              </label>
            ))}
          </div>
          <p className="hint m-0">Soft ranker score deltas applied per candidate by regime × direction/family (applies to strategy-less opportunities too — not a hard block). Tune via <code>aletheia config set universe.scanner_regime_scoring &#123;…&#125;</code>.</p>
        </div>
      )}
      <p className="hint">
        When enabled, the Universe Generator scans all exchange USDT pairs every {refreshMin} min, scores them by momentum/mean-reversion/volatility/derivatives,
        and feeds the top {maxCandidates} to agents. Explore ratio ({(parseFloat(exploreRatio) * 100).toFixed(0)}%) adds random diversity.
        Interval and TTL changes take effect after gateway restart.
      </p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// EXECUTION TAB — exchange, credentials, mode, markets, live unlock, trading params
// ─────────────────────────────────────────────────────────────────────────────

function executionExchangeFromPaperMode(mode: string, configuredExchange = ''): string | null {
  if (mode === 'paper_internal') return null
  const byMode: Record<string, string> = {
    bitget_demo: 'bitget',
    binance_testnet: 'binance',
    binance_live: 'binance',
    okx_demo: 'okx',
    okx_live: 'okx',
  }
  return byMode[mode] || configuredExchange || null
}

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

      <Section title="Trading Mode — Advanced" description="Switch between demo (paper trading) and live execution. Live requires explicit unlock below. Individual overrides for data source and execution mode are available in Market Data Sources below.">
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
        description="Record deposits and withdrawals so the Cumulative PnL curve reflects true performance. Initial deposits are auto-recorded when a broker first syncs."
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

function MarketDataCard({ settings, qc, onDirty }: any) {
  const md = settings?.market_data || {}
  const exchangeSettings = settings?.exchange || {}
  const EXCHANGES = ['binance', 'bybit', 'okx', 'bitget', 'gate', 'kucoin', 'htx']

  const [primary, setPrimary] = useState(md.ccxt_exchange || 'binance')
  const [fallback, setFallback] = useState(md.ccxt_fallback || 'bybit')
  const [cacheTtl, setCacheTtl] = useState(md.ohlcv_cache_ttl_min ?? 10)
  const [yfinance, setYfinance] = useState(!!md.enable_yfinance_macro)
  const [longShortRatio, setLongShortRatio] = useState(md.enable_long_short_ratio ?? true)
  const [historyHours, setHistoryHours] = useState(md.derivatives_history_hours ?? 24)
  const [stocks, setStocks] = useState(listToCsv(md.observed_stocks))
  const [stockTokens, setStockTokens] = useState(listToCsv(md.observed_stock_tokens))
  const [predMarkets, setPredMarkets] = useState(listToCsv(md.observed_prediction_markets))
  const [confirmExchangeDivergence, setConfirmExchangeDivergence] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    setPrimary(md.ccxt_exchange || 'binance')
    setFallback(md.ccxt_fallback || 'bybit')
    setCacheTtl(md.ohlcv_cache_ttl_min ?? 10)
    setYfinance(!!md.enable_yfinance_macro)
    setLongShortRatio(md.enable_long_short_ratio ?? true)
    setHistoryHours(md.derivatives_history_hours ?? 24)
    setStocks(listToCsv(md.observed_stocks))
    setStockTokens(listToCsv(md.observed_stock_tokens))
    setPredMarkets(listToCsv(md.observed_prediction_markets))
    setConfirmExchangeDivergence(false)
  }, [settings])

  const executionExchange = executionExchangeFromPaperMode(
    String(exchangeSettings.paper_execution_mode || ''),
    String(exchangeSettings.name || ''),
  )
  const createsDivergence = executionExchange !== null
    && primary !== executionExchange
    && primary !== String(md.ccxt_exchange || 'binance')

  const mut = useMutation({
    mutationFn: () => api.updateMarketDataConfig({
      ccxt_exchange: primary,
      ccxt_fallback: fallback,
      ohlcv_cache_ttl_min: cacheTtl,
      enable_yfinance_macro: yfinance,
      enable_long_short_ratio: longShortRatio,
      derivatives_history_hours: historyHours,
      observed_stocks: parseCsvList(stocks),
      observed_stock_tokens: parseCsvList(stockTokens),
      observed_prediction_markets: parseCsvList(predMarkets),
      confirm_exchange_divergence: confirmExchangeDivergence,
    }),
    onSuccess: () => { setConfirmExchangeDivergence(false); setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const dirty = primary !== (md.ccxt_exchange || 'binance')
    || fallback !== (md.ccxt_fallback || 'bybit')
    || cacheTtl !== (md.ohlcv_cache_ttl_min ?? 10)
    || yfinance !== !!md.enable_yfinance_macro
    || longShortRatio !== (md.enable_long_short_ratio ?? true)
    || historyHours !== (md.derivatives_history_hours ?? 24)
    || stocks !== listToCsv(md.observed_stocks)
    || stockTokens !== listToCsv(md.observed_stock_tokens)
    || predMarkets !== listToCsv(md.observed_prediction_markets)

  useEffect(() => { onDirty?.('market-data', dirty) }, [dirty])

  return (
    <>
      <Row label="Primary exchange" hint="CCXT exchange ID used for OHLCV and ticker data. Must match a supported CCXT exchange.">
        <select value={primary} onChange={e => { setPrimary(e.target.value); setConfirmExchangeDivergence(false); setMsg(null) }} className="w-auto">
          {EXCHANGES.map(ex => <option key={ex} value={ex}>{ex}</option>)}
        </select>
      </Row>
      <Row label="Fallback exchange" hint="Used when the primary exchange fails or returns stale data.">
        <select value={fallback} onChange={e => { setFallback(e.target.value); setMsg(null) }} className="w-auto">
          {EXCHANGES.map(ex => <option key={ex} value={ex}>{ex}</option>)}
        </select>
      </Row>
      {createsDivergence && (
        <div className="settings-save-msg err mb-2">
          <Toggle
            checked={confirmExchangeDivergence}
            onChange={setConfirmExchangeDivergence}
            label={`I understand: orders will execute on ${executionExchange}, while market analysis will use ${primary}.`}
          />
        </div>
      )}
      <Row label="OHLCV cache TTL" hint="Minutes to cache candlestick data before re-fetching. Lower = fresher data, higher = fewer API calls.">
        <input type="number" min={1} max={60} value={cacheTtl}
          onChange={e => { setCacheTtl(Math.max(1, Math.min(60, parseInt(e.target.value) || 1))); setMsg(null) }}
          style={{ width: '80px' }} />
        <span className="hint">min</span>
      </Row>
      <Row label="Yahoo Finance macro" hint="Enable macro indicator collection (DXY, VIX, S&P 500) via yfinance. Requires network access to Yahoo.">
        <Toggle checked={yfinance} label="" onChange={v => { setYfinance(v); setMsg(null) }} />
      </Row>
      <Row label="Long/Short ratio" hint="Pull Binance Futures globalLongShortAccountRatio per observed symbol every 2 min. Filled into open_interest_snapshots.long_short_ratio and folded into crowding_score. USDT-margined linear perps only.">
        <Toggle checked={longShortRatio} label="" onChange={v => { setLongShortRatio(v); setMsg(null) }} />
      </Row>
      <Row label="Derivatives backfill" hint="Backfill funding/OI history on startup if the local window is sparse (idempotent — re-running with ≥3 rows in window is a no-op). 0 disables. Bitget OI history is not supported by CCXT; the path silently skips that exchange.">
        <input type="number" min={0} max={168} value={historyHours}
          onChange={e => { setHistoryHours(Math.max(0, Math.min(168, parseInt(e.target.value) || 0))); setMsg(null) }}
          style={{ width: '80px' }} />
        <span className="hint">hours (0 = off)</span>
      </Row>
      <Row label="Observed stocks" hint="Yahoo Finance tickers for macro correlation. Comma-separated (e.g. SPY, QQQ, NVDA).">
        <input value={stocks} placeholder="SPY, QQQ, NVDA, AAPL"
          onChange={e => { setStocks(e.target.value); setMsg(null) }}
          className="mono w-full" />
      </Row>
      <Row label="Stock tokens" hint="Exchange-traded equity tokens for real-time price tracking. Supports Bitget r-series (rNVDA/USDT, rTSLA/USDT) and ON-series (AAPLON/USDT). Comma-separated. Used by equity_observer for cross-validation and Serenity for supply-chain research.">
        <input value={stockTokens} placeholder="rNVDA/USDT, rTSLA/USDT, AAPLON/USDT"
          onChange={e => { setStockTokens(e.target.value); setMsg(null) }}
          className="mono w-full" />
      </Row>
      <Row label="Prediction markets" hint="Polymarket category slugs to track. Comma-separated.">
        <input value={predMarkets} placeholder="crypto, politics"
          onChange={e => { setPredMarkets(e.target.value); setMsg(null) }}
          className="mono w-full" />
      </Row>
      {dirty && (
        <div className="flex justify-end" style={{ padding: '0.5rem 0' }}>
          <button onClick={() => mut.mutate()} disabled={mut.isPending || (createsDivergence && !confirmExchangeDivergence)}>
            {mut.isPending ? 'Saving…' : 'Save market data config'}
          </button>
        </div>
      )}
      <SaveMsg msg={msg} />
    </>
  )
}

function MacroSignalsCard({ settings, qc, onDirty }: any) {
  const md = settings?.market_data || {}

  const [fearGreed, setFearGreed] = useState(md.enable_fear_greed ?? true)
  const [cgTop, setCgTop] = useState(md.enable_coingecko_top ?? true)
  const [cgTopN, setCgTopN] = useState(md.coingecko_top_n ?? 100)
  const [hyperliquid, setHyperliquid] = useState(md.enable_hyperliquid ?? true)
  const [defillama, setDefillama] = useState(md.enable_defillama ?? true)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    setFearGreed(md.enable_fear_greed ?? true)
    setCgTop(md.enable_coingecko_top ?? true)
    setCgTopN(md.coingecko_top_n ?? 100)
    setHyperliquid(md.enable_hyperliquid ?? true)
    setDefillama(md.enable_defillama ?? true)
  }, [settings])

  const mut = useMutation({
    mutationFn: () => api.updateMarketDataConfig({
      enable_fear_greed: fearGreed,
      enable_coingecko_top: cgTop,
      coingecko_top_n: cgTopN,
      enable_hyperliquid: hyperliquid,
      enable_defillama: defillama,
    }),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const dirty = fearGreed !== (md.enable_fear_greed ?? true)
    || cgTop !== (md.enable_coingecko_top ?? true)
    || cgTopN !== (md.coingecko_top_n ?? 100)
    || hyperliquid !== (md.enable_hyperliquid ?? true)
    || defillama !== (md.enable_defillama ?? true)

  useEffect(() => { onDirty?.('macro-signals', dirty) }, [dirty])

  return (
    <>
      <Row label="Fear & Greed Index" hint="Pull Alternative.me's crypto fear/greed index (single 0-100 value, updated daily). No API key. Surfaces in the macro block of enriched_context fed to the LLM.">
        <Toggle checked={fearGreed} label="" onChange={v => { setFearGreed(v); setMsg(null) }} />
      </Row>
      <Row label="CoinGecko top markets" hint="Pull top-N coins by market cap every ~10 min. Builds a 'Top Gainers / Top Losers' string folded into the macro block. No API key.">
        <Toggle checked={cgTop} label="" onChange={v => { setCgTop(v); setMsg(null) }} />
      </Row>
      <Row label="Top-N count" hint="How many coins to pull per tick (10-250). Higher = broader universe context; CoinGecko free tier caps at 30 req/min.">
        <input type="number" min={10} max={250} value={cgTopN}
          onChange={e => { setCgTopN(Math.max(10, Math.min(250, parseInt(e.target.value) || 10))); setMsg(null) }}
          style={{ width: '80px' }} />
        <span className="hint">coins</span>
      </Row>
      <Row label="Hyperliquid cross-venue" hint="Pull perp open-interest + funding from Hyperliquid's public API (no key) every ~10 min. Tagged as a cross-venue feed (kept separate from the primary exchange's per-symbol read), surfaced as cross-venue derivatives context.">
        <Toggle checked={hyperliquid} label="" onChange={v => { setHyperliquid(v); setMsg(null) }} />
      </Row>
      <Row label="DefiLlama on-chain" hint="Pull total stablecoin market cap, DeFi TVL, and DEX volume from DefiLlama's public API (no key) every ~10 min. Folded into the macro block of the LLM context as on-chain regime signal.">
        <Toggle checked={defillama} label="" onChange={v => { setDefillama(v); setMsg(null) }} />
      </Row>
      {dirty && (
        <div className="flex justify-end" style={{ padding: '0.5rem 0' }}>
          <button onClick={() => mut.mutate()} disabled={mut.isPending}>
            {mut.isPending ? 'Saving…' : 'Save macro signals config'}
          </button>
        </div>
      )}
      <SaveMsg msg={msg} />
    </>
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
    <Row label="Exchange mode" hint={`demo uses ${modeLabel[paperMode] ?? paperMode} for paper trading; live requires unlocking below and real credentials.`}>
      <select value={String(status.exchange_mode)} onChange={e => {
        const next = e.target.value
        if (next === 'live' && !confirm('Switch to live mode? Real orders will execute against real funds.')) return
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
// All three elements MUST be present (spec LU-S03):
//   (1) "System Operator 将完全自主执行交易，操作不可预测"
//   (2) "真实资金损失可能发生"
//   (3) "开启后所有操作由人类承担后果"
function LiveUnlockWarningWall({ credError, onConfirm, onCancel }: {
  credError?: string
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div style={{ border: '2px solid var(--color-danger, #d9534f)', borderRadius: 6, padding: '16px', marginTop: 8, background: 'var(--color-danger-bg, #fff5f5)' }}>
      <p style={{ fontWeight: 700, marginBottom: 8 }}>实盘解锁警示 — 请仔细阅读</p>
      <ul style={{ margin: '0 0 12px 18px', lineHeight: 1.7 }}>
        <li>System Operator 将完全自主执行交易，操作不可预测</li>
        <li>真实资金损失可能发生</li>
        <li>开启后所有操作由人类承担后果</li>
      </ul>
      {credError && (
        <p style={{ color: 'var(--color-danger, #d9534f)', marginBottom: 10 }}>
          ⚠ 凭证预检失败：{credError}。建议先修复凭证再解锁。
        </p>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={onConfirm}>确认解锁实盘</button>
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
      <Row label="Live trading state" hint="Disabled by default. Unlocking runs a credential pre-flight, requires confirmation, and is audited. Use Kill Switch on Risk & Controls to halt immediately." danger={!isDisabled}>
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
            When ON, sizing reads the active persona's bounds instead of the flat fields above.
            P1 uses each param's <code>init</code>; the System Operator picks values within range in
            later phases. Default OFF = legacy sizing (zero change). Risk-engine hard caps always bound
            the final size regardless of persona.
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
                P4: unfreezes the persona's ladder params one at a time as clean closes accrue;
                account distress (drawdown / kill switch) refreezes ALL. Default OFF = static decidable set.
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
                  label="Auto-advance rollout (跨会话:每 8h 健康门控推进一步)"
                  onChange={v => { setPersonaAutoAdvance(v); setMsg(null) }}
                />
                <p className="hint mt-1">
                  P4 ops: the <code>persona_rollout</code> scheduler job (in the gateway, watchdog-backed →
                  cross-session/reboot) advances the ladder one health-gated step every 8h
                  (progressive_unlock → learning). Holds on distress/thrash. NEVER auto-touches leverage /
                  roll / pyramid live orders. Default OFF.
                </p>
              </div>
            </div>
            <div className="mt-2">
              <Toggle
                checked={personaPyramidEnabled}
                label="Pyramid add-on (滚仓:盈利加仓,roll only)"
                onChange={v => { setPersonaPyramidEnabled(v); setMsg(null) }}
              />
              <p className="hint mt-1">
                P4: scale into winners up to the persona's max_adds, each add = add_fraction of base,
                HARD-CAPPED by per-symbol exposure. Decision layer only; live scale-in order placement
                is a separate owner-gated step. Default OFF.
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
      <Collapsible title="Market-Aware Exits">
        <div className="settings-inline-note">
          <Toggle
            checked={maeEnabled}
            label="Market-aware exits (urgent exit / tighten stop / confidence collapse)"
            onChange={v => { setMaeEnabled(v); setMsg(null) }}
          />
          <p className="hint mt-1">
            When ON, position_manager evaluates market signals and may force-close losing positions early,
            tighten stops on rising risk, or exit when System Operator confidence drops below threshold.
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
// RISK & SYMBOLS TAB — allowed symbols, risk limits
// ─────────────────────────────────────────────────────────────────────────────

function RiskTab({ settings, qc, onDirty }: any) {
  return (
    <div>
      <Section
        id="universe"
        title="Universe Board"
        description="Controls the dynamic candidate selector shown on Universe Board. When disabled, agents use the static fallback list instead."
      >
        <UniverseConfigCard settings={settings} qc={qc} />
      </Section>

      <Section
        title="Strategy Factory"
        description="Controls how fast the system generates, validates, and eliminates trading strategies. Higher velocity = faster edge discovery."
      >
        <Collapsible title="Strategy Factory Parameters">
          <StrategyFactoryCard settings={settings} qc={qc} />
        </Collapsible>
      </Section>

      <Section
        title="Event Triggers"
        description="Real-time price/volume breakout detection. Fires notifications and optionally triggers strategy design when thresholds are breached."
      >
        <Collapsible title="Trigger Thresholds">
          <EventTriggerCard settings={settings} qc={qc} />
        </Collapsible>
      </Section>

      <Section
        title="Tradable Symbols"
        description="Coins agents are allowed to analyse and trade. Applied to both the risk engine and exchange config atomically."
      >
        <SymbolsCard settings={settings} qc={qc} />
      </Section>

      <Section
        title="Risk Limits"
        description="Hard limits enforced by the Risk Engine on every order evaluation. Changes are audited and take effect on the next agent cycle."
      >
        <RiskCard settings={settings} qc={qc} />
      </Section>
    </div>
  )
}

// ─── Prediction Markets Toggle ───

function PredictionToggleCard({ qc }: { qc: any }) {
  const { data: status } = useQuery({
    queryKey: ['prediction-status'],
    queryFn: api.getPredictionStatus,
  })
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const mut = useMutation({
    mutationFn: (enabled: boolean) => api.togglePrediction(enabled),
    onSuccess: (data) => {
      setMsg({ ok: true, text: data.enabled ? '✓ enabled' : '✓ disabled' })
      qc.invalidateQueries({ queryKey: ['prediction-status'] })
      qc.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const enabled = status?.enabled ?? false

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <Row label="Prediction observer" hint="When ON, the scheduler fetches Polymarket data every 30 min. When OFF, all prediction-related agent work stops.">
        <Toggle checked={enabled} onChange={v => mut.mutate(v)} disabled={mut.isPending} />
      </Row>
      <SaveMsg msg={msg} />
    </div>
  )
}

// ─── Equities ───

function EquitiesCard({ settings, qc }: any) {
  const serverTickers: string[] = settings?.market_data?.observed_equities ?? []
  const [draft, setDraft] = useState<string[]>(serverTickers)
  const [input, setInput] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => { setDraft(serverTickers) }, [settings])

  const mut = useMutation({
    mutationFn: () => api.updateEquities(draft),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const add = () => {
    const t = input.trim().toUpperCase()
    if (!t || draft.includes(t)) return
    setDraft(prev => [...prev, t]); setInput(''); setMsg(null)
  }
  const remove = (t: string) => { setDraft(prev => prev.filter(x => x !== t)); setMsg(null) }

  const dirty = JSON.stringify(draft.slice().sort()) !== JSON.stringify(serverTickers.slice().sort())

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div className="flex flex-wrap gap-1 mb-3" style={{ minHeight: '2rem' }}>
        {draft.map(t => (
          <span key={t} className="symbol-chip allow">
            {t}
            <button onClick={() => remove(t)} title={`Remove ${t}`}>×</button>
          </span>
        ))}
        {draft.length === 0 && (
          <span className="hint" style={{ margin: 0 }}>No tickers — equity_observer will not run.</span>
        )}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <input
          value={input} placeholder="e.g. AAPL"
          onChange={e => setInput(e.target.value.toUpperCase())}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
          style={{ width: '120px', fontFamily: 'var(--mono)', textTransform: 'uppercase' }}
        />
        <button className="secondary" onClick={add}>Add</button>
        {dirty && (
          <button onClick={() => mut.mutate()} disabled={mut.isPending}>
            {mut.isPending ? 'Saving…' : 'Save changes'}
          </button>
        )}
      </div>
      <SaveMsg msg={msg} />
      <p className="hint">
        US equity tickers observed by equity_observer (yfinance daily bars + Bitget xStock price deviation).
        Observation-only — not traded. Changes take effect on next agent cycle (Mon–Fri market hours).
      </p>
    </div>
  )
}

// ─── Symbols ───

function SymbolsCard({ settings, qc }: any) {
  const serverMode: string = settings?.risk?.symbol_filter_mode ?? 'blacklist'
  const serverSymbols: string[] = settings?.risk?.symbol_list ?? []

  const [mode, setMode] = useState<'whitelist' | 'blacklist'>(serverMode as any)
  const [draft, setDraft] = useState<string[]>([])
  const [input, setInput] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    setMode(serverMode as any)
    setDraft(serverSymbols)
  }, [settings])

  const mut = useMutation({
    mutationFn: () => api.updateSymbolFilter({ mode, symbols: draft }),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const add = () => {
    const s = input.trim().toUpperCase()
    if (!s || draft.includes(s)) return
    setDraft(prev => [...prev, s]); setInput(''); setMsg(null)
  }
  const remove = (s: string) => { setDraft(prev => prev.filter(x => x !== s)); setMsg(null) }

  const dirty = mode !== serverMode
    || JSON.stringify(draft.slice().sort()) !== JSON.stringify(serverSymbols.slice().sort())

  const canSave = mode === 'blacklist' || draft.length > 0

  const chipTone = mode === 'whitelist' ? 'allow' : 'deny'

  return (
    <div className="card mb-0">
      {/* Mode toggle */}
      <div className="flex items-center gap-2 mb-3">
        <span className="text-sm text-muted">Mode:</span>
        <button className={`btn-sm ${mode === 'whitelist' ? '' : 'secondary'}`} onClick={() => { setMode('whitelist'); setMsg(null) }}>Whitelist</button>
        <button className={`btn-sm ${mode === 'blacklist' ? '' : 'secondary'}`} onClick={() => { setMode('blacklist'); setMsg(null) }}>Blacklist</button>
      </div>

      {/* Symbol chips */}
      <div className="flex flex-wrap gap-1 mb-3" style={{ minHeight: '2rem' }}>
        {draft.map(s => (
          <span key={s} className={`symbol-chip ${chipTone}`}>
            {s}
            <button onClick={() => remove(s)} title={`Remove ${s}`}>×</button>
          </span>
        ))}
        {draft.length === 0 && (
          <span className="hint m-0">
            {mode === 'whitelist' ? 'No symbols — add at least one.' : 'Empty — all symbols are tradable.'}
          </span>
        )}
      </div>

      {/* Input + save */}
      <div className="flex items-center gap-2 flex-wrap">
        <input
          value={input} placeholder="e.g. SUIUSDT"
          onChange={e => setInput(e.target.value.toUpperCase())}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
          style={{ width: '140px', fontFamily: 'var(--mono)', textTransform: 'uppercase' }}
        />
        <button className="secondary" onClick={add}>Add</button>
        {dirty && (
          <button onClick={() => mut.mutate()} disabled={mut.isPending || !canSave}>
            {mut.isPending ? 'Saving…' : 'Save changes'}
          </button>
        )}
      </div>
      {mode === 'whitelist' && draft.length === 0 && dirty && (
        <div className="settings-save-msg err" style={{ marginTop: 'var(--space-1)' }}>Add at least one symbol to save in whitelist mode</div>
      )}
      <SaveMsg msg={msg} />
      <p className="hint">
        {mode === 'whitelist'
          ? 'Only these symbols can be traded. Add any Bitget pair (e.g. SOLUSDT, SUIUSDT).'
          : 'These symbols are blocked from trading. All other symbols are allowed.'}
        {' '}Changes take effect on the next agent cycle.
      </p>

      {/* Read-only: show the derived InstrumentId keys that will be observed */}
      {mode === 'whitelist' && (settings?.market_data?.observed_instruments ?? []).length > 0 && (
        <div style={{ marginTop: 'var(--space-3)', paddingTop: 'var(--space-2)', borderTop: '1px solid var(--border)' }}>
          <span className="text-xs text-muted mb-1" style={{ display: 'block' }}>
            Observed instruments (auto-derived · read-only)
          </span>
          <div className="flex flex-wrap gap-1">
            {(settings.market_data.observed_instruments as string[]).map((id: string) => {
              const [exch, sym, mtype] = id.split(':')
              return (
                <span key={id} className="instrument-chip" title={id}>
                  <span className="exch">{exch}</span>
                  {' '}{sym}{' '}
                  <span className="mtype">{mtype}</span>
                </span>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Risk limits ───

const RISK_LIVE: NumField[] = [
  { key: 'max_daily_loss_pct',            label: 'Max daily loss',          unit: '%',    min: 0.1,  max: 20,     step: 0.1 },
  { key: 'max_single_trade_risk_pct',     label: 'Max single trade risk',   unit: '%',    min: 0.01, max: 10,     step: 0.01 },
  { key: 'max_total_live_exposure_pct',   label: 'Max total exposure',      unit: '%',    min: 1,    max: 50,     step: 0.5 },
  { key: 'starting_capital_usdt',         label: 'Starting capital',        unit: 'USDT', min: 1,    max: 100000, step: 1 },
  { key: 'max_live_limited_cap_usdt',     label: 'Live cap',                unit: 'USDT', min: 1,    max: 100000, step: 1 },
  { key: 'max_live_position_notional_usdt', label: 'Position hard ceiling', unit: 'USDT', min: 1,    max: 100000, step: 1 },
  { key: 'max_futures_leverage_live',     label: 'Max leverage',            unit: 'x',    min: 1,    max: 20,     step: 1, isInt: true },
  { key: 'max_open_positions_live',       label: 'Max open positions',      unit: '',     min: 1,    max: 50,     step: 1, isInt: true },
]

const RISK_DEMO: NumField[] = [
  { key: 'max_demo_position_notional_usdt', label: 'Max position notional', unit: 'USDT', min: 1,    max: 100000, step: 1 },
  { key: 'max_demo_single_trade_risk_pct',  label: 'Max single trade risk', unit: '%',    min: 0.01, max: 20,     step: 0.01 },
  { key: 'max_futures_leverage_demo',       label: 'Max leverage',          unit: 'x',    min: 1,    max: 125,    step: 1, isInt: true },
  { key: 'max_open_positions_demo',         label: 'Max open positions',    unit: '',     min: 1,    max: 200,    step: 1, isInt: true },
]

// Cross-mode guards (apply to both demo and live). Always visible.
const RISK_SHARED: NumField[] = [
  { key: 'max_per_symbol_notional_usdt', label: 'Max per-symbol notional (cumulative)', unit: 'USDT', min: 0, max: 100000, step: 10 },
]

const RISK_BOOLS: BoolField[] = [
  { key: 'kill_switch_enabled_live', label: 'Kill switch auto-trigger (live)' },
  { key: 'kill_switch_enabled_demo', label: 'Kill switch auto-trigger (demo)' },
  { key: 'allow_short_live', label: 'Allow short selling (live)' },
  { key: 'allow_short_demo', label: 'Allow short selling (demo)' },
  { key: 'correlation_guard_enabled', label: 'Correlation guard (sector + beta)' },
]

const RISK_CORR: NumField[] = [
  { key: 'max_sector_concentration_pct', label: 'Max sector concentration', unit: '%',  min: 10, max: 100, step: 5 },
  { key: 'max_portfolio_beta',           label: 'Max portfolio BTC beta',   unit: '',   min: 0.5, max: 10, step: 0.5 },
]

// Scanner-family live-eligibility gate (risk check #14). A LIVE trade needs its
// scanner_family (in the proposal's direction) to show a clean-window edge.
const RISK_FAMILY_GATE: NumField[] = [
  { key: 'live_family_min_samples',      label: 'Live family min samples',  unit: '',  min: 1, max: 500, step: 1, isInt: true },
  { key: 'live_family_min_win_rate_pct', label: 'Live family min win rate', unit: '%', min: 0, max: 100, step: 1 },
]

const RISK_FUTURES_LIVE: NumField[] = [
  { key: 'max_leverage_major_live', label: 'Major (BTC/ETH)',    unit: 'x', min: 1, max: 10, step: 1, isInt: true },
  { key: 'max_leverage_mid_live',   label: 'Mid-cap',            unit: 'x', min: 1, max: 5,  step: 1, isInt: true },
  { key: 'max_leverage_alt_live',   label: 'Alt/Small',          unit: 'x', min: 1, max: 3,  step: 1, isInt: true },
  { key: 'max_account_leverage_live', label: 'Account leverage cap', unit: 'x', min: 1, max: 5, step: 0.1 },
]

const RISK_FUTURES_DEMO: NumField[] = [
  { key: 'max_leverage_major_demo', label: 'Major (BTC/ETH)',    unit: 'x', min: 1, max: 20, step: 1, isInt: true },
  { key: 'max_leverage_mid_demo',   label: 'Mid-cap',            unit: 'x', min: 1, max: 10, step: 1, isInt: true },
  { key: 'max_leverage_alt_demo',   label: 'Alt/Small',          unit: 'x', min: 1, max: 5,  step: 1, isInt: true },
  { key: 'max_account_leverage_demo', label: 'Account leverage cap', unit: 'x', min: 1, max: 10, step: 0.1 },
]

const RISK_MARGIN: NumField[] = [
  { key: 'futures_margin_warning_ratio',     label: 'Margin warning at',       unit: '', min: 0.3, max: 0.95, step: 0.05 },
  { key: 'futures_margin_force_close_ratio', label: 'Margin block at',         unit: '', min: 0.5, max: 0.99, step: 0.05 },
  { key: 'min_liquidation_buffer_pct',       label: 'Liquidation buffer min',  unit: '%', min: 1, max: 20, step: 0.5 },
]

function RiskCard({ settings, qc }: any) {
  const risk = settings?.risk as any
  const [vals, setVals] = useState<Record<string, any>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!risk) return
    const init: Record<string, any> = {}
    ;[...RISK_LIVE, ...RISK_DEMO, ...RISK_SHARED, ...RISK_CORR, ...RISK_FAMILY_GATE, ...RISK_FUTURES_LIVE, ...RISK_FUTURES_DEMO, ...RISK_MARGIN].forEach(f => { init[f.key] = risk[f.key] })
    RISK_BOOLS.forEach(f => { init[f.key] = risk[f.key] })
    setVals(init)
  }, [settings])

  const set = (k: string, v: any) => { setVals(prev => ({ ...prev, [k]: v })); setMsg(null); setErrors({}) }
  const [errors, setErrors] = useState<Record<string, string>>({})

  const allNumFields = [...RISK_LIVE, ...RISK_DEMO, ...RISK_SHARED, ...RISK_CORR, ...RISK_FAMILY_GATE, ...RISK_FUTURES_LIVE, ...RISK_FUTURES_DEMO, ...RISK_MARGIN]

  const validateAndSave = () => {
    const errs: Record<string, string> = {}
    for (const f of allNumFields) {
      const v = vals[f.key]
      if (v == null || v === '') continue
      if (typeof v === 'number') {
        if (v < f.min) errs[f.key] = `Min ${f.min}`
        else if (v > f.max) errs[f.key] = `Max ${f.max}`
      }
    }
    if (Object.keys(errs).length) {
      setErrors(errs)
      setMsg({ ok: false, text: `✗ ${Object.keys(errs).length} field(s) out of range` })
      return
    }
    setErrors({})
    mut.mutate()
  }

  const mut = useMutation({
    mutationFn: () => api.updateRiskConfig(vals as any),
    onSuccess: () => {
      setMsg({ ok: true, text: '✓ saved' })
      qc.invalidateQueries({ queryKey: ['settings'] })
      qc.invalidateQueries({ queryKey: ['risk-boundaries'] })
    },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  if (!risk) return null

  return (
    <div className="card mb-0">
      {/* Immutable fields notice */}
      <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', padding: '0.45rem 0.75rem', background: 'var(--bg)', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', marginBottom: '1.25rem' }}>
        <span className="text-xs text-muted">
          allowed_exchange: <span className="mono">{risk.allowed_exchange}</span>
        </span>
        <span className="text-xs text-muted">
          require_risk_check: <span className={`badge ${risk.require_risk_check_for_all_orders ? 'badge-green' : 'badge-red'}`}>{humanizeBool(risk.require_risk_check_for_all_orders)}</span>
          <span className="text-xs" style={{ marginLeft: 'var(--space-1)' }}>locked</span>
        </span>
      </div>

      {/* Live limits */}
      <div className="section-label">Live limits</div>
      <NumGrid fields={RISK_LIVE} vals={vals} set={set} errors={errors} />
      <p className="hint">Exchange-backed orders use the lower of this ceiling and the live account's cached available USDT. If no balance snapshot is available, this ceiling is used.</p>

      {/* Demo limits */}
      <div className="section-label mt-4">Demo / paper limits</div>
      <NumGrid fields={RISK_DEMO} vals={vals} set={set} errors={errors} />
      <p className="hint">Demo account orders use the lower of this ceiling and the demo account's cached available USDT. Local paper simulation has no external balance and uses this ceiling directly.</p>

      {/* Cross-mode guards (both demo and live) */}
      <div className="section-label mt-4">Per-symbol exposure (demo + live)</div>
      <NumGrid fields={RISK_SHARED} vals={vals} set={set} errors={errors} />

      {/* Scanner-family live-eligibility gate (risk check #14) */}
      <div className="section-label mt-4">Live scanner-family evidence gate (#14)</div>
      <NumGrid fields={RISK_FAMILY_GATE} vals={vals} set={set} errors={errors} />

      <ToggleGrid fields={RISK_BOOLS} vals={vals} set={set} />

      {/* Correlation guard thresholds — only shown when guard is enabled */}
      {vals.correlation_guard_enabled && (
        <>
          <div className="section-label mt-4">Correlation guard thresholds</div>
          <NumGrid fields={RISK_CORR} vals={vals} set={set} errors={errors} />
        </>
      )}

      {/* Futures / leverage tier limits — shown when usdt_futures is enabled */}
      {(settings?.exchange as any)?.enabled_markets?.usdt_futures && (
        <>
          <div className="section-label mt-4">Futures leverage — Live (per-tier)</div>
          <NumGrid fields={RISK_FUTURES_LIVE} vals={vals} set={set} errors={errors} />

          <div className="section-label mt-4">Futures leverage — Demo (per-tier)</div>
          <NumGrid fields={RISK_FUTURES_DEMO} vals={vals} set={set} errors={errors} />

          <div className="section-label mt-4">Margin safety thresholds</div>
          <NumGrid fields={RISK_MARGIN} vals={vals} set={set} errors={errors} />
        </>
      )}

      {/* Kill switch trigger conditions — always visible for transparency */}
      <div className="kill-switch-info">
        <div className="kill-switch-info-title">Kill Switch Auto-Trigger Conditions</div>
        <ul>
          {/* #39 fix: the trigger formula is MODE-DEPENDENT (risk/engine.py:209-216
              — CR-RE-03). Live trades use `max_live_limited_cap_usdt × pct`;
              demo trades use `starting_capital_usdt × pct` (the demo base — using
              the tiny live cap spuriously tripped demo mode on any small loss).
              The old UI only showed the live formula, so operators reading it for
              a demo account saw a threshold ~1/1000th of the actual one. */}
          <li><strong>Trigger formula (live):</strong> daily realized PnL &le; &minus;(Live Cap &times; Max Daily Loss %)</li>
          <li><strong>Live threshold:</strong> &minus;{fmtNum((vals.max_live_limited_cap_usdt || 0) * (vals.max_daily_loss_pct || 0) / 100)} USDT</li>
          <li><strong>Trigger formula (demo):</strong> daily realized PnL &le; &minus;(Starting Capital &times; Max Daily Loss %)</li>
          <li><strong>Demo threshold:</strong> &minus;{fmtNum((vals.starting_capital_usdt || 0) * (vals.max_daily_loss_pct || 0) / 100)} USDT</li>
          <li><strong>Scope:</strong> applies to live trades when "Kill switch auto-trigger (live)" is ON; applies to demo trades when "Kill switch auto-trigger (demo)" is ON</li>
          <li><strong>Effect:</strong> once tripped, ALL proposals (live + demo) are rejected until manually released</li>
          <li><strong>Reset:</strong> release from Risk Controls panel or CLI after UTC new day</li>
        </ul>
      </div>

      <div className="settings-action-row">
        <button onClick={validateAndSave} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save risk limits'}</button>
      </div>
      <SaveMsg msg={msg} />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// HEALTH & ALERTS TAB — health, self-heal, startup gate, notification delivery
// ─────────────────────────────────────────────────────────────────────────────

function HealthNotificationsTab({ settings, qc }: any) {
  return (
    <div>
      <Section title="Health Monitoring" description="Thresholds that determine when the system raises internal health alerts.">
        <HealthCard settings={settings} qc={qc} />
      </Section>

      <Section title="Telegram" description="Alert delivery when agents act or limits are breached. Token is stored in secrets (write-only via CLI).">
        <TelegramCard settings={settings} qc={qc} />
      </Section>

      <Section title="Webhook" description="Push JSON events to an external URL (Slack, Discord, n8n, etc). Payloads are HMAC-signed when a secret is set.">
        <WebhookCard settings={settings} qc={qc} />
      </Section>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// SIGNALS TAB — market data, news, macro signals, prediction markets, equities
// ─────────────────────────────────────────────────────────────────────────────

function SignalsTab({ settings, qc, onDirty }: any) {
  return (
    <div>
      <Section
        title="Market Data Sources"
        description="Override primary exchange data routing. Normally managed by the Primary Exchange selector in Execution."
      >
        <Collapsible title="Data Source Overrides">
          <MarketDataCard settings={settings} qc={qc} onDirty={onDirty} />
        </Collapsible>
      </Section>

      <Section
        title="Macro & Sentiment Signals"
        description="Whole-market context fed into the LLM enriched_context: Fear & Greed Index (Alternative.me) and CoinGecko top movers. All free, no API key required."
      >
        <MacroSignalsCard settings={settings} qc={qc} onDirty={onDirty} />
      </Section>

      <Section
        title="News Feeds"
        description="Automated RSS news ingestion. Feeds are fetched periodically and displayed on the News Intelligence page."
      >
        <NewsFeedsCard settings={settings} qc={qc} />
      </Section>

      <Section
        title="Prediction Markets"
        description="Polymarket observer — fetches probability data for information-edge signals. Observation-only, never routed to execution."
      >
        <PredictionToggleCard qc={qc} />
      </Section>

      <Section
        title="Observed US Equities"
        description="US equity tickers for equity_observer (observation-only, not traded). Requires Bitget xStock support for the ticker."
      >
        <EquitiesCard settings={settings} qc={qc} />
      </Section>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// RUNTIME & SYSTEM TAB — display, app, retention, updates, network
// ─────────────────────────────────────────────────────────────────────────────

function RuntimeSystemTab({ settings, qc, onDirty }: any) {
  return (
    <div>
      <Section title="Display & App" description="Visual formatting and dashboard process settings.">
        <DisplayRow />
        <AppRows settings={settings} qc={qc} />
      </Section>

      <Section title="Data & Retention" description="Local OHLCV storage for backtesting and telemetry retention policy.">
        <Collapsible title="Historical Data">
          <p className="hint mb-2">Controls local OHLCV bulk storage for backtesting. Nightly backfill downloads missing candles so backtests run entirely offline.</p>
          <Collapsible title="Backfill & Storage Parameters">
            <HistoricalDataCard settings={settings} qc={qc} />
          </Collapsible>
        </Collapsible>
        <Collapsible title="Data Retention">
          <p className="hint mb-2">How long telemetry is kept before automatic pruning. Trust-bearing records (trades, audit log) are never pruned.</p>
          <RetentionCard settings={settings} qc={qc} />
        </Collapsible>
      </Section>

      <Section title="Software Updates" description="On-demand self-upgrade via git pull. Never runs automatically.">
        <UpdateCard settings={settings} qc={qc} />
      </Section>

      <Section title="Network Proxy" description="Route all outbound traffic (exchange, market data, news, LLM) through a managed proxy. Set the protocol/host/port, and enable Enforce to require the proxy (fail-closed) so a proxy outage never leaks your real IP.">
        <NetworkCard settings={settings} qc={qc} />
      </Section>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// ADVANCED TAB — read-only coverage and diagnostics
// ─────────────────────────────────────────────────────────────────────────────

function AdvancedTab({ settings }: { settings: any }) {
  return (
    <div>
      <Section title="Configuration Matrix" description="Read-only coverage map for the sanitized local config. If a key exists in config.yaml but has no dedicated control above, it is still visible here.">
        <ConfigCoverageCard settings={settings} />
      </Section>
    </div>
  )
}

function DisplayRow() {
  const decimals = useDecimals()
  return (
    <Row label="Decimal places" hint="Applied to all monetary and continuous values. Applies immediately.">
      <input
        type="number" min={DECIMAL_BOUNDS.min} max={DECIMAL_BOUNDS.max} value={decimals}
        onChange={e => setDecimals(Number(e.target.value))}
        className="decimals-input" style={{ width: '70px' }}
      />
      <span className="hint mono m-0">e.g. {fmtNum(1234.5, { decimals })} · {fmtNum(0, { decimals })}</span>
    </Row>
  )
}

function AppRows({ settings, qc }: any) {
  const app = settings?.app as any || {}
  const runtime = settings?.runtime as any || {}
  const autoBrowser = app.auto_open_browser !== false

  const appMut = useMutation({
    mutationFn: (body: any) => api.updateAppConfig(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })
  const runtimeMut = useMutation({
    mutationFn: (body: any) => api.updateRuntimeConfig(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })
  const runtimePending = useRef(false)
  const safeRuntimeMut = (body: any) => {
    if (runtimePending.current) return
    runtimePending.current = true
    runtimeMut.mutate(body, { onSettled: () => { runtimePending.current = false } })
  }

  const gitEnabled = !!runtime.git_enabled

  return (
    <>
      <Row label="App name" hint="Read-only product identity from config.yaml.">
        <span className="mono text-sm">{app.name || 'Aletheia Trading OS'}</span>
      </Row>
      <Row label="Dashboard port" hint={`Currently listening on :${app.dashboard_port || 8787}. Change requires gateway restart.`}>
        <span className="mono text-sm">{app.dashboard_port || 8787}</span>
        <span className="text-xs text-muted">(read-only — set via config.yaml)</span>
      </Row>
      <Row label="Bind host" hint="Loopback is safest. Non-loopback hosts expose the local API without multi-user auth.">
        <span className="mono text-sm">{app.bind_host || '127.0.0.1'}</span>
        <span className="text-xs text-muted">(read-only — set via config.yaml)</span>
      </Row>
      <Row label="Gateway mode" hint="Process lifecycle mode used by the CLI/supervisor.">
        <span className="mono text-sm">{runtime.gateway_mode || 'background'}</span>
      </Row>
      <Row label="Auto-open browser" hint="Open the dashboard in a browser tab automatically when the gateway starts.">
        <Toggle checked={autoBrowser} onChange={v => appMut.mutate({ auto_open_browser: v })} disabled={appMut.isPending} />
      </Row>
      <Row label="Cumulative PnL mode" hint="Full cumulative: every range shows the same all-time curve, the end point never changes. Window relative: each range re-accumulates from 0, so 7D shows only what was earned in the last 7 days.">
        <select
          value={app.equity_curve_mode || 'full_cumulative'}
          onChange={e => appMut.mutate({ equity_curve_mode: e.target.value })}
          disabled={appMut.isPending}
          className="w-auto"
        >
          <option value="full_cumulative">Full cumulative (end point fixed)</option>
          <option value="window_relative">Window relative (per-period PnL)</option>
        </select>
      </Row>
      <Row label="Git versioning of data" hint="When enabled, knowledge, playbooks, and reports are committed to git automatically for history and rollback.">
        <Toggle checked={gitEnabled} onChange={v => {
          if (!v && !confirm('Disable git versioning? Knowledge, playbooks, and reports will no longer be committed.')) return
          safeRuntimeMut({ git_enabled: v })
        }} disabled={runtimeMut.isPending} />
      </Row>
      <Row label="Auto-reload Python changes" hint="Development convenience for hot-reloading backend code. Turn off before a controlled live run.">
        <Toggle checked={runtime.auto_reload !== false} onChange={v => {
          if (v && !confirm('Enable auto-reload? Code changes will hot-reload mid-run. Disable before controlled live sessions.')) return
          safeRuntimeMut({ auto_reload: v })
        }} disabled={runtimeMut.isPending} />
      </Row>
      <Row label="Runtime continuity" hint="Experimental: enables shadow gateway staging for zero-disruption upgrades. When off, updates use legacy restart.">
        <Toggle checked={!!runtime.runtime_continuity_enabled} onChange={v => safeRuntimeMut({ runtime_continuity_enabled: v })} disabled={runtimeMut.isPending} />
      </Row>
      {!!runtime.runtime_continuity_enabled && (
        <>
          <Row label="Promote lock TTL" hint="Seconds the exclusive promote/rollback lock is held. Prevents concurrent transitions.">
            <input type="number" min={30} max={3600} value={runtime.runtime_continuity_lock_ttl_sec ?? 300}
              onChange={e => safeRuntimeMut({ runtime_continuity_lock_ttl_sec: Math.max(30, Math.min(3600, parseInt(e.target.value) || 300)) })}
              disabled={runtimeMut.isPending} style={{ width: '80px' }} />
            <span className="hint m-0">sec</span>
          </Row>
          <Row label="Shadow port offset" hint="Shadow gateway listens on dashboard_port + this offset (e.g. 8787 + 100 = 8887).">
            <input type="number" min={1} max={10000} value={runtime.runtime_continuity_shadow_port_offset ?? 100}
              onChange={e => safeRuntimeMut({ runtime_continuity_shadow_port_offset: Math.max(1, Math.min(10000, parseInt(e.target.value) || 100)) })}
              disabled={runtimeMut.isPending} style={{ width: '80px' }} />
          </Row>
        </>
      )}
    </>
  )
}

function ConfigCoverageCard({ settings }: { settings: any }) {
  const controlled = new Set([
    'agent.permission_mode', 'agent.autonomous_learning', 'agent.bypass_distress_gate',
    'learning.contribution_evaluation_enabled', 'learning.lifecycle_automation_enabled',
    'learning.paper_influence_enabled', 'learning.paper_influence_paths',
    'learning.auto_refreeze', 'learning.evaluation_interval_minutes',
    'learning.paper_active_allocation_pct',
    'runtime.scheduler_enabled', 'runtime.git_enabled', 'runtime.auto_reload',
    'runtime.runtime_continuity_enabled', 'runtime.runtime_continuity_lock_ttl_sec', 'runtime.runtime_continuity_shadow_port_offset',
    'app.auto_open_browser',
    'exchange.mode', 'exchange.enabled_markets.spot', 'exchange.enabled_markets.usdt_futures', 'exchange.paper_execution_mode',
    'live.state',
    'risk.symbol_filter_mode', 'risk.symbol_list',
    ...RISK_LIVE.map(f => `risk.${f.key}`),
    ...RISK_DEMO.map(f => `risk.${f.key}`),
    ...RISK_SHARED.map(f => `risk.${f.key}`),
    ...RISK_BOOLS.map(f => `risk.${f.key}`),
    ...RISK_CORR.map(f => `risk.${f.key}`),
    ...RISK_FAMILY_GATE.map(f => `risk.${f.key}`),
    'trading.default_stop_loss_pct', 'trading.default_take_profit_pct', 'trading.max_position_hold_hours',
    'trading.slippage_bps', 'trading.execution_fee_pct', 'trading.min_confidence_to_trade', 'trading.auto_adopt_enabled',
    'trading.trail_enabled', 'trading.trail_atr_mult', 'trading.trail_pct', 'trading.breakeven_trigger_pct', 'trading.exchange_tpsl_enabled',
    'trading.default_notional_usdt', 'trading.vol_sizing_enabled', 'trading.vol_target_risk_usdt', 'trading.vol_target_pct', 'trading.vol_kelly_cap_fraction',
    'trading.persona_sizing_enabled', 'trading.active_persona', 'trading.personas',
    'trading.persona_learning_enabled', 'trading.persona_learning_min_samples',
    'trading.persona_progressive_unlock_enabled', 'trading.persona_unlock_step_samples',
    'trading.persona_pyramid_enabled', 'trading.persona_pyramid_add_trigger_pct',
    'trading.persona_auto_advance_enabled',
    'health.enabled', 'health.alert_via_telegram', 'health.alert_via_windows_toast', 'health.require_llm_at_startup',
    'health.self_heal_enabled', 'health.self_heal_code_drift', 'health.self_heal_market_data', 'health.self_heal_port_cleanup',
    'health.max_agent_failure_ratio', 'health.llm_unavailable_alert_after', 'health.market_data_max_staleness_min',
    'health.supervisor_max_restarts', 'health.supervisor_restart_window_sec', 'health.supervisor_backoff_cap_sec',
    'health.self_heal_min_interval_sec', 'health.self_heal_max_per_hour',
    'health.startup_gate_max_retries', 'health.startup_gate_retry_delay_sec',
    'retention.agent_runs_days', 'retention.tool_calls_days', 'retention.run_steps_days',
    'retention.market_snapshots_days', 'retention.portfolio_snapshots_days', 'retention.opportunity_ttl_hours', 'retention.jsonl_max_mb',
    'telegram.enabled', 'telegram.allowed_chat_ids',
    'telegram.notify_trade_fills', 'telegram.notify_daily_summary', 'telegram.daily_summary_hour_cst',
    'telegram.dnd_enabled', 'telegram.dnd_start_hour', 'telegram.dnd_end_hour',
    'update.enabled', 'update.branch',
    'universe.enabled', 'universe.refresh_interval_min', 'universe.max_candidates', 'universe.core_pool_size',
    'universe.min_volume_24h_usdt', 'universe.max_spread_bps', 'universe.min_depth_10bps_usd',
    'universe.explore_ratio', 'universe.blacklist', 'universe.fallback_symbols',
    'universe.market_type', 'universe.family_quota', 'universe.family_quota_by_regime',
    'tunnel.enabled', 'tunnel.tunnel_id', 'tunnel.hostname',
    'market_data.ccxt_exchange', 'market_data.ccxt_fallback', 'market_data.ohlcv_cache_ttl_min', 'market_data.enable_yfinance_macro',
    'market_data.observed_stocks', 'market_data.observed_stock_tokens', 'market_data.observed_prediction_markets',
    // These DO have UI controls — were missing from the set so the coverage matrix
    // falsely labelled them "config.yaml only" (CUI-3, honesty fix):
    'market_data.prediction_enabled',   // PredictionToggleCard
    'market_data.observed_equities',     // EquitiesCard
    'trading.market_aware_exits.enabled', 'trading.market_aware_exits.urgent_exit_score',
    'trading.market_aware_exits.tighten_stop_score', 'trading.market_aware_exits.confidence_collapse_score',
    'trading.market_aware_exits.tighten_atr_mult',  // TradingParamsCard
    'health.auto_resume_enabled', 'health.auto_resume_interval_sec',  // HealthCard (DO-1/CUI-4)
    'health.exchange_probe_interval_sec', 'health.exchange_probe_failure_threshold',
    'health.exchange_auto_resume_interval_sec', 'health.exchange_auto_resume_success_threshold',
    'health.startup_behavior_gate_enabled', 'health.startup_gate_recheck_interval_sec',  // Startup Behavior Gate
    'network.proxy_protocol', 'network.proxy_host', 'network.proxy_port',
    'network.enforce', 'network.proxy_url', 'network.no_proxy',
    'operator.loop_enabled',
    'guard.auto_promote_enabled',
  ])
  const readOnlyPrefixes = [
    'app.', 'runtime.paused', 'runtime.kill_switch_active',
    'update.last_', 'update.current_commit',
    'live.first_live_confirmation_done', 'tunnel.credentials_path', 'tunnel.cloudflared_path', 'tunnel.metrics_port',
  ]

  const rows = flattenConfig(settings)
    .filter(r => !r.key.includes('api_key') && !r.key.includes('oauth_token') && !r.key.includes('oauth_refresh'))
    .sort((a, b) => a.key.localeCompare(b.key))

  return (
    <div className="card mb-0">
      <p className="hint m-0">
        This matrix is intentionally read-only. It prevents hidden config drift; dedicated controls above remain the only editable surface for safety-sensitive settings.
      </p>
      <div style={{ maxHeight: '420px', overflow: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)' }}>
        <table>
          <thead>
            <tr>
              <th>Config key</th>
              <th>Value</th>
              <th>Setting surface</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.key}>
                <td className="mono">{row.key}</td>
                <td className="mono" style={{ maxWidth: '520px', wordBreak: 'break-word' }}>{formatConfigValue(row.value)}</td>
                <td>
                  {controlled.has(row.key)
                    ? <span className="badge badge-green">editable above</span>
                    : readOnlyPrefixes.some(prefix => row.key === prefix || row.key.startsWith(prefix))
                      ? <span className="badge badge-muted">read-only visible here</span>
                      : <span className="badge badge-muted">read-only / config.yaml</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── Health config ───

function HealthCard({ settings, qc }: any) {
  const health = settings?.health as any
  const [vals, setVals] = useState<Record<string, any>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!health) return
    setVals({ ...health })
  }, [settings])

  const set = (k: string, v: any) => { setVals(prev => ({ ...prev, [k]: v })); setMsg(null) }

  const mut = useMutation({
    mutationFn: () => api.updateHealthConfig(vals as any),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  if (!health) return null

  const HEALTH_FIELDS: NumField[] = [
    { key: 'max_agent_failure_ratio',      label: 'Max agent failure ratio',  unit: '',    min: 0,  max: 1,     step: 0.01 },
    { key: 'llm_unavailable_alert_after',  label: 'LLM alert after N failures', unit: '', min: 1,  max: 100,   step: 1, isInt: true },
    { key: 'market_data_max_staleness_min',label: 'Market data staleness',    unit: 'min', min: 5,  max: 1440,  step: 1, isInt: true },
    { key: 'supervisor_max_restarts',      label: 'Supervisor max restarts',  unit: '',    min: 1,  max: 50,    step: 1, isInt: true },
    { key: 'supervisor_restart_window_sec',label: 'Restart window',           unit: 'sec', min: 60, max: 86400, step: 60, isInt: true },
    { key: 'supervisor_backoff_cap_sec',   label: 'Backoff cap',              unit: 'sec', min: 5,  max: 3600,  step: 1, isInt: true },
    { key: 'self_heal_min_interval_sec',   label: 'Self-heal min interval',   unit: 'sec', min: 300,max: 86400, step: 60, isInt: true },
    { key: 'self_heal_max_per_hour',       label: 'Self-heal max per hour',   unit: '',    min: 0,  max: 12,    step: 1, isInt: true },
    { key: 'startup_gate_max_retries',     label: 'LLM gate retries',         unit: '',    min: 1,  max: 10,    step: 1, isInt: true },
    { key: 'startup_gate_retry_delay_sec', label: 'LLM gate retry delay',     unit: 'sec', min: 5,  max: 120,   step: 5, isInt: true },
    { key: 'auto_resume_interval_sec',     label: 'Auto-resume probe interval', unit: 'sec', min: 30, max: 3600,  step: 30, isInt: true },
    { key: 'exchange_probe_interval_sec',  label: 'Exchange probe interval',    unit: 'sec', min: 10, max: 300,   step: 10, isInt: true },
    { key: 'exchange_probe_failure_threshold', label: 'Exchange fail threshold', unit: '',    min: 1,  max: 20,    step: 1, isInt: true },
    { key: 'exchange_auto_resume_interval_sec', label: 'Exchange resume interval', unit: 'sec', min: 10, max: 600, step: 10, isInt: true },
    { key: 'exchange_auto_resume_success_threshold', label: 'Exchange resume successes', unit: '', min: 1, max: 10, step: 1, isInt: true },
    { key: 'startup_gate_recheck_interval_sec', label: 'Startup gate recheck interval', unit: 'sec', min: 5, max: 3600, step: 5, isInt: true },
  ]
  const HEALTH_TOGGLES: BoolField[] = [
    { key: 'enabled', label: 'Health monitoring' },
    { key: 'alert_via_telegram', label: 'Telegram alerts' },
    { key: 'alert_via_windows_toast', label: 'Windows toast alerts' },
    { key: 'require_llm_at_startup', label: 'Require LLM at startup' },
    { key: 'startup_behavior_gate_enabled', label: 'Startup behavior gate' },
    { key: 'self_heal_enabled', label: 'Self-heal enabled' },
    { key: 'self_heal_code_drift', label: 'Restart on code drift' },
    { key: 'self_heal_market_data', label: 'Restart on stale market data' },
    { key: 'self_heal_port_cleanup', label: 'Clean orphan port holders' },
    { key: 'auto_resume_enabled', label: 'Auto-resume after LLM gate' },
  ]

  return (
    <div className="card mb-0">
      <ToggleGrid fields={HEALTH_TOGGLES} vals={vals} set={set} />
      <NumGrid fields={HEALTH_FIELDS} vals={vals} set={set} />
      <div className="settings-action-row">
        <button onClick={() => mut.mutate()} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save'}</button>
      </div>
      <SaveMsg msg={msg} />
    </div>
  )
}

// ─── News Feeds config ───

function NewsFeedsCard({ settings, qc }: any) {
  const news = settings?.news as any
  const [enabled, setEnabled] = useState(true)
  const [interval, setInterval_] = useState(15)
  const [feeds, setFeeds] = useState<any[]>([])
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!news) return
    setEnabled(news.enabled ?? true)
    setInterval_(news.fetch_interval_minutes ?? 15)
    setFeeds(news.feeds ?? [])
  }, [settings])

  const mut = useMutation({
    mutationFn: () => api.updateNewsConfig({
      enabled,
      fetch_interval_minutes: interval,
      feeds: feeds.map((f: any) => ({ name: f.name, url: f.url, source: f.source, enabled: f.enabled })),
    }),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const toggleFeed = (idx: number) => {
    setFeeds(prev => prev.map((f, i) => i === idx ? { ...f, enabled: !f.enabled } : f))
    setMsg(null)
  }

  if (!news) return null

  return (
    <div className="card mb-0">
      <Row label="News ingestion" hint="Master switch for automatic RSS feed collection.">
        <Toggle checked={enabled} onChange={setEnabled} />
      </Row>
      <Row label="Fetch interval" hint="Minutes between feed checks (5–120).">
        <input type="number" min={5} max={120} step={5} value={interval}
          onChange={e => { setInterval_(Number(e.target.value)); setMsg(null) }}
          className="mono" style={{ width: '70px' }}
        />
      </Row>
      <div className="section-label mt-4">Feeds</div>
      <div className="news-feeds-grid">
        {feeds.map((f: any, i: number) => (
          <div key={f.url} className="news-feed-row">
            <Toggle checked={f.enabled} onChange={() => toggleFeed(i)} />
            <div className="news-feed-info">
              <span className="news-feed-name">{f.name}</span>
              <span className="news-feed-source text-muted text-xs">{f.source}</span>
            </div>
          </div>
        ))}
      </div>
      <div className="settings-action-row">
        <button onClick={() => mut.mutate()} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save news config'}</button>
      </div>
      <SaveMsg msg={msg} />
    </div>
  )
}

// ─── Retention config ───

function RetentionCard({ settings, qc }: any) {
  const retention = settings?.retention as any
  const [vals, setVals] = useState<Record<string, any>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!retention) return
    setVals({ ...retention })
  }, [settings])

  const set = (k: string, v: any) => { setVals(prev => ({ ...prev, [k]: v })); setMsg(null) }

  const mut = useMutation({
    mutationFn: () => api.updateRetentionConfig(vals as any),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  if (!retention) return null

  const RETENTION_FIELDS: NumField[] = [
    { key: 'agent_runs_days',          label: 'Agent runs',          unit: 'days', min: 1, max: 9999, step: 1, isInt: true },
    { key: 'tool_calls_days',          label: 'Tool calls',          unit: 'days', min: 1, max: 9999, step: 1, isInt: true },
    { key: 'run_steps_days',           label: 'Run steps',           unit: 'days', min: 1, max: 9999, step: 1, isInt: true },
    { key: 'market_snapshots_days',    label: 'Market snapshots',    unit: 'days', min: 1, max: 9999, step: 1, isInt: true },
    { key: 'portfolio_snapshots_days', label: 'Portfolio snapshots', unit: 'days', min: 1, max: 9999, step: 1, isInt: true },
    { key: 'opportunity_ttl_hours',    label: 'Opportunity TTL',     unit: 'hours', min: 1, max: 720, step: 1, isInt: true },
    { key: 'jsonl_max_mb',             label: 'JSONL max size',      unit: 'MB',   min: 1, max: 99999, step: 10, isInt: true },
  ]

  return (
    <div className="card mb-0">
      <NumGrid fields={RETENTION_FIELDS} vals={vals} set={set} />
      <div className="flex gap-2">
        <button onClick={() => mut.mutate()} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save'}</button>
      </div>
      <SaveMsg msg={msg} />
      <p className="hint">Trades, proposals, knowledge, and audit events are never pruned — only raw telemetry.</p>
    </div>
  )
}

// ─── Telegram ───

function TelegramCard({ settings, qc }: any) {
  const { data: health } = useQuery({
    queryKey: ['telegram-health'], queryFn: api.getTelegramHealth, refetchInterval: 30000,
  })
  const tg = settings?.telegram as any || {}
  const [enabled, setEnabled] = useState(false)
  const [chatIdsInput, setChatIdsInput] = useState('')
  const [notifyFills, setNotifyFills] = useState(true)
  const [notifySummary, setNotifySummary] = useState(true)
  const [summaryHour, setSummaryHour] = useState(8)
  const [dndEnabled, setDndEnabled] = useState(false)
  const [dndStart, setDndStart] = useState(23)
  const [dndEnd, setDndEnd] = useState(7)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!tg) return
    setEnabled(!!tg.enabled)
    setChatIdsInput((tg.allowed_chat_ids || []).join(', '))
    setNotifyFills(tg.notify_trade_fills !== false)
    setNotifySummary(tg.notify_daily_summary !== false)
    setSummaryHour(tg.daily_summary_hour_cst ?? 8)
    setDndEnabled(!!tg.dnd_enabled)
    setDndStart(tg.dnd_start_hour ?? 23)
    setDndEnd(tg.dnd_end_hour ?? 7)
  }, [settings])

  const mut = useMutation({
    mutationFn: () => {
      const ids = chatIdsInput.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n))
      return api.updateTelegramConfig({
        enabled, allowed_chat_ids: ids,
        notify_trade_fills: notifyFills,
        notify_daily_summary: notifySummary,
        daily_summary_hour_cst: summaryHour,
        dnd_enabled: dndEnabled,
        dnd_start_hour: dndStart,
        dnd_end_hour: dndEnd,
      })
    },
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  let state: ConnState = 'muted'
  let connText = 'not configured'
  if (health) {
    if (health.reachable) { state = 'green'; connText = health.username ? `@${health.username}` : 'reachable' }
    else if (health.error === 'not_configured') { state = 'muted'; connText = 'not configured' }
    else { state = 'red'; connText = `✗ ${health.error || 'unreachable'}` }
  }

  return (
    <div className="card mb-0">
      <div className="mb-3">
        <ConnectionStatus label="telegram bot" state={state} text={connText} />
      </div>
      <div className="form-grid mb-3">
        <label className="col-full">
          <span>Allowed chat IDs</span>
          <input
            value={chatIdsInput}
            placeholder="123456789, 987654321"
            onChange={e => { setChatIdsInput(e.target.value); setMsg(null) }}
            className="mono"
          />
        </label>
      </div>
      <div className="settings-action-row settings-action-row-start">
        <Toggle checked={enabled} label="Telegram delivery" onChange={v => { setEnabled(v); setMsg(null) }} />
      </div>
      <div className="settings-action-row settings-action-row-start">
        <Toggle checked={notifyFills} label="Trade fill notifications" onChange={v => { setNotifyFills(v); setMsg(null) }} />
      </div>
      <div className="settings-action-row settings-action-row-start">
        <Toggle checked={notifySummary} label="Daily PnL summary (08:00 CST)" onChange={v => { setNotifySummary(v); setMsg(null) }} />
      </div>
      {notifySummary && (
        <div className="form-grid mb-3">
          <label>
            <span>Summary hour (CST)</span>
            <input type="number" min={0} max={23} value={summaryHour}
              onChange={e => { setSummaryHour(parseInt(e.target.value) || 8); setMsg(null) }} />
          </label>
        </div>
      )}
      <div className="settings-action-row settings-action-row-start">
        <Toggle checked={dndEnabled} label="Do-not-disturb (suppress info alerts)" onChange={v => { setDndEnabled(v); setMsg(null) }} />
      </div>
      {dndEnabled && (
        <div className="form-grid mb-3">
          <label>
            <span>DND start hour</span>
            <input type="number" min={0} max={23} value={dndStart}
              onChange={e => { setDndStart(parseInt(e.target.value) || 23); setMsg(null) }} />
          </label>
          <label>
            <span>DND end hour</span>
            <input type="number" min={0} max={23} value={dndEnd}
              onChange={e => { setDndEnd(parseInt(e.target.value) || 7); setMsg(null) }} />
          </label>
        </div>
      )}
      <div className="settings-action-row">
        <button onClick={() => mut.mutate()} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save'}</button>
      </div>
      <SaveMsg msg={msg} />
      <p className="hint">
        Bot token is write-only — set via <code>aletheia secrets set telegram.bot_token</code>.
        Chat IDs are comma-separated Telegram user or group IDs.
      </p>
    </div>
  )
}

// ─── Webhook ───

function WebhookCard({ settings, qc }: any) {
  const wh = settings?.webhook as any || {}
  const [enabled, setEnabled] = useState(false)
  const [url, setUrl] = useState('')
  const [secret, setSecret] = useState('')
  const [notifyFills, setNotifyFills] = useState(true)
  const [notifyAlerts, setNotifyAlerts] = useState(true)
  const [notifySummary, setNotifySummary] = useState(true)
  const [timeoutSec, setTimeoutSec] = useState(10)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!wh) return
    setEnabled(!!wh.enabled)
    setUrl(wh.url || '')
    setNotifyFills(wh.notify_trade_fills !== false)
    setNotifyAlerts(wh.notify_alerts !== false)
    setNotifySummary(wh.notify_daily_summary !== false)
    setTimeoutSec(wh.timeout_sec ?? 10)
  }, [settings])

  const mut = useMutation({
    mutationFn: () => api.updateWebhookConfig({
      enabled, url, secret: secret || undefined,
      notify_trade_fills: notifyFills,
      notify_alerts: notifyAlerts,
      notify_daily_summary: notifySummary,
      timeout_sec: timeoutSec,
    }),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); setSecret(''); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  return (
    <div className="card mb-0">
      <div className="form-grid mb-3">
        <label className="col-full">
          <span>Webhook URL</span>
          <input
            value={url}
            placeholder="https://hooks.slack.com/services/..."
            onChange={e => { setUrl(e.target.value); setMsg(null) }}
            className="mono"
          />
        </label>
        <label className="col-full">
          <span>HMAC secret (write-only)</span>
          <input
            type="password"
            value={secret}
            placeholder={wh.url ? '••••••• (unchanged)' : 'optional'}
            onChange={e => { setSecret(e.target.value); setMsg(null) }}
            className="mono"
          />
        </label>
        <label>
          <span>Timeout (sec)</span>
          <input type="number" min={1} max={60} value={timeoutSec}
            onChange={e => { setTimeoutSec(parseFloat(e.target.value) || 10); setMsg(null) }} />
        </label>
      </div>
      <div className="settings-action-row settings-action-row-start">
        <Toggle checked={enabled} label="Webhook delivery" onChange={v => { setEnabled(v); setMsg(null) }} />
      </div>
      <div className="settings-action-row settings-action-row-start">
        <Toggle checked={notifyAlerts} label="Alert events" onChange={v => { setNotifyAlerts(v); setMsg(null) }} />
      </div>
      <div className="settings-action-row settings-action-row-start">
        <Toggle checked={notifyFills} label="Trade fill events" onChange={v => { setNotifyFills(v); setMsg(null) }} />
      </div>
      <div className="settings-action-row settings-action-row-start">
        <Toggle checked={notifySummary} label="Daily summary event" onChange={v => { setNotifySummary(v); setMsg(null) }} />
      </div>
      <div className="settings-action-row">
        <button onClick={() => mut.mutate()} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save'}</button>
      </div>
      <SaveMsg msg={msg} />
      <p className="hint">
        Payload is signed with <code>X-Aletheia-Signature</code> (HMAC SHA-256) when a secret is set.
        Secret is write-only — leave blank to keep the current value.
      </p>
    </div>
  )
}

// ─── Software update ───

function UpdateCard({ settings, qc }: any) {
  const updateCfg = settings?.update as any || {}
  const [branch, setBranch] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [cfgMsg, setCfgMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!updateCfg) return
    setBranch(updateCfg.branch || 'master')
    setEnabled(updateCfg.enabled !== false)
  }, [settings])

  const { data, isLoading } = useQuery({ queryKey: ['update-status'], queryFn: api.getUpdateStatus })
  const [applyMsg, setApplyMsg] = useState('')

  const cfgMut = useMutation({
    mutationFn: () => api.updateUpdateConfig({ enabled, branch }),
    onSuccess: () => { setCfgMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setCfgMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const apply = useMutation({
    mutationFn: api.applyUpdate,
    onSuccess: (r) => {
      setApplyMsg(r.applied
        ? `Updated to ${r.new_commit?.slice(0, 8)} (deps reinstalled: ${r.deps_reinstalled}).`
        : `Not applied: ${r.reason}`)
      qc.invalidateQueries({ queryKey: ['update-status'] })
    },
  })

  let badge = <span className="badge badge-yellow">not available</span>
  if (isLoading) badge = <span className="badge">checking…</span>
  else if (data?.ok && data.has_update) badge = <span className="badge badge-yellow">{data.behind_count} behind</span>
  else if (data?.ok && !data.has_update) badge = <span className="badge badge-green">up to date</span>

  return (
    <div className="card mb-0">
      {/* Status line */}
      <div className="flex items-center gap-3 mb-3 flex-wrap">
        {badge}
        {data?.ok && (
          <span className="hint mono m-0">
            {data.branch} · {data.current_commit?.slice(0, 8)}{data.has_update ? ` → ${data.remote_commit?.slice(0, 8)}` : ''}
          </span>
        )}
        <button className="secondary" onClick={() => qc.invalidateQueries({ queryKey: ['update-status'] })}>Re-check</button>
        {data?.ok && data.has_update && (
          <button
            onClick={() => { if (confirm('Pull latest code? Restart the gateway afterward.')) apply.mutate() }}
            disabled={apply.isPending}
          >Apply update</button>
        )}
      </div>
      {applyMsg && <p className="hint mb-3">{applyMsg}</p>}

      {/* Config */}
      <div className="flex items-center gap-2 mb-3">
        <Toggle checked={enabled} onChange={v => { setEnabled(v); setCfgMsg(null) }} />
      </div>
      <div className="form-grid mb-3">
        <label>
          <span>Branch</span>
          <input value={branch} onChange={e => { setBranch(e.target.value); setCfgMsg(null) }} className="mono" />
        </label>
      </div>
      <div className="flex gap-2">
        <button className="secondary" onClick={() => cfgMut.mutate()} disabled={cfgMut.isPending}>{cfgMut.isPending ? 'Saving…' : 'Save config'}</button>
      </div>
      <SaveMsg msg={cfgMsg} />
    </div>
  )
}

function NetworkCard({ settings, qc }: any) {
  const net = (settings?.network as any) || {}
  const [protocol, setProtocol] = useState<string>(net.proxy_protocol || 'http')
  const [host, setHost] = useState<string>(net.proxy_host ?? '')
  const [port, setPort] = useState<string>(net.proxy_port != null ? String(net.proxy_port) : '')
  const [enforce, setEnforce] = useState<boolean>(!!net.enforce)
  const [noProxy, setNoProxy] = useState<string>(net.no_proxy || '')
  const [testResult, setTestResult] = useState<{ reachable: boolean; latency_ms: number; error: string | null } | null>(null)
  const [testing, setTesting] = useState(false)

  // Sync from server state when settings load
  useEffect(() => {
    setProtocol(net.proxy_protocol || 'http')
    setHost(net.proxy_host ?? '')
    setPort(net.proxy_port != null ? String(net.proxy_port) : '')
    setEnforce(!!net.enforce)
    setNoProxy(net.no_proxy || '')
  }, [net.proxy_protocol, net.proxy_host, net.proxy_port, net.enforce, net.no_proxy])

  const mut = useMutation({
    mutationFn: (body: any) => api.updateNetworkConfig(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  const handleSave = () => {
    const portNum = parseInt(port, 10)
    mut.mutate({
      proxy_protocol: protocol,
      proxy_host: host.trim(),
      proxy_port: Number.isFinite(portNum) ? portNum : 0,
      enforce,
      no_proxy: noProxy.trim(),
    })
    setTestResult(null)
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const r = await api.testNetworkProxy()
      setTestResult(r)
    } catch (e: any) {
      setTestResult({ reachable: false, latency_ms: 0, error: e.message || 'request failed' })
    } finally {
      setTesting(false)
    }
  }

  const isConfigured = !!host.trim()
  const composed = isConfigured ? `${protocol}://${host.trim()}:${port || '?'}` : '(disabled — direct connection)'
  let statusDot = <span style={{ color: 'var(--muted)', fontSize: '0.75rem' }}>●</span>
  if (isConfigured && testResult?.reachable) statusDot = <span style={{ color: 'var(--green)', fontSize: '0.75rem' }}>●</span>
  else if (isConfigured && testResult && !testResult.reachable) statusDot = <span style={{ color: 'var(--red)', fontSize: '0.75rem' }}>●</span>

  return (
    <div className="flex flex-col gap-2">
      <Row label="Protocol" hint="HTTP for a Clash/Mihomo mixed port; SOCKS5 for a SOCKS proxy (requires the aiohttp_socks backend package).">
        <select value={protocol} onChange={e => setProtocol(e.target.value)} className="text-sm">
          <option value="http">HTTP</option>
          <option value="https">HTTPS</option>
          <option value="socks5">SOCKS5</option>
        </select>
      </Row>
      <Row label="Host" hint="Proxy hostname or IP. Leave empty to disable the proxy (direct connection).">
        <div className="flex gap-2 items-center">
          {statusDot}
          <input
            type="text" value={host}
            onChange={e => setHost(e.target.value)}
            placeholder="127.0.0.1"
            className="mono text-sm" style={{ width: '160px' }}
          />
        </div>
      </Row>
      <Row label="Port" hint="Proxy port (e.g. Clash 7890 / 7897).">
        <input
          type="number" value={port}
          onChange={e => setPort(e.target.value)}
          placeholder="7890"
          className="mono text-sm" style={{ width: '110px' }}
        />
      </Row>
      <Row label="Enforce (fail-closed)" hint="Require ALL outbound traffic to go through the proxy. If the proxy is unreachable, requests fail and trading auto-pauses instead of silently connecting direct and leaking your real IP. Recommended for production.">
        <Toggle checked={enforce} onCheckedChange={setEnforce} label={enforce ? 'Strict — proxy required' : 'Off — direct fallback allowed'} />
      </Row>
      <Row label="No Proxy" hint="Comma-separated hosts to bypass. Loopback (127.0.0.1, localhost, ::1) is always bypassed automatically.">
        <input
          type="text" value={noProxy}
          onChange={e => setNoProxy(e.target.value)}
          placeholder="example.internal"
          className="mono text-sm" style={{ width: '220px' }}
        />
      </Row>
      <div className="hint" style={{ paddingLeft: '0.2rem' }}>Effective proxy: <span className="mono">{composed}</span></div>
      <div className="flex gap-2 flex-wrap">
        <button onClick={handleSave} disabled={mut.isPending}>Save</button>
        <button className="secondary" onClick={handleTest} disabled={testing}>
          {testing ? 'Testing…' : 'Test Connection'}
        </button>
      </div>
      {mut.isSuccess && <div className="settings-save-msg ok">Saved.</div>}
      {mut.isError && <div className="settings-save-msg err">{(mut.error as any)?.message}</div>}
      {testResult && (
        <div className={`settings-save-msg ${testResult.reachable ? 'ok' : 'err'}`}>
          {testResult.reachable ? `✓ reachable (${testResult.latency_ms}ms)` : `✗ ${testResult.error || 'unreachable'}`}
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Model provider — card-list design (one card per provider, inline expand)
// ─────────────────────────────────────────────────────────────────────────────

function ModelProviderCard({ qc }: any) {
  const { data } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig })
  const { data: health = [] } = useQuery({ queryKey: ['model-health'], queryFn: api.getModelHealth, refetchInterval: 30000 })
  const { data: broker } = useQuery({ queryKey: ['model-broker'], queryFn: api.getInferenceBroker, refetchInterval: 30000 })
  const [expanded, setExpanded] = useState<string | null>(null)

  if (!data) return <p className="hint">Loading providers…</p>

  const deepName = (data as any).deep_provider
  const fastName = (data as any).fast_provider
  const defaultName = String((data as any).default_provider || '')
  const routingFallback = defaultName ? `→ ${defaultName}` : 'not set'

  return (
    <div className="flex flex-col gap-2">
      <ModelPingButton configured={!!defaultName} />
      <div style={{ fontSize: '0.72rem', color: 'var(--muted)', display: 'flex', gap: '1rem', paddingLeft: '0.2rem' }}>
        <span>Default: <span style={{ color: 'var(--text)' }}>{defaultName || 'not set'}</span></span>
        <span>Deep (decisions): <span style={{ color: 'var(--text)' }}>{deepName || routingFallback}</span></span>
        <span>Fast (research): <span style={{ color: 'var(--text)' }}>{fastName || routingFallback}</span></span>
      </div>
      {ALL_PROVIDERS.map(p => {
        // The 'openai_compatible' card is the merged API card: it manages both
        // openai_compatible and anthropic_compatible backend slots.
        const anthroConfig = p === 'openai_compatible' ? data.providers?.anthropic_compatible : undefined

        // Pick health from whichever backend is currently active for the merged card.
        let cardHealth: any
        if (p === 'openai_compatible') {
          const oCfg = data.providers?.openai_compatible
          const aCfg = data.providers?.anthropic_compatible
          const oH = (health as any[]).find(x => x.provider === 'openai_compatible')
          const aH = (health as any[]).find(x => x.provider === 'anthropic_compatible')
          const anthroActive = (aCfg?.base_url || aCfg?.api_key?.configured) && !(oCfg?.base_url || oCfg?.api_key?.configured)
          cardHealth = anthroActive ? aH : oH
        } else {
          cardHealth = (health as any[]).find(x => x.provider === p)
        }

        // Routing badges: either backend slot may carry the role for the merged card.
        const cardIsDefault = p === data.default_provider || (p === 'openai_compatible' && data.default_provider === 'anthropic_compatible')
        const cardIsDeep = p === deepName || (p === 'openai_compatible' && deepName === 'anthropic_compatible')
        const cardIsFast = p === fastName || (p === 'openai_compatible' && fastName === 'anthropic_compatible')

        return (
        <ProviderCard
          key={p}
          name={p}
          config={data.providers?.[p]}
          anthroConfig={anthroConfig}
          health={cardHealth}
          allHealth={health as any[]}
          broker={broker}
          isDefault={cardIsDefault}
          isDeep={cardIsDeep}
          isFast={cardIsFast}
          isExpanded={expanded === p}
          onToggle={() => setExpanded(expanded === p ? null : p)}
          qc={qc}
        />
        )
      })}
    </div>
  )
}

function ModelPingButton({ configured }: { configured: boolean }) {
  const ping = useMutation({ mutationFn: () => api.pingModel() })
  const result = ping.data
  return (
    <div className="model-ping-row">
      <button className="secondary" onClick={() => ping.mutate()} disabled={!configured || ping.isPending}>
        {ping.isPending ? 'Testing…' : configured ? 'Test Default Model' : 'No Default Model Selected'}
      </button>
      {result && (
        <span className={`model-ping-result mono ${result.ok ? 'ok' : 'fail'}`}>
          {result.ok
            ? `OK — ${result.latency_ms}ms · ${result.model}`
            : `Error — ${result.error}`}
        </span>
      )}
    </div>
  )
}

function ProviderCard({ name, config, anthroConfig, health, allHealth, broker, isDefault, isDeep, isFast, isExpanded, onToggle, qc }: {
  name: string; config: any; anthroConfig?: any; health: any; allHealth: any[]; isDefault: boolean; isDeep: boolean; isFast: boolean;
  broker?: any;
  isExpanded: boolean; onToggle: () => void; qc: any
}) {
  const [showInferenceLimits, setShowInferenceLimits] = useState(false)
  const isHTTP = HTTP_PROVIDERS.has(name)
  const isOAuth = OAUTH_PROVIDERS.has(name)
  const isCLI = !isHTTP
  // Merged API card: enabled if either backend slot is active.
  const enabled = name === 'openai_compatible'
    ? (config?.enabled === true || anthroConfig?.enabled === true)
    : config?.enabled === true

  // Toggle the backend slot that is actually active.
  const effectiveToggleProvider = (name === 'openai_compatible' && anthroConfig?.enabled === true && config?.enabled !== true)
    ? 'anthropic_compatible'
    : name
  const toggleEnabled = useMutation({
    mutationFn: (next: boolean) => api.setModelProvider({ provider: effectiveToggleProvider, enabled: next }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['model'] })
      qc.invalidateQueries({ queryKey: ['model-health'] })
      qc.invalidateQueries({ queryKey: ['status'] })
    },
  })

  // Status resolution
  let statusColor: string = 'var(--muted)'
  let statusText = 'not configured'
  const keyConfigured = isHTTP && (config?.api_key?.configured || (name === 'openai_compatible' && anthroConfig?.api_key?.configured))
  const oauthConfigured = isOAuth && config?.oauth?.configured
  const hasCredential = keyConfigured || oauthConfigured
  if (!enabled) { statusColor = 'var(--muted)'; statusText = 'disabled' }
  else if (health?.available) { statusColor = 'var(--green)'; statusText = 'connected' }
  else if (isCLI && health?.error === 'not_installed') { statusColor = 'var(--yellow)'; statusText = 'not installed' }
  else if (isCLI && health?.error?.includes('disabled')) { statusColor = 'var(--muted)'; statusText = 'disabled' }
  else if (isOAuth && !hasCredential) { statusColor = 'var(--muted)'; statusText = 'not connected' }
  else if (isHTTP && !isOAuth && !keyConfigured) { statusColor = 'var(--muted)'; statusText = 'not configured' }
  else if (health && !health.available) { statusColor = 'var(--red)'; statusText = health.error || 'unreachable' }
  else if (hasCredential && !health) { statusColor = 'var(--yellow)'; statusText = 'checking…' }

  // Summary line — prefer the configured slot for base_url / model / key display.
  const displayConfig = (name === 'openai_compatible' && !config?.base_url && anthroConfig?.base_url) ? anthroConfig : config
  const details: string[] = []
  if (isHTTP && !NATIVE_PROVIDERS.has(name) && displayConfig?.base_url) details.push(displayConfig.base_url)
  if (displayConfig?.model && displayConfig.model !== 'default') details.push(`model: ${displayConfig.model}`)
  if (isHTTP && displayConfig?.api_key?.configured) details.push(`key: ··${displayConfig.api_key.last4}`)
  if (isCLI && health?.model) details.push(`model: ${health.model}`)

  return (
    <div style={{
      border: `1px solid ${isExpanded ? 'var(--accent-dim)' : 'var(--border)'}`,
      borderRadius: 'var(--r)',
      background: 'var(--surface)',
      transition: 'border-color 0.15s',
    }}>
      {/* Header row — always visible */}
      <div
        onClick={onToggle}
        style={{
          display: 'flex', alignItems: 'center', gap: '0.6rem',
          padding: '0.7rem 0.9rem', cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        {/* Status dot — color encodes provider state; hover for label */}
        <span title={statusText} style={{
          width: '8px', height: '8px', borderRadius: '50%',
          background: statusColor, flexShrink: 0,
          boxShadow: statusColor === 'var(--muted)' ? 'none' : `0 0 6px ${statusColor}`,
        }} />
        {/* Label */}
        <span style={{ fontWeight: 600, fontSize: '0.82rem', fontFamily: 'var(--display)' }}>
          {PROVIDER_LABELS[name] || name}
        </span>
        {/* Default badge */}
        {isDefault && (
          <span style={{
            fontSize: '0.62rem', fontWeight: 700, textTransform: 'uppercase',
            letterSpacing: '0.06em', color: 'var(--accent)',
            padding: '0.1rem 0.4rem', borderRadius: '3px',
            border: '1px solid var(--accent-dim)', background: 'rgba(34,211,238,0.06)',
          }}>DEFAULT</span>
        )}
        {/* Spacer */}
        <span className="flex-1" />
        <span
          onClick={e => e.stopPropagation()}
          style={{ display: 'inline-flex', alignItems: 'center', marginRight: '0.35rem' }}
          title={isDefault ? 'The default provider must stay enabled' : `Toggle ${PROVIDER_LABELS[name] || name}`}
        >
          <Toggle
            checked={enabled}
            onCheckedChange={checked => {
              if (!checked) {
                const available = (allHealth || []).filter((h: any) => h.available)
                if (available.length === 1 && available[0].provider === name) {
                  if (!confirm(`"${PROVIDER_LABELS[name] || name}" is your only available provider. Disabling it will cut off all agents from LLM access. Disable anyway?`)) return
                }
              }
              toggleEnabled.mutate(checked)
            }}
            disabled={isDefault || toggleEnabled.isPending}
            aria-label={`${enabled ? 'Disable' : 'Enable'} ${PROVIDER_LABELS[name] || name}`}
          />
        </span>
        {/* Status text removed — dot color + hover tooltip conveys state */}
        {/* Chevron */}
        <span style={{ fontSize: '0.7rem', color: 'var(--muted)', transform: isExpanded ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }}>▸</span>
      </div>

      {/* Summary detail (only when collapsed and has info) */}
      {!isExpanded && details.length > 0 && (
        <div style={{
          padding: '0 0.9rem 0.6rem 2.3rem',
          fontSize: '0.72rem', color: 'var(--muted)', fontFamily: 'var(--mono)',
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {details.join(' · ')}
        </div>
      )}

      {/* Expanded content */}
      {isExpanded && (
        <div style={{ padding: '0 0.9rem 0.9rem', borderTop: '1px solid var(--border)' }}>
          {name === 'openai_native' ? (
            <OpenAIOAuthPanel config={config} health={health} qc={qc} />
          ) : name === 'anthropic_native' ? (
            <ClaudeOAuthPanel config={config} health={health} qc={qc} />
          ) : isHTTP ? (
            <HTTPProviderForm name={name} config={config} anthroConfig={anthroConfig} isDefault={isDefault} isDeep={isDeep} isFast={isFast} qc={qc} />
          ) : name === 'codex_cli' ? (
            <CodexCLIPanel config={config} health={health} qc={qc} />
          ) : (
            <ClaudeCLIPanel config={config} health={health} isDefault={isDefault} qc={qc} />
          )}
          <div style={{ marginTop: '0.75rem', borderTop: '1px solid var(--border)', paddingTop: '0.5rem' }}>
            <button
              className="secondary"
              style={{ fontSize: '0.75rem', width: '100%', textAlign: 'left', display: 'flex', alignItems: 'center', gap: '0.4rem' }}
              onClick={() => setShowInferenceLimits(v => !v)}
            >
              <span style={{ fontSize: '0.65rem', color: 'var(--muted)' }}>{showInferenceLimits ? '▾' : '▸'}</span>
              Inference admission
              {!showInferenceLimits && broker?.providers?.[effectiveToggleProvider]?.metrics && (
                <span className="hint mono" style={{ marginLeft: 'auto', fontSize: '0.7rem' }}>
                  active {broker.providers[effectiveToggleProvider].metrics.active ?? 0}
                </span>
              )}
            </button>
            {showInferenceLimits && (
              <InferenceLimitsForm
                name={effectiveToggleProvider}
                config={effectiveToggleProvider === 'anthropic_compatible' ? anthroConfig : config}
                metrics={broker?.providers?.[effectiveToggleProvider]?.metrics}
                qc={qc}
              />
            )}
          </div>
        </div>
      )}
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
    mutationFn: () => api.setModelProvider({
      provider: name,
      inference_max_concurrency: Number(maxConcurrency),
      inference_requests_per_minute: Number(rpm),
      inference_tpm: Number(tpm),
      inference_queue_capacity: Number(queueCapacity),
      inference_reserved_high_priority_slots: Number(reservedSlots),
      inference_timeout_sec: Number(timeoutSec),
    }),
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

// ─── HTTP Provider inline form ───

const TIER_COLORS: Record<string, string> = {
  flagship: 'var(--accent)',
  standard: 'var(--green)',
  fast: 'var(--yellow)',
  reasoning: 'var(--blue)',
}

function HTTPProviderForm({ name, config, anthroConfig, isDefault, isDeep, isFast, qc }: {
  name: string; config: any; anthroConfig?: any; isDefault: boolean; isDeep: boolean; isFast: boolean; qc: any
}) {
  const isNative = NATIVE_PROVIDERS.has(name)

  // For the merged API card (name === 'openai_compatible'), determine which backend
  // slot is currently active so we can pre-fill the form correctly.
  const anthroIsActive = name === 'openai_compatible' &&
    !!(anthroConfig?.base_url || anthroConfig?.api_key?.configured) &&
    !(config?.base_url || config?.api_key?.configured)
  const activeConfig = anthroIsActive ? anthroConfig : config

  const [protocol, setProtocol] = useState<string>(() => {
    if (anthroIsActive) return 'anthropic_messages'
    return activeConfig?.protocol || 'chat_completions'
  })
  const [baseUrl, setBaseUrl] = useState(() => activeConfig?.base_url || '')
  const [model, setModel] = useState(() => activeConfig?.model || '')
  const [authScheme, setAuthScheme] = useState(() => anthroConfig?.auth_scheme || 'x-api-key')
  const [apiKey, setApiKey] = useState('')
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null)
  const [detectedModels, setDetectedModels] = useState<{ slug: string; display_name: string; tier?: string; context_window?: number }[] | null>(null)
  const [modelFocused, setModelFocused] = useState(false)
  const [modelEditing, setModelEditing] = useState(false)

  // Derived: which backend provider does the current protocol map to?
  const isAnthropicMode = protocol === 'anthropic_messages'
  const backendProvider = isAnthropicMode ? 'anthropic_compatible' : (name === 'openai_compatible' ? 'openai_compatible' : name)

  // When the user switches protocol, load the corresponding config slot's values.
  const handleProtocolChange = (newProtocol: string) => {
    setProtocol(newProtocol)
    setResult(null)
    setDetectedModels(null)
    if (name === 'openai_compatible') {
      if (newProtocol === 'anthropic_messages') {
        if (anthroConfig?.base_url) setBaseUrl(anthroConfig.base_url)
        if (anthroConfig?.model) setModel(anthroConfig.model)
      } else {
        if (config?.base_url) setBaseUrl(config.base_url)
        if (config?.model) setModel(config.model)
      }
    }
  }

  // For API key badge: show the key hint from the active slot.
  const activeApiKey = isAnthropicMode ? anthroConfig?.api_key : config?.api_key

  const detectMut = useMutation({
    mutationFn: async () => {
      const body: any = { provider: backendProvider }
      if (!isNative) body.base_url = baseUrl || undefined
      if (apiKey) body.api_key = apiKey
      if (isAnthropicMode) body.auth_scheme = authScheme
      return api.detectModels(body)
    },
    onSuccess: (data) => {
      const models = data.models || []
      setDetectedModels(models)
      if (models.length > 0 && !model) {
        setModel(models[0].slug)
      }
    },
  })

  // Auto-detect models on mount when API key is already configured
  useEffect(() => {
    const keyConfigured = isAnthropicMode ? anthroConfig?.api_key?.configured : config?.api_key?.configured
    if (keyConfigured) detectMut.mutate()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const verifyAndSave = useMutation({
    mutationFn: async () => {
      setResult(null)
      const testBody: any = {
        provider: backendProvider,
        model: model || undefined,
        api_key: apiKey || undefined,
        probe: true,
      }
      if (!isNative) testBody.base_url = baseUrl || undefined
      if (isAnthropicMode) testBody.auth_scheme = authScheme
      else testBody.protocol = protocol

      const test = await api.testModelCandidate(testBody)
      if (!test.available) throw new Error(test.error || 'provider unreachable')

      const saveBody: any = {
        provider: backendProvider,
        enabled: true,
        ...(model ? { model } : {}),
        ...(apiKey ? { api_key: apiKey } : {}),
      }
      if (!isNative && baseUrl) saveBody.base_url = baseUrl
      if (isAnthropicMode) saveBody.auth_scheme = authScheme
      else saveBody.protocol = protocol

      await api.setModelProvider(saveBody)
      return test
    },
    onSuccess: (test) => {
      setApiKey('')
      setResult({ ok: true, msg: `verified & saved (${test.model || 'ok'})` })
      qc.invalidateQueries({ queryKey: ['model'] })
      qc.invalidateQueries({ queryKey: ['model-health'] })
      qc.invalidateQueries({ queryKey: ['status'] })
    },
    onError: (e: any) => setResult({ ok: false, msg: `${e.message} — not saved` }),
  })

  const setDefault = useMutation({
    mutationFn: () => api.setModelProvider({ provider: backendProvider, enabled: true, default: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
  })

  const setRoutingRole = useMutation({
    mutationFn: (role: string) => {
      if (role === 'default') return api.setModelProvider({ provider: backendProvider, enabled: true, default: true })
      if (role === 'deep') return api.setModelProvider({ provider: backendProvider, enabled: true, set_as_deep: true })
      return api.setModelProvider({ provider: backendProvider, enabled: true, set_as_fast: true })
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
  })

  const currentRole = isDefault ? 'default' : isDeep ? 'deep' : isFast ? 'fast' : 'none'

  const filteredModels = (!modelEditing || !model) ? (detectedModels ?? []) : (detectedModels?.filter(m =>
    m.slug.toLowerCase().includes(model.toLowerCase()) || m.display_name.toLowerCase().includes(model.toLowerCase())
  ) ?? [])
  const showSuggestions = modelFocused && filteredModels.length > 0

  return (
    <div className="form-grid" style={{ paddingTop: '0.75rem', gridTemplateColumns: '1fr 1fr' }}>
      {/* Row 1: Base URL — full width (hidden for native providers) */}
      {!isNative && (
        <label className="col-full">
          Base URL
          <input value={baseUrl} placeholder="http://localhost:8000/v1" onChange={e => { setBaseUrl(e.target.value); setResult(null); setDetectedModels(null) }} />
        </label>
      )}

      {/* Row 2: Model (combobox) + secondary field */}
      <label style={{ position: 'relative' }}>
        <span>Model{detectedModels && detectedModels.length > 0 && ` (${detectedModels.length})`}</span>
        <input
          value={model}
          placeholder="model slug"
          onChange={e => { setModel(e.target.value); setModelEditing(true); setResult(null) }}
          onFocus={() => { setModelFocused(true); setModelEditing(false) }}
          onBlur={() => setTimeout(() => setModelFocused(false), 150)}
          autoComplete="off"
        />
        {showSuggestions && (
          <div style={{
            position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 20,
            background: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: '0 0 var(--r) var(--r)', maxHeight: '10rem', overflowY: 'auto',
            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          }}>
            {filteredModels.map(m => (
              <div
                key={m.slug}
                onMouseDown={() => { setModel(m.slug); setResult(null) }}
                style={{
                  padding: '0.4rem 0.6rem', cursor: 'pointer', fontSize: '0.78rem',
                  fontFamily: 'var(--mono)',
                  background: m.slug === model ? 'var(--accent-dim)' : 'transparent',
                  display: 'flex', alignItems: 'center', gap: '0.4rem',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--accent-dim)')}
                onMouseLeave={e => (e.currentTarget.style.background = m.slug === model ? 'var(--accent-dim)' : 'transparent')}
              >
                <span className="flex-1">{m.display_name}</span>
                {(m as any).tier && (
                  <span style={{
                    fontSize: '0.62rem', fontWeight: 600, textTransform: 'uppercase',
                    padding: '0.05rem 0.3rem', borderRadius: '3px',
                    color: TIER_COLORS[(m as any).tier] || 'var(--muted)',
                    border: `1px solid ${TIER_COLORS[(m as any).tier] || 'var(--border)'}`,
                    opacity: 0.8,
                  }}>{(m as any).tier}</span>
                )}
                {(m as any).context_window && (
                  <span style={{ fontSize: '0.62rem', color: 'var(--muted)' }}>
                    {Math.round((m as any).context_window / 1000)}K
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </label>
      {!isNative && (
        <label>
          Protocol
          <select value={protocol} onChange={e => handleProtocolChange(e.target.value)}>
            <option value="chat_completions">OpenAI – Chat Completions</option>
            <option value="responses">OpenAI – Responses</option>
            <option value="anthropic_messages">Anthropic Messages</option>
          </select>
        </label>
      )}
      {isAnthropicMode && !isNative && (
        <label>
          Auth Scheme
          <select value={authScheme} onChange={e => { setAuthScheme(e.target.value); setResult(null); setDetectedModels(null) }}>
            <option value="x-api-key">X-API-Key (Native Anthropic)</option>
            <option value="bearer">Bearer (Gateway / Databricks)</option>
          </select>
        </label>
      )}
      {!isAnthropicMode && !isNative && <div />}

      {/* Row 3: API Key — full width */}
      <label className="col-full">
        API Key
        <input type="password" value={apiKey} placeholder={activeApiKey?.configured ? `•••••• configured (ends ··${activeApiKey.last4})` : 'paste key'} onChange={e => { setApiKey(e.target.value); setResult(null) }} />
      </label>

      {/* Row 4: Actions — full width */}
      <div style={{ gridColumn: '1 / -1', display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <button onClick={() => verifyAndSave.mutate()} disabled={verifyAndSave.isPending}>
          {verifyAndSave.isPending ? 'Verifying…' : 'Verify & Save'}
        </button>
        <label style={{ display: 'flex', flexDirection: 'row', alignItems: 'center', gap: '0.35rem', fontSize: '0.8rem', color: 'var(--muted)', margin: 0 }}>
          Routing role
          <select
            value={currentRole}
            onChange={e => setRoutingRole.mutate(e.target.value)}
            disabled={setRoutingRole.isPending}
            style={{ fontSize: '0.78rem', padding: '0.28rem 0.4rem' }}
          >
            <option value="default">Default</option>
            <option value="deep">Deep tier (decisions)</option>
            <option value="fast">Fast tier (research)</option>
            {currentRole === 'none' && <option value="none" disabled>— unassigned —</option>}
          </select>
        </label>
        <button className="secondary" onClick={() => detectMut.mutate()} disabled={detectMut.isPending} title="Re-detect models" style={{ marginLeft: 'auto', padding: '0.42rem 0.55rem' }}>
          {detectMut.isPending ? '↻ …' : '↻'}
        </button>
      </div>
      {result && (
        <div className={`col-full settings-save-msg ${result.ok ? 'ok' : 'err'}`}>
          {result.ok ? '✓' : '✗'} {result.msg}
        </div>
      )}
      {detectMut.isError && (
        <div className="col-full settings-save-msg err">
          ✗ detection failed
        </div>
      )}
    </div>
  )
}

// ─── Claude CLI panel ───

function ClaudeCLIPanel({ config, health, isDefault, qc }: {
  config: any; health: any; isDefault: boolean; qc: any
}) {
  const enableMut = useMutation({
    mutationFn: () => api.setModelProvider({ provider: 'claude_cli', enabled: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['model-health'] }) },
  })
  const setDefault = useMutation({
    mutationFn: () => api.setModelProvider({ provider: 'claude_cli', enabled: true, default: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
  })

  const available = health?.available
  const notInstalled = health?.error === 'not_installed'
  const loading = !health

  return (
    <div className="pt-3">
      {loading ? (
        <p className="hint">Checking CLI…</p>
      ) : notInstalled ? (
        <p className="hint">Claude Code CLI not found in PATH. Install it first.</p>
      ) : available ? (
        <div>
          <div className="flex gap-3 items-center flex-wrap mb-2">
            <span className="badge badge-green">connected</span>
            {health.model && <span className="text-xs mono text-muted">model: {health.model}</span>}
          </div>
          <div className="flex gap-2 items-center">
            {!isDefault && (
              <button className="secondary" onClick={() => setDefault.mutate()} disabled={setDefault.isPending}>
                Set as Default
              </button>
            )}
          </div>
          <p className="hint mt-2">Auth managed by Claude Code CLI. Aletheia stores no credentials.</p>
        </div>
      ) : (
        <div>
          <p className="hint mb-2">CLI detected but not enabled or not authenticated.</p>
          <button onClick={() => enableMut.mutate()} disabled={enableMut.isPending}>Enable</button>
        </div>
      )}
    </div>
  )
}

// ─── Codex CLI panel ───

function CodexCLIPanel({ config, health, qc }: { config: any; health: any; qc: any }) {
  const { data: cfg } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig })
  const selectedModel = cfg?.providers?.codex_cli?.codex_model || ''
  const [login, setLogin] = useState<{ state: string; verification_uri: string | null; user_code: string | null; error: string | null } | null>(null)

  const statusPoll = useQuery({
    queryKey: ['codex-login-status'], queryFn: api.codexLoginStatus,
    enabled: login?.state === 'awaiting_authorization', refetchInterval: 3000,
  })
  if (statusPoll.data && statusPoll.data.state !== login?.state && statusPoll.data.state === 'completed') {
    qc.invalidateQueries({ queryKey: ['model-health'] })
    qc.invalidateQueries({ queryKey: ['status'] })
  }

  const start = useMutation({ mutationFn: api.codexLoginStart, onSuccess: r => setLogin(r) })
  const cancel = useMutation({ mutationFn: api.codexLoginCancel, onSuccess: () => setLogin(null) })
  const { data: models = [] } = useQuery({ queryKey: ['codex-models'], queryFn: api.getCodexModels, enabled: health?.available === true })
  const pickModel = useMutation({
    mutationFn: (slug: string) => api.setModelProvider({ provider: 'codex_cli', codex_model: slug, enabled: true, default: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
  })

  const notInstalled = health?.error === 'not_installed'
  const available = health?.available

  return (
    <div className="pt-3">
      {notInstalled ? (
        <p className="hint">Codex CLI not found in PATH. Install it and log in with your ChatGPT subscription.</p>
      ) : available ? (
        <div>
          <div className="flex gap-2 items-center mb-2">
            <span className="badge badge-green">connected</span>
            <span className="text-xs text-muted">via ChatGPT subscription</span>
          </div>
          <div className="form-grid" style={{ maxWidth: '320px' }}>
            <label>
              <span>Model</span>
              <select value={selectedModel} onChange={e => pickModel.mutate(e.target.value)}>
                <option value="">codex default</option>
                {models.map((m: any) => <option key={m.slug} value={m.slug}>{m.display_name}</option>)}
              </select>
            </label>
          </div>
          <p className="hint mt-2">OAuth token managed by Codex CLI (~/.codex). Aletheia stores no token.</p>
        </div>
      ) : (
        <div>
          <p className="mb-2">
            <span className="badge badge-red">not logged in</span>
            <span style={{ fontSize: '0.75rem', color: 'var(--muted)', marginLeft: '0.5rem' }}>CLI installed but no active session.</span>
          </p>
          {login?.state === 'awaiting_authorization' && login.verification_uri ? (
            <div className="text-sm">
              <p>1. Open <a href={login.verification_uri} target="_blank" rel="noreferrer">{login.verification_uri}</a></p>
              <p>2. Enter code: <strong className="mono">{login.user_code}</strong></p>
              <p className="hint">Waiting… ({statusPoll.data?.state || 'polling'})</p>
              <button className="secondary mt-2" onClick={() => cancel.mutate()}>Cancel</button>
            </div>
          ) : login?.state === 'failed' ? (
            <p className="hint"><span className="badge badge-red">failed</span> {login.error === 'device_auth_parse_failed' ? 'Run `codex login` in a terminal instead.' : login.error}</p>
          ) : (
            <button onClick={() => start.mutate()} disabled={start.isPending}>Connect Codex</button>
          )}
        </div>
      )}
    </div>
  )
}

// ─── OpenAI OAuth panel (ChatGPT Plus/Pro subscription) ───

function OpenAIOAuthPanel({ config, health, qc }: { config: any; health: any; qc: any }) {
  const [oauthState, setOauthState] = useState<{ state: string; auth_url?: string | null; error?: string | null } | null>(null)
  const [manualModel, setManualModel] = useState('')

  const finalize = useMutation({
    mutationFn: api.openaiOAuthFinalize,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['model-health'] })
      qc.invalidateQueries({ queryKey: ['model'] })
    },
  })
  const statusPoll = useQuery({
    queryKey: ['openai-oauth-status'], queryFn: api.openaiOAuthStatus,
    enabled: oauthState?.state === 'awaiting', refetchInterval: 2000,
  })
  if (statusPoll.data?.state === 'completed' && oauthState?.state !== 'completed') {
    setOauthState(statusPoll.data)
    finalize.mutate()
  }

  const start = useMutation({
    mutationFn: api.openaiOAuthStart,
    onSuccess: r => {
      setOauthState(r)
      if (r.auth_url) window.open(r.auth_url, '_blank')
    },
  })
  const cancel = useMutation({ mutationFn: api.openaiOAuthCancel, onSuccess: () => setOauthState(null) })
  const pickModel = useMutation({
    mutationFn: (slug: string) => api.setModelProvider({ provider: 'openai_native', model: slug, enabled: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['model-health'] }) },
  })
  const setDefault = useMutation({
    mutationFn: () => api.setModelProvider({ provider: 'openai_native', default: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
  })
  const disconnect = useMutation({
    mutationFn: api.openaiOAuthDisconnect,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['model-health'] }) },
  })

  const available = health?.available
  const models: string[] = health?.models || []
  const modelMeta: Array<{ slug: string; display_name?: string; tier?: string; context_window?: number }> = health?.model_metadata || []
  const currentModel = config?.model && config.model !== 'default' ? config.model : null

  return (
    <div className="pt-3">
      {available ? (
        <div>
          <div className="flex gap-2 items-center mb-2">
            <span className="badge badge-green">connected</span>
            <span className="text-xs text-muted">via ChatGPT OAuth (experimental)</span>
          </div>
          <div className="mb-2">
            <label className="text-xs text-muted mb-1" style={{ display: 'block' }}>Model</label>
            {models.length > 0 ? (
              <select
                value={currentModel || ''}
                onChange={e => pickModel.mutate(e.target.value)}
                className="w-full text-sm mono"
              >
                {!currentModel && <option value="">Select a model</option>}
                {modelMeta.length > 0
                  ? modelMeta.map(m => (
                    <option key={m.slug} value={m.slug}>
                      {m.display_name || m.slug}{m.tier || m.context_window ? ' — ' : ''}{m.tier || ''}{m.tier && m.context_window ? ' · ' : ''}{m.context_window ? `${Math.round(m.context_window / 1000)}K ctx` : ''}
                    </option>
                  ))
                  : models.map(m => <option key={m} value={m}>{m}</option>)
                }
              </select>
            ) : (
              <div className="flex gap-1 items-center">
                <input
                  type="text" value={manualModel}
                  onChange={e => setManualModel(e.target.value)}
                  placeholder={currentModel || 'gpt-4o'}
                  className="flex-1 text-sm mono"
                />
                <button className="secondary text-xs" onClick={() => { if (manualModel.trim()) pickModel.mutate(manualModel.trim()) }}
                  disabled={!manualModel.trim() || pickModel.isPending}>Save</button>
              </div>
            )}
            {currentModel && <span className="text-2xs text-muted mono">current: {currentModel}</span>}
          </div>
          <div className="flex gap-2 items-center">
            <button className="secondary text-xs" onClick={() => setDefault.mutate()} disabled={setDefault.isPending}>Set as default</button>
            <button className="secondary text-xs" onClick={() => { if (confirm('Disconnect OpenAI OAuth?')) disconnect.mutate() }}
              disabled={disconnect.isPending} style={{ color: 'var(--red)' }}>Disconnect</button>
            <p className="hint m-0">Auto-refreshes before expiry.</p>
          </div>
        </div>
      ) : oauthState?.state === 'awaiting' ? (
        <div className="text-sm">
          <p>Waiting for authorization in browser...</p>
          <p className="hint">Complete login at auth.openai.com, then return here.</p>
          <button className="secondary mt-2" onClick={() => cancel.mutate()}>Cancel</button>
        </div>
      ) : oauthState?.state === 'failed' ? (
        <div>
          <p className="hint"><span className="badge badge-red">failed</span> {oauthState.error}</p>
          <button onClick={() => start.mutate()} disabled={start.isPending} className="mt-2">Retry</button>
        </div>
      ) : (
        <div>
          <p className="text-sm text-muted mb-2">
            Sign in with your ChatGPT Plus or Pro account via OAuth. Availability depends on your subscription entitlement.
          </p>
          <button onClick={() => start.mutate()} disabled={start.isPending}>Connect ChatGPT</button>
          <p className="hint mt-2">If this fails, use an API key via OpenAI Compatible provider instead.</p>
        </div>
      )}
    </div>
  )
}

// ─── Claude OAuth panel (Claude Max/Pro subscription) ───

function ClaudeOAuthPanel({ config, health, qc }: { config: any; health: any; qc: any }) {
  const [oauthState, setOauthState] = useState<{ state: string; auth_url?: string | null; error?: string | null } | null>(null)
  const [code, setCode] = useState('')
  const [manualModel, setManualModel] = useState('')

  const start = useMutation({
    mutationFn: api.claudeOAuthStart,
    onSuccess: r => {
      setOauthState(r)
      if (r.auth_url) window.open(r.auth_url, '_blank')
    },
  })
  const complete = useMutation({
    mutationFn: () => api.claudeOAuthComplete({ code: code.trim() }),
    onSuccess: r => {
      setOauthState(r)
      if (r.state === 'completed') {
        qc.invalidateQueries({ queryKey: ['model-health'] })
        qc.invalidateQueries({ queryKey: ['model'] })
      }
    },
  })
  const cancel = useMutation({ mutationFn: api.claudeOAuthCancel, onSuccess: () => { setOauthState(null); setCode('') } })
  const pickModel = useMutation({
    mutationFn: (slug: string) => api.setModelProvider({ provider: 'anthropic_native', model: slug, enabled: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['model-health'] }) },
  })
  const setDefault = useMutation({
    mutationFn: () => api.setModelProvider({ provider: 'anthropic_native', default: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
  })
  const disconnect = useMutation({
    mutationFn: api.claudeOAuthDisconnect,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['model-health'] }) },
  })

  const available = health?.available
  const models: string[] = health?.models || []
  const modelMeta: Array<{ slug: string; display_name?: string; tier?: string; context_window?: number }> = health?.model_metadata || []
  const currentModel = config?.model && config.model !== 'default' ? config.model : null

  return (
    <div className="pt-3">
      {available ? (
        <div>
          <div className="flex gap-2 items-center mb-2">
            <span className="badge badge-green">connected</span>
            <span className="text-xs text-muted">via Claude OAuth (experimental)</span>
          </div>
          <div className="mb-2">
            <label className="text-xs text-muted mb-1" style={{ display: 'block' }}>Model</label>
            {models.length > 0 ? (
              <select
                value={currentModel || ''}
                onChange={e => pickModel.mutate(e.target.value)}
                className="w-full text-sm mono"
              >
                {!currentModel && <option value="">Select a model</option>}
                {modelMeta.length > 0
                  ? modelMeta.map(m => (
                    <option key={m.slug} value={m.slug}>
                      {m.display_name || m.slug}{m.tier || m.context_window ? ' — ' : ''}{m.tier || ''}{m.tier && m.context_window ? ' · ' : ''}{m.context_window ? `${Math.round(m.context_window / 1000)}K ctx` : ''}
                    </option>
                  ))
                  : models.map(m => <option key={m} value={m}>{m}</option>)
                }
              </select>
            ) : (
              <div className="flex gap-1 items-center">
                <input
                  type="text" value={manualModel}
                  onChange={e => setManualModel(e.target.value)}
                  placeholder={currentModel || 'claude-sonnet-4-6'}
                  className="flex-1 text-sm mono"
                />
                <button className="secondary text-xs" onClick={() => { if (manualModel.trim()) pickModel.mutate(manualModel.trim()) }}
                  disabled={!manualModel.trim() || pickModel.isPending}>Save</button>
              </div>
            )}
            {currentModel && <span className="text-2xs text-muted mono">current: {currentModel}</span>}
          </div>
          <div className="flex gap-2 items-center">
            <button className="secondary text-xs" onClick={() => setDefault.mutate()} disabled={setDefault.isPending}>Set as default</button>
            <button className="secondary text-xs" onClick={() => { if (confirm('Disconnect Claude OAuth?')) disconnect.mutate() }}
              disabled={disconnect.isPending} style={{ color: 'var(--red)' }}>Disconnect</button>
            <p className="hint m-0">Auto-refreshes before expiry.</p>
          </div>
        </div>
      ) : oauthState?.state === 'awaiting_code' ? (
        <div className="text-sm">
          <p className="mb-1">1. Complete login at claude.ai in your browser</p>
          <p className="mb-1">2. Copy the authorization code shown on the page</p>
          <p className="mb-2">3. Paste it below:</p>
          <div className="flex gap-1 items-center">
            <input
              type="text" value={code} onChange={e => setCode(e.target.value)}
              placeholder="Paste authorization code"
              className="flex-1 mono text-sm"
            />
            <button onClick={() => complete.mutate()} disabled={!code.trim() || complete.isPending}>Submit</button>
          </div>
          <button className="secondary mt-2" onClick={() => cancel.mutate()}>Cancel</button>
        </div>
      ) : oauthState?.state === 'failed' ? (
        <div>
          <p className="hint"><span className="badge badge-red">failed</span> {oauthState.error}</p>
          <button onClick={() => start.mutate()} disabled={start.isPending} className="mt-2">Retry</button>
        </div>
      ) : (
        <div>
          <p className="text-sm text-muted mb-2">
            Sign in with your Claude Max or Pro account via OAuth. Availability depends on your subscription entitlement.
          </p>
          <button onClick={() => start.mutate()} disabled={start.isPending}>Connect Claude</button>
          <p className="hint mt-2">If this fails, use an API key via Anthropic Compatible provider instead.</p>
        </div>
      )}
    </div>
  )
}
