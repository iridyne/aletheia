---
id: 0002-futures-only-convergence
title: Converge the code onto the futures-only trading mainline
status: draft
depth: D1
prefix: FC
issue: 3930
contracts:
  - ../../contracts/risk_engine_contract.md
  - ../../contracts/entry_sizing_and_financing_contract.md
  - ../../contracts/execution_contract.md
  - ../../contracts/scheduler_contract.md
  - ../../data/data_utilization.md
supersedes: []
superseded_by: []
---

# 0002 — Converge the code onto the futures-only trading mainline

## Summary

The product trades only USDT-margined crypto perpetuals on Bitget through one
mainline (Universe → Candidate → Entry Plan → Risk → Gateway → Position → verified
close). The documentation review of 2026-10-10 found code that still serves scopes
the owner has closed: spot branches and a `spot: True` default, a spot-only market
data fallback with a Bybit default, out-of-scope observers, data collected for a
retired LLM consumer, a second sizing implementation and functions with no caller.
This spec removes them so that each capability keeps one live implementation and no
default can reopen a closed scope.

## Problem and evidence

All evidence is in the frozen
[documentation review](../../../research/audits/doc-golden-standard-2026-10-10/README.md);
the facts this spec acts on:

- Spot is out of scope (owner 2026-10-09,
  [DECISIONS](../../../governance/decisions/DECISIONS.md)) and the registry never
  admits it (`universe/registry.py`, contract CS-B1). Yet
  `config/schema.py::ExchangeConfig.enabled_markets` defaults to
  `{"spot": True, "usdt_futures": True}`; Risk still evaluates
  `spot_market_disabled` and `invalid_spot_leverage` (`risk/engine.py`); sizing keeps a
  `not_applicable_spot` branch; the Planner keeps `spot_short_not_allowed`.
- `extensions/data/ccxt_provider.py` installs a CoinGecko/Binance spot fallback chain by
  default (`enable_crypto_fallback=True`) and `market_data.ccxt_fallback` defaults to
  `"bybit"`, although `config/exchange_alignment.py` clears it when the account is
  aligned. Perpetual requests never use that chain.
- `equity_observer` and `prediction_observer` are registered whenever
  `extensions.research` imports (`agent_runtime/scheduler.py`), and the prediction
  surface ships 15 `/prediction/*` routes and a page; the product excludes equities and
  prediction markets. CN Equity has an accepted proposal and zero code.
- The Entry Planner's model-visible inputs are only Candidate feature fields, the exact
  order book, structure windows and `entry-learning-context/v1`
  (`trading/entry_analysis.py`). `render_enriched_context_md`
  (`data/context_builder.py`) and `trading/market_context.py::build_market_context`
  have no production caller, so macro, fear/greed, top movers, on-chain, Hyperliquid and
  yfinance macro rows are collected for no decision consumer
  ([data_utilization.md](../../data/data_utilization.md)).
- `news_analyze` runs every 5 minutes on the single `agents` worker that the Entry
  Planner job also uses; its only consumer is Serenity, which defaults to off
  (`config/schema.py::SerenityConfig.enabled=False`).
- The entry sizing contract excludes persona sizing from the mainline, while
  `persona_rollout` still runs every 8 hours.
- No production caller: `portfolio/position_manager.py::reduce_position_routed`,
  `assess_trading_mandate_fit`, and the K1/K2 kill functions in
  `analytics/go_live_scorecard.py`. `gateway/windows_*.py` and `_win32_proc.py`
  (1038 lines) remain although Windows is never a runtime target (root `AGENTS.md`).

## Goals and non-goals

Goals:

1. No default, branch or job can create, size, approve or display a spot, equity,
   prediction-market or CN position.
2. Every scheduled data collector has a named decision consumer or an explicit,
   owner-accepted observation-only reason.
3. One sizing implementation, and no function on the trading path without a caller.

Non-goals:

- No change to Risk limits, leverage bounds, Live unlock, credentials or the Gateway.
- No migration rewrites history: historical spot/equity/prediction rows stay readable
  under their original identity (data layer is additive-only).
- Frozen OKX/Binance routes keep their exposure-recovery and history paths.
- Learning stores are out of scope here (spec 0001).

## Glossary

| Term | Definition |
|---|---|
| Mainline | Universe → Candidate → Planning Generation → Entry Planner/Compiler → Entry Plan → Proposal → Risk → Gateway → UTA → Order/Fill/Position → verified close → Outcome |
| Residual branch | Code reachable only for an instrument or route the registry never admits |
| Decision consumer | Code that puts a value into the Entry Planner input, a Compiler/sizing computation, a Risk check or a position-management rule |
| Observation-only | Persisted for research or diagnostics with no decision consumer, declared as such in data_utilization.md |

## Requirements

### FC-R01 No spot default

`The configuration shall define enabled_markets with usdt_futures only, and when a persisted config enables spot, the loader shall drop that key, write one audit event and expose a doctor info item.`

#### Scenarios

- WHEN a fresh config is created THEN `enabled_markets == {"usdt_futures": True}`.
- WHEN a persisted config contains `spot: true` THEN the loaded config has no `spot` key, one `config_scope_normalized` audit event is written, and the runtime starts.

### FC-R02 Residual spot branches removed

`If a Proposal, Entry Plan or sizing request carries a market_type other than usdt_futures, then Risk shall reject it with market_type_out_of_scope and no spot-specific check, token or sizing branch shall remain.`

#### Scenarios

- WHEN Risk evaluates a Proposal with `market_type="spot"` THEN the only violation is `market_type_out_of_scope`.
- WHEN the code is searched THEN `spot_market_disabled`, `invalid_spot_leverage`, `not_applicable_spot` and `spot_short_not_allowed` have no definition outside migrations and history readers.

### FC-R03 Perpetual-only market data path

`The market data provider for the trading venue shall not install a spot-only fallback chain, and market_data.ccxt_fallback shall default to empty.`

#### Scenarios

- WHEN `get_market_data_provider` is built for `bitget` perpetuals THEN no CoinGecko/Binance spot provider is in its chain.
- WHEN a fresh config is created THEN `market_data.ccxt_fallback == ""`.

### FC-R04 Out-of-scope producers

`The scheduler shall not register equity_observer or prediction_observer, and the server shall not expose prediction-market routes or pages.`

[NEEDS CLARIFICATION: delete the equity/prediction observers, their routes and the Prediction page outright, or keep the code frozen behind an off-by-default flag? Also: reject the CN Equity proposal?]

#### Scenarios

- WHEN the scheduler starts with default config THEN neither job id is registered.
- WHEN `GET /api/prediction/...` is called THEN the route does not exist.

### FC-R05 Every collector has a consumer

`When a data collector job is registered, data_utilization.md shall name its decision consumer or its accepted observation-only reason, and LLM renderers without a production caller shall be deleted.`

[NEEDS CLARIFICATION: for macro, fear/greed, top movers, on-chain (DefiLlama), Hyperliquid and yfinance macro — stop collecting, keep as observation-only research data, or feed one of them into the Entry Planner input under a new spec?]

#### Scenarios

- WHEN data_utilization.md is checked THEN it contains no row marked ❌ orphan.
- WHEN the code is searched THEN `render_enriched_context_md` and `trading/market_context.py::build_market_context` have no definition, or each has a production caller.

### FC-R06 News analysis follows its consumer

`Where Serenity is disabled, the scheduler shall not register news_analyze, so no LLM call competes with the Entry Planner for the agents worker.`

#### Scenarios

- WHEN `serenity.enabled` is false THEN `news_analyze` is not registered and `news_fetch` is not registered.
- WHEN `serenity.enabled` is true THEN both are registered as today.

### FC-R07 One sizing implementation

`The system shall size entries only through trading/entry_sizing.py, and the persona sizing state machine and its persona_rollout job shall be removed.`

[NEEDS CLARIFICATION: confirm removal of persona sizing (trading/persona.py, persona_rollout.py, sizing_policy.py and the Settings persona controls).]

#### Scenarios

- WHEN the scheduler starts THEN `persona_rollout` is not registered.
- WHEN Settings is loaded THEN no persona rollout control is returned.

### FC-R08 No uncalled trading-path functions

`The trading path shall contain no function without a production caller; reduce_position_routed, assess_trading_mandate_fit and the Windows gateway service modules shall be deleted.`

[NEEDS CLARIFICATION: delete the trading mandate capability (contract trading_mandate_contract.md, ~1444 lines) or keep it frozen? Should K1/K2 kill functions gain a runtime consumer (pause new entries) or stay manual?]

#### Scenarios

- WHEN the code is searched THEN `reduce_position_routed` and `windows_machine_service` have no definition.
- WHEN `gateway service status` runs on Linux THEN it reports the systemd user service only.

## Contract Delta

### ADDED

- `risk_engine_contract.md` §2: check #2 becomes "market_type must be `usdt_futures`" with token `market_type_out_of_scope` (FC-R02).
- `configuration_contract.md`: spot-key normalization and its audit event (FC-R01).

### MODIFIED

- `entry_sizing_and_financing_contract.md`: sizing has no spot branch (FC-R02); persona sizing removed from Non-goals because it no longer exists (FC-R07).
- `execution_contract.md` §3.1: residual spot branch sentence removed (FC-R02).
- `scheduler_contract.md` and `SYSTEM_MAP.md`: job table loses `equity_observer`, `prediction_observer`, `persona_rollout`; `news_fetch`/`news_analyze` become Serenity-conditional (FC-R04, R06, R07).
- `data_utilization.md` §3: every row has a consumer or accepted observation-only reason (FC-R05).

### REMOVED

- `trading_mandate_contract.md` if FC-R08 removes the capability.
- `kill_conditions_contract.md` K1/K2 if they stay manual (moved to EXPERIMENT_PROTOCOL as owner judgment).
- Spot sections of `ohlcv_provider_result_contract.md` and `data_layer.md` (FC-R03).

## Clarify

| Question | Answer |
|---|---|
| Error path | A spot `market_type` reaching Risk is a typed rejection, never a crash; a persisted `spot: true` config is normalized, never a startup failure (FC-R01, R02) |
| Empty state | With all optional collectors removed, the Entry Planner input is unchanged (it never read them) |
| Boundaries | Historical spot/equity/prediction rows stay readable; no migration rewrites or drops their tables in this spec |
| Concurrency | Removing `news_analyze` frees the shared `agents` worker; no new worker is added |
| Idempotency | Config normalization runs on every load and writes the audit event once per persisted change |
| Authority | No Risk limit, Live, credential or Gateway authority changes; deletions only shrink surfaces |
| Observability | doctor reports the normalized config item; the scheduler job list and data_utilization registry show what remains |

## Open questions

- [NEEDS CLARIFICATION: FC-R04 delete vs freeze the equity/prediction observers; reject CN Equity.]
- [NEEDS CLARIFICATION: FC-R05 fate of each orphan data domain.]
- [NEEDS CLARIFICATION: FC-R07 confirm persona sizing removal.]
- [NEEDS CLARIFICATION: FC-R08 trading mandate delete vs freeze; K1/K2 runtime consumer or manual.]

## Decision log

- 2026-10-09 — Owner: spot is out of scope; USDT perpetuals are the only product ([DECISIONS](../../../governance/decisions/DECISIONS.md)).
- 2026-10-10 — Owner: one capability keeps one implementation; framework changes must remove coupling, not swap one hard-coded value for another.
