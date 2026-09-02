# Observation Experiment Ownership V2 Migration Contract

## Purpose and current status

This Contract governs the currently active Phase 1 migration boundary for Issue
#2737. The accepted future design is
[Observation Experiment Ownership V2](../proposals/observation_experiment_ownership_v2.md).

The target design is not current runtime behavior. Until the explicit Phase 8
authority cutover, the legacy Main/Learning Experiment resolver and its
`app.db` ledger remain authoritative. Observation Lab owns only the behavior
already guaranteed by
[Observation Lab v1](observation_lab_contract.md). Phase 1 changes documentation
and CI architecture constraints only; it does not change Runtime, schema,
treatment, scheduler, retention, or trading behavior.

## Current ownership

- Learning owns research, lessons, knowledge, mutable pre-registration
  nominations, and the existing legacy Experiment control plane.
- Main owns production/trading facts, behavior-policy identity, Risk,
  Execution, Gateway, credentials, and Live authority.
- Observation Lab v1 owns its implemented windows, units, Manifests, Lab
  exposure facts, runner, and joint-checkpoint primitives.
- Existing no-prune and earliest-exposure guards remain authoritative until
  later phases prove exact Lab retention claims and archive/replay safety.
- The existing `execution_owner=main|worker` modes host the same Lab runner;
  exactly one cycle owner remains mandatory.

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

- Phases execute serially under #2737; only the current phase may implement.
- Phase 1 does not authorize Lab schema, outbox, importer, TreatmentAdapter,
  lifecycle, retention, archive, or authority-cutover implementation.
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

Rollback removes only the Phase 1 documentation and architecture tests. It does
not migrate or rewrite durable state. The next phase remains locked until this
phase merges, all review findings are resolved, and its done-when proof is
recorded on #2737.
