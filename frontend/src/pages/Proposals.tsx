import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { TradeProposal } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'

const col = createColumnHelper<TradeProposal>()

const columns = [
  col.accessor('symbol', { header: 'Symbol' }),
  col.accessor('market_type', { header: 'Market' }),
  col.accessor('direction', { header: 'Direction' }),
  col.accessor('execution_mode', { header: 'Mode' }),
  col.accessor('agent_rationale', {
    header: 'Rationale',
    cell: info => {
      const v = info.getValue()
      return <span title={v || ''}>{v ? (v.length > 60 ? v.slice(0, 60) + '...' : v) : '—'}</span>
    },
  }),
  col.accessor('status', {
    header: 'Status',
    cell: info => <StatusBadge status={info.getValue()} />,
  }),
]

export default function Proposals() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data = [] } = useQuery({ queryKey: ['proposals'], queryFn: api.getProposals })
  const [selected, setSelected] = useState<TradeProposal | null>(null)

  const approve = useMutation({
    mutationFn: (id: string) => api.approveProposal(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['proposals'] }); qc.invalidateQueries({ queryKey: ['positions'] }) },
  })
  const reject = useMutation({
    mutationFn: (id: string) => api.rejectProposal(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['proposals'] }),
  })

  const rows = data as TradeProposal[]

  function handleApprove() {
    if (!selected) return
    if (!confirm(`Approve ${selected.direction} ${selected.symbol} (${selected.execution_mode})?`)) return
    approve.mutate(selected.id)
    setSelected(null)
  }

  function handleReject() {
    if (!selected) return
    reject.mutate(selected.id)
    setSelected(null)
  }

  return (
    <div>
      <h2 className="page-title">Proposals</h2>
      <div className="card">
        <DataTable
          data={rows}
          columns={columns}
          onRowClick={setSelected}
          emptyMessage="No proposals yet."
        />
      </div>

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title="Proposal Detail">
        {selected && (
          <>
            <DrawerSection label="Trade">
              <DrawerField label="Symbol" value={selected.symbol} mono />
              <DrawerField label="Market" value={selected.market_type} />
              <DrawerField label="Direction" value={selected.direction} />
              <DrawerField label="Mode" value={selected.execution_mode} />
              <DrawerField label="Order Type" value={selected.order_type} />
              <DrawerField label="Quantity" value={selected.quantity} mono />
              <DrawerField label="Price" value={selected.price} mono />
              <DrawerField label="Leverage" value={selected.leverage ? `${selected.leverage}x` : null} mono />
              <DrawerField label="Stop" value={selected.stop_price} mono />
            </DrawerSection>
            <DrawerSection label="Rationale">
              <p style={{ fontSize: '0.82rem', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
                {selected.agent_rationale || 'No rationale provided.'}
              </p>
            </DrawerSection>
            <RiskDecisionSection proposalId={selected.id} />
            <DrawerSection label="Links">
              <DrawerField label="Strategy ID" value={selected.strategy_id ? (
                <a href="#" onClick={e => { e.preventDefault(); navigate('/strategies') }} className="mono">{selected.strategy_id}</a>
              ) : null} />
              <DrawerField label="Opportunity ID" value={selected.opportunity_id ? (
                <a href="#" onClick={e => { e.preventDefault(); navigate('/opportunities') }} className="mono">{selected.opportunity_id}</a>
              ) : null} />
            </DrawerSection>
            <DrawerSection label="Status">
              <DrawerField label="Status" value={<StatusBadge status={selected.status} />} />
              <DrawerField label="Created" value={selected.created_at?.slice(0, 16)} />
            </DrawerSection>
            <div className="flex gap-2 mt-3">
              {selected.status === 'pending' && (
                <>
                  <button onClick={handleApprove} disabled={approve.isPending}>Approve</button>
                  <button className="secondary" onClick={handleReject} disabled={reject.isPending}>Reject</button>
                </>
              )}
              <button className="secondary" onClick={() => navigate('/charts')}>View on Chart</button>
            </div>
          </>
        )}
      </DetailDrawer>
    </div>
  )
}

function RiskDecisionSection({ proposalId }: { proposalId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['risk-decision', proposalId],
    queryFn: () => api.getProposalRiskDecision(proposalId),
    enabled: !!proposalId,
  })
  if (isLoading) return <DrawerSection label="Risk Decision"><p className="text-muted text-sm">Loading...</p></DrawerSection>
  const d = data?.decision
  if (!d) return <DrawerSection label="Risk Decision"><p className="text-muted text-sm">No risk decision recorded.</p></DrawerSection>
  return (
    <DrawerSection label="Risk Decision">
      <DrawerField label="Decision" value={<StatusBadge status={d.decision || d.status} />} />
      <DrawerField label="Risk Score" value={d.risk_score} mono />
      <DrawerField label="Message" value={d.message} />
      {d.violations && (
        <div style={{ marginTop: '0.3rem' }}>
          <span className="text-muted text-sm">Violations:</span>
          <ul style={{ paddingLeft: '1rem', fontSize: '0.78rem', color: 'var(--red)' }}>
            {(typeof d.violations === 'string' ? JSON.parse(d.violations) : d.violations).map((v: string, i: number) => (
              <li key={i}>{v}</li>
            ))}
          </ul>
        </div>
      )}
    </DrawerSection>
  )
}
