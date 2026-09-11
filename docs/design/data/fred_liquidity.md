# FRED rates and dollar-liquidity research facts

Owner: #3101. Implementation: `aletheia/data/macro_liquidity.py` and the existing
`MacroCollector` FRED adapter. DU-S33 registers the read-only CLI consumer.
Authority is observation/research-only; no Candidate, Entry, prompt, Risk,
sizing, execution or Live consumer is added. Existing MacroSnapshot fields and
risk-regime calculation retain their semantics. No runtime deployment is implied.

## Source contract and bounded acquisition

Official FRED pages inspected 2026-09-11; units below are native levels, all
not seasonally adjusted. Metadata is an explicit pinned series contract, not
metadata returned by the observations endpoint. The adapter requires the API's
`units=lin` response (native levels, not percent-change transformation).

| Series | Meaning | Native unit | Frequency / source age limit |
| --- | --- | --- | --- |
| [SOFR](https://fred.stlouisfed.org/series/SOFR) | Secured overnight funding rate, not an FOMC target | Percent | Daily / 5 calendar days |
| [DGS2](https://fred.stlouisfed.org/series/DGS2) | 2-year constant-maturity Treasury yield | Percent | Daily / 5 days |
| [DGS10](https://fred.stlouisfed.org/series/DGS10) | 10-year constant-maturity Treasury yield | Percent | Daily / 5 days |
| [WALCL](https://fred.stlouisfed.org/series/WALCL) | Fed total assets | Millions of U.S. Dollars | Wednesday level / 10 days |
| [RRPONTSYD](https://fred.stlouisfed.org/series/RRPONTSYD) | Overnight reverse-repo transaction amount | Billions of US Dollars | Daily / 5 days |
| [WDTGAL](https://fred.stlouisfed.org/series/WDTGAL/) | Treasury General Account | Millions of U.S. Dollars | Wednesday level / 10 days |

TGA has one identity: **WDTGAL**, not the weekly-average WTREGEN. Wednesday
levels align with WALCL. No separate provider or metadata-polling service.
`tests/fixtures/fred_series_contract.json` contains attributed, manually
transcribed public-page excerpts and the observed `.` missing-value example.
Test API payloads use the [official JSON response shape](https://fred.stlouisfed.org/docs/api/fred/series_observations.html);
they are explicitly not authenticated live API captures. No key was inspected
or credentialed smoke request made for this implementation.

The existing context scheduler invokes MacroCollector. Each daily series gets
one attempt per UTC date; WALCL/WDTGAL one per Friday-UTC week bucket (after
normal Thursday H.4.1 publication), with immediate first acquisition on startup.
Late publication, outages and newly configured keys wait for the next bucket;
there is no intraday retry of a weekly series. Persisted unavailable attempts
also consume the bucket across restarts. Absent storage fails closed before
requests. A six-entry in-process attempt map (updated before awaiting HTTP)
prevents repeated requests after persistence failure/cancellation and concurrent
calls. A process restart after failed persistence can repeat that failed attempt;
there is no claim of durable exactly-once HTTP. Existing macro collector health
reports persistence failure. Budgets below assume working persistence.

Requests share the existing observations helper, with a separate bounded
research gather: max 8 seconds per series, 32 daily or 4 weekly observations,
six extra concurrent requests at the initial/weekly tick, four on other days,
zero elsewhere. Existing four macro requests and fallback behavior are unchanged.
No key means explicit `not_configured` without HTTP. Exceptions are reduced to
reason codes so URLs containing the key are never emitted by the new path.

Each immutable acquisition is one row, including failed attempts. The existing
retention job prunes after 90 days. Healthy steady-state budget is 30 extra
requests/rows per 7 days, at most 390 retained rows (calendar-boundary allowance),
16 KiB hard payload limit each: under 6.1 MiB JSON plus SQLite/index overhead.
The offline seven-day full-response test measures requests, rows and actual
serialized bytes; raw response bodies and an unbounded macro history are not stored.
The seven-day offline fixture measured 30 requests, 30 rows, 150,892 JSON bytes,
and a 5,328-byte maximum row (synthetic response values, not live storage).

## Cutoff, derivation and limitations

Every source has separate observation **date** and timezone-aware acquisition
time. Date precision is never promoted to a release timestamp. Export selects
the latest batch per series with `acquired_at <= cutoff`; every chosen date must
also be no later than the acquisition date and requested as-of date. Refetching
an old date does not change its source age. Invalid dates, duplicate dates,
non-Wednesday weekly points, NaN/Inf and unproved transformations fail closed.
Holidays retain holes; backward selection is limited by the documented age.

Revision policy is `current_known_revised_not_historical_vintage`. FRED realtime
fields are retained as metadata, not treated as first-publication proof. Today's
returned historical points may be used descriptively at today's cutoff, never
as information known at their original observation dates. Existing historical
batches are not overwritten when FRED revises a value. Retention limits local
replay to stored batches or independently saved exports.

- RatesRegime: latest SOFR level and change from seven calendar days earlier,
  choosing the preceding available baseline at most 3 days back. Values above,
  below or exactly at zero change label tightening/easing/neutral respectively;
  this describes SOFR movement, **not** a calibrated monetary-policy stance.
- CurveRegime: `(DGS10 - DGS2) * 100` basis points; both yields must have exactly
  the same source date within 5 days. Positive/zero/negative means normal/flat/
  inverted. Its seven-day change applies the same date-match rule to the
  baseline, with at most 3 days of backward tolerance. Missing history produces
  unavailable, not neutral. No interpolation or fuzzy near-zero band.
- DollarLiquidityImpulse: current WALCL Wednesday is the anchor, at most 10 days
  old. Compare exactly the preceding Wednesday for WALCL and WDTGAL. Select RRP
  at or before each anchor separately, at most 3 days back. Preserve each
  actual source date. In billions USD the formula is
  `delta(WALCL)/1000 - delta(RRPONTSYD) - delta(WDTGAL)/1000`.
  Each signed component and its inputs remain visible. Missing any component
  means an unavailable total and partial coverage, never an implicit zero.
  This is a balance-sheet proxy, not measured crypto inflow or a BTC price claim.

`aletheia data macro-liquidity --cutoff <timezone-aware ISO>` does no network,
DB creation/migration or writes. It emits units, source coverage, selected input
IDs, storage row IDs, actual dates/acquisitions, version, input fingerprint and
frozen batches. Replaying those batches reproduces the projection. Outcome-join
metadata declares the cutoff, global scope and 15m/1h/4h horizons for **all
observations**, not only trades. It does not compute outcomes or prove predictive
value. Promotion requires independent point-in-time, cost-complete evidence.
