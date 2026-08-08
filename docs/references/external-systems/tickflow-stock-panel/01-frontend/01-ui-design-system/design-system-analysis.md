# TickFlow UI Design-System Analysis

## Finding

TickFlow's UI quality is primarily the result of a small, coherent visual
vocabulary applied consistently across high-density pages. It is not the result
of a sophisticated third-party component framework, and it is not backed by a
complete internal primitive library.

That distinction matters: copying React components would miss the main value,
while copying the constraints and state vocabulary can improve another
application without copying its page designs.

## 1. The Token Model Is Semantic

The central colour names describe roles rather than arbitrary shades:

- `base`, `surface`, and `elevated` describe depth;
- `foreground`, `secondary`, and `muted` describe information hierarchy;
- `accent` describes interaction emphasis;
- `bull` and `bear` describe market direction;
- `warning` and `danger` describe UI state.

The explicit comment that bull/bear colours are not generic UI status colours
is a valuable domain boundary. It prevents a positive price move, successful
operation, destructive action, and system health from collapsing into one
ambiguous red/green convention.

The mapping is completed in Tailwind configuration, so the same vocabulary is
available in every component and supports opacity modifiers such as
`bg-accent/15` without creating extra CSS classes.

### Limitation

The discipline is not absolute. Component source still contains many palette
escape hatches such as amber, emerald, purple, sky, and raw hex values. A simple
static count found 348 colour-literal occurrences in TS/TSX and 25 in the small
global CSS file. Some are legitimate chart or branding colours; others weaken
the claim that the semantic palette is the only visual authority.

## 2. Density Comes from Hierarchy, Not Uniform Smallness

The screenshots show several repeatable decisions:

- navigation and global controls retain stable hit areas;
- labels and metadata are small and muted;
- decision values, prices, percentages, and status counts receive stronger
  contrast and monospaced numerals;
- panels use thin borders and small surface changes instead of heavy shadows;
- related values sit in compact grids, while unrelated areas retain clear
  panel boundaries;
- page headers remain short so vertical space goes to market information.

The result is dense because less-important information is compressed, not
because every element is indiscriminately made smaller. This is a more durable
lesson than copying a particular font size.

## 3. Theme Switching Is End-to-End

Theme support crosses four rendering layers:

1. a pre-render script prevents a light/dark flash;
2. local storage preserves the user's selection;
3. CSS variables and Tailwind utilities restyle DOM content;
4. a typed chart palette restyles canvas-based charts.

The custom event plus browser `storage` event also synchronizes theme changes
within the current page and across tabs. This is a complete theme propagation
path rather than a cosmetic class toggle.

### Limitations

- The initial `theme-color` meta remains a fixed purple value rather than being
  synchronized with the selected theme.
- Inter and JetBrains Mono are loaded from external font services. The fallback
  stack prevents total failure, but a local-first desktop application can still
  change metrics and visual identity when offline.
- Chart palettes duplicate literal colours outside the CSS-variable source of
  truth because canvas renderers need concrete values. The typed central module
  makes this duplication controlled, but it still requires deliberate updates.

## 4. Behavioral Primitives Are Stronger Than Visual Primitives

The best shared component is `Modal`. It solves behavioral failure modes that
individual pages routinely get wrong:

- `role="dialog"`, `aria-modal`, and a supplied accessible name;
- first-focus placement;
- Tab and Shift+Tab focus trapping;
- Escape closure;
- focus restoration;
- a stable close callback that does not steal focus on every parent render;
- backdrop closure only when both pointer down and pointer up occur on the
  backdrop, preventing accidental close after selecting text and dragging out.

`AlertToast` similarly combines product semantics with interaction semantics:
source, severity, market value, keyboard activation, close control, bounded
visible count, sound, and voice routing meet in one presentation boundary.

By contrast, the system has no general typed Button/Card/Badge/Form layer.
Hundreds of raw buttons carry repeated utility recipes. This makes the current
visual result dependent on continued author discipline and makes global state
or accessibility improvements expensive.

## 5. State Is Shown Near the User's Decision

Several components turn backend limitations into visible explanations:

- `SealedBadge` distinguishes corrected data from degraded data and explains
  the missing depth capability or historical limitation.
- `WarmupBadge` exposes a derived-data warm-up boundary.
- `EmptyState` gives a title and optional next-step hint rather than presenting
  absence as a generic error.
- pending mutations generally change the action label and disable the control.

This is valuable because the UI describes why a result is limited instead of
merely changing colour.

### Limitation

The shared EmptyState has no action slot, retry slot, status role, or explicit
distinction among true empty, not configured, unavailable, stale, and partial.
Those semantics must still be supplied by callers and can be collapsed by a
careless page.

## 6. Accessibility Is Uneven

Strong examples:

- shared Modal keyboard and focus behavior;
- AlertToast live region and keyboard-activatable notification cards;
- labelled close controls;
- checkbox roles and `aria-checked` in several settings controls.

Gaps found in source:

- the custom DatePicker has no calendar/grid/dialog roles, `aria-expanded`,
  keyboard date navigation, or Escape handling;
- Framer Motion is imported by approximately 50 frontend source files, but no
  `prefers-reduced-motion` or `useReducedMotion` handling was found;
- many custom buttons and popovers are implemented independently, so the Modal
  standard does not automatically protect them;
- no frontend automated tests preserve the accessibility behavior.

## 7. Motion Has a Consistent Feel but No User Safety Boundary

The project frequently uses the same ease curve and short opacity/translation
transitions. This creates a coherent feel and avoids dramatic page motion.
However, the absence of a reduced-motion path is a material gap precisely
because motion is used so widely. The lesson is to centralize motion policy,
not to copy the raw number of animations.

## 8. Why the Screens Feel Better Than the Component Inventory Suggests

The project benefits from four mutually reinforcing constraints:

1. a very small semantic palette;
2. repeated surface/border/radius geometry;
3. strict information hierarchy through type, opacity, and numeric alignment;
4. product-specific components that explain market-data states.

This produces visual unity even though the primitive architecture is partial.
It also explains the maintenance risk: consistency lives partly in convention
and reviewer taste rather than in enforced component APIs.
