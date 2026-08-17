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
	ModelHealthView,
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

	it("keeps stale and unknown observations out of green", () => {
		const config: ProviderConfigView = {
			type: "openai_compatible",
			kind: "api",
			display_name: "API",
			enabled: true,
			model: "model-a",
			api_key: { configured: true, last4: "1234" },
			oauth: { configured: false, last4: null },
		};
		const base: ModelHealthView = {
			provider: "openai_compatible",
			available: true,
			model: "model-a",
			error: null,
			models: ["model-a"],
			model_metadata: [],
			observed_at: "2026-08-17T12:00:00Z",
			age_seconds: 0,
			freshness: "fresh",
			cached: false,
			stale_after_seconds: 30,
		};

		expect(providerStatus(config, base)).toEqual({
			tone: "green",
			label: "available",
		});
		expect(providerStatus(config, { ...base, freshness: "stale" })).toEqual({
			tone: "yellow",
			label: "stale",
		});
		expect(providerStatus(config, { ...base, freshness: "unknown" })).toEqual({
			tone: "yellow",
			label: "unknown",
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
