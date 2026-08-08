# Domain 3 Aletheia Comparison

## Summary

TickFlow is stronger at very-large-result rendering, user-configurable analytical
columns, and coupling expensive column visibility to bounded data acquisition.
Aletheia is stronger at typed generic table composition, row keyboard
activation, explicit PnL evidence classes, and incremental financial-chart
history management.

Neither application currently provides a complete accessible table/chart
interaction contract or direct regression tests for these shared components.

## Comparison Matrix

| Boundary | TickFlow | Aletheia | Assessment |
|---|---|---|---|
| reusable table engine | custom semantic table over TanStack Virtual | TanStack Table | different strengths: TickFlow controls render volume; Aletheia standardizes table state and typed columns |
| large result behavior | virtual rows above 100; virtual watchlist card rows | client pagination, default 25 rows; no virtualizer | pagination is sufficient for bounded lists; TickFlow is stronger for continuous large scans |
| column configuration | rich source/format/chart model, persisted by page/backend-local merge | column visibility with optional per-table local persistence | TickFlow is materially richer, but also more coupled |
| filtering | board plus per-column numeric/text filters and hidden counts | generic global client filter; richer page-specific facets exist outside DataTable | reference provides the stronger shared analytical filtering experience |
| sorting | reusable three-state hook, numeric/string comparison | TanStack sorted-row model | Aletheia should retain library semantics and add explicit header accessibility rather than copy the comparator |
| row accessibility | no shared row-keyboard behavior; sortable headers are mouse-only | clickable rows support Enter/Space and tab focus; headers still mouse-only | Aletheia is partially stronger; both need `aria-sort` and keyboard headers |
| expensive cell data | visible chart columns gate batch requests; capability and batch caps applied | no comparable general column-to-fetch-requirement contract | directly useful design gap for future analytical surfaces |
| partial-result honesty | pending data, filtered-hidden, expired matches, and batch truncation are distinct | strong empty/error/stale vocabulary globally, but generic DataTable only receives one empty message | both have useful semantics; Aletheia should preserve its trust vocabulary when adopting richer tables |
| main OHLC chart | ECharts full option updates with zoom restoration | lightweight-charts incremental paging and tail updates with viewport preservation | Aletheia's data-update algorithm is stronger and should remain |
| resize | ResizeObserver on major reference charts | PriceChart/PnLChart use window resize; some other charts use ResizeObserver | TickFlow's container resize boundary is more robust for sidebar/layout changes |
| chart lifecycle | explicit event off, observer disconnect, dispose | clear interval, unsubscribe range, window listener removal, chart removal | both demonstrate deliberate cleanup |
| chart decomposition | candlestick component 1,174 lines; intraday 586 | PriceChart 232 lines; PnLChart 172; separate Recharts analytics | Aletheia is narrower, though lifecycle helpers are still inconsistent |
| regression evidence | no frontend test/spec files found | 29 frontend test/spec files, but none named for DataTable or chart components | Aletheia has the stronger test culture but the studied boundary remains uncovered |

## What Aletheia Already Does Better

### Incremental price-history management

PriceChart maintains an ascending, deduplicated candle store and a set of known
timestamps. When the user approaches the oldest visible bars it fetches one
older page, prepends only new records, resets series data, and shifts the logical
range by the number of inserted bars. The visible window therefore stays on the
same candles.

At the live edge it requests only recent bars and uses `series.update` to
replace the forming candle or append a new one. This is more efficient and
explicit than treating every update as a full ECharts option rebuild.

### Honest financial curves

PnLChart visually separates:

- raw recorded history;
- verified/genuine outcomes;
- the bounded current unrealized mark.

It also distinguishes loading, failed request with retry, genuinely empty data,
and rendered data. This evidence model is more important to Aletheia than
copying the reference's denser chart chrome.

### Typed table composition

TanStack Table supplies a reviewed state model for sorting, filtering,
pagination, and column visibility. Page columns are generally created through
typed helpers. Aletheia should extend this foundation rather than replace it
with TickFlow's custom comparator.

### Keyboard-activatable rows

When a DataTable row is clickable, it receives tab focus and supports Enter or
Space. The reference shared table has no equivalent general row contract.

### Deliberate chart-library division

Aletheia reserves lightweight-charts for financial time series and uses
Recharts for general analytics. This is a reasonable domain boundary. Adding
ECharts solely because the reference uses it would add bundle and maintenance
cost without establishing a product need.

## Where Aletheia Can Learn

### Separate render cardinality from result cardinality

DataTable's pagination limits mounted rows, but every consumer still supplies
the full client array for filter/sort/pagination. That is appropriate for
bounded datasets. For an unbounded universe, event ledger, or continuous scan,
Aletheia should explicitly choose among:

- server pagination;
- cursor/infinite loading;
- viewport virtualization;
- bounded snapshots.

TickFlow's threshold-based virtualization is a good option only when the UX
requires continuous scrolling through a large already-loaded set.

### Make expensive columns declare requirements

A future analytical column model can declare that a visible cell needs a
particular capability and batch resource. The request planner can then derive a
stable, deduplicated request set and surface truncation or unavailable data.

This should remain separate from trading authority. Hiding a presentation
column may suppress optional analytical I/O; it must never suppress required
risk or execution checks.

### Use container resize observation

PriceChart and PnLChart listen to window resize. A sidebar collapse, panel split,
font load, or parent layout change can change container width without a window
resize. The reference's ResizeObserver boundary is more precise.

### Preserve distinct row-state counts

If a table includes pending enrichment, filtered-out records, server truncation,
stale records, or unavailable fields, show those as different facts. A single
`data.length === 0` empty branch cannot explain partial results.

### Persist reviewed view preferences

Aletheia already persists column visibility when `tableId` is provided. The
reference shows a broader but coherent preference surface: table/card view,
board filter, visible columns, chart columns, and chart toggles. Any expansion
should use versioned schemas and stable column IDs so removed or renamed fields
do not corrupt the view.

## Aletheia Risks Found During Comparison

### PriceChart lifecycle dependencies are incomplete

The main chart effect uses `marketType` and `onMarkerClick`, but its dependency
list contains only `symbol` and `timeframe` and disables the exhaustive-deps
rule. A same-symbol market-type change can therefore retain the old data source,
and a changed marker-click callback can remain captured by the old chart
subscription.

This is static risk evidence, not a runtime reproduction, and no code change is
authorized by this study.

### Container-only resize is not observed

PriceChart and PnLChart update width from the global window resize event. They
may not react immediately to layout-only width changes. Other Aletheia charts
already use ResizeObserver, so the project currently has inconsistent lifecycle
patterns.

### PnLChart rebuilds on each query data object

PnLChart's creation effect depends on `data`. Its ten-second query refresh can
remove and recreate the chart, fitting content again even when the user had
changed the viewport. TickFlow's retained zoom and Aletheia PriceChart's
long-lived instance both suggest a better lifecycle boundary.

### Table headers are not keyboard-sortable

DataTable makes sortable `th` elements clickable, but it does not expose
`aria-sort`, tab focus, or Enter/Space activation. The filter input also relies
on placeholder text without an explicit accessible label. The column menu has
an expanded state, but no explicit focus entry/return or Escape behavior in the
component.

### Some dense tables bypass the shared component

Charts and other pages still contain manual tables. Static inspection found
clickable signal rows without the shared row keyboard behavior. Improving
DataTable alone will not automatically normalize every dense surface.

## Invariants That Must Remain

- Raw, verified, current-mark, stale, unavailable, and empty data must not be
  visually collapsed into one generic state.
- Market type is part of a price-series identity, not a decorative label.
- A hidden optional visualization may avoid optional data I/O; a hidden field
  must never bypass risk, audit, or execution requirements.
- Any viewport or incremental update optimization must preserve ascending,
  deduplicated time-series input expected by the chart library.
- Provider batch limits must be visible product facts, not silently dropped
  rows presented as a complete scan.
