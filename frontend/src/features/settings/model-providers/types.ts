export type ProviderCategory = "api" | "cli" | "oauth";
export type RoutingRole = "default" | "deep" | "fast";

export interface KeyStatus {
	configured: boolean;
	last4: string | null;
}

export interface ProviderConfigView {
	type: string;
	kind: ProviderCategory;
	display_name: string;
	enabled: boolean;
	model: string;
	base_url?: string;
	workspace_url?: string | null;
	protocol?: string;
	auth_scheme?: string;
	codex_model?: string | null;
	api_key: KeyStatus;
	oauth: KeyStatus;
}

export interface ModelConfigView {
	default_provider: string;
	deep_provider: string | null;
	fast_provider: string | null;
	providers: Record<string, ProviderConfigView>;
}

export interface ProviderTypeView {
	id: string;
	label: string;
	category: ProviderCategory;
	fields: string[];
	singleton: boolean;
	protocols?: string[];
}

export interface ModelHealthView {
	provider: string;
	available: boolean;
	model: string;
	error: string | null;
	models: string[];
	model_metadata?: Array<{
		slug?: string;
		display_name?: string;
		tier?: string;
		context_window?: number;
	}>;
}

export interface ApiProfileDraft {
	profile_id: string;
	display_name: string;
	type: string;
	base_url: string;
	workspace_url: string;
	protocol: string;
	auth_scheme: string;
	model: string;
	api_key: string;
	enabled: boolean;
}
