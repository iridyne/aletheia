import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export default function Dreaming() {
  const { data = [] } = useQuery({ queryKey: ['dreaming'], queryFn: api.getDreamingLogs })
  return (
    <div>
      <h2 className="page-title">Dreaming / Learning</h2>
      <div className="card">
        {(data as any[]).length === 0 ? <p style={{ color: 'var(--muted)' }}>No dreaming logs yet.</p> : (
          <table>
            <thead><tr><th>Title</th><th>Created</th><th>Status</th></tr></thead>
            <tbody>
              {(data as any[]).map((d: any) => (
                <tr key={d.id}><td>{d.title}</td><td>{d.created_at}</td><td>{d.status}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
