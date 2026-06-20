import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import ConnectionStatus, { ConnState } from '../components/ConnectionStatus'
import PageHeader from '../components/PageHeader'
import {
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
const ALL_PROVIDERS = ['openai_compatible', 'anthropic_compatible', 'anthropic_native', 'openai_native', 'claude_cli', 'codex_cli'] as const
const HTTP_PROVIDERS = new Set(['openai_compatible', 'anthropic_compatible', 'anthropic_native', 'openai_native'])
const NATIVE_PROVIDERS = new Set(['anthropic_native', 'openai_native'])
const PROVIDER_LABELS: Record<string, string> = {
  openai_compatible: 'OpenAI Compatible',
  anthropic_compatible: 'Anthropic Compatible',
  anthropic_native: 'Claude OAuth (Experimental)',
  openai_native: 'OpenAI OAuth (Experimental)',
  codex_cli: 'Codex CLI',
  claude_cli: 'Claude Code CLI',
}
const OAUTH_PROVIDERS = new Set(['anthropic_native', 'openai_native'])
const CONFIDENCE_LEVELS = ['high', 'medium', 'speculative'] as const

type Tab = 'agent' | 'execution' | 'risk' | 'health' | 'runtime' | 'advanced'
const TABS: { id: Tab; label: string; description: string }[] = [
  { id: 'agent',     label: 'Agent & Models',      description: 'Autonomy, scheduler, and model providers' },
  { id: 'execution', label: 'Execution',           description: 'Exchange, credentials, live state, and paper fills' },
  { id: 'risk',      label: 'Risk & Universe',     description: 'Risk limits, tradable symbols, and universe board' },
  { id: 'health',    label: 'Health & Alerts',     description: 'Self-heal, startup gates, and notifications' },
  { id: 'runtime',   label: 'Runtime & System',    description: 'App shell, updates, autostart, retention, and network' },
  { id: 'advanced',  label: 'Advanced',            description: 'Read-only configuration coverage and diagnostics' },
]

const TAB_ALIASES: Record<string, Tab> = {
  intelligence: 'agent',
  infrastructure: 'runtime',
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
  const [tab, setTab] = useState<Tab>(() => {
    return parseTab(searchParams.get('tab')) ?? 'agent'
  })
  const dirtyRef = useRef<Set<string>>(new Set())
  const registerDirty = (id: string, isDirty: boolean) => {
    if (isDirty) dirtyRef.current.add(id)
    else dirtyRef.current.delete(id)
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

      <div className="settings-shell">
        <aside className="settings-nav" aria-label="Settings sections">
          {TABS.map(t => (
            <button key={t.id} className={`settings-nav-item ${tab === t.id ? 'active' : ''}`} onClick={() => selectTab(t.id)}>
              <span className="settings-nav-label">{t.label}</span>
              <span className="settings-nav-desc">{t.description}</span>
            </button>
          ))}
        </aside>
        <div className="settings-panel">
          {tab === 'agent'     && <AgentModelsTab        status={status} settings={settings} qc={qc} />}
          {tab === 'execution' && <ExecutionTab          status={status} settings={settings} qc={qc} onDirty={registerDirty} />}
          {tab === 'risk'      && <RiskTab               settings={settings} qc={qc} onDirty={registerDirty} />}
          {tab === 'health'    && <HealthNotificationsTab settings={settings} qc={qc} />}
          {tab === 'runtime'   && <RuntimeSystemTab      settings={settings} qc={qc} onDirty={registerDirty} />}
          {tab === 'advanced'  && <AdvancedTab           settings={settings} />}
        </div>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// INTELLIGENCE TAB — agent behaviour, permission, autonomy, scheduler
// ─────────────────────────────────────────────────────────────────────────────

function AgentModelsTab({ status, settings, qc }: any) {
  const agent = settings?.agent as any || {}
  const runtime = settings?.runtime as any || {}

  const permMut = useMutation({
    mutationFn: (mode: string) => api.setPermissionMode(mode),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['status'] }); qc.invalidateQueries({ queryKey: ['settings'] }) },
  })
  const autoLearnMut = useMutation({
    mutationFn: (enabled: boolean) => api.setAutonomousLearning(enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })
  const runtimeMut = useMutation({
    mutationFn: (body: any) => api.updateRuntimeConfig(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  const perm = String(status.permission_mode || agent.permission_mode || '')
  const autoLearn = !!agent.autonomous_learning
  const schedulerEnabled = runtime.scheduler_enabled !== false

  return (
    <div>
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
    { key: 'event_driven_enabled', label: 'Event-driven design' },
    { key: 'walk_forward_enabled', label: 'Walk-forward validation' },
  ]

  const numFields: NumField[] = [
    { key: 'sweep_max_variants', label: 'Sweep max variants', unit: '', min: 10, max: 500, step: 10, isInt: true },
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
  }, [settings])

  const mut = useMutation({
    mutationFn: () => {
      const fqParsed: Record<string, number> = {}
      for (const [k, v] of Object.entries(familyQuota)) fqParsed[k] = parseFloat(v) || 0
      return api.updateUniverseConfig({
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
      <div className="flex items-center gap-3 mb-3">
        <Toggle checked={enabled} onChange={v => { setEnabled(v); setChanged(true); setMsg(null) }} />
        {!enabled && <span className="hint m-0">Agents use static symbol list (BTC/ETH/SOL) when disabled.</span>}
      </div>
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
      <div className="flex items-center gap-2">
        <button onClick={() => {
          const total = Object.values(familyQuota).reduce((s, v) => s + (parseFloat(v) || 0), 0)
          if (Math.abs(total - 1.0) > 0.001) {
            setMsg({ ok: false, text: `Family quota must sum to 1.0 (currently ${total.toFixed(3)})` })
            return
          }
          mut.mutate()
        }} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save'}</button>
      </div>
      <SaveMsg msg={msg} />
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

function ExecutionTab({ status, settings, qc, onDirty }: any) {
  return (
    <div>
      <Section
        title="Exchange Connection"
        description="Configure exchange API credentials. Select an exchange and profile, then enter your credentials."
      >
        <ExchangeConnectionCard qc={qc} />
      </Section>

      <Section title="Trading Mode" description="Switch between demo (paper trading) and live execution. Live requires explicit unlock below.">
        <ExchangeModeRow status={status} qc={qc} />
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
        title="Market Data Sources"
        description="Primary and fallback exchanges for OHLCV, orderbook, and ticker data. Agents use CCXT for all market queries."
      >
        <MarketDataCard settings={settings} qc={qc} onDirty={onDirty} />
      </Section>
    </div>
  )
}

function MarketDataCard({ settings, qc, onDirty }: any) {
  const md = settings?.market_data || {}
  const EXCHANGES = ['binance', 'bybit', 'okx', 'bitget', 'gate', 'kucoin', 'htx']

  const [primary, setPrimary] = useState(md.ccxt_exchange || 'binance')
  const [fallback, setFallback] = useState(md.ccxt_fallback || 'bybit')
  const [cacheTtl, setCacheTtl] = useState(md.ohlcv_cache_ttl_min ?? 10)
  const [yfinance, setYfinance] = useState(!!md.enable_yfinance_macro)
  const [stocks, setStocks] = useState(listToCsv(md.observed_stocks))
  const [xstocks, setXstocks] = useState(listToCsv(md.observed_xstocks))
  const [predMarkets, setPredMarkets] = useState(listToCsv(md.observed_prediction_markets))
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    setPrimary(md.ccxt_exchange || 'binance')
    setFallback(md.ccxt_fallback || 'bybit')
    setCacheTtl(md.ohlcv_cache_ttl_min ?? 10)
    setYfinance(!!md.enable_yfinance_macro)
    setStocks(listToCsv(md.observed_stocks))
    setXstocks(listToCsv(md.observed_xstocks))
    setPredMarkets(listToCsv(md.observed_prediction_markets))
  }, [settings])

  const mut = useMutation({
    mutationFn: () => api.updateMarketDataConfig({
      ccxt_exchange: primary,
      ccxt_fallback: fallback,
      ohlcv_cache_ttl_min: cacheTtl,
      enable_yfinance_macro: yfinance,
      observed_stocks: parseCsvList(stocks),
      observed_xstocks: parseCsvList(xstocks),
      observed_prediction_markets: parseCsvList(predMarkets),
    }),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const dirty = primary !== (md.ccxt_exchange || 'binance')
    || fallback !== (md.ccxt_fallback || 'bybit')
    || cacheTtl !== (md.ohlcv_cache_ttl_min ?? 10)
    || yfinance !== !!md.enable_yfinance_macro
    || stocks !== listToCsv(md.observed_stocks)
    || xstocks !== listToCsv(md.observed_xstocks)
    || predMarkets !== listToCsv(md.observed_prediction_markets)

  useEffect(() => { onDirty?.('market-data', dirty) }, [dirty])

  return (
    <>
      <Row label="Primary exchange" hint="CCXT exchange ID used for OHLCV and ticker data. Must match a supported CCXT exchange.">
        <select value={primary} onChange={e => { setPrimary(e.target.value); setMsg(null) }} className="w-auto">
          {EXCHANGES.map(ex => <option key={ex} value={ex}>{ex}</option>)}
        </select>
      </Row>
      <Row label="Fallback exchange" hint="Used when the primary exchange fails or returns stale data.">
        <select value={fallback} onChange={e => { setFallback(e.target.value); setMsg(null) }} className="w-auto">
          {EXCHANGES.map(ex => <option key={ex} value={ex}>{ex}</option>)}
        </select>
      </Row>
      <Row label="OHLCV cache TTL" hint="Minutes to cache candlestick data before re-fetching. Lower = fresher data, higher = fewer API calls.">
        <input type="number" min={1} max={60} value={cacheTtl}
          onChange={e => { setCacheTtl(Math.max(1, Math.min(60, parseInt(e.target.value) || 1))); setMsg(null) }}
          style={{ width: '80px' }} />
        <span className="hint">min</span>
      </Row>
      <Row label="Yahoo Finance macro" hint="Enable macro indicator collection (DXY, VIX, S&P 500) via yfinance. Requires network access to Yahoo.">
        <Toggle checked={yfinance} label="" onChange={v => { setYfinance(v); setMsg(null) }} />
      </Row>
      <Row label="Observed stocks" hint="Yahoo Finance tickers for macro correlation. Comma-separated (e.g. SPY, QQQ, NVDA).">
        <input value={stocks} placeholder="SPY, QQQ, NVDA, AAPL"
          onChange={e => { setStocks(e.target.value); setMsg(null) }}
          className="mono w-full" />
      </Row>
      <Row label="Observed xStocks" hint="Bitget ON-series tokenized stocks (e.g. AAPLON/USDT). Comma-separated.">
        <input value={xstocks} placeholder="AAPLON/USDT, TSLAON/USDT"
          onChange={e => { setXstocks(e.target.value); setMsg(null) }}
          className="mono w-full" />
      </Row>
      <Row label="Prediction markets" hint="Polymarket category slugs to track. Comma-separated.">
        <input value={predMarkets} placeholder="crypto, politics"
          onChange={e => { setPredMarkets(e.target.value); setMsg(null) }}
          className="mono w-full" />
      </Row>
      {dirty && (
        <div className="flex justify-end" style={{ padding: '0.5rem 0' }}>
          <button onClick={() => mut.mutate()} disabled={mut.isPending}>
            {mut.isPending ? 'Saving…' : 'Save market data config'}
          </button>
        </div>
      )}
      <SaveMsg msg={msg} />
    </>
  )
}

function ExchangeModeRow({ status, qc }: any) {
  const mut = useMutation({
    mutationFn: (mode: string) => api.setExchangeMode(mode),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })
  return (
    <Row label="Exchange mode" hint="demo uses Bitget paper trading; live requires unlocking below and real credentials.">
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
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    setSpot(!!markets.spot)
    setFutures(!!markets.usdt_futures)
    setPaperMode(serverPaperMode)
  }, [settings])

  const mut = useMutation({
    mutationFn: () => api.setExchangeMarkets({ spot, usdt_futures: futures, paper_execution_mode: paperMode }),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
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
        <select value={paperMode} onChange={e => { setPaperMode(e.target.value); setMsg(null) }} className="w-auto">
          <option value="okx_demo">OKX Demo</option>
          <option value="binance_testnet">Binance Testnet</option>
          <option value="bitget_demo">Bitget Demo</option>
          <option value="paper_internal">Paper (internal)</option>
        </select>
        {dirty && (
          <button className="secondary" onClick={() => mut.mutate()} disabled={mut.isPending || (!spot && !futures)}>
            {mut.isPending ? 'Saving…' : 'Save exchange config'}
          </button>
        )}
      </Row>
      <SaveMsg msg={msg} />
    </>
  )
}

function LiveStateRow({ status, qc }: any) {
  const unlock = useMutation({ mutationFn: api.liveUnlock, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const lock = useMutation({ mutationFn: api.liveLock, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const isDisabled = status.live_state === 'live_disabled'
  const [warning, setWarning] = useState('')

  const preflightUnlock = async () => {
    setWarning('')
    let msg = 'Unlock live-limited trading? This enables real orders within hard risk limits.'
    try {
      const r = await api.testBitget('live')
      if (!r.connected) {
        setWarning(`Live credentials not reachable: ${r.error || 'unknown'}. Fix credentials first.`)
        msg = `⚠ Live creds did not connect (${r.error || 'unknown'}). Unlock anyway?`
      }
    } catch { /* preflight failure shouldn't block user decision */ }
    if (confirm(msg)) unlock.mutate()
  }

  return (
    <div>
      <Row label="Live trading state" hint="Disabled by default. Unlocking runs a credential pre-flight, requires confirmation, and is audited. Use Kill Switch on Risk & Controls to halt immediately." danger={!isDisabled}>
        <span className={`badge ${isDisabled ? 'badge-muted' : 'badge-yellow'}`}>{humanize(String(status.live_state))}</span>
        <button onClick={preflightUnlock} disabled={!isDisabled}>Unlock Live-Limited</button>
        <button className="secondary" onClick={() => lock.mutate()} disabled={isDisabled}>Lock</button>
      </Row>
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
    setTrailAtrMult(String(trading.trail_atr_mult ?? 1.5))
    setTrailPct(String(trading.trail_pct ?? 1.0))
    setBreakevenTriggerPct(String(trading.breakeven_trigger_pct ?? 1.5))
    setVolSizingEnabled(trading.vol_sizing_enabled !== false)
    setDefaultNotional(String(trading.default_notional_usdt ?? 25))
    setVolRiskUsdt(String(trading.vol_target_risk_usdt ?? 25))
    setVolTargetPct(String(trading.vol_target_pct ?? 1.5))
    setVolKellyCap(String(trading.vol_kelly_cap_fraction ?? 0.25))
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
        <StrategyFactoryCard settings={settings} qc={qc} />
      </Section>

      <Section
        title="Tradable Symbols"
        description="Coins agents are allowed to analyse and trade. Applied to both the risk engine and exchange config atomically."
      >
        <SymbolsCard settings={settings} qc={qc} />
      </Section>

      <Section
        title="Observed US Equities"
        description="US equity tickers for equity_observer (observation-only, not traded). Requires Bitget xStock support for the ticker."
      >
        <EquitiesCard settings={settings} qc={qc} />
      </Section>

      <Section
        title="Prediction Markets"
        description="Polymarket observer — fetches probability data for information-edge signals. Observation-only, never routed to execution."
      >
        <PredictionToggleCard qc={qc} />
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
  { key: 'max_live_position_notional_usdt', label: 'Max position notional', unit: 'USDT', min: 1,    max: 100000, step: 1 },
  { key: 'max_futures_leverage_live',     label: 'Max leverage',            unit: 'x',    min: 1,    max: 20,     step: 1, isInt: true },
  { key: 'max_open_positions_live',       label: 'Max open positions',      unit: '',     min: 1,    max: 50,     step: 1, isInt: true },
]

const RISK_DEMO: NumField[] = [
  { key: 'max_demo_position_notional_usdt', label: 'Max position notional', unit: 'USDT', min: 1,    max: 100000, step: 1 },
  { key: 'max_demo_single_trade_risk_pct',  label: 'Max single trade risk', unit: '%',    min: 0.01, max: 20,     step: 0.01 },
  { key: 'max_futures_leverage_demo',       label: 'Max leverage',          unit: 'x',    min: 1,    max: 125,    step: 1, isInt: true },
  { key: 'max_open_positions_demo',         label: 'Max open positions',    unit: '',     min: 1,    max: 200,    step: 1, isInt: true },
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

function RiskCard({ settings, qc }: any) {
  const risk = settings?.risk as any
  const [vals, setVals] = useState<Record<string, any>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!risk) return
    const init: Record<string, any> = {}
    ;[...RISK_LIVE, ...RISK_DEMO, ...RISK_CORR].forEach(f => { init[f.key] = risk[f.key] })
    RISK_BOOLS.forEach(f => { init[f.key] = risk[f.key] })
    setVals(init)
  }, [settings])

  const set = (k: string, v: any) => { setVals(prev => ({ ...prev, [k]: v })); setMsg(null) }

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
      <NumGrid fields={RISK_LIVE} vals={vals} set={set} />

      {/* Demo limits */}
      <div className="section-label mt-4">Demo / paper limits</div>
      <NumGrid fields={RISK_DEMO} vals={vals} set={set} />

      <ToggleGrid fields={RISK_BOOLS} vals={vals} set={set} />

      {/* Correlation guard thresholds — only shown when guard is enabled */}
      {vals.correlation_guard_enabled && (
        <>
          <div className="section-label mt-4">Correlation guard thresholds</div>
          <NumGrid fields={RISK_CORR} vals={vals} set={set} />
        </>
      )}

      {/* Kill switch trigger conditions — always visible for transparency */}
      <div className="kill-switch-info">
        <div className="kill-switch-info-title">Kill Switch Auto-Trigger Conditions</div>
        <ul>
          <li><strong>Trigger formula:</strong> daily realized PnL &le; &minus;(Live Cap &times; Max Daily Loss %)</li>
          <li><strong>Current threshold:</strong> &minus;{fmtNum((vals.max_live_limited_cap_usdt || 0) * (vals.max_daily_loss_pct || 0) / 100)} USDT</li>
          <li><strong>Scope:</strong> applies to live trades when "Kill switch auto-trigger (live)" is ON; applies to demo trades when "Kill switch auto-trigger (demo)" is ON</li>
          <li><strong>Effect:</strong> once tripped, ALL proposals (live + demo) are rejected until manually released</li>
          <li><strong>Reset:</strong> release from Risk Controls panel or CLI after UTC new day</li>
        </ul>
      </div>

      <div className="settings-action-row">
        <button onClick={() => mut.mutate()} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save risk limits'}</button>
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
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// RUNTIME & SYSTEM TAB — display, app, retention, updates, autostart, network
// ─────────────────────────────────────────────────────────────────────────────

function RuntimeSystemTab({ settings, qc, onDirty }: any) {
  return (
    <div>
      <Section title="Display" description="Visual formatting applied across the dashboard.">
        <DisplayRow />
      </Section>

      <Section title="App" description="Dashboard process settings.">
        <AppRows settings={settings} qc={qc} />
      </Section>

      <Section title="Data Retention" description="How long telemetry is kept before automatic pruning. Trust-bearing records (trades, audit log) are never pruned.">
        <RetentionCard settings={settings} qc={qc} />
      </Section>

      <Section title="Software Updates" description="On-demand self-upgrade via git pull. Never runs automatically.">
        <UpdateCard settings={settings} qc={qc} />
      </Section>

      <Section title="Autostart" description="Start the gateway automatically when you log in to Windows.">
        <AutostartCard qc={qc} />
      </Section>

      <Section title="Network Proxy" description="Route all outbound HTTP through a proxy. Required when direct internet access is unavailable (e.g. Task Scheduler-started gateway).">
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
      <Row label="Enable autostart on gateway start" hint="When enabled, gateway start attempts to register the Windows watchdog/autostart task.">
        <Toggle checked={runtime.autostart_on_start !== false} onChange={v => safeRuntimeMut({ autostart_on_start: v })} disabled={runtimeMut.isPending} />
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
    'agent.permission_mode', 'agent.autonomous_learning',
    'runtime.scheduler_enabled', 'runtime.git_enabled', 'runtime.auto_reload', 'runtime.autostart_on_start',
    'runtime.runtime_continuity_enabled', 'runtime.runtime_continuity_lock_ttl_sec', 'runtime.runtime_continuity_shadow_port_offset',
    'app.auto_open_browser',
    'exchange.mode', 'exchange.enabled_markets.spot', 'exchange.enabled_markets.usdt_futures', 'exchange.paper_execution_mode',
    'live.state',
    'risk.symbol_filter_mode', 'risk.symbol_list',
    ...RISK_LIVE.map(f => `risk.${f.key}`),
    ...RISK_DEMO.map(f => `risk.${f.key}`),
    ...RISK_BOOLS.map(f => `risk.${f.key}`),
    ...RISK_CORR.map(f => `risk.${f.key}`),
    'trading.default_stop_loss_pct', 'trading.default_take_profit_pct', 'trading.max_position_hold_hours',
    'trading.slippage_bps', 'trading.execution_fee_pct', 'trading.min_confidence_to_trade', 'trading.auto_adopt_enabled',
    'trading.trail_enabled', 'trading.trail_atr_mult', 'trading.trail_pct', 'trading.breakeven_trigger_pct', 'trading.exchange_tpsl_enabled',
    'trading.default_notional_usdt', 'trading.vol_sizing_enabled', 'trading.vol_target_risk_usdt', 'trading.vol_target_pct', 'trading.vol_kelly_cap_fraction',
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
    'universe.market_type', 'universe.family_quota',
    'tunnel.enabled', 'tunnel.tunnel_id', 'tunnel.hostname',
    'market_data.ccxt_exchange', 'market_data.ccxt_fallback', 'market_data.ohlcv_cache_ttl_min', 'market_data.enable_yfinance_macro',
    'market_data.observed_stocks', 'market_data.observed_xstocks', 'market_data.observed_prediction_markets',
    'network.proxy_url', 'network.no_proxy',
  ])
  const readOnlyPrefixes = [
    'app.', 'runtime.paused', 'runtime.kill_switch_active',
    'operator.', 'guard.', 'update.last_', 'update.current_commit',
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
  ]
  const HEALTH_TOGGLES: BoolField[] = [
    { key: 'enabled', label: 'Health monitoring' },
    { key: 'alert_via_telegram', label: 'Telegram alerts' },
    { key: 'alert_via_windows_toast', label: 'Windows toast alerts' },
    { key: 'require_llm_at_startup', label: 'Require LLM at startup' },
    { key: 'self_heal_enabled', label: 'Self-heal enabled' },
    { key: 'self_heal_code_drift', label: 'Restart on code drift' },
    { key: 'self_heal_market_data', label: 'Restart on stale market data' },
    { key: 'self_heal_port_cleanup', label: 'Clean orphan port holders' },
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

// ─── Autostart ───

function AutostartCard({ qc }: any) {
  const { data, isLoading } = useQuery({ queryKey: ['autostart'], queryFn: api.getAutostartStatus })
  const [msg, setMsg] = useState('')
  const mut = useMutation({
    mutationFn: (enabled: boolean) => api.setAutostart(enabled),
    onSuccess: (r) => {
      setMsg(r.ok === false ? `Failed: ${r.reason}${r.detail ? ` — ${r.detail}` : ''}` : r.enabled ? 'Enabled.' : 'Disabled.')
      qc.invalidateQueries({ queryKey: ['autostart'] })
    },
  })

  const supported = data?.supported
  const enabled = data?.enabled

  let badge = <span className="badge">checking…</span>
  if (!isLoading && data) {
    if (!supported) badge = <span className="badge badge-yellow">Not Available</span>
    else if (enabled === true) badge = <span className="badge badge-green">Enabled</span>
    else if (enabled === false) badge = <span className="badge badge-muted">Disabled</span>
    else badge = <span className="badge badge-red">Unknown</span>
  }

  return (
    <div className="card mb-0">
      <div className="flex items-center gap-3 flex-wrap">
        {badge}
        {supported === false ? (
          <span className="hint m-0">Not available{data?.reason ? ` (${data.reason})` : ''}. Uses Windows Task Scheduler.</span>
        ) : (
          <>
            {enabled === true && <button className="secondary" onClick={() => mut.mutate(false)} disabled={mut.isPending}>Disable</button>}
            {enabled === false && <button onClick={() => mut.mutate(true)} disabled={mut.isPending}>Enable</button>}
            {enabled === null && <span className="hint" style={{ color: 'var(--yellow)', margin: 0 }}>Could not read task state{data?.reason ? ` (${data.reason})` : ''}</span>}
            <button className="secondary" onClick={() => qc.invalidateQueries({ queryKey: ['autostart'] })}>Re-check</button>
          </>
        )}
        {msg && <span className="hint m-0">{msg}</span>}
      </div>
      <p className="hint mt-2">Starts the gateway on login and watchdogs it every 5 min. Runs gated start — live trading stays disabled.</p>
    </div>
  )
}

function NetworkCard({ settings, qc }: any) {
  const net = (settings?.network as any) || {}
  const [proxyUrl, setProxyUrl] = useState<string>(net.proxy_url || '')
  const [noProxy, setNoProxy] = useState<string>(net.no_proxy || '')
  const [testResult, setTestResult] = useState<{ reachable: boolean; latency_ms: number; error: string | null } | null>(null)
  const [testing, setTesting] = useState(false)

  // Sync from server state when settings load
  useEffect(() => {
    setProxyUrl(net.proxy_url || '')
    setNoProxy(net.no_proxy || '')
  }, [net.proxy_url, net.no_proxy])

  const mut = useMutation({
    mutationFn: (body: any) => api.updateNetworkConfig(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  const handleSave = () => {
    mut.mutate({ proxy_url: proxyUrl.trim(), no_proxy: noProxy.trim() })
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

  const isConfigured = !!proxyUrl.trim()
  let statusDot = <span style={{ color: 'var(--muted)', fontSize: '0.75rem' }}>●</span>
  if (isConfigured && testResult?.reachable) statusDot = <span style={{ color: 'var(--green)', fontSize: '0.75rem' }}>●</span>
  else if (isConfigured && testResult && !testResult.reachable) statusDot = <span style={{ color: 'var(--red)', fontSize: '0.75rem' }}>●</span>

  return (
    <div className="flex flex-col gap-2">
      <Row label="Proxy URL" hint="Applied to all outbound HTTP (LLM, exchange, market data). Leave empty for direct connection.">
        <div className="flex gap-2 items-center">
          {statusDot}
          <input
            type="text" value={proxyUrl}
            onChange={e => setProxyUrl(e.target.value)}
            placeholder="http://127.0.0.1:7897"
            className="mono text-sm" style={{ width: '220px' }}
          />
        </div>
      </Row>
      <Row label="No Proxy" hint="Comma-separated hosts to bypass (e.g. 127.0.0.1,localhost).">
        <input
          type="text" value={noProxy}
          onChange={e => setNoProxy(e.target.value)}
          placeholder="127.0.0.1,localhost"
          className="mono text-sm" style={{ width: '220px' }}
        />
      </Row>
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
  const { data: health = [] } = useQuery({ queryKey: ['model-health'], queryFn: api.getModelHealth, refetchInterval: 5000 })
  const [expanded, setExpanded] = useState<string | null>(null)

  if (!data) return <p className="hint">Loading providers…</p>

  return (
    <div className="flex flex-col gap-2">
      <ModelPingButton />
      {ALL_PROVIDERS.map(p => (
        <ProviderCard
          key={p}
          name={p}
          config={data.providers?.[p]}
          health={(health as any[]).find(x => x.provider === p)}
          allHealth={health as any[]}
          isDefault={p === data.default_provider}
          isExpanded={expanded === p}
          onToggle={() => setExpanded(expanded === p ? null : p)}
          qc={qc}
        />
      ))}
    </div>
  )
}

function ModelPingButton() {
  const ping = useMutation({ mutationFn: () => api.pingModel() })
  const result = ping.data
  return (
    <div className="model-ping-row">
      <button className="secondary" onClick={() => ping.mutate()} disabled={ping.isPending}>
        {ping.isPending ? 'Testing…' : 'Test Default Model'}
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

function ProviderCard({ name, config, health, allHealth, isDefault, isExpanded, onToggle, qc }: {
  name: string; config: any; health: any; allHealth: any[]; isDefault: boolean
  isExpanded: boolean; onToggle: () => void; qc: any
}) {
  const isHTTP = HTTP_PROVIDERS.has(name)
  const isOAuth = OAUTH_PROVIDERS.has(name)
  const isCLI = !isHTTP
  const enabled = config?.enabled !== false

  const toggleEnabled = useMutation({
    mutationFn: (next: boolean) => api.setModelProvider({ provider: name, enabled: next }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['model'] })
      qc.invalidateQueries({ queryKey: ['model-health'] })
      qc.invalidateQueries({ queryKey: ['status'] })
    },
  })

  // Status resolution
  let statusColor: string = 'var(--muted)'
  let statusText = 'not configured'
  const keyConfigured = isHTTP && config?.api_key?.configured
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

  // Summary line
  const details: string[] = []
  if (isHTTP && !NATIVE_PROVIDERS.has(name) && config?.base_url) details.push(config.base_url)
  if (config?.model && config.model !== 'default') details.push(`model: ${config.model}`)
  if (isHTTP && config?.api_key?.configured) details.push(`key: ··${config.api_key.last4}`)
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
            <HTTPProviderForm name={name} config={config} isDefault={isDefault} qc={qc} />
          ) : name === 'codex_cli' ? (
            <CodexCLIPanel config={config} health={health} qc={qc} />
          ) : (
            <ClaudeCLIPanel config={config} health={health} isDefault={isDefault} qc={qc} />
          )}
        </div>
      )}
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

function HTTPProviderForm({ name, config, isDefault, qc }: {
  name: string; config: any; isDefault: boolean; qc: any
}) {
  const [baseUrl, setBaseUrl] = useState(() => config?.base_url || '')
  const [model, setModel] = useState(() => config?.model || '')
  const [protocol, setProtocol] = useState(() => config?.protocol || 'chat_completions')
  const [authScheme, setAuthScheme] = useState(() => config?.auth_scheme || 'x-api-key')
  const [apiKey, setApiKey] = useState('')
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null)
  const [detectedModels, setDetectedModels] = useState<{ slug: string; display_name: string; tier?: string; context_window?: number }[] | null>(null)
  const [modelFocused, setModelFocused] = useState(false)
  const [modelEditing, setModelEditing] = useState(false)

  const isNative = NATIVE_PROVIDERS.has(name)
  const isAnthropic = name === 'anthropic_compatible'
  const isOpenAI = name === 'openai_compatible'

  const detectMut = useMutation({
    mutationFn: async () => {
      const body: any = { provider: name }
      if (!isNative) body.base_url = baseUrl || undefined
      if (apiKey) body.api_key = apiKey
      if (isAnthropic) body.auth_scheme = authScheme
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
    if (config?.api_key?.configured) detectMut.mutate()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const verifyAndSave = useMutation({
    mutationFn: async () => {
      setResult(null)
      const testBody: any = {
        provider: name,
        model: model || undefined,
        api_key: apiKey || undefined,
        probe: true,
      }
      if (!isNative) testBody.base_url = baseUrl || undefined
      if (isOpenAI) testBody.protocol = protocol
      if (isAnthropic) testBody.auth_scheme = authScheme

      const test = await api.testModelCandidate(testBody)
      if (!test.available) throw new Error(test.error || 'provider unreachable')

      const saveBody: any = {
        provider: name,
        enabled: true,
        ...(model ? { model } : {}),
        ...(apiKey ? { api_key: apiKey } : {}),
      }
      if (!isNative && baseUrl) saveBody.base_url = baseUrl
      if (isOpenAI) saveBody.protocol = protocol
      if (isAnthropic) saveBody.auth_scheme = authScheme

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
    mutationFn: () => api.setModelProvider({ provider: name, enabled: true, default: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
  })

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
      {isOpenAI && (
        <label>
          Protocol
          <select value={protocol} onChange={e => { setProtocol(e.target.value); setResult(null) }}>
            <option value="chat_completions">chat_completions</option>
            <option value="responses">responses</option>
          </select>
        </label>
      )}
      {isAnthropic && (
        <label>
          Auth Scheme
          <select value={authScheme} onChange={e => { setAuthScheme(e.target.value); setResult(null); setDetectedModels(null) }}>
            <option value="x-api-key">X-API-Key (Native Anthropic)</option>
            <option value="bearer">Bearer (Gateway / Databricks)</option>
          </select>
        </label>
      )}
      {!isOpenAI && !isAnthropic && !isNative && <div />}

      {/* Row 3: API Key — full width */}
      <label className="col-full">
        API Key
        {config?.api_key?.configured && <span className="badge badge-green" style={{ marginLeft: '0.4rem', fontSize: '0.62rem' }}>··{config.api_key.last4}</span>}
        <input type="password" value={apiKey} placeholder={config?.api_key?.configured ? '•••••• (leave blank to keep)' : 'paste key'} onChange={e => { setApiKey(e.target.value); setResult(null) }} />
      </label>

      {/* Row 4: Actions — full width */}
      <div style={{ gridColumn: '1 / -1', display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        <button onClick={() => verifyAndSave.mutate()} disabled={verifyAndSave.isPending}>
          {verifyAndSave.isPending ? 'Verifying…' : 'Verify & Save'}
        </button>
        {!isDefault && (
          <button className="secondary" onClick={() => setDefault.mutate()} disabled={setDefault.isPending}>
            Set as Default
          </button>
        )}
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
