# Retired Gateway symbol-cache aliases

Under #3134, Gateway no longer exposes `_registry_symbol_cache` or
`_load_registry_symbols` as test compatibility aliases. The cache remains owned
by `execution/venue_capabilities.py`; tests reset it through that owner.

Gateway consumes the existing `get_supported_symbols` boundary for all execution
identities. Unknown availability remains `None`, a loaded empty set rejects the
symbol, and refresh failures propagate to the pre-order failure path. Rejection
messages identify the execution mode and requested market type without a
venue-specific Gateway branch.

The unconditional `okx_demo_symbols` scheduler job and its runner were also
removed under the frozen-Extension boundary. They previously warmed the cache
45 seconds after every startup and every four hours, including Bitget runtimes.
Explicit symbol queries still refresh expired cache entries on demand; their
failure handling is unchanged. This retirement does not authorize OKX promotion.

Public CCXT market loading moved from `data/ccxt_provider.py` to
`extensions/exchanges/ccxt/market_capabilities.py`. The existing Extension
composition entrypoint explicitly binds that credential-free reader. An
unbound reader cannot refresh market metadata; Core has no fallback constructor
for this operation. The market filter and loop-local cleanup are unchanged.

The account-specific OKX sandbox cache subsequently moved to
`extensions/exchanges/okx/market_capabilities.py`, without a scheduler or forwarding
alias. Extension composition selects restricted accounts and returns Core market
types (`usdt_futures` and `spot`), using lazy imports for the frozen venue. Core
consumes bound cached restrictions for execution and fresh restrictions for
Universe synchronization. Accounts without extra restrictions retain their
ordinary registry behavior. The shared universe-registry cache remains in Core;
it is a different source from the removed venue-specific cache.
