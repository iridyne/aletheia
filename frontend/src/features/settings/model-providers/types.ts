import type { ModelProviderHealth } from "../../../api/client";

export type ProviderCategory = "api";
export type RoutingRole = "default" | "deep" | "fast";

export interface KeyStatus {
	configured: boolean | null;
	last4: string | null;
	unavailable_reason?: string;
}

export interface ProviderConfigView {
	type: string;
	kind: ProviderCategory | "unknown";
	configured: boolean;
	display_name: string;
	enabled: boolean;
	model: string;
	base_url?: string;
	protocol?: string;
	auth_scheme?: string;
	api_key: KeyStatus;
	oauth: KeyStatus;
}

export interface ModelConfigView {
	default_provider: string;
	deep: ModelSelection | null;
	fast: ModelSelection | null;
	providers: Record<string, ProviderConfigView>;
}

export interface ModelSelection {
	endpoint_id: string;
	model_id: string;
}

export interface ProviderTypeView {
	id: string;
	label: string;
	category: ProviderCategory;
	fields: string[];
	singleton: boolean;
	protocols?: string[];
}

export type ModelHealthView = ModelProviderHealth;

export interface ApiProfileDraft {
	profile_id: string;
	display_name: string;
	type: string;
	base_url: string;
	protocol: string;
	auth_scheme: string;
	model: string;
	api_key: string;
	enabled: boolean;
}
