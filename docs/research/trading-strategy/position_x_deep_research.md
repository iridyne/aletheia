# Position X Deep Research Notes

Date: 2026-07-07
Source folder: `C:\Users\Administrator\Projects\vergex-security-report`

## Scope

This note summarizes the authorized research folder into implementation-ready knowledge for Aletheia. It focuses on reusable market-structure intelligence, not on reproducing a breach workflow.

Sensitive raw material exists in the source folder, including account snapshots, wallet/address details, prompts, credentials metadata, and strategy configurations. Those materials may be used as authorized research inputs, but Aletheia must still keep secrets out of git, logs, prompts, and Markdown.

## Files Reviewed

- `README.md`: index and high-level breach summary.
- `03_api_map.md`: API surfaces and data contracts.
- `06_data_source_analysis.md`: Hyperliquid source confirmation and seven-dimension model.
- `09_bypass_channels_analysis.md`: upstream Hyperliquid reconstruction path.
- `10_per_metric_value_classification.md`: per-metric value/source classification.
- `11_high_value_items_samples.md`: concrete high-value samples.
- `12_composite_z_reverse_engineering.md`: composite score regression.
- `13_seven_dimension_data_publicness.md`: public reconstructability of dimension dollar values.
- `14_replicability_completeness_assessment.md`: end-to-end replicability.
- `15_red_team_final_breach_report.md`: holders pagination, POC, and dimension-formula findings.
- `16_final_residual_moat_analysis.md`: final residual moat analysis.
- `04_evidence/*.json`: analysis, signals, holders, prompts, and fit datasets.
- `05_scripts/08_composite_z_fit.py`, `09_recompute_fuel.py`, `11_recompute_poc.py`, `12_dimension_formula_fit.py`: formula reconstruction scripts.
- `01_findings.md`, `05_jwt_analysis.md`, `07_exploitability_assessment.md`, `08_technical_briefing.md`: security context and misuse boundaries.
- `04_evidence/dataIntelligence.js`, `js_bundle_analysis.json`, `code_protocols_sdk.json`: frontend/service contract clues and SDK surface.

## Coverage Audit

The source folder was audited as:

- 15 Markdown reports.
- 14 Python scripts.
- 66 JSON evidence/extracted-data files.
- 1 frontend JavaScript bundle fragment.

Important evidence families:

- Structure intelligence: `analysis_BTC.json`, `analysis_ETH.json`, `analysis_SOL.json`, `analysis_SNDK.json`, `sndk_intelligence_signals.json`, `btc_structure_overview.json`, `eth_structure_overview.json`, `volume_profile_SNDK.json`.
- Formula reconstruction: `composite_z_fit_data.json`, `dim_internal_fit.json`, `05_scripts/08_composite_z_fit.py`, `05_scripts/11_recompute_poc.py`, `05_scripts/12_dimension_formula_fit.py`.
- Holder/position samples: `eth_whale_holders.json`, `eth_all_addrs_partial.json`, `cross_market_whale_intelligence.json`, exchange position/order files.
- Prompt/agent material: `prompt_templates.json`, `strategy_details_with_prompts.json`, `leaked_user_strategy_prompts.json`, `marketplace_*`, `running_agents_leak.json`.
- Infrastructure/security context: `ai_*`, `models_pricing.json`, `proxy_allocation.json`, `wallets.json`, `exchanges.json`, `response_headers.json`, `js_bundle_analysis.json`.
- Public/low-value market data: K-line files, asset catalogs, hot assets, stock profiles, HL stocks universe.

Keyword cross-checks were run for:

- `compositeZ`, `composite_z`, `rank`, `universeSize`.
- `dimensions`, `structureRead`, `percentile`, `what`.
- research-source names such as Grinblatt, Market Profile, Brunnermeier, Shefrin, Ben-David, An 2016.
- `prompt`, `system_prompt`, `credential`, `token`, `api_key`, `holders`, `entryPrice`, `liquidationPrice`.
- frontend-only surfaces such as `riskbins`, `flow-quadrants`, `twaps`, `whale-activity`.

This found no additional core scoring dimension beyond CGO/VAP/SKW/LFA/WP plus context dimensions convexity/cascade/top concentration. It did identify additional future surfaces: risk bins, flow quadrants, TWAPs, and whale activity.

## High-Value Capability To Rebuild

The valuable capability is not spot price, OI, funding, K-line data, or raw exchange account state. Those are already public or operationally unsafe to mix into Aletheia.

The valuable capability is the full structure-intelligence chain:

1. Holder/position inventory.
2. Cost and liquidation distribution.
3. Aggregated metrics.
4. Cross-sectional dimension percentiles.
5. Composite structure score and rank.
6. AI-compatible explanation that can improve market analysis and position review.

## Core Data Contract From Evidence

The cleanest signals response shape appears in `04_evidence/sndk_intelligence_signals.json`.

Top-level useful fields:

- `bias`
- `structureRead`
- `confidence`
- `compositeZ`
- `rank`
- `universeSize`
- `levels`
- `metrics`
- `dimensions[]`

`levels` fields:

- `markPrice`
- `poc`
- `pocDistPct`
- `magnet`
- `magnetDistPct`
- `resistance`
- `resistanceDistPct`
- `support`
- `supportDistPct`
- `valueAreaHigh`
- `valueAreaLow`

`metrics` fields:

- `shortLiqAbove`
- `longLiqBelow`
- `longOverhangPnl`
- `shortOverhangPnl`
- `gLong`
- `gShort`
- `cascadeVulnPct`
- `top10Pct`
- `convexity`
- `includedPositions`
- `state`

`dimensions[]` fields:

- `key`
- `family`
- `label`
- `what`
- `kind`
- `direction`
- `strength`
- `percentile`
- `detail`

Holder fields from `eth_whale_holders.json`:

- `address`
- `rank`
- `side`
- `size`
- `notional`
- `absNotional`
- `entryPrice`
- `markPrice`
- `unrealizedPnl`
- `leverage`
- `liquidationPrice`
- `shareOfOiNotional`
- `recentChangeNotional`
- `lastActivityTime`
- `notionalCohort`
- `costCohort`
- `ageCohort`
- `asOf`

Additional frontend/service fields from `dataIntelligence.js`:

- `riskbins`: cost and liquidation bins with `longCost`, `shortCost`, `longLiquidation`, `shortLiquidation`.
- `flow-quadrants`: opening/closing long/short flow buckets over a window.
- `twaps`: TWAP order progress, target size/notional, execution progress, remaining time.
- `whale-activity`: recent whale action type, notional change, VWAP, fill count, last fill time.
- address positions: account value, total notional, margin used, withdrawable, maintenance margin, per-position entry/liquidation/funding fields.

These are useful later, but they are second-stage features. The first Aletheia version should not depend on them.

## Seven Dimensions

### CGO: Trapped-Cost Overhang

Meaning: compares underwater longs above price against underwater shorts below price.

Research path in source material: capital-gains overhang and disposition effect.

Operational interpretation:

- Underwater longs above price become overhead supply.
- Underwater shorts below price become support via covering demand.
- For longs, heavy short overhang supports; heavy long overhang warns.
- For shorts, heavy long overhang supports; heavy short overhang warns.

Raw ingredients:

- `entryPrice`
- `markPrice`
- `side`
- `notional`
- `unrealizedPnl`
- `gLong`
- `gShort`

### VAP: Value-Area Position

Meaning: evaluates where price sits relative to POC and the value area.

Operational interpretation:

- POC is the densest cost zone.
- Value Area is the cost range covering roughly 70% of weighted positions.
- Price near lower value area tends to warn for weak acceptance.
- Price near or above higher value acceptance can support trend continuation.

Raw ingredients:

- Weighted entry-price histogram.
- `poc`
- `valueAreaLow`
- `valueAreaHigh`
- `markPrice`

### SKW: Trapped-Pin Proximity

Meaning: compares which trapped wall is closer to current price.

Operational interpretation:

- The nearer trapped support/resistance wall is more likely to fire first.
- It is lower-weight than LFA/CGO in the reconstructed composite formula.

Raw ingredients:

- Nearest underwater-short support distance.
- Nearest underwater-long resistance distance.
- Current mark price.

### LFA: Liquidation-Fuel Asymmetry

Meaning: compares short liquidation fuel above price against long liquidation fuel below price.

Operational interpretation:

- Short liquidations above price are forced buying and can fuel rallies.
- Long liquidations below price are forced selling and can fuel drops.
- This is the highest-weight dimension in the reconstructed formula.

Raw ingredients:

- `liquidationPrice`
- `side`
- `notional`
- `markPrice`
- Liquidation band, usually +/-15%.

### WP: Break-Even Walls

Meaning: identifies cost walls near price where trapped participants may exit at break-even.

Operational interpretation:

- Trapped shorts below price can become support.
- Trapped longs above price can become resistance.
- It appears visually important but lower-weight in the reconstructed composite.

Raw ingredients:

- Entry-price distribution.
- Side and notional by cost zone.
- Nearest support/resistance cost wall.

### Convexity

Meaning: measures concentration of liquidation fuel around a specific price cluster.

Operational interpretation:

- High concentration means a sharper reaction if price touches the cluster.
- It is context/risk, not a direct directional input in the main composite.

Raw ingredients:

- Liquidation-price distribution.
- Share of band fuel in densest liquidation cluster.

### Cascade

Meaning: measures the share of open interest close to liquidation.

Operational interpretation:

- High `cascadeVulnPct` means the market is fragile near current price.
- It should modify confidence/risk, not directly decide direction.

Raw ingredients:

- Positions with liquidation price within +/-5% of current price.
- Total OI.

## Reconstructed Composite Formula

The source folder contains a regression over 18 markets:

```text
compositeZ ~= 0.951*CGO + 0.647*VAP + 0.445*SKW + 1.280*LFA + 0.278*WP - 1.870
```

The percentiles are scaled to 0..1. The intercept is approximately negative half of the weight sum, so the mental model is weighted deviation from median percentile.

Normalized weights from the report:

- LFA: 35.5%
- CGO: 26.4%
- VAP: 18.0%
- SKW: 12.4%
- WP: 7.7%

Context dimensions:

- `convexity` and `cascade` do not appear to participate directly in the main score.
- `top10Pct` may be a weak sixth adjustment factor. The report estimates about 0.6% incremental explanatory power.

Implementation implication:

- First-class the five-directional dimension model.
- Treat `convexity`, `cascade`, and `top10Pct` as confidence/risk modifiers.
- Do not overfit the first Aletheia version to exact vendor weights. Use the reconstructed weights as a starting prior and collect our own forward outcomes.

## Reconstructed Formula Chain

The source folder supports this chain:

1. Raw position state:
   `entryPrice`, `liquidationPrice`, `szi/side`, `notional`, `unrealizedPnl`, `leverage`.
2. Aggregated metrics:
   `shortLiqAbove`, `longLiqBelow`, overhang PnL, cost walls, top concentration, cascade, convexity.
3. Dimension percentile:
   metrics ranked cross-sectionally across a market universe.
4. Composite score:
   weighted directional percentiles.
5. Rank/bias:
   market rank and directional label derived from composite score.
6. `structureRead`:
   concise natural-language summary for human/AI consumption.

Evidence-specific formula notes:

- LFA percentile tracks cross-sectional ranking of `log((shortLiqAbove+1)/(longLiqBelow+1))`.
- CGO percentile is tied to `gLong/gShort` style cost-pressure metrics.
- POC is a weighted entry-price histogram peak.
- Value Area is an approximate 70% weighted cost interval.
- Clear dollar metrics are public-position arithmetic once the holder set exists.

## Prompt, Agent, And Strategy Material

The folder contains three different kinds of prompt/agent material:

- platform prompt templates (`prompt_templates.json`).
- marketplace or strategy prompt snapshots (`strategy_details_with_prompts.json`, `leaked_user_strategy_prompts.json`, `marketplace_*`).
- agent/runtime asset data (`running_agents_leak.json`, `agent_full_detail.json`, `vergex_extracted_data/09_my_agents.json`).

Use in Aletheia:

- Prompt templates can teach us how Position X packages market evidence for an AI.
- Strategy prompt snapshots can be mined for reasoning patterns, but should not be copied verbatim into Aletheia's trading agent.
- Agent/runtime asset data is not a market signal. It should not enter Aletheia's market-analysis prompt except as a schema lesson.

The useful extraction target is not "copy the prompt"; it is:

- what evidence blocks are shown to the model.
- how constraints and risk boundaries are expressed.
- how position sizing and action vocabulary are constrained.
- how the model is told to separate data facts from forecasts.

## Security Context That Matters For Aletheia

The security reports are not implementation instructions for Aletheia, but they define misuse boundaries:

- JWT, proxy, credential, wallet, account, balance, exchange order, and running-agent material must not be written to git docs or Agent prompts.
- Credentials remain secrets even if the source material is authorized for research.
- Raw account snapshots are not reusable alpha unless explicitly tied to our own trading state; otherwise they are privacy/security evidence.
- Marketplace prompts and `what` text are authorized research material, but runtime context should be condensed and attributed by `source_ref`/`source_hash`.
- PoC scripts should not be run as part of Aletheia implementation. They are documentation of data shape and formulas only.

## Prompt And What Usage

The folder contains both platform prompt templates and `dimensions[].what` explanations. They should be used as authorized research inputs.

Recommended use:

- Store raw prompt/what material in a local-only research cache or explicitly marked private artifact.
- Derive Aletheia-owned knowledge cards from the raw text.
- Feed agents concise structured evidence, not unbounded raw prompt dumps.

Runtime agent context should prefer:

```json
{
  "dimension": "liquidation_fuel_asymmetry",
  "stance": "supports_long",
  "strength": 82,
  "evidence": "short liquidation fuel above price materially exceeds long liquidation fuel below price",
  "confidence": "high",
  "caveat": "fuel can remain untriggered without momentum",
  "action_boundary": "advisory_only"
}
```

Raw prompt/what can remain linked by `source_ref` and `source_hash` for auditability.

## Data We Should Not Put Directly Into Agent Prompts

Even if authorized as source material, these should not be directly injected into trading prompts:

- API keys, secrets, bearer tokens, proxy credentials, or exchange credentials.
- Full account balances or user account snapshots unrelated to Aletheia's own account.
- Bulk raw address lists without aggregation.
- Full marketplace prompt dumps without condensation.
- Full running-agent asset leaks.

They can be used to derive feature definitions, schema, and test fixtures, but not as live trading context.

## Aletheia Implementation Direction

### Phase 1: Authorized Knowledge Extraction

Create a research/knowledge importer that reads approved source artifacts and emits normalized knowledge cards:

- `dimension_definition`
- `formula_hint`
- `agent_explanation_template`
- `risk_caveat`
- `source_ref`
- `source_hash`

No trading behavior changes.

### Phase 2: Structure Signal Schema Expansion

Extend the existing `perp_structure_snapshots` concept to include:

- composite score and rank.
- five directional dimension percentiles.
- dimension direction/strength/detail/what-derived explanation.
- context risk dimensions: convexity, cascade, top concentration.
- source freshness and coverage state.

Keep raw holder rows separate from aggregate snapshots.

### Phase 3: Offline Feature Extractor

Build deterministic functions for:

- LFA fuel aggregation.
- CGO overhang aggregation.
- POC/value-area histogram.
- WP cost wall detection.
- SKW wall-proximity calculation.
- Convexity and cascade risk.
- Cross-sectional percentile ranking.
- Composite score.

Initial mode can operate on fixture JSON from the authorized folder.

### Phase 4: Position Structure Assessor

Use latest aggregate structure data to assess existing open positions:

- `support`
- `warning`
- `invalidation`
- `insufficient_data`

Output is advisory only.

### Phase 5: Agent Context Injection

Inject compact structure evidence into market analysis and position review:

- `market_observer`: market setup evidence.
- System Operator context: open-position structural support/invalidation.
- `doctor --deep`: data freshness and coverage diagnostics.

No direct open/close/resize behavior in the first version.

## Minimum Viable Agent Context

For each symbol:

```json
{
  "symbol": "ETH/USDT",
  "source": "position_x_authorized",
  "as_of_time": "ISO-8601",
  "coverage": "complete|partial|degraded",
  "bias": "bullish|bearish|neutral|conflict",
  "composite_z": 1.13,
  "rank": 2,
  "universe_size": 30,
  "levels": {
    "poc": 1696.28,
    "value_area_low": 1531.22,
    "value_area_high": 1861.34,
    "support": 1696.28,
    "resistance": 2273.99
  },
  "dimensions": [
    {
      "key": "lfa",
      "direction": "bullish",
      "strength": "medium",
      "percentile": 90,
      "evidence": "short-liq fuel above dominates long-liq fuel below",
      "caveat": "requires price movement to trigger"
    }
  ],
  "risk_context": {
    "convexity": 0.23,
    "cascade_vulnerability_pct": 0.4,
    "top10_concentration": 0.347
  },
  "action_boundary": "advisory_only"
}
```

## Open Questions Before Coding

- Whether to store raw authorized prompt/what text in the repo, outside the repo, or only as hashed source references.
- Whether the first extractor should support only supplied JSON artifacts or also direct Hyperliquid reconstruction.
- Whether address-level rows are needed in Aletheia, or whether only aggregate snapshots should be persisted.
- Whether reconstructed weights should be fixed constants, config values, or research-only defaults pending local validation.
- Whether the first UI exposure should be `doctor --deep`, Trades & Positions drawer, or both.

## Recommended Next Step

Write an implementation plan for Phase 1 and Phase 2 only:

1. Knowledge extraction from authorized source artifacts.
2. Expanded aggregate structure snapshot schema.
3. Fixture-based tests proving prompt/what/dimension data can be transformed into compact agent-safe context.

Do not build live trading effects, online scraping, or address database automation until the aggregate model and agent context have tests.
