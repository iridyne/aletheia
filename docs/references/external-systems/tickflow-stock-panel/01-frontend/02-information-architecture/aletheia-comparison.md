# Domain 2 Aletheia Comparison

## Summary

TickFlow has the stronger code-loading boundary and a more explicit
login/onboarding route structure. Aletheia has the stronger capability taxonomy,
responsive navigation shell, canonical safety-oriented labels, and deployment
preload recovery.

Both applications place too much responsibility in the top-level shell and
duplicate route metadata between navigation and route declarations.

## Comparison Matrix

| Boundary | TickFlow | Aletheia | Assessment |
|---|---|---|---|
| router API | `createBrowserRouter` with nested Layout/Outlet | BrowserRouter plus Routes inside App | TickFlow provides a cleaner shell/child route boundary |
| top-level page loading | 18 feature pages lazy-loaded | 14 feature pages statically imported | TickFlow is structurally better for initial bundle isolation |
| route fallback | one Suspense spinner | no top-level route Suspense; global query progress bar | each covers a different wait; Aletheia needs a route fallback if it adopts lazy pages |
| failed chunk handling | no explicit recovery found | `vite:preloadError` reload handler | Aletheia already has an important deployment-recovery guard |
| public/bootstrap routes | separate Auth and five-step Onboarding | no equivalent global route gate; setup guidance lives on Overview | product boundaries differ; do not infer Aletheia needs TickFlow's auth flow |
| main navigation | flat 14-item list plus dynamic pages and user ordering/hiding | 14 items grouped into Research, Decision, Portfolio, and global actions | Aletheia has the clearer trading-system taxonomy |
| responsive shell | fixed 14rem desktop sidebar | desktop rail, group collapse, 700px off-canvas drawer | Aletheia is materially stronger |
| operational context | tier, AI, provider, realtime, indices, sync, unread alerts in sidebar | market ticker, master status cluster, pause/resume, preview label, command palette | both use the shell as an operational surface; Aletheia's grouping is clearer |
| legacy paths | canonical Navigate redirects | mix of redirects and rendering the canonical component under old URLs | TickFlow's redirect approach preserves canonical location more consistently |
| unknown path | no explicit 404 | no explicit 404 | shared gap |
| route error boundary | not found | not found | shared gap |
| route metadata | split between router, Layout, and pages | split between App NAV, Routes, and PageHeader consumers | shared architectural gap |

## What Aletheia Already Does Better

### Workflow taxonomy

Research, Decision, and Portfolio are stable operator concepts. They explain
why a destination exists, not just what component it contains. The grouping
also fits Aletheia's Observe/Decide/Execute/Evolve/Operate language better than
TickFlow's flat functional list.

### Honest route catalogue

The NAV comment requires visible items to point only at implemented routes.
This aligns with Aletheia's capability-integrity rule. Dynamic or aspirational
entries should not appear merely because a plugin or design exists.

### Responsive navigation

Aletheia explicitly supports:

- a full desktop sidebar;
- a persistent compact icon rail;
- collapsible workflow groups;
- a mobile drawer with backdrop and hamburger;
- a mobile-specific suppression of the meaningless rail toggle.

TickFlow's shell has no equivalent responsive path.

### Ambient safety and market context

The top bar prioritizes market ticker, system state, live/kill indicators, and
pause/resume. This is more appropriate for an automated trading system than
placing plan/tier marketing context at the top of the shell.

### Asset-version recovery

The `vite:preloadError` handler reloads when an old page tries to import a chunk
that no longer exists after deployment. This must remain if route-level lazy
loading is introduced.

## Where Aletheia Can Learn

### Route-level lazy boundaries

Static imports pull every top-level page into the initial dependency graph.
Some pages include charting, settings, research, or analytics code irrelevant
to the first view. TickFlow's “eager shell, lazy page” boundary is directly
applicable.

The safe target is not merely adding `lazy(...)`. It includes:

- one shell-preserving Suspense fallback;
- the existing preload-error reload path;
- an explicit route error boundary;
- a 404 surface;
- destination-aware fallback text when useful.

### Canonical redirects for all retired routes

Aletheia redirects `/proposals` to `/blotter`, but `/agent-runs`,
`/opportunities`, `/paper-trades`, `/positions`, `/dreaming`, `/knowledge`, and
`/reports` directly render the replacement component under the old URL.

This can leave the browser on a non-canonical path and causes the PageHeader's
exact NAV lookup to miss its group breadcrumb. Canonical redirects make links,
analytics, breadcrumbs, and support language converge.

### A standalone route manifest

PageHeader imports NAV from App, while App imports pages that themselves render
PageHeader. This creates an avoidable module cycle and makes page metadata
dependent on the shell component.

A small, data-only route manifest could own:

- canonical path;
- legacy aliases;
- navigation label and group;
- icon key;
- page title/short role;
- visibility and operator capability rules;
- lazy component loader.

Router, sidebar, breadcrumbs, command palette, and possibly document titles can
then consume one source without putting runtime business state into it.

### Keep the shell mounted while pages load

TickFlow's nested Layout/Outlet design naturally preserves the sidebar and
global status during a page chunk transition. Aletheia should preserve its
market ticker, safety status, and command palette when adopting lazy routes;
only the page content area should suspend.

## Shared Problems to Avoid Preserving

### God shells

TickFlow Layout is 698 lines and Aletheia App is 697 lines. Both combine route
composition with operational queries, global controls, status rendering, and
navigation. Page lazy loading alone does not fix shell ownership.

A more durable split is:

```text
route manifest
  -> router
  -> navigation view

AppShell
  -> Sidebar
  -> Topbar/Status
  -> PageOutlet
  -> Global overlays
```

Operational hooks belong in the smallest status/control component that needs
them, not in the route catalogue or shell root.

### No 404 or route error surface

Both applications can render an empty or framework-default result for an
unknown path or route failure. A local operator interface should keep the shell,
name the failed destination, and offer a safe return route.

## Safety Boundary

TickFlow's onboarding is deliberately fail-open. Aletheia must not copy that
behavior for:

- live-trading unlock;
- risk acknowledgement;
- credential presence;
- execution authority;
- destructive commands;
- required migration or runtime-health gates.

Optional product tours may fail open. Safety and authority gates must use their
own approved failure semantics.
