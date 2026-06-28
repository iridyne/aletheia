import { useState, useMemo, lazy, Suspense } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { createColumnHelper } from '@tanstack/react-table'
import { api } from '../api/client'
import type { Order, Position, ReconcileItem } from '../types'
import { DataTable, StatusBadge } from '../components/DataTable'
import { DetailDrawer, DrawerSection, DrawerField } from '../components/DetailDrawer'
import AccountSnapshot from '../components/AccountSnapshot'
import CausalChain from '../components/CausalChain'
import PageHeader from '../components/PageHeader'
import Tabs from '../components/Tabs'
import { Badge, Button, Card, TableWrap } from '../components/ui'
import { fmtNum, fmtSigned, useDecimals, humanize, fmtTimestamp, fmtSymbol, exchangeOf, marketBadgeTone, isPerpMarket } from '../lib/format'
import TradeAnalyticsPanel from '../charts/TradeAnalyticsPanel'
const TradeInsightsPanel = lazy(() => import('../charts/TradeInsightsPanel'))

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
  // Null/unknown PnL must not read as a flat green gain — show a muted dash.
  if (v == null) return <span className="mono text-muted">—</span>
  return <span className={`mono ${v >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>{fmtSigned(v)}</span>
}

const marketBadge = (v: string) => <Badge tone={marketBadgeTone(v)}>{isPerpMarket(v) ? 'Perp' : humanize(v)}</Badge>

const openColumns = [
  posCol.accessor('symbol', { header: 'Symbol', cell: info => <span className="mono">{fmtSymbol(info.getValue(), info.row.original.market_type)}</span> }),
  posCol.accessor('execution_mode', { id: 'exchange', header: 'Exchange', cell: info => exchangeOf(info.getValue()) }),
  posCol.accessor('market_type', { id: 'market', header: 'Market', cell: info => marketBadge(info.getValue()) }),
  posCol.accessor('direction', { header: 'Dir', cell: info => humanize(info.getValue()) }),
  posCol.accessor('quantity', { header: 'Qty', cell: info => <span className="mono">{fmtNum(info.getValue())}</span> }),
  posCol.accessor('entry_price', { header: 'Entry (USDT)', cell: info => <span className="mono">{fmtNum(info.getValue())}</span> }),
  posCol.accessor('current_price', { header: 'Current (USDT)', cell: info => <span className="mono">{fmtNum(info.getValue())}</span> }),
  posCol.display({ id: 'leverage', header: 'Lev', cell: ({ row }) => {
    const lev = (row.original as any).leverage
    return lev && lev > 1 ? <Badge tone="blue">{lev}x</Badge> : <span className="text-muted">1x</span>
  }}),
  posCol.accessor('stop_price', { header: 'Stop (USDT)', cell: info => <span className="mono">{fmtNum(info.getValue())}</span> }),
  posCol.accessor('take_profit_price', { header: 'TP (USDT)', cell: info => <span className="mono">{fmtNum(info.getValue())}</span> }),
  posCol.accessor('unrealized_pnl', { header: 'uPnL (USDT)', cell: info => pnlCell(info.getValue()) }),
]

const closedColumns = [
  posCol.accessor('symbol', { header: 'Symbol', cell: info => <span className="mono">{fmtSymbol(info.getValue(), info.row.original.market_type)}</span> }),
  posCol.accessor('direction', { header: 'Dir', cell: info => humanize(info.getValue()) }),
  posCol.accessor('quantity', { header: 'Qty', cell: info => <span className="mono">{fmtNum(info.getValue())}</span> }),
  posCol.accessor('entry_price', { header: 'Entry (USDT)', cell: info => <span className="mono">{fmtNum(info.getValue())}</span> }),
  posCol.accessor('realized_pnl', { header: 'PnL (USDT)', cell: info => pnlCell(info.getValue()) }),
  posCol.accessor('close_reason', { header: 'Reason', cell: info => info.getValue() ? humanize(info.getValue()!) : '—' }),
  posCol.accessor('closed_at', { header: 'Closed', cell: info => <span className="mono">{fmtTimestamp(info.getValue())}</span> }),
]

const orderColumns = [
  ordCol.accessor('symbol', { header: 'Symbol', cell: info => <span className="mono">{fmtSymbol(info.getValue(), info.row.original.market_type)}</span> }),
  ordCol.accessor('direction', { header: 'Dir', cell: info => humanize(info.getValue()) }),
  ordCol.accessor('quantity', { header: 'Qty', cell: info => <span className="mono">{fmtNum(info.getValue())}</span> }),
  ordCol.accessor('price', { header: 'Price (USDT)', cell: info => <span className="mono">{fmtNum(info.getValue())}</span> }),
  ordCol.accessor('execution_mode', { header: 'Mode', cell: info => humanize(info.getValue()) }),
  ordCol.accessor('status', { header: 'Status', cell: info => <StatusBadge status={info.getValue()} /> }),
  ordCol.accessor('error_reason', { header: 'Error', cell: info => info.getValue() || '—' }),
  ordCol.accessor('filled_at', { header: 'Filled', cell: info => <span className="mono">{fmtTimestamp(info.getValue())}</span> }),
  ordCol.accessor('created_at', { header: 'Created', cell: info => <span className="mono">{fmtTimestamp(info.getValue())}</span> }),
]

type Selection =
  | { kind: 'order'; order: Order }
  | { kind: 'position'; position: Position }
  | null

export default function TradesPositions() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const initialTab = searchParams.get('tab') || 'blotter'
  const { data: positions = [] } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions })
  const { data: orders = [] } = useQuery({ queryKey: ['trades'], queryFn: api.getTrades })
  const { data: reconciliation } = useQuery({ queryKey: ['positions-reconciliation'], queryFn: api.getPositionsReconciliation })
  const [sel, setSel] = useState<Selection>(null)
  useDecimals() // re-render tables when the display-decimals setting changes

  const allPos = positions as Position[]
  const open = allPos.filter(p => p.status === 'open')
  const closed = allPos.filter(p => p.status !== 'open')
  const orderRows = orders as Order[]

  // Per-position notional (local book), same formula as the backend exposure_by_symbol
  // (pnl.py:248): quantity × (current_price ?? entry_price). Total over all open
  // positions is the denominator for each row's exposure %.
  const posNotional = (p: Position) => p.quantity * (p.current_price ?? p.entry_price)
  const totalNotional = open.reduce((s, p) => s + posNotional(p), 0)

  // Exposure column folded into the open-positions blotter (USDT / % share of total
  // open notional), replacing the standalone "Exposure by Symbol" card.
  const openColumnsWithExposure = useMemo(() => [
    ...openColumns,
    posCol.accessor(p => posNotional(p), {
      id: 'exposure',
      header: 'Exposure (USDT / %)',
      cell: info => {
        const notional = info.getValue() as number
        const pct = totalNotional > 0 ? (notional / totalNotional) * 100 : 0
        return <span className="mono">{fmtNum(notional)} / {pct.toFixed(1)}%</span>
      },
    }),
  ], [totalNotional])

  return (
    <div>
      <PageHeader
        title="Trades & Positions"
        role="Execute · the blotter for both ends of the causal chain — orders & fills feeding open and closed positions. Click any order or position to inspect its full proposal → risk → order → fill → position chain."
      />
      <AccountSnapshot />

      <Tabs initial={initialTab} tabs={[
        { id: 'blotter', label: 'Blotter', badge: open.length > 0 ? <Badge tone="green">{open.length}</Badge> : undefined, render: () => (
          <>
            {/* OPEN POSITIONS — the live book */}
            <div className="blotter-section-head">
              <h3>Open Positions</h3>
              {open.length > 0
                ? <Badge tone="green">{open.length}</Badge>
                : <Badge tone="muted">flat</Badge>}
            </div>
            {open.length === 0 ? (
              <Card><p className="text-muted text-sm">No open positions.</p></Card>
            ) : (
              <Card>
                <DataTable
                  data={open}
                  columns={openColumnsWithExposure}
                  onRowClick={p => setSel({ kind: 'position', position: p })}
                  columnToggle
                  tableId="open-positions"
                  noWrap
                />
              </Card>
            )}

            <ReconciliationCard
              external={reconciliation?.external ?? []}
              orphaned={reconciliation?.orphaned ?? []}
              syncStatus={reconciliation?.sync_status}
            />

            {/* ORDER / FILL HISTORY */}
            <div className="blotter-section-head">
              <h3>Orders &amp; Fills</h3>
              <span className="text-muted text-sm">click a row for the causal chain</span>
              <a href={api.exportTradesCsv()} download className="btn btn-secondary btn-sm ml-auto">Export CSV</a>
            </div>
            <Card>
              <DataTable
                data={orderRows}
                columns={orderColumns}
                onRowClick={o => setSel({ kind: 'order', order: o })}
                emptyMessage="No orders yet."
              />
            </Card>

            {/* CLOSED POSITIONS */}
            {closed.length > 0 && (
              <>
                <div className="blotter-section-head"><h3>Closed Positions</h3></div>
                <Card>
                  <DataTable
                    data={closed}
                    columns={closedColumns}
                    onRowClick={p => setSel({ kind: 'position', position: p })}
                  />
                </Card>
              </>
            )}
          </>
        )},
        { id: 'analytics', label: 'Analytics', render: () => <TradeAnalyticsPanel /> },
        { id: 'insights', label: 'Insights', render: () => <Suspense fallback={<Card><p className="text-muted text-sm">Loading charts…</p></Card>}><TradeInsightsPanel /></Suspense> },
      ]} />

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

function ReconciliationCard({
  external,
  orphaned,
  syncStatus,
}: {
  external: ReconcileItem[]
  orphaned: ReconcileItem[]
  syncStatus?: string
}) {
  // Nothing to surface when there is no drift and the sync is healthy.
  if (external.length === 0 && orphaned.length === 0) return null

  return (
    <Card>
      <div className="blotter-section-head">
        <h4 className="blotter-group-label">Account Reconciliation</h4>
        {external.length > 0 && <Badge tone="yellow">{external.length} unmanaged</Badge>}
        {orphaned.length > 0 && <Badge tone="muted">{orphaned.length} orphaned</Badge>}
      </div>

      {external.length > 0 && (
        <>
          <p className="text-muted text-sm mb-1">
            On the exchange but <strong>not opened by Aletheia</strong> — adoption candidates.
            {syncStatus === 'stale' && ' (exchange view is stale)'}
          </p>
          <TableWrap>
          <table>
            <thead>
              <tr><th>Symbol</th><th>Dir</th><th>Qty</th><th>Entry</th><th>uPnL</th></tr>
            </thead>
            <tbody>
              {external.map((i, idx) => (
                <tr key={`ext-${i.symbol}-${i.direction}-${idx}`}>
                  <td className="mono">{fmtSymbol(i.symbol, i.market_type)}</td>
                  <td>{humanize(i.direction)}</td>
                  <td className="mono">{fmtNum(i.exchange?.quantity)}</td>
                  <td className="mono">{fmtNum(i.exchange?.entry_price)}</td>
                  <td>{pnlCell(i.exchange?.unrealized_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </TableWrap>
        </>
      )}

      {orphaned.length > 0 && (
        <>
          <p className="text-muted text-sm mt-3 mb-1">
            Open in Aletheia's books but <strong>no longer on the exchange</strong> — reconcile-close candidates.
          </p>
          <TableWrap>
          <table>
            <thead>
              <tr><th>Symbol</th><th>Dir</th><th>Qty</th><th>Entry</th><th>Mode</th></tr>
            </thead>
            <tbody>
              {orphaned.map((i, idx) => (
                <tr key={`orph-${i.symbol}-${i.direction}-${idx}`}>
                  <td className="mono">{fmtSymbol(i.symbol, i.market_type)}</td>
                  <td>{humanize(i.direction)}</td>
                  <td className="mono">{fmtNum(i.local?.quantity)}</td>
                  <td className="mono">{fmtNum(i.local?.entry_price)}</td>
                  <td>{humanize(i.local?.execution_mode)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </TableWrap>
        </>
      )}
    </Card>
  )
}

function OrderDetail({ order, navigate }: { order: Order; navigate: (p: string) => void }) {
  return (
    <>
      <DrawerSection label="Order">
        <DrawerField label="Symbol" value={fmtSymbol(order.symbol, order.market_type)} mono />
        <DrawerField label="Market" value={humanize(order.market_type)} />
        <DrawerField label="Direction" value={humanize(order.direction)} />
        <DrawerField label="Order Type" value={humanize(order.order_type)} />
        <DrawerField label="Mode" value={humanize(order.execution_mode)} />
        <DrawerField label="Status" value={<StatusBadge status={order.status} />} />
        {order.error_reason && (
          <DrawerField label="Error Reason" value={order.error_reason} />
        )}
      </DrawerSection>
      <DrawerSection label="Execution">
        <DrawerField label="Quantity" value={order.quantity} mono />
        <DrawerField label="Price" value={order.price} mono />
        <DrawerField label="Exchange Order ID" value={order.exchange_order_id} mono />
        <DrawerField label="Filled At" value={fmtTimestamp(order.filled_at)} mono />
        <DrawerField label="Created" value={fmtTimestamp(order.created_at)} mono />
      </DrawerSection>
      <DrawerSection label="Causal Chain">
        <CausalChain anchor={{ kind: 'order', order }} />
      </DrawerSection>
      {order.filled_at && (
        <div className="mt-2">
          <Button variant="secondary" onClick={() => navigate('/charts')}>View on Chart</Button>
        </div>
      )}
    </>
  )
}

function PositionDetail({ position }: { position: Position }) {
  return (
    <>
      <DrawerSection label="Position">
        <DrawerField label="Symbol" value={fmtSymbol(position.symbol, position.market_type)} mono />
        <DrawerField label="Market" value={humanize(position.market_type)} />
        <DrawerField label="Direction" value={humanize(position.direction)} />
        <DrawerField label="Mode" value={humanize(position.execution_mode)} />
        <DrawerField label="Status" value={<StatusBadge status={position.status} />} />
        {position.source === 'adopted' && (
          <DrawerField label="Source" value={<Badge tone="yellow">Adopted</Badge>} />
        )}
      </DrawerSection>
      <DrawerSection label="Pricing">
        <DrawerField label="Quantity" value={position.quantity} mono />
        <DrawerField label="Entry Price" value={position.entry_price} mono />
        <DrawerField label="Current Price" value={position.current_price} mono />
        <DrawerField label="Stop Price" value={position.stop_price} mono />
        <DrawerField label="Take Profit" value={position.take_profit_price} mono />
        {position.leverage && position.leverage > 1 && (
          <>
            <DrawerField label="Leverage" value={`${position.leverage}x`} />
            <DrawerField label="Liquidation Price" value={position.liquidation_price} mono />
          </>
        )}
      </DrawerSection>
      <DrawerSection label="PnL">
        <DrawerField label="Unrealized" value={position.unrealized_pnl} mono />
        <DrawerField label="Realized" value={position.realized_pnl} mono />
      </DrawerSection>
      {(position.entry_fee != null || position.exit_fee != null || position.slippage_cost != null) && (
        <DrawerSection label="Costs">
          <DrawerField label="Entry Fee" value={position.entry_fee != null ? `-${position.entry_fee.toFixed(4)}` : null} mono />
          <DrawerField label="Exit Fee" value={position.exit_fee != null ? `-${position.exit_fee.toFixed(4)}` : null} mono />
          <DrawerField label="Slippage (est.)" value={position.slippage_cost != null ? `-${position.slippage_cost.toFixed(4)}` : null} mono />
        </DrawerSection>
      )}
      {position.decision_reasoning && (
        <DrawerSection label="AI Decision">
          <pre className="decision-reasoning">{position.decision_reasoning}</pre>
        </DrawerSection>
      )}
      <DrawerSection label="Metadata">
        <DrawerField label="Strategy ID" value={position.strategy_id} mono />
        <DrawerField label="Close Reason" value={position.close_reason ? humanize(position.close_reason) : null} />
        <DrawerField label="Opened" value={fmtTimestamp(position.opened_at)} mono />
        <DrawerField label="Closed" value={fmtTimestamp(position.closed_at)} mono />
      </DrawerSection>
      <DrawerSection label="Causal Chain">
        <CausalChain anchor={{ kind: 'position', position }} />
      </DrawerSection>
    </>
  )
}
