import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

export default function Proposals() {
  const qc = useQueryClient()
  const { data = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  const approve = useMutation({
    mutationFn: (id: string) => api.approveProposal(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['proposals'] }); qc.invalidateQueries({ queryKey: ['positions'] }) },
  })
  const reject = useMutation({
    mutationFn: (id: string) => api.rejectProposal(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['proposals'] }),
  })

  const rows = data as any[]
  return (
    <div>
      <h2 className="page-title">Proposals</h2>
      <div className="card">
        {rows.length === 0 ? <p style={{ color: 'var(--muted)' }}>No proposals yet.</p> : (
          <table>
            <thead><tr><th>Symbol</th><th>Market</th><th>Direction</th><th>Mode</th><th>Rationale</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>
              {rows.map((p: any) => (
                <tr key={p.id}>
                  <td>{p.symbol}</td>
                  <td>{p.market_type}</td>
                  <td>{p.direction}</td>
                  <td>{p.execution_mode}</td>
                  <td style={{ maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.agent_rationale || ''}>{p.agent_rationale || '—'}</td>
                  <td><span className={`badge ${p.status === 'executed' || p.status === 'approved' ? 'badge-green' : p.status === 'rejected' ? 'badge-red' : 'badge-yellow'}`}>{p.status}</span></td>
                  <td>
                    {p.status === 'pending' ? (
                      <div style={{ display: 'flex', gap: '0.25rem' }}>
                        <button onClick={() => approve.mutate(p.id)}>Approve</button>
                        <button className="secondary" onClick={() => reject.mutate(p.id)}>Reject</button>
                      </div>
                    ) : <span style={{ color: 'var(--muted)' }}>—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
