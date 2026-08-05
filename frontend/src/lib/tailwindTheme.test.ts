import { describe, expect, it } from "vitest";
import CSS from "../index.css?raw";

/**
 * Locks the Tailwind v4 `@theme` values that reproduce the utility classes this
 * project used to hand-write in index.css.
 *
 * Adopting Tailwind was meant to be mechanical: the ~26 in-use utilities that
 * were deleted (.flex, .gap-2, .mb-3, .text-sm …) had to keep their exact
 * computed values, because they are referenced from every page. Two of them are
 * easy to get wrong:
 *
 *   - Font sizes. Tailwind's defaults are LARGER than this project's terminal
 *     density (.text-sm 0.875rem vs 0.78rem), so `--text-*: initial` clears the
 *     namespace and the sizes are re-declared. They intentionally reproduce the
 *     OLD values, not the --fs-* scale: snapping to --fs-* would shrink text on
 *     ~158 call sites, which is a visual change needing owner review.
 *
 *   - Spacing. NOT overridden, because Tailwind's --spacing (0.25rem) already
 *     makes gap-2 resolve to 8px, matching the old var(--space-2).
 *
 * Without this test, a Tailwind upgrade that changed a default could resize text
 * across the app with no build or type error.
 */

function themeBlock(): string {
	const start = CSS.indexOf("@theme {");
	expect(start, "@theme block not found in index.css").toBeGreaterThan(-1);
	return CSS.slice(start, CSS.indexOf("}", start));
}

describe("Tailwind @theme", () => {
	it("clears Tailwind's font-size namespace before redeclaring it", () => {
		// Without this, Tailwind's defaults survive alongside ours and .text-base
		// (1rem) would leak in at nearly 25% larger than this project's body size.
		expect(themeBlock()).toMatch(/--text-\*:\s*initial/);
	});

	it("preserves the exact font sizes the deleted utilities had", () => {
		const theme = themeBlock();
		// These three values are load-bearing: 122 call sites use text-sm and 35
		// use text-xs. Changing them here silently resizes text on every page.
		expect(theme).toMatch(/--text-sm:\s*0?\.78rem/);
		expect(theme).toMatch(/--text-xs:\s*0?\.7rem/);
		expect(theme).toMatch(/--text-2xs:\s*0?\.68rem/);
	});

	it("does not override spacing, so gap/margin utilities keep their values", () => {
		// --space-1..4 are 4/8/12/16px and Tailwind's 1..4 steps already match.
		// Declaring --spacing here would be a silent app-wide layout change.
		expect(themeBlock()).not.toMatch(/--spacing:/);
	});

	it("imports Tailwind without a prefix", () => {
		// A prefix (`@import 'tailwindcss' prefix(tw)`) would make every generated
		// class `tw:flex`, so the existing className strings across all pages would
		// stop resolving. The project deliberately adopted the unprefixed names.
		expect(CSS).toMatch(/@import\s+['"]tailwindcss['"]\s*;/);
		expect(CSS).not.toMatch(/@import\s+['"]tailwindcss['"]\s+prefix\(/);
	});

	it("keeps project-semantic classes that Tailwind has no equivalent for", () => {
		// These were deliberately NOT deleted with the layout utilities.
		for (const cls of [".mono", ".text-muted", ".text-error", ".col-full", ".text-pre-wrap"]) {
			expect(CSS, `${cls} must remain hand-written`).toContain(`${cls} `);
		}
	});

	it("no longer hand-writes utilities Tailwind now generates", () => {
		// Duplicate definitions are the failure mode this migration removed: the
		// same class name defined twice, with the later one silently winning.
		expect(CSS).not.toMatch(/^\.flex\s*\{/m);
		expect(CSS).not.toMatch(/^\.gap-2\s*\{/m);
		expect(CSS).not.toMatch(/^\.mb-3\s*\{/m);
		expect(CSS).not.toMatch(/^\.text-sm\s*\{/m);
	});
});
