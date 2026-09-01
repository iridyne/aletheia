# Observation Lab v1 Contract

## Purpose

Observation Lab is the long-running evidence runtime for bounded prospective observations. Its job is to let a frozen experiment continue collecting under its own causal identity while the Main Runtime may continue to evolve independently.

A valid Observation binds:

- a frozen hypothesis / treatment-control contract;
- frozen first-exposure inputs or another exact causal unit;
- a canonical outcome contract;
- exact behavior / metric / cost identities;
- fixed evidence and verdict rules;
- zero direct trading authority unless a separate existing authority explicitly permits a treatment.

This Contract describes current behavior only. It does not contain a future implementation order.

## Related authorities

- [counterfactual_contract.md](counterfactual_contract.md) owns the underlying prospective/counterfactual evidence rules.
- [observation_lab_backup_contract.md](observation_lab_backup_contract.md) owns app.db + lab.db + manifest backup/consistency semantics.
- [observation_lab_v1_design.md](observation_lab_v1_design.md) describes the current implementation shape and operational boundaries.
- [../../research/audits/UFC_OWNERSHIP_MAP_2026-08-18.md](../../research/audits/UFC_OWNERSHIP_MAP_2026-08-18.md) is frozen historical ownership evidence, not a development plan.

When historical design text conflicts with current code, executable specs or this Contract, correct the current authority rather than replaying an old implementation sequence.

## Storage boundary

Observation Lab uses two SQLite roots under the configured Aletheia data directory:

- `app.db` — production canonical facts such as filter decisions/outcomes and Learning/execution evidence owned by their existing domains;
- `observation-lab/lab.db` — Lab-owned frozen units, equivalence observations, execution manifests, Lab exposure facts and joint checkpoint evidence.

Lab storage is physically separate so Main Runtime migrations cannot silently rewrite Lab snapshots.

`open_readonly_snapshot()` provides SQLite `mode=ro` + `PRAGMA query_only=ON` for paths that require a physical read-only boundary.

Lab must not create a second source of market, Candidate, Position, cost, Risk or execution truth.

## Adapter causal identity

Every supported Observation adapter declares the exact semantic inputs that define whether an existing cohort is still comparable.

For the current ranker-confidence adapter, the declaration includes the adapter/cohort/evaluator/outcome identities and the exact input/parameter fields consumed by the treatment.

Rules:

- causal identity is explicit and versioned;
- source commit/version may remain provenance without automatically fragmenting a cohort;
- a material causal-identity change terminates the incompatible collecting cohort fail-closed while preserving historical evidence;
- filters/adapters without a declared causal-identity contract retain their existing stricter compatibility behavior until a separately reviewed change defines otherwise.

## Frozen first-exposure evidence

`filter_shadow_frozen_units` stores immutable first-exposure units in `lab.db`.

Each row preserves at least:

- experiment/observation identity;
- candidate/outcome unit identity;
- treatment-relevant decision/input/parameter facts at first exposure;
- first-evaluated time;
- source Runtime provenance.

Capture is idempotent. Missing causal facts remain unavailable. A later schema/runtime state must not retroactively rewrite the frozen unit.

Evaluation may combine frozen Lab units with canonical outcomes from `app.db`, but the join must preserve exact unit identity and outcome semantics.

## Runtime ownership

Exactly one execution owner runs the Observation cycle:

- `main` — Main Runtime scheduler owns the cycle;
- `worker` — the dedicated Observation Worker owns the cycle through the supported one-shot command/systemd boundary.

There is no supported state in which both owners execute the same cycle.

Ownership changes must remove the non-owner scheduler/entry path, preserve the same Observation semantics and never create duplicate collection authority.

The Worker must not start or own Gateway, Risk, Execution, exchange credentials, Operator or trading services.

## Lab runner

`run_lab_windows()` executes registered observation windows with bounded, deterministic and exception-isolated behavior.

Current windows include filter-shadow evaluation and UFC equivalence observation. Additional windows may register through the same descriptor/runner contract when independently authorized.

One window failure must not prevent other Lab windows or ordinary Paper operation from continuing. Failures remain visible in the returned/durable status rather than being projected as success.

## Execution manifests and exposure facts

Where a treatment contract is represented by an `ExecutionManifest`:

- manifest identity is immutable/versioned;
- `(observation_id, generation)` cannot silently change meaning;
- manifest hashes are stable and conflict-checked;
- Lab exposure facts are append-only;
- Main Runtime may consume only a manifest/adapter combination explicitly supported by the relevant behavior authority;
- Lab publication never grants Risk/Execution/Live authority by itself.

## Zero-authority guarantees

Observation Lab is evidence infrastructure, not a trading authority.

- `filter_shadow_experiments.production_mutation_allowed = 0` remains a storage-enforced invariant for filter-shadow observations.
- Lab does not own Risk, Gateway, credentials, orders, fills or Positions.
- `ready_for_review` or any equivalent Lab result means only that evidence is reviewable under its declared contract.
- Paper influence still requires the existing path-specific experiment/authority and owner gates.
- Live authority is outside this Contract.

## Runtime independence

An Observation may survive unrelated Main Runtime code changes when its declared causal identity and required canonical facts remain compatible.

Lab does **not** promise continuity through a real change to source population, outcome semantics, cost identity, required schema or treatment meaning. Such a change must terminate or rotate the affected evidence identity explicitly.

## Current deliberate boundaries

The following capabilities are not implied by this Contract merely because related code or historical design notes exist:

- migration of every filter/Learning path to one adapter-identity model;
- deletion/migration of existing Learning evidence tables;
- a generic behavior-comparability engine;
- randomized assignment for paths whose current contract is first-exposure/cohort based;
- Web UI as a prerequisite;
- new scheduler infrastructure;
- Risk/Execution/credential/config-mutation authority.

A future capability becomes current only through its own focused Change Contract and verified implementation. No ordering among these possibilities is implied.

## Verification

Current executable evidence must continue to prove:

- unrelated source-version changes do not invalidate a causally compatible adapter;
- material adapter-identity changes fail closed without rewriting history;
- frozen-unit replay matches the declared live/reference evaluator where equivalence is required;
- multiple observations can advance independently without starvation;
- read-only SQLite boundaries reject writes;
- `lab.db` is physically separate from `app.db`;
- Lab CLI/runtime surfaces cannot mutate trading authority;
- execution manifests/exposures remain immutable/idempotent;
- only one cycle owner is active;
- Lab failure does not stop ordinary Paper operation.

Repository/test success proves implementation semantics only. It does not prove a treatment is economically positive or production-qualified.