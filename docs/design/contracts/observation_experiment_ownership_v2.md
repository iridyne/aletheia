# Observation Experiment Ownership Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `OEO` · **Owner code:** `extensions/observation_lab/canonical.py`
> (`get_authority_source`, `set_authority_source`) and `extensions/observation_lab/migration.py`

## Scope

Exactly one Experiment authority exists per database, stored in `app.db`
`observation_authority_state`: `legacy` or `lab`. Under `lab`, Observation Lab is the sole
online Experiment owner and the only state in which a Lab manifest may change behavior; under
`legacy` (or an unreadable row) every Lab consumer stays on its deterministic baseline. The
cutover is one-way. Lab mechanics (receipts, checkpoint, windows) are owned by the
[Observation Lab Contract](observation_lab_contract.md); learning semantics by
[spec 0001](../specs/0001-learning-loop/spec.md). The completed migration narrative is in
[history](../history/observation_experiment_ownership_v2_migration.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `extensions/observation_lab/canonical.py::get_authority_source` | call | `resolve_observation_treatment`, `extensions/counterfactual/filter_experiments.py::_require_lab_authority`, `extensions/observation_lab/runner.py` (`filter_shadow_evaluate`), `ops/update/continuity.py` |
| `extensions/observation_lab/canonical.py::set_authority_source` | call | `core/aletheia/storage/migrations.py` (fresh install), CLI `aletheia observe set-experiment-authority lab` |
| `extensions/observation_lab/migration.py::apply_legacy_learning_migration` | CLI `aletheia observe migrate-learning-ledger --apply` | operator, one-shot |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `observation_authority_state` (`state_key='experiment_authority'`) | baseline DDL seeds `legacy`; `set_authority_source` | `get_authority_source` | one-way `legacy → lab` |
| legacy `learning_experiments`, `learning_exposures`, `learning_contribution_verdicts`, `filter_shadow_experiments` | none on the trading path; allowlisted retention/migration code only | migration import, backup verification | frozen; dropped by `retire_legacy_experiment_tables` |

## Requirements

### Executable V2 boundary

#### OEO-C01 One-way cutover

`The authority writer shall accept only lab and reject any request to restore legacy.`

- WHEN `set_authority_source("legacy", …)` is called THEN it raises `authority_source_must_be_lab`.
- WHEN a fresh database is migrated THEN authority is set to `lab` (`fresh_install_lab_authority`) and the legacy Experiment tables are dropped.
- WHEN an existing database is upgraded THEN its seeded `legacy` row stays until an explicit cutover.

#### OEO-C02 Cutover requires proof

`If a non-empty legacy database is cut over to lab, then the writer shall require no pending Main receipts and a proof whose migration plan, checkpoint, retention-claim fingerprints and runtime commit match current state.`

- WHEN pending receipts exist THEN it raises `authority_cutover_pending_main_receipts`.
- WHEN the checkpoint fingerprint differs THEN `authority_cutover_checkpoint_mismatch`; WHEN unimported legacy rows exist THEN `authority_cutover_unprovable_legacy_evidence`.
- Import never upgrades a historical positive into current treatment qualification.

#### OEO-C03 Legacy authority keeps baseline

`While authority is legacy or unreadable, every Lab treatment consumer shall return its deterministic baseline and Lab-only registration paths shall refuse.`

- WHEN filter-shadow registration runs under `legacy` THEN it raises `observation_lab_authority_required`.
- WHEN the `filter_shadow_evaluate` window runs under `legacy` THEN it errors with `authority_source_unavailable`.
- The entry-learning adapter is one Lab adapter with empty control and the `mechanism-aware-grounded-entry` interference key ([OL-C03](observation_lab_contract.md#ol-c03-entry-learning-adapter-boundary)).

#### OEO-C04 Results never grant authority

`The Lab shall expose observation_results as an append-only projection that no consumer may treat as a Main behavior-policy change.`

- WHEN a result verdict is positive THEN no Risk, Gateway, credential, sizing or Live input changes; adoption is a separate owner-approved behavior-policy change.

### Frozen legacy storage inventory

#### OEO-C05 Legacy coupling cannot grow

`The codebase shall keep production references to and writers of the four legacy Experiment tables within the exact allowlists in tests/specs/spec_observation_experiment_ownership.py.`

- WHEN a module outside the reference allowlist mentions a legacy table THEN the claim fails; WHEN an allowlisted module stops mentioning it THEN the claim also fails until the allowlist shrinks.

### Lab runtime independence

#### OEO-C06 Runner does not import the legacy runtime

`The Lab runner and evaluator shall not import extensions.learning.experiments or extensions.learning.ledger.`

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| authority row missing / malformed | baseline | `authority_source_unavailable` | never treated as `lab` |
| authority `legacy` | baseline; Lab registration refuses | `authority_source_legacy`, `observation_lab_authority_required` | not "no effect" |
| cutover proof mismatch | cutover refused, nothing written | `authority_cutover_*` reason | legacy evidence preserved |

## Authority

This capability may change only the Experiment authority row; it never grants Risk, Gateway,
credential, signer, order, Position or Live authority.

## Verification and rollback

| Requirement | Executable claim / focused test |
|---|---|
| OEO-C01 | `tests/test_observation_lab_migration.py::test_authority_cli_refuses_legacy_even_with_the_recorded_cutover_proof`, `test_explicit_retirement_requires_import_proof_and_drops_legacy_tables` |
| OEO-C02 | `tests/test_observation_lab_migration.py::test_lab_cutover_refuses_unimported_legacy_rows`, `test_nonempty_lab_cutover_requires_and_records_complete_proof`; `tests/test_observation_canonical_bridge.py::test_pending_receipt_blocks_cutover_without_losing_evidence` |
| OEO-C03 | `tests/specs/spec_observation_experiment_ownership.py::OEO-S05`; `tests/test_observation_canonical_bridge.py::test_numeric_treatment_without_lab_authority_preserves_baseline` |
| OEO-C04 | not covered by a dedicated test; the only non-Lab reader is `extensions/learning/read_models.py` (read-only projection). Related: `tests/test_observation_canonical_bridge.py::test_paper_manifest_without_explicit_lab_authorization_falls_back_to_baseline` |
| OEO-C05 | `tests/specs/spec_observation_experiment_ownership.py::OEO-S01`, `OEO-S03` |
| OEO-C06 | `tests/specs/spec_observation_experiment_ownership.py::OEO-S04` |

Deployment rollback restores through the joint checkpoint ([OL-C15](observation_lab_contract.md#ol-c15-restore-and-drift-resolution)); it never re-enables a legacy writer.

## Decision references

- #2737 single Lab authority ([accepted proposal](../proposals/observation_experiment_ownership_v2.md));
  #2945 retired the legacy Learning Experiment runtime; #470/#447 historical UFC evidence is
  fixed, negative and non-authorizing.
- Migration narrative and the offline `migrate-learning-ledger` recipe:
  [history](../history/observation_experiment_ownership_v2_migration.md).
