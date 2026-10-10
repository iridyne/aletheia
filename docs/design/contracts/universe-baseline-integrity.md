# Universe Baseline Integrity Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `UB` · **Owner code:** `core/aletheia/universe/baseline_integrity.py`

## Scope

This contract owns a read-only, lossless measurement view of the retired six-family
step scorer (`run_all_scanners`, behavior `UNIVERSE_SCORER_BEHAVIOR_VERSION`) as it is
used by the Universe Discovery Benchmark **research population**. It is a research
baseline only: it is not the Candidate selection authority. Current Candidate admission
belongs to the [Candidate Selection Utility contract](candidate_selection_utility_contract.md);
the six-family scorer survives only at the post-publication research seam
`core/aletheia/universe/service.py::_capture_universe_discovery_nonfatal`. This contract
does not own the benchmark window lifecycle, its outcomes, or any trading behavior.

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/universe/baseline_integrity.py::audit_universe_baseline` | call | `core/aletheia/universe/discovery_benchmark.py` (per captured instrument), reached from `service.py::_capture_universe_discovery_nonfatal` after a successful Universe publication |
| `core/aletheia/universe/baseline_integrity.py::family_semantics` | call | `core/aletheia/universe/discovery_benchmark.py` (frozen benchmark contract material) |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| none | — | — | The adapter is pure; its output is embedded in benchmark records owned by `discovery_benchmark.py` under `baseline_integrity: universe-baseline-integrity/v1`. |

## Requirements

### UB-C01 Research population only

`The baseline adapter shall be read only by the Universe Discovery Benchmark research capture, and no Candidate selection, publication, Risk, Entry Plan or execution path shall read its output.`

- WHEN a Universe refresh publishes Candidates THEN selection is decided by Candidate Selection Utility before the research capture runs, and a research-capture failure cannot revoke the publication.

### UB-C02 Exact reproduction of the scorer

`When the adapter reconstructs every family for one feature input, the adapter shall raise UniverseBaselineIntegrityError unless the reconstructed score vector equals run_all_scanners all_scores exactly.`

- WHEN a family score differs, the family set differs, or the primary family is unknown THEN the audit fails closed with a typed `authority_*` reason.

### UB-C03 Non-winning families stay observable

`The adapter shall return, for every registered family, its score, direction, reasons, applicability, semantic role and behavior version, including families that did not win the winner-take-all primary.`

- WHEN `momentum` wins THEN `mean_reversion` still carries its own direction and reasons.

### UB-C04 Truthful family semantics

`The adapter shall label the event family Market Shock / Event Proxy with true_external_event_evidence=false and shall declare the derivatives capability gaps basis, liquidation and cross_venue_positioning rather than filling them with zeros.`

- WHEN the event family scores THEN its record cannot claim news, announcement or social-attention provenance.

### UB-C05 Not-applicable families

`If a family does not apply to the instrument (derivatives on a non-derivative market, short_bias on a stock_linked_perp context), then the adapter shall report it as not_applicable with zero score, no reasons and no direction.`

- WHEN the market type is `spot` THEN `derivatives` is `not_applicable`. Both cases are residual inputs; the product registry admits neither spot nor stock-linked instruments.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| reconstruction disagrees with `run_all_scanners` | raise | `UniverseBaselineIntegrityError(authority_…)` | no audit record, never a partial one |
| research capture raises | swallowed by the non-fatal seam | warning log | Candidate publication unaffected |
| missing capability | declared gap | `capability_gaps` list | unavailable, never `0` |

## Authority

May only describe the frozen research scorer; it can never change Candidate admission,
Universe publication, Risk, Gateway, credentials or Live unlock.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| UB-C01 | no executable claim (import-graph property; see Known conformance gaps) |
| UB-C02 | `tests/test_universe_baseline_integrity.py::test_audit_preserves_authority_output_exactly` |
| UB-C03 | `tests/test_universe_baseline_integrity.py::test_non_winner_family_direction_and_reasons_are_not_lost` |
| UB-C04 | `tests/test_universe_baseline_integrity.py::test_event_is_truthfully_marked_as_market_shock_proxy`, `::test_derivatives_v1_capability_gaps_are_explicit` |
| UB-C05 | `tests/test_universe_baseline_integrity.py::test_spot_derivatives_are_explicitly_not_applicable`, `::test_stock_linked_short_bias_is_explicitly_not_applicable` |

### Known conformance gaps

- The research seam recomputes the six-family scores on every Universe refresh even
  when no benchmark window is active (`service.py::_capture_universe_discovery_nonfatal`
  scores before `discovery_benchmark.py::capture_completed_run` returns
  `no_active_window`). This is not a behavior violation, but it is per-refresh
  computation with no consumer outside an active window.

## Decision references

- Parent research program: #2556 (Universe Discovery Benchmark). Original baseline branch
  point `master@a48c4f42e2d97882278b0e18bdd6ef7e3421bfc4`.
- Open research questions recorded by the original contract and still emitted as
  `competition_questions` (all `unresolved`): whether Relative Rank beats the step score,
  whether the complex Universe beats a simple Momentum baseline, and whether basis /
  liquidation / cross-venue data lift Derivatives. `relative-rank-shadow-v1` has no
  current producer.
- 2026-10-10 rewrite: re-scoped from "current `universe-step-v3` authority" to research
  baseline, because Candidate Selection Utility is the current selection authority.
