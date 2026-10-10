# Forced-flow event study — results (2026-10-10)

> Research evidence, not an approved strategy change. Refs #3934. Rules and pass criteria were frozen in
> [12_forced_flow_event_study_preregistration_2026-10-10.md](12_forced_flow_event_study_preregistration_2026-10-10.md)
> (commit `fbff17aaf`) before any number below existed. Reproduce with
> [`scripts/analysis/forced_flow_event_study.py`](../../../scripts/analysis/forced_flow_event_study.py)
> (`download`, then `run --period insample|holdout`; about 30 s per period after a 308 MB download).

## Verdict: FAIL

The pre-registered thesis — a limit buy resting 1σ√15 below an extreme one-minute taker-selling bar in a
thin altcoin perpetual, held 60 minutes, earns money net of 14 bp — **fails criterion 4**: the five
in-sample months are negative (−6.3 bp, CI −13.0..+0.0) and each month on its own is negative. The holdout
month passes criteria 1–3, but one positive month after five negative ones is not replication. Per the
registration, this thesis is not built into the trading mainline.

| Criterion (primary cell: long, k=1, H=60) | Result | Pass |
|---|---|---|
| 1. Holdout net mean, 95% CI lower bound > 0 | +22.7 bp, CI +9.4..+38.4 | yes |
| 2. Holdout event − control > 0 | +19.5 bp, CI +4.0..+36.3 | yes |
| 3. Holdout ≥ 100 fills | 1,787 fills over 30 days | yes |
| 4. In-sample net mean > 0 | −6.3 bp, CI −13.0..+0.0; event − control −6.3 bp | **no** |

## Why the holdout month looks good and why that is not evidence

| Month | fills | primary net mean bp | median bp | avg 60m forward return of any minute, bp |
|---|---|---|---|---|
| 2026-04 | 1,316 | −5.2 | −6.4 | +3.5 |
| 2026-05 | 1,642 | −9.5 | −2.9 | +0.2 |
| 2026-06 | 1,540 | −1.8 | −2.7 | −0.4 |
| 2026-07 | 1,552 | −3.4 | −6.7 | +0.6 |
| 2026-08 | 1,605 | −10.9 | −2.9 | +2.3 |
| 2026-09 (holdout) | 1,787 | +22.7 | +8.9 | **+5.6** |

- September was the strongest up-drift month in the sample for these coins; buying dips pays in such a
  month whatever the trigger.
- Five days carry most of the holdout mean: dropping the best 1/3/5 days leaves +17.7/+12.2/+7.8 bp; 18 of
  30 days are positive. The event − control difference is also exploratory-sized against the in-sample −6.3.
- In-sample the control (same order at random minutes) earns −0.0 bp and the event earns −6.3 bp: in
  normal months the extreme-selling minute is a *worse* time to rest a bid than a random minute, which is
  the adverse-selection pattern (Linnainmaa 2010): the orders that fill are the ones price runs through.

## Exploratory observations (not passes; 17 cells + 5 splits, a nominal hit is expected by chance)

- **Short side (resting a sell above an extreme taker-buying bar) is negative everywhere**, in both periods,
  and below its control in 15 of 18 cells: chasing-buyer flow continues rather than reverting.
- **Open interest falling ≥0.5% after the event** — the closest proxy for liquidations — is the *worst*
  in-sample split (−23.4 bp, CI −36.8..−10.1), not the best. Fills into actual deleveraging get run over.
- No liquidity tier is positive in-sample (thin −10.6, mid −0.5, thick −7.9 bp).
- Longer hold (240 min) or a deeper limit (k=2) look less negative in-sample with CIs spanning zero; these
  are exactly the forking paths the registration forbids citing. Any one of them is a new hypothesis that
  needs its own registration and October 2026+ data.

## What this does and does not establish

- Established: on public Binance data for 30 thin perps, the simple mechanical "rest a bid under extreme
  taker selling" rule has no net edge across five months, and its liquidation-like subset is negative.
- Not established: that no forced-flow edge exists. Untested variants include real liquidation prints
  (not published by Binance), cross-coin cascade days only, stops, and other venues. Each is a new
  registration, not a re-run of this one.
- Known optimistic biases remain (survivorship of listed coins, trade-through fill rule without queue
  position), so the true result is at most as good as shown.

## Deviations

One code fix after the first in-sample run, logged in the registration: two OI snapshots of 0 caused a
division by zero in a secondary split. No rule changed; the primary cell is unaffected.
