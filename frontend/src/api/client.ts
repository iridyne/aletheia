import type { GuardRow } from "../lib/guards";
import type {
	CandidateDetail,
	EntryPlan,
	EntryWorkbenchResponse,
	InfraEvent,
	KnowledgeItem,
	MarketMicrostructure,
	MarketSnapshot,
	Opportunity,
	PlanningHistoryResponse,
	Position,
	PositionsReconciliation,
	SystemStatus,
	ThesisLineageRecord,
	UniverseCurrentResponse,
	UniverseSnapshot,
} from "../types";
import type { SettingsResponse } from "./settings";
import { safeErrorText } from "../lib/mutationErrors";
import type { components, operations } from "./generated/api";

// Core Trading OpenAPI Contract Types (derived directly from FastAPI schema)
export type PositionsQuery = NonNullable<operations["get_positions_api_positions_get"]["parameters"]["query"]>;
export type TradesQuery = NonNullable<operations["get_trades_api_trades_get"]["parameters"]["query"]>;
export type KillSwitchRequest = components["schemas"]["KillSwitchRequest"];
export type PlanningHistoryQuery = NonNullable<operations["get_planning_history_api_universe_planning_history_get"]["parameters"]["query"]>;
export type EntryWorkbenchQuery = NonNullable<operations["get_entry_workbench_api_universe_workbench_get"]["parameters"]["query"]>;
export type SnapshotsQuery = NonNullable<operations["list_snapshots_api_universe_snapshots_get"]["parameters"]["query"]>;

const BASE = "/api";
const REMOTE_CONTROL_TOKEN_KEY = "aletheia.remoteControlToken";

function remoteControlHeaders(): Record<string, string> {
	try {
		const token = window.localStorage.getItem(REMOTE_CONTROL_TOKEN_KEY)?.trim();
		return token ? { "X-Aletheia-Token": token } : {};
	} catch {
		return {};
	}
}

type RemoteControlTokenStorageResult =
	| { ok: true }
	| { ok: false; error: string };

export type SoftwareUpdateStatus = {
    mode: "source_release";
    ok?: boolean;
    reason?: string;
    has_update?: boolean;
    current_commit?: string;
    target_tag?: string;
    target_sha?: string;
    phase?: string;
    status?: string;
    hold?: boolean;
    detail?: string;
    updated_at?: string;
} | {
	ok: boolean;
	reason?: string;
    mode?: "development" | "packaged";
	has_update?: boolean;
	behind_count?: number;
	current_commit?: string;
	remote_commit?: string;
	branch?: string;
};

export interface CandidateSelectionStatus {
	configured_policy: "legacy_total_score_v1" | "candidate_selection_utility_v1";
	configured_policy_epoch: string | null;
	authority_enabled: boolean;
	authority_block_reason: string | null;
	selection_bases: ("measured" | "startup")[];
}

function setRemoteControlToken(token: string): RemoteControlTokenStorageResult {
	try {
		const normalized = token.trim();
		if (normalized)
			window.localStorage.setItem(REMOTE_CONTROL_TOKEN_KEY, normalized);
		else window.localStorage.removeItem(REMOTE_CONTROL_TOKEN_KEY);
		return { ok: true };
	} catch {
		return {
			ok: false,
			error:
				"Browser storage is unavailable; the Remote Control Token was not saved.",
		};
	}
}

export interface LearningContributionProjection {
	schema_version: "lco-v1";
	generated_at: string;
	availability: "available" | "partial" | "unavailable";
	unavailable_reason: string | null;
	data_freshness: {
		last_evaluated_at: string | null;
		age_sec: number | null;
		stale_after_sec: number | null;
		state: "fresh" | "stale" | "not_available" | "unavailable";
	};
	paths: Array<{
		path_id: string;
		producer: string;
		consumer: string;
		consumer_wired: boolean;
		settings_supported: boolean;
		measurement_supported: boolean;
		authority: string;
		allowed_effects: string[];
		fallback: string;
		outcome_source: string;
	}>;
	path_states: Array<{
		path_id: string;
		availability: "available" | "partial" | "unavailable";
		unavailable_reason: string | null;
		state: "active" | "empty" | "unavailable";
		data_freshness: LearningContributionProjection["data_freshness"];
	}>;
	items: Array<{
		path_id: string;
		availability: "available" | "partial" | "unavailable";
		data_freshness: LearningContributionProjection["data_freshness"];
		hypothesis_id: string;
		experiment_id: string;
		stage: string;
		experiment_status: string;
		allocation_pct: number;
		current_identity: {
			policy_epoch: string | null;
			target_horizon: string;
			outcome_contract_version: string;
			metric_version: string;
			behavior_contract_hash: string | null;
			code_commit: string | null;
			matches_running_cohort: boolean;
			mismatch_reason: string | null;
		};
		measurement: {
			state:
				| "not_measured"
				| "insufficient_data"
				| "neutral"
				| "positive"
				| "negative"
				| "invalid";
			reason: string | null;
			baseline: number | null;
			observed: number | null;
			delta: number | null;
			confidence_low: number | null;
			confidence_high: number | null;
			randomization_contract_version: string | null;
			randomization_p_value: number | null;
			randomization_mode: "exact" | "monte_carlo" | "unavailable" | null;
			iid_outcome_assumption: boolean | null;
			evaluated_at: string | null;
			window_start: string | null;
			window_end: string | null;
			coverage?: {
				status?: string;
				required_consecutive_days?: number;
				current_consecutive_market_days?: number;
				missing_market_day_n?: number;
			};
		};
		samples: {
			raw_exposures: number;
			treatment_assignment_units: number;
			control_assignment_units: number;
			required_per_arm: number;
			complete_assignment_units: number;
			cluster_members: number;
			selected_members: number;
			treatment_members: number | null;
			treatment_selected_members: number | null;
			control_members: number | null;
			control_selected_members: number | null;
		};
		integrity: {
			source_code_commits: string[];
			source_code_commit_unknown_n: number;
			code_commit_provenance_complete: boolean;
			outcome_source_member_counts: Record<string, number>;
			allocation_selection_source_member_counts: Record<string, number>;
			matching_mirror_rows: number;
			exclusion_reasons: Record<string, number>;
			invalid_reasons: Record<string, number>;
			conflict_count: number;
			evidence_conflicts: Array<{
				reason: string;
				outcome_candidate_key: string;
				row_count: number;
				row_provenance_truncated: boolean;
				canonical?: { source: string; row_id: number | string | null };
				mirrors?: Array<{ source: string; row_id: number | string | null }>;
				rows?: Array<{ source: string; row_id: number | string | null }>;
				differing_fields: Record<string, unknown>;
			}>;
			evidence_conflict_provenance_truncated: boolean;
			harm_state: "indeterminate" | "clear" | "breached" | "unknown";
			harm_indeterminate_reason: string | null;
			harm_breached: boolean;
			breach_reasons: string[];
		};
		lifecycle: {
			recommended_action: string;
			recommendation_reason: string | null;
			authority_enabled: boolean;
			authority_block_reason: string | null;
			mutation_available: boolean;
			mutation_unavailable_reason: string | null;
		};
		latest_historical_measurement: {
			verdict_id: string;
			state: string;
			reason: string | null;
			policy_epoch: string | null;
			stage: string | null;
			target_horizon: string | null;
			outcome_contract_version: string | null;
			metric_version: string | null;
			behavior_contract_hash: string | null;
			code_commit: string | null;
			source_code_commits: string[];
			source_code_commit_unknown_n: number;
			code_commit_provenance_complete: boolean;
			harm_state: "indeterminate" | "clear" | "breached" | "unknown";
			harm_indeterminate_reason: string | null;
			evidence_conflicts: Array<Record<string, unknown>>;
			evidence_conflict_provenance_truncated: boolean;
			current: boolean;
			mismatch_reason: string | null;
		} | null;
	}>;
	operations: {
		availability: "available" | "partial";
		query_errors: Record<string, string>;
		config: {
			contribution_evaluation_enabled: boolean;
			autonomous_learning_enabled: boolean;
			lifecycle_automation_enabled: boolean;
			paper_influence_enabled: boolean;
		};
		evaluator: {
			job_id: string;
			current_status: string;
			last_started_at: string | null;
			last_succeeded_at: string | null;
			next_due_at: string | null;
			input_watermark: string | null;
			backlog_count: number;
			oldest_backlog_at: string | null;
			consecutive_failures: number;
			last_failed_at: string | null;
			last_failure_code: string | null;
			catchup_due: boolean | null;
		} | null;
		lifecycle_audit: {
			job_id: string;
			current_status: string;
			last_started_at: string | null;
			last_succeeded_at: string | null;
			next_due_at: string | null;
			input_watermark: string | null;
			backlog_count: number;
			oldest_backlog_at: string | null;
			consecutive_failures: number;
			last_failed_at: string | null;
			last_failure_code: string | null;
			catchup_due: boolean;
		} | null;
	} | null;
}

export interface DreamingSummary {
	recent_research_notes: KnowledgeItem[];
	recent_strategy_hypotheses: KnowledgeItem[];
	deprecated_memories: KnowledgeItem[];
	recent_dreaming_logs: KnowledgeItem[];
	recent_daily_reviews: KnowledgeItem[];
	recent_knowledge_items: KnowledgeItem[];
	recent_knowledge_total: number;
	recent_knowledge_returned: number;
	recent_knowledge_truncated: boolean;
	deprecated_memories_total: number;
	deprecated_memories_truncated: boolean;
	recent_dreaming_runs: Array<{
		id: string; status: string; trigger_type?: string; started_at?: string;
		ended_at?: string; model_provider?: string; tools_called?: string | string[];
		outputs_created?: string | string[]; errors?: string | string[];
		artifacts?: Array<{ artifact_type: string; artifact_id: string; created_at: string }>;
	}>;
	dreaming_job: {
		job_id: string; schedule_key: string;
		last_expected_at?: string | null; last_started_at: string | null; last_succeeded_at: string | null;
		next_due_at?: string | null; input_watermark?: string | null; input_cursor?: string | null;
		backlog_count: number | null; oldest_backlog_at?: string | null; consecutive_failures?: number;
		status: string; last_failed_at?: string | null; updated_at?: string | null;
		overdue?: boolean; overdue_seconds?: number | null;
	} | null;
	dreaming_job_availability: "available" | "unavailable";
}

export interface KnowledgeCatalogResponse {
	items: KnowledgeItem[];
	total: number;
	limit: number;
	offset: number;
	returned: number;
	has_more: boolean;
}

export interface LearningFunnelProjection {
	knowledge_items_by_type: Array<{ item_type: string; count: number }>;
	pi_actual_visibility: { layer3_lesson_hits: number; layer3_guards_available: number; note: string };
	ps_lessons: { availability: "available" | "unavailable"; total: number | null; by_status: Array<{ status: string; count: number }> | null; latest_created_at: string | null };
	active_guards_total: number | null;
	serenity_opportunities_active: number | null;
	contradictions: { checked: boolean; count: number | null; items: unknown[]; eligible_total: number | null; inspected: number | null; truncated: boolean | null; note: string };
	query_errors: Record<string, string>;
}

function cleanErrorText(value: unknown): string | null {
	if (value == null) return null;
	const text = String(value).trim();
	return text ? text.replace(/_/g, " ") : null;
}

function formatErrorValue(value: unknown): string | null {
	if (value == null) return null;
	if (
		typeof value === "string" ||
		typeof value === "number" ||
		typeof value === "boolean"
	) {
		return cleanErrorText(value);
	}
	if (Array.isArray(value)) {
		const items = value.map(formatErrorValue).filter(Boolean);
		return items.length ? items.join("; ") : null;
	}
	if (typeof value === "object") {
		const record = value as Record<string, unknown>;
		const explicit =
			cleanErrorText(record.message) ||
			cleanErrorText(record.reason) ||
			cleanErrorText(record.error) ||
			cleanErrorText(record.detail);
		const failures =
			formatErrorValue(record.failures) ||
			formatErrorValue(record.errors) ||
			formatErrorValue(record.gates);

		const parts: string[] = [];
		if (explicit) parts.push(explicit);
		if (failures) parts.push(`Failures: ${failures}`);

		if (parts.length === 0) {
			for (const [key, item] of Object.entries(record).slice(0, 4)) {
				const formatted = formatErrorValue(item);
				if (formatted) parts.push(`${cleanErrorText(key)}: ${formatted}`);
			}
		}
		return parts.length ? parts.join(" - ") : null;
	}
	return null;
}

function formatApiErrorMessage(status: number, body: string): string {
	try {
		const obj = JSON.parse(body);
		const detail = obj.detail;
		const formatted = formatErrorValue(detail);
		if (formatted) return safeErrorText(formatted, `${status} error`);
	} catch {
		/* not JSON, fall through */
	}
	if (body.length > 80) return `${status} error`;
	return safeErrorText(body, `${status} error`);
}

export class ApiError extends Error {
	status: number;
	code?: string;
	details?: unknown;
	fieldErrors?: unknown;
	retryAfter?: string;
	correlationId?: string;

	constructor(status: number, message: string) {
		super(message);
		this.name = "ApiError";
		this.status = status;
	}
}

function errorField(value: unknown): string | undefined {
	if (typeof value !== "string" && typeof value !== "number") return undefined;
	const text = String(value).trim();
	return text || undefined;
}

export function parseApiError(status: number, body: string, headers?: Headers): ApiError {
	const error = new ApiError(status, formatApiErrorMessage(status, body));
	try {
		const payload = JSON.parse(body) as Record<string, unknown>;
		const detail = payload.detail;
		const fields = detail && typeof detail === "object" && !Array.isArray(detail)
			? detail as Record<string, unknown>
			: payload;
		error.code = errorField(fields.code ?? payload.code);
		error.details = fields.details ?? (typeof detail === "object" ? detail : undefined);
		error.fieldErrors = fields.field_errors ?? fields.fieldErrors ?? (Array.isArray(detail) ? detail : undefined);
		error.retryAfter = errorField(fields.retry_after ?? fields.retryAfter)
			?? headers?.get("Retry-After") ?? undefined;
		error.correlationId = errorField(fields.correlation_id ?? fields.correlationId)
			?? headers?.get("X-Correlation-ID") ?? headers?.get("X-Request-ID") ?? undefined;
	} catch {
		/* Non-JSON bodies still retain status and the bounded legacy message. */
	}
	return error;
}

export function extractApiError(status: number, body: string): string {
	return formatApiErrorMessage(status, body);
}

// Hard cap on every API request. Prevents the top loading bar from staying
// stuck for minutes when the backend is slow to respond after a restart.
const _FETCH_TIMEOUT_MS = 15_000;

export async function fetchJson<T>(
	path: string,
	init?: RequestInit,
	decode?: (body: unknown, headers: Headers) => T,
): Promise<T> {
	const timeoutCtrl = new AbortController();
	const timer = setTimeout(() => timeoutCtrl.abort(), _FETCH_TIMEOUT_MS);

	// Combine our timeout signal with any signal the caller already provided
	// (e.g. React Query's per-query cancellation signal threaded via init.signal).
	// Aborting either source aborts the request.
	const callerSignal = init?.signal;
	if (callerSignal) {
		if (callerSignal.aborted) {
			clearTimeout(timer);
			timeoutCtrl.abort();
		} else {
			callerSignal.addEventListener("abort", () => timeoutCtrl.abort(), {
				once: true,
			});
		}
	}

	try {
		const res = await fetch(`${BASE}${path}`, {
			...init,
			headers: {
				"Content-Type": "application/json",
				...remoteControlHeaders(),
				...(init?.headers || {}),
			},
			signal: timeoutCtrl.signal,
		});
		if (!res.ok) throw parseApiError(res.status, await res.text(), res.headers);
		const body: unknown = await res.json();
		return decode ? decode(body, res.headers) : body as T;
	} finally {
		clearTimeout(timer);
	}
}

export async function getWsTicket(): Promise<string | null> {
	try {
		const data = await fetchJson<{ ticket: string }>("/ws/ticket");
		return data.ticket ?? null;
	} catch {
		return null;
	}
}

function post<T>(path: string, body?: unknown): Promise<T> {
	return fetchJson<T>(path, {
		method: "POST",
		body: body ? JSON.stringify(body) : undefined,
	});
}

function put<T>(path: string, body?: unknown): Promise<T> {
	return fetchJson<T>(path, {
		method: "PUT",
		body: body ? JSON.stringify(body) : undefined,
	});
}

export interface KeyStatus {
	configured: boolean;
	last4: string | null;
}

export interface OhlcvCandle {
	time: number;
	open: number;
	high: number;
	low: number;
	close: number;
	volume: number;
}

export interface OhlcvResult {
	candles: OhlcvCandle[];
	meta: {
		source: string;
		instrument: string | null;
		asOf: string | null;
		ageMs: number | null;
		stale: boolean | null;
	};
}

export interface MarketOpportunityInstrument {
	symbol: string;
	market: string;
	total_count: number;
	open_count: number;
	latest_created_at: string;
	latest_status: string;
	latest_direction_bias: string | null;
}

export type MarketTrade = components["schemas"]["OrderView"];

export interface MarketNewsItem {
	id: string;
	title: string;
	sentiment?: string | null;
	source?: string | null;
	published_at?: string | null;
	summary?: string | null;
}

function timeframeMs(timeframe: string): number | null {
	const match = /^(\d+)([mhd])$/.exec(timeframe);
	if (!match) return null;
	const units = { m: 60_000, h: 3_600_000, d: 86_400_000 };
	return Number(match[1]) * units[match[2] as keyof typeof units];
}

export function decodeOhlcvResponse(
	body: unknown,
	headers: Headers,
	timeframe: string,
	nowMs = Date.now(),
): OhlcvResult {
	if (!Array.isArray(body)) throw new TypeError("Invalid OHLCV response");
	const candles = body.map((row): OhlcvCandle => {
		if (!row || typeof row !== "object") throw new TypeError("Invalid OHLCV candle");
		const value = row as Record<string, unknown>;
		for (const field of ["time", "open", "high", "low", "close", "volume"] as const) {
			if (typeof value[field] !== "number" || !Number.isFinite(value[field])) {
				throw new TypeError("Invalid OHLCV candle");
			}
		}
		const candle = {
			time: value.time, open: value.open, high: value.high,
			low: value.low, close: value.close, volume: value.volume,
		};
		return candle as OhlcvCandle;
	});
	const lastTime = candles[candles.length - 1]?.time;
	const ageMs = lastTime === undefined ? null : Math.max(0, nowMs - lastTime * 1000);
	const intervalMs = timeframeMs(timeframe);
	return {
		candles,
		meta: {
			source: headers.get("X-Aletheia-Data-Source") || "unknown",
			instrument: headers.get("X-Aletheia-Data-Instrument"),
			asOf: lastTime === undefined ? null : new Date(lastTime * 1000).toISOString(),
			ageMs,
			stale: ageMs === null || intervalMs === null ? null : ageMs > intervalMs * 2,
		},
	};
}

// ── Economics (token-usage ledger) ───────────────────────────────────────────
// Bound to `runtime_cost_events` rows returned by `/api/economics/costs` with
// `include_events=true`. The `usage_json` field is the raw JSON string from the
// ledger; callers parse it with `JSON.parse` and read `.normalized` for the
// provider-normalized token fields (see `economics/tracing._extract_usage`).
export interface RuntimeCostEvent {
	id: string;
	event_type: string;
	provider: string | null;
	model_or_tool: string;
	started_at: string;
	latency_ms: number | null;
	estimated_cost_usd: number | null;
	cost_status: "known" | "estimated" | "unknown";
	usage_json: string;
}

export interface EconomicsProviderBucket {
	event_count: number;
	known_cost_usd: number;
	unknown_count: number;
	input_tokens: number;
	output_tokens: number;
	reasoning_tokens: number;
	cached_input_tokens: number;
	success_count: number;
	error_count: number;
	avg_latency_ms: number | null;
}

export interface EconomicsDailyPoint extends EconomicsProviderBucket {
	date: string; // YYYY-MM-DD (UTC)
	provider: string;
}

// Named response shapes for the read-only economics surface. These mirror the
// contract of the `/api/economics/*` endpoints so page components can type
// their query results instead of reaching for `any`.
export interface EconomicsStatusResponse {
	tracing_enabled: boolean;
	audit_enabled: boolean;
}

export interface EconomicsCostsByTypeBucket {
	event_count: number;
	known_cost_usd: number;
	unknown_count: number;
}

export interface EconomicsCostsResponse {
	window_start: string;
	window_end: string;
	event_count: number;
	known_cost_subtotal_usd: number;
	unknown_event_count: number;
	by_type: Record<string, EconomicsCostsByTypeBucket>;
	events?: RuntimeCostEvent[];
}

export interface EconomicsCostsByProviderResponse {
	window_start: string;
	window_end: string;
	tracing_enabled: boolean;
	event_count: number;
	parse_errors: number;
	providers: Record<string, EconomicsProviderBucket>;
	/** Per-(provider, model) rollup of the same bucket fields. */
	models: Record<string, Record<string, EconomicsProviderBucket>>;
	daily_series: EconomicsDailyPoint[];
}

export interface EconomicsToolBucket {
	event_count: number;
	known_cost_usd: number;
	unknown_count: number;
	recorded_count: number;
	denied_count: number;
}

export interface EconomicsCostsByToolResponse {
	window_start: string;
	window_end: string;
	tracing_enabled: boolean;
	event_count: number;
	/** Per-tool rollup (keyed by tool name) of the call/cost split. */
	tools: Record<string, EconomicsToolBucket>;
}

export interface EconomicsBudget {
	id: string;
	scope: string;
	subject: string | null;
	usd_cap: number | null;
	invalid_reason?: string;
	window: string;
	enabled: number;
	source: string;
	created_at: string;
	updated_at: string;
}

export interface EconomicsBudgetsResponse {
	budgets: EconomicsBudget[];
}

export interface WouldBlockObservation {
	policy_id: string;
	scope: string;
	subject: string | null;
	window: string;
	usd_cap: number | null;
	spent_before_call_usd?: number;
	projected_after_call_usd?: number;
	over_by_usd?: number;
	reason_code?: string;
}

export interface EconomicsWouldBlockEvent extends Record<string, unknown> {
	would_block: WouldBlockObservation[];
}

export interface EconomicsWouldBlockResponse {
	events: EconomicsWouldBlockEvent[];
	window_days: number;
	limit: number;
}

export interface ModelHealthEntry {
	provider: string;
	model: string;
	consecutive_failures: number;
	cooldown_until: number;
	last_error: string | null;
	last_failure_at: number | null;
	last_success_at: number | null;
	cooling_down: boolean;
	cooldown_remaining_sec: number;
}

export interface EconomicsModelHealthResponse {
	snapshot: ModelHealthEntry[];
}

export type ModelHealthFreshness = components["schemas"]["ModelHealthRecord"]["freshness"];
export type ModelProviderState = components["schemas"]["ModelHealthRecord"]["state"];
export type ModelProviderHealth = components["schemas"]["ModelHealthRecord"];

export interface UsageWindow {
	days: 1 | 7 | 30 | 90;
	started_at: string;
	ended_at: string;
	updated_at: string;
	timezone: "UTC";
	granularity: "hour" | "day" | "week";
}

export interface UsageOverview {
	cost_usd: number | null;
	total_tokens: number | null;
	llm_calls: number | null;
	success_rate_pct: number | null;
	avg_cost_per_call_usd: number | null;
}

export interface UsageComparison {
	complete: boolean;
	cost_usd_change_pct?: number | null;
	total_tokens_change_pct?: number | null;
	llm_calls_change_pct?: number | null;
	success_rate_change_pct_points?: number | null;
	avg_cost_per_call_usd_change_pct?: number | null;
}

export interface UsageTokenComposition {
	input_tokens: number | null;
	output_tokens: number | null;
	cache_read_tokens?: number;
	cache_write_tokens?: number;
	reasoning_tokens?: number;
}

export interface UsageTrendPoint {
	started_at: string;
	cost_usd: number | null;
	input_tokens: number | null;
	output_tokens: number | null;
	llm_calls: number | null;
}

export interface UsageAgentBreakdown { agent: string; runs: number; calls: number; success_rate_pct: number | null; tokens: number | null; cost_usd: number | null; p95_latency_ms: number | null }
export interface UsageModelBreakdown { model: string; provider: string; calls: number; success_rate_pct: number | null; input_tokens: number | null; output_tokens: number | null; cost_usd: number | null; p95_latency_ms: number | null }
export interface UsageProviderBreakdown { provider: string; models: number; calls: number; success_rate_pct: number | null; tokens: number | null; cost_usd: number | null; p95_latency_ms: number | null }
export interface UsageToolBreakdown { tool: string; class: string; permitted: number; denied: number; calls: number; p50_latency_ms: number | null; p95_latency_ms: number | null }
export interface UsageReliability { attempts: number; errors: number; retries: number; fallbacks: number; p50_latency_ms: number | null; p95_latency_ms: number | null; error_categories?: Array<{ category: string; count: number }> }
export interface UsageCooldown { provider: string; model: string; remaining_seconds: number | null }
export interface UsageBudget { limit_usd: number; spend_usd: number | null; remaining_usd: number | null; utilization_pct: number | null; period: string }

export interface UsageSummaryResponse {
	contract_version: 2;
	window: UsageWindow;
	overview: UsageOverview;
	comparison: UsageComparison;
	token_composition: UsageTokenComposition;
	trend: UsageTrendPoint[];
}

export interface UsageOperationsResponse {
	contract_version: 2;
	window: UsageWindow;
	breakdowns: { agents: UsageAgentBreakdown[]; models: UsageModelBreakdown[]; providers: UsageProviderBreakdown[]; tools: UsageToolBreakdown[] };
	reliability: UsageReliability;
	active_cooldowns: UsageCooldown[];
	budgets: UsageBudget[];
}

export type UsageInsightsGroup = "summary" | "operations";
export interface UsageInsightsByGroup { summary: UsageSummaryResponse; operations: UsageOperationsResponse }

export interface PricingRefreshStatus {
	status: "idle" | "running" | "succeeded" | "failed";
	started_at: string | null;
	completed_at: string | null;
	last_success_at: string | null;
	error: string | null;
	source_url: string;
	version: string | null;
	fetched_model_count: number | null;
	matched_model_count: number;
	snapshots_created: number;
	unmatched_models: string[];
}

export interface SerenityTheme {
	id: string;
	name: string;
	demand_wave: string | null;
	status: string;
	objective: string | null;
	description: string | null;
	strategy_notes: string | null;
	tags: string[] | string;
	created_at: string;
	updated_at: string;
}

export interface SerenityEvidence {
	id: string;
	theme_id: string;
	symbol?: string | null;
	source: string;
	source_ref?: string | null;
	title: string;
	summary: string | null;
	tier: string;
	supports: boolean;
	evidence_type: string;
	event_at: string;
	created_at: string;
	actor?: string | null;
	confidence: string;
	tags: string[] | string;
}

export interface SerenityRiskGate {
	id: string;
	opportunity_id: string;
	gate_name: string;
	status: string;
	metric: string;
	observed: string | number | null;
	threshold: string | number | null;
	message?: string | null;
	checked_at: string;
}

export interface SerenityOpportunity {
	id: string;
	theme_id: string;
	symbol: string;
	direction: string;
	thesis: string | null;
	evidence_tier: string;
	stage: string;
	average_mode: string;
	action_intent: string;
	status: string;
	risk_flags: string[] | string;
	risk_metrics: Record<string, unknown> | string;
	confidence_score: number | null;
	created_at: string;
	updated_at: string;
	// EQ-R (2026-07-01): instrument classification for display + routing.
	// asset_class: "crypto" (default) | "equity" (US stock tokens from equity_chain lens)
	// market_type: "spot" | "swap" | "perp" — drives slash/no-slash display via fmtSymbol()
	asset_class?: string;
	market_type?: string;
	serenity_score?: {
		opportunity_id: string;
		score: number;
		bucket: string;
		evidence_score: number;
		stage_score: number;
		average_score: number;
		confidence_component: number;
		stage_index: number;
		evidence_count: number;
		risk_fail_gates: number;
		risk_warn_gates: number;
		risk_flag_count: number;
		average_engine?: {
			mode: string;
			active: boolean;
			dca_ladder?: Record<string, unknown>;
			historical_mismatch?: Record<string, unknown>;
			signals?: Record<string, unknown>;
		};
		reasons: Record<string, unknown>;
	};
	risk_gates?: SerenityRiskGate[];
}

export interface SerenityTimelinePoint {
	id: string;
	as_of: string;
	created_at: string;
	event_type: string;
	event_title: string;
	stage: string;
	score_total: number;
	bucket?: string;
	evidence_count?: number;
	risk_fail_gates?: number;
	risk_warn_gates?: number;
}

export interface SerenityResearchCycle {
	opportunity: SerenityOpportunity & {
		serenity_score: SerenityOpportunity["serenity_score"];
	};
	timeline: SerenityTimelinePoint[];
	risk_gates: SerenityRiskGate[];
	risk_gate_summary: Record<string, number>;
	post_mortems: Record<string, unknown>[];
	post_mortem_summary: {
		count: number;
		failure_bucket: Record<string, number>;
	};
}

export interface SerenityThemeOverview {
	theme: SerenityTheme;
	metrics: {
		opportunity_count: number;
		evidence_count: number;
		stage_counts: Record<string, number>;
		average_mode_counts: Record<string, number>;
		pipeline_order: string[];
	};
}

// #1939: append `?execution_mode=<mode>` to profitability endpoints so a
// selected-mode UI panel is never combined with all-mode aggregates.
function _withMode(path: string, execution_mode?: string): string {
	if (!execution_mode) return path;
	const sep = path.includes("?") ? "&" : "?";
	return `${path}${sep}execution_mode=${encodeURIComponent(execution_mode)}`;
}

export const api = {
	getHealth: () =>
		fetchJson<{ status: string; issues: string[]; version: string }>("/health"),
	getReady: () =>
		fetchJson<{
			ok: boolean;
			version: string;
			pid: number;
			uptime_sec: number;
		}>("/ready"),
	// Restart the gateway worker (warm) or full process (cold). Issued from the
	// topbar restart button when the health endpoint reports code_updated_pending_restart.
	restartGateway: () =>
		post<{ mode: "warm" | "cold"; ok: boolean; message: string }>(
			"/gateway/restart",
		),
	getStatus: () => fetchJson<SystemStatus>("/status"),
	getInfraEvents: () =>
		fetchJson<{ events: InfraEvent[] }>(
			"/infra/events?limit=12&min_severity=info",
		),
	getOperatorStatus: () => fetchJson<any>("/operator/status"),
	getSettings: () =>
		fetchJson<SettingsResponse>("/settings"),

	// Post-trade lessons (LessonStore). System Operator consumes them via trading_context.py:592
	// (wired in b9ad957). TTL fields: accepted lessons enter 24h dry-run then expire after 30d.
	getLessons: (limit = 50, status?: string) =>
		fetchJson<
			Array<{
				lesson_id: string;
				source_type?: string;
				source_id?: string;
				category?: string;
				severity?: string;
				summary?: string;
				status?: string;
				created_at?: string;
				activated_at?: string;
				dry_run_until?: string;
				expires_at?: string;
			}>
		>(
			`/persistence/lessons?limit=${limit}${status ? `&status=${encodeURIComponent(status)}` : ""}`,
		),
	acceptLesson: (lessonId: string) =>
		post<any>(`/persistence/lessons/${lessonId}/accept`),
	rejectLesson: (lessonId: string) =>
		post<any>(`/persistence/lessons/${lessonId}/reject`),
	archiveLesson: (lessonId: string) =>
		post<any>(`/persistence/lessons/${lessonId}/archive`),

	// Guard rows include storage lifecycle and effective authority as separate fields.
	getGuards: () => fetchJson<GuardRow[]>("/knowledge/guards"),
	getGuardCandidates: () =>
		fetchJson<GuardRow[]>("/knowledge/guard-candidates"),
	promoteGuardCandidate: (guardId: string) =>
		post<any>(`/knowledge/guard-candidates/${guardId}/promote`),
	getGuardStats: () =>
		fetchJson<
			Array<{
				guard_id: string;
				title: string;
				fire_count: number;
				loss_rate_when_fired: number;
				avg_loss_pct: number;
				estimated_savings: number;
			}>
		>("/knowledge/guards/stats"),

	pause: () => post("/gateway/pause"),
	resume: () => post("/gateway/resume"),
	pauseOperator: () => post("/operator/pause"),
	resumeOperator: () => post("/operator/resume"),
	wakeOperator: () => post("/operator/wake"),

	setPermissionMode: (mode: string) =>
		post("/settings/permission-mode", { mode }),
	setExchangeMode: (mode: string) => post("/settings/exchange-mode", { mode }),

	// Model provider config
	getModelConfig: () =>
		fetchJson<{
			default_provider: string;
			deep_provider: string | null;
			fast_provider: string | null;
			providers: Record<string, any>;
		}>("/model"),
	getModelProviderTypes: () =>
		fetchJson<
			Record<
				string,
				{
					id: string;
					label: string;
					category: "api" | "cli" | "oauth";
					fields: string[];
					singleton: boolean;
					protocols?: string[];
				}
			>
		>("/model/provider-types"),
	getModelHealth: () =>
		fetchJson<ModelProviderHealth[]>("/model/health"),
	getInferenceBroker: () =>
		fetchJson<{
			running: boolean;
			providers: Record<
				string,
				{ policy: Record<string, number>; metrics: Record<string, number> }
			>;
		}>("/model/broker"),

	// Read-only economics surface (Phase 1 of the tokenomics harness).
	// Mirrors `aletheia audit` CLI subcommands so operators can see LLM
	// spend and cooldown state from the dashboard.
		getUsageInsights: <G extends UsageInsightsGroup>(days: 1 | 7 | 30 | 90, group: G) =>
			fetchJson<UsageInsightsByGroup[G]>(
				`/economics/usage-insights?days=${days}&group=${group}`,
			),
	getPricingRefreshStatus: () =>
		fetchJson<PricingRefreshStatus>("/economics/pricing/refresh-status"),
	refreshPricing: () => post<PricingRefreshStatus>("/economics/pricing/refresh"),
	getEconomicsStatus: () =>
		fetchJson<EconomicsStatusResponse>("/economics/status"),
	getEconomicsCosts: (params?: {
		days?: number;
		run_id?: string;
		include_events?: boolean;
		event_limit?: number;
	}) => {
		const qs = new URLSearchParams();
		if (params?.days !== undefined) qs.set("days", String(params.days));
		if (params?.run_id) qs.set("run_id", params.run_id);
		if (params?.include_events) qs.set("include_events", "true");
		if (params?.event_limit !== undefined)
			qs.set("event_limit", String(params.event_limit));
		const suffix = qs.toString() ? `?${qs.toString()}` : "";
		return fetchJson<EconomicsCostsResponse>(`/economics/costs${suffix}`);
	},
	getEconomicsCostsByProvider: (params?: {
		days?: number;
		run_id?: string;
	}) => {
		const qs = new URLSearchParams();
		if (params?.days !== undefined) qs.set("days", String(params.days));
		if (params?.run_id) qs.set("run_id", params.run_id);
		const suffix = qs.toString() ? `?${qs.toString()}` : "";
		return fetchJson<EconomicsCostsByProviderResponse>(
			`/economics/costs/by-provider${suffix}`,
		);
	},
	getEconomicsCostsByTool: (params?: {
		days?: number;
		run_id?: string;
	}) => {
		const qs = new URLSearchParams();
		if (params?.days !== undefined) qs.set("days", String(params.days));
		if (params?.run_id) qs.set("run_id", params.run_id);
		const suffix = qs.toString() ? `?${qs.toString()}` : "";
		return fetchJson<EconomicsCostsByToolResponse>(
			`/economics/costs/by-tool${suffix}`,
		);
	},
	getEconomicsTokens: (params?: { days?: number; run_id?: string }) => {
		const qs = new URLSearchParams();
		if (params?.days !== undefined) qs.set("days", String(params.days));
		if (params?.run_id) qs.set("run_id", params.run_id);
		const suffix = qs.toString() ? `?${qs.toString()}` : "";
		return fetchJson<{
			window_start: string;
			window_end: string;
			llm_attempt_count: number;
			input_output_available_attempt_count: number;
			input_output_unavailable_attempt_count: number;
			tokens: {
				input_tokens: number;
				output_tokens: number;
				total_tokens: number;
				reasoning_tokens: number;
				cached_input_tokens: number;
				provider_cache_read_input_tokens: number;
				provider_cache_creation_input_tokens: number;
			};
			field_availability: Record<string, number>;
			by_logical_agent: Record<string, Record<string, number>>;
			by_role: Record<string, Record<string, number>>;
			token_integrity_observation_counts: Record<string, number>;
		}>(`/economics/tokens${suffix}`);
	},
	getEconomicsViability: (params: {
		scope: "system" | "operator";
		days?: number;
		execution_mode?: string;
		data_cost_usd?: number;
		infrastructure_cost_usd?: number;
		other_cost_usd?: number;
		as_of?: string;
	}) => {
		const qs = new URLSearchParams();
		qs.set("scope", params.scope);
		if (params.days !== undefined) qs.set("days", String(params.days));
		if (params.execution_mode) qs.set("execution_mode", params.execution_mode);
		if (params.data_cost_usd !== undefined)
			qs.set("data_cost_usd", String(params.data_cost_usd));
		if (params.infrastructure_cost_usd !== undefined)
			qs.set("infrastructure_cost_usd", String(params.infrastructure_cost_usd));
		if (params.other_cost_usd !== undefined)
			qs.set("other_cost_usd", String(params.other_cost_usd));
		if (params.as_of) qs.set("as_of", params.as_of);
		return fetchJson<{
			scope: string;
			window_start: string;
			window_end: string;
			verdict:
				| "positive"
				| "neutral"
				| "negative"
				| "insufficient_data"
				| "invalid";
			viability_amount_usd: number | null;
			net_trading_result_usd: number | null;
			deployment_cost_usd: number | null;
			intelligence_cost_usd: number | null;
			tool_cost_usd: number | null;
			data_cost_usd: number | null;
			cost_completeness: string;
			sample_count: number;
			reason: string;
		}>(`/economics/viability?${qs.toString()}`);
	},
	getEconomicsViabilityReports: (params?: {
		scope?: "system" | "operator";
		limit?: number;
		offset?: number;
	}) => {
		const qs = new URLSearchParams();
		if (params?.scope) qs.set("scope", params.scope);
		if (params?.limit !== undefined) qs.set("limit", String(params.limit));
		if (params?.offset !== undefined) qs.set("offset", String(params.offset));
		const suffix = qs.toString() ? `?${qs.toString()}` : "";
		return fetchJson<{
			reports: Array<Record<string, unknown>>;
			limit: number;
			offset: number;
			total: number;
		}>(`/economics/viability/reports${suffix}`);
	},
	getEconomicsModelHealth: () =>
		fetchJson<EconomicsModelHealthResponse>("/economics/model-health"),
	getEconomicsPricing: () =>
		fetchJson<{
			models: Array<Record<string, unknown>>;
			tools: Array<Record<string, unknown>>;
		}>("/economics/pricing"),
	getEconomicsBudgets: (params?: {
		scope?:
			| "system"
			| "agent_run"
			| "logical_agent"
			| "provider"
			| "provider_tier";
		enabled_only?: boolean;
	}) => {
		const qs = new URLSearchParams();
		if (params?.scope) qs.set("scope", params.scope);
		if (params?.enabled_only) qs.set("enabled_only", "true");
		const suffix = qs.toString() ? `?${qs.toString()}` : "";
		return fetchJson<EconomicsBudgetsResponse>(`/economics/budgets${suffix}`);
	},
	getEconomicsBudgetsWouldBlock: (params?: {
		days?: number;
		limit?: number;
		scope?:
			| "system"
			| "agent_run"
			| "logical_agent"
			| "provider"
			| "provider_tier";
	}) => {
		const qs = new URLSearchParams();
		if (params?.days !== undefined) qs.set("days", String(params.days));
		if (params?.limit !== undefined) qs.set("limit", String(params.limit));
		if (params?.scope) qs.set("scope", params.scope);
		const suffix = qs.toString() ? `?${qs.toString()}` : "";
		return fetchJson<EconomicsWouldBlockResponse>(
			`/economics/budgets/would-block${suffix}`,
		);
	},
	pingModel: () =>
		post<{
			ok: boolean;
			latency_ms: number;
			model: string;
			response: string;
			error: string | null;
			verified?: boolean;
			verification_identity?: string | null;
			verification_persisted?: boolean;
		}>("/model/ping"),
	createModelProfile: (body: Record<string, unknown>) =>
		post("/settings/model-profiles", body),
	updateModelProfile: (profileId: string, body: Record<string, unknown>) =>
		fetchJson(`/settings/model-profiles/${encodeURIComponent(profileId)}`, {
			method: "PATCH",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(body),
		}),
	deleteModelProfile: (profileId: string) =>
		fetchJson(
			`/settings/model-profiles/${encodeURIComponent(profileId)}?confirm=true`,
			{
				method: "DELETE",
			},
		),
	updateModelIntegration: (
		integrationId: string,
		body: Record<string, unknown>,
	) =>
		fetchJson(
			`/settings/model-integrations/${encodeURIComponent(integrationId)}`,
			{
				method: "PATCH",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
			},
		),
	updateModelRouting: (body: {
		default_provider?: string | null;
		deep_provider?: string | null;
		fast_provider?: string | null;
	}) => post("/settings/model-routing", body),
	setModelProvider: (body: {
		provider: string;
		base_url?: string;
		protocol?: string;
		model?: string;
		enabled?: boolean;
		default?: boolean;
		set_as_deep?: boolean;
		set_as_fast?: boolean;
		api_key?: string;
		codex_model?: string;
		workspace_url?: string;
		auth_scheme?: string;
		inference_max_concurrency?: number;
		inference_requests_per_minute?: number;
		inference_tpm?: number;
		inference_queue_capacity?: number;
		inference_reserved_high_priority_slots?: number;
		inference_timeout_sec?: number;
	}) => post("/settings/model", body),
	testModelCandidate: (body: {
		provider: string;
		type?: string;
		base_url?: string;
		workspace_url?: string;
		protocol?: string;
		model?: string;
		api_key?: string;
		auth_scheme?: string;
		probe?: boolean;
	}) =>
		post<{
			available: boolean;
			configured: boolean;
			reachable: boolean | null;
			verified: boolean | null;
			verified_at?: string | null;
			verification_identity?: string | null;
			verification_source?: string | null;
			state?: ModelProviderState;
			reason_code?: string;
			model: string;
			error: string | null;
			models?: string[];
			persisted?: boolean;
		}>("/model/test-candidate", body),
	detectModels: (body: {
		provider: string;
		type?: string;
		base_url?: string;
		workspace_url?: string;
		protocol?: string;
		api_key?: string;
		auth_scheme?: string;
	}) =>
		post<{
			models: { slug: string; display_name: string; ready_state?: string }[];
		}>("/model/detect-models", body),

	// Codex subscription provider: web-triggered device-auth login + model listing
	codexLoginStart: () =>
		post<{
			state: string;
			verification_uri: string | null;
			user_code: string | null;
			error: string | null;
		}>("/model/codex/login-start"),
	codexLoginStatus: () =>
		fetchJson<{
			state: string;
			verification_uri: string | null;
			user_code: string | null;
			error: string | null;
		}>("/model/codex/login-status"),
	codexLoginCancel: () => post("/model/codex/login-cancel"),
	getCodexModels: () =>
		fetchJson<{ slug: string; display_name: string }[]>("/model/codex/models"),

	// OpenAI subscription OAuth (ChatGPT Plus/Pro)
	openaiOAuthStart: () =>
		post<{ state: string; auth_url: string | null; error: string | null }>(
			"/model/openai-oauth/start",
		),
	openaiOAuthStatus: () =>
		fetchJson<{ state: string; auth_url: string | null; error: string | null }>(
			"/model/openai-oauth/status",
		),
	openaiOAuthFinalize: () =>
		post<{ persisted: boolean; reason?: string }>(
			"/model/openai-oauth/finalize",
		),
	openaiOAuthCancel: () => post("/model/openai-oauth/cancel"),
	openaiOAuthDisconnect: () =>
		post<{ disconnected: boolean }>("/model/openai-oauth/disconnect"),

	// Claude subscription OAuth (Claude Max/Pro)
	claudeOAuthStart: () =>
		post<{ state: string; auth_url: string | null; error: string | null }>(
			"/model/claude-oauth/start",
		),
	claudeOAuthComplete: (body: { code: string }) =>
		post<{ state: string; error: string | null }>(
			"/model/claude-oauth/complete",
			body,
		),
	claudeOAuthStatus: () =>
		fetchJson<{ state: string; auth_url: string | null; error: string | null }>(
			"/model/claude-oauth/status",
		),
	claudeOAuthCancel: () => post("/model/claude-oauth/cancel"),
	claudeOAuthDisconnect: () =>
		post<{ disconnected: boolean }>("/model/claude-oauth/disconnect"),

	// Exchange credentials — unified per-exchange APIs
	getBitgetCredStatus: () =>
		fetchJson<{ demo: any; live: any }>("/bitget/credentials-status"),
	setBitgetCredentials: (body: {
		profile: string;
		api_key?: string;
		api_secret?: string;
		passphrase?: string;
	}) => post("/settings/exchange-credentials", body),
	testBitget: (mode?: string) =>
		post<{ connected: boolean; mode?: string; error?: string }>(
			"/bitget/test-connection",
			mode ? { mode } : {},
		),
	testBitgetCandidate: (body: {
		profile: string;
		api_key: string;
		api_secret: string;
		passphrase: string;
	}) =>
		post<{ connected: boolean; profile?: string; error?: string }>(
			"/bitget/test-candidate",
			body,
		),

	getBinanceCredStatus: () =>
		fetchJson<{ testnet: any; live: any }>("/binance/credentials-status"),
	setBinanceCredentials: (body: {
		profile: string;
		api_key?: string;
		api_secret?: string;
	}) => post("/settings/binance-credentials", body),
	testBinance: (mode?: string) =>
		post<{ connected: boolean; mode?: string; error?: string }>(
			"/binance/test-connection",
			mode ? { mode } : {},
		),
	testBinanceCandidate: (body: {
		mode: string;
		api_key: string;
		api_secret: string;
	}) =>
		post<{ connected: boolean; mode?: string; error?: string }>(
			"/binance/test-candidate",
			body,
		),

	getOkxCredStatus: () =>
		fetchJson<{ demo: any; live: any }>("/okx/credentials-status"),
	setOkxCredentials: (body: {
		profile?: string;
		api_key?: string;
		api_secret?: string;
		passphrase?: string;
	}) => post("/settings/okx-credentials", body),
	testOkx: (mode?: string) =>
		post<{ connected: boolean; mode?: string; error?: string }>(
			"/okx/test-connection",
			mode ? { mode } : {},
		),
	testOkxCandidate: (body: {
		mode?: string;
		api_key: string;
		api_secret: string;
		passphrase: string;
	}) =>
		post<{ connected: boolean; mode?: string; error?: string }>(
			"/okx/test-candidate",
			body,
		),

	// Legacy aliases (keep for any remaining callers)
	setExchangeCredentials: (body: {
		profile: string;
		api_key?: string;
		api_secret?: string;
		passphrase?: string;
	}) => post("/settings/exchange-credentials", body),
	getBinanceTestnetCredStatus: () =>
		fetchJson<{ api_key: any; api_secret: any }>(
			"/binance-testnet/credentials-status",
		),
	setBinanceTestnetCredentials: (body: {
		api_key?: string;
		api_secret?: string;
	}) => post("/settings/binance-testnet-credentials", body),
	testBinanceTestnet: () =>
		post<{ connected: boolean; positions_count?: number; error?: string }>(
			"/binance-testnet/test-connection",
			{},
		),

	// Telegram bot reachability (facet A — never returns the token)
	getTelegramHealth: () =>
		fetchJson<{ reachable: boolean; username?: string; error?: string }>(
			"/telegram/health",
		),

	// Live state
	livePreflight: () =>
		post<{
			ok: boolean;
			broker?: string;
			execution_mode?: string;
			account_environment?: string;
			private_authenticated?: boolean;
			public_reachability?: string;
			account_readable?: boolean;
			permission_status?: string;
			reason_code?: string;
			fetched_at?: string;
			expires_at?: string;
		}>("/settings/live-preflight", {}),
	liveUnlock: () => post("/settings/live-unlock", {
		confirm: true,
		acknowledge_first_unlock: true,
	}),
	liveLock: () => post("/settings/live-lock"),

	// Risk: boundaries display + kill switch + editable config
	getRiskBoundaries: () => fetchJson<components["schemas"]["RiskConfig"]>("/risk/boundaries"),
	getRiskStatus: () => fetchJson<components["schemas"]["RiskStatusResponse"]>("/risk/status"),
	// 24h pipeline counts — used by Risk Active Risk panel to surface "the system is
	// actively guarding" (approved/rejected/top rejection reason) even in the healthy state.
	getPipelineFunnel: (hours = 24) =>
		fetchJson<components["schemas"]["PipelineFunnelResponse"]>(`/analytics/pipeline-funnel?hours=${hours}`),
	// Recent terminal operator decisions — legacy compatibility projection.
	// Non-terminal conviction deferrals remain in the opportunity trace.
	//
	// Optional `outcome` / `symbol` filters are applied server-side AFTER the
	// full-window counts are computed — so counts and total_count never lie
	// about totals even when items is filtered. matched_count reports how many
	// rows matched the filter before truncation to limit.
	getOperatorDecisions: (
		hours = 24,
		limit = 50,
		opts: { outcome?: string | string[]; symbol?: string } = {},
	) => {
		const params = new URLSearchParams({
			hours: String(hours),
			limit: String(limit),
		});
		if (opts.outcome) {
			const value = Array.isArray(opts.outcome)
				? opts.outcome.join(",")
				: opts.outcome;
			if (value) params.set("outcome", value);
		}
		if (opts.symbol) params.set("symbol", opts.symbol);
		return fetchJson<components["schemas"]["OperatorDecisionsResponse"]>(`/analytics/operator-decisions?${params.toString()}`);
	},

	// Full lifecycle trace for one opportunity — powers the decision drawer.
	getOpportunityTrace: (opportunityId: string) =>
		fetchJson<{
			opportunity_id: string;
			found: boolean;
			symbol?: string | null;
			direction?: string | null;
			market?: string | null;
			scanner_family?: string | null;
			created_at?: string | null;
			final_state?: string | null;
			final_stage?: string | null;
			final_reason?: string | null;
			timeline?: Array<{
				at: string;
				stage?: string;
				actor?: string;
				summary?: string;
				reason?: string;
				details?: Record<string, unknown>;
			}>;
			counterfactual?: {
				verdict?: string | null;
				shadow?: Record<string, unknown> | null;
				forward?: Record<string, unknown> | null;
			};
		}>(`/analytics/opportunity-trace/${encodeURIComponent(opportunityId)}`),

	// Market Observer → Operator funnel. Surfaces WHY opportunities don't reach
	// the operator (e.g. confidence gate, TTL expiry) — counterpart to the
	// legacy Opportunity diagnostics. Full-window response; see the compact
	// /current-snapshot sibling for a faster subset when only `current.*` is
	// needed.
	getOpportunityFunnel: (hours = 24) =>
		fetchJson<{
			window_hours: number;
			emitted_count: number;
			setup_count: number;
			active_setup_count: number;
			setup_identity_basis: "symbol+market+direction+scanner_family";
			reviewed_count: number;
			still_open_count: number;
			queued_count: number;
			expired_count: number;
			terminal_breakdown: {
				snapshot_superseded: number;
				ttl_expired: number;
				other_expired: number;
				operator_trade: number;
				operator_skip: number;
				operator_watch: number;
				deterministic_review: number;
				other_reviewed: number;
			};
			proposed_count: number;
			cohort_basis: "opportunities_emitted_in_window";
            current: {
                as_of: string;
                summary: { active_entry_plans: number; needs_planning: number; expiring_soon: number };
                candidate_attention: import("../types").CandidateAttention[];
                entry_plans: import("../types").EntryPlan[];
                planning_history: Record<string, unknown>[];
            };
			snapshot: {
				executable_count: number;
				excluded_by_reason: Array<{
					reason: string;
					count: number;
					examples: Array<{
						symbol?: string | null;
						direction?: string | null;
						trade_score?: number | null;
						scanner_family?: string | null;
						skip_reason?: string | null;
						opportunity_id?: string | null;
						valid_until?: string | null;
						expired_seconds?: number | null;
					}>;
				}>;
				main_reason: string | null;
				below_floor: boolean | null;
				floor: number | null;
				snapshot_at: string | null;
				checked_at: string | null;
				freshness: "fresh" | "stale" | "missing" | "unavailable";
				age_sec: number | null;
				stale_after_sec: number;
			};
			family_ttl_sec: Record<string, number>;
			universe_snapshot_age_sec: number | null;
		}>(`/analytics/opportunity-funnel?hours=${hours}`),

	// Fast subset of getOpportunityFunnel — returns just `current` + config +
	// family_ttl_sec. Retained for legacy Opportunity diagnostics.
	getOpportunityFunnelCurrentSnapshot: (hours = 24) =>
		fetchJson<{
			window_hours: number;
			// Same shape as getOpportunityFunnel().current — inlined for the
			// fast endpoint's compact response. Fields not enumerated here (the
			// rich candidate/ranker sub-shape) are inherited from the full type
			// via structural typing at call sites.
			current: Awaited<ReturnType<typeof api.getOpportunityFunnel>>["current"];
			family_ttl_sec: Record<string, number>;
		}>(`/analytics/opportunity-funnel/current-snapshot?hours=${hours}`),

	getLearningFunnel: () =>
		fetchJson<LearningFunnelProjection>("/analytics/learning-funnel"),
	getLearningContribution: () =>
		fetchJson<LearningContributionProjection>(
			"/analytics/learning-contribution",
		),
	getKillSwitch: () => fetchJson<components["schemas"]["KillSwitchStatusResponse"]>("/risk/kill-switch"),
	// Releasing the brake requires confirm_release=true (backend returns 422 otherwise).
	setKillSwitch: (active: boolean, reason = "", confirmRelease = false) => {
		const body: KillSwitchRequest = {
			active,
			reason,
			confirm_release: confirmRelease,
		};
		return post<{ active: boolean }>("/risk/kill-switch", body);
	},
	getRiskPolicies: () =>
		fetchJson<Record<"demo" | "live", any>>("/settings/risk-policies"),
	replaceRiskPolicy: (
		mode: "demo" | "live",
		policy: any,
		expectedRevision: number,
		reason: string,
	) =>
		put<Record<"demo" | "live", any>>(`/settings/risk-policies/${mode}`, {
			policy,
			expected_revision: expectedRevision,
			confirm_target_replacement: true,
			reason,
		}),
	copyRiskPolicy: (
		source: "demo" | "live",
		target: "demo" | "live",
		sourceRevision: number,
		targetRevision: number,
		reason: string,
	) =>
		post<Record<"demo" | "live", any>>("/settings/risk-policies/copy", {
			source,
			target,
			source_revision: sourceRevision,
			target_revision: targetRevision,
			confirm_target_replacement: true,
			reason,
		}),
	resetRiskPolicy: (
		mode: "demo" | "live",
		expectedRevision: number,
		reason: string,
	) =>
		post<Record<"demo" | "live", any>>(
			`/settings/risk-policies/${mode}/reset-defaults`,
			{
				expected_revision: expectedRevision,
				confirm_target_replacement: true,
				reason,
			},
		),
	getRiskBrakes: () =>
		fetchJson<components["schemas"]["BrakesStatusResponse"]>("/risk/brakes"),
	setRiskBrake: (
		scope: "global" | "demo" | "live",
		active: boolean,
		reason = "",
		confirmRelease = false,
	) =>
		post(`/risk/brakes/${scope}`, {
			active,
			reason,
			confirm_release: confirmRelease,
		}),
	updateRiskConfig: (
		body: Partial<{
			max_daily_loss_pct: number;
			max_single_trade_risk_pct: number;
			max_total_live_exposure_pct: number;
			max_live_position_margin_usdt: number;
			max_live_limited_cap_usdt: number;
			starting_capital_usdt: number;
			max_demo_position_margin_usdt: number;
			max_demo_single_trade_risk_pct: number;
			max_futures_leverage_live: number;
			max_futures_leverage_demo: number;
			max_open_positions_live: number;
			max_open_positions_demo: number;
			allow_short_demo: boolean;
			allow_short_live: boolean;
			kill_switch_enabled_live: boolean;
			kill_switch_enabled_demo: boolean;
			correlation_guard_enabled: boolean;
			max_sector_concentration_pct: number;
			max_portfolio_beta: number;
			live_family_min_samples: number;
			live_family_min_win_rate_pct: number;
		}>,
	) =>
		post<{ updated: Record<string, any>; risk: Record<string, any> }>(
			"/settings/risk",
			body,
		),
	updateSymbolFilter: (body: { mode: string; symbols: string[] }) =>
		post<{ mode: string; symbols: string[] }>("/settings/symbols", body),
	updateEquities: (tickers: string[]) =>
		post<{ observed_equities: string[] }>("/settings/equities", { tickers }),
	updateTradingConfig: (
		body: Partial<{
			default_stop_loss_pct: number;
			default_take_profit_pct: number;
			max_position_hold_hours: number;
			slippage_bps: number;
			execution_fee_pct: number;
			auto_adopt_enabled: boolean;
			trail_enabled: boolean;
			trail_atr_mult: number;
			trail_pct: number;
			breakeven_trigger_pct: number;
			exchange_tpsl_enabled: boolean;
			default_notional_usdt: number;
			vol_sizing_enabled: boolean;
			vol_target_risk_usdt: number;
			vol_target_pct: number;
			vol_kelly_cap_fraction: number;
			persona_sizing_enabled: boolean;
			active_persona: string;
			persona_learning_enabled: boolean;
			persona_learning_min_samples: number;
			persona_progressive_unlock_enabled: boolean;
			persona_unlock_step_samples: number;
			persona_pyramid_enabled: boolean;
			persona_pyramid_add_trigger_pct: number;
			persona_auto_advance_enabled: boolean;
			sweep_use_optuna: boolean;
			sweep_n_trials: number;
			sweep_optuna_timeout_sec: number;
			sweep_top_k: number;
			market_aware_exits: Partial<{
				enabled: boolean;
				min_hold_hours_before_market_exit: number;
				max_signal_age_min: number;
				urgent_exit_score: number;
				urgent_exit_max_pnl_pct: number;
				tighten_stop_score: number;
				confidence_collapse_score: number;
				tighten_atr_mult: number;
				tighten_pct: number;
			}>;
		}>,
	) =>
		post<{
			updated: Record<string, any>;
			trading: Record<string, any>;
			persona_rollout?: Record<string, any>;
		}>(
			"/settings/trading",
			body,
		),

	setAutonomousLearning: (enabled: boolean) =>
		post<{ old: boolean; new: boolean }>("/settings/autonomous-learning", {
			enabled,
		}),
	setBypassDistressGate: (enabled: boolean) =>
		post<{ old: boolean; new: boolean }>("/settings/bypass-distress-gate", {
			enabled,
		}),
	setExchangeMarkets: (body: {
		spot: boolean;
		usdt_futures: boolean;
		paper_execution_mode?: string;
		confirm_exchange_divergence?: boolean;
	}) => post<Record<string, any>>("/settings/exchange-markets", body),
	setPrimaryExchange: (exchange_id: string) =>
		post<{
			exchange_id: string;
			"exchange.name": string;
			"exchange.paper_execution_mode": string;
			"market_data.ccxt_exchange": string;
			"risk.allowed_exchange": string;
		}>("/settings/primary-exchange", { exchange_id }),
	updateRuntimeConfig: (
		body: Partial<{
			scheduler_enabled: boolean;
			git_enabled: boolean;
			auto_reload: boolean;
		}>,
	) => post<{ updated: Record<string, any> }>("/settings/runtime", body),
	updateEconomicsConfig: (
		body: Partial<{
			enabled: boolean;
			budgets_shadow_enabled: boolean;
			prompt_guardrail_detection_enabled: boolean;
		}>,
	) =>
		post<{ updated: Record<string, any>; economics: Record<string, any> }>(
			"/settings/economics",
			body,
		),
	updateAppConfig: (
		body: Partial<{
			auto_open_browser: boolean;
			equity_curve_mode: string;
			bind_host: string;
		}>,
	) => post<{ updated: Record<string, any> }>("/settings/app", body),
	getRemoteControlInterfaces: () =>
		fetchJson<{
			interfaces: Array<{
				name: string;
				address: string;
				netmask: string;
				is_up: boolean;
			}>;
		}>("/settings/remote-control/interfaces"),
	updateRemoteControl: (body: {
		enabled?: boolean;
		bind_hosts?: string[];
		require_token?: boolean;
		api_token?: string;
	}) =>
		post<{
			updated: Record<string, any>;
			remote_control: {
				enabled: boolean;
				bind_hosts: string[];
				require_token: boolean;
			};
			restart_required: boolean;
		}>("/settings/remote-control", body),
	setRemoteControlToken,
	updatePiDaemonConfig: (
		body: Partial<{
			enabled: boolean;
			debounce_sec: number;
			cooldown_sec: number;
			heartbeat_sec: number;
		}>,
	) => post<{
		updated: Record<string, any>;
		persisted: true;
		applied: true;
		runtime_status: SettingsResponse["pi_daemon_runtime"] & {
			configured: boolean;
			blocker: string | null;
		};
	}>("/settings/pi_daemon", body),
	updateSerenityConfig: (
		body: Partial<{
			enabled: boolean;
			auto_promote: boolean;
			min_score_for_pi: number;
			max_hold_days: number;
		}>,
	) => post<{ updated: Record<string, any> }>("/settings/serenity", body),
	updateControllerConfig: (
		body: Partial<{
			shadow_enabled: boolean;
			observe_interval_hours: number;
			min_samples_gate: number;
			max_step: number;
		}>,
	) => post<{ updated: Record<string, any> }>("/settings/controller", body),
	updateLearningConfig: (
		body: Partial<{
			contribution_evaluation_enabled: boolean;
			lifecycle_automation_enabled: boolean;
			paper_influence_enabled: boolean;
			paper_influence_paths: string[];
			auto_refreeze: boolean;
			evaluation_interval_minutes: number;
			paper_active_allocation_pct: number;
		}>,
	) =>
		post<{ updated: Record<string, any>; learning: Record<string, any> }>(
			"/settings/learning",
			body,
		),
	updateOperatorConfig: (body: Partial<{ loop_enabled: boolean }>) =>
		post<{ updated: Record<string, any> }>("/settings/operator", body),
	updateGuardConfig: (
		body: Partial<{
			enabled: boolean;
			enforce_in_paper_ranker: boolean;
			auto_promote_enabled: boolean;
			min_matching_trades: number;
			max_score_penalty: number;
		}>,
	) => post<{ updated: Record<string, any> }>("/settings/guard", body),
	updateRegimeConfig: (
		body: Partial<{
			enabled: boolean;
			short_enabled: boolean;
			bias_threshold: number;
			min_hold_periods: number;
		}>,
	) => post<{ updated: Record<string, any> }>("/settings/regime", body),
	updateHealthConfig: (
		body: Partial<{
			enabled: boolean;
			alert_via_telegram: boolean;
			alert_via_windows_toast: boolean;
			max_agent_failure_ratio: number;
			llm_unavailable_alert_after: number;
			market_data_max_staleness_min: number;
			supervisor_max_restarts: number;
			supervisor_restart_window_sec: number;
			supervisor_backoff_cap_sec: number;
			self_heal_enabled: boolean;
			self_heal_min_interval_sec: number;
			self_heal_max_per_hour: number;
			self_heal_code_drift: boolean;
			self_heal_market_data: boolean;
			self_heal_port_cleanup: boolean;
			require_llm_at_startup: boolean;
			startup_gate_max_retries: number;
			startup_gate_retry_delay_sec: number;
			auto_resume_enabled: boolean;
			auto_resume_interval_sec: number;
			startup_behavior_gate_enabled: boolean;
			startup_gate_recheck_interval_sec: number;
		}>,
	) => post<{ updated: Record<string, any> }>("/settings/health-config", body),
	updateRetentionConfig: (
		body: Partial<{
			agent_runs_days: number;
			runtime_sessions_days: number;
			tool_calls_days: number;
			run_steps_days: number;
			market_snapshots_days: number;
			portfolio_snapshots_days: number;
			opportunity_ttl_hours: number;
			jsonl_max_mb: number;
		}>,
	) => post<{ updated: Record<string, any> }>("/settings/retention", body),
	updateTelegramConfig: (
		body: Partial<{
			enabled: boolean;
			allowed_chat_ids: number[];
			notify_trade_fills: boolean;
			notify_daily_summary: boolean;
			daily_summary_hour_cst: number;
			dnd_enabled: boolean;
			dnd_start_hour: number;
			dnd_end_hour: number;
		}>,
	) => post<{ updated: Record<string, any> }>("/settings/telegram", body),
	updateWebhookConfig: (
		body: Partial<{
			enabled: boolean;
			url: string;
			secret: string;
			notify_trade_fills: boolean;
			notify_alerts: boolean;
			notify_daily_summary: boolean;
			timeout_sec: number;
		}>,
	) => post<{ updated: Record<string, any> }>("/settings/webhook", body),
	updateUpdateConfig: (body: Partial<{ enabled: boolean; branch: string }>) =>
		post<{ enabled: boolean; branch: string }>("/settings/update-config", body),

	// Self-upgrade (on-demand code update)
	getUpdateStatus: () =>
		fetchJson<SoftwareUpdateStatus>("/update/status"),
	checkForUpdate: () =>
		post<SoftwareUpdateStatus>("/update/check"),
	applyUpdate: () =>
		post<{
			applied: boolean;
			reason?: string;
			old_commit?: string;
			new_commit?: string;
			deps_reinstalled?: boolean;
			restart_required?: boolean;
			message?: string;
		}>("/update/apply"),

	// Data
	getAgentRuns: () => fetchJson<any[]>("/agent-runs"),
	getAgentRunsInWindow: (days: number, limit = 5_000) => {
		const qs = new URLSearchParams();
		qs.set("days", String(days));
		qs.set("limit", String(limit));
		return fetchJson<any[]>(`/agent-runs?${qs.toString()}`);
	},
	getAgentRunToolCalls: (runId: string) =>
		fetchJson<import("../types").AgentRunToolCall[]>(`/agent-runs/${runId}/tool-calls`),
	// #72 fix: the per-step reasoning trace backend endpoint exists at
	// /api/agent-runs/{run_id}/steps (server/routes/data.py:236). The
	// client method was missing so AgentActivity was displaying the
	// "not yet available" placeholder. Frontend surface can now consume it.
	getAgentRunSteps: (runId: string) =>
		fetchJson<import("../types").AgentRunStep[]>(`/agent-runs/${runId}/steps`),
	getAgentCatalog: () =>
		fetchJson<{ agents: { name: string; side_effect_class: string; cost_class: string; gate_check: boolean; runnable?: boolean; blocked_reason?: string | null }[] }>("/agents/catalog"),
	startAgentRun: (agent_name: string) =>
		post("/agent-runs/start", { agent_name }),
	getDreamingSummary: () =>
		fetchJson<DreamingSummary>("/dreaming/summary"),
	getOpportunities: () => fetchJson<Opportunity[]>("/opportunities"),
	getMarketOpportunityInstruments: () =>
		fetchJson<MarketOpportunityInstrument[]>("/market/opportunity-instruments"),
	getMarketOpportunities: (symbol: string, marketType: string, limit = 500) => {
		const params = new URLSearchParams({
			symbol,
			market_type: marketType,
			limit: String(limit),
		});
		return fetchJson<Opportunity[]>(`/market/opportunities?${params}`);
	},
	getProposals: () => fetchJson<any[]>("/proposals"),
	getProposal: (id: string) => fetchJson<any>(`/proposals/${id}`),
	approveProposal: (id: string) => post(`/proposals/${id}/approve-paper`),
	rejectProposal: (id: string) => post(`/proposals/${id}/reject`),
	getProposalRiskDecision: (id: string) =>
		fetchJson<{
			proposal_id: string;
			status: string;
			reason_code: string | null;
			decision: any;
			preflight_decision?: any;
		}>(
			`/proposals/${id}/risk-decision`,
		),
	getOrderRiskDecision: (id: string) =>
		fetchJson<{
			order_id: string;
			proposal_id: string | null;
			risk_decision_id: string | null;
			status: string;
			reason_code: string | null;
			decision: any;
		}>(
			`/orders/${id}/risk-decision`,
		),
	getTrades: (opts: TradesQuery = {}) => {
		const q = new URLSearchParams();
		if (opts.entry_plan_id != null) q.set("entry_plan_id", opts.entry_plan_id);
		const qs = q.toString();
		return fetchJson<MarketTrade[]>(qs ? `/trades?${qs}` : "/trades");
	},
	exportTradesCsv: () => `${BASE}/trades/export?fmt=csv`,
	getPositions: (opts: PositionsQuery = {}) => {
		const q = new URLSearchParams();
		if (opts.status != null) q.set("status", opts.status);
		if (opts.limit != null) q.set("limit", String(opts.limit));
		const qs = q.toString();
		return fetchJson<Position[]>(qs ? `/positions?${qs}` : "/positions");
	},
	getPosition: (id: string) => fetchJson<Position>(`/positions/${encodeURIComponent(id)}`),
	getPositionsReconciliation: () =>
		fetchJson<import("../types").PositionsReconciliation>(
			"/positions/reconciliation",
		),
	getPortfolio: () => fetchJson<import("../types").PortfolioAccount>("/portfolio"),
	getUtaState: () => fetchJson<Record<string, any>>("/uta/state"),
	getStrategies: () => fetchJson<any[]>("/strategies"),
	// #95: removed dead setStrategyVenue() — it PATCHed /strategies/{id}/venue, a route
	// deleted with the rules engine (Position C); no backend handler, no TSX caller.
	getKnowledge: (type = "research_note") =>
		fetchJson<KnowledgeItem[]>(`/knowledge?type=${encodeURIComponent(type)}`),
	getAllKnowledge: (offset = 0, limit = 100, search = "", status = "active") => {
		const q = new URLSearchParams({ type: "all", offset: String(offset), limit: String(limit), status });
		if (search.trim()) q.set("q", search.trim());
		return fetchJson<KnowledgeCatalogResponse>(`/knowledge?${q.toString()}`);
	},
	getStrategyMemory: () => fetchJson<any[]>("/knowledge/strategy-memory"),
	getDreamingLogs: () => fetchJson<any[]>("/knowledge/dreaming-logs"),
	getPostTradeReviews: () =>
		fetchJson<any[]>("/knowledge?type=post_trade_review"),
	getKnowledgeContent: (id: string) =>
		fetchJson<{ id: string; content: string; error?: string }>(
			`/knowledge/${id}/content`,
		),

	// Prediction Market Lens endpoints
	getPredictionOpportunities: () =>
		fetchJson<import("../types").PredictionMarket[]>(
			"/prediction/opportunities",
		),
	getPredictionStatus: () =>
		fetchJson<import("../types").PredictionStatus>("/prediction/status"),
	getPredictionMarkets: () =>
		fetchJson<import("../types").PredictionMarket[]>("/prediction/markets"),
	getPredictionRuns: () =>
		fetchJson<import("../types").PredictionObserverRun[]>("/prediction/runs"),
	refreshPrediction: () => post("/prediction/refresh"),
	getPredictionEvents: (category?: string) =>
		fetchJson<import("../types").PredictionEvent[]>(
			`/prediction/events${category ? `?category=${category}` : ""}`,
		),
	getPredictionEventDetail: (id: string) =>
		fetchJson<{
			event: import("../types").PredictionEvent;
			markets: import("../types").PredictionMarketDetail[];
		}>(`/prediction/events/${id}`),
	getPredictionMarketDetail: (id: string) =>
		fetchJson<import("../types").PredictionMarketDetail>(
			`/prediction/markets/${id}/detail`,
		),
	getPredictionMarketHistory: (id: string) =>
		fetchJson<{
			source: string;
			points: import("../types").ProbabilitySnapshot[];
		}>(`/prediction/markets/${id}/history`),
	getPredictionMarketClaims: (id: string) =>
		fetchJson<import("../types").PredictionClaim[]>(
			`/prediction/markets/${id}/claims`,
		),
	getPredictionPreferences: () =>
		fetchJson<import("../types").PredictionPreferences>(
			"/prediction/preferences",
		),
	// #86 fix: backend is @router.patch("/preferences") (prediction.py:232),
	// client was POSTing → 405 Method Not Allowed on every save. Use PATCH.
	updatePredictionPreferences: (body: { categories?: string[] }) =>
		fetchJson<{ status: string; categories: string[] }>("/prediction/preferences", {
			method: "PATCH",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(body),
		}),
	togglePrediction: (enabled: boolean) =>
		post<{ enabled: boolean }>("/prediction/toggle", { enabled }),

	// Serenity Lens endpoints (research-first investigation pipeline)
	getSerenityThemes: (status?: string) =>
		fetchJson<SerenityTheme[]>(
			status ? `/serenity/themes?status=${status}` : "/serenity/themes",
		),
	getSerenityTheme: (id: string) =>
		fetchJson<SerenityTheme & { tags: string[] }>(`/serenity/themes/${id}`),
	getSerenityThemeOverview: (id: string) =>
		fetchJson<SerenityThemeOverview>(`/serenity/themes/${id}/overview`),
	createSerenityTheme: (body: Partial<SerenityTheme> & { name: string }) =>
		post<{ id: string }>(`/serenity/themes`, body),
	getSerenityEvidence: (themeId?: string, limit = 100) =>
		fetchJson<SerenityEvidence[]>(
			`/serenity/evidence${themeId ? `?theme_id=${encodeURIComponent(themeId)}&limit=${limit}` : `?limit=${limit}`}`,
		),
	createSerenityEvidence: (
		body: Partial<SerenityEvidence> & { theme_id: string; title: string },
	) => post<{ id: string }>("/serenity/evidence", body as never),
	getSerenityOpportunities: (
		themeId?: string,
		status?: string,
		stage?: string,
		includeScores = true,
		limit = 100,
	) => {
		const qs = new URLSearchParams();
		if (themeId) qs.set("theme_id", themeId);
		if (status) qs.set("status", status);
		if (stage) qs.set("stage", stage);
		qs.set("include_scores", String(includeScores));
		qs.set("limit", String(limit));
		return fetchJson<SerenityOpportunity[]>(
			`/serenity/opportunities?${qs.toString()}`,
		);
	},
	createSerenityOpportunity: (
		body: Partial<SerenityOpportunity> & {
			theme_id: string;
			symbol: string;
			direction: string;
		},
	) => post<{ id: string }>("/serenity/opportunities", body as never),
	getSerenityOpportunity: (id: string, includeScores = true) =>
		fetchJson<SerenityOpportunity>(
			`/serenity/opportunities/${id}?include_scores=${includeScores}`,
		),
	scoreSerenityOpportunity: (id: string) =>
		fetchJson<Record<string, unknown>>(`/serenity/opportunities/${id}/score`),
	getSerenityResearchCycle: (id: string) =>
		fetchJson<SerenityResearchCycle>(
			`/serenity/opportunities/${id}/research-cycle`,
		),
	getSerenityOpportunityRiskGates: (id: string) =>
		fetchJson<SerenityRiskGate[]>(`/serenity/opportunities/${id}/risk-gates`),
	createSerenityOpportunityRiskGate: (
		id: string,
		body: {
			gate_name: string;
			status: string;
			metric: string;
			observed?: string | number | null;
			threshold?: string | number | null;
			message?: string | null;
		},
	) => post<{ id: string }>(`/serenity/opportunities/${id}/risk-gates`, body),
	getSerenityReplay: (id: string) =>
		fetchJson<Record<string, unknown>[]>(
			`/serenity/opportunities/${id}/replay`,
		),
	createSerenityReplaySnapshot: (
		id: string,
		body: {
			as_of: string;
			stage?: string;
			event_type?: string;
			event_title?: string;
			payload?: Record<string, unknown>;
		},
	) => post<{ id: string }>(`/serenity/opportunities/${id}/replay`, body),
	getSerenityPostMortems: (id: string) =>
		fetchJson<Record<string, unknown>[]>(
			`/serenity/opportunities/${id}/post-mortems`,
		),
	createSerenityPostMortem: (
		id: string,
		body: {
			outcome: string;
			summary: string;
			failure_bucket?: string | null;
			score_delta?: number | null;
			attribution?: string | null;
			created_by?: string;
		},
	) => post<{ id: string }>(`/serenity/opportunities/${id}/post-mortems`, body),

	// Charts
	// `before` (unix seconds) requests the page of candles strictly older than that
	// time — used for scroll-back history paging. 0 = latest window.
	getOhlcv: (
		symbol: string,
		timeframe = "1h",
		limit = 100,
		before = 0,
		exchange = "",
		marketType = "spot",
	) => {
		const params = new URLSearchParams({
			symbol,
			timeframe,
			limit: String(limit),
			...(before > 0 ? { before: String(before) } : {}),
			...(exchange ? { exchange } : {}),
			...(marketType !== "spot" ? { market_type: marketType } : {}),
		});
		return fetchJson<OhlcvCandle[]>(`/market/ohlcv?${params}`);
	},
	getOhlcvWithMeta: (
		symbol: string,
		timeframe = "1h",
		limit = 100,
		marketType = "spot",
		exchange = "",
	) => {
		const params = new URLSearchParams({
			symbol,
			timeframe,
			limit: String(limit),
			...(exchange ? { exchange } : {}),
			...(marketType !== "spot" ? { market_type: marketType } : {}),
		});
		return fetchJson<OhlcvResult>(
			`/market/ohlcv?${params}`,
			undefined,
			(body, headers) => decodeOhlcvResponse(body, headers, timeframe),
		);
	},
	getPortfolioHistory: (days?: number) =>
		fetchJson<any[]>(`/market/portfolio-history${days ? `?days=${days}` : ""}`),
	getAccountEquityHistory: (days?: number, maxPoints = 500, signal?: AbortSignal) =>
		fetchJson<AccountEquityHistory>(
			`/market/account-equity-history?${new URLSearchParams({ ...(days ? { days: String(days) } : {}), max_points: String(maxPoints) })}`,
			{ signal },
		),

	// Capital events (deposit/withdraw tracking)
	getNetDeposits: () =>
		fetchJson<{ net_deposits_usdt: number; first_event_at: string | null }>(
			"/capital/net-deposits",
		),
	getCapitalEvents: (params?: {
		limit?: number;
		before_detected_at?: string;
		before_id?: string;
	}) => {
		const qs = new URLSearchParams();
		if (params?.limit !== undefined) qs.set("limit", String(params.limit));
		if (params?.before_detected_at) qs.set("before_detected_at", params.before_detected_at);
		if (params?.before_id) qs.set("before_id", params.before_id);
		const suffix = qs.toString() ? `?${qs.toString()}` : "";
		return fetchJson<{
			events: any[];
			total: number;
			has_more: boolean;
			next_cursor: { detected_at: string; id: string } | null;
		} | any[]>(`/capital/events${suffix}`);
	},
	createCapitalEvent: (body: {
		broker_id: string;
		event_type: string;
		amount_usdt: number;
		note?: string;
	}) => post<any>("/capital/events", body),

	// Local paper account (available only in Paper internal / No exchange mode)
	getPaperAccount: () => fetchJson<any>("/settings/paper-account"),
	creditPaperAccount: (amount_usdt: number, note?: string) =>
		post<any>("/settings/paper-account/credit", { amount_usdt, note }),
	debitPaperAccount: (amount_usdt: number, note?: string) =>
		post<any>("/settings/paper-account/debit", { amount_usdt, note }),
	setPaperAccountBalance: (cash_balance_usdt: number, note?: string) =>
		post<any>("/settings/paper-account/set-balance", {
			cash_balance_usdt,
			note,
		}),
	closeAllPaperPositions: () => post<any>("/settings/paper-account/close-all"),
	closePaperSpotPositions: () =>
		post<any>("/settings/paper-account/close-spot"),

	// Manual single-position close (human actor, paper/demo only; live returns 409).
	// order_type: 'market' (immediate) | 'limit' (rest exit_limit_price locally)
	closePosition: (
		positionId: string,
		opts: {
			order_type?: "market" | "limit";
			limit_price?: number;
			reason?: string;
		} = {},
	) =>
		post<{
			closed?: boolean;
			status?: string;
			order_id?: string;
			close_order_id?: string;
			exchange_order_id?: string;
			already_pending?: boolean;
			pending_limit_exit?: boolean;
			position_id: string;
			close_reason?: string;
			limit_price?: number;
			error?: string;
		}>(`/positions/${encodeURIComponent(positionId)}/close`, {
			order_type: opts.order_type ?? "market",
			limit_price: opts.limit_price ?? null,
			reason: opts.reason ?? null,
		}),

	// Audit / Activity
	getAuditEvents: (limit = 50) =>
		fetchJson<any[]>(`/audit/events?limit=${limit}`),

	// Market snapshots (watchlist)
	getMarketSnapshots: (symbol?: string, limit = 10) =>
		fetchJson<MarketSnapshot[]>(
			`/market/snapshots${symbol ? `?symbol=${encodeURIComponent(symbol)}&limit=${limit}` : `?limit=${limit}`}`,
		),
	getMarketMicrostructure: (symbol: string, exchange = "", marketType = "spot") =>
		fetchJson<MarketMicrostructure>(
			`/market/microstructure?symbol=${encodeURIComponent(symbol)}&exchange=${encodeURIComponent(exchange)}&market_type=${encodeURIComponent(marketType)}`,
		),
	getDefaultExchange: () => fetchJson<{ exchange: string }>("/config/default-exchange"),

	// Macro indicators (VIX, DXY, SPY, BTC dominance, risk regime)
	getMacro: () => fetchJson<{ available: boolean; data: any }>("/market/macro"),
	getCollectorHealth: () =>
		fetchJson<import("../types").CollectorHealthReport>(
			"/market/collector-health",
		),
	getDataCoverage: () =>
		fetchJson<import("../types").MarketDataCoverageReport>(
			"/market/data-coverage",
		),

	// News Intelligence
	getNews: (limit = 50) => fetchJson<MarketNewsItem[]>(`/news?limit=${limit}`),
	getNewsItem: (id: string) => fetchJson<any>(`/news/${id}`),
	analyzeNews: (id: string) => post<any>(`/news/${id}/analyze`),
	createNews: (body: {
		title: string;
		source?: string;
		summary?: string;
		symbols_mentioned?: string[];
		sentiment?: string;
	}) => post<any>("/news", body),
	updateNewsConfig: (body: any) => post<any>("/settings/news", body),

	// Command Center
	sendCommand: (input: string, context?: Record<string, string>) =>
		post<any>("/command", { input, context }),
	sendOperatorCommand: (
		input: string,
		params?: { context?: Record<string, string>; sessionId?: string },
	) =>
		post<any>("/operator/command", {
			input,
			context: params?.context,
			session_id: params?.sessionId,
		}),
	previewOperatorCommand: (body: { input: string; sessionId?: string }) =>
		post<any>("/operator/command-preview", {
			input: body.input,
			session_id: body.sessionId,
		}),
	setOperatorActiveBranch: (sessionId: string, selectedBy = "command-center") =>
		post<any>("/operator/active-branch", {
			session_id: sessionId,
			selected_by: selectedBy,
		}),
	createOperatorBranch: (body: {
		title?: string;
		kind?: string;
		user_input?: string;
		parent_session_id?: string;
		activate?: boolean;
		selected_by?: string;
	}) => post<any>("/operator/branches", body),
	getOperatorSessions: () => fetchJson<any[]>("/operator/sessions"),
	getOperatorSession: (id: string) =>
		fetchJson<any>(`/operator/sessions/${encodeURIComponent(id)}`),
	getOperatorTasks: () => fetchJson<any[]>("/operator/tasks"),
	getOperatorCommands: (params?: {
		sessionId?: string;
		query?: string;
		limit?: number;
	}) => {
		const search = new URLSearchParams();
		if (params?.sessionId) search.set("session_id", params.sessionId);
		if (params?.query) search.set("query", params.query);
		if (params?.limit) search.set("limit", String(params.limit));
		const suffix = search.size > 0 ? `?${search.toString()}` : "";
		return fetchJson<any[]>(`/operator/commands${suffix}`);
	},
	getOperatorExtensions: (params?: { status?: string; limit?: number }) => {
		const search = new URLSearchParams();
		if (params?.status) search.set("status", params.status);
		if (params?.limit) search.set("limit", String(params.limit));
		const suffix = search.size > 0 ? `?${search.toString()}` : "";
		return fetchJson<any[]>(`/operator/extensions${suffix}`);
	},
	getOperatorContext: (body: {
		sessionId?: string;
		profile?: string;
		sections?: string[];
	}) =>
		post<any>("/operator/internal/context/query", {
			session_id: body.sessionId,
			profile: body.profile,
			sections: body.sections,
		}),
	getOperatorRecommendations: () =>
		fetchJson<any[]>("/operator/recommendations"),
	getOperatorEvents: (params?: {
		sessionId?: string;
		taskId?: string;
		limit?: number;
	}) => {
		const search = new URLSearchParams();
		if (params?.sessionId) search.set("session_id", params.sessionId);
		if (params?.taskId) search.set("task_id", params.taskId);
		if (params?.limit) search.set("limit", String(params.limit));
		const suffix = search.size > 0 ? `?${search.toString()}` : "";
		return fetchJson<any[]>(`/operator/events${suffix}`);
	},

	// --- Serenity Research Workbench ---
	getSerenityStatus: () => fetchJson<any>("/serenity/status"),
	createSerenityRun: (body: {
		mode?: string;
		triggerType?: string;
		brief?: string;
		saveToProfile?: boolean;
		idempotencyKey?: string;
	}) => post<any>("/serenity/runs", body),
	getSerenityRuns: (params?: { status?: string; limit?: number }) => {
		const search = new URLSearchParams();
		if (params?.status) search.set("status", params.status);
		if (params?.limit) search.set("limit", String(params.limit));
		const suffix = search.size > 0 ? `?${search.toString()}` : "";
		return fetchJson<any>(`/serenity/runs${suffix}`);
	},
	getSerenityRunDetail: (runId: string) =>
		fetchJson<any>(`/serenity/runs/${runId}`),
	retrySerenityRun: (runId: string) =>
		post<any>(`/serenity/runs/${runId}/retry`, {}),
	getSerenityProfile: () => fetchJson<any>("/serenity/profile"),
	updateSerenityProfile: (body: any) =>
		fetchJson<any>("/serenity/profile", {
			method: "PATCH",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(body),
		}),
	parseSerenityBrief: (brief: string) =>
		post<any>("/serenity/profile/parse", { brief }),
	updateSerenityThemeStatus: (themeId: string, status: string) =>
		fetchJson<any>(`/serenity/themes/${themeId}/status`, {
			method: "PATCH",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ status }),
		}),
	createSerenityThemeFeedback: (themeId: string, action: string, value?: any) =>
		post<any>(`/serenity/themes/${themeId}/feedback`, { action, value }),
	createSerenityThemeDeepDive: (themeId: string, brief?: string) =>
		post<any>(`/serenity/themes/${themeId}/deep-dive`, { brief }),
	cancelSerenityRun: (runId: string) =>
		post<any>(`/serenity/runs/${runId}/cancel`, {}),
	promoteSerenityTheme: (themeId: string) =>
		post<any>(`/serenity/themes/${themeId}/promote`, {}),
	getSerenityThemeDetail: (themeId: string) =>
		fetchJson<any>(`/serenity/themes/${themeId}/detail`),
	getSerenityThemeClaims: (themeId: string) =>
		fetchJson<any>(`/serenity/themes/${themeId}/claims`),
	getSerenityRunArtifacts: (runId: string) =>
		fetchJson<any>(`/serenity/runs/${runId}/artifacts`),
	getSereniyClaims: (params?: {
		run_id?: string;
		price_relevant?: boolean;
	}) => {
		const qs = new URLSearchParams();
		if (params?.run_id) qs.set("run_id", params.run_id);
		if (params?.price_relevant !== undefined)
			qs.set("price_relevant", String(params.price_relevant));
		return fetchJson<any>(`/serenity/claims?${qs}`);
	},

	// Universe Generator endpoints
	getUniverseCurrent: () =>
		fetchJson<UniverseCurrentResponse>("/universe/current"),
	getEntryWorkbench: (opts: EntryWorkbenchQuery = {}) => {
		const q = new URLSearchParams();
		if (opts.limit != null) q.set("limit", String(opts.limit));
		if (opts.plan_status != null) q.set("plan_status", opts.plan_status);
		const qs = q.toString();
		return fetchJson<EntryWorkbenchResponse>(qs ? `/universe/workbench?${qs}` : "/universe/workbench");
	},
	getEntryPlan: (id: string) =>
		fetchJson<EntryPlan>(`/universe/entry-plans/${encodeURIComponent(id)}`),
	getPlanningHistory: (opts: PlanningHistoryQuery = {}) => {
		const q = new URLSearchParams();
		if (opts.limit != null) q.set("limit", String(opts.limit));
		if (opts.before != null) q.set("before", opts.before);
		const qs = q.toString();
		return fetchJson<PlanningHistoryResponse>(qs ? `/universe/planning-history?${qs}` : "/universe/planning-history");
	},
	getUniverseSnapshots: (opts: SnapshotsQuery = {}) => {
		const q = new URLSearchParams();
		if (opts.market_type != null) q.set("market_type", opts.market_type);
		if (opts.limit != null) q.set("limit", String(opts.limit));
		const qs = q.toString();
		return fetchJson<UniverseSnapshot[]>(qs ? `/universe/snapshots?${qs}` : "/universe/snapshots");
	},
	getUniverseSnapshot: (id: string) =>
		fetchJson<operations["get_snapshot_api_universe_snapshots__snapshot_id__get"]["responses"][200]["content"]["application/json"]>(`/universe/snapshots/${id}`),
	getCandidate: (id: string) =>
		fetchJson<CandidateDetail>(
			`/universe/candidates/${encodeURIComponent(id)}`,
		),
	refreshUniverse: () =>
		post<operations["trigger_refresh_api_universe_refresh_post"]["responses"][200]["content"]["application/json"]>("/universe/refresh"),
	getUniversePolicy: () => fetchJson<operations["get_current_policy_api_universe_policy_get"]["responses"][200]["content"]["application/json"]>("/universe/policy"),
	getUniverseRegistryStats: () =>
		fetchJson<operations["registry_stats_api_universe_registry_stats_get"]["responses"][200]["content"]["application/json"]>(
			"/universe/registry/stats",
		),
	getUniverseCalibration: () =>
		fetchJson<import("../types").CalibrationResponse>("/universe/calibration"),
	updateUniverseConfig: (
		body: Partial<{
			enabled: boolean;
			refresh_interval_min: number;
			max_candidates: number;
			min_volume_24h_usdt: number;
			max_spread_bps: number;
			min_depth_10bps_usd: number;
			snapshot_ttl_enabled: boolean;
			snapshot_ttl_hours: number;
			entry_ttl_sec: number;
			candidate_max_continuous_age_sec: number;
			blacklist: string[];
			fallback_symbols: string[];
			market_type: string;
		}>,
	) => post<any>("/settings/universe", body),
	getCandidateSelectionStatus: () =>
		fetchJson<CandidateSelectionStatus>("/settings/universe/candidate-selection"),
	updateFastTriggerConfig: (
		body: Partial<{
			mode: "off" | "shadow" | "execute";
			max_setups: number;
			authorization_ttl_minutes: number;
		}>,
	) => post<any>("/settings/fast-trigger", body),

	// Market data config
	updateMarketDataConfig: (
		body: Partial<{
			ccxt_exchange: string;
			ccxt_fallback: string;
			confirm_exchange_divergence: boolean;
			ohlcv_cache_ttl_min: number;
			enable_yfinance_macro: boolean;
			observed_stocks: string[];
			observed_stock_tokens: string[];
			observed_xstocks: string[];
			observed_prediction_markets: string[];
			// Round 2 macro signals
			enable_long_short_ratio: boolean;
			enable_fear_greed: boolean;
			enable_coingecko_top: boolean;
			coingecko_top_n: number;
			derivatives_history_hours: number;
			enable_hyperliquid: boolean;
			enable_defillama: boolean;
		}>,
	) =>
		post<{ updated: Record<string, any>; market_data: Record<string, any> }>(
			"/settings/market-data",
			body,
		),

	// Historical data config
	updateHistoricalDataConfig: (
		body: Partial<{
			enabled: boolean;
			max_bars_per_pair: number;
			default_months: number;
			nightly_backfill_enabled: boolean;
			nightly_backfill_hour_cst: number;
			min_bars_for_backtest: number;
			active_universe_backfill_enabled: boolean;
			active_universe_backfill_interval_minutes: number;
			active_universe_backfill_max_pairs_per_run: number;
		}>,
	) => post<any>("/settings/historical_data", body),

	// Event trigger config
	updateEventTriggerConfig: (
		body: Partial<{
			enabled: boolean;
			price_move_pct_threshold: number;
			volume_surge_multiplier: number;
			watch_universe_members: boolean;
		}>,
	) => post<any>("/settings/event_triggers", body),

	// Network / proxy config
	updateNetworkConfig: (
		body: Partial<{
			proxy_protocol: string;
			proxy_host: string;
			proxy_port: number;
			enforce: boolean;
			no_proxy: string;
			proxy_url: string;
		}>,
	) =>
		post<{ updated: Record<string, any>; network: Record<string, any> }>(
			"/settings/network",
			body,
		),
	testNetworkProxy: () =>
		post<{ reachable: boolean; latency_ms: number; error: string | null }>(
			"/settings/network/test",
		),

	// Trade Analytics panel. Every request MUST scope to a single
	// `execution_mode` so operator surfaces never combine populations (#1939).
	getTradeStats: (execution_mode?: string) =>
		fetchJson<operations["get_trade_stats_api_analytics_trade_stats_get"]["responses"][200]["content"]["application/json"]>(_withMode("/analytics/trade-stats", execution_mode)),
	getStrategyKpis: (execution_mode?: string) =>
		fetchJson<operations["get_strategy_kpis_api_analytics_strategy_kpis_get"]["responses"][200]["content"]["application/json"]>(_withMode("/analytics/strategy-kpis", execution_mode)),
	getEquityCurve: (days?: number, execution_mode?: string) => {
		const q = new URLSearchParams();
		if (days != null) q.set("days", String(days));
		if (execution_mode) q.set("execution_mode", execution_mode);
		const qs = q.toString();
		return fetchJson<components["schemas"]["EquityCurveResponse"]>(
			`/analytics/equity-curve${qs ? `?${qs}` : ""}`,
		);
	},
	getTradeInsights: (execution_mode?: string) =>
		fetchJson<operations["get_trade_insights_api_analytics_trade_insights_get"]["responses"][200]["content"]["application/json"]>(_withMode("/analytics/trade-insights", execution_mode)),
	getThesisLineage: (params: {
		days?: number;
		position_id?: string;
		order_id?: string;
		proposal_id?: string;
		opportunity_id?: string;
		limit?: number;
	}) => {
		const q = new URLSearchParams();
		if (params.days != null) q.set("days", String(params.days));
		if (params.position_id) q.set("position_id", params.position_id);
		if (params.order_id) q.set("order_id", params.order_id);
		if (params.proposal_id) q.set("proposal_id", params.proposal_id);
		if (params.opportunity_id) q.set("opportunity_id", params.opportunity_id);
		if (params.limit != null) q.set("limit", String(params.limit));
		return fetchJson<{ records: ThesisLineageRecord[] }>(
			`/analytics/thesis-lineage?${q.toString()}`,
		);
	},
	getGoLiveScorecard: () =>
		fetchJson<GoLiveScorecard>("/analytics/go-live-scorecard"),
	getFilterController: () =>
		fetchJson<{
			// Cells whose overrestrictive_suspect verdict has held across N+ consecutive
			// controller runs — the human-review signal. Not a Learning hypothesis.
			persistent_recommendations: Array<{
				filter_name: string;
				scanner_family: string;
				direction: string | null;
				market_type: string | null;
				horizon: string | null;
				filter_version: string | null;
				consecutive_suspect_runs: number;
				runs_analyzed: number;
				first_suspect_at: string;
				last_suspect_at: string;
				latest_error_signal: number | null;
				latest_reasoning: string;
				latest_run_id: string;
			}>;
			persistent_config: {
				min_consecutive_runs: number;
				max_runs_lookback: number;
			};
			config_availability: "available" | "unavailable";
			runs_available: number;
			last_run_at: string | null;
			schedule: { enabled: boolean; observe_interval_hours: number };
			empty_reason: "config_unavailable" | "controller_disabled" | "no_controller_runs" | "insufficient_run_history" | "no_persistent_signals" | null;
		}>("/analytics/filter-controller/persistent"),
	getCostSummary: (execution_mode?: string) =>
		fetchJson<operations["get_cost_summary_api_analytics_cost_summary_get"]["responses"][200]["content"]["application/json"]>(_withMode("/analytics/cost-summary", execution_mode)),
	getConfidenceCalibration: (execution_mode?: string) =>
		fetchJson<CalibrationReport>(
			_withMode("/analytics/confidence-calibration", execution_mode),
		),
	getReasoningFeedback: (execution_mode?: string) =>
		fetchJson<ReasoningFeedback>(
			_withMode("/analytics/reasoning-feedback", execution_mode),
		),

	getStrategyPerformance: (execution_mode?: string) =>
		fetchJson<operations["get_strategy_performance_api_analytics_strategy_performance_get"]["responses"][200]["content"]["application/json"]>(_withMode("/analytics/strategy-performance", execution_mode)),
	getSerenityPipeline: () => fetchJson<any>("/serenity/pipeline"),

	// Generic helpers for pages that need simple get/post
	get: <T = any>(path: string) => fetchJson<T>(path),
	post: <T = any>(path: string, body?: unknown) => post<T>(path, body),

	// Data export
	getExportCatalog: () =>
		fetchJson<{
			datasets: Array<{
				table: string;
				filename: string;
				presets: string[];
				row_count: number;
			}>;
		}>("/export/catalog"),
	downloadExport: async (body: {
		preset?: string;
		include_datasets?: string[];
		include_config?: boolean;
	}) => {
		const res = await fetch(`${BASE}/export/download`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(body),
		});
		if (!res.ok) throw new Error(await res.text());
		const blob = await res.blob();
		const cd = res.headers.get("Content-Disposition") ?? "";
		const match = cd.match(/filename="([^"]+)"/);
		return { blob, filename: match?.[1] ?? "aletheia_export.zip" };
	},
};

export type TradeStatsSource = components["schemas"]["TradeStatsSource"];
export type TradeStatsFamily = components["schemas"]["TradeStatsFamily"];
export type HoldBucket = components["schemas"]["HoldBucket"];
export type TradeStats = components["schemas"]["TradeStatsResponse"];
export type EquityPoint = components["schemas"]["InsightEquityPoint"];
export type DrawdownPoint = components["schemas"]["InsightDrawdownPoint"];
export type DirectionStats = components["schemas"]["DirectionStats"];
export type ProfitFactorData = components["schemas"]["ProfitFactorStats"];
export type DayStats = components["schemas"]["DayOfWeekStats"];
export type HourStats = components["schemas"]["HourStats"];
export type PnlBin = components["schemas"]["DistributionBin"];
export type RegimeStats = components["schemas"]["RegimeStats"];
export type StreakData = components["schemas"]["StreakStats"];
export type HoldPnlPoint = components["schemas"]["HoldPnlPoint"];
export type HourDayCell = components["schemas"]["HeatmapCell"];
export type TradeFrequencyDay = components["schemas"]["TradeFrequencyDay"];
export type SymbolDirectionEntry = components["schemas"]["SymbolDirectionStats"];
export type HoldDurationBucket = components["schemas"]["HoldDurationStats"];
export type RMultipleBin = components["schemas"]["DistributionBin"];
export type ExitReasonEntry = components["schemas"]["ExitReasonStats"];
export type TradeInsights = components["schemas"]["TradeInsightsResponse"];

// ── Trade Insights (comprehensive charts) ────────────────────────────────────


export interface AccountEquityHistory {
	source: "local_paper_ledger" | "exchange_account_snapshot";
	state: "available" | "unavailable";
	points: { time: string; equity_usdt: number }[];
	adjusted_points: {
		time: string;
		raw_equity_usdt: number;
		cumulative_net_flow_usdt: number;
		adjusted_equity_usdt: number;
	}[];
	capital_flow_markers: {
		id: string;
		time: string;
		broker_id: string;
		event_type: "deposit" | "withdraw";
		amount_usdt: number;
		signed_amount_usdt: number;
		change_type:
			| "deposit"
			| "withdraw"
			| "initial_funding"
			| "manual_credit"
			| "manual_debit"
			| "manual_set_balance";
		balance_before_usdt: number | null;
		balance_after_usdt: number | null;
		note: string;
	}[];
	adjustment: {
		state: "available" | "unavailable";
		method: "visible_window_net_flow_adjusted_usdt";
		scope: "aggregate_tracked_account_equity";
		baseline_at: string | null;
		note: string;
	};
	as_of: string | null;
}


export interface GoLiveCriterion {
	key: string;
	label: string;
	score: number;
	weight: number;
	weighted_contribution: number;
	detail: string;
	status: "pass" | "warning" | "fail";
}

export interface GoLiveScorecard {
	composite_score: number;
	traffic_light: "red" | "yellow" | "green";
	criteria: GoLiveCriterion[];
	blockers: string[];
	computed_at: string;
}

export type CostSummary = components["schemas"]["CostSummaryResponse"];

export interface CalibrationBucket {
	label: string;
	conviction_range: string;
	sample_size: number;
	win_count: number;
	actual_win_rate: number;
	expected_win_rate: number;
	calibration_error: number;
	reliable: boolean;
}

export interface CalibrationReport {
	generated_at?: string;
	window_days?: number;
	total_executed?: number;
	reliable_sample_count?: number;
	calibration_state?: "available" | "insufficient_data" | string;
	buckets: CalibrationBucket[];
	ece: number | null;
	ece_grade: string;
	overall_win_rate?: number;
	overconfident_buckets?: string[];
	underconfident_buckets?: string[];
	calibration_summary?: string;
	status?: string;
}

export interface ReasoningFeedback {
	generated_at?: string;
	window_days?: number;
	sample_proposals?: number;
	sample_executed?: number;
	approval_outcomes?: {
		pattern_label: string;
		win_rate: number;
		sample_size: number;
		avg_pnl: number;
		avg_conviction: number;
	}[];
	rejection_patterns?: {
		pattern_label: string;
		rejection_rate: number;
		rejection_count: number;
	}[];
	recommended_avoid?: string[];
	recommended_pursue?: string[];
	high_conviction_win_rate?: number | null;
	low_conviction_win_rate?: number | null;
	status?: string;
}
