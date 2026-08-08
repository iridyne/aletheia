# Domain 1: UI Design System and Primitives

## Scope

Study the implementation behind TickFlow Stock Panel's visual coherence and
data-dense usability:

- Tailwind theme and CSS-variable tokens;
- financial, status, and interaction colour semantics;
- typography and numeric presentation;
- spacing, borders, radius, elevation, and density;
- reusable Button, Modal, Card, EmptyState, PageHeader, badge, tooltip, and
  skeleton patterns;
- focus, keyboard, ARIA, disabled, loading, empty, stale, refreshing, and error
  states;
- the boundary between global CSS, utilities, page composition, and component
  variants.

## Exclusions

Routing, query-cache behavior, data-table algorithms, chart data processing,
backend APIs, and implementation changes to Aletheia are separate domains.

## Expected Evidence Outputs

The domain report will include a token map, component inventory, representative
composition paths, state-presentation matrix, accessibility observations,
Aletheia comparison, valuable lessons, rejected patterns, and evidence gaps.

Screenshots may illustrate visual results, but implementation claims must trace
to current source. Repository screenshots with a different displayed version
will be labelled as dated visual artifacts.

## Domain Reports

- `source-map.md` — implementation entry points, dependencies, consumers, and
  evidence coverage.
- `design-system-analysis.md` — how the reference visual system works, why it
  feels coherent, and where its abstractions break down.
- `aletheia-comparison.md` — like-for-like comparison with Aletheia's current
  tokens, primitives, state presentation, and accessibility.
- `lessons.md` — direct, adapted, deferred, and rejected lessons plus durable
  design reflections.
