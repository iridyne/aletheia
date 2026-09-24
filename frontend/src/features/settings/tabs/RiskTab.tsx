import { useEffect, useId, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import {
  CollapsibleSection as Collapsible,
  NumberFieldGrid as NumGrid,
  SaveMessage as SaveMsg,
  SettingsRow as Row,
  Switch as Toggle,
  SwitchGrid as ToggleGrid,
  type BoolField,
  type NumField,
  useConfirm,
} from '../../../components/ui'
import { fmtNum, humanize, humanizeBool } from '../../../lib/format'
import { listToCsv, parseCsvList } from '../csvList'
import { useSettingsDraftLifecycle } from '../draftLifecycle'
import {
  RISK_BOOLS, RISK_CORR, RISK_DEMO, RISK_FAMILY_GATE, RISK_FUTURES_DEMO, RISK_FUTURES_LIVE,
  RISK_LIVE, RISK_MARGIN, RISK_POLICY_BOOLS, RISK_POLICY_LIVE_FIELDS, RISK_POLICY_NUM_FIELDS,
  RISK_POLICY_PATHS, RISK_SHARED, type RiskPolicyPath,
} from '../risk/riskFields'
import { SettingsFeatureSection, type SettingsFeatureDescriptor } from '../SettingsFeatureSection'
import type { DeepPartial, SettingsRiskConfig, SettingsRiskPolicy, components } from '../../../api/settings'
import type { SettingsDirtyProps, SettingsProps } from '../types'

export const UNIVERSE_SECTION = {
  id: 'universe', title: 'Universe & Candidate Attention', tab: 'risk',
  description: 'Controls Universe selection plus the separate soft and hard Candidate attention bounds.',
  searchTerms: ['scanner', 'tradable universe', 'candidate attention', 'soft lease', 'hard age'],
} satisfies SettingsFeatureDescriptor

export const EVENT_TRIGGERS_SECTION = {
  id: 'event-triggers', title: 'Event Triggers', tab: 'risk',
  description: 'Real-time price/volume breakout detection. Fires notifications and wakes the System Operator when thresholds are breached.',
  searchTerms: ['threshold', 'fire conditions'],
} satisfies SettingsFeatureDescriptor

export const TRADABLE_SYMBOLS_SECTION = {
  id: 'tradable-symbols', title: 'Tradable Symbols', tab: 'risk',
  description: 'Coins agents are allowed to analyse and trade. Applied to both the risk engine and exchange config atomically.',
  searchTerms: ['allowlist', 'symbol filter'],
} satisfies SettingsFeatureDescriptor

export const RISK_LIMITS_SECTION = {
  id: 'risk-limits', title: 'Risk Limits', tab: 'risk',
  description: 'Hard limits enforced by the Risk Engine on every order evaluation. Changes are audited and take effect on the next agent cycle.',
  searchTerms: ['notional cap', 'leverage', 'daily loss', 'margin ratio', 'kill switch', 'force close'],
} satisfies SettingsFeatureDescriptor

// ─── Event Triggers config ───

function EventTriggerCard({ settings, qc, onDirty }: SettingsDirtyProps) {
  const et = settings?.event_triggers
  const [vals, setVals] = useState<Record<string, unknown>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const serverVals = et ? {
      enabled: et.enabled ?? true,
      price_move_pct_threshold: et.price_move_pct_threshold ?? 3.0,
      volume_surge_multiplier: et.volume_surge_multiplier ?? 3.0,
      watch_universe_members: et.watch_universe_members ?? true,
  } : {}
  const dirty = !!et && Object.keys(vals).length > 0 && JSON.stringify(vals) !== JSON.stringify(serverVals)
  useSettingsDraftLifecycle({
    id: 'event-triggers',
    dirty,
    onDirty,
    serverKey: et,
    adoptServer: () => { if (et) setVals({ ...serverVals }) },
  })

  const { mutate: save } = useMutation({
    mutationFn: () => api.updateEventTriggerConfig(vals as components['schemas']['EventTriggerConfig']),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); setMsg({ ok: true, text: 'Saved' }) },
    onError: (e: any) => setMsg({ ok: false, text: e.message }),
  })

  if (!et) return null

  const boolFields: BoolField[] = [
    { key: 'enabled', label: 'Enabled' },
    { key: 'watch_universe_members', label: 'Watch universe' },
  ]
  const numFields: NumField[] = [
    { key: 'price_move_pct_threshold', label: 'Price move threshold', unit: '%', min: 0.5, max: 20, step: 0.5, isInt: false },
    { key: 'volume_surge_multiplier', label: 'Volume surge multiplier', unit: '×', min: 1.5, max: 20, step: 0.5, isInt: false },
  ]

  const setBool = (key: string, value: boolean) => setVals(v => ({ ...v, [key]: value }))
  const setNum = (key: string, value: number) => setVals(v => ({ ...v, [key]: value }))

  return (
    <div className="space-y-4">
      <ToggleGrid fields={boolFields} vals={vals} set={setBool} persistence="staged" />
      <NumGrid fields={numFields} vals={vals} set={setNum} />
      <button onClick={() => save()} className="btn btn-primary btn-sm mt-2">Save Event Triggers</button>
      <SaveMsg msg={msg} />
    </div>
  )
}

// ─── Universe config ───

function UniverseConfigCard({ settings, qc, onDirty }: SettingsDirtyProps) {
  const universe = settings?.universe
  const snapshotTtlLabelId = useId()
  const [enabled, setEnabled] = useState(false)
  const [refreshMin, setRefreshMin] = useState('10')
  const [maxCandidates, setMaxCandidates] = useState('30')
  const [minVolume, setMinVolume] = useState('5000000')
  const [maxSpread, setMaxSpread] = useState('25')
  const [minDepth, setMinDepth] = useState('25000')
  const [snapshotTtlEnabled, setSnapshotTtlEnabled] = useState(true)
  const [snapshotTtlHours, setSnapshotTtlHours] = useState('24')
  const [candidateSoftLease, setCandidateSoftLease] = useState('900')
  const [candidateMaxAge, setCandidateMaxAge] = useState('3600')
  const [blacklist, setBlacklist] = useState('')
  const [allowlist, setAllowlist] = useState('')
  const [fallbackSymbols, setFallbackSymbols] = useState('BTC/USDT, ETH/USDT, SOL/USDT')
  const [marketType, setMarketType] = useState('crypto')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [changed, setChanged] = useState(false)

  useSettingsDraftLifecycle({
    id: 'universe-config',
    dirty: changed,
    onDirty,
    serverKey: settings,
    adoptServer: () => {
      if (!universe) return
      setEnabled(!!universe.enabled)
      setRefreshMin(String(universe.refresh_interval_min ?? 10))
      setMaxCandidates(String(universe.max_candidates ?? 30))
      setMinVolume(String(universe.min_volume_24h_usdt ?? 5000000))
      setMaxSpread(String(universe.max_spread_bps ?? 25))
      setMinDepth(String(universe.min_depth_10bps_usd ?? 25000))
      setSnapshotTtlEnabled(universe.snapshot_ttl_enabled ?? true)
      setSnapshotTtlHours(String(universe.snapshot_ttl_hours ?? 24))
      setCandidateSoftLease(String(universe.entry_ttl_sec ?? 900))
      setCandidateMaxAge(String(universe.candidate_max_continuous_age_sec ?? 3600))
      setBlacklist(listToCsv(universe.blacklist))
      setAllowlist(listToCsv(universe.allowlist))
      setFallbackSymbols(listToCsv(universe.fallback_symbols ?? ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']))
      setMarketType(universe.market_type || 'crypto')
    },
  })

  const mut = useMutation({
    mutationFn: () => {
      return api.updateUniverseConfig({
        enabled,
        refresh_interval_min: parseInt(refreshMin),
        max_candidates: parseInt(maxCandidates),
        min_volume_24h_usdt: parseFloat(minVolume),
        max_spread_bps: parseFloat(maxSpread),
        min_depth_10bps_usd: parseFloat(minDepth),
        snapshot_ttl_enabled: snapshotTtlEnabled,
        snapshot_ttl_hours: parseFloat(snapshotTtlHours),
        entry_ttl_sec: parseInt(candidateSoftLease),
        candidate_max_continuous_age_sec: parseInt(candidateMaxAge),
        blacklist: parseCsvList(blacklist),
        allowlist: parseCsvList(allowlist),
        fallback_symbols: parseCsvList(fallbackSymbols),
        market_type: marketType,
      })
    },
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); setChanged(false); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  if (!universe) return null

  return (
    <div className="card mb-0">
      <Row
        label="Enable Universe Scanner"
        hint="When on, dynamically discovers and scores USDT pairs each refresh cycle. When off, agents fall back to the static symbol list (BTC/ETH/SOL)."
      >
        <Toggle checked={enabled} persistence="staged" onChange={v => { setEnabled(v); setChanged(true); setMsg(null) }} />
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
        <div className="settings-field">
          <span id={snapshotTtlLabelId}>Snapshot TTL</span>
          <div className="flex items-center gap-2">
            <Toggle checked={snapshotTtlEnabled} persistence="staged" aria-label="Enable snapshot TTL" onChange={v => { setSnapshotTtlEnabled(v); setChanged(true); setMsg(null) }} />
            <input type="number" min={1} max={72} step={1} value={snapshotTtlHours}
              aria-labelledby={snapshotTtlLabelId}
              disabled={!snapshotTtlEnabled}
              onChange={e => { setSnapshotTtlHours(e.target.value); setChanged(true); setMsg(null) }} />
            <span className="hint m-0">hrs{!snapshotTtlEnabled ? ' (disabled — toggle on to edit)' : ''}</span>
          </div>
        </div>
        <label className="settings-field">
          <span>Candidate soft lease (sec)</span>
          <input type="number" min={300} max={3600} step={60} value={candidateSoftLease}
            onChange={e => { setCandidateSoftLease(e.target.value); setChanged(true); setMsg(null) }} />
          <span className="hint m-0">Extended only by a completed real re-selection.</span>
        </label>
        <label className="settings-field">
          <span>Max continuous Candidate attention (sec)</span>
          <input type="number" min={300} max={86400} step={60} value={candidateMaxAge}
            onChange={e => { setCandidateMaxAge(e.target.value); setChanged(true); setMsg(null) }} />
          <span className="hint m-0">Frozen on admission; must be at least the soft lease.</span>
        </label>
      </div>
      <div className="form-grid mb-3">
        <label>
          <span>Universe allowlist</span>
          <input value={allowlist} placeholder="empty = all symbols"
            onChange={e => { setAllowlist(e.target.value); setChanged(true); setMsg(null) }}
            className="mono" />
          <span className="hint m-0">Empty admits every symbol. Listing any restricts the universe to exactly those.</span>
        </label>
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
      <div className="flex items-center gap-2">
        <button onClick={() => {
          mut.mutate()
        }} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save'}</button>
      </div>
      <SaveMsg msg={msg} />
      {((universe?.scanner_weights && Object.keys(universe.scanner_weights).length > 0) ||
        (universe?.scanner_regime_scoring && Object.keys(universe.scanner_regime_scoring).length > 0)) && (
      <Collapsible title="Legacy scanner internals (historical, read-only)">
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
          <p className="hint m-0">Historical Legacy weights; not used by current Candidate selection.</p>
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
          <p className="hint m-0">Historical Legacy scoring deltas; not current Candidate admission or allocation controls.</p>
        </div>
      )}
      </Collapsible>
      )}
      <p className="hint">
        {`每 ${refreshMin} 分钟检查真实行情与硬门槛，最多选择 ${maxCandidates} 个 Candidate。收益排序：预期净收益；成熟且收益合格者优先，冷启动：信号强度，仅在样本不足时补剩余名额。未知收益不是 0，信号强度不是收益概率。`}
        {' '}
        Interval and TTL changes take effect after gateway restart.
      </p>
    </div>
  )
}

function CandidateSelectionAuthorityCard() {
  const status = useQuery({ queryKey: ['candidate-selection-authority'], queryFn: api.getCandidateSelectionStatus })
  return (
    <div className="card mb-0 mt-3">
      <div className="section-label">Candidate Selection</div>
      {status.isError ? <p className="hint">选择状态不可用</p> : status.isPending ? <p className="hint">正在加载选择状态…</p> : <>
        <p className="hint">Configured policy: <span className="mono">{humanize(status.data.configured_policy)}</span></p>
        <p className="hint">Epoch: <span className="mono">{status.data.configured_policy_epoch ?? '未知'}</span></p>
        <p className="hint">运行选择：{status.data.authority_enabled ? '已启用' : '未启用'}</p>
        <p className="hint">收益排序：预期净收益；冷启动：信号强度。冷启动不代表经济资格通过。Risk 与执行门槛不变。</p>
        {status.data.authority_block_reason && <p className="hint">阻止原因：{humanize(status.data.authority_block_reason)}</p>}
      </>}
    </div>
  )
}

function FastTriggerConfigCard({ settings, qc, onDirty }: SettingsDirtyProps) {
  const server = settings?.fast_trigger
  const [mode, setMode] = useState<'off' | 'shadow' | 'execute'>('shadow')
  const [maxSetups, setMaxSetups] = useState('10')
  const [ttlMinutes, setTtlMinutes] = useState('60')
  const [changed, setChanged] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useSettingsDraftLifecycle({
    id: 'fast-trigger-config',
    dirty: changed,
    onDirty,
    serverKey: settings,
    adoptServer: () => {
      if (!server) return
      setMode(server.mode ?? 'shadow')
      setMaxSetups(String(server.max_setups ?? 10))
      setTtlMinutes(String(server.authorization_ttl_minutes ?? 60))
    },
  })

  const mut = useMutation({
    mutationFn: () => api.updateFastTriggerConfig({
      mode,
      max_setups: parseInt(maxSetups),
      authorization_ttl_minutes: parseInt(ttlMinutes),
    }),
    onSuccess: (result: any) => {
      const reason = result?.runtime_status?.reason
      setMsg({ ok: !reason, text: reason ? `Saved; inactive: ${reason}` : '✓ saved and applied' })
      setChanged(false)
      qc.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  if (!server) return null
  const update = (fn: () => void) => { fn(); setChanged(true); setMsg(null) }
  return (
    <div className="card mt-3 mb-0">
      <h3>Momentum Fast Trigger</h3>
      <p className="hint">Turns authorized Momentum setups into triggered Opportunities; other Universe families remain Candidate-only.</p>
      <div className="settings-field-grid">
        <label className="settings-field">
          <span>Mode</span>
          <select value={mode} onChange={e => update(() => setMode(e.target.value as typeof mode))}>
            <option value="off">off</option>
            <option value="shadow">shadow (default)</option>
            <option value="execute">execute</option>
          </select>
        </label>
        <label className="settings-field">
          <span>Max watched setups</span>
          <input type="number" min={1} max={10} value={maxSetups}
            onChange={e => update(() => setMaxSetups(e.target.value))} />
        </label>
        <label className="settings-field">
          <span>Authorization TTL (min)</span>
          <input type="number" min={1} max={60} value={ttlMinutes}
            onChange={e => update(() => setTtlMinutes(e.target.value))} />
        </label>
      </div>
      <div className="flex items-center gap-2">
        <button onClick={() => mut.mutate()} disabled={!changed || mut.isPending}>
          {mut.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
      <SaveMsg msg={msg} />
      <p className="hint m-0">
        Shadow never submits orders. Local Paper Execute uses Bitget public fast data and the local broker; exchange Execute is limited to Bitget Demo. Live is always refused.
      </p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// RISK & SYMBOLS TAB — allowed symbols, risk limits
// ─────────────────────────────────────────────────────────────────────────────

export function RiskTab({ settings, qc, onDirty, sectionId }: SettingsDirtyProps & { sectionId?: string }) {
  // onDirty is forwarded to UniverseConfigCard below; its own dirty reporting
  // now adopts or preserves local state through useSettingsDraftLifecycle.
  const isVisible = (id: string) => !sectionId || sectionId === id
  return (
    <div>
      {isVisible(UNIVERSE_SECTION.id) && (
        <SettingsFeatureSection descriptor={UNIVERSE_SECTION}>
          <UniverseConfigCard settings={settings} qc={qc} onDirty={onDirty} />
          <CandidateSelectionAuthorityCard />
          <FastTriggerConfigCard settings={settings} qc={qc} onDirty={onDirty} />
        </SettingsFeatureSection>
      )}

      {isVisible(EVENT_TRIGGERS_SECTION.id) && (
        <SettingsFeatureSection descriptor={EVENT_TRIGGERS_SECTION}>
          <Collapsible title="Trigger Thresholds">
            <EventTriggerCard settings={settings} qc={qc} onDirty={onDirty} />
          </Collapsible>
        </SettingsFeatureSection>
      )}

      {isVisible(TRADABLE_SYMBOLS_SECTION.id) && (
        <SettingsFeatureSection descriptor={TRADABLE_SYMBOLS_SECTION}>
          <SymbolsCard settings={settings} qc={qc} onDirty={onDirty} />
        </SettingsFeatureSection>
      )}

      {isVisible(RISK_LIMITS_SECTION.id) && (
        <SettingsFeatureSection descriptor={RISK_LIMITS_SECTION}>
          <RiskCard settings={settings} qc={qc} onDirty={onDirty} />
        </SettingsFeatureSection>
      )}
    </div>
  )
}


// ─── Symbols ───

function SymbolsCard({ settings, qc, onDirty }: SettingsDirtyProps) {
  const serverMode: string = settings?.risk?.symbol_filter_mode ?? 'blacklist'
  const serverSymbols: string[] = settings?.risk?.symbol_list ?? []

  const [mode, setMode] = useState<'whitelist' | 'blacklist'>(serverMode === 'whitelist' ? 'whitelist' : 'blacklist')
  const [draft, setDraft] = useState<string[]>([])
  const [input, setInput] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

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

  useSettingsDraftLifecycle({
    id: 'tradable-symbols',
    dirty,
    onDirty,
    serverKey: settings,
    adoptServer: () => {
      setMode(serverMode === 'whitelist' ? 'whitelist' : 'blacklist')
      setDraft(serverSymbols)
    },
  })

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
      <div className="flex flex-wrap gap-1 mb-3 min-h-8">
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
          className="mono w-36 uppercase"
        />
        <button className="secondary" onClick={add}>Add</button>
        {dirty && (
          <button onClick={() => mut.mutate()} disabled={mut.isPending || !canSave}>
            {mut.isPending ? 'Saving…' : 'Save changes'}
          </button>
        )}
      </div>
      {mode === 'whitelist' && draft.length === 0 && dirty && (
        <div className="settings-save-msg err mt-1">Add at least one symbol to save in whitelist mode</div>
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
            {((settings?.market_data?.observed_instruments ?? []) as string[]).map((id: string) => {
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

export type RiskPolicyDraft = DeepPartial<SettingsRiskPolicy> & Record<string, unknown>

export function readPolicyPath(value: RiskPolicyDraft, path: RiskPolicyPath): unknown {
  if (!RISK_POLICY_PATHS.has(path)) {
    throw new Error(`Unsupported risk policy path: ${path}`)
  }
  return path.split('.').reduce<unknown>((current, key) => {
    if (!current || typeof current !== 'object') return undefined
    return (current as Record<string, unknown>)[key]
  }, value)
}

export function writePolicyPath(value: RiskPolicyDraft, path: RiskPolicyPath, next: unknown): RiskPolicyDraft {
  if (!RISK_POLICY_PATHS.has(path)) {
    throw new Error(`Unsupported risk policy path: ${path}`)
  }
  const clone = structuredClone(value) as RiskPolicyDraft
  const parts = path.split('.')
  let cursor: Record<string, unknown> = clone
  for (const key of parts.slice(0, -1)) {
    const child = cursor[key]
    if (!child || typeof child !== 'object') {
      throw new Error(`Missing risk policy object for path: ${path}`)
    }
    cursor = child as Record<string, unknown>
  }
  cursor[parts[parts.length - 1]] = next
  return clone
}

export function RiskPolicyEditor({ mode, initial, qc, onDirty }: { mode: 'demo' | 'live'; initial: RiskPolicyDraft; qc: SettingsProps['qc']; onDirty?: SettingsDirtyProps['onDirty'] }) {
  const confirm = useConfirm()
  const [policy, setPolicy] = useState<RiskPolicyDraft>(initial)
  const [isDirty, setIsDirty] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  useEffect(() => {
    if (isDirty) return
    if (Number(initial.revision) > Number(policy.revision)) setPolicy(initial)
  }, [initial, isDirty, policy.revision])
  useEffect(() => {
    const id = `risk-policy-${mode}`
    onDirty?.(id, isDirty)
    return () => onDirty?.(id, false)
  }, [isDirty, mode, onDirty])
  const numFields = mode === 'live' ? [...RISK_POLICY_NUM_FIELDS, ...RISK_POLICY_LIVE_FIELDS] : RISK_POLICY_NUM_FIELDS
  const vals = Object.fromEntries(
    [...numFields, ...RISK_POLICY_BOOLS].map(field => [field.key, readPolicyPath(policy, field.key)])
  )
  const save = useMutation({
    mutationFn: () => api.replaceRiskPolicy(
      mode,
      policy,
      Number(policy.revision ?? 0),
      `${mode} policy edited in Settings`,
    ),
    onSuccess: (risk) => {
      setPolicy(risk[mode])
      setIsDirty(false)
      setMsg({ ok: true, text: '✓ policy saved with a new revision' })
      qc.invalidateQueries({ queryKey: ['settings'] })
      qc.invalidateQueries({ queryKey: ['risk-boundaries'] })
    },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })
  const reset = useMutation({
    mutationFn: () => api.resetRiskPolicy(
      mode,
      Number(policy.revision ?? 0),
      `${mode} policy reset to shipped defaults in Settings`,
    ),
    onSuccess: (risk) => {
      setPolicy(risk[mode])
      setIsDirty(false)
      setMsg({ ok: true, text: '✓ policy reset with a new revision' })
      qc.invalidateQueries({ queryKey: ['settings'] })
      qc.invalidateQueries({ queryKey: ['risk-boundaries'] })
    },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })
  const set = (path: RiskPolicyPath, value: unknown) => {
    setPolicy(current => writePolicyPath(current, path, value))
    setIsDirty(true)
    setMsg(null)
  }
  const capital = policy.capital ?? {}
  const loss = policy.loss ?? {}
  const capitalSource = capital.base_source ?? 'account_equity'
  const customCapital = Number(capital.base_usdt || 0)
  const threshold = customCapital * Number(loss.max_daily_loss_pct || 0) / 100
  const setNumber = (path: string, value: number) => set(path as RiskPolicyPath, value)
  const setBoolean = (path: string, value: boolean) => set(path as RiskPolicyPath, value)
  return (
    <div style={{ flex: '1 1 420px', border: '1px solid var(--border)', borderRadius: 'var(--r-sm)', padding: 'var(--space-4)' }}>
      <div className="section-label">{mode === 'demo' ? 'Demo / Paper Policy' : 'Live Policy'}</div>
      <p className="hint mono">{policy.policy_id} · revision {policy.revision}</p>
      <div className="settings-form-grid">
        <label className="settings-form-field">
          <span className="settings-form-label">Capital base source</span>
          <select value={capitalSource} onChange={event => set('capital.base_source', event.target.value)}>
            <option value="account_equity">Follow account equity</option>
            <option value="custom">Custom</option>
          </select>
          <span className="settings-form-description">
            Account equity follows the matching paper, demo broker, or live account pool and fails closed when unavailable.
          </span>
        </label>
        {capitalSource === 'custom' && (
          <label className="settings-form-field">
            <span className="settings-form-label">Custom capital base</span>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={1}
                step={1}
                value={capital.base_usdt}
                aria-label="Custom capital base"
                onChange={event => set('capital.base_usdt', Number(event.target.value))}
              />
              <span className="text-xs text-muted">USDT</span>
            </div>
          </label>
        )}
      </div>
      <NumGrid fields={numFields} vals={vals} set={setNumber} />
      <p className="hint">
        System Operator entry cost = matching available balance × this per-trade budget. For perpetual futures, final notional = cost × effective leverage; position, account-leverage, and stop-loss safety caps still apply.
      </p>
      <ToggleGrid fields={RISK_POLICY_BOOLS} vals={vals} set={setBoolean} persistence="staged" />
      <p className="hint">
        {capitalSource === 'custom'
          ? `Daily-loss threshold: −${fmtNum(threshold)} USDT.`
          : 'Daily-loss threshold follows the matching account equity.'}
        {' '}A breach trips only the {mode} brake.
      </p>
      <p className="hint">Margin warning/block ratios are retained for migration but are not enforced yet; entry/runtime liquidation buffers are enforced.</p>
      {mode === 'live' && policy.eligibility && (
        <p className="hint">Live evidence gate: ≥{policy.eligibility.family_min_samples} samples and ≥{policy.eligibility.family_min_win_rate_pct}% win rate. Copying Demo does not replace this gate.</p>
      )}
      <div className="settings-action-row">
        <button
          onClick={async () => {
            if (mode === 'live' && !(await confirm({
              title: 'Replace the Live risk policy?',
              description: 'Live remains locked, but future Live evaluations will use this revision.',
              confirmLabel: 'Save Live policy',
              variant: 'warning',
            }))) return
            save.mutate()
          }}
          disabled={save.isPending}
        >{save.isPending ? 'Saving…' : `Save ${mode} policy`}</button>
        <button
          onClick={async () => {
            if (!(await confirm({
              title: `Reset only the ${mode} policy?`,
              description: 'The other policy and all runtime authority remain unchanged.',
              confirmLabel: 'Reset defaults',
              variant: 'danger',
            }))) return
            reset.mutate()
          }}
          disabled={reset.isPending}
        >{reset.isPending ? 'Resetting…' : 'Reset defaults'}</button>
      </div>
      <SaveMsg msg={msg} />
    </div>
  )
}

type RiskPolicyProfilesData = {
  demo: RiskPolicyDraft
  live: RiskPolicyDraft
}

function RiskPolicyProfiles({ risk, qc, onDirty }: { risk: RiskPolicyProfilesData; qc: SettingsProps['qc']; onDirty?: SettingsDirtyProps['onDirty'] }) {
  const confirm = useConfirm()
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const copy = useMutation({
    mutationFn: ({ source, target }: { source: 'demo' | 'live'; target: 'demo' | 'live' }) =>
      api.copyRiskPolicy(
        source,
        target,
        Number(risk[source].revision ?? 0),
        Number(risk[target].revision ?? 0),
        `${source} policy copied to ${target} in Settings`,
      ),
    onSuccess: () => {
      setMsg({ ok: true, text: '✓ policy copied with a new target revision' })
      qc.invalidateQueries({ queryKey: ['settings'] })
      qc.invalidateQueries({ queryKey: ['risk-boundaries'] })
    },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })
  const runCopy = async (source: 'demo' | 'live', target: 'demo' | 'live') => {
    const warning = target === 'live'
      ? 'Copy all common Demo values into Live? Live eligibility, unlock, permissions, credentials and brake state are preserved. Review this as a multi-variable Live policy change.'
      : 'Copy all common Live values into Demo? Demo runtime state and brake state are preserved.'
    if (await confirm({
      title: `Copy ${source === 'demo' ? 'Demo' : 'Live'} policy to ${target === 'demo' ? 'Demo' : 'Live'}?`,
      description: warning,
      confirmLabel: 'Copy policy',
      variant: target === 'live' ? 'warning' : 'default',
    })) copy.mutate({ source, target })
  }
  return (
    <div className="card mb-0">
      <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', padding: '0.45rem 0.75rem', background: 'var(--bg)', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', marginBottom: '1.25rem' }}>
        <span className="text-xs text-muted">Risk Engine: <span className="badge badge-green">shared and mandatory</span></span>
        <span className="text-xs text-muted">Policies: independently versioned; runtime authority and credentials are never copied</span>
      </div>
      <div className="settings-action-row">
        <button onClick={() => runCopy('demo', 'live')} disabled={copy.isPending}>Copy Demo → Live</button>
        <button onClick={() => runCopy('live', 'demo')} disabled={copy.isPending}>Copy Live → Demo</button>
      </div>
      <SaveMsg msg={msg} />
      <div style={{ display: 'flex', gap: 'var(--space-4)', flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <RiskPolicyEditor mode="demo" initial={risk.demo} qc={qc} onDirty={onDirty} />
        <RiskPolicyEditor mode="live" initial={risk.live} qc={qc} onDirty={onDirty} />
      </div>
    </div>
  )
}

function RiskCard({ settings, qc, onDirty }: SettingsDirtyProps) {
  type RiskSettings = Partial<SettingsRiskConfig> & Record<string, unknown> & RiskPolicyProfilesData
  const risk = settings?.risk as RiskSettings | undefined
  const [vals, setVals] = useState<Record<string, unknown>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const serverVals: Record<string, unknown> = {}
  ;[...RISK_LIVE, ...RISK_DEMO, ...RISK_SHARED, ...RISK_CORR, ...RISK_FAMILY_GATE, ...RISK_FUTURES_LIVE, ...RISK_FUTURES_DEMO, ...RISK_MARGIN].forEach(f => { serverVals[f.key] = risk?.[f.key] })
  RISK_BOOLS.forEach(f => { serverVals[f.key] = risk?.[f.key] })
  const dirty = !!risk && Object.keys(vals).length > 0 && JSON.stringify(vals) !== JSON.stringify(serverVals)
  useSettingsDraftLifecycle({
    id: 'risk-limits',
    dirty,
    onDirty,
    serverKey: settings,
    adoptServer: () => { if (risk) setVals({ ...serverVals }) },
  })

  const set = (k: string, v: unknown) => { setVals(prev => ({ ...prev, [k]: v })); setMsg(null); setErrors({}) }
  const [errors, setErrors] = useState<Record<string, string>>({})

  const allNumFields: readonly NumField[] = [...RISK_LIVE, ...RISK_DEMO, ...RISK_SHARED, ...RISK_CORR, ...RISK_FAMILY_GATE, ...RISK_FUTURES_LIVE, ...RISK_FUTURES_DEMO, ...RISK_MARGIN]

  const validateAndSave = () => {
    const errs: Record<string, string> = {}
    for (const f of allNumFields) {
      const v = vals[f.key]
      if (v == null || v === '') continue
      if (typeof v === 'number') {
        if (v < f.min) errs[f.key] = `Min ${f.min}`
        else if (f.max != null && v > f.max) errs[f.key] = `Max ${f.max}`
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
    mutationFn: () => api.updateRiskConfig(vals as Parameters<typeof api.updateRiskConfig>[0]),
    onSuccess: () => {
      setMsg({ ok: true, text: '✓ saved' })
      qc.invalidateQueries({ queryKey: ['settings'] })
      qc.invalidateQueries({ queryKey: ['risk-boundaries'] })
    },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  if (!risk) return null
  if (risk.demo && risk.live) return <RiskPolicyProfiles risk={risk} qc={qc} onDirty={onDirty} />

  return (
    <div className="card mb-0">
      {/* Immutable fields notice */}
      <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', padding: '0.45rem 0.75rem', background: 'var(--bg)', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', marginBottom: '1.25rem' }}>
        <span className="text-xs text-muted">
          allowed_exchange: <span className="mono">{risk.allowed_exchange}</span>
        </span>
        <span className="text-xs text-muted">
          require_risk_check: <span className={`badge ${risk.require_risk_check_for_all_orders ? 'badge-green' : 'badge-red'}`}>{humanizeBool(risk.require_risk_check_for_all_orders)}</span>
        </span>
      </div>

      {/* Live limits */}
      <div className="section-label">Live limits</div>
      <NumGrid fields={RISK_LIVE} vals={vals} set={set} errors={errors} />
      <p className="hint">Exchange-backed orders use the lower of this ceiling and the live account's cached available USDT. If no balance snapshot is available, this ceiling is used.</p>

      {/* Demo limits */}
      <Collapsible title="Demo / paper limits">
      <NumGrid fields={RISK_DEMO} vals={vals} set={set} errors={errors} />
      <p className="hint">Demo account orders use the lower of this ceiling and the demo account's cached available USDT.</p>
      </Collapsible>

      {/* Cross-mode guards (both demo and live) */}
      <Collapsible title="Per-symbol & family-evidence gates">
      <div className="section-label">Per-symbol exposure (demo + live)</div>
      <NumGrid fields={RISK_SHARED} vals={vals} set={set} errors={errors} />

      {/* Scanner-family live-eligibility gate (risk check #14) */}
      <div className="section-label mt-4">Live scanner-family evidence gate (#14)</div>
      <NumGrid fields={RISK_FAMILY_GATE} vals={vals} set={set} errors={errors} />
      </Collapsible>

      <ToggleGrid fields={RISK_BOOLS} vals={vals} set={set} persistence="staged" />

      {/* Correlation guard thresholds — only shown when guard is enabled */}
      {Boolean(vals.correlation_guard_enabled) && (
        <>
          <div className="section-label mt-4">Correlation guard thresholds</div>
          <NumGrid fields={RISK_CORR} vals={vals} set={set} errors={errors} />
        </>
      )}

      {/* Futures / leverage tier limits — shown when usdt_futures is enabled */}
      {settings?.exchange?.enabled_markets?.usdt_futures && (
        <Collapsible title="Futures leverage & margin">
          <div className="section-label">Futures leverage — Live (per-tier)</div>
          <NumGrid fields={RISK_FUTURES_LIVE} vals={vals} set={set} errors={errors} />

          <div className="section-label mt-4">Futures leverage — Demo (per-tier)</div>
          <NumGrid fields={RISK_FUTURES_DEMO} vals={vals} set={set} errors={errors} />

          <div className="section-label mt-4">Margin safety thresholds</div>
          <NumGrid fields={RISK_MARGIN} vals={vals} set={set} errors={errors} />
        </Collapsible>
      )}

      {/* Kill switch trigger conditions — read-only reference, collapsed by default (§渐进式披露) */}
      <Collapsible title="Kill switch trigger conditions">
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
          <li><strong>Live threshold:</strong> &minus;{fmtNum(Number(vals.max_live_limited_cap_usdt || 0) * Number(vals.max_daily_loss_pct || 0) / 100)} USDT</li>
          <li><strong>Trigger formula (demo):</strong> daily realized PnL &le; &minus;(Starting Capital &times; Max Daily Loss %)</li>
          <li><strong>Demo threshold:</strong> &minus;{fmtNum(Number(vals.starting_capital_usdt || 0) * Number(vals.max_daily_loss_pct || 0) / 100)} USDT</li>
          <li><strong>Scope:</strong> applies to live trades when "Kill switch auto-trigger (live)" is ON; applies to demo trades when "Kill switch auto-trigger (demo)" is ON</li>
          <li><strong>Effect:</strong> once tripped, ALL proposals (live + demo) are rejected until manually released</li>
          <li><strong>Reset:</strong> release from Risk Controls panel or CLI after UTC new day</li>
        </ul>
      </div>
      </Collapsible>

      <div className="settings-action-row">
        <button onClick={validateAndSave} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save risk limits'}</button>
      </div>
      <SaveMsg msg={msg} />
    </div>
  )
}
