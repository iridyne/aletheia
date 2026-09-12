---
id: slim-core-research-bundle-import
status: accepted
github_issue: 3134
related_contracts:
  - ../contracts/persistence_recovery_contract.md
  - ../contracts/execution_contract.md
---

# Research bundle import without trading authority

This bounded D2 convergence implements #3134's single-writer and inert Legacy
boundaries. At `a87ed65935b2a296199b46c6b53f5d2175c67fd4`, the registered
`POST /api/persistence/import` calls `sync.importer.ImportService`. Its dynamic
table writer admits `trade_proposals` and six retired PS runtime tables outside
the canonical Proposal/Order/Fill/Position owners. A valid checksum proves
payload integrity, not authority to create a trading fact.

Restrict that existing writer to its six namespaced research tables: Serenity
themes, evidence, claims and opportunities, plus lessons and skills. Delete the
Proposal and retired runtime table admissions and their unused primary-key
dispatch entries. Do not route imported trading rows through a compatibility
shim or reconstruct executable authority from an archive.

Both preview and merge reject an archive containing an unauthorized table before
any table mutation. If table preflight or transactional insertion fails, the
outer importer returns its errors before copying artifacts. Existing checksum,
safe extraction, primary-key/column checks, conflict handling, transaction and
isolated staging behavior remain; no new importer framework or database schema.

Research artifacts remain under the existing research transport behavior. This
change does not claim a cross-filesystem/database atomic restore, change normal
ArtifactStore ownership or widen permissible archive paths. Managed full backup
and isolated restore remain their separate explicit Ops workflow.

Keep historical Proposal and PS snapshot export/read behavior and all existing
rows unchanged. Export permission is not import permission; these bundles are
diagnostic/history, not a way to recreate current trading state. The earlier
PS Gateway retirement deferred archive import; this batch closes that separate
writer seam rather than reviving the retired runtime.

Verification covers former admissions in preview/merge, mixed valid research
plus forbidden table payloads, rejection without artifact writes, and existing
research import/export/backup safety regressions. No operational archive import,
historical data rewrite, credential resolution, exchange call or deployment is
authorized by this source change.
