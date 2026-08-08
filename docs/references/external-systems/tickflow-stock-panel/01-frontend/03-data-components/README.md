# Domain 3: High-Density Data Components

This domain studies how TickFlow presents and manipulates large analytical
result sets without treating visual density as a substitute for truthful data
semantics.

## Included

- reusable stock tables and card grids;
- client-side sorting, filtering, column configuration, and search-adjacent
  interaction;
- table and card virtualization;
- conditional and batched data loading driven by visible columns;
- candlestick, intraday, and analytical-chart lifecycle management;
- loading, empty, malformed, pending, truncated, and filtered-result states;
- comparison with Aletheia's DataTable and financial-chart components.

## Excluded

- TanStack Query cache ownership and invalidation, except where it directly
  controls a data component; those belong to Domain 4;
- API schema and backend batching implementation; those belong to Domains 5
  and 8;
- live browser performance measurements and visual acceptance testing;
- any change to Aletheia UI or trading behavior.

## Reports

- `source-map.md` records the current implementation map.
- `data-components-analysis.md` explains TickFlow's mechanisms, strengths, and
  failure modes.
- `aletheia-comparison.md` compares the same boundaries in Aletheia.
- `lessons.md` classifies what to reuse, adapt, study, or reject.

## Evidence Boundary

This is a static source and CodeGraph study. No frontend build, pytest suite,
browser profile, screen-reader pass, network trace, or runtime chart exercise
was performed.
