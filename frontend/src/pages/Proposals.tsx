import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import StatusPill from '../components/shared/StatusPill'
import { useInspector } from '../context/InspectorContext'

export default function Proposals() {
  const qc = useQueryClient()
  const { setObject } = useInspector()
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
        {rows.length === 0 ? <p className="text-muted">No proposals yet.</p> : (
          <table>
            <thead><tr><th>Symbol</th><th>Market</th><th>Direction</th><th>Mode</th><th>Rationale</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>
              {rows.map((p: any) => (
                <tr key={p.id} onClick={() => setObject({ type: 'proposal', id: p.id, data: p })}>
                  <td>{p.symbol}</td>
                  <td>{p.market_type}</td>
                  <td>{p.direction}</td>
                  <td>{p.execution_mode}</td>
                  <td className="text-truncate text-truncate-240" title={p.agent_rationale || ''}>{p.agent_rationale || '—'}</td>
                  <td><StatusPill status={p.status} /></td>
                  <td>
                    {p.status === 'pending' ? (
                      <div className="action-bar" onClick={e => e.stopPropagation()}>
                        <button onClick={() => approve.mutate(p.id)}>Approve</button>
                        <button className="secondary" onClick={() => reject.mutate(p.id)}>Reject</button>
                      </div>
                    ) : <span className="text-muted">—</span>}
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
