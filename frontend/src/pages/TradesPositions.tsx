import { useEffect, useState, useMemo, useCallback, lazy, Suspense } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { createColumnHelper } from "@tanstack/react-table";
import { api } from "../api/client";
import type {
	Order,
	Position,
	ReconcileItem,
	ThesisLineageRecord,
} from "../types";
import { DataTable, StatusBadge } from "../components/DataTable";
import {
	DetailDrawer,
	DrawerSection,
	DrawerField,
} from "../components/DetailDrawer";
import AccountSnapshot from "../components/AccountSnapshot";
import CausalChain from "../components/CausalChain";
import PageHeader from "../components/PageHeader";
import { PositionActions } from "../components/PositionActions";
import { PositionDecisionSummary } from "../components/PositionDecisionSummary";
import Tabs from "../components/Tabs";
import ThesisText from "../components/ThesisText";
import {
	ActionRow,
	Badge,
	Button,
	Card,
	SymbolLink,
	TableWrap,
	useConfirm,
	useToast,
} from "../components/ui";
import {
	fmtNum,
	fmtSigned,
	humanize,
	fmtTimestamp,
	fmtSymbol,
	exchangeOf,
	marketBadgeTone,
	isPerpMarket,
} from "../lib/format";
import { useCellFlash } from "../lib/useCellFlash";
import { orderIntent, orderIntentLabel, type OrderIntent } from "../lib/orderIntent";
import { positionFreshness, positionKeys } from "../lib/queryKeys";
import { bootApi } from "../api/boot";
import TradeAnalyticsPanel from "../charts/TradeAnalyticsPanel";
const TradeInsightsPanel = lazy(() => import("../charts/TradeInsightsPanel"));
const TRADES_TABS = ["blotter", "analytics"] as const;
type TradesTab = typeof TRADES_TABS[number];
function isTradesTab(tab: string | null): tab is TradesTab {
	return !!tab && TRADES_TABS.includes(tab as TradesTab);
}

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

const posCol = createColumnHelper<Position>();
const ordCol = createColumnHelper<Order>();

export const pnlCell = (v: number | null | undefined) => {
	// Null/unknown PnL must not read as a flat green gain — show a muted dash.
	if (v == null) return <span className="mono text-muted">—</span>;
	if (v === 0) return <span className="mono">{fmtNum(v)}</span>;
	return (
		<span className={`mono ${v > 0 ? "pnl-positive" : "pnl-negative"}`}>
			{fmtSigned(v)}
		</span>
	);
};

/** PnL cell with 380ms flash feedback. */
function FlashPnLCell({ value }: { value: number | null | undefined }) {
  const flash = useCellFlash(value)
  if (value == null) return <span className="mono text-muted">—</span>
  if (value === 0) return <span className="mono">{fmtNum(value)}</span>
  return (
    <span className={`mono ${value > 0 ? 'pnl-positive' : 'pnl-negative'} ${flash}`}>
      {fmtSigned(value)}
    </span>
  )
}

/** Price cell with 380ms flash feedback. */
function FlashPriceCell({ value }: { value: number | null | undefined }) {
  const flash = useCellFlash(value)
  return <span className={`mono ${flash}`}>{fmtNum(value)}</span>
}

type CloseResult = Awaited<ReturnType<typeof api.closePosition>>;

export const closeResultQueryKeys = [
	positionKeys.all,
	["trades"],
	positionKeys.reconciliation,
	["portfolio"],
] as const;

export function closeResultNotice(
	position: Pick<Position, "symbol" | "market_type">,
	result: unknown,
): { kind: "closed" | "pending" | "error"; message: string } {
	const symbol = fmtSymbol(position.symbol, position.market_type);
	const payload = result && typeof result === "object" ? result as Partial<CloseResult> : {};
	if (payload.closed === true) return { kind: "closed", message: `${symbol} closed` };

	const status = typeof payload.status === "string" ? payload.status.trim().toLowerCase() : "";
	const rawOrderId = payload.exchange_order_id || payload.order_id || payload.close_order_id;
	const orderId = typeof rawOrderId === "string" && rawOrderId ? rawOrderId : undefined;
	const detail = ` (${status || "status unavailable"}${orderId ? `, order ${orderId}` : ""})`;
	if (payload.closed !== false) {
		return { kind: "error", message: `${symbol} close result is ambiguous${detail}` };
	}
	if (status === "submission_rejected" || status === "cancelled" || status === "error") {
		return { kind: "error", message: `${symbol} close was not accepted${detail}` };
	}
	if (status === "partially_filled") {
		return {
			kind: "pending",
			message: `${symbol} close partially filled — residual exposure remains${detail}`,
		};
	}
	if (status === "materialization_missing") {
		return {
			kind: "pending",
			message: `${symbol} fill acknowledged — awaiting reconciliation${detail}`,
		};
	}
	if (status === "pending" || status === "submitted") {
		return {
			kind: "pending",
			message: `${symbol} close ${payload.already_pending === true ? "already pending" : "submitted"} — awaiting confirmation${detail}`,
		};
	}
	return { kind: "error", message: `${symbol} close result is ambiguous${detail}` };
}

const marketBadge = (v: string) => (
	<Badge tone={marketBadgeTone(v)}>
		{isPerpMarket(v) ? "Perp" : humanize(v)}
	</Badge>
);

const pnlSourceBadge = (source: string | undefined) => {
	const value = source || "unavailable";
	const tone = value === "exchange_exact"
		? "green"
		: value === "local_estimate"
			? "muted"
			: "yellow";
	return <Badge tone={tone}>{humanize(value)}</Badge>;
};

const intentTone = (intent: OrderIntent) =>
	intent === "close" ? "yellow" : intent === "entry" ? "blue" : "muted";

const orderIntentBadge = (order: Order) => {
	const intent = orderIntent(order);
	return <Badge tone={intentTone(intent)}>{orderIntentLabel(intent)}</Badge>;
};

const openColumns = [
	posCol.display({
		id: 'instrument',
		header: 'Instrument',
		cell: ({ row }) => {
			const p = row.original
			const lev = p.leverage
			return (
				<span>
					<SymbolLink symbol={p.symbol} marketType={p.market_type} bold />
					<br />
					<span className="text-muted text-sm mono">{exchangeOf(p.execution_mode)} · {p.market_type}{lev && lev > 1 ? ` · ${lev}x` : ''}</span>
				</span>
			)
		},
	}),
	// 2. Side / Qty
	posCol.display({
		id: 'side_qty',
		header: 'Side / Qty',
		cell: ({ row }) => {
			const p = row.original
			return (
				<span>
					<Badge tone={p.direction === 'long' ? 'green' : 'red'}>{humanize(p.direction)}</Badge>
					<br />
					<span className="mono text-sm">{fmtNum(p.quantity)}</span>
				</span>
			)
		},
	}),
	// 3. Price (Entry / Current)
	posCol.display({
		id: 'price_pair',
		header: 'Entry / Current',
		cell: ({ row }) => {
			const p = row.original
			return (
				<span className="mono">
					{fmtNum(p.entry_price)}
					<br />
					<FlashPriceCell value={p.current_price} />
				</span>
			)
		},
	}),
	// 4. Risk Bracket (SL / TP)
	posCol.display({
		id: 'bracket',
		header: 'SL / TP',
		cell: ({ row }) => {
			const p = row.original
			return (
				<span className="mono">
					{fmtNum(p.stop_price)} / {fmtNum(p.take_profit_price)}
				</span>
			)
		},
	}),
	// 5. Unrealized PnL (value + source badge)
	posCol.display({
		id: 'upnl',
		header: 'Unrealized PnL',
		cell: ({ row }) => {
			const p = row.original
			return (
				<span>
					<FlashPnLCell value={p.unrealized_pnl} />
					{p.unrealized_pnl_source && (
						<br />
					)}
					{p.unrealized_pnl_source && pnlSourceBadge(p.unrealized_pnl_source)}
				</span>
			)
		},
	}),
	// 6. Actions (kept as-is for close button)
	posCol.display({
		id: 'actions',
		header: '',
		cell: ({ row }) => {
			const p = row.original
			return (
				<PositionActions position={p} />
			)
		},
	}),
];

const closedColumns = [
	posCol.accessor("symbol", {
		header: "Symbol",
		cell: (info) => <SymbolLink symbol={info.getValue()} marketType={info.row.original.market_type} bold />,
	}),
	posCol.accessor("direction", {
		header: "Dir",
		cell: (info) => humanize(info.getValue()),
	}),
	posCol.accessor("quantity", {
		header: "Qty",
		cell: (info) => <span className="mono">{fmtNum(info.getValue())}</span>,
	}),
	posCol.accessor("entry_price", {
		header: "Entry (USDT)",
		cell: (info) => <span className="mono">{fmtNum(info.getValue())}</span>,
	}),
	posCol.display({
		id: "realized_pnl_combo",
		header: "PnL",
		cell: ({ row }) => {
			const p = row.original
			const statusNode = p.realized_pnl_complete !== false ? null : (
				<span className="pnl-warning text-sm">
					{p.realized_pnl_unavailable_reason === "funding_permanently_unrecoverable"
						? 'funding lost'
						: p.realized_pnl_unavailable_reason === "broker_funding_history_expired"
							? 'history expired'
							: humanize(p.realized_pnl_unavailable_reason ?? 'incomplete')}
				</span>
			)
			return (
				<span>
					<FlashPnLCell value={p.realized_pnl} />
					{statusNode && <><br />{statusNode}</>}
				</span>
			)
		},
	}),
	posCol.accessor("close_reason", {
		header: "Reason",
		cell: (info) => (info.getValue() ? humanize(info.getValue()!) : "—"),
	}),
	posCol.accessor("closed_at", {
		header: "Closed",
		cell: (info) => <span className="mono">{fmtTimestamp(info.getValue())}</span>,
	}),
];

const orderColumns = [
	ordCol.display({
		id: "intent",
		header: "Intent",
		cell: ({ row }) => orderIntentBadge(row.original),
	}),
	ordCol.accessor("symbol", {
		header: "Symbol",
		cell: (info) => <SymbolLink symbol={info.getValue()} marketType={info.row.original.market_type} bold />,
	}),
	ordCol.accessor("direction", {
		header: "Dir",
		cell: (info) => humanize(info.getValue()),
	}),
	ordCol.accessor("quantity", {
		header: "Qty",
		cell: (info) => <span className="mono">{fmtNum(info.getValue())}</span>,
	}),
	ordCol.accessor("price", {
		header: "Price (USDT)",
		cell: (info) => <span className="mono">{fmtNum(info.getValue())}</span>,
	}),
	ordCol.accessor("execution_mode", {
		header: "Mode",
		cell: (info) => humanize(info.getValue()),
	}),
	ordCol.accessor("status", {
		header: "Status",
		cell: (info) => <StatusBadge status={info.getValue()} />,
	}),
	ordCol.accessor("error_reason", {
		header: "Error",
		cell: (info) => info.getValue() || "—",
	}),
	ordCol.accessor("filled_at", {
		header: "Filled",
		cell: (info) => (
			<span className="mono">{fmtTimestamp(info.getValue())}</span>
		),
	}),
	ordCol.accessor("created_at", {
		header: "Created",
		cell: (info) => (
			<span className="mono">{fmtTimestamp(info.getValue())}</span>
		),
	}),
];

type Selection =
	| { kind: "order"; id: string }
	| { kind: "position"; id: string }
	| null;

/**
 * Shared manual-close hook used by both the row button and the drawer.
 * Paper/demo only — live positions get a clear error (409), not a silent no-op.
 * Confirm → market close → invalidate + toast + auto-close drawer.
 */
function useManualClose(onSuccess?: () => void) {
	const confirm = useConfirm();
	const { toast } = useToast();
	const qc = useQueryClient();

	const mutation = useMutation({
		mutationFn: (position: Position) => api.closePosition(position.id),
		onSuccess: (data, position) => {
			for (const queryKey of closeResultQueryKeys) {
				qc.invalidateQueries({ queryKey });
			}
			const notice = closeResultNotice(position, data);
			toast(notice.kind === "closed" ? "success" : notice.kind === "pending" ? "info" : "error", notice.message);
			if (notice.kind === "closed") onSuccess?.();
		},
		onError: (err: any, position) => {
			const detail = err?.message || "close failed";
			const isLive =
				detail.includes("live_position_requires_exchange_close") ||
				detail.includes("live");
			toast(
				"error",
				isLive
					? `${fmtSymbol(position.symbol, position.market_type)} — close this live position directly on the exchange.`
					: `Could not close: ${detail}`,
			);
		},
	});

	const close = useCallback(
		async (position: Position) => {
			const mode = position.execution_mode || "";
			const isLive = mode.endsWith("_live_limited");
			if (isLive) {
				toast(
					"error",
					`${fmtSymbol(position.symbol, position.market_type)} — close this live position directly on the exchange.`,
				);
				return;
			}
			const ok = await confirm({
				title: `Close ${fmtSymbol(position.symbol, position.market_type)}?`,
				description: `Market-close this ${humanize(position.direction)} position at current price.`,
				detail:
					position.unrealized_pnl != null
						? `Unrealized PnL: ${fmtSigned(position.unrealized_pnl)} USDT`
						: undefined,
				confirmLabel: "Close position",
				cancelLabel: "Cancel",
				variant: "danger",
			});
			if (ok) mutation.mutate(position);
		},
		[confirm, toast, mutation],
	);

	return { close, isPending: mutation.isPending };
}

export default function TradesPositions() {
	const navigate = useNavigate();
	const [searchParams, setSearchParams] = useSearchParams();
	const requestedTab = searchParams.get("tab");
	const entryPlanId = searchParams.get("entry_plan_id");
	const activeTab = isTradesTab(requestedTab) ? requestedTab : "blotter";
	const [closedExhausted, setClosedExhausted] = useState(false);
	const [closedExtra, setClosedExtra] = useState<Position[]>([]);
	const { data: positions, isPending: positionsPending, isError: positionsError } = useQuery({
		queryKey: positionKeys.all,
		queryFn: api.getPositions,
		...positionFreshness,
	});
	const { data: orders, isPending: ordersPending, isError: ordersError } = useQuery({
		queryKey: entryPlanId ? ["trades", entryPlanId] : ["trades"],
		queryFn: () => api.getTrades(entryPlanId),
	});
	const { data: reconciliation, isPending: reconPending, isError: reconError } = useQuery({
		queryKey: positionKeys.reconciliation,
		queryFn: api.getPositionsReconciliation,
	});
	const { data: overviewBootstrap } = useQuery({
		queryKey: ["ui-overview-bootstrap"],
		queryFn: ({ signal }) => bootApi.getOverviewBootstrap({ signal }),
		staleTime: 5_000,
		refetchInterval: 15_000,
	});
	const executionMode = typeof overviewBootstrap?.account?.execution_mode === "string"
		? overviewBootstrap.account.execution_mode
		: undefined;

	const [closedLoadingMore, setClosedLoadingMore] = useState(false);
	const loadMoreClosed = useCallback(async (currentClosed: Position[]) => {
		const last = currentClosed[currentClosed.length - 1];
		if (!last) return;
		setClosedLoadingMore(true);
		try {
			const next = await api.getPositionsPaged({ limit: 100, cursor_id: last.id, status: 'closed' });
			if (next.length) {
				setClosedExtra(prev => [...prev, ...next]);
			} else {
				setClosedExhausted(true);
			}
		} finally {
			setClosedLoadingMore(false);
		}
	}, []);

	const positionsArray = positions ?? []
	const ordersArray = orders ?? []
	const positionsAvailable = !positionsPending && !positionsError && positions != null
	const ordersAvailable = !ordersPending && !ordersError && orders != null
	const reconAvailable = !reconPending && !reconError && reconciliation != null
	const [sel, setSel] = useState<Selection>(null);

	// useManualClose: row-inline close button. Closing from the drawer shares the
	// same hook so both paths invalidate the same query keys and show the same toast.
	const { close: manualClose, isPending: closePending } = useManualClose(() =>
		setSel(null),
	);

	const allPos = positionsArray as Position[];
	const open = allPos.filter((p) => p.status === "open");
	const closed = [...allPos.filter((p) => p.status !== "open"), ...closedExtra];
	const orderRows = ordersArray as Order[];
	const selectedOrder =
		sel?.kind === "order"
			? orderRows.find((order) => order.id === sel.id) ?? null
			: null;
	const selectedPosition =
		sel?.kind === "position"
			? allPos.find((position) => position.id === sel.id) ?? null
			: null;
	// Lazy-load full position detail (includes raw_json reasoning fields) when drawer opens.
	// Falls back to the light projection row already in memory while loading.
	const { data: positionDetail } = useQuery({
		queryKey: ['position-detail', selectedPosition?.id],
		queryFn: () => api.getPosition(selectedPosition!.id),
		enabled: !!selectedPosition,
		staleTime: 60_000,
	});
	const displayedPosition = positionDetail ?? selectedPosition;

	useEffect(() => {
		if (requestedTab === activeTab) return;
		const next = new URLSearchParams(searchParams);
		next.set("tab", activeTab);
		setSearchParams(next, { replace: true });
	}, [activeTab, requestedTab, searchParams, setSearchParams]);

	const setActiveTab = (tab: string) => {
		const next = new URLSearchParams(searchParams);
		next.set("tab", isTradesTab(tab) ? tab : "blotter");
		setSearchParams(next);
	};

	// Map query state → DataTable.state; initial error/no data → unavailable, success empty → empty, error with cached data → stale
	const positionsState = positionsPending && positionsArray.length === 0
		? 'loading'
		: positionsError && positionsArray.length === 0
			? 'unavailable'
			: positionsError && positionsArray.length > 0
				? 'stale'
				: undefined
	const ordersState = ordersPending && ordersArray.length === 0
		? 'loading'
		: ordersError && ordersArray.length === 0
			? 'unavailable'
			: ordersError && ordersArray.length > 0
				? 'stale'
				: undefined

	// Per-position notional (local book), same formula as the backend exposure_by_symbol
	// (pnl.py:248): quantity × (current_price ?? entry_price). Total over all open
	// positions is the denominator for each row's exposure %.
	const posNotional = (p: Position) =>
		p.quantity * (p.current_price ?? p.entry_price);
	const totalNotional = open.reduce((s, p) => s + posNotional(p), 0);

	// Exposure column folded into the open-positions blotter (USDT / % share of total
	// open notional), replacing the standalone "Exposure by Symbol" card.
	const openColumnsWithExposure = useMemo(
		() => [
			...openColumns,
			posCol.accessor((p) => posNotional(p), {
				id: "exposure",
				header: "Exposure (USDT / %)",
				cell: (info) => {
					const notional = info.getValue() as number;
					const pct = totalNotional > 0 ? (notional / totalNotional) * 100 : 0;
					return (
						<span className="mono">
							{fmtNum(notional)} / {pct.toFixed(1)}%
						</span>
					);
				},
			}),
			// Inline close button. Live positions show a disabled badge (close on exchange UI).
			posCol.display({
				id: "manual_close",
				header: "",
				cell: ({ row }) => {
					const pos = row.original;
					const isLive = (pos.execution_mode || "").endsWith("_live_limited");
					if (isLive) {
						return <span className="text-muted text-xs">exchange only</span>;
					}
					return (
						<Button
							variant="danger"
							size="sm"
							disabled={closePending}
							onClick={(e) => {
								e.stopPropagation();
								manualClose(pos);
							}}
						>
							Close
						</Button>
					);
				},
			}),
		],
		[totalNotional, closePending, manualClose],
	);

	return (
		<div>
			<PageHeader
				title="Trades & Positions"
				role="Execute · the blotter for both ends of the causal chain — orders & fills feeding open and closed positions. Click any order or position to inspect its full proposal → risk → order → fill → position chain."
			/>
			<AccountSnapshot />

			<Tabs
				value={activeTab}
				onChange={setActiveTab}
				tabs={[
					{
						id: "blotter",
						label: "Blotter",
					badge:
						positionsAvailable && open.length > 0 ? (
							<Badge tone="green">{open.length}</Badge>
						) : undefined,
						render: () => (
							<>
								{/* OPEN POSITIONS — the live book */}
								<div className="blotter-section-head">
									<h3>Open Positions</h3>
									{positionsAvailable ? (
										open.length > 0 ? (
											<Badge tone="green">{open.length}</Badge>
										) : (
											<Badge tone="muted">flat</Badge>
										)
									) : (
										<Badge tone="muted">not available</Badge>
									)}
								</div>
								{positionsAvailable ? (
									open.length === 0 ? (
										<Card>
											<p className="text-muted text-sm">No open positions.</p>
										</Card>
										) : (
											<Card>
												<DataTable
													data={open}
													columns={openColumnsWithExposure}
													onRowClick={(p) =>
														setSel({ kind: "position", id: p.id })
													}
													columnToggle
													tableId="open-positions"
													noWrap
												state={positionsState}
											/>
										</Card>
									)
								) : (
									<Card>
										<DataTable
											data={[]}
											columns={openColumnsWithExposure}
											state="unavailable"
										/>
									</Card>
								)}

								<ReconciliationCard
									external={reconAvailable ? reconciliation?.external ?? [] : []}
									orphaned={reconAvailable ? reconciliation?.orphaned ?? [] : []}
									syncStatus={reconAvailable ? reconciliation?.sync_status : undefined}
									unavailable={!reconAvailable}
								/>

								{/* ORDERS & FILLS  |  CLOSED POSITIONS — side by side so realized PnL is
                visible without scrolling past the full-width order ledger. Falls back
                to a single full-width column when there are no closed positions yet. */}
								<div
									className={`blotter-split${closed.length > 0 ? "" : " single"}`}
								>
									<div className="blotter-col">
										<div className="blotter-section-head">
											<h3>Orders &amp; Fills</h3>
											<span className="text-muted text-sm">
												click a row for the causal chain
											</span>
											<a
												href={api.exportTradesCsv()}
												download
												className="btn btn-secondary btn-sm ml-auto"
											>
												Export CSV
											</a>
										</div>
											<Card>
												<DataTable
													data={orderRows}
													columns={orderColumns}
													onRowClick={(o) => setSel({ kind: "order", id: o.id })}
													state={ordersState}
													emptyMessage="No orders yet."
												/>
											</Card>
									</div>

								{closed.length > 0 && (
									<div className="blotter-col">
										<div className="blotter-section-head">
											<h3>Closed Positions</h3>
											{closed.length >= 200 && (
												<span className="text-muted text-sm">First {closed.length} rows</span>
											)}
										</div>
										<Card>
											<DataTable
												data={closed}
												columns={closedColumns}
												onRowClick={(p) =>
													setSel({ kind: "position", id: p.id })
												}
											/>
										</Card>
										{!closedExhausted && (closed.length >= 200 || closedExtra.length > 0) && (
											<div className="mt-2 flex justify-end">
												<Button
													variant="secondary"
													onClick={() => loadMoreClosed(closed)}
													disabled={closedLoadingMore}
												>
													{closedLoadingMore ? 'Loading…' : 'Load more closed positions'}
												</Button>
											</div>
										)}
									</div>
								)}
								</div>
							</>
						),
					},
					// Analytics + Insights merged into one scrolling view (both read the same
					// closed-trade population; Insights is the superset of charts, Analytics
					// adds the by-strategy-source / by-scanner-family attribution bars).
					{
						id: "analytics",
						label: "Analytics",
						render: () => (
							<>
								<TradeAnalyticsPanel executionMode={executionMode} />
								<Suspense
									fallback={
										<Card>
											<p className="text-muted text-sm">Loading charts…</p>
										</Card>
									}
								>
									<TradeInsightsPanel executionMode={executionMode} />
								</Suspense>
							</>
						),
					},
				]}
			/>

				<DetailDrawer
					open={!!sel}
					onClose={() => setSel(null)}
					title={sel?.kind === "order" ? "Order Detail" : "Position Detail"}
				>
					{sel?.kind === "order" && (
						ordersAvailable && selectedOrder ? (
							<OrderDetail order={selectedOrder} navigate={navigate} />
						) : (
							<SelectionUnavailable kind="order" unavailable={!ordersAvailable} />
						)
					)}
					{sel?.kind === "position" && (
					positionsAvailable && displayedPosition ? (
						<PositionDetail
							position={displayedPosition}
							onClose={() => setSel(null)}
						/>
						) : (
							<SelectionUnavailable kind="position" unavailable={!positionsAvailable} />
						)
					)}
				</DetailDrawer>
		</div>
	);
}

function SelectionUnavailable({
	kind,
	unavailable,
}: {
	kind: "order" | "position";
	unavailable: boolean;
}) {
	return (
		<DrawerSection label="Current Record">
			<p className="text-muted text-sm">
				{unavailable
					? `Current ${kind} facts are unavailable; the drawer is not showing the last clicked snapshot.`
					: `Selected ${kind} is no longer in the current query result.`}
			</p>
		</DrawerSection>
	);
}

function ReconciliationCard({
	external,
	orphaned,
	syncStatus,
	unavailable = false,
}: {
	external: ReconcileItem[];
	orphaned: ReconcileItem[];
	syncStatus?: string;
	unavailable?: boolean;
}) {
	// #117 fix: previously returned null whenever external.length + orphaned
	// .length was 0 — including when syncStatus was 'stale' / 'error' /
	// 'unconfigured'. That hid the reconciliation status entirely: an operator
	// couldn't tell whether the "no drift" was because everything was in sync
	// OR because the sync itself had never run / had failed / was stale (which
	// is not the same story at all). Show the status card in those cases too,
	// just without the drift lists — the badge carries the honest state.
	const hasDrift = external.length > 0 || orphaned.length > 0;
	const isDegraded = syncStatus && !["ok", "unconfigured"].includes(syncStatus);
	if (!unavailable && !hasDrift && !isDegraded) return null;

	return (
		<Card>
			<div className="blotter-section-head">
				<h4 className="blotter-group-label">Account Reconciliation</h4>
				{unavailable ? (
					<Badge tone="muted">not available</Badge>
				) : (
					<>
						{external.length > 0 && (
							<Badge tone="yellow">{external.length} unmanaged</Badge>
						)}
						{orphaned.length > 0 && (
							<Badge tone="muted">{orphaned.length} orphaned</Badge>
						)}
						{!hasDrift && isDegraded && (
							<Badge tone={syncStatus === "stale" ? "yellow" : "red"}>
								sync {syncStatus}
							</Badge>
						)}
					</>
				)}
			</div>
			{unavailable ? (
				<p className="text-muted text-sm">
					Reconciliation unavailable. The read failed; this is not an empty result.
				</p>
			) : (
				<>
					{!hasDrift && isDegraded && (
						<p className="text-muted text-sm">
							No drift reported, but the exchange sync is{" "}
							<strong>{syncStatus}</strong> — the "no drift" result may be based on
							stale or missing data. Fix the sync source before treating the
							reconciliation as complete.
						</p>
					)}

					{external.length > 0 && (
						<>
							<p className="text-muted text-sm mb-1">
								On the exchange but <strong>not opened by Aletheia</strong> —
								adoption candidates.
								{syncStatus === "stale" && " (exchange view is stale)"}
							</p>
							<TableWrap>
								<table>
									<thead>
										<tr>
											<th>Symbol</th>
											<th>Dir</th>
											<th>Qty</th>
											<th>Entry</th>
											<th>uPnL</th>
										</tr>
									</thead>
									<tbody>
										{external.map((i, idx) => (
											<tr key={`ext-${i.symbol}-${i.direction}-${idx}`}>
												<td><SymbolLink symbol={i.symbol} marketType={i.market_type} /></td>
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
								Open in Aletheia's books but{" "}
								<strong>no longer on the exchange</strong> — reconcile-close
								candidates.
							</p>
							<TableWrap>
								<table>
									<thead>
										<tr>
											<th>Symbol</th>
											<th>Dir</th>
											<th>Qty</th>
											<th>Entry</th>
											<th>Mode</th>
										</tr>
									</thead>
									<tbody>
										{orphaned.map((i, idx) => (
											<tr key={`orph-${i.symbol}-${i.direction}-${idx}`}>
												<td><SymbolLink symbol={i.symbol} marketType={i.market_type} /></td>
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
				</>
			)}
		</Card>
	);
}

function OrderDetail({
	order,
	navigate,
}: {
	order: Order;
	navigate: (p: string) => void;
}) {
	const intent = orderIntent(order);
	const showCloseIdentity = intent !== "entry";
	return (
		<>
			<DrawerSection label="Order">
				<DrawerField label="Intent" value={orderIntentBadge(order)} />
				<DrawerField
					label="Symbol"
					value={fmtSymbol(order.symbol, order.market_type)}
					mono
				/>
				<DrawerField label="Market" value={humanize(order.market_type)} />
				<DrawerField label="Direction" value={humanize(order.direction)} />
				<DrawerField label="Order Type" value={humanize(order.order_type)} />
				<DrawerField label="Mode" value={humanize(order.execution_mode)} />
				<DrawerField
					label="Status"
					value={<StatusBadge status={order.status} />}
				/>
				{order.error_reason && (
					<DrawerField label="Error Reason" value={order.error_reason} />
				)}
				{showCloseIdentity && (
					<>
						<DrawerField
							label="Linked Position"
							value={order.position_id || "unavailable"}
							mono
						/>
						<DrawerField
							label="Close Reason"
							value={
								order.close_reason ? humanize(order.close_reason) : "unavailable"
							}
						/>
					</>
				)}
				<DrawerField
					label="Provenance"
					value={order.source ? humanize(order.source) : null}
				/>
			</DrawerSection>
			<DrawerSection label="Execution">
				<DrawerField label="Quantity" value={order.quantity} mono />
				<DrawerField label="Price" value={order.price} mono />
				<DrawerField
					label="Exchange Order ID"
					value={order.exchange_order_id}
					mono
				/>
				<DrawerField
					label="Filled At"
					value={fmtTimestamp(order.filled_at)}
					mono
				/>
				<DrawerField
					label="Created"
					value={fmtTimestamp(order.created_at)}
					mono
				/>
			</DrawerSection>
			<DrawerSection label="Causal Chain">
				<CausalChain anchor={{ kind: "order", order }} />
			</DrawerSection>
			<DrawerSection label="Thesis Lineage">
				<ThesisLineagePanel order={order} />
			</DrawerSection>
			{order.filled_at && (
				<div className="mt-2">
					{/* #116 fix: `View on Chart` used to navigate('/charts') with no
              params, so the Charts page always initialised to its default
              symbol (BTC/USDT) — the operator had to manually pick the
              order's actual symbol to see anything relevant. Pass the
              order's symbol + market_type via searchParams so the chart
              lands on the RIGHT instrument. Charts must read these on mount. */}
					<Button
						variant="secondary"
						onClick={() =>
							navigate({
								pathname: "/charts",
								search: `?symbol=${encodeURIComponent(order.symbol)}&market_type=${encodeURIComponent(order.market_type || "spot")}`,
							} as any)
						}
					>
						View on Chart
					</Button>
				</div>
			)}
		</>
	);
}

function PositionDetail({
	position,
	onClose,
}: {
	position: Position;
	onClose: () => void;
}) {
	return (
		<>
			<PositionActions position={position} />
			<DrawerSection label="Position">
				<DrawerField
					label="Symbol"
					value={fmtSymbol(position.symbol, position.market_type)}
					mono
				/>
				<DrawerField label="Market" value={humanize(position.market_type)} />
				<DrawerField label="Direction" value={humanize(position.direction)} />
				<DrawerField label="Mode" value={humanize(position.execution_mode)} />
				<DrawerField
					label="Status"
					value={<StatusBadge status={position.status} />}
				/>
				{position.source === "adopted" && (
					<DrawerField
						label="Source"
						value={<Badge tone="yellow">Adopted</Badge>}
					/>
				)}
			</DrawerSection>
			<DrawerSection label="Pricing">
				<DrawerField label="Quantity" value={position.quantity} mono />
				<DrawerField label="Entry Price" value={position.entry_price} mono />
				<DrawerField
					label="Current Price"
					value={position.current_price}
					mono
				/>
				<DrawerField label="Stop Price" value={position.stop_price} mono />
				<DrawerField
					label="Take Profit"
					value={position.take_profit_price}
					mono
				/>
				{position.leverage && position.leverage > 1 && (
					<>
						<DrawerField label="Leverage" value={`${position.leverage}x`} />
						<DrawerField
							label="Liquidation Price"
							value={position.liquidation_price}
							mono
						/>
					</>
				)}
			</DrawerSection>
			<DrawerSection label="PnL">
				<DrawerField label="Unrealized" value={position.unrealized_pnl} mono />
				<DrawerField
					label="uPnL source"
					value={pnlSourceBadge(position.unrealized_pnl_source)}
				/>
				<DrawerField label="Realized" value={position.realized_pnl} mono />
				{position.realized_pnl_complete === false && (
					<DrawerField
						label="Realized status"
						value={
							position.realized_pnl_unavailable_reason ===
							"funding_permanently_unrecoverable"
								? "funding lost — settler gave up (never fabricated)"
								: position.realized_pnl_unavailable_reason ===
										"broker_funding_history_expired"
									? "funding history expired — economics unavailable (never fabricated)"
									: humanize(
											position.realized_pnl_unavailable_reason ||
												"economics unavailable",
										)
						}
					/>
				)}
			</DrawerSection>
			{(position.entry_fee != null ||
				position.exit_fee != null ||
				position.slippage_cost != null ||
				position.funding_status != null ||
				position.funding_cost != null ||
				position.funding_source != null) && (
				<DrawerSection label="Costs">
					<DrawerField
						label="Entry Fee"
						value={
							position.entry_fee != null
								? `-${fmtNum(position.entry_fee)}`
								: null
						}
						mono
					/>
					<DrawerField
						label="Exit Fee"
						value={
							position.exit_fee != null ? `-${fmtNum(position.exit_fee)}` : null
						}
						mono
					/>
					<DrawerField
						label="Slippage (diagnostic)"
						value={
							position.slippage_cost != null
								? `-${fmtNum(position.slippage_cost)}`
								: null
						}
						mono
					/>
					<DrawerField
						label="Funding"
						value={
							position.funding_status === "complete" &&
							position.funding_cost != null
								? fmtSigned(-position.funding_cost)
								: position.funding_status === "not_applicable"
									? fmtNum(0)
									: null
						}
						mono
					/>
					<DrawerField
						label="Funding source"
						value={humanize(position.funding_source)}
					/>
					<DrawerField
						label="Funding status"
						value={humanize(position.funding_status)}
					/>
				</DrawerSection>
			)}
			{position.decision_reasoning && (
				<DrawerSection label="AI Decision">
					<PositionDecisionSummary position={position} />
				</DrawerSection>
			)}
			<DrawerSection label="Metadata">
				<DrawerField label="Strategy ID" value={position.strategy_id} mono />
				<DrawerField
					label="Close Reason"
					value={position.close_reason ? humanize(position.close_reason) : null}
				/>
				<DrawerField
					label="Opened"
					value={fmtTimestamp(position.opened_at)}
					mono
				/>
				<DrawerField
					label="Closed"
					value={fmtTimestamp(position.closed_at)}
					mono
				/>
			</DrawerSection>
			<DrawerSection label="Causal Chain">
				<CausalChain anchor={{ kind: "position", position }} />
			</DrawerSection>
			<DrawerSection label="Thesis Lineage">
				<ThesisLineagePanel position={position} />
			</DrawerSection>
			{position.status === "open" && (
				<ClosePositionPanel position={position} onClose={onClose} />
			)}
		</>
	);
}

/** Drawer action panel — manual market/limit close for paper/demo positions. */
function ClosePositionPanel({
	position,
	onClose,
}: {
	position: Position;
	onClose: () => void;
}) {
	const { close, isPending } = useManualClose(onClose);
	const isLive = (position.execution_mode || "").endsWith("_live_limited");
	const [showLimit, setShowLimit] = useState(false);
	const [limitPrice, setLimitPrice] = useState("");
	const confirm = useConfirm();
	const { toast } = useToast();
	const qc = useQueryClient();

	const limitMutation = useMutation({
		mutationFn: (price: number) =>
			api.closePosition(position.id, {
				order_type: "limit",
				limit_price: price,
			}),
		onSuccess: (_data, price) => {
			qc.invalidateQueries({ queryKey: positionKeys.all });
			toast("success", `Limit exit set at ${fmtNum(price)} USDT`);
			setShowLimit(false);
			setLimitPrice("");
			onClose();
		},
		onError: (err: any) =>
			toast("error", err?.message || "Could not set limit exit"),
	});

	const submitLimit = async () => {
		const price = parseFloat(limitPrice);
		if (!isFinite(price) || price <= 0) {
			toast("error", "Limit price must be a positive number");
			return;
		}
		const ok = await confirm({
			title: `Set limit exit at ${fmtNum(price)} USDT?`,
			description: `Position will close when price reaches ${fmtNum(price)}. OCO: stop still fires first if hit.`,
			confirmLabel: "Set limit exit",
			cancelLabel: "Cancel",
		});
		if (ok) limitMutation.mutate(price);
	};

	if (isLive) {
		return (
			<DrawerSection label="Manual Close">
				<p className="text-muted text-sm">
					Live positions must be closed directly on the exchange UI.
				</p>
			</DrawerSection>
		);
	}

	return (
		<DrawerSection label="Manual Close">
			<ActionRow align="start">
				<Button
					variant="danger"
					onClick={() => close(position)}
					disabled={isPending || limitMutation.isPending}
				>
					{isPending ? "Closing…" : "Close at market price"}
				</Button>
				<Button
					variant="secondary"
					onClick={() => setShowLimit((v) => !v)}
					disabled={isPending || limitMutation.isPending}
				>
					{limitMutation.isPending ? "Setting…" : showLimit ? "Cancel limit" : "Set limit exit"}
				</Button>
			</ActionRow>
			{showLimit && (
				<div className="close-limit-row">
					<input
						type="number"
						placeholder="Limit price (USDT)"
						value={limitPrice}
						onChange={(e) => setLimitPrice(e.target.value)}
						className="dt-filter close-limit-input"
					/>
					<Button
						variant="primary"
						onClick={submitLimit}
						disabled={!limitPrice || isPending || limitMutation.isPending}
					>
						Confirm limit
					</Button>
				</div>
			)}
			<p className="text-muted text-sm close-panel-note">
				Market close uses a fresh mark price (never uses stored current_price).
				Limit exit is watched locally — position stays open until price crosses
				the level, then closes at market.
			</p>
		</DrawerSection>
	);
}

function ThesisLineagePanel({
	order,
	position,
}: {
	order?: Order;
	position?: Position;
}) {
	const query = useQuery({
		queryKey: [
			"thesis-lineage",
			order?.id ?? null,
			position?.id ?? null,
			order?.proposal_id ?? position?.proposal_id ?? null,
		],
		queryFn: () =>
			api.getThesisLineage({
				days: 90,
				order_id: order?.id,
				position_id: position?.id,
				proposal_id: order?.proposal_id ?? position?.proposal_id ?? undefined,
				limit: 3,
			}),
		enabled: !!(
			order?.id ||
			position?.id ||
			order?.proposal_id ||
			position?.proposal_id
		),
	});

	if (query.isLoading)
		return <p className="text-muted text-sm">Loading thesis lineage…</p>;
	if (query.error)
		return <p className="text-muted text-sm">Thesis lineage not available.</p>;
	const record = query.data?.records?.[0];
	if (!record) {
		return (
			<p className="text-muted text-sm">
				No thesis lineage record found for this trade.
			</p>
		);
	}
	return <ThesisLineageRecordView record={record} />;
}

function ThesisLineageRecordView({ record }: { record: ThesisLineageRecord }) {
	const actual = record.actual_pnl_pct;
	const shadow = record.shadow_pnl_pct;
	const batch = record.counterfactual_avg_pnl_pct;
	return (
		<div className="thesis-lineage">
			<div className="thesis-lineage-head">
				<Badge tone={verdictTone(record.verdict)}>
					{humanize(record.verdict)}
				</Badge>
				<span className="mono text-xs">{record.lineage_id}</span>
			</div>
			<div className="thesis-lineage-grid">
				<LineageMetric
					label="Actual"
					value={actual == null ? "—" : `${fmtSigned(actual)}%`}
					tone={actual == null ? undefined : actual >= 0 ? "good" : "bad"}
				/>
				<LineageMetric
					label="Shadow"
					value={shadow == null ? "—" : `${fmtSigned(shadow)}%`}
					tone={shadow == null ? undefined : shadow >= 0 ? "good" : "bad"}
				/>
				<LineageMetric
					label="Batch avg"
					value={batch == null ? "—" : `${fmtSigned(batch)}%`}
					tone={batch == null ? undefined : batch >= 0 ? "good" : "bad"}
				/>
			</div>
			<div className="thesis-lineage-meta">
				<span>{humanize(record.root_type)}</span>
				{record.scanner_family && (
					<span>{humanize(record.scanner_family)}</span>
				)}
				{record.decision_action && (
					<span>decision: {humanize(record.decision_action)}</span>
				)}
				{record.decision_conviction != null && (
					<span>conviction: {record.decision_conviction}</span>
				)}
				{record.universe_rank != null && (
					<span>universe rank: {record.universe_rank}</span>
				)}
			</div>
			{record.observation_thesis ? (
				<ThesisText text={record.observation_thesis} />
			) : (
				<p className="text-muted text-sm">No observation thesis recorded.</p>
			)}
			{(record.supporting_signals?.length > 0 ||
				record.opposing_signals?.length > 0 ||
				record.key_assumptions?.length > 0 ||
				record.bear_case) && (
				<div className="thesis-lineage-evidence">
					{record.supporting_signals?.length > 0 && (
						<LineageList
							label="Supporting"
							values={record.supporting_signals}
						/>
					)}
					{record.opposing_signals?.length > 0 && (
						<LineageList label="Opposing" values={record.opposing_signals} />
					)}
					{record.key_assumptions?.length > 0 && (
						<LineageList label="Assumptions" values={record.key_assumptions} />
					)}
					{record.bear_case && (
						<div>
							<span className="text-muted">Bear case:</span> {record.bear_case}
						</div>
					)}
				</div>
			)}
			{record.failure_bucket && (
				<p className="thesis-lineage-note">
					Failure bucket: {humanize(record.failure_bucket)}
				</p>
			)}
			{record.parse_warning && (
				<p className="thesis-lineage-note">
					Rationale metadata parse warning: {humanize(record.parse_warning)}
				</p>
			)}
		</div>
	);
}

function LineageMetric({
	label,
	value,
	tone,
}: {
	label: string;
	value: string;
	tone?: "good" | "bad";
}) {
	return (
		<div className="thesis-lineage-metric">
			<span>{label}</span>
			<strong
				className={
					tone === "good"
						? "pnl-positive"
						: tone === "bad"
							? "pnl-negative"
							: undefined
				}
			>
				{value}
			</strong>
		</div>
	);
}

function LineageList({ label, values }: { label: string; values: unknown[] }) {
	return (
		<div>
			<span className="text-muted">{label}:</span>{" "}
			{values
				.map((v) => (typeof v === "string" ? v : JSON.stringify(v)))
				.join("; ")}
		</div>
	);
}

function verdictTone(
	verdict: string,
): "green" | "red" | "yellow" | "blue" | "muted" {
	if (verdict === "validated") return "green";
	if (verdict === "failed" || verdict === "poor_expression") return "red";
	if (verdict === "missed_alpha") return "yellow";
	if (verdict === "pending") return "blue";
	return "muted";
}
