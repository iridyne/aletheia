import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ModelProvidersSection } from "./ModelProvidersSection";

function renderCleanModelSettings() {
	const client = new QueryClient();
	client.setQueryData(["model"], {
		default_provider: "",
		deep_provider: null,
		fast_provider: null,
		providers: {},
	});
	client.setQueryData(["model-provider-types"], {
		openai_compatible: {
			id: "openai_compatible",
			label: "OpenAI Compatible API",
			category: "api",
			fields: [],
			singleton: false,
		},
		codex_cli: {
			id: "codex_cli",
			label: "Codex CLI",
			category: "cli",
			fields: [],
			singleton: true,
		},
		openai_native: {
			id: "openai_native",
			label: "OpenAI OAuth",
			category: "oauth",
			fields: [],
			singleton: true,
		},
	});
	client.setQueryData(["model-health"], []);
	return renderToStaticMarkup(
		<QueryClientProvider client={client}>
			<ModelProvidersSection qc={client} />
		</QueryClientProvider>,
	);
}

describe("ModelProvidersSection", () => {
	it("renders the clean state without forcing a provider or role", () => {
		const html = renderCleanModelSettings();

		expect(html).toContain("API");
		expect(html).toContain("CLI");
		expect(html).toContain("OAUTH");
		expect(html).toContain("No API profiles configured.");
		expect(html.match(/Not assigned/g)?.length).toBe(3);
		expect(html).toContain(
			"Configuration never enables a provider automatically.",
		);
	});
});
