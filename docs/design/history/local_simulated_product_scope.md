# Archived product scope before local exchange retirement

**Archived.** This preserves the earlier scope definition, including its former
support claims and Learning gates. It does not authorize current execution or
qualify an external account. Current scope is [Product Scope](../../product/SCOPE.md).

---

# Aletheia Trading OS - Product Scope

> **Role:** Product Truth for inclusions, deferrals, and exclusions.
> Product purpose and long-term direction are defined only by
> [Product Direction](../../product/definition/DIRECTION.md). Current support classifications
> are defined by [Capability Ground Truth](../../product/CAPABILITY_GROUND_TRUTH.md), and the
> machine-readable trading mainline is [Active Runtime Scope](../../product/ACTIVE_RUNTIME_SCOPE.md).
> This file is not a roadmap or runtime-status page.
>
> Last aligned with current authorities: 2026-08-31.

## Product boundary

Aletheia is a local-first, single-owner trading research and decision system. Its
currently supported mainline remains crypto-focused. It connects market
observation, System Operator judgment, bounded paper execution, hard Risk
approval, outcome accounting, and measured Learning without granting an LLM
direct exchange, credential, Risk-limit, or live-unlock authority.

The current MVP mainline is deliberately narrow:

```text
crypto spot/perpetual evidence
  -> Opportunity
  -> System Operator decision
  -> Proposal
  -> RiskDecision
  -> Execution Gateway
  -> paper_internal order/fill/position
  -> position evaluation / exact protective close
  -> verified genuine outcome
  -> review / counterfactual / measured Learning
  -> owner-gated bounded influence on a later decision
```

`System Operator` is the only native entry-decision role. Historical strategy
records are attribution data; there is no supported RuleSet/DSL/backtest strategy
factory driving production entries.

## Included current scope

| Area | Included promise | Canonical detail |
|---|---|---|
| Market evidence | Crypto spot/perpetual observation, features, opportunities, and exact provider/venue identity | Capability Ground Truth and data contracts |
| Decision | One attributable System Operator decision path with neutral/fail-closed behavior when required evidence is missing | Active Runtime Scope and agent contracts |
| Risk | Deterministic Risk approval is mandatory before every execution attempt | Risk Engine contract |
| Execution | Local `paper_internal` is the active proof profile; exchange demo adapters exist outside that active proof | Active Runtime Scope and execution contract |
| Position and exit | New protected native Paper positions freeze an immutable ExitPlan and exact-instrument 1m cursor; point-mark protection remains a second layer and all closes use the routed fresh-mark path | Execution, persistence, and data-model contracts |
| Accounting | Orders, fills, positions, costs, funding, and genuine outcomes retain causal identity and explicit integrity status | Data/outcome contracts |
| Learning artifacts | Reviews, lessons, cases, memos, retrieval, calibration, and learned-guard observation | Learning and knowledge contracts |
| Measured Learning | `universe_family_calibration`, `momentum_entry_geometry`, `mechanism_entry_learning`, and `ranker_case_score` are measurement-supported; the first three are owner-gated Paper-capable, while Ranker Case is Shadow-only with zero Ranker delta | Learning contract and registry |
| Owner surfaces | Local API, CLI, Dashboard, Doctor, audit history, and honest unavailable/degraded states | Capability Ground Truth |

The repository contains additional implemented or experimental capabilities. Their
presence does not widen the supported mainline; use the exact status and gap in
[Capability Ground Truth](../../product/CAPABILITY_GROUND_TRUTH.md).

## Approved implementation scope, not current support

The accepted [CN Equity Market Kernel Proposal](../proposals/cn-equity-market-kernel.md)
authorizes implementation of `market_id=cn_equity` under Issue #2572. This is
an implementation boundary, not a claim that any current Runtime observes,
trades, accounts for, or qualifies mainland equities.

The bounded target is CNY-denominated Shanghai and Shenzhen A-shares, long-only,
through `disabled -> observe -> shadow -> paper`, with `disabled` as the
default. Initial entry eligibility is limited to ordinary Shanghai and Shenzhen
main-board shares. ST/*ST, IPO/listing-stage, STAR Market, and ChiNext shares are
observation-only for new entry while canonical close-only handling remains
mandatory for an existing Position whose classification changes. Unknown
classification, B-shares, Beijing Stock Exchange, and every non-CNY or other
instrument class fail closed.

CN evidence, strategy, and Paper accounting must reuse the existing Candidate →
Plan → Proposal → Risk → Execution Gateway → Order/Fill/Position → Outcome →
Learning authority chain. Research and execution Sidecars remain replaceable
tools with no Risk, accounting, credential, route, or decision authority. No
broker route or `live` state is authorized. Capability Ground Truth may report
CN support only after the exact implementation, Runtime, accounting, and strategy
qualification gates record their separate results.

## Current Learning scope

Learning is allowed to:

- create and retrieve attributable artifacts;
- record prospective treatment/control exposure for a declared supported path;
- settle exact cost-aware outcomes;
- compute identity-matched contribution and harm evidence;
- recommend an owner action;
- apply bounded paper influence only after every default-closed gate and explicit
  owner trial admission;
- treat recurring contribution evaluations as monitoring only and allow positive
  Paper progression only from the one consumed prospective fixed-look verdict;
- refreeze or invalidate harmful, stale, or untrustworthy authority.

Learning is not allowed to infer effectiveness from artifact counts, evaluator
health, PnL alone, or retrospective matches. All declared paths except
`universe_family_calibration` remain measurement-unsupported and return their
static fallback. Learned guards are observe-only; seed/manual guards are a
separate static safety class.

The exact current software gate is 50 complete assignment units per arm inside
the latest run of seven consecutive UTC exposure dates. Every counted day must
contain at least one complete eligible unit from either arm, while the current
run must independently reach 50 treatment and 50 control units. A missing day
resets the run and pre-gap samples cannot bridge the new qualification window.

## Deferred or separately authorized

| Capability | Current boundary |
|---|---|
| Live trading | Disabled by default; requires the live contract, quantitative gates, and explicit human authorization |
| Exchange-backed qualification | Adapters may be implemented, but the active proof remains `paper_internal` until Active Runtime Scope and executable evidence change together |
| Additional measured Learning paths | Require a causal unit, outcome adapter, behavior contract, failure semantics, focused tests, and a fresh observation window |
| Authoritative learned exit policy | Current exit advice and the prospective entry-lock treatment are read-only evidence; any real ExitPlan mutation requires a separately reviewed treatment and fresh window |
| Full Learning owner workbench | Proposal exists; delivery is tracked by GitHub issue #614 |
| Multiple long-lived managed Runtimes | Requires the approved multi-instance Runtime design; manual directory copies are not supported |
| New agents, assets, markets, data sources, or self-evolution tiers | Separate scope and evidence required; they do not enter the MVP by implication |

## Excluded

- direct order placement from an agent, Dashboard, Telegram, or Learning module;
- agent-controlled credentials, hard Risk limits, live unlock, or signer logic;
- automatic generation and execution of arbitrary Python strategy code;
- restoring a RuleSet/DSL/backtest engine as the production decision source
  without a new owner-approved design;
- end-to-end opaque reinforcement learning;
- multi-user SaaS, high-frequency trading, or broad Docker/Postgres/Redis
  infrastructure in the MVP;
- treating repository documents as current Gateway, database, deployment, or
  observation-window state.

## How scope is evaluated

- Product direction: [DIRECTION.md](../../product/definition/DIRECTION.md)
- Current support: [CAPABILITY_GROUND_TRUTH.md](../../product/CAPABILITY_GROUND_TRUTH.md)
- Active mainline: [ACTIVE_RUNTIME_SCOPE.md](../../product/ACTIVE_RUNTIME_SCOPE.md)
- Current behavioral contracts: [docs/design/contracts](../contracts/README.md)
- Metrics and admission: [METRICS.md](../../product/gates/METRICS.md) and
  [PHASE_GATES.md](../../product/gates/PHASE_GATES.md)
- Experiment-change rules: [EXPERIMENT_PROTOCOL.md](../../product/experiments/EXPERIMENT_PROTOCOL.md)
- Runtime truth: `uv run aletheia doctor`, provenance, logs, and the selected data root
- Ownership, priority, progress, and unresolved work: GitHub Issues and PRs
