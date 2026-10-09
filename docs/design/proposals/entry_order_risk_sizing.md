---
id: entry-order-risk-sizing
status: draft
github_issue: 3777
related_contracts:
  - ../contracts/entry-plan-contract.md
  - ../contracts/entry_sizing_and_financing_contract.md
  - ../contracts/risk_engine_contract.md
  - ../contracts/execution_contract.md
supersedes: []
---

# Entry, Stop, Target and Position Sizing — Original Design

> Proposal status: **draft**. This proposal records the owner's original intent
> and the design selected from evidence. It does not claim that any of it is
> implemented, deployed, or profitable.

## Summary

This proposal is the single design record for how Aletheia turns an Entry Plan into
an order. It covers where the stop goes, where the target goes, how long a position
may be held, how large the position is, what leverage it uses, and how the stop is
executed.

It does two things:

- It keeps the owner's original requirements verbatim, so that later changes can be
  checked against them.
- It records the design chosen from evidence: competitor and reference code, industry
  practice, Fedora runtime data, and a Monte Carlo of a 500 USDT starting account.

## Owner's original requirements (verbatim intent, 2026-10-09)

1. **Wider stops are acceptable.** The owner agrees stops may be widened rather than
   rejecting a plan whose structural stop is too close.
2. **Starting capital, not position size.** The first live deployment starts with about
   **500 USDT of available capital**. That is the account, not the size of one
   position.
3. **Goal: grow it fast.** The system should balance itself so that the 500 USDT can
   **double quickly**.
4. **Derive the numbers, don't guess them.** Starting from the target, and from
   published data and practice, work out:
   - what **risk per trade** (% of equity) should be;
   - the **leverage** that follows;
   - the resulting **notional position**.

   All three must be consistent with Aletheia's entry point, exit point, take-profit
   and stop-loss habits.
5. **Learn from others.** Study how the main competitors and known reference systems
   place stops: R20/AstraQuant (`_refs/r20-quantum-trader`), OpenAlice
   (`_refs/OpenAlice`), nofx, Vibe-Trading, and the VergeX platform the owner knows
   well. Adopt what is closest to best practice.
6. **The owner accepts the overall direction and wants the best version of it.** The
   overall direction is #3777 / PR #3780: structural support/resistance stops, a floor
   that widens the stop instead of rejecting the plan, and the target placed at the
   nearest opposing structure.
7. **Write it down.** The design must live in `docs/`. It must record both the owner's
   ideas and the final engineering choice, as at least one complete original design.

## Current fact (2026-10-09, master `1cea7366`, Fedora read-only)

- **Stop placement.** The Compiler derives the stop as anchor ∓ ≈0.05 × ATR(15m). It
  then **rejects** any plan whose stop is below a fixed 1.5% floor (Demo only).
- **Floor rejections in the last 7 days.** 187 of 405 planning attempts (46%) died on
  this floor. Their median stop was 0.56%.
- **Sizing.** Position size is loss-budget based:
  `notional = loss_budget / stop_fraction`. The Demo policy uses
  `max_single_trade_risk_pct = 20`. On the Bitget demo account (≈442 USDT equity)
  that is ≈88 USDT of loss budget per trade.
- **Friction.**
  - Taker fee: 0.06% per side (measured over 79 fills).
  - Local-poll stop slippage: median ≈0.42%, n=28. This is a proxy measurement.
  - Exchange-native TP/SL is off (#3619).
- **Edge.** The 9-day replay on #3777 shows **no gross edge at any stop width**:
  every confidence interval contains 0. Wider stops only reduce the share of each
  loss that friction eats.
- **Live.** Live trading is disabled on Fedora. Live caps exist in the risk policy;
  see the resolved design for how they interact with the chosen sizing.

## Research and evidence

_Filled from the 2026-10-09 research workflow. It covers the reference systems,
industry practice, and the 500 USDT Monte Carlo, and was checked by an independent
critic._

## Selected design

_Filled after the owner discussion. It will cover stop placement, floor and ceiling,
target and horizon, risk per trade, leverage, notional, concurrency, daily loss
stop, scale-up rule, and stop execution._

## Trust and complexity review

_To be completed with the selected design._

## Verification contract

_To be completed with the selected design._

## Rollout and rollback

_To be completed with the selected design._

## Resolution

_Pending owner decision._
