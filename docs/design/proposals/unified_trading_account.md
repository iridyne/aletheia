---
id: unified-trading-account
status: accepted
github_issue: 2299
related_contracts:
  - docs/design/contracts/execution_contract.md
supersedes:
  - issue-2189-read-only-uta-decision
---

# Complete Unified Trading Account

## Summary

Aletheia will converge all private external-account reads and writes on one UTA
product boundary. Gateway keeps business mutation authority, a generic execution
coordinator owns local claim/evidence/CAS mechanics, UTA performs external I/O
and returns immutable normalized evidence, and Aletheia storage remains local
truth. GitHub Issue #2299 is the closed semantic implementation specification.

## Current fact

UTA owns normalized private reads for all configured profiles and the evidence,
recovery, funding, verified-exit and Trust consumers. Binance testnet, OKX demo,
Bitget Classic v2 and Bitget UTA v3 have configuration-gated execution slices
through Gateway and the generic Coordinator, including their profile-specific
contract, close and protection semantics. The same statically implemented path
is available to live-limited profiles only after the existing Live Trust/account-
equivalence gates and exact qualification pass. No exchange credentials were
available for this work, so every profile remains runtime-unqualified and legacy-
selected; no demo or real-money probe was run.
Existing `submission_unknown`, `materialization_missing`, Risk-before-Gateway
authority and `paper_internal` behavior remain authoritative.

## Goals and non-goals

UTA covers exact account identity and generations, balances, positions,
capabilities, orders, fills, funding, verified-exit evidence, reconciliation,
lifecycle and read-only authority probes. It uses immutable Decimal-safe models,
explicit completeness and `not_sent / venue_rejected / acknowledged / ambiguous`
command evidence.

It does not add venue selection, a second Risk/execution/ledger system,
NautilusTrader or OpenAlice runtime dependencies, new exchanges, unrestricted
live trading, automatic external-position adoption, or dual writes.

## Proposed design

The public `UTAAdapter` surface and canonical semantics are those frozen in
Issue #2299. `CCXTUTAAdapter` is the common crypto driver through
`aletheia.net.make_ccxt`; Binance, OKX and the independent Bitget Classic-v2 and
UTA-v3 profiles contain venue-only translation. Raw CCXT data never crosses the
boundary, capabilities perform no property-time I/O, and every adapter/client is
owned and closed on its creating event loop.

For authorized mutations, the coordinator commits the pending local Order,
performs one UTA call outside any SQLite writer transaction, then applies the
typed result with a short expected-state/CAS transition. Reconciliation and
recovery use the same evidence-outside/CAS-inside order. Incomplete reads never
become empty, zero, cancellation, disappearance or settled economics.

## Trust and complexity review

This consolidation reduces the existing number of private-I/O side channels.
The complete contract is necessary because a read-only account facade cannot
preserve command ambiguity, exact order/fill identity, verified exit, funding or
same-generation Trust binding. Narrow venue profiles are retained only for
semantics the common driver cannot prove.

## Verification contract

Each migration phase requires focused immutable-model, precision, completeness,
lifecycle, identity-generation, parity, command-outcome, concurrency,
reconciliation, funding, verified-exit, Trust, profile and structural tests.
Every switched demo/testnet profile normally requires secret-safe credential-
backed qualification bound to exact code, account generation and authoritative
private Fill evidence. The owner amendment to Issue #2299 waives those unavailable
probes for static completion only; it does not create qualification or activate a
selector. No real-money order is an acceptance gate.
The Binance and OKX demo gate is `scripts/qualify_uta.py`; its execution probe
requires an explicit testnet/demo-order confirmation and derives minimum legal
size from venue constraints. OKX qualification additionally proves native
protection submit/cancel. A missing credential or any unresolved command
produces a non-qualified artifact and cannot authorize selector activation.

## Rollout and rollback

One phase and profile is active at a time. The per-execution-mode selector stays
`legacy` until automated parity and credential qualification pass; `shadow_read`
never mutates. There is no dual write. Before final legacy removal, rollback is
configuration-only and an ambiguous command must be reconciled on the same
external account before any backend change permits another submission.

## Adding a profile within the current exchange scope

A contributor adds one narrow class under `aletheia/uta/profiles/`, registers its
immutable factory in `aletheia/uta/manager.py`, supplies canonical fixture and
conformance tests, and extends the existing secret-safe qualification evidence for
that exact profile and generation. Raw venue parameters stay in the profile. Agent,
Risk, Gateway, Portfolio, recovery and Trust business logic must not change merely
to add the profile; needing such a change means the frozen contract is insufficient
and requires an Issue amendment before implementation.

## Resolution

Accepted by the owner-authored, semantically closed Issue #2299. When all modes
are qualified and cut over, move current behavior into the execution and account
contracts and mark this Proposal implemented.
