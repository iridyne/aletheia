import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { Position, PortfolioSummary } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'

const col = createColumnHelper<Position>()

const columns = [
  col.accessor('symbol', { header: 'Symbol' }),
  col.accessor('direction', { header: 'Direction' }),
  col.accessor('quantity', { header: 'Qty', cell: info => info.getValue() }),
  col.accessor('entry_price', { header: 'Entry', cell: info => info.getValue() }),
  col.accessor('current_price', { header: 'Current', cell: info => info.getValue() ?? '—' }),
  col.accessor('stop_price', { header: 'Stop', cell: info => info.getValue() ?? '—' }),
  col.accessor('take_profit_price', { header: 'TP', cell: info => info.getValue() ?? '—' }),
  col.accessor('unrealized_pnl', {
    header: 'Unrealized PnL',
    cell: info => {
      const v = info.getValue() ?? 0
      return <span style={{ color: v >= 0 ? 'var(--green)' : 'var(--red)' }}>{v}</span>
    },
  }),
]

export default function Positions() {
  const { data = [] } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions })
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })
  const [selected, setSelected] = useState<Position | null>(null)

  const rows = (data as Position[]).filter(p => p.status === 'open')

  const groups: Record<string, Position[]> = {}
  for (const p of rows) {
    const key = `${p.execution_mode} · ${p.market_type}`
    ;(groups[key] ||= []).push(p)
  }

  return (
    <div>
      <h2 className="page-title">Positions</h2>

      {(portfolio as PortfolioSummary | undefined)?.exposure_by_symbol &&
        Object.keys((portfolio as PortfolioSummary).exposure_by_symbol!).length > 0 && (
        <div className="card">
          <h3>Exposure by Symbol</h3>
          <table>
            <tbody>
              {Object.entries((portfolio as PortfolioSummary).exposure_by_symbol!).map(([sym, v]) => (
                <tr key={sym}><th>{sym}</th><td>{v} USDT</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rows.length === 0 ? (
        <div className="card"><p className="text-muted">No open positions.</p></div>
      ) : (
        Object.entries(groups).map(([group, items]) => (
          <div className="card" key={group}>
            <h3>{group}</h3>
            <DataTable data={items} columns={columns} onRowClick={setSelected} />
          </div>
        ))
      )}

      <DetailDrawer open={!!selected} onClose={() => setSelected(null)} title="Position Detail">
        {selected && (
          <>
            <DrawerSection label="Position">
              <DrawerField label="Symbol" value={selected.symbol} mono />
              <DrawerField label="Market" value={selected.market_type} />
              <DrawerField label="Direction" value={selected.direction} />
              <DrawerField label="Mode" value={selected.execution_mode} />
              <DrawerField label="Status" value={<StatusBadge status={selected.status} />} />
            </DrawerSection>
            <DrawerSection label="Pricing">
              <DrawerField label="Quantity" value={selected.quantity} mono />
              <DrawerField label="Entry Price" value={selected.entry_price} mono />
              <DrawerField label="Current Price" value={selected.current_price} mono />
              <DrawerField label="Stop Price" value={selected.stop_price} mono />
              <DrawerField label="Take Profit" value={selected.take_profit_price} mono />
            </DrawerSection>
            <DrawerSection label="PnL">
              <DrawerField label="Unrealized" value={selected.unrealized_pnl} mono />
              <DrawerField label="Realized" value={selected.realized_pnl} mono />
            </DrawerSection>
            <DrawerSection label="Metadata">
              <DrawerField label="Strategy ID" value={selected.strategy_id} mono />
              <DrawerField label="Close Reason" value={selected.close_reason} />
              <DrawerField label="Opened" value={selected.opened_at?.slice(0, 16)} />
              <DrawerField label="Closed" value={selected.closed_at?.slice(0, 16)} />
            </DrawerSection>
          </>
        )}
      </DetailDrawer>
    </div>
  )
}
