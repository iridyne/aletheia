---
id: cn-equity-market-kernel
status: on_hold
github_issue: 2573
related_contracts:
  - docs/design/contracts/entry-plan-contract.md
  - docs/design/contracts/risk_engine_contract.md
  - docs/design/contracts/execution_contract.md
  - docs/design/contracts/market_data_coverage_contract.md
  - docs/design/contracts/persistence_recovery_contract.md
  - docs/design/contracts/opportunity_outcome_contract.md
supersedes: []
---

> **On hold (owner, 2026-10-10).** No implementation work is admitted while the product is
> futures-only; the authorisation in Active Runtime Scope is suspended until the hold is lifted.

# CN Equity Market Kernel

## Accepted boundary after simulator retirement

CN Equity remains a bounded, not-yet-qualified market-evidence extension under
Issues #2572 and #2573. The owner-selected
[retirement of the local simulated exchange](retire_local_simulated_exchange.md)
also retires this proposal's internal execution route. The
[original proposal](../history/cn_equity_internal_paper.md) preserves the former
account, matching, settlement and promotion design solely as history.

The remaining scope is CNY-denominated Shanghai and Shenzhen A-share identity,
calendar, market rules, tradability, fees, settlement facts and point-in-time
research evidence. These facts must retain their source, effective time and
content identity. Unknown classifications, stale or incomplete evidence stay
unavailable; they cannot become execution permission. B-shares, Beijing Stock
Exchange and non-CNY instruments remain outside this boundary.

CN starts `disabled`. The permitted progression is `observe`, then non-executing
`shadow`, subject to the existing owner admission and evidence gates. Observation
may publish market evidence; Shadow may evaluate hypothetical decisions. Neither
may reserve account resources, issue executable Risk approval, submit orders,
materialize fills or mutate account balances and positions. Research outcomes
remain separate from verified external-account outcomes.

Market evidence and hypothetical decisions reuse the existing evidence and
System Operator boundaries. There is no second Risk Engine, Gateway, account
authority, strategy runner or scheduler. External research tools and Sidecars
remain replaceable and gain no credentials or execution authority.

## Execution boundary

No CN execution route is currently authorized. The retired route is not replaced
by a renamed simulator or by routing A-shares through a crypto exchange. Any
future broker or exchange integration requires a separately accepted design and
exact account, instrument, settlement and execution evidence. This proposal does
not authorize broker connectivity or Live trading.

## Evidence and promotion

[Gate CN](../../product/gates/PHASE_GATES.md#gate-cn---bounded-cn-equity-implementation-and-promotion)
owns admission to observation and Shadow. Repository implementation does not
claim a qualified Runtime, strategy profitability or execution support.
Historical records, identities, economics and research evidence remain immutable
and readable; the retired qualification labels grant no current authority.
