// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
	ApiProfileEditor,
	ModelProvidersSection,
} from "./ModelProvidersSection";
import { blankApiProfile, draftFromProfile } from "./modelProviderState";
import type { ProviderConfigView, ProviderTypeView } from "./types";

const API_TYPE: ProviderTypeView = {
	id: "openai_compatible",
	label: "OpenAI Compatible API",
	category: "api",
	fields: [],
	singleton: false,
};

const EXISTING_PROFILE: ProviderConfigView = {
	type: "openai_compatible",
	kind: "api",
	display_name: "Primary API",
	enabled: false,
	model: "model-a",
	base_url: "https://provider.example/v1",
	protocol: "openai",
	auth_scheme: "bearer",
	api_key: { configured: true, last4: "1234" },
	oauth: { configured: false, last4: null },
};

function cleanModelClient(providers: Record<string, unknown> = {}) {
	const client = new QueryClient({
		defaultOptions: { queries: { retry: false, staleTime: Number.POSITIVE_INFINITY } },
	});
	client.setQueryData(["model"], {
		default_provider: "",
		deep_provider: null,
		fast_provider: null,
		providers,
	});
	client.setQueryData(["model-provider-types"], {
		openai_compatible: API_TYPE,
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
	return client;
}

function renderCleanModelSettings() {
	const client = cleanModelClient();
	return renderToStaticMarkup(
		<QueryClientProvider client={client}>
			<ModelProvidersSection qc={client} />
		</QueryClientProvider>,
	);
}

function changeInput(input: HTMLInputElement, value: string) {
	const setter = Object.getOwnPropertyDescriptor(
		HTMLInputElement.prototype,
		"value",
	)?.set;
	setter?.call(input, value);
	input.dispatchEvent(new Event("input", { bubbles: true }));
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

	it.each([
		{
			name: "a new profile ID",
			initial: blankApiProfile(API_TYPE.id),
			existing: undefined,
			selector: 'input[placeholder="primary-api"]',
			value: "typed-profile",
		},
		{
			name: "an existing profile API key",
			initial: draftFromProfile("primary_api", EXISTING_PROFILE),
			existing: EXISTING_PROFILE,
			selector: 'input[type="password"]',
			value: "unsaved-key-draft",
		},
	])("preserves $name across a parent refresh", async (testCase) => {
		const client = cleanModelClient();
		const container = document.createElement("div");
		document.body.append(container);
		const root = createRoot(container);
		(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean })
			.IS_REACT_ACT_ENVIRONMENT = true;
		const renderEditor = (initial = testCase.initial) => (
			<QueryClientProvider client={client}>
				<ApiProfileEditor
					initial={initial}
					existing={testCase.existing}
					providerTypes={[API_TYPE]}
					qc={client}
					onClose={() => undefined}
				/>
			</QueryClientProvider>
		);

		await act(async () => {
			root.render(renderEditor());
		});

		const input = container.querySelector<HTMLInputElement>(testCase.selector);
		expect(input).not.toBeNull();
		await act(async () => {
			changeInput(input as HTMLInputElement, testCase.value);
			root.render(renderEditor({ ...testCase.initial }));
		});

		expect(input?.value).toBe(testCase.value);
		await act(async () => root.unmount());
		container.remove();
	});
});
