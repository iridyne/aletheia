---
schema_version: 2
scope_id: slim-core-bitget-http-v1
status: active
authorized_extensions:
  cn_equity:
    proposal: docs/design/proposals/cn-equity-market-kernel.md
    tracking_issue: 2572
    implementation_state: on_hold_not_implemented
    default_runtime_state: disabled
    permitted_runtime_states:
      - disabled
      - observe
      - shadow
    live_allowed: false
instrument_profiles:
  crypto:
    asset_class: crypto
    market_types:
      - perp
execution_modes:
  - bitget_demo
decision_authority: aletheia.agent_shell.entry_planner.system_operator_entry_planner
risk_authority: aletheia.risk.engine.evaluate_proposal
execution_entrypoint: aletheia.execution.gateway.execute_existing_proposal
outcome_entrypoint: aletheia.portfolio.outcome_closure.close_position_verified
learning_measurement: enabled
learning_influence_default: enabled_owner_gated
learning_authority_gate: extensions.learning.experiments.apply_numeric_treatment
causal_chain:
  - opportunity
  - system_operator_decision
  - proposal
  - risk_decision
  - order
  - fill
  - position
  - genuine_outcome
---
# Active Runtime Scope

生产交易策略不得由固定 RuleSet、SignalEngine 或自动生成规则驱动；System Operator 是唯一策略决策主体，确定性规则仅用于数据校验、成本预筛、风险和执行。

This file is the single machine-readable declaration of the trading path that is
allowed to count as the active MVP mainline. It narrows the broader product map;
it does not describe every research, observation, reconciliation, or historical
compatibility path in the repository.

The accepted [Slim Product boundary](CAPABILITY_GROUND_TRUTH.md), under
[#3134](https://github.com/iridyne/aletheia/issues/3134), is exchange/provider-agnostic
Core plus the active Bitget Exchange Extension and configured HTTP Model Endpoints.
Crypto USDT perpetuals on Bitget Demo are the current qualification route; spot
is outside the product scope and is not planned (owner decision 2026-10-09).
OKX/Binance and the formerly registered stock-linked profile are frozen, not
active Runtime support. Their historical account/instrument identities remain
readable and retain their original economics; this declaration does not rewrite
history or remove existing exposure-recovery obligations.

The existing startup gate consumes `execution_modes` above: frozen routes do not
gain new-entry admission simply because an adapter still exists. This declaration
does not prove credentials, Golden Path completion, deployment or economic
qualification. With no usable Exchange Extension, trading remains unavailable
while Core diagnostics/history must remain accessible. Live remains outside
this scope. The former simulator scope is [archived](../design/history/local_simulated_runtime_scope.md).

For Universe-linked entry, the same System Operator authority is expressed as
Candidate → canonical Planning Cycle → immutable Entry Plan → Proposal. All new
typed Candidates use one bounded Context/Hypothesis/Compiler implementation;
historical numeric planner labels are compatibility data, not runtime selectors.
Entry Plan admission follows the registered capability/permission map. The current
Bitget Demo route requires Full permission; registering an account in this document
does not enable Entry Plan on another route. Observe, unsupported routes and any
effective Live state fail closed before model invocation.

The `authorized_extensions.cn_equity` declaration is **on hold** (owner
2026-10-10): no CN implementation work is admitted until the hold is lifted. When
lifted, it admits only the bounded implementation work approved by its Proposal. It does not add CN Equity to the
active instrument profiles or current Runtime proof. CN starts semantically
`disabled`: no CN V2 producer, Candidate, Proposal, reservation, Order, Fill,
Position, Outcome, or qualification fact may be created until the corresponding
Contract, executable SpecClaim, feature state, and preceding Phase Gate are
merged. Observation and Shadow grant no executable approval, account reservation,
order submission, fill or account mutation authority. No `live` state,
broker route, cross-currency netting, or support claim is admitted here.

Learning measurement, lifecycle automation, and the sole measured Paper influence
path are enabled by default. A Learning result can affect a later decision only
after explicit owner trial admission and through the declared authority gate, with
a neutral fallback when prospective evidence is missing or invalid. It never gains
direct risk, execution, credential, or live-unlock authority.

## Enforced boundaries

- Scanner and observer code may create evidence, opportunities, and pending
signals; it cannot submit or execute an order.
- The System Operator is the only native entry decision maker. Entry Plan execution
creates immutable Order Intent and enters the Execution Gateway for Risk approval
and UTA submission.
- `execution_entrypoint` is the production entry: Entry Plan execution persists the
Proposal and calls `execute_existing_proposal`. `submit_proposal` (create Proposal,
then `execute_existing_proposal`) has one explicitly debug-only caller, the CLI
`aletheia dev test-order`, which refuses every non-demo execution mode before
creating a Proposal. Both pass through the same Risk and Gateway path.
- The Risk Engine is the only approval authority. Guard output is advisory or a
pre-risk candidate filter and cannot replace Risk Engine approval.
- Frontend and Operator surfaces cannot call an exchange order adapter directly.
- A native active-scope position must retain its opportunity, proposal, order, and
risk-decision ancestry. A close counts as a genuine outcome only when Outcome
Integrity has verified its economic evidence.
- Paper-candidate blockers must be declared in
`core/aletheia/trust/behavior_authority.py` and pass its consumer-side gate. A lesson,
historical auto-guard, or inline historical ranker condition has no block authority.

The executable checks live in `tests/specs/spec_active_runtime_scope.py`, the
external-exchange Golden Path and entry/close pipeline tests, and production
reachability tests. They establish only their declared software claims.
`runtime_replay_v1` is an [archived simulator pack](../design/history/local_simulated_replay.md),
not current runtime or external-account qualification evidence.
