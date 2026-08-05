import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { parseTelegramDraft, TelegramSection } from "./TelegramSection";
import { TELEGRAM_SECTION } from "./telegramState";

function renderTelegram(health: {
	reachable: boolean;
	username?: string;
	error?: string;
}) {
	const client = new QueryClient();
	client.setQueryData(["telegram-health"], health);
	return renderToStaticMarkup(
		<QueryClientProvider client={client}>
			<TelegramSection settings={{ telegram: {} }} qc={client} />
		</QueryClientProvider>,
	);
}

describe("TelegramSection", () => {
	it("preserves zero hours and complete signed chat IDs", () => {
		expect(
			parseTelegramDraft({
				chatIdsInput: "123, -456, +789",
				summaryHourInput: "0",
				dndStartInput: "0",
				dndEndInput: "0",
			}),
		).toEqual({
			ok: true,
			value: {
				allowed_chat_ids: [123, -456, 789],
				daily_summary_hour_cst: 0,
				dnd_start_hour: 0,
				dnd_end_hour: 0,
			},
		});
	});

	it.each([
		["123abc", "8", "23", "7", "Chat ID"],
		["123", "", "23", "7", "Summary hour"],
		["123", "8", "24", "7", "DND start hour"],
	])(
		"rejects malformed Telegram drafts without coercing them",
		(chatIdsInput, summaryHourInput, dndStartInput, dndEndInput, errorField) => {
			const result = parseTelegramDraft({
				chatIdsInput,
				summaryHourInput,
				dndStartInput,
				dndEndInput,
			});

			expect(result.ok).toBe(false);
			if (!result.ok) expect(result.error).toContain(errorField);
		},
	);

	it("keeps the registered deep-link descriptor", () => {
		expect(TELEGRAM_SECTION).toEqual({
			id: "telegram",
			title: "Telegram",
			tab: "health",
			description:
				"Alert delivery when agents act or limits are breached. Token is stored in secrets (write-only via CLI).",
			searchTerms: ["bot", "chat id", "notification"],
		});
	});

	it("preserves the legacy card markup while moving feature ownership", () => {
		const html = renderTelegram({ reachable: true, username: "aletheia_bot" });

		expect(html).toContain('id="settings-section-telegram"');
		expect(html).toContain("@aletheia_bot");
		expect(html).toContain('<div class="card mb-0">');
		expect(html).toContain('<div class="form-grid mb-3">');
		expect(html).toContain("Allowed chat IDs");
		expect(html).toContain('placeholder="123456789, 987654321"');
		expect(html).toContain('class="mono"');
		expect(html).toContain("Daily PnL summary (08:00 CST)");
		expect(html).toContain("Summary hour (CST)");
		expect(html).toContain("aletheia secrets set telegram.bot_token");
		expect(html).not.toContain("settings-config-title");
	});

	it("renders an unavailable health result honestly", () => {
		const html = renderTelegram({ reachable: false, error: "network_timeout" });

		expect(html).toContain("✗ network_timeout");
		expect(html).not.toContain("reachable</");
	});
});
