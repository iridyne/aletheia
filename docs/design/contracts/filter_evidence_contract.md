# Filter Evidence Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `FE` · **Owner code:** `core/aletheia/analytics/filter_evidence.py`

## 1. Scope

This contract owns the identity, immutability, encoding and retention of
`filter_decisions` and `filter_decision_batches`: the ex-ante record of what each gate saw
and decided, so a pass or reject can later be joined to the fixed-horizon net result
owned by the [Opportunity Outcome contract](opportunity_outcome_contract.md). Current
producers are the Universe ticker and tradability gates, the Universe Discovery Benchmark
research capture, and the authoritative Risk decision (plus the optional prediction
divergence research scanner). It never changes a parameter, a Risk limit, Live unlock or
Gateway behavior. Text describing the retired Ranker / PiDecision / Market Observer /
System Operator entry chain is preserved in
[history](../history/filter_evidence_retired_chain.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/analytics/filter_evidence.py::record_filter_decision_batch` | call | `core/aletheia/universe/features.py` (`ticker_availability`, `ticker_volume_prefilter`) and `core/aletheia/universe/filters.py` (seven `tradability_*` gates), inside the `universe_refresh` job |
| `core/aletheia/analytics/filter_evidence.py::record_filter_decision` | call | `core/aletheia/universe/filters.py` (`tradability_spread`, fine stage); `extensions/research/divergence_scanner.py` (`prediction_divergence`, optional) |
| `core/aletheia/analytics/filter_evidence.py::record_risk_filter_decision` | call | `core/aletheia/risk/engine.py::evaluate_proposal` (authoritative role) |
| `core/aletheia/universe/discovery_benchmark.py::capture_completed_run` | call (writes a `universe_discovery_benchmark` batch) | `core/aletheia/universe/service.py::_capture_universe_discovery_nonfatal` |
| `core/aletheia/analytics/filter_evidence.py::filter_feedback_stats` | call | `extensions/analytics/filter_controller.py` (job `filter_controller_observe`, off unless `controller.shadow_enabled`) |
| `core/aletheia/diagnostics/probes/trading.py::_probe_filter_evidence` | doctor probe | `aletheia doctor` |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `filter_decisions` | `storage/repositories_universe.py::insert_filter_decision` | filter feedback, alpha attribution, doctor, historical funnel views | append-only; idempotent by dedup key; pruned after retention |
| `filter_decision_batches` | `storage/repositories_universe.py::insert_filter_decision_batch` | doctor, filter controller, benchmark readers | append-only; pruned after retention |
| `controller_recommendations` | `extensions/analytics/filter_controller.py` | analytics routes | append-only shadow findings; never configuration |

## Requirements

Requirement ids `FE-C01` and `FE-C03..C12` keep the meaning of the pre-existing executable
claims with those ids; new requirements use `FE-C02` and continue from `FE-C13`.

### 2. Candidate identity

#### FE-C02 Candidate key is a decision-time hypothesis

`The recorder shall key every decision by a candidate_key that identifies one candidate at one decision time, not a bare symbol.`

- Current keys: high-volume Universe gates store each feature's `asset_id`/`feature_snapshot_id` inside the `scanner_run_id` batch; the fine spread gate uses `feature_snapshot_id` (or `asset_id`, or `instrument:<venue>:<market>:<symbol>`); Risk uses `proposal:<proposal_id>`.
- Retired key families (`observer:…`, `opportunity:…`, `pi_decision:…`, `universe_member:…`) remain readable on historical rows only.

#### FE-C13 Identity fields

`The recorder shall store candidate_kind, candidate_key, parent_candidate_key, run/snapshot/member/proposal ids, venue, symbol, market_type, direction, scanner_family, regime_tag and evaluated_at with every per-candidate decision.`

- WHEN the direction is NULL THEN the row measures coverage and price movement only, never attributable net alpha.

### 3. Filter decision record

#### FE-C14 Append-only, idempotent record

`When a gate decision is recorded, the recorder shall insert it once under the dedup key candidate_key|filter_name|stage|sequence_no|decision|filter_version and shall never update an earlier decision.`

- `decision` is one of `passed`, `passed_with_penalty`, `rejected`, `not_applicable`, `error`; `passed` means the gate actually ran and did not block, and an unevaluated gate is `not_applicable`, never `passed`.
- `filter_version` is computed from the schema version, filter name and canonical parameter JSON; `code_commit` is the boot commit, never a hot-path shell-out.
- WHEN the same candidate is first rejected and later passes THEN both facts remain; WHEN the same gate name runs at two stages THEN they are two facts.

#### FE-C03 Secret exclusion

`The recorder shall drop any input or parameter key containing api_key, authorization, credential, password, prompt, raw_context, secret or token before writing.`

- The schema has no credential, authorization, raw prompt or raw context column.

#### FE-C15 Lossless high-volume batches

`When a directionless high-volume gate runs, the recorder shall write one filter_decision_batches row per run, filter, stage and version that holds every evaluated candidate fact and per-decision counts equal to those facts.`

- Facts are a `{"fields":[…],"rows":[[presence_mask,…]]}` matrix compressed as `zlib-base64:`; `decode_filter_decision_batch` also reads the uncompressed matrix and the legacy plain-list JSON without changing counts, values or missing-field meaning.
- WHEN filter, stage and version are unchanged THEN a heavy batch is written at most once per 55 minutes; a version change writes immediately; a failed cadence lookup writes anyway (fail open).
- Directional, settleable gates use per-candidate `filter_decisions`, never batches.

#### FE-C16 Filter evidence is not the current entry funnel

`The Overview, inspect entry-status and inspect candidates surfaces shall read current entry facts from the entry workbench (get_entry_workbench_summary, list_entry_workbench_candidates), and shall report unavailable on a read failure instead of reconstructing a funnel from filter_decisions.`

- Historical filter traces, blockers and wake telemetry stay readable under their original meaning.

### 3.1 B2 System Operator candidate evidence (retired)

The B2 debate-evidence summary served the retired System Operator entry path; its rules
are in [history](../history/filter_evidence_retired_chain.md). The summary builder
`core/aletheia/analytics/pi_evidence.py::build_b2_evidence_summary` survives only behind
`extensions/learning/b2_evidence_policy.py`.

### 4. Outcome linkage

#### FE-C17 Directional decisions seed outcomes

`When a decision is recorded with an outcome key, an exact venue, a positive decision-time entry price and a long or short direction, the recorder shall register the fixed-horizon outcome rows in the same call.`

- WHEN the direction is absent THEN no outcome row is created ([OO-C05](opportunity_outcome_contract.md)); the shared row, horizons and economics are owned by the Opportunity Outcome contract.

#### FE-C18 Bounded settlement priority

`While settling inside its bounded row and time capacity, the outcome settler shall take pending rows in this order: active Universe Discovery Benchmark outcomes, active Observation Lab exact behavior-contract keys together with open Candidate Selection Utility selection keys, active Lab retention-claim keys, then plain due_at order.`

- WHEN the Lab reader is unavailable or its time-boxed prologue expires THEN the settler logs `Lab priority unavailable` (or the truncation) and continues in due order.
- Priority changes only order; it never changes frozen costs, prices, retention, economics or trading authority.

### 5. Gate registry and authority class

#### FE-C08 Registry completeness

`Every gate that persists filter evidence shall appear in extensions/analytics/filter_registry.py, live or historical, and no unregistered permanent inline gate shall be added.`

#### FE-C19 One authority class per gate

`The registry shall give every gate exactly one authority_class of fact_or_execution, risk_or_safety, attention_ranking or strategy_judgment, and gates of the first two classes shall never be loosened from return evidence.`

#### FE-C06 Capability gates are report-only

`The registry shall register the directionless exchange and tradability gates as report-only (auto_tunable=false).`

- RWA perpetuals never reach these gates: they are blacklisted at registry admission ([CS-B2/CS-B3](candidate_selection_utility_contract.md)). `tradability_stablecoin` excludes only USD-stablecoin bases.

#### FE-C20 Risk evidence is atomic with the RiskDecision

`When Risk writes an authoritative RiskDecision, the engine shall write its final risk_engine filter evidence and outcome seed in the same transaction with raise_on_error, so an evidence failure rolls back the decision.`

- Preflight decisions write no final candidate evidence.

### 6. Feedback statistics

No current gate is an evaluation object for these rules: the three `strategy_judgment`
registry entries (`family_ttl`, `observer_pre_filter`, `scanner_weights_quota`) have no
current production writer, and the controller job is off by default
(`controller.shadow_enabled=false`). The rules below govern the historical rows the
controller can still read. Entry Planner judgment quality is measured by
[spec 0001](../specs/0001-learning-loop/spec.md), not by these statistics.

#### FE-C01 Feedback isolation

`The feedback statistics shall group only complete net outcomes by filter_name, stage, filter_version, scanner_family, direction, market_type and horizon.`

#### FE-C09 First-exposure cohorts

`The feedback statistics shall assign each outcome_candidate_key to the earliest persisted decision per filter_name, stage and filter_version, and shall apply the time window after that first-exposure ordering.`

- Later retries stay for tracing and never contribute a second sample.

#### FE-C21 Suspect is the strongest verdict

`The controller shall emit overrestrictive_suspect only when both pass and reject have at least 20 complete net samples and at least 70% outcome coverage on one filter_version, the reject shrunk mean (k = 20) exceeds the pass shrunk mean by at least 0.05 percentage points and is itself positive, and the gate is not a risk, safety, credential or capability gate.`

- A suspect finding never changes configuration.

#### FE-C10 Prospective shadow experiments

`When a replay is ready, the controller shall only preregister a prospective observe-only experiment on future first exposures, and no experiment state shall write production configuration or change a candidate's admission.`

#### FE-C04 Alpha attribution uses fixed-horizon net

`Alpha attribution shall read exact settled 24h filter_candidate_outcomes net economics, not variable-hold Position or legacy shadow PnL.`

#### FE-C05 Prospective controls share outcomes

`Prospective alpha controls shall register through ensure_candidate_outcomes and link the shared exact-instrument outcome; legacy gross-only controls are excluded.`

#### FE-C12 Historical Observer diagnostics are replay-only

`Where historical observer_pre_filter rows exist, the diagnostics shall use the persisted rule facts and parameter snapshot, and the bounded RSI/ATR replay shall be read-only and never cross a filter-version boundary.`

### 7. Retired batch selection

Ranker / PaperTrader batch and `signal_pending` queue rules are retired; see
[history](../history/ranker_paper_trader_filter_evidence.md).

### 8. Operations and retention

#### FE-C22 Observation failure does not change the trading decision

`If a non-authoritative evidence write fails, then the recorder shall log a warning and return without changing the caller's decision.`

- The authoritative Risk path is the exception (FE-C20).

#### FE-C11 Settler cadence in doctor

`The doctor probe shall report due outcomes inside the 20-minute settler grace as awaiting_cadence and only older ones as stale_overdue, given the outcome_settler job runs every 10 minutes.`

- The probe also reports batches whose `candidate_count` differs from the sum of decision counts as malformed.

#### FE-C07 Bounded retention

`The retention job shall prune filter_decision_batches after 30 days, filter_decisions after 45 days and filter_candidate_outcomes after 45 days by default, extended by active Observation retention claims and open-experiment floors, and shall never prune RiskDecision, proposal, order, fill, position or audit records.`

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| non-authoritative evidence write fails | warn, decision unchanged | `filter evidence write failed` warning; doctor coverage | missing evidence is not reported as complete |
| authoritative Risk evidence fails | transaction rolls back | Risk error | no approval without evidence |
| batch cadence lookup fails | write anyway | debug log | never suppresses evidence |
| batch counts disagree with facts | doctor finding | `malformed` count | — |
| Lab priority unavailable | plain due order | warning | ordering only |

## Authority

May write append-only gate evidence and shadow findings; may never change gate parameters,
Risk limits, Gateway behavior, credentials or Live unlock.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| FE-C01 | `tests/specs/spec_filter_evidence.py::FE-C01` |
| FE-C02 | `tests/specs/spec_filter_evidence.py::FE-S01` |
| FE-C03 | `tests/specs/spec_filter_evidence.py::FE-C03` |
| FE-C04 | `tests/specs/spec_filter_evidence.py::FE-C04` |
| FE-C05 | `tests/specs/spec_filter_evidence.py::FE-C05` |
| FE-C06 | `tests/specs/spec_filter_evidence.py::FE-C06`, `::FE-B08`, `::FE-B09` |
| FE-C07 | `tests/specs/spec_filter_evidence.py::FE-C07` |
| FE-C08 | `tests/specs/spec_filter_evidence.py::FE-C08` |
| FE-C09 | `tests/specs/spec_filter_evidence.py::FE-C09` |
| FE-C10 | `tests/specs/spec_filter_evidence.py::FE-C10` |
| FE-C11 | `tests/specs/spec_filter_evidence.py::FE-C11` |
| FE-C12 | `tests/specs/spec_filter_evidence.py::FE-C12` |
| FE-C13 | `tests/specs/spec_filter_evidence.py::FE-S01`, `::FE-B03` |
| FE-C14 | `tests/specs/spec_filter_evidence.py::FE-B01`, `::FE-B10` |
| FE-C15 | `tests/specs/spec_filter_evidence.py::FE-S03` |
| FE-C16 | no dedicated claim |
| FE-C17 | `tests/specs/spec_filter_evidence.py::FE-B02`, `::FE-S02` |
| FE-C18 | `tests/specs/spec_filter_evidence.py::FE-B04`, `::FE-B04a` |
| FE-C19 | `tests/specs/spec_filter_evidence.py::FE-C06` |
| FE-C20 | `tests/specs/spec_filter_evidence.py::FE-B07` |
| FE-C21 | `tests/test_filter_evidence.py::test_controller_only_flags_overrestriction_after_paired_gates` |
| FE-C22 | no dedicated claim |

Claims `FE-B05`, `FE-B2`, `FE-S15`, `FE-S17`, `FE-S18`, `FE-S20`, `FE-S24` exercise historical
shadow-trade, B2, skip-halt and migration-backfill readers; their anchors resolve to the
numbered sections above and their full original rules are in
[history](../history/filter_evidence_retired_chain.md).

### Known conformance gaps

- The pre-rewrite contract required the scheduler's shadow controller to use the full
  history window; `extensions/analytics/filter_controller.py` defaults `evaluated_since`
  to 90 days when none is passed.
- Claim `FE-C11`'s invariant text still says "15-minute settler cadence"; the job runs every
  10 minutes (`core/aletheia/agent_runtime/scheduler.py`, `id="outcome_settler"`).
- The pre-rewrite contract ordered canonical settlement by current boot `code_commit` and the
  open `universe_family_calibration` epoch first; `core/aletheia/universe/filter_outcome_recorder.py::settle_due_filter_outcomes`
  has no such tier (FE-C18 records what it does). Keep or retire the old tier is an owner call.
- The pre-rewrite contract set `filter_candidate_outcomes` retention to 30 days and heavy
  batches to "at most hourly"; code defaults are 45 days
  (`core/aletheia/config/schema.py`, deliberately aligned with `filter_decisions` and enforced by
  `tests/audit/test_alpha_cohort_reachability.py`) and 55 minutes
  (`HEAVY_BATCH_MIN_INTERVAL_SEC`). FE-C07 / FE-C15 record the code values.

## Decision references

- Stage E (2026-07): per-gate evidence and negative-feedback statistics; full narrative in
  [history](../history/filter_evidence_retired_chain.md) and
  [ranker_paper_trader_filter_evidence.md](../history/ranker_paper_trader_filter_evidence.md).
- 2026-09-07 canonical entry cutover: Ranker, PaperTrader, `signal_pending`, Operator
  debate/claim and Opportunity queue rules became historical only.
- #3701: `universe_forward_outcomes` mirror dropped in v94; FCO is canonical.
- 2026-10-10 rewrite: removed the XAUT/PAXG "gold RWA must not be stablecoin-excluded" rule
  as a product statement (RWA perpetuals are blacklisted at registry admission); the code
  fact that gold tokens are not in `_STABLECOIN_BASES` remains and is asserted by `FE-B09`.
  The two settlement-priority descriptions (old §4 and §8) were unified into FE-C18.
