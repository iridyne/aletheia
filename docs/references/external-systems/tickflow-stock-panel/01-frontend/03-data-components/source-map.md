# Domain 3 Source Map

## TickFlow Entry Points and Consumers

| Area | Current source | Role |
|---|---|---|
| shared stock table | `frontend/src/components/stock-table/StockDataTable.tsx` | renders configurable columns and switches to virtual rows above 100 records |
| scroll integration | `frontend/src/components/virtual-list/useParentScroll.ts` | discovers the enclosing vertical scroller and maintains the virtualizer's offset |
| shared sorting | `frontend/src/components/stock-table/useTableSort.ts` | owns three-state client sorting and numeric/string comparison |
| cell primitives | `frontend/src/components/stock-table/primitives.tsx` | renders built-in stock metrics with alignment and formatting |
| column model | `frontend/src/lib/list-columns.ts` | describes built-in and extension columns, visibility, alignment, formatting, and chart configuration |
| screener composition | `frontend/src/pages/Screener.tsx` | combines strategies, filters, sort, result limits, conditional mini-chart batches, and the shared table |
| screener table adapter | `frontend/src/components/screener/ScreenerTable.tsx` | maps screener rows and mini charts into StockDataTable cells |
| watchlist composition | `frontend/src/pages/Watchlist.tsx` | owns persisted view/columns/board filters, table/card modes, filtering, data-readiness counts, and card virtualization |
| daily-chart adapter | `frontend/src/components/StockDailyKChart.tsx` | queries and normalizes OHLC/indicator data and composes chart controls |
| candlestick engine | `frontend/src/components/EChartsCandlestick.tsx` | owns ECharts candlestick, volume, indicators, markers, zoom, hover, resize, and click behavior |
| intraday engine | `frontend/src/components/EChartsIntraday.tsx` | owns full-day coordinate mapping, hover linkage, average price, price limits, and lifecycle |
| simpler chart hook | `frontend/src/pages/backtest/charts/useECharts.ts` | initializes, updates, resizes, and disposes backtest ECharts instances |

## TickFlow Production Paths

```text
Screener
  -> strategy/result composition
  -> visible columns
      -> conditional daily/minute batch queries
  -> filters -> three-state sort -> result limit
  -> ScreenerTable -> StockDataTable
      -> parent-scroll discovery -> TanStack Virtual rows
      -> MiniCandlestick / MiniIntraday cells

Watchlist
  -> saved column/view/filter preferences
  -> client filters -> three-state sort
  -> table mode -> StockDataTable -> virtual rows above 100
  -> card mode -> virtual card rows above 100
  -> pending-data count + filtered-hidden count

StockDailyKChart
  -> query and normalize rows
  -> marker and indicator composition
  -> EChartsCandlestick
      -> long-lived chart instance
      -> ResizeObserver and event subscriptions
      -> full option updates with zoom restoration
      -> event removal and disposal
```

## Important TickFlow Constants and Boundaries

- `VIRTUAL_LIST_THRESHOLD = 100`.
- Stock table row-size estimate is 56 pixels with overscan 10.
- Watchlist card-row estimates are 140 or 180 pixels with overscan 3.
- The stock table minimum width is at least 900 pixels and otherwise grows by
  roughly 110 pixels per visible column.
- The daily chart caps the requested date span at 2,000 days.
- Screener minute batches are clipped to the capability-provided batch limit;
  the source comments identify typical Pro and Expert limits of 100 and 200.
- Visible daily and intraday chart columns determine whether their batch data
  is requested.

## Aletheia Comparison Sources

| Area | Current source | Role |
|---|---|---|
| shared table | `frontend/src/components/DataTable.tsx` | TanStack Table sorting, global filter, pagination, column visibility, and row keyboard activation |
| table consumers | `frontend/src/pages/AgentActivity.tsx`, `Universe.tsx`, `Blotter.tsx`, `Learning.tsx`, `TradesPositions.tsx` | reuse the common table with page-specific typed columns |
| financial price chart | `frontend/src/charts/PriceChart.tsx` | imperative OHLC store, older-history paging, deduplication, polling, markers, and viewport preservation |
| PnL chart | `frontend/src/charts/PnLChart.tsx` | distinguishes raw, verified, and live-mark curves and explicit request states |
| other chart engines | `frontend/src/charts/ProbabilityChart.tsx`, `AccountEquityChart.tsx`, `TradeInsightsPanel.tsx` | lightweight-charts for time-series finance and Recharts for general analytics |
| markets composition | `frontend/src/pages/Charts.tsx` | symbol strip, chart controls, data-source timestamp, signals, news, and prediction panels |
| command search | `frontend/src/components/CommandPalette.tsx` | keyboard-first navigation/action search with async execution state and error feedback |
| table/chart styling | `frontend/src/index.css` | horizontal overflow, no-wrap tables, column menu, pagination, dense layouts, and responsive rules |

## Dependency Snapshot

TickFlow currently declares TanStack Virtual, ECharts, lightweight-charts, and
TanStack Query. Aletheia declares TanStack Table, lightweight-charts, and
Recharts but not TanStack Virtual or ECharts.

This difference does not by itself establish that either stack is superior.
The relevant evidence is how each library is bounded and how user-visible data
states are preserved.
