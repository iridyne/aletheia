# Retired native Bitget trading implementation

Under #3134, the native Bitget client is restricted to the private read methods
used by the current native-protection reader: parent/child plan history, exact
order fills, and normalized Fill reports. These reads retain their request-scoped
transport, signing, profile selection, exact-order filtering and pagination.
The client is an Exchange implementation, not a Core or Legacy credential owner.
Its surviving implementation, native reader, signing and profile configuration
live in `extensions/exchanges/bitget`; the old package has no forwarding facade.

The former account, position, order submission/cancellation, leverage, funding,
verified-exit and public-market methods were not selected by current production
composition. Those implementations and their local request/response DTOs are
removed. Current private account I/O belongs to the existing CCXT UTA adapter and
venue profiles; public market facts belong to the Data provider. There is no
forwarding alias, alternate broker selector or whole-position flash-close path.

Tests follow the surviving behavior owners:

- Current UTA account/order/fill/funding tests preserve normalized evidence,
  private-history completeness and exact account identity.
- Bitget wire tests use the installed CCXT request builder for Classic-v2 and
  UTA-v3 entry/close direction and post-only semantics. Old native wire keys are
  not a second protocol authority.
- Verified-exit tests cover both Bitget profiles and frozen venue recovery,
  complete absence, missing/partial history, entry-fill exclusion, fees and
  account mismatch. Core projection remains covered separately.
- Native history and exact-fill tests retain the remaining reader's protocol
  and lifecycle checks. UTA owns protection mutations.
- The test mutation guard covers real CCXT HTTP mutation transport; the historical
  direct-client test marker no longer grants an opt-out.

The [UTA proposal](../proposals/unified_trading_account.md),
[execution contract](../contracts/execution_contract.md) and
[active runtime scope](../../product/ACTIVE_RUNTIME_SCOPE.md) retain authority.
This retirement does not change admission, credentials, Risk, accounting,
qualification, or the evidence required before local state can be settled.
