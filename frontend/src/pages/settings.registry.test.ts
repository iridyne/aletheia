import { describe, expect, it } from "vitest";
import SRC from "./Settings.tsx?raw";

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
	return [...block.matchAll(/\{ title: (?:'([^']+)'|MODEL_PROVIDERS_SECTION\.title)/g)].map(
		(m) => m[1] ?? "@model-providers",
	);
}

/** Slugs registered in SETTINGS_INDEX. */
function registeredSlugs(): string[] {
	const start = SRC.indexOf("const SETTINGS_INDEX");
	const end = SRC.indexOf("/** Anchor id for a registered section title");
	const block = SRC.slice(start, end);
	return [...block.matchAll(/slug: (?:'([^']+)'|MODEL_PROVIDERS_SECTION\.id)/g)].map(
		(m) => m[1] ?? "@model-providers",
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
		const stale = registeredTitles().filter((t) => t !== "@model-providers" && !rendered.has(t));
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
			(s) => s !== "@model-providers" && !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(s),
		);
		expect(bad, "slug is not url-safe lowercase-kebab").toEqual([]);
	});

	it("preserves the pre-existing ?section=universe deep link", () => {
		// This was the only working anchor before #632; other surfaces may link to it.
		expect(registeredSlugs()).toContain("universe");
	});

	it("resolves ids through the registry, not per-call-site id props", () => {
		// A stray id= on <Section> would bypass the registry and reintroduce drift.
		const body = SRC.slice(SRC.indexOf("export default function Settings"));
		expect(body).not.toMatch(/<Section\s+[^>]*\bid=/);
	});
});
