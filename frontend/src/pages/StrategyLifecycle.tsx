import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { StrategyRecord } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import PageHeader from '../components/PageHeader'
import { Badge, Button, Card, TableWrap } from '../components/ui'
import { humanize, fmtTimestamp } from '../lib/format'

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
    cell: info => <StatusBadge status={info.getValue()} />,
  }),
  col.accessor('created_at', {
    header: 'Created',
    cell: info => <span className="mono">{fmtTimestamp(info.getValue())}</span>,
  }),
]

function PipelineStepper({ currentState }: { currentState: string }) {
  const idx = STATES.indexOf(currentState)
  return (
    <div className="pipeline-stepper">
      {STATES.filter(s => s !== 'retired').map((s, i) => (
        <div key={s} className={`pipeline-step ${i < idx ? 'step-done' : i === idx ? 'step-active' : 'step-future'}`}>
          <div className="step-dot" />
          <span className="step-label">{humanize(s)}</span>
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
      <PageHeader
        title="Strategy Lifecycle"
        role="Evolve · the promotion pipeline (draft → research → hypothesis → paper → live-limited). Click a strategy to see its promotion gates and advance or retire it."
      />
      <Card>
        <DataTable
          data={rows}
          columns={columns}
          onRowClick={setSelected}
          emptyMessage="No strategies yet."
        />
      </Card>

      <DetailDrawer open={!!selected} onClose={() => { setSelected(null); setPromoteError(null) }} title={selected?.name || 'Strategy'}>
        {selected && (
          <>
            <DrawerSection label="Pipeline">
              <PipelineStepper currentState={selected.state} />
            </DrawerSection>
            <DrawerSection label="Details">
              <DrawerField label="Name" value={selected.name} />
              <DrawerField label="State" value={<StatusBadge status={selected.state} />} />
              <DrawerField label="Description" value={selected.description} />
              <DrawerField label="Created" value={fmtTimestamp(selected.created_at)} mono />
              <DrawerField label="Updated" value={fmtTimestamp(selected.updated_at)} mono />
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
                <Button onClick={handlePromote} disabled={promote.isPending}>
                  Promote → {humanize(NEXT_STATE[selected.state])}
                </Button>
              )}
              {selected.state !== 'retired' && selected.state !== 'deprecated' && (
                <Button variant="secondary" onClick={() => retire.mutate(selected.id)} disabled={retire.isPending}>
                  Retire
                </Button>
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
      <p className="mb-2">
        <Badge tone={passed ? 'green' : 'red'}>{passed ? 'All gates pass' : `${failures.length} gate(s) failed`}</Badge>
      </p>
      <TableWrap>
        <table className="drawer-table">
          <thead>
            <tr><th>Metric</th><th>Value</th></tr>
          </thead>
          <tbody>
            {Object.entries(metrics).map(([k, v]) => (
              <tr key={k}>
                <td>{humanize(k)}</td>
                <td className="mono">{v != null ? String(v) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
      {failures.length > 0 && (
        <ul className="drawer-list drawer-list-error mt-2">
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
          <ul className="drawer-list drawer-list-error">
            {failures.map((f: string, i: number) => <li key={i}>{f}</li>)}
          </ul>
        )
      }
    } catch { /* fall through */ }
  }
  return <p className="text-sm pnl-negative">{raw}</p>
}
