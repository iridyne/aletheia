import { describe, expect, it } from "vitest";
import { fundingTerminalCopy } from "../lib/fundingStatus";

describe("fundingTerminalCopy", () => {
	it("distinguishes broker retention expiry from an in-flight retry", () => {
		expect(fundingTerminalCopy("broker_funding_history_expired")).toEqual({
			short: "funding history expired",
			detail: "funding history expired — venue ledger can no longer prove this holding window",
			title: "the matching venue can no longer prove the full private funding window; realized_pnl stays NULL",
		});
		expect(fundingTerminalCopy("funding_permanently_unrecoverable")?.short)
			.toBe("funding lost");
		expect(fundingTerminalCopy("incomplete_funding_economics")).toBeNull();
	});
});
