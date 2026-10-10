---
id: slim-core-partial-reduce-retirement
status: implemented
github_issue: 3134
related_contracts:
  - ../contracts/execution_contract.md
---

# Retire the unused partial-reduce accounting writer

This bounded deletion implements the owner-approved #3134 single-writer and
Legacy containment decision. At `c2e9b0f684561aa7ed6e2d5ec916a508c99e3719`,
`portfolio.pnl.reduce_position` has no production caller: only four tests call
it. It directly reduces Position quantity and writes a partial close event from
a supplied price and estimated fees, without private Fill evidence. Its own
documentation calls it an isolated reference; the product router rejects strict
partial reductions and delegates full exits through the canonical close path.

Delete that unused writer and its producer-only tests, with no alias or new
implementation. Preserve historical partial events and daily-loss readers.
Preserve the distinct, active `exchange_materialization` partial-close path:
verified private Fills can reduce exposure during an authorized full-close
attempt; quantity, fee allocation, replay and incomplete funding remain factual.
This does not authorize discretionary partial-reduce commands, change Risk or
fee/funding policy, alter schemas/history, or touch a runtime database.

Verification retains router rejection/full-exit delegation, historical ledger
aggregation, and current private partial-Fill/clipped-close/replay tests. A small
retirement assertion prevents restoring the unused accounting entrypoint.
