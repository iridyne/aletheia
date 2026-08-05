import { useEffect, useState } from "react";
import {
	useMutation,
	useQuery,
	type QueryClient,
} from "@tanstack/react-query";
import { api } from "../../../api/client";
import ConnectionStatus, {
	type ConnState,
} from "../../../components/ConnectionStatus";
import {
	SaveMessage as SaveMsg,
	Switch as Toggle,
} from "../../../components/ui";
import { SettingsFeatureSection } from "../SettingsFeatureSection";
import { TELEGRAM_SECTION } from "./telegramState";

interface TelegramConfig {
	enabled?: boolean;
	allowed_chat_ids?: number[];
	notify_trade_fills?: boolean;
	notify_daily_summary?: boolean;
	daily_summary_hour_cst?: number;
	dnd_enabled?: boolean;
	dnd_start_hour?: number;
	dnd_end_hour?: number;
}

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : "Request failed";
}

export function TelegramSection({
	settings,
	qc,
}: {
	settings?: Record<string, unknown>;
	qc: QueryClient;
}) {
	const { data: health } = useQuery({
		queryKey: ["telegram-health"],
		queryFn: api.getTelegramHealth,
		refetchInterval: 30_000,
	});
	const [enabled, setEnabled] = useState(false);
	const [chatIdsInput, setChatIdsInput] = useState("");
	const [notifyFills, setNotifyFills] = useState(true);
	const [notifySummary, setNotifySummary] = useState(true);
	const [summaryHour, setSummaryHour] = useState(8);
	const [dndEnabled, setDndEnabled] = useState(false);
	const [dndStart, setDndStart] = useState(23);
	const [dndEnd, setDndEnd] = useState(7);
	const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(
		null,
	);

	useEffect(() => {
		const telegram = (settings?.telegram as TelegramConfig | undefined) ?? {};
		setEnabled(!!telegram.enabled);
		setChatIdsInput((telegram.allowed_chat_ids ?? []).join(", "));
		setNotifyFills(telegram.notify_trade_fills !== false);
		setNotifySummary(telegram.notify_daily_summary !== false);
		setSummaryHour(telegram.daily_summary_hour_cst ?? 8);
		setDndEnabled(!!telegram.dnd_enabled);
		setDndStart(telegram.dnd_start_hour ?? 23);
		setDndEnd(telegram.dnd_end_hour ?? 7);
	}, [settings]);

	const mutation = useMutation({
		mutationFn: () => {
			const ids = chatIdsInput
				.split(",")
				.map((value) => parseInt(value.trim()))
				.filter((value) => !Number.isNaN(value));
			return api.updateTelegramConfig({
				enabled,
				allowed_chat_ids: ids,
				notify_trade_fills: notifyFills,
				notify_daily_summary: notifySummary,
				daily_summary_hour_cst: summaryHour,
				dnd_enabled: dndEnabled,
				dnd_start_hour: dndStart,
				dnd_end_hour: dndEnd,
			});
		},
		onSuccess: () => {
			setMessage({ ok: true, text: "✓ saved" });
			qc.invalidateQueries({ queryKey: ["settings"] });
		},
		onError: (error) =>
			setMessage({ ok: false, text: `✗ ${errorMessage(error)}` }),
	});

	let state: ConnState = "muted";
	let connectionText = "not configured";
	if (health) {
		if (health.reachable) {
			state = "green";
			connectionText = health.username ? `@${health.username}` : "reachable";
		} else if (health.error === "not_configured") {
			state = "muted";
		} else {
			state = "red";
			connectionText = `✗ ${health.error || "unreachable"}`;
		}
	}

	return (
		<SettingsFeatureSection descriptor={TELEGRAM_SECTION}>
			<div className="card mb-0">
				<div className="mb-3">
					<ConnectionStatus
						label="telegram bot"
						state={state}
						text={connectionText}
					/>
				</div>
				<div className="form-grid mb-3">
					<label className="col-full">
						<span>Allowed chat IDs</span>
						<input
							value={chatIdsInput}
							placeholder="123456789, 987654321"
							onChange={(event) => {
								setChatIdsInput(event.target.value);
								setMessage(null);
							}}
							className="mono"
						/>
					</label>
				</div>
				<div className="settings-action-row settings-action-row-start">
					<Toggle
						checked={enabled}
						label="Telegram delivery"
						onChange={(value) => {
							setEnabled(value);
							setMessage(null);
						}}
					/>
				</div>
				<div className="settings-action-row settings-action-row-start">
					<Toggle
						checked={notifyFills}
						label="Trade fill notifications"
						onChange={(value) => {
							setNotifyFills(value);
							setMessage(null);
						}}
					/>
				</div>
				<div className="settings-action-row settings-action-row-start">
					<Toggle
						checked={notifySummary}
						label={`Daily PnL summary (${String(summaryHour).padStart(2, "0")}:00 CST)`}
						onChange={(value) => {
							setNotifySummary(value);
							setMessage(null);
						}}
					/>
				</div>
				{notifySummary && (
					<div className="form-grid mb-3">
						<label>
							<span>Summary hour (CST)</span>
							<input
								type="number"
								min={0}
								max={23}
								value={summaryHour}
								onChange={(event) => {
									setSummaryHour(Number.parseInt(event.target.value, 10) || 8);
									setMessage(null);
								}}
							/>
						</label>
					</div>
				)}
				<div className="settings-action-row settings-action-row-start">
					<Toggle
						checked={dndEnabled}
						label="Do-not-disturb (suppress info alerts)"
						onChange={(value) => {
							setDndEnabled(value);
							setMessage(null);
						}}
					/>
				</div>
				{dndEnabled && (
					<div className="form-grid mb-3">
						<label>
							<span>DND start hour</span>
							<input
								type="number"
								min={0}
								max={23}
								value={dndStart}
								onChange={(event) => {
									setDndStart(Number.parseInt(event.target.value, 10) || 23);
									setMessage(null);
								}}
							/>
						</label>
						<label>
							<span>DND end hour</span>
							<input
								type="number"
								min={0}
								max={23}
								value={dndEnd}
								onChange={(event) => {
									setDndEnd(Number.parseInt(event.target.value, 10) || 7);
									setMessage(null);
								}}
							/>
						</label>
					</div>
				)}
				<div className="settings-action-row">
					<button
						onClick={() => mutation.mutate()}
						disabled={mutation.isPending}
					>
						{mutation.isPending ? "Saving…" : "Save"}
					</button>
				</div>
				<SaveMsg msg={message} />
				<p className="hint">
					Bot token is write-only — set via{" "}
					<code>aletheia secrets set telegram.bot_token</code>. Chat IDs are
					comma-separated Telegram user or group IDs.
				</p>
			</div>
		</SettingsFeatureSection>
	);
}
