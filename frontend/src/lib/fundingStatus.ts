export function fundingTerminalCopy(reason: string | null | undefined) {
	if (reason === "funding_permanently_unrecoverable") {
		return {
			short: "funding lost",
			detail: "funding lost — settler gave up (never fabricated)",
			title: "paper_funding_settler exhausted retries; realized_pnl stays NULL (never fabricated)",
		};
	}
	if (reason === "broker_funding_history_expired") {
		return {
			short: "funding history expired",
			detail: "funding history expired — venue ledger can no longer prove this holding window",
			title: "the matching venue can no longer prove the full private funding window; realized_pnl stays NULL",
		};
	}
	return null;
}
