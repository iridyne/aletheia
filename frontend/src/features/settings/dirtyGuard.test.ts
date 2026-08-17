import { describe, expect, it } from "vitest";
import { hasUncontrolledDraft, isFallbackDraftControl, isUnsavedEdit, shouldBlockNavigation } from "./dirtyGuard";

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
		// Precise editors such as TradingParamsCard use this path.
		expect(shouldBlockNavigation(1, false)).toBe(true);
	});

	it("blocks when only the delegated fallback fired", () => {
		// Editors without precise state still use the shell fallback.
		expect(shouldBlockNavigation(0, true)).toBe(true);
	});

	it("does not block a clean tab", () => {
		expect(shouldBlockNavigation(0, false)).toBe(false);
	});
});

describe("isFallbackDraftControl", () => {
	it("keeps browser-managed text controls in the shell fallback", () => {
		expect(isFallbackDraftControl({ tagName: "INPUT", type: "number" })).toBe(true);
		expect(isFallbackDraftControl({ tagName: "TEXTAREA" })).toBe(true);
	});

	it("leaves controlled select and boolean controls to their own lifecycle", () => {
		expect(isFallbackDraftControl({ tagName: "SELECT" })).toBe(false);
		expect(isFallbackDraftControl({ tagName: "INPUT", type: "checkbox" })).toBe(false);
		expect(isFallbackDraftControl({ tagName: "INPUT", type: "radio" })).toBe(false);
	});
});

describe("hasUncontrolledDraft", () => {
	it("keeps browser-managed input drafts across a refetch", () => {
		expect(hasUncontrolledDraft({ tagName: "INPUT", type: "number", value: "2", defaultValue: "1" } as any)).toBe(true);
		expect(hasUncontrolledDraft({ tagName: "INPUT", type: "number", value: "1", defaultValue: "1" } as any)).toBe(false);
	});

	it("detects staged select and checkbox changes", () => {
		expect(hasUncontrolledDraft({
			tagName: "SELECT",
			options: [{ selected: true, defaultSelected: false }],
		} as any)).toBe(true);
		expect(hasUncontrolledDraft({
			tagName: "INPUT",
			type: "checkbox",
			checked: true,
			defaultChecked: false,
		} as any)).toBe(true);
	});
});
