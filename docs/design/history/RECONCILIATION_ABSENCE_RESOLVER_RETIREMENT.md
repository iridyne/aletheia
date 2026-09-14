# Absence-only reconciliation resolver retirement

Under #3134, the unused `resolve_orders_missing_from_exchange` helper and its
private mode selector were deleted. CodeGraph and current-source caller checks
found only tests, not a production consumer. The helper recommended filled
status from disappearance in an open-order list, without private Fill evidence.
That contradicts the execution contract: open-list absence is not proof of a
fill or cancellation.

Tests asserting the old absence-only behavior were removed. OCO resolution and
normalized Fill-quantity property tests remain. Actual reconciliation tests
continue requiring complete private evidence and the appropriate durable local
chain; absence alone leaves the Order unresolved. No runtime data was rewritten
and no replacement resolver, forwarding alias or fallback was introduced.
