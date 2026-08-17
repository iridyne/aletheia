import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { positionKeys } from '../../../lib/queryKeys'
import {
  CollapsibleSection as Collapsible,
  SaveMessage as SaveMsg,
  SettingsRow as Row,
  Switch as Toggle,
} from '../../../components/ui'
import { useSettingsDraftLifecycle } from '../draftLifecycle'
import type { SettingsDirtyProps } from '../types'

const CONFIDENCE_LEVELS = ['high', 'medium', 'speculative'] as const

export function PaperAccountCard({ qc, onDirty }: { qc: ReturnType<typeof useQueryClient>; onDirty?: (id: string, isDirty: boolean) => void }) {
  const { data: account, isLoading } = useQuery({ queryKey: ['paper-account'], queryFn: api.getPaperAccount })
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['paper-account'] })
    qc.invalidateQueries({ queryKey: positionKeys.all })
    qc.invalidateQueries({ queryKey: ['portfolio'] })
    qc.invalidateQueries({ queryKey: ['account-equity-history'] })
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
  useSettingsDraftLifecycle({
    id: 'paper-account',
    dirty: amount !== '' || note !== '',
    onDirty,
    serverKey: undefined,
    adoptServer: () => undefined,
  })

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

export function CapitalEventsCard({ onDirty }: { onDirty?: (id: string, isDirty: boolean) => void }) {
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
      setBrokerId('')
      setEventType('deposit')
      setMsg('Recorded')
      setTimeout(() => setMsg(null), 3000)
    },
    onError: (e: any) => setMsg(e.message || 'Failed'),
  })

  const netDeposits = netData?.net_deposits_usdt ?? 0
  useSettingsDraftLifecycle({
    id: 'capital-events',
    dirty: brokerId !== '' || eventType !== 'deposit' || amount !== '' || note !== '',
    onDirty,
    serverKey: undefined,
    adoptServer: () => undefined,
  })

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

export function TradingParamsCard({ settings, qc, onDirty }: SettingsDirtyProps) {
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
  const mae = trading?.market_aware_exits || {}
  const dirty = !!trading && (
    stopLoss !== String(trading.default_stop_loss_pct ?? 2)
    || takeProfit !== String(trading.default_take_profit_pct ?? 4)
    || holdHours !== String(trading.max_position_hold_hours ?? 72)
    || slippageBps !== String(trading.slippage_bps ?? 5)
    || executionFeePct !== String(trading.execution_fee_pct ?? 0.1)
    || confidence !== (trading.min_confidence_to_trade ?? 'medium')
    || autoAdopt !== (trading.auto_adopt_enabled !== false)
    || trailEnabled !== !!trading.trail_enabled
    || exchangeTpslEnabled !== !!trading.exchange_tpsl_enabled
    || trailAtrMult !== String(trading.trail_atr_mult ?? 1.5)
    || trailPct !== String(trading.trail_pct ?? 1.0)
    || breakevenTriggerPct !== String(trading.breakeven_trigger_pct ?? 1.5)
    || volSizingEnabled !== (trading.vol_sizing_enabled !== false)
    || defaultNotional !== String(trading.default_notional_usdt ?? 25)
    || volRiskUsdt !== String(trading.vol_target_risk_usdt ?? 25)
    || volTargetPct !== String(trading.vol_target_pct ?? 1.5)
    || volKellyCap !== String(trading.vol_kelly_cap_fraction ?? 0.25)
    || personaEnabled !== !!trading.persona_sizing_enabled
    || activePersona !== (trading.active_persona ?? 'conservative')
    || personaLearningEnabled !== !!trading.persona_learning_enabled
    || personaLearningMinSamples !== String(trading.persona_learning_min_samples ?? 15)
    || personaUnlockEnabled !== !!trading.persona_progressive_unlock_enabled
    || personaUnlockStep !== String(trading.persona_unlock_step_samples ?? 25)
    || personaPyramidEnabled !== !!trading.persona_pyramid_enabled
    || personaPyramidTrigger !== String(trading.persona_pyramid_add_trigger_pct ?? 1.0)
    || personaAutoAdvance !== !!trading.persona_auto_advance_enabled
    || maeEnabled !== !!mae.enabled
    || maeUrgentScore !== String(mae.urgent_exit_score ?? 80)
    || maeTightenScore !== String(mae.tighten_stop_score ?? 60)
    || maeCollapseScore !== String(mae.confidence_collapse_score ?? 30)
    || maeTightenAtrMult !== String(mae.tighten_atr_mult ?? 0.8)
  )
  useSettingsDraftLifecycle({
    id: 'trading-params',
    dirty,
    onDirty,
    serverKey: settings,
    adoptServer: () => {
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
    },
  })

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
          persistence="staged"
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
            persistence="staged"
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
            persistence="staged"
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
              <span>Legacy ATR target (%)</span>
              <input type="number" min={0.5} max={10} step={0.1} value={volTargetPct}
                onChange={e => { setVolTargetPct(e.target.value); setMsg(null) }} className="mono" />
            </label>
            <label>
              <span>Legacy sizing scale</span>
              <input type="number" min={0.05} max={0.5} step={0.05} value={volKellyCap}
                onChange={e => { setVolKellyCap(e.target.value); setMsg(null) }} className="mono" />
            </label>
          </>)}
        </div>
        <p className="hint mt-1">
          Compatibility note: ATR target is not consumed by the current sizing formula, and the
          legacy sizing scale is a bounded multiplier—not a calibrated Kelly fraction.
        </p>
        <div className="settings-inline-note">
          <Toggle
            checked={exchangeTpslEnabled}
            persistence="staged"
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
            persistence="staged"
            label="Persona-based sizing (动态 sizing 蓝本)"
            onChange={v => { setPersonaEnabled(v); setMsg(null) }}
          />
          <p className="hint mt-1">
            When ON, entry sizing reads the active persona's notional and legacy scaling bounds instead of
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
                    Active entry consumers: <code>notional_usdt</code> and the legacy-named{' '}
                    <code>kelly_fraction</code> scaling field.
                    Leverage, stop, and add parameters remain experimental/reference-only.
                  </p>
                </div>
              )
            })()}
            <div className="mt-2">
              <Toggle
                checked={personaLearningEnabled}
                persistence="staged"
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
                persistence="staged"
                label="Progressive unlock (逐个解冻 operator-decidable 参数)"
                onChange={v => { setPersonaUnlockEnabled(v); setMsg(null) }}
              />
              <p className="hint mt-1">
                Only active entry parameters (<code>notional_usdt</code> and the{' '}
                legacy-named <code>kelly_fraction</code> scaling field) can unlock. Each rollout
                starts a durable Persona/policy epoch and accepts only exact attributed outcomes
                from that step; historical or mismatched outcomes never count. Account distress,
                invalid provenance, or degradation refreezes all. Default OFF.
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
                  persistence="staged"
                  label="Auto-advance rollout (evidence-gated)"
                  onChange={v => { setPersonaAutoAdvance(v); setMsg(null) }}
                />
                <p className="hint mt-1">
                  The 8-hour job only evaluates the durable rollout state. It can unlock at most
                  one parameter after the scoped sample, market-day, and stable non-degrading
                  verdict gates pass; the interval itself is never evidence.
                </p>
              </div>
            </div>
            {settings?.persona_rollout && (
              <div className="settings-inline-note mt-2">
                <span className="settings-field-label">Progressive-unlock authority</span>
                <div className="mono text-sm">
                  desired={settings.persona_rollout.desired_progressive_unlock ? 'on' : 'off'} ·{' '}
                  status={settings.persona_rollout.status || 'not available'} ·{' '}
                  actual={((settings.persona_rollout.actual_authorized_params || []) as string[]).join(', ') || 'none'}
                </div>
                <div className="mono text-sm">
                  evidence={String(settings.persona_rollout.evidence?.sample_count ?? 'not available')}/
                  {String(settings.persona_rollout.evidence?.required_samples ?? '—')} samples ·{' '}
                  {String(settings.persona_rollout.evidence?.market_day_count ?? 'not available')}/
                  {String(settings.persona_rollout.evidence?.required_market_days ?? '—')} market days ·{' '}
                  verdict={String(settings.persona_rollout.evidence?.reason || 'not available')}
                </div>
                <p className="hint mt-1">
                  Actual authority and evidence come from the backend rollout record; desired flags
                  alone never authorize a parameter.
                </p>
              </div>
            )}
            <div className="mt-2">
              <Toggle
                checked={personaPyramidEnabled}
                persistence="staged"
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
            persistence="staged"
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
