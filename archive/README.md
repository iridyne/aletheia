# Aletheia archive

Aletheia was a crypto perpetual-futures trading research framework. Development
stopped on 2026-10-29: the framework could not be shown to produce alpha, so
there was no reason to keep building it. This repository is archived read-only.

This directory preserves the material that existed only on the development
machine and in no git history. Everything else in the repository was already
committed.

## `salvage/`

Uncommitted working-tree content recovered from abandoned worktrees. Each
subdirectory is named after the git worktree it came from; the relative paths
inside are the paths the files had in that worktree.

| Subdirectory | Files | Origin | What it is |
|---|---|---|---|
| `hapi-docs-spec-cleanup/` | 70 | branch `hapi-docs-spec-cleanup` | A documentation restructure that never landed: retired passages moved to `docs/design/history/`, the SDD spec set under `docs/design/specs/`, the capability-integrity and doc-golden-standard audits under `docs/research/audits/`, and two new contracts (`exit_and_close_contract.md`, `outcome_integrity_contract.md`). |
| `hapi-alpha-possibility-research/` | 8 | branch `hapi-alpha-possibility-research` | The research output itself: five trading-theory studies (academic edge evidence, forced-flow event study pre-registration and results, TSMOM trend pre-registration and results), one alpha-source survey, and the two study scripts that produced them. |
| `hapi-learning-evo/` | 4 | branch `hapi-learning-evo` | SDD specs 0001 and 0002 plus the learning-spec README. |
| `ws-exchange-one-key/` | 2 | worktree `ws-exchange-one-key` | Two test files that exist in no commit: `test_position_reconciler.py` and `test_security.py`. |
| `ws-learning-spec/` | 1 | worktree `ws-learning-spec` | SDD spec 0002, causal evidence. |

The five `aletheia-*` project skills that were untracked in the repository
working tree were committed in place under `.claude/skills/` rather than copied
here.

## `evidence/`

Investigation artifacts from three debugging sessions, moved here from
`~/Projects/_archive`.

| Subdirectory | Files | What it is |
|---|---|---|
| `salvage-20260921/` | 15 | The issue-3134 salvage audit: `AUDIT.md`, the branch divergence and tree dumps (`divergence.txt`, `all-commit-diffs.txt`, `branch-*.txt`, `tree-*.txt`), the GitHub issue and PR payloads, and a probe script. |
| `3134-final-evidence/` | 18 | Runtime and deployment checkpoints for issue 3134: the maintenance-hold and stage-db scripts, probe scripts, the gateway unit file, and the PR and operator proof notes. |
| `memleak-20261010/` | 9 | A memory-leak investigation: `REPORT.md`, tracemalloc snapshots, a timeline, and the snapshot and trim scripts that produced them. |

## Deliberately not kept

- **18 worktree checkouts** (9.0 GB): fifteen under `.claude/worktrees/` and three
  orphaned ones at `aletheia-worktrees/`. Their tracked modifications were
  checked file by file: every one was a file state whose blob already exists in
  this repository's history, so they carried no unique content.
- **`legacy-branch.bundle`** (38 MB): its only ref,
  `refs/heads/refactor/3134-product-truth` at `ee62856e9`, resolves directly in
  this repository.
- **`legacy-generated-core-20260922/`** (19 MB): 646 compiled `.pyc` files and
  `dashboard_static/` build output, both regenerable.
- **Seven files that turned out to be already committed**: `decision_ledger.py`,
  `test_decision_ledger.py`, `spec_stock_tokens.py` and
  `test_market_data_composition.py` (present on `origin/master` at these same
  paths), `AUDIT_METHOD.md` (present on `origin/master` under
  `docs/product/capability-integrity/`), and two empty patch files.

## Duplicates consolidated

Twenty-six files appeared identically in more than one worktree. One copy was
kept; the equivalents removed were: SDD specs 0001 and the `_template` set (also
in `hapi-learning-evo` and `ws-learning-spec`), both learning research documents
(three copies each), `AI_TRADING_PRODUCTS_ALPHA_SOURCES_2026-10-10.md` and
`11_academic_edge_evidence_small_llm_perp_trader_2026-10-10.md` (two copies
each), `decision_ledger.py` and `test_decision_ledger.py` (five copies each),
and `spec_stock_tokens.py` and `test_market_data_composition.py` (two copies
each).
