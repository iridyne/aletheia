# Observation Experiment Ownership V2 Migration Contract

## Purpose and authority

This Contract records the durable migration boundary established for Issue
#2737. The accepted future design is
[Observation Experiment Ownership V2](../proposals/observation_experiment_ownership_v2.md).

The target design is not a second experiment ledger. The executable V2 bridge
now provides the Lab-owned canonical schema, immutable generation contracts,
receipts/import cursors, lifecycle/audit outbox, fixed-look/result identities,
retention claims and verified generation archives. The durable Main authority
switch still defaults to `legacy`; until an explicit cutover proof is recorded,
legacy treatment remains the production authority and the V2 bridge is
non-authoritative compatibility/evidence infrastructure.

The bridge is intentionally fail-closed: a missing Lab manifest, incompatible
adapter, revoked contract, unresolved conflict, deny tombstone, unavailable
Lab, or full delivery outbox returns the declared deterministic baseline while
preserving a Main receipt. A `lab` authority epoch is the only state in which
the generic `TreatmentAdapter` seam may apply a behavior-changing Manifest.

## Current ownership

- Learning owns research, lessons, knowledge, mutable pre-registration
  nominations, and the existing legacy Experiment control plane.
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

## Frozen legacy storage inventory

The temporary reference and writer allowlists in
`tests/specs/spec_observation_experiment_ownership.py` are the executable
inventory. They cover current production references to:

- `learning_experiments`;
- `learning_exposures`;
- `learning_contribution_verdicts`;
- `filter_shadow_experiments`.

Current persistence and mutation are concentrated in the Learning ledger,
experiment/lifecycle/evaluator modules, Universe publication, filter-shadow
repository/CLI/scheduler paths, retention tooling, and legacy migrations.
Current read projections include Learning cohort/authority/read-model,
analytics, API/CLI, continuity, and Observation compatibility paths.

The inventory is an exact debt ceiling. CI compares discovered references,
legacy treatment API references, and writers for equality with their respective
allowlists. Removing coupling without shrinking its allowlist fails CI, as does
adding coupling outside it.

## Frozen legacy treatment-call inventory

| Legacy API | Current production references |
|---|---|
| `apply_numeric_treatment` | `learning/experiments.py`, `knowledge/guards.py`, `trading/sizing_policy.py`, `universe/allocator.py`, `analytics/factor_feedback.py`, `learning/economic_knowledge.py`, `agents/paper_trader_ranker.py`, `trading/entry_plan_compiler.py` |
| `apply_boolean_treatment` | `learning/experiments.py`, `learning/prompt_bundles.py`, `risk/engine.py` |

No new caller or legacy Experiment store may be introduced during migration.
Later phases migrate these exact consumers to the accepted Proposal's
ObservationHandler/TreatmentAdapter boundary or preserve their deterministic
baseline.

## Current migration invariants

- The V2 bridge does not authorize a cutover by itself; the exact authority
  source/epoch, current-head equivalence, retention safety, recovery checkpoint
  and rollback proof are still required before selecting `lab`.
- Existing Universe production fact, outcome seed, and legacy Exposure
  atomicity remains unchanged.
- Observation Lab grants no Risk, Gateway, credential, signer, order, Position,
  or Live authority.
- Filter-shadow remains hard observe-only with
  `production_mutation_allowed=0`.
- #470/#447 historical UFC evidence remains fixed, negative/refrozen, and
  non-authorizing.
- Runtime or economic qualification is not required for this documentation/test
  phase; repository checks prove only the architecture freeze.

## Compatibility with existing authorities

- #1072 remains current for Main/Worker independence and one Lab cycle owner.
- #2614 remains current for the generic Observation window/unit kernel, handler
  architecture, fairness, and failure isolation.
- #1135 remains current for existing rolling-update continuity.
- #1008 remains the continuous Learning product authority.
- The accepted Proposal describes which ownership clauses those authorities
  will eventually supersede; no supersession occurs merely by merging Phase 1.

## Verification and rollback

Executable SpecClaims must prove that:

1. every current legacy-table reference is exactly inventoried;
2. every current `apply_numeric_treatment` and `apply_boolean_treatment`
   reference is exactly inventoried;
3. actual legacy-table writes are detected without textual-proximity false
   positives;
4. no allowlist can remain stale after coupling is removed.

The V2 compatibility suite additionally proves receipt atomicity/idempotency,
contiguous import, Lab audit mirroring, archive-before-eviction, exact claim
protection, and authority-epoch fail-closed behavior. These checks establish
storage and transport semantics; they do not claim that any treatment is
economically positive or production-qualified.

Rolling back this documentation-and-test boundary does not migrate or rewrite
durable state. Phase sequencing, active work, merge status, review evidence, and
done-when tracking remain exclusively in #2737 rather than in this Contract.
