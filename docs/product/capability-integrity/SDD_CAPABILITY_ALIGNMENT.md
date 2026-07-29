# SDD and Product Capability Alignment

This review asks whether the executable SDD system can be used as product
capability truth. It reviews the same current worktree and capability inventory
as `CAPABILITY_GROUND_TRUTH.md`.

## Current facts

| System | Unit | Current result |
|---|---|---:|
| Executable SDD | Discovered spec modules | 100 |
| Executable SDD | Registered claims | 1,142 |
| Executable SDD | Conforming / partial / missing / superseded claims | 1,115 / 12 / 14 / 1 |
| Capability Ground Truth | Product capabilities | 100 |
| Capability Ground Truth | Supported / Implemented / Partial / Experimental / Broken | 68 / 4 / 6 / 21 / 1 |

These numbers measure different things. A claim protects one invariant, while a
capability requires an entry, production trigger, durable flow, final consumer,
observable result, failure path, executable acceptance, and inclusion in the
declared support surface. Claim counts therefore cannot be converted into a
product-support percentage.

## Confirmed alignment classes

| Class | Capability examples | Finding |
|---|---|---|
| Correctly scoped local claims | Risk gates, paper execution, causal identity, persistence invariants | SDD is useful and prevents implementation drift. Ground Truth adds the product-level final-result and support-surface decision. |
| Complete implementation outside support | EX-06, EX-12, EX-13 | Conforming claims are accurate, but external exchange execution is not part of `crypto-paper-mainline-v1`; the capabilities correctly remain `Implemented`. |
| Intentional experiments | Serenity, shadow trades, Learning experiments, live preparation | Conforming subpaths do not make the experiments Supported. This is a product-scope decision, not an SDD failure. |
| Reference implementation without a product consumer | LN-17, EX-15 | Persona pyramid and scale-in helpers are explicitly Experimental and blocked from production mutation. |
| Honest safety experiment | LN-16, LN-18 | `PS-S10` proves consumer filtering and distress refreeze; `PS-S13` proves the scheduled rollout is read-only. Neither lifetime closes nor elapsed time become prospective evidence for the current Persona/policy, so both remain Experimental. |
| Composite capability corrected by boundary | LN-14 | `PS-S03` proves persona notional changes sizing; `PS-S14` crosses the result into immutable Proposal quantity. Settings assigns leverage/stop/add ranges to separate experiments instead of treating them as base sizing. |
| Honest non-behavioral boundary | DC-13 feedback | `SP-S07` protects the owner-facing statement that More/Less is durable history, not a research-adaptation consumer. |
| Existing producer/consumer join | LN-03 Dreaming | Dreaming emits `trade_case_study` lessons; review marks qualified lessons applied; focused acceptance crosses that exact durable population into Layer3 and the Operator prompt. |
| Cash-flow adjustment remains separate | PF-03 | `AN-S06` protects distinct raw/adjusted/PnL semantics and `AN-C06` proves the public Capital Event consumer. |
| Known gap represented explicitly | EX-04 | `LU-S03` protects the experimental authority-only UI semantics and `LU-C07` remains a missing tripwire until live state participates in new-Proposal mode selection. |
| Honest but incomplete boundary | EX-09 | `execution_contract.md`, Settings, `PL-PM-14`, and runtime tests agree that market-aware evaluation emits a best-effort wake and never directly mutates positions. They do not establish a wake-driven Operator position decision. |

## Current non-experimental delivery cross-check

All 7 current Partial/Broken capabilities, plus recently repaired delivery
boundaries, were checked against the registered claim set and human-readable
contracts.

| Capability | Product status | What SDD actually establishes | Alignment verdict |
|---|---|---|---|
| RT-10 Health, readiness, Doctor, stability | Supported | Health/Doctor invariants, serving-Runtime attribution, two-context isolation, and API contracts are executable. | Default-branch delivery resolves #426; no missing capability link remains. |
| RT-14 Tunnel lifecycle | Implemented | `API-S08` proves auth fail-closed; focused lifecycle tests verify start/status/stop and PID-reuse refusal. | Executable and config-command identity close the ownership gap; natural cross-platform dogfood remains outside core. |
| EX-04 New-proposal live execution mode | Broken | Live unlock/relock, audit, scorecard, fail-closed Risk behavior, and the authority-only UI boundary are protected. `LU-C07` explicitly records the absent mode-selection join. | Honest downgrade complete; #418 remains open and live trading remains Experimental. |
| EX-09 Market-aware position review | Supported | Evaluator wake-only authority, exact-position review routing, wake evidence, hold/adjust/close materialization, unavailable-result persistence, and delivery-failure visibility are executable. | Complete bounded paper path; it reuses the existing portfolio-review command and adds no second Agent, queue, or scheduler. |
| EX-14 Local paper cost-complete close | Supported | Exact local fills, fees, funding windows, atomic cash settlement, recovery, Outcome, Attribution, and Learning eligibility are executable. | Complete inside the `paper_internal` Supported Core boundary. |
| EX-16 Exchange-backed cost-complete close | Implemented | Private ledger normalization, exact attribution, atomic settlement, delayed recovery, Outcome hooks, and fail-closed boundaries are executable. | Bitget/OKX/Binance code paths are complete; natural venue dogfood remains outside Supported Core. |
| EX-15 Partial reduce and scale-in | Experimental | Active entry actions reject unsupported reduce/scale forms; the dispatcher fails closed and the OKX helper is isolated. | Reference-only surface; no authorized scale capability is promised. |
| PF-03 Capital-adjusted curve | Implemented | `DM-S07`, `AN-S06`, and `AN-C06` cross persistence into a separate public adjusted series without changing verified PnL. | Complete executable path; natural UI dogfood remains unclaimed. |
| LN-17 Persona pyramid decision | Experimental | `PS-S11`/`PS-S12` prove the bounded reference helper and blocked-enablement boundary. | No production caller is intentional while the experiment is held. |
| LN-19 Policy history and diff | Supported | `LP-S22` creates two immutable versions and crosses the public diff, detail, and history endpoints. | Route-order defect repaired without adding another API; no missing product link remains. |
| OS-03 Exchange credential verification | Implemented | Secret handling, masked reads, private account endpoint usage, client cleanup, and route safety are protected. | Deliberately outside Supported Core; external venue availability is not claimed. |

The remaining non-Supported capabilities are fully classified elsewhere:
four Implemented rows are complete but deliberately outside the supported
paper profile, and all 21 Experimental capabilities are intentionally bounded
experiments. Their implemented subclaims are not product-support declarations.

## What SDD is good for

- precise trust-boundary and behavioral invariants;
- executable regression protection for known seams;
- explicit missing/partial tripwires;
- keeping code and an approved technical contract aligned.

## What SDD does not establish

- that every public promise has a production trigger;
- that a producer has a final consumer;
- that a route is reachable through the real router;
- that a helper is called by the production lifecycle;
- that an implemented experimental path belongs to the supported product;
- that the full capability produces the promised owner-visible result.

The Conformance Matrix therefore reports a **claim conformance ratio**, not
“coverage.” Product status remains authoritative only in
`CAPABILITY_GROUND_TRUTH.md`.

## First-principles conclusion

The SDD system is useful and should remain, but only as a local contract and
regression layer. It should not be expanded into a second capability database,
a claim-to-capability registry, or a new runtime detector. The existing
Ground Truth already owns end-to-end status. The minimal integration is:

```text
SDD claim → proves a named local invariant
Capability fact card → verifies the complete observable path
Ground Truth → decides product status and supported scope
```

This division preserves the Alpha-first boundary: tests protect trading
correctness, while product truth prevents green intermediate checks from being
mistaken for a working trading capability.
