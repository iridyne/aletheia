import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import SetupBanner from '../components/SetupBanner'

export default function Overview() {
  const qc = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: killSwitch } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch })
  const { data: riskStatus } = useQuery({ queryKey: ['risk-status'], queryFn: api.getRiskStatus })
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })
  const { data: positions = [] } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions })
  const { data: runs = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns })
  const { data: proposals = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })

  if (!status) return <p>Loading...</p>

  const latestRun = (runs as any[])[0]
  const latestProposal = (proposals as any[])[0]
  const openPositions = (positions as any[]).filter(p => p.status === 'open')
  const riskState = String(riskStatus?.status || 'not_available')
  const riskBadge = riskState === 'blocked' ? 'badge-red' : (riskState === 'warning' || riskState === 'not_available') ? 'badge-yellow' : 'badge-green'
  const liveState = String(status.live_state)
  const liveStateBadge = liveState === 'live_disabled' ? 'badge-red' : liveState === 'live_limited' ? 'badge-yellow' : 'badge-green'
  const riskText = (riskStatus?.violations as string[] | undefined)?.length
    ? `${riskState}: ${(riskStatus?.violations as string[]).join(', ')}`
    : riskState

  return (
    <div>
      <h2 className="page-title">Overview</h2>
      <SetupBanner />
      <div className="card">
        <h3>System</h3>
        <table>
          <tbody>
            <tr><th>Gateway</th><td><span className={`badge ${status.gateway_running ? 'badge-green' : 'badge-red'}`}>{status.gateway_running ? 'Running' : 'Stopped'}</span></td></tr>
            <tr><th>State</th><td><span className={`badge ${status.paused ? 'badge-yellow' : 'badge-green'}`}>{status.paused ? 'Paused' : 'Active'}</span></td></tr>
            <tr><th>Exchange</th><td>{String(status.exchange)} ({String(status.exchange_mode)})</td></tr>
            <tr><th>Live State</th><td><span className={`badge ${liveStateBadge}`}>{String(status.live_state)}</span></td></tr>
            <tr><th>Permission</th><td><span className="badge badge-blue">{String(status.permission_mode)}</span></td></tr>
            <tr><th>Kill Switch</th><td><span className={`badge ${killSwitch?.active ? 'badge-red' : 'badge-green'}`}>{killSwitch?.active ? 'ENGAGED' : 'inactive'}</span></td></tr>
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>Account / PnL</h3>
        <table>
          <tbody>
            <tr><th>Total value (USDT)</th><td>{portfolio?.total_value_usdt ?? '—'}</td></tr>
            <tr><th>Today PnL</th><td>{portfolio?.today_pnl ?? portfolio?.unrealized_pnl ?? '—'}</td></tr>
            <tr><th>Open positions</th><td>{openPositions.length}</td></tr>
            <tr><th>Risk state</th><td><span className={`badge ${riskBadge}`}>{riskText}</span></td></tr>
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>Latest Activity</h3>
        <p>Latest agent run: {latestRun ? `${latestRun.agent_name} — ${latestRun.status}` : 'none yet'}</p>
        <p>Latest proposal: {latestProposal ? `${latestProposal.symbol} ${latestProposal.direction} — ${latestProposal.status}` : 'none yet'}</p>
      </div>

      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button onClick={() => pauseMut.mutate()} disabled={!!status.paused}>Pause</button>
        <button onClick={() => resumeMut.mutate()} disabled={!status.paused}>Resume</button>
      </div>
    </div>
  )
}
