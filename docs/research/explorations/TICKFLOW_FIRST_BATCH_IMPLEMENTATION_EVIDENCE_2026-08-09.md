# TickFlow First-Batch Implementation Evidence — 2026-08-09

## Document Role

This dated record captures what Aletheia learned and changed while applying the
first seven selected TickFlow-inspired improvements. It is implementation and
validation evidence, not current Runtime truth and not a task tracker. Git and
pull requests remain authoritative for code status.

The work was performed on `codex/tickflow-first-batch` from Aletheia `master`
at `a6944936533ce3029bf0e380788b1ef4d5f06805`.

## Outcome by Boundary

### 1. OHLCV fallback cache identity

- `Candle` now carries optional provider and exchange provenance.
- CCXT candles are cached under the venue that actually returned them.
- A primary lookup/fetch remains preferred; a fallback cache is considered
  only after the primary path cannot supply data.
- Route and market-context layers no longer re-cache provider results under a
  requested venue that may not be the observed venue.
- The existing data-instrument header reports the observed venue when the
  returned candles identify one unambiguously.

The important lesson is that provenance must be attached at the provider
result boundary. An outer caller cannot safely reconstruct it from the request
after a fallback has occurred.

### 2. Chart lifecycle and data identity

- PriceChart now treats symbol, timeframe, and market type as one data
  identity.
- Marker clicks use the latest callback through a ref without reconstructing
  the chart on every parent render.
- PriceChart and PnLChart reuse the existing ProbabilityChart
  `ResizeObserver` pattern and disconnect it during cleanup.
- PriceChart also unregisters click/range subscriptions and clears its polling
  timer before removing the chart.

The useful reusable practice is not a new chart framework. It is a small,
consistent lifecycle contract: complete data identity, current callbacks,
container observation, and symmetric cleanup.

### 3. Structured API errors

- The shared transport throws `ApiError`, which remains an ordinary `Error`
  for existing message-only callers.
- HTTP status plus supplied code, details, field errors, retry guidance, and
  correlation identity are retained.
- Existing message formatting and bounded handling of long non-JSON bodies are
  unchanged.
- Timeout and caller-abort wiring remain unchanged.

This is deliberately not successful-response schema decoding. Runtime decoding
should be added only to economically or operationally critical endpoints when
a concrete contract failure justifies the migration cost.

### 4. Realtime degraded state and diagnostics

- The existing single WebSocket owner now exposes connecting, connected,
  reconnecting, polling-only, and disconnected states.
- Valid frames record a last-frame time.
- Malformed frames and unknown categories increment counters capped at 999 and
  do not crash invalidation processing.
- The existing status cluster displays a compact push/poll/retry/off indicator
  and exposes detail in its status popover.
- REST and React Query remain the authoritative reads and polling fallback.

This status is transport truth only. It does not claim that backend data is
fresh or that the trading system is healthy. A durable committed-change cursor
remains a separate, higher-cost improvement if timestamp fingerprints prove
insufficient in production.

### 5. Route recovery and canonical URLs

- Feature pages are loaded with React `lazy` under a route-level `Suspense`
  fallback, so the global shell and operator status remain mounted.
- Retired paths redirect with replacement semantics to their canonical URLs.
- Unknown paths render a shell-preserving 404 with a safe Overview return.
- The existing `vite:preloadError` reload behavior remains the chunk recovery
  mechanism.

The production build's main JavaScript chunk fell from approximately 1.09 MB
before page lazy-loading to approximately 398 KB after it. Feature and shared
chunks now carry the remainder. This is a build observation, not a promise of
network or interaction performance.

### 6. Visible PageHeader role

- Existing page-role text is now visible supporting copy instead of a title
  tooltip.
- `aria-describedby` associates that copy with the page heading.
- Breadcrumbs, actions, and role-absent layouts keep the same shared component.

The main lesson is to render already-owned semantics before inventing more UI
metadata. The pages had useful role descriptions; the shared shell was hiding
them.

### 7. DataTable accessibility

- The optional filter has an explicit accessible name.
- Sortable headers expose `none`, `ascending`, or `descending` through
  `aria-sort`.
- Native buttons own sorting activation, giving Enter and Space behavior
  without custom keyboard code.
- TanStack Table remains the sorting authority and clickable-row behavior is
  unchanged.

Using the native button is both smaller and more reliable than teaching a table
header to imitate button semantics.

## Focused Evidence

- OHLCV and frontend source-contract pytest: 11 passed.
- API client Vitest: 5 passed.
- Focused Ruff check: passed.
- Frontend TypeScript and production Vite build: passed.
- ESLint: zero errors; 23 existing warning-class findings remain, primarily
  hook dependency and TanStack React Compiler compatibility notices.
- Documentation governance and whitespace checks: passed.
- Browser smoke test: shell-preserving 404, safe return, canonical
  `/positions` to `/trades` redirect, persistent global status, and zero browser
  console errors all verified. Backend proxy failures were expected because the
  backend was intentionally not started for this frontend-only smoke test.

No broad macOS pytest suite was run.

## Preserved Ceilings

- No new database, cache framework, state library, schema dependency, chart
  abstraction, or routing framework was introduced.
- Successful API responses still rely on TypeScript compile-time types.
- Realtime invalidation still uses database fingerprints rather than a durable
  committed-change sequence.
- Non-CCXT fallback candles carry provenance but are not opportunistically
  relabeled or cached by outer request layers.
- Risk, live execution, credential authority, and order routing were unchanged.
