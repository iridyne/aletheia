# Design Proposals

Proposals describe substantial future behavior without claiming that behavior
already exists.

> Since 2026-10-10 new designs are written as SDD specs in [`../specs/`](../specs/README.md).
> This folder keeps legacy Proposals; a revised one moves into the spec format.

A Proposal is optional and never a precondition
([2026-09-26 decision](../../governance/decisions/DECISIONS.md)). Write one when
the reasoning will not survive in a PR body; otherwise use an Issue and a PR.

## Lifecycle

| State | Meaning |
|---|---|
| `draft` | Design is under review; no implementation claim |
| `accepted` | Direction is approved; implementation may still be absent |
| `rejected` | Direction will not be pursued |
| `implemented` | Current normative behavior has moved to its Contract, Product Truth, Decision, or Operations owner |
| `superseded` | A newer Proposal or Decision replaced this design |

Proposal state is not work progress. Owners, priorities, checklists, issue
status, and PR status stay in GitHub. Accepted does not mean implemented.

Use [template.md](template.md). When a Proposal becomes current behavior,
transfer the normative content to its permanent document role and leave the
Proposal as stable decision history.

## Current proposals

Status `accepted` or `draft`: direction recorded, complete behavior not yet current.

- [Lifecycle capability map](lifecycle_capability_map.md) (`accepted`): a long-lived
  capability map along the opportunity/position lifecycle. Stable definitions live
  in docs, progress lives in GitHub, and the current level comes from an AI-run review.
- [Entry, stop, target and position sizing](entry_order_risk_sizing.md) (`draft`):
  the owner's original intent for a 500 USDT starting account and the
  evidence-selected design for stop placement, sizing, leverage and stop execution.
- [CN Equity Market Kernel](cn-equity-market-kernel.md): a second explicitly
  gated market using versioned identity, point-in-time market facts, the existing
  non-executing observation and Shadow boundaries; external-account execution
  requires a separate accepted design.
- [Universe continuous scoring safety](universe_continuous_scoring_safety.md):
  bounded continuous curves plus raw-evidence, missingness, reversible-feedback,
  and pessimistic-promotion safeguards.
- [Learning observability](learning_observability.md): learning measurement and
  operator visibility; the remaining full-workbench targets are unplanned.
- [Observation Experiment Ownership V2](observation_experiment_ownership_v2.md):
  moves registered Experiment ownership to Observation Lab; the Lab-only write guard
  is in place, the authority cutover is tracked by #2737 / #3808.
- [Complete Unified Trading Account](unified_trading_account.md): one private
  external-account read/write boundary beneath Gateway authority (Classic-v2 and
  profile qualification have since been removed; see its header note).
- [Mechanism-aware Grounded Entry](mechanism_aware_grounded_entry.md): one
  Grounded Entry across Opportunity mechanisms with bounded Learning context; its
  planner version labels are superseded by the canonical Entry lifecycle.
- [Fedora immutable Runtime updates](fedora_immutable_runtime_updates.md)
  (`draft`): signed Linux releases, headless activation ownership, migration
  preflight, health-gated activation, and bounded recovery.
- Slim Core convergence (#3134, OPEN) — accepted per-compartment boundaries:
  [Candidate publication](slim_core_candidate_publication.md),
  [official HTTP model transport](slim_core_http_model_transport.md)
  (the former [three-category design](../history/model_provider_profiles_and_access_methods.md)
  is historical),
  [legacy Gateway retirement](slim_core_legacy_gateway_retirement.md),
  [research bundle import](slim_core_research_bundle_import.md),
  [partial-reduce retirement](slim_core_partial_reduce_retirement.md);
  Data compartment batch:
  [news](slim_core_news_compartment.md),
  [scoped credential collectors](slim_core_scoped_data_collectors.md),
  [options](slim_core_options_data_compartment.md),
  [native collectors](slim_core_native_data_collectors.md),
  [public-context adapters](slim_core_public_context_adapters.md),
  [Planner Data boundary](slim_core_planner_data_boundary.md),
  [macro projection](slim_core_macro_projection.md).
  The settlement ownership investigation formerly here is dated evidence at
  [`research/audits/SLIM_CORE_SETTLEMENT_OWNERSHIP_2026-09-12.md`](../../research/audits/SLIM_CORE_SETTLEMENT_OWNERSHIP_2026-09-12.md).

## Implemented proposals

Normative behavior lives in the named Contract / Product Truth owner; the proposal
is stable decision history.

- [Entry Plan Contract](entry-plan-contract.md): canonical durable Entry Plan,
  on-demand planning ownership, RESTING_MAKER/TRIGGERED execution, Gateway-owned
  cancel and canonical Risk → `contracts/entry-plan-contract.md`.
- [Canonical Entry lifecycle](canonical_entry_lifecycle.md): one Candidate → Plan
  cycle replacing the V2/V3/V4 runtime forks → `contracts/entry-plan-contract.md`.
- [Minimal canonical Derivatives Facts](derivatives_facts.md): explicit
  source/unit/time semantics → `contracts/derivatives_economic_semantics.md`.
- [Position replay and profit-gradient evidence](position_replay_profit_gradient.md)
  → `contracts/profit_loop_observability_contract.md`,
  `contracts/profit_gradient_evidence_contract.md`.
- [Retire the local simulated exchange](retire_local_simulated_exchange.md)
  → `product/CAPABILITY_GROUND_TRUTH.md`, `history/local_simulated_*`.
- [Retire the Market Observer pipeline](retire_market_observer.md)
  → `contracts/agent_contracts.md`.
- [Retire SHA-bound UTA mutation qualification](retire_uta_qualification.md)
  → `contracts/execution_contract.md`.
- [Tokenomics / LLM Economics Harness](tokenomics_harness.md): in-process LLM
  usage/budget/admission governance; dated research remains under
  `docs/research/tokenomics/`.
- [Usage v2 production read model and workspace](usage_v2_workspace.md):
  event-time fail-closed cost semantics and one typed, read-only Usage page.
- [Gateway service lifecycle](gateway_service_lifecycle.md): install, enablement
  and runtime control; only the Linux systemd user session is maintained.
- [OHLCV Provider Observation/Result](ohlcv_provider_observation_result.md):
  exact-timeframe and real-OHLCV result semantics.
- [Serenity Research Attempt Truth](serenity_research_attempt_truth.md):
  cooperative cancellation, guarded terminal transitions, restart reconciliation.

## Superseded proposals

- [Universe Fast Trigger Watch](universe_fast_trigger_watch.md): historical
  Momentum Setup/authorization design, replaced by the generic Entry Plan
  contract.
- [Tokenomics harness phase plan (2026-07-30)](tokenomics_harness_phase_plan_2026-07-30.md):
  superseded by [Tokenomics / LLM Economics Harness](tokenomics_harness.md).
- [Learning evolution](learning_evolution.md): superseded by spec
  [0001-learning-loop](../specs/0001-learning-loop/spec.md).

The executed V2/V3 convergence plan (2026-09-26) is archived at
[`archive/repair/V2_V3_CONVERGENCE_PLAN_2026-09-26.md`](../../archive/repair/V2_V3_CONVERGENCE_PLAN_2026-09-26.md);
its "明确排除清单" (V2/V3 names that must survive) still applies to #2971.
