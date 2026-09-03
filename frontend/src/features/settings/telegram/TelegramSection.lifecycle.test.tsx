// @vitest-environment happy-dom
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ useQuery: vi.fn() }));

vi.mock("@tanstack/react-query", async () => {
	const actual = await vi.importActual<typeof import("@tanstack/react-query")>(
		"@tanstack/react-query",
	);
	return {
		...actual,
		useQuery: mocks.useQuery,
		useMutation: () => ({ isPending: false, mutate: vi.fn() }),
	};
});

import { TelegramSection } from "./TelegramSection";

describe("TelegramSection health query lifecycle", () => {
	it("shows unavailable when an initial query is paused offline", () => {
		mocks.useQuery.mockReturnValue({
			data: undefined,
			isPending: true,
			isError: false,
			fetchStatus: "paused",
		});

		const html = renderToStaticMarkup(
			<TelegramSection
				settings={{ telegram: {} }}
				qc={{ invalidateQueries: vi.fn() } as never}
			/>,
		);

		expect(html).toContain("status unavailable");
		expect(html).not.toContain("checking…");
	});

	it("shows unavailable instead of not configured after a query failure", () => {
		mocks.useQuery.mockReturnValue({
			data: undefined,
			isPending: false,
			isError: true,
		});

		const html = renderToStaticMarkup(
			<TelegramSection
				settings={{ telegram: {} }}
				qc={{ invalidateQueries: vi.fn() } as never}
			/>,
		);

		expect(html).toContain("status unavailable");
		expect(html).not.toContain("not configured");
	});
});
