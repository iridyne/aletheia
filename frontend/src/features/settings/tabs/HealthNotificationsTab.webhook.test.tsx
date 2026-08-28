// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const mocks = vi.hoisted(() => ({
	updateWebhookConfig: vi.fn(),
}));

vi.mock("../../../api/client", () => ({
	api: { updateWebhookConfig: mocks.updateWebhookConfig },
}));

vi.mock("@tanstack/react-query", async () => {
	const actual = await vi.importActual<typeof import("@tanstack/react-query")>(
		"@tanstack/react-query",
	);
	return {
		...actual,
		useMutation: (options: any) => ({
			isPending: false,
			mutate: () => {
				const result = options.mutationFn();
				options.onSuccess?.(result);
			},
		}),
	};
});

import { WebhookCard } from "./HealthNotificationsTab";

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

afterEach(() => {
	act(() => root?.unmount());
	host?.remove();
	mocks.updateWebhookConfig.mockReset();
});

describe("WebhookCard secret lifecycle", () => {
	it("sends an explicit empty secret only after Remove HMAC secret", () => {
		host = document.createElement("div");
		document.body.append(host);
		root = createRoot(host);
		let cached: unknown;
		const settings = {
			webhook: {
				url: "https://example.test/hook",
				secret_configured: true,
			},
		};
		const qc = {
			invalidateQueries: vi.fn(),
			setQueryData: vi.fn((_key, updater) => { cached = updater(settings); }),
		} as never;
		mocks.updateWebhookConfig.mockReturnValue({ updated: { secret_configured: false } });

		act(() => root.render(
			<WebhookCard
				settings={settings}
				qc={qc}
			/>,
		));

		const remove = Array.from(host.querySelectorAll("button"))
			.find((button) => button.textContent === "Remove HMAC secret") as HTMLButtonElement;
		act(() => remove.click());
		const save = Array.from(host.querySelectorAll("button"))
			.find((button) => button.textContent === "Save") as HTMLButtonElement;
		act(() => save.click());

		expect(mocks.updateWebhookConfig).toHaveBeenCalledWith(
			expect.objectContaining({ secret: "" }),
		);
		expect(cached).toEqual({
			webhook: expect.objectContaining({ secret_configured: false }),
		});
	});
});
