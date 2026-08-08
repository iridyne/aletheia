# Domain 2: Information Architecture and Route Loading

## Scope

Study how each frontend:

- mounts its router and application providers;
- separates public/bootstrap routes from the authenticated application shell;
- groups and labels product capabilities;
- maintains navigation context and legacy links;
- loads page code and represents route transitions;
- adapts sidebar navigation to smaller viewports;
- exposes global operational state without entering page internals.

## Exclusions

Query-key design, API request semantics, page-level table and chart behavior,
backend authentication, and implementation changes are separate domains.

## Domain Reports

- `source-map.md` — route, shell, guard, navigation, and loading entry points.
- `information-architecture-analysis.md` — reference mechanisms, strengths,
  coupling, and failure modes.
- `aletheia-comparison.md` — like-for-like comparison with Aletheia.
- `lessons.md` — reusable, adapted, deferred, and rejected lessons.
