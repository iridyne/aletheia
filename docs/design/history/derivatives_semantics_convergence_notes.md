# Derivatives Economic Semantics: convergence narrative

> **History (moved 2026-10-10).** Moved verbatim from
> `docs/design/contracts/derivatives_economic_semantics.md` when it was rewritten into numbered
> requirements. These passages are change narrative and the PR's behavior delta, not current
> behavior; the current rules are the `DE-C*` requirements.

## Preamble

This contract governs current Funding/OI consumers after the owner-approved
convergence in [#3067](https://github.com/iridyne/aletheia/issues/3067), associated
with #3065. The original #2890 golden-equivalence boundary is historical; it must
not preserve an incorrect unit or settlement-interval assumption in current data.

## Schema note

Schema v82 only relaxes the annualized
carrier's NOT NULL constraint; migration preserves historical values and rowids.

## Behavior delta and verification

A current Feature previously receiving guessed amount-times-Mark USD now receives
`None`; explicit USD continues to work. Unknown/1h Funding no longer becomes an
assumed 8h annualized value. Candidate inputs can consequently change, but Utility,
Entry, Risk, Gateway, Execution and Alpha parameters are unchanged.

`tests/test_derivatives_semantics.py` exercises real Feature acquisition, current
reads, context/UI, quantity proof, actual Funding intervals and history-preserving
migration. Canonical API and neighboring collector/Universe/context checks remain
required. Fedora acceptance must verify the merged runtime, nullable storage and
actual consumer output; tests alone do not prove deployment or Alpha.
