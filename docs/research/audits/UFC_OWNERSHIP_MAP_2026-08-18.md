# UFC Ownership + Deletion Map — Observation Lab Packet 0

> Status: Issue #1072 Packet 0 deliverable. Audit-only, no schema or behavior change.
> Date: 2026-08-18. Anchored at commit `d2fd2191` (branch `iridite/feat-observation-observation-lab-v1-decouple-pro`).
> Path in scope: `universe_family_calibration` (UFC) — the only current authority-bearing Learning path.

This doc maps every UFC-specific responsibility, table, scheduler entry, CLI surface, and file to one of four target owners defined by [#1072](https://github.com/iridite/aletheia/issues/1072):

| Target owner | Contract |
|---|---|
| **Lab** | canonical `ObservationSpec`; assignment contract; experiment lifecycle; time/sample/harm/conflict/integrity gates; continuity; evidence watermark; evaluator/fixed look; append-only evaluations; immutable verdict; multi-window scheduling |
| **Main Runtime generic seam** | load frozen `ExecutionManifest`; determine assignment deterministically; detect undeclared manifest conflicts; apply bounded treatments through registered adapters; persist actual exposure/provenance before outcome; continue Paper when Lab is unavailable |
| **Domain / Owner authority** | admit a treatment into Paper; accept/reject a verdict; promotion, refreeze, policy-epoch and successor decisions |
| **Canonical fact owner** | market, candidate, decision, Position, cost and outcome facts; Risk and Execution authority remain unchanged |

If a row does not belong to a canonical target, it is called out as **DELETE** (item disappears after Packet 3) or **RENAME/GENERALIZE** (item survives but loses UFC-specific coupling).

---

## 1. UFC identity (facts from current code)

| Field | Current value | Source of truth |
|---|---|---|
| `path_id` | `universe_family_calibration` | `aletheia/learning/registry.py:47` |
| Statistical unit | `universe_allocation_member` | `aletheia/learning/registry.py:52` |
| Behavior contract version | `universe-family-calibration/v11` | `aletheia/learning/registry.py:51` |
| Metric version | `universe_family_calibration_cluster_allocation_utility_v10` | `aletheia/learning/registry.py:30` |
| Outcome source | `fixed-horizon candidate net outcome` | `aletheia/learning/registry.py:49` |
| Fallback | neutral multiplier `1.0` | `aletheia/learning/registry.py:49` |
| Allowed effects | `("score_adjustment",)` | `aletheia/learning/registry.py:48` |
| Overlap group | `universe` | `aletheia/learning/registry.py:48` |
| Consumer | universe allocator (`apply_numeric_treatment` call at `aletheia/universe/allocator.py:349`) | code path |
| Assignment key | `universe:cluster:{scoring_cohort_key}:{family}:{direction}:{market_type}` | `aletheia/universe/allocator.py:343-345` |
| Assignment | deterministic `treatment/control` via `stable_assignment(experiment, key)`; `treatment ⇔ hash(exp_id + salt + key) < allocation_pct` | `aletheia/learning/experiments.py:55-58` |
| Sample gate | `min_samples_per_arm=50` (hard-coded) | `aletheia/learning/experiments.py:183` |
| Continuity gate | `min_market_days=7` | `aletheia/learning/experiments.py:184` |
| Allocation | `allocation_pct=0.5` at shadow (`_stage_allocation` scales later stages) | `aletheia/learning/experiments.py:182`; `aletheia/learning/lifecycle.py:1078` |
| Harm budget | `max_loss_rate_increase=0.15`, `max_mae_p95_worsening_pp=0.25` | `aletheia/learning/experiments.py:160-163` |
| Confirmatory inference | `ufc-fixed-look/v2`, conditional-randomization, α=0.025 | `aletheia/learning/fixed_look.py`; `aletheia/learning/verdicts.py::_conditional_randomization_inference` |
| Behavior-contract hash | `f(behavior_version, policy_epoch, target_horizon, outcome_contract_version, metric_version)` | `aletheia/learning/behavior_contracts.py::current_behavior_contract_hash` |
| Population / interference key | `assignment_unit_key` cluster (see §4) | see §4 |

---

## 2. Responsibility ownership map

Each row = one current UFC responsibility. Column "current site" = where it lives now; "target owner" = post-migration home.

### 2.1 Registration and spec

| Responsibility | Current site | Target owner |
|---|---|---|
| Register hypothesis (fingerprint = f(path_id, scope, action, horizon, source_cohort, behavior_contract_version)) | `learning/experiments.py::_ensure_experiment`; `learning/hypotheses.py::hypothesis_fingerprint` | **Lab** — becomes canonical `ObservationSpec` |
| Create `LearningExperiment` (assignment_salt, allocation_pct, min_samples_per_arm, min_market_days, promotion_gate_json, harm_gate_json) | `learning/experiments.py::_ensure_experiment`, lines 179-188 | **Lab** — becomes `ObservationSpec.gates` + `ExecutionManifest` |
| Terminal-hypothesis guard (refrozen ⇒ no new experiment on scanner tick) | `learning/experiments.py:171-178`; `TERMINAL_HYPOTHESIS_STATUSES` | **Lab** — spec lifecycle |
| `spec_hash` (must exist per new #1072 §"Minimal contracts") | **absent** in current code (fingerprint exists, but no explicit spec_hash) | **Lab** — new field |
| Population / eligibility declaration (scope dict passed by allocator) | ad hoc — allocator builds scope dict at `universe/allocator.py:~330` | **Lab** authors it; **Main Runtime seam** reads it from the manifest |

### 2.2 Assignment and unit construction

| Responsibility | Current site | Target owner |
|---|---|---|
| Assignment unit key construction (`universe:cluster:…`) | `universe/allocator.py:343-345` | **Main Runtime generic seam** — driven by manifest's `assignment_unit_key` template |
| Salt storage | `LearningExperiment.assignment_salt` (per-experiment UUID4) | **Lab** — part of `ObservationSpec` |
| Deterministic `stable_assignment` (hash + allocation_pct) | `learning/experiments.py:55-58` | **Main Runtime generic seam** — pure function, reusable |
| Suppress-shadow-if-paper-active exclusion | `learning/experiments.py:418-428` (checks staged experiments in same overlap_group) | **Main Runtime seam** — becomes part of manifest-set conflict resolution (see §5) |

### 2.3 Exposure persistence and treatment application

| Responsibility | Current site | Target owner |
|---|---|---|
| `apply_numeric_treatment` — resolve authority, decide value, persist `LearningExposure` | `learning/experiments.py:352-579` | Split: **Main Runtime generic seam** (assign + apply + persist exposure fact); **Lab** owns manifest inputs; **Canonical fact owner** owns `filter_candidate_outcomes` etc. |
| `apply_boolean_treatment` variant | `learning/experiments.py:583+` | Same split (guard authorization) |
| `LearningExposure.manifest_json` fields `authority_applied` / `authority_reason` / `behavior_changed` / `behavior_change_reason` | `learning/experiments.py:523-530` | **Main Runtime seam** — becomes canonical `Exposure fact` (issue §"Exposure fact") |
| `code_commit` on every exposure (provenance, folded into policy_epoch) | `learning/experiments.py:539` | **Canonical fact owner** — provenance only, per new #1072 §"Main Runtime iteration semantics" |
| `insert_exposure` idempotency (dedup on `dedup_key = f"{exp_id}:{cand}:{stage}:{epoch}:{contract_hash}"`) | `learning/experiments.py:505-508`, `insert_exposure` | **Canonical fact owner** — exposure ledger stays where it is |

### 2.4 Behavior contract, policy epoch

| Responsibility | Current site | Target owner |
|---|---|---|
| `current_behavior_contract_hash(...)` — combines behavior_version + policy_epoch + horizon + outcome_contract + metric | `learning/behavior_contracts.py` | **Lab** — part of `ObservationSpec` identity vector |
| `_policy_epoch_definition` / `persist_policy_epoch` — rotates on config-file diff, boot commit change, or Learning param change | `learning/experiments.py:217-349`; `learning/policy_epochs.py` | Split: **Lab** owns "does this generation continue?" decision (§"Main Runtime iteration semantics"); **Main Runtime seam** owns the boot-time snapshot; **Domain/Owner** ratifies. Table remains **Canonical**. |
| `_code_fingerprint` (module-cached boot commit) | `learning/experiments.py:206-214` | **Canonical fact owner** — pure provenance; do NOT let it fragment cohorts |

### 2.5 Fixed-look, evaluator, verdict

| Responsibility | Current site | Target owner |
|---|---|---|
| `register_fixed_look_decision` — atomic plan registration at trial admission | `learning/fixed_look.py:48-140` | **Lab** — fixed-look plan is a Lab artifact |
| `freeze_fixed_look_cutoff` — freeze earliest qualifying prefix `(exposed_at, assignment_unit_key)` | `learning/fixed_look.py:148-200` | **Lab** |
| `_prospective_assignment_units` / `_earliest_qualifying_prefix` | `learning/fixed_look.py:380-486` | **Lab** — pure read functions over exposures |
| `consume_fixed_look_verdict` (write-once fingerprint match) | `learning/fixed_look.py:223-260` | **Lab** — evaluator seam |
| `evaluate_cohort` — full verdict builder | `learning/verdicts.py:45-217` | **Lab** — extract as pure function |
| `_conditional_randomization_inference` (α=0.025 confirmatory) | `learning/verdicts.py:411-508` | **Lab** — pure evaluator |
| `_market_day_coverage`, `_consecutive_day_runs`, `_missing_days` (7-day continuity) | `learning/verdicts.py:277-410` | **Lab** — pure evaluator |
| `_treatment_intervention_metrics` (actual-intervention count) | `learning/verdicts.py:220-275` | **Lab** — pure evaluator |
| `_harm_metrics`, `_harm_gates`, `_loss_rate`, `_mae_p95` | `learning/verdicts.py:510-707` | **Lab** — pure evaluator |
| Cohort assembly + manifest-authority read | `learning/cohorts.py::build_experiment_cohorts`; `_manifest_authority` (565); `_exposure_behavior_change` (580); `_aggregate_assignment_units` (929) | **Lab** owns aggregation logic; **Canonical fact owner** owns `filter_candidate_outcomes` / exposures the reader consumes |
| Verdict persistence to `learning_contribution_verdicts` (append-only) | `learning/verdicts.py`; repo helpers | **Lab** — verdict ownership moves to `lab.db` after Packet 3; interim: dual-write in shadow-equivalence mode |
| `inference_authority_*` / `measurement_*_reason` (authority-eligibility gates) | `learning/fixed_look.py:262+`; `learning/authority.py:373+` | Split: **Lab** computes the gate reason; **Domain/Owner** consumes the eligibility decision |

### 2.6 Lifecycle, admission, promotion, refreeze

| Responsibility | Current site | Target owner |
|---|---|---|
| `process_lifecycle` — advance experiment on latest verdict | `learning/lifecycle.py:120-306` | **Lab** — window lifecycle |
| `admit_shadow_trial` — trial admission (Shadow → paper_10) | `learning/lifecycle.py:307-324` | **Domain / Owner authority** (owner PR); Lab only surfaces admission-ready state |
| `_execute_trial_admission` (allocates 10%, registers fixed-look) | `learning/lifecycle.py:326-510` | Split: **Domain/Owner** authorizes; **Lab** creates the new fixed-look plan and updates spec generation |
| `_promote_one_stage` — later stage progression | `learning/lifecycle.py:582-656` | **Domain / Owner authority** — owner-only |
| `_apply_safety_action` — refreeze | `learning/lifecycle.py:657-713` | **Domain / Owner authority** — owner-only |
| `_stage_allocation` (allocation_pct per stage) | `learning/lifecycle.py:1078+` | **Lab** — becomes a manifest field |
| `_promotion_gate_reason` / `trial_admission_gate_reason` | `learning/lifecycle.py:511-560` | **Lab** — surfaces gate reasons; owner still decides |
| `_is_experiment_idle` / `_retire_idle_experiment` (24h no-exposure retirement) | `learning/lifecycle.py:990-1077` | **Lab** — window lifecycle |
| `run_contribution_evaluator` (scheduler entry) | `learning/lifecycle.py:873-938` | **Lab** — becomes the multi-window runner |
| `run_lifecycle_controller` (scheduler entry) | `learning/lifecycle.py:939-989` | **Lab** — becomes the multi-window runner |
| Lifecycle audit events (`_write_lifecycle_audit`, `reconcile_lifecycle_audits`) | `learning/lifecycle.py:1097-1200` | **Lab** — audit trail moves with the lifecycle; verdict identity stays immutable |

### 2.7 Authority resolution

| Responsibility | Current site | Target owner |
|---|---|---|
| `resolve_learning_authority` — canonical authority gate | `learning/authority.py:73-343` | **Domain / Owner authority** — this stays outside Lab; Lab only surfaces "eligible / not eligible" |
| `_underpowered_current_stage_authority_allowed` (collection-liveness exception) | `learning/authority.py:457-485` | **Domain / Owner authority** |
| `_legacy_authority_adapter` fallback | `learning/authority.py:344-372` | **DELETE** after Packet 3 — legacy shim |
| `measurement_identity_reason` — identity match gate | `learning/authority.py:486-546` | **Lab** computes; **Domain/Owner** consumes |
| `trial_admission_measurement_block_reason` | `learning/authority.py:547+` | **Lab** computes; **Domain/Owner** consumes |
| `_enforce_shadow_authority_provenance` (invariant guard) | `learning/experiments.py:66-125` | **Main Runtime seam** — the "manifest says shadow but consumer applied treatment" check moves to manifest-conflict detector |

### 2.8 CLI / status / server surfaces

| Responsibility | Current site | Target owner |
|---|---|---|
| `_UFC_PATH_ID = "universe_family_calibration"` — hard string | `cli/control_cmd.py:23` | **RENAME/GENERALIZE** — `aletheia control` becomes owner-facing; drop the UFC hard-coding, iterate all Lab windows |
| `_open_experiments`, `_open_policy_epochs`, `_learning_authority`, `_evidence_epochs` | `cli/control_cmd.py:315-377` | Read: **Lab** post-migration; **Canonical fact owner** for evidence epochs. Same CLI, new backing store. |
| `aletheia observe {list,status,sync,evaluate,register,close}` (this branch's PR #1083) | `cli/observe_cmd.py` (new) | **Lab** — reuse the same CLI shell; extend from filter-only to any spec kind |
| Learning-related dashboard / server routes | (present, not enumerated here) | Same read-only projection over Lab-owned tables |
| Data seed / test fixtures for UFC | `cli/data_cmd.py` (UFC hits) | **Lab** — test harness moves with Lab |

### 2.9 Storage — tables in `app.db` today

`grep -oE 'learning_[a-z_]+' aletheia/storage/migrations.py` (deduped):

| Table | Current owner | Target owner |
|---|---|---|
| `learning_hypotheses` | Learning module | **Lab** — becomes `observation_specs` view or is renamed. Sample dual-writing during shadow-equivalence (Packet 1) |
| `learning_experiments` | Learning module | **Lab** — becomes `observation_generations` (one per generation) |
| `learning_exposures` | Learning module | **Canonical fact owner** — this IS the Exposure fact table (§"Exposure fact") — stays in `app.db`, Lab reads only |
| `learning_contribution_verdicts` | Learning module | **Lab** — verdict is Lab-owned; migrate write side to `lab.db` after Packet 2; keep read shim during transition |
| `learning_inference_decisions` (+ `_no_delete` trigger, `_v` view) | Learning module | **Lab** — fixed-look plan is Lab-owned |
| `learning_policy_epochs` | Learning module | Split: **Canonical fact owner** (immutable epoch ledger); **Lab** consumes for identity comparison |
| `learning_job_health` | Scheduler | **Lab** — one health record per Lab window; UFC-specific columns retired |
| `learning_verdict_runner` | Scheduler | **DELETE** — replaced by Lab runner cursor |
| `filter_shadow_experiments` / `filter_shadow_evaluations` | Counterfactual (PR #1083) | Stays canonical for filter-shadow POC; unrelated to UFC route |
| `filter_shadow_frozen_units` (in `lab.db`, PR #1083) | Observation Lab (this branch) | **Lab** — first Lab-owned table; extend schema for UFC generation in Packet 2 |

### 2.10 Backup / restart

| Responsibility | Current site | Target owner |
|---|---|---|
| SQLite WAL / migration ledger for `app.db` | `aletheia/storage/{migrations,sqlite}.py` | **Canonical fact owner** — unchanged |
| Lab DB init (`lab.db` schema on first open) | `aletheia/observation_lab/db.py` (this branch) | **Lab** |
| Backup consistency of `app.db` + `lab.db` + active manifests (new #1072 §"Storage and runtime boundary") | **absent** — new requirement | **Lab** — must land a joint checkpoint contract before Packet 2 |

---

## 3. Population / interference key

**Definition today:**
- The assignment unit is a `universe_allocation_member` — one candidate proposal from the universe allocator within one scoring cohort.
- The key: `universe:cluster:{scoring_cohort_key}:{family}:{direction}:{market_type}` (`universe/allocator.py:343-345`).
- One candidate belongs to exactly one cluster per snapshot; `stable_assignment` is deterministic given `(experiment_id, assignment_salt, cluster_key)`.
- The interference boundary is at the cluster level: allocator emits candidates for a family × direction × market_type; treatment/control within the cluster share market path and are the analysis unit for `_conditional_randomization_inference`.

**Overlap semantics:**
- `overlap_group="universe"` (registry.py:48) marks UFC as claiming exclusive authority over this behavior dimension.
- `suppress_shadow_if_paper_active` (learning/experiments.py:418-428) walks open experiments in the same overlap_group and blocks a new Shadow exposure when a `paper_*` stage is active on the same universe cohort. This is today's manifest-conflict detector — inline, per-candidate, in the treatment application function.

**What the new `ExecutionManifest` must carry:**
- `assignment_unit_key` template (currently the `universe:cluster:…` format string)
- `overlap_group` (currently `"universe"`)
- `eligible_population` predicate (currently implicit in the allocator's scope construction)
- `composability_contract`: today = "exclusive" (default rule "at most one authority-bearing treatment per population + behavior dimension")

---

## 4. Manifest-conflict boundary

**Where undeclared conflict is detected today:**
- Inline in `apply_numeric_treatment` — `suppress_shadow_if_paper_active` scans open experiments in the same `overlap_group` (learning/experiments.py:418-428).
- `_enforce_shadow_authority_provenance` (learning/experiments.py:66-125) — invariant check: if a Shadow experiment's manifest already carries `authority_applied=True`, emit a violation event and refuse.

**Gaps vs new #1072 requirement:**
- No enumeration of the "complete active manifest set for an eligible unit". Today the check is pairwise (this experiment vs any other `paper_*` in overlap_group).
- No typed conflict exposure fact — a suppressed exposure just returns baseline; there is no `conflict_reason` written to `learning_exposures`.
- No factorial / composability contract encoded anywhere.

**Target boundary (post-migration):**
- Main Runtime seam loads the active manifest set (per eligible unit) → checks composability → applies exactly one authority-bearing treatment OR falls back to declared baseline.
- Undeclared conflict must write a typed exposure fact for every affected window; no window is silently starved.

---

## 5. Deletion list (post-Packet 3)

Items to remove from Learning / Main Runtime once Packet 2 proves the Lab-native UFC generation runs end-to-end. Grouped by risk.

### 5.1 Delete outright

- `learning/authority.py::_legacy_authority_adapter` — pre-canonical shim; unused after Lab owns admission surfaces.
- `learning/experiments.py::_verdict_authorizes` — one-line compatibility shim already; delete once no caller.
- `learning_verdict_runner` scheduler table — replaced by Lab runner cursor per window.
- Every `_UFC_PATH_ID = "universe_family_calibration"` literal that treats UFC as a special case rather than iterating Lab windows (`cli/control_cmd.py:23`, and mirror references in server/routes).

### 5.2 Generalize / rename (not delete)

- `learning/experiments.py::apply_numeric_treatment` — extract the pure "load manifest → deterministic assignment → apply treatment → persist exposure" flow into `aletheia/runtime_seam/treatment.py` (or similar). Keep it path-agnostic; UFC becomes one caller among many.
- `learning/lifecycle.py::run_contribution_evaluator` + `run_lifecycle_controller` — merge into the Lab bounded runner (issue §"Runner semantics"); today they are UFC-shaped iterators.
- `learning/registry.py::LEARNING_PATHS` — decouple from `apply_numeric_treatment`; `LearningPath` becomes one input to `ObservationSpec` composition rather than the ownership record.
- `_ensure_experiment` in `learning/experiments.py` — becomes `Lab.ensure_generation(spec)` — spec identity + generation counter comes from Lab, not from the scanner tick.

### 5.3 Keep — canonical fact ownership

- `learning_exposures` table stays. Add manifest-set / conflict fields per §4 target boundary.
- `learning_hypotheses`, `learning_experiments`, `learning_inference_decisions`, `learning_policy_epochs`, `learning_contribution_verdicts` — keep the underlying storage in `app.db` for one release after Packet 2 (dual-write / read-only fallback), then move write ownership to `lab.db` in Packet 3.
- `learning_job_health` — retain, but Lab owns per-window rows and legacy UFC rows are archived not deleted.

### 5.4 Not touched by this migration

- `filter_shadow_experiments`, `filter_shadow_evaluations`, `filter_shadow_frozen_units` — the counterfactual-filter POC on this branch. Unrelated to UFC ownership; Packet 4 territory.
- `universe/allocator.py` outside the `apply_numeric_treatment` call site — allocator business logic (family multipliers, cluster construction) stays canonical.
- Every non-UFC `LearningPath` in `registry.py` — no path is migrated until Packet 4 selects one based on real friction.
- Risk, Execution, credential, Live-unlock, order placement code — new #1072 §"Non-goals" and §"Anti-overengineering" explicitly forbid moving these.

---

## 6. Non-migration items (stay outside Lab forever, per #1072)

- Risk Engine, Execution Gateway, credentials, exchange connectivity, order placement, Position management, market collector.
- Owner admission actions (protected route).
- Second full Aletheia Runtime / Gateway.
- Per-window daemon / worker fleet / DAG / queue.

---

## 7. Open questions surfaced by the audit

Marked so Packet 1 or 2 can resolve without re-auditing:

1. **`spec_hash` field**. Not present today; `hypothesis_fingerprint` covers most identity but omits `execution_class`, `manifest_hash`, and population predicate. Needs a new column in `learning_hypotheses` (Packet 1) or a Lab-side view (Packet 2).
2. **Behavior-contract hash uses `code_commit` fold**. `_policy_epoch` folds boot commit into the epoch id when config-file inputs are missing (`learning/experiments.py:307-325`). Per new #1072 §"Main Runtime iteration semantics", `code_commit` should be provenance only. This is a latent coupling — audit whether the fold ever fires in production, and if so, remove it as part of Packet 1.
3. **`_load_ranker_case_outcomes` in `cohorts.py:817`** — separate outcome loader for `ranker_case_score` path. Confirms per-path outcome loading is already the pattern; Lab must generalize this into a `spec.outcome_contract` dispatch.
4. **Backup/restore consistency across `app.db` + `lab.db` + active manifests** — required by new #1072 but no code exists. Design owed before Packet 2.
5. **Manifest-conflict typed exposure fact** — required by new #1072 §"Treatment-bearing overlap" but current code just returns baseline. Design owed at the Packet 2 boundary.

---

## 8. Referenced files (audit-time snapshot)

- `aletheia/learning/registry.py` — path declaration, LearningPath dataclass
- `aletheia/learning/experiments.py` — assignment, apply_numeric_treatment, ensure_experiment, policy_epoch resolution
- `aletheia/learning/hypotheses.py` — fingerprint, hypothesis persistence
- `aletheia/learning/behavior_contracts.py` — `current_behavior_contract_hash`
- `aletheia/learning/policy_epochs.py` — persist_policy_epoch, build_policy_epoch
- `aletheia/learning/lifecycle.py` — process_lifecycle, admit_shadow_trial, promotion, refreeze, runners
- `aletheia/learning/fixed_look.py` — register / freeze / consume fixed-look plan
- `aletheia/learning/verdicts.py` — evaluate_cohort, conditional-randomization inference, market-day, harm
- `aletheia/learning/authority.py` — resolve_learning_authority + reason codes
- `aletheia/learning/cohorts.py` — build_experiment_cohorts, exposure/outcome joins
- `aletheia/universe/allocator.py` — the UFC consumer (`apply_numeric_treatment("universe_family_calibration", …)`)
- `aletheia/universe/filter_outcome_recorder.py` — outcome persistence path
- `aletheia/cli/control_cmd.py` — status/read surface
- `aletheia/storage/migrations.py` — table DDL
- `docs/design/contracts/learning_contract.md` — normative spec

## 9. What this doc does NOT do

- No schema change proposed. Packet 0 is audit-only.
- No PR to move any UFC code. Packet 1 (shadow-equivalence adoption) is next.
- No decision on which non-UFC path migrates in Packet 4. New #1072 §"Only then migrate another experiment class" makes that dependent on real friction observed after Packet 3.
- No commitment on Fedora deployment timing — that follows Packet 2's real-run proof.
