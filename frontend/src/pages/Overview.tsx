import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import SetupBanner from '../components/SetupBanner'

export default function Overview() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: killSwitch } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch })
  const { data: riskStatus } = useQuery({ queryKey: ['risk-status'], queryFn: api.getRiskStatus })
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })
  const { data: positions = [] } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions })
  const { data: runs = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns })
  const { data: proposals = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  const { data: portfolioHistory = [] } = useQuery({ queryKey: ['portfolio-history'], queryFn: api.getPortfolioHistory })
  const { data: modelConfig } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig })
  const { data: modelHealth = [] } = useQuery({ queryKey: ['model-health'], queryFn: api.getModelHealth, refetchInterval: 5000 })
  const { data: dreamingSummary } = useQuery({ queryKey: ['dreaming-summary'], queryFn: api.getDreamingSummary })
  const { data: snapshots = [] } = useQuery({ queryKey: ['market-snapshots'], queryFn: () => api.getMarketSnapshots(undefined, 20) })
  const exchangeMode = String((status as any)?.exchange_mode || '')
  const { data: exchangeHealth } = useQuery({
    queryKey: ['bitget-test', exchangeMode],
    queryFn: () => api.testBitget(exchangeMode),
    refetchInterval: 15000,
    enabled: !!exchangeMode,
  })
  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })

  if (!status) return <p>Loading...</p>

  const latestRun = (runs as any[])[0]
  const openPositions = (positions as any[]).filter(p => p.status === 'open')
  const pendingProposals = (proposals as any[]).filter(p => p.status === 'pending')
  const riskState = String(riskStatus?.status || 'not_available')
  const riskBadge = riskState === 'blocked' ? 'badge-red' : (riskState === 'warning' || riskState === 'not_available') ? 'badge-yellow' : 'badge-green'
  const violations = (riskStatus?.violations as string[]) || []
  const liveState = String((status as any).live_state)
  const liveStateBadge = liveState === 'live_disabled' ? 'badge-red' : liveState === 'live_limited' ? 'badge-yellow' : 'badge-green'
  const killed = !!killSwitch?.active

  const defaultProvider = String(modelConfig?.default_provider || '')
  const dph = (modelHealth as any[]).find(h => h.provider === defaultProvider)
  const modelBadge = dph?.available ? 'badge-green' : dph ? 'badge-red' : 'badge-yellow'
  const modelText = dph?.available ? `connected (${defaultProvider})` : dph ? `unreachable: ${dph.error || '—'}` : 'checking...'
  const exBadge = exchangeHealth?.connected ? 'badge-green' : exchangeHealth ? 'badge-red' : 'badge-yellow'
  const exText = exchangeHealth?.connected ? 'connected' : exchangeHealth ? `unreachable: ${exchangeHealth.error || '—'}` : 'checking...'

  const recentNotes = (dreamingSummary as any)?.recent_research_notes || []
  const latestInsight = recentNotes[0]?.title || null

  const watchSymbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
  const snapshotMap: Record<string, any> = {}
  for (const s of snapshots as any[]) {
    if (!snapshotMap[s.symbol]) snapshotMap[s.symbol] = s
  }

  return (
    <div>
      <h2 className="page-title">Cockpit</h2>
      <SetupBanner />

      {/* Cockpit Grid — 4 panel overview */}
      <div className="cockpit-grid">
        {/* Risk State */}
        <div className="card cockpit-panel">
          <h3>Risk State</h3>
          <div className="flex items-center gap-2 mb-2">
            <span className={`badge ${riskBadge}`}>{riskState}</span>
            {killed && <span className="badge badge-red">KILL SWITCH</span>}
          </div>
          {violations.length > 0 && (
            <div style={{ fontSize: '0.75rem', color: 'var(--red)' }}>
              {violations.slice(0, 3).join(', ')}
              {violations.length > 3 && ` +${violations.length - 3} more`}
            </div>
          )}
          {violations.length === 0 && <p className="text-muted text-sm">No violations</p>}
        </div>

        {/* Agent Focus */}
        <div className="card cockpit-panel">
          <h3>Agent Focus</h3>
          {latestRun ? (
            <>
              <p style={{ fontSize: '0.82rem' }}>
                <span className="mono">{latestRun.agent_name}</span>
                {' '}
                <span className={`badge ${latestRun.status === 'completed' ? 'badge-green' : latestRun.status === 'running' ? 'badge-yellow' : 'badge-red'}`}>
                  {latestRun.status}
                </span>
              </p>
              {latestInsight && (
                <p className="text-muted text-sm mt-2" title={latestInsight}>
                  Latest insight: {latestInsight.length > 60 ? latestInsight.slice(0, 60) + '...' : latestInsight}
                </p>
              )}
            </>
          ) : (
            <p className="text-muted text-sm">No agent runs yet</p>
          )}
        </div>

        {/* Market Snapshot */}
        <div className="card cockpit-panel">
          <h3>Market Snapshot</h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.5rem' }}>
            {watchSymbols.map(sym => {
              const snap = snapshotMap[sym]
              if (!snap) return <div key={sym} className="text-muted text-sm">{sym.replace('USDT', '')}: —</div>
              const change = snap.change_pct_24h
              const color = change > 0 ? 'var(--green)' : change < 0 ? 'var(--red)' : 'var(--muted)'
              return (
                <div key={sym}>
                  <div style={{ fontSize: '0.7rem', color: 'var(--faint)' }}>{sym.replace('USDT', '')}</div>
                  <div className="mono" style={{ fontSize: '0.82rem' }}>{Number(snap.price).toLocaleString()}</div>
                  {change != null && <div className="mono" style={{ fontSize: '0.7rem', color }}>{change > 0 ? '+' : ''}{change.toFixed(2)}%</div>}
                </div>
              )
            })}
          </div>
        </div>

        {/* Action Queue */}
        <div className="card cockpit-panel">
          <h3>Action Queue</h3>
          {pendingProposals.length > 0 ? (
            <>
              <p style={{ fontSize: '0.82rem' }}>
                <span className="badge badge-yellow">{pendingProposals.length} pending</span> proposals
              </p>
              <ul style={{ margin: '0.4rem 0 0 1rem', fontSize: '0.78rem', listStyle: 'disc' }}>
                {pendingProposals.slice(0, 3).map((p: any) => (
                  <li key={p.id}>{p.symbol} {p.direction}</li>
                ))}
              </ul>
              <button className="secondary mt-2" style={{ fontSize: '0.72rem', padding: '0.2rem 0.5rem' }} onClick={() => navigate('/proposals')}>
                Review →
              </button>
            </>
          ) : (
            <p className="text-muted text-sm">No pending actions</p>
          )}
        </div>
      </div>

      {/* System Card (existing, kept) */}
      <div className="card">
        <h3>System</h3>
        <table>
          <tbody>
            <tr><th>Gateway</th><td><span className={`badge ${(status as any).gateway_running ? 'badge-green' : 'badge-red'}`}>{(status as any).gateway_running ? 'Running' : 'Stopped'}</span></td></tr>
            <tr><th>State</th><td><span className={`badge ${(status as any).paused ? 'badge-yellow' : 'badge-green'}`}>{(status as any).paused ? 'Paused' : 'Active'}</span></td></tr>
            <tr><th>Exchange</th><td>{String((status as any).exchange)} ({String((status as any).exchange_mode)}) <span className={`badge ${exBadge}`}>{exText}</span></td></tr>
            <tr><th>Model</th><td><span className={`badge ${modelBadge}`}>{modelText}</span></td></tr>
            <tr><th>Live State</th><td><span className={`badge ${liveStateBadge}`}>{String((status as any).live_state)}</span></td></tr>
            <tr><th>Permission</th><td><span className="badge badge-blue">{String((status as any).permission_mode)}</span></td></tr>
            <tr><th>Kill Switch</th><td><span className={`badge ${killed ? 'badge-red' : 'badge-green'}`}>{killed ? 'ENGAGED' : 'inactive'}</span></td></tr>
          </tbody>
        </table>
      </div>

      {/* Account / PnL */}
      <div className="card">
        <h3>Account / PnL</h3>
        <table>
          <tbody>
            <tr><th>Total value (USDT)</th><td>{(portfolio as any)?.total_value_usdt ?? '—'}</td></tr>
            <tr><th>Today PnL</th><td>{(portfolio as any)?.today_pnl != null ? (portfolio as any).today_pnl : <span className="text-muted">not available</span>}</td></tr>
            <tr><th>Unrealized PnL</th><td>{(portfolio as any)?.unrealized_pnl ?? '—'}</td></tr>
            <tr><th>Open positions</th><td>{openPositions.length}</td></tr>
          </tbody>
        </table>
        <PnLSparkline data={portfolioHistory as any[]} />
      </div>

      {/* Recent Runs */}
      <div className="card">
        <h3>Recent Runs</h3>
        <RecentRunsTimeline runs={(runs as any[]).slice(0, 8)} />
      </div>

      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button onClick={() => pauseMut.mutate()} disabled={!!(status as any).paused}>Pause</button>
        <button onClick={() => resumeMut.mutate()} disabled={!(status as any).paused}>Resume</button>
      </div>
    </div>
  )
}

function PnLSparkline({ data }: { data: any[] }) {
  if (!data || data.length < 2) return null
  const values = data
    .map(s => (s.unrealized_pnl ?? 0) + (s.realized_pnl ?? 0))
    .slice(-30)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const w = 200
  const h = 32
  const points = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w
    const y = h - ((v - min) / range) * h
    return `${x},${y}`
  }).join(' ')
  const lastVal = values[values.length - 1]
  const color = lastVal >= 0 ? 'var(--green)' : 'var(--red)'
  return (
    <div style={{ marginTop: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
      <svg width={w} height={h} style={{ display: 'block' }}>
        <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" />
      </svg>
      <span className="mono text-sm" style={{ color }}>{lastVal >= 0 ? '+' : ''}{lastVal.toFixed(2)}</span>
    </div>
  )
}

function RecentRunsTimeline({ runs }: { runs: any[] }) {
  if (!runs || runs.length === 0) return null
  return (
    <div style={{ marginTop: '0.5rem', display: 'flex', gap: '0.25rem', alignItems: 'center', flexWrap: 'wrap' }}>
      {runs.map((r: any) => {
        const badge = r.status === 'completed' ? 'badge-green' : r.status === 'error' ? 'badge-red' : 'badge-yellow'
        return (
          <span key={r.id} className={`badge ${badge}`} title={`${r.agent_name} — ${r.started_at?.slice(0, 16)}`}>
            {r.agent_name?.slice(0, 3)}
          </span>
        )
      })}
      <span className="text-muted text-sm" style={{ marginLeft: '0.3rem' }}>recent runs</span>
    </div>
  )
}
