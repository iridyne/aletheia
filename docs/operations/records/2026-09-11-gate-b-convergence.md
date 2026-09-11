# Gate-B Phase 1 census — 2026-09-11T05:44:16.893883+00:00

Owner-authorized task: Executable EntryPlan → Bitget Demo Gate B convergence. This supersedes the earlier task ordering for this batch; no change to Candidate Utility/capacity, Alpha/Prompt, Risk limits, venue/market or Live. Main tracking stays here in #2899.

Baseline master and Fedora: fbb187161cc023d93c872273717fbdeb3d6aad69, schema v84, Bitget Demo. Census includes **all 20 natural EntryPlans since #3091 deployment**, with full Generation/frozen Hypothesis/Plan/monitoring/activation/Proposal/Risk/claim/Order/Fill/Position/Close joins. No Orders, Fills, Positions or closes exist for this cohort.

| Plan | Instrument | Unique observed first breakpoint | Classification |
|---|---|---|---|
| `5f1d07499d60` | KORU/USDT perp short | entry_plan_price_invalidated | market/identity lifecycle state |
| `5a390d5d1a85` | QQQ/USDT perp long | Risk: stop_distance_below_vol_target:0.0454<1.5000 | static feasibility split |
| `2d10cca65205` | MRVL/USDT perp short | entry_plan_price_invalidated | market/identity lifecycle state |
| `7e7ab794580e` | AMD/USDT perp short | Monitoring: trade channel_event_stale; passive not yet eligible | temporary dependency / waiting |
| `e111421781eb` | DRAM/USDT perp short | entry_plan_price_invalidated | market/identity lifecycle state |
| `b6b1264e48b5` | PAXG/USDT perp long | Risk: stop_distance_below_vol_target:0.0782<1.5000 | static feasibility split |
| `8d839ccd256e` | NVDA/USDT perp long | Risk: stop_distance_below_vol_target:0.0975<1.5000 | static feasibility split |
| `f82449f75295` | BTC/USDT perp long | Risk: stop_distance_below_vol_target:0.2616<1.5000 | static feasibility split |
| `00f47f3a39b0` | AAVE/USDT perp long | Risk: stop_distance_below_vol_target:0.6186<1.5000 | static feasibility split |
| `a79900c13286` | ANTHROPIC/USDT perp short | Risk: entry_plan_max_notional_exceeded | Risk handoff mismatch; replay pending |
| `2a98732bd72d` | NEAR/USDT perp short | material_input_changed | market/identity lifecycle state |
| `3e00564885e2` | SAMSUNG/USDT perp short | Risk: stop_distance_below_vol_target:0.3416<1.5000 | static feasibility split |
| `065a10b4efe4` | DOT/USDT perp long | Risk: stop_distance_below_vol_target:1.3798<1.5000 | static feasibility split |
| `9f98f1f4725f` | NEAR/USDT perp short | Risk: stop_distance_below_vol_target:0.4462<1.5000 | static feasibility split |
| `1714ea8653ee` | AAVE/USDT perp long | Risk: stop_distance_below_vol_target:1.2991<1.5000 | static feasibility split |
| `2e1719f1b837` | LTC/USDT perp long | Risk: stop_distance_below_vol_target:0.6255<1.5000 | static feasibility split |
| `5b6e388e351b` | CRCL/USDT perp long | Risk: stop_distance_below_vol_target:1.3361<1.5000 | static feasibility split |
| `b747a7100224` | FARTCOIN/USDT perp long | Risk: entry_plan_max_notional_exceeded | Risk handoff mismatch; replay pending |
| `93746d4595c0` | APT/USDT perp long | entry_plan_price_invalidated | market/identity lifecycle state |
| `ab278cd6bb37` | LINK/USDT perp long | Risk: stop_distance_below_vol_target:0.7524<1.5000 | static feasibility split |

12 Plans reached a lawful canonical Risk floor refusal (the upstream deterministic geometry split); 2 passed initial Risk but failed subsequent max-notional verification; 5 invalidated through market/material state; 1 waits on monitoring/passive eligibility. No no-fill state is automatically called a bug.

The five historical Activation black boxes are retained: `17f982a5260a`, `ac765f0066e4`, `d80a2f96c436`, `54da9d828e0d`, `96403750da78`. All five frozen Candidate context hashes differ from their retained last_candidate_id comparison rows. This points to `entry_plan_candidate_opportunity_mismatch`; replay must verify preceding guards before claiming unique historical attribution. Historical result rows will not be rewritten.

Next: restore bounded Activation reasons; converge immutable Spot-short capability and structural stop feasibility with existing Risk helpers, retain final Risk backstops; classify all other Risk rejection branches and reproduce the two max-notional handoffs. Exact evidence packet: `/Users/yixian/.codex/artifacts/aletheia/gate-b-20260911/`.


## Current-head canonical Risk classification

Audit source: `risk/engine.py` native-intent validation, `_entry_plan_authority_violations`,
`_evaluate_proposal_locked` and its quality/lesson/correlation/leverage/liquidation helpers.
This classifies rejection families, not just the 20-plan observed reasons.

| Risk rejection family | Creation-time static fact or changing state | Upstream treatment |
|---|---|---|
| Native symbol/quote/market/direction/order/mode, finite positive price/quantity/leverage, exit-intent fields | Static shape | Existing Universe USDT registry/eligibility, EntryPlan validation, Compiler and order-intent construction; no second validator introduced |
| Spot short | Immutable instrument capability | One predicate shared by pre-model Planner, Compiler and Risk |
| Missing/inverted stop/target, mismatched or implausible RR | Static geometry | Existing EntryPlan validators and shared RR helpers retained |
| Structural stop below Demo floor | Static geometry against creation-time approved floor | Compiler calls existing floor helper; Risk rechecks current configuration/published regime |
| Plan hash/provenance/receipt/identity/tactic/stop-source mismatch | Trust-boundary integrity | Existing checked immutable Plan and receipt; final Risk backstop retained |
| Plan superseded/expired/status/activation/entry-zone/worst-price/passive eligibility | Lifecycle or current market | Remains at activation/execution/Risk; cannot be promised at creation |
| Max-notional after venue quantity normalization | Deterministic handoff split observed twice | Normalize bounded Entry quantity downward; retain exchange minima, max quantity and final Risk |
| Plan policy revoked/max leverage/max risk | Frozen budget plus current policy/account | Existing bounded Compiler sizing; current authority and exposure remain Risk's decision |
| Symbol allow/block list, enabled markets, allow-short flag, permissions, allowed exchange/account mode, Live identity | Mutable owner configuration/authority | Existing producer/route authority and final Risk; no config or Live expansion |
| Kill switch/brake, daily loss, capital/equity missing, single-trade risk and margin | Account/policy state | Final Risk only; Compiler's existing budget availability gate is not an approval |
| Positions, same-symbol exposure, unresolved entries, reservations, position count and portfolio/per-symbol caps | Dynamic holdings and concurrency | Risk only, including authoritative re-evaluation after preparation |
| Market freshness, Demo entry quality, regime/OOD, family Live qualification | Time-dependent market/qualification | Existing final Risk only; no relaxed quality threshold |
| Learned lesson constraints, correlation/sector/BTC beta | Dynamic treatment/portfolio/evidence | Existing Risk only |
| Account leverage ratio | Dynamic account exposure | Risk only |
| Liquidation buffer | Leverage/instrument formula plus configured buffer | Current Compiler LOW/MEDIUM leverage caps remain 2/3; existing final helper retained, no observed failure in this cohort |

## Historical Activation attribution and repaired owner

Retained inputs/hypotheses for all five cases replay through the ordered activation
checks to `entry_plan_candidate_opportunity_mismatch`. Their planning input identifies
the replacement `last_candidate_id`, while `candidate_opportunity_context` comes from
the immutable original generation birth proof. Publication changed that comparison
Candidate before input binding; it did not update the birth context, correctly.
The repair deletes this retargeting write and retains the existing explicit
Candidate/cycle association. Adding only an input-bind race check would not repair
this already mixed context.

Replay restores historical claim/time and old identity-pair preconditions, and
uses a stub risk budget only to reconstruct a Plan for the identity checks.
It reproduces the first failure from retained evidence; it does not recover lost
historical exception text or rewrite result rows. Evidence: `gateb-replay.py/json`
in the control-host packet.

Two quantity handoffs are directly persisted: ANTHROPIC proposal 0.02177243881066086
at 2032.08 (Plan cap 44.24333745836772) was normalized to 0.03; FARTCOIN
264.9848287124447 at 0.1407 (cap 37.28336539984097) became 265.0. Both preflights
approved and both authoritative decisions rejected the increased notional.

This record is a pre-deployment baseline and implementation rationale. Post-fix
runtime census, Order/Fill/Position/Close proof and remaining negative results
must be recorded in #2899; passing local tests is not Gate-B completion.
