import { useEffect, useState } from "react";
import { useMutation, type QueryClient } from "@tanstack/react-query";
import { api } from "../../../api/client";
import {
	NumberFieldGrid as NumGrid,
	SaveMessage as SaveMsg,
	Switch as Toggle,
	SwitchGrid as ToggleGrid,
	type BoolField,
	type NumField,
} from "../../../components/ui";
import { SettingsFeatureSection } from "../SettingsFeatureSection";
import { HEALTH_MONITORING_SECTION, WEBHOOK_SECTION } from "../health/healthState";
import { TelegramSection } from "../telegram/TelegramSection";

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : "Request failed";
}

function HealthCard({ settings, qc }: { settings?: Record<string, unknown>; qc: QueryClient }) {
	const health = settings?.health as Record<string, unknown> | undefined;
	const [vals, setVals] = useState<Record<string, unknown>>({});
	const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

	useEffect(() => {
		if (!health) return;
		setVals({ ...health });
	}, [settings]);

	const set = (key: string, value: unknown) => {
		setVals((previous) => ({ ...previous, [key]: value }));
		setMsg(null);
	};

	const mutation = useMutation({
		mutationFn: () => api.updateHealthConfig(vals),
		onSuccess: () => {
			setMsg({ ok: true, text: "✓ saved" });
			qc.invalidateQueries({ queryKey: ["settings"] });
		},
		onError: (error) =>
			setMsg({ ok: false, text: `✗ ${errorMessage(error)}` }),
	});

	if (!health) return null;

	const fields: NumField[] = [
		{ key: "max_agent_failure_ratio", label: "Max agent failure ratio", unit: "", min: 0, max: 1, step: 0.01 },
		{ key: "llm_unavailable_alert_after", label: "LLM alert after N failures", unit: "", min: 1, max: 100, step: 1, isInt: true },
		{ key: "market_data_max_staleness_min", label: "Market data staleness", unit: "min", min: 5, max: 1440, step: 1, isInt: true },
		{ key: "supervisor_max_restarts", label: "Supervisor max restarts", unit: "", min: 1, max: 50, step: 1, isInt: true },
		{ key: "supervisor_restart_window_sec", label: "Restart window", unit: "sec", min: 60, max: 86400, step: 60, isInt: true },
		{ key: "supervisor_backoff_cap_sec", label: "Backoff cap", unit: "sec", min: 5, max: 3600, step: 1, isInt: true },
		{ key: "self_heal_min_interval_sec", label: "Self-heal min interval", unit: "sec", min: 300, max: 86400, step: 60, isInt: true },
		{ key: "self_heal_max_per_hour", label: "Self-heal max per hour", unit: "", min: 0, max: 12, step: 1, isInt: true },
		{ key: "startup_gate_max_retries", label: "LLM gate retries", unit: "", min: 1, max: 10, step: 1, isInt: true },
		{ key: "startup_gate_retry_delay_sec", label: "LLM gate retry delay", unit: "sec", min: 5, max: 120, step: 5, isInt: true },
		{ key: "auto_resume_interval_sec", label: "Auto-resume probe interval", unit: "sec", min: 30, max: 3600, step: 30, isInt: true },
		{ key: "exchange_probe_interval_sec", label: "Exchange probe interval", unit: "sec", min: 10, max: 300, step: 10, isInt: true },
		{ key: "exchange_probe_failure_threshold", label: "Exchange fail threshold", unit: "", min: 1, max: 20, step: 1, isInt: true },
		{ key: "exchange_auto_resume_interval_sec", label: "Exchange resume interval", unit: "sec", min: 10, max: 600, step: 10, isInt: true },
		{ key: "exchange_auto_resume_success_threshold", label: "Exchange resume successes", unit: "", min: 1, max: 10, step: 1, isInt: true },
		{ key: "startup_gate_recheck_interval_sec", label: "Startup gate recheck interval", unit: "sec", min: 5, max: 3600, step: 5, isInt: true },
	];
	const toggles: BoolField[] = [
		{ key: "enabled", label: "Health monitoring" },
		{ key: "alert_via_telegram", label: "Telegram alerts" },
		{ key: "alert_via_windows_toast", label: "Windows toast alerts" },
		{ key: "require_llm_at_startup", label: "Require LLM at startup" },
		{ key: "startup_behavior_gate_enabled", label: "Startup behavior gate" },
		{ key: "self_heal_enabled", label: "Self-heal enabled" },
		{ key: "self_heal_code_drift", label: "Restart on code drift" },
		{ key: "self_heal_market_data", label: "Restart on stale market data" },
		{ key: "self_heal_port_cleanup", label: "Clean orphan port holders" },
		{ key: "auto_resume_enabled", label: "Auto-resume after LLM gate" },
	];

	return (
		<div className="card mb-0">
			<ToggleGrid fields={toggles} vals={vals} set={set} />
			<NumGrid fields={fields} vals={vals} set={set} />
			<div className="settings-action-row">
				<button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
					{mutation.isPending ? "Saving…" : "Save"}
				</button>
			</div>
			<SaveMsg msg={msg} />
		</div>
	);
}

interface WebhookConfig {
	enabled?: boolean;
	url?: string;
	notify_trade_fills?: boolean;
	notify_alerts?: boolean;
	notify_daily_summary?: boolean;
	timeout_sec?: number;
}

function WebhookCard({ settings, qc }: { settings?: Record<string, unknown>; qc: QueryClient }) {
	const webhook = (settings?.webhook as WebhookConfig | undefined) ?? {};
	const [enabled, setEnabled] = useState(false);
	const [url, setUrl] = useState("");
	const [secret, setSecret] = useState("");
	const [notifyFills, setNotifyFills] = useState(true);
	const [notifyAlerts, setNotifyAlerts] = useState(true);
	const [notifySummary, setNotifySummary] = useState(true);
	const [timeoutSec, setTimeoutSec] = useState(10);
	const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

	useEffect(() => {
		setEnabled(!!webhook.enabled);
		setUrl(webhook.url || "");
		setNotifyFills(webhook.notify_trade_fills !== false);
		setNotifyAlerts(webhook.notify_alerts !== false);
		setNotifySummary(webhook.notify_daily_summary !== false);
		setTimeoutSec(webhook.timeout_sec ?? 10);
	}, [settings]);

	const mutation = useMutation({
		mutationFn: () =>
			api.updateWebhookConfig({
				enabled,
				url,
				secret: secret || undefined,
				notify_trade_fills: notifyFills,
				notify_alerts: notifyAlerts,
				notify_daily_summary: notifySummary,
				timeout_sec: timeoutSec,
			}),
		onSuccess: () => {
			setMsg({ ok: true, text: "✓ saved" });
			setSecret("");
			qc.invalidateQueries({ queryKey: ["settings"] });
		},
		onError: (error) =>
			setMsg({ ok: false, text: `✗ ${errorMessage(error)}` }),
	});

	return (
		<div className="card mb-0">
			<div className="form-grid mb-3">
				<label className="col-full">
					<span>Webhook URL</span>
					<input
						value={url}
						placeholder="https://hooks.slack.com/services/..."
						onChange={(event) => {
							setUrl(event.target.value);
							setMsg(null);
						}}
						className="mono"
					/>
				</label>
				<label className="col-full">
					<span>HMAC secret (write-only)</span>
					<input
						type="password"
						value={secret}
						placeholder={webhook.url ? "••••••• (unchanged)" : "optional"}
						onChange={(event) => {
							setSecret(event.target.value);
							setMsg(null);
						}}
						className="mono"
					/>
				</label>
				<label>
					<span>Timeout (sec)</span>
					<input
						type="number"
						min={1}
						max={60}
						value={timeoutSec}
						onChange={(event) => {
							setTimeoutSec(Number.parseFloat(event.target.value) || 10);
							setMsg(null);
						}}
					/>
				</label>
			</div>
			<div className="settings-action-row settings-action-row-start">
				<Toggle checked={enabled} label="Webhook delivery" onChange={(value) => { setEnabled(value); setMsg(null); }} />
			</div>
			<div className="settings-action-row settings-action-row-start">
				<Toggle checked={notifyAlerts} label="Alert events" onChange={(value) => { setNotifyAlerts(value); setMsg(null); }} />
			</div>
			<div className="settings-action-row settings-action-row-start">
				<Toggle checked={notifyFills} label="Trade fill events" onChange={(value) => { setNotifyFills(value); setMsg(null); }} />
			</div>
			<div className="settings-action-row settings-action-row-start">
				<Toggle checked={notifySummary} label="Daily summary event" onChange={(value) => { setNotifySummary(value); setMsg(null); }} />
			</div>
			<div className="settings-action-row">
				<button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
					{mutation.isPending ? "Saving…" : "Save"}
				</button>
			</div>
			<SaveMsg msg={msg} />
			<p className="hint">
				Payload is signed with <code>X-Aletheia-Signature</code> (HMAC SHA-256)
				when a secret is set. Secret is write-only — leave blank to keep the
				current value.
			</p>
		</div>
	);
}

export function HealthNotificationsTab({
	settings,
	qc,
}: {
	settings?: Record<string, unknown>;
	qc: QueryClient;
}) {
	return (
		<div>
			<SettingsFeatureSection descriptor={HEALTH_MONITORING_SECTION}>
				<HealthCard settings={settings} qc={qc} />
			</SettingsFeatureSection>

			<TelegramSection settings={settings} qc={qc} />

			<SettingsFeatureSection descriptor={WEBHOOK_SECTION}>
				<WebhookCard settings={settings} qc={qc} />
			</SettingsFeatureSection>
		</div>
	);
}
