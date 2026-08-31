---
id: cn-equity-market-kernel
status: accepted
github_issue: 2573
related_contracts:
  - docs/design/contracts/entry-plan-contract.md
  - docs/design/contracts/risk_engine_contract.md
  - docs/design/contracts/execution_contract.md
  - docs/design/contracts/market_data_coverage_contract.md
  - docs/design/contracts/persistence_recovery_contract.md
  - docs/design/contracts/opportunity_outcome_contract.md
supersedes: []
---

# CN Equity Market Kernel

## Summary

Aletheia will admit mainland China cash equities as a second explicitly gated
market without creating a parallel trading system. The existing Candidate →
Entry Plan → Proposal → Risk → Execution Gateway → Order/Fill/Position →
Outcome → Learning chain remains the only authority path. The change adds only
the market identity, calendar, rules, tradability, costs, settlement, and
point-in-time evidence needed to represent CN equities honestly.

The first supported boundary is `market_id=cn_equity`, CNY-denominated A-shares
listed in Shanghai or Shenzhen, long-only. Only seasoned, non-ST Shanghai and
Shenzhen main-board A-shares may progress through observation, Shadow, and the
internal Paper route. ST/*ST, IPO/listing-stage, STAR Market, and ChiNext shares
are observation-only for new entry; they cannot produce an entry Plan, entry
Proposal, cash reservation, or buy Order. If an already-owned supported Position
later enters one of those categories, it becomes close-only: no exposure may be
added, but canonical exit continues through the current RuleSnapshot,
sellable-share reservation, RiskApproval, and Gateway. Unknown
board/treatment/listing classification, B-shares, and any non-CNY instrument fail
closed for entry. Widening entry eligibility requires a new D1 Issue and
qualification epoch. This Proposal does not authorize broker connectivity or
Live trading.
Implementation and qualification are tracked by parent Issue #2572 and its
child Issues.

## Current fact

Current Product Scope and Active Runtime Scope admit crypto spot/perpetuals and
a narrow stock-linked-perpetual profile. Ordinary equities are not executable.
The current instrument identity requires crypto-style base/quote symbols;
market data and Entry Plan v1 use crypto market and execution semantics; UTA
cannot represent per-instrument share settlement; and Paper accounting does not
model CN equity trading sessions, price limits, lots, statutory fees, or T+1
sellability.

The reusable part of the current system is its authority and audit chain. Risk
approval precedes execution, only the backend Execution Gateway may submit an
order, and durable causal identity connects proposals, decisions, orders,
fills, positions, and outcomes. This Proposal extends those boundaries; it does
not replace them.

## Goals and non-goals

### Goals

- Represent CNY-denominated Shanghai and Shenzhen A-shares without forcing them
  through crypto symbol canonicalization; reject B-shares and other currencies.
- Preserve effective-dated, source-attributed market rules, tradability, fee,
  settlement, and point-in-time data facts.
- Add a versioned CN Entry Plan payload while retaining the current Crypto v1
  reader and producer behavior.
- Derive an immutable exact Proposal from a bounded plan plus exact account,
  portfolio, reservation, entitlement, and execution-cost facts.
- Extend the existing Risk Engine and Execution Gateway for Shadow and internal
  Paper only.
- Model cash availability, share sellability, typed reservations, settlement,
  corporate actions, fees, partial fills, restart, and reconciliation without
  weakening ledger invariants.
- Qualify infrastructure/accounting independently from the economic promotion
  result of one falsifiable strategy epoch.

### Non-goals

- No generic multi-market plugin framework, dynamic loader, configuration DSL,
  or all-purpose `MarketKernel` service or class.
- No migration or dual write of existing Crypto v1 identities, plans, or UTA
  producers.
- No second Risk Engine, Gateway, permission system, audit system, account
  authority, strategy runner, or scheduler.
- No Docker, Postgres, Redis, Kafka, vector search, or wide-market-data storage
  in SQLite.
- No financing, securities lending, Stock Connect, Beijing Stock Exchange,
  convertible bonds, options, high-frequency trading, or mixed T+0 product
  support.
- No broker credentials, broker network order, `live` feature state, or claim of
  strategy profitability.
- No new CN equity UI. Existing surfaces may expose only backend facts and must
  use `not available` or `not configured` when authority is absent.

## Proposed design

### Market Kernel is a bounded contract set

“Market Kernel” names six versioned facts, not a deployable component:

1. `InstrumentIdentityV2`
2. `MarketCalendar` and `TradingSession`
3. `MarketRuleSnapshot`
4. `TradabilitySnapshot`
5. `MarketFeeSchedule`
6. `SettlementProfile`

The first implementation is specific to `cn_equity`. Shared Protocols are
introduced only when a separately authorized Crypto adopter needs the same
semantics. Market-specific fields used by one strategy remain in that strategy
or its CN Entry Plan payload rather than entering a speculative shared core.

### Instrument identity

A CN instrument uses immutable `market_id`, `venue`, `canonical_symbol`,
`instrument_class`, and `currency` fields. Its key is the lossless tuple:

```text
cn_equity:<venue>:<canonical_symbol>:<instrument_class>
```

Provider and broker aliases exist only at adapter boundaries. CN producers
write `instrument-identity/v2`; current Crypto producers continue to write their
existing schema. A discriminated reader may read both, but no producer double
writes and a CN symbol never passes through Crypto `to_canonical()` behavior.
Executable prices, quantities, cash, and fees use `Decimal` with lossless string
serialization.

### Calendar, rules, tradability, fees, and settlement

`MarketRuleSnapshot` owns effective-dated normative market rules, including
Asia/Shanghai sessions, trading phases, tick and price constraints, and
side/action-aware quantity rules. It references, but does not copy,
`MarketFeeSchedule` and `SettlementProfile`.

`TradabilitySnapshot` owns public market facts: suspension, special treatment,
listing-stage rules, current price-limit state, observable liquidity, data
completeness, observation time, and the exact rule snapshot. Being at a price
limit is not itself untradable. The model distinguishes visible opposing
liquidity, `locked_limit_no_opposing_liquidity`, and `liquidity_unknown`.
Account entitlement and route capability remain UTA/Execution facts.

`MarketFeeSchedule` contains statutory and exchange charges.
`PlanningCostAssumption` is a conservative account-independent research input.
`ExecutionCostProfile` owns route/account commission, minimum-fee, and slippage
assumptions. Order-level fees use a cumulative accumulator: every partial Fill
books only the delta between the aggregate fee owed after that Fill and the
amount already booked, and a terminal adjustment remains covered by the
original worst-case reservation. If the exact execution profile invalidates a
plan boundary or edge, the system rejects it and creates a new planning
generation; it never mutates an accepted plan.

`SettlementProfile` is the single settlement authority. It separately defines
share sellability, cash availability, and cash withdrawability. Rule snapshots
reference its ID and hash. Entry artifacts retain the historical profile used
when they were created. Every exit Proposal binds the currently effective
profile and explicitly links any transition from the Position's prior profile;
its Risk, order, fill, and ledger chain then retain that exit profile identity.

Missing, stale, future-dated, incomplete, or mismatched facts fail closed with
stable reason codes. Implementations may not fall back to customary percentages,
lot sizes, or assumed sellability.

### Point-in-time data boundary

Small, causal, authoritative metadata remains in SQLite. Raw and adjusted bars,
quotes, trades, factor inputs, and replay datasets remain in Sidecar-owned
Parquet; DuckDB may query those files locally but is not a service. Aletheia
imports a versioned manifest rather than depending on an external project's
private database schema.

Every event, financial record, membership fact, and corporate action retains the
applicable `event_time`, `published_at`, `effective_at`, `ingested_at`, revision,
source, and content hash. Research-adjusted series and execution raw prices are
separate. Missingness and freshness are reported as complete, partial, stale, or
unavailable, never converted to healthy zero values.

The first corporate-action contract supports only cash dividends and
split/bonus actions. Each action has stable action and revision IDs plus record,
ex, effective, and payable dates. Unsupported or incomplete actions freeze new
entry and place every affected open Position, UTA/account fact, exit sizing,
accounting result, Outcome, qualification sample, and Learning cohort in an
explicit integrity-blocked state until canonical posting or reconciliation
resolves the action. Approximate Position mutation and incomplete exit economics
are forbidden.

External TSP, Qlib, AKQuant, vn.py, provider, and broker code is replaceable.
TSP or another licensed provider may export manifests; Qlib is offline research
only; AKQuant is a differential test implementation rather than an oracle; and
vn.py may be evaluated only in a later broker Sidecar Proposal. None owns Risk,
accounting, permissions, execution, or Learning truth.

### Candidate and Entry Plan V2

A CN Candidate freezes instrument, session, manifest, rule, tradability, fee,
settlement, membership, listing, halt, and point-in-time classification IDs and
hashes. A scanner may publish observation evidence and refusal reasons but may
not create an executable plan or Proposal.

CN plans use a stable `entry-plan-envelope/v2` with a discriminated
`cn-equity-entry-plan/v1` immutable payload. The payload contains a long-only
buy intent, DAY limit tactic, price and time bounds, earliest sell session,
entry/invalidation/stop/target facts, maximum notional and share bounds, stress
loss, participation limit, internal-Paper route, and all market/planning fact
references. It does not bind account state or exact order quantity.

A deterministic compiler consumes exact Candidate facts and a versioned policy.
The planner or LLM supplies only bounded intent; unknown or account-authority
fields are rejected. Exact quantity is computed only at Plan → Proposal handoff
from immutable UTA, portfolio, reservation, entitlement, and execution-cost
snapshots. Risk approves or rejects that Proposal unchanged.

The TriggerSpec key becomes `(market_id, candidate_family, spec_id, version)`.
A spec declares required data, closed-bar semantics, trading phase, freshness,
parameters, failure reasons, and referenced market facts. Before the first
eligible strategy exposure, one immutable experiment identity preregisters the
path/hypothesis/experiment/stage, decision consumer, treatment and control
behavior including static fallback, allowed effect, statistical/assignment unit,
assignment salt and allocation, outcome source/cost/integrity contract, metric
version and horizon, behavior-contract hash and policy epoch, sample/time/
confidence/economic/harm gates, owner admission and termination rules, and every
refutation or invalidation condition required by the Experiment Protocol.
Strategy thresholds belong to that identity and cannot be selected after
viewing its evidence; any identity change starts a new cohort from zero.

### UTA, Risk, and typed reservations

CN account evidence separately represents cash total, available, and
withdrawable balances and position total, sellable, frozen, and pending
quantities plus settlement lots. Completeness, source time, account generation,
SettlementProfile identity, and a versioned `AccountTaxProfile` with investor
tax status and holding-lot acquisition dates are mandatory.

Internal Paper uses a dedicated account and Risk pool with `base_currency=CNY`.
Its cash, positions, reservations, collateral, exposure, leverage, and limits are
segregated from every Crypto/USDT pool; neither may collateralize or net the
other. Cross-pool exposure or PnL aggregation is `not available` in this Epic.
A future combined portfolio view requires a separately approved D1 contract
binding effective-dated FX source/hash and base-currency identity through Risk
and Outcome; it cannot infer or silently treat CNY as USDT.

One persistent reservation repository owns two resource types: CNY cash for buy
Proposals and instrument shares for sell Proposals. Buy reservation equals exact
quantity times worst price plus maximum execution fees; sell reservation cannot
exceed sellable shares. Decimal state preserves:

```text
original = held + consumed + released
```

Paper Risk compare-and-set creates `HELD`; Gateway atomically binds an order and
moves it to `SUBMITTED`; fills consume exact resources and recompute remaining
worst-case held value. Approval TTL may expire and release only a pre-submission
`HELD` reservation. Once `SUBMITTED`, resources remain held until rejection,
cancellation remainder, failure, or reconciliation proves a terminal order
state and releases them atomically; an elapsed approval TTL alone can never
release a live order. Every transition records expected state, actor, resource,
amount, and causal IDs.

Shadow produces only non-executable `RiskEvaluation` and
`RouteValidationResult` artifacts. Route validation does not resolve, register,
or invoke an adapter. Paper submission requires a one-time `RiskApproval` bound
to mode, exact Proposal hash, reservation, TTL, and idempotency capability.
Submitting a Shadow artifact is a hard rejection.

### Gateway, Paper accounting, exit, and corporate actions

Internal Paper is the only execution route in this Epic. It consumes only
Gateway-approved entry and exit intents. It models DAY limit orders,
side/action-aware quantity rules, public liquidity state, partial fills, fees,
T+1 share settlement, typed reservations, restart, and reconciliation.

A fill may consume only market events after order acceptance. The first bar-only
model uses an eligible closed bar whose `open_at >= accepted_at`; it may not read
the final high, low, or volume of a forming same bar. Every Fill stores the exact
event or window hash. Unknown queue/liquidity state yields conservative no-fill
or unavailable evidence rather than invented microstructure precision.

Exit extends the canonical Position close path. Target, stop, expiry, and manual
close produce an exact immutable Proposal bound to the original plan/position,
sellable quantity, current market/account/cost facts, exit reason, and
idempotency key. It passes through RiskApproval and Gateway. A T+1-blocked exit
remains blocked evidence and cannot be fabricated as a fill.

Corporate actions apply only through an append-only
`CorporateActionLedgerEvent` transaction. A cash dividend first records gross
receivable and a worst-case contingent tax liability under the exact
`AccountTaxProfile`; only the conservatively net amount may become available
cash. Sale/lot settlement computes the final holding-period liability and books
the delta. Missing or stale tax identity keeps dividend cash, realized Outcome,
and qualification unavailable. Split/bonus events adjust settlement lots and
cost basis. Both supported action types create an effective-dated,
revision-linked immutable projection for every active stop, target, invalidation,
and other price-based protection fact using the official ex-right/ex-dividend
adjustment. If that projection cannot be proved, the protection and Position
remain integrity-blocked. The original plan is never rewritten; canonical exit
reads the latest complete projection linked to it. Open orders and reservations
are cancelled, released, and reconciled before posting. Revisions reverse and
rebook; callers cannot directly overwrite a Position. If a revision affects a
closed Position, the same append-only correction chain recomputes or invalidates
realized economics and Outcome eligibility before the revision becomes complete,
and Learning excludes the superseded cohort.

### Outcome and qualification truth

Outcome keeps Candidate, event revision, plan, original Position/exit authority,
market and account fact hashes, theoretical/executable/actual prices, fill event,
fees, opportunity return, executable return, realized PnL, blocked/unfilled
reasons, production commit, manifest, and policy epoch. Learning consumes only
closed, complete, attributable cohorts. Backtests, Shadow, and Paper outcomes are
separate evidence classes.

The Epic ends at two independent facts:

- `paper_accounting_qualified`; and
- exactly one of `strategy_paper_promotion_passed` or
  `strategy_paper_promotion_failed` for the same frozen epoch.

A failed economic result does not erase accounting evidence, but it blocks
strategy promotion. Capability Ground Truth must report infrastructure and
strategy status separately and must not claim Live.

## Trust and complexity review

A separate CN stack would duplicate authority and create an unreviewable route
to capital. Patching the current crypto models with scattered `if cn_equity`
branches would instead spread false symbol, session, fee, and settlement
semantics into Risk and accounting. The bounded versioned cuts above are the
smallest design that preserves both safety and semantic honesty.

The highest-risk changes are UTA settlement, atomic reservations, canonical
exit, Paper ledger, and Risk/Gateway handoff. They remain in the existing
controlled paths and require focused failure, concurrency, idempotency, restart,
and reconciliation evidence. Agents and Sidecars retain no credential, Risk,
kill-switch, route-selection, accounting, or live-unlock authority.

The design intentionally stops at two real market implementations. It rejects a
plugin platform, broad instrument taxonomy, generic strategy framework, or
external account system until a third concrete adopter proves that abstraction
necessary.

## Verification contract

Implementation advances only through these gates:

1. **Governance:** this Proposal is accepted; Product Scope, Active Runtime
   Scope, and Phase Gates explicitly admit the bounded work; GitHub Issue #2572
   owns execution state.
2. **Kernel conformance:** CN identity, calendar, rules, tradability, fees, and
   settlement are versioned, reproducible, fail closed, and leave Crypto v1
   behavior unchanged.
3. **Point-in-time data:** manifests replay deterministically; leakage canaries
   remain zero; terms and provenance are acceptable; missing/stale data remains
   unavailable.
4. **Authority-chain conformance:** synthetic Candidate → Plan → exact Proposal
   → Risk → Gateway → Paper entry/exit retains causal IDs and reservation
   invariants; no `live` feature state exists.
5. **Shadow qualification:** an exact Fedora runtime commit and frozen
   data/policy/account epoch produce no execution side effect, stale plan,
   future-data use, or executable Shadow approval.
6. **Paper qualification:** the same frozen epoch proves settlement, fees,
   reservations, entry/exit, corporate actions, restart, reconciliation, and
   causal fill behavior, then records independent accounting and strategy
   results.

Each implementation Issue defines its focused executable proof. macOS does not
run the broad pytest suite. Before publishing a CI-triggering head,
`scripts/ci_plan.py` selects the required lane and `scripts/ci_local.py` runs it.
Runtime qualification occurs only on the owner-selected Fedora Paper runtime
after deploying the exact merged commit.

## Rollout and rollback

Schema adoption follows expand → compatibility reader → feature-disabled
verification → CN producer switch → consumer convergence → current Contract
update. There is no dual write. With the feature disabled, no CN V2 rows may be
created.

Before the first CN V2 write, rollback is configuration or binary rollback to
the prior behavior. After the first V2 write, an old binary that cannot read V2
is forbidden and forward repair/replay plus reconciliation is the default.
Backup restoration is allowed only from a dependency-closed checkpoint captured
under an authoritative write fence, including SQLite and referenced manifests,
with proof that no Crypto or CN order, fill, position, ledger, outcome, or other
authoritative write exists after the checkpoint; otherwise restoration is data
loss and prohibited. Because current Crypto producers remain on v1, the
discriminated v1/v2 reader and required projections remain for the lifetime of
both producer schemas. Removing either requires a separately approved migration,
not expiry of the Paper qualification window.

The runtime state is exactly `disabled | observe | shadow | paper`, defaulting
to `disabled`; no `live` value is introduced. Failure of a gate stops promotion
and creates a new immutable evidence epoch after the root cause is fixed. It
does not edit prior evidence or add fallback semantics.

Data or license ambiguity, uncorrectable point-in-time leakage, inconsistent
official-rule replay, broken ledger invariants, unbounded Sidecar authority, or
loss of Crypto hardening capacity stops the expansion. Each external tool may
be rejected without changing the canonical downstream contract.

## Resolution

Accepted by the owner approval of the execution plan and tracked by Issues
#2572 and #2573. Acceptance authorizes the bounded design direction, not current
support, implementation completion, runtime qualification, strategy promotion,
or Live trading. When the complete Paper behavior is implemented and qualified,
move normative behavior to Product Truth and current Contracts, update
Capability Ground Truth with separate infrastructure and strategy facts, and
mark this Proposal `implemented`.