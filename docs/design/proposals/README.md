# Design Proposals

Proposals describe substantial future behavior without claiming that behavior
already exists.

Create one only for a major new capability, a cross-domain change, or a
trust-boundary change. Ordinary bugs, narrow refactors, and routine
documentation or operations changes use a GitHub Issue when tracking is needed
and a PR for review.

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

- [Gateway service lifecycle](gateway_service_lifecycle.md): cross-platform
  install, enablement, runtime control, and optional Windows machine-service
  design.
- [Gateway recovery](gateway_recovery.md): restart reconciliation and safe
  freeze behavior.
- [Learning evolution](learning_evolution.md): governed learning changes.
- [Learning observability](learning_observability.md): learning measurement and
  operator visibility.
- [Universe continuous scoring safety](universe_continuous_scoring_safety.md):
  bounded continuous curves plus raw-evidence, missingness, reversible-feedback,
  and pessimistic-promotion safeguards.
- [Tokenomics harness phase plan](tokenomics_harness_phase_plan_2026-07-30.md):
  controlled inference-harness rollout.
- [Model provider profiles and access methods](model_provider_profiles_and_access_methods.md):
  API profile instances, CLI harness integrations, OAuth connections, and the
  staged retirement of the legacy model-settings mutation path.
- [OHLCV Provider Observation/Result](ohlcv_provider_observation_result.md):
  exact-timeframe and real-OHLCV result semantics, source/venue identity, and
  explicit rejection of coarse or field-incomplete fallback data.
- [Serenity Research Attempt Truth](serenity_research_attempt_truth.md):
  cooperative cancellation acknowledgement, guarded terminal transitions,
  durable progress facts, and restart reconciliation for Serenity runs.
