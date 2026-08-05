import type { SettingsFeatureDescriptor } from "../SettingsFeatureSection";

export const HEALTH_MONITORING_SECTION = {
	id: "health-monitoring",
	title: "Health Monitoring",
	tab: "health",
	description:
		"Thresholds that determine when the system raises internal health alerts.",
	searchTerms: ["alert thresholds", "self-heal"],
} satisfies SettingsFeatureDescriptor;

export const WEBHOOK_SECTION = {
	id: "webhook",
	title: "Webhook",
	tab: "health",
	description:
		"Push JSON events to an external URL (Slack, Discord, n8n, etc). Payloads are HMAC-signed when a secret is set.",
	searchTerms: ["slack", "discord", "n8n", "hmac"],
} satisfies SettingsFeatureDescriptor;
