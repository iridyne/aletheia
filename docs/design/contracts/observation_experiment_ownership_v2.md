# Observation Experiment Ownership V2 Contract

## 1. Status and purpose

This D2 Contract freezes the target ownership boundary for Issue #2737. It is
authoritative for the migration design, but it does not claim that the authority
cutover has happened. Until the Phase 8 gate is explicitly satisfied, the
legacy Main/Learning experiment resolver remains authoritative and all new Lab
V2 state is non-authoritative.

The target invariant is:

> Learning may propose hypotheses. Observation Lab owns every registered
> Experiment definition, Generation, assignment, Exposure, Evaluation,
> fixed-look state, lifecycle, retention claim, and archive. Main Runtime owns
> production and trading facts, applies only an immutable Lab-issued Manifest
> through a registered adapter, and transactionally records the bounded receipt
> proving what actually happened.

"Experiment" means a durable prospective, counterfactual, or qualification
cohort whose evidence may compare, justify, admit, reject, or withdraw a
behavior or policy. Unit tests, deterministic regressions, and one-shot offline
diagnostics are not Experiments. An offline result used for qualification must
first be registered as an Observation under an appropriate causal contract.

## 2. Stable vocabulary and ownership

| Term | Meaning and canonical owner |
|---|---|
| Observation | One frozen causal claim/specification; Lab-owned after registration. |
| Generation | One immutable stage/version/window of an Observation; Lab-owned. |
| Unit | One exact prospective statistical/assignment unit; Lab-owned projection of an exact source identity. |
| Exposure | What Main actually assigned, applied, refused, or left unassigned at T0; Lab owns the canonical imported ledger. |
| Evaluation | One immutable monitoring or confirmatory result over an exact source cut; Lab-owned. |
| Manifest | Minimum immutable Lab-to-Main executable behavior contract; Lab publishes, Main validates and consumes. |
| Behavior-policy epoch | Actual production configuration/behavior identity; Main/domain-owned. |
| Nomination | Mutable research proposal remains Learning-owned; Lab stores only an immutable admitted snapshot/provenance. |

### 2.1 Learning / research

Learning continues to own cases, attribution, reviews, lessons, memories,
reasoning, calibration, research synthesis, and hypothesis nomination before
registration. Nomination publication grants no Experiment or production
authority. Registration content-addresses the exact admitted research,
hypothesis, treatment, and evidence envelope and records
`source_nomination_id` plus its hash. `observation_nominations`, if physically
needed, is only an immutable admitted snapshot/import index, never a second
mutable Learning nomination ledger.

Mixed health state is split by responsibility: importer, evaluator, lifecycle,
archive, and Manifest health belongs to Lab; Dreaming, reviewer, lesson, and
continuous-Learning health remains with Learning.

### 2.2 Observation Lab

Lab owns registered Observation identity, immutable Generations, assignment
contract, allocation, gates, frozen Units, Exposures, outcome-adapter identity,
Evaluation and exact evidence membership, fixed looks, lifecycle, Manifest
publication/revocation, experiment-cycle health, derived retention claims,
terminal result artifacts, and archives.

Lab is not a second market or trading database. It has no Risk, Gateway,
credential, signer, order, fill, Position, or Live authority.

### 2.3 Main Runtime and domain owners

Main owns market, Candidate, Feature, Filter, Opportunity, Decision, Proposal,
Risk, Order, Fill, Position, cost, outcome, runtime-session, producer-commit,
actual behavior-policy identity, and a bounded Observation delivery outbox.
Domain/owner authority still controls the first Shadow-to-Paper admission, new
behavior dimensions, adapters allowed to influence Paper, permanent policy
adoption, and all existing Risk/Execution/Live boundaries.

Main safety gates are monotonic deny-only gates stronger than Lab authority. A
valid `paper_treatment` Manifest is necessary but never sufficient. A denial
executes the deterministic baseline and still records a receipt with the stable
refusal reason so Lab retains honest ITT/refusal evidence.

## 3. Current legacy ownership inventory and freeze

Phase 1 freezes the existing coupling; it does not move or delete it.

| Responsibility | Current legacy locations allowed during migration |
|---|---|
| Experiment/exposure/verdict persistence | `aletheia/learning/ledger.py`, `experiments.py`, `contribution_runner.py`, `fixed_look.py` |
| Experiment lifecycle mutation | `aletheia/learning/lifecycle.py`, `maintenance.py`, `hypothesis_compiler.py` |
| Atomic Universe exposure publication | `aletheia/universe/service.py` |
| Filter-shadow lifecycle | `aletheia/counterfactual/filter_experiments.py`, scheduler/retention callers |
| Legacy schema/repair | `aletheia/storage/migrations.py` |
| Cohort, authority, and read projections | `aletheia/learning/cohorts.py`, `authority.py`, `read_models.py`, `knowledge_yield.py`, `economic_knowledge.py`, analytics and current API/CLI projections |
| Observation compatibility reads | current `aletheia/observation_lab` runner, exposure, checkpoint, and DB modules |

The executable architecture inventory allowlists current production references
to `learning_experiments`, `learning_exposures`,
`learning_contribution_verdicts`, and `filter_shadow_experiments`. Any new
reference requires deliberate review, and any new direct or repository-style
write outside the temporary writer allowlist fails CI. The allowlist is a
migration debt ceiling, not approval for additional coupling. It may only
shrink or move to the Lab-owned model in later serial phases.

The current legacy treatment API inventory is frozen as follows:

| Legacy API | Current production references |
|---|---|
| `apply_numeric_treatment` | definition/dispatch in `aletheia/learning/experiments.py`; references in `knowledge/guards.py`, `trading/sizing_policy.py`, `universe/allocator.py`, `analytics/factor_feedback.py`, `learning/economic_knowledge.py`, `agents/paper_trader_ranker.py`, and `trading/entry_plan_compiler.py` |
| `apply_boolean_treatment` | definition/dispatch in `aletheia/learning/experiments.py`; references in `learning/prompt_bundles.py` and `risk/engine.py` |

No new caller may be added during migration. Later phases move these exact consumers through registered ObservationHandlers/TreatmentAdapters or preserve their deterministic baseline; they do not grow the legacy API surface.

`LearningPath` is not a second Experiment registry after cutover. Consumer and
knowledge-path identity may remain domain/Learning-owned; `ObservationHandler`
owns measurement capability, `TreatmentAdapter` plus Main allow/deny gates owns
executable behavior capability, and Lab Generation state owns stage/readiness.
Flags such as `measurement_supported` and `paper_authority_supported` must be
retired or reduced to non-authoritative compatibility projections.

## 4. Canonical model and schema economy

Reuse `observation_windows`, `observation_units`, `execution_manifests`, and
existing append-only Lab primitives whenever they satisfy identity,
immutability, transaction, query, and retention requirements. The V2 storage
list in #2737 is a logical responsibility checklist, not a mandate for one
table per noun. Add a table only for an independently enforceable lifecycle,
uniqueness, transaction, or retention invariant; do not collapse genuinely
independent authority state merely to reduce table count.

The Lab durable model must cover:

- schema migrations and compatibility identity;
- immutable Generation treatment/allocation/gate/outcome contracts;
- admitted nomination provenance;
- exact Units and idempotent Exposures keyed by source receipt;
- monitoring and confirmatory Evaluations;
- fixed-look plans and consumption;
- lifecycle and append-only revocation;
- import watermark/checkpoint state and job health;
- derived exact retention claims;
- verified archives with compact permanent Generation roots;
- a reliable Lab audit outbox.

Effective production behavior-policy identity remains Main-owned. Each
Generation and Exposure stores an immutable reference/hash to the Main policy
identity it observed. A table's `learning_` prefix alone is not evidence that
its semantics should move.

## 5. Registration and feedback flows

Learning publishes an immutable nomination artifact. Lab ingests it
idempotently by exact nomination/spec/behavior identity. A registered handler
validates the bounded Observation contract. An ineligible nomination remains a
nomination with an explicit rejection; automatic registration is permitted only
for a handler that explicitly allows an observe-only Shadow Generation.
Resource and overlap limits remain explicit, and no Main candidate call lazily
creates an Experiment after cutover.

A terminal Lab Generation publishes one immutable result identity containing
the Observation, Generation, spec and Evaluation fingerprints, evidence cutoff,
verdict/disposition, exclusions/harm, and archive identity. Learning may ingest
that artifact idempotently as evidence for future research and link derived
knowledge by exact artifact ID/hash. It cannot infer production authority from
the result and must not create another mutable Lab-to-Learning result ledger.

Experiment graduation is separate from experimentation. If the owner adopts a
treatment as the normal baseline, Main/domain authority records a new behavior
policy epoch/version. Lab marks the Generation terminal adopted/superseded,
revokes the temporary Manifest, and preserves the evidence/archive. A positive
verdict never changes permanent production policy automatically.

## 6. Assignment, Manifest, and treatment resolution

Every behavior-capable path uses one registered, path-agnostic TreatmentAdapter
seam. A caller supplies production facts and deterministic baseline; it does
not query or create hypotheses, Generations, verdicts, fixed looks, predecessors,
readiness, or promotion recommendations.

The seam loads the complete active Manifest set through a physical read-only
projection, validates effective interval and revocation, checks explicit
Manifest-contract and adapter versions, resolves interference, assigns the
exact unit, applies a bounded payload or baseline, and emits a transactional
receipt. There is no implicit `latest` handler or adapter fallback.

`ExecutionManifest` is reused and gains an explicit wire-contract version plus
the minimum missing authority shape, including an effect mode such as
`observe_only` or `paper_treatment`. Observe-only assigns arms and records an
Exposure but executes baseline. Only owner-admitted Paper Generations may
publish behavior-bearing Manifests. Missing, stale, expired, inverted,
revoked, incompatible, conflicting, or unsupported composition resolves to
baseline.

`factorial_ok` conflict classification is not executable authority by itself.
Baseline remains mandatory unless a versioned adapter implements and tests the
exact factorial assignment, composition, and estimand contract.

Filter-shadow keeps a hard equivalent of
`production_mutation_allowed = 0`: it has no TreatmentAdapter and cannot publish
`paper_treatment`. A later behavior-changing filter is a separately admitted
Observation Generation.

### 6.1 Assignment identity

Assignment identity includes an immutable `assignment_namespace_key`, salt,
unit-key semantics, hash algorithm/normalization, and denominator contract.
New Lab-native Generations may derive the namespace from canonical Generation
identity. Imported active legacy Generations preserve the exact legacy
`experiment_id` namespace, salt, unit key, and assignment-contract version.
Cutover requires bit-for-bit arm parity over a large deterministic fixture;
preserving the salt alone is insufficient.

### 6.2 Authority decision instant

Because `app.db` and `lab.db` are not globally linearizable, every treatment
receipt persists the exact Manifest authority snapshot used: active Manifest
hashes, revocation-set/version fingerprint, resolution time, Manifest-set
fingerprint, and contract version. Revocation is authoritative for decisions
resolved after durable `revoked_at`; an already admitted in-flight Main
transaction is not rewritten retroactively. Any cache has a versioned snapshot
identity and bounded validity.

## 7. Main-to-Lab delivery and owner actions

Main never commits a production behavior change and then best-effort writes
`lab.db`. One bounded Main-owned outbox is the transport/proof boundary, not an
Experiment ledger. A transaction that applies or refuses an Experiment
treatment atomically commits the canonical production fact, exact outcome seed
when required, and receipt.

Receipts bind kind, durable identity/order, Manifest and adapter identity,
assignment and candidate/unit identity, consumer/stage, applied and
behavior-changed flags, baseline/applied payload hashes, exact outcome source,
typed resolution reason, Runtime Session, producer commit, authority snapshot,
and application time.

Transport is at-least-once and Lab import is exactly-once by source receipt ID.
The importer watermark is the highest successfully processed existing committed
receipt, not assumed integer contiguity. Numeric gaps alone are not evidence
gaps. A missing-receipt alarm requires a persisted source
checkpoint/range/hash proving a committed receipt should exist. Restore checks
use persisted checkpoint, watermark, and source identity. Main prunes only
after Lab acknowledgement plus a safety grace. Capacity backpressure disables
Experiment treatments to baseline while ordinary Paper continues.

Authenticated owner actions use the same durable bridge with a typed
`owner_action` receipt binding actor, action, Observation and expected
Generation, expected Manifest/behavior-policy identity where relevant, reason,
action time, audit identity, and dedup key. Lab imports idempotently and performs
the lifecycle transition. Main never edits Lab lifecycle state directly.

API acceptance of an owner-action receipt means `persisted/pending`, not
`admitted`, `promoted`, or `refrozen`. Those states may be reported only after
the Lab-applied transition is observable; stale/conflicted/rejected actions are
reported honestly.

## 8. Evaluation, retention, archive, and audit

An Evaluation declares its exact input fingerprint and source cutoff. It uses
immutable Lab Exposures, exact Main outcome identities, and a bounded read
transaction/watermark or imported immutable outcome projection. Retry with
unchanged inputs preserves measurement identity; newly settled outcomes create
a new monitoring Evaluation, while a frozen confirmatory cutoff never moves.

Retention claims are a derived, rebuildable index of canonical active
Generation, Exposure, and pending-outcome state. A claim row cannot invent
evidence authority. Release requires exact settlement, import, and archive
proof. Unknown/corrupt claim state fails closed only for the affected domain.
Before any Generation becomes Lab-authoritative, its T0 inputs must be frozen
and outcomes exactly protected, or a proven sufficient temporary legacy guard
must remain active.

Archive writing, hash verification/replay, and compact archived roots for new
Lab Generations must exist before authority cutover. Cold eviction may remove
bulky Units, Exposures, and Evaluation payloads but permanently retains
`(observation_id, generation)`, terminal status, spec/generation fingerprint,
archive path/hash/schema, and terminal identity. Generation IDs cannot be
reused, and status/history reports `archived`, never `not_found`. Phase 11 uses
this already-proven mechanism for bulk legacy import and retirement.

Lifecycle transitions, positive Manifest publication, and withdrawal are
transactional inside Lab. A behavior-enabling transition atomically persists
the transition/successor Generation, required fixed-look plan, Manifest or
explicit no-Manifest reason, and audit-outbox row. Withdrawal atomically
persists lifecycle event, revocation, closure/refreeze, and audit row.

The audit reconciler mirrors stable dedup identities to canonical audit JSONL
and records acknowledgement/failure health. Before implementation, Phase 2
must explicitly freeze which authority transitions require successful audit
mirroring before effectiveness and which preserve eventual-mirror semantics.

## 9. Runtime ownership, compatibility, and observability

The Lab runner is the sole Experiment-cycle authority. `execution_owner=worker`
may be the production choice, but existing `execution_owner=main` remains a
supported host for the same Lab runner unless separately retired. It never
revives the legacy Learning scheduler, and owner transitions preserve mutual
exclusion.

Main and Worker may run different commits only when Lab schema,
Manifest-contract, and TreatmentAdapter versions are mutually compatible.
Incompatibility fails closed to baseline. Rolling-update preflight must prove
compatibility before a behavior-bearing Manifest remains effective.

Exactly one durable Main-owned authority epoch selects
`experiment_authority_source = legacy | lab`. In `legacy`, only the legacy
resolver may affect behavior while Lab may shadow/import/evaluate. In `lab`,
only the generic seam may affect Experiment behavior and legacy rows are
history. There is no dual-authoritative state. A switch rotates Main behavior
identity, is owner/audit attributable, is consistent across processes and
startup, and proves the old resolver unreachable before Phase 8 completes.

Before cutover, the canonical observe overview read model exposes, without
direct SQL: Manifest/schema/adapter compatibility; active Manifest/revocation
snapshot fingerprint and age; outbox count, oldest age, and high watermark;
Lab watermark and proven missing committed receipts; importer/evaluator/
lifecycle health; baseline fallback counts by stable reason; retention-claim
summary; Lab capacity and archive backlog. Missing facts are `unavailable`,
never zero or healthy by invention. These are operational facts, not economic
verdicts.

## 10. Compatibility with existing authorities

- #1072 remains authoritative for physical Main/Worker independence, one Lab
  cycle owner, and supported `main`/`worker` hosting. #2737 supersedes only the
  clause that migrated Learning Experiment truth remains canonical in
  `app.db`.
- #2614 remains authoritative for the generic `observation_windows` /
  `observation_units` kernel, handler architecture, fairness, and isolation.
  #2737 supersedes only its temporary non-goals of leaving every Learning
  Experiment and all treatment authority outside the Lab control plane.
- #1135 remains authoritative for rolling-update continuity and must add Lab
  schema, Manifest wire-contract, and adapter compatibility before treatment
  becomes behavior-bearing.
- #1008 remains the continuous Learning product authority. This Contract moves
  Experiment responsibilities only.
- #470/#447 historical UFC evidence remains fixed, negative/refrozen, and
  non-authorizing; migration cannot reopen it.

## 11. Serial migration gates and rollback

Phases execute serially under #2737. Phase 1 freezes this Contract and the
architecture debt ceiling. It does not authorize schema, treatment, import, or
cutover work. Each later phase records its child Issue/PR, exact merged commit,
head reviewed, tests, runtime/deploy proof, rollback proof, contract delta, and
next-phase gate as an immutable umbrella comment. A checkbox closes only after
merge and its `done when` proof.

Before Phase 8, exact-head revalidation must resolve or prove non-applicability
for #1386, #1478, #1193, #1597, #1773, #1806, #1812, #2110, #2114, and material
open #2616 findings. Green CI cannot override an unresolved Class-A blocker.
Authority cutover additionally requires bounded archives for new terminal
Generations and proven retention safety for every active Generation.

Before cutover, rollback leaves legacy authority active and Lab state
non-authoritative. After Lab cutover and legacy-write freeze, default rollback
is deterministic baseline. Legacy authority may resume only for a preregistered
rollback whose exact Generation remained continuously dual-recorded and proven
equivalent. Otherwise recover Lab or restore one coherent joint checkpoint.
After physical retirement, only the verified joint pre-retirement backup is a
valid rollback source. Rollback never deletes evidence or grants authority.

Fresh-schema retirement means the effective schema after normal initialization
contains no retired Experiment tables and current declarations do not recreate
them. Immutable historical migrations may create and later drop them when the
migration model requires; migration history is not rewritten for a grep result.

## 12. Verification and non-goals

Required executable proof is phased and includes assignment parity, immutable
identity, exact outcome membership, ITT versus actual intervention,
monitoring/confirmatory separation, fixed-look cutoff immutability, immediate
harm withdrawal, owner-only first Paper admission, atomic production fact plus
receipt, idempotent import, restore mismatch failure, bounded outbox and Lab
capacity, exact claim release, archive replay, single authority source, and
absence of new legacy writes.

Repository tests prove mechanics, not economic benefit. Each behavior-changing
Generation still requires prospective evidence, frozen identity, harm state,
and owner admission.

This migration does not add another scheduler/queue service, generic workflow
engine, third-party experiment platform, market/outcome database, statistical
DSL, automatic parameter tuning, dashboard prerequisite, per-experiment
process/database/worktree, broad Learning-memory migration, or any Risk,
Gateway, credential, signer, order, Position, or Live authority. Existing
cohort/evaluator/fixed-look algorithms move only after equivalence proof; a
statistical contract change is a new Generation.
