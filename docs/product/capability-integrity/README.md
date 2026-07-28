# Product Capability Integrity Review

This directory contains the evidence-first review of every product capability
Aletheia exposes or promises.

`CAPABILITY_GROUND_TRUTH.md` is the current Product Truth. The remaining files
in this directory are frozen evidence from the completed review; they are not a
live issue queue, execution tracker, or SHA-gated support detector.

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
| [ISSUE_CANDIDATES.md](ISSUE_CANDIDATES.md) | Frozen mismatch candidates from the review; current problems live in GitHub Issues. |
| [EVIDENCE_LEDGER.md](EVIDENCE_LEDGER.md) | Frozen source coverage and executed evidence from the review. |
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

Do not append progress, rerun logs, issue state, or branch state to these review
files. A later integrity review should update Product Ground Truth from current
facts and preserve any new detailed evidence as a dated research audit.
