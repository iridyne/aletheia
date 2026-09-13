# Aletheia Trading OS - Product Scope

> **Role:** Product Truth for inclusions, deferrals, and exclusions.
> Product purpose and long-term direction are defined only by
> [Product Direction](definition/DIRECTION.md). Current support classifications
> are defined by [Capability Ground Truth](CAPABILITY_GROUND_TRUTH.md), and the
> machine-readable trading mainline is [Active Runtime Scope](ACTIVE_RUNTIME_SCOPE.md).
> This file is not a roadmap or runtime-status page.
>
> The former local-exchange scope is [archived](../design/history/local_simulated_product_scope.md).

## Product boundary

The accepted product is **exchange/provider-agnostic Slim Core + active Bitget
Exchange Extension + configured HTTP Model Endpoint(s)**, per
[#3134](https://github.com/iridyne/aletheia/issues/3134). Bitget Demo is the current
external qualification route. OKX/Binance and additional markets remain frozen
until separately promoted; retained executable registrations are not support
claims. Core must expose history and diagnostics with trading unavailable when
no usable Exchange Extension is configured. This is the accepted target, not
proof that all implementation and deployed-runtime gaps have converged.

Model protocols are OpenAI Chat Completions, OpenAI Responses and Anthropic
Messages over configured HTTP endpoints, with explicit Fast/Deep endpoint/model
pairs. CLI/subscription/OAuth transports are not current product paths.
No account qualification, credential expansion or Live authority is implied.

The current entry chain connects market evidence and Candidate/Opportunity to
System Operator planning, an immutable Proposal, Risk approval, Execution
Gateway submission, private Fill materialization, and verified outcome evidence.
The LLM gains no direct exchange, credential, Risk-limit, or live-unlock authority.

`System Operator` is the only native entry-decision role. Historical strategy
records are attribution data; there is no supported RuleSet/DSL/backtest strategy
factory driving production entries.

## Included current scope

| Area | Included promise | Canonical detail |
|---|---|---|
| Market evidence | Crypto market facts required by the Bitget route; additional markets and enrichments remain optional/frozen rather than a Slim Core promise | Capability Ground Truth and data contracts |
| Decision | One attributable System Operator decision path with neutral/fail-closed behavior when required evidence is missing | Active Runtime Scope and agent contracts |
| Risk | Deterministic Risk approval is mandatory before every execution attempt | Risk Engine contract |
| Execution | Active Bitget Extension; other retained route registrations are frozen. Exact-account qualification remains separate | Active Runtime Scope and execution contract |
| Position and exit | Exchange Fill evidence materializes Positions; the existing position owner and Gateway control external protection and closes | Execution, persistence, and data-model contracts |
| Accounting | Orders, fills, positions, costs, funding, and genuine outcomes retain causal identity and explicit integrity status | Data/outcome contracts |
| Learning artifacts and measurements | Optional research Extension, not required Slim Core capability; existing owner admission and influence gates remain mandatory | Learning and knowledge contracts |
| Owner surfaces | Local API, CLI, Dashboard, Doctor, audit history, and honest unavailable/degraded states | Capability Ground Truth |

The repository contains additional implemented or experimental capabilities. Their
presence does not widen the supported mainline; use the exact status and gap in
[Capability Ground Truth](CAPABILITY_GROUND_TRUTH.md).

## Approved implementation scope, not current support

The accepted [CN Equity Market Kernel Proposal](../design/proposals/cn-equity-market-kernel.md)
authorizes implementation of `market_id=cn_equity` under Issue #2572. This is
an implementation boundary, not a claim that any current Runtime observes,
trades, accounts for, or qualifies mainland equities.

The bounded target is CNY-denominated Shanghai and Shenzhen A-share market
identity, calendar/rules, tradability and point-in-time evidence. The permitted
states are `disabled`, `observe` and non-executing `shadow`, defaulting to
`disabled`. Observation and Shadow cannot reserve account resources, issue
executable approvals, submit orders, produce fills or mutate account positions.
No CN execution route or broker connectivity is authorized. Future execution
requires a separate accepted design and qualification against an external account.

Research tools and Sidecars remain replaceable and gain no Risk, accounting,
credential, route or decision authority. CN support is reported only from the
corresponding implementation and Runtime evidence. The former internal-account
and qualification design is [archived](../design/history/cn_equity_internal_paper.md).

## Current Learning scope

Learning may create attributable artifacts, measure evidence and recommend owner
actions within the [Learning contract](../design/contracts/learning_contract.md).
Current path support, outcome units, thresholds and influence gates belong to
that contract and its registry; this scope page does not maintain a second copy.
Explicit owner trial admission and the declared authority gate remain required
for behavioral influence. Research never acquires Risk, execution, credentials,
or Live authority. Historical simulator cohorts retain their original identities
and cannot qualify an external-account path.

## Deferred or separately authorized

| Capability | Current boundary |
|---|---|
| Live trading | Disabled by default; requires the live contract, quantitative gates, and explicit human authorization |
| Exchange-backed qualification | Route registration is not acceptance; qualification names the exact account, instrument, tested commit, private Fill and verified-close evidence |
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

- Product direction: [DIRECTION.md](definition/DIRECTION.md)
- Current support: [CAPABILITY_GROUND_TRUTH.md](CAPABILITY_GROUND_TRUTH.md)
- Active mainline: [ACTIVE_RUNTIME_SCOPE.md](ACTIVE_RUNTIME_SCOPE.md)
- Current behavioral contracts: [docs/design/contracts](../design/contracts/README.md)
- Metrics and admission: [METRICS.md](gates/METRICS.md) and
  [PHASE_GATES.md](gates/PHASE_GATES.md)
- Experiment-change rules: [EXPERIMENT_PROTOCOL.md](experiments/EXPERIMENT_PROTOCOL.md)
- Runtime truth: `uv run aletheia doctor`, provenance, logs, and the selected data root
- Ownership, priority, progress, and unresolved work: GitHub Issues and PRs
