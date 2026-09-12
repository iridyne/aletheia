# Slim Core private-I/O reachability evidence

Dated source audit for [#3134](https://github.com/iridyne/aletheia/issues/3134),
based on `992203fa65fb9cb29e8ae1d5246b0aef71d5206f`. This is a bounded evidence
inventory, not a complete production subsystem inventory, a support promise, or
runtime qualification.
The accepted [UTA boundary](../../design/proposals/unified_trading_account.md)
and current Issue define the target; the older
[consumer matrix](uta_consumer_port_matrix_2026-08-27.md) is historical evidence.

## Remaining concrete-client reachability

`UTA-S07` scans production imports outside `uta/` and `exchanges/`. Its three
remaining importing modules do not perform interchangeable work:

| Entrypoint / consumer | Actual read and downstream authority | Containment consequence |
| --- | --- | --- |
| `server.routes.bitget` stored-credential connection endpoints, called by `ExecutionTab` | Previously three native clients read account/positions. They now call existing `uta.manager.probe_account_reachable` off the HTTP event loop. It accepts only complete account evidence, classifies errors without raw messages, and uses `query_uta` teardown. UI consumes `connected` / `error`; it does not consume the old Binance position count. | One existing account-probe owner; no separate route client or probe executor. Endpoint mode identifies the configured account, not trading permission. |
| Same module's candidate-credential endpoints | Bitget still signs a candidate account-info request with `build_headers` and imports the legacy client's `BASE_URL`; Binance/OKX construct temporary CCXT balance probes. These requests do not persist candidate credentials. Credential-status/write routes are separate from the stored-account probe. | The module remains on the concrete-import allowlist. This cutover does not claim adapter-only credential ownership or remove candidate-probe side channels. |
| `agent_runtime.scheduler._tpsl_fill_check_body` | The configured Bitget profile selects classic v2 plan history or UTA v3 `strategy-sub-orders`; UTA maps an exact filled `subOrderId` to `executeOrderId`. A UTA exact Order read proves account membership before `_claim_observed_tpsl_close`; native exact order fills then feed `materialize_bitget_close`, with another configured-identity check. | This is an active recovery/economic-evidence path, not inert history. Parent-trigger discovery remains a native venue concern; normalized `OrderQuery` / `OrderStatusReport` still do not own it. |
| `execution.venue_capabilities.fetch_venue_capabilities` | Calls `OKXClient.load_demo_markets` for public sandbox listing facts. Cached consumers distinguish unavailable from a successfully loaded empty symbol set. | Importing a private-capable class does not make this read private. Preserve actual public listing/failure semantics when extracting it; do not replace it with account balance or an empty default. |

## Independently necessary evidence boundaries

The scheduler's UTA membership check precedes the durable pending-close claim:
rejecting a foreign execution Order only after claiming would wedge an open
Position. Plan status and trigger prices are not private Fill economics. Exact
Fill identity, quantity, price, fees and immutable materialization remain required.
The raw client's per-request credential resolution is distinct from UTA's
account-identity proof; moving only its import would not converge that authority.

Installed CCXT `bitget.fetch_order` selects ordinary spot/mix/UTA order-detail
endpoints. `fetch_closed_orders` has a trigger-history option, but its normalized
parent/child evidence is not the scheduler's authority. The native client's
public `get_plan_order_history` now dispatches to the configured profile: classic
v2 uses plan history, while UTA v3 uses the official strategy-child endpoint and
preserves the existing exact-account-proof/fill-materialization bridge.

## Verification boundary

The additional startup inventory was bound to
`d1bac0e7e8a9da039a498c56577af3a4f2569cf6`. `server.app.lifespan` was the sole
production caller of `config.secrets.migrate_keyring_to_file`; it enumerated
Windows Credential Manager (or a broad fixed key list) and copied missing values
into the encrypted store before runtime initialization. This hidden historical
repair conflicts with #3134's startup/migration and bounded credential rules.
The caller, bulk migration function and advertised startup stage are removed.
Per-reference encrypted-file-first reads, keyring fallback, explicit writes,
credential generations and cross-process locking are unchanged. No operational
credential store is read, migrated or deleted by this source repair. Architecture
and startup-policy checks prevent restoration of the removed authority; scoped
keyring-only reads remain covered without materializing an encrypted store.

Stored-probe regression covers six account modes, complete versus partial
reports, execution off the request loop, refusal to construct native clients,
and sanitized failure output. Manager tests retain identity-mismatch teardown.
These mocked reads grant neither Live authority nor account qualification.
The unchanged concrete-import allowlist is deliberately not a claim that all
private I/O is contained: candidate probes and TP/SL recovery remain visible
above, and package movement alone cannot settle their ownership.

The current-head identity audit removes duplicate generic external-mode sets
from Gateway routing, position routing, pending/stale cleanup, funding
settlement, Demo Risk freshness, startup scope, and account-mode reconciliation.
Those consumers now reuse `config.exchanges` identity classifications. The
remaining direct mode branches are the documented UTA profile composition or
venue-native capability/recovery exceptions above; this change does not claim
that those concrete clients have moved out of Core reachability.

The subsequent Entry identity pass also removes the generic consumers' direct
current-route comparisons. A small lookup over the existing
`ENTRY_EXECUTION_MINIMUM_PERMISSION` registry is shared by Entry authority,
compiler, Entry execution, Gateway and same-Plan Risk handling; demo account
matching and live exposure use the existing registered identity helpers. This
keeps the current Bitget Demo-only Entry policy while making any future route
admission an explicit registry change with its own Planner/Risk/Gateway tests.

The single-writer pass also removed the pending-close cleanup's direct
`orders.error_reason` SQL mutation. The owner-authorized release now calls a
repository CAS helper that changes only the expected cancelled Order reason;
the exchange/private-I-O boundaries above remain unchanged.

The follow-up production AST/literal-DML scan found no additional direct write
to a Core lifecycle table outside its documented owner. The only remaining
non-repository statements touching those facts are the materializer's
`fills.fee_source` provenance upgrade, the accounting owner's close-event
completion in `_commit_close_economics`, and two fixed-target historical Position
repairs. Their callers and predicates are explicit and bounded; no scheduler,
importer, optional Extension or UI path reaches them. Writes to settings,
Observation/Learning namespaces and audit-mirror metadata remain separate
owners, not alternate Order/Fill/Position lifecycle paths.

The merged optional-lifecycle repair closes the startup absence boundary without
moving private I/O. A missing Observation owner module resolves to `unknown`,
so Scheduler startup omits that optional cycle and reconciliation removes any
stale registration. Missing Learning control/lesson modules preserve Core job
guards and return an explicit unavailable advisory projection; Telegram adapter
import/configuration failure is non-blocking for lifespan startup. The focused
absence regressions (88 tests) and architecture fitness tests (19 tests) pass;
they do not grant account, exchange, deployment, or qualification authority.

The architecture-map seam is also merged at this checkpoint. The maintained
guide, map, Agent Contracts, and README D2 source/rendered pairs now identify
Candidate Planning Generations and immutable Entry Plans as the current entry
mainline, while retired Ranker/PaperTrader and learning evaluator/catch-up/
lifecycle jobs remain historical or absent. This changes no private-I/O owner
and is not runtime or exchange qualification evidence.

The execution-identity seam is also merged at this checkpoint. Generic Gateway
and capability consumers no longer import or maintain a second
mode-to-venue dictionary; they read `config.exchanges.EXECUTION_MODE_VENUES`.
The OKX Demo public sandbox loader remains the only specialized capability
branch, and UTA profile composition/private-I-O boundaries are unchanged. The
focused local tests (82 architecture/spec and 212 execution/registry/pipeline)
cover this structural reuse; they do not prove private-account containment,
deployment, or exchange qualification.

## Startup filesystem repair ownership

At `bcb88659ebece300db0a54a7802323e0d2f0a24e`, the next entrypoint audit found
two callers of `gateway.git_hooks.remove_managed_git_hooks`: backend lifespan
and explicit `diagnostics.doctor.apply_fixes`. The helper resolves the shared
Git hooks directory, removes only standalone managed legacy restart hooks and
their debounce stamp, and preserves foreign/wrapped hooks. This is historical
filesystem repair, not ordinary runtime reconciliation. The implicit lifespan
caller is removed under #3134's no-hidden-startup-repair boundary; explicit
`doctor --fix` retains the existing bounded implementation and the normal
Doctor probe remains read-only. An architecture regression checks the sole
production caller, while real temporary-repository tests cover idempotency and
foreign-hook preservation. No actual user or runtime hook files were changed.

Read-only Fedora inspection during this batch found checkout
`6749c31b62a36a0497659cf25608025f8ab4218f`, clean, with Gateway active/running
under PID 106503 and invocation `9f00a439f9964455b6c00f189665083f`. These facts
prove neither current program deployment nor schema/provider/settlement health.
