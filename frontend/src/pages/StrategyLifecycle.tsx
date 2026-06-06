import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import StatusPill from '../components/shared/StatusPill'
import ActionBar from '../components/shared/ActionBar'
import { useInspector } from '../context/InspectorContext'

const STATES = ['draft', 'research_note', 'strategy_hypothesis', 'paper_candidate', 'paper_running', 'paper_passed', 'live_candidate', 'live_limited', 'retired']

const NEXT_STATE: Record<string, string> = {
  draft: 'research_note',
  research_note: 'strategy_hypothesis',
  strategy_hypothesis: 'paper_candidate',
  paper_candidate: 'paper_running',
  paper_running: 'paper_passed',
  paper_passed: 'live_candidate',
  live_candidate: 'live_limited',
}

export default function StrategyLifecycle() {
  const qc = useQueryClient()
  const { setObject } = useInspector()
  const { data = [] } = useQuery({ queryKey: ['strategies'], queryFn: api.getStrategies })
  const promote = useMutation({
    mutationFn: ({ id, target }: { id: string; target: string }) => api.promoteStrategy(id, target),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
    onError: (e: any) => alert(e.message),
  })
  const retire = useMutation({
    mutationFn: (id: string) => api.retireStrategy(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
  })

  const rows = data as any[]
  return (
    <div>
      <h2 className="page-title">Strategy Lifecycle</h2>
      <div className="card">
        <div className="action-bar" style={{ flexWrap: 'wrap', marginBottom: '1rem', borderTop: 'none', paddingTop: 0 }}>
          {STATES.map(s => <StatusPill key={s} status={s} />)}
        </div>
        {rows.length === 0 ? <p className="text-muted">No strategies yet.</p> : (
          <table>
            <thead><tr><th>Name</th><th>State</th><th>Created</th><th>Actions</th></tr></thead>
            <tbody>
              {rows.map((s: any) => {
                const next = NEXT_STATE[s.state]
                const terminal = s.state === 'retired' || s.state === 'deprecated'
                const actions = []
                if (next) actions.push({ label: `Promote → ${next}`, onClick: () => promote.mutate({ id: s.id, target: next }) })
                if (!terminal) actions.push({ label: 'Retire', onClick: () => retire.mutate(s.id), variant: 'secondary' as const })
                return (
                  <tr key={s.id} onClick={() => setObject({ type: 'strategy', id: s.id, data: s })}>
                    <td>{s.name}</td>
                    <td><StatusPill status={s.state} /></td>
                    <td className="text-faint font-sm">{s.created_at?.slice(0, 16)}</td>
                    <td onClick={e => e.stopPropagation()}>
                      {actions.length > 0 && <ActionBar actions={actions} />}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
