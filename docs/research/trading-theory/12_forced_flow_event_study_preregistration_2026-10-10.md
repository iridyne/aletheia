# Forced-flow event study — pre-registration (2026-10-10)

> Research evidence, not an approved strategy change. Refs #3934. Written and committed **before**
> any result was computed. Script: [`scripts/analysis/forced_flow_event_study.py`](../../../scripts/analysis/forced_flow_event_study.py).
> Background and literature: [11_academic_edge_evidence_small_llm_perp_trader_2026-10-10.md](11_academic_edge_evidence_small_llm_perp_trader_2026-10-10.md) §3 and ranked item 1.

## Why this test, and why first

The owner accepted on 2026-10-10 that there is no evidence an LLM's directional call earns money, and that
the one candidate edge with a named counterparty is **being paid to absorb forced or panic flow in thin
altcoin perpetuals**: someone who must sell now (liquidated or panicking) sells below fair value, and a
patient resting bid is paid for taking the other side. The owner's rule for this round: **prove first,
build after.** No trading-mainline engineering is added for this thesis until this test passes.

The test answers one question with public data and no system code: *after an extreme one-minute
taker-selling shock in a thin altcoin perpetual, does a limit buy resting below the price make money net
of costs, and more than the same order placed at an ordinary minute?*

## Data (public, no credentials)

- `data.binance.vision` USDT-M monthly **1m klines** (`quote_volume`, `taker_buy_quote_volume`) and daily
  **metrics** (open interest snapshots, nominally 5-minute, observed irregular 5–25 min).
- Liquidation snapshots are not published (`liquidationSnapshot` returns HTTP 404 for every probed date),
  so forced flow is **proxied** by taker imbalance and split afterwards by the open-interest change.
- Window 2026-04-01 .. 2026-09-30. **In-sample** = April–August. **Holdout** = September, run once,
  only after this document and the script are committed.

## Universe (frozen)

USDT `PERPETUAL`, `underlyingType = COIN`, onboarded before 2026-03-02, 24h quote volume 1M–50M USDT on
2026-10-10 (362 symbols). 30 drawn at evenly spaced volume ranks, lowest first:

MAV KOMA XVG 1000XEC XAN ACH BLESS TREE AGLD SAFE CARV CLO ARKM SCR DIA AZTEC CVC BIO SYN JST SKY BULLA
MANTA LSK HUMA AXS ALGO ICP PYTH LTC (all `…USDT`). Tiers: ranks 1–10 thin, 11–20 mid, 21–30 thick.

Known bias: selection uses today's volume and only listed symbols, so delisted coins are absent
(survivorship). It makes the result optimistic, not pessimistic.

## Rules (frozen)

| Item | Rule |
|---|---|
| Flow | net taker sell `= quote_volume − 2 × taker_buy_quote_volume` (USDT), per 1m bar |
| Event (long) | net sell of bar t above its trailing 7-day 99.5th percentile (bars before t only, ≥3 days history), net sell > 0, red bar |
| Event (short, mirror) | net taker buy above its 99.5th percentile, green bar |
| Declustering | at most one event per coin per 60 minutes |
| Order | after bar t closes: limit at `close_t × exp(∓k × σ × √15)`, σ = trailing 24h std of 1m log returns; `k ∈ {0, 1, 2}` |
| Fill | in bars t+1..t+30, only if the low (high for short) trades **strictly through** the limit; fill price = limit (maker); touching is not a fill |
| Exit | close of bar fill+H as taker, `H ∈ {15, 60, 240}` min; no stop (pure event study) |
| Cost | 14 bp per round trip (maker 2 + taker 5 + 7 bp for exit spread/slippage on thin books and funding) |
| Control | same order rule at random minutes of the same coin and period, ≥60 min from any event, 3× the event count |
| CI | day-cluster bootstrap of the mean (resample UTC fill days, 2000 draws, seed 20261010) |

## Pass criteria (frozen)

**Primary cell:** long side, `k = 1`, `H = 60`. The thesis **passes** only if all hold:

1. **Holdout** mean net return per filled trade has a 95% CI lower bound **> 0**;
2. **Holdout** event-minus-control mean net is **> 0** (point estimate; CI reported);
3. **Holdout** has ≥ 100 filled trades;
4. **In-sample** mean net return of the same cell is **> 0** (replication on independent months).

Anything else fails, including "positive but CI crosses zero". Failure means: do not build this thesis into
the trading mainline; record the result and stop.

All other cells (short side, `k = 0, 2`, `H = 15, 240`) and splits (liquidity tier; open interest fell
≥0.5% vs flat/up; per-coin counts; cross-coin clustering) are **exploratory**: 17 extra cells plus 5 splits
are reported, so a single nominally significant one among them is expected by chance and cannot be cited
as a pass. A promising exploratory cell becomes a new hypothesis that needs its own registration and an
unseen period (October 2026 onward).

## What this does not prove

- A pass is evidence for a mechanism on historical public data, not a deployable strategy: no stop loss,
  no position sizing, queue position and our own market impact are approximated by the trade-through rule.
- Binance is not our venue; a pass must be re-checked on the venue we trade before any engineering.
- The proxy cannot separate forced liquidations from voluntary panic selling; the OI split is the only
  hint.

## Deviations log

Any change to the rules after the first in-sample run is recorded here with its reason. Code-bug fixes are
allowed (they make the script do what this document says) and are listed; rule changes are not.

- (none yet)
