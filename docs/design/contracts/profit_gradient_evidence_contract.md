# Profit Gradient Contract (frozen research tool)

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `PG` · **Owner code:** `core/aletheia/analytics/profit_gradient_evidence.py::build_profit_gradient_evidence`

## Scope

**Frozen research tool, not on the trading mainline.** Current behavior in one line: offline
`aletheia inspect` commands compile a fixed-horizon candidate funnel, a first-60-minute
exploratory counterfactual registry and a seven-layer Profit Gradient report from durable
facts, and nothing in the runtime consumes the result. No confirmatory layer mapping is
registered, so the gradient's `layer_samples` are always empty and only exploratory output
exists. The tool is kept as-is (no extension); its outcome units (24h net return, 60-minute
counterfactual) are not learning inputs. Decision-level learning and counterfactual labels
are owned by [spec 0001](../specs/0001-learning-loop/spec.md) (judgment R from
`core/aletheia/analytics/signal_replay.py`). Per-trade replay and story are owned by the
[Position Replay and Lifecycle Story Contract](profit_loop_observability_contract.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/cli/inspect_cmd.py::candidate_facts` | CLI `aletheia inspect candidate-facts --horizon <15m\|1h\|4h\|24h> [--as-of <ts>]` | owner / offline analysis |
| `core/aletheia/cli/inspect_cmd.py::candidate_diagnosis` | CLI `aletheia inspect candidate-diagnosis` | owner |
| `core/aletheia/cli/inspect_cmd.py::profit_counterfactual` | CLI `aletheia inspect profit-counterfactual --id <position> --cost-model <json>` | owner |
| `core/aletheia/cli/inspect_cmd.py::profit_gradient_evidence` | CLI `aletheia inspect profit-gradient-evidence --epoch --cutoff --cost-model-id --output` | owner |
| `core/aletheia/cli/inspect_cmd.py::profit_gradient` | CLI `aletheia inspect profit-gradient --evidence <file>` | owner |
| `extensions/learning/evidence_epoch.py::ensure_evidence_epochs` | call at Gateway startup | `core/aletheia/server/app.py` (after a durable Runtime Session) |
| `core/aletheia/cli/control_cmd.py` (profit-loop section) | `aletheia control status` | owner |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `learning_policy_epochs` row, `epoch_kind=evidence_anchor:profit_loop_observability` | `extensions/learning/evidence_epoch.py::ensure_evidence_epochs` | counterfactual registry, evidence compiler, control status | immutable identity; ended on identity change |
| `--output` JSON file | `core/aletheia/cli/inspect_cmd.py::profit_gradient_evidence` | `inspect profit-gradient` | owner file, never persisted in a database |

No replay, diagnosis, counterfactual, gradient, manifest or protected-retention table exists.

## Requirements

### Candidate funnel

#### PG-C01 Complete fixed-horizon denominator

`When candidate-facts runs, the system shall emit one candidate_fact per candidate at exactly one of the 15m, 1h, 4h or 24h horizons, preserving filtered, skipped, rejected, unfilled and traded terminal states, and shall reject any other horizon.`

- WHEN `--horizon 2h` is given THEN the command fails with a parameter error.
- WHEN a candidate was filtered THEN it appears in the denominator with its terminal state, priced on the frozen ex-ante analysis basis, never on downstream Proposal/Position capital.

Evidence: `core/aletheia/analytics/candidate_facts.py::build_candidate_facts` (`_FIXED_HORIZONS`).

#### PG-C02 Candidate and Position populations stay separate

`The system shall never pool fixed-horizon candidate outcomes (keyed by outcome_candidate_key) with variable-horizon settled Position economics (keyed by position_id).`

#### PG-C03 Diagnosis is read-only

`The candidate-diagnosis artifact and the opportunity audit --view entry-exit root shall declare read_only=true and behavior_changed=false and shall not change their inputs.`

Evidence: `core/aletheia/analytics/profit_loop_diagnosis.py::diagnose_candidate_report`.

### Evidence anchor

#### PG-C04 Anchor identity comes from the running session

`When the Gateway starts with a durable Runtime Session, the system shall register or reuse one evidence_anchor:profit_loop_observability epoch whose identity is the session boot commit, behavior fingerprint and the canonical SHA-256 of the counterfactual registry, and an offline --commit override shall not supply that identity.`

- WHEN the Runtime Session is not durable THEN registration is skipped with a warning and no epoch is written.
- WHEN commit, fingerprint or registry hash changes THEN a new epoch with a new 30-day window starts.

Evidence: `extensions/learning/evidence_epoch.py::_profit_loop_effective_values`, `ensure_evidence_epochs`.

### Counterfactual registry

#### PG-C05 Finite, locked, first-60-minute registry

`The counterfactual registry shall compare the initial-Stop baseline with exactly five treatments (delay_1_bar, delay_3_bars, delay_5_bars, breakeven_after_1r, thesis_invalidation_exit) over 60 one-minute bars after each arm's hypothetical entry, and shall label every result exploratory with promotion_eligible=false.`

- WHEN a delay arm runs THEN it also reads its delay prefix and enters at the next bar open; invalidation during the delay yields `no_trade`.
- WHEN a result is better early THEN it is not a rule that Positions should exit within one hour (`counterfactual_scope=first_60_minutes` is an evidence boundary).

Evidence: `core/aletheia/analytics/profit_loop_counterfactual.py` (`COUNTERFACTUAL_SCOPE`, `_TREATMENTS`, `evaluate_counterfactual_registry`).

#### PG-C06 Exactly one registered anchor, fixed cutoff

`When the registry is evaluated, it shall resolve exactly one anchor matching the complete producer commit, behavior fingerprint and registry hash, use started_at + 30 days as the only cutoff, and return not_evaluable for missing, mixed, conflicting or early-ended identity.`

- WHEN the epoch ended before its cutoff THEN the reason is `registered_epoch_ended_before_cutoff`.
- WHEN the Position opened before registration or closed after the cutoff THEN the reason is `registered_cohort_eligibility_failed`.

Evidence: `core/aletheia/analytics/profit_loop_counterfactual.py::resolve_profit_loop_registration`.

#### PG-C07 Explicit cost model and frozen inputs

`If the cost model lacks a model ID, verification time, source-evidence SHA-256, fee, funding or slippage assumptions, or the Position fact lacks a frozen position_hypothesis, then the registry shall return not_evaluable.`

- WHEN the cost model is invalid THEN the reason is `verified_cost_model_required`; WHEN the hypothesis is missing THEN `frozen_inputs_unavailable`.

### Evidence compiler

#### PG-C08 Source-bound, side-effect-free compiler

`When profit-gradient-evidence runs, the compiler shall accept only epoch, cutoff and cost-model selectors, read one SQLite snapshot without writes, network or model calls, and emit profit_gradient_evidence/v2 with a content_hash over the complete artifact.`

Evidence: `core/aletheia/analytics/profit_gradient_evidence.py::build_profit_gradient_evidence` (`PROFIT_GRADIENT_EVIDENCE_CONTRACT_VERSION`).

#### PG-C09 Cutoff-visible evidence only

`The compiler shall admit a candidate outcome only on the 24h net-return contract with an analysis_notional_quote basis and matching producer/settlement identities, Position economics only when materialized by the cutoff, and OHLCV rows only when first_persisted_at and last_mutated_at are at or before the cutoff.`

- WHEN a funding settlement lands after the cutoff THEN the older replay is not rewritten; the value is `unavailable`.

#### PG-C10 Empty confirmatory mapping

`The compiler shall leave every layer path in _LAYER_PATH_IDS empty and shall emit the counterfactual registry only under exploratory_evidence with promotion_eligible=false, never in layer_samples.`

Evidence: `core/aletheia/analytics/profit_gradient_evidence.py` (`_LAYER_PATH_IDS`).

### Gradient selector

#### PG-C11 Only verified v2 artifacts are adjudicated

`When profit-gradient reads an evidence file, the CLI shall reject any contract_version other than profit_gradient_evidence/v2 and any artifact whose rebuilt sources do not reproduce its content_hash, before evaluating samples.`

Evidence: `core/aletheia/cli/inspect_cmd.py::profit_gradient`, `core/aletheia/analytics/profit_gradient_evidence.py::verify_profit_gradient_evidence`.

#### PG-C12 Seven layers, method-specific gates

`The profit_gradient/v3 selector shall report Selection, Decision, Risk, Execution, Holding, Exit and Costs under one declared comparison method and shall apply coverage, epoch, unit-integrity, clustered-interval, practical-effect, temporal-stability, concentration and Risk-layer hard gates plus the method gate.`

- WHEN the method is `prospective_randomized` THEN ≥ 30 independent clusters and ≥ 20 in the smaller arm are required.
- WHEN the method is `paired_same_unit`, `deterministic_decomposition` or `directional_observational` THEN ≥ 30 clusters and 30 unique units are required; the last two never produce a winner.
- WHEN the method is missing or unknown THEN the evidence is ineligible.

Evidence: `core/aletheia/analytics/profit_gradient.py` (method profiles, `PROFIT_GRADIENT_CONTRACT_VERSION`), `core/aletheia/analytics/profit_gradient_estimators.py`.

#### PG-C13 Exact reconciliation

`The selector shall admit a sample only with an exclusive or residual profit_gradient/reconciliation/v1 declaration that sums to the declared quote and basis-point total within 1e-8, and shall reject a shared cost event across layers.`

- WHEN Costs has no persisted realizable alternative THEN the layer is `unavailable`, not counted beside Execution.

Evidence: `core/aletheia/analytics/profit_gradient.py` (`RECONCILIATION_CONTRACT_VERSION`, `RECONCILIATION_TOLERANCE_QUOTE`).

#### PG-C14 Unique winner never authorizes change

`The selector shall return unique_max_direction only when exactly one layer clears every gate and its lower bound exceeds every runner-up upper bound, else no_unique_direction, and automatic_behavior_change_authorized shall always be false.`

Evidence: `core/aletheia/analytics/profit_gradient.py::select_unique_max_direction`.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Identity, cutoff, cost model or sample evidence missing | return explicit reason; no fallback fact, no mutation | `not_evaluable` / `ineligible` reason tokens | `unavailable`, never `0` |
| Evidence file not v2 or hash mismatch | CLI parameter error | Typer `BadParameter` | — |
| Anchor registration fails at startup | logged warning, Gateway continues | `Evidence epoch registration incomplete` log | no epoch |

## Authority

None: outputs never feed the Entry Planner, Risk, Execution, Learning or an experiment
cohort, and no result may select, start or authorize a change.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| PG-C01, PG-C02 | `tests/test_candidate_facts.py` |
| PG-C03 | `tests/test_profit_loop_diagnosis.py` |
| PG-C04 | `tests/test_p3_evidence_epoch.py`, `tests/test_evidence_epoch_cli.py` |
| PG-C05 – PG-C07 | `tests/test_profit_loop_counterfactual.py` |
| PG-C08 – PG-C10 | `tests/test_profit_gradient_evidence.py`, `tests/test_profit_gradient_evidence_properties.py` |
| PG-C11 | `tests/test_profit_loop_cli.py` |
| PG-C12 – PG-C14 | `tests/test_profit_gradient.py`, `tests/test_profit_gradient_properties.py` |

## Known conformance gaps

- Mainline Universe trades never carry `position_hypothesis` (see PL-C06 gap in the
  [Position Replay Contract](profit_loop_observability_contract.md#known-conformance-gaps)),
  so PG-C07 returns `frozen_inputs_unavailable` for them.

## Decision references

- Long-form v3 method and reconciliation text, status line and moved runtime notes:
  [history](../history/profit_loop_and_rationale_retired_text.md).
- Product plan: [position replay / profit gradient proposal](../proposals/position_replay_profit_gradient.md).
- Owner 2026-10-10: the AI trades like a person, not a factor miner; this tool is frozen and
  whether to delete it is a separate owner decision (D2).
