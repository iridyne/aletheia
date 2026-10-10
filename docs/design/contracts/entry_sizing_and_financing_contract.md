# Entry Sizing and Financing Contract

> Status: current authority.
> Related: [Risk Engine Contract](risk_engine_contract.md) (§5 sizing, checks #13/#18/#19),
> [Entry Plan Contract](entry-plan-contract.md) (Plan schema and execution mainline).
> Decision references: #3729 (this contract), #3726 (the coupling it removes),
> #1451 (margin-mode safety).

## Scope and authority

This Contract owns three decisions on the canonical entry path and nothing else:

1. **AI Risk Intent** — what qualitative risk posture the System Operator may express.
2. **Adaptive Sizing** — how a loss budget and stop geometry become a target exposure.
3. **Deterministic Leverage Resolution** — how much leverage finances that exposure.

Core semantics:

> AI decides qualitative risk intent; sizing decides target exposure; leverage only
> finances that exposure.

An AI never emits leverage, notional, margin, or a risk multiplier. A mechanical
resolver never enlarges the target exposure, and never treats the Owner policy
leverage cap as the execution leverage.

## Contract A — AI Risk Intent

`core/aletheia/trading/risk_intent.py`, `EntryHypothesis.risk_intent`.

| Field | Contract |
|---|---|
| `posture` | `BASELINE` or `REDUCED`. There is no `INCREASED` in V1. |
| `reason` | Non-empty, ≤512 chars. |
| `evidence_refs` | 1–8 admissible current fact refs, same admissibility as `capture_evidence_refs`. |

- `ENTRY_PLAN` carries a `RiskIntent`; `NO_PLAN` clears it.
- A **partial or omitted** intent is normalized rather than rejected: a bare posture
  string, or a posture without a reason/evidence, resolves to that posture (omitted →
  `BASELINE`) with the reason and `evidence_refs` taken from the hypothesis's own
  `capture_reason` / `capture_evidence_refs`, which `ENTRY_PLAN` already requires and
  which the reference validator checks against the supplied context. The live
  System Operator model is not reliable at emitting a nested object it was never
  trained for, and defaulting to the Owner baseline is the non-expanding direction:
  an omitted intent can never increase risk, and no numeric field exists to omit.
  An explicit posture is never overridden, and an unknown posture (`INCREASED`, …)
  still fails validation. The normalization is logged.
- An explicit intent is validated as written: nonempty reason, 1–8 admissible refs.
- `BASELINE` maps to multiplier `1.0`.
- `REDUCED` maps to `policy.loss.reduced_risk_multiplier`, which must satisfy `0 < m < 1`.
- Anything else fails with `RISK_INTENT_INVALID`; a hypothesis carrying a numeric
  leverage/notional/margin field is rejected by the frozen `extra="forbid"` schema.
- Insufficient or conflicting evidence stays `NO_PLAN`; a smaller position is never a
  substitute for an invalid thesis.
- `risk_tier` keeps its existing payoff-geometry meaning and is not merged with posture.

## Contract B — Adaptive Sizing

`core/aletheia/trading/entry_sizing.py`, policy version `adaptive-risk-intent-sizing/v2`.

```text
baseline_loss_budget_usdt = min(shared_hard_capital_base, fresh_available_balance)
                            x max_single_trade_risk_pct / 100
effective_loss_budget_usdt = baseline_loss_budget_usdt x risk_intent_multiplier

stop_fraction              = |worst_entry_price - stop_price| / worst_entry_price
raw_target_notional_usdt   = effective_loss_budget_usdt / stop_fraction
target_notional_usdt       = min(raw_target, every constraint below)
margin_budget_usdt         = min(fresh_available_balance, policy.max_position_margin_usdt)
```

Constraints applied to `target_notional_usdt`, each recorded in `binding_constraints`:

| Token | Bound |
|---|---|
| `remaining_total_exposure` | `policy.capital.max_total_exposure_pct` of the capital base, net of the same pool's committed notional |
| `remaining_account_leverage` | `policy.leverage.account_cap x equity`, net of the same pool's committed notional |
| `market_liquidity` | executable notional the entry market can absorb (the preflight depth envelope) |
| `per_symbol_risk_units` | `max_per_symbol_entries` full-size **risk units**, projected as same-symbol stop-loss risk — never `risk_pct x leverage` |
| `instrument_max_notional` | venue/instrument hard notional bound |

`max_single_trade_risk_pct` has exactly one meaning in this contract: the share of the
capital base that may be lost at the stop. Margin budget is a separate quantity; one
field never stands for both.

When the capital base, available balance, policy identity, or geometry is unusable, the
sizer returns a typed `unavailable` resolution (`SIZING_CAPITAL_UNAVAILABLE`,
`SIZING_GEOMETRY_INVALID`, `NO_EXECUTABLE_NOTIONAL`). Configured capital is never a
substitute for an unreadable account authority.

`SizingResolution` persists: policy version, posture, multiplier and its source, baseline
and effective loss budget, worst entry and stop prices, stop distance %, raw and final
target notional, margin budget, binding constraints, account/policy snapshot identities,
and `facts_fingerprint`.

## Contract C — Deterministic Leverage Resolution

`core/aletheia/trading/leverage_resolution.py`.

The resolver answers one question: *which leverage safely and truthfully implements the
most of the target exposure, without exceeding it?*

1. `policy_leverage_cap` is the Owner policy ceiling (`get_max_leverage`); it is a
   ceiling, never the execution leverage.
2. Venue tier, tier `max_leverage`, and maintenance-margin rate come from a qualified
   `VenueMarginEnvelope`.
3. Candidates are enumerated from **low to high**:
   `candidate_notional(L) = min(target_notional, margin_budget x L, remaining venue and
   instrument notional capacity, remaining hard exposure capacity)`.
4. The venue tier is selected for the **projected** position notional
   (`existing + unresolved + reservation + candidate`).
5. A candidate is admissible only when `L <= tier.max_leverage`, the candidate notional is
   inside the tier/instrument range, the margin mode matches the submitted one, evidence is
   fresh and complete, and liquidation safety is provable for that margin mode
   (`(1/L - tier.maintenance_margin_rate) x 100 >= liquidation buffer`).
6. Among admissible candidates, choose the **largest execution notional that does not
   exceed the target**; among equal notionals choose the **lowest leverage**.
7. No admissible candidate → `no_feasible_financing` (`NO_FEASIBLE_FINANCING`). A plan is
   never produced that cannot be financed.

Consequences this contract requires: a target reachable at 5x is executed at 5x even when
18x is safe; a target needing 20x while only 18x is provable shrinks the execution notional
instead of raising the loss budget; 1x is used when 1x suffices.

`LeverageResolution` persists: status, reason, policy cap, venue absolute cap, safe
leverage cap, required leverage for target, execution leverage, target and execution
notional, target fill ratio, margin budget, projected position notional, selected tier and
maintenance-margin rate, margin mode, binding constraint, liquidation buffer, provenance,
and `facts_fingerprint`.

## Margin mode and liquidation safety

`core/aletheia/execution/margin_mode_policy.py` declares the mode each execution route
actually submits, and both the resolver and the submission path read the same declaration.

- **Isolated** (V1 qualified): requires the exact venue tier and maintenance-margin rate;
  the position-local liquidation model then matches what the adapter submits. `bitget_demo`
  is the one registered Entry Plan route and declares `isolated`.
- **Cross**: `calc_liquidation_price()` is an isolated-position estimate and MUST NOT be
  reused as cross-account safety. Cross resolves only from a fresh venue-reported safety
  fact or an account-aware conservative model; without one the result is
  `CROSS_MARGIN_SAFETY_UNAVAILABLE`. There is no isolated fallback.
- A route with no declared mode fails closed with `MARGIN_MODE_UNAVAILABLE`.
- Spot is out of product scope and never reaches this path; the residual 1x
  `not_applicable` branch in code is not a support claim.

Venue tier facts come from the venue that will **hold the position** — the execution
route's venue, not the candidate's market-data venue (they differ whenever analysis and
execution run on different venues, e.g. a binance-sourced universe executed on
`bitget_demo`). The Plan's own `venue` still identifies the market data. Facts come from
`VenueMarginEnvelope` (CCXT public market leverage tiers where
the venue exposes them, persisted with fetch timestamp and provenance, read through a
freshness TTL). The read path is synchronous and network-free; the fetch is out-of-band, and
the runtime maintains it: the `venue_margin_facts` scheduler job refreshes exactly the
instruments the Planner's current claimable queue reads (`list_candidate_planning_demand`),
skipping rows that are already fresh, so a rolling universe costs one public tier fetch per
newly demanded instrument. `uv run aletheia data venue-margins --venue <venue> --symbol
<symbol>` remains the explicit single-instrument/ops entry point (add `--show` to inspect
the cached row). Until a fresh row exists for an instrument, its leveraged entry is
unavailable — that is the fail-closed contract, not a defect to work around. An absent, stale, or incomplete envelope is a typed failure
(`VENUE_MARGIN_TIERS_UNAVAILABLE`, `VENUE_MARGIN_TIERS_STALE`); a static in-repo
maintenance-margin default is never the authority.

## EntryPlan / Proposal schema and authority migration

New plans write explicit fields; historical JSON and hashes are never rewritten.

| Field | Meaning |
|---|---|
| `risk_intent_posture`, `risk_intent_multiplier` | the frozen intent and its multiplier |
| `sizing_resolution`, `leverage_resolution` | the two frozen resolutions |
| `policy_leverage_cap` | Owner policy ceiling |
| `execution_leverage` | financing actually resolved for this plan |
| `target_notional_usdt`, `execution_notional_usdt` | wanted vs financed exposure |
| `margin_mode` | the submitted mode the resolution is valid for; the Gateway reads it from this frozen Plan, so no Proposal can carry a different one |

- `TradeProposal.leverage = EntryPlan.execution_leverage`. The Plan is the sole
authority for the submission margin mode: the order boundary reads it from the frozen
Plan (the Proposal does not repeat it, so the two can never disagree).
- Quantity is derived from `execution_notional_usdt`, not from the policy cap.
- `max_leverage`, `max_notional`, and `max_risk_pct` remain populated as legacy mirrors so
  historical rows and existing readers keep working; only `execution_leverage` and
  `execution_notional_usdt` carry submission authority. A plan without them is historical
  and obtains no new submission authority.
- Schema and behavior fingerprints upgrade with the new fields; the
  `available-balance-risk-budget/v1` policy version is retired in favour of
  `adaptive-risk-intent-sizing/v2`.

## Risk convergence

Risk calls the same pure functions with submission-time facts and only verifies:

- identical facts → identical or equivalent resolution;
- a smaller recomputed execution notional → reject `SIZING_RESOLUTION_DRIFT`;
- a larger recomputed allowance → keep the original, never silently enlarge;
- Risk never mutates leverage, notional, or margin mode.

Check #13 becomes the single loss-risk reading of `max_single_trade_risk_pct`
(`stop risk <= effective loss budget`), and check #19 becomes a projected same-symbol
stop-loss-risk bound in risk units. Check #18 (liquidation buffer) stays as the safety net
and is expected to pass by construction for plans the resolver produced.

Key invariant: **with the same facts snapshot, a compiled Plan must not be rejected by Risk
for leverage, margin, or stop-risk feasibility.**

## Stable failure reasons

`RISK_INTENT_INVALID`, `RISK_INTENT_EVIDENCE_INVALID`, `SIZING_CAPITAL_UNAVAILABLE`,
`SIZING_GEOMETRY_INVALID`, `NO_EXECUTABLE_NOTIONAL`, `VENUE_MARGIN_TIERS_UNAVAILABLE`,
`VENUE_MARGIN_TIERS_STALE`, `MARGIN_MODE_UNAVAILABLE`, `CROSS_MARGIN_SAFETY_UNAVAILABLE`,
`NO_FEASIBLE_FINANCING`, `SIZING_RESOLUTION_DRIFT`.

Arbitrary exchange exception text is never persisted as a contract reason.

## Non-goals

- No legacy conviction/Kelly/persona sizing enters this path; `compute_position_size()`
  and `vol_target_size()` keep their existing (non-canonical) status.
- No additional forecast-volatility or correlation sizing term in V1.
- No renaming or migration of `risk_tier`, Alpha, Serenity, or execution redesigns.

## Verification

`tests/test_entry_sizing.py`, `tests/test_leverage_resolution.py`,
`tests/test_venue_margin.py`, `tests/test_risk_intent.py`, and the canonical
Entry → Compiler → Proposal → Risk → Gateway integration tests.
