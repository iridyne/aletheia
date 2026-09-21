---
id: slim-core-macro-projection
status: accepted
github_issue: 3134
related_contracts:
  - ../data/data_utilization.md
---

# Slim Core macro projection ownership

This bounded ownership change implements the owner-accepted
[#3134 Core/optional Data boundary](https://github.com/iridyne/aletheia/issues/3134).
Core stored-fact reads must not require an optional acquisition implementation.

The existing macro freshness projection owns the pure risk-label classification
it consumes. Move the unchanged classifier out of the optional MacroCollector
module into that existing projection module and delete the reverse import.
The collector continues to use the same projection before publishing its
snapshot. No second classifier, forwarding alias, Port or module is introduced.

Preserve VIX/capitalization thresholds, source-time filtering, unavailable
results, stored snapshot fields and historical rows. The separate optional
market-regime detector is not replaced by this macro label.

Verification must read persisted macro facts and build current context while
collector imports are rejected, retain stale-field filtering and prove that
historical rows are unchanged. Existing collection and classification tests
remain applicable. This does not authorize new data sources, credentials,
Risk/economic policy, runtime changes or trading.
