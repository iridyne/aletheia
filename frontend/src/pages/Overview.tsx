import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

export default function Overview() {
  const qc = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })

  if (!status) return <p>Loading...</p>

  return (
    <div>
      <h2 className="page-title">Overview</h2>
      <div className="card">
        <table>
          <tbody>
            <tr><th>Gateway</th><td><span className={`badge ${status.gateway_running ? 'badge-green' : 'badge-red'}`}>{status.gateway_running ? 'Running' : 'Stopped'}</span></td></tr>
            <tr><th>Paused</th><td><span className={`badge ${status.paused ? 'badge-yellow' : 'badge-green'}`}>{status.paused ? 'Paused' : 'Active'}</span></td></tr>
            <tr><th>Exchange</th><td>{String(status.exchange)} ({String(status.exchange_mode)})</td></tr>
            <tr><th>Live State</th><td><span className="badge badge-red">{String(status.live_state)}</span></td></tr>
            <tr><th>Permission</th><td><span className="badge badge-blue">{String(status.permission_mode)}</span></td></tr>
          </tbody>
        </table>
      </div>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button onClick={() => pauseMut.mutate()} disabled={!!status.paused}>Pause</button>
        <button onClick={() => resumeMut.mutate()} disabled={!status.paused}>Resume</button>
      </div>
    </div>
  )
}
