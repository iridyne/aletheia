# Current Derivatives Economic Semantics

This contract governs current Funding/OI consumers after the owner-approved
convergence in [#3067](https://github.com/iridyne/aletheia/issues/3067), associated
with #3065. The original #2890 golden-equivalence boundary is historical; it must
not preserve an incorrect unit or settlement-interval assumption in current data.

## One economic owner

`core/aletheia/data/derivatives_facts.py` owns the unit conversion, Funding annualization,
source-proved projection and compatible OI baseline rules. Existing collectors,
Universe Feature acquisition and current repository/context/API reads reuse them.
No additional provider, comparison-source admission or execution authority follows.

- USD OI requires explicit provider USD proof, or proved base/contracts quantity
  with the required instrument/contract size and compatible Mark semantics.
  A unified field name alone or amount plus Mark does not prove USD.
- Unknown quantity units and nonfinite results are unavailable. A quantity cannot
  be relabeled as base coins solely because CCXT returns an amount.
- Observed Funding remains the source's per-settlement rate. Annualization is
  `rate * 365 * 86400 / interval_seconds` only for a proved positive interval.
  Unknown interval produces `None`, including predictions and history without proof.
- OI change requires the canonical same-provider/scope/venue/instrument/unit/clock
  baseline within 45–75 minutes. Stored legacy delta or a magnitude heuristic
  cannot substitute for that proof.
- Current readers do not expose unproved historical USD OI or annualization as
  valid economics. Raw history remains readable without rewriting values,
  identifiers, timestamps or provenance. Schema v82 only relaxes the annualized
  carrier's NOT NULL constraint; migration preserves historical values and rowids.
- Corrected OI values/deltas must get a matching new Feature snapshot rather than
  reuse a previous snapshot identity under the numeric suppression interval.
- Comparison observations remain observation-only and excluded from the existing
  primary/native consumer selectors. This change does not promote Positioning.

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
