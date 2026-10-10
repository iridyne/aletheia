---
id: slim-core-planner-data-boundary
status: implemented
github_issue: 3134
related_contracts:
  - ../contracts/entry-plan-contract.md
---

# Planner Data boundary

This implements the owner-accepted [#3134 Core/Data dependency
direction](https://github.com/iridyne/aletheia/issues/3134), not a new provider
framework or trading policy.

Entry Planner reads qualified stored facts without importing an optional
collector. Acquisition uses the existing Core provider factory and normalized
orderbook observation capability instead of constructing MicrostructureCollector.
Concrete protocol handling remains in the Data adapter. Core retains freshness,
exact identity admission, canonical persistence and generation retry ownership.

Preserve the two-second freshness threshold, five-second total acquisition and
close timeout, no venue fallback, phase diagnostics, and no model attempt or
economic NO_PLAN for unavailable inputs. Do not refresh already-bound inputs.

Verification covers stored-book reads with collector imports blocked, exact
venue/market routing, unavailable/malformed facts, provider cleanup including
cancellation, and unchanged persistence and retry behavior. This does not
authorize runtime changes, credentials, orders or economic-policy changes.
