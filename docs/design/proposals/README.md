# Design Proposals

Proposals describe substantial future behavior without claiming that behavior
already exists.

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

- [V2/V3 residue and dual-mainline convergence](v2_v3_convergence_plan.md):
  delete the retired V2 entry authority and the dead profit-gradient branch,
  collapse the flat Risk fields that shadow the nested mode policy, and list the
  V2/V3 names that are external protocol fact and must survive.

- [Optional news compartment](slim_core_news_compartment.md): isolate existing
  RSS/analysis/API work while retaining Core historical matching.

- [Scoped credential Data collectors](slim_core_scoped_data_collectors.md): preserve
  the approved per-adapter credential exception within the Data compartment.

- [Options Data compartment](slim_core_options_data_compartment.md): isolate
  Deribit acquisition and derivation while preserving the Core snapshot contract.

- [Native Data collector compartment](slim_core_native_data_collectors.md): keep
  existing native protocol acquisition in the Data Extension.

- [Public-context Data adapters](slim_core_public_context_adapters.md): place
  existing optional protocol collectors behind the established Data boundary.

- [Planner Data boundary](slim_core_planner_data_boundary.md): use normalized
  observations without coupling stored-fact reads to optional collectors.

- [Slim Core macro projection](slim_core_macro_projection.md): keep stored macro
  fact classification independent of optional acquisition code.

- [Minimal canonical Derivatives Facts](derivatives_facts.md): explicit source/unit/time semantics over existing collectors and tables, with observation-only cross-venue readiness.

- [Retire the local simulated exchange](retire_local_simulated_exchange.md): accepted removal of the internal execution/account runtime while preserving historical data.

- [Observation Experiment Ownership V2](observation_experiment_ownership_v2.md): moves registered Experiment definition, Generation, assignment, Exposure, Evaluation, lifecycle, retention, and archive ownership to Observation Lab through the serial #2737 migration while preserving Main production facts and trading trust boundaries.
- [CN Equity Market Kernel](cn-equity-market-kernel.md): a second explicitly
  gated market using versioned identity, point-in-time market facts, the existing
  non-executing observation and Shadow boundaries; external-account execution
  requires a separate accepted design.
- [Complete Unified Trading Account](unified_trading_account.md): one private
  external-account read/write boundary beneath Gateway authority, with explicit
  evidence, reconciliation and profile qualification semantics.
- [Position replay and profit-gradient evidence](position_replay_profit_gradient.md):
  exact candidate/Position facts, frozen hypotheses, prefix-only analysis, and
  seven-layer unique-winner gates without trading authority.
- [Slim Core legacy Gateway retirement](slim_core_legacy_gateway_retirement.md):
  remove the dormant PS recovery/write graph and its startup authority while
  preserving historical data and canonical Core reconciliation gates.
- [Retire the Market Observer pipeline](retire_market_observer.md): accepted removal
  of the `market_observer` agent, its scheduler job and its liveness surfaces,
  whose admitted workset lost its only producer with the deterministic Universe
  emitter; historical `opportunities` reads and attribution labels are preserved.
- [Slim Core partial-reduce retirement](slim_core_partial_reduce_retirement.md):
  delete the unused estimated-fee writer; preserve verified partial Fill
  materialization and historical ledger readers.
- [Learning evolution](learning_evolution.md): governed learning changes.
- [Learning observability](learning_observability.md): learning measurement and
  operator visibility.
- [Universe continuous scoring safety](universe_continuous_scoring_safety.md):
  bounded continuous curves plus raw-evidence, missingness, reversible-feedback,
  and pessimistic-promotion safeguards.
- [Official HTTP model transport](slim_core_http_model_transport.md):
  accepted SDK transport and HTTP-only model endpoint convergence; the former
  [three-category design](../history/model_provider_profiles_and_access_methods.md)
  is historical.
- [Fedora immutable Runtime updates](fedora_immutable_runtime_updates.md):
  signed Linux releases, headless activation ownership, migration preflight,
  health-gated activation, and bounded recovery.

## Implemented proposals

- [Entry Plan Contract](entry-plan-contract.md): canonical durable Entry Plan,
  on-demand planning ownership, RESTING_MAKER/TRIGGERED execution, Gateway-owned
  cancel and canonical Risk. Normative behavior is in the decision-lifecycle and
  execution contracts.
- [Tokenomics / LLM Economics Harness](tokenomics_harness.md): implemented
  in-process LLM usage/budget/admission governance layered on the existing
  provider and economics truth surfaces; historical dated research remains under
  `docs/research/tokenomics/`.
- [Usage v2 production read model and workspace](usage_v2_workspace.md):
  event-time fail-closed cost semantics and one typed, read-only Usage page for
  overview, trend, attribution, reliability, and configured budgets.
- [Gateway service lifecycle](gateway_service_lifecycle.md): cross-platform
  install, enablement, runtime control, and optional Windows machine-service
  design.
- [OHLCV Provider Observation/Result](ohlcv_provider_observation_result.md):
  exact-timeframe and real-OHLCV result semantics, source/venue identity, and
  explicit rejection of coarse or field-incomplete fallback data.
- [Serenity Research Attempt Truth](serenity_research_attempt_truth.md):
  cooperative cancellation acknowledgement, guarded terminal transitions,
  durable progress facts, and restart reconciliation for Serenity runs.

## Superseded proposals

- [Universe Fast Trigger Watch](universe_fast_trigger_watch.md): historical
  Momentum Setup/authorization design, replaced by the generic Entry Plan
  contract; its retired adapter and local-execution requirements are archived.
- [Tokenomics harness phase plan (2026-07-30)](tokenomics_harness_phase_plan_2026-07-30.md):
  dated phase sequencing for the Tokenomics harness, superseded by
  [Tokenomics / LLM Economics Harness](tokenomics_harness.md).
