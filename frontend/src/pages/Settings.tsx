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
      <SoftwareUpdateCard />
    </div>
  )
}

function SoftwareUpdateCard() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['update-status'], queryFn: api.getUpdateStatus })
  const [msg, setMsg] = useState('')
  const apply = useMutation({
    mutationFn: api.applyUpdate,
    onSuccess: (r) => {
      setMsg(r.applied
        ? `Updated to ${r.new_commit?.slice(0, 8)} (deps reinstalled: ${r.deps_reinstalled}). ${r.message ?? ''}`
        : `Not applied: ${r.reason}${r.message ? ` — ${r.message}` : ''}`)
      qc.invalidateQueries({ queryKey: ['update-status'] })
    },
  })

  let badge = <span className="badge badge-yellow">not available</span>
  if (isLoading) badge = <span className="badge">checking…</span>
  else if (data?.ok && data.has_update) badge = <span className="badge badge-yellow">{data.behind_count} behind</span>
  else if (data?.ok && !data.has_update) badge = <span className="badge badge-green">up to date</span>

  return (
    <div className="card">
      <h3>Software Update {badge}</h3>
      {data?.ok ? (
        <>
          <p className="hint">
            Branch <code>{data.branch}</code> · current <code>{data.current_commit?.slice(0, 8)}</code>
            {data.has_update && <> · remote <code>{data.remote_commit?.slice(0, 8)}</code></>}
          </p>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button className="secondary" onClick={() => qc.invalidateQueries({ queryKey: ['update-status'] })}>Re-check</button>
            {data.has_update && (
              <button
                onClick={() => { if (confirm('Pull latest code and apply update? You must restart the gateway afterward.')) apply.mutate() }}
                disabled={apply.isPending}
              >Apply update</button>
            )}
          </div>
        </>
      ) : (
        <p className="hint">Update status not available{data?.reason ? ` (${data.reason})` : ''}. Requires a git checkout.</p>
      )}
      {msg && <p className="hint">{msg}</p>}
      <p className="hint">On-demand only. Applying pulls from git; restart the gateway to load new code. Never runs automatically.</p>
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

  const isCodex = provider === 'codex_cli'

  return (
    <div className="card">
      <h3>Model Provider</h3>
      <div className="form-grid">
        <label>Provider
          <select value={provider} onChange={e => setProvider(e.target.value)}>
            {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
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
        : <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
            <button onClick={() => save.mutate()}>Save</button>
            <button className="secondary" onClick={() => test.mutate()}>Test</button>
            {health && <span className="hint" style={{ alignSelf: 'center' }}>{health}</span>}
          </div>}
      <p className="hint">Default provider: <strong>{data?.default_provider}</strong>. API keys are write-only — never shown after save.</p>
    </div>
  )
}

function CodexPanel({ qc }: any) {
  const { data: health } = useQuery({
    queryKey: ['model-health'], queryFn: api.getModelHealth, refetchInterval: 5000,
  })
  const cx = (health || []).find((h: any) => h.provider === 'codex_cli')
  const { data: cfg } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig })
  const selectedModel = cfg?.providers?.codex_cli?.codex_model || ''

  const [login, setLogin] = useState<{ state: string; verification_uri: string | null; user_code: string | null; error: string | null } | null>(null)

  // Poll login status while a device-auth flow is awaiting authorization.
  const statusPoll = useQuery({
    queryKey: ['codex-login-status'],
    queryFn: api.codexLoginStatus,
    enabled: login?.state === 'awaiting_authorization',
    refetchInterval: 3000,
  })
  if (statusPoll.data && statusPoll.data.state !== login?.state) {
    if (statusPoll.data.state === 'completed') {
      qc.invalidateQueries({ queryKey: ['model-health'] })
      qc.invalidateQueries({ queryKey: ['status'] })
    }
  }

  const start = useMutation({ mutationFn: api.codexLoginStart, onSuccess: (r) => setLogin(r) })
  const cancel = useMutation({ mutationFn: api.codexLoginCancel, onSuccess: () => setLogin(null) })
  const { data: models = [] } = useQuery({
    queryKey: ['codex-models'], queryFn: api.getCodexModels,
    enabled: cx?.available === true,
  })
  const pickModel = useMutation({
    mutationFn: (slug: string) => api.setModelProvider({ provider: 'codex_cli', codex_model: slug, enabled: true, default: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['model'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
  })

  if (!cx) return <p className="hint">Checking Codex status…</p>

  // not installed → honest "not configured", no Connect button
  if (cx.error === 'not_installed') {
    return <p className="hint"><span className="badge badge-yellow">not configured</span> Codex CLI not found in PATH. Install the Codex CLI and log in with your ChatGPT subscription.</p>
  }

  // available → connected + model picker
  if (cx.available) {
    return (
      <div>
        <p><span className="badge badge-green">connected</span> via ChatGPT subscription (codex login).</p>
        <label>Model
          <select value={selectedModel} onChange={e => pickModel.mutate(e.target.value)}>
            <option value="">codex default</option>
            {models.map(m => <option key={m.slug} value={m.slug}>{m.display_name}</option>)}
          </select>
        </label>
        <p className="hint">Real OAuth + token are managed by the Codex CLI (~/.codex). Aletheia stores no token.</p>
      </div>
    )
  }

  // present but not logged in → Connect Codex (device-auth)
  return (
    <div>
      <p><span className="badge badge-red">not logged in</span> Codex CLI is installed but no ChatGPT login.</p>
      {login?.state === 'awaiting_authorization' && login.verification_uri
        ? <div>
            <p>1. Open <a href={login.verification_uri} target="_blank" rel="noreferrer">{login.verification_uri}</a></p>
            <p>2. Enter code: <strong>{login.user_code}</strong></p>
            <p className="hint">Waiting for authorization… (status: {statusPoll.data?.state || 'polling'})</p>
            <button className="secondary" onClick={() => cancel.mutate()}>Cancel</button>
          </div>
        : login?.state === 'failed'
        ? <p className="hint"><span className="badge badge-red">login failed</span> {login.error === 'device_auth_parse_failed'
            ? 'Could not parse device login on this Codex version. Run `codex login` in a terminal, then it will show as connected.'
            : login.error}</p>
        : <button onClick={() => start.mutate()} disabled={start.isPending}>Connect Codex</button>}
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
