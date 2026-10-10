# Risk Engine Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `RE` · **Owner code:** `core/aletheia/risk/engine.py`
> Retired narrative: [history](../history/risk_kill_live_contract_narrative.md).

## Scope

The Risk Engine is the one hard gate between an Entry Plan's Proposal and the Execution
Gateway on the mainline (Entry Plan → **Risk** → Gateway → Position). It approves or
rejects; it never resizes, re-prices or re-levers a Proposal. It does not choose
direction, size a Plan ([entry sizing](entry_sizing_and_financing_contract.md) owns
that), or manage open positions beyond the frozen-policy rule in §8. Product scope is
USDT-margined perpetuals on `bitget_demo`; branches for spot, `okx_*` and `binance_*`
named below are residual code for closed or frozen routes, not support.

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/risk/engine.py::evaluate_proposal` | call | `core/aletheia/execution/gateway.py::_execute_proposal` (preflight, then authoritative) |
| `core/aletheia/risk/engine.py::check_account_leverage` | API route | `core/aletheia/server/routes/risk.py::account_leverage` (`GET /api/risk/account-leverage`) |
| `core/aletheia/risk/kill_switch.py::activate` / `deactivate` | API route, CLI, call | `server/routes/risk.py` (`POST /api/risk/kill-switch`, `/brakes/{scope}`), `cli/risk_cmd.py`, check #8 |
| `core/aletheia/risk/policy.py::resolve_entry_risk_policy` | call | `core/aletheia/portfolio/position_manager.py::_report_entry_policy_unresolved` |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `risk_decisions` | `storage/repositories.py::insert_risk_decision`, `insert_authoritative_risk_decision` | Gateway, analytics, audit | append-only; one authoritative row per identity |
| `risk_policy_snapshots` | `storage/repositories.py::insert_risk_policy_snapshot` | `risk/policy.py::resolve_entry_risk_policy` | insert-or-ignore by `policy_hash`; immutable |
| `entry_risk_reservations` | `storage/repositories.py::acquire_entry_risk_reservation` | engine pre-Order exposure | row per approved native Proposal until `release_entry_risk_reservation` |
| `filter_decisions` | `analytics/filter_evidence.py::record_risk_filter_decision` | filter evidence | append-only |
| config `runtime.{kill_switch,demo_brake,live_brake}_active`, `runtime.pause_owners` | `risk/kill_switch.py::activate` / `deactivate` | check #0, scheduler | mutable, every change audited |
| `audit.jsonl` | `files/jsonl.py::write_audit` | audit mirror | append-only |

## Requirements

### 1. Entry and serialization

#### RE-C01 Sole hard gate
`The Execution Gateway shall reach a broker order call only after evaluate_proposal returned an approved authoritative result for that Proposal.`
- WHEN preflight or authoritative Risk is not approved THEN `_execute_proposal` returns a rejection (or `waiting_risk` for a DEFER, RE-C42) and no broker call happens.

#### RE-C02 Serialized evaluation
`The Risk Engine shall evaluate one Proposal at a time under _eval_lock, and an authoritative evaluation shall read its working set, write its decision and acquire its pre-Order reservation in one immediate SQLite transaction.`
- WHEN two Proposals compete for the last free slot THEN only the first authoritative evaluation sees it free.

#### RE-C03 Idempotent authority
`When an authoritative decision already exists for the same Proposal commit hash, evaluated quantity, policy hash and execution attempt, the Risk Engine shall return that decision instead of writing a second one.`
- WHEN the stored decision is approved but the native Proposal has neither an Order nor a reservation THEN the engine re-runs a preflight evaluation and returns it if it rejects.

### 1.1 Policy identity

#### RE-C04 Mode-scoped policy
`The Risk Engine shall take every limit from risk.demo or risk.live as selected by the Proposal's execution_mode; if the mode is not registered, then it shall reject with unregistered_risk_execution_mode:{mode}.`
- WHEN `execution_mode` is unknown THEN it is never evaluated under the Demo policy.

#### RE-C05 Policy identity on the decision
`The Risk Engine shall store risk_mode, policy_id, policy_revision and policy_hash on every RiskDecision; if an approved native authoritative decision lacks any of them, then it shall reject with risk_policy_identity_unavailable.`
- WHEN the policy changes THEN its hash changes and RE-C03 no longer reuses the earlier approval.

#### RE-C06 Policy validation at ingress
`The configuration layer shall reject a Risk Policy with a non-finite number, an empty policy_id, a revision below 1, or (for Live) a value outside LIVE_RISK_POLICY_BOUNDS, before the policy is persisted or hashed.`
- WHEN a Demo value above a Live ceiling is copied to Live THEN the copy fails.
- WHEN `capital.max_total_exposure_pct=0` or `capital.max_per_symbol_entries=0` THEN that check is disabled, not failed.

Owner code: `config/schema.py::StrictRiskPolicyModel`, `validate_mode_risk_policy`; `server/routes/settings.py::copy_risk_policy`, `replace_risk_policy`. Copyable policy excludes `require_risk_check_for_all_orders`, Live unlock, permissions, credentials and runtime brakes.

### 2. Admission checks

Checks run in this order inside `_evaluate_proposal_locked`; all violations of one
evaluation are collected, except RE-C07 which stops early. Numbers are stable names, not
positions. Every violation is a typed token; any token rejects.

#### RE-C07 Native input validation
`If a native Proposal has a non-canonical symbol, an unknown market type, direction, order type or execution mode, or a non-positive or non-finite quantity, price or futures leverage, then the Risk Engine shall persist a rejection without running the configurable checks.`
- Tokens: `invalid_symbol_format`, `invalid_market_type`, `invalid_direction`, `invalid_order_type`, `invalid_quantity`, `invalid_price`, `invalid_leverage`, `invalid_execution_mode`, exit-intent tokens; residual spot: `invalid_spot_leverage` (spot leverage other than NULL or 1).

#### RE-C08 Check #0 — trading brakes
`While the global brake or the brake of the Proposal's policy mode is active, the Risk Engine shall reject the Proposal.`
- Tokens: `kill_switch_active`, `demo_brake_active`, `live_brake_active`.
- WHEN only the demo brake is active THEN a live Proposal is not blocked by it.

#### RE-C09 Check #1 — quote asset and symbol filter
`If the canonical symbol's quote asset is not USDT, or the symbol is blacklisted or missing from a non-empty whitelist, then the Risk Engine shall reject the Proposal.`
- Tokens: `unsupported_trading_quote_asset:{quote}`, `symbol_blocked:{sym}`, `symbol_not_allowed:{sym}`. Config: `risk.symbol_filter_mode`, `risk.symbol_list`.

#### RE-C10 Check #1a — Entry Plan authority
`When a Proposal carries an entry_plan_id, the Risk Engine shall reject it unless the persisted Plan's hash, current generation, active lifecycle, expiry, exact instrument and account, price/protection/tactic envelope, prior-fill evidence and capital limits all match the Proposal.`
- Tokens: `entry_plan_*` (for example `entry_plan_integrity_failed`, `entry_plan_generation_superseded`, `entry_plan_expired`, `entry_plan_worst_price_exceeded`, `entry_plan_max_risk_exceeded`). Owner: `_entry_plan_authority_violations`. The Gateway validates the typed execution receipt (`entry_execution_receipt_*`) and binds it to the RiskDecision.

#### RE-C11 Check #2 — market allowed
`If the Proposal's market type is disabled in exchange.enabled_markets, then the Risk Engine shall reject it.`
- Tokens: `futures_market_disabled`; residual spot: `spot_market_disabled`. See gap G2.

#### RE-C12 Check #2b — complete entry geometry
`When a native Proposal is a limit entry, the Risk Engine shall reject it unless entry, stop, take-profit and risk_reward are finite and positive, sit on the correct side of entry, and risk_reward equals round(abs(tp-entry)/abs(entry-stop), 2) inside the inclusive envelope [1.00, 10.00].`
- Tokens: `incomplete_entry_plan:{fields}`, `entry_plan_geometry:*`, `entry_plan_risk_reward_mismatch`, `entry_plan_risk_reward_outside_envelope`. Market close/reduce and adopted chains are exempt.

#### RE-C13 Check #2d — stop floor
`If the stop distance is tighter than the floor computed by trading/entry_plan.py::stop_distance_below_min_floor, then the Risk Engine shall reject the Proposal.`
- Token: `stop_distance_below_vol_target:{distance}<{floor}`. The floor policy is owned by [entry-plan-contract](entry-plan-contract.md).

#### RE-C14 Check #3 — Live state and selected live account
`If a Proposal uses a live execution mode while live.state is not live_limited, or the mode is not the selected exchange's live mode, or the stored unlock evidence no longer matches the selected account, then the Risk Engine shall reject it.`
- Tokens: `live_disabled`, `selected_exchange_mismatch:{mode}!={expected}`, `live_identity_unverified:{reason}`.
- WHEN evidence is stale THEN the engine also revokes the unlock (see [live unlock](live_unlock_contract.md)).

#### RE-C15 Check #4 — worker permission
`If agent.permission_mode is Observe, then the Risk Engine shall reject the Proposal with permission_mode_insufficient:Observe.`
- The engine never reads `operator.permission_mode` (verified by `PCI-G06`).

#### RE-C16 Check #5 — position margin
`If the Proposal's pledged margin (notional / max(1, leverage)) exceeds the mode's capital.max_position_margin_usdt, tightened to the matching account's cached available USDT and multiplied by the drawdown scale, then the Risk Engine shall reject it.`
- Tokens: `exceeds_max_position_margin:{m}>{cap}` (live), `exceeds_demo_position_margin:{m}>{cap}` (demo), `account_balance_unavailable`.
- WHEN no healthy cached balance exists THEN the configured cap applies unchanged; the cache only tightens.

#### RE-C17 Check #5 — drawdown scale
`The Risk Engine shall scale the check #5 cap by 1.0 at ≤5% drawdown, linearly to 0.5 at 10%, to 0.0 at 20%, and 0.0 beyond, measured on fresh exchange_account equity snapshots.`
- WHEN the active broker view is incomplete, fewer than 3 snapshots exist, or the latest is stale THEN the scale is 1.0 (no scaling; configured caps still bound size).
- Per-mode notional rows are never read as equity (`_portfolio_drawdown_assessment`).

#### RE-C18 Check #6 — open-position slots
`If open Positions, unresolved entry Orders and pre-Order reservations of the Proposal's live or demo pool reach positions.max_open_positions, then the Risk Engine shall reject the Proposal.`
- Tokens: `max_live_positions_reached:{n}`, `max_demo_positions_reached:{n}`; read failures `unresolved_entry_exposure_unavailable`, `entry_risk_reservation_unavailable`; broken reservation identity rejects every Proposal.
- WHEN the Proposal continues the same Plan's still-open Position THEN that slot is reused, not counted twice. A `pending` entry Order counts as exposure.

#### RE-C19 Check #6b — same-symbol dedup
`If the Proposal's pool already holds an open Position, unresolved entry Order or pre-Order reservation on the same symbol, then the Risk Engine shall reject it with symbol_already_open:{sym}.`

#### RE-C20 Check #7 — short allowed
`If a short Proposal's mode policy has positions.allow_short false, then the Risk Engine shall reject it.`
- Tokens: `short_not_allowed_live`, `short_not_allowed_demo`; residual spot: `spot_short_not_allowed`.

#### RE-C21 Percentage-limit denominator
`The Risk Engine shall compute every percentage limit against the matching account equity, or against capital.base_usdt only when capital.base_source is custom; if that base is unavailable or non-positive, then it shall reject with capital_base_unavailable:{reason}.`
- WHEN equity is unavailable THEN stored custom capital is never used as a fallback.

#### RE-C22 Check #8 — daily loss
`Where loss.max_daily_loss_pct is positive, the Risk Engine shall reject a Proposal whose account's authoritative realized loss today reaches that share of the capital base, and shall trip the mode brake when auto_trip_mode_brake_on_daily_loss is set.`
- Tokens: `max_daily_loss_exceeded:{pnl}<=-{limit}`, `account_loss_unavailable:{reason}`.
- WHEN `max_daily_loss_pct` is 0 (the default) THEN the check and its snapshot read are skipped.
- WHEN the day has an administrative, maintenance, legacy or incomplete close THEN the snapshot is unavailable, the Proposal is rejected, and no brake trips. Source: `portfolio/account_loss.py::authoritative_account_realized_loss`.

#### RE-C23 Check #9a/#9b — exchange and account mode binding
`When the Proposal's execution mode has a GLOBAL account-config binding, the Risk Engine shall reject it if exchange.name differs from a set risk.allowed_exchange or exchange.mode differs from the order's live/demo environment.`
- Tokens: `exchange_not_allowed:{a}!={e}`, `account_mode_mismatch:order={env},exchange={mode}`. `bitget_*` modes are GLOBAL; the SELF_CONTAINED exemption on frozen `okx_*`/`binance_*` modes is residual (`config/exchanges.py::EXECUTION_MODE_ACCOUNT_CONFIG_BINDINGS`).

#### RE-C24 Check #10 — instrument leverage
`If a futures Proposal's leverage exceeds the tier cap from trading/instruments.py::get_max_leverage, then the Risk Engine shall reject it with exceeds_max_leverage:{l}>{cap}(tier={tier}).`

#### RE-C25 Check #11 — data freshness
`If the exact venue and market snapshot for the Proposal's symbol is missing or older than 120 s (live) or 1800 s (registered demo modes), then the Risk Engine shall reject it.`
- Tokens: `stale_market_data:age={a}`, `stale_market_data_demo:age={a}`. Other venues or markets of the same symbol never substitute. Limits are module constants `_MAX_DATA_AGE_SECONDS`, `_MAX_DEMO_DATA_AGE_SECONDS`.

#### RE-C26 Check #12 — total account exposure
`If the pool's open, unresolved and reserved notional plus the Proposal's notional exceeds capital.max_total_exposure_pct of the capital base, then the Risk Engine shall reject it.`
- Tokens: `max_total_live_exposure_exceeded:{p}>{cap}`, `max_total_demo_exposure_exceeded:{p}>{cap}`, `account_exposure_check_error:*`. Each demo mode is its own account pool.

#### RE-C27 Check #13 — single-trade risk
`If the Proposal's loss at stop exceeds loss.max_single_trade_risk_pct of the capital base, then the Risk Engine shall reject it; a live Proposal without a stop shall be rejected.`
- Tokens: `exceeds_single_trade_risk:{r}>{max}`, `exceeds_demo_single_trade_risk:{r}>{max}`, `max_single_trade_risk_missing_stop_price`. A financed Plan is bounded by its frozen `effective_loss_budget_usdt` through RE-C10 (`entry_plan_max_risk_exceeded`).

#### RE-C28 Check #14 — live family evidence
`When a Proposal is live, the Risk Engine shall reject it unless its resolved candidate family has at least eligibility.family_min_samples settled outcomes for its direction and market with win rate at least eligibility.family_min_win_rate_pct.`
- Token: `family_not_live_eligible:{unknown | family:no_evidence | family:samples=n<N | family:wr=w<T}`.

#### RE-C29 Check #14b — demo entry quality
`When a demo Proposal opens a new entry, the Risk Engine shall reject it while the published regime confidence is at or below the threshold (mean_reversion exempt), while the published regime is sideways, when its family×direction has enough evidence and negative expectancy, or when it reverses a same-symbol stop_loss within 24 h.`
- Tokens: `regime_confidence_standdown:*`, `regime_sideways_standdown:*`, `family_not_demo_eligible:*`, `reverse_after_stop:*`, `demo_entry_quality_unavailable:{source}`.
- WHEN regime is unpublished or samples are too few THEN the clause passes; WHEN an authority read or the policy-epoch write fails THEN it rejects. Owner: `risk/demo_entry_quality.py::collect_demo_entry_quality_violations`.

#### RE-C30 Check #15 — lesson constraints (lesson_guards)
`When an applied, unexpired category='risk' lesson outside its dry-run window blocks the Proposal's symbol, direction or family, the Risk Engine shall reject the Proposal if the lesson's source is manual, owner or human, or if the bound risk_lesson_constraint treatment authorizes it.`
- Tokens: `lesson_block_symbol:{id}:{v}`, `lesson_block_direction:{id}:{v}`, `lesson_block_scanner:{id}:{v}`, `lesson_check_error:internal_failure`.
- WHEN an automatic lesson has no treatment authority THEN it is logged as advisory. `reduce_size` is always advisory. Source: `ps_lessons` via `storage/repositories_research.py::list_lessons`. Target replacement: [spec 0001](../specs/0001-learning-loop/spec.md) (gap G1).

#### RE-C31 Check #16 — portfolio correlation
`Where correlation.enabled is true, the Risk Engine shall reject a Proposal that pushes the pool's same-family share above correlation.max_sector_concentration_pct or portfolio BTC beta above correlation.max_portfolio_beta.`
- Tokens: `sector_concentration:{family}:{p}%>{max}%`, `portfolio_beta:{b}>{max}`, `correlation_check_error:internal_failure`. Concentration is skipped below `correlation.min_positions_for_concentration` positions.

#### RE-C32 Check #17 — account leverage ratio
`If a futures Proposal makes the pool's Σ|notional| (open Positions, unresolved Orders, reservations and the Proposal) divided by the same named account's fresh equity exceed leverage.account_cap, then the Risk Engine shall reject it.`
- Tokens: `account_leverage_ratio:{r}x>{cap}x`, `account_equity_unavailable:{mode}`, `account_equity_non_positive:{mode}`, `account_leverage_check_error:invalid_position_exposure:position={id}:field={category}`, `account_leverage_check_error:invalid_entry_reservation`, `account_leverage_check_error:internal_failure`.
- WHEN a relevant Position has a missing, non-finite, non-positive or contradictory field THEN it rejects instead of counting zero. Residual: a spot Position with NULL leverage counts as 1x.

#### RE-C33 Check #18 — liquidation buffer
`If a futures Proposal with leverage above 1 has an estimated liquidation price closer to entry than margin.min_entry_liquidation_buffer_pct, then the Risk Engine shall reject it.`
- Token: `liquidation_too_close:{g}%<{b}%(liq=…,lev=…x)`.

#### RE-C34 Check #19 — per-symbol notional (unfinanced Proposals)
`When a Proposal's Plan carries no frozen financing, the Risk Engine shall reject it if the pool's same-symbol notional plus the Proposal exceeds capital_base × max_single_trade_risk_pct × leverage × capital.max_per_symbol_entries.`
- Token: `exceeds_per_symbol_notional:{sym}:{proj}>{cap}(entries={n})`. Applies only to Proposals without frozen financing.

#### RE-C35 Check #20 — per-symbol risk units (financed Plans)
`When a Plan carries frozen financing, the Risk Engine shall reject it if same-symbol stop-loss risk in the pool exceeds effective_loss_budget_usdt × capital.max_per_symbol_entries.`
- Token: `exceeds_per_symbol_risk:{risk}>{cap}(entries={n})`. Owner: `_entry_plan_financing_violations`.

#### RE-C36 Check #21 — financing drift
`When a Plan carries frozen financing, the Risk Engine shall re-run the same sizing functions on submission-time facts and reject if the recomputed execution notional is smaller than the frozen one.`
- Tokens: `SIZING_RESOLUTION_DRIFT`, recomputation failures (`SIZING_CAPITAL_UNAVAILABLE`, `VENUE_MARGIN_TIERS_*`, `NO_FEASIBLE_FINANCING`).
- WHEN the recomputation is larger THEN the frozen value stands. Owner: `_recomputed_entry_financing`.

#### RE-C37 Check #22 — financed leverage and margin mode
`When a Plan carries frozen financing, the Risk Engine shall reject the Proposal unless its leverage equals the Plan's execution_leverage and a perp Plan's margin mode is isolated.`
- Tokens: `entry_plan_execution_leverage_mismatch`, `CROSS_MARGIN_SAFETY_UNAVAILABLE`, `MARGIN_MODE_UNAVAILABLE`. The Gateway reads margin mode from the frozen Plan (`margin_mode_policy.py`).

### 3. Decision record

#### RE-C38 Every evaluation persisted
`The Risk Engine shall persist one role-labelled RiskDecision (preflight or authoritative) for every evaluation, approved or rejected, and return RiskCheckResult{approved, risk_score, violations, allowed_execution_mode, message, decision_id}.`
- WHEN rejected THEN `allowed_execution_mode` is empty; `risk_score = min(0.2 × len(violations), 1.0)`.

#### RE-C39 Authoritative evidence is atomic
`When an authoritative decision is persisted, the Risk Engine shall record its final filter decision in the same transaction, and if that write fails, then the decision and its reservation shall roll back and the Gateway shall receive no approval.`
- Preflight rows create no filter evidence.

#### RE-C40 Snapshot binding
`If any Proposal field covered by the stored commit_hash other than quantity differs from the evaluated copy, then the Risk Engine shall raise ProposalIntegrityError and write no authoritative decision.`
- WHEN venue normalization changes quantity THEN `RiskDecision.evaluated_quantity` records it and the stored Proposal is not rewritten.

#### RE-C41 Retry disposition
`The Risk Engine shall classify a rejection as DEFER when every violation's prefix is in the RiskCheckResult.disposition temporary set, and as REJECT otherwise.`
- WHEN an Entry Plan Proposal is DEFER THEN its status is not set to `rejected` and the Gateway parks it as `waiting_risk` until the Plan expires.

#### RE-C42 Proposal status on rejection
`When an authoritative evaluation rejects a Proposal in pending or approved status, the Risk Engine shall set it to rejected in the same transaction, unless the rejection is receipt-only or a DEFER of an Entry Plan Proposal.`

### 4. Brakes and kill switch

#### RE-C43 Brake activation
`When a brake is activated, kill_switch.activate shall persist the runtime flag and its pause-owner lease, pause the scheduler when that owner blocks it, and write a trading_brake_activated audit event.`
- WHEN the process restarts THEN the brake is still active.

#### RE-C44 Brake release
`When a brake is released, kill_switch.deactivate shall write a trading_brake_deactivated audit event first, and if that write fails, then it shall raise AuditPersistenceError and leave the brake active.`
- Release paths are human-only: `server/routes/risk.py` (requires `confirm_release=true`) and `cli/risk_cmd.py`; `deactivate_kill_switch` is a forbidden agent intent.

### 5. Sizing boundary

#### RE-C45 Approve or reject only
`The Risk Engine shall never change a Proposal's quantity, leverage, notional or margin mode; it shall only approve or reject.`
- Sizing formulas are owned by [entry_sizing_and_financing_contract](entry_sizing_and_financing_contract.md); a sized Plan still passes checks #5, #12, #13, #17, #18 and #20–#22.

### 6. Bypass resistance

#### RE-C46 Gateway re-check for live
`When the authoritative result allows a live mode, the Execution Gateway shall reject the Proposal if that mode is not the selected exchange's live mode or live.state is live_disabled.`

#### RE-C47 No agent path to Risk authority
`The agent runtime shall register unlock_live, place_live_order, change_hard_risk_limits, modify_credentials and modify_execution_gateway as restricted tools that no permission mode may call.`
- Owner: `agent_runtime/tool_registry.py::RESTRICTED_TOOLS`, `agent_runtime/permissions.py::TOOL_CLASSES`; command-level intents `bypass_risk`, `change_risk_limits` are forbidden ([live unlock §6](live_unlock_contract.md)).

### 7. Monitoring surface

#### RE-C48 Honest account-leverage monitor
`The account-leverage monitor shall use the same pool, exposure and equity rules as check #17, and if any input is corrupt or unavailable, then it shall return status=unavailable with ratio=null, violation=null and a stable reason.`
- WHEN the corrupt input is a Position THEN `invalid_position={position_id, field_category}` is returned; no credential or raw venue payload is included.

### 8. Open-position guards

#### RE-C49 Guards frozen at the entry policy
`While a native Position is open, the Position Manager shall evaluate its Layer-0 catastrophic stop, runtime liquidation buffer and horizon backstop under the policy identified by its entry RiskDecision's policy_hash.`
- WHEN that policy cannot be resolved THEN each evaluation reports `entry_risk_policy_unresolved:{reason}`, one audit event is written, and guards keep running under the current policy. Adopted Positions use the current policy.

#### RE-C50 One stop-adjustment entry
`The Position Manager shall change an open Position's stop only through apply_operator_adjustment, which never loosens the current protection or the Layer-0 bound.`

### 9. Serenity shared pool (serenity_isolation)

#### RE-C51 Serenity shares the main pool
`The Risk Engine shall count Serenity-sourced Positions and Proposals in the same check #5/#6 pools as main-track ones.`
- No separate Serenity executor or Risk pool exists; `go_live_scorecard.py::compute_pnl_by_trade_school` groups PnL for reading only and is not an isolated authority. Adding isolation requires a [spec](../specs/README.md).

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Account equity / capital base unreadable | reject | `capital_base_unavailable:*`, `account_equity_unavailable:*` | never replaced by stored capital |
| Daily-loss snapshot incomplete | reject, no brake trip | `account_loss_unavailable:*` | not a breach, not 0 |
| Pending-entry or reservation read fails | reject | `unresolved_entry_exposure_unavailable`, `entry_risk_reservation_unavailable` | never zero exposure |
| Lesson / correlation / leverage check raises | reject | `*_check_error:internal_failure` | — |
| Demo-quality authority read fails | reject (DEFER) | `demo_entry_quality_unavailable:{source}` | empty evidence passes |
| Drawdown evidence incomplete or stale | scale 1.0 | — | configured cap still applies |
| Market snapshot missing | reject | `stale_market_data*:age=None` | — |
| Filter-evidence write fails | roll back decision | exception to Gateway | — |
| Hash-covered field drift | raise | `ProposalIntegrityError` | — |

## Authority

Risk may approve or reject a Proposal and trip the mode brake on a daily-loss breach; it
may never change Risk limits, credentials, Live unlock, permission modes, or a Proposal's
size, leverage or margin mode, and only a human may release a brake.

## Verification

Claim ids are `SpecClaim` ids in `tests/specs/` — a separate namespace from `RE-C<NN>`.

| Requirement | Executable claim / focused test |
|---|---|
| RE-C01 | `spec_risk_engine.py::RE-B17`, `::D2-C01`, `::RE-S01` |
| RE-C02 | `spec_risk_engine.py::RE-S02` |
| RE-C04, RE-C05 | `spec_risk_engine.py::RE-S08` |
| RE-C07 | `spec_risk_engine.py::RE-B32` |
| RE-C08 | `spec_risk_engine.py::RE-B02` |
| RE-C09 | `spec_risk_engine.py::RE-B06` |
| RE-C11 | `spec_risk_engine.py::D2-S01`, `::D2-B01` |
| RE-C14 | `spec_risk_engine.py::RE-B07` |
| RE-C15 | `spec_risk_engine.py::RE-B08`, `spec_pi_decision_chain.py::PCI-G06` |
| RE-C16, RE-C17 | `spec_risk_engine.py::RE-B09`, `::RE-B18`, `::RE-B05`, `::RE-B05a`, `::RE-B05c`, `::RE-B05d` |
| RE-C18 | `spec_risk_engine.py::RE-B10` |
| RE-C19 | `spec_risk_engine.py::RE-B16` |
| RE-C20 | `spec_risk_engine.py::RE-B11` |
| RE-C21 | `spec_risk_engine.py::RE-B31` |
| RE-C22 | `spec_risk_engine.py::RE-B13`, `::RE-B20`, `::RE-B21` |
| RE-C27 | `spec_risk_engine.py::RE-B12` |
| RE-C28 | `spec_risk_engine.py::RE-S06` |
| RE-C29 | `spec_risk_engine.py::RE-S07` |
| RE-C30 | `spec_risk_engine.py::RE-B03`, `::RE-B14`; `spec_lesson_guards.py::LG-S01`–`LG-S06` |
| RE-C31 | `spec_risk_engine.py::RE-B04`, `::RE-B04a`–`RE-B04d` |
| RE-C32 | `spec_risk_engine.py::RE-B19`, `::RE-B23`, `::RE-B24`, `::RE-B26`, `::RE-B27` |
| RE-C34 | `spec_risk_engine.py::RE-B15`, `::RE-B15b` |
| RE-C38 | `spec_risk_engine.py::RE-S03`, `::RE-B01` |
| RE-C47 | `spec_risk_engine.py::RE-C01` |
| RE-C48 | `spec_risk_engine.py::RE-B25`, `::RE-B28`, `::RE-S09` |
| RE-C50 | `spec_risk_engine.py::RE-S05` |
| RE-C51 | `spec_risk_engine.py::D2-S02`, `::D2-S03`, `::D2-B02`, `::D2-C02` |
| all checks | `spec_risk_engine.py::RE-S04`, `::RE-S04b` (token floor) |

RE-C03, C06, C10, C12, C13, C23–C26, C33, C35–C37, C39–C46, C49 have no `SpecClaim`;
focused coverage lives in `tests/` outside `tests/specs/`.

## Known conformance gaps

- **G1 — check #15 vs spec 0001.** RE-C30 is driven by `ps_lessons`. Approved
  [spec 0001](../specs/0001-learning-loop/spec.md) removes that lifecycle and allows
  blocking only through its authority tier (LL-R13, LL-R19), but its Contract Delta does
  not list this contract.
- **G2 — spot reachable at check #2.** `config/schema.py` defaults
  `exchange.enabled_markets` to `{"spot": True, "usdt_futures": True}`, so RE-C11 admits a
  spot Proposal although spot is out of scope. Target: [spec 0003](../specs/0003-futures-only-convergence/spec.md) FC-R01/FC-R02.

## Decision references

- Mode-scoped Demo/Live policy and policy hash on every decision: Risk Policy v2.
- #1911 (owner 2026-10-09): open-position guards stay on the entry policy (RE-C49).
- #3729: financed Plans, risk units and drift (RE-C34–RE-C37).
- #3743 (2026-09-30): ATR stop-floor draft read in check #2d (RE-C13).
- #3134: SELF_CONTAINED binding exemptions retained for OKX/Binance modes (RE-C23).
- #98: entry geometry side check; #24: spot short rejection; #7: Live state fails closed.
- 2026-09-07: `trust/guard_chain.py` deleted; EX-C03 keeps the one-hard-gate invariant.
- [trust_and_change_control §6.3](../../governance/policies/trust_and_change_control.md): no Risk check is weakened or removed without explicit owner approval; a new check persists a RiskDecision and tests both paths.
