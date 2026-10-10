# Macro Source-Time Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `MT` · **Owner code:** `core/aletheia/data/freshness.py::fresh_macro_row`

## Scope

Macro snapshots mix fields acquired at one time from sources observed at different times.
This contract keeps each TradFi field's source observation time separate from row
acquisition, and defines how current consumers admit those fields. Macro is not read by the
Entry Planner; its current consumers are Regime, persona sizing, Gateway attribution and the
macro API. It grants no trading authority. Clock limits are detailed in
[Decision Context Freshness](../market-systems/decision_context_freshness.md).

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `extensions/data/collectors/macro.py::MacroCollector.collect` | scheduler collector | `core/aletheia/agent_runtime/scheduler.py::_async_data_collector_context` |
| `core/aletheia/data/freshness.py::fresh_macro_row` | call (projection) | `core/aletheia/storage/repositories_market_data.py::latest_macro_snapshot`, `core/aletheia/data/freshness.py::fresh_row` (`domain="macro"`) |

## Durable state

| Table / file | Writer | Readers | Mutability |
|---|---|---|---|
| `macro_snapshots` (`observed_at` = row acquisition, `source_times_json` per field) | `MacroCollector.collect` | `latest_macro_snapshot` → `/api/market/macro`, `core/aletheia/data/context_builder.py`, `extensions/regime/collector.py`, `core/aletheia/trading/persona.py`, `core/aletheia/trading/entry_snapshot.py` | append-only; historical rows never rewritten |

## Requirements

### MT-C01 Source time is stored per field

`When the macro collector stores a snapshot, it shall set observed_at to the mixed-row acquisition time and record, for each TradFi field, its provider, series, original source observation date or timestamp, precision and acquisition time in source_times_json.`

- WHEN FRED returns a daily observation THEN `source_observed_at` is that date with `precision="date"`, not the acquisition time.

Evidence: `extensions/data/collectors/macro.py::MacroCollector.collect`.

### MT-C02 Daily dates cannot prove minute freshness

`If a TradFi field's source proof is missing, has date precision, or is older than the 30-minute macro limit, then fresh_macro_row shall project that field as None while leaving the stored value unchanged.`

- WHEN a legacy row has no `source_times_json` THEN its TradFi fields are unavailable; missing proof is not reconstructed.

Evidence: `core/aletheia/data/freshness.py::fresh_macro_row`.

### MT-C03 One shared projection for consumers

`When a current consumer reads macro data, the system shall first apply the row assembly-age limit, then admit each TradFi field by its source time, keep independently healthy fields, and recompute the risk-regime classifier from admitted inputs only, reporting unavailable when no input is admitted.`

- WHEN VIX is unadmitted and total market-cap change is admitted THEN `risk_regime` is classified from market-cap change alone.
- WHEN neither is admitted THEN `risk_regime == "unavailable"`, never `neutral`.

Evidence: `core/aletheia/data/freshness.py::fresh_macro_row`, `_classify_risk_regime`.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Row older than the assembly limit | whole row rejected | `None` | unavailable |
| Field proof missing, date-only or stale | field projected `None` | field `None` | stored value kept |
| No admitted classifier input | regime unavailable | `risk_regime="unavailable"` | never `neutral` |

## Authority

Produces macro context only; grants no trading, Risk, Gateway or Live authority.

## Verification

| Requirement | Executable claim / focused test |
|---|---|
| MT-C01 – MT-C03 | `tests/specs/spec_macro_signals.py::MS-C09` (delegates to `tests/test_macro_source_time.py`) |
| MT-C02 | `tests/test_macro_source_time.py::test_historical_macro_is_not_rewritten_or_given_source_proof` |
| MT-C03 | `tests/test_macro_source_time.py::test_proved_field_and_independent_field_remain_fresh` |

### Known conformance gaps

- Both current TradFi sources write `precision="date"`
  (`extensions/data/collectors/macro.py`, FRED and yfinance paths), so under MT-C02 every SPY/VIX/
  DXY/NASDAQ field is always projected `None` and the risk regime depends only on market-cap change.

## Decision references

- Owner-approved #1465 superseded acquisition-only TradFi freshness, as a source-truth correction
  within #3065.
- Additive schema v83 added `source_times_json`, preserving historical rows.
