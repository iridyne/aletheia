---
id: slim-core-news-compartment
status: accepted
github_issue: 3134
---

# Optional news compartment

Implement the owner-accepted #3134 optional Data boundary for the existing RSS
collector, Model-backed analyzer and news API. Their single implementations
belong to `extensions/data`; delete their former paths without forwarding aliases.
Core retains exact-entity matching and persisted historical news consumption.

The existing Web entrypoint mounts the optional router only when available;
unrelated internal import failures remain errors. Scheduler composition retains
cadence, pause/config gates, bounded workers and failure accounting. Collection
and analysis retain their namespaced state machine, Model Port usage and existing
Learning information-policy epoch effects. No credential authority, lifecycle,
writer, interface, fallback or trading policy is added.

Verify unchanged collector/analyzer bodies, unchanged configured API behavior,
and fresh-process Web/history reads without news routes or external effects.
Preserve the existing regression coverage for RSS failures, timeouts, dedup,
fair retries and manual/shared analysis. These bounded proofs do not establish
full optional-subsystem absence safety, Scheme A completion or runtime economics.
