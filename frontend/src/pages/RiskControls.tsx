import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

export default function RiskControls() {
  const qc = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const { data: riskStatus } = useQuery({ queryKey: ['risk-status'], queryFn: api.getRiskStatus })
  const { data: boundaries } = useQuery({ queryKey: ['risk-boundaries'], queryFn: api.getRiskBoundaries })
  const { data: killSwitch } = useQuery({ queryKey: ['kill-switch'], queryFn: api.getKillSwitch })

  const killMut = useMutation({
    mutationFn: (active: boolean) => api.setKillSwitch(active, active ? 'Manual kill switch engaged' : 'Kill switch released'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kill-switch'] }),
  })
  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })

  const paused = !!(status as any)?.paused
  const killed = !!killSwitch?.active
  const perm = String((status as any)?.permission_mode || '—')
  const live = String((status as any)?.live_state || 'live_disabled')

  return (
    <div>
      <h2 className="page-title">Risk & Controls</h2>

      <div className="card">
        <h3>Safety State</h3>
        <table>
          <tbody>
            <tr>
              <th>Permission Mode</th>
              <td><span className="badge badge-blue">{perm}</span></td>
            </tr>
            <tr>
              <th>Live State</th>
              <td><span className={`badge ${live === 'live_disabled' ? 'badge-red' : 'badge-yellow'}`}>{live}</span></td>
            </tr>
            <tr>
              <th>Kill Switch</th>
              <td><span className={`badge ${killed ? 'badge-red' : 'badge-green'}`}>{killed ? 'ENGAGED' : 'inactive'}</span></td>
            </tr>
            <tr>
              <th>Agents</th>
              <td><span className={`badge ${paused ? 'badge-yellow' : 'badge-green'}`}>{paused ? 'Paused' : 'Running'}</span></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>Controls</h3>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <button className={paused ? '' : 'secondary'} onClick={() => paused ? resumeMut.mutate() : pauseMut.mutate()}>
            {paused ? '▶ Resume Agents' : '⏸ Pause Agents'}
          </button>
          <button
            className={killed ? '' : 'secondary'}
            onClick={() => killMut.mutate(!killed)}
            style={killed ? { background: 'var(--red)', color: '#fff' } : {}}
          >
            {killed ? '🔓 Release Kill Switch' : '🛑 Engage Kill Switch'}
          </button>
        </div>
      </div>

      {riskStatus && (
        <div className="card">
          <h3>Risk Status</h3>
          <table>
            <tbody>
              <tr><th>Status</th><td><span className={`badge ${riskStatus.status === 'ok' ? 'badge-green' : riskStatus.status === 'blocked' ? 'badge-red' : 'badge-yellow'}`}>{riskStatus.status}</span></td></tr>
              {riskStatus.violations && (riskStatus.violations as string[]).length > 0 && (
                <tr><th>Violations</th><td>{(riskStatus.violations as string[]).join(', ')}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {boundaries && (
        <div className="card">
          <h3>Risk Boundaries</h3>
          <table>
            <tbody>
              {Object.entries(boundaries as Record<string, any>).map(([k, v]) => (
                <tr key={k}><th>{k}</th><td className="mono">{JSON.stringify(v)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
