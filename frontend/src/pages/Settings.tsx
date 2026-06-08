import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import ConnectionStatus, { ConnState } from '../components/ConnectionStatus'
import PageHeader from '../components/PageHeader'
import { useDecimals, setDecimals, fmtNum, DECIMAL_BOUNDS } from '../lib/format'

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const PERMISSION_MODES = ['Observe', 'Paper', 'Full']
const PROVIDERS = ['openai_compatible', 'anthropic_compatible', 'codex_cli']
const CONFIDENCE_LEVELS = ['high', 'medium', 'speculative'] as const

type Tab = 'intelligence' | 'execution' | 'risk' | 'infrastructure'
const TABS: { id: Tab; label: string }[] = [
  { id: 'intelligence',   label: 'Intelligence' },
  { id: 'execution',      label: 'Execution' },
  { id: 'risk',           label: 'Risk & Symbols' },
  { id: 'infrastructure', label: 'Infrastructure' },
]

// ─────────────────────────────────────────────────────────────────────────────
// Layout primitives
// ─────────────────────────────────────────────────────────────────────────────

function Section({ title, description, children }: {
  title: string; description?: string; children: React.ReactNode
}) {
  return (
    <section style={{ marginBottom: '2.5rem' }}>
      {/* Section header — display font + accent-dim left bar to distinguish from row labels */}
      <div style={{
        display: 'flex', flexDirection: 'column', gap: '0.2rem',
        paddingLeft: '0.75rem', borderLeft: '2px solid var(--accent-dim)',
        marginBottom: '1rem',
      }}>
        <h3 style={{
          margin: 0, fontFamily: 'var(--display)', fontSize: '0.88rem',
          fontWeight: 600, color: 'var(--text)', letterSpacing: '0.01em',
        }}>
          {title}
        </h3>
        {description && (
          <p style={{ margin: 0, fontSize: '0.76rem', color: 'var(--muted)', lineHeight: 1.45 }}>
            {description}
          </p>
        )}
      </div>
      {/* Heavier rule seals the header from the rows — visually heavier than row separators (which are gone) */}
      <div style={{ borderTop: '1px solid var(--border-2)', marginBottom: '0.25rem' }} />
      {children}
    </section>
  )
}

// Two-column label ↔ control row.
// No bottom border — rows use padding-based spacing only, so the only lines
// the eye sees are Section headers. This is the fix for the "all lines look the same" problem.
function Row({ label, hint, children, danger }: {
  label: string; hint?: string; children: React.ReactNode; danger?: boolean
}) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem', alignItems: 'start',
      padding: '0.7rem 0',
    }}>
      <div>
        <div style={{ fontSize: '0.82rem', fontWeight: 500, color: danger ? 'var(--red)' : 'var(--text)' }}>{label}</div>
        {hint && <div style={{ fontSize: '0.73rem', color: 'var(--muted)', marginTop: '0.2rem', lineHeight: 1.4 }}>{hint}</div>}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        {children}
      </div>
    </div>
  )
}

function Toggle({ checked, onChange, disabled }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.82rem', cursor: 'pointer' }}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} disabled={disabled} />
      {checked ? 'Enabled' : 'Disabled'}
    </label>
  )
}

function SaveMsg({ msg }: { msg: { ok: boolean; text: string } | null }) {
  if (!msg) return null
  return <span style={{ fontSize: '0.78rem', color: msg.ok ? 'var(--green)' : 'var(--red)' }}>{msg.text}</span>
}

// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────

export default function Settings() {
  const qc = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
  const [tab, setTab] = useState<Tab>('intelligence')

  if (!status || !settings) return <p style={{ padding: '1rem', color: 'var(--muted)' }}>Loading…</p>

  return (
    <div>
      <PageHeader
        title="Settings"
        role="Operate · Configure AI agent behaviour, exchange connections, risk limits, and system infrastructure. Secrets are write-only and never displayed."
      />

      <div className="tab-bar" style={{ marginBottom: '2rem' }}>
        {TABS.map(t => (
          <button key={t.id} className={`tab ${tab === t.id ? 'tab-active' : ''}`} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'intelligence'   && <IntelligenceTab   status={status} settings={settings} qc={qc} />}
      {tab === 'execution'      && <ExecutionTab      status={status} settings={settings} qc={qc} />}
      {tab === 'risk'           && <RiskTab           settings={settings} qc={qc} />}
      {tab === 'infrastructure' && <InfrastructureTab settings={settings} qc={qc} />}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// INTELLIGENCE TAB — agent behaviour, permission, autonomy, scheduler
// ─────────────────────────────────────────────────────────────────────────────

function IntelligenceTab({ status, settings, qc }: any) {
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
          <select value={perm} onChange={e => permMut.mutate(e.target.value)} style={{ width: 'auto' }}>
            {PERMISSION_MODES.map(m => <option key={m}>{m}</option>)}
          </select>
        </Row>
      </Section>

      <Section title="Learning" description="Controls whether agents autonomously generate research, knowledge, and strategy ideas.">
        <Row label="Autonomous learning" hint="When ON, agents run dreaming/research cycles unprompted. When OFF, only human-triggered runs execute.">
          <Toggle checked={autoLearn} onChange={v => autoLearnMut.mutate(v)} disabled={autoLearnMut.isPending} />
          <span className={`badge ${autoLearn ? 'badge-green' : 'badge-red'}`}>{autoLearn ? 'ON' : 'OFF'}</span>
        </Row>
      </Section>

      <Section title="Scheduler" description="Controls background scheduling of all agent jobs.">
        <Row
          label="Scheduler enabled"
          hint="When disabled, no agent jobs fire automatically. The gateway stays up; runs can still be triggered manually."
        >
          <Toggle
            checked={schedulerEnabled}
            onChange={v => runtimeMut.mutate({ scheduler_enabled: v })}
            disabled={runtimeMut.isPending}
          />
          <span className={`badge ${schedulerEnabled ? 'badge-green' : 'badge-yellow'}`}>{schedulerEnabled ? 'active' : 'disabled'}</span>
        </Row>
      </Section>

      <Section title="AI Model Providers" description="LLM endpoints used by all agents. Verify & Save tests the connection before persisting. API keys are write-only.">
        <ModelProviderCard qc={qc} />
      </Section>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// EXECUTION TAB — exchange, credentials, mode, markets, live unlock, trading params
// ─────────────────────────────────────────────────────────────────────────────

function ExecutionTab({ status, settings, qc }: any) {
  return (
    <div>
      <Section
        title="Exchange Connection"
        description="Bitget API credentials for demo and live profiles. All three fields must be present for a live connection test."
      >
        <ExchangeCredentialsCard qc={qc} />
      </Section>

      <Section title="Trading Mode" description="Switch between demo (paper trading) and live execution. Live requires explicit unlock below.">
        <ExchangeModeRow status={status} qc={qc} />
        <ExchangeMarketsRow settings={settings} qc={qc} />
        <LiveStateRow status={status} qc={qc} />
      </Section>

      <Section
        title="Paper Trading Parameters"
        description="Default exit rules applied when the paper position manager opens a new trade."
      >
        <TradingParamsCard settings={settings} qc={qc} />
      </Section>
    </div>
  )
}

function ExchangeModeRow({ status, qc }: any) {
  const mut = useMutation({
    mutationFn: (mode: string) => api.setExchangeMode(mode),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })
  return (
    <Row label="Exchange mode" hint="demo uses Bitget paper trading; live requires unlocking below and real credentials.">
      <select value={String(status.exchange_mode)} onChange={e => mut.mutate(e.target.value)} style={{ width: 'auto' }}>
        <option value="demo">demo</option>
        <option value="live">live</option>
      </select>
    </Row>
  )
}

function ExchangeMarketsRow({ settings, qc }: any) {
  const markets = (settings?.exchange as any)?.enabled_markets || { spot: true, usdt_futures: true }
  const [spot, setSpot] = useState(!!markets.spot)
  const [futures, setFutures] = useState(!!markets.usdt_futures)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    setSpot(!!markets.spot)
    setFutures(!!markets.usdt_futures)
  }, [settings])

  const mut = useMutation({
    mutationFn: () => api.setExchangeMarkets({ spot, usdt_futures: futures }),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const dirty = spot !== !!markets.spot || futures !== !!markets.usdt_futures

  return (
    <Row label="Enabled markets" hint="Which Bitget market types agents can place orders in. At least one must be enabled.">
      <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.82rem', cursor: 'pointer' }}>
        <input type="checkbox" checked={spot} onChange={e => { setSpot(e.target.checked); setMsg(null) }} />
        Spot
      </label>
      <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.82rem', cursor: 'pointer' }}>
        <input type="checkbox" checked={futures} onChange={e => { setFutures(e.target.checked); setMsg(null) }} />
        USDT Futures
      </label>
      {dirty && (
        <button className="secondary" onClick={() => mut.mutate()} disabled={mut.isPending || (!spot && !futures)}>
          {mut.isPending ? 'Saving…' : 'Save'}
        </button>
      )}
      <SaveMsg msg={msg} />
    </Row>
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
    <Row label="Live trading state" hint="Disabled by default. Unlocking runs a credential pre-flight, requires confirmation, and is audited. Use Kill Switch on Risk & Controls to halt immediately." danger={!isDisabled}>
      <span className={`badge ${isDisabled ? 'badge-red' : 'badge-yellow'}`}>{String(status.live_state)}</span>
      <button onClick={preflightUnlock} disabled={!isDisabled}>Unlock Live-Limited</button>
      <button className="secondary" onClick={() => lock.mutate()} disabled={isDisabled}>Lock</button>
      {warning && <span style={{ color: 'var(--red)', fontSize: '0.75rem' }}>⚠ {warning}</span>}
    </Row>
  )
}

// ─── Exchange credentials ───

function ExchangeProfileStatus({ profile, configured }: { profile: 'demo' | 'live'; configured: boolean }) {
  const { data } = useQuery({
    queryKey: ['bitget-test', profile], queryFn: () => api.testBitget(profile),
    refetchInterval: 10000, enabled: configured,
  })
  let state: ConnState = 'muted'
  let text = 'not configured'
  if (configured) {
    if (!data) { state = 'yellow'; text = 'checking…' }
    else if (data.connected) { state = 'green'; text = 'connected' }
    else { state = 'red'; text = `✗ ${data.error || 'unreachable'}` }
  }
  return <ConnectionStatus label={`bitget ${profile}`} state={state} text={text} />
}

function ExchangeCredentialsCard({ qc }: any) {
  const { data } = useQuery({ queryKey: ['bitgetCreds'], queryFn: api.getBitgetCredStatus })
  const [profile, setProfile] = useState<'demo' | 'live'>('demo')
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [passphrase, setPassphrase] = useState('')
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null)

  const cur = data?.[profile]
  const fullTriple = !!(apiKey && apiSecret && passphrase)

  const verifyAndSave = useMutation({
    mutationFn: async () => {
      setResult(null)
      if (fullTriple) {
        const test = await api.testBitgetCandidate({ profile, api_key: apiKey, api_secret: apiSecret, passphrase })
        if (!test.connected) throw new Error(test.error || 'connection failed')
      }
      await api.setExchangeCredentials({
        profile, api_key: apiKey || undefined, api_secret: apiSecret || undefined, passphrase: passphrase || undefined,
      })
      return fullTriple
    },
    onSuccess: (tested) => {
      setApiKey(''); setApiSecret(''); setPassphrase('')
      setResult({ ok: true, msg: tested ? '✓ verified & saved' : '✓ saved (partial)' })
      qc.invalidateQueries({ queryKey: ['bitgetCreds'] })
      qc.invalidateQueries({ queryKey: ['bitget-test', profile] })
    },
    onError: (e: any) => setResult({ ok: false, msg: `✗ ${e.message} — not saved` }),
  })

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div style={{ marginBottom: '0.8rem' }}>
        <ExchangeProfileStatus profile="demo" configured={!!data?.demo?.api_key?.configured} />
        <ExchangeProfileStatus profile="live" configured={!!data?.live?.api_key?.configured} />
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.8rem' }}>
        {(['demo', 'live'] as const).map(p => (
          <button key={p} className={profile === p ? '' : 'secondary'} onClick={() => { setProfile(p); setResult(null) }}>{p}</button>
        ))}
      </div>
      <div className="form-grid">
        <label>API Key {cur?.api_key?.configured && <span className="badge badge-green">··{cur.api_key.last4}</span>}
          <input type="password" value={apiKey} placeholder={cur?.api_key?.configured ? '•••••• (keep)' : 'paste'} onChange={e => setApiKey(e.target.value)} />
        </label>
        <label>API Secret {cur?.api_secret?.configured && <span className="badge badge-green">set</span>}
          <input type="password" value={apiSecret} placeholder={cur?.api_secret?.configured ? '•••••• (keep)' : 'paste'} onChange={e => setApiSecret(e.target.value)} />
        </label>
        <label>Passphrase {cur?.passphrase?.configured && <span className="badge badge-green">set</span>}
          <input type="password" value={passphrase} placeholder={cur?.passphrase?.configured ? '•••••• (keep)' : 'paste'} onChange={e => setPassphrase(e.target.value)} />
        </label>
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem', alignItems: 'center' }}>
        <button onClick={() => verifyAndSave.mutate()} disabled={verifyAndSave.isPending}>
          {verifyAndSave.isPending ? 'Verifying…' : `Verify & Save ${profile}`}
        </button>
        {result && <span className="hint" style={{ color: result.ok ? 'var(--green)' : 'var(--red)' }}>{result.msg}</span>}
      </div>
      <p className="hint">Enter all three fields to test the connection live before saving. Credentials are write-only.</p>
    </div>
  )
}

// ─── Trading parameters ───

function TradingParamsCard({ settings, qc }: any) {
  const trading = settings?.trading as any
  const [stopLoss, setStopLoss] = useState('')
  const [takeProfit, setTakeProfit] = useState('')
  const [holdHours, setHoldHours] = useState('')
  const [confidence, setConfidence] = useState('medium')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!trading) return
    setStopLoss(String(trading.default_stop_loss_pct ?? 2))
    setTakeProfit(String(trading.default_take_profit_pct ?? 4))
    setHoldHours(String(trading.max_position_hold_hours ?? 72))
    setConfidence(trading.min_confidence_to_trade ?? 'medium')
  }, [settings])

  const mut = useMutation({
    mutationFn: () => api.updateTradingConfig({
      default_stop_loss_pct: parseFloat(stopLoss),
      default_take_profit_pct: parseFloat(takeProfit),
      max_position_hold_hours: parseFloat(holdHours),
      min_confidence_to_trade: confidence,
    }),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  if (!trading) return null

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div className="form-grid" style={{ marginBottom: '0.75rem' }}>
        <label>Default stop-loss
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
            <input type="number" min={0.1} max={50} step={0.1} value={stopLoss}
              onChange={e => { setStopLoss(e.target.value); setMsg(null) }}
              style={{ width: '90px', fontFamily: 'var(--mono)' }} />
            <span style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>%</span>
          </div>
        </label>
        <label>Default take-profit
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
            <input type="number" min={0.1} max={100} step={0.1} value={takeProfit}
              onChange={e => { setTakeProfit(e.target.value); setMsg(null) }}
              style={{ width: '90px', fontFamily: 'var(--mono)' }} />
            <span style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>%</span>
          </div>
        </label>
        <label>Max hold time
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
            <input type="number" min={1} max={8760} step={1} value={holdHours}
              onChange={e => { setHoldHours(e.target.value); setMsg(null) }}
              style={{ width: '90px', fontFamily: 'var(--mono)' }} />
            <span style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>hours</span>
          </div>
        </label>
        <label>Min confidence to trade
          <select value={confidence} onChange={e => { setConfidence(e.target.value); setMsg(null) }}>
            {CONFIDENCE_LEVELS.map(c => <option key={c}>{c}</option>)}
          </select>
        </label>
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <button onClick={() => mut.mutate()} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save'}</button>
        <SaveMsg msg={msg} />
      </div>
      <p className="hint">Stop-loss / take-profit apply to new positions only. Existing positions keep their original exits.</p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// RISK & SYMBOLS TAB — allowed symbols, risk limits
// ─────────────────────────────────────────────────────────────────────────────

function RiskTab({ settings, qc }: any) {
  return (
    <div>
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

// ─── Symbols ───

function SymbolsCard({ settings, qc }: any) {
  const symbols: string[] = settings?.risk?.allowed_symbols ?? []
  const [draft, setDraft] = useState<string[]>([])
  const [input, setInput] = useState('')
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => { if (symbols.length) setDraft(symbols) }, [settings])

  const mut = useMutation({
    mutationFn: () => api.updateTradableSymbols(draft),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  const add = () => {
    const s = input.trim().toUpperCase()
    if (!s || draft.includes(s)) return
    setDraft(prev => [...prev, s]); setInput(''); setMsg(null)
  }
  const remove = (s: string) => { setDraft(prev => prev.filter(x => x !== s)); setMsg(null) }
  const dirty = JSON.stringify(draft.slice().sort()) !== JSON.stringify(symbols.slice().sort())

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem', marginBottom: '0.75rem', minHeight: '2rem' }}>
        {draft.map(s => (
          <span key={s} style={{
            display: 'inline-flex', alignItems: 'center', gap: '0.2rem',
            padding: '0.18rem 0.5rem', borderRadius: '4px',
            background: 'rgba(34,211,238,0.1)', border: '1px solid rgba(34,211,238,0.25)',
            fontSize: '0.79rem', fontFamily: 'var(--mono)',
          }}>
            {s}
            <button onClick={() => remove(s)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--muted)', padding: '0 0.1rem', lineHeight: 1, fontSize: '0.9rem' }} title={`Remove ${s}`}>×</button>
          </span>
        ))}
        {draft.length === 0 && <span className="hint" style={{ margin: 0 }}>No symbols — add at least one.</span>}
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          value={input} placeholder="e.g. SUIUSDT"
          onChange={e => setInput(e.target.value.toUpperCase())}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add() } }}
          style={{ width: '140px', fontFamily: 'var(--mono)', textTransform: 'uppercase' }}
        />
        <button className="secondary" onClick={add}>Add</button>
        {dirty && (
          <button onClick={() => mut.mutate()} disabled={mut.isPending || draft.length === 0}>
            {mut.isPending ? 'Saving…' : 'Save changes'}
          </button>
        )}
        <SaveMsg msg={msg} />
      </div>
      <p className="hint">
        Add any Bitget symbol pair (e.g. <code>SOLUSDT</code>, <code>SUIUSDT</code>, <code>DOTUSDT</code>).
        Changes take effect on the next agent cycle. Removing a symbol does not close open positions.
      </p>
      {/* Read-only: show the derived InstrumentId keys that will be observed */}
      {(settings?.market_data?.observed_instruments ?? []).length > 0 && (
        <div style={{ marginTop: '0.75rem', paddingTop: '0.6rem', borderTop: '1px solid var(--border)' }}>
          <span style={{ fontSize: '0.68rem', color: 'var(--muted)', marginBottom: '0.35rem', display: 'block' }}>
            Observed instruments (auto-derived · read-only)
          </span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
            {(settings.market_data.observed_instruments as string[]).map((id: string) => {
              const [exch, sym, mtype] = id.split(':')
              return (
                <span key={id} style={{
                  fontSize: '0.68rem', fontFamily: 'var(--mono)',
                  padding: '0.12rem 0.45rem', borderRadius: '3px',
                  background: 'rgba(100,100,120,0.12)', border: '1px solid rgba(100,100,120,0.25)',
                  color: 'var(--muted)',
                }} title={id}>
                  <span style={{ color: 'var(--accent)', opacity: 0.7 }}>{exch}</span>
                  {' '}{sym}{' '}
                  <span style={{ opacity: 0.6 }}>{mtype}</span>
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

interface NumField { key: string; label: string; unit: string; min: number; max: number; step: number; isInt?: boolean }
interface BoolField { key: string; label: string }

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
  { key: 'allow_short_live', label: 'Allow short selling (live)' },
  { key: 'allow_short_demo', label: 'Allow short selling (demo)' },
]

function NumGrid({ fields, vals, set }: { fields: NumField[]; vals: Record<string, any>; set: (k: string, v: any) => void }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: '0.5rem 1.5rem', marginBottom: '0.5rem' }}>
      {fields.map(f => (
        <label key={f.key} style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
          <span style={{ fontSize: '0.72rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{f.label}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
            <input type="number" min={f.min} max={f.max} step={f.step} value={vals[f.key] ?? ''}
              onChange={e => set(f.key, f.isInt ? parseInt(e.target.value) : parseFloat(e.target.value))}
              style={{ width: '100px', fontFamily: 'var(--mono)' }} />
            {f.unit && <span style={{ fontSize: '0.74rem', color: 'var(--muted)' }}>{f.unit}</span>}
          </div>
        </label>
      ))}
    </div>
  )
}

function RiskCard({ settings, qc }: any) {
  const risk = settings?.risk as any
  const [vals, setVals] = useState<Record<string, any>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!risk) return
    const init: Record<string, any> = {}
    ;[...RISK_LIVE, ...RISK_DEMO].forEach(f => { init[f.key] = risk[f.key] })
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
    <div className="card" style={{ marginBottom: 0 }}>
      {/* Immutable fields notice */}
      <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', padding: '0.45rem 0.75rem', background: 'var(--bg)', borderRadius: 'var(--r-sm)', border: '1px solid var(--border)', marginBottom: '1.25rem' }}>
        <span style={{ fontSize: '0.73rem', color: 'var(--muted)' }}>
          allowed_exchange: <span style={{ fontFamily: 'var(--mono)', color: 'var(--text)' }}>{risk.allowed_exchange}</span>
        </span>
        <span style={{ fontSize: '0.73rem', color: 'var(--muted)' }}>
          require_risk_check: <span className={`badge ${risk.require_risk_check_for_all_orders ? 'badge-green' : 'badge-red'}`} style={{ fontSize: '0.68rem' }}>{String(risk.require_risk_check_for_all_orders)}</span>
          <span style={{ marginLeft: '0.3rem', fontSize: '0.67rem' }}>locked</span>
        </span>
      </div>

      {/* Live limits */}
      <div style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--muted)', marginBottom: '0.5rem' }}>Live limits</div>
      <NumGrid fields={RISK_LIVE} vals={vals} set={set} />

      {/* Demo limits */}
      <div style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--muted)', margin: '1rem 0 0.5rem' }}>Demo / paper limits</div>
      <NumGrid fields={RISK_DEMO} vals={vals} set={set} />

      {/* Bool toggles */}
      <div style={{ display: 'flex', gap: '1.75rem', margin: '1rem 0', flexWrap: 'wrap' }}>
        {RISK_BOOLS.map(f => (
          <label key={f.key} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.82rem', cursor: 'pointer' }}>
            <input type="checkbox" checked={!!vals[f.key]} onChange={e => set(f.key, e.target.checked)} />
            {f.label}
          </label>
        ))}
      </div>

      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <button onClick={() => mut.mutate()} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save risk limits'}</button>
        <SaveMsg msg={msg} />
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// INFRASTRUCTURE TAB — display, app, health, retention, telegram, update, autostart
// ─────────────────────────────────────────────────────────────────────────────

function InfrastructureTab({ settings, qc }: any) {
  return (
    <div>
      <Section title="Display" description="Visual formatting applied across the dashboard.">
        <DisplayRow />
      </Section>

      <Section title="App" description="Dashboard process settings.">
        <AppRows settings={settings} qc={qc} />
      </Section>

      <Section title="Health Monitoring" description="Thresholds that determine when the system raises internal health alerts.">
        <HealthCard settings={settings} qc={qc} />
      </Section>

      <Section title="Data Retention" description="How long telemetry is kept before automatic pruning. Trust-bearing records (trades, audit log) are never pruned.">
        <RetentionCard settings={settings} qc={qc} />
      </Section>

      <Section title="Telegram" description="Alert delivery when agents act or limits are breached. Token is stored in secrets (write-only via CLI).">
        <TelegramCard settings={settings} qc={qc} />
      </Section>

      <Section title="Software Updates" description="On-demand self-upgrade via git pull. Never runs automatically.">
        <UpdateCard settings={settings} qc={qc} />
      </Section>

      <Section title="Autostart" description="Start the gateway automatically when you log in to Windows.">
        <AutostartCard qc={qc} />
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
      <span className="hint mono" style={{ margin: 0 }}>e.g. {fmtNum(1234.5, { decimals })} · {fmtNum(0, { decimals })}</span>
    </Row>
  )
}

function AppRows({ settings, qc }: any) {
  const app = settings?.app as any || {}
  const autoBrowser = app.auto_open_browser !== false

  const appMut = useMutation({
    mutationFn: (body: any) => api.updateAppConfig(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })
  const runtimeMut = useMutation({
    mutationFn: (body: any) => api.updateRuntimeConfig(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  const gitEnabled = !!(settings?.runtime as any)?.git_enabled

  return (
    <>
      <Row label="Dashboard port" hint={`Currently listening on :${app.dashboard_port || 8787}. Change requires gateway restart.`}>
        <span className="mono" style={{ fontSize: '0.82rem' }}>{app.dashboard_port || 8787}</span>
        <span style={{ fontSize: '0.72rem', color: 'var(--muted)' }}>(read-only — set via config.yaml)</span>
      </Row>
      <Row label="Auto-open browser" hint="Open the dashboard in a browser tab automatically when the gateway starts.">
        <Toggle checked={autoBrowser} onChange={v => appMut.mutate({ auto_open_browser: v })} disabled={appMut.isPending} />
      </Row>
      <Row label="Git versioning of data" hint="When enabled, knowledge, playbooks, and reports are committed to git automatically for history and rollback.">
        <Toggle checked={gitEnabled} onChange={v => runtimeMut.mutate({ git_enabled: v })} disabled={runtimeMut.isPending} />
      </Row>
    </>
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

  const numField = (key: string, label: string, unit: string, min: number, max: number, step: number) => (
    <label key={key} style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
      <span style={{ fontSize: '0.72rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
        <input type="number" min={min} max={max} step={step} value={vals[key] ?? ''}
          onChange={e => set(key, parseFloat(e.target.value))}
          style={{ width: '90px', fontFamily: 'var(--mono)' }} />
        {unit && <span style={{ fontSize: '0.74rem', color: 'var(--muted)' }}>{unit}</span>}
      </div>
    </label>
  )

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', marginBottom: '0.85rem' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.82rem', cursor: 'pointer' }}>
          <input type="checkbox" checked={!!vals.enabled} onChange={e => set('enabled', e.target.checked)} />
          Health monitoring enabled
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.82rem', cursor: 'pointer' }}>
          <input type="checkbox" checked={!!vals.alert_via_telegram} onChange={e => set('alert_via_telegram', e.target.checked)} />
          Alert via Telegram
        </label>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '0.5rem 1.5rem', marginBottom: '0.75rem' }}>
        {numField('max_agent_failure_ratio',     'Max agent failure ratio',      '',    0,    1,    0.01)}
        {numField('llm_unavailable_alert_after', 'LLM alert after N failures',  '',    1,    100,  1)}
        {numField('market_data_max_staleness_min','Market data staleness',       'min', 5,    1440, 1)}
        {numField('supervisor_max_restarts',     'Supervisor max restarts',      '',    1,    50,   1)}
        {numField('supervisor_restart_window_sec','Restart window',             'sec', 60,   86400,60)}
        {numField('supervisor_backoff_cap_sec',  'Backoff cap',                 'sec', 5,    3600, 1)}
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <button onClick={() => mut.mutate()} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save'}</button>
        <SaveMsg msg={msg} />
      </div>
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

  const fields: { key: string; label: string; unit: string; step: number }[] = [
    { key: 'agent_runs_days',          label: 'Agent runs',          unit: 'days', step: 1 },
    { key: 'tool_calls_days',          label: 'Tool calls',          unit: 'days', step: 1 },
    { key: 'run_steps_days',           label: 'Run steps',           unit: 'days', step: 1 },
    { key: 'market_snapshots_days',    label: 'Market snapshots',    unit: 'days', step: 1 },
    { key: 'portfolio_snapshots_days', label: 'Portfolio snapshots', unit: 'days', step: 1 },
    { key: 'jsonl_max_mb',             label: 'JSONL max size',      unit: 'MB',   step: 10 },
  ]

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '0.5rem 1.5rem', marginBottom: '0.75rem' }}>
        {fields.map(f => (
          <label key={f.key} style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
            <span style={{ fontSize: '0.72rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{f.label}</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
              <input type="number" min={1} step={f.step} value={vals[f.key] ?? ''}
                onChange={e => set(f.key, parseFloat(e.target.value))}
                style={{ width: '90px', fontFamily: 'var(--mono)' }} />
              <span style={{ fontSize: '0.74rem', color: 'var(--muted)' }}>{f.unit}</span>
            </div>
          </label>
        ))}
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <button onClick={() => mut.mutate()} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save'}</button>
        <SaveMsg msg={msg} />
      </div>
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
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!tg) return
    setEnabled(!!tg.enabled)
    setChatIdsInput((tg.allowed_chat_ids || []).join(', '))
  }, [settings])

  const mut = useMutation({
    mutationFn: () => {
      const ids = chatIdsInput.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n))
      return api.updateTelegramConfig({ enabled, allowed_chat_ids: ids })
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
    <div className="card" style={{ marginBottom: 0 }}>
      <div style={{ marginBottom: '0.75rem' }}>
        <ConnectionStatus label="telegram bot" state={state} text={connText} />
      </div>
      <div className="form-grid" style={{ marginBottom: '0.75rem' }}>
        <label>
          <span>Bot status</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.3rem' }}>
            <Toggle checked={enabled} onChange={v => { setEnabled(v); setMsg(null) }} />
            <span className={`badge ${enabled ? 'badge-green' : 'badge-red'}`}>{enabled ? 'ON' : 'OFF'}</span>
          </div>
        </label>
        <label>
          <span>Allowed chat IDs</span>
          <input
            value={chatIdsInput}
            placeholder="123456789, 987654321"
            onChange={e => { setChatIdsInput(e.target.value); setMsg(null) }}
            style={{ fontFamily: 'var(--mono)' }}
          />
        </label>
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <button onClick={() => mut.mutate()} disabled={mut.isPending}>{mut.isPending ? 'Saving…' : 'Save'}</button>
        <SaveMsg msg={msg} />
      </div>
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
    <div className="card" style={{ marginBottom: 0 }}>
      {/* Status line */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
        {badge}
        {data?.ok && (
          <span className="hint mono" style={{ margin: 0 }}>
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
      {applyMsg && <p className="hint" style={{ marginBottom: '0.75rem' }}>{applyMsg}</p>}

      {/* Config */}
      <div className="form-grid" style={{ marginBottom: '0.75rem' }}>
        <label>
          <span>Self-update enabled</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.3rem' }}>
            <Toggle checked={enabled} onChange={v => { setEnabled(v); setCfgMsg(null) }} />
          </div>
        </label>
        <label>
          <span>Branch</span>
          <input value={branch} onChange={e => { setBranch(e.target.value); setCfgMsg(null) }} style={{ fontFamily: 'var(--mono)' }} />
        </label>
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <button className="secondary" onClick={() => cfgMut.mutate()} disabled={cfgMut.isPending}>{cfgMut.isPending ? 'Saving…' : 'Save config'}</button>
        <SaveMsg msg={cfgMsg} />
      </div>
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
    if (!supported) badge = <span className="badge badge-yellow">not available</span>
    else if (enabled === true) badge = <span className="badge badge-green">enabled</span>
    else if (enabled === false) badge = <span className="badge">disabled</span>
    else badge = <span className="badge badge-red">unknown</span>
  }

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
        {badge}
        {supported === false ? (
          <span className="hint" style={{ margin: 0 }}>Not available{data?.reason ? ` (${data.reason})` : ''}. Uses Windows Task Scheduler.</span>
        ) : (
          <>
            {enabled === true && <button className="secondary" onClick={() => mut.mutate(false)} disabled={mut.isPending}>Disable</button>}
            {enabled === false && <button onClick={() => mut.mutate(true)} disabled={mut.isPending}>Enable</button>}
            {enabled === null && <span className="hint" style={{ color: 'var(--yellow)', margin: 0 }}>Could not read task state{data?.reason ? ` (${data.reason})` : ''}</span>}
            <button className="secondary" onClick={() => qc.invalidateQueries({ queryKey: ['autostart'] })}>Re-check</button>
          </>
        )}
        {msg && <span className="hint" style={{ margin: 0 }}>{msg}</span>}
      </div>
      <p className="hint" style={{ marginTop: '0.6rem' }}>Starts the gateway on login and watchdogs it every 5 min. Runs gated start — live trading stays disabled.</p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Model provider (full-width card used inside Intelligence tab)
// ─────────────────────────────────────────────────────────────────────────────

function ProviderStatusList({ data }: any) {
  const { data: health = [] } = useQuery({ queryKey: ['model-health'], queryFn: api.getModelHealth, refetchInterval: 5000 })
  if (!data) return null
  return (
    <div style={{ marginBottom: '0.75rem' }}>
      {PROVIDERS.map(p => {
        const h = (health as any[]).find(x => x.provider === p)
        const cfg = data.providers?.[p]
        const configured = p === 'codex_cli' ? undefined : cfg?.api_key?.configured
        let state: ConnState = 'muted'
        let text = 'unknown'
        if (h?.available) { state = 'green'; text = 'connected' }
        else if (p === 'codex_cli' && h?.error === 'not_installed') { state = 'yellow'; text = 'not configured' }
        else if (p === 'codex_cli' && h?.error?.includes('disabled')) { state = 'muted'; text = 'disabled' }
        else if (configured === false) { state = 'muted'; text = 'not configured' }
        else if (h && !h.available) { state = 'red'; text = `✗ ${h.error || 'unreachable'}` }
        const detail: string[] = []
        if (cfg?.model && cfg.model !== 'default') detail.push(`model: ${cfg.model}`)
        if (cfg?.api_key?.configured) detail.push(`··${cfg.api_key.last4}`)
        return <ConnectionStatus key={p} label={p} state={state} text={text} detail={detail.join(' · ') || undefined} isDefault={p === data.default_provider} />
      })}
    </div>
  )
}

function ModelProviderCard({ qc }: any) {
  const { data } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig })
  const [provider, setProvider] = useState('openai_compatible')
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [protocol, setProtocol] = useState('chat_completions')
  const [apiKey, setApiKey] = useState('')
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null)

  const cur = data?.providers?.[provider]

  const verifyAndSave = useMutation({
    mutationFn: async () => {
      setResult(null)
      const test = await api.testModelCandidate({
        provider, base_url: baseUrl || cur?.base_url || undefined,
        model: model || cur?.model || undefined, protocol, api_key: apiKey || undefined,
      })
      if (!test.available) throw new Error(test.error || 'provider unreachable')
      await api.setModelProvider({
        provider, base_url: baseUrl || undefined, model: model || undefined,
        protocol, default: true, api_key: apiKey || undefined,
      })
      return test
    },
    onSuccess: (test) => {
      setApiKey('')
      setResult({ ok: true, msg: `✓ verified & saved (${test.model || 'ok'})` })
      qc.invalidateQueries({ queryKey: ['model'] })
      qc.invalidateQueries({ queryKey: ['model-health'] })
      qc.invalidateQueries({ queryKey: ['status'] })
    },
    onError: (e: any) => setResult({ ok: false, msg: `✗ ${e.message} — not saved` }),
  })

  const isCodex = provider === 'codex_cli'

  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <ProviderStatusList data={data} />
      <div className="form-grid">
        <label>Provider
          <select value={provider} onChange={e => { setProvider(e.target.value); setResult(null) }}>
            {PROVIDERS.map(p => <option key={p}>{p}</option>)}
          </select>
        </label>
        {!isCodex && <>
          <label>Base URL
            <input value={baseUrl} placeholder={cur?.base_url || 'http://localhost:8000/v1'} onChange={e => setBaseUrl(e.target.value)} />
          </label>
          <label>Model
            <input value={model} placeholder={cur?.model || 'default'} onChange={e => setModel(e.target.value)} />
          </label>
          <label>Protocol
            <select value={protocol} onChange={e => setProtocol(e.target.value)}>
              <option value="chat_completions">chat_completions</option>
              <option value="responses">responses</option>
            </select>
          </label>
          <label>API Key {cur?.api_key?.configured && <span className="badge badge-green">configured ··{cur.api_key.last4}</span>}
            <input type="password" value={apiKey} placeholder={cur?.api_key?.configured ? '•••••• (leave blank to keep)' : 'paste key'} onChange={e => setApiKey(e.target.value)} />
          </label>
        </>}
      </div>
      {isCodex
        ? <CodexPanel qc={qc} />
        : <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem', alignItems: 'center' }}>
            <button onClick={() => verifyAndSave.mutate()} disabled={verifyAndSave.isPending}>
              {verifyAndSave.isPending ? 'Verifying…' : 'Verify & Save'}
            </button>
            {result && <span style={{ fontSize: '0.78rem', color: result.ok ? 'var(--green)' : 'var(--red)' }}>{result.msg}</span>}
          </div>}
      <p className="hint">API keys are write-only — never shown after save.</p>
    </div>
  )
}

function CodexPanel({ qc }: any) {
  const { data: health } = useQuery({ queryKey: ['model-health'], queryFn: api.getModelHealth, refetchInterval: 5000 })
  const cx = (health || []).find((h: any) => h.provider === 'codex_cli')
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
  const { data: models = [] } = useQuery({ queryKey: ['codex-models'], queryFn: api.getCodexModels, enabled: cx?.available === true })
  const pickModel = useMutation({
    mutationFn: (slug: string) => api.setModelProvider({ provider: 'codex_cli', codex_model: slug, enabled: true, default: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
  })

  if (!cx) return <p className="hint">Checking Codex status…</p>
  if (cx.error === 'not_installed') return <p className="hint"><span className="badge badge-yellow">not configured</span> Codex CLI not found in PATH. Install it and log in with your ChatGPT subscription.</p>
  if (cx.available) {
    return (
      <div>
        <p><span className="badge badge-green">connected</span> via ChatGPT subscription.</p>
        <label>Model
          <select value={selectedModel} onChange={e => pickModel.mutate(e.target.value)}>
            <option value="">codex default</option>
            {models.map(m => <option key={m.slug} value={m.slug}>{m.display_name}</option>)}
          </select>
        </label>
        <p className="hint">OAuth token managed by Codex CLI (~/.codex). Aletheia stores no token.</p>
      </div>
    )
  }
  return (
    <div>
      <p><span className="badge badge-red">not logged in</span> Codex CLI installed but no ChatGPT login.</p>
      {login?.state === 'awaiting_authorization' && login.verification_uri
        ? <div>
            <p>1. Open <a href={login.verification_uri} target="_blank" rel="noreferrer">{login.verification_uri}</a></p>
            <p>2. Enter code: <strong>{login.user_code}</strong></p>
            <p className="hint">Waiting… ({statusPoll.data?.state || 'polling'})</p>
            <button className="secondary" onClick={() => cancel.mutate()}>Cancel</button>
          </div>
        : login?.state === 'failed'
        ? <p className="hint"><span className="badge badge-red">failed</span> {login.error === 'device_auth_parse_failed' ? 'Run `codex login` in a terminal instead.' : login.error}</p>
        : <button onClick={() => start.mutate()} disabled={start.isPending}>Connect Codex</button>}
    </div>
  )
}
