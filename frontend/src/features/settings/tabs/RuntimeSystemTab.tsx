import { useEffect, useRef, useState } from 'react'
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
} from '../../../components/ui'
import { SettingsFeatureSection, type SettingsFeatureDescriptor } from '../SettingsFeatureSection'
import type { SettingsProps, SettingsStatusProps } from '../types'

export const APP_SECTION = {
  id: 'app', title: 'App', tab: 'runtime', description: 'Dashboard process settings.',
  searchTerms: ['theme', 'dashboard', 'shell', 'auto open browser', 'pnl mode'],
} satisfies SettingsFeatureDescriptor

export const DATA_RETENTION_SECTION = {
  id: 'data-retention', title: 'Data & Retention', tab: 'runtime',
  description: 'Local OHLCV storage for backtesting and telemetry retention policy.',
  searchTerms: ['backfill', 'ohlcv storage', 'backtest', 'prune', 'cleanup', 'telemetry'],
} satisfies SettingsFeatureDescriptor

export const DATA_EXPORT_SECTION = {
  id: 'data-export', title: 'Data Export', tab: 'runtime',
  description: 'Download trading data as CSV archives. Use Analysis for sharing with external analysts; use Migration for transferring the full dataset to another instance.',
  searchTerms: ['export', 'download', 'csv', 'zip', 'backup', 'migration', 'analysis', 'trade data'],
} satisfies SettingsFeatureDescriptor

export const SOFTWARE_UPDATES_SECTION = {
  id: 'software-updates', title: 'Software Updates', tab: 'runtime',
  description: 'On-demand self-upgrade via git pull. Never runs automatically.',
  searchTerms: ['git pull', 'upgrade', 'self-update'],
} satisfies SettingsFeatureDescriptor

export const NETWORK_PROXY_SECTION = {
  id: 'network-proxy', title: 'Network Proxy', tab: 'runtime',
  description: 'Route all outbound traffic (exchange, market data, news, LLM) through a managed proxy. Set the protocol/host/port, and enable Enforce to require the proxy (fail-closed) so a proxy outage never leaks your real IP.',
  searchTerms: ['proxy', 'socks', 'http proxy', 'enforce', 'outbound', 'ip leak'],
} satisfies SettingsFeatureDescriptor

function HistoricalDataCard({ settings, qc }: SettingsProps) {
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
    mutationFn: () => api.updateHistoricalDataConfig(vals as Parameters<typeof api.updateHistoricalDataConfig>[0]),
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

function AppRows({ status, settings, qc }: SettingsStatusProps) {
  const app = settings?.app as any || {}
  const runtime = settings?.runtime as any || {}
  const remoteControl = settings?.remote_control as any || {}
  const remoteBindHosts = Array.isArray(remoteControl.bind_hosts) ? remoteControl.bind_hosts as string[] : []
  const [remoteToken, setRemoteToken] = useState('')
  const [remoteTokenMessage, setRemoteTokenMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const storeRemoteControlToken = () => {
    const result = api.setRemoteControlToken(remoteToken)
    setRemoteTokenMessage({
      ok: result.ok,
      text: result.ok ? 'Token saved in this browser.' : result.error,
    })
  }
  const { data: remoteInterfaces } = useQuery({
    queryKey: ['remote-control-interfaces'],
    queryFn: api.getRemoteControlInterfaces,
  })
  const autoBrowser = app.auto_open_browser !== false
  const bootCommit = status?.boot_commit as string | null | undefined
  const currentCommit = status?.current_commit as string | null | undefined
  const codeDrift = !!bootCommit && !!currentCommit && bootCommit !== currentCommit

  const appMut = useMutation({
    mutationFn: (body: Parameters<typeof api.updateAppConfig>[0]) => api.updateAppConfig(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })
  const runtimeMut = useMutation({
    mutationFn: (body: Parameters<typeof api.updateRuntimeConfig>[0]) => api.updateRuntimeConfig(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })
  const remoteControlMut = useMutation({
    mutationFn: (body: { enabled?: boolean; bind_hosts?: string[]; require_token?: boolean; api_token?: string }) => api.updateRemoteControl(body),
    onSuccess: () => {
      if (remoteToken.trim()) storeRemoteControlToken()
      qc.invalidateQueries({ queryKey: ['settings'] })
    },
  })
  const runtimePending = useRef(false)
  const safeRuntimeMut = (body: Parameters<typeof api.updateRuntimeConfig>[0]) => {
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
      <Row label="Running code commit" hint="Git commit loaded when this backend process started. Use this value to identify the code the current instance is actually running.">
        <span className="mono text-sm break-all">{bootCommit || 'not available'}</span>
        {codeDrift && <span className="text-xs text-warning">Disk HEAD has changed — restart the gateway to run the checked-out code.</span>}
      </Row>
      <Row label="Checked-out commit" hint="Current Git HEAD on disk. This can differ from the running code commit until the gateway restarts.">
        <span className="mono text-sm break-all">{currentCommit || 'not available'}</span>
      </Row>
      <Row label="Backend release" hint="Application package version and operating-system process ID reported by the responding backend.">
        <span className="mono text-sm">{status ? `${status.version || 'not available'} · PID ${status.pid ?? 'not available'}` : 'not available'}</span>
      </Row>
      <Row label="Dashboard port" hint={`Currently listening on :${app.dashboard_port || 8787}. Change requires gateway restart.`}>
        <span className="mono text-sm">{app.dashboard_port || 8787}</span>
        <span className="text-xs text-muted">(read-only — set via config.yaml)</span>
      </Row>
      <Row label="Listener binding" hint="Remote Control owns the dashboard listener. The internal backend is always loopback-only and is never exposed on :8788.">
        <span className="text-sm">{remoteControl.enabled ? `${remoteBindHosts.length} selected adapter address(es)` : 'Localhost only'}</span>
        <span className="text-xs text-muted">Restart gateway after changing Remote Control adapters.</span>
      </Row>
      <Row label="Remote Control" hint="Allow HTTP API control over selected EasyTier or LAN adapter addresses. It is off by default and requires a gateway restart after changes.">
        <Toggle
          checked={!!remoteControl.enabled}
          onChange={enabled => remoteControlMut.mutate({ enabled })}
          disabled={remoteControlMut.isPending || (!remoteControl.enabled && remoteBindHosts.length === 0)}
        />
        <span className="text-xs text-muted">{remoteBindHosts.length === 0 ? 'Select at least one adapter below before enabling.' : 'Only selected adapter addresses will listen on :8787.'}</span>
      </Row>
      <Row label="Remote network adapters" hint="Detected IPv4 addresses. Select one or more interfaces for Remote Control; inactive adapters are shown for diagnosis but cannot accept traffic until they are up.">
        <div className="flex flex-col gap-2">
          {(remoteInterfaces?.interfaces || []).map(iface => (
            <label key={`${iface.name}-${iface.address}`} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={remoteBindHosts.includes(iface.address)}
                onChange={event => {
                  const bind_hosts = event.target.checked
                    ? [...remoteBindHosts, iface.address]
                    : remoteBindHosts.filter(host => host !== iface.address)
                  remoteControlMut.mutate({ bind_hosts })
                }}
                disabled={remoteControlMut.isPending || !iface.is_up}
              />
              <span className="mono">{iface.name} · {iface.address}/{iface.netmask || 'unknown'}</span>
              {!iface.is_up && <span className="text-xs text-warning">inactive</span>}
            </label>
          ))}
          {!remoteInterfaces && <span className="text-xs text-muted">Loading network adapters…</span>}
          {remoteInterfaces?.interfaces.length === 0 && <span className="text-xs text-warning">No non-loopback IPv4 adapter is available.</span>}
        </div>
      </Row>
      <Row label="Remote Control Token" hint="Optional owner-token protection for every private API. Turn it on only after entering a Token; Cloudflare Tunnel always requires a Token.">
        <Toggle
          checked={!!remoteControl.require_token}
          onChange={require_token => remoteControlMut.mutate({
            require_token,
            ...(remoteToken.trim() ? { api_token: remoteToken.trim() } : {}),
          })}
          disabled={!remoteControl.enabled || remoteControlMut.isPending || !remoteToken.trim()}
        />
        <input
          type="password"
          value={remoteToken}
          onChange={e => { setRemoteToken(e.target.value); setRemoteTokenMessage(null) }}
          placeholder="Optional owner API token"
          autoComplete="off"
          className="w-56"
          disabled={!remoteControl.enabled}
        />
        <button
          type="button"
          className="button-secondary"
          onClick={storeRemoteControlToken}
          disabled={!remoteToken.trim()}
        >Use in this browser</button>
        {remoteTokenMessage && (
          <span
            className={`settings-save-msg ${remoteTokenMessage.ok ? 'ok' : 'err'}`}
            role={remoteTokenMessage.ok ? 'status' : 'alert'}
          >{remoteTokenMessage.text}</span>
        )}
        <span className="text-xs text-muted">Write-only; stored only in this browser so the local dashboard keeps access after Token protection is enabled.</span>
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

function RetentionCard({ settings, qc }: SettingsProps) {
  const retention = settings?.retention as any
  const [vals, setVals] = useState<Record<string, unknown>>({})
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!retention) return
    setVals({ ...retention })
  }, [settings])

  const set = (k: string, v: unknown) => { setVals(prev => ({ ...prev, [k]: v })); setMsg(null) }

  const RETENTION_FIELDS: NumField[] = [
    { key: 'agent_runs_days',          label: 'Agent runs',          unit: 'days', min: 1, max: 90, step: 1, isInt: true },
    { key: 'runtime_sessions_days',    label: 'Runtime sessions',    unit: 'days', min: 1, max: 90, step: 1, isInt: true },
    { key: 'tool_calls_days',          label: 'Tool calls',          unit: 'days', min: 1, max: 90, step: 1, isInt: true },
    { key: 'run_steps_days',           label: 'Run steps',           unit: 'days', min: 1, max: 9999, step: 1, isInt: true },
    { key: 'market_snapshots_days',    label: 'Market snapshots',    unit: 'days', min: 1, max: 9999, step: 1, isInt: true },
    { key: 'portfolio_snapshots_days', label: 'Portfolio snapshots', unit: 'days', min: 1, max: 9999, step: 1, isInt: true },
    { key: 'opportunity_ttl_hours',    label: 'Opportunity TTL',     unit: 'hours', min: 1, max: 720, step: 1, isInt: true },
    { key: 'jsonl_max_mb',             label: 'JSONL max size',      unit: 'MB',   min: 1, max: 99999, step: 10, isInt: true },
  ]

  const mut = useMutation({
    mutationFn: () => api.updateRetentionConfig(
      Object.fromEntries(RETENTION_FIELDS.map(({ key }) => [key, vals[key]])) as Parameters<typeof api.updateRetentionConfig>[0],
    ),
    onSuccess: () => { setMsg({ ok: true, text: '✓ saved' }); qc.invalidateQueries({ queryKey: ['settings'] }) },
    onError: (e: any) => setMsg({ ok: false, text: `✗ ${e.message}` }),
  })

  if (!retention) return null

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

function UpdateCard({ settings, qc }: SettingsProps) {
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

  const check = useMutation({
    mutationFn: api.checkForUpdate,
    onSuccess: result => qc.setQueryData(['update-status'], result),
  })

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
  if (isLoading || check.isPending) badge = <span className="badge">checking…</span>
  else if (data?.reason === 'update_check_required') badge = <span className="badge">not checked</span>
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
        <button className="secondary" onClick={() => check.mutate()} disabled={check.isPending}>Re-check</button>
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
        <Toggle checked={enabled} label="Auto-update enabled" onChange={v => { setEnabled(v); setCfgMsg(null) }} />
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

function NetworkCard({ settings, qc }: SettingsProps) {
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
    mutationFn: (body: Parameters<typeof api.updateNetworkConfig>[0]) => api.updateNetworkConfig(body),
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

const EXPORT_PRESETS = [
  {
    id: 'analysis',
    label: 'Analysis Export',
    description: 'Trading history as CSV — suitable for sharing with external analysts or running your own analysis scripts.',
    tables: ['trade_proposals', 'risk_decisions', 'orders', 'fills', 'positions', 'capital_events', 'paper_account_events'],
    includeConfig: false,
  },
  {
    id: 'migration',
    label: 'Migration Export',
    description: 'Full dataset plus a redacted config backup — for transferring this instance\'s history to a fresh installation.',
    tables: ['trade_proposals', 'risk_decisions', 'orders', 'fills', 'positions', 'capital_events', 'paper_account_events', 'agent_runs', 'agent_run_steps', 'audit_events'],
    includeConfig: true,
  },
] as const

function DataExportCard() {
  const { data: catalog } = useQuery({
    queryKey: ['export-catalog'],
    queryFn: api.getExportCatalog,
    staleTime: 30_000,
  })

  const countMap = Object.fromEntries(
    (catalog?.datasets ?? []).map(d => [d.table, d.row_count])
  )

  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null)
  const [downloading, setDownloading] = useState<string | null>(null)

  const triggerDownload = async (preset: typeof EXPORT_PRESETS[number]) => {
    setStatus(null)
    setDownloading(preset.id)
    try {
      const { blob, filename } = await api.downloadExport({
        preset: preset.id,
        include_config: preset.includeConfig,
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      setStatus({ ok: true, text: `✓ ${filename} downloaded` })
    } catch (e: any) {
      setStatus({ ok: false, text: `✗ ${e.message}` })
    } finally {
      setDownloading(null)
    }
  }

  return (
    <div className="card mb-0">
      <div className="flex flex-col gap-4">
        {EXPORT_PRESETS.map(preset => (
          <div key={preset.id} className="border rounded p-3" style={{ borderColor: 'var(--border)' }}>
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <div className="font-medium text-sm mb-1">{preset.label}</div>
                <p className="hint mb-2">{preset.description}</p>
                <div className="flex flex-wrap gap-1">
                  {preset.tables.map(t => {
                    const n = countMap[t]
                    return (
                      <span key={t} className="badge badge-neutral text-xs font-mono">
                        {t.replace(/_/g, ' ')}{n != null && n >= 0 ? ` (${n.toLocaleString()})` : ''}
                      </span>
                    )
                  })}
                  {preset.includeConfig && (
                    <span className="badge badge-neutral text-xs font-mono">config_backup.yaml</span>
                  )}
                </div>
              </div>
              <button
                onClick={() => triggerDownload(preset)}
                disabled={downloading !== null}
                className="shrink-0"
              >
                {downloading === preset.id ? 'Preparing…' : 'Download ZIP'}
              </button>
            </div>
          </div>
        ))}
      </div>
      {status && (
        <p className={`text-sm mt-3 ${status.ok ? 'text-success' : 'text-danger'}`}>{status.text}</p>
      )}
      <p className="hint mt-3">
        Exports are generated on demand from the local SQLite database. Config backup has secrets redacted.
        Large datasets (agent runs, audit log) may take a few seconds.
      </p>
    </div>
  )
}

// No child on this tab reports precise dirty state; the shell's delegated guard
// (features/settings/dirtyGuard.ts) covers them, so there is no onDirty to accept.
export function RuntimeSystemTab({ status, settings, qc }: SettingsStatusProps) {
  return (
    <div>
      <SettingsFeatureSection descriptor={APP_SECTION}>
        <AppRows status={status} settings={settings} qc={qc} />
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={DATA_RETENTION_SECTION}>
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
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={DATA_EXPORT_SECTION}>
        <Collapsible title="Export">
          <DataExportCard />
        </Collapsible>
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={SOFTWARE_UPDATES_SECTION}>
        <UpdateCard settings={settings} qc={qc} />
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={NETWORK_PROXY_SECTION}>
        <NetworkCard settings={settings} qc={qc} />
      </SettingsFeatureSection>
    </div>
  )
}
