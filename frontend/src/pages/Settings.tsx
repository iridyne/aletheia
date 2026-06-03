import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

const PERMISSION_MODES = ['ReadOnly', 'Research', 'PaperAuto', 'AllowEditing', 'Auto']
const PROVIDERS = ['openai_compatible', 'anthropic_compatible', 'codex_cli']

export default function Settings() {
  const qc = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })

  if (!status) return <p>Loading...</p>

  return (
    <div>
      <h2 className="page-title">Settings</h2>
      <KillSwitchCard qc={qc} />
      <PermissionCard status={status} qc={qc} />
      <GatewayCard status={status} qc={qc} />
      <ModelProviderCard />
      <ExchangeCredentialsCard />
      <ExchangeModeCard status={status} qc={qc} />
      <LiveStateCard status={status} qc={qc} />
      <RiskCard settings={settings} />
      <TelegramCard />
    </div>
  )
}

function KillSwitchCard({ qc }: any) {
  const { data } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch })
  const mut = useMutation({
    mutationFn: (active: boolean) => api.setKillSwitch(active),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['kill-switch'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
  })
  const active = !!data?.active
  return (
    <div className="card">
      <h3>Kill Switch <span className={`badge ${active ? 'badge-red' : 'badge-green'}`}>{active ? 'ENGAGED' : 'inactive'}</span></h3>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button
          onClick={() => { if (confirm('Engage the kill switch? ALL trading proposals (paper and live) will be rejected until released.')) mut.mutate(true) }}
          disabled={active}
        >Engage Kill Switch</button>
        <button className="secondary" onClick={() => mut.mutate(false)} disabled={!active}>Release</button>
      </div>
      <p className="hint">Global trading brake. Rejects every proposal regardless of mode. Auto-engages if the daily-loss limit is breached. All changes are audited.</p>
    </div>
  )
}

function PermissionCard({ status, qc }: any) {
  const mut = useMutation({
    mutationFn: (mode: string) => api.setPermissionMode(mode),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })
  return (
    <div className="card">
      <h3>Permission Mode</h3>
      <select value={String(status.permission_mode)} onChange={e => mut.mutate(e.target.value)}>
        {PERMISSION_MODES.map(m => <option key={m} value={m}>{m}</option>)}
      </select>
      <p className="hint">Controls how much autonomy agents have. Changes are audited.</p>
    </div>
  )
}

function GatewayCard({ status, qc }: any) {
  const pause = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resume = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  return (
    <div className="card">
      <h3>Gateway Control</h3>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button onClick={() => pause.mutate()} disabled={!!status.paused}>Pause All</button>
        <button onClick={() => resume.mutate()} disabled={!status.paused}>Resume</button>
      </div>
      <p className="hint">Pausing stops all autonomous agent and trading jobs. Dashboard stays accessible.</p>
    </div>
  )
}

function ModelProviderCard() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig })
  const [provider, setProvider] = useState('openai_compatible')
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [protocol, setProtocol] = useState('chat_completions')
  const [apiKey, setApiKey] = useState('')
  const [health, setHealth] = useState<string>('')

  const cur = data?.providers?.[provider]
  const save = useMutation({
    mutationFn: () => api.setModelProvider({
      provider, base_url: baseUrl || undefined, model: model || undefined,
      protocol, default: true, api_key: apiKey || undefined,
    }),
    onSuccess: () => { setApiKey(''); qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
  })
  const test = useMutation({
    mutationFn: api.getModelHealth,
    onSuccess: (results) => {
      const h = results.find(r => r.provider === provider)
      setHealth(h ? (h.available ? '✓ available' : `✗ ${h.error}`) : 'unknown')
    },
  })

  return (
    <div className="card">
      <h3>Model Provider</h3>
      <div className="form-grid">
        <label>Provider
          <select value={provider} onChange={e => setProvider(e.target.value)}>
            {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
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
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
        <button onClick={() => save.mutate()}>Save</button>
        <button className="secondary" onClick={() => test.mutate()}>Test</button>
        {health && <span className="hint" style={{ alignSelf: 'center' }}>{health}</span>}
      </div>
      <p className="hint">Default provider: <strong>{data?.default_provider}</strong>. API keys are write-only — never shown after save.</p>
    </div>
  )
}

function ExchangeCredentialsCard() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['bitgetCreds'], queryFn: api.getBitgetCredStatus })
  const [profile, setProfile] = useState<'demo' | 'live'>('demo')
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [passphrase, setPassphrase] = useState('')
  const [testResult, setTestResult] = useState('')

  const cur = data?.[profile]
  const save = useMutation({
    mutationFn: () => api.setExchangeCredentials({
      profile, api_key: apiKey || undefined, api_secret: apiSecret || undefined, passphrase: passphrase || undefined,
    }),
    onSuccess: () => { setApiKey(''); setApiSecret(''); setPassphrase(''); qc.invalidateQueries({ queryKey: ['bitgetCreds'] }) },
  })
  const test = useMutation({
    mutationFn: () => api.testBitget(profile),
    onSuccess: (r) => setTestResult(r.connected ? `✓ connected (${r.mode})` : `✗ ${r.error}`),
  })

  return (
    <div className="card">
      <h3>Exchange Credentials (Bitget)</h3>
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
        {(['demo', 'live'] as const).map(p => (
          <button key={p} className={profile === p ? '' : 'secondary'} onClick={() => setProfile(p)}>{p}</button>
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
      <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
        <button onClick={() => save.mutate()}>Save {profile} creds</button>
        <button className="secondary" onClick={() => test.mutate()}>Test Connection</button>
        {testResult && <span className="hint" style={{ alignSelf: 'center' }}>{testResult}</span>}
      </div>
      <p className="hint">Demo keys are created in Bitget demo mode. Credentials are write-only.</p>
    </div>
  )
}

function ExchangeModeCard({ status, qc }: any) {
  const mut = useMutation({
    mutationFn: (mode: string) => api.setExchangeMode(mode),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })
  return (
    <div className="card">
      <h3>Exchange Mode</h3>
      <select value={String(status.exchange_mode)} onChange={e => mut.mutate(e.target.value)}>
        <option value="demo">demo</option>
        <option value="live">live</option>
      </select>
      <p className="hint">Demo uses Bitget paper trading. Live still requires unlocking below.</p>
    </div>
  )
}

function LiveStateCard({ status, qc }: any) {
  const unlock = useMutation({ mutationFn: api.liveUnlock, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const lock = useMutation({ mutationFn: api.liveLock, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const isDisabled = status.live_state === 'live_disabled'
  return (
    <div className="card">
      <h3>Live State <span className={`badge ${isDisabled ? 'badge-red' : 'badge-yellow'}`}>{String(status.live_state)}</span></h3>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button
          onClick={() => { if (confirm('Unlock live-limited trading? This enables real orders within hard risk limits.')) unlock.mutate() }}
          disabled={!isDisabled}
        >Unlock Live-Limited</button>
        <button className="secondary" onClick={() => lock.mutate()} disabled={isDisabled}>Lock</button>
      </div>
      <p className="hint">Live trading is disabled by default. Unlocking requires explicit confirmation and is audited.</p>
    </div>
  )
}

function RiskCard({ settings }: any) {
  const risk = settings?.risk
  if (!risk) return null
  return (
    <div className="card">
      <h3>Risk Boundaries (read-only)</h3>
      <table>
        <tbody>
          {Object.entries(risk).map(([k, v]) => (
            <tr key={k}><th>{k}</th><td>{Array.isArray(v) ? v.join(', ') : String(v)}</td></tr>
          ))}
        </tbody>
      </table>
      <p className="hint">Hard limits enforced by the Risk Engine. Agents cannot modify these.</p>
    </div>
  )
}

function TelegramCard() {
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
  const tg = (settings?.telegram as any) || {}
  return (
    <div className="card">
      <h3>Telegram</h3>
      <p>Enabled: <span className={`badge ${tg.enabled ? 'badge-green' : 'badge-red'}`}>{String(tg.enabled)}</span></p>
      <p className="hint">Allowed chat IDs: {(tg.allowed_chat_ids || []).join(', ') || 'none'}. Configure the bot token via <code>aletheia secrets set telegram.bot_token</code>.</p>
    </div>
  )
}
