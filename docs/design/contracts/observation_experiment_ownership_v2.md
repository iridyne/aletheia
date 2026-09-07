# Observation Experiment Ownership V2 Migration Contract

## Purpose and authority

This Contract records the durable migration boundary established for Issue
#2737. The accepted future design is
[Observation Experiment Ownership V2](../proposals/observation_experiment_ownership_v2.md).

Observation Lab is the sole online Experiment authority after the single-mainline
cutover. Legacy ledgers are retained only for explicit proof-gated import and
historical reads. Fresh databases initialize Lab authority. Existing legacy
databases cannot run the retired writer while awaiting migration.

The bridge is intentionally fail-closed: a missing Lab manifest, incompatible
adapter, revoked contract, unresolved conflict, deny tombstone, unavailable
Lab, or full delivery outbox returns the declared deterministic baseline while
preserving a Main receipt. A `lab` authority epoch is the only state in which
the generic `TreatmentAdapter` seam may apply a behavior-changing Manifest.

## Current ownership

- Learning owns research, lessons, knowledge and pre-registration nominations,
  not an Experiment control plane.
- Main owns production/trading facts, behavior-policy identity, Risk,
  Execution, Gateway, credentials, and Live authority.
- Observation Lab v1 owns its implemented windows, units, Manifests, Lab
  exposure facts, runner, and joint-checkpoint primitives.
- Legacy mode keeps the existing no-prune and earliest-exposure guards. Lab
  mode uses exact active retention claims plus verified archive-before-eviction;
  missing claim/source state fails closed and never widens deletion.
- The existing `execution_owner=main|worker` modes host the same Lab runner;
  exactly one cycle owner remains mandatory.

## Executable V2 boundary

`observation_delivery_outbox` is a bounded Main transport/proof surface, not a
second Experiment owner. A production transaction writes its domain fact and
receipt together. `import_delivery_receipts()` consumes present receipts in
sequence order, inserts each Lab Exposure by `source_receipt_id`, and
acknowledges Main only after the Lab transaction commits. A numeric sequence gap
alone is not evidence loss; only a proven source checkpoint may classify a
missing receipt. A malformed present row stops the prefix and remains retryable.
Owner actions use the same transport and remain `pending` until Lab applies the
lifecycle event.

`observation_results` is an immutable Lab result projection for Learning and
review. Learning may import/read that artifact by fingerprint; a positive
verdict never grants production authority or changes a Main behavior policy.
Graduation/adoption remains a separate owner-approved Main behavior-policy
change, after which the Lab generation is revoked/superseded and retained as
evidence.

For measurement-supported paths, the Lab evaluator reads the immutable Lab
Exposure snapshot and exact Main outcome identities, then invokes the existing
cohort and verdict algorithms in memory. It records only Lab Evaluation and
Result rows; it never creates or reads legacy Experiment rows. Monitoring uses
the descriptive look, while confirmatory evaluation requires a hash-valid,
frozen Lab inference plan, its assignment-unit cutoff, and a declared finite
authority alpha; a consumed plan
returns its already-bound Evaluation rather than recomputing history.

## Single-authority migration

`set_authority_source` accepts only `lab`; restoring `legacy` is rejected.
Missing authority/schema keeps treatments at baseline with an unavailable reason.
The old evaluator, lifecycle controller, scheduler jobs and Experiment/exposure/
verdict writers are removed. Filter-shadow registration and evaluation use Lab.

The existing offline migration remains explicit:

```text
aletheia observe migrate-learning-ledger --apply --retire-legacy-schema --runtime-commit <exact-sha> --rollback-backup-identity <verified-id> --json
```

Checkpoint, archive hashes, source identity, pending receipts, retention claims
and verified recovery backup remain mandatory. Failure preserves source evidence.
Import never upgrades historical positives into current treatment qualification.
Learning projections read Lab results; unavailable canonical economic qualification
is reported as unavailable, not as zero effect or legacy success.

## Current migration invariants

- Main domain facts and delivery receipts remain atomic.
- Observation Lab grants no Risk, Gateway, credential, signer, order, Position,
  or Live authority.
- Filter-shadow remains hard observe-only with
  `production_mutation_allowed=0`.
- #470/#447 historical UFC evidence remains fixed, negative/refrozen, and
  non-authorizing.

## Compatibility with existing authorities

- #1072 remains current for Main/Worker independence and one Lab cycle owner.
- #2614 remains current for the generic Observation window/unit kernel, handler
  architecture, fairness, and failure isolation.
- #1135 remains current for existing rolling-update continuity.
- #1008 remains the continuous Learning product authority.

## Verification and recovery

Focused migration, canonical bridge, Lab evaluator/window/checkpoint and
end-to-end suites prove import idempotency, archive/receipt safety, one-way
authority, fail-closed treatment and preserved historical evidence. They do not
prove economic benefit or production qualification. Deployment recovery preserves
the existing joint-checkpoint and Observer progression guards; it never turns
the legacy writer back on or rewrites an advanced Observation window.
