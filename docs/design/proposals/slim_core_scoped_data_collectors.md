---
id: slim-core-scoped-data-collectors
status: accepted
github_issue: 3134
---

# Scoped credential Data collectors

Implement the owner-accepted #3134 Data compartment and explicitly approved
credential exception: concrete Macro/FRED and Coinalyze adapters may resolve only
their own existing credential reference. Move their unchanged implementations to
`extensions/data/collectors`; remove the old files without forwarding aliases.

Scheduler composition retains cadence, health and canonical snapshot persistence.
Doctor's optional Coinalyze probe receives only a configuration-presence boolean
and reads persisted facts; it never receives a raw credential. Preserve the
existing unavailable diagnostic when the adapter cannot be imported.

Preserve FRED research attempt throttling/source clocks, missing-key results,
configured yFinance fallback, Coinalyze coverage/cursors and existing Learning
policy-epoch provenance. This is physical ownership convergence, not a new
credential API, permission, source, writer, fallback or trading policy.

Verify byte-identical adapter bodies, scoped-credential and secret-error tests,
source-time/cadence/persistence tests, stored macro reads without acquisition,
Doctor unavailable behavior and context/primary lane isolation. Offline evidence
does not qualify either external feed, the full optional subsystem or Demo runtime.
