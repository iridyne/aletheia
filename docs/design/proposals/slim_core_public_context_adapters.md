---
id: slim-core-public-context-adapters
status: implemented
github_issue: 3134
---

# Public-context Data adapters

Implement the owner-accepted [#3134 optional Data compartment
boundary](https://github.com/iridyne/aletheia/issues/3134) for the existing
CoinGecko Top, DefiLlama and Hyperliquid collectors. Their single implementation
lives in `extensions/data/collectors`; delete the old paths without forwarding
aliases. Preserve protocol parsing, DTOs, source-time rules and logger names.

The existing scheduler remains the composition and cadence owner. It persists
returned observations through the same Core repositories and records health.
Hyperliquid retains its existing optional Learning policy-epoch provenance call;
this move neither reassigns nor expands that effect. No new Port, registry,
credential access, source, writer or lifecycle is introduced.

Verify unchanged module bodies, retarget existing behavior/spec tests, and prove
that an absent adapter fails its context lane without stopping the independent
primary lane. This is bounded isolation, not proof that every optional Data
consumer is absent-safe or that full Scheme A/runtime qualification is complete.
