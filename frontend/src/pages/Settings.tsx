import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

const PERMISSION_MODES = ['ReadOnly', 'Research', 'PaperAuto', 'AllowEditing', 'Auto']

export default function Settings() {
  const qc = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['status'], queryFn: api.getStatus })
  const permMut = useMutation({
    mutationFn: (mode: string) => api.setPermissionMode(mode),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  })
  const pauseMut = useMutation({ mutationFn: api.pause, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })
  const resumeMut = useMutation({ mutationFn: api.resume, onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }) })

  if (!status) return <p>Loading...</p>

  return (
    <div>
      <h2 className="page-title">Settings</h2>
      <div className="card">
        <h3 style={{ marginBottom: '0.5rem' }}>Permission Mode</h3>
        <select
          value={String(status.permission_mode)}
          onChange={e => permMut.mutate(e.target.value)}
        >
          {PERMISSION_MODES.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>
      <div className="card">
        <h3 style={{ marginBottom: '0.5rem' }}>Gateway Control</h3>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button onClick={() => pauseMut.mutate()} disabled={!!status.paused}>Pause All</button>
          <button onClick={() => resumeMut.mutate()} disabled={!status.paused}>Resume</button>
        </div>
        <p style={{ marginTop: '0.5rem', color: 'var(--muted)', fontSize: '0.8rem' }}>
          Pausing stops all autonomous agent and trading jobs. Dashboard remains accessible.
        </p>
      </div>
      <div className="card">
        <h3 style={{ marginBottom: '0.5rem' }}>Exchange</h3>
        <p>{String(status.exchange)} — mode: <span className="badge badge-yellow">{String(status.exchange_mode)}</span></p>
      </div>
      <div className="card">
        <h3 style={{ marginBottom: '0.5rem' }}>Live State</h3>
        <p><span className="badge badge-red">{String(status.live_state)}</span></p>
        <p style={{ marginTop: '0.5rem', color: 'var(--muted)', fontSize: '0.8rem' }}>
          Live trading is disabled by default. Unlock from this page once ready.
        </p>
      </div>
    </div>
  )
}
