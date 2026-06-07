import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { Opportunity } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'

const col = createColumnHelper<Opportunity>()

const columns = [
  col.accessor('symbol', { header: 'Symbol' }),
  col.accessor('market', { header: 'Market' }),
  col.accessor('direction_bias', { header: 'Direction' }),
  col.accessor('confidence', {
    header: 'Confidence',
    cell: info => info.getValue() != null ? `${info.getValue()}%` : '—',
  }),
  col.accessor('source_agent', {
    header: 'Source',
    cell: info => info.getValue() || '—',
  }),
  col.accessor('status', {
    header: 'Status',
    cell: info => <StatusBadge status={info.getValue()} />,
  }),
  col.accessor('created_at', {
    header: 'Created',
    cell: info => info.getValue()?.slice(0, 16) || '—',
  }),
]

export default function Opportunities() {
  const navigate = useNavigate()
  const { data = [] } = useQuery({ queryKey: ['opportunities'], queryFn: api.getOpportunities })
  const [selected, setSelected] = useState<Opportunity | null>(null)
  const rows = data as Opportunity[]

  return (
    <div>
      <h2 className="page-title">Opportunities</h2>
      <div className="card">
        <DataTable
          data={rows}
          columns={columns}
          onRowClick={setSelected}
          emptyMessage="No opportunities yet."
        />
      </div>

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title="Opportunity Detail">
        {selected && (
          <>
            <DrawerSection label="Market Signal">
              <DrawerField label="Symbol" value={selected.symbol} mono />
              <DrawerField label="Market" value={selected.market} />
              <DrawerField label="Direction Bias" value={selected.direction_bias} />
              <DrawerField label="Confidence" value={selected.confidence != null ? `${selected.confidence}%` : null} />
              <DrawerField label="Source Agent" value={selected.source_agent} />
              <DrawerField label="Status" value={<StatusBadge status={selected.status} />} />
            </DrawerSection>
            <DrawerSection label="Thesis">
              <p style={{ fontSize: '0.82rem', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
                {selected.thesis || 'No thesis provided.'}
              </p>
            </DrawerSection>
            <DrawerSection label="Links">
              <DrawerField label="Linked Strategy" value={selected.linked_strategy_id ? (
                <a href="#" onClick={e => { e.preventDefault(); navigate('/strategies') }} className="mono">{selected.linked_strategy_id}</a>
              ) : null} />
              <DrawerField label="Created" value={selected.created_at?.slice(0, 16)} />
            </DrawerSection>
          </>
        )}
      </DetailDrawer>
    </div>
  )
}
