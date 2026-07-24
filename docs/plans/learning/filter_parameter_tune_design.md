# `filter_parameter_tune` Learning Path — Design Blueprint

> Status: **skeleton declared, no runtime wiring**. This document describes the full
> architecture. Every wiring stage is an independent PR requiring safety review.
> Created 2026-07-19.

---

## 1. Why this path exists

`filter_controller` produces `overrestrictive_suspect` verdicts on
`(filter_name, scanner_family, direction, market_type, horizon)` cells — a
signal that "rejected candidates from filter X on family Y had a better net
outcome than passed candidates, so filter X is likely too tight on family Y".
Today this signal:

- persists into `controller_recommendations` and is retention-pruned at 30 days
- is surfaced by `GET /analytics/filter-controller` (as of PR #145) with the
  stability signal (PR #145) and the Learning Reports UI (PR #146)
- is **not acted on** — the read-only `filter_ttl_shadow` path
  (`is_learning_treatment=False, allowed_effects=("read_only",)`) is by design
  observational only

The `ranker_factor_filter` path cannot consume this signal — its action space
is per-candidate `factor_key` block/score, not filter parameter mutation
(see PR #145 description for the full mismatch).

`filter_parameter_tune` is the future path that WILL close this loop.

## 2. Action space (declared)

- **`allowed_effects=("filter_parameter",)`** — a new effect type. When
  authorized, a runtime consumer may read a bounded parameter override at
  filter-check time instead of the static config value.
- **`overlap_group="filter_parameter"`** — isolates from `filter_shadow`
  (observation-only), `ranker` (per-candidate), and `universe` (allocation
  multipliers). An experiment on this path cannot compete with an experiment
  on a different overlap group.
- **`authority`**: "bounded paper filter-parameter override". A parameter
  multiplier `m ∈ [1.0, MAX_MULTIPLIER]` (or `[MIN, 1.0]` for filters whose
  `loosen_direction` is decreasing) times the static default.
- **`outcome_source="aggregate fixed-horizon filter-cell outcome"`** — the
  cost-aware net expectation across candidates admitted under the tuned
  parameter, aggregated per `(filter, cell, arm, horizon)`.

## 3. Missing pieces (each an independent PR)

### 3.1 Outcome contract

`build_experiment_cohorts` reads `filter_candidate_outcomes` and
`universe_forward_outcomes`. Neither knows about "candidate would have been
rejected under the static parameter but was admitted under the tuned one".

Two design options:

**Option A — new table `filter_parameter_experiment_outcomes`**:
- One row per `(experiment_id, arm, horizon, admitted_candidate_key)`
- Populated by the runtime consumer at admission time (arm assignment + entry)
- Settled by the outcome recorder after horizon window
- Cohorts.py joins this table alongside the existing two

**Option B — extend `filter_candidate_outcomes` with `experiment_arm` column**:
- Reuse the existing settler and retention plumbing
- Cohorts.py filters by arm when the path is `filter_parameter_tune`

Option A is cleaner (segregated data) but doubles the outcome-source count.
Option B is more compact but overloads a table used by other consumers.

**Prerequisite for either**: `SUPPORTED_PATH_IDS` in `cohorts.py` must gain
`filter_parameter_tune`, and the outcome-loading branch must know how to
resolve the aggregate contract without changing behavior for existing paths.

### 3.2 Filter parameter override table

```sql
CREATE TABLE filter_parameter_overrides (
    override_id       TEXT PRIMARY KEY,
    filter_name       TEXT NOT NULL,
    scanner_family    TEXT NOT NULL,
    direction         TEXT,
    market_type       TEXT,
    horizon           TEXT,
    filter_version    TEXT,
    parameter_multiplier REAL NOT NULL,
    hypothesis_id     TEXT NOT NULL,
    experiment_id     TEXT NOT NULL,
    exposure_id       TEXT NOT NULL,
    stage             TEXT NOT NULL,   -- shadow | paper_10 | ... | paper_active
    applied_at        TEXT NOT NULL,
    expires_at        TEXT,
    UNIQUE (filter_name, scanner_family, direction, market_type, horizon, filter_version)
);
```

Writer: the exposure record of `apply_numeric_treatment(path_id="filter_parameter_tune", ...)`
also stamps a row here. Reader (§3.4) enforces the safety envelope on read.

### 3.3 Auto-hypothesis generator

A new job (probably `filter_parameter_hypothesis_generator`, scheduled at 6h+
intervals) that:

1. Reads `compute_persistent_recommendations(min_consecutive_runs=cfg.min, ...)`
2. For each cell whose `filter_name` is in `cfg.filter_parameter_tune_allowlist`:
   - Computes a proposed multiplier via each filter's `loosen_direction`
     (bounded by `cfg.filter_parameter_tune_max_multiplier`)
   - Calls `apply_numeric_treatment(path_id="filter_parameter_tune", ...)` with
     the cell as scope and the proposed multiplier as `treatment`
   - This inline-creates the hypothesis + experiment in shadow, and records
     an exposure for the current cohort of candidates in that cell

Because `_ensure_experiment` is a hypothesis lifecycle constructor (`shadow`
default), the auto-generator does not need any new hypothesis-lifecycle code —
it just needs to invoke `apply_numeric_treatment` correctly.

### 3.4 Runtime consumer wiring

The filter check-time reader:

```python
def resolved_filter_parameter(
    filter_name: str, scanner_family: str, direction: str,
    market_type: str, horizon: str, static_value: float,
) -> float:
    """Return the authoritative parameter (static or tuned) for this check."""
    # 1. Fail closed in live mode — never read overrides.
    if is_live_mode():
        return static_value
    # 2. Fail closed if the path is not consumer_wired (registry).
    if not get_learning_path("filter_parameter_tune").consumer_wired:
        return static_value
    # 3. Fail closed if the master switch is off.
    cfg = load_config().learning
    if not cfg.filter_parameter_tune_enabled:
        return static_value
    # 4. Fail closed if the filter is not in the allowlist.
    if filter_name not in set(cfg.filter_parameter_tune_allowlist or []):
        return static_value
    # 5. Look up the current experiment for this cell; return baseline if none.
    override = _lookup_override(filter_name, scanner_family, ...)
    if override is None:
        return static_value
    # 6. Enforce the multiplier bound.
    m = max(1.0, min(override.parameter_multiplier, cfg.filter_parameter_tune_max_multiplier))
    return static_value * m
```

Every gate here is redundant on purpose. Overrides only take effect when ALL
of the following are true simultaneously:

- paper (not live) mode
- `consumer_wired=True` in the registry
- master config switch on
- filter allowlisted
- the experiment for this cell has `authority_applied=True` (i.e. positive
  verdict and `paper_10+` stage — guaranteed by the standard learning gate)
- the multiplier respects the bound

### 3.5 Safety envelope config

```python
class LearningConfig(BaseModel):
    filter_parameter_tune_enabled: bool = False
    filter_parameter_tune_allowlist: list[str] = []  # explicit filter names
    filter_parameter_tune_max_multiplier: float = 2.0
```

Default: **off, empty allowlist, cap 2×**. A future PR that wants to
activate the path must:
- flip `filter_parameter_tune_enabled=True`
- add specific filter names to the allowlist
- ensure `paper_influence_paths` includes `filter_parameter_tune`
- have positive verdicts on the shadow-stage experiments for those cells
- have `lifecycle_automation_enabled=True` for auto-promotion (else stays shadow)

## 4. Landing sequence

Each stage is one PR, gated by review:

1. ✅ **This PR** — path declared, `consumer_wired=False`, `is_learning_treatment=False`, no wiring
2. **Outcome contract** — pick Option A or B (§3.1), migrate schema, extend cohorts.py
3. **Override table** — schema + writer (§3.2)
4. **Auto-hypothesis generator** — the job that reads persistent signals and creates shadow experiments (§3.3)
5. **Config safety envelope** — declare the three knobs (§3.5), all default off
6. **Runtime consumer wiring for ONE pilot filter** — implement `resolved_filter_parameter` and route ONE filter (candidate: `family_ttl_sec`) through it. Path flag flips to `consumer_wired=True, is_learning_treatment=True, settings_supported=True`.
7. **Verdict flow & UI** — surface auto-generated hypotheses and their contribution verdicts in the Learning UI
8. **Operational rollout** — one specific `(filter, family)` cell shadow-runs for weeks before admit-trial is invoked

## 5. Why this deserves a skeleton PR

Declaring the path early in the registry (with fail-closed flags) has three
benefits:

- **Naming stability** — every future PR can reference `filter_parameter_tune`
  consistently instead of inventing (and renaming) an ad-hoc identifier
- **Inventory truth** — the registry currently claims to be a complete list of
  paths in `LEARNING_PATHS`. Adding this entry closes the gap between "signals
  we produce" and "paths we could wire them to"
- **Safety by construction** — `consumer_wired=False, is_learning_treatment=False`
  in this skeleton means no code path can accidentally consume overrides even
  if a well-intentioned developer copy-pastes a wrong invocation elsewhere;
  `cohorts.SUPPORTED_PATH_IDS` gate rejects any exposure written under this
  path (see `build_experiment_cohorts` early return)

## 6. What this PR does not do

- ❌ Not create any hypotheses
- ❌ Not create any exposures
- ❌ Not create the override table
- ❌ Not read overrides in any consumer
- ❌ Not touch `SUPPORTED_PATH_IDS`, `paper_influence_paths` allowlist, or any live
  code path
- ❌ Not enable auto-hypothesis generation

## 7. 2026-07-24 Observer pre-filter pilot boundary

The first mature persistent signal concerns `observer_pre_filter`, whose
production decision is a two-dimensional hardcoded OR rule rather than one
scalar TTL. Do not use that finding to wire this entire generic learning path.

The pilot defined in
`docs/plans/trading-pipeline/observer_prefilter_alpha_repair_2026-07-24.md`
must proceed through:

1. read-only matched-rule attribution;
2. a fixed and bounded RSI/ATR replay grid;
3. one exact-cell prospective shadow;
4. a separately authorized paper-only canary.

Until those gates pass, `filter_parameter_tune` remains
`consumer_wired=False`, no override table is read by Market Observer, and no
controller recommendation may mutate the production pre-filter.
