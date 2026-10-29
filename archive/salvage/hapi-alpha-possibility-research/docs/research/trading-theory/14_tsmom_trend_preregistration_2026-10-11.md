# Time-series trend (TSMOM) on Binance perpetuals — pre-registration (2026-10-11)

> Research evidence, not an approved strategy change. Refs #3934. Written and committed **before**
> any result was computed. Script: [`scripts/analysis/tsmom_trend_study.py`](../../../scripts/analysis/tsmom_trend_study.py).
> Previous test in this series: [13_forced_flow_event_study_results_2026-10-10.md](13_forced_flow_event_study_results_2026-10-10.md) (failed).

## Why this test

The forced-flow thesis failed its registration. The owner then asked what a real exchange quant desk
researches (KCEX job post, V2EX t/1242609): CTA / trend, factors, stat-arb, with explicit controls for
look-ahead, survivorship, overfitting, data snooping and parameter mining, and a live track record as
the hiring bar. Of those families, only **low-turnover time-series trend** survives retail costs
(Binance USDⓈ-M VIP0: maker 2 bp, taker 5 bp; weekly rebalancing costs about 5 %/yr at 1× taker,
daily about 36 %/yr) and has published support in crypto (Liu & Tsyvinski, *RFS* 2021, time-series
momentum; Liu, Tsyvinski & Wu, *JF* 2022, market/size/momentum factors).

The question: *does a pre-specified, untuned TSMOM rule on liquid Binance USDT-M perpetuals earn a
positive net return out of sample, after fees, slippage and actual funding, beyond simply holding
the market?*

The goal is a credible public research record and, only if it passes, a small verifiable live record.
It is **not** a path to the 100万 target by itself (capital-bound).

## Data (public, no credentials)

- `data.binance.vision` USDT-M monthly **1d klines** (open, close, quote volume) and monthly
  **fundingRate** files, for **every** symbol in the archive, including delisted ones (SRM, FTT verified
  present), listed via the S3 bucket index with pagination.
- Window 2020-01-01 .. 2026-09-30, UTC.
- **In-sample (replication)** = 2020-01-01 .. 2023-12-31. **Holdout** = 2024-01-01 .. 2026-09-30, run
  once, only after this document and the script are committed. No parameter is fitted on either period:
  every parameter below is fixed from the literature convention before any data is read.

## Universe (point-in-time, frozen rule)

- At each rebalance, eligible = USDT-quoted perpetuals with ≥ 180 days of daily history before the
  rebalance date and a valid close on it.
- Select the **top 20 by trailing 30-day quote volume** known at the rebalance date. Symbols that
  later delist stay in the sample while they traded; a delisted position is closed at its last close
  (taker cost applied).
- Exclude stablecoin-vs-stablecoin pairs (USDC, FDUSD, TUSD, BUSD, USDP, DAI bases) and index /
  `DEFI`-style composite contracts.

## Rules (frozen)

| Item | Rule |
|---|---|
| Rebalance | weekly, Monday 00:00 UTC; signal uses daily closes up to and including Sunday |
| Execution | at Monday's daily **open** (next bar after the signal); no same-bar fills |
| Signal | `s_L = sign(close_t / close_{t−L} − 1)` for `L ∈ {20, 60, 120}` days; primary signal = mean of the three (∈ {−1, −⅓, ⅓, 1}) |
| Risk scaling | `w_i = s_i × (σ* / σ_i) / N`, `σ_i` = trailing 60-day std of daily log returns × √365, `σ*` = 40 %/yr per asset; portfolio scaled so ex-ante vol (trailing 60-day covariance) = **20 %/yr** |
| Caps | per-asset gross weight ≤ 0.25; portfolio gross ≤ 2.0 |
| Costs | per unit of traded notional: 5 bp taker + 5 bp slippage = **10 bp** per side |
| Funding | actual 8h (or venue-native) funding payments applied to held notional: long pays positive funding |
| Benchmarks | (a) BTCUSDT perp buy-and-hold, (b) same universe, same vol targeting, **long-only** (`s = +1`) — isolates trend timing from market beta |
| Statistics | weekly net returns; annualised Sharpe = mean/std × √52; moving-block bootstrap of the mean (4-week blocks, 2000 draws, seed 20261011) |

## Pass criteria (frozen)

**Primary cell:** ensemble signal, weekly, long/short, 20 %/yr target, all costs and funding. The
thesis **passes** only if all hold:

1. **Holdout** annualised net Sharpe ≥ **0.5**, and the bootstrap 95 % CI lower bound of the mean
   weekly net return **> 0**;
2. **Holdout** intercept of weekly primary returns regressed on benchmark (b) is **> 0** (point
   estimate; t-stat reported);
3. **In-sample** annualised net Sharpe **> 0** (replication on independent years);
4. **Holdout** net Sharpe **> 0 for each single lookback** `L = 20, 60, 120` (not carried by one
   parameter);
5. **Holdout** maximum drawdown of the primary cell ≤ **35 %** (otherwise not survivable live for
   the owner).

Anything else fails, including "positive but CI crosses zero". Failure means: no live capital, no
trading-mainline engineering; record the result, publish the write-up, stop.

**Exploratory (cannot be cited as a pass):** long-only vs long/short; daily rebalancing; cost × 2
stress; universe top 10 / top 30; single-lookback cells; per-year Sharpe; BTC+ETH-only. A promising
exploratory cell is a new hypothesis that needs its own registration and an unseen period
(2026-10 onward).

## If it passes: live phase (also frozen now)

- Capital 3000–5000 CNY equivalent, same rules, one venue, read-only API key exported weekly so the
  record is verifiable; 6–12 months.
- Hard stop: live drawdown > 30 % of starting capital, or two consecutive months of rule
  violations (manual overrides) → stop and record.
- Purpose is a verifiable track record for quant-dev / research job applications, not income.

## What this does not prove

- A pass is evidence on historical public data, not a guarantee; trend premia decay and are crowded.
- Daily-bar execution at the open ignores intraday gaps and queue position; the 5 bp slippage is an
  assumption, checked by the cost × 2 stress.
- Binance is the data venue; a live venue with different funding or liquidity needs a re-check.

## Deviations log

Any change to the rules after the first in-sample run is recorded here with its reason. Code-bug fixes
are allowed (they make the script do what this document says) and are listed; rule changes are not.

- (none yet)
