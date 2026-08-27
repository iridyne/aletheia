# UTA private-I/O consumer-port audit

Evidence snapshot for `master@ada6ab6e2c8a4fd154bbb6b3900d7197f234140e`.
This is migration input for Issue #2299, not current implementation status.

| Consumer | Exact-head coupling | Target port |
|---|---|---|
| Gateway entry | `execution/gateway.py` dispatches `binance_broker.py`, `okx_broker.py`, `bitget_broker.py` | Gateway -> coordinator -> UTA mutation |
| Position close | `portfolio/position_manager.py` imports three venue close functions | Close intent -> Gateway -> coordinator -> UTA |
| TP/SL and trailing protection | `execution/stops.py` imports Bitget/OKX venue protection functions | Protection intent -> Gateway -> coordinator -> generic UTA command |
| account sync | `portfolio/exchange_sync.py` and `portfolio/uta/*_adapter.py` construct concrete private clients | UTAManager plus UTA-backed projection |
| Portfolio/PnL | `portfolio/exchange_sync.py` cache and broker-specific funding/exit evidence | Exact-account UTA read projection |
| Risk | `risk/engine.py` reads exchange-sync cache | Exact-account, generation-bound UTA projection |
| Startup Gate | `server/app.py` starts exchange sync; `agent_runtime/startup_behavior_gate.py` reads Bitget profile facts | Same UTA-derived readiness projection |
| UI/API/inspect | `server/routes/data.py`, `server/routes/bitget.py`, `cli/bitget_cmd.py`, analytics and agent context consume cache or private clients | UTA-backed compatibility DTO/cache |
| Live unlock | `trust/live_unlock.py` constructs Bitget/OKX/Binance clients | `AccountAuthorityProbeReport` |
| reconciliation | `execution/reconciliation.py` imports venue brokers and all three concrete clients | UTA order/fill/position/mass reports |
| stale-order cleanup | `execution/stale_order_cleanup.py` constructs Bitget client | Exact UTA order/fill reports |
| pending-close recovery | `execution/pending_close_recovery.py` constructs Bitget client | UTA exact reports plus evidence-then-CAS |
| disappearance settlement | `portfolio/disappearance_settlement.py` constructs all three concrete clients | `VerifiedExitReport` plus exact Position report |
| funding settlement | `portfolio/broker_funding_settlement.py` constructs all three concrete clients | `FundingLedgerReport` |
| venue capabilities | `execution/venue_capabilities.py` imports OKX broker/client facts | UTA/profile capability projection |
| scheduler diagnostics | `agent_runtime/scheduler.py` constructs Bitget client and refreshes OKX symbols | UTA read/health projection or public-market path |
| maintenance close repair | `maintenance/repair_administrative_close.py` constructs Bitget client | Narrow audited Gateway/coordinator maintenance port |
| secrets and account diagnostics | `cli/secrets_cmd.py` constructs Bitget client | Read-only UTA authority probe |
| legacy Bitget route | `server/routes/bitget.py` imports Bitget auth/client and constructs CCXT for private reads | UTA-backed route DTO; public market reads remain separate |

Allowed non-private exceptions remain `aletheia/data/**` public market data and
`aletheia/net.py` CCXT construction/session ownership. Existing concrete clients
and venue brokers are migration sources, not approved final-cutover exceptions.

## Phase 2 account/position read cutover

Fixing baseline: `master@abdfbecab65e3e93090ab85234ead8d53ae19be1`.

| Boundary | Phase 2 state | Authority result |
|---|---|---|
| Registry/manager | `aletheia/uta/manager.py` owns loop-local UTA factories, selected reads, completeness projection and legacy/shadow rollback; the old manager is an import shim. | One selected account-read coordinator; no client singleton. |
| Account cache | Unified rows persist exact account/profile/environment/generation, actual authoritative adapter implementation and boundary. Credential/profile changes invalidate the old generation immediately. | A mismatched cache remains diagnostic only and reports `generation_mismatch`. |
| Portfolio/UI | Default external-account reads use the unified cache for non-Bitget accounts and any `uta_v1` account. Unknown totals are `null`; complete zero remains zero. | No legacy Bitget balance can substitute for an unavailable UTA read. |
| Risk/Startup | Risk resolves exact named healthy rows; Startup uses `active_broker_view_complete`. Both reject stale, partial, missing and generation-mismatched views. | Sizing and startup fail closed without current account authority. |
| Reconciliation | Existing compatibility positions now originate from the selected manager and retain broker/account/instrument identity. Reconciliation already requires an `ok` unified view. | An incomplete active account cannot silently become authoritative absence. |
| Selector | Missing selector remains `legacy`; `shadow_read` compares without changing authority; production settings reject `uta_v1` until persisted qualification exists. | Rollback remains config-only and unqualified profiles cannot activate UTA reads. |

Phase 2 intentionally does not implement order/fill/funding/verified-exit/Trust
evidence or any mutation. Those remain Phase 3 and later gates.

## Adjacent execution findings

| Issue | Exact-head evidence | Composition decision |
|---|---|---|
| #2042 Bitget late acknowledgement | Merged PR #2102 (`9adbace4`) added shared `repositories.finalize_exchange_acknowledgement()` and all three brokers call it. The helper uses `transaction(immediate=True)`, a conditional unresolved-state update, immutable compatible venue identity, close-claim ownership and returns the durable Fill/Position winner. | Root is already repaired on this head. The future coordinator must reuse and preserve this CAS contract; moving the network boundary does not supersede it. |
| #2045 OKX/Binance acknowledgement TOCTOU | The same merged helper replaces their prior read-then-write guard and current barrier regressions live in `tests/test_exchange_submission_integrity.py`. | Same composition as #2042. The Issue remains separate history until its own closure gate is handled; #2299 must not relabel that proof. |
| #1421 stale cleanup fabricates cancellation | `execution/stale_order_cleanup.py` still maps open-list absence to `stale_order_absent_on_exchange`, outage/age to `stale_order_unverifiable_aged_out`, and a closed local Position to pending-close removal without exact remote terminal evidence. | Still unresolved. Phase 3 must route ordinary reconciliation and cleanup through UTA exact Order/Fill reports; open-list absence or unavailable evidence must remain unresolved. This is composed into the migration, not declared fixed by Phase 0. |
