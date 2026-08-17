/**
 * Unsaved-edit detection for the Settings shell.
 *
 * Why this is delegated rather than per-section: not every Settings editor has
 * precise local dirty state, and a new section must not silently opt out of the
 * navigation guard. Hand-wiring every call site would reproduce that failure
 * mode the next time a section is added.
 *
 * Instead the shell listens for change events bubbling out of the settings
 * panel, exactly like the existing delegated `handleNumericFocus`. One handler
 * covers every control in every section, including sections not yet written.
 *
 * The trade is deliberate: this detects "the operator touched a control" and
 * cannot know whether the value was later reverted by hand, so it can
 * over-warn. Over-warning is an annoyance; under-warning loses operator data.
 * Sections that need precision still report exact dirty state through
 * `registerDirty`, and the guard blocks when EITHER source says so.
 */

/** Structural shape of a DOM control, so this stays testable without a DOM. */
export interface ControlLike {
	tagName?: string;
	type?: string;
	readOnly?: boolean;
	disabled?: boolean;
}

/**
 * Does a change event from this element represent an unsaved configuration edit?
 *
 * Excludes the Settings search box: typing a query is navigation, not an edit,
 * and warning about it would train the operator to dismiss the prompt.
 */
export function isUnsavedEdit(el: ControlLike | null | undefined): boolean {
	if (!el) return false;
	const tag = (el.tagName ?? "").toLowerCase();
	if (tag !== "input" && tag !== "select" && tag !== "textarea") return false;
	if (el.readOnly === true || el.disabled === true) return false;
	// type="search" is only used by SettingsSearch (Settings.tsx:416).
	if (tag === "input" && el.type === "search") return false;
	return true;
}

/**
 * Whether an event target still contains a browser-managed draft after a
 * settings refetch. This is only the delegated fallback path; controlled
 * editors report exact dirtiness through `registerDirty`.
 */
export function hasUncontrolledDraft(el: ControlLike | null | undefined): boolean {
	if (!el || el.disabled || el.readOnly) return false;
	const tag = (el.tagName ?? "").toLowerCase();
	if (tag === "input") {
		const input = el as ControlLike & {
			checked?: boolean;
			defaultChecked?: boolean;
			value?: string;
			defaultValue?: string;
		};
		if (input.type === "checkbox" || input.type === "radio") {
			return input.checked !== input.defaultChecked;
		}
		return input.value !== input.defaultValue;
	}
	if (tag === "textarea") {
		const textarea = el as ControlLike & { value?: string; defaultValue?: string };
		return textarea.value !== textarea.defaultValue;
	}
	if (tag === "select") {
		const select = el as ControlLike & {
			options?: ArrayLike<{ selected: boolean; defaultSelected: boolean }>;
		};
		return Array.from(select.options ?? []).some(option => option.selected !== option.defaultSelected);
	}
	return false;
}

/**
 * Should leaving the current tab be blocked?
 *
 * `preciseDirtyCount` comes from sections that track their own dirty state;
 * `touched` is the delegated fallback for the sections that do not.
 */
export function shouldBlockNavigation(preciseDirtyCount: number, touched: boolean): boolean {
	return preciseDirtyCount > 0 || touched;
}
