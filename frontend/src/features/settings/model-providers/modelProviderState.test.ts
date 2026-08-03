import { describe, expect, it } from "vitest";
import {
	apiTypes,
	blankApiProfile,
	integrationTypes,
	providerStatus,
	routingRoles,
} from "./modelProviderState";
import type {
	ModelConfigView,
	ProviderConfigView,
	ProviderTypeView,
} from "./types";

const clean: ModelConfigView = {
	default_provider: "",
	deep_provider: null,
	fast_provider: null,
	providers: {},
};

describe("model provider settings state", () => {
	it("keeps a clean installation unassigned and disabled", () => {
		expect(routingRoles(clean, "anything")).toEqual([]);
		expect(providerStatus(undefined, undefined)).toEqual({
			tone: "muted",
			label: "disabled",
		});
		expect(blankApiProfile().enabled).toBe(false);
	});

	it("shows disconnected enabled OAuth as authentication required", () => {
		const oauth: ProviderConfigView = {
			type: "openai_native",
			kind: "oauth",
			display_name: "OpenAI OAuth",
			enabled: true,
			model: "default",
			api_key: { configured: false, last4: null },
			oauth: { configured: false, last4: null },
		};
		expect(providerStatus(oauth, undefined)).toEqual({
			tone: "yellow",
			label: "authentication required",
		});
	});

	it("allows roles to be distributed across independent profiles", () => {
		const configured = {
			...clean,
			default_provider: "main",
			deep_provider: "deep",
			fast_provider: "fast",
		};
		expect(routingRoles(configured, "main")).toEqual(["default"]);
		expect(routingRoles(configured, "deep")).toEqual(["deep"]);
		expect(routingRoles(configured, "fast")).toEqual(["fast"]);
	});

	it("groups type metadata by access method instead of vendor", () => {
		const types: Record<string, ProviderTypeView> = {
			openai_compatible: {
				id: "openai_compatible",
				label: "API",
				category: "api",
				fields: [],
				singleton: false,
			},
			codex_cli: {
				id: "codex_cli",
				label: "Codex",
				category: "cli",
				fields: [],
				singleton: true,
			},
			openai_native: {
				id: "openai_native",
				label: "OpenAI",
				category: "oauth",
				fields: [],
				singleton: true,
			},
		};
		expect(apiTypes(types).map((type) => type.id)).toEqual([
			"openai_compatible",
		]);
		expect(integrationTypes(types, "cli").map((type) => type.id)).toEqual([
			"codex_cli",
		]);
		expect(integrationTypes(types, "oauth").map((type) => type.id)).toEqual([
			"openai_native",
		]);
	});
});
