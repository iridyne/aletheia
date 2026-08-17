import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import ConnectionStatus, { type ConnState } from '../../../components/ConnectionStatus'
import {
  SaveMessage as SaveMsg,
  SettingsRow as Row,
  Switch as Toggle,
} from '../../../components/ui'
import { humanize } from '../../../lib/format'
import { executionExchangeFromPaperMode } from '../execution/exchangeMode'
import { useSettingsDraftLifecycle } from '../draftLifecycle'
import { CapitalEventsCard, PaperAccountCard, TradingParamsCard } from '../execution/PaperTradingSettings'
import { SettingsFeatureSection, type SettingsFeatureDescriptor } from '../SettingsFeatureSection'

export const EXCHANGE_CONNECTION_SECTION = {
  id: 'exchange-connection', title: 'Exchange Connection', tab: 'execution',
  description: 'Select primary exchange to unify data source, paper execution, and risk allowlist. Then configure API credentials below.',
  searchTerms: ['bitget', 'okx', 'binance', 'api key'],
} satisfies SettingsFeatureDescriptor

export const TRADING_MODE_SECTION = {
  id: 'trading-mode', title: 'Trading Mode — Advanced', tab: 'execution',
  description: 'Configure demo execution or inspect the experimental Live readiness controls. Live Unlock currently records an authority prerequisite; it does not route new proposals away from the paper/demo execution mode selected below.',
  searchTerms: ['demo', 'live unlock', 'paper', 'trading mode'],
} satisfies SettingsFeatureDescriptor

export const PAPER_PARAMETERS_SECTION = {
  id: 'paper-trading-parameters', title: 'Paper Trading Parameters', tab: 'execution',
  description: 'Default exit rules applied when the paper position manager opens a new trade.',
  searchTerms: ['stop loss', 'take profit', 'hold time', 'confidence'],
} satisfies SettingsFeatureDescriptor

export const CAPITAL_EVENTS_SECTION = {
  id: 'capital-events', title: 'Capital Events', tab: 'execution',
  description: 'Record deposits and withdrawals as audited cash-flow history. Account Equity remains visible as a raw balance series; when events fall inside the visible window, Overview also renders a separate cash-flow-adjusted series. Cumulative PnL remains verified trading PnL.',
  searchTerms: ['deposit', 'withdrawal', 'pnl baseline'],
} satisfies SettingsFeatureDescriptor

export const PAPER_ACCOUNT_SECTION = {
  id: 'local-paper-account', title: 'Local Paper Account', tab: 'execution',
  description: 'No exchange only. Manage local simulated cash and positions; every change is recorded for audit and shown to the trading agent.',
  searchTerms: ['no exchange', 'paper cash', 'available balance', 'close positions'],
} satisfies SettingsFeatureDescriptor

export function ExecutionTab({ status, settings, qc, onDirty }: any) {
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
      <SettingsFeatureSection descriptor={EXCHANGE_CONNECTION_SECTION}>
        <PrimaryExchangeCard settings={settings} qc={qc} />
        <ExchangeConnectionCard qc={qc} onDirty={onDirty} />
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={TRADING_MODE_SECTION}>
        <ExchangeModeRow status={status} qc={qc} settings={settings} />
        <ExchangeMarketsRow settings={settings} qc={qc} onDirty={onDirty} />
        <div className="danger-zone">
          <div className="danger-zone-header">Danger Zone</div>
          <LiveStateRow status={status} settings={settings} qc={qc} />
        </div>
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={PAPER_PARAMETERS_SECTION}>
        <TradingParamsCard settings={settings} qc={qc} onDirty={onDirty} />
      </SettingsFeatureSection>

      <SettingsFeatureSection descriptor={CAPITAL_EVENTS_SECTION}>
        <CapitalEventsCard onDirty={onDirty} />
      </SettingsFeatureSection>

      {String((settings?.exchange as any)?.paper_execution_mode) === 'paper_internal' && (
        <SettingsFeatureSection descriptor={PAPER_ACCOUNT_SECTION}>
          <PaperAccountCard qc={qc} onDirty={onDirty} />
        </SettingsFeatureSection>
      )}

    </div>
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
    <Row label="Exchange mode" hint={`demo uses ${modeLabel[paperMode] ?? paperMode} for paper trading. live is an experimental readiness intent only; new proposals still use ${modeLabel[paperMode] ?? paperMode}.`}>
      <select value={String(status.exchange_mode)} onChange={e => {
        const next = e.target.value
        if (next === 'live' && !confirm('Select experimental live readiness? This does not route new proposals to real-fund execution.')) return
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

  useSettingsDraftLifecycle({
    id: 'exchange-markets',
    dirty,
    onDirty,
    serverKey: settings,
    adoptServer: () => {
      setSpot(!!markets.spot)
      setFutures(!!markets.usdt_futures)
      setPaperMode(serverPaperMode)
      setConfirmExchangeDivergence(false)
    },
  })

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
// This is an experimental authority-state prerequisite, not proof that the
// new-Proposal path can select a live execution mode (EX-04 / GitHub #418).
function LiveUnlockWarningWall({ credError, preflight, broker, onConfirm, onCancel }: {
  credError?: string
  preflight?: any
  broker?: string
  onConfirm: () => void
  onCancel: () => void
}) {
  // Styling only: this wall previously hardcoded `var(--color-danger-bg, #fff5f5)`,
  // and neither --color-danger nor --color-danger-bg is declared in index.css, so
  // the fallback painted a near-white panel on this app's near-black --bg #0a0e12.
  // Now uses the shared .danger-zone classes.
  //
  // COPY IS CONTRACT-BOUND — DO NOT REWORD OR TRANSLATE.
  // docs/design/contracts/live_unlock_contract.md §4b mandates these three
  // honest-boundary elements, and tests/specs/spec_live_unlock.py::
  // test_ui_warning_wall_three_elements (claim LU-S03) asserts the literal
  // strings. Changing the wording requires updating the contract and its
  // executable spec in the same change.
  return (
    <div className="danger-zone" role="alertdialog" aria-label="实验性 Live 权限准备确认">
      <div className="danger-zone-header">实验性 Live 权限准备 — 请仔细阅读</div>
      <ul className="settings-danger-list">
        <li>此操作只记录 live_limited 权限前置状态，不会把新 Proposal 路由到实盘</li>
        <li>当前自主 Proposal 仍使用 Paper execution mode 中选择的模拟执行通道</li>
        <li>只有未来独立完成并验证 live Proposal 路由后，真实资金风险才会出现</li>
      </ul>
      <div className="settings-danger-list">
        <div>目标账户：{broker || 'not available'} / {preflight?.account_environment || 'not available'}</div>
        <div>公共可达性：{preflight?.public_reachability || '不作为授权证据'}</div>
        <div>私有认证：{preflight?.private_authenticated ? '已验证' : '未验证'}</div>
        <div>账户可读：{preflight?.account_readable ? '已验证' : '未验证'}</div>
        <div>交易权限：{preflight?.permission_status || 'permission_unknown'}</div>
      </div>
      {credError && (
        <p className="settings-danger-error">
          ⚠ 凭证预检失败：{credError}。建议先修复凭证再解锁。
        </p>
      )}
      <div className="settings-action-row settings-action-row-start">
        <button onClick={onConfirm} disabled={!preflight?.ok}>确认记录实验性权限状态</button>
        <button className="secondary" onClick={onCancel}>取消</button>
      </div>
    </div>
  )
}

function LiveStateRow({ status, settings, qc }: any) {
  const isDisabled = status.live_state === 'live_disabled' || status.live_state === 'disabled'
  const [warning, setWarning] = useState('')
  const [showWall, setShowWall] = useState(false)
  const [credError, setCredError] = useState<string | undefined>(undefined)
  const [preflight, setPreflight] = useState<any | null>(null)
  const broker = String((settings?.exchange as any)?.name || status.exchange || 'not available')
  const refreshLiveState = () => {
    qc.invalidateQueries({ queryKey: ['status'] })
    qc.invalidateQueries({ queryKey: ['settings'] })
  }
  const unlock = useMutation({
    mutationFn: api.liveUnlock,
    onSuccess: refreshLiveState,
    onError: (error: any) => setWarning(error?.message || 'Live unlock failed; re-run the selected-account preflight.'),
  })
  const lock = useMutation({ mutationFn: api.liveLock, onSuccess: refreshLiveState })

  const preflightUnlock = async () => {
    setWarning('')
    setCredError(undefined)
    setPreflight(null)
    try {
      const r = await api.livePreflight()
      setPreflight(r)
      if (!r.ok) {
        setCredError(r.reason_code || 'live_preflight_failed')
      }
    } catch (error: any) {
      setCredError(error?.message || 'live_preflight_unavailable')
    }
    setShowWall(true)
  }

  const handleConfirmUnlock = () => {
    if (!preflight?.ok) {
      setWarning('Live unlock requires a successful selected-account preflight.')
      return
    }
    setShowWall(false)
    unlock.mutate()
  }

  return (
    <div>
      <Row label="Experimental Live authority state" hint="Disabled by default. Unlocking runs a credential pre-flight, requires confirmation, and is audited, but does not change new-Proposal routing. New autonomous proposals remain paper/demo until EX-04 is implemented and verified." danger={!isDisabled}>
        <span className={`badge ${isDisabled ? 'badge-muted' : 'badge-yellow'}`}>{humanize(String(status.live_state))}</span>
        <button onClick={preflightUnlock} disabled={!isDisabled}>Unlock Live-Limited</button>
        <button className="secondary" onClick={() => lock.mutate()} disabled={isDisabled}>Lock</button>
      </Row>
      {showWall && (
        <LiveUnlockWarningWall
          credError={credError}
          preflight={preflight}
          broker={preflight?.broker || broker}
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

function ExchangeConnectionCard({ qc, onDirty }: any) {
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
  useSettingsDraftLifecycle({
    id: 'exchange-credentials',
    dirty,
    onDirty,
    serverKey: undefined,
    adoptServer: () => undefined,
  })

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
