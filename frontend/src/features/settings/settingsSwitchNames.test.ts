import { describe, expect, it } from "vitest";

const sources = import.meta.glob("./**/*.tsx", {
	eager: true,
	import: "default",
	query: "?raw",
}) as Record<string, string>;

const switchTag = /<(?:Switch|Toggle)\b[\s\S]*?\/>/g;
const checkboxTag = /<input\b[\s\S]*?type=["']checkbox["'][\s\S]*?\/>/g;
const rowBlock = /<(?:Row|SettingsRow)\b[\s\S]*?<\/(?:Row|SettingsRow)>/g;
const labelBlock = /<label\b[\s\S]*?<\/label>/g;

function lineAt(source: string, offset: number): number {
	return source.slice(0, offset).split("\n").length;
}

function hasProp(tag: string, name: string): boolean {
	return new RegExp(`\\b${name}\\s*=`).test(tag);
}

function stripComments(source: string): string {
	return source.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, (comment) =>
		comment.replace(/[^\n]/g, " "),
	);
}

describe("Settings switch naming source contract", () => {
	it("requires every direct Settings switch to be named or enclosed by a SettingsRow", () => {
		const violations: string[] = [];

		for (const [file, source] of Object.entries(sources)) {
			if (file.includes(".test.")) continue;
			const rows = [...source.matchAll(rowBlock)].map((match) => ({
				start: match.index ?? 0,
				end: (match.index ?? 0) + match[0].length,
			}));
			for (const match of source.matchAll(switchTag)) {
				const start = match.index ?? 0;
				const tag = match[0];
				const named = ["label", "aria-label", "aria-labelledby"].some((prop) =>
					hasProp(tag, prop),
				);
				const inRow = rows.some((row) => row.start < start && start < row.end);
				if (!named && !inRow) {
					violations.push(`${file}:${lineAt(source, start)} has no naming path`);
				}
				if (/\b(?:label|aria-label|aria-labelledby)\s*=\s*(?:""|''|\{\s*["']["']\s*\})/.test(tag)) {
					violations.push(`${file}:${lineAt(source, start)} has an empty naming prop`);
				}
			}

			const labels = [...source.matchAll(labelBlock)].map((match) => ({
				start: match.index ?? 0,
				end: (match.index ?? 0) + match[0].length,
			}));
			for (const match of source.matchAll(checkboxTag)) {
				const start = match.index ?? 0;
				const tag = match[0];
				const named = ["aria-label", "aria-labelledby"].some((prop) =>
					hasProp(tag, prop),
				);
				const inLabel = labels.some((label) => label.start < start && start < label.end);
				if (!named && !inLabel) {
					violations.push(`${file}:${lineAt(source, start)} checkbox has no naming path`);
				}
				if (/\b(?:aria-label|aria-labelledby)\s*=\s*(?:""|''|\{\s*["']["']\s*\})/.test(tag)) {
					violations.push(`${file}:${lineAt(source, start)} checkbox has an empty naming prop`);
				}
			}
		}

		expect(violations).toEqual([]);
	});

	it("does not put a Switch or Toggle inside another label", () => {
		const violations: string[] = [];

		for (const [file, source] of Object.entries(sources)) {
			if (file.includes(".test.")) continue;
			let labelDepth = 0;
			for (const token of stripComments(source).matchAll(
				/<\/label\s*>|<label\b[^>]*>|<(?:Switch|Toggle)\b[\s\S]*?\/>/g,
			)) {
				const value = token[0];
				if (value.startsWith("</label")) {
					labelDepth = Math.max(0, labelDepth - 1);
				} else if (value.startsWith("<label") && !value.endsWith("/>")) {
					labelDepth += 1;
				} else if (value.startsWith("<Switch") || value.startsWith("<Toggle")) {
					if (labelDepth > 0) {
						violations.push(`${file}:${lineAt(source, token.index ?? 0)} is nested in <label>`);
					}
				}
			}
		}

		expect(violations).toEqual([]);
	});
});
