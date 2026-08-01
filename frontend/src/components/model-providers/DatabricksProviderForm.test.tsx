import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DatabricksProviderForm } from "./DatabricksProviderForm";

function renderForm(config: Record<string, unknown> = {}) {
	const client = new QueryClient();
	return renderToStaticMarkup(
		<QueryClientProvider client={client}>
			<DatabricksProviderForm
				config={config}
				isDefault={false}
				isDeep={false}
				isFast={false}
				hasDefault={false}
				qc={client}
			/>
		</QueryClientProvider>,
	);
}

describe("DatabricksProviderForm", () => {
	it("presents explicit serving and AI Gateway modes without overstating capabilities", () => {
		const html = renderForm();

		expect(html).toContain("Serving Endpoint");
		expect(html).toContain("AI Gateway Service");
		expect(html).toContain("Verify &amp; Apply");
		expect(html).toContain(
			"The first verified provider becomes the default automatically.",
		);
		expect(html).toContain("Native JSON schema");
		expect(html).toContain("Not available");
		expect(html).toContain("Single final response");
	});

	it("shows only a redacted configured-token hint", () => {
		const html = renderForm({
			workspace_url: "https://adb-123.azuredatabricks.net",
			protocol: "serving_endpoint",
			model: "research-claude",
			api_key: { configured: true, last4: "9F3A" },
			enabled: true,
		});

		expect(html).toContain("ends ··9F3A");
		expect(html).not.toContain("dapi");
		expect(html).toContain("Workspace detected:");
		expect(html).toContain("adb-123.azuredatabricks.net");
	});
});
