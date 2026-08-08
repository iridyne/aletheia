# Domain 1 Aletheia Comparison

## Summary

TickFlow is ahead in cross-page visual composition and complete light/dark
theme propagation. Aletheia is ahead in typed primitive structure, semantic
state honesty, form accessibility, reduced-motion handling, and frontend test
evidence.

The correct conclusion is not “replace Aletheia's UI framework.” Both projects
use React and Tailwind, but they concentrate complexity in different places.

## Like-for-Like Matrix

| Boundary | TickFlow | Aletheia | Assessment |
|---|---|---|---|
| styling architecture | 79-line global CSS plus Tailwind 3 semantic utilities composed in TSX | 4,717-line global CSS, semantic classes, and a deliberately limited Tailwind 4 utility bridge | TickFlow is easier to compose locally; Aletheia has stronger legacy visual preservation but a much larger cascade surface |
| colour hierarchy | depth, text, accent, market direction, warning, and danger are named separately | depth, text, accent, green/red/yellow/blue tokens are centralized | both are coherent; TickFlow expresses market-vs-UI intent more explicitly |
| density tokens | radii and fonts are centralized; spacing mostly follows Tailwind | explicit 4/8/12/16/20/24/32 spacing and nine-step font scale | Aletheia's written density contract is more precise; adoption remains incremental |
| theme support | dark/light DOM tokens, pre-render class, cross-tab sync, and dual chart palettes | dark-only DOM theme and one centralized lightweight-chart palette | TickFlow is materially more complete if light theme is a product requirement |
| class composition | `clsx` plus `tailwind-merge` resolves conflicting utilities | `cx` only joins semantic class names | each fits its style model; Aletheia would need merge semantics only if it expands utility-driven variants |
| primitive library | strong Modal/EmptyState/PageHeader/Skeleton, but no general Button/Card/Badge/Form system | typed Button/Card/Badge/Status/ListRow/Field/Switch/Toast barrel | Aletheia has the stronger primitive architecture |
| raw button surface | about 604 raw `<button>` tokens | about 105 raw `<button>` and 76 shared `<Button>` tokens | TickFlow depends much more on repeated recipes and discipline |
| modal behavior | focus entry, trap, restore, Escape, accessible naming, safe backdrop handling | ConfirmModal has alertdialog, accessible title, and autofocus but no Escape/trap/restore; DetailDrawer separately implements a trap | TickFlow's common Modal is the clearer reusable contract |
| form accessibility | inconsistent custom controls; DatePicker lacks calendar keyboard semantics | Field composes label, description, error IDs and `aria-invalid`; Switch resolves visible and inherited labels | Aletheia is stronger |
| empty/loading/error | visually polished EmptyState and reusable Skeleton; capability badges explain some degraded states | page-specific skeletons plus strong stale/partial/unavailable/empty distinctions in Learning and inline trust-critical errors in Risk | combine TickFlow presentation quality with Aletheia semantic precision |
| page header | visible subtitle, extras, and right controls | breadcrumb and actions; the `role` description is only placed in the title attribute | TickFlow communicates page purpose more visibly; Aletheia communicates hierarchy better |
| motion | Framer Motion across roughly 50 files with consistent short easing | mostly CSS transitions and animations | TickFlow feels more polished, but Aletheia is safer because it has global reduced-motion suppression |
| UI evidence | no frontend automated tests found | theme-compatibility, settings semantics, switches, guards, command palette, and other frontend tests exist | Aletheia has substantially stronger regression evidence |
| font delivery | remote Inter and JetBrains Mono with fallbacks | remote Space Grotesk and IBM Plex families with fallbacks | both retain an unnecessary external dependency for local-first rendering |

Static token counts are orientation evidence, not quality scores.

## What Aletheia Already Does Better

### 1. Typed, discoverable primitives

`frontend/src/components/ui/index.ts` presents one import surface for Button,
Card, Badge, status components, settings fields, Switch, Confirm, and Toast.
Variant unions reject unknown button and badge tones at compile time. TickFlow's
equivalent visual recipes are often repeated strings.

### 2. Semantic honesty for read states

Aletheia's Learning surfaces distinguish:

- loading;
- stale retained data;
- partial data;
- unavailable reads;
- legitimate empty results.

The UI text explicitly says that a failed read is not an empty result. This is
more important to a trading system than a prettier generic EmptyState.

### 3. Trust-critical errors stay in context

RiskControls deliberately places mutation failure beneath the control instead
of relying on an auto-dismissed toast. TickFlow's global toast is useful for
ordinary feedback, but should not replace persistent, local error evidence for
risk or execution operations.

### 4. Form semantics

Aletheia's `Field` binds labels, descriptions, and errors to the input and
preserves an existing `aria-describedby`. The Switch component also has a
documented label precedence model. These are durable component contracts, not
page-level conventions.

### 5. Reduced motion

Aletheia globally suppresses transitions, infinite pulses, shimmer, and smooth
scrolling when the operating system requests reduced motion. TickFlow currently
has no equivalent source path despite wider animation use.

### 6. Visual-migration caution

The Tailwind v4 bridge intentionally preserves existing fixed-pixel spacing and
font sizes and has a source-level contract test preventing an accidental
Preflight or utility change. The 4,717-line stylesheet is a liability, but the
incremental migration method is careful and appropriate for a live dashboard.

## Where Aletheia Can Learn

### 1. Separate visual depth from feature-specific class names

Aletheia's tokens are sound, but thousands of CSS lines encode page and feature
geometry in one global cascade. TickFlow demonstrates the benefit of exposing
depth, text hierarchy, radius, and motion as composable primitives at the
component boundary.

This does not justify a global Tailwind rewrite. It supports moving newly
touched, repeated patterns behind typed components and semantic tokens while
leaving unrelated pages unchanged.

### 2. One common dialog contract

ConfirmModal and DetailDrawer separately implement parts of dialog behavior.
Aletheia would benefit from one internal dialog shell that owns focus entry,
trap, restore, Escape, accessible naming, scroll behavior, and backdrop
semantics. Confirm and Drawer can then be variants or consumers.

### 3. A semantic EmptyState family

TickFlow's icon/title/hint composition is more deliberate than many inline
Aletheia empty messages. The Aletheia version should preserve its stronger
semantics by encoding variants such as:

- `empty`;
- `not-configured`;
- `unavailable`;
- `stale`;
- `partial`;
- `permission-limited`.

The purpose is consistent communication, not turning every absence into a
large illustration.

### 4. Make page roles visible

Aletheia's PageHeader documentation says its role descriptor explains where a
page belongs in Observe/Decide/Execute/Evolve/Operate. The current component
renders that string only as the `<h2 title>` tooltip. TickFlow's subtitle is
visibly rendered. Aletheia can retain its breadcrumb and make a concise role or
trust-boundary sentence visible where it materially helps orientation.

### 5. Unify DOM and chart colour authority

Aletheia already centralizes lightweight-chart colours, but it is dark-only and
duplicates CSS token values as literals. TickFlow shows a typed bridge that can
support multiple renderers and theme variants. The useful pattern is a single
semantic chart palette API, not necessarily adding a light theme now.

## Domain Boundary That Must Remain

UI semantics must not reinterpret trading state. In particular:

- TickFlow's A-share convention uses red for bull/up and green for bear/down.
- Aletheia's global crypto-oriented charts use green for up/profit and red for
  down/loss/error.

Copying the reference colour values would reverse operator meaning. Only the
separation of market direction from generic success/error is reusable.

No UI refactor should conceal unavailable backend facts, turn stale data into
an empty state, auto-dismiss a trust-critical error, or weaken execution/risk
confirmation.
