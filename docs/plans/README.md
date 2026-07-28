
# plans/ - Frozen Legacy Plans

<!-- LEGACY-FROZEN -->

This entire tree is retained for provenance. Its checklists, status labels,
dates, and sequencing are historical and must not be used as current work
state. Do not add plans or update completion markers here.

## Subfolders

| Folder | Use |
|---|---|
| `engineering/` | CI, persistence, bugfix path, integrity remediation |
| `learning/` | Learning loop, self-evolution, thesis lineage, insight feedback |
| `market-data/` | Universe, futures, data sources, scanner/observer, proxy support |
| `operator/` | PI agent, operator supervision, command center |
| `product-ui/` | Frontend and product definition plans |
| `trading-pipeline/` | Strategy, Serenity, trade rationale, opportunity lifecycle, paper controls |
| `strategy-research/` | Research-oriented and exploratory implementation plans |
| `archive/` | Plans that were already explicitly archived |

## Agent Rule

Existing plans never override product truth, governance, current contracts,
code, or GitHub work state. A future major capability, cross-domain change, or
trust-boundary change belongs in `design/proposals/`. Ordinary bugs belong in a
GitHub Issue and PR. Implementation ownership, priority, progress, and
completion remain outside documentation.
