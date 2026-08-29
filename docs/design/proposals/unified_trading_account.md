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
recovery, funding, verified-exit and Trust consumers. Binance testnet also has a
configuration-gated execution slice through Gateway and the generic Coordinator;
it remains legacy-selected until credential-backed qualification succeeds. Other
OKX demo now has the same configuration-gated entry, full-close and native
protection slice, including exact contract conversion and demo client identity.
Both profiles remain legacy-selected until their own credential-backed
qualification succeeds. Bitget mutations and all live-limited execution remain
on their migration paths.
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
Every switched demo/testnet profile additionally requires secret-safe
credential-backed qualification bound to exact code, account generation and
authoritative private Fill evidence. No real-money order is an acceptance gate.
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

## Resolution

Accepted by the owner-authored, semantically closed Issue #2299. When all modes
are qualified and cut over, move current behavior into the execution and account
contracts and mark this Proposal implemented.
