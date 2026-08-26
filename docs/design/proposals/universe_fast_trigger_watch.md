---
id: universe-fast-trigger-watch
status: implemented
related_contracts:
  - ../contracts/decision_lifecycle_contract.md
  - ../contracts/execution_contract.md
  - ../contracts/risk_engine_contract.md
  - ../contracts/filter_evidence_contract.md
supersedes: []
---

# Universe Fast Trigger Watch

> Proposal status: implemented in this worktree, not a deployment or
> economic-qualification claim.

Repository implementation includes Opportunity Generation V2, terminal entry
validity evidence, and observation-only market-structure assessments. Exact-commit
Fedora latency/no-LLM evidence and prospective Shadow qualification remain
deployment/economic gates; no deployed or profitable claim is made.

## Summary

Momentum-ranked Universe members should become durable, expiring setups before
they become Opportunities. The System Operator may publish a bounded
conditional authorization for one exact setup; a deterministic, no-LLM fast
trigger then uses fresh same-venue public market data to materialize one
immutable Opportunity and TradeProposal at the actual entry instant. The
existing Risk Engine and Execution Gateway remain the only order authority.

## Current fact

`UniverseService.refresh()` currently publishes selected members and calls
`emit_deterministic_opportunities()` in the same SQLite publication unit.
Momentum members therefore become Opportunities before a pullback/reclaim
entry exists. The current Proposal, RiskDecision, Order, Fill, and Position
causal chain remains authoritative, and `execute_existing_proposal()` already
keeps Risk and broker I/O outside its short execution-claim transaction.

## Goals and non-goals

Goals:

- introduce the sequence `Momentum Candidate -> Setup -> conditional
  authorization -> Fast Trigger -> Opportunity -> Proposal -> Risk -> Gateway`;
- preserve exact `venue + symbol + market_type` identity through every fact;
- keep the trigger hot path deterministic and free of LLM/provider calls;
- fail closed on unavailable adapters, stale or unordered data, expired or
  changed setups, and uncertain submission;
- start with a generic stream contract and a Bitget public-stream adapter.

Non-goals:

- no Live execution, credential access, new broker, Risk weakening, or Gateway
  registry refactor;
- no trigger authority for the other Universe families;
- no automatic Shadow-to-execute promotion;
- no REST polling substitute for a failed real-time stream.

## Proposed design

### Durable setup and authorization

`UniverseSetup` stores the exact snapshot/member/instrument identity, rank,
Momentum material fingerprint, regime, breakout level, ATR, pullback band,
entry zone, structural invalidation, TriggerSpec identity, expiry, and state.
Its state transitions use compare-and-swap:

`pending_authorization -> armed -> pullback_seen -> triggering -> executed`

Terminal alternatives are `refused`, `expired`, and `invalidated`. A setup may
own at most one authorization and one Proposal.

`ConditionalEntryAuthorization` stores one System Operator decision id plus the
authorized `shadow|execute` mode, frozen direction, TriggerSpec hash, entry zone, invalidation, spread/depth and
risk/leverage/RR/horizon envelope, and a validity interval no longer than 60
minutes. It is a formula authorization, not a future order: it contains no
quantity, absolute entry, TP, or final RR.

The first TriggerSpec is `momentum-retrace-reclaim/v1`. It uses 21 completed
15-minute bars: the first 20 define the directional breakout level, the final
bar must close outside it, ATR is `last_close * atr_15m_pct`, pullback/entry use
0.25 ATR, and invalidation is one ATR beyond the level. Any instrument,
direction, regime, fingerprint, or structure change creates a new setup rather
than mutating the old generation.

### Stream and trigger

`FastMarketStream` exposes `connect`, `set_instruments`, `events`, `health`, and
`close`. `FastMarketEvent` represents quote, trade, depth, or candle data and
always carries exact instrument identity, exchange and receive timestamps, and
a channel sequence identity.

The first adapter uses Bitget's unauthenticated public WebSocket and subscribes
to ticker, public trades, books5, and candle1m for at most ten ranked armed
setups. Other configured venues return
`fast_trigger_stream_unavailable:<venue>`; cross-venue substitution is
forbidden.

A setup may trigger only after entering its pullback band and then reclaiming
the breakout level on two valid quotes at least 300ms apart. The rolling
10-second taker-buy ratio must be at least 60% for long or at most 40% for
short; spread and 10bps depth must satisfy the authorization; and the
executable ask/bid must remain inside the entry zone. Missing subscription
acknowledgement, sequence regression, or quote/depth older than one second or
trade data older than two seconds blocks triggering.

### T0 materialization and execution

Long uses current ask and short current bid. Stop is the authorized structural
invalidation; TP is calculated from actual entry at the authorized target RR.
Quantity is derived from the existing available-balance risk sizing seam, and
leverage is capped by both authorization and existing asset policy.

One short SQLite transaction revalidates setup and authorization identity/TTL,
CASes `armed|pullback_seen -> triggering`, persists the exact quote/trade/depth
snapshot, and creates one immutable Opportunity and one complete
TradeProposal. Risk and broker I/O occur only after commit through
`execute_existing_proposal()`. Risk refusal, price escape, and
`submission_unknown` never cause automatic resubmission.

`fast_trigger.mode` is `off`, `shadow`, or `execute`; the shipped default is
`shadow`, which observes and prewarms without Gateway order authority. In
`paper_internal`, `execute` uses the configured exact public market-data venue for the local paper
account and local paper broker; it never submits an exchange order. For
exchange-backed execution, the first executable mode is `bitget_demo` only;
Live modes are always refused. Shadow persists the same trigger facts but does
not call the Gateway.
An authorization is bound to the mode in force when it was issued, so changing
Shadow to execute cannot convert an already-armed Shadow setup into order authority.

Every new execution-eligible Crypto Opportunity carries canonical
`opportunity-generation/v2` JSON and SHA-256 over exact instrument, family/archetype,
direction, T0 mark, motive, entry trigger/zone, invalidation, horizon, cost snapshot,
material fingerprint, and deadline. SQLite keeps that generation immutable and
retained while lifecycle status advances independently. Pre-Gateway entry-zone
escape or structural invalidation permanently ends the generation; a later setup
must create a new Opportunity rather than repricing the old one.

`market-structure/v1` computes confirmed swings and BOS/CHoCH from continuous,
closed same-venue 1m bars. It is persisted as append-only Shadow evidence only;
the production hard-gate allowlist is empty. The shared gate seam exists for a
future separately approved policy version and fails closed for unqualified or
unavailable assessments.

## Trust and complexity review

This design adds durable trading-intent state and a real-time public-data
consumer, so a D1 patch is insufficient. Reusing Opportunity as a setup would
preserve the current semantic error and allow the existing ranker to treat a
non-entry as executable. Reusing Bitget prices for another configured venue
would break exact-market evidence. The new state is bounded to ten subscriptions
and one setup generation, uses the existing SQLite writer discipline, and does
not introduce a broker abstraction or dependency beyond the installed HTTP
stack.

## Verification contract

Repository evidence must cover long and short setup construction and
pullback/reclaim transitions; authorization expiry and identity mismatch;
stale, missing, or out-of-order quote/trade/depth; Bitget parsing,
acknowledgement and ping handling; concurrent duplicate materialization; Risk
refusal and unknown submission without retry; and the complete causal chain to
both a local `paper_internal` fill and a mocked Bitget Demo Gateway call.

Runtime acceptance additionally requires a Fedora measurement showing
WebSocket-frame-to-Gateway-call p95 at most 250ms and zero LLM/provider calls in
the trigger hot path. Economic execution remains disabled until prospective
Shadow has at least 100 eligible Momentum candidates, 50 triggers, seven
consecutive UTC market dates, positive paired 4h cost-after delta, no
determinate downside harm, and complete venue/behavior/cost/runtime identity.

## Rollout and rollback

Default `shadow` records evidence without order authority; `off` is an explicit
disable. Owner review is required before `execute`; local Paper may execute
through the local broker when its configured public data venue has an adapter,
while the first exchange-backed adapter is Bitget Demo only. Rollback sets mode
to `off`, closes the public stream, and leaves all setup/authorization/trigger
history intact.

## Resolution

Accepted: conditionally authorize exact Momentum setups, materialize entry
facts only at a same-venue deterministic trigger, and preserve Risk/Gateway as
the execution authority. When repository and runtime behavior converge, move
the normative state and failure semantics into the decision, execution,
filter-evidence, and data contracts and mark this Proposal implemented.
