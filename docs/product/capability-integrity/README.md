# Product Capability Integrity Review

This directory contains the evidence-first review of every product capability
Aletheia exposes or promises.

The review currently covers:

- branch: `codex/capability-integrity-review`
- the current reviewed worktree, including the repairs documented here;
- a reproducibility ledger that records the original base commit and executed
  evidence without making commit identity a capability-status gate.

## Authority order

When evidence conflicts, use:

1. current code and durable data flow;
2. focused executable tests or local reproductions;
3. current natural runtime facts;
4. current executable contracts;
5. active product and operations documentation;
6. historical design and research documents.

Code existence, a unit test, a page, or a producer table is not enough to prove
that a capability reaches its final result.

Natural Runtime evidence is optional operational confidence. It is not required
to classify a complete, executable product contract as `Supported`.

## Files

| File | Purpose |
|---|---|
| [CAPABILITY_INVENTORY.md](CAPABILITY_INVENTORY.md) | Deduplicated product capability list. It intentionally contains no implementation verdicts. |
| [AUDIT_METHOD.md](AUDIT_METHOD.md) | Evidence levels, status vocabulary, card schema, review rules, and completion gate. |
| [FACT_CARDS.md](FACT_CARDS.md) | Compact end-to-end fact card for every inventory capability. |
| [../CAPABILITY_GROUND_TRUTH.md](../CAPABILITY_GROUND_TRUTH.md) | Canonical owner-facing status table. |
| [ISSUE_CANDIDATES.md](ISSUE_CANDIDATES.md) | Reviewed product/implementation mismatches ready to turn into scoped issues. |
| [EVIDENCE_LEDGER.md](EVIDENCE_LEDGER.md) | Source coverage, executable runs, runtime evidence, and review limitations. |
| [EXPERIMENTAL_REVIEW.md](EXPERIMENTAL_REVIEW.md) | Read-only full-chain review of every capability previously classified Experimental. |
| [IMPLEMENTED_REVIEW.md](IMPLEMENTED_REVIEW.md) | Full-chain review of the four external-exchange capabilities classified Implemented. |
| [SDD_CAPABILITY_ALIGNMENT.md](SDD_CAPABILITY_ALIGNMENT.md) | Boundary and mismatch review between executable SDD claims and product capability truth. |

## Non-goals

- No production or shared-runtime mutation; E3 uses an isolated temporary data
  directory.
- No live unlock or live order.
- No code repair while gathering evidence.
- No issue creation before a fact card passes review.
- No automatic promotion from `Implemented` to `Supported`.
