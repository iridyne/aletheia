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

## Phase 3 evidence/recovery/Trust cutover

Fixing baseline: `master@324ddafe53d5b577979b84da9ba83da8d6e3cdc7`.

| Boundary | Phase 3 state | Authority result |
|---|---|---|
| UTA reports | `CCXTUTAAdapter` implements exact Order, bounded Fill history, mass status, bounded funding ledger, verified exit and account-authority probe for all six configured account modes. | Complete empty remains distinct from partial/unavailable; history bounds fail closed. |
| Reconciliation | Existing atomic Fill/Position materializers consume normalized UTA evidence through a temporary raw-row projection. No concrete venue client is constructed. | Open-list absence is diagnostic only and never creates a terminal fact. |
| Stale/pending recovery | Cleanup uses complete exact UTA mass status; pending-close recovery uses exact Order/Fill reports and retains evidence-then-CAS. | Age and unavailable evidence never fabricate cancellation; stable venue cancel reasons remain available. |
| Disappearance/funding | Verified-exit and funding settlement consume `VerifiedExitReport`, exact current Position reports and `FundingLedgerReport`. | Account identity is checked before query; incomplete evidence cannot close or fabricate zero funding. |
| Live Trust | Preflight consumes the selected UTA identity and `AccountAuthorityProbeReport`, including profile and credential generation. | Permission unknown stays unknown and Trust continues to deny it by policy. |
| Compatibility seams | Reconciliation, funding and verified-exit expose temporary projection factories for existing tests/rollback wiring; they contain no private network implementation and delegate to UTA by default. | Phase 8 removes these seams after every execution profile is qualified. |

Phase 3 does not submit, modify or cancel an exchange order. Mutation methods
remain `NOT_SENT`; execution cutover starts in Phase 4.

## Phase 4 Binance testnet execution slice

Fixing baseline: `master@b54ae5af48a162e3a009d0820616c9866937f58f`.

| Boundary | Phase 4 implementation state | Authority result |
|---|---|---|
| UTA mutation | The Binance testnet profile implements market/limit submit, leverage preparation, reduce-only, modify and cancel through normalized command reports. Other profiles remain `NOT_SENT`. | Raw Binance parameters remain profile-owned; stable command evidence distinguishes not-sent, rejected, acknowledged and ambiguous. |
| Entry | Gateway selects `execution/coordinator.py` only when `binance_testnet` is configured `uta_v1`; legacy and shadow selectors keep the existing broker. | The Coordinator commits pending Order before I/O and reuses the shared acknowledgement CAS afterward; there is no dual submission. |
| Full close | Position Manager routes Binance close intent back through Gateway. A selected testnet UTA close commits the durable Position claim, sends one reduce-only command, then applies its report by CAS. | Late private Fill/close settlement wins over a late acknowledgement or ambiguous report. Binance live-limited remains legacy. |
| Protection | Binance testnet had no implemented exchange-native protection placement/replacement before this phase. | Capability remains explicitly unsupported; the default-off local protection behavior is unchanged rather than invented. |
| Rollback/qualification | Selection remains configuration-only and defaults to legacy. Automated tests cover pending-before-I/O, no writer-lock network call, ambiguity, exact Fill materialization and late-ack barriers. | Neither macOS nor the Fedora Paper runtime had Binance testnet credentials at the dated check, so credential qualification is **not available** and `uta_v1` must remain inactive. |

The repeatable secret-safe gate is:

```bash
uv run python scripts/qualify_uta.py \
  --execution-mode binance_testnet --read-parity --execution-probe \
  --confirm-testnet-order --reference-price <fresh-public-price>
```

It accepts only Binance testnet in Phase 4, derives the minimum legal size from
current venue constraints, exercises resting submit/modify/cancel and market
entry/private-Fill/reduce-only-close/verified-exit evidence, and writes a
credential-free artifact bound to code SHA, account fingerprint and credential
generation. Missing credentials exits non-zero with `qualified=false`.

## Phase 5 OKX demo execution slice

Fixing baseline: `master@de924e04aaae7f83fabbaab52797c9082e346a41`.

| Boundary | Phase 5 implementation state | Authority result |
|---|---|---|
| OKX profile | `OKXDemoProfile` owns spot/perp quantity translation, exact integer contract conversion, demo `clOrdId`, leverage/margin mode, reduce-only, trigger TP/SL, modify and cancel parameters. `OKXLiveProfile` remains mutation-disabled. | Unknown contract size or non-integral contract quantity is `NOT_SENT`; raw OKX params remain below `uta/profiles/`. |
| Entry/full close | Gateway selects the existing Coordinator only for an exact current `okx_demo + uta_v1` qualification; Position Manager returns close intent to Gateway. | Pending local Order precedes external I/O; acknowledgement uses the shared CAS and legacy remains the rollback path. |
| Protection | `execution/stops.py` returns OKX demo protection authority to Gateway. Coordinator persists each protection child before UTA submission and cancels the proven old stop before a replacement. | No direct Position Manager/protection UTA call and no dual submission; successful venue TP/SL identities remain on the Position. |
| Qualification | The secret-safe script accepts `okx_demo`, uses venue-valid client ids, and requires submit/modify/cancel, private entry/close fills, verified exit plus protection submit/cancel evidence. | No credential probe has been authorized or run on this baseline, so OKX demo remains unqualified and legacy-selected. |

## Phase 6 Bitget Classic v2 and UTA v3 static execution slice

Fixing baseline: `master@c431122cccf4b515c3db16dd144eb2dd66728dbe`.

| Boundary | Phase 6 implementation state | Authority result |
|---|---|---|
| Profile isolation | `BitgetClassicV2Profile` and `BitgetUTAV3Profile` independently own submit/modify/cancel, leverage, hedge close, contract conversion and trigger protection parameters. Every mutation carries its explicit `uta=false/true` family selector. | Static conformance proves the two profiles never share a v2/v3 request family. The native Bitget implementation remains the legacy parity oracle and rollback path. |
| Quantity and close | Canonical coin quantity is converted to an exact integer contract count from loaded instrument metadata. Both profiles submit canonical opposite-side reduce-only hedge closes; the pinned CCXT driver maps that intent to each profile's distinct close convention. | Missing multiplier and non-integral quantities are `NOT_SENT`; close preparation never changes leverage or guesses a contract size. |
| Gateway/Coordinator | Qualified Bitget demo entry, full close, protection and replacement use the same Gateway → Coordinator → UTA path as the earlier profiles. Position Manager and the shared stop dispatcher return authority to Gateway. | Pending local state still precedes I/O and acknowledgement still uses the shared CAS. An unqualified selector continues to call the native legacy broker; there is no dual submission. |
| Qualification | The repeatable qualification script accepts `bitget_demo` and requires modify/cancel, private entry/close fills, verified exit and protection submit/cancel evidence for the exact configured Classic-v2 or UTA-v3 identity. | The owner cannot provide exchange credentials and waived real demo probes. Neither profile is claimed qualified; both remain legacy-selected. Live variants remain mutation-disabled. |

Static Phase 6 evidence does not assert Bitget account reachability, exchange
acceptance, or runtime qualification. A future switch still requires the exact-code,
account/profile/generation artifact enforced by `uta_qualification.py`.

## Adjacent execution findings

| Issue | Exact-head evidence | Composition decision |
|---|---|---|
| #2042 Bitget late acknowledgement | Merged PR #2102 (`9adbace4`) added shared `repositories.finalize_exchange_acknowledgement()` and all three brokers call it. The helper uses `transaction(immediate=True)`, a conditional unresolved-state update, immutable compatible venue identity, close-claim ownership and returns the durable Fill/Position winner. | Root is already repaired on this head. The future coordinator must reuse and preserve this CAS contract; moving the network boundary does not supersede it. |
| #2045 OKX/Binance acknowledgement TOCTOU | The same merged helper replaces their prior read-then-write guard and current barrier regressions live in `tests/test_exchange_submission_integrity.py`. | Same composition as #2042. The Issue remains separate history until its own closure gate is handled; #2299 must not relabel that proof. |
| #1421 stale cleanup fabricates cancellation | Phase 3 removes the hard-age unverifiable cancellation path and requires complete UTA mass-status evidence with no Order or Fill before ordinary orphan cancellation. | Root composes with Phase 3 and now has focused regressions; Issue closure still requires its own GitHub evidence gate. |
