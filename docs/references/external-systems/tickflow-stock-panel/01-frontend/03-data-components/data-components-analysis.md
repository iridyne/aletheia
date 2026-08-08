# TickFlow High-Density Data-Component Analysis

## Finding

TickFlow's strongest data-interface practice is coordinated cost control across
four layers: configurable visible columns, conditional batch requests, explicit
provider/capability limits, and DOM virtualization. It does not merely render a
dense table; it tries to avoid fetching and mounting work that the user cannot
currently see.

Its chart behavior is also deliberate, especially zoom preservation and
cleanup, but the implementation is concentrated in very large components and
contains accessibility and comparator defects that should not be copied.

## 1. One Column Model Drives Several Behaviors

The `ColumnConfig` model carries more than a label. It identifies the data
source, visibility, alignment, width and formatting-related options, and
special chart configuration. The same model participates in:

- header and cell rendering;
- sortable-value selection;
- table minimum-width calculation;
- watchlist filtering;
- saved user configuration;
- deciding whether daily or intraday mini-chart data should be loaded.

This is valuable because customization is not implemented as CSS-only hiding.
A hidden expensive column can stop its supporting request.

### Maintenance risk

The model is still interpreted in several large page components. Rendering,
filterability, sortability, fetch requirements, and persistence are related by
convention rather than a single typed capability contract. A new column can be
visually correct while missing one of those behaviors.

## 2. Virtualization Adapts to the Existing Page Scroller

StockDataTable keeps a semantic table and switches to TanStack Virtual only
when the result exceeds 100 rows. It:

- discovers the closest actual vertical scroll parent;
- calculates the table's offset inside that parent;
- observes both the table container and scroll element for geometry changes;
- estimates row height, then measures rendered rows;
- renders overscan rows plus top and bottom spacer rows;
- uses the caller's stable row key rather than an array index.

The same scroll-parent mechanism supports watchlist card grids, where an item
is a responsive row of cards rather than a table record. This is a reusable
solution to a common dashboard problem: the data component is not itself the
page's scroll container.

### Why the threshold matters

Virtualization has its own geometry, focus, browser, and testing costs. Keeping
ordinary lists as normal DOM preserves native table behavior and avoids paying
that complexity for small results.

### Boundaries

- The fixed 900-pixel minimum still makes the table desktop-first and relies on
  horizontal scrolling in narrow containers.
- DOM virtualization does not reduce the result array, sort cost, filter cost,
  or supporting batch payload. Those remain whole-list operations.
- Focus behavior when a virtualized interactive row scrolls out of the mounted
  range is not defined in the component.
- No frontend test or runtime browser profile was found to prove row
  measurement, scroll restoration, or browser accessibility behavior.

## 3. Expensive Cell Data Is Demand-Driven

The screener derives daily and intraday data requirements from visible column
configuration and separate eye/toggle state. When a mini-chart is not active,
the corresponding batch query is disabled and its data map is empty.

For intraday data, the page also combines:

- a server-advertised capability check;
- a capability-provided batch limit;
- stable, deduplicated, sorted request symbols;
- truncation when the visible result exceeds the limit;
- optional polling only when the preference is enabled and realtime quotes are
  actually running;
- short stale time and previous-data retention.

This is the most transferable mechanism in this domain. Visual customization,
entitlement, provider cost, trading-hours state, request cardinality, and cache
behavior are treated as one resource decision.

### Important limitation

The request set is based on the displayed result, not only the virtual rows in
the viewport. Virtualization therefore controls browser rendering but does not
automatically make network I/O viewport-driven. The explicit batch cap prevents
unbounded minute requests but also means some displayed rows can lack a mini
chart. That partial state must remain visible to the user.

## 4. Pending Data and Filtered Data Are Not Conflated

The watchlist computes two separate counts:

- rows whose fundamental enriched fields are not ready;
- rows removed by active user filters.

The title area displays these as different badges. This prevents “the stock
disappeared because data has not arrived” from being misread as “the stock does
not satisfy my filter.” It is a small but important semantic-honesty practice.

The screener also distinguishes currently matching rows from entries that
matched earlier in the day but have expired, appending the latter in a muted
state. This preserves analytical history instead of silently erasing a changed
result.

## 5. Sorting Is Reusable but Has Edge Defects

`useTableSort` provides a simple three-state interaction:

```text
none -> ascending -> descending -> none
```

It creates a new array, tries numeric comparison first, and falls back to
locale-aware string comparison. Callers can provide a column-specific value
extractor.

However:

- when both compared values are null, the comparator returns `1` rather than
  `0`, violating comparator symmetry;
- empty numeric strings can be interpreted as zero by `Number(...)`;
- sorting happens entirely in memory over the complete result;
- sortable headers are mouse-click targets without keyboard activation or
  `aria-sort` state;
- the unsortable-key convention is separate from the column configuration
  itself.

The interaction idea is useful; this exact comparator and header behavior are
not a suitable shared foundation.

## 6. Filtering Is Expressive but Page-Owned

Watchlist filtering combines board membership with per-column numeric ranges or
substring matching. It persists board selection and exposes active-filter,
hidden-row, and reset affordances. Screener composition separately applies
strategy filters, default scoring, result limits, and expired-row behavior.

The product behavior is rich, but the main pages own the orchestration. At
1,519 lines for Watchlist and 1,025 for Screener, filter state, request state,
view configuration, mutations, result semantics, and rendering are difficult
to review independently.

The reusable asset is the explicit pipeline, not the page size:

```text
source rows
  -> readiness classification
  -> domain filters
  -> user filters
  -> user sort or documented default sort
  -> capability/result limit
  -> table or card presentation
```

## 7. Candlestick Charts Preserve User Context

EChartsCandlestick separates instance creation from full option updates. It
maintains the current zoom window in a ref, reads zoom events without forcing a
React render, and restores the prior window after a full `setOption`. It also:

- memoizes dates and the date-index map;
- uses a ResizeObserver rather than only the window resize event;
- registers hover, click, and zoom events once;
- removes handlers, disconnects observers, and disposes the chart;
- updates some series independently instead of always rebuilding everything;
- distinguishes loading, request failure, malformed non-empty data, and a
  valid rendered result in the StockDailyKChart adapter.

These are good chart-lifecycle practices because a data refresh should not
unexpectedly reset the operator's analytical window.

## 8. Chart Complexity Has Escaped the Shared Hook

The generic backtest hook is only about 50 lines and correctly initializes,
resizes, updates, and disposes an ECharts instance. The production candlestick
and intraday components are 1,174 and 586 lines respectively. They independently
own chart configuration, calculation, graphics, interaction, observers, and
cleanup.

EChartsIntraday also uses a MutationObserver to repeatedly force canvas cursor
behavior after ECharts changes internal DOM attributes. This may solve a real
interaction problem, but it couples the component to library-generated DOM and
raises maintenance cost across upgrades.

The lesson is not to force every chart through one tiny generic hook. It is to
extract stable lifecycle, resize, event-subscription, zoom-state, and data-state
contracts while allowing domain-specific option builders to remain separate.

## 9. Desktop Density Is Prioritized Over Narrow-Screen Semantics

Wide minimum table widths, compact typography, mini charts inside cells, and
icon-heavy controls are effective on a desktop analytical terminal. Horizontal
overflow prevents destructive wrapping, and watchlist card mode gives an
alternative representation.

Static source does not establish a complete mobile workflow or accessible
equivalent for every chart interaction. Several icon buttons rely primarily on
`title`, and sortable table headers do not expose keyboard or screen-reader
state.

## 10. There Is No Direct Frontend Regression Evidence

No correctly named frontend test/spec files were found in the reference tree.
CodeGraph likewise reported no covering tests for the table, screener,
watchlist, or chart symbols. The static mechanisms are instructive, but current
scroll performance, retained zoom, batch truncation messaging, and accessibility
remain unproved runtime behavior.
