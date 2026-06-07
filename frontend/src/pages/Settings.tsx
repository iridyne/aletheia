import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import ConnectionStatus, { ConnState } from '../components/ConnectionStatus'

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
      <EffectiveConfigCard />
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
      <AutostartCard />
    </div>
  )
}

function EffectiveConfigCard() {
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
  if (!settings) return null
  const runtime = settings.runtime as any || {}
  const agent = settings.agent as any || {}
  const flags = [
    { label: 'Paused', value: runtime.paused, warn: runtime.paused },
    { label: 'Scheduler Enabled', value: runtime.scheduler_enabled },
    { label: 'Permission Mode', value: agent.permission_mode },
    { label: 'Autonomous Learning', value: agent.autonomous_learning },
    { label: 'Autonomous Paper Trading', value: agent.autonomous_paper_trading, warn: agent.permission_mode === 'PaperAuto' && !agent.autonomous_paper_trading },
    { label: 'Kill Switch Active', value: runtime.kill_switch_active, warn: runtime.kill_switch_active },
  ]
  return (
    <div className="card">
      <h3>Effective Config (read-only)</h3>
      <table>
        <tbody>
          {flags.map(f => (
            <tr key={f.label}>
              <th>{f.label}</th>
              <td>
                <span className={`badge ${f.value === true ? 'badge-green' : f.value === false ? 'badge-red' : 'badge-blue'}`}>
                  {String(f.value ?? '—')}
                </span>
                {f.warn && <span style={{ marginLeft: '0.4rem', color: 'var(--yellow)', fontSize: '0.75rem' }}>warning</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">Live snapshot of runtime + agent flags that govern system behavior. Yellow = requires attention.</p>
    </div>
  )
}

function AutostartCard() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['autostart'], queryFn: api.getAutostartStatus })
  const [msg, setMsg] = useState('')
  const mut = useMutation({
    mutationFn: (enabled: boolean) => api.setAutostart(enabled),
    onSuccess: (r) => {
      if (r.ok === false) setMsg(`Failed: ${r.reason}${r.detail ? ` — ${r.detail}` : ''}`)
      else setMsg(r.enabled ? 'Autostart enabled.' : 'Autostart disabled.')
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
    <div className="card">
      <h3>Autostart &amp; Watchdog {badge}</h3>
      {!isLoading && data && !supported ? (
        <p className="hint">Not available on this OS{data.reason ? ` (${data.reason})` : ''}. Autostart uses the Windows Task Scheduler.</p>
      ) : (
        <>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            {enabled === true ? (
              <button className="secondary" onClick={() => mut.mutate(false)} disabled={mut.isPending}>Disable autostart</button>
            ) : (
              <button onClick={() => mut.mutate(true)} disabled={mut.isPending || enabled === null}>Enable autostart</button>
            )}
            <button className="secondary" onClick={() => qc.invalidateQueries({ queryKey: ['autostart'] })}>Re-check</button>
          </div>
          {enabled === null && (
            <p className="hint"><span className="badge badge-red">unknown</span> Could not read the task state{data?.reason ? ` (${data.reason})` : ''}. Try running the dashboard from your own user account.</p>
          )}
        </>
      )}
      {msg && <p className="hint">{msg}</p>}
      <p className="hint">Starts the gateway automatically when you log in, and re-checks every 5 minutes so the supervisor is restarted if it ever dies. Runs the same gated start as <code>aletheia gateway start</code> — live trading stays disabled. All changes are audited.</p>
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
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
  const mut = useMutation({
    mutationFn: (mode: string) => api.setPermissionMode(mode),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['status'] }); qc.invalidateQueries({ queryKey: ['settings'] }) },
  })
  const autoPaper = useMutation({
    mutationFn: (enabled: boolean) => api.setAutonomousPaperTrading(enabled),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['status'] }) },
  })
  const autonomousEnabled = !!(settings?.agent as any)?.autonomous_paper_trading
  const isPaperAuto = String(status.permission_mode) === 'PaperAuto'
  return (
    <div className="card">
      <h3>Permission Mode</h3>
      <select value={String(status.permission_mode)} onChange={e => mut.mutate(e.target.value)}>
        {PERMISSION_MODES.map(m => <option key={m} value={m}>{m}</option>)}
      </select>
      <div style={{ marginTop: '0.6rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.82rem', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={autonomousEnabled}
            onChange={e => autoPaper.mutate(e.target.checked)}
            disabled={autoPaper.isPending}
          />
          Autonomous paper trading
        </label>
        <span className={`badge ${autonomousEnabled ? 'badge-green' : 'badge-red'}`}>{autonomousEnabled ? 'ON' : 'OFF'}</span>
      </div>
      {isPaperAuto && !autonomousEnabled && (
        <p className="hint" style={{ color: 'var(--yellow)' }}>PaperAuto mode is selected but autonomous paper trading is OFF — the scheduler will not auto-submit paper orders until this flag is enabled.</p>
      )}
      <p className="hint">Controls how much autonomy agents have. Changes are audited. Autonomous paper trading is default OFF — enable it to allow the scheduler to auto-submit paper trades when in PaperAuto or higher modes.</p>
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

function ProviderStatusList({ data }: any) {
  // Facet A: at-a-glance real state for every provider, live-polled (5s, shared key
  // with CodexPanel so the queries dedupe).
  const { data: health = [] } = useQuery({
    queryKey: ['model-health'], queryFn: api.getModelHealth, refetchInterval: 5000,
  })
  if (!data) return null
  return (
    <div style={{ marginBottom: '0.6rem' }}>
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
        const detailParts: string[] = []
        if (cfg?.model && cfg.model !== 'default') detailParts.push(`model: ${cfg.model}`)
        if (cfg?.api_key?.configured) detailParts.push(`··${cfg.api_key.last4}`)
        return (
          <ConnectionStatus
            key={p} label={p} state={state} text={text}
            detail={detailParts.join(' · ') || undefined}
            isDefault={p === data.default_provider}
          />
        )
      })}
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
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null)

  const cur = data?.providers?.[provider]

  // Facet B: verify-before-commit. Test the candidate config first; persist only on pass.
  const verifyAndSave = useMutation({
    mutationFn: async () => {
      setResult(null)
      const test = await api.testModelCandidate({
        provider,
        base_url: baseUrl || cur?.base_url || undefined,
        model: model || cur?.model || undefined,
        protocol,
        api_key: apiKey || undefined,
      })
      if (!test.available) {
        throw new Error(test.error || 'provider unreachable')
      }
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
    <div className="card">
      <h3>Model Provider</h3>
      <ProviderStatusList data={data} />
      <div className="form-grid">
        <label>Provider
          <select value={provider} onChange={e => { setProvider(e.target.value); setResult(null) }}>
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
        : <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem', alignItems: 'center' }}>
            <button onClick={() => verifyAndSave.mutate()} disabled={verifyAndSave.isPending}>
              {verifyAndSave.isPending ? 'Verifying…' : 'Verify & Save'}
            </button>
            {result && <span className="hint" style={{ alignSelf: 'center', color: result.ok ? 'var(--green)' : 'var(--red)' }}>{result.msg}</span>}
          </div>}
      <p className="hint">"Verify &amp; Save" tests the endpoint live and only saves if it responds. API keys are write-only — never shown after save.</p>
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

function ExchangeProfileStatus({ profile, configured }: { profile: 'demo' | 'live'; configured: boolean }) {
  // Facet A: live reachability of the SAVED creds for this profile (10s poll, only
  // when configured so we never spam errors for an empty profile).
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

function ExchangeCredentialsCard() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['bitgetCreds'], queryFn: api.getBitgetCredStatus })
  const [profile, setProfile] = useState<'demo' | 'live'>('demo')
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [passphrase, setPassphrase] = useState('')
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null)

  const cur = data?.[profile]
  const fullTriple = !!(apiKey && apiSecret && passphrase)

  // Facet B: verify-before-commit. When a full credential triple is supplied, test it
  // live and persist only on success. (Bitget signing needs all three together and the
  // client reads creds from the store at use, so a candidate test requires the full set.)
  const verifyAndSave = useMutation({
    mutationFn: async () => {
      setResult(null)
      if (fullTriple) {
        const test = await api.testBitgetCandidate({
          profile, api_key: apiKey, api_secret: apiSecret, passphrase,
        })
        if (!test.connected) throw new Error(test.error || 'connection failed')
      }
      await api.setExchangeCredentials({
        profile, api_key: apiKey || undefined, api_secret: apiSecret || undefined, passphrase: passphrase || undefined,
      })
      return fullTriple
    },
    onSuccess: (tested) => {
      setApiKey(''); setApiSecret(''); setPassphrase('')
      setResult({ ok: true, msg: tested ? '✓ verified & saved' : '✓ saved (partial — live badge will confirm)' })
      qc.invalidateQueries({ queryKey: ['bitgetCreds'] })
      qc.invalidateQueries({ queryKey: ['bitget-test', profile] })
    },
    onError: (e: any) => setResult({ ok: false, msg: `✗ ${e.message} — not saved` }),
  })

  return (
    <div className="card">
      <h3>Exchange Credentials (Bitget)</h3>
      <div style={{ marginBottom: '0.6rem' }}>
        <ExchangeProfileStatus profile="demo" configured={!!data?.demo?.api_key?.configured} />
        <ExchangeProfileStatus profile="live" configured={!!data?.live?.api_key?.configured} />
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
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
      <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem', alignItems: 'center' }}>
        <button onClick={() => verifyAndSave.mutate()} disabled={verifyAndSave.isPending}>
          {verifyAndSave.isPending ? 'Verifying…' : `Verify & Save ${profile} creds`}
        </button>
        {result && <span className="hint" style={{ alignSelf: 'center', color: result.ok ? 'var(--green)' : 'var(--red)' }}>{result.msg}</span>}
      </div>
      <p className="hint">Enter all three fields to test the connection live before saving — a failed test is rejected, not stored. Demo keys are created in Bitget demo mode. Credentials are write-only.</p>
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
  const [warning, setWarning] = useState('')

  // Pre-flight (warn-only): verify the LIVE creds actually reach Bitget before unlock.
  // This does NOT replace the backend gate — it surfaces a bad-creds state honestly so
  // the user isn't unlocking into a broken connection. The user may still proceed.
  const preflightUnlock = async () => {
    setWarning('')
    let proceedMsg = 'Unlock live-limited trading? This enables real orders within hard risk limits.'
    try {
      const r = await api.testBitget('live')
      if (!r.connected) {
        setWarning(`Live credentials not reachable: ${r.error || 'unknown'}. Fix in Exchange Credentials above before trading.`)
        proceedMsg = `⚠ Live creds did NOT connect (${r.error || 'unknown'}). Unlock anyway?`
      }
    } catch {
      // probe failure shouldn't block the human decision
    }
    if (confirm(proceedMsg)) unlock.mutate()
  }

  return (
    <div className="card">
      <h3>Live State <span className={`badge ${isDisabled ? 'badge-red' : 'badge-yellow'}`}>{String(status.live_state)}</span></h3>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button onClick={preflightUnlock} disabled={!isDisabled}>Unlock Live-Limited</button>
        <button className="secondary" onClick={() => lock.mutate()} disabled={isDisabled}>Lock</button>
      </div>
      {warning && <p className="hint" style={{ color: 'var(--red)' }}>⚠ {warning}</p>}
      <p className="hint">Live trading is disabled by default. Unlocking runs a credential pre-flight, requires explicit confirmation, and is audited.</p>
    </div>
  )
}

const RISK_UNITS: Record<string, string> = {
  max_daily_loss_pct: '%', max_position_notional_usdt: 'USDT',
  max_open_positions: 'positions', max_leverage: 'x',
  max_single_trade_pct: '%', max_consecutive_losses_halt: 'losses',
  cooldown_after_loss_minutes: 'min',
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
            <tr key={k}>
              <th>{k.replace(/_/g, ' ')}</th>
              <td>
                {Array.isArray(v) ? v.join(', ') : String(v)}
                {RISK_UNITS[k] && <span className="text-muted text-sm" style={{ marginLeft: '0.3rem' }}>{RISK_UNITS[k]}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">Hard limits enforced by the Risk Engine. Agents cannot modify these.</p>
    </div>
  )
}

function TelegramCard() {
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings })
  const { data: health } = useQuery({
    queryKey: ['telegram-health'], queryFn: api.getTelegramHealth, refetchInterval: 30000,
  })
  const tg = (settings?.telegram as any) || {}

  let state: ConnState = 'muted'
  let text = 'not configured'
  if (health) {
    if (health.reachable) { state = 'green'; text = health.username ? `@${health.username}` : 'reachable' }
    else if (health.error === 'not_configured') { state = 'muted'; text = 'not configured' }
    else { state = 'red'; text = `✗ ${health.error || 'unreachable'}` }
  }

  return (
    <div className="card">
      <h3>Telegram</h3>
      <ConnectionStatus label="telegram bot" state={state} text={text} />
      <p>Enabled: <span className={`badge ${tg.enabled ? 'badge-green' : 'badge-red'}`}>{String(tg.enabled)}</span></p>
      <p className="hint">Allowed chat IDs: {(tg.allowed_chat_ids || []).join(', ') || 'none'}. Configure the bot token via <code>aletheia secrets set telegram.bot_token</code>.</p>
    </div>
  )
}
