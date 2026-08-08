# Domain 3 Lessons and Reflections

## Reuse Directly

### Threshold-based virtualization as an explicit policy

Keep ordinary result sets in normal semantic DOM and switch to virtualization
only above a measured threshold. Use stable record keys, measured variable
heights, overscan, and the actual page scroll container.

This is directly reusable as a technique, but only for an Aletheia surface that
has a demonstrated continuous large-list requirement.

### Visible expensive columns should control optional I/O

If an optional mini chart or analytical field is hidden, do not continue its
supporting batch request. Derive request symbols from the displayed result,
deduplicate and sort them for stable cache identity, apply provider capability
and cardinality limits, and expose any truncation.

### Preserve zoom or logical range across data refresh

A chart refresh should not throw the operator back to a default view. Capture
the current dataZoom/logical range and restore or translate it after data is
replaced.

### Distinguish missing, pending, filtered, expired, and truncated rows

These states answer different operational questions and should remain separate
in counts, labels, and empty/partial-state messaging.

### Observe the chart container, not only the window

ResizeObserver follows the geometry that actually matters and handles shell,
drawer, and grid changes that do not fire a window resize.

## Reuse After Adaptation

### Rich analytical column descriptors

Extend Aletheia's typed TanStack column definitions with reviewed metadata for
optional fetch requirements, capability facts, user visibility, formatting,
and partial-data behavior. Avoid one parallel column registry for every
concern.

Adaptation is required so the model cannot affect safety checks and so backend
field availability remains authoritative.

### Table/card alternate views

A card view can make a watchlist or universe easier to scan on compact screens
and gives mini charts more room. It should share selection, filters, state
labels, capability limits, and canonical record identity with the table rather
than becoming a second business pipeline.

### View preference persistence

Persist stable view preferences with a schema version, durable column IDs,
safe defaults, and migration/cleanup for removed columns. Treat local storage
failure as a loss of convenience, not of authoritative application state.

### Long-lived chart instance with domain-specific option builders

Share instance creation, ResizeObserver, event cleanup, and retained viewport
logic. Keep financial calculations and series construction in typed,
domain-specific functions. Do not force every chart into one universal option
builder.

### Dense table pipeline

Make the sequence from source rows through readiness, domain filter, user
filter, sort, bounded result, and presentation explicit. For large or remote
sets, move the appropriate stages to the backend rather than assuming all
client work is cheap.

## Study Further

- Measure actual row counts and interaction latency on Universe, Blotter,
  Agent Activity, Learning, and event-ledger surfaces before selecting
  pagination, server cursors, or virtualization.
- Determine whether Aletheia needs a general analytical-column requirement
  model or only one focused implementation for a future universe scanner.
- Reproduce same-symbol market-type changes in PriceChart and container-only
  resize behavior with a bounded browser probe before proposing a fix.
- Define accessible sorting semantics, column-menu focus management, chart
  summaries, and keyboard equivalents before expanding dense interaction.
- Decide which chart viewport state should survive data polling, tab changes,
  symbol changes, and route changes.
- Inventory manual tables that bypass DataTable and classify which are static
  detail tables versus interactive data grids.
- Decide whether partial provider batches should show per-row unavailable
  cells, a table-level coverage badge, or both.

## Do Not Reuse

### The custom sort comparator unchanged

Its null/null behavior violates comparator symmetry, numeric coercion is broad,
and the header interaction has no keyboard or `aria-sort` contract. Retain
TanStack Table's state model and implement explicit value/null policies.

### ECharts as a goal in itself

Aletheia already has a deliberate financial/general chart split. A library
change needs a specific product or performance requirement, not visual
similarity to the reference.

### Thousand-line chart or page components

The reference's capabilities are valuable, but concentrating request planning,
filters, calculations, chart options, lifecycle, mutation controls, and view
rendering in one component makes the behavior harder to verify.

### Silent batch truncation

Provider limits are real. Never render the available subset as though it were
the complete analytical universe.

### Viewport-driven safety semantics

Virtualization and hidden columns may control optional rendering or analytical
I/O only. Risk evaluation, audit capture, execution checks, and authoritative
state must not depend on whether a row is mounted or visible.

### Mouse-only dense interaction

Do not copy clickable headers and icon-only chart controls without keyboard,
focus, label, and state equivalents.

## Durable Design Reflections

### Data density is a resource contract

A dense surface consumes DOM nodes, CPU, memory, provider requests, cache
entries, visual attention, and accessibility complexity. The best reference
practice coordinates all of them instead of optimizing only render speed.

### A configurable column is a query plan

Once a column can require extra data, visibility is no longer merely a display
preference. It participates in a bounded query plan. That plan needs stable
identity, capability checks, explicit partial results, and observability.

### Virtualization solves mounting, not completeness

Only rendering visible rows does not make a whole-list filter, sort, or batch
fetch cheap. Keep render cardinality, client-result cardinality, and provider
request cardinality as three separately reviewed numbers.

### The operator's viewport is working state

Zoom, logical range, selected record, sort, filters, and visible columns are
part of the analytical task. Unnecessary chart recreation or refresh resets can
destroy context even when the underlying data is correct.

### Partial results require positive disclosure

Pending enrichment, provider batch caps, unavailable fields, and expired
signals are not implementation trivia. They change what conclusions an
operator may draw from the table.

### Aletheia should borrow mechanisms, not visual density

The reference is optimized for a stock-analysis workstation. Aletheia should
adopt bounded requests, retained context, and semantic state distinctions while
preserving its own workflow taxonomy, safety vocabulary, and evidence model.

## Evidence Boundary

These lessons come from current source, declared dependencies, and static
CodeGraph call paths. No frame-rate measurement, heap profile, network trace,
screen-reader session, narrow-viewport browser run, or chart-event exercise was
performed. Runtime behavior that depends on layout, timing, or third-party
library internals remains unverified.
