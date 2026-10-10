# Entry Sizing and Financing Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `ES` · **Owner code:** `core/aletheia/trading/risk_intent.py`,
> `core/aletheia/trading/entry_sizing.py`, `core/aletheia/trading/leverage_resolution.py`,
> `core/aletheia/execution/margin_mode_policy.py`

Core semantics: **AI decides qualitative risk intent; sizing decides target exposure; leverage only
finances that exposure.** No AI emits leverage, notional, margin or a risk multiplier; no resolver
enlarges target exposure or treats the Owner policy leverage cap as the execution leverage.

## Scope

This Contract owns three decisions on the canonical entry path (Entry Plan compilation, re-verified
by Risk): AI Risk Intent, Adaptive Sizing, and Deterministic Leverage Resolution, plus the margin
mode those decisions assume. Product scope is USDT-margined perpetuals on `bitget_demo` with
isolated margin. It does not own the Plan lifecycle ([Entry Plan](entry-plan-contract.md)), Risk
gate ordering ([Risk Engine](risk_engine_contract.md)), or order submission.

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/trading/risk_intent.py::resolve_risk_intent_multiplier` | call | `trading/entry_plan_compiler.py` |
| `core/aletheia/trading/entry_sizing.py::resolve_entry_sizing` | call | `trading/entry_plan_compiler.py`; Risk re-verification |
| `core/aletheia/trading/leverage_resolution.py::resolve_execution_financing` | call | `trading/entry_plan_compiler.py`; Risk re-verification |
| `core/aletheia/trading/venue_margin.py::load_venue_margin_envelope` | call (sync, network-free) | `trading/entry_plan_compiler.py` |
| `core/aletheia/agent_runtime/scheduler.py::_run_venue_margin_facts` | scheduler job `venue_margin_facts` | refreshes tiers for `list_candidate_planning_demand` instruments |
| `core/aletheia/cli/data_cmd.py` `data venue-margins` | CLI | operator single-instrument refresh / `--show` |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| venue margin envelope (settings row keyed venue/symbol/market_type) | `venue_margin.py::store_venue_margin_envelope` | `load_venue_margin_envelope` (24 h TTL) | replaceable cache with fetch timestamp and provenance |
| `entry_plans.plan_json` financing fields | Compiler | Plan execution, Risk, Gateway | immutable per Plan |

## Requirements

### ES-C01 Risk intent is a posture, never a number

`The EntryHypothesis risk_intent shall be a RiskIntent with posture BASELINE or REDUCED, reason ≤ 512 chars and 1–8 evidence_refs, under the frozen extra="forbid" schema.`

- WHEN posture is `INCREASED` or unknown, or a numeric leverage/notional/margin field is present THEN hypothesis validation fails.
- WHEN the decision is `NO_PLAN` THEN `risk_intent` is cleared.
- WHEN `risk_tier` is read THEN it keeps its payoff-geometry meaning and is not merged with posture.
- WHEN evidence is insufficient or conflicting THEN the decision stays `NO_PLAN`; a smaller position never substitutes for an invalid thesis.

### ES-C02 Partial intent normalizes to the non-expanding default

`If an ENTRY_PLAN hypothesis omits risk_intent or part of it, then the system shall resolve the supplied posture (omitted → BASELINE) with reason and evidence taken from capture_reason / capture_evidence_refs, log the normalization, and never override an explicit posture.`

- WHEN `risk_intent` is a bare posture string THEN it is treated as `{posture}` and completed (`entry_analysis.py::EntryHypothesis._normalize_risk_intent`).
- WHEN `risk_intent` is omitted THEN posture is `BASELINE` and a warning is logged (`_resolved_risk_intent`).

### ES-C03 Posture maps to an Owner-policy multiplier

`When sizing resolves the posture, the system shall map BASELINE to 1.0 and REDUCED to policy.loss.reduced_risk_multiplier, and if that value is missing, non-finite or outside (0, 1) or the intent is absent, then it shall fail with RISK_INTENT_INVALID.`

- WHEN posture is `REDUCED` and the policy multiplier is 0.5 THEN `risk_intent_multiplier = 0.5` with source `policy.loss.reduced_risk_multiplier`.

### ES-C04 Loss budget and target notional

`The sizer (policy version adaptive-risk-intent-sizing/v2) shall compute target exposure from the loss budget and stop geometry as below, recording every binding constraint.`

```text
baseline_loss_budget_usdt  = min(shared_hard_capital_base, fresh_available_balance)
                             x max_single_trade_risk_pct / 100
effective_loss_budget_usdt = baseline_loss_budget_usdt x risk_intent_multiplier
stop_fraction              = |worst_entry_price - stop_price| / worst_entry_price
raw_target_notional_usdt   = effective_loss_budget_usdt / stop_fraction
target_notional_usdt       = min(raw_target, every constraint below)
margin_budget_usdt         = min(fresh_available_balance, policy.max_position_margin_usdt)
```

| `binding_constraints` token | Bound |
|---|---|
| `remaining_total_exposure` | `policy.capital.max_total_exposure_pct` of the capital base, net of the same pool's committed notional |
| `remaining_account_leverage` | `policy.leverage.account_cap × equity`, net of the same pool's committed notional |
| `market_liquidity` | executable notional the entry market can absorb (preflight depth envelope) |
| `per_symbol_risk_units` | `max_per_symbol_entries` full-size risk units, projected as same-symbol stop-loss risk — never `risk_pct × leverage` |
| `instrument_max_notional` | venue/instrument hard notional bound |

- WHEN `max_single_trade_risk_pct` is read THEN it means only the share of the capital base that may be lost at the stop; margin budget is a separate quantity.

### ES-C05 Sizing fails closed

`If the capital base, available balance, policy identity or geometry is unusable, then the sizer shall return a typed unavailable resolution (SIZING_CAPITAL_UNAVAILABLE, SIZING_GEOMETRY_INVALID, NO_EXECUTABLE_NOTIONAL) and never substitute configured capital for unreadable account authority.`

- WHEN a resolution is produced THEN `SizingResolution` persists policy version, posture, multiplier and source, baseline/effective loss budget, worst entry and stop prices, stop distance %, raw/final target notional, margin budget, binding constraints, account/policy snapshot identities and `facts_fingerprint`.

### ES-C06 Leverage only finances the target

`The resolver shall enumerate leverage from low to high up to min(policy_leverage_cap, venue absolute cap) and choose the largest execution notional not exceeding the target, the lowest leverage among equal notionals, and NO_FEASIBLE_FINANCING when no candidate is admissible.`

- WHEN evaluating leverage L THEN `candidate_notional(L) = min(target_notional, margin_budget × L, remaining tier/instrument notional capacity)` and the tier is selected for the projected position notional (existing + candidate, re-resolved after capping).
- WHEN L exceeds `tier.max_leverage` or `(1/L − tier.maintenance_margin_rate) × 100 < liquidation buffer` THEN L is not admissible.
- WHEN a target is reachable at 5x and 18x is safe THEN execution leverage is 5x; WHEN the target needs 20x and only 18x is provable THEN execution notional shrinks; WHEN 1x suffices THEN 1x is used.
- WHEN `policy_leverage_cap` is read THEN it is a ceiling (`get_max_leverage`), never the execution leverage.
- WHEN a resolution is produced THEN `LeverageResolution` persists status, reason, policy cap, venue absolute cap, safe cap, required leverage for target, execution leverage, target/execution notional, fill ratio, margin budget, projected notional, tier and maintenance-margin rate, margin mode, binding constraint, liquidation buffer and `facts_fingerprint`.

### ES-C07 One declared margin mode per route

`The resolver and the submission path shall read the same declared submission margin mode (execution/margin_mode_policy.py::submission_margin_mode), and a route with no declared mode shall fail closed with MARGIN_MODE_UNAVAILABLE.`

- WHEN the route is `bitget_demo` THEN the mode is `isolated` (`config/exchanges.py::ENTRY_ROUTE_POLICIES`), the only qualified mode, using the exact tier maintenance-margin rate.
- WHEN the envelope reports `cross` THEN the result is `CROSS_MARGIN_SAFETY_UNAVAILABLE`; cross is not qualified, and the isolated estimate `calc_liquidation_price()` is never reused as cross-account safety.
- WHEN market type is spot THEN the residual `not_applicable` 1x branch in `resolve_execution_financing` applies; spot never reaches this path on the mainline and this is not a support claim.

### ES-C08 Venue tier facts come from the holding venue

`The Compiler shall load the VenueMarginEnvelope for the execution route's venue (the venue that will hold the position), not the Candidate's market-data venue, and if it is absent, stale or incomplete then it shall fail with VENUE_MARGIN_TIERS_UNAVAILABLE or VENUE_MARGIN_TIERS_STALE.`

- WHEN analysis runs on one venue and execution on `bitget_demo` THEN tiers come from `bitget_demo`'s venue; the Plan's own `venue` still identifies market data.
- WHEN no fresh row exists for an instrument THEN its leveraged entry is unavailable; the read path is synchronous and network-free and a static in-repo maintenance-margin default is never the authority.

### ES-C09 Plan carries the frozen financing

`New EntryPlans shall write risk_intent_posture, risk_intent_multiplier, sizing_resolution, leverage_resolution, policy_leverage_cap, execution_leverage, target_notional_usdt, execution_notional_usdt and margin_mode, and only execution_leverage and execution_notional_usdt shall carry submission authority.`

- WHEN a Proposal is built THEN `TradeProposal.leverage = EntryPlan.execution_leverage`, quantity derives from `execution_notional_usdt`, and the order boundary reads `margin_mode` from the frozen Plan (the Proposal does not repeat it).
- WHEN a historical Plan lacks these fields THEN it obtains no new submission authority; `max_leverage`, `max_notional`, `max_risk_pct` remain legacy mirrors; historical JSON and hashes are not rewritten. The retired `available-balance-risk-budget/v1` version remains only in `trading/sizing.py`.

### ES-C10 Risk verifies, never mutates

`Risk shall recompute sizing and financing with the same pure functions on submission-time facts and shall reject a smaller recomputed execution notional with SIZING_RESOLUTION_DRIFT, keep the original on a larger one, and never mutate leverage, notional or margin mode.`

- WHEN facts are identical THEN the resolution is identical or equivalent, and a compiled Plan is not rejected by Risk for leverage, margin or stop-risk feasibility.
- WHEN Risk reads `max_single_trade_risk_pct` THEN it is the single loss-risk check (stop risk ≤ effective loss budget); same-symbol exposure is a projected stop-loss-risk bound in risk units; the liquidation-buffer check stays as a safety net ([Risk Engine](risk_engine_contract.md) gate table).

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Intent absent / policy multiplier invalid | Plan not compiled | `RISK_INTENT_INVALID` | unavailable |
| Capital / balance / geometry unusable | Plan not compiled | `SIZING_CAPITAL_UNAVAILABLE`, `SIZING_GEOMETRY_INVALID`, `NO_EXECUTABLE_NOTIONAL` | unavailable, never configured capital |
| Tier facts absent / stale | Plan not compiled | `VENUE_MARGIN_TIERS_UNAVAILABLE`, `VENUE_MARGIN_TIERS_STALE` | unavailable, never a static default |
| Mode undeclared / cross | Plan not compiled | `MARGIN_MODE_UNAVAILABLE`, `CROSS_MARGIN_SAFETY_UNAVAILABLE` | unavailable, no isolated fallback |
| No admissible leverage | Plan not compiled | `NO_FEASIBLE_FINANCING` | — |
| Submission-time allowance shrank | Risk rejects | `SIZING_RESOLUTION_DRIFT` | — |

Arbitrary exchange exception text is never persisted as a contract reason.

## Authority

Sizing and leverage resolution may only shrink exposure within Owner policy and venue facts; they
never raise the loss budget, change Risk limits, choose a margin mode the route does not declare,
or submit orders.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| ES-C01, ES-C02, ES-C03 | `tests/test_risk_intent.py` |
| ES-C04, ES-C05 | `tests/test_entry_sizing.py` |
| ES-C06, ES-C07 | `tests/test_leverage_resolution.py` |
| ES-C08 | `tests/test_venue_margin.py` |
| ES-C09, ES-C10 | `tests/test_entry_plan_execution.py`, `tests/test_risk_engine.py` (no dedicated SpecClaim) |

## Known conformance gaps

- **ES-C01 evidence admissibility.** The approved rule says explicit `risk_intent.evidence_refs`
  have "the same admissibility as `capture_evidence_refs`" and the stable reasons list
  `RISK_INTENT_EVIDENCE_INVALID`. `trading/entry_analysis.py::validate_hypothesis_references` checks
  only supporting/opposing/capture refs; explicit risk-intent refs are length-checked (1–8) but never
  checked against the supplied context, and `RISK_INTENT_EVIDENCE_INVALID` has 0 hits in `core/` and
  `extensions/`.
- **ES-C01 failure token.** The approved rule says an unknown posture fails with
  `RISK_INTENT_INVALID`; in code it fails `EntryHypothesis` schema validation (the `Literal`), so the
  visible failure is a hypothesis validation error, not that token.

## Decision references

- #3729 this contract (Contracts A/B/C); #3726 the policy-cap-as-execution-leverage coupling it
  removes; #1451 margin-mode safety.
