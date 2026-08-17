import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SettingsRow } from "./SettingsSection";
import { Switch } from "./Switch";

function checkboxMarkup(html: string): string {
	return html.match(/<input[^>]*type="checkbox"[^>]*>/)?.[0] ?? "";
}

describe("rendered switch naming", () => {
	it("associates a label-less switch with its enclosing SettingsRow", () => {
		const html = renderToStaticMarkup(
			<SettingsRow label="Prediction observer">
				<Switch checked={false} persistence="immediate" />
			</SettingsRow>,
		);
		const labelId = html.match(/<div id="([^"]+)" class="settings-row-label">Prediction observer/)?.[1];

		expect(labelId).toBeTruthy();
		expect(checkboxMarkup(html)).toContain(`aria-labelledby="${labelId}"`);
	});

	it("keeps one explicit naming path when callers provide an aria label", () => {
		const html = renderToStaticMarkup(
			<Switch checked persistence="immediate" aria-label="Enable provider" aria-labelledby="stale-label" />,
		);
		const input = checkboxMarkup(html);

		expect(input).toContain('aria-label="Enable provider"');
		expect(input).not.toContain("aria-labelledby");
	});

	it("rejects an explicitly empty label instead of rendering an anonymous switch", () => {
		expect(() =>
			renderToStaticMarkup(<Switch checked persistence="immediate" label="" />),
		).toThrow("Switch label must be non-empty");
	});
});
