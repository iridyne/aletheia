# Domain 2 Lessons and Reflections

## Reuse Directly

### Eager shell, lazy feature pages

Keep the navigation and global operator state mounted; suspend only the page
outlet while loading a feature chunk. This is the reference's clearest frontend
architecture advantage.

### Canonical redirects

Retired paths should redirect to one canonical URL rather than render the same
component under several locations. This keeps breadcrumbs, bookmarks, support,
and future telemetry consistent.

### Preserve cached shell decisions during background refresh

An initialization guard should distinguish first load from background fetch so
ordinary cache refresh does not blank the entire application frame.

## Reuse After Adaptation

### Navigation as an ambient status surface

Small, directly relevant indicators such as unread decisions, active data
sync, or stale connectivity can reduce context switching. Keep them bounded and
delegate their queries to narrow components; do not turn the sidebar root into
a service orchestrator.

### Dynamic navigation

Dynamic reference pages are appropriate when the product truly supports
user-defined analysis surfaces. Aletheia should not add generic plugin
navigation during MVP hardening. If a real supported extension path emerges,
derive its entries from explicit capability facts and preserve a stable core
workflow taxonomy.

### First-use onboarding

The five-step sequence, capability discovery, immediate cache update, and clear
skip paths are useful product patterns. Aletheia should apply them only to
optional configuration or education. Safety and authority gates require
fail-closed, audited behavior.

### Destination-aware route fallback

The reference spinner is better than a blank page. Aletheia can improve it by
keeping the shell visible, naming the destination, and offering retry/return
behavior through a route error boundary.

## Study Further

- Which Aletheia pages contribute most to the initial JavaScript dependency
  graph before choosing the first lazy boundary.
- Whether canonical redirects should preserve query strings for every existing
  legacy path.
- Whether the command palette, sidebar, and PageHeader can all consume one
  route manifest without importing React page modules eagerly.
- Which operational indicators deserve global visibility and which should stay
  on Overview or their owning page.
- Whether compact/mobile operation is a supported operator workflow or only a
  graceful fallback.

## Do Not Reuse

### Flat capability catalogue

Aletheia's Research/Decision/Portfolio grouping expresses the trading workflow
better than a growing flat list. Do not discard it for visual similarity.

### Fail-open safety gates

Do not generalize TickFlow's optional-onboarding failure behavior to trading
authority or risk controls.

### Business orchestration in Layout

Do not move provider selection, realtime mutation, market-hours logic, and
multiple data queries into Aletheia's route root merely because the result is
globally visible.

### Desktop-only shell assumptions

Aletheia already has a responsive drawer and rail. Do not regress that boundary
when adopting a fixed terminal-style sidebar.

### Lazy imports without failure recovery

Adding route splitting without preserving preload-error recovery and adding an
error boundary trades bundle size for blank-screen risk.

## Durable Design Reflections

### Information architecture is a product model

TickFlow's flat list says “these are the available tools.” Aletheia's grouped
list says “this is the path from research through decision to portfolio.” For a
trading operating system, the second model teaches the workflow and should
remain the primary organizing principle.

### Navigation metadata is durable system data

Path, label, group, aliases, visibility, and page role are currently scattered
across components. Treating them as a reviewed data manifest makes navigation
easier to validate without turning documentation into a second route registry.

### Global visibility does not imply global ownership

A status can appear in the shell while its data hook and behavior remain owned
by a small child component. This keeps the shell stable both visually and
architecturally.

### Lazy loading is a recovery design, not only a performance trick

Once code is split, deployment skew, missing chunks, offline state, and route
errors become visible product states. The route boundary must include recovery,
not only `lazy(...)` and a spinner.

### Canonical URLs are part of semantic honesty

Rendering a replacement page under an obsolete URL hides the product's current
structure. Redirecting to the canonical route makes the interface, navigation,
and documentation agree.

## Evidence Boundary

The findings are based on current source and static call paths. No bundle
measurement, current browser navigation, deep-link reload, mobile viewport,
chunk-failure exercise, or backend-authentication observation was performed.
