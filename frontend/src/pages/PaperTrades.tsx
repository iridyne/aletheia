import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { Order } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import AccountSnapshot from '../components/AccountSnapshot'

const col = createColumnHelper<Order>()

const columns = [
  col.accessor('symbol', { header: 'Symbol' }),
  col.accessor('direction', { header: 'Direction' }),
  col.accessor('quantity', { header: 'Qty' }),
  col.accessor('price', { header: 'Price', cell: info => info.getValue() ?? '—' }),
  col.accessor('execution_mode', { header: 'Mode' }),
  col.accessor('status', {
    header: 'Status',
    cell: info => <StatusBadge status={info.getValue()} />,
  }),
  col.accessor('created_at', {
    header: 'Created',
    cell: info => info.getValue()?.slice(0, 16) || '—',
  }),
]

export default function PaperTrades() {
  const navigate = useNavigate()
  const { data = [] } = useQuery({ queryKey: ['trades'], queryFn: api.getTrades })
  const [selected, setSelected] = useState<Order | null>(null)
  const rows = data as Order[]

  return (
    <div>
      <h2 className="page-title">Paper Trades</h2>
      <AccountSnapshot />
      <div className="card">
        <DataTable
          data={rows}
          columns={columns}
          onRowClick={setSelected}
          emptyMessage="No paper trades yet."
        />
      </div>

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title="Trade Detail">
        {selected && (
          <>
            <DrawerSection label="Order">
              <DrawerField label="Symbol" value={selected.symbol} mono />
              <DrawerField label="Market" value={selected.market_type} />
              <DrawerField label="Direction" value={selected.direction} />
              <DrawerField label="Order Type" value={selected.order_type} />
              <DrawerField label="Mode" value={selected.execution_mode} />
              <DrawerField label="Status" value={<StatusBadge status={selected.status} />} />
            </DrawerSection>
            <DrawerSection label="Execution">
              <DrawerField label="Quantity" value={selected.quantity} mono />
              <DrawerField label="Price" value={selected.price} mono />
              <DrawerField label="Exchange Order ID" value={selected.exchange_order_id} mono />
              <DrawerField label="Filled At" value={selected.filled_at?.slice(0, 16)} />
            </DrawerSection>
            <DrawerSection label="Links">
              <DrawerField label="Proposal ID" value={selected.proposal_id ? (
                <a href="#" onClick={e => { e.preventDefault(); navigate('/proposals') }} className="mono">{selected.proposal_id}</a>
              ) : null} />
              <DrawerField label="Risk Decision ID" value={selected.risk_decision_id} mono />
            </DrawerSection>
            <DrawerSection label="Timestamps">
              <DrawerField label="Created" value={selected.created_at?.slice(0, 16)} />
            </DrawerSection>
            {selected.filled_at && (
              <div className="mt-2">
                <button className="secondary" onClick={() => navigate(`/charts`)}>View on Chart</button>
              </div>
            )}
          </>
        )}
      </DetailDrawer>
    </div>
  )
}
