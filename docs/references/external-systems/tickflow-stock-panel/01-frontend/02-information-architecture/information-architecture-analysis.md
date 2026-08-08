# TickFlow Information-Architecture Analysis

## Finding

TickFlow separates the public/bootstrap experience from the application shell
and aggressively splits feature-page code. Inside the shell, however, it uses a
flat, increasingly personalized menu and concentrates navigation, operational
status, realtime controls, queries, and global overlays in one large Layout.

The result is quick page loading and strong ambient context on a desktop, but a
highly coupled shell and a weaker cognitive hierarchy as capabilities grow.

## 1. The Router Has a Clear Shell Boundary

The route tree distinguishes:

- `/login`;
- `/onboarding`;
- the guarded root application shell;
- nested feature pages;
- hidden developer tooling;
- compatibility redirects.

Because the shell is the parent route, navigation, global notices, and global
hosts remain mounted while only the Outlet changes. This avoids rebuilding the
entire application frame on every page transition.

## 2. Lazy Loading Is Applied at the Right Granularity

All eighteen feature pages use route-level `lazy(...)`, while Layout,
Onboarding, and Auth remain eager. This is a sensible boundary:

- the minimum viable shell is immediately available;
- ECharts, lightweight-charts, Framer Motion consumers, backtest logic, and
  feature-only components can stay out of the initial page chunk;
- one Suspense fallback gives every route a transition state.

The value comes from page-boundary splitting, not from making every small
component lazy.

### Failure gaps

- There is no route error element or React error boundary.
- There is no explicit recovery from a failed lazy chunk after a deployment.
- The Suspense fallback is a spinner without the destination name, retry, or
  shell-level diagnostic.
- Unknown paths have no explicit 404 route.

## 3. Onboarding Avoids a Common Cache Race

On successful completion, Onboarding updates the settings cache before
invalidating and navigating. The guard therefore sees the new completion flag
immediately instead of redirecting the user back while a background refetch is
still pending.

The guard also distinguishes first load from background fetch. Existing cached
content remains visible during a refresh, preventing a whole-shell loading
flash on ordinary navigation.

### Trust limitation

The guard fails open in two places:

- a settings query error or missing data allows the shell to render;
- a failed completion write still navigates into the shell.

This may be reasonable for an optional personal-product tour. It is not a
general pattern for safety acknowledgement, credentials, permissions, live
unlock, or destructive-operation gates.

## 4. The Sidebar Is an Operational Console

The sidebar does more than navigate. It continuously answers:

- which provider and data capabilities are active;
- whether realtime quotes are enabled, paused, or outside trading hours;
- whether data synchronization is active or just completed;
- whether monitor events are unread;
- which plan/tier and AI configuration are active;
- how major indices are moving.

This shortens the distance between “where am I?” and “is the system ready?” It
also makes status visible without opening a settings or health page.

### Coupling cost

The 698-line Layout owns multiple queries, stream subscription, mutations,
market-hours logic, capability checks, provider display, navigation
personalization, shell rendering, and global overlay hosts. A failure or change
in one domain can rerender or complicate the entire shell. The shell is stable
visually but not narrow architecturally.

## 5. Navigation Personalization Is Powerful but Weakens Shared Orientation

Dynamic analysis pages, saved order, and hidden items let a user shape the
terminal around their workflow. This is valuable in a single-user analytical
product where extensions can add pages.

The trade-off is that documentation, screenshots, support, and other operators
cannot assume the same menu. A saved order can also outlive renamed or removed
routes. The current code appends unknown new pages after the saved set, which is
a reasonable forward-compatibility behavior, but the navigation list and route
definitions still live separately.

## 6. The Flat Catalogue Will Become Harder to Scan

Fourteen built-in destinations plus dynamic analysis entries appear in one
vertical list. Icons and order help, but there are no explicit groups such as
Research, Decision, Portfolio, or Operations. The sidebar compensates with
strong labels and active-state styling, but the user must still remember the
functional taxonomy.

This architecture fits a focused stock-analysis panel better than an expanding
trading operating system.

## 7. Desktop-First Is an Explicit Practical Boundary

The fixed 14rem sidebar and full-height grid align with the dense screenshots
and desktop trading use case. Individual pages may respond internally, but the
shell itself has no mobile drawer or narrow-screen collapse. This is a valid
choice if the supported product surface is desktop-only; it becomes a defect if
mobile or compact-window operation is promised.

## 8. Route Metadata Is Duplicated

Labels/icons/order live in Layout, paths/elements/redirects live in router.tsx,
and page subtitles live in individual pages. There is no single route manifest
from which the router, navigation, command palette, breadcrumbs, and document
titles can derive.

This creates possible drift:

- a route can exist but be absent from navigation;
- a navigation entry can point at a removed route;
- a hidden dev route can be exposed accidentally;
- a renamed route may keep multiple inconsistent labels;
- page loading, title, and capability metadata cannot be reviewed together.

## 9. The Router Has No Direct Regression Evidence

CodeGraph found no covering frontend tests for router, Layout, Auth, or
Onboarding. Static source shows deliberate race handling and redirects, but it
does not prove navigation, deep links, reloads, or chunk failures in the current
runtime.
