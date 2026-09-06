import { describe, expect, it } from "vitest";
const CSS = Object.values(import.meta.glob("../**/*.css", { eager: true, query: "?raw", import: "default" })).join("\n");
import SETTINGS_SRC from "../pages/Settings.tsx?raw";

const SETTINGS_FEATURE_SOURCES = Object.entries(
	import.meta.glob("../features/settings/**/*.{ts,tsx}", {
		eager: true,
		query: "?raw",
		import: "default",
	}),
)
	.filter(([path]) => !path.includes(".test."))
	.map(([, source]) => source as string);
const SETTINGS_SOURCES = [SETTINGS_SRC, ...SETTINGS_FEATURE_SOURCES];

/**
 * Guards against the failure mode found in the Live-authority warning wall:
 * it styled itself with `var(--color-danger-bg, #fff5f5)`, but neither
 * `--color-danger-bg` nor `--color-danger` is defined anywhere in index.css.
 * CSS silently used the fallback, painting a near-white panel on this app's
 * near-black surface (`--bg: #0a0e12`) — on the most safety-critical control
 * in the product. No type checker, linter, or build step catches this: an
 * undefined custom property with a fallback is valid CSS.
 *
 * The design system already requires this check by hand ("新写的 var(--xxx) 在
 * :root 有定义（grep 确认）", ui_design_system.md §7). A hand-run grep did not
 * catch it, so it is asserted here instead.
 */

/** Custom properties declared anywhere in the stylesheet. */
function declaredTokens(): Set<string> {
	// Matches `--name:` in a declaration position (`:root`, `[data-theme]`, any rule).
	return new Set([...CSS.matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((m) => m[1]));
}

/** Strips comments so prose mentioning a token name is not read as a reference. */
function stripComments(source: string): string {
	return source.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/(^|[^:])\/\/[^\n]*/g, "$1 ");
}

/**
 * Custom properties injected at runtime from JS rather than declared in CSS.
 * `--settings-nav-width` is set by the Settings shell's inline style so the
 * resizable nav width can drive CSS; it is legitimately absent from :root.
 * `--charts-shell-height` is computed from window.innerHeight in Charts.tsx.
 * `--market-chart-grid-rows` is set per-render in PriceChart.tsx.
 */
const RUNTIME_INJECTED = new Set(["--settings-nav-width", "--charts-shell-height", "--market-chart-grid-rows"]);

/** Custom properties referenced via var(), with or without a fallback. */
function referencedTokens(source: string): Set<string> {
	const refs = [...stripComments(source).matchAll(/var\(\s*(--[a-z0-9-]+)/gi)].map((m) => m[1]);
	return new Set(refs.filter((t) => !RUNTIME_INJECTED.has(t)));
}

describe("CSS custom properties", () => {
	it("defines every token authored CSS references", () => {
		const declared = declaredTokens();
		const missing = [...referencedTokens(CSS)].filter((t) => !declared.has(t));
		expect(missing, "var(--x) in authored CSS with no declaration").toEqual([]);
	});

	it("defines every token the Settings implementation references", () => {
		const declared = declaredTokens();
		const missing = [...referencedTokens(SETTINGS_SOURCES.join("\n"))].filter(
			(t) => !declared.has(t),
		);
		expect(missing, "var(--x) in Settings modules with no declaration").toEqual([]);
	});

});
