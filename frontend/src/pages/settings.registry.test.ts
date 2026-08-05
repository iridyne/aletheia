import { describe, expect, it } from "vitest";
import { HEALTH_MONITORING_SECTION, WEBHOOK_SECTION } from "../features/settings/health/healthState";
import { MODEL_PROVIDERS_SECTION } from "../features/settings/model-providers/modelProviderState";
import { ADVANCED_SECTION } from "../features/settings/tabs/AdvancedTab";
import {
	INFERENCE_ADMISSION_SECTION,
	LEARNING_SECTION,
	LLM_COST_SECTION,
	PERMISSION_SECTION,
	REGIME_SECTION,
	SCHEDULER_SECTION,
	SERENITY_SECTION,
} from "../features/settings/tabs/AgentModelsTab";
import {
	APP_SECTION,
	DATA_EXPORT_SECTION,
	DATA_RETENTION_SECTION,
	NETWORK_PROXY_SECTION,
	SOFTWARE_UPDATES_SECTION,
} from "../features/settings/tabs/RuntimeSystemTab";
import {
	EQUITIES_SECTION,
	MACRO_SIGNALS_SECTION,
	MARKET_DATA_SECTION,
	NEWS_FEEDS_SECTION,
	PREDICTION_MARKETS_SECTION,
} from "../features/settings/tabs/SignalsTab";
import {
	EVENT_TRIGGERS_SECTION,
	RISK_LIMITS_SECTION,
	TRADABLE_SYMBOLS_SECTION,
	UNIVERSE_SECTION,
} from "../features/settings/tabs/RiskTab";
import { TELEGRAM_SECTION } from "../features/settings/telegram/telegramState";
import CSS from "../index.css?raw";
import SRC from "./Settings.tsx?raw";

const EXTRACTED_DESCRIPTORS = [
	{ symbol: "PERMISSION_SECTION", descriptor: PERMISSION_SECTION },
	{ symbol: "LEARNING_SECTION", descriptor: LEARNING_SECTION },
	{ symbol: "REGIME_SECTION", descriptor: REGIME_SECTION },
	{ symbol: "SERENITY_SECTION", descriptor: SERENITY_SECTION },
	{ symbol: "SCHEDULER_SECTION", descriptor: SCHEDULER_SECTION },
	{ symbol: "MODEL_PROVIDERS_SECTION", descriptor: MODEL_PROVIDERS_SECTION },
	{ symbol: "INFERENCE_ADMISSION_SECTION", descriptor: INFERENCE_ADMISSION_SECTION },
	{ symbol: "LLM_COST_SECTION", descriptor: LLM_COST_SECTION },
	{ symbol: "HEALTH_MONITORING_SECTION", descriptor: HEALTH_MONITORING_SECTION },
	{ symbol: "TELEGRAM_SECTION", descriptor: TELEGRAM_SECTION },
	{ symbol: "WEBHOOK_SECTION", descriptor: WEBHOOK_SECTION },
	{ symbol: "ADVANCED_SECTION", descriptor: ADVANCED_SECTION },
	{ symbol: "APP_SECTION", descriptor: APP_SECTION },
	{ symbol: "DATA_RETENTION_SECTION", descriptor: DATA_RETENTION_SECTION },
	{ symbol: "DATA_EXPORT_SECTION", descriptor: DATA_EXPORT_SECTION },
	{ symbol: "SOFTWARE_UPDATES_SECTION", descriptor: SOFTWARE_UPDATES_SECTION },
	{ symbol: "NETWORK_PROXY_SECTION", descriptor: NETWORK_PROXY_SECTION },
	{ symbol: "MARKET_DATA_SECTION", descriptor: MARKET_DATA_SECTION },
	{ symbol: "MACRO_SIGNALS_SECTION", descriptor: MACRO_SIGNALS_SECTION },
	{ symbol: "NEWS_FEEDS_SECTION", descriptor: NEWS_FEEDS_SECTION },
	{ symbol: "PREDICTION_MARKETS_SECTION", descriptor: PREDICTION_MARKETS_SECTION },
	{ symbol: "EQUITIES_SECTION", descriptor: EQUITIES_SECTION },
	{ symbol: "UNIVERSE_SECTION", descriptor: UNIVERSE_SECTION },
	{ symbol: "EVENT_TRIGGERS_SECTION", descriptor: EVENT_TRIGGERS_SECTION },
	{ symbol: "TRADABLE_SYMBOLS_SECTION", descriptor: TRADABLE_SYMBOLS_SECTION },
	{ symbol: "RISK_LIMITS_SECTION", descriptor: RISK_LIMITS_SECTION },
] as const;

const EXTRACTED_COMPONENTS = [
	{
		component: "AgentModelsTab",
		descriptors: [
			PERMISSION_SECTION,
			LEARNING_SECTION,
			REGIME_SECTION,
			SERENITY_SECTION,
			SCHEDULER_SECTION,
			MODEL_PROVIDERS_SECTION,
			INFERENCE_ADMISSION_SECTION,
			LLM_COST_SECTION,
		],
	},
	{
		component: "HealthNotificationsTab",
		descriptors: [HEALTH_MONITORING_SECTION, TELEGRAM_SECTION, WEBHOOK_SECTION],
	},
	{ component: "AdvancedTab", descriptors: [ADVANCED_SECTION] },
	{
		component: "RuntimeSystemTab",
		descriptors: [
			APP_SECTION,
			DATA_RETENTION_SECTION,
			DATA_EXPORT_SECTION,
			SOFTWARE_UPDATES_SECTION,
			NETWORK_PROXY_SECTION,
		],
	},
	{
		component: "SignalsTab",
		descriptors: [
			MARKET_DATA_SECTION,
			MACRO_SIGNALS_SECTION,
			NEWS_FEEDS_SECTION,
			PREDICTION_MARKETS_SECTION,
			EQUITIES_SECTION,
		],
	},
	{
		component: "RiskTab",
		descriptors: [
			UNIVERSE_SECTION,
			EVENT_TRIGGERS_SECTION,
			TRADABLE_SYMBOLS_SECTION,
			RISK_LIMITS_SECTION,
		],
	},
] as const;

const EXTRACTED_BY_SYMBOL = new Map<string, { id: string; title: string }>(
	EXTRACTED_DESCRIPTORS.map(({ symbol, descriptor }) => [symbol, descriptor]),
);

/**
 * Enforces the SETTINGS_INDEX invariant that was previously documented in a
 * comment only ("MAINTAIN INVARIANT: every <Section> must have a matching entry
 * below"). That comment had already drifted three times, leaving sections
 * unsearchable and their `?section=` deep links dead (#632).
 *
 * Settings.tsx is inspected as source text rather than imported: importing it
 * pulls in the whole page module graph, and this project's vitest runs in a node
 * environment with no DOM. Comparing the two lists is enough. `?raw` is Vite's
 * built-in text import (declared by vite/client), so this needs no extra deps.
 */

/** Titles registered in SETTINGS_INDEX. */
function registeredTitles(): string[] {
	const start = SRC.indexOf("const SETTINGS_INDEX");
	const end = SRC.indexOf("/** Anchor id for a registered section title");
	expect(start, "SETTINGS_INDEX declaration not found").toBeGreaterThan(-1);
	expect(end, "sectionSlug helper not found").toBeGreaterThan(start);
	const block = SRC.slice(start, end);
	return [...block.matchAll(/\{ title: (?:'([^']+)'|([A-Z_]+)\.title)/g)].map(
		(match) => match[1] ?? EXTRACTED_BY_SYMBOL.get(match[2])?.title ?? "",
	);
}

/** Slugs registered in SETTINGS_INDEX. */
function registeredSlugs(): string[] {
	const start = SRC.indexOf("const SETTINGS_INDEX");
	const end = SRC.indexOf("/** Anchor id for a registered section title");
	const block = SRC.slice(start, end);
	return [...block.matchAll(/slug: (?:'([^']+)'|([A-Z_]+)\.id)/g)].map(
		(match) => match[1] ?? EXTRACTED_BY_SYMBOL.get(match[2])?.id ?? "",
	);
}

/** Titles actually rendered by a <Section> in the page body. */
function renderedTitles(): string[] {
	const body = SRC.slice(SRC.indexOf("export default function Settings"));
	const lines = body.split("\n");
	const out: string[] = [];
	for (let i = 0; i < lines.length; i++) {
		if (!/<Section\b/.test(lines[i])) continue;
		for (let k = i; k < Math.min(i + 5, lines.length); k++) {
			const m = lines[k].match(/title="([^"]*)"/);
			if (m) {
				out.push(m[1]);
				break;
			}
		}
	}
	for (const { component, descriptors } of EXTRACTED_COMPONENTS) {
		if (body.includes(`<${component}`)) {
			out.push(...descriptors.map((descriptor) => descriptor.title));
		}
	}
	return out;
}

describe("SETTINGS_INDEX registry", () => {
	it("registers every rendered Section, so search can find it", () => {
		const registered = new Set(registeredTitles());
		const unregistered = renderedTitles().filter((t) => !registered.has(t));
		expect(unregistered, "rendered <Section> missing from SETTINGS_INDEX").toEqual([]);
	});

	it("has no registry entry without a rendered Section", () => {
		// A stale entry produces a search result that navigates nowhere.
		const rendered = new Set(renderedTitles());
		const stale = registeredTitles().filter((title) => !rendered.has(title));
		expect(stale, "SETTINGS_INDEX entry with no rendered <Section>").toEqual([]);
	});

	it("gives every entry a slug, so ?section= resolves", () => {
		// Before #632 only 1 of 30 sections had an anchor id.
		expect(registeredSlugs().length).toBe(registeredTitles().length);
	});

	it("keeps slugs unique, so a deep link is unambiguous", () => {
		const slugs = registeredSlugs();
		expect(slugs.length).toBe(new Set(slugs).size);
	});

	it("uses url-safe slugs", () => {
		const bad = registeredSlugs().filter(
			(slug) => !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(slug),
		);
		expect(bad, "slug is not url-safe lowercase-kebab").toEqual([]);
	});

	it("preserves the pre-existing ?section=universe deep link", () => {
		// This was the only working anchor before #632; other surfaces may link to it.
		expect(registeredSlugs()).toContain("universe");
	});

	it("keeps deep-linked section headings below the fixed header", () => {
		expect(CSS).toMatch(
			/\.settings-section\s*\{[^}]*scroll-margin-top:\s*calc\(var\(--header-height\)\s*\+\s*var\(--space-4\)\)/,
		);
	});

	it("resolves ids through the registry, not per-call-site id props", () => {
		// A stray id= on <Section> would bypass the registry and reintroduce drift.
		const body = SRC.slice(SRC.indexOf("export default function Settings"));
		expect(body).not.toMatch(/<Section\s+[^>]*\bid=/);
	});
});
