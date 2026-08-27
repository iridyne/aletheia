// @vitest-environment happy-dom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Order, Position, TradeProposal } from "../types";

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

function makeOrder(overrides: Partial<Order>): Order {
	return {
		id: "order",
		proposal_id: "proposal-entry",
		risk_decision_id: "risk-entry",
		symbol: "BTC/USDT",
		market_type: "perp",
		direction: "long",
		order_type: "market",
		quantity: 1,
		price: 100,
		execution_mode: "paper",
		exchange_order_id: null,
		status: "filled",
		created_at: createdAt,
		filled_at: createdAt,
		error_reason: null,
		position_id: null,
		close_reason: null,
		source: null,
		...overrides,
	};
}

const orders: Order[] = [
	makeOrder({ id: "order-entry", price: 100 }),
	makeOrder({
		id: "order-close",
		price: 101,
		position_id: "position-btc-long",
		close_reason: "take_profit",
		source: "position_manager",
	}),
	makeOrder({
		id: "order-partial-close-identity",
		price: 102,
		close_reason: "manual",
	}),
];

const positions: Position[] = [{
	id: "position-btc-long",
	symbol: "BTC/USDT",
	market_type: "perp",
	direction: "long",
	quantity: 1,
	entry_price: 100,
	current_price: 101,
	unrealized_pnl: null,
	realized_pnl: 1,
	execution_mode: "paper",
	strategy_id: "strategy-a",
	proposal_id: "proposal-entry",
	order_id: "order-entry",
	status: "closed",
	stop_price: 90,
	take_profit_price: 110,
	close_reason: "take_profit",
	opened_at: createdAt,
	closed_at: createdAt,
}];

const proposals: TradeProposal[] = [{
	id: "proposal-entry",
	strategy_id: "strategy-a",
	opportunity_id: "opp-a",
	symbol: "BTC/USDT",
	market_type: "perp",
	direction: "long",
	order_type: "market",
	quantity: 1,
	price: 100,
	leverage: null,
	stop_price: 90,
	take_profit: 110,
	risk_reward: null,
	execution_mode: "paper",
	agent_rationale: null,
	status: "executed",
	created_at: createdAt,
}];

function makeClient() {
	const client = new QueryClient({
		defaultOptions: { queries: { staleTime: Infinity, retry: false } },
	});
	client.setQueryData(["positions"], positions);
	client.setQueryData(["trades"], orders);
	client.setQueryData(["positions-reconciliation"], {
		sync_status: "ok",
		external: [],
		orphaned: [],
	});
	client.setQueryData(["ui-overview-bootstrap"], {
		account: { execution_mode: "paper" },
	});
	client.setQueryData(["proposals"], proposals);
	client.setQueryData(["risk-decision", "proposal-entry"], {
		proposal_id: "proposal-entry",
		decision: {
			approved: true,
			risk_score: 0.2,
			message: "entry risk accepted",
		},
	});
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
	root = createRoot(node);
	await act(async () => {
		root!.render(
			<MemoryRouter initialEntries={["/trades?tab=blotter"]}>
				<QueryClientProvider client={makeClient()}>
					<TradesPositions />
				</QueryClientProvider>
			</MemoryRouter>,
		);
	});
	return node;
}

function rowContaining(node: ParentNode, text: string): HTMLTableRowElement {
	const row = [...node.querySelectorAll("tbody tr")].find((item) =>
		item.textContent?.includes(text),
	);
	expect(row, `row containing ${text}`).toBeTruthy();
	return row as HTMLTableRowElement;
}

describe("TradesPositions order intent display", () => {
	it("distinguishes close orders from entry orders without guessing partial identity", async () => {
		const node = await renderPage();
		const text = node.textContent || "";

		expect(text).toContain("Entry");
		expect(text).toContain("Close");
		expect(text).toContain("Intent unavailable");

		await act(async () => rowContaining(node, "Close").click());
		const closeDrawerText = node.querySelector(".drawer-panel")?.textContent || "";
		expect(closeDrawerText).toContain("Order Detail");
		expect(closeDrawerText).toContain("Linked Position");
		expect(closeDrawerText).toContain("position-btc-long");
		expect(closeDrawerText).toContain("Close Reason");
		expect(closeDrawerText).toContain("Take Profit");
		expect(closeDrawerText).toContain("Provenance");
		expect(closeDrawerText).toContain("Position Manager");
		expect(closeDrawerText).toContain("entry approval (not close authorization)");

		await act(async () => rowContaining(node, "Intent unavailable").click());
		const partialDrawerText = node.querySelector(".drawer-panel")?.textContent || "";
		expect(partialDrawerText).toContain("Intent unavailable");
		expect(partialDrawerText).toContain("Linked Position");
		expect(partialDrawerText).toContain("unavailable");
		expect(partialDrawerText).toContain("Close Reason");
		expect(partialDrawerText).toContain("Manual");
	});
});
