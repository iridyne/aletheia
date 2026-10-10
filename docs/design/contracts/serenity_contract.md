# Serenity Lens Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `SE` · **Owner code:** `core/aletheia/trading/serenity_evidence.py`, `core/aletheia/trading/serenity_scoring.py`, `core/aletheia/trading/serenity_publication.py`, `extensions/research/research_worker.py`
>
> **Status: Experimental, default disabled.** `SerenityConfig.enabled=False` and
> `auto_promote=False` (`core/aletheia/config/schema.py::SerenityConfig`). The only
> current publication entry is `publish_serenity_candidates`, reached from the manual route
> `core/aletheia/server/routes/data.py::promote_serenity_theme` or from the optional research
> worker's auto-promotion. Changing a trust requirement below requires change control.

## §1 Scope

Serenity Lens is a deep-research lens: multi-lens LLM research on one instrument, a
deterministic evidence-tier evaluator, a researchability grade, a forced verdict and a
scorecard, ending in a `SerenityOpportunity`. Through real price-action (PA) admission it can
publish a durable Candidate into the same mainline as the Universe (Candidate → Planner →
Compiler → EntryPlan → Risk → Gateway → Position), with a long holding horizon that the
position layer honours by `trade_school`. It does not own Entry Plan geometry
([Entry Plan contract](entry-plan-contract.md)), Risk or Live unlock, and it is a second,
optional Candidate source; the product trades only USDT-margined perpetuals on `bitget_demo`.
Close-time review (`SerenityPostMortem`) is a learning path; its replacement by one uniform
decision record is [spec 0001](../specs/0001-learning-loop/spec.md) LL-R01.

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/trading/serenity_publication.py::publish_serenity_candidates` | call | `core/aletheia/server/routes/data.py::promote_serenity_theme`; `extensions/research/research_worker.py::_auto_promote_serenity_opportunities` |
| `extensions/research/research_worker.py::enqueue_research_run` | scheduler callback | `dreaming_daily` and `serenity_startup_catchup` jobs (only when `serenity.enabled`), and the create/retry/deep-dive API |
| `core/aletheia/trading/serenity_evidence.py::assess_evidence_tier` / `assess_opportunity_tier` / `assess_researchability` | call | `research_worker`, `extensions/research/prediction_observer.py` |
| `core/aletheia/trading/serenity_scoring.py::score_serenity_opportunity` | call | `research_worker`, publication |
| `core/aletheia/portfolio/position_manager.py` `trade_school=="serenity"` branches | call | `position_manager` job |

## Durable state

| Table / model | Writer | Readers | Mutability |
|---|---|---|---|
| `serenity_research_runs` | `research_worker` (CAS) and the cancel API | status/list/detail API, startup reconcile | monotonic status (SE-C13) |
| `serenity_run_events` | `research_worker` | progress surface | append-only |
| `SerenityOpportunity` (`assessed_tier`, `researchability_grade`, `verdict_json`) | `research_worker` | scoring, publication, `position_manager` thesis check | replaceable research record |
| `SerenityEvidence` (`agent_lens`, `assessed_tier`, `assessed_by`) | `research_worker` | evaluator, scoring | append-only |
| Candidate snapshot / member / PlanningGeneration (source `serenity`) | `publish_serenity_candidates` via Core `publish_candidate_snapshot` | Entry Planner | immutable birth context |
| `TradeProposal.trade_school`, `Position.trade_school` (default `short_term`) | Compiler / Gateway | `position_manager`, attribution | frozen at birth |
| `SerenityPostMortem` | `extensions/learning/outcome_feedback.py::_close_serenity_feedback` | research readers | append-only |

## Requirements

### §2 Trust invariants

#### SE-C01 Generator is not evaluator

`The evaluator in serenity_evidence.py shall assign the authoritative assessed_tier that gates scoring and promotion, and it shall only lower, never raise, the generator's self-graded tier.`

- WHEN the LLM self-grades `disclosed` THEN the stored `evidence_tier` is advisory and scoring reads `assessed_tier`.
- WHEN `research_worker` or `prediction_observer` creates evidence THEN it calls `assess_evidence_tier` / `assess_opportunity_tier` before persistence.

#### SE-C02 One tier ladder

`The system shall order evidence tiers speculative < likely < high_confidence < disclosed (TIER_LADDER), and normalize_tier shall map legacy or free-text values onto it, flooring unknown or empty values to speculative.`

- Legacy mapping: `verified`→`disclosed`, `credible`→`high_confidence`, `medium`→`likely`, `low`/`weak`/`unsupported`→`speculative`.

#### SE-C03 The evaluator is deterministic

`The evidence evaluator shall use no LLM, database or I/O, and the same input shall always yield the same tier with ASSESSOR_ID "deterministic_v1".`

#### SE-C04 Information vacuum is capped

`If the researchability grade is C, then the score shall be capped at 40, below the research_priority threshold of 52.`

#### SE-C05 Only registered USDT perpetual instruments become Candidates

`If a Serenity opportunity does not resolve to an active universe_registry row for its exact venue, symbol and market_type, then publish_serenity_candidates shall reject it with serenity_exact_instrument_unavailable and publish nothing for that batch.`

- WHEN the research symbol is a bare ticker, a forex/USD pair or an aggregate (MSTR, SPY, GBP/USD, ALTCOINS) THEN `_is_allowed_serenity_symbol` rejects it before persistence.
- A stock-token research classification is never an execution approval.

#### SE-C06 Trust boundary does not see trade_school

`The Live unlock route and the Risk Engine's live-disabled guard shall not read trade_school.`

- `core/aletheia/server/routes/live.py` contains no `trade_school`; it still requires `confirm=true` and keeps `"live_disabled"`.
- `LiveConfig.state` defaults to `live_disabled` and `first_live_confirmation_done` to `False`.
- The Risk Engine reads `trade_school` only to check Plan/Proposal consistency and counts Serenity positions in the shared pool.

#### SE-C07 Attribution has no silent None

`A Serenity Plan and Proposal shall carry source_kind='serenity', source_id (the SerenityOpportunity id), opportunity_id and trade_school='serenity'.`

### §3 Tier vocabulary and evaluator

#### SE-C08 Source ceilings

`The evaluator shall cap prediction_market, ict_methodology, smc_methodology, price_action, llm and model sources at speculative (_SOURCE_CEILING), and cap other listed sources at their SOURCE_RELIABILITY_TIER.`

| Source | Ceiling |
|---|---|
| `news`, `social`, `sentiment`, `analyst`, `research` | `likely` |
| `market_data`, `exchange_data`, `on_chain` | `high_confidence` |
| `official`, `regulatory`, `sec`, `filing` | `disclosed` |
| unlisted | none |

#### SE-C09 Over-claim needs an external reference

`If evidence claims disclosed or high_confidence without a url or source_ref starting with http(s)://, www., sec.gov or ftp://, then the evaluator shall lower it to likely.`

#### SE-C10 Opportunity tier and researchability

`The evaluator shall set an opportunity's gating tier to the strongest assessed_tier among its supports=True evidence (speculative when none), and shall grade researchability A for at least two high_confidence records with a URL, B for at least three records at likely or above, and C otherwise.`

- The opportunity's own self-reported tier is not trusted.

### §4 Scorecard and bucket

#### SE-C11 Score composition

`score_serenity_opportunity shall compute evidence (by assessed_tier) + stage + average + min(10, 0.8 × evidence count) + confidence component − 18 × fail gates − 6 × warn gates − 4 × risk flags, clamp it to 0–100, then apply the researchability ceiling {A:100, B:70, C:40}.`

- Bucket with zero fail gates: `≥72` → `candidate_position`, `≥52` → `research_priority`, otherwise `watch`.
- The output carries `reasons` (authoritative and self tier, stage, raw, grade, ceiling); `make_replay_snapshot_payload()` produces the audit snapshot.

### §5 Research pipeline

#### SE-C12 Multi-lens evidence and forced verdict

`When a research run collects evidence, research_worker shall call one LLM per entry in RESEARCH_LENSES, record each evidence item's agent_lens, emit a lens_synthesis event, and in Stage 4 require a verdict_json with recommendation, bull_case, bear_case and key_assumption.`

- WHEN the run uses the `_ICT_METHODOLOGY_SYSTEM` methodology mode THEN it bypasses multi-lens, and its source is capped at `speculative` (SE-C08).
- A verdict is research evidence, not an executable decision.

### 5.1 Research run lifecycle truth

#### SE-C13 Run lifecycle

`The serenity_research_runs row shall be the attempt lifecycle authority, with queued→running claimed by compare-and-set and terminal outcomes written only WHERE status='running'.`

| Current | Event | Next | `finished_at` |
|---|---|---|---|
| `queued` | cancel before worker claim | `cancelled` | written now |
| `running` | owner requests cancel | `cancel_requested` | empty |
| `cancel_requested` | worker reaches a checkpoint | `cancelled` | written by worker |
| `cancel_requested` | gateway restart, old worker gone | `cancelled` | written by startup reconcile |
| `running` | pipeline ends | its terminal outcome | guarded write |

- `cancel_requested` still holds the single Serenity run slot; repeated cancel is idempotent; after a cancel request the worker skips auto-promotion and keeps artifacts already committed.
- Cancellation is cooperative: it takes effect only between stage/lens/provider calls.

#### SE-C14 Absent worker is unavailable before any write

`If the research worker is not composed, then the create, retry and deep-dive API shall return 503 before writing a profile, snapshot, run or retry, and the scheduler shall not enqueue research.`

- History reads, cancel, startup interrupted-run recovery and publication of existing evidence do not need the worker.

#### SE-C15 Durable progress without percentages

`The status, list and detail API shall expose each run's stage and lastProgressAt from durable state, and shall not report an overall or per-stage percentage.`

### §6–§7 Publication

#### SE-C16 One publication implementation

`The manual route and auto-promotion shall both publish through publish_serenity_candidates, which shall write snapshot, member and current PlanningGeneration through Core publish_candidate_snapshot in one short transaction and only then mark the theme promoted.`

- WHEN the source material hash changed, evidence is missing, the instrument is not exactly resolvable or not eligible, or another source owns the instrument THEN the whole batch rolls back.
- Repeated publication of the same source+material reuses the existing Candidate; the function returns de-duplicated Candidate ids in request order (`new_candidates_only=True` counts only new ones).
- Publication prepares evidence, exact instrument, features and the shared PA evaluator outside the writer transaction, never runs a Universe refresh and never calls a model.

#### SE-C17 Research direction never overrides PA

`If the research direction conflicts with the shared PA evaluator, then publication shall reject with serenity_pa_direction_conflict, and missing Profile or capability evidence shall be unavailable.`

- `serenity_research_v1` / `research_authority` proves source admission only, not Universe Utility.

#### SE-C18 Auto-promotion keeps its score gate

`Where serenity.auto_promote is enabled, the research worker shall publish only children scoring at least serenity.min_score_for_pi (default 72), while manual promotion applies no score gate but the same evidence, risk-gate and PA admission.`

- Promotion only wakes the canonical Planner.

### §8 Holding horizon by trade_school

#### SE-C19 Long holding is honoured

`While a Position has trade_school='serenity', the position manager shall cap its hold at serenity.max_hold_days × 24 hours (default 2160), skip trailing-stop updates, and exit on thesis_invalidated when its research opportunity is no longer valid.`

- WHEN a `short_term` Position passes the 72 h cap THEN it still exits (`max_hold`).
- The Compiler freezes `holding_horizon_hours = serenity.max_hold_days × 24` into the Plan and Proposal; entry timing (4 h/24 h) and entry expiry (60/240 min) stay separate, and the final structural stop determines sizing.
- `TradeProposal.trade_school` and `Position.trade_school` default to `short_term`.

#### SE-C20 Close review is retryable

`When a Serenity Position's close is verified, the outcome-feedback hook shall write a SerenityPostMortem, and if the optional learning component is absent the delivery shall fail and stay retryable without changing confirmed close or economic facts.`

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| research worker not composed | API 503 before writes; no enqueue | `unavailable` | no failed run |
| instrument not in active registry | batch rolled back | `serenity_exact_instrument_unavailable` | — |
| PA unavailable / conflicting | not published | `serenity_pa_unavailable` / `serenity_pa_direction_conflict` | unavailable ≠ rejection |
| source evidence missing | not published | `serenity_source_evidence_unavailable` | — |
| cancel during provider call | takes effect at next checkpoint | `cancel_requested` | — |
| post-mortem component absent | hook fails, retried | outcome hook queue | close facts unchanged |

## Authority

Serenity may write research records and publish Candidates through the shared admission path;
it may never bypass PA, Planner, Compiler, Risk or Gateway, and `trade_school` never reaches the
Live unlock surface or the Risk Engine's live-disabled guard.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| SE-C01 | `tests/specs/spec_serenity_evidence.py` (SE-C01, SE-S01–S03, SE-I01–I05) |
| SE-C02, SE-C08, SE-C09 | `tests/specs/spec_serenity_evidence.py` (SE-C02, SE-S06, SE-I06) |
| SE-C03 | `tests/specs/spec_serenity_evidence.py` (SE-S01) |
| SE-C04, SE-C10 | `tests/specs/spec_serenity_researchability.py` (SR-S01–S03, SR-B01, SR-B02) |
| SE-C05 | `tests/specs/spec_serenity_evidence.py` (SE-S07, SE-I07); `tests/test_serenity_manual_opportunity_evidence_gate.py` |
| SE-C06 | `tests/specs/spec_serenity_live_readiness.py` (LR-S06) |
| SE-C07 | `tests/test_entry_plan_execution.py`; `tests/specs/spec_serenity_attribution.py` (historical) |
| SE-C11 | `tests/specs/spec_serenity_evidence.py` (SE-C01) |
| SE-C12 | `tests/specs/spec_serenity_multi_lens.py` (ML-S01–S03, ML-B01–B03); `tests/specs/spec_serenity_verdict.py` (SV-S01, SV-S02, SV-B01) |
| SE-C13, SE-C15 | `tests/test_serenity_workbench.py`, `tests/test_serenity_scheduler_continuity.py` |
| SE-C14 | `tests/test_serenity_routes.py` |
| SE-C16, SE-C17, SE-C18 | `tests/specs/spec_serenity_promote.py` (SP-S01–S07, SP-B01–B04); `tests/test_serenity_p0_p3_e2e.py` |
| SE-C19 | `tests/specs/spec_serenity_holding.py` (SH-B01–B03) |
| SE-C20 | `tests/test_serenity_p0_p3_e2e.py` |

Claim ids `SE-C01`/`SE-C02` in `spec_serenity_evidence.py` predate this numbering and coincide
with SE-C01/SE-C02 here by meaning.

## Known conformance gaps

- **Symbol filter is wider than product scope.** `_ALLOWED_QUOTE_CURRENCIES` in
  `extensions/research/research_worker.py` is `{USDT, USDC, BTC, ETH, BUSD}` and
  `_is_allowed_serenity_symbol` deliberately accepts stock tokens, which `research_worker` tags
  `asset_class='equity_tradeable'` with a comment that they "can execute". Spot cross pairs such
  as `ETH/BTC` and tokenised equities are outside the USDT-perpetual scope. SE-C05 still holds at
  publication (exact active registry row; RWA perpetuals are blacklisted at registration), so no
  entry authority results, but the persisted research set is wider than the product.
- **Stock-token research lens.** `research_worker` adds an `equity_chain` lens when
  `observed_equities` / `observed_stock_tokens` are configured — research effort on an
  out-of-scope instrument class.
- **Funding cost of long holds is not stated.** A 90-day perpetual hold accrues funding; this
  Contract has no requirement that Serenity outcomes be cost-complete. Outcome economics are owned
  by [opportunity_outcome_contract.md](opportunity_outcome_contract.md) and
  [derivatives_economic_semantics.md](derivatives_economic_semantics.md).
- **Second review write path.** `SerenityPostMortem` is a separate close-review record from the
  uniform decision record of spec 0001 LL-R01.
- **Scoring weights are hard-coded.** `tier_score_map`, `stage_score_map`, `avg_score_map` and
  `_RESEARCHABILITY_CEILING` are constants in `serenity_scoring.py`; behaviour parameters
  (`max_hold_days`, `auto_promote`, `min_score_for_pi`) are configurable.

## Decision references

- D2 generator ≠ evaluator: owner decision 2026-06-27. Source reliability tiers: S3, 2026-06-28.
  Canonical Candidate entry for Serenity: 2026-09-07. Thesis-invalidation auto-close: D5 option 2.
- Retired Option A promotion chain, stock-token execution section, historical spec mapping and
  the former header: [Serenity history](../history/serenity_contract_retired_sections.md).
- Related: [decision_lifecycle_contract.md](decision_lifecycle_contract.md),
  [knowledge_contract.md](knowledge_contract.md), [risk_engine_contract.md](risk_engine_contract.md),
  [live_unlock_contract.md](live_unlock_contract.md), [data_model.md](../data/data_model.md).
