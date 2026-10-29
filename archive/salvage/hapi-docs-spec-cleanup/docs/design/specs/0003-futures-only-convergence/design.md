# 0003 — Converge the code onto the futures-only trading mainline: design

Answers [spec.md](spec.md). Every section cites the requirement ids it serves.

## Overview

This is a deletion spec. Each requirement removes a branch, default, job or module
whose only users are closed scopes, and replaces at most one of them with a single
typed rejection. Nothing new is added to the mainline.

```text
config load ──► normalize enabled_markets (FC-R01)
registry    ──► perp only (CS-B1, unchanged)
Planner     ──► no spot block (FC-R02)
sizing      ──► no spot branch (FC-R02)        ──► one sizing path (FC-R07)
Risk #2     ──► market_type_out_of_scope (FC-R02)
market data ──► no spot fallback chain, no Bybit default (FC-R03)
scheduler   ──► no equity/prediction/persona jobs; news only with Serenity (FC-R04, R06, R07)
renderers   ──► deleted or given a caller (FC-R05)
dead code   ──► deleted (FC-R08)
```

## Data model

No schema change (FC-R01–R08). Historical rows keep their stored `market_type`,
`asset_class` and producer labels and stay readable; no table is dropped or
rewritten, because applied migrations are frozen
([data_model.md §6](../../data/data_model.md)). Tables whose writers are deleted keep
their retention rules until a later, separate schema spec drops them.

## Components and flow

### Configuration (FC-R01)

`config/schema.py::ExchangeConfig.enabled_markets` default becomes
`{"usdt_futures": True}`. `config/loader.py` removes a persisted `spot` key during
load, writes `config_scope_normalized` once per persisted change, and the doctor
config probe reports it as `info`.

### Spot residue (FC-R02)

Delete the spot leg of Risk check #2, the `invalid_spot_leverage` check, the
`not_applicable_spot` sizing branch and the Planner's `spot_short_not_allowed`
block. Risk check #2 becomes one predicate: `market_type == "usdt_futures"`, else
`market_type_out_of_scope`. Historical readers that decode stored spot rows stay.

### Market data (FC-R03)

`extensions/data/ccxt_provider.py::get_market_data_provider` stops wrapping the
venue provider in the CoinGecko/Binance spot chain; `enable_crypto_fallback` and
its config field are deleted. `market_data.ccxt_fallback` default becomes `""`,
matching what `config/exchange_alignment.py` already writes.

### Out-of-scope producers (FC-R04)

Frozen behind one default-off flag, `research.frozen_observers`: when false, neither
`equity_observer` nor `prediction_observer` is registered, the `/prediction/*` routes
are not mounted and the Prediction page is hidden. Registration code exists twice
(`main.py`, `server/app.py`); it moves to one site that reads the flag. CN Equity's
proposal is marked on hold.

### Data consumers (FC-R05)

Macro, breadth and cross-venue facts get a consumer through spec 0004, which also deletes
the dead renderers. On-chain stays observation-only with its reason recorded in
`data_utilization.md`. A `check_docs` rule fails when the registry contains an ❌
row, so the state cannot drift back.

### News (FC-R06)

Register `news_fetch` and `news_analyze` only when `serenity.enabled` is true.

### Sizing (FC-R07)

Delete `trading/persona.py`, `trading/persona_rollout.py`, `trading/sizing_policy.py`,
the `persona_rollout` job and the Settings persona fields; `trading/entry_sizing.py`
stays the only sizing implementation.

### Zero-open alert (FC-R09)

`analytics/go_live_scorecard.py::count_consecutive_zero_fill_days` returns a typed
`unavailable` on error instead of `0`. The existing `health_check` job reads it and raises
`candidate_entry_stalled` through the existing alert sink once per stall (cleared when a
position opens). No entry is paused.

### Dead code (FC-R08)

Persona (FC-R07) and trading mandate (FC-R08) are on hold: their jobs and Settings controls
are unregistered, code and tables stay. Delete `portfolio/position_manager.py::reduce_position_routed` and the Windows
gateway service modules (`gateway/windows_*.py`, `_win32_proc.py`) with their
imports in `gateway/service_control.py`. Trading mandate and K1/K2 follow the
clarification.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Persisted config has `spot: true` (FC-R01) | key dropped, runtime starts | `config_scope_normalized` audit + doctor info | n/a |
| Spot `market_type` reaches Risk (FC-R02) | reject | `market_type_out_of_scope` violation | n/a |
| Venue market data unavailable (FC-R03) | no spot substitute; existing typed unavailable | existing provider error | unavailable, never a spot price |
| Serenity off (FC-R06) | news jobs absent | scheduler job list | n/a |

## Trust and complexity review

Every change shrinks a surface; none adds authority. The one new behavior is config
normalization (FC-R01), chosen over a validation error so that an existing runtime
with `spot: true` (written by the old default) keeps starting. Deleting rather than
flagging follows the convergence rule in root `AGENTS.md`; a flag is offered only
where the owner has not yet decided (FC-R04, R08).

## Alternatives considered

- **Leave residual branches, rely on the registry.** Rejected: a default
  (`spot: True`) or a later registry change silently reopens spot, and readers keep
  being told spot is a product path.
- **Validation error on `spot: true`.** Rejected: it would stop a deployed runtime on
  upgrade for a value the old default wrote.
- **Feed the orphan data into the Planner now.** Out of scope: changing Planner input
  changes decisions and needs its own spec and measurement.

## Traceability

| Requirement | Design section | Verification | Evidence type |
|---|---|---|---|
| FC-R01 | Configuration | `tests/specs/spec_futures_only.py::FC-R01` (fresh default; persisted spot normalized + audit) | deterministic test |
| FC-R02 | Spot residue | `tests/specs/spec_futures_only.py::FC-R02` (Risk rejects spot with one token; AST search for removed tokens) | deterministic test |
| FC-R03 | Market data | `tests/specs/spec_futures_only.py::FC-R03` (provider chain composition; default) | deterministic test |
| FC-R04 | Out-of-scope producers | `tests/specs/spec_futures_only.py::FC-R04` (registered job ids; served route table) | deterministic test |
| FC-R05 | Data consumers | `check_docs` orphan-row rule + `tests/specs/spec_data_utilization.py` | deterministic test |
| FC-R06 | News | `tests/specs/spec_futures_only.py::FC-R06` (job ids by `serenity.enabled`) | deterministic test |
| FC-R07 | Sizing | `tests/specs/spec_futures_only.py::FC-R07` (job ids; Settings payload) | deterministic test |
| FC-R09 | Zero-open alert | `tests/specs/spec_kill_conditions.py::FC-R09` (3 empty days → one alert; query failure → unavailable) | deterministic test |
| FC-R08 | Dead code | `tests/specs/spec_futures_only.py::FC-R08` (AST: no definition) + `tests/test_service_control.py` | deterministic test |

## Rollout and rollback

Land as independent PRs in requirement order R01→R08; each is a pure deletion or a
one-predicate change and is reverted by reverting its PR. FC-R01 must land before
FC-R02 so no runtime still has spot enabled when the branches disappear. No
observation window is opened or reset by this spec: none of the removed code is a
decision input of the Entry Planner.
