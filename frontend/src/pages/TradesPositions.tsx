import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { Order, Position, PortfolioSummary } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import AccountSnapshot from '../components/AccountSnapshot'
import CausalChain from '../components/CausalChain'
import PageHeader from '../components/PageHeader'
import { fmtNum, fmtSigned, useDecimals } from '../lib/format'

/**
 * Trades & Positions (WEBUI_PAGE_MAP.md — Positions + Paper Trades MERGED).
 *
 * One blotter for the two ends of the same causal chain (trust §1.1):
 *   orders/fills (§23.9) → positions (§23.10).
 *
 *   ACCOUNT      — the snapshot tiles (reused).
 *   OPEN         — open positions, grouped by mode·market (the live book).
 *   ORDERS       — full order/fill history (the §23.9 ledger).
 *   CLOSED       — closed positions with realized PnL + close_reason.
 *
 * The DETAIL DRAWER for an order OR a position embeds the CAUSAL-CHAIN INSPECTOR.
 * Honesty: local position vs exchange drift is surfaced (exposure note), never hidden;
 * the position→proposal link gap is shown in the chain as "not recorded", not faked.
 */

const posCol = createColumnHelper<Position>()
const ordCol = createColumnHelper<Order>()

const pnlCell = (v: number | null | undefined) => {
  const n = v ?? 0
  return <span style={{ color: n >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmtSigned(v)}</span>
}

const openColumns = [
  posCol.accessor('symbol', { header: 'Symbol' }),
  posCol.accessor('direction', { header: 'Dir' }),
  posCol.accessor('quantity', { header: 'Qty', cell: info => fmtNum(info.getValue()) }),
  posCol.accessor('entry_price', { header: 'Entry', cell: info => fmtNum(info.getValue()) }),
  posCol.accessor('current_price', { header: 'Current', cell: info => fmtNum(info.getValue()) }),
  posCol.accessor('stop_price', { header: 'Stop', cell: info => fmtNum(info.getValue()) }),
  posCol.accessor('take_profit_price', { header: 'TP', cell: info => fmtNum(info.getValue()) }),
  posCol.accessor('unrealized_pnl', { header: 'uPnL', cell: info => pnlCell(info.getValue()) }),
]

const closedColumns = [
  posCol.accessor('symbol', { header: 'Symbol' }),
  posCol.accessor('direction', { header: 'Dir' }),
  posCol.accessor('quantity', { header: 'Qty', cell: info => fmtNum(info.getValue()) }),
  posCol.accessor('entry_price', { header: 'Entry', cell: info => fmtNum(info.getValue()) }),
  posCol.accessor('realized_pnl', { header: 'Realized PnL', cell: info => pnlCell(info.getValue()) }),
  posCol.accessor('close_reason', { header: 'Reason', cell: info => info.getValue() ?? '—' }),
  posCol.accessor('closed_at', { header: 'Closed', cell: info => info.getValue()?.slice(0, 16) ?? '—' }),
]

const orderColumns = [
  ordCol.accessor('symbol', { header: 'Symbol' }),
  ordCol.accessor('direction', { header: 'Dir' }),
  ordCol.accessor('quantity', { header: 'Qty', cell: info => fmtNum(info.getValue()) }),
  ordCol.accessor('price', { header: 'Price', cell: info => fmtNum(info.getValue()) }),
  ordCol.accessor('execution_mode', { header: 'Mode' }),
  ordCol.accessor('status', { header: 'Status', cell: info => <StatusBadge status={info.getValue()} /> }),
  ordCol.accessor('filled_at', { header: 'Filled', cell: info => info.getValue()?.slice(0, 16) ?? '—' }),
  ordCol.accessor('created_at', { header: 'Created', cell: info => info.getValue()?.slice(0, 16) || '—' }),
]

type Selection =
  | { kind: 'order'; order: Order }
  | { kind: 'position'; position: Position }
  | null

export default function TradesPositions() {
  const navigate = useNavigate()
  const { data: positions = [] } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions })
  const { data: orders = [] } = useQuery({ queryKey: ['trades'], queryFn: api.getTrades })
  const { data: portfolio } = useQuery({ queryKey: ['portfolio'], queryFn: api.getPortfolio })
  const [sel, setSel] = useState<Selection>(null)
  useDecimals() // re-render tables when the display-decimals setting changes

  const allPos = positions as Position[]
  const open = allPos.filter(p => p.status === 'open')
  const closed = allPos.filter(p => p.status !== 'open')
  const orderRows = orders as Order[]

  const openGroups: Record<string, Position[]> = {}
  for (const p of open) {
    const key = `${p.execution_mode} · ${p.market_type}`
    ;(openGroups[key] ||= []).push(p)
  }

  const exposure = (portfolio as PortfolioSummary | undefined)?.exposure_by_symbol

  return (
    <div>
      <PageHeader
        title="Trades & Positions"
        role="Execute · the blotter for both ends of the causal chain — orders & fills feeding open and closed positions. Click any order or position to inspect its full proposal → risk → order → fill → position chain."
      />
      <AccountSnapshot />

      {/* OPEN POSITIONS — the live book */}
      <div className="blotter-section-head">
        <h3>Open Positions</h3>
        {open.length > 0
          ? <span className="badge badge-green">{open.length} open</span>
          : <span className="badge badge-muted">flat</span>}
      </div>
      {open.length === 0 ? (
        <div className="card"><p className="text-muted text-sm">No open positions.</p></div>
      ) : (
        Object.entries(openGroups).map(([group, items]) => (
          <div className="card" key={group}>
            <h4 className="blotter-group-label">{group}</h4>
            <DataTable data={items} columns={openColumns} onRowClick={p => setSel({ kind: 'position', position: p })} />
          </div>
        ))
      )}

      {exposure && Object.keys(exposure).length > 0 && (
        <div className="card">
          <h4 className="blotter-group-label">Exposure by Symbol</h4>
          <p className="text-muted text-sm" style={{ marginTop: 0 }}>
            aletheia-managed open notional · exchange sync pending (local view may drift from the exchange)
          </p>
          <table>
            <tbody>
              {Object.entries(exposure).map(([sym, v]) => (
                <tr key={sym}><th>{sym}</th><td className="mono">{v} USDT</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ORDER / FILL HISTORY — the §23.9 ledger */}
      <div className="blotter-section-head">
        <h3>Orders &amp; Fills</h3>
        <span className="text-muted text-sm">click a row for the causal chain</span>
      </div>
      <div className="card">
        <DataTable
          data={orderRows}
          columns={orderColumns}
          onRowClick={o => setSel({ kind: 'order', order: o })}
          emptyMessage="No orders yet."
        />
      </div>

      {/* CLOSED POSITIONS — realized PnL ledger */}
      {closed.length > 0 && (
        <>
          <div className="blotter-section-head"><h3>Closed Positions</h3></div>
          <div className="card">
            <DataTable
              data={closed}
              columns={closedColumns}
              onRowClick={p => setSel({ kind: 'position', position: p })}
            />
          </div>
        </>
      )}

      <DetailDrawer
        open={!!sel}
        onClose={() => setSel(null)}
        title={sel?.kind === 'order' ? 'Order Detail' : 'Position Detail'}
      >
        {sel?.kind === 'order' && <OrderDetail order={sel.order} navigate={navigate} />}
        {sel?.kind === 'position' && <PositionDetail position={sel.position} />}
      </DetailDrawer>
    </div>
  )
}

function OrderDetail({ order, navigate }: { order: Order; navigate: (p: string) => void }) {
  return (
    <>
      <DrawerSection label="Order">
        <DrawerField label="Symbol" value={order.symbol} mono />
        <DrawerField label="Market" value={order.market_type} />
        <DrawerField label="Direction" value={order.direction} />
        <DrawerField label="Order Type" value={order.order_type} />
        <DrawerField label="Mode" value={order.execution_mode} />
        <DrawerField label="Status" value={<StatusBadge status={order.status} />} />
      </DrawerSection>
      <DrawerSection label="Execution">
        <DrawerField label="Quantity" value={order.quantity} mono />
        <DrawerField label="Price" value={order.price} mono />
        <DrawerField label="Exchange Order ID" value={order.exchange_order_id} mono />
        <DrawerField label="Filled At" value={order.filled_at?.slice(0, 16)} />
        <DrawerField label="Created" value={order.created_at?.slice(0, 16)} />
      </DrawerSection>
      <DrawerSection label="Causal Chain">
        <CausalChain anchor={{ kind: 'order', order }} />
      </DrawerSection>
      {order.filled_at && (
        <div className="mt-2">
          <button className="secondary" onClick={() => navigate('/charts')}>View on Chart</button>
        </div>
      )}
    </>
  )
}

function PositionDetail({ position }: { position: Position }) {
  return (
    <>
      <DrawerSection label="Position">
        <DrawerField label="Symbol" value={position.symbol} mono />
        <DrawerField label="Market" value={position.market_type} />
        <DrawerField label="Direction" value={position.direction} />
        <DrawerField label="Mode" value={position.execution_mode} />
        <DrawerField label="Status" value={<StatusBadge status={position.status} />} />
      </DrawerSection>
      <DrawerSection label="Pricing">
        <DrawerField label="Quantity" value={position.quantity} mono />
        <DrawerField label="Entry Price" value={position.entry_price} mono />
        <DrawerField label="Current Price" value={position.current_price} mono />
        <DrawerField label="Stop Price" value={position.stop_price} mono />
        <DrawerField label="Take Profit" value={position.take_profit_price} mono />
      </DrawerSection>
      <DrawerSection label="PnL">
        <DrawerField label="Unrealized" value={position.unrealized_pnl} mono />
        <DrawerField label="Realized" value={position.realized_pnl} mono />
      </DrawerSection>
      <DrawerSection label="Metadata">
        <DrawerField label="Strategy ID" value={position.strategy_id} mono />
        <DrawerField label="Close Reason" value={position.close_reason} />
        <DrawerField label="Opened" value={position.opened_at?.slice(0, 16)} />
        <DrawerField label="Closed" value={position.closed_at?.slice(0, 16)} />
      </DrawerSection>
      <DrawerSection label="Causal Chain">
        <CausalChain anchor={{ kind: 'position', position }} />
      </DrawerSection>
    </>
  )
}
