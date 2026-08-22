import { useEffect, useId, useMemo, useState } from "react";
import { useMutation, useQuery, type QueryClient } from "@tanstack/react-query";
import { api } from "../../../api/client";
import { Switch, useConfirm } from "../../../components/ui";
import { SettingsFeatureSection } from "../SettingsFeatureSection";
import { useSettingsDraftLifecycle } from "../draftLifecycle";
import {
	MODEL_PROVIDERS_SECTION,
	apiTypes,
	blankApiProfile,
	draftFromProfile,
	integrationTypes,
	providerStatus,
	routingRoles,
} from "./modelProviderState";
import type {
	ApiProfileDraft,
	ModelConfigView,
	ModelHealthView,
	ProviderConfigView,
	ProviderTypeView,
	RoutingRole,
} from "./types";
import "./model-providers.css";

const ROLE_LABELS: Record<RoutingRole, string> = {
	default: "Default",
	deep: "Deep",
	fast: "Fast",
};

function mutationMessage(error: unknown): string {
	return error instanceof Error ? error.message : "Request failed";
}

function createModelInvalidator(qc: QueryClient) {
	return () => {
		qc.invalidateQueries({ queryKey: ["model"] });
		qc.invalidateQueries({ queryKey: ["model-health"] });
		qc.invalidateQueries({ queryKey: ["status"] });
		qc.invalidateQueries({ queryKey: ["settings"] });
	};
}

function RoleButtons({
	providerId,
	model,
	qc,
}: {
	providerId: string;
	model: ModelConfigView;
	qc: QueryClient;
}) {
	const invalidate = createModelInvalidator(qc);
	const mutation = useMutation({
		mutationFn: (role: RoutingRole) =>
			api.updateModelRouting({
				[`${role}_provider`]: providerId,
			}),
		onSuccess: invalidate,
	});
	const roles = routingRoles(model, providerId);
	return (
		<div className="model-role-actions" aria-label="Routing roles">
			{(Object.keys(ROLE_LABELS) as RoutingRole[]).map((role) => (
				<button
					key={role}
					type="button"
					className={roles.includes(role) ? "active" : "secondary"}
					onClick={() => mutation.mutate(role)}
					disabled={mutation.isPending}
				>
					{ROLE_LABELS[role]}
				</button>
			))}
		</div>
	);
}

function RoutingBoard({
	model,
	qc,
}: {
	model: ModelConfigView;
	qc: QueryClient;
}) {
	const invalidate = createModelInvalidator(qc);
	const mutation = useMutation({
		mutationFn: ({ role, value }: { role: RoutingRole; value: string }) =>
			api.updateModelRouting({
				[`${role}_provider`]: value || null,
			}),
		onSuccess: invalidate,
	});
	const providers = Object.entries(model.providers).sort((left, right) =>
		(left[1].display_name || left[0]).localeCompare(
			right[1].display_name || right[0],
		),
	);
	return (
		<div className="model-routing-board">
			<div>
				<strong>Routing roles</strong>
				<p>
					Roles and activation are independent. Assigning a role never turns a
					provider on.
				</p>
			</div>
			<div className="model-routing-grid">
				{(Object.keys(ROLE_LABELS) as RoutingRole[]).map((role) => {
					const value =
						role === "default"
							? model.default_provider
							: role === "deep"
								? model.deep_provider || ""
								: model.fast_provider || "";
					return (
						<label key={role}>
							<span>{ROLE_LABELS[role]}</span>
							<select
								value={value}
								onChange={(event) =>
									mutation.mutate({ role, value: event.target.value })
								}
								disabled={mutation.isPending}
							>
								<option value="">Not assigned</option>
								{providers.map(([providerId, provider]) => (
									<option key={providerId} value={providerId}>
										{provider.display_name || providerId}
										{provider.enabled ? "" : " (disabled)"}
									</option>
								))}
							</select>
						</label>
					);
				})}
			</div>
		</div>
	);
}

function Status({
	config,
	health,
}: {
	config?: ProviderConfigView;
	health?: ModelHealthView;
}) {
	const status = providerStatus(config, health);
	const freshness = health?.freshness;
	const label = freshness && freshness !== status.label
		? `${status.label} · ${freshness}`
		: status.label;
	const observation = health?.observed_at
		? `${health.observed_at} (age ${health.age_seconds ?? "?"}s at response)`
		: "no observation";
	const source = health?.observed_at
		? health.cached
			? "cached"
			: "probed"
		: "unobserved";
	const evidence = health
		? `${health.state}; ${health.reason_code}; ${freshness ?? "unknown"}; ${source}; observed ${observation}`
		: "no health observation";
	return (
		<span className={`model-provider-status ${status.tone}`} title={evidence}>
			<span aria-hidden="true" />
			{label}
		</span>
	);
}

export function ApiProfileEditor({
	initial,
	existing,
	providerTypes,
	qc,
	onClose,
	onDirty,
}: {
	initial: ApiProfileDraft;
	existing?: ProviderConfigView;
	providerTypes: ProviderTypeView[];
	qc: QueryClient;
	onClose: () => void;
	onDirty?: (id: string, isDirty: boolean) => void;
}) {
	const [draft, setDraft] = useState(initial);
	const [message, setMessage] = useState<string | null>(null);
	const enabledToggleId = useId();
	const enabledToggleLabelId = `${enabledToggleId}-label`;
	const [models, setModels] = useState<
		Array<{ slug: string; display_name: string }>
	>([]);
	const invalidate = createModelInvalidator(qc);
	const set = <K extends keyof ApiProfileDraft>(
		key: K,
		value: ApiProfileDraft[K],
	) => setDraft((current) => ({ ...current, [key]: value }));
	const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
	useSettingsDraftLifecycle({
		id: "model-profile-editor",
		dirty,
		onDirty,
		serverKey: JSON.stringify(initial),
		adoptServer: () => setDraft(initial),
	});

	const candidate = () => ({
		provider: draft.profile_id,
		type: draft.type,
		...(draft.type === "databricks"
			? { workspace_url: draft.workspace_url }
			: { base_url: draft.base_url }),
		protocol: draft.protocol,
		model: draft.model || undefined,
		api_key: draft.api_key || undefined,
		auth_scheme: draft.auth_scheme,
	});

	const detect = useMutation({
		mutationFn: () => api.detectModels(candidate()),
		onSuccess: (result) => {
			setModels(result.models || []);
			setMessage(
				result.models?.length
					? `Detected ${result.models.length} models.`
					: "No model catalogue was returned.",
			);
		},
		onError: (error) => setMessage(mutationMessage(error)),
	});

	const save = useMutation({
		mutationFn: async () => {
			if (!draft.profile_id || !draft.display_name)
				throw new Error("Profile ID and display name are required.");
			const test = await api.testModelCandidate({
				...candidate(),
				probe: true,
			});
			if (!test.available)
				throw new Error(test.error || "Provider verification failed.");
			const body = {
				display_name: draft.display_name,
				type: draft.type,
				enabled: draft.enabled,
				...(draft.type === "databricks"
					? { workspace_url: draft.workspace_url }
					: { base_url: draft.base_url }),
				protocol: draft.protocol,
				model: draft.model || "default",
				auth_scheme: draft.auth_scheme,
				...(draft.api_key ? { api_key: draft.api_key } : {}),
			};
			return existing
				? api.updateModelProfile(draft.profile_id, body)
				: api.createModelProfile({ profile_id: draft.profile_id, ...body });
		},
		onSuccess: () => {
			invalidate();
			onClose();
		},
		onError: (error) => setMessage(mutationMessage(error)),
	});

	const selectedType = providerTypes.find((type) => type.id === draft.type);
	const protocols = selectedType?.protocols || [];
	return (
		<div className="model-profile-editor">
			<div className="model-editor-grid">
				<label>
					<span>Profile ID</span>
					<input
						value={draft.profile_id}
						disabled={!!existing}
						placeholder="primary-api"
						onChange={(event) =>
							set(
								"profile_id",
								event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-"),
							)
						}
					/>
				</label>
				<label>
					<span>Display name</span>
					<input
						value={draft.display_name}
						onChange={(event) => set("display_name", event.target.value)}
					/>
				</label>
				<label>
					<span>API type</span>
					<select
						value={draft.type}
						disabled={!!existing}
						onChange={(event) => {
							const next = blankApiProfile(event.target.value);
							setDraft({
								...next,
								profile_id: draft.profile_id,
								display_name: draft.display_name,
								enabled: draft.enabled,
							});
						}}
					>
						{providerTypes.map((type) => (
							<option key={type.id} value={type.id}>
								{type.label}
							</option>
						))}
					</select>
				</label>
				{draft.type === "databricks" ? (
					<label className="wide">
						<span>Workspace URL</span>
						<input
							value={draft.workspace_url}
							onChange={(event) => set("workspace_url", event.target.value)}
						/>
					</label>
				) : (
					<label className="wide">
						<span>Base URL</span>
						<input
							value={draft.base_url}
							placeholder="https://provider.example/v1"
							onChange={(event) => set("base_url", event.target.value)}
						/>
					</label>
				)}
				<label>
					<span>Protocol</span>
					<select
						value={draft.protocol}
						onChange={(event) => set("protocol", event.target.value)}
					>
						{protocols.map((protocol) => (
							<option key={protocol} value={protocol}>
								{protocol}
							</option>
						))}
					</select>
				</label>
				{draft.type === "anthropic_compatible" && (
					<label>
						<span>Authentication header</span>
						<select
							value={draft.auth_scheme}
							onChange={(event) => set("auth_scheme", event.target.value)}
						>
							<option value="x-api-key">X-API-Key</option>
							<option value="bearer">Bearer</option>
						</select>
					</label>
				)}
				<label>
					<span>Model</span>
					<input
						list={`models-${draft.profile_id || "new"}`}
						value={draft.model}
						onChange={(event) => set("model", event.target.value)}
					/>
					<datalist id={`models-${draft.profile_id || "new"}`}>
						{models.map((model) => (
							<option key={model.slug} value={model.slug}>
								{model.display_name}
							</option>
						))}
					</datalist>
				</label>
				<label className="wide">
					<span>API key</span>
					<input
						type="password"
						value={draft.api_key}
						placeholder={
							existing?.api_key.configured
								? `Configured · ends ${existing.api_key.last4}`
								: "Paste key"
						}
						onChange={(event) => set("api_key", event.target.value)}
					/>
				</label>
				<div className="model-editor-enabled">
					{/* Switch renders its own <label> wrapper, so the visible text must be
					    a sibling <label htmlFor> rather than nested inside another
					    <label> — nested labels are invalid HTML. */}
					<Switch
						id={enabledToggleId}
						aria-labelledby={enabledToggleLabelId}
						checked={draft.enabled}
						persistence="staged"
						onCheckedChange={(value) => set("enabled", value)}
					/>
					<label id={enabledToggleLabelId} htmlFor={enabledToggleId}>Enable after verification</label>
				</div>
			</div>
			<div className="model-editor-actions">
				<button
					type="button"
					className="secondary"
					onClick={() => detect.mutate()}
					disabled={detect.isPending}
				>
					Detect models
				</button>
				<button
					type="button"
					onClick={() => save.mutate()}
					disabled={save.isPending}
				>
					{save.isPending ? "Verifying…" : "Verify & Save"}
				</button>
				<button type="button" className="secondary" onClick={onClose}>
					Cancel
				</button>
			</div>
			{message && (
				<div className="model-inline-message" role="status">
					{message}
				</div>
			)}
		</div>
	);
}

function ApiProfiles({
	model,
	types,
	health,
	qc,
	onDirty,
}: {
	model: ModelConfigView;
	types: Record<string, ProviderTypeView>;
	health: ModelHealthView[];
	qc: QueryClient;
	onDirty?: (id: string, isDirty: boolean) => void;
}) {
	const confirm = useConfirm();
	const [editing, setEditing] = useState<string | "new" | null>(null);
	const [error, setError] = useState<string | null>(null);
	const invalidate = createModelInvalidator(qc);
	const profiles = Object.entries(model.providers).filter(
		([, provider]) => provider.kind === "api",
	);
	const supportedTypes = apiTypes(types);
	const update = useMutation({
		mutationFn: ({
			providerId,
			enabled,
		}: {
			providerId: string;
			enabled: boolean;
		}) => api.updateModelProfile(providerId, { enabled }),
		onSuccess: invalidate,
		onError: (value) => setError(mutationMessage(value)),
	});
	const remove = useMutation({
		mutationFn: api.deleteModelProfile,
		onSuccess: invalidate,
		onError: (value) => setError(mutationMessage(value)),
	});

	return (
		<div className="model-category-panel">
			<div className="model-category-toolbar">
				<div>
					<strong>API profiles</strong>
					<p>
						Multiple endpoints of the same protocol can coexist. Saving a valid
						profile does not enable or assign it.
					</p>
				</div>
				<button type="button" onClick={() => setEditing("new")}>
					New API Profile
				</button>
			</div>
			{editing === "new" && supportedTypes.length > 0 && (
				<ApiProfileEditor
					initial={blankApiProfile(supportedTypes[0].id)}
					providerTypes={supportedTypes}
					qc={qc}
					onClose={() => setEditing(null)}
					onDirty={onDirty}
				/>
			)}
			{error && (
				<div className="model-inline-message error" role="alert">
					{error}
				</div>
			)}
			{profiles.length === 0 && editing !== "new" && (
				<div className="model-empty-state">No API profiles configured.</div>
			)}
			<div className="model-provider-list">
				{profiles.map(([providerId, provider]) => {
					const profileHealth = health.find(
						(item) => item.provider === providerId,
					);
					const roles = routingRoles(model, providerId);
					return (
						<article className="model-provider-card" key={providerId}>
							<header>
								<div>
									<div className="model-card-title">
										<strong>{provider.display_name || providerId}</strong>
										<code>{providerId}</code>
										{roles.map((role) => (
											<span key={role} className="provider-role-badge">
												{ROLE_LABELS[role]}
											</span>
										))}
									</div>
									<div className="model-card-meta">
										{provider.type} · {provider.model || "model not selected"}
										{provider.api_key.configured
											? ` · key ··${provider.api_key.last4}`
											: " · key not configured"}
									</div>
								</div>
								<div className="model-card-controls">
									<Status config={provider} health={profileHealth} />
									<Switch
										checked={provider.enabled}
										persistence="immediate"
										aria-label={`Enable ${provider.display_name || providerId}`}
										onCheckedChange={(enabled) =>
											update.mutate({ providerId, enabled })
										}
										disabled={update.isPending}
									/>
								</div>
							</header>
							<div className="model-card-footer">
								<RoleButtons providerId={providerId} model={model} qc={qc} />
								<div>
									<button
										type="button"
										className="secondary"
										onClick={() => setEditing(providerId)}
									>
										Edit
									</button>
									<button
										type="button"
										className="secondary danger"
										onClick={async () => {
											const impact = roles.length
												? ` This clears: ${roles.map((role) => ROLE_LABELS[role]).join(", ")}.`
												: "";
											if (await confirm({
												title: `Delete “${provider.display_name || providerId}”?`,
												description: `${impact} New calls will stop; already-dispatched remote requests may finish.`,
												confirmLabel: "Delete",
												variant: "danger",
											})) {
												remove.mutate(providerId);
											}
										}}
									>
										Delete
									</button>
								</div>
							</div>
							{editing === providerId && (
								<ApiProfileEditor
									initial={draftFromProfile(providerId, provider)}
									existing={provider}
									providerTypes={supportedTypes}
									qc={qc}
									onClose={() => setEditing(null)}
									onDirty={onDirty}
								/>
							)}
						</article>
					);
				})}
			</div>
		</div>
	);
}

function CodexLogin({ qc }: { qc: QueryClient }) {
	const [login, setLogin] = useState<{
		state: string;
		verification_uri: string | null;
		user_code: string | null;
		error: string | null;
	} | null>(null);
	const status = useQuery({
		queryKey: ["codex-login-status"],
		queryFn: api.codexLoginStatus,
		enabled: login?.state === "awaiting_authorization",
		refetchInterval: 3000,
	});
	useEffect(() => {
		if (status.data?.state === "completed") {
			qc.invalidateQueries({ queryKey: ["model-health"] });
			setLogin(status.data);
		}
	}, [status.data, qc]);
	const start = useMutation({
		mutationFn: api.codexLoginStart,
		onSuccess: setLogin,
	});
	const cancel = useMutation({
		mutationFn: api.codexLoginCancel,
		onSuccess: () => setLogin(null),
	});
	if (login?.state === "awaiting_authorization") {
		return (
			<div className="model-auth-flow">
				<span>{login.verification_uri}</span>
				<button
					type="button"
					className="secondary"
					onClick={() =>
						login.verification_uri &&
						window.open(login.verification_uri, "_blank", "noopener,noreferrer")
					}
				>
					Open verification page
				</button>
				<strong>Code: {login.user_code}</strong>
				<button
					type="button"
					className="secondary"
					onClick={() => cancel.mutate()}
				>
					Cancel
				</button>
			</div>
		);
	}
	return (
		<button
			type="button"
			className="secondary"
			onClick={() => start.mutate()}
			disabled={start.isPending}
		>
			Connect Codex
		</button>
	);
}

function IntegrationCard({
	definition,
	config,
	health,
	model,
	qc,
	onDirty,
	children,
}: {
	definition: ProviderTypeView;
	config?: ProviderConfigView;
	health?: ModelHealthView;
	model: ModelConfigView;
	qc: QueryClient;
	onDirty?: (id: string, isDirty: boolean) => void;
	children?: React.ReactNode;
}) {
	const [modelValue, setModelValue] = useState(
		config?.codex_model || config?.model || "",
	);
	const invalidate = createModelInvalidator(qc);
	const serverModelValue = config?.codex_model || config?.model || "";
	const dirty = modelValue !== serverModelValue;
	useSettingsDraftLifecycle({
		id: `model-integration-${definition.id}`,
		dirty,
		onDirty,
		serverKey: serverModelValue,
		adoptServer: () => setModelValue(serverModelValue),
	});
	const update = useMutation({
		mutationFn: (body: Record<string, unknown>) =>
			api.updateModelIntegration(definition.id, body),
		onSuccess: invalidate,
		meta: { errorSurface: "inline" },
	});
	const roles = routingRoles(model, definition.id);
	return (
		<article className="model-provider-card">
			<header>
				<div>
					<div className="model-card-title">
						<strong>{definition.label}</strong>
						{roles.map((role) => (
							<span key={role} className="provider-role-badge">
								{ROLE_LABELS[role]}
							</span>
						))}
					</div>
					<div className="model-card-meta">
						Local credentials remain owned by the integration.
					</div>
				</div>
				<div className="model-card-controls">
					<Status config={config} health={health} />
					<Switch
						checked={config?.enabled === true}
						persistence="immediate"
						aria-label={`Enable ${definition.label}`}
						onCheckedChange={(enabled) => update.mutate({ enabled })}
						disabled={update.isPending}
					/>
				</div>
			</header>
			<div className="model-integration-body">
				<label>
					<span>Model</span>
					<input
						value={modelValue === "default" ? "" : modelValue}
						placeholder="integration default"
						onChange={(event) => setModelValue(event.target.value)}
					/>
				</label>
				<button
					type="button"
					className="secondary"
					onClick={() =>
						update.mutate(
							definition.id === "codex_cli"
								? { codex_model: modelValue || null }
								: { model: modelValue || "default" },
						)
					}
				>
					Save model
				</button>
				{children}
			</div>
			{config && (
				<div className="model-card-footer">
					<RoleButtons providerId={definition.id} model={model} qc={qc} />
				</div>
			)}
			{update.isError && (
				<div className="model-inline-message error">
					{mutationMessage(update.error)}
				</div>
			)}
		</article>
	);
}

function CliIntegrations({
	model,
	types,
	health,
	qc,
	onDirty,
}: {
	model: ModelConfigView;
	types: Record<string, ProviderTypeView>;
	health: ModelHealthView[];
	qc: QueryClient;
	onDirty?: (id: string, isDirty: boolean) => void;
}) {
	return (
		<div className="model-category-panel">
			<div className="model-category-toolbar">
				<div>
					<strong>Local CLI agents</strong>
					<p>
						One installation per integration. Aletheia uses a no-shell harness
						and disables agent tools for model calls.
					</p>
				</div>
			</div>
			<div className="model-provider-list">
				{integrationTypes(types, "cli").map((definition) => (
					<IntegrationCard
						key={definition.id}
						definition={definition}
						config={model.providers[definition.id]}
						health={health.find((item) => item.provider === definition.id)}
						model={model}
						qc={qc}
						onDirty={onDirty}
					>
						{definition.id === "codex_cli" ? (
							<CodexLogin qc={qc} />
						) : (
							<span className="model-integration-hint">
								Authenticate from the {definition.label} terminal before
								enabling it here.
							</span>
						)}
					</IntegrationCard>
				))}
			</div>
		</div>
	);
}

function OAuthAction({
	providerId,
	qc,
	onDirty,
}: {
	providerId: string;
	qc: QueryClient;
	onDirty?: (id: string, isDirty: boolean) => void;
}) {
	const [state, setState] = useState<{
		state: string;
		auth_url?: string | null;
		error?: string | null;
	} | null>(null);
	const [code, setCode] = useState("");
	const isOpenAI = providerId === "openai_native";
	const status = useQuery({
		queryKey: ["provider-oauth-status", providerId],
		queryFn: isOpenAI ? api.openaiOAuthStatus : api.claudeOAuthStatus,
		enabled: isOpenAI && state?.state === "awaiting",
		refetchInterval: 2000,
	});
	const finalize = useMutation({ mutationFn: api.openaiOAuthFinalize });
	useSettingsDraftLifecycle({
		id: `oauth-${providerId}`,
		dirty: code !== "",
		onDirty,
		serverKey: undefined,
		adoptServer: () => undefined,
	});
	useEffect(() => {
		if (
			isOpenAI &&
			status.data?.state === "completed" &&
			state?.state !== "completed"
		) {
			setState(status.data);
			finalize.mutate(undefined, {
				onSuccess: () => {
					qc.invalidateQueries({ queryKey: ["model"] });
					qc.invalidateQueries({ queryKey: ["model-health"] });
					setCode("");
				},
			});
		}
	}, [isOpenAI, status.data, state?.state, finalize, qc]);
	const start = useMutation({
		mutationFn: isOpenAI ? api.openaiOAuthStart : api.claudeOAuthStart,
		onSuccess: (result) => {
			setState(result);
			if (result.auth_url)
				window.open(result.auth_url, "_blank", "noopener,noreferrer");
		},
	});
	const complete = useMutation({
		mutationFn: () => api.claudeOAuthComplete({ code: code.trim() }),
		onSuccess: (result) => {
			setState(result);
			setCode("");
			qc.invalidateQueries({ queryKey: ["model"] });
			qc.invalidateQueries({ queryKey: ["model-health"] });
		},
	});
	const cancel = useMutation({
		mutationFn: isOpenAI ? api.openaiOAuthCancel : api.claudeOAuthCancel,
		onSuccess: () => { setState(null); setCode("") },
	});
	if (state?.state === "awaiting" || state?.state === "awaiting_code") {
		return (
			<div className="model-auth-flow">
				<span>Complete authorization in the provider browser page.</span>
				{!isOpenAI && (
					<input
						value={code}
						placeholder="Paste authorization code"
						onChange={(event) => setCode(event.target.value)}
					/>
				)}
				{!isOpenAI && (
					<button
						type="button"
						onClick={() => complete.mutate()}
						disabled={!code.trim()}
					>
						Submit code
					</button>
				)}
				<button
					type="button"
					className="secondary"
					onClick={() => cancel.mutate()}
				>
					Cancel
				</button>
			</div>
		);
	}
	return (
		<button
			type="button"
			onClick={() => start.mutate()}
			disabled={start.isPending}
		>
			Connect
		</button>
	);
}

function OAuthConnections({
	model,
	types,
	health,
	qc,
	onDirty,
}: {
	model: ModelConfigView;
	types: Record<string, ProviderTypeView>;
	health: ModelHealthView[];
	qc: QueryClient;
	onDirty?: (id: string, isDirty: boolean) => void;
}) {
	const confirm = useConfirm();
	const invalidate = createModelInvalidator(qc);
	const disconnect = useMutation({
		mutationFn: (providerId: string) =>
			providerId === "openai_native"
				? api.openaiOAuthDisconnect()
				: api.claudeOAuthDisconnect(),
		onSuccess: invalidate,
	});
	return (
		<div className="model-category-panel">
			<div className="model-category-toolbar">
				<div>
					<strong>Official OAuth connections</strong>
					<p>
						One connection per provider. Tokens are write-only and refreshed by
						Aletheia.
					</p>
				</div>
			</div>
			<div className="model-provider-list">
				{integrationTypes(types, "oauth").map((definition) => {
					const config = model.providers[definition.id];
					return (
						<IntegrationCard
							key={definition.id}
							definition={definition}
							config={config}
							health={health.find((item) => item.provider === definition.id)}
							model={model}
							qc={qc}
							onDirty={onDirty}
						>
							{config?.oauth.configured ? (
									<button
										type="button"
										className="secondary danger"
										onClick={async () => {
										if (await confirm({
											title: `Disconnect ${definition.label}?`,
											confirmLabel: "Disconnect",
											variant: "danger",
										})) disconnect.mutate(definition.id)
									}}
								>
									Disconnect
								</button>
							) : (
								<OAuthAction providerId={definition.id} qc={qc} onDirty={onDirty} />
							)}
						</IntegrationCard>
					);
				})}
			</div>
		</div>
	);
}

export function ModelProvidersSection({ qc, onDirty }: { qc: QueryClient; onDirty?: (id: string, isDirty: boolean) => void }) {
	const [category, setCategory] = useState<"api" | "cli" | "oauth">("api");
	const modelQuery = useQuery({
		queryKey: ["model"],
		queryFn: api.getModelConfig,
	});
	const typesQuery = useQuery({
		queryKey: ["model-provider-types"],
		queryFn: api.getModelProviderTypes,
	});
	const healthQuery = useQuery({
		queryKey: ["model-health"],
		queryFn: api.getModelHealth,
		refetchInterval: 30000,
	});
	const error = modelQuery.error || typesQuery.error || healthQuery.error;
	const model = modelQuery.data as ModelConfigView | undefined;
	const types = typesQuery.data as Record<string, ProviderTypeView> | undefined;
	const health = useMemo(
		() => (healthQuery.data || []) as ModelHealthView[],
		[healthQuery.data],
	);

	return (
		<SettingsFeatureSection
			descriptor={MODEL_PROVIDERS_SECTION}
			error={error ? mutationMessage(error) : null}
			pendingLabel={!model || !types ? "Loading model access settings…" : null}
		>
			{model && types && (
				<div className="model-providers-feature">
					<RoutingBoard model={model} qc={qc} />
					<div
						className="model-category-tabs"
						role="tablist"
						aria-label="Model access method"
					>
						{(["api", "cli", "oauth"] as const).map((item) => (
							<button
								key={item}
								type="button"
								role="tab"
								aria-selected={category === item}
								className={category === item ? "active" : ""}
								onClick={() => setCategory(item)}
							>
								{item.toUpperCase()}
							</button>
						))}
					</div>
					{category === "api" && (
						<ApiProfiles model={model} types={types} health={health} qc={qc} onDirty={onDirty} />
					)}
					{category === "cli" && (
						<CliIntegrations
							model={model}
							types={types}
							health={health}
							qc={qc}
							onDirty={onDirty}
						/>
					)}
					{category === "oauth" && (
						<OAuthConnections
							model={model}
							types={types}
							health={health}
							qc={qc}
							onDirty={onDirty}
						/>
					)}
				</div>
			)}
		</SettingsFeatureSection>
	);
}
