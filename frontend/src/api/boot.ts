import type {
	CollectorHealthReport,
	InfraEvent,
	MarketSnapshot,
	SystemStatus,
} from "../types";
import { fetchJson, post } from "./request";

// Keep the always-mounted shell on a small transport module. Route-specific
// API methods remain in client.ts and are loaded with their page chunks.
export const bootApi = {
	getHealth: () =>
		fetchJson<{ status: string; issues: string[]; version: string }>("/health"),
	getReady: () =>
		fetchJson<{
			ok: boolean;
			version: string;
			pid: number;
			uptime_sec: number;
		}>("/ready"),
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
	getKillSwitch: () => fetchJson<{ active: boolean }>("/risk/kill-switch"),
	getRiskStatus: () => fetchJson<Record<string, any>>("/risk/status"),
	getAgentRuns: () => fetchJson<any[]>("/agent-runs"),
	getCollectorHealth: () =>
		fetchJson<CollectorHealthReport>("/market/collector-health"),
	getMarketSnapshots: (symbol?: string, limit = 10) =>
		fetchJson<MarketSnapshot[]>(
			`/market/snapshots${symbol ? `?symbol=${encodeURIComponent(symbol)}&limit=${limit}` : `?limit=${limit}`}`,
		),
	pause: () => post("/gateway/pause"),
	resume: () => post("/gateway/resume"),
	setPermissionMode: (mode: string) =>
		post("/settings/permission-mode", { mode }),
	setKillSwitch: (active: boolean, reason = "", confirmRelease = false) =>
		post<{ active: boolean }>("/risk/kill-switch", {
			active,
			reason,
			confirm_release: confirmRelease,
		}),
	startAgentRun: (agent_name: string) =>
		post("/agent-runs/start", { agent_name }),
};
