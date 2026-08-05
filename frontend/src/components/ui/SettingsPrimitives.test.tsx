import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import CSS from "../../index.css?raw";
import {
	ConfigSection,
	Field,
	SelectFieldGrid,
	SettingsScrollArea,
	TextFieldGrid,
} from "./SettingsPrimitives";

describe("Settings primitives", () => {
	it("binds a field label, description, and error to its control", () => {
		const html = renderToStaticMarkup(
			<Field
				controlId="telegram-chat-ids"
				label="Allowed chat IDs"
				description="Comma-separated IDs."
				error="At least one ID is required."
			>
				<input aria-describedby="existing-help" />
			</Field>,
		);

		expect(html).toContain('for="telegram-chat-ids"');
		expect(html).toContain('id="telegram-chat-ids"');
		expect(html).toContain(
			'aria-describedby="existing-help telegram-chat-ids-description telegram-chat-ids-error"',
		);
		expect(html).toContain('aria-invalid="true"');
		expect(html).toContain('id="telegram-chat-ids-description"');
		expect(html).toContain('id="telegram-chat-ids-error" role="alert"');
	});

	it("generates a matching control id when callers do not provide one", () => {
		const html = renderToStaticMarkup(
			<Field label="Endpoint">
				<input />
			</Field>,
		);
		const controlId = html.match(/<label[^>]+for="([^"]+)"/)?.[1];

		expect(controlId).toBeTruthy();
		expect(html).toContain(`id="${controlId}"`);
	});

	it("renders the section and single scroll owner contracts", () => {
		const html = renderToStaticMarkup(
			<SettingsScrollArea className="test-scroll" aria-label="Settings content">
				<ConfigSection id="notifications" title="Notifications" description="Delivery rules">
					<div>Controls</div>
				</ConfigSection>
			</SettingsScrollArea>,
		);

		expect(html).toContain('data-settings-scroll-area="true"');
		expect(html).toContain('class="settings-scroll-area test-scroll"');
		expect(html).toContain('aria-label="Settings content"');
		expect(html).toContain('<section class="settings-config-section" id="notifications">');
		expect(html).toContain('<h3 class="settings-config-title">Notifications</h3>');
		expect(html).toContain('<div class="settings-config-content"><div>Controls</div></div>');
	});

	it("keeps one vertical scroll owner and a bounded two-column section", () => {
		expect(CSS).toMatch(
			/\.settings-scroll-area\s*\{[^}]*@apply\s+min-h-0\s+flex-1\s+overflow-y-auto\s+overscroll-contain/,
		);
		expect(CSS).toMatch(
			/\.settings-config-section\s*\{\s*grid-template-columns:\s*240px\s+minmax\(0,\s*1fr\)/,
		);
		expect(CSS).toMatch(/\.settings-form-field-full\s*\{\s*grid-column:\s*1\s*\/\s*-1/);
	});

	it("renders text fields with their declared behavior and errors", () => {
		const set = vi.fn();
		const html = renderToStaticMarkup(
			<TextFieldGrid
				fields={[
					{
						key: "token",
						label: "Bot token",
						description: "Stored write-only.",
						type: "password",
						autoComplete: "new-password",
						fullWidth: true,
					},
				]}
				values={{ token: "masked" }}
				set={set}
				errors={{ token: "Replace the masked value to update it." }}
			/>,
		);

		expect(html).toContain('class="settings-form-field settings-form-field-full"');
		expect(html).toContain('type="password"');
		expect(html).toContain('autoComplete="new-password"');
		expect(html).toContain('value="masked"');
		expect(html).toContain('role="alert"');
	});

	it("keeps an unknown select value visible instead of showing another option", () => {
		const html = renderToStaticMarkup(
			<SelectFieldGrid
				fields={[
					{
						key: "provider",
						label: "Provider",
						options: [
							{ value: "native", label: "Native" },
							{ value: "custom", label: "Custom" },
						],
					},
				]}
				values={{ provider: "legacy" }}
				set={vi.fn()}
			/>,
		);

		expect(html).toContain('<option value="legacy" selected="">legacy</option>');
		expect(html).toContain('<option value="native">Native</option>');
	});
});
