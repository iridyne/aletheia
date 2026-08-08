# Domain 2 Source Map

## Reference Production Path

```text
frontend/index.html
  -> frontend/src/main.tsx
  -> RouterProvider(router)
  -> public /login or /onboarding route
     OR root OnboardingGuard
       -> Layout shell
       -> one Suspense boundary
       -> Outlet
       -> lazy page chunk
```

## Reference Route Sources

| Source | Responsibility | Evidence |
|---|---|---|
| `frontend/src/main.tsx:30-49` | Query client and RouterProvider mount | S2 production call path |
| `frontend/src/router.tsx:12-29` | 18 route-level lazy page imports | S1 implementation |
| `frontend/src/router.tsx:34-57` | first-load OnboardingGuard | S1 implementation |
| `frontend/src/router.tsx:59-97` | public routes, nested application routes, redirects, and hidden dev route | S2 production call path |
| `frontend/src/components/Layout.tsx:70-85` | 14 built-in navigation entries | S1 implementation |
| `frontend/src/components/Layout.tsx:383-404` | dynamic analysis pages, saved ordering, and hidden-item preferences | S1 implementation |
| `frontend/src/components/Layout.tsx:427-662` | fixed sidebar, brand/capability cards, navigation, provider/realtime status, theme, and settings | S2 production call path |
| `frontend/src/components/Layout.tsx:664-696` | main scroll owner, reconnect notice, Suspense/Outlet, toasts, and global AI hosts | S2 production call path |
| `frontend/src/pages/Onboarding.tsx:35-164` | five-step first-use journey | S2 production call path |
| `frontend/src/pages/Auth.tsx:21-160` | password setup/login and return-to-requested-route behavior | S2 production call path |

## Reference Navigation Model

The built-in list is a flat functional catalogue:

```text
Dashboard
Watchlist
Screener
Backtest
Stock analysis
Limit ladder
Concept analysis
Industry analysis
Financials
Monitor
Market regime
Review
Indices
Data
```

Visible external-analysis menu entries are appended at runtime. User
preferences can reorder or hide built-in and dynamic entries. Settings remains
anchored at the bottom, outside the configurable list.

The sidebar also owns operational context:

- data tier and AI configuration;
- active data provider and supported datasets;
- realtime quote switch and session status;
- selected market-index quotes;
- data-sync progress and recent completion;
- unread monitor count;
- stream reconnect notice;
- theme and version.

## Reference Loading and Recovery Surface

- Layout, Auth, and Onboarding are eagerly loaded.
- Eighteen feature pages are lazy imports.
- One Suspense boundary around Outlet displays a small centered spinner.
- Background settings refresh preserves the current shell; the guard uses
  `isLoading`, not every `isFetching` transition.
- Legacy route aliases redirect to canonical destinations.
- No `errorElement`, React route error boundary, lazy-chunk recovery handler,
  wildcard route, or 404 surface was found.

## Reference Viewport Surface

The shell root is a fixed `14rem + 1fr` grid. No responsive variant, sidebar
drawer, rail collapse, or navigation breakpoint was found in Layout or the
79-line global stylesheet. Individual onboarding content uses responsive grid
utilities, but the main application shell is desktop-first.

## Aletheia Production Path

```text
frontend/index.html
  -> frontend/src/main.tsx
  -> QueryClientProvider
  -> BrowserRouter
  -> ToastProvider / ConfirmProvider
  -> App
     -> persistent sidebar + topbar + global helpers
     -> Routes
     -> eagerly imported page component
```

## Aletheia Route Sources

| Source | Responsibility | Evidence |
|---|---|---|
| `frontend/src/main.tsx:12-15` | reloads after a Vite preload error | S1 implementation |
| `frontend/src/main.tsx:43-54` | provider stack and BrowserRouter mount | S2 production call path |
| `frontend/src/App.tsx:26-39` | 14 top-level page modules imported eagerly | S1 implementation |
| `frontend/src/App.tsx:50-101` | typed, grouped navigation catalogue | S1 implementation |
| `frontend/src/App.tsx:575-697` | responsive shell, navigation, Routes, redirects, and global surfaces | S2 production call path |
| `frontend/src/index.css:173-280` | fixed desktop sidebar, mobile drawer, desktop rail, and breakpoint behavior | S1 implementation |
| `frontend/src/components/PageHeader.tsx:20-35` | group breadcrumb derived from NAV and current location | S2 production call path |
| `frontend/src/pages/Overview.tsx:130` | setup guidance is placed on Overview rather than a global route gate | S2 production call path |

## Aletheia Navigation Model

The visible catalogue is grouped by operator workflow:

```text
Global: Overview, Command Center
Research: Universe, Markets, Prediction Markets, Serenity Lens, News Intel
Decision: Blotter, Agent Activity
Portfolio: Trades & Positions, Risk & Controls
Global: Learning, Usage, Settings
```

Groups can collapse. The whole sidebar can become a persistent 56px icon rail
on desktop. At 700px and below it becomes an off-canvas 240px drawer with a
backdrop and hamburger control.

## Aletheia Loading and Recovery Surface

- Fourteen top-level pages are statically imported into App.
- There is no route-level Suspense boundary.
- One expensive Trade Insights chart panel is lazy-loaded inside
  TradesPositions.
- A global FetchProgress bar distinguishes foreground queries from background
  polling.
- A Vite preload error triggers a reload, protecting the existing dynamic
  import and any future route chunks from an old-HTML/new-assets mismatch.
- No wildcard route or route-level error boundary was found.

## Evidence Gaps

- No current browser session or bundle analyzer was used.
- Initial JavaScript byte sizes and route transition timings are not available.
- Responsive behavior was inferred from current CSS, not exercised at multiple
  viewports.
- Authentication and setup behavior were inspected only at the frontend route
  boundary.
- Neither project has direct automated route-manifest parity coverage at this
  baseline.
