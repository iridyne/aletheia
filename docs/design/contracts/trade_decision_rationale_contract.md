# Trade Decision Rationale Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `TR` · **Owner code:** `core/aletheia/portfolio/pnl.py::build_exit_reasoning` (close) and `core/aletheia/execution/entry_plan_execution.py::build_entry_plan_order_intent` (entry)

## Scope

Every trade leaves a readable, verifiable record of why it was opened and why it was
closed, without inventing a source. Entry rationale is the Entry Planner's frozen
`EntryHypothesis` carried by the immutable Entry Plan and its Proposal; close rationale is
`positions.exit_reasoning`, written only once the close is a verified fact. This contract
supplements the [Entry Plan](entry-plan-contract.md) and [execution](execution_contract.md)
contracts; it does not change Risk approval, order routing or Live permission.

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/execution/entry_plan_execution.py::build_entry_plan_order_intent` | call | Entry Plan execution (`execute_entry_plan`) |
| `core/aletheia/portfolio/pnl.py::close_position` | call | verified close materialization (`core/aletheia/execution/gateway.py`, `core/aletheia/execution/exchange_materialization.py`) |
| `core/aletheia/portfolio/position_manager.py::close_position_routed` | call | System Operator portfolio review / owner close |
| `core/aletheia/execution/exchange_materialization.py::_close_reasoning_for_order` | call | pending Close Order materialization |
| `frontend/src/components/CausalChain.tsx` | UI | Trades & Positions Causal Chain |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `entry_plans.entry_hypothesis_json` / `entry_hypothesis_hash` | `core/aletheia/storage/repositories.py::complete_candidate_planning_with_plan` | Entry Plan execution, replay, story | immutable |
| `trade_proposals.agent_rationale`, `attribution_snapshot` | `core/aletheia/storage/repositories.py::insert_trade_proposal` | Risk, materialization, replay, UI | immutable, inside the Proposal hash |
| `positions.decision_reasoning` | `core/aletheia/execution/exchange_materialization.py::_attribution_fields` | UI, post-trade review | set at open |
| `orders.close_reasoning` | `core/aletheia/execution/coordinator.py::_claim_close` | close materialization | pending carrier; `NULL` = not recorded |
| `positions.exit_reasoning` | `core/aletheia/portfolio/pnl.py::close_position`, `persist_exit_reasoning` | UI, post-trade review | written at verified close; replaced only on an already-closed Position |

## Requirements

### Entry rationale

#### TR-C01 The hypothesis is the only entry rationale

`The Entry Planner's EntryHypothesis shall be the only source of entry rationale and shall carry only qualitative fields (decision, reason code, mechanism, direction, anchor refs, entry profile, capture priority and reason, risk tier and intent posture, horizon, supporting/opposing/learning evidence refs), never a price, quantity, leverage, stop or target value.`

- WHEN a model output contains an unknown field THEN validation fails (`extra="forbid"`).

Evidence: `core/aletheia/trading/entry_analysis.py::EntryHypothesis`.

#### TR-C02 Frozen hypothesis in the Entry Plan

`When an Entry Plan carries provenance, the plan shall store the validated hypothesis as entry_hypothesis_json with its SHA-256 entry_hypothesis_hash and shall reject a hash mismatch or partially present provenance.`

- WHEN the hash differs from `sha256(entry_hypothesis_json)` THEN the plan fails with `entry_plan_hypothesis_hash_mismatch`.
- WHEN only some provenance fields are present THEN it fails with `entry_plan_provenance_incomplete`.

Evidence: `core/aletheia/trading/entry_plan.py::EntryPlan` (`_contract_is_coherent`).

#### TR-C03 No Proposal without hypothesis provenance

`If an Entry Plan lacks entry_hypothesis_json or source_kind, then the system shall not build an order intent or Proposal for it.`

- WHEN `entry_hypothesis_json` is `None` THEN `build_entry_plan_order_intent` raises `ai_entry_plan_provenance_required`.

Evidence: `core/aletheia/execution/entry_plan_execution.py::_require_ai_entry_authority`.

#### TR-C04 Proposal back-links the hypothesis

`When an Entry Plan Proposal is built, it shall set agent_rationale to plan.thesis and put entry_plan_id and entry_hypothesis_hash into the hash-covered attribution_snapshot, and the opened Position shall carry that agent_rationale as decision_reasoning.`

- WHEN the Proposal is stored THEN `commit_hash` covers `attribution_snapshot` (only `status` and `commit_hash` are excluded).
- WHEN reading `agent_rationale` THEN it is the compiler label `"<mechanism> grounded entry"`, not model prose; the model's own words are in the hypothesis (`capture_reason`, `risk_intent.reason`).

Evidence: `core/aletheia/execution/entry_plan_execution.py::build_entry_plan_order_intent`, `core/aletheia/trading/entry_plan_compiler.py::_compile_bounded_risk_envelope` (sets `thesis`), `core/aletheia/storage/repositories.py` (`_PROPOSAL_HASH_EXCLUDES`), `core/aletheia/storage/repositories_execution.py::_compute_proposal_hash`, `core/aletheia/execution/exchange_materialization.py::_attribution_fields`.

#### TR-C05 Numbers are attributed to the compiler

`Readers shall attribute entry, stop, target, leverage and quantity to the deterministic compiler and sizing (with capture_policy_id), never label them as decided by the AI.`

#### TR-C06 Retired five-item entry parameters are history only

`The system shall not write or synthesize agent_rationale.entry_parameters for any new trade; historical rows shall be shown as stored and as not recorded when absent.`

- WHEN grepping Python sources for `entry_parameters` THEN there is no writer.

History: [retired entry lifecycle](../history/decision_lifecycle_retired_entry.md).

### Close rationale

#### TR-C07 Close rationale only after a verified close

`When a close fact is written, the system shall store positions.exit_reasoning as trade_decision_rationale.v1 with version, actor, source, reason_code, reason, is_ai_judgment and trigger, and a later AI-specific rewrite shall only apply to an already-closed Position.`

- WHEN `persist_exit_reasoning` is called on a Position whose status is not `closed` THEN it returns `False` and writes nothing.

Evidence: `core/aletheia/portfolio/pnl.py::build_exit_reasoning`, `close_position`, `persist_exit_reasoning`.

#### TR-C08 Rule triggers are described as rules

`When a Position closes by stop_loss, take_profit, max_hold or market_signal_exit, the rationale shall describe an automatic rule (not an AI judgment) and record the committed stop/target price, the opened_at time, or the last market-signal reason in trigger.`

- WHEN the reason is `verified_exit` or `reconciled_gone` THEN the reason states closure from reconciliation evidence and invents no price, PnL or AI motive.

#### TR-C09 Real actor and AI flag

`The rationale shall name the real actor (system_operator, system, human or position_reconciler) and shall set is_ai_judgment=true only when the actor is system_operator with its original review text.`

- WHEN the System Operator closes via portfolio review THEN `actor=system_operator`, `source=portfolio_review`, `reason` is its original text (≤ 1200 chars) and `is_ai_judgment=true`.

Evidence: `core/aletheia/portfolio/position_manager.py::close_position_routed`.

#### TR-C10 Pending close carrier

`When an asynchronous exchange close has real Operator text, the system shall save it as orders.close_reasoning on the pending Close Order, promote it to positions.exit_reasoning only after the full private Fill settles, and fail materialization closed if the carrier is malformed, mismatches close_reason, or conflicts actor with the AI flag.`

- WHEN `close_reasoning.reason_code != orders.close_reason` THEN materialization raises `MaterializationIntegrityError`.
- WHEN `close_reasoning` is `NULL` THEN the generic rule rationale is written and the carrier means not recorded.

Evidence: `core/aletheia/execution/exchange_materialization.py::_close_reasoning_for_order`.

### Display

#### TR-C11 Missing means not recorded

`If a historical or external record has no rationale, then the API and UI shall show not recorded and shall not reconstruct a story from final price, PnL or close_reason.`

#### TR-C12 Causal Chain reads persisted fields

`The Causal Chain shall read close rationale only from the resulting Position's exit_reasoning, resolved through positions.order_id, and shall create no link or inferred fill.`

Evidence: `frontend/src/components/CausalChain.tsx`.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Entry Plan without hypothesis/source | no order intent | `ai_entry_plan_provenance_required` | no Proposal |
| Hypothesis hash mismatch / partial provenance | plan rejected | `entry_plan_hypothesis_hash_mismatch` / `entry_plan_provenance_incomplete` | — |
| Malformed or mismatched close carrier | materialization fails closed | `MaterializationIntegrityError` | Position not closed by that write |
| Rationale absent | displayed as missing | `not recorded` | never a synthesized story |

## Authority

Records rationale only; it may never change Risk approval, order routing, close decisions,
credentials or Live permission.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| TR-C01 – TR-C03 | `tests/test_entry_plan_execution.py`, `tests/test_entry_activation.py` |
| TR-C04 | `tests/specs/spec_pipeline_entry_enrichment.py` (`PL-ENRICH-02`); no focused test asserts the `entry_hypothesis_hash` back-link |
| TR-C06, TR-C11, TR-C12 | `tests/test_trade_decision_rationale_ui.py` |
| TR-C07 – TR-C09 | `tests/specs/spec_pipeline_multibroker_reconciliation.py`, `tests/test_exchange_close_confirmation.py` |
| TR-C10 | `tests/specs/spec_pipeline_exchange_materialization.py` (`test_invalid_pending_close_reasoning_*`, `test_legacy_null_pending_close_reasoning_keeps_generic_final_provenance`) |

## Known conformance gaps

- **TR-C01/TR-C12 entry side of the Causal Chain.** `frontend/src/components/CausalChain.tsx`
  still reads only `meta.entry_parameters` for Entry Reasoning, which no Python code writes,
  so every Entry Plan trade shows the entry rationale as missing. The UI should read the Entry
  Plan hypothesis; `tests/test_trade_decision_rationale_ui.py` asserts the
  `entry_parameters` string and must change with it.
- **TR-C03 has no focused regression.** No test builds an order intent from an Entry Plan
  without `entry_hypothesis_json` and asserts `ai_entry_plan_provenance_required`.

## Decision references

- Retired PiDecision five-item entry rationale and its old verification line:
  [retired entry lifecycle](../history/decision_lifecycle_retired_entry.md),
  [moved text](../history/profit_loop_and_rationale_retired_text.md).
- [Spec 0001](../specs/0001-learning-loop/spec.md) LL-R01 adds `p_target_first` and
  `lessons_considered` to the same `EntryHypothesis` carrier when implemented.
