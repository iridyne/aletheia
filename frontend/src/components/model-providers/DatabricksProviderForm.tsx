import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../../api/client";

const PROTOCOLS = new Set([
	"serving_endpoint",
	"chat_completions",
	"responses",
	"anthropic_messages",
]);

type Props = {
	config: any;
	isDefault: boolean;
	isDeep: boolean;
	isFast: boolean;
	hasDefault: boolean;
	qc: any;
};

type Result = { ok: boolean; message: string } | null;
type ApplyError = Error & { applyAmbiguous?: boolean };

function normalizedWorkspace(raw: string) {
	try {
		const url = new URL(raw);
		return `${url.protocol}//${url.host}`;
	} catch {
		return "";
	}
}

export function DatabricksProviderForm({
	config,
	isDefault,
	isDeep,
	isFast,
	hasDefault,
	qc,
}: Props) {
	const configuredProtocol = PROTOCOLS.has(config?.protocol)
		? config.protocol
		: "serving_endpoint";
	const [workspaceUrl, setWorkspaceUrl] = useState(config?.workspace_url || "");
	const [protocol, setProtocol] = useState(configuredProtocol);
	const [model, setModel] = useState(
		config?.model === "default" ? "" : config?.model || "",
	);
	const [apiKey, setApiKey] = useState("");
	const [applyPhase, setApplyPhase] = useState<"idle" | "verifying" | "applying">("idle");
	const [result, setResult] = useState<Result>(null);
	const [detectedModels, setDetectedModels] = useState<Array<{
		slug: string;
		display_name: string;
		ready_state?: string;
	}> | null>(null);

	const canonicalWorkspace = normalizedWorkspace(workspaceUrl);
	const configuredWorkspace = normalizedWorkspace(config?.workspace_url || "");
	const workspaceChanged = canonicalWorkspace !== configuredWorkspace;
	const storedCredentialUsable = config?.api_key?.configured && !workspaceChanged;
	const credentialReady = apiKey.length > 0 || storedCredentialUsable;
	const connectionType =
		protocol === "serving_endpoint" ? "serving" : "gateway";
	const dirty =
		workspaceUrl !== (config?.workspace_url || "") ||
		protocol !== configuredProtocol ||
		model !== (config?.model === "default" ? "" : config?.model || "") ||
		apiKey.length > 0;

	const refreshQueries = () => {
		qc.invalidateQueries({ queryKey: ["model"] });
		qc.invalidateQueries({ queryKey: ["model-health"] });
		qc.invalidateQueries({ queryKey: ["status"] });
	};

	const detect = useMutation({
		mutationFn: () =>
			api.detectModels({
				provider: "databricks",
				workspace_url: workspaceUrl,
				protocol,
				api_key: apiKey || undefined,
			}),
		onSuccess: (data) => {
			const models = data.models || [];
			setDetectedModels(models);
			const firstReady = models.find(
				(entry) => !entry.ready_state || entry.ready_state === "READY",
			);
			if (!model && firstReady) setModel(firstReady.slug);
			setResult(
				models.length > 0
					? {
							ok: true,
							message: `${models.length} endpoint/model option${models.length === 1 ? "" : "s"} detected.`,
						}
					: {
							ok: false,
							message:
								"Catalogue unavailable. Enter a known endpoint or model and verify it directly.",
						},
			);
		},
		onError: () => {
			setDetectedModels([]);
			setResult({
				ok: false,
				message:
					"Catalogue unavailable. Enter a known endpoint or model and verify it directly.",
			});
		},
	});

	const verifyAndApply = useMutation({
		mutationFn: async () => {
			setApplyPhase("verifying");
			const test = await api.testModelCandidate({
				provider: "databricks",
				workspace_url: workspaceUrl,
				protocol,
				model,
				api_key: apiKey || undefined,
				probe: true,
			});
			if (!test.available)
				throw new Error(test.error || "Databricks inference unavailable");
			setApplyPhase("applying");
			try {
				await api.setModelProvider({
					provider: "databricks",
					workspace_url: workspaceUrl,
					protocol,
					model,
					api_key: apiKey || undefined,
					enabled: true,
					...(!hasDefault ? { default: true } : {}),
				});
			} catch (cause) {
				const error = new Error(
					`Connection verified, but applying the configuration failed: ${cause instanceof Error ? cause.message : "unknown error"}. Reload settings before retrying.`,
				) as ApplyError;
				error.applyAmbiguous = true;
				throw error;
			}
			return test;
		},
		onSuccess: (test) => {
			setApplyPhase("idle");
			setApiKey("");
			setResult({
				ok: true,
				message: `Verified and applied (${test.model || model}).`,
			});
			refreshQueries();
		},
		onError: (error: ApplyError) => {
			setApplyPhase("idle");
			if (error.applyAmbiguous) {
				qc.invalidateQueries({ queryKey: ["model"] });
				setResult({ ok: false, message: error.message });
				return;
			}
			setResult({
				ok: false,
				message: `${error?.message || "Verification failed"}. The active configuration was not changed.`,
			});
		},
	});

	const setRoutingRole = useMutation({
		mutationFn: (role: string) =>
			api.setModelProvider({
				provider: "databricks",
				enabled: true,
				...(role === "default" ? { default: true } : {}),
				...(role === "deep" ? { set_as_deep: true } : {}),
				...(role === "fast" ? { set_as_fast: true } : {}),
			}),
		onSuccess: refreshQueries,
	});

	const resetDraft = () => {
		setWorkspaceUrl(config?.workspace_url || "");
		setProtocol(configuredProtocol);
		setModel(config?.model === "default" ? "" : config?.model || "");
		setApiKey("");
		setApplyPhase("idle");
		setDetectedModels(null);
		setResult(null);
	};

	return (
		<div className="pt-3">
			{dirty && (
				<div className="provider-draft-banner">
					Unsaved changes — the active Databricks connection remains unchanged
					until verification succeeds.
				</div>
			)}

			<div className="provider-form-block">
				<div className="provider-form-title">Connection</div>
				<label className="provider-field-label">Connection type</label>
				<div
					className="provider-mode-switch"
					role="group"
					aria-label="Databricks connection type"
				>
					<button
						type="button"
						className={connectionType === "serving" ? "active" : ""}
						onClick={() => {
							setProtocol("serving_endpoint");
							setResult(null);
							setDetectedModels(null);
						}}
					>
						Serving Endpoint
					</button>
					<button
						type="button"
						className={connectionType === "gateway" ? "active" : ""}
						onClick={() => {
							setProtocol("chat_completions");
							setResult(null);
							setDetectedModels(null);
						}}
					>
						AI Gateway Service
					</button>
				</div>

				<div className="form-grid mt-3">
					<label className="col-full">
						Workspace URL
						<input
							value={workspaceUrl}
							placeholder="https://adb-123.azuredatabricks.net"
							onChange={(event) => {
								setWorkspaceUrl(event.target.value);
								setResult(null);
								setDetectedModels(null);
							}}
						/>
						{canonicalWorkspace && (
							<span className="provider-field-help mono">
								Workspace detected: {canonicalWorkspace}
							</span>
						)}
					</label>
					{connectionType === "gateway" && (
						<label className="col-full">
							API protocol
							<select
								value={protocol}
								onChange={(event) => {
									setProtocol(event.target.value);
									setResult(null);
									setDetectedModels(null);
								}}
							>
								<option value="chat_completions">
									OpenAI — Chat Completions
								</option>
								<option value="responses">OpenAI — Responses</option>
								<option value="anthropic_messages">Anthropic Messages</option>
							</select>
						</label>
					)}
				</div>
			</div>

			<div className="provider-form-block">
				<div className="provider-form-title">Endpoint</div>
				<div className="form-grid">
					<label className="col-full">
						{connectionType === "serving" ? "Serving endpoint" : "Model"}
						<input
							value={model}
							list="databricks-model-options"
							placeholder={
								connectionType === "serving" ? "research-claude" : "model name"
							}
							onChange={(event) => {
								setModel(event.target.value);
								setResult(null);
							}}
							autoComplete="off"
						/>
						<datalist id="databricks-model-options">
							{(detectedModels || []).map((entry) => (
								<option key={entry.slug} value={entry.slug}>
									{entry.display_name}
								</option>
							))}
						</datalist>
					</label>
				</div>
				{detectedModels && detectedModels.length > 0 && (
					<div
						className="provider-catalogue"
						aria-label="Detected Databricks endpoints and models"
					>
						{detectedModels.slice(0, 8).map((entry) => {
							const ready = !entry.ready_state || entry.ready_state === "READY";
							return (
								<button
									type="button"
									className={entry.slug === model ? "selected" : ""}
									key={entry.slug}
									onClick={() => setModel(entry.slug)}
									disabled={!ready}
								>
									<span
										className={`provider-state-dot ${ready ? "ready" : "unavailable"}`}
									/>
									<span className="mono">{entry.display_name}</span>
								</button>
							);
						})}
					</div>
				)}
				<button
					type="button"
					className="secondary mt-2"
					onClick={() => detect.mutate()}
					disabled={!canonicalWorkspace || !credentialReady || detect.isPending}
				>
					{detect.isPending ? "Discovering…" : "Discover endpoints / models"}
				</button>
			</div>

			<div className="provider-form-block">
				<div className="provider-form-title">Authentication</div>
				<div className="form-grid">
					<label className="col-full">
						Databricks token
						<input
							type="password"
							value={apiKey}
							placeholder={
								workspaceChanged && config?.api_key?.configured
									? "enter a token for the new workspace"
									: config?.api_key?.configured
										? `•••••• configured (ends ··${config.api_key.last4}) — enter only to replace`
										: "paste personal access token"
							}
							onChange={(event) => {
								setApiKey(event.target.value);
								setResult(null);
							}}
							autoComplete="new-password"
						/>
					</label>
				</div>
			</div>

			<div className="provider-form-block">
				<div className="provider-form-title">Verification & routing</div>
				<div className="provider-verification-steps" aria-live="polite">
					<span className={canonicalWorkspace ? "done" : ""}>Workspace</span>
					<span className={credentialReady ? "done" : ""}>Authentication</span>
					<span className={model ? "done" : ""}>Endpoint</span>
					<span
						className={
							result?.ok && result.message.startsWith("Verified") ? "done" : ""
						}
					>
						Inference
					</span>
				</div>
				<div className="flex gap-2 items-center flex-wrap mt-3">
					<button
						type="button"
						onClick={() => verifyAndApply.mutate()}
						disabled={
							!canonicalWorkspace ||
							!model ||
							!credentialReady ||
							verifyAndApply.isPending
						}
					>
						{applyPhase === "applying"
							? "Applying…"
							: verifyAndApply.isPending
								? "Verifying…"
								: "Verify & Apply"}
					</button>
					{dirty && (
						<button
							type="button"
							className="secondary"
							onClick={resetDraft}
							disabled={verifyAndApply.isPending}
						>
							Cancel changes
						</button>
					)}
					<div className="provider-routing-assignments" aria-label="Databricks routing assignments">
						<span>Routing assignments</span>
						<button
							type="button"
							className="secondary"
							onClick={() => setRoutingRole.mutate("default")}
							disabled={isDefault || setRoutingRole.isPending || !config?.enabled}
						>{isDefault ? "✓ Default" : "Set default"}</button>
						<button
							type="button"
							className="secondary"
							onClick={() => setRoutingRole.mutate("deep")}
							disabled={isDeep || setRoutingRole.isPending || !config?.enabled}
						>{isDeep ? "✓ Deep" : "Use for deep"}</button>
						<button
							type="button"
							className="secondary"
							onClick={() => setRoutingRole.mutate("fast")}
							disabled={isFast || setRoutingRole.isPending || !config?.enabled}
						>{isFast ? "✓ Fast" : "Use for fast"}</button>
					</div>
				</div>
				{!hasDefault && (
					<p className="hint">
						The first verified provider becomes the default automatically.
					</p>
				)}
				{result && (
					<div
						className={`settings-save-msg ${result.ok ? "ok" : "err"}`}
						role={result.ok ? "status" : "alert"}
					>
						{result.ok ? "✓" : "✕"} {result.message}
					</div>
				)}
			</div>

			<details className="provider-advanced">
				<summary>Advanced</summary>
				<div className="provider-capability-grid">
					<span>
						Structured JSON<strong>Prompt validation</strong>
					</span>
					<span>
						Native JSON schema<strong>Not available</strong>
					</span>
					<span>
						Streaming<strong>Single final response</strong>
					</span>
					<span>
						Tool calling<strong>Not configured</strong>
					</span>
				</div>
				<p className="hint">
					Inference concurrency and rate limits remain under Inference
					Admission.
				</p>
			</details>
		</div>
	);
}
