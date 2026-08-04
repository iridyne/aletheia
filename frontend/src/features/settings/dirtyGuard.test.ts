import { describe, expect, it } from "vitest";
import { isUnsavedEdit, shouldBlockNavigation } from "./dirtyGuard";

describe("isUnsavedEdit", () => {
	it("treats a text input as an edit", () => {
		expect(isUnsavedEdit({ tagName: "INPUT", type: "text" })).toBe(true);
	});

	it("treats number, select and textarea as edits", () => {
		expect(isUnsavedEdit({ tagName: "INPUT", type: "number" })).toBe(true);
		expect(isUnsavedEdit({ tagName: "SELECT" })).toBe(true);
		expect(isUnsavedEdit({ tagName: "TEXTAREA" })).toBe(true);
	});

	it("treats a checkbox/toggle as an edit", () => {
		// Switch renders <input type="checkbox" className="settings-switch-input">.
		expect(isUnsavedEdit({ tagName: "INPUT", type: "checkbox" })).toBe(true);
	});

	it("ignores the Settings search box so navigation never warns", () => {
		expect(isUnsavedEdit({ tagName: "INPUT", type: "search" })).toBe(false);
	});

	it("ignores non-control elements and nullish targets", () => {
		expect(isUnsavedEdit({ tagName: "BUTTON" })).toBe(false);
		expect(isUnsavedEdit({ tagName: "DIV" })).toBe(false);
		expect(isUnsavedEdit(null)).toBe(false);
		expect(isUnsavedEdit(undefined)).toBe(false);
	});

	it("ignores readonly and disabled controls", () => {
		expect(isUnsavedEdit({ tagName: "INPUT", type: "text", readOnly: true })).toBe(false);
		expect(isUnsavedEdit({ tagName: "INPUT", type: "text", disabled: true })).toBe(false);
	});
});

describe("shouldBlockNavigation", () => {
	it("blocks when a section reports precise dirty state", () => {
		// The 5 sections that already call registerDirty (e.g. TradingParamsCard).
		expect(shouldBlockNavigation(1, false)).toBe(true);
	});

	it("blocks when only the delegated fallback fired", () => {
		// The 26 sections that report nothing — the #631 regression.
		expect(shouldBlockNavigation(0, true)).toBe(true);
	});

	it("does not block a clean tab", () => {
		expect(shouldBlockNavigation(0, false)).toBe(false);
	});
});
