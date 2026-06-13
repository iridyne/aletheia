import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader'
import { fmtSigned, useDecimals, humanize } from '../lib/format'
import { formatViolation, riskStateLabel } from '../lib/riskViolations'

export default function RiskControls() {
  const qc = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: riskStatus } = useQuery({ queryKey: ['risk-status'], queryFn: api.getRiskStatus, refetchInterval: 15000 })
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
              <tr><th>Status</th><td><span className={`badge ${riskBadge}`}>{riskStateLabel(riskState)}</span></td></tr>
              <tr><th>Violations</th><td>
                {violations.length > 0 ? (
                  <ul className="risk-violations">
                    {violations.map((v, i) => {
                      const f = formatViolation(v)
                      return (
                        <li key={i} className="risk-violation">
                          <span className={`risk-dot risk-dot-${f.severity}`} />
                          <div className="risk-violation-body">
                            <span className="risk-violation-label">{f.label}</span>
                            {f.detail && <span className="risk-violation-detail">{f.detail}</span>}
                          </div>
                        </li>
                      )
                    })}
                  </ul>
                ) : <span className="text-muted">none</span>}
              </td></tr>
              <tr><th>Permission Mode</th><td><span className="badge badge-blue">{humanize(String((status as any)?.permission_mode || '—'))}</span></td></tr>
              <tr><th>Live State</th><td><span className={`badge ${(status as any)?.live_state === 'live_disabled' ? 'badge-muted' : 'badge-yellow'}`}>{humanize(String((status as any)?.live_state || '—'))}</span></td></tr>
            </tbody>
          </table>
        </div>

        {/* Kill Switch */}
        <div className="card">
          <h3>Kill Switch</h3>
          <div className="flex items-center gap-3 mb-3">
            <span className={`badge badge-lg ${killed ? 'badge-red' : 'badge-green'}`}>
              {killed ? 'ENGAGED' : 'INACTIVE'}
            </span>
          </div>
          <p className="text-sm text-muted mb-3">
            {killed
              ? 'All trading is halted. No new proposals will be executed until released.'
              : 'Kill switch is inactive. The system can execute approved proposals.'}
          </p>
          <div className="flex gap-2">
            {!killed && (
              <button
                className="secondary danger"
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
                    <td className={`mono ${val >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
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
          <div className="boundaries-grid">
            {Object.entries(boundaries as Record<string, any>).map(([key, val]) => (
              <div key={key} className="drawer-field">
                <span className="drawer-field-label">{humanize(key)}</span>
                <span className="drawer-field-value mono">{fmtBoundary(val)}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-muted text-sm">Loading…</p>
        )}
      </div>
    </div>
  )
}

/** Render a risk-boundary value: scalars as-is, objects as readable "key: value" pairs
 *  (not a raw JSON.stringify dump). */
function fmtBoundary(val: any): string {
  if (val == null) return '—'
  if (typeof val === 'object') {
    return Object.entries(val).map(([k, v]) => `${humanize(k)}: ${v}`).join(' · ')
  }
  return String(val)
}
