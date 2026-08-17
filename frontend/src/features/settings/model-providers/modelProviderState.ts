import type {
	ApiProfileDraft,
	ModelConfigView,
	ModelHealthView,
	ProviderConfigView,
	ProviderTypeView,
	RoutingRole,
} from "./types";

export const MODEL_PROVIDERS_SECTION = {
	id: "ai-model-providers",
	title: "AI Model Providers",
	tab: "agent" as const,
	description:
		"Configure API profiles, local agent CLIs, and official OAuth connections. Configuration never enables a provider automatically.",
	searchTerms: [
		"llm",
		"api profile",
		"openai",
		"anthropic",
		"claude",
		"codex",
		"pi",
		"oauth",
		"cli",
	],
};

export function routingRoles(
	config: ModelConfigView,
	providerId: string,
): RoutingRole[] {
	const roles: RoutingRole[] = [];
	if (config.default_provider === providerId) roles.push("default");
	if (config.deep_provider === providerId) roles.push("deep");
	if (config.fast_provider === providerId) roles.push("fast");
	return roles;
}

export function providerStatus(
	config: ProviderConfigView | undefined,
	health: ModelHealthView | undefined,
): { tone: "muted" | "green" | "yellow" | "red"; label: string } {
	if (
		health?.state === "disabled" ||
		config?.enabled === false ||
		(!config && !health)
	)
		return { tone: "muted", label: "disabled" };
	if (
		health?.configured === false ||
		(config?.configured === false && config.enabled)
	)
		return { tone: "muted", label: "not configured" };
	if (config?.kind === "oauth" && !config.oauth.configured) {
		return { tone: "yellow", label: "authentication required" };
	}
	if (!health) return { tone: "yellow", label: "checking" };
	if (health.freshness !== "fresh") {
		if (health.freshness === "stale")
			return { tone: "yellow", label: "stale" };
		if (health.reason_code === "not_installed" || health.error === "not_installed")
			return { tone: "yellow", label: "not installed" };
		if (
			health.reason_code === "authentication_failure" ||
			health.reason_code === "missing_configuration" ||
			health.error === "not_logged_in" ||
			health.error === "no_configured_models"
		) {
			return { tone: "yellow", label: "authentication required" };
		}
		return { tone: "yellow", label: "unknown" };
	}
	if (health.reachable && health.verified) {
		return { tone: "green", label: "verified" };
	}
	if (health.reachable) return { tone: "yellow", label: "reachable · unverified" };
	if (health.reason_code === "not_installed" || health.error === "not_installed")
		return { tone: "yellow", label: "not installed" };
	if (
		health.reason_code === "authentication_failure" ||
		health.reason_code === "missing_configuration" ||
		health.error === "not_logged_in" ||
		health.error === "no_configured_models"
	) {
		return { tone: "yellow", label: "authentication required" };
	}
	return { tone: "red", label: health.reason_code || health.error || "unavailable" };
}

export function apiTypes(
	types: Record<string, ProviderTypeView>,
): ProviderTypeView[] {
	return Object.values(types).filter((type) => type.category === "api");
}

export function integrationTypes(
	types: Record<string, ProviderTypeView>,
	category: "cli" | "oauth",
): ProviderTypeView[] {
	return Object.values(types).filter(
		(type) => type.category === category && type.singleton,
	);
}

export function blankApiProfile(
	providerType = "openai_compatible",
): ApiProfileDraft {
	let protocol = "chat_completions";
	if (providerType === "databricks") protocol = "serving_endpoint";
	if (providerType === "anthropic_compatible") protocol = "anthropic_messages";
	return {
		profile_id: "",
		display_name: "",
		type: providerType,
		base_url: "",
		workspace_url: "",
		protocol,
		auth_scheme: "x-api-key",
		model: "",
		api_key: "",
		enabled: false,
	};
}

export function draftFromProfile(
	providerId: string,
	config: ProviderConfigView,
): ApiProfileDraft {
	return {
		profile_id: providerId,
		display_name: config.display_name || providerId,
		type: config.type,
		base_url: config.base_url || "",
		workspace_url: config.workspace_url || "",
		protocol:
			config.protocol ||
			(config.type === "anthropic_compatible"
				? "anthropic_messages"
				: "chat_completions"),
		auth_scheme: config.auth_scheme || "x-api-key",
		model: config.model === "default" ? "" : config.model,
		api_key: "",
		enabled: config.enabled,
	};
}
