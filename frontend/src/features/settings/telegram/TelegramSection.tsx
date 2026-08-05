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

interface TelegramDraftInputs {
	chatIdsInput: string;
	summaryHourInput: string;
	dndStartInput: string;
	dndEndInput: string;
}

type TelegramDraftError = {
	ok: false;
	field: keyof TelegramDraftInputs;
	error: string;
};

interface TelegramDraftValue {
	allowed_chat_ids: number[];
	daily_summary_hour_cst: number;
	dnd_start_hour: number;
	dnd_end_hour: number;
}

type TelegramDraftResult =
	| { ok: true; value: TelegramDraftValue }
	| TelegramDraftError;

function parseHourDraft(
	field: keyof TelegramDraftInputs,
	label: string,
	rawValue: string,
): { ok: true; value: number } | TelegramDraftError {
	const value = rawValue.trim();
	if (!/^\d+$/.test(value)) {
		return {
			ok: false,
			field,
			error: `${label} must be a whole hour from 0 to 23.`,
		};
	}
	const hour = Number(value);
	if (!Number.isInteger(hour) || hour < 0 || hour > 23) {
		return {
			ok: false,
			field,
			error: `${label} must be a whole hour from 0 to 23.`,
		};
	}
	return { ok: true, value: hour };
}

export function parseTelegramDraft({
	chatIdsInput,
	summaryHourInput,
	dndStartInput,
	dndEndInput,
}: TelegramDraftInputs): TelegramDraftResult {
	const chatIds: number[] = [];
	for (const rawToken of chatIdsInput.split(",")) {
		const token = rawToken.trim();
		if (!token) continue;
		if (!/^[+-]?\d+$/.test(token)) {
			return {
				ok: false,
				field: "chatIdsInput",
				error: "Each Chat ID must be a complete signed integer.",
			};
		}
		const chatId = Number(token);
		if (!Number.isSafeInteger(chatId)) {
			return {
				ok: false,
				field: "chatIdsInput",
				error: "Each Chat ID must be a safe signed integer.",
			};
		}
		chatIds.push(chatId);
	}

	const summaryHour = parseHourDraft(
		"summaryHourInput",
		"Summary hour",
		summaryHourInput,
	);
	if (!summaryHour.ok) return summaryHour;
	const dndStart = parseHourDraft(
		"dndStartInput",
		"DND start hour",
		dndStartInput,
	);
	if (!dndStart.ok) return dndStart;
	const dndEnd = parseHourDraft(
		"dndEndInput",
		"DND end hour",
		dndEndInput,
	);
	if (!dndEnd.ok) return dndEnd;

	return {
		ok: true,
		value: {
			allowed_chat_ids: chatIds,
			daily_summary_hour_cst: summaryHour.value,
			dnd_start_hour: dndStart.value,
			dnd_end_hour: dndEnd.value,
		},
	};
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
	const [summaryHourInput, setSummaryHourInput] = useState("8");
	const [dndEnabled, setDndEnabled] = useState(false);
	const [dndStartInput, setDndStartInput] = useState("23");
	const [dndEndInput, setDndEndInput] = useState("7");
	const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(
		null,
	);

	useEffect(() => {
		const telegram = (settings?.telegram as TelegramConfig | undefined) ?? {};
		setEnabled(!!telegram.enabled);
		setChatIdsInput((telegram.allowed_chat_ids ?? []).join(", "));
		setNotifyFills(telegram.notify_trade_fills !== false);
		setNotifySummary(telegram.notify_daily_summary !== false);
		setSummaryHourInput(String(telegram.daily_summary_hour_cst ?? 8));
		setDndEnabled(!!telegram.dnd_enabled);
		setDndStartInput(String(telegram.dnd_start_hour ?? 23));
		setDndEndInput(String(telegram.dnd_end_hour ?? 7));
	}, [settings]);

	const draft = parseTelegramDraft({
		chatIdsInput,
		summaryHourInput,
		dndStartInput,
		dndEndInput,
	});
	const inputErrorId = "telegram-input-error";
	const summaryHourLabel = summaryHourInput.trim() || "--";

	const mutation = useMutation({
		mutationFn: () => {
			if (!draft.ok) throw new Error(draft.error);
			return api.updateTelegramConfig({
				enabled,
				notify_trade_fills: notifyFills,
				notify_daily_summary: notifySummary,
				dnd_enabled: dndEnabled,
				...draft.value,
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
							aria-invalid={!draft.ok && draft.field === "chatIdsInput"}
							aria-describedby={
								!draft.ok && draft.field === "chatIdsInput"
									? inputErrorId
									: undefined
							}
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
						label={`Daily PnL summary (${summaryHourLabel.padStart(2, "0")}:00 CST)`}
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
								value={summaryHourInput}
								onChange={(event) => {
									setSummaryHourInput(event.target.value);
									setMessage(null);
								}}
								aria-invalid={!draft.ok && draft.field === "summaryHourInput"}
								aria-describedby={
									!draft.ok && draft.field === "summaryHourInput"
										? inputErrorId
										: undefined
								}
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
								value={dndStartInput}
								onChange={(event) => {
									setDndStartInput(event.target.value);
									setMessage(null);
								}}
								aria-invalid={!draft.ok && draft.field === "dndStartInput"}
								aria-describedby={
									!draft.ok && draft.field === "dndStartInput"
										? inputErrorId
										: undefined
								}
							/>
						</label>
						<label>
							<span>DND end hour</span>
							<input
								type="number"
								min={0}
								max={23}
								value={dndEndInput}
								onChange={(event) => {
									setDndEndInput(event.target.value);
									setMessage(null);
								}}
								aria-invalid={!draft.ok && draft.field === "dndEndInput"}
								aria-describedby={
									!draft.ok && draft.field === "dndEndInput"
										? inputErrorId
										: undefined
								}
							/>
						</label>
					</div>
				)}
				{!draft.ok && (
					<div className="save-err-banner" id={inputErrorId} role="alert">
						{draft.error}
					</div>
				)}
				<div className="settings-action-row">
					<button
						onClick={() => mutation.mutate()}
						disabled={mutation.isPending || !draft.ok}
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
