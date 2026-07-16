# Aletheia Trading OS — Product Direction

> **Role:** L1 product direction. This is the durable interpretation of the
> owner's 2026-07 discussion, consolidated from the former repository-root
> note. It records intent and boundaries, not machine-local runtime facts or a
> claim that a feature is currently working.
>
> For product scope see [PRD](PRD.md); for success gates see
> [PHASE_GATES](../gates/PHASE_GATES.md) and [EXPERIMENT_PROTOCOL](../experiments/EXPERIMENT_PROTOCOL.md);
> for implementation truth see the linked L2 contracts and executable tests.
> Last updated: 2026-07-12.

The currently admitted executable subset is declared separately in
[ACTIVE_RUNTIME_SCOPE](ACTIVE_RUNTIME_SCOPE.md). That file is machine checked and
must stay smaller than, or equal to, this broader product direction.

---

## 1. Direction of travel

Aletheia is a medium-horizon agentic trading system, not a high-frequency
signal bot and not a conventional rule/DSL/backtest strategy factory. Before
the next evaluation round, the priority is to prove that the existing loop is
truthful and connected:

```text
market observation -> opportunity -> System Operator decision
-> proposal -> risk/execution -> outcome -> review/knowledge
-> a later, attributable decision
```

Counts, attractive UI states, and retrospective narratives are not proof that
the loop improves trading. A path may influence behaviour only with its own
causal record, outcome quality checks, and the measured contribution gates in
[learning_evolution_contract](../../design/contracts/learning_evolution_contract.md).
Round admission is governed by [EXPERIMENT_PROTOCOL](../experiments/EXPERIMENT_PROTOCOL.md),
not by this document.

## 2. System Operator and supporting agents

The **System Operator** is the sole top-level trading decision maker. It works
on the system's medium-horizon cadence: it receives structured market context,
candidate opportunities, learning context, portfolio facts, and hard risk
boundaries, then decides whether to propose, hold, or skip. It does not bypass
the Risk Engine or Execution Gateway.

Specialised agents collect evidence, form opportunities, maintain knowledge,
and perform bounded supporting work. They are not independent order channels.
The Operator may inspect runtime diagnostics and make only already-authorised
runtime actions; it may not edit code, alter credentials or hard risk limits,
unlock live trading, or place orders outside the normal proposal path. The
implementation boundary is [operator_layer](../../design/operator/operator_layer.md) and
[trust_and_change_control](../../governance/policies/trust_and_change_control.md).

`System Operator` is the product and documentation name. Historical technical
identifiers such as `pi_daemon` and `pi_decisions` remain compatibility/audit
identifiers until a separately approved migration; they are not a second
product role.

## 3. Learning is an evidence pipeline, not a page

Learning must turn observations, decisions, entries, exits, realised or
fixed-horizon outcomes, reviews, and later corrections into machine-readable
artifacts that can be retrieved by a later judgment. It must also retain enough
causal identity to answer which source influenced which decision and whether
that influence helped.

- New evidence may revise, demote, freeze, expire, or archive earlier advice;
  it must not silently overwrite history.
- Insufficient, contaminated, synthetic, or economically incomplete outcomes
  are unavailable for behavioural authority, not treated as positive evidence.
- Learning may provide bounded prompt/ranking/sizing/guard inputs only through
  its declared path. It never changes credentials, live authority, hard risk
  limits, or execution semantics.
- Universe-family diversity is part of the same loop. Momentum is not a
  default winner: family/direction/market/regime evidence must be recorded, and
  insufficient evidence falls back to neutral treatment.

The current contract is [learning_contract](../../design/contracts/learning_contract.md).
The measured promotion, control, attribution, and demotion target is
[learning_evolution_contract](../../design/contracts/learning_evolution_contract.md).

## 4. Trade planning and execution honesty

An entry is a planned risk decision, not a gambler-style market click. For a
native limit entry, the product direction requires a usable entry price, stop
loss, take profit, and expected reward-to-risk. The implementation contract and
its issue ledger decide what is currently enforced and record any missing-field
or price-geometry gap. Every order still passes the Risk Engine and the single
Execution Gateway, and exchange rules remain the final authority.

The implementation details, including exchange minimum-order constraints,
limit-entry validation, causal records, and reconciliation rules, belong to
[execution_contract](../../design/contracts/execution_contract.md) and
[risk_engine_contract](../../design/contracts/risk_engine_contract.md). A change to that
behaviour is a trust-boundary task, not a Learning or UI change.

## 5. Research, Serenity, and the user interface

Serenity is an independent deep-research lens: it studies real-world supply,
demand, industry, and geopolitical relationships before producing evidence for
the System Operator. It is not merely a sub-tab of the Learning page. Its
research and trading boundary is [serenity_contract](../../design/contracts/serenity_contract.md).

The UI must organise work by operator purpose—research, decision, portfolio,
learning, and settings—rather than preserving obsolete module names. Existing
legacy strategy records may remain visible for historical attribution, but the
interface must not imply that a removed executable RuleSet/DSL strategy engine
is driving trades. A broad navigation redesign remains an owner-approved
information-architecture decision; concrete UI gaps belong in
[FRONTEND_ISSUES](../../issues/domain/FRONTEND_ISSUES.md), not in this direction document.

## 6. Engineering choices

- Keep the Python backend. A wholesale TypeScript rewrite would not solve API
  latency, caching, or data-contract defects and would put the trading safety
  path through a high-risk rewrite. Type-safe API generation is the preferred
  frontend/backend alignment mechanism.
- Keep tests layered: focused tests during development; contracts and
  trust-boundary tests for governed changes; the full suite before integration.
  Profile and label genuinely slow tests before changing fixtures or removing
  coverage. See [TESTING](../../operations/development/TESTING.md) and
  [PYTEST_OPTIMIZATION_GUIDE](../../operations/development/PYTEST_OPTIMIZATION_GUIDE.md).
- UI latency and missing price states are operational defects to measure and
  record in the issue ledger. They are not evidence for a language migration.

## 7. Source-to-home map

| Original discussion theme | Canonical home | What belongs there |
| --- | --- | --- |
| Why the system must learn before the next evaluation | [VISION](VISION.md), [EXPERIMENT_PROTOCOL](../experiments/EXPERIMENT_PROTOCOL.md) | product bet and evaluation gates |
| Learning, correction, attribution, and Universe diversity | [learning_contract](../../design/contracts/learning_contract.md), [learning_evolution_contract](../../design/contracts/learning_evolution_contract.md) | implementation obligations and measurable authority |
| System Operator responsibilities and naming | [operator_layer](../../design/operator/operator_layer.md), [agent_contracts](../../design/contracts/agent_contracts.md) | role, tools, cadence, and safety boundary |
| Planned limit entries and four entry fields | [execution_contract](../../design/contracts/execution_contract.md), [risk_engine_contract](../../design/contracts/risk_engine_contract.md) | executable order/risk behaviour |
| Serenity research | [serenity_contract](../../design/contracts/serenity_contract.md) | evidence and promotion boundary |
| UI layout, price-feed, loading, and navigation gaps | [FRONTEND_ISSUES](../../issues/domain/FRONTEND_ISSUES.md) | evidence-backed, actionable UI work |
| Test-speed practice | [TESTING](../../operations/development/TESTING.md), [PYTEST_OPTIMIZATION_GUIDE](../../operations/development/PYTEST_OPTIMIZATION_GUIDE.md) | test tiers and profiling procedure |
| Historical decomposition and completed work | `docs/_ai/DISCUSSION_*.md` | dated process evidence; not current product truth |

When a statement is about the current machine, use `aletheia doctor` or a
fresh measurement. Do not promote a historical report into a standing fact.
