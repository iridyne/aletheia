---
schema_version: 1
scope_id: crypto-paper-mainline-v1
status: active
asset_classes:
  - crypto
market_types:
  - spot
  - perp
execution_modes:
  - paper_internal
decision_authority: aletheia.agent_shell.trading_decision.evaluate_trading_opportunity
risk_authority: aletheia.risk.engine.evaluate_proposal
execution_entrypoint: aletheia.execution.gateway.submit_proposal
outcome_entrypoint: aletheia.portfolio.outcome_closure.close_position_verified
learning_measurement: enabled
learning_influence_default: disabled
learning_authority_gate: aletheia.learning.experiments.apply_numeric_treatment
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

This file is the single machine-readable declaration of the trading path that is
allowed to count as the active MVP mainline. It narrows the broader product map;
it does not describe every research, observation, reconciliation, or historical
compatibility path in the repository.

The active asset class is crypto. Spot and perpetual instruments share one
System Operator, one Risk Engine, and one Execution Gateway. The active execution
mode is the local `paper_internal` simulator. Exchange demo modes remain supported
adapters, but are not part of this active-scope proof until an owner-selected demo
mode is added here with matching Golden Path and replay evidence.

Learning may keep measuring outcomes and recording shadow/control exposure while
behavioural influence is disabled by default. A Learning result can affect a later
decision only through the declared authority gate, with a neutral fallback when
prospective evidence is missing or invalid. It never gains direct risk, execution,
credential, or live-unlock authority.

## Enforced boundaries

- Scanner and observer code may create evidence, opportunities, and pending
  signals; it cannot submit or execute an order.
- The System Operator is the only native entry decision maker. It submits a paper
  proposal through the paper wrapper, which reaches the Execution Gateway.
- The Risk Engine is the only approval authority. Guard output is advisory or a
  pre-risk candidate filter and cannot replace Risk Engine approval.
- Frontend and Operator surfaces cannot call an exchange order adapter directly.
- A native active-scope position must retain its opportunity, proposal, order, and
  risk-decision ancestry. A close counts as a genuine outcome only when Outcome
  Integrity has verified its economic evidence.
- Paper-candidate blockers must be declared in
  `aletheia/trust/behavior_authority.py` and pass its consumer-side gate. A lesson,
  historical auto-guard, or inline historical ranker condition has no block authority.

The executable proof lives in `tests/specs/spec_active_runtime_scope.py`, the
Golden Path test, the production reachability tests, and the runtime replay pack.
