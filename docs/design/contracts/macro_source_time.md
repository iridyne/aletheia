# Macro Source-Time Contract

Owner-approved #1465 supersedes acquisition-only TradFi freshness. This is a
source-truth correction within #3065; it grants no new trading authority.

- `MacroSnapshot.observed_at` is mixed-row acquisition/assembly time.
- Each TradFi field preserves its provider/series, original source observation
  date or timestamp, precision and acquisition time in `source_times_json`.
- Source date and acquisition are distinct. Daily FRED/yfinance dates do not
  prove minute-level freshness; they remain stored but unavailable to the current
  30-minute context gate. Missing historical proof is not reconstructed.
- Macro consumers share `fresh_macro_row`: apply the existing assembly-age limit,
  then source-time admission per TradFi field. Healthy independent fields retain
  their existing admission policy. Recompute the existing risk-regime classifier
  from admitted inputs; do not change thresholds or substitute neutral for no inputs.
- Additive schema v83 preserves historical rows. Raw storage remains readable;
  current repository/API, Market Context/Observer and Regime use the projection.

Clock limits and field behavior are detailed in
[Decision Context Freshness](../market-systems/decision_context_freshness.md).
Executable claim MS-C09 exercises collector, persistence and consumers together.
