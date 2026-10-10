# Exit and Close Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `XC` · **Owner code:** `core/aletheia/portfolio/position_manager.py`
> **Split from:** [execution contract](execution_contract.md) §10 and §11.1–11.3 (2026-10-10).
> Paths below are relative to `core/aletheia/` unless they start with a top-level directory.

## Scope

This contract owns the close half of the trading mainline: which triggers close an open
Position, the single routed close exit, the durable close claim and its bounded states,
pause/kill-switch semantics for closes, the owner manual close, and the split between a
verified strategy close and an administrative close. It applies to USDT perpetuals on
`bitget_demo`; Live closes are human-only. It does not own order submission, leases or
reconciliation mechanics ([execution contract](execution_contract.md)), or close economics,
funding and Outcome read surfaces ([outcome integrity contract](outcome_integrity_contract.md)).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/portfolio/position_manager.py::evaluate_open_positions` | scheduler job `position_manager` (30 s, executor `protective`) | `agent_runtime/scheduler.py::_run_position_manager` |
| `core/aletheia/portfolio/position_manager.py::close_position_routed` | call | `evaluate_open_positions`, `agent_shell/commands.py::review_portfolio_state` (`close_one`) |
| `core/aletheia/execution/gateway.py::close_exchange_position` | call | `close_position_routed` |
| `core/aletheia/execution/coordinator.py::close_position` | call | `close_exchange_position` |
| `core/aletheia/agent_shell/commands.py::review_portfolio_state` | System Operator command | Position wake via `agent_runtime/pi_daemon.py` |
| `core/aletheia/server/routes/data.py::manual_close_position` | API `POST /api/positions/{id}/close` | dashboard Close button |
| `core/aletheia/execution/pending_close_recovery.py::release_rate_limited_close_claim` | API `POST /api/positions/{id}/recover-parked-close` | dashboard owner unlock |
| `core/aletheia/execution/pending_close_recovery.py::recover_absent_stale_close_orders` | call | `execution/reconciliation.py::reconcile` |
| `core/aletheia/portfolio/disappearance_settlement.py::settle_ready_disappearance_candidates` | call | `portfolio/position_reconciler.py` (demo) |
| `scripts/maintenance/manual_close_resolver.py` | operator tool | owner, for `manual_reconciliation_pending` |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `positions.pending_close_*` (status, order id, requested_at) | `execution/coordinator.py::_claim_close`, `execution/pending_close_recovery.py` | Position Manager, startup gate, UI read model | claim fence; released only by the rules below |
| `orders` (close rows, `close_reason`, `close_reasoning`) | `execution/coordinator.py` | reconciliation, materializer | monotonic status |
| `positions.horizon_wake_sent_at` | `portfolio/position_manager.py` | Position Manager | set on review completion, cleared on re-arm |
| `positions.exit_limit_price`, `exit_limit_source` | `server/routes/data.py`, Operator adjustment | `_decide_exit` | replaceable |
| `position_disappearance_candidates` | `portfolio/disappearance_confirmation.py` | `portfolio/disappearance_settlement.py` | monotonic `direct_query_state` |
| `pi_decisions` | `agent_shell/commands.py::review_portfolio_state` | audit, UI | append-only |

## Requirements

### 1. Exit triggers

#### XC-C01 Fixed trigger order

`When evaluate_open_positions checks an open Position against a fresh exact mark, position_manager.py::_decide_exit shall return the first matching trigger in this order: stop_loss, limit exit, take_profit, max_hold, thesis_invalidated.`

- stop_loss: price crosses `stop_price` (long at or below, short at or above).
- limit exit: `exit_limit_price` is crossed; it replaces take-profit when set and is attributed
  `human_limit_exit` when `exit_limit_source="human"`, else `operator_limit_exit`.
- take_profit: price crosses `take_profit_price` (only when no limit exit is set).
- max_hold: age ≥ hard backstop = min(planned horizon × `horizon_hard_backstop_mult`, school
  cap), where the planned horizon is the Risk-approved Proposal's `expected_horizon_hours` and
  the school cap is `serenity.max_hold_days × 24` or `trading.max_position_hold_hours`. With no
  readable Proposal horizon (adopted / legacy) the current horizon is used. An Operator
  extension moves only the review wake, never the backstop.
- thesis_invalidated: only `trade_school="serenity"` with `trading.thesis_auto_close` on.

#### XC-C02 Market-aware review only wakes

`Where market_aware_exits.enabled is on and no trigger in XC-C01 fired, the Position Manager shall only wake the System Operator and never close a Position or change a stop.`

- `portfolio/exit_rules.py::evaluate_market_exit_rules` returns `urgent_exit` / `tighten_stop`
  as review suggestions; Position, Order and stop stay unchanged until the Operator decides.

#### XC-C03 Stop and target come from approved values

`The Position Manager shall take stop and take-profit from the Position row, else from its immutable Proposal, and shall never invent them from current config for a native Position.`

- WHEN the native Proposal field is `NULL` THEN that trigger does not exist.
- Only Positions without a Proposal identity, or `source=adopted`, fall back to config
  default percentages (`_effective_stop`, `_effective_take_profit`).
- Venue-triggered native SL/TP closes converge through `tpsl_fill_check`
  (`agent_runtime/scheduler.py::_tpsl_fill_check_body`, 90 s) using the persisted
  `exchange_tpsl_order_id`; the local stop remains the fallback.

#### XC-C04 Fresh exact mark only

`The Position Manager shall trigger an exit only on a fresh mark of the Position's exact venue, symbol and market type.`

- In-memory exact-instrument cache < 90 s (`data/price_feed.py::_STALE_THRESHOLD_SEC`); SQLite
  snapshot of the same venue and market type ≤ 120 s (`_PROTECTIVE_DB_MAX_AGE_SEC`).
- `Position.current_price`, symbol-only, cross-venue, cross-market or stale prices never
  trigger an exit; the exit price comes from the private Fill.

#### XC-C05 Per-position failures are visible

`If a mark lookup, market-aware evaluation or close raises for one Position, then evaluate_open_positions shall continue with the others and report a per-position error.`

- The summary carries stable per-position `errors` and `failed_close_count`; `closed=0` with
  every close failing is not a healthy run.

### 2. Close routing

#### XC-C06 One routed close exit

`The system shall close a single Position only through position_manager.py::close_position_routed.`

- Automatic triggers and the Operator `close_one` both use it; the caller passes no fallback
  price and the fill price comes from the venue's private Fill.
- Every external execution mode goes Gateway → Coordinator → UTA; an unsupported mode raises,
  with no fallback to a legacy broker. Demo closes are real venue closes.
- `reduce_position_routed` fails closed for discretionary partial reduce; partial Fills of an
  authorized full close are materialized by `execution/exchange_materialization.py`.

#### XC-C07 Live closes are human-only

`If the Position's execution mode is live and allow_live is not true, then close_position_routed shall raise before any close is attempted.`

#### XC-C08 Close authority is the Position

`The Coordinator shall create a close Order that consumes the open Position it reduces and has risk_decision_id NULL.`

- `position_id + close_reason + close-{position_id}…` plus the Position's exact
  proposal/instrument/mode/quantity form the authority chain; integrity scans classify
  entry and close Orders separately.

#### XC-C09 Operator rationale travels with the claim

`When a routed close carries decision_reasoning, the Coordinator shall store trade_decision_rationale.v1 on the single Close Order's close_reasoning before the first exchange side effect.`

- A repeat close returns the original claim and never overwrites it.
- The Position stays open with empty `exit_reasoning` until a complete private Fill is
  verified by the shared materializer.
- WHEN a non-empty carrier is corrupt or contradicts `close_reason` THEN
  `materialization_missing`; historical `NULL` carriers stay valid.

#### XC-C10 Operator review scope

`When a Position wake reaches the System Operator, review_portfolio_state shall offer only hold, adjust_position and close_one for the Positions named by the wake.`

- The prompt carries the global open-Position count and global unrealized PnL only when every
  Position has a valid PnL, else `not available`; other Positions' identities never enter it.
- `adjust_position` uses `apply_operator_adjustment`; `close_one` uses
  `close_position_routed` and needs a fresh mark (`fresh_review_mark_unavailable`
  otherwise). An unsupported action such as legacy `reduce_exposure` becomes `hold`.
- The result and wake fingerprint are written to `pi_decisions`; an unavailable model writes
  `review_unavailable`.

#### XC-C11 Horizon review must be answered

`While a HORIZON_DUE Position is open, the Position Manager shall keep re-delivering the review until it is answered by a confirmed close or by adjust_position extending new_horizon_hours strictly beyond the current age.`

- `horizon_wake_sent_at` means "review completed", not "queued"; a restart re-delivers.
- `hold`, empty adjustment, stop/target-only changes and non-extending horizons do not
  confirm. `review_unavailable`, timeout, error or unconfirmed `close_one` retries the same
  `position_id + wake batch` and never records job success.
- A durable pending-close claim confirms; review retries never create a second Close Order.
- WHEN the extended horizon falls due again THEN the manager clears the old confirmation in a
  short transaction, writes `horizon_review_rearmed`, and re-delivers — except while a durable
  pending-close claim exists.

### 3. Close claims

#### XC-C12 Venue-reported absence

`If an adapter can prove from a typed venue code that a close was rejected because no matching position exists, then the Coordinator shall keep the Order as error and park the Position as pending_close_status=position_absence_unverified.`

- Bitget codes: `extensions/exchanges/bitget/profile.py::close_position_absent`
  (`22002`, `25227`, `45110`). Other profiles have no absence codes; plain text rejections
  stay `error`.
- Later cycles never create a new Close Order or touch the venue; the read-only reconciler
  resolves it. The response itself never creates a Fill, PnL or eligible Outcome.

#### XC-C13 Deterministic rejection is not retried

`If a venue deterministically rejects a close for a reason other than proven absence, then the Coordinator shall keep the Order as error and park the Position as pending_close_status=submission_rejected without automatic resend.`

#### XC-C14 Owner-unlock parks

`When a close is cancelled with trade_too_many_times_cancel, or fails before submission with uta_account_identity_mismatch while the venue cannot prove the exact Position, the Coordinator shall park the claim as venue_trade_rate_limited or account_identity_unproven respectively.`

- Both are members of `execution/close_convergence.py::CLOSE_CLAIM_STATES` (the fence holds;
  any second close returns `already_pending`) and are not in `IMMEDIATE_RECONCILIATION_STATES`;
  machine recovery never releases them.
- The owner read model shows `close_convergence_state` and an owner-unlock text that survives
  the stale threshold; past it they still escalate as `stale:<state>`.
- Unlock is only `POST /api/positions/{id}/recover-parked-close`
  (`release_rate_limited_close_claim`), actor=human with an owner reason, and only when the
  park preconditions still hold (rate-limit: Order `cancelled` with matching reason;
  identity: Order `error`/`uta_account_identity_mismatch` with no exchange id). Unlock allows
  one retry with a fresh `close-{id}-rN`; otherwise the request is refused with no change.
  No Fill, price or PnL is produced.

#### XC-C15 Match-cap close fallback (Bitget demo)

`Where the profile declares match_cap_close_fallback and the Position has a prior match-cap cancel, the Coordinator shall submit the next close as a marketable limit at last price ±1% toward the venue counter side.`

- This is a Bitget demo matching-engine workaround; only `extensions/exchanges/bitget/profile.py`
  declares the capability. It should be removed when the demo defect is gone.
- WHEN the reference price cannot be read THEN the close is a market order clipped by
  `coordinator.py::_clipped_close_quantity`: halved once per prior match-cap cancel, floor
  1/64 of the remaining quantity.
- A fully filled clip only reduces the Position to its remainder (`is_partial=1`, claim
  released, next cycle sends the next clip); it never closes a Position that still has venue
  quantity. Replaying a booked clip is a no-op; a partially filled clip keeps the
  `partially_filled` claim.

#### XC-C16 Pending claim keeps protection observing

`While a close claim is pending, the Position Manager shall keep evaluating protection and shall only record that it is waiting when a rule fires again.`

- It never calls an exchange close or creates a replacement Order. The read model exposes
  `close_convergence_state`, `close_convergence_owner_status`, claim age and
  `close_convergence_requires_reconciliation`; missing timestamps or stale claims show
  `manual attention required`.

#### XC-C17 Unique close client id

`When a close claim is allocated, _claim_close shall obtain its client_order_id from pending_close_recovery.next_close_client_oid inside the same immediate writer transaction that sets positions.pending_close_*.`

- `close-{position_id}` when no prior attempt exists, else `close-{position_id}-r{N+1}`.

#### XC-C18 Absent stale close release

`When reconcile runs, recover_absent_stale_close_orders shall release a stale close claim only if a complete open-order view lacks it, exact fill history is empty, and the venue positively reports that exact order terminal with filled_quantity 0 and remaining_quantity equal to original_quantity.`

- A local CAS re-check (Order status/clientOid/exchange id, Position claim fields, zero local
  Fills) precedes release; the retry uses a fresh `close-{id}-rN`.
- A `COMPLETE` read with no match is not evidence; missing or contradictory quantity fields
  fail closed. `manual_reconciliation_pending` and `venue_trade_rate_limited` are never
  candidates. Release and park change local state only.

#### XC-C19 Terminal bound on close-side ambiguity

`When a submission_unknown close claim is 24 hours old (SUBMISSION_UNKNOWN_TERMINAL_HOURS) from pending_close_requested_at, the Position Manager shall set pending_close_status=manual_reconciliation_pending, record one manual-close-reconciliation- audit, raise one P1 MANUAL_CLOSE_RECONCILIATION_REQUIRED wake and record one critical infra state.`

- At 4 h (`PENDING_CLOSE_STALE_HOURS`) the claim is stale: escalate once per claim identity and
  keep retrying.
- At the bound it never changes the Order status, releases the claim, closes the Position or
  infers a fill; a later fill is materialized by ordinary reconciliation.
- A `NULL`, malformed or future `pending_close_requested_at` counts as terminal. The bound is a
  local-DB judgment evaluated for every unresolved claim on each pass.
- Resolution is human-only via `scripts/maintenance/manual_close_resolver.py`, which
  revalidates evidence under the writer lock and records `manual_close_claim_resolved` with
  `actor=human`: venue-confirmed fill (venue fill id required), venue-confirmed absence (Order
  → `submission_rejected`, claim released, next close uses a fresh client id), or
  operator-settled-outside-gateway (audit only).

#### XC-C20 Entry overfill is a normal Position

`When an entry Order's complete private Fills exceed its authorized quantity beyond tolerance, the materializer shall open a normal Position and mark the divergence.`

- Proposal `executed`, Entry Plan converged, `entry_fill_verified` with broker-fill price;
  no quarantine and no admission block.
- The Order keeps `overfilled` + `exchange_quantity_exceeds_authoritative_order`; the same
  transaction writes `positions.entry_quantity_divergence` (`order_id`, `planned_quantity`,
  `filled_quantity`) and one `entry_fill_quantity_diverged` audit; the Operator review shows
  it. Replay is idempotent. Close-side `overfilled` semantics are unchanged.

#### XC-C21 Startup admission reads the ledger

`When the Startup Behavior Gate evaluates pending orders and closes, it shall read only Core orders, open positions.pending_close_status and the reconciliation incident.`

- `submission_unknown`, `submission_rejected`, `materialization_missing`,
  `position_absence_unverified` and close-claim `overfilled` block new AI trading immediately.
- `pending`, `submitted`, partial and the two owner-unlock parks block only past the stale
  threshold. Legacy `ps_*` state grants or blocks nothing.
- Layered pause never stops Position Manager, order reconciliation or verified-exit settlement.

**Close-claim state table**

| Claim state | Meaning | Membership / behavior |
|---|---|---|
| `manual_reconciliation_pending` | terminally ambiguous close, human-owned | In `CLOSE_CLAIM_STATES`; not in `IMMEDIATE_RECONCILIATION_STATES`; not a `position_manager` error. Leaves only via operator resolution or later venue proof. |
| `venue_trade_rate_limited` | Bitget 40725 park, owner-unlock-only | In `CLOSE_CLAIM_STATES`; not in `IMMEDIATE_RECONCILIATION_STATES`; owner text survives stale. Leaves only via `recover-parked-close`. |
| `account_identity_unproven` | unprovable account identity before close submission, owner-unlock-only | Same as above; unlock needs the Order still `error`/`uta_account_identity_mismatch` with no exchange id. |
| `submission_unknown`, `submission_rejected`, `materialization_missing`, `position_absence_unverified`, `overfilled` | reconciliation required | In both sets; block startup admission immediately. |

### 4. Pause and kill switch

#### XC-C22 Pause and kill switch never block closes

`While runtime.paused or the kill switch is active, the system shall keep managing and closing existing Positions and block only new entries.`

- The kill switch is Risk check 0 and applies only to Proposals (opening).
- `_run_position_manager`, `_run_position_reconcile` and `_run_reconciliation` have no paused
  gate, and `position_manager`, `reconciliation`, `position_reconcile`, `tpsl_fill_check` are in
  `agent_runtime/scheduler.py::_ESSENTIAL_JOBS`.

### 5. Manual close

#### XC-C23 Demo manual close

`When the owner closes a demo or testnet Position through POST /api/positions/{id}/close, the system shall send a real venue close order and attribute it to the human.`

- `market` closes now and converges on the confirmed Fill (`close_reason=human_manual_close`);
  `limit` rests `exit_limit_price` with `exit_limit_source='human'` and triggers as
  `human_limit_exit` (local watch, OCO with the stop). `NULL` `exit_limit_source` means
  `system_operator`. Actor `human` keeps manual closes out of AI exit statistics.

#### XC-C24 Live manual close is refused

`If the Position's mode is *_live_limited, then the manual close route shall return HTTP 409 live_position_requires_exchange_close.`

- Live Positions are closed on the exchange UI; adding a Live close path crosses the trust
  boundary ([trust §6](../../governance/policies/trust_and_change_control.md)).

### 6. Verified and administrative close

`positions.status='closed'` ends the local lifecycle; it does not by itself prove an exit fill.

| Path | Required evidence | Allowed effects | Outcome |
|---|---|---|---|
| verified strategy close | verifiable exit fill for the current broker/account/instrument; futures also need a trusted funding ledger for net economics | real fee, slippage; net PnL and outcome-only hook only when economics complete | eligible once funding and other gates are complete |
| administrative close | durable proof the Position is gone, no verifiable exit fill | state convergence, audit, alert | never eligible; no economic fields |

#### XC-C25 Disappearance confirmation

`The system shall treat an exchange Position as gone only after two independent complete same-account absent views, or a direct broker report of confirmed_absent_without_fill or verified_fill.`

- Stale, incomplete, missing broker identity, wrong-account, partial pagination, network and
  permission errors are `inconclusive` and never count. Candidates survive restarts; one
  observation counts once; reappearance closes the candidate as `reappeared`
  (`portfolio/disappearance_confirmation.py`). A ready candidate never closes by itself.
  Live stays alert-only.

#### XC-C26 Broker-neutral verified exit

`When a ready demo candidate is settled, the system shall query the UTA VerifiedExitReport and accept exactly three results: verified_fill, confirmed_absent_without_fill, inconclusive.`

- The query identity includes venue, market type and account environment; it reads the
  same-account Position directly, then pages private fills over an explicit window.
- Missing or repeated cursors, insufficient retention, permission/network errors, missing
  fields, direction or quantity ambiguity → `inconclusive` (the Position stays open).
- `verified_fill` needs current absence, complete history and an opposite fill covering the
  local quantity; `confirmed_absent_without_fill` needs complete history with no fill. Raw
  private responses are not stored; normalized results carry no credential material.

#### XC-C27 Administrative close needs a fresh direct query

`If the verified-exit result is confirmed_absent_without_fill, then settlement shall close administratively only after a fresh exact same-identity Position query still shows absence, recording direct_query_state=confirmed_absent.`

- Position present → stays open (next classify marks `reappeared`); query unavailable → defer.
  `verified_fill` does not need this extra query.

#### XC-C28 Administrative close side-effect ban

`The administrative close entry portfolio/outcome_closure.py::close_position_administrative shall not create any economic fact.`

- It never fabricates an exit fill from entry/mark/current price; never adds fees, slippage
  or realized strategy PnL; never triggers calibration, shadow resolution, strategy KPI,
  Learning or reviewer hooks; and is never an authoritative account loss for Risk.
- It keeps causal ids, actor/source, the confirmation basis and an audit event; realized PnL,
  exit fee and slippage stay unavailable and current/entry price are unchanged.
- An active reconciliation incident blocks settlement first.

#### XC-C29 Verified close entry

`When a verified exit is accepted, portfolio/outcome_closure.py::close_position_verified shall require the complete scoped identity, the exact quantity and verified_fill.`

- A proven exit fill closes exposure and lifecycle; missing fee amount, fee currency, or
  unconvertible third-currency fees are stored as observed and leave quote exit fee, final PnL
  and Outcome unavailable/ineligible. `NULL` fee is not zero; zero fee is a fact; base-currency
  fees convert at the fill price. Quantity/identity mismatch or malformed numbers fail closed.
- Economic commit is owned by the [outcome integrity contract](outcome_integrity_contract.md).

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| mark missing / stale / cross-venue | no exit trigger | per-position `errors` | never `current_price` |
| one Position's close raises | continue others | `failed_close_count`, job error | `closed=0` not healthy |
| venue says no position (typed code) | park | `position_absence_unverified` | no Fill / PnL |
| deterministic venue rejection | park, no resend | `submission_rejected` | — |
| Bitget 40725 / identity unproven | park, owner unlock | `venue_trade_rate_limited` / `account_identity_unproven` owner text | — |
| close `submission_unknown` ≥ 24 h | human-owned | `manual_reconciliation_pending`, P1 wake | no inferred fill |
| verified-exit read inconclusive | Position stays open | candidate unchanged | not "gone" |
| administrative close | close without economics | `administrative_close` | PnL / fee `NULL` |
| live Position close without authority | raise / HTTP 409 | `live_position_requires_exchange_close` | — |

## Authority

The close path may submit reduce-only close orders for existing Positions on admitted modes and
park or release its own claims under these rules; it may never open exposure, create a
RiskDecision, close a Live Position autonomously, or fabricate Fills, prices or PnL.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| XC-C01 | `PL-PM-02`, `PL-PM-03`, `PL-PM-04` (`tests/specs/spec_pipeline_position_manager.py`); `tests/test_horizon_exit.py`, `tests/test_manual_close_route.py` |
| XC-C02 | `PL-PM-14` |
| XC-C03 | `PL-PM-09`; `tests/test_exit_plan.py` |
| XC-C04 | `PL-PM-10`, `PL-PM-13` |
| XC-C05 | `PL-PM-11`, `PL-PM-12` |
| XC-C06 | `tests/test_position_manager.py` |
| XC-C07 | `PL-ENRICH-04` (`tests/specs/spec_pipeline_entry_enrichment.py`) |
| XC-C08 | `tests/uta/test_execution_coordinator.py`, `tests/specs/spec_pipeline_exchange_materialization.py` |
| XC-C09 | `tests/uta/test_execution_coordinator.py` |
| XC-C10 | `tests/test_agent_shell.py` |
| XC-C11 | `tests/test_horizon_exit.py`, `tests/test_agent_shell.py` |
| XC-C12 | `PL-RX-22`, `PL-RX-24` |
| XC-C13 | `tests/test_exchange_submission_integrity.py`, `tests/test_pending_close_recovery.py` |
| XC-C14 | `tests/test_pending_close_recovery.py`, `tests/uta/test_execution_coordinator.py` |
| XC-C15 | `tests/test_clipped_close.py` |
| XC-C16 | `PL-PM-15` |
| XC-C17 | `tests/test_close_client_oid_uniqueness.py`, `tests/audit/test_close_client_oid_transaction_guard.py` |
| XC-C18 | `tests/test_pending_close_recovery.py` |
| XC-C19 | `tests/test_close_terminal_ambiguity.py`, `tests/test_manual_close_resolver.py` |
| XC-C20 | `tests/test_entry_overfill.py` |
| XC-C21 | `PL-RX-23` |
| XC-C22 | `PL-PM-07`; `tests/test_position_manager.py` |
| XC-C23, XC-C24 | `tests/test_manual_close_route.py` |
| XC-C25 | `OI-B01` (`tests/specs/spec_outcome_integrity.py`); `tests/test_disappearance_confirmation.py` |
| XC-C26 | `OI-B02`; `tests/test_outcome_closure.py` |
| XC-C27, XC-C28 | `OI-B03`; `tests/test_outcome_closure.py`, `tests/specs/spec_pipeline_live_disappearance_settlement.py` |
| XC-C29 | `OI-B03`, `OI-C06`; `tests/test_outcome_closure.py` |

## Decision references

- Horizon backstop anchored to the planned horizon; horizon review must-answer: position-d0 PR #3894.
- Typed absence rejection and startup admission: `PL-RX-22`…`PL-RX-24` lineage.
- Bitget 40725 owner unlock and demo match-cap workaround: measured 2026-08-22 on Bitget demo
  ([history](../history/execution_contract_retired_narrative.md#former-102-bitget-demo-measurement-narrative)).
- `account_identity_unproven` park: #3507, owner 2026-10-09.
- Entry overfill as normal Position with divergence mark: #2084, owner 2026-10-09.
- Fresh direct query before administrative close: added after the 2026-08-19 incident.
- Market-aware review wake-only; retired `ruleset_exit` trigger (#105) and Settings-copy
  deviation (#417): [history](../history/execution_contract_retired_narrative.md#former-101-notes).
- Old local protection barrier: [history](../history/local_simulated_protection.md).
- Owner runbook: [manual close reconciliation](../../operations/runbooks/manual_close_reconciliation.md).
