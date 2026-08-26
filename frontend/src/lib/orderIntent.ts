import type { Order } from "../types";

export type OrderIntent = "entry" | "close" | "unavailable";

function hasText(value: string | null | undefined): boolean {
	return typeof value === "string" && value.trim().length > 0;
}

export function orderIntent(
	order: Pick<Order, "position_id" | "close_reason">,
): OrderIntent {
	const hasPosition = hasText(order.position_id);
	const hasReason = hasText(order.close_reason);
	if (hasPosition && hasReason) return "close";
	if (!hasPosition && !hasReason) return "entry";
	return "unavailable";
}

export function orderIntentLabel(intent: OrderIntent): string {
	if (intent === "close") return "Close";
	if (intent === "entry") return "Entry";
	return "Intent unavailable";
}
