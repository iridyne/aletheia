---
id: mechanism-aware-grounded-entry
status: accepted
github_issue: 2791
related_contracts:
  - ../contracts/candidate_selection_utility_contract.md
  - ../contracts/entry-plan-contract.md
  - ../contracts/learning_contract.md
  - ../contracts/observation_experiment_ownership_v2.md
supersedes: []
---

> 2026-10-09 注：文中的 planner 版本标签已由 canonical_entry_lifecycle.md（implemented）取代，当前只有一个 Grounded Entry 实现。状态保持 accepted。

# Mechanism-aware Grounded Entry Proposal

> Proposal status: owner-accepted design from Issue #2791, not an implementation,
> deployment, qualification, or profitability claim.

## Decision

Aletheia uses one Grounded AI Entry Planner for every execution-eligible Candidate
that carries a valid typed Opportunity context. Continuation, reversal,
positioning/forced-flow, and information/catalyst remain upstream predictive
hypotheses; they do not become four Entry agents, four plans, or causal claims.

The versioned path is Candidate Opportunity Context V1 -> Candidate Planning V2
-> Entry Analysis Context V2 -> Entry Hypothesis V2 -> a registered deterministic
profile compiler -> immutable Entry Plan. The model may choose only supplied
references and a compatible profile. It cannot change mechanism or direction,
invent prices, select size/leverage, alter Risk, or bypass Gateway.

## Typed handoff and lifecycle

Candidate Selection Utility persists the exact selected mechanism, typed source
component, direction, mechanism version, capability truth, supporting and opposing
evidence, 4h economic horizon, policy epoch, target-record hash, source cutoff,
and bounded context as `candidate-opportunity-context/v1`. The current Demo
handoff also records measured/startup admission basis and the exact scarcity
reason: valid Startup retains null economic Utility, not a synthetic return.
It has the same supported-capability and planning-time evidence requirements.
Legacy compatibility is historical only: Momentum/Short Bias -> continuation,
Mean Reversion -> reversal and Derivatives -> positioning proxy retain explicit
gaps. Volatility and Event do not gain invented mechanisms or new Legacy
runtime authority. Old JSON/hashes are never rewritten for this amendment.

`candidate-planning/v2` ownership is venue, symbol, market type, mechanism,
direction, Candidate-selection policy and epoch, and mechanism version. A change
to any of those facts supersedes the current generation and invalidates only
active pre-order plans. Rank, score, price, utility, and ordinary snapshot churn
do not rotate ownership. Historical V1 generations and plans are never rewritten.

## Grounded analysis and profiles

`entry-analysis-context/v2` separates frozen Candidate-T0 selection facts from
planning-T1 current facts and from Learning evidence available by T1. It exposes
all bounded mechanism summaries, opposing evidence, and capability gaps. External
claim text is untrusted data and is represented only by bounded structured refs.

`entry-hypothesis/v2` must exactly repeat the supplied mechanism and direction,
select registered anchors and one compatible profile, remain intraday for the
Candidate Selection Utility 4h policy, and cite at least one current planning-time
market fact. Numerical execution authority remains exclusively deterministic.

The profile registry initially reuses only the existing bounded resting-maker and
retrace/reclaim geometry under new generic identities. Missing facts or an
unsupported mechanism/profile combination fail before model invocation when
deterministically knowable. Historical Momentum profile identities retain their
original meaning.

## Bounded Learning and experiment

`entry-learning-context/v1` is a read-only, identity-matched summary over existing
Candidate, generation, plan, proposal, order, fill, position, and fixed-horizon
outcome owners. Its `as_of_at` is Planning T1. It excludes outcomes settled after
that cutoff, includes NO_PLAN/no-trigger/no-fill/open-at-cutoff denominator units,
preserves code/model/prompt/profile/cost/market/mechanism/policy identity, and
returns explicit unavailable evidence for insufficient or mixed cohorts.
Filled units are anchored by the existing `filter_candidate_outcomes` owner at
each actual Fill T0; Candidate-T0 4h outcomes are never re-anchored. Multiple
fills are combined by fill notional. Profile evidence is either exact-profile or
an explicit `mechanism-aware-grounded/v1` parent summary, and the historical
cohort must match the current code, behavior, planner, mechanism, provider,
model, prompt, profile-registry, market, and policy identities.

Observation Lab owns one treatment: control is mechanism-aware Grounded Entry with
the deterministic empty Learning context; treatment differs only by the bounded
Learning context. Candidate-selection epoch and all model/profile/compiler/Risk/
execution facts are frozen, interference is declared, control is retained, and
results are reported separately by mechanism, direction, and market type. No
result grants Live, Risk, Gateway, credential, or capital authority.
The registered `mechanism_entry_learning` measurement path uses Planning
Generation as its statistical unit and the same generation ID in the treatment
receipt; terminal non-entry units settle to zero after the 4h look, while filled
units resolve only through their exact Fill outcomes. Unavailable treatment
evidence is replaced with the deterministic empty context before model input.

## Rollback and non-goals

Rollback routes new Candidates back to existing Planner behavior without rewriting
Candidate, generation, plan, or outcome history. Learning influence can refreeze
independently of baseline mechanism-aware planning.

This change does not retune Universe scoring, add a Candidate/Outcome/Learning
database, add event/news/liquidation/Basis feeds, expand SMC/ICT geometry, change
Risk/sizing/leverage, replace the model provider, deploy to Fedora, alter #2556,
or claim component or whole-policy profitability.
