import { describe, expect, it } from "vitest";
import {
	apiTypes,
	blankApiProfile,
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
	deep: null,
	fast: null,
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

	it("shows retired enabled native profiles as not configured", () => {
		const oauth: ProviderConfigView = {
			type: "openai_native",
			kind: "unknown",
			configured: false,
			display_name: "Historical profile",
			enabled: true,
			model: "default",
			api_key: { configured: false, last4: null },
			oauth: { configured: false, last4: null },
		};
		expect(providerStatus(oauth, undefined)).toEqual({
			tone: "muted",
			label: "not configured",
		});
	});

	it("keeps stale and unknown observations out of green", () => {
		const config: ProviderConfigView = {
			type: "openai_compatible",
			kind: "api",
			configured: true,
			display_name: "API",
			enabled: true,
			model: "model-a",
			api_key: { configured: true, last4: "1234" },
			oauth: { configured: false, last4: null },
		};
		const base: ModelHealthView = {
			provider: "openai_compatible",
			configured: true,
			enabled: true,
			reachable: true,
			verified: true,
			verified_at: "2026-08-17T12:00:00Z",
			verification_identity: "identity",
			verification_source: "model_ping",
			verification_invalidated: false,
			verification_detail: null,
			state: "verified",
			reason_code: "verified",
			routing_roles: [],
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
			label: "verified",
		});
		expect(providerStatus(config, { ...base, verified: null })).toEqual({
			tone: "yellow",
			label: "reachable · unverified",
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
			deep: { endpoint_id: "deep", model_id: "large" },
			fast: { endpoint_id: "fast", model_id: "small" },
		};
		expect(routingRoles(configured, "main")).toEqual(["default"]);
		expect(routingRoles(configured, "deep")).toEqual(["deep"]);
		expect(routingRoles(configured, "fast")).toEqual(["fast"]);
	});

	it("returns HTTP API profile metadata", () => {
		const types: Record<string, ProviderTypeView> = {
			openai_compatible: {
				id: "openai_compatible",
				label: "API",
				category: "api",
				fields: [],
				singleton: false,
			},
		};
		expect(apiTypes(types).map((type) => type.id)).toEqual([
			"openai_compatible",
		]);
	});
});
