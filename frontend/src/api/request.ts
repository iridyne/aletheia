export const BASE = "/api";
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

export function setRemoteControlToken(token: string): RemoteControlTokenStorageResult {
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
		const formatted = formatErrorValue(obj.detail);
		if (formatted) return formatted;
	} catch {
		/* not JSON, fall through */
	}
	if (body.length > 80) return `${status} error`;
	return body || `${status} error`;
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

export function post<T>(path: string, body?: unknown): Promise<T> {
	return fetchJson<T>(path, {
		method: "POST",
		body: body ? JSON.stringify(body) : undefined,
	});
}

export function put<T>(path: string, body?: unknown): Promise<T> {
	return fetchJson<T>(path, {
		method: "PUT",
		body: body ? JSON.stringify(body) : undefined,
	});
}
