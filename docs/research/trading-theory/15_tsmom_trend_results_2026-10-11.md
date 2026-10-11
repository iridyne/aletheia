# Time-series trend (TSMOM) on Binance perpetuals — results (2026-10-11)

> Research evidence, not an approved strategy change. Refs #3934. Registration:
> [14_tsmom_trend_preregistration_2026-10-11.md](14_tsmom_trend_preregistration_2026-10-11.md), committed in
> `06dbff093` before any run. Script: [`scripts/analysis/tsmom_trend_study.py`](../../../scripts/analysis/tsmom_trend_study.py).
> The in-sample run came first. The holdout ran once, after it.

## Verdict: **FAIL**

The rule makes money in the holdout, but **the result is not statistically reliable, and only one of the three lookbacks contributes**. That fails criteria 1 and 4. As registered: no live capital, no trading-mainline engineering. This write-up is the deliverable.

## Data actually used

- 895 USDT-quoted perpetuals from the `data.binance.vision` archive, after the registered exclusions. Delisted symbols are included (SRMUSDT and FTTUSDT verified present). 42,906 monthly files were fetched with 0 failures.
- In-sample: 183 rebalance weeks in 2020–2023. The first weeks of 2020 drop out because no symbol had 180 days of history yet. Holdout: 143 weeks, 2024-01 to 2026-09.

## Primary cell (ensemble of 20/60/120-day signals, weekly rebalance, long/short, 20% vol target, all costs and funding)

| | Weeks | Annualised net | Sharpe | Max drawdown |
|---|---|---|---|---|
| In-sample, primary | 183 | 18.3% | 0.89 | 27.5% |
| In-sample, (a) BTC buy-and-hold | 208 | 34.0% | 0.79 | 77.4% |
| In-sample, (b) long-only, vol-targeted | 183 | 17.1% | 0.84 | 32.8% |
| **Holdout, primary** | **143** | **12.0%** | **0.59** | **24.9%** |
| Holdout, (a) BTC buy-and-hold | 143 | 20.0% | 0.62 | 52.5% |
| Holdout, (b) long-only, vol-targeted | 143 | 3.5% | 0.26 | 28.3% |

Holdout details:
- Bootstrap 95% CI of the mean weekly net return: **[−0.28%, +0.78%]**. The interval crosses zero.
- Intercept against benchmark (b): +0.28% per week, t = 1.00. Beta against (b): −0.06.
- Average gross exposure: 0.52.
- Total costs: 6.3%. Funding was a net receipt of 2.1%. In-sample, by contrast, it was a net cost of 2.5%.

## Frozen criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Holdout Sharpe ≥ 0.5 **and** bootstrap CI lower bound > 0 | **FAIL**: Sharpe 0.59, but the CI lower bound is −0.28% per week |
| 2 | Holdout intercept against (b) > 0 | pass (+0.28% per week, t = 1.00) |
| 3 | In-sample Sharpe > 0 | pass (0.89) |
| 4 | Holdout Sharpe > 0 for every single lookback | **FAIL**: L=20: 0.53; L=60: −0.02; L=120: 0.005 |
| 5 | Holdout max drawdown ≤ 35% | pass (24.9%) |

## Reading

- The in-sample period replicates the published result. Per-year Sharpe: 2020: 2.24, 2021: 1.43, 2022: −0.84, 2023: 1.03. The losing year is the 2022 bear market, the year trend-following is supposed to win. The rule was whipsawed rather than paid for being short.
- The holdout is weaker, and it is carried by the 20-day signal alone. The 60- and 120-day signals were worth nothing in 2024–2026. That is what decay or crowding of the slower trend premium would look like. 143 weeks cannot tell decay apart from noise.
- What did survive: the holdout drawdown (25%) is half of BTC's (52%), and beta to the long-only book is about zero. That is a diversification property, not evidence of edge, and the registration does not count it.
- Exploratory cells (cannot be cited as a pass): top 30 had Sharpe 0.80; daily rebalancing 0.66; cost ×2 0.49; BTC+ETH only 0.01. Treating "top 30" as the strategy now would be parameter mining. Using it would need a new registration evaluated on 2026-10 onward.

## Capital view (for the owner)

Even taking the holdout point estimate at face value, 12% a year on about 60,000 CNY is about 7,000 CNY a year, with a 25% drawdown along the way. This confirms the earlier conclusion: this rule, on this capital, cannot reach the 100万 goal. It is not a reason to deploy money.

## Deviations log

- None. No rule or parameter was changed after any run.
