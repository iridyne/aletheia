import { describe, expect, it } from "vitest";
import CSS from "../index.css?raw";
import SETTINGS_SRC from "../pages/Settings.tsx?raw";

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
 */
const RUNTIME_INJECTED = new Set(["--settings-nav-width"]);

/** Custom properties referenced via var(), with or without a fallback. */
function referencedTokens(source: string): Set<string> {
	const refs = [...stripComments(source).matchAll(/var\(\s*(--[a-z0-9-]+)/gi)].map((m) => m[1]);
	return new Set(refs.filter((t) => !RUNTIME_INJECTED.has(t)));
}

describe("CSS custom properties", () => {
	it("defines every token index.css references", () => {
		const declared = declaredTokens();
		const missing = [...referencedTokens(CSS)].filter((t) => !declared.has(t));
		expect(missing, "var(--x) in index.css with no declaration").toEqual([]);
	});

	it("defines every token Settings.tsx references", () => {
		// Settings.tsx has the most inline styles of any page (68 blocks), so it is
		// the likeliest source of a token typo reaching an operator-facing surface.
		const declared = declaredTokens();
		const missing = [...referencedTokens(SETTINGS_SRC)].filter((t) => !declared.has(t));
		expect(missing, "var(--x) in Settings.tsx with no declaration").toEqual([]);
	});

	it("declares the base tokens the danger surfaces rely on", () => {
		const declared = declaredTokens();
		for (const token of ["--red", "--bg", "--text", "--muted", "--space-3", "--fs-base"]) {
			expect(declared.has(token), `${token} must be declared`).toBe(true);
		}
	});

	it("keeps the Live authority wall on themed classes, not inline colours", () => {
		// Regression guard: this wall previously inlined light-theme hex fallbacks
		// (`var(--color-danger-bg, #fff5f5)`), painting near-white on a dark app.
		const start = SETTINGS_SRC.indexOf("function LiveUnlockWarningWall");
		expect(start, "LiveUnlockWarningWall not found").toBeGreaterThan(-1);
		// Slice to the function's own closing brace. `\n}` alone would stop at the
		// destructured-props block, truncating the body before its JSX.
		const wall = SETTINGS_SRC.slice(start, SETTINGS_SRC.indexOf("\n}\n", start));
		expect(wall).toContain("danger-zone");
		// Comments are stripped: the fix documents the old hex values in prose, and
		// that explanation should not read as a violation.
		const code = stripComments(wall);
		expect(code, "hardcoded colour in the Live authority wall").not.toMatch(/#[0-9a-fA-F]{3,6}/);
		expect(code, "inline style in the Live authority wall").not.toMatch(/style=\{\{/);
	});
});
