import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { HealthNotificationsTab } from "./HealthNotificationsTab";

function renderHealthTab() {
	const client = new QueryClient();
	client.setQueryData(["telegram-health"], {
		reachable: false,
		error: "not_configured",
	});
	return renderToStaticMarkup(
		<QueryClientProvider client={client}>
			<HealthNotificationsTab
				settings={{ health: {}, telegram: {}, webhook: {} }}
				qc={client}
			/>
		</QueryClientProvider>,
	);
}

describe("HealthNotificationsTab", () => {
	it("preserves section order, anchors, and legacy card markup", () => {
		const html = renderHealthTab();
		const health = html.indexOf('id="settings-section-health-monitoring"');
		const telegram = html.indexOf('id="settings-section-telegram"');
		const webhook = html.indexOf('id="settings-section-webhook"');

		expect(health).toBeGreaterThan(-1);
		expect(telegram).toBeGreaterThan(health);
		expect(webhook).toBeGreaterThan(telegram);
		expect(html.match(/class="card mb-0"/g)?.length).toBe(3);
		expect(html).not.toContain("settings-config-title");
	});

	it("keeps health and webhook controls and write-only copy", () => {
		const html = renderHealthTab();

		expect(html).toContain("Max agent failure ratio");
		expect(html).toContain("Auto-resume after LLM gate");
		expect(html).toContain("Webhook URL");
		expect(html).toContain("HMAC secret (write-only)");
		expect(html).toContain("X-Aletheia-Signature");
		expect(html).toContain("Secret is write-only — leave blank to keep the current value.");
	});
});
