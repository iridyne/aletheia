# Transparent Cross-Sectional Universe Research Report

> Date: 2026-08-10
>
> Role: dated research evidence
>
> Decision status: no implementation or experiment approved

## 1. Question

Can Aletheia compare `universe-step-v1` with a transparent, continuous,
cross-sectional ranking policy without weakening the current authority or
pretending that incomplete derivative data is evidence?

The answer is **yes as a separate Shadow candidate**, but not by directly using
the current continuous total score. The smallest defensible candidate is:

```text
hard eligibility
  -> pessimistic continuous opportunity evidence
  -> within-run relative ranks
  -> existing fine filters / dedupe / quotas / allocator
  -> prospective Shadow outcomes only
```

## 2. Research process

The analysis used four evidence layers:

1. **Current Aletheia behavior:** traced Step-v1, continuous point and
   pessimistic scoring, Twin persistence/evaluation, allocator, feature
   construction, derivative collectors, and data-coverage checks.
2. **Open-source implementations:** compared how production-oriented frameworks
   form liquid candidate pools, refresh them, control churn, and separate
   universe selection from portfolio/execution.
3. **Academic evidence:** reviewed cross-sectional crypto factors, continuous
   learning-to-rank methods, transaction-cost sensitivity, and backtest
   multiple-testing risk.
4. **Runtime availability:** used a read-only Fedora snapshot to distinguish
   fields that exist in code from fields that are sufficiently populated for a
   prospective comparison.

No external source is treated as proof that a factor is profitable in Aletheia.
The sources constrain the candidate design; Aletheia's prospective outcomes
must establish its own result.

## 3. Findings

### 3.1 What is reusable

- The current Step-v1 policy is a valid frozen control.
- The existing continuous curve library provides deterministic, bounded,
  auditable factor evidence and pessimistic missing-data semantics.
- The existing Twin machinery already provides scanner-run assignment,
  full-candidate records, selected-candidate `4h` cost-aware outcomes, and a
  non-authoritative boundary.
- The existing allocator already owns fine tradability filters, spot/perp
  dedupe, family quotas, exploration, and final candidate count. A second
  allocator is unnecessary.

### 3.2 What must not be copied unchanged

The current continuous total score is not the proposed relative-rank policy:

- quote volume is consumed by both tradability and allocator liquidity;
- spread is consumed by both tradability and allocator cost;
- freshness is consumed by both tradability and allocator staleness;
- price-derived RSI/returns appear in family evidence and later penalties;
- point and pessimistic paths may select different winning families;
- absolute anchors do not normalize cross-sectional scale within one scanner
  run;
- point priors can make missing spread/depth look better than pessimistic
  evidence.

Using that total directly would preserve duplicate influence and merely express
it as a float. The candidate therefore assigns each raw feature one scoring
owner and uses the pessimistic path only.

### 3.3 Common external pattern

Across the reviewed implementations, the recurring structure is:

```text
point-in-time tradable universe
  -> liquidity/capacity filter
  -> continuous score or rank
  -> discrete Top-N membership
  -> rebalance/churn control
  -> independent risk and execution
```

Continuous scoring does not make membership continuous. A finite capital and
attention budget still requires a discrete Top-N decision. The useful change is
to make evidence ordering smooth and relative before that boundary.

### 3.4 Factor evidence

- Crypto momentum/reversal changes materially with size and liquidity; one
  absolute rule across all assets is poorly supported.
- Cross-sectional ranking is a better semantic fit than treating a `0..100`
  score as a calibrated probability.
- Liquidity, spread, depth, turnover, and slippage belong in selection economics,
  not as an afterthought.
- OI, funding, long/short ratios, and taker flow can add positioning context,
  but venue definitions and historical coverage differ. They are not mandatory
  V1 inputs until exact prospective coverage is demonstrated.
- Trying many factors, windows, thresholds, and weights and then selecting the
  winner creates backtest overfitting. Candidate identity and the primary
  comparison must be frozen before outcomes.

## 4. Data-source audit

### 4.1 Existing Aletheia inputs

The repository already models or collects:

| Layer | Existing evidence |
| --- | --- |
| Price/activity | closed OHLCV, quote volume, returns, RSI, ATR, Bollinger values, relative strength |
| Executability | bid/ask spread, depth within 10 bps, freshness, market identity |
| Derivatives | funding, OI, OI change, optional long/short ratio, optional taker ratio |
| Context | liquidation events, CoinGecko market data, macro and DefiLlama on-chain summaries |

Primary implementation references are listed in section 7.

### 4.2 Read-only Fedora snapshot

The 2026-08-10 snapshot showed active collection but incomplete feature
propagation:

| Evidence | Observed snapshot |
| --- | ---: |
| 1m OHLCV | 208,171 rows / 209 symbols in 24h |
| 15m OHLCV | 11,804 / 156 |
| 1h OHLCV | 2,069 / 109 |
| 4h OHLCV | 478 / 106 |
| Orderbook | 16,508 / 109 |
| Funding | 23,386 / 109 |
| OI | 22,502 / 109 |
| Universe feature snapshots | 8,772 |
| Feature depth coverage | 12.72% |
| Feature funding coverage | 80.85% |
| Feature OI coverage | 80.79% |
| Feature OI-delta coverage | 13.13% |
| Feature spread coverage | 31.51% |
| Feature taker-ratio coverage | 0% |

Binance OI rows contained long/short ratio on about 60% of rows; Hyperliquid did
not. No liquidation events were present. These are dated facts, not current
guarantees.

The immediate conclusion is **not** “add another provider.” Collection tables
were flowing, while the Universe feature snapshot was losing much of the
available spread/depth/OI-change evidence. The cheaper next investigation is
the existing collector-to-feature join, timestamp, venue, and freshness path.

### 4.3 Proposed input contract

Required before a candidate receives a score:

- exact point-in-time symbol, venue, market type, listing/tradability state;
- complete closed OHLCV for the frozen feature window;
- positive price and quote volume;
- explicit `observed_at`, `available_at`, provider, units, and freshness;
- no forward-filled price, volume, spread, depth, or derivative observation;
- at least 20 eligible candidates in the declared cross-section.

Spread and depth are required for an **authority-eligible** candidate design,
but the Shadow draft must stay blocked until their exact feature-snapshot
coverage reaches the preregistered level. A missing value is not assigned a
reassuring neutral score.

OI, funding, long/short ratio, taker flow, liquidations, and on-chain context are
diagnostic overlays in V1. A field may enter the primary score only in a new
epoch after at least seven complete UTC dates, at least 90% exact-cohort
coverage, stable units/provider semantics, and no unresolved source conflict.

## 5. Design consequences

The evidence supports a small challenger, not an AI black box:

- use empirical percentile ranks inside each complete scanner run;
- use one owner per raw feature so repeated transforms cannot manufacture
  corroboration;
- rank only pessimistic continuous family evidence;
- retain hard eligibility and the current allocator;
- record the whole eligible denominator, not just selected winners;
- evaluate scanner runs as clusters and include turnover and cost;
- freeze one candidate before observing outcomes.

NOFX AI500 is useful only as a product pattern: a dynamic scored coin pool with
a Top-N interface. Its public documentation does not expose sufficient feature,
training, calibration, cost, or causal-evaluation detail to use its score as an
authority model.

## 6. Explicit non-conclusions

This research does not establish that:

- relative ranking is profitable;
- Continuous should replace Step-v1;
- OI or long/short ratio improves selection;
- the current Twin result can be pooled with the new candidate;
- a Shadow winner may automatically receive Paper authority.

## 7. Reference index

### Aletheia sources

- [`aletheia/universe/scanners.py`](../../../../../aletheia/universe/scanners.py) — frozen Step-v1 family scoring.
- [`aletheia/universe/continuous_scoring.py`](../../../../../aletheia/universe/continuous_scoring.py) — point/pessimistic curves, feature groups, and current total-score composition.
- [`aletheia/universe/twin_scoring.py`](../../../../../aletheia/universe/twin_scoring.py) and [`twin_allocation.py`](../../../../../aletheia/universe/twin_allocation.py) — diagnostic score/allocation replay.
- [`aletheia/universe/twin_experiment.py`](../../../../../aletheia/universe/twin_experiment.py), [`twin_persistence.py`](../../../../../aletheia/universe/twin_persistence.py), and [`twin_evaluation.py`](../../../../../aletheia/universe/twin_evaluation.py) — prospective identity, evidence, and evaluation.
- [`aletheia/universe/features.py`](../../../../../aletheia/universe/features.py) — Universe feature construction.
- [`aletheia/data/data_coverage.py`](../../../../../aletheia/data/data_coverage.py) and [`aletheia/data/collectors/derivatives.py`](../../../../../aletheia/data/collectors/derivatives.py) — source freshness and derivative collection.
- [Continuous curve specification](../02_continuous_curve_specification_2026-08-07.md), [adversarial review](../03_adversarial_review_2026-08-07.md), and [reference notes](../04_references_and_practical_notes.md).
- [Continuous scoring safety Proposal](../../../proposals/universe_continuous_scoring_safety.md) and [Twin Shadow contract](../../../contracts/universe_twin_shadow_contract.md).

### Open-source and vendor references

- [NOFX AI500 API](https://nofxos.ai/api-docs) and [NOFX strategy module](https://github.com/NoFxAiOS/nofx/blob/dev/docs/architecture/STRATEGY_MODULE.md) — dynamic AI-rated Top-N pool; insufficient public scoring detail for model adoption.
- [Freqtrade Pairlists](https://docs.freqtrade.io/en/2023.12/plugins/) — volume-ranked pools, chained filters, refresh and cache behavior.
- [QuantConnect Crypto Universe](https://www.quantconnect.com/docs/v2/writing-algorithms/universes/crypto) and [Universe Settings](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/universe-selection/universe-settings) — point-in-time selection, USD-volume comparability, refresh, and minimum time in universe.
- [Trading Strategy examples](https://github.com/tradingstrategy-ai/getting-started) — TVL/volume eligibility and capacity-aware construction.
- [Hermes Trader](https://github.com/Julian-dev28/hermes-trader) — volume prefilter, bounded scan budget, and rotating tail coverage; implementation pattern only.
- [Bitget Open Interest](https://www.bitget.com/api-doc/classic/contract/market/Get-Open-Interest), [Binance OI statistics](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest-Statistics), and [Bybit OI](https://bybit-exchange.github.io/docs/v5/market/open-interest) — public derivative inputs with venue-specific definitions and retention.

### Academic and quantitative-method references

- [Common Risk Factors in Cryptocurrency (NBER 25882)](https://www.nber.org/papers/w25882) — market, size, and momentum cross-sections.
- [Impact of Size and Volume on Cryptocurrency Momentum and Reversal](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4378429) — size/liquidity-conditioned momentum and reversal.
- [Building Cross-Sectional Systematic Strategies by Learning to Rank](https://arxiv.org/abs/2012.07149) — ranking as the direct cross-sectional objective; not crypto-specific proof.
- [Diversification benefits of cryptocurrency factor portfolios](https://link.springer.com/article/10.1007/s11156-024-01260-w) — transaction-cost-aware factor portfolios.
- [Backtesting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2345489) and [Backtest Overfitting in Financial Markets](https://papers.ssrn.com/abstract=2731886) — multiple testing and selection-bias controls.
