import type { SettingsFeatureDescriptor } from "../SettingsFeatureSection";

export const TELEGRAM_SECTION = {
	id: "telegram",
	title: "Telegram",
	tab: "health",
	description:
		"Alert delivery when agents act or limits are breached. Token is stored in secrets (write-only via CLI).",
	searchTerms: ["bot", "chat id", "notification"],
} satisfies SettingsFeatureDescriptor;
