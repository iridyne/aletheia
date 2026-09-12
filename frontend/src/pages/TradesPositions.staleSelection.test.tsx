// @vitest-environment happy-dom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Order, Position } from "../types";

vi.mock("../components/AccountSnapshot", () => ({
	default: () => <div data-testid="account-snapshot" />,
}));
vi.mock("../charts/TradeAnalyticsPanel", () => ({
	default: () => <div />,
}));
vi.mock("../charts/TradeInsightsPanel", () => ({
	default: () => <div />,
}));

import TradesPositions from "./TradesPositions";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const createdAt = "2026-08-13T00:00:00Z";

const openPosition: Position = {
	id: "position-eth-long",
	symbol: "ETH/USDT",
	market_type: "perp",
	direction: "long",
	quantity: 1,
	entry_price: 100,
	current_price: 105,
	unrealized_pnl: 5,
	realized_pnl: null,
	execution_mode: "bitget_demo",
	strategy_id: "strategy-a",
	proposal_id: "proposal-a",
	order_id: "order-a",
	status: "open",
	stop_price: 90,
	take_profit_price: 120,
	close_reason: null,
	opened_at: createdAt,
	closed_at: null,
};

const closedPosition: Position = {
	...openPosition,
	market_type: "usdt_futures",
	current_price: 110,
	unrealized_pnl: null,
	realized_pnl: 10,
	entry_fee: 1,
	exit_fee: 1,
	funding_cost: 10,
	funding_source: "broker_ledger",
	funding_status: "complete",
	slippage_cost: 0.5,
	status: "closed",
	close_reason: "take_profit",
	closed_at: "2026-08-13T01:00:00Z",
};

const submittedOrder: Order = {
	id: "order-a",
	proposal_id: "proposal-a",
	risk_decision_id: "risk-a",
	symbol: "ETH/USDT",
	market_type: "perp",
	direction: "long",
	order_type: "market",
	quantity: 1,
	price: 100,
	execution_mode: "bitget_demo",
	exchange_order_id: null,
	status: "submitted",
	created_at: createdAt,
	filled_at: null,
	error_reason: null,
};

const filledOrder: Order = {
	...submittedOrder,
	price: 101,
	exchange_order_id: "exchange-a",
	status: "filled",
	filled_at: "2026-08-13T00:01:00Z",
};

function makeClient() {
	const client = new QueryClient({
		defaultOptions: { queries: { staleTime: Infinity, retry: false } },
	});
	client.setQueryData(["positions"], [openPosition]);
	client.setQueryData(["trades"], [submittedOrder]);
	client.setQueryData(["positions-reconciliation"], {
		sync_status: "ok",
		external: [],
		orphaned: [],
	});
	client.setQueryData(["ui-overview-bootstrap"], {
		account: { execution_mode: "bitget_demo" },
	});
	client.setQueryData(["proposals"], []);
	return client;
}

let root: Root | undefined;

beforeEach(() => {
	vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 503 })));
});

afterEach(() => {
	act(() => root?.unmount());
	root = undefined;
	document.body.innerHTML = "";
	vi.unstubAllGlobals();
});

async function renderPage() {
	const node = document.createElement("div");
	document.body.appendChild(node);
	const client = makeClient();
	root = createRoot(node);
	await act(async () => {
		root!.render(
			<MemoryRouter initialEntries={["/trades?tab=blotter"]}>
				<QueryClientProvider client={client}>
					<TradesPositions />
				</QueryClientProvider>
			</MemoryRouter>,
		);
	});
	return { node, client };
}

function rowContaining(node: ParentNode, text: string): HTMLTableRowElement {
	const row = [...node.querySelectorAll("tbody tr")].find((item) =>
		item.textContent?.includes(text),
	);
	expect(row, `row containing ${text}`).toBeTruthy();
	return row as HTMLTableRowElement;
}

function drawerText(node: ParentNode): string {
	return node.querySelector(".drawer-panel")?.textContent || "";
}

async function updateQuery(client: QueryClient, key: readonly unknown[], data: unknown) {
	await act(async () => {
		client.setQueryData(key, data);
		await new Promise((resolve) => setTimeout(resolve, 0));
	});
}

describe("TradesPositions drawer selection freshness", () => {
	it("resolves an open position drawer from the latest query row", async () => {
		const { node, client } = await renderPage();

		await act(async () => rowContaining(node, "ETHUSDT").click());
		expect(drawerText(node)).toContain("Open");
		expect(drawerText(node)).toContain("105");
		expect(drawerText(node)).toContain("Close at market price");

		await updateQuery(client, ["positions"], [closedPosition]);
		const updated = drawerText(node);
		expect(updated).toContain("Closed");
		expect(updated).toContain("110");
		expect(updated).toContain("Funding");
		expect(updated).toContain("-10.00");
		expect(updated).toContain("Broker Ledger");
		expect(updated).toContain("Slippage (diagnostic)");
		expect(updated).not.toContain("Close at market price");

		await updateQuery(client, ["positions"], [{
			...closedPosition,
			funding_cost: -3,
		}]);
		expect(drawerText(node)).toContain("+3.00");
	});

	it("keeps unavailable funding explicit instead of displaying a zero", async () => {
		const { node, client } = await renderPage();

		await act(async () => rowContaining(node, "ETHUSDT").click());
		await updateQuery(client, ["positions"], [{
			...closedPosition,
			funding_cost: null,
			funding_source: "unavailable",
			funding_status: "unavailable",
			realized_pnl: null,
			realized_pnl_complete: false,
			realized_pnl_unavailable_reason: "incomplete_funding_economics",
			known_gross_pnl: 10,
			known_net_before_unresolved_costs: 8,
			known_economics_missing_components: ["funding_cost"],
		}]);

		const updated = drawerText(node);
		expect(updated).toContain("Funding status");
		expect(updated).toContain("Unavailable");
		expect(updated).not.toContain("-0.00");
		expect(updated).not.toContain("+0.00");
		expect(updated).toContain("Incomplete Funding Economics");
		expect(updated).toContain("Known gross PnL");
		expect(updated).toContain("Known net before unresolved costs (not final)");
		expect(updated).toContain("Unresolved economic componentsFunding Cost");
		expect(node.textContent).toContain("Known before unresolved costs: +8.00");
	});

	it("resolves order status changes and missing rows from current query data", async () => {
		const { node, client } = await renderPage();

		await act(async () => rowContaining(node, "Submitted").click());
		expect(drawerText(node)).toContain("Submitted");

		await updateQuery(client, ["trades"], [filledOrder]);
		expect(drawerText(node)).toContain("Filled");
		expect(drawerText(node)).toContain("101");

		await updateQuery(client, ["trades"], []);
		const missing = drawerText(node);
		expect(missing).toContain("Current Record");
		expect(missing).toContain("Selected order is no longer in the current query result.");
		expect(missing).not.toContain("Filled");
	});
});
