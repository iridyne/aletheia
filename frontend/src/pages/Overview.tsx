import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import SetupBanner from '../components/SetupBanner'
import { useInspector } from '../context/InspectorContext'

export default function Overview() {
  const qc = useQueryClient()
  const { setObject } = useInspector()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: killSwitch } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch })
  const { data: riskStatus } = useQuery({ queryKey: ['risk-status'], queryFn: api.getRiskStatus })
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })
  const { data: positions = [] } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions })
  const { data: runs = [] } = useQuery({ queryKey: ['agent-runs'], queryFn: api.getAgentRuns })
  const { data: proposals = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  const { data: opportunities = [] } = useQuery({ queryKey: ['opportunities'], queryFn: api.getOpportunities })
  const { data: modelHealth = [] } = useQuery({ queryKey: ['model-health'], queryFn: api.getModelHealth, refetchInterval: 5000 })
  const { data: modelConfig } = useQuery({ queryKey: ['model'], queryFn: api.getModelConfig })
  const exchangeMode = String((status as any)?.exchange_mode || '')
  const { data: exchangeHealth } = useQuery({
    queryKey: ['bitget-test', exchangeMode],
    queryFn: () => api.testBitget(exchangeMode),
    refetchInterval: 15000,
    enabled: !!exchangeMode,
  })

  if (!status) return <p>Loading...</p>

  const openPositions = (positions as any[]).filter(p => p.status === 'open')
  const pendingProposals = (proposals as any[]).filter(p => p.status === 'pending')
  const recentRuns = (runs as any[]).slice(0, 5)
  const recentOpps = (opportunities as any[]).slice(0, 5)

  const defaultProvider = String(modelConfig?.default_provider || '')
  const dph = (modelHealth as any[]).find(h => h.provider === defaultProvider)
  const riskState = String(riskStatus?.status || 'unknown')

  return (
    <div>
      <h2 className="page-title">Cockpit</h2>
      <SetupBanner />

      {/* System State */}
      <div className="cockpit-grid">
        <div className="card">
          <h3>System State</h3>
          <div className="cockpit-fields">
            <Field label="Gateway" value={status.gateway_running ? 'Running' : 'Stopped'} badge={status.gateway_running ? 'badge-green' : 'badge-red'} />
            <Field label="Agents" value={status.paused ? 'Paused' : 'Active'} badge={status.paused ? 'badge-yellow' : 'badge-green'} />
            <Field label="Permission" value={String(status.permission_mode)} badge="badge-blue" />
            <Field label="Live State" value={String(status.live_state)} badge={status.live_state === 'live_disabled' ? 'badge-red' : 'badge-yellow'} />
            <Field label="Kill Switch" value={killSwitch?.active ? 'ENGAGED' : 'inactive'} badge={killSwitch?.active ? 'badge-red' : 'badge-green'} />
            <Field label="Model" value={dph?.available ? `connected (${defaultProvider})` : 'checking...'} badge={dph?.available ? 'badge-green' : 'badge-yellow'} />
            <Field label="Exchange" value={exchangeHealth?.connected ? 'connected' : 'checking...'} badge={exchangeHealth?.connected ? 'badge-green' : 'badge-yellow'} />
          </div>
        </div>

        <div className="card">
          <h3>Risk State</h3>
          <div className="cockpit-fields">
            <Field label="Status" value={riskState} badge={riskState === 'ok' ? 'badge-green' : riskState === 'blocked' ? 'badge-red' : 'badge-yellow'} />
            <Field label="Open Positions" value={String(openPositions.length)} />
            <Field label="Total Value" value={`${portfolio?.total_value_usdt ?? '—'} USDT`} />
            <Field label="Today PnL" value={String(portfolio?.today_pnl ?? portfolio?.unrealized_pnl ?? '—')} />
          </div>
        </div>
      </div>

      {/* Action Queue */}
      {pendingProposals.length > 0 && (
        <div className="card">
          <h3>Pending Actions ({pendingProposals.length})</h3>
          <table>
            <thead><tr><th>Symbol</th><th>Direction</th><th>Mode</th><th>Rationale</th></tr></thead>
            <tbody>
              {pendingProposals.slice(0, 5).map((p: any) => (
                <tr key={p.id} onClick={() => setObject({ type: 'proposal', id: p.id, data: p })}>
                  <td>{p.symbol}</td>
                  <td>{p.direction}</td>
                  <td>{p.execution_mode}</td>
                  <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.agent_rationale || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Agent Focus */}
      <div className="cockpit-grid">
        <div className="card">
          <h3>Recent Agent Activity</h3>
          {recentRuns.length === 0 ? <p style={{ color: 'var(--muted)' }}>No runs yet.</p> : (
            <table>
              <tbody>
                {recentRuns.map((r: any) => (
                  <tr key={r.id} onClick={() => setObject({ type: 'agent_run', id: r.id, data: r })}>
                    <td>{r.agent_name}</td>
                    <td><span className={`badge ${r.status === 'completed' ? 'badge-green' : 'badge-yellow'}`}>{r.status}</span></td>
                    <td style={{ color: 'var(--faint)', fontSize: '0.75rem' }}>{r.started_at?.slice(11, 16)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <h3>Recent Opportunities</h3>
          {recentOpps.length === 0 ? <p style={{ color: 'var(--muted)' }}>None yet.</p> : (
            <table>
              <tbody>
                {recentOpps.map((o: any) => (
                  <tr key={o.id} onClick={() => setObject({ type: 'opportunity', id: o.id, data: o })}>
                    <td>{o.symbol}</td>
                    <td>{o.direction_bias}</td>
                    <td><span className="badge badge-blue">{o.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}

function Field({ label, value, badge }: { label: string; value: string; badge?: string }) {
  return (
    <div className="cockpit-field">
      <span className="cockpit-field-label">{label}</span>
      {badge ? <span className={`badge ${badge}`}>{value}</span> : <span className="cockpit-field-value">{value}</span>}
    </div>
  )
}
