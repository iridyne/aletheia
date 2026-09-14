# Retired duplicate observed-stocks control

Signals previously exposed both an observed_stocks text field in Market Data
Sources and the canonical observed_equities editor. Runtime prioritizes the
canonical list and otherwise falls back to the legacy list, so saving one control
could appear ineffective and clearing canonical tickers could reactivate old ones.

The duplicate writer in Market Data Sources is removed. The equities editor
shows the effective current list, including legacy-only configuration, until the
owner explicitly saves it. Its existing save endpoint writes canonical tickers
and clears observed_stocks in that same config save, auditing both prior values.
An empty explicit save therefore disables equity observation.

The generic market-data update API also rejects observed_stocks as an unknown
field before config access; its request schema and frontend request types no
longer advertise the retired writer. Stored config remains readable.

The generic endpoint also rejects observed_equities. That duplicate canonical
field bypassed the dedicated equities endpoint's normalization and legacy-list
clearing. Current UI callers use the dedicated endpoint; its request and the
canonical config read field remain supported.

The dedicated equities save now uses the existing locked `mutate_config`
read-modify-write owner. Audit before-values come from that latest revision,
so an observation change cannot overwrite unrelated settings from a stale
loaded snapshot.

The remaining runtime alias is now retired. Historical configuration decoding
maps observed_stocks to observed_equities only when the canonical list is absent
or empty, preserving the previous effective workset. Both historical lists are
validated even when the canonical list wins. The decoder copies its input and
does not write a file; no startup migration flag is added. Normal explicit
config saves serialize only the canonical field.

Scheduler, Observation, settings audit and UI now consume only observed_equities.
The earlier dual-field save/audit behavior above describes the intermediate
retirement step, not a surviving runtime writer. The separate observed_xstocks
token configuration retirement remains open.
