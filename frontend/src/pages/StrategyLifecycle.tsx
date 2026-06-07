import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { StrategyRecord } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'

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

const col = createColumnHelper<StrategyRecord>()

const columns = [
  col.accessor('name', { header: 'Name' }),
  col.accessor('state', {
    header: 'State',
    cell: info => <span className="badge badge-blue">{info.getValue()}</span>,
  }),
  col.accessor('created_at', {
    header: 'Created',
    cell: info => info.getValue()?.slice(0, 16) || '—',
  }),
]

function PipelineStepper({ currentState }: { currentState: string }) {
  const idx = STATES.indexOf(currentState)
  return (
    <div className="pipeline-stepper">
      {STATES.filter(s => s !== 'retired').map((s, i) => (
        <div key={s} className={`pipeline-step ${i < idx ? 'step-done' : i === idx ? 'step-active' : 'step-future'}`}>
          <div className="step-dot" />
          <span className="step-label">{s.replace(/_/g, ' ')}</span>
        </div>
      ))}
    </div>
  )
}

export default function StrategyLifecycle() {
  const qc = useQueryClient()
  const { data = [] } = useQuery({ queryKey: ['strategies'], queryFn: api.getStrategies })
  const [selected, setSelected] = useState<StrategyRecord | null>(null)
  const [promoteError, setPromoteError] = useState<string | null>(null)

  const promote = useMutation({
    mutationFn: ({ id, target }: { id: string; target: string }) => api.promoteStrategy(id, target),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['strategies'] }); setPromoteError(null) },
    onError: (e: any) => setPromoteError(e.message || 'Promotion failed'),
  })
  const retire = useMutation({
    mutationFn: (id: string) => api.retireStrategy(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
  })

  const rows = data as StrategyRecord[]

  function handlePromote() {
    if (!selected) return
    const next = NEXT_STATE[selected.state]
    if (!next) return
    setPromoteError(null)
    promote.mutate({ id: selected.id, target: next })
  }

  return (
    <div>
      <h2 className="page-title">Strategy Lifecycle</h2>
      <div className="card">
        <DataTable
          data={rows}
          columns={columns}
          onRowClick={setSelected}
          emptyMessage="No strategies yet."
        />
      </div>

      <DetailDrawer open={!!selected} onClose={() => { setSelected(null); setPromoteError(null) }} title={selected?.name || 'Strategy'}>
        {selected && (
          <>
            <DrawerSection label="Pipeline">
              <PipelineStepper currentState={selected.state} />
            </DrawerSection>
            <DrawerSection label="Details">
              <DrawerField label="Name" value={selected.name} />
              <DrawerField label="State" value={<span className="badge badge-blue">{selected.state}</span>} />
              <DrawerField label="Description" value={selected.description} />
              <DrawerField label="Created" value={selected.created_at?.slice(0, 16)} />
              <DrawerField label="Updated" value={selected.updated_at?.slice(0, 16)} />
            </DrawerSection>
            <PromotionGates strategyId={selected.id} />
            {promoteError && (
              <DrawerSection label="Promotion Error">
                <div className="promote-error">
                  {parsePromotionError(promoteError)}
                </div>
              </DrawerSection>
            )}
            <div className="flex gap-2 mt-3">
              {NEXT_STATE[selected.state] && (
                <button onClick={handlePromote} disabled={promote.isPending}>
                  Promote → {NEXT_STATE[selected.state]?.replace(/_/g, ' ')}
                </button>
              )}
              {selected.state !== 'retired' && selected.state !== 'deprecated' && (
                <button className="secondary" onClick={() => retire.mutate(selected.id)} disabled={retire.isPending}>
                  Retire
                </button>
              )}
            </div>
          </>
        )}
      </DetailDrawer>
    </div>
  )
}

function PromotionGates({ strategyId }: { strategyId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['strategy-gates', strategyId],
    queryFn: () => api.getStrategyGates(strategyId),
    enabled: !!strategyId,
  })
  if (isLoading) return <DrawerSection label="Promotion Gates"><p className="text-muted text-sm">Loading gates...</p></DrawerSection>
  if (!data) return null
  const metrics = data.metrics as Record<string, any> | undefined
  const failures = (data.failures || []) as string[]
  const passed = data.passed as boolean
  if (!metrics) return null
  return (
    <DrawerSection label="Promotion Gates">
      <p style={{ marginBottom: '0.4rem' }}>
        <span className={`badge ${passed ? 'badge-green' : 'badge-red'}`}>{passed ? 'All gates pass' : `${failures.length} gate(s) failed`}</span>
      </p>
      <table style={{ fontSize: '0.78rem' }}>
        <thead>
          <tr><th>Metric</th><th>Value</th></tr>
        </thead>
        <tbody>
          {Object.entries(metrics).map(([k, v]) => (
            <tr key={k}>
              <td>{k.replace(/_/g, ' ')}</td>
              <td className="mono">{v != null ? String(v) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {failures.length > 0 && (
        <ul style={{ paddingLeft: '1rem', marginTop: '0.4rem', fontSize: '0.75rem', color: 'var(--red)' }}>
          {failures.map((f, i) => <li key={i}>{f}</li>)}
        </ul>
      )}
    </DrawerSection>
  )
}

function parsePromotionError(raw: string): React.ReactNode {
  const match = raw.match(/\{.*\}/)
  if (match) {
    try {
      const obj = JSON.parse(match[0])
      if (obj.detail) {
        const failures = typeof obj.detail === 'string' ? obj.detail.split(', ') : [obj.detail]
        return (
          <ul style={{ paddingLeft: '1rem', fontSize: '0.8rem', color: 'var(--red)' }}>
            {failures.map((f: string, i: number) => <li key={i}>{f}</li>)}
          </ul>
        )
      }
    } catch { /* fall through */ }
  }
  return <p style={{ fontSize: '0.8rem', color: 'var(--red)' }}>{raw}</p>
}
