# Position Structure Assessor Design

Date: 2026-07-07

## Purpose

Build an observation-only auxiliary module that evaluates current open positions using four structure dimensions derived from Hyperliquid/Vergex-style aggregate positioning data:

- `LFA`: liquidation fuel asymmetry.
- `CGO`: capital gains overhang / trapped cost pressure.
- `VAP`: value-area position around POC and Value Area.
- `Cascade risk`: liquidation convexity plus near-liquidation cascade vulnerability.

The module answers one question:

> Does the current positioning structure still support this open position, warn against it, or invalidate its original thesis?

It is not a trading agent. It must not open, close, resize, lever up, or unlock live trading.

## Existing System Fit

Current relevant Aletheia paths:

- `positions`: source of open position state.
- `perp_structure_snapshots`: latest aggregate structure row per symbol.
- `market_observer`: creates the original opportunity and thesis.
- `agent_shell/trading_context.py`: builds System Operator context.
- `risk_critic`: writes advisory risk lessons.
- `doctor --deep`: operator-facing diagnostics entry point.
- Trades & Positions drawer: natural UI surface for per-position explanation.

The assessor should sit in the read-only analytics/diagnostics layer first. It can later feed System Operator context only after it has collected enough local evidence.

## Non-Goals

- No automatic position close.
- No automatic stop movement.
- No position sizing change.
- No leverage increase.
- No risk limit change.
- No kill-switch or live-mode changes.
- No address-level wallet database.
- No import of strategy prompts, account data, credentials, or raw leaked evidence.
- No direct reuse of VergeX `composite_z` as a trade switch.

## Inputs

For each open position:

- `position_id`
- `symbol`
- `direction`
- `entry_price`
- `current_price`
- `stop_price`
- `take_profit_price`
- `leverage`
- `liquidation_price`
- `opened_at`
- `unrealized_pnl`
- `opportunity_id`
- `proposal_id`
- original thesis / decision reasoning when available

For latest structure snapshot:

- `symbol`
- `source`
- `venue`
- `as_of_time`
- `data_quality`
- `poc`
- `value_area_low`
- `value_area_high`
- `short_liq_above_usd`
- `long_liq_below_usd`
- `long_unrealized_pnl_usd`
- `short_unrealized_pnl_usd`
- `top10_concentration`
- `top50_concentration`
- `cascade_risk`
- `convexity`

Freshness rule:

- Use only rows with `as_of_time` or `observed_at` within 60 minutes.
- If data is stale or missing, return `insufficient_data`.
- If `data_quality=degraded`, keep the assessment but cap confidence and explicitly report degradation.

## Output Contract

The assessor returns deterministic structured output:

```json
{
  "position_id": "pos_123",
  "symbol": "BTC/USDT",
  "direction": "long",
  "verdict": "support",
  "confidence": 72,
  "recommended_use": "hold_ok",
  "primary_reason": "LFA supports the long and price remains inside value area; cascade risk is moderate.",
  "dimension_reads": {
    "lfa": {
      "stance": "supports_position",
      "strength": 78,
      "evidence": "short-liq above exceeds long-liq below"
    },
    "cgo": {
      "stance": "neutral",
      "strength": 42,
      "evidence": "overhang pressure is balanced"
    },
    "vap": {
      "stance": "supports_position",
      "strength": 65,
      "evidence": "current price remains near POC / value area"
    },
    "cascade": {
      "stance": "risk_amplifier",
      "strength": 38,
      "evidence": "near-liquidation concentration is not elevated"
    }
  }
}
```

Allowed `verdict` values:

- `support`: structure supports holding the position.
- `warning`: structure is mixed or risk is rising.
- `invalidation`: structure now contradicts the position thesis.
- `insufficient_data`: data is missing, stale, or unusable.

Allowed `recommended_use` values:

- `hold_ok`: no structure objection.
- `tighten_stop`: risk is rising; advisory only.
- `no_add`: do not add exposure.
- `manual_review`: strong contradiction or high cascade risk.

## Dimension Semantics

### LFA: Directional Fuel

For a long:

- Short liquidation fuel above current price supports the position.
- Long liquidation fuel below current price warns against the position.

For a short:

- Long liquidation fuel below current price supports the position.
- Short liquidation fuel above current price warns against the position.

This is the highest-priority direction dimension because it measures forced-flow fuel, not just sentiment.

### CGO: Trapped Cost Pressure

CGO asks which side is carrying more painful unrealized loss.

For a long:

- Heavy short overhang supports the long, because a rally can force short covering.
- Heavy long overhang warns against the long, because holders may sell into rebounds or capitulate lower.

For a short:

- Heavy long overhang supports the short.
- Heavy short overhang warns against the short.

The module must report this as pressure, not as certainty. Trapped positions can hold longer than expected.

### VAP: Position Quality Around POC / Value Area

VAP checks whether the position is well located relative to cost concentration.

For a long:

- Entry/current near POC or lower Value Area is healthier than entry far above Value Area.
- Current price falling back below POC after a high entry warns that the trade may have chased away from value.

For a short:

- Entry/current near POC or upper Value Area is healthier than entry far below Value Area.
- Current price reclaiming POC after a low entry warns that the short thesis is weakening.

VAP is a location-quality check, not a standalone direction signal.

### Cascade: Risk Amplifier

Cascade combines:

- `convexity`: how concentrated liquidation prices are.
- `cascade_risk`: how much OI is near forced liquidation.

It does not decide long or short. It changes how aggressively the module trusts the other dimensions:

- High cascade risk lowers confidence.
- High cascade risk with adverse LFA/CGO upgrades `warning` to `invalidation`.
- High cascade risk with supportive LFA still recommends `no_add` or `manual_review`, not `hold_ok`.

## Scoring Model

Initial deterministic scoring:

```text
direction_support =
  0.40 * lfa_score
+ 0.30 * cgo_score
+ 0.20 * vap_score
+ 0.10 * thesis_alignment_score

risk_penalty =
  max(cascade_score, convexity_score)
+ stale_data_penalty
+ degraded_quality_penalty

final_score = direction_support - risk_penalty
```

Initial verdict mapping:

```text
final_score >= 35       -> support
-15 <= final_score < 35 -> warning
final_score < -15       -> invalidation
missing/stale data      -> insufficient_data
```

This is deliberately simple. The purpose is to produce explainable local evidence before optimizing weights. Weights must be recalibrated only after outcome data exists.

## Observation And Validation

Every assessment should be stored with enough data to settle later:

- `position_id`
- `symbol`
- `direction`
- `assessment_time`
- `verdict`
- `confidence`
- each dimension stance/strength
- current price at assessment
- stop distance
- unrealized PnL at assessment

Outcome windows:

- 1h
- 4h
- 24h

Outcome metrics:

- forward return from assessment time.
- maximum adverse excursion.
- maximum favorable excursion.
- whether stop loss or take profit occurred.
- whether position was closed manually or by max hold.

Validation questions:

- Do `support` assessments have better forward return or lower adverse excursion than `warning` assessments?
- Do `invalidation` assessments precede larger drawdowns or stop losses?
- Does high cascade risk predict larger adverse excursions?
- When structure assessment disagrees with RSI/funding/OI, which side is closer to realized outcome?

Promotion gate:

- Keep observation-only for at least two weeks.
- Require enough settled assessments per verdict bucket before using in System Operator context.
- Do not allow it to affect execution or hard risk rules without a separate approved design.

## Integration Phases

### Phase 0: Design And Contract

- Add this design.
- Add data-utilization claims if the module writes new assessment tables.
- Keep current `perp_structure_snapshots` as the only structure input.

### Phase 1: Read-Only Analyzer

Add:

- `aletheia/analytics/position_structure_assessor.py`
- focused unit tests for LFA/CGO/VAP/Cascade stance.
- no scheduler side effects yet.

The analyzer reads open positions and latest structure rows and returns in-memory assessments.

### Phase 2: Persistence And Doctor

Add:

- `position_structure_assessments`
- `position_structure_outcomes`
- repository insert/latest/settle functions.
- `doctor --deep` compact summary.

Still no trading behavior change.

### Phase 3: Advisory Surfaces

Expose the latest assessment in:

- `risk_critic` context.
- `agent_shell/trading_context.py` as advisory context for System Operator.
- Trades & Positions position drawer.

Recommended wording must stay advisory:

- "structure supports holding"
- "structure warns against adding"
- "manual review suggested"

Avoid:

- "close now"
- "increase position"
- "override stop"

### Phase 4: Evidence-Based Calibration

After observation outcomes exist:

- compute hit rates and drawdown rates by verdict.
- compare against baseline technical/funding/OI signals.
- recalibrate weights only if evidence supports it.

## Safety Requirements

- The module is read-only against trading state.
- The module may write only analytics/assessment records.
- It must not call exchange APIs directly.
- It must not place or close orders.
- It must not modify positions.
- It must not modify risk configuration.
- It must not expose secrets, prompts, wallet addresses, or account data.
- If structure data is stale, missing, or degraded, the module must say so explicitly.

## Testing Requirements

Minimum tests before implementation is considered complete:

- Long position: LFA support produces supportive stance.
- Long position: adverse LFA produces warning.
- Short position: LFA direction reverses correctly.
- CGO long-overhang warns a long and supports a short.
- VAP identifies chase risk outside Value Area.
- High cascade risk caps confidence and recommends `no_add` or `manual_review`.
- Stale structure data returns `insufficient_data`.
- `data_quality=degraded` caps confidence and is visible in output.
- Analyzer never calls execution/risk mutation functions.

## Open Decisions

Decision 1:

- Whether Phase 2 persistence should be implemented before UI exposure.
- Recommendation: yes. Without persistence, there is no way to prove added value.

Decision 2:

- Whether the first UI surface should be `doctor --deep` or Trades & Positions.
- Recommendation: `doctor --deep` first, because it is operator-facing and avoids frontend scope.

Decision 3:

- Whether to include `WP` as a fifth dimension later.
- Recommendation: defer. `WP` overlaps with VAP/CGO and should be added only if validation shows missing explanatory power.
