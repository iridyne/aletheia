// @vitest-environment happy-dom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { api } from "../../../api/client";
import {
	ApiProfileEditor,
	ModelProvidersSection,
} from "./ModelProvidersSection";
import { blankApiProfile, draftFromProfile } from "./modelProviderState";
import type { ProviderConfigView, ProviderTypeView } from "./types";
import SOURCE from "./ModelProvidersSection.tsx?raw";

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
	configured: true,
	display_name: "Primary API",
	enabled: false,
	model: "model-a",
	base_url: "https://provider.example/v1",
	protocol: "chat_completions",
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
		deep: null,
		fast: null,
		providers,
	});
	client.setQueryData(["model-provider-types"], {
		openai_compatible: API_TYPE,
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

function renderModelSettingsWithProvider(protocol = EXISTING_PROFILE.protocol) {
	const client = cleanModelClient({ primary_api: { ...EXISTING_PROFILE, protocol } });
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
	it("preserves a tier draft through refresh and submits its exact endpoint/model pair", async () => {
		const client = cleanModelClient({ shared: EXISTING_PROFILE });
		vi.spyOn(api, "getModelConfig").mockImplementation(async () => client.getQueryData(["model"])!);
		vi.spyOn(api, "getModelHealth").mockResolvedValue([]);
		const update = vi.spyOn(api, "updateModelRouting").mockResolvedValue({});
		const onDirty = vi.fn();
		const container = document.createElement("div");
		document.body.append(container);
		const root = createRoot(container);
		(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
		try {
			await act(async () => root.render(
				<QueryClientProvider client={client}><ModelProvidersSection qc={client} onDirty={onDirty} /></QueryClientProvider>,
			));
			const fieldset = [...container.querySelectorAll("fieldset")].find(item => item.querySelector("legend")?.textContent === "Fast")!;
			await act(async () => {
				const select = fieldset.querySelector("select")!;
				select.value = "shared";
				select.dispatchEvent(new Event("change", { bubbles: true }));
			});
			await act(async () => changeInput(fieldset.querySelector("input")!, "small-manual"));
			expect(onDirty).toHaveBeenCalledWith("model-routing-fast", true);
			await act(async () => {
				client.setQueryData(["model"], (current: Record<string, unknown>) => ({ ...current, default_provider: "shared" }));
				await new Promise(resolve => setTimeout(resolve, 0));
			});
			expect(fieldset.querySelector("input")!.value).toBe("small-manual");
			await act(async () => {
				[...fieldset.querySelectorAll("button")].find(item => item.textContent === "Verify & assign Fast")!.click();
			});
			expect(update).toHaveBeenCalledExactlyOnceWith({ fast: { endpoint_id: "shared", model_id: "small-manual" } });
		} finally {
			await act(async () => root.unmount());
			container.remove();
			client.clear();
			vi.restoreAllMocks();
		}
	});

	it("sends only discovery fields and editable fields to strict HTTP APIs", async () => {
		const detect = vi.spyOn(api, "detectModels").mockResolvedValue({ models: [] });
		vi.spyOn(api, "testModelCandidate").mockResolvedValue({ available: true } as Awaited<ReturnType<typeof api.testModelCandidate>>);
		const update = vi.spyOn(api, "updateModelProfile").mockResolvedValue({});
		const client = new QueryClient();
		const container = document.createElement("div");
		document.body.append(container);
		const root = createRoot(container);
		(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
		try {
			await act(async () => root.render(
				<QueryClientProvider client={client}>
					<ApiProfileEditor initial={draftFromProfile("primary-api", EXISTING_PROFILE)} existing={EXISTING_PROFILE} providerTypes={[API_TYPE]} qc={client} onClose={() => undefined} />
				</QueryClientProvider>,
			));
			for (const label of ["Detect models", "Verify & Save"]) {
				await act(async () => {
					const button = [...container.querySelectorAll("button")].find(item => item.textContent === label);
					expect(button).toBeDefined();
					button!.click();
				});
			}
			expect(detect).toHaveBeenCalledOnce();
			expect(detect.mock.calls[0][0]).not.toHaveProperty("model");
			expect(detect.mock.calls[0][0]).not.toHaveProperty("workspace_url");
			expect(update).toHaveBeenCalledOnce();
			expect(update.mock.calls[0][0]).toBe("primary-api");
			expect(update.mock.calls[0][1]).not.toHaveProperty("type");
			expect(update.mock.calls[0][1]).not.toHaveProperty("workspace_url");
		} finally {
			await act(async () => root.unmount());
			container.remove();
			client.clear();
			vi.restoreAllMocks();
		}
	});

	it.each(["codex_cli", "openai_native", "anthropic_native", "databricks"])("keeps assigned retired %s visible without an activation control", (providerType) => {
		const client = cleanModelClient({
			"historical-cli": { ...EXISTING_PROFILE, type: providerType, kind: "unknown", enabled: true, configured: false },
		});
		client.setQueryData(["model"], (current: Record<string, unknown>) => ({
			...current, default_provider: "historical-cli",
		}));
		const container = document.createElement("div");
		container.innerHTML = renderToStaticMarkup(
			<QueryClientProvider client={client}>
				<ModelProvidersSection qc={client} />
			</QueryClientProvider>,
		);
		const option = container.querySelector<HTMLOptionElement>('option[value="historical-cli"]');
		expect(option?.disabled).toBe(true);
		expect(option?.selected).toBe(true);
		expect(option?.textContent).toContain("unsupported");
		expect(container.querySelector('[aria-label="Enable Primary API"]')).toBeNull();
	});

	it("clears an old API profile error when a retry starts", () => {
		expect(SOURCE.match(/onMutate: \(\) => setError\(null\)/g)).toHaveLength(2);
	});

	it("renders the clean state without forcing a provider or role", () => {
		const html = renderCleanModelSettings();

		expect(html).toContain("API");
		expect(html).not.toContain("CLI");
		expect(html).not.toContain("OAUTH");
		expect(html).toContain("No API profiles configured.");
		expect(html.match(/Not assigned/g)?.length).toBe(3);
		expect(html).toContain(
			"Configuration never enables a provider automatically.",
		);
	});

	it.each(["chat_completions", "responses", "anthropic_messages", ""])("shows protocol %s and names the provider switch", (protocol) => {
		const html = renderModelSettingsWithProvider(protocol);
		expect(html).toContain('aria-label="Enable Primary API"');
		expect(html).toContain(protocol || "protocol not configured");

		const client = cleanModelClient();
		const editor = renderToStaticMarkup(
			<QueryClientProvider client={client}>
				<ApiProfileEditor
					initial={draftFromProfile("primary_api", EXISTING_PROFILE)}
					existing={EXISTING_PROFILE}
					providerTypes={[API_TYPE]}
					qc={client}
					onClose={() => undefined}
				/>
			</QueryClientProvider>,
		);

		expect(editor).toContain("aria-labelledby=");
		expect(editor).toContain("for=");
		expect(editor).not.toContain('<label class="model-editor-enabled">');
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
