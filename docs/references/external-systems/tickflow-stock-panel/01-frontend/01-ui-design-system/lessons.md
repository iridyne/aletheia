# Domain 1 Lessons and Reflections

## Reuse Directly

### One modal behavior contract

The focus lifecycle and safe backdrop logic in TickFlow's Modal solve real,
repeatable interaction bugs. The behavior is reusable even if Aletheia keeps
its current CSS and component names.

### Visible page-purpose text

Page headers should expose the page's role or limitation in visible text when
it helps an operator understand the workflow. A tooltip-only description is too
easy to miss.

### Chart palettes as an explicit adapter

Canvas renderers cannot inherit DOM CSS variables. Keep a typed adapter between
semantic UI colours and chart-library options, and make all chart components
consume it.

### Explain degraded data at the point of use

SealedBadge is valuable because it connects a visible badge to the missing
capability, the consequence, and the recovery route. Apply that pattern to
Aletheia's data quality and configuration boundaries without copying the
A-share-specific content.

## Reuse After Adaptation

### Semantic utility composition

TickFlow's `bg-surface`, `text-muted`, and `rounded-card` vocabulary makes local
component composition fast and legible. Aletheia should expand this approach
only at repeated boundaries and behind typed primitives. A global conversion of
the 4,717-line stylesheet would create a large, low-value visual regression
surface.

### EmptyState presentation

Reuse the visual anatomy—icon, clear title, helpful explanation, optional
action—but require an explicit semantic variant so empty, unavailable, stale,
partial, not configured, and permission-limited cannot be conflated.

### Density rules

Reuse the principle that secondary text compresses while primary decisions and
controls retain clarity and usable targets. Do not copy raw sizes from a Chinese
A-share desktop UI into every Aletheia viewport.

### Motion language

Short opacity and translation transitions can improve spatial continuity, but
any adoption must consume the existing reduced-motion boundary. Framer Motion
itself is not the lesson and is not required.

### Theme propagation

The pre-render class, persisted preference, cross-tab signal, DOM variables,
and chart adapter form a good complete path. Adding a light theme is a separate
product choice; the reusable lesson is that a theme must include canvas and
first-paint behavior, not just DOM colours.

## Study Further

- Whether Aletheia operators actually need light mode in the main trading
  surface.
- Whether the existing chart palette should derive from generated CSS-token
  data or remain a small reviewed literal adapter.
- Which repeated Aletheia CSS families provide the highest-value first
  extraction into typed components.
- Whether local font bundling materially improves offline fidelity, package
  size, and startup behavior for both projects.
- What minimum responsive and contrast evidence should accompany any future
  visual change.

## Do Not Reuse

### Raw-button proliferation

Approximately 604 raw buttons mean disabled, busy, destructive, keyboard, and
focus behavior remain distributed. A visually consistent screenshot is not a
substitute for a constrained action primitive.

### A-share colour direction

Do not copy red-up/green-down into Aletheia's crypto/global operator surfaces.
Copy only the separation between market direction and generic UI status.

### Unconditional animation

Do not reproduce the reference's broad Framer Motion usage without a reduced-
motion policy.

### Custom calendar semantics as currently implemented

The reference DatePicker has useful viewport positioning and portal behavior,
but lacks the keyboard and ARIA model expected of a calendar control. Its
positioning logic can be studied; the accessibility contract should not be
copied.

### Remote fonts as an assumed dependency

Both applications currently request fonts from external origins. A local-first
application should not assume those requests succeed or treat the remote font
metrics as guaranteed.

### Visual confidence without frontend regression evidence

The reference contains no frontend tests. Its source and screenshots show
valuable practices, but they do not prove current cross-browser, keyboard,
theme, or responsive correctness.

## Durable Design Reflections

### Visual quality is a constraint system

The reference feels coherent because colour roles, depth, radius, density,
numeric typography, and page composition agree. The lesson is not “use
Tailwind” or “use Framer Motion.” Either project can become visually fragmented
while using the same libraries if those constraints are not shared.

### Component architecture and visual polish are different axes

TickFlow is currently stronger in polish but weaker in general primitive
abstraction. Aletheia is stronger in typed primitives but its global stylesheet
and feature-specific compositions make the system feel less unified. The best
target combines Aletheia's semantic APIs with TickFlow's composition discipline.

### State honesty belongs inside the design system

Loading, empty, stale, partial, unavailable, degraded, and failed are not merely
copywriting choices. They determine whether an operator trusts the interface.
A future Aletheia presentation layer should make the honest state harder to
omit than the misleading state.

### A trading UI needs two semantic colour planes

One plane describes the market: direction, price movement, exposure, and PnL.
The other describes the application: success, warning, failure, danger,
disabled, stale, and unavailable. Keeping them separate is more important than
the exact hue values.

### Migrate at repeated boundaries, not by stylesheet ideology

Aletheia's Tailwind bridge already documents why a mechanical global migration
could silently resize or restyle the cockpit. Future extraction should start
where behavior and visual rules repeat—dialogs, state panels, page headers,
actions—not with a wholesale class rewrite.

## Evidence Boundary

These lessons are based on current source and dated reference screenshots. No
current browser session, contrast audit, responsive sweep, or keyboard exercise
was performed. The domain conclusions describe practices and source risks; they
do not claim the reference UI is currently defect-free.
