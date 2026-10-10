# Execution Gateway Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `EX` · **Owner code:** `core/aletheia/execution/gateway.py`
> **Invariant source:** [trust_and_change_control §1, §6.3](../../governance/policies/trust_and_change_control.md).
> Paths below are relative to `core/aletheia/` unless they start with a top-level directory.

## Scope

This contract owns the entry half of the trading mainline from an immutable Proposal to the
exchange and back: Risk-approved Proposal → Gateway claim → Order → private Fill → Position,
plus native entry protection, order and position reconciliation, and the execution audit
trail. The product route is USDT-margined perpetuals on `bitget_demo`; Live is off.
It does not own exit triggers, close routing or close claims
([exit and close contract](exit_and_close_contract.md)), close economics and Outcome
eligibility ([outcome integrity contract](outcome_integrity_contract.md)), Risk checks
([risk engine contract](risk_engine_contract.md)), or Entry Plan compilation
([entry plan contract](entry-plan-contract.md)).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/execution/gateway.py::execute_existing_proposal` | call | `execution/entry_plan_execution.py::execute_entry_plan` |
| `core/aletheia/execution/gateway.py::submit_proposal` | call (debug only) | `core/aletheia/cli/dev.py` (`aletheia dev test-order`, demo modes only) |
| `core/aletheia/execution/coordinator.py::execute_order` | call | `gateway.py::_execute_proposal` |
| `core/aletheia/execution/coordinator.py::replace_protective_stop` | call | `execution/stops.py::replace_exchange_protective_stop` (trailing / Operator adjustment) |
| `core/aletheia/portfolio/native_protection.py::verify_native_protection` | scheduler job `tpsl_fill_check` (90 s) | `agent_runtime/scheduler.py::_tpsl_fill_check_body` |
| `core/aletheia/execution/reconciliation.py::reconcile` | scheduler job `reconciliation` (2 min) | `agent_runtime/scheduler.py::_run_reconciliation` |
| `core/aletheia/portfolio/position_reconciler.py::run_reconcile` | scheduler job `position_reconcile` (5 min) | `agent_runtime/scheduler.py::_run_position_reconcile` |
| `core/aletheia/execution/stale_order_cleanup.py::cleanup_stale_orphan_orders` | scheduler job `cleanup` (daily) | `agent_runtime/scheduler.py::_run_cleanup` |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `trade_proposals` | `gateway.py` | Risk, Gateway, reconciliation, UI | hash-covered intent immutable; `status` monotonic |
| `proposal_execution_claims` | `gateway.py::execute_existing_proposal` | Gateway | replaceable lease, owner-token CAS release |
| `risk_decisions` | `risk/engine.py::evaluate_proposal` | Gateway, audit | write-once |
| `orders` | `execution/coordinator.py` | reconciliation, Risk working set, UI | monotonic status; `uta_account_identity_json` set before submit |
| `fills` | `execution/exchange_materialization.py` | Position, PnL | append-only |
| `positions` | `execution/exchange_materialization.py`, `portfolio/position_reconciler.py` | Position Manager, Risk, UI | `open → closed` |
| `position_disappearance_candidates`, `position_disappearance_observations` | `portfolio/disappearance_confirmation.py` | disappearance settlement | candidates monotonic, observations append-only |
| `audit_events`, `data/logs/audit.jsonl` | `files/jsonl.py::write_audit`, `execution/post_commit_audit.py::write_post_commit_audit` | audit chain verifier, UI | append-only, SHA-256 chained |

## Requirements

### 1. Entry points and causal chain

The opening chain is Order Intent (physical `TradeProposal`) → authoritative `RiskDecision` →
Order → private Fill → Position. A close Order consumes the open Position it reduces and never
a copied entry `RiskDecision` (see [exit and close contract](exit_and_close_contract.md)).

#### EX-C01 Sole order path

`The Execution Gateway shall be the only path by which an order reaches an exchange.`

- WHEN an agent, dashboard route or messaging channel wants to trade THEN it goes through
  `gateway.py`; no other module calls an exchange order API.
- WHEN the Gateway evaluates an order THEN `risk/engine.py::evaluate_proposal` is the sole hard
  gate; no advisory guard chain is wired into `gateway.py`.

#### EX-C02 Risk before execution

`When a Proposal is submitted, the Gateway shall obtain an authoritative RiskDecision before any broker call.`

- WHEN `submit_proposal` is called THEN it persists the Proposal and delegates to
  `execute_existing_proposal`, never to `_execute_proposal` directly.

#### EX-C03 Native source only

`If a caller passes a Proposal source other than native, then the Gateway shall reject it before persisting a Proposal, claiming a lease, calling Risk or calling a broker.`

- WHEN `submit_proposal(source="adopted")` THEN it returns `non_native_proposal_not_executable`
  and no Proposal row exists.
- WHEN `execute_existing_proposal` loads a Proposal whose `source != "native"` THEN it rejects
  before the claim. `source="adopted"` rows exist only as synthetic facts persisted by the
  Position Reconciler.

#### EX-C04 One Proposal row per trade

`The Gateway shall create exactly one trade_proposals row per proposed trade.`

- WHEN a Proposal is approved, rejected, Risk-evaluated or executed THEN the same row's `status`
  changes; no second row is created.

#### EX-C05 Entry Plan binding

`When an Entry Plan Proposal is executed, the Gateway shall require one typed EntryExecutionReceiptV1 whose proposal id, plan id and hash match the stored Proposal.`

- WHEN the receipt is missing or malformed THEN `entry_execution_receipt_required`.
- WHEN its identity or hash differs THEN `entry_execution_receipt_integrity_failed`.
- WHEN a receipt is passed for a Proposal without `entry_plan_id` THEN
  `unexpected_entry_execution_receipt`.

#### EX-C06 Exchange order rules before submission

`When a Risk-approved Proposal targets an exchange, the Gateway shall read the receiving venue's current order rules, align the quantity, re-run Risk on the aligned quantity, and only then call the broker.`

- WHEN minimum quantity, step, minimum notional, maximum quantity or contract-size conversion
  applies THEN the coin quantity is rounded up to a legal step and re-evaluated by Risk.
- WHEN rules are missing, stale and unrefreshable (5-minute cache,
  `execution/order_constraints.py::_CACHE_TTL_SECONDS`), the market is inactive or precision
  cannot be converted safely THEN the Proposal is `rejected` with a
  `ConstraintUnavailable.reason_code`, an audit row is written and no Order is created.

### 2. Chain data structures

| Link | Model (`storage/models.py`) | Status flow |
|---|---|---|
| Order Intent | `TradeProposal` — internal immutable Risk/Gateway carrier | `pending/approved → executing → executed`, `→ rejected`, `→ error`, `submission_unknown`, `materialization_missing` |
| RiskDecision | `RiskDecision` ([risk engine contract](risk_engine_contract.md)) | write-once |
| Order | `Order` — entry carries `risk_decision_id`; close carries `position_id`, `close_reason`, optional `close_reasoning`, `risk_decision_id=NULL` | `pending → submitted → partially_filled → filled`, `→ error`, `→ cancelled` |
| Fill | `Fill(order_id, fill_price, fill_quantity, fee)` | immutable |
| Position | `Position` + entry context + `exit_reasoning` | `open → closed` |

#### EX-C07 Proposal status meaning

`The Gateway shall use each Proposal status only with its defined meaning.`

- `pending` = no human approval; `approved` = human or permitted workflow approval;
  `executing` = external submission may have started; `executed` = Order, Fill and Position
  are all persisted; `rejected` = human or Risk refusal; `error` = terminal failure confirmed
  not submitted.
- `submission_unknown` = the exchange boundary may have been crossed; reconcile first, never
  resubmit automatically. `materialization_missing` = an Order exists or the venue reported
  fill activity but local Fill/Position is incomplete; never resubmit.
- WHEN execution happened THEN an Order row exists; WHEN Risk ran THEN a RiskDecision row
  exists. A status change never substitutes for a missing record.

#### EX-C08 Risk and broker see one intent

`The Gateway shall pass to the broker only the Risk-approved execution copy of the Proposal.`

- WHEN exchange rules change only the quantity THEN the RiskDecision records
  `evaluated_quantity` and binds the original `proposal_commit_hash`.
- WHEN any other hash-covered field differs from the stored Proposal THEN execution fails
  before Risk and no authoritative RiskDecision or Order is produced.

### 3. Execution mode and broker

| Mode | State | Path |
|---|---|---|
| `bitget_demo` | the only route admitted for new entries ([Active Runtime Scope](../../product/ACTIVE_RUNTIME_SCOPE.md) `execution_modes`) | Gateway → Coordinator → UTA v3 |
| `okx_demo`, `binance_testnet` | frozen: no new entries; residual adapters kept only for historical identity and recovery of existing exposure | same |
| `*_live_limited` | Live off; reachable only through [Live Unlock](live_unlock_contract.md); Entry Plans never route here | same |

#### EX-C09 Entry route admission

`The Gateway shall execute Entry Plan orders only for an execution mode registered in config/exchanges.py::ENTRY_ROUTE_POLICIES.`

- WHEN `venue_capabilities.entry_execution_minimum_permission(mode)` is `None` THEN the Gateway
  rejects with `entry_plan_tactics_unavailable` and `execute_entry_plan` raises
  `entry_plan_tactic_capability_unavailable`. Today only `bitget_demo` is registered.
- WHEN the startup gate (`agent_runtime/startup_behavior_gate.py::_check_active_runtime_scope`)
  runs THEN only modes listed in Active Runtime Scope gain new-entry admission.
- `risk/engine.py::_is_live` identifies `*_live_limited`.

### 3.0a Entry Plan Order Policy

#### EX-C10 Entry Plan order tactics

`When an Entry Plan Proposal reaches the Gateway, the Gateway shall submit only a price-bounded Limit order whose tactic semantics are complete.`

- WHEN `order_type != "limit"` THEN `entry_plan_requires_limit_order`.
- WHEN tactic is `POST_ONLY_LIMIT` without `post_only=true` and `time_in_force="post_only"`
  THEN `entry_plan_requires_post_only`; a plain GTC Limit is never a Maker order.
- WHEN tactic is `OPPOSING_BBO_IOC` without `post_only=false`, `time_in_force="ioc"`, a
  `bbo_reference` of `bid`/`ask` and `max_slippage_bps` THEN `entry_plan_requires_bounded_ioc`.
  Out-of-bound `max_slippage_bps` or `worst_price` rejects; the Gateway never chases and
  Market orders are not in Entry Plan authority.
- WHEN the plan is no longer `active` THEN `entry_plan_lifecycle_closed`.
- The Risk reservation exists before the Order is persisted; Order insert and Entry Plan
  `risk_reserved → ordered` advance in one SQLite writer transaction.

#### EX-C11 Entry cancel convergence

`When a cancel is fenced (error_reason starts with entry_cancel_pending:) and exact venue evidence shows the order still working, the Gateway shall resend the cancel idempotently under the same client_order_id on a backoff schedule.`

- WHEN inside the backoff window (`orders.entry_cancel_retry_after`) THEN the Gateway returns
  `cancel_pending` without touching the venue.
- WHEN the sixth attempt is made THEN one `entry_cancel_stalled` audit event is written; the
  Order and Entry Plan stay in durable reconciliation and no fill is inferred.
- WHEN a full Fill wins the race THEN the Fill stands; a partial Fill keeps the real Position
  and the remainder may be cancelled. Request-side `requested_liquidity` never stands in for
  `realized_liquidity`.

### 3.0b Account identity relation

#### EX-C12 One identity relation

`The execution layer shall compare UTA account identities only through uta/identity.py::identity_relation.`

- Identity = account facts (`account_id`, `execution_mode`, `venue`, `environment`,
  `profile`, `sub_accounts`) plus `credential_generation`. `adapter_implementation`,
  `adapter_version`, `ccxt_version` and `account_fingerprint` are provenance only.
- Results are `SAME`, `GENERATION_DRIFT` or `DIFFERENT`; a CCXT or adapter upgrade alone
  never produces `DIFFERENT`. A persisted Bitget `classic_v2` profile is `DIFFERENT`.
- WHEN `GENERATION_DRIFT` THEN rows may be re-anchored (audit `uta_account_reanchored`) only
  after a complete venue read proves continuity: the venue holds that exact open Position, or,
  with no open Position, a complete exact read returns an Order of the old generation that is
  filled locally. A suspect Order merely not found is not proof.
- WHEN no proof, `DIFFERENT`, unbound or malformed identity THEN fail closed: no relabel, no
  submit. Proof capability comes from the venue profile (`client_order_lookup_param`,
  `private_history_retention_days`).

### 3.1 Instrument scope at the adapter boundary

#### EX-C13 USDT perpetuals only

`The execution layer shall accept only canonical market_type=usdt_futures instruments taken from the canonical Candidate / Entry Plan.`

- Spot and venue RWA perpetuals (Bitget `isRwa=YES`) never enter the universe registry
  (`data/symbols.py`, `universe/registry.py`), so they produce no Candidate, Entry Plan or
  Proposal. There is no fallback market-type selection.
- Venue differences (contract size, client id, protection parameters) are handled only in
  `extensions/exchanges/{venue}/profile.py`; Bitget has one profile, UTA v3.
- `spot` branches still present in Core and Risk are residual and unreachable from the
  mainline; they are not a support claim.

### 3.2 Symbol Availability Pre-check

#### EX-C14 Symbol availability pre-check

`When Risk has approved a Proposal for an exchange mode, the Gateway shall reject a symbol that the venue's normalized capability set does not list for that market type.`

- WHEN `gateway.py::_check_symbol_availability` finds the symbol absent THEN the Proposal is
  `rejected` (not `error`), the Entry Plan fails with `symbol_not_available_on_venue`, and a
  `proposal_skipped` audit row is written.
- Sandbox modes read `venue_capabilities.refresh_demo_capabilities` (cold cache refreshes
  synchronously and fails closed); non-sandbox modes read `universe_registry` via
  `load_registry_symbols`. Both caches live 4 h.
- WHEN a non-sandbox profile or registry is unavailable THEN the pre-check returns no verdict
  (fail-open); `read_order_constraints` (EX-C06) still fails closed independently.
- `agents/`, `universe/`, `trading/` and `risk/` consume only `execution.venue_capabilities`
  facts and never import an exchange client.

### 3.3 Exchange minimum order rules

See EX-C06. The venue may still reject for price movement, balance or position mode; its final
receipt is the source of truth.

### 3.4 Worktree preview runtime

Operating rules moved to
[DEVELOPMENT_PREVIEW](../../operations/development/DEVELOPMENT_PREVIEW.md#execution-gateway-refusal).

#### EX-C15 Preview refuses execution

`While the process runs in Preview mode, the Gateway shall reject submit_proposal and execute_existing_proposal before any durable execution state changes.`

- WHEN `config/loader.py::is_preview_mode` is true THEN `gateway.py::_reject_preview_execution`
  raises `PreviewExecutionBlockedError` before Proposal, claim, RiskDecision, Order or broker.

### 4. TPSL exchange protection

#### EX-C16 Protection rides on the entry order

`When an entry is submitted with an approved stop or target, the execution layer shall carry that protection as a preset on the entry order itself.`

- SL/TP prices are the approved Proposal/Position values; none are invented.
- WHEN the profile lacks native TPSL THEN no success is faked and local protection remains.
- `execution/stops.py::native_tpsl_enabled`: unset `trading.exchange_tpsl_enabled` follows
  `ENTRY_ROUTE_POLICIES[...].native_tpsl_default` (only `bitget_demo`); explicit true/false
  applies to every mode.

#### EX-C17 Pre-send preset guard

`If native TPSL is enabled for a futures entry that carries stop_loss_price or take_profit_price, then the adapter shall refuse to send unless the built venue request carries those trigger prices as strings equal to the approved values.`

- WHEN the guard fails THEN `NOT_SENT` + `native_tpsl_params_missing`, the Order ends `error`
  and is audited (`extensions/exchanges/ccxt/adapter.py::submit_order`).
- WHEN the venue accepts THEN the `uta_command_applied` audit records
  `protection_source=entry_preset_ack`. Reduce-only and exit orders are outside this guard.

#### EX-C18 Preset read-back is bookkeeping only

`The tpsl_fill_check job shall record venue preset ids only from a complete read with SAME identity and shall never close a Position.`

- WHEN exactly one same-account, same-direction SL (and at most one TP) exists THEN its id is
  written to `positions.exchange_tpsl_order_id` with `origin=entry_preset` and a
  `native_protection_verified` audit is written.
- WHEN a complete read still finds no SL after `execution/stops.py::PROTECTION_GRACE_SECONDS`
  THEN only `native_protection_absent` is audited and the job turns red.
- WHEN the read fails, is incomplete, identity is not `SAME`, or SL count is ambiguous THEN
  nothing is written.

#### EX-C19 Stop amendment in place

`When a trailing or Operator adjustment tightens a stop, the execution layer shall amend the existing venue stop in place and never cancel-and-replace it.`

- `coordinator.py::replace_protective_stop` sends only when exactly one target strategy order
  of this symbol exists (else `NOT_SENT` + `modify_target_unavailable`); the request restates
  every leg so a shared TP survives (`extensions/exchanges/bitget/profile.py::protection_amend_request`).
- WHEN the amendment loosens the stop THEN `protection_amend_loosens`; limit-type stops are
  not amended; profiles without amend support return `protection_amend_unsupported`.
- Only a venue ACK is success (`native_protection_amended`); otherwise the old venue stop stays,
  the result is False and `native_protection_amend_failed` is audited.
- The tighter local stop is persisted regardless and reported as `exchange_synced` /
  `venue_stop_synced` (`portfolio/position_manager.py::apply_operator_adjustment`,
  `agent_shell/commands.py::review_portfolio_state`).

### 5. Atomicity, idempotency and leases

#### EX-C20 Durable execution lease

`When execute_existing_proposal claims a Proposal, it shall write one proposal_execution_claims row and move the Proposal to executing inside one short BEGIN IMMEDIATE transaction that ends before Risk or broker I/O.`

- WHEN a fresh lease exists THEN `already_claimed`; no Risk, broker or Order.
- WHEN an exchange-mode lease expired with the Proposal `executing` THEN the Proposal becomes
  `submission_unknown` and the call returns `reconciliation_required`
  (`execution_lease_expired_exchange_reconciliation_required`); nothing is resent.
- Release is a compare-and-set on the owner token; an old owner cannot delete a newer lease.
  Lease fields are not part of the hash-covered Proposal.

#### EX-C21 Local record before exchange call

`The Coordinator shall insert Order(pending) with the exact non-secret uta_account_identity_json before calling the exchange.`

- Sequence: insert Order(pending) → call exchange → backfill `exchange_order_id` and status →
  insert Fill and Position (recoverable on failure). The exchange never holds a fill with no
  local trace.
- A historical `NULL` identity is never back-filled from current config; such orders block new
  entries and unprovable private operations on that account.
- `SubmitOrderCommand.reference_price` is the approved reference, not a market limit price; a
  preparation failure is `NOT_SENT`.

#### EX-C22 Pending orders occupy risk

`While an Order is pending or otherwise unresolved, Risk shall count it in the working set.`

- It occupies an entry slot and same-symbol right in its pool, and its quantity not yet in an
  open Position counts toward notional exposure. Close Orders do not consume entry capacity.

#### EX-C23 Accepted means crossed

`When the broker returns an accepted result and the native Order is persisted, the Gateway shall never report that Proposal as error.`

- Recovery prefers the identity-linked durable Order (`submitted` / `partially_filled`);
  `executed` only when the same `filled` Order has a unique, non-zero, id-matched Fill and
  Position.
- WHEN evidence is unreadable, incomplete or the status write is interrupted THEN
  `submission_unknown` / `materialization_missing`, no automatic resend, and no raw exception
  text in logs/API. Repeated read failure is "evidence unavailable", not "no accepted Order".

#### EX-C24 Bounded failure codes

`The Gateway shall store an Entry Plan terminal_reason only as a bounded token (token or token:token, lowercase letters, digits, underscore).`

- WHEN a value does not match THEN it is stored as `entry_failure_code_invalid`.
- Venue-rule failures use `ConstraintUnavailable.reason_code`; order exceptions use
  `demo_order_failed:<code>` (`unclassified` when none).

#### EX-C25 External calls outside the writer lock

`The Coordinator shall make every UTA mutation outside the SQLite writer transaction and apply its result with a short expected-state compare-and-set.`

- The Gateway is the only business write authority; UTA never writes the Aletheia DB.
- WHEN a mutation may have been sent but the outcome is partial, unsupported, unavailable,
  timed out or raised THEN it stays ambiguous and is never retried automatically.

### 6. Reconciliation

#### EX-C26 Reconciliation never trades

`The reconciliation path shall never place or cancel an order and never insert a Fill outside the shared materializer.`

- `execution/reconciliation.py` imports no order-placement function; it compares state and
  writes convergence and audit records only.
- Only a complete, identity-matched private Fill read through UTA enters
  `execution/exchange_materialization.py`.

#### EX-C27 No inference from absence

`If an open-order list misses an order, paginates to its cap, or reports unsupported, unavailable or partial, then reconciliation shall keep the order unresolved.`

- Missing from an open list never means filled or cancelled; the only bounded terminations
  are EX-C33 (entry side) and the close-side rules of the
  [exit and close contract](exit_and_close_contract.md).

#### EX-C28 Typed read incompleteness

`If a UTA read cannot prove ReadCompleteness.COMPLETE, then it shall raise execution/errors.py::IncompleteReadError, never a bare RuntimeError.`

- The exception carries `reason_code`, `completeness`, `endpoint`, `execution_mode`,
  `market_type`, `symbol`, so callers can tell structural `PARTIAL`/`UNSUPPORTED` from
  transient `UNAVAILABLE` without matching text. It subclasses `RuntimeError`.
- Every `logger.warning` in an `except … as exc:` handler around such a read logs
  `type(exc).__name__` and `reason_code`.
- Typing grants no authority: never a reason to retry, synthesize an empty or zero result,
  or treat an incomplete read as absence. A `COMPLETE` read with no match proves only that the
  read window completed. Exact order lookups supply `start_at` from the order's `created_at`
  minus `reconciliation.py::_ORDER_LOOKUP_SKEW`; an order older than the profile's
  `private_history_retention_days` still answers `PARTIAL`.

#### EX-C29 Position matching identity

`The Position Reconciler shall match a local and venue Position only on execution_mode + broker_id + account_environment + canonical symbol + normalized market_type + direction, with matching account fingerprints when both carry one.`

- External accounts compare only Positions of the current execution account; archived local
  accounts and unregistered modes (`unconfigured`) are not classified or closed.
- Live scope derives `*_live_limited` from `exchange.name`; Live stays alert-only.
- Every non-zero row in the unified Demo snapshot needs a `broker_id`; missing account labels
  degrade to unavailable, never to "local Position gone".
- WHEN either side has a duplicate full key or a missing/unknown market type THEN the identity
  goes to typed `integrity_issues`: no last-row-wins, no drift, no disappearance evidence, no
  adoption; input order never changes the result.

#### EX-C30 Adoption needs valid leverage

`The Position Reconciler shall adopt an external futures Position only when its leverage is finite, positive and integral.`

- Adapters keep missing or invalid leverage as `leverage=None` with
  `leverage_source=unavailable/invalid`; never a default 1x.
- Adoption writes the verified value to the synthetic Proposal, Position and credential-free
  `reconciliation_identity` / `reconciliation_evidence`. A later complete exact-account Demo
  view may fill missing leverage on the same Position id; an incomplete view never clears it.
- `position_reconciler.py::report_invalid_open_position_exposures` lists invalid open
  Positions read-only and repairs nothing.

#### EX-C31 Venue uPnL attribution

`The Position Reconciler shall copy a venue uPnL onto a local Position only when the scoped identity is unique on both sides and the cache is ok.`

- Exposed as `unrealized_pnl_source`: `exchange_exact`, `ambiguous`, `unavailable` or
  `local_estimate`. A broker Position identity must match within the same fingerprint scope;
  duplicates are `ambiguous`. A `stale` or undeterminable cache falls back to the local mark
  estimate and is never persisted. Legacy rows without fingerprint or broker identity keep the
  single-candidate path and are not Demo qualification evidence.

#### EX-C32 Disappearance is evidence, not a close

`When a fresh complete snapshot lacks a local Position, the Position Reconciler shall record a position_disappearance_candidates row and one immutable observation and shall not close the Position.`

- Repeated cache, time regression, stale, incomplete or wrong-account views do not count; a
  second newer complete view marks the candidate ready for verified exit; reappearance closes
  the candidate as `reappeared` with an audit row.
- The logical account fingerprint comes only from non-secret broker/environment/mode config,
  never from credentials. Ready candidates are settled only by the exit and close contract.

### 6.1 Entry-side bounded termination

#### EX-C33 Stale entry order termination

`When an entry Order is at least 48 hours old, has no Fill, open Position or in-flight close, and a complete exact read under a provable identity shows no fill and no order or a terminal order, the cleanup shall terminate the entry chain in one transaction.`

- Conditions: `stale_order_cleanup.py::_STALE_ORDER_SOFT_HOURS`, `_has_real_economics`, and a
  `ReadCompleteness.COMPLETE` lookup by `client_order_id`/`venue_order_id` with `start_at`.
- Effect: Order → `cancelled`; a `submission_unknown`/`executing` Proposal → `error`; a
  non-terminal Entry Plan whose latest leg is this Order → `failed` with
  `terminal_reason=venue_confirmed_absence`; one `order_stale_cancelled` audit listing them.
- WHEN the read is incomplete, unavailable or identity continuity is unprovable THEN the Order
  stays unresolved. Close-side ambiguity is never auto-terminated.
- The reconciler logs an inconclusive verdict only when its reason changes.

### 7. Audit

#### EX-C34 Audit records

`The execution layer shall write an audit record for every significant state transition with timestamp, actor, source, event_type, entity_type, entity_id and message, and never a secret or signature.`

- `entity_id` points at the row actually changed (fields per
  [trust §1.4](../../governance/policies/trust_and_change_control.md)).

#### EX-C35 Post-commit audit isolation

`If the JSONL audit write fails after the execution boundary has been crossed, then the execution layer shall log a warning with the exception type only and keep the committed state.`

- SQLite rows (Order/Fill/Position/Proposal, `submission_unknown`, `materialization_missing`,
  `position_absence_unverified`) are authoritative; JSONL is secondary.
- Entry and close paths use `execution/post_commit_audit.py::write_post_commit_audit`, which
  wraps only the JSONL sink. A failure never rewrites state to `error` or triggers a resend;
  a close keeps `pending_close_status` and a repeat close returns the durable claim.

### 8. Audit hash chain

Each audit line carries the SHA-256 of the previous line (`_prev_hash`), written and verified
in `core/aletheia/files/jsonl.py`; the rules are in [audit chain contract](audit_chain_contract.md) §3.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| non-native source, preview mode | reject before any durable write | `non_native_proposal_not_executable`, `PreviewExecutionBlockedError` | no row |
| fresh lease held | return without side effect | `already_claimed` | — |
| exchange lease expired while `executing` | block resend | `submission_unknown`, `reconciliation_required` | unknown, never "not sent" |
| venue rules unavailable | reject Proposal, no Order | `ConstraintUnavailable.reason_code`, audit | — |
| symbol not listed | reject Proposal | `proposal_skipped` audit | non-sandbox lookup unavailable → no verdict |
| broker accepted, local read fails | keep crossed state | `submission_unknown` / `materialization_missing` | never `error` |
| incomplete UTA read | stay unresolved | `IncompleteReadError.reason_code` in `errors.log` | never empty / zero / absent |
| preset params dropped | `NOT_SENT`, Order `error` | `native_tpsl_params_missing` | — |
| stop amend refused | old venue stop stays, local stop tighter | `native_protection_amend_failed`, `venue_stop_synced=False` | — |
| uPnL unattributable | local estimate | `unrealized_pnl_source=ambiguous/unavailable` | never copied aggregate |
| JSONL audit failure after commit | warning, state kept | warning with exception type | — |

## Authority

The Gateway may submit, cancel and amend orders for Risk-approved native Proposals and close
Orders on admitted execution modes; it may never change Risk limits, credentials, Live unlock,
or create a RiskDecision, and reconciliation may never trade.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| EX-C01 | `EX-S01`, `EX-S02`, `EX-C03` (`tests/specs/spec_execution.py`) |
| EX-C02 | `EX-B01` |
| EX-C03 | `EX-B20` |
| EX-C04 | `EX-B02`, `EX-B04` |
| EX-C05 | `tests/test_entry_plan_execution.py` |
| EX-C06 | `EX-B05`; `tests/test_order_constraints.py`, `tests/test_gateway_order_constraints.py` |
| EX-C07 | `EX-B02`; `PL-PN-06`…`PL-PN-10` (`tests/specs/spec_pipeline_exchange_commit.py`) |
| EX-C08 | `PL-RX-01`, `PL-RX-02`, `PL-RX-18` (`tests/specs/spec_pipeline_risk_execution_integrity.py`) |
| EX-C09 | `EX-S05`; `tests/test_entry_plan_execution.py` |
| EX-C10 | `EP-S03` (`tests/specs/spec_entry_plan_contract.py`) |
| EX-C11 | `EP-S14`; `tests/test_entry_plan_execution.py` (the `entry_cancel_stalled` event has no focused test) |
| EX-C12 | `tests/uta/test_identity_liveness.py` |
| EX-C13 | `EX-B16` |
| EX-C14 | `EX-S07`, `EX-B11`, `EX-B12`, `EX-B16`; `tests/test_symbol_capability_consumer.py` |
| EX-C15 | `EX-B13` |
| EX-C16, EX-C17, EX-C18 | `tests/specs/spec_pipeline_bitget_preset_protection.py`, `tests/test_tpsl_native_protection_check.py` |
| EX-C19 | `tests/uta/test_execution_coordinator.py`, `tests/test_broker_averaging_and_trail_fixes.py` |
| EX-C20 | `EX-B17`, `EX-B15` |
| EX-C21 | `EX-B03` |
| EX-C22 | `tests/specs/spec_pipeline_unresolved_entry_exposure.py` |
| EX-C23 | `PL-PN-06`…`PL-PN-10` |
| EX-C24 | `tests/test_entry_failure_codes.py` |
| EX-C25 | `tests/specs/spec_uta_boundary.py` |
| EX-C26 | `EX-C04`, `EX-B06`, `EX-S04`; `tests/audit/test_reconciliation_no_gateway_imports.py` |
| EX-C27 | `PL-RX-07` |
| EX-C28 | `tests/audit/test_reconciliation_incomplete_read_logging.py`, `tests/test_reconciliation_incomplete_read.py` |
| EX-C29 | `PL-ADOPT-23`, `PL-ADOPT-24` (`tests/specs/spec_pipeline_adopted_position.py`) |
| EX-C30 | `PL-ADOPT-26`…`PL-ADOPT-28`; `tests/test_position_reconciler.py` |
| EX-C31 | `PL-PM-16`…`PL-PM-19` (`tests/test_pnl_pipeline.py`) |
| EX-C32 | `OI-B01` (`tests/specs/spec_outcome_integrity.py`); `tests/test_disappearance_confirmation.py` |
| EX-C33 | `tests/test_stale_order_cleanup.py`, `tests/test_entry_plan_execution.py` |
| EX-C34 | `tests/audit/test_audit_events_have_actor_source.py` |
| EX-C35 | `PL-PN-04`, `PL-PN-05`, `PL-RX-19`, `PL-RX-20` |

Claim ids `EX-C03` and `EX-C04` in `tests/specs/spec_execution.py` predate this numbering and
name claims, not the requirements with the same id here; the table maps them explicitly.

## Decision references

- Execution lease and crash recovery: trust §1.2–1.3.
- Entry cancel convergence and re-anchor: #3507 (2026-09-27); schema v90 adds
  `orders.entry_cancel_attempts` / `entry_cancel_retry_after`.
- Account identity relation, continuity proof, entry-side bounded termination: #3798 (2026-10-08);
  fixed Bitget profile: #3806.
- Entry-preset protection, in-place amendment, native TPSL default: #3848 PR-A item 4, PR-B;
  owner D1 2026-10-09 replaced PR #3849 "read back or close"; preset guard prevents the #3783
  class of silently dropped parameters.
- Order lookup start window: #3564 (2026-09-27).
- UTA V1 single mainline: #2299; Demo/Testnet mutation without SHA-bound certificate: #3035.
- Futures-only scope and RWA exclusion: owner 2026-10-09, [Capability Ground Truth](../../product/CAPABILITY_GROUND_TRUTH.md).
- Retired narrative (rule engine, preview text, demo measurements, §12):
  [history](../history/execution_contract_retired_narrative.md); local simulated execution:
  [history](../history/local_simulated_risk_execution.md),
  [position observation](../history/local_simulated_position_observation.md#reconciliation).

## Moved sections

Old section numbers stay as headings so existing references resolve.

### 9. Strategy rule engine

Deleted; see [history](../history/execution_contract_retired_narrative.md#former-9-strategy-rule-engine-deleted).

## 10. 平仓/退出通路 (Exit Path)

Moved to [exit_and_close_contract.md](exit_and_close_contract.md).

### 10.1 Exit trigger priority

→ [exit and close contract](exit_and_close_contract.md#1-exit-triggers).

### 10.2 close_position_routed

→ [exit and close contract](exit_and_close_contract.md#2-close-routing).

#### 10.2.4 Terminal bound on close-side submission ambiguity

→ [exit and close contract](exit_and_close_contract.md#3-close-claims).

### 10.3 paused / kill_switch and closes

→ [exit and close contract](exit_and_close_contract.md#4-pause-and-kill-switch).

### 10.4 stop/take-profit fallback

→ [exit and close contract](exit_and_close_contract.md#1-exit-triggers).

### 10.5 Manual close

→ [exit and close contract](exit_and_close_contract.md#5-manual-close).

## 11. Position close and outcome integrity

The close-path split and §11.1–11.3 moved to the
[exit and close contract](exit_and_close_contract.md#6-verified-and-administrative-close);
§11.4–11.6 moved to the [outcome integrity contract](outcome_integrity_contract.md).

### 11.1 Disappearance confirmation

→ [exit and close contract](exit_and_close_contract.md#6-verified-and-administrative-close).

### 11.2 Broker-neutral verified exit

→ [exit and close contract](exit_and_close_contract.md#6-verified-and-administrative-close).

### 11.3 Administrative close side-effect ban

→ [exit and close contract](exit_and_close_contract.md#6-verified-and-administrative-close).

### 11.4 Central close semantics and read surfaces

→ [outcome integrity contract](outcome_integrity_contract.md#1-read-surfaces).

### 11.5 Durable incident circuit breaker

→ [outcome integrity contract](outcome_integrity_contract.md#2-incident-circuit-breaker).

### 11.6 Futures funding and honest close

→ [outcome integrity contract](outcome_integrity_contract.md#3-futures-funding-and-close-economics).
Claims `EX-B18`, `EX-B19`, `EX-B21` cite this section number.
