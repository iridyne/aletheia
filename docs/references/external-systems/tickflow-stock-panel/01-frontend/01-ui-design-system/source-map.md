# Domain 1 Source Map

## Baseline

This report uses the source baseline captured on 2026-08-07. Reference claims
apply to TickFlow Stock Panel commit
`99bdec875d4bbf5c30ddc43534b81ada0f3b0f6b` unless labelled otherwise.

## Reference Framework Surface

| Concern | Package or mechanism | Version at baseline |
|---|---|---|
| UI runtime | React | `18.3.1` |
| Utility styling | Tailwind CSS | `3.4.10` |
| conditional class composition | `clsx` + `tailwind-merge` | `2.1.1` + `2.5.2` |
| icons | Lucide React | `0.439.0` |
| motion | Framer Motion | `11.5.0` |

The framework selection is conventional. The valuable part is how the project
wires the layers together.

## Theme and Token Path

```text
frontend/index.html
  -> pre-render script chooses html.dark before React mounts
  -> frontend/src/index.css defines light/dark CSS variables
  -> frontend/tailwind.config.ts maps variables into semantic utilities
  -> components compose utilities such as bg-surface/text-muted/rounded-card
  -> frontend/src/lib/theme.ts synchronizes theme state and chart palettes
  -> ECharts/lightweight-charts consumers rebuild from useChartTheme()
```

### Entry points

| Source | Relevant implementation | Evidence |
|---|---|---|
| `frontend/index.html:5-16` | applies the dark class before first paint, using `tf-theme` from local storage | S1 implementation |
| `frontend/src/index.css:9-38` | defines the base/surface/elevated/border/foreground/accent/bull/bear/warning/danger palettes for both themes | S1 implementation |
| `frontend/tailwind.config.ts:11-40` | exposes CSS variables as semantic Tailwind colours, font families, radii, and easing | S1 implementation |
| `frontend/src/lib/theme.ts:16-48` | persists theme, toggles `html.dark`, and synchronizes same-page and cross-tab changes | S1 implementation |
| `frontend/src/lib/theme.ts:51-120` | owns chart-specific dark and light palettes that canvas renderers cannot inherit from CSS | S1 implementation |
| `frontend/src/components/Layout.tsx:87-100` | exposes the theme toggle in the application shell | S2 production call path |

The CSS foundation is deliberately small: `frontend/src/index.css` is 79 lines
and `frontend/tailwind.config.ts` is 44 lines. Most visual composition lives in
component-local utility classes.

## Shared Presentation Primitives

| Primitive | Source | Responsibility | Observed reach |
|---|---|---|---|
| `PageHeader` | `frontend/src/components/PageHeader.tsx:12-27` | common title, visible subtitle, title extras, and right-side controls | 21 JSX occurrences in the current source tree |
| `EmptyState` | `frontend/src/components/EmptyState.tsx:10-19` | icon, explanatory title, and optional guidance | 16 JSX occurrences |
| `Skeleton` | `frontend/src/components/data/Skeleton.tsx:1-7` | dimension-configurable pulse placeholder | 42 JSX occurrences |
| `Modal` | `frontend/src/components/Modal.tsx:40-148` | overlay, dialog semantics, focus entry/trap/restore, Escape, and safe backdrop close | 6 JSX occurrences and multiple dialog families |
| `Toast` | `frontend/src/components/Toast.tsx` | polite global success/error announcements | application-shell consumer |
| `AlertToast` | `frontend/src/components/AlertToast.tsx` | bounded monitoring notifications, action navigation, severity/source language, keyboard activation | application-shell consumer |
| `SealedBadge` | `frontend/src/components/SealedBadge.tsx` | explains degraded versus corrected market-limit data instead of showing an unexplained badge | dashboard/limit-ladder consumers |
| `WarmupBadge` | `frontend/src/components/WarmupBadge.tsx` | exposes derived-data readiness and warm-up state | page-header consumer |

CodeGraph found broad use for `PageHeader`, `EmptyState`, and `Skeleton`, and no
direct frontend tests covering them. The exact JSX counts above are simple
source-token counts, useful for relative reach but not a semantic coverage
metric.

## Component Composition Pattern

The reference does **not** have a complete typed UI primitive library. There is
no shared general-purpose `Button`, `Card`, `Badge`, `Field`, or `Switch` family.
At this baseline the frontend contains approximately 604 raw `<button>` tokens.
Visual consistency is therefore maintained mainly by:

- semantic Tailwind tokens;
- repeated utility-class recipes;
- a small number of high-value behavioral primitives;
- strong page-composition discipline.

`frontend/src/lib/cn.ts` combines `clsx` and `tailwind-merge`; 124 source calls
use `cn(...)`. This lets a component accept class overrides without leaving
contradictory Tailwind utilities active at the same time.

## State-Presentation Sources

| State | Primary evidence |
|---|---|
| initial loading | shared Skeleton plus page-specific skeleton compositions |
| empty result | shared EmptyState with title and guidance rather than a bare `暂无数据` string |
| mutation pending | button text and disabled state commonly derive from `isPending` |
| degraded capability/data | SealedBadge exposes reason, available correction, and settings route |
| long warm-up | WarmupBadge surfaces readiness rather than inventing data |
| success/error notification | global Toast live region |
| realtime alert | AlertToast live region, severity bar, source badge, keyboard action, and bounded visible queue |

The reference project describes a broader four-state review discipline in
comments, but the shared component set does not encode a single typed state
machine for loading, stale, partial, empty, unavailable, and error. Those states
remain partly page-specific.

## Visual Evidence

The repository screenshots for dashboard, screener, backtest, and monitor show:

- a fixed, stable navigation rail;
- dense but predictable 8-ish pixel rhythm;
- low-shadow panels separated mainly by borders and surface tone;
- monospaced/tabular financial values;
- small labels paired with larger decision values;
- restrained accent blue plus market and warning colours;
- repeated card and list geometry across distinct workflows.

The screenshots display `v0.1.19`, while the inspected source declares
`0.1.88`. They are S5 visual artifacts: useful evidence of the design language,
not proof of the current implementation or interaction behavior.

## Evidence Gaps

- No current reference UI runtime was opened for this domain.
- No computed-style or viewport measurements were taken.
- Keyboard behavior was inferred from source, not exercised in a browser.
- Colour contrast ratios were not measured.
- There are no frontend `.test` or `.spec` files in the reference source tree.
- The effect of remote font unavailability was not observed; source confirms
  both Inter and JetBrains Mono are loaded from external origins with fallbacks.
