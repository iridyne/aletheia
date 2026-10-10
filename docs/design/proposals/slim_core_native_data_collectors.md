---
id: slim-core-native-data-collectors
status: implemented
github_issue: 3134
---

# Native Data collector compartment

Implement the owner-accepted [#3134 Data adapter
boundary](https://github.com/iridyne/aletheia/issues/3134) by moving the existing
Microstructure, Derivatives and VolumeScanner modules into
`extensions/data/collectors`, without a forwarding facade or body rewrite.

The Data provider reuses the same orderbook parser at its new path. Scheduler
composition remains the cadence/health owner and persists live snapshots through
the same Core repository. Derivatives sparse history continues to call existing
Core repository APIs; it is not pure I/O and gains no new mutation authority.
VolumeScanner retains the shared numeric volume conversion and existing ranking.

Preserve protocol selection, unit/time/market identity checks, budgets, cleanup,
backfill admission/deduplication and logger names. No new abstraction, credentials,
source, lifecycle, business policy or runtime action is introduced.

Verify byte-identical moved modules and retarget the existing parser, collection,
history, exact-instrument, Planner/provider, persistence and scheduler tests.
Physical guards must cover both namespaces; this is not full Scheme A or proof
that every optional Data consumer is absent-safe.
