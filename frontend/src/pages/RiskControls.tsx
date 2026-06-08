import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import { fmtSigned, useDecimals } from '../lib/format'

export default function RiskControls() {
  const qc = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: riskStatus } = useQuery({ queryKey: ['risk-status'], queryFn: api.getRiskStatus })
  const { data: boundaries } = useQuery({ queryKey: ['risk-boundaries'], queryFn: api.getRiskBoundaries })
  const { data: killSwitch } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch })
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })

  const engageKill = useMutation({
    mutationFn: () => api.setKillSwitch(true, 'manual_engage'),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['kill-switch'] }); qc.invalidateQueries({ queryKey: ['risk-status'] }) },
  })
  const releaseKill = useMutation({
    mutationFn: () => api.setKillSwitch(false),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['kill-switch'] }); qc.invalidateQueries({ queryKey: ['risk-status'] }) },
  })
  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })

  useDecimals() // re-render when the display-decimals setting changes
  const killed = !!killSwitch?.active
  const paused = !!(status as any)?.paused
  const violations = (riskStatus?.violations as string[]) || []
  const riskState = String(riskStatus?.status || 'unknown')
  const riskBadge = riskState === 'blocked' ? 'badge-red' : riskState === 'warning' ? 'badge-yellow' : riskState === 'healthy' ? 'badge-green' : 'badge-blue'

  const exposure = (portfolio as any)?.exposure_by_symbol as Record<string, number> | undefined

  return (
    <div>
      <PageHeader
        title="Risk & Controls"
        role="Operate · the brakes console. Live risk state, the kill switch, gateway pause/resume, exposure, and the configured risk boundaries — the deep counterpart to the Cockpit's glanceable risk rail."
      />

      <div className="risk-grid">
        {/* Risk State */}
        <div className="card">
          <h3>Risk State</h3>
          <table>
            <tbody>
              <tr><th>Status</th><td><span className={`badge ${riskBadge}`}>{riskState}</span></td></tr>
              <tr><th>Violations</th><td>{violations.length > 0 ? violations.map((v, i) => <span key={i} className="badge badge-red" style={{ marginRight: '0.3rem' }}>{v}</span>) : <span className="text-muted">none</span>}</td></tr>
              <tr><th>Permission Mode</th><td><span className="badge badge-blue">{String((status as any)?.permission_mode || '—')}</span></td></tr>
              <tr><th>Live State</th><td>{String((status as any)?.live_state || '—')}</td></tr>
            </tbody>
          </table>
        </div>

        {/* Kill Switch */}
        <div className="card">
          <h3>Kill Switch</h3>
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '0.8rem' }}>
            <span className={`badge ${killed ? 'badge-red' : 'badge-green'}`} style={{ fontSize: '0.85rem', padding: '0.3rem 0.7rem' }}>
              {killed ? 'ENGAGED' : 'INACTIVE'}
            </span>
          </div>
          <p style={{ fontSize: '0.78rem', color: 'var(--muted)', marginBottom: '0.8rem' }}>
            {killed
              ? 'All trading is halted. No new proposals will be executed until released.'
              : 'Kill switch is inactive. The system can execute approved proposals.'}
          </p>
          <div className="flex gap-2">
            {!killed && (
              <button
                className="secondary"
                style={{ borderColor: 'rgba(255,93,108,0.4)', color: 'var(--red)' }}
                onClick={() => { if (confirm('Engage kill switch? All trading will halt immediately.')) engageKill.mutate() }}
              >
                Engage Kill Switch
              </button>
            )}
            {killed && (
              <button onClick={() => releaseKill.mutate()}>
                Release Kill Switch
              </button>
            )}
          </div>
        </div>

        {/* Gateway Control */}
        <div className="card">
          <h3>Gateway Control</h3>
          <table>
            <tbody>
              <tr><th>Gateway</th><td><span className={`badge ${(status as any)?.gateway_running ? 'badge-green' : 'badge-red'}`}>{(status as any)?.gateway_running ? 'Running' : 'Stopped'}</span></td></tr>
              <tr><th>Scheduler</th><td><span className={`badge ${paused ? 'badge-yellow' : 'badge-green'}`}>{paused ? 'Paused' : 'Active'}</span></td></tr>
            </tbody>
          </table>
          <div className="flex gap-2 mt-3">
            <button className="secondary" onClick={() => pauseMut.mutate()} disabled={paused}>Pause All Agents</button>
            <button onClick={() => resumeMut.mutate()} disabled={!paused}>Resume</button>
          </div>
        </div>

        {/* Exposure */}
        <div className="card">
          <h3>Exposure by Symbol</h3>
          {exposure && Object.keys(exposure).length > 0 ? (
            <table>
              <thead><tr><th>Symbol</th><th>Exposure (USDT)</th></tr></thead>
              <tbody>
                {Object.entries(exposure).map(([sym, val]) => (
                  <tr key={sym}>
                    <td className="mono">{sym}</td>
                    <td className="mono" style={{ color: val >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {fmtSigned(val)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-muted text-sm">No open exposure</p>
          )}
        </div>
      </div>

      {/* Risk Boundaries (read-only) */}
      <div className="card">
        <h3>Risk Boundaries (configured limits)</h3>
        {boundaries ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '0.5rem 1.5rem' }}>
            {Object.entries(boundaries as Record<string, any>).map(([key, val]) => (
              <div key={key} className="drawer-field">
                <span className="drawer-field-label">{key.replace(/_/g, ' ')}</span>
                <span className="drawer-field-value mono">{typeof val === 'object' ? JSON.stringify(val) : String(val)}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-muted text-sm">Loading...</p>
        )}
      </div>
    </div>
  )
}
