# TickFlow First-Batch Selection — 2026-08-09

## Document Role

This is dated research evidence recording the first implementation batch
selected from the TickFlow Stock Panel comparison. It is a durable reference
for scope and acceptance, not a task tracker and not a statement that the work
is complete. Git commits and pull requests carry execution status.

The selection was rechecked against Aletheia `master` at
`a6944936533ce3029bf0e380788b1ef4d5f06805` before implementation began.

## Preserved Boundaries

The batch may improve market-data provenance, frontend correctness, transport
diagnostics, routing, presentation, and accessibility. It does not change:

- SQLite as the authority for transactional trading and business state;
- Risk approval before the Execution Gateway;
- the Execution Gateway as the only route to an exchange;
- live trading being disabled by default;
- backend authority over permissions, confirmations, and safety state;
- exact exchange, canonical symbol, market type, and timeframe identity;
- write-only credential semantics.

## Selected Work, Highest Priority First

| Order | Module | Change class | Expected value | Estimated cost | Selection |
|---:|---|---|---|---|---|
| 1 | OHLCV fallback cache identity | correctness repair | very high | small to medium | implement now |
| 2 | PriceChart lifecycle and data identity | correctness repair | high | small | implement now |
| 3 | structured API errors | contract refactor | high | medium | implement compatibly |
| 4 | realtime degraded state and diagnostics | semantic honesty | high | small to medium | implement now |
| 5 | route recovery, 404, and canonical redirects | frontend hardening | medium-high | small | implement now |
| 6 | visible PageHeader role and state expression | UI improvement | medium | small | implement now |
| 7 | DataTable accessibility completion | UI correctness | medium | small | implement now |

## 1. OHLCV Fallback Cache Identity

### Problem

The CCXT market-data provider persists fallback candles under the actual venue,
but the in-memory cache can store them under the requested primary venue.
Additional route and market-context cache writes can repeat the same false
identity. A later primary-scoped read may therefore receive fallback-venue data
without knowing that substitution occurred.

### Intended change

- attach actual provider/venue provenance to fetched candles;
- cache CCXT results under the actual successful venue;
- prefer primary cached/fetched data before reusing fallback cache;
- remove outer cache writes that relabel provider results as the requested
  venue;
- retain exact SQLite persistence identity;
- expose actual venue in the existing data-instrument response header where it
  is known.

### Acceptance evidence

- a primary failure followed by fallback success never populates the primary
  cache key;
- fallback data is reusable from its actual-venue cache after primary fails
  again;
- persisted exchange remains the actual venue;
- no execution or Risk path changes.

## 2. PriceChart Lifecycle and Data Identity

### Problem

PriceChart's long-lived effect is keyed by symbol and timeframe while its data
requests also depend on market type. Callback closures can become stale, and
PriceChart/PnLChart observe the window rather than their actual containers.

### Intended change

- make market type part of chart data identity;
- keep marker callbacks current without unnecessary chart reconstruction;
- use the existing ResizeObserver pattern already present in ProbabilityChart;
- retain logical range, incremental tail updates, and complete cleanup.

### Acceptance evidence

- changing spot/perpetual identity cannot retain the old series;
- the latest marker callback is used;
- container-only resize updates chart width;
- observer, timers, subscriptions, and chart instances are cleaned up.

## 3. Structured API Errors

### Problem

The shared frontend transport reduces structured HTTP failures to a message and
throws an ordinary Error. Status, code, details, retry guidance, field errors,
and correlation identity are then unavailable to callers.

### Intended change

- introduce an `ApiError` that remains compatible with `Error` and preserves the
  existing human-readable message;
- retain safe structured fields when the backend supplies them;
- keep timeout and caller-abort behavior unchanged;
- do not introduce a new schema dependency or migrate every endpoint at once.

### Acceptance evidence

- existing message-only callers still behave correctly;
- a structured FastAPI detail object retains status/code/details;
- non-JSON and long error bodies remain safely bounded;
- request cancellation behavior is unchanged.

## 4. Realtime Degraded State and Diagnostics

### Problem

The application owns one reconnecting WebSocket and performs initial catch-up,
but the hook returns no status. Malformed frames and unknown change categories
are silently ignored, so the operator cannot distinguish healthy push from
polling-only degradation.

### Intended change

- expose `connecting`, `connected`, `reconnecting`, `polling_only`, and
  `disconnected` transport state;
- expose last-frame time plus bounded malformed/unknown counters;
- render a compact status in the existing application status surface;
- preserve REST/query reads as authority and existing polling as fallback.

### Acceptance evidence

- transport state is visible without claiming data freshness or backend health;
- malformed and unknown frames increment diagnostics without crashing;
- hidden-tab close and visible-tab catch-up still work;
- no second WebSocket owner or new state library is introduced.

## 5. Route Recovery, 404, and Canonical Redirects

### Problem

Several retired paths render replacement components under obsolete URLs, and
unknown routes have no shell-preserving 404. Major pages are eagerly imported.

### Intended change

- redirect legacy aliases to canonical URLs with replacement semantics;
- add a shell-preserving not-found route and safe return action;
- lazy-load feature pages behind a page-outlet fallback;
- preserve current deployment chunk-recovery behavior.

### Acceptance evidence

- every retired URL resolves to its canonical location;
- unknown routes retain the shell and offer a safe return;
- feature-page loading does not unmount global operator status;
- missing/stale chunks have an explicit recovery path.

## 6. Visible PageHeader Role and State Expression

### Problem

Pages already provide workflow-role text, but PageHeader exposes it only through
the title attribute. Important purpose or limitation text is therefore hidden
from ordinary reading and assistive structure.

### Intended change

- render the existing role as concise visible supporting text;
- preserve breadcrumb, title, and actions;
- use backend-grounded state language and avoid invented readiness.

### Acceptance evidence

- supplied role text is visible and semantically associated with the heading;
- layout remains usable with and without actions or role text;
- no page-specific duplicate subtitle mechanism is introduced.

## 7. DataTable Accessibility Completion

### Problem

Clickable rows already support keyboard activation, but sortable headers are
mouse-only, do not expose `aria-sort`, and the optional filter lacks an explicit
accessible name.

### Intended change

- make sortable headers keyboard-operable;
- expose ascending, descending, or unsorted state;
- label the filter independently of placeholder text;
- preserve current TanStack Table sorting and row behavior.

### Acceptance evidence

- Enter/Space can activate sortable headers;
- assistive technology receives current sort state;
- the filter has an accessible name;
- no custom table/sort engine replaces TanStack Table.

## Explicitly Outside This Batch

- Parquet, DuckDB, Polars, or matrix storage;
- a new database, Redis, or a generic cache framework;
- a complete query-key migration;
- a generic background-job platform;
- a global FastAPI dependency-injection rewrite;
- a plugin marketplace or executable strategy sandbox;
- remote-token/session redesign;
- Risk, execution, live, credential, or trading-authority changes.

## Verification Policy

Use the smallest evidence that covers each changed boundary:

- focused Python tests for market-data cache identity;
- focused frontend unit/source contract tests for API errors, chart lifecycle,
  realtime state, routes, PageHeader, and DataTable;
- frontend typecheck/build after the frontend batch;
- documentation governance and whitespace checks;
- broad regression suites remain CI/Windows work, not repeated macOS analysis
  steps.
