import { readFile } from "node:fs/promises";
import path from "node:path";
import { compile } from "tailwindcss";
import { beforeAll, describe, expect, it } from "vitest";
import CSS from "../index.css?raw";

const LEGACY_UTILITIES = [
	"flex", "flex-col", "flex-wrap", "flex-1",
	"gap-1", "gap-2", "gap-3", "gap-4",
	"items-center", "items-baseline", "justify-between", "justify-end",
	"mt-1", "mt-2", "mt-3", "mt-4",
	"mb-0", "mb-1", "mb-2", "mb-3", "mb-4",
	"m-0", "ml-auto", "pt-3", "w-auto", "w-full",
	"text-sm", "text-xs", "text-2xs",
];

let compiledCSS = "";

beforeAll(async () => {
	const base = process.cwd();
	const compiler = await compile(CSS, {
		base,
		loadStylesheet: async (id) => {
			const file = path.join(base, "node_modules", id);
			return { path: file, base: path.dirname(file), content: await readFile(file, "utf8") };
		},
	});
	compiledCSS = compiler.build([]);
});

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
 *   - Spacing. Fixed at 4px, because the old --space-1..4 utilities used fixed
 *     pixels. Tailwind's 0.25rem default would drift with browser root font size.
 *
 *   - Preflight. NOT imported, because its list reset removes existing bullets
 *     from Settings, Learning markdown, and the Live authority warning.
 *
 * Without this test, a Tailwind upgrade or import simplification could change
 * rendering across the app with no build or type error.
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

	it("preserves the exact fixed-pixel spacing the deleted utilities had", () => {
		// Using Tailwind's 0.25rem default would only equal 4px at a 16px root.
		expect(themeBlock()).toMatch(/--spacing:\s*4px/);
		expect(compiledCSS).toMatch(/\.gap-2\s*\{[^}]*gap:\s*calc\(var\(--spacing\)\s*\*\s*2\)/);
	});

	it("generates unprefixed utilities without Preflight", () => {
		// The blanket import includes preflight.css, whose list reset strips bullets
		// from existing UI. This project already owns its reset, so import only the
		// theme and utilities.
		expect(CSS).toMatch(
			/^\s*@import\s+['"]tailwindcss\/theme\.css['"]\s+layer\(theme\)/m,
		);
		expect(CSS).toMatch(
			/^\s*@import\s+['"]tailwindcss\/utilities\.css['"]\s+source\(none\)/m,
		);
		expect(CSS).not.toMatch(/^\s*@import\s+['"]tailwindcss['"]/m);
		expect(CSS).not.toContain("tailwindcss/preflight");
		expect(CSS).not.toMatch(/prefix\(/);
		expect(compiledCSS).not.toContain("ol, ul, menu");
		expect(compiledCSS).not.toContain("h1, h2, h3, h4, h5, h6");
	});

	it("generates only the deleted hand-written utility allowlist", () => {
		const match = CSS.match(/@source\s+inline\("([^"]+)"\)/);
		expect(match, "inline utility allowlist is missing").not.toBeNull();
		expect(match?.[1].split(/\s+/).sort()).toEqual([...LEGACY_UTILITIES].sort());
		for (const utility of LEGACY_UTILITIES) {
			expect(compiledCSS, `.${utility} should be generated`).toContain(`.${utility} {`);
		}
		// These names already occur in JSX but were inert before Tailwind. Automatic
		// scanning would activate them and make T1 a visual change.
		for (const utility of ["border", "rounded", "p-3"]) {
			expect(compiledCSS, `.${utility} must remain inert`).not.toContain(`.${utility} {`);
		}
	});

	it("keeps generated utilities at the old cascade position", () => {
		const utilities = compiledCSS.indexOf(".flex {");
		expect(utilities).toBeGreaterThan(compiledCSS.indexOf(".drawer-field-value {"));
		expect(utilities).toBeLessThan(compiledCSS.indexOf(".text-muted {"));
		// Layered utilities would lose to every unlayered project rule, changing
		// card/hint spacing even when both declarations have one-class specificity.
		expect(compiledCSS).not.toMatch(/@layer\s+utilities/);
	});

	it("keeps project-semantic classes that Tailwind has no equivalent for", () => {
		// These were deliberately NOT deleted with the layout utilities.
		for (const cls of [
			".mono",
			".text-muted",
			".text-error",
			".col-full",
			".text-pre-wrap",
		]) {
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
