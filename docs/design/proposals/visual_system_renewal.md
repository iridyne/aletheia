---
id: visual-system-renewal
status: draft
github_issue: 689
related_contracts: []
supersedes: []
---

# Visual system renewal

## Summary

Aletheia should renew its visual system around one durable product identity:

> A calm, dense, truthful financial workstation.

The product remains dark, professional, and information-first. The renewal is
not a decorative reskin and does not imitate another product pixel for pixel.
It translates the workflow discipline of mature financial tools into an
Aletheia-native system: explicit hierarchy, stable context, restrained color,
compact productive typography, aligned controls, and observable system state.

The existing interface already has useful foundations: a cold dark palette,
IBM Plex for most working text, IBM Plex Mono for data, and several dense
workstation pages. The change is still substantial because the current visual
rules do not form one enforceable system. Settings is the clearest failure:
navigation, section headings, cards, rows, controls, and state indicators all
compete without a coherent hierarchy.

This Proposal defines the future design. It does not claim that the current
frontend implements it. Execution, ownership, child issues, and closure remain
in GitHub issue #689.

## Current fact

The current authority is `docs/design/frontend/ui_design_system.md`. It defines
a dark terminal language, a cyan accent, IBM Plex Sans/Mono, Space Grotesk
display headings, Settings `Section + Row`, and a left accent border on Settings
section headings.

A rendered audit on 2026-08-06 found that most non-Settings pages remain
serviceable, with Blotter closest to the desired dense evidence-workstation
archetype. Settings is materially less clear.

At a 1370 x 1086 review viewport (1370 x 1129 CSS viewport in the embedded
browser, device pixel ratio 1.52), the current Settings implementation has:

- an application sidebar, a second Settings category sidebar, section-level
  headings, collapsible groups, and local tabs/switches competing as navigation;
- a `Software Updates` section whose `h3` computes to about 14.08px while a
  normal setting label computes to about 13.12px, leaving less than one pixel of
  size separation between two hierarchy levels;
- a 2px cyan left border that is visually stronger than the section title and
  makes headings read like quoted or callout content;
- a section title, sealing rule, and elevated `.card` around the same content,
  repeating the container boundary three times;
- an approximately 799px content panel split into a 296px copy column and a
  483px control column, with controls justified to the start of the second
  column rather than the far edge of the row;
- switches that leave a large unused area to their right;
- a `Software Updates` switch for `update.enabled` with no visible or accessible
  name, so the user cannot determine what the switch controls;
- multiple other switches whose accessibility snapshot exposes an empty
  `LabelText` and unnamed checkbox;
- card, row, form-grid, and ad hoc flex patterns mixed inside the same section;
- responsive rules and custom component dialects that are broader than the
  canonical token and breakpoint set.

These are not isolated spacing defects. They show that visual emphasis does
not consistently encode information architecture or interaction semantics.

## Goals and non-goals

### Goals

1. Establish an unmistakable dark financial-workstation identity.
2. Make hierarchy readable from typography, alignment, spacing, and structure
   before color is applied.
3. Make Settings a coherent configuration workbench and the first reference
   implementation of the renewed system.
4. Use IBM Plex as the productive interface voice: professional, neutral,
   legible, and compact.
5. Reserve IBM Plex Mono for values that benefit from stable comparison:
   prices, quantities, timestamps, IDs, commits, symbols, ports, and code.
6. Make every visual state reflect a real product fact; unknown and unavailable
   must never look healthy or silently become zero.
7. Reduce component dialects and encode the system in shared primitives.
8. Preserve keyboard, low-vision, and screen-reader usability as part of the
   component contract rather than a final polish pass.
9. Adopt the renewal incrementally, with Settings as the proving surface and
   without a whole-frontend rewrite.

### Non-goals

- No light-theme requirement for the MVP.
- No logo redesign or marketing-site visual language.
- No Tailwind, shadcn, Carbon, or other framework rewrite. External systems are
  references, not dependencies.
- No change to trading, risk, execution, routing, persistence, credentials, or
  runtime behavior merely to satisfy a visual design.
- No generic OAuth, Auto Routing, or new AI Provider capability.
- No deletion of routes or features as an incidental visual refactor.
- No decorative glass, gradients, large glow fields, animated backgrounds, or
  dashboard-card mosaic.
- No big-bang CSS replacement.

## Proposed design

### 1. Reference translation

The renewal takes principles, not visual costumes, from four sources.

#### Bloomberg Terminal: task density and action continuity

Bloomberg describes the Terminal as fast access to data, insight, trading tools,
and integrated execution, and describes Launchpad as a customized workspace of
dynamic monitors, alerts, charts, and news. The relevant lesson is that a
financial interface earns density by keeping evidence and action in the same
stable work context.

Aletheia should adopt:

- persistent task context;
- dense but aligned evidence;
- status that can be scanned without opening decorative cards;
- short paths from evidence to the authorized action;
- stable workspaces rather than page-specific visual inventions.

It should not adopt Bloomberg's historical color saturation, keyboard syntax,
or legacy window chrome merely to look “terminal-like.”

Reference: <https://professional.bloomberg.com/products/bloomberg-terminal/>

#### TradingView: focus, continuity, and synchronized context

TradingView emphasizes focused workspaces, restored per-tab context, linked
symbols, synchronized tools, customizable layouts, multi-monitor continuity,
and a consistent dark experience. The relevant lesson is that layout is part of
the user's working state, not decoration.

Aletheia should adopt:

- predictable page and panel placement;
- context-preserving navigation and deep links;
- the same interaction grammar across browser and machine sizes;
- dark surfaces whose contrast remains legible without ornamental borders;
- settings that expose state and consequences without breaking task focus.

References:

- <https://www.tradingview.com/support/solutions/43000671618-what-is-tradingview-desktop/>
- <https://www.tradingview.com/support/solutions/43000673888-tradingview-desktop-releases-and-release-notes/>

#### IBM Plex and Carbon: productive hierarchy

Carbon distinguishes productive type for compact product work from expressive
type for editorial or marketing moments. It uses IBM Plex with calibrated type
tokens to establish information hierarchy. Its form guidance treats headers,
form bodies, actions, labels, helper text, alignment, and states as one system.

Aletheia should use those principles without importing Carbon components.

References:

- <https://carbondesignsystem.com/elements/typography/overview/>
- <https://carbondesignsystem.com/components/form/usage/>
- <https://carbondesignsystem.com/components/toggle/usage/>

#### W3C: perceivable controls and programmatic relationships

Color cannot be the only carrier of meaning. Controls need programmatically
associated labels, meaningful non-text boundaries/state indicators need at
least 3:1 contrast, and focus must remain clearly visible.

References:

- <https://www.w3.org/WAI/tutorials/forms/labels/>
- <https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html>
- <https://www.w3.org/WAI/WCAG22/Understanding/use-of-color>

### 2. Visual thesis

Every product surface must satisfy four adjectives:

- **Calm**: low chroma by default; no decorative urgency.
- **Dense**: compact information with disciplined alignment, not cramped text.
- **Professional**: neutral typography, predictable controls, stable geometry.
- **Truthful**: visual confidence is proportional to observed evidence.

The interface should look credible in grayscale. Color then adds interaction or
semantic meaning; it must not be required to discover the hierarchy.

### 3. Surface model

Use four layers only:

| Layer | Purpose | Typical treatment |
| --- | --- | --- |
| Canvas | Application and page background | Near-black cold neutral; no border or shadow |
| Navigation | Global/local wayfinding | Slightly distinct dark layer; selection by text, indicator, and modest fill |
| Work surface | Sections, rows, tables, editors | Mostly flat; separation by spacing and 1px rules |
| Overlay | Dialog, drawer, popover, critical confirmation | Elevated layer with border and restrained shadow |

An ordinary section is not elevated. A card is permitted only when the object
has an independent identity or lifecycle that the user can act on as a unit,
for example a named Provider Profile, connection, account, strategy, or queued
job. A card must not be used merely to put a background behind a form.

There must not be a card inside a card unless the inner item is a real entity
with a separate action boundary. Shadows are reserved for overlays and
transient floating content; ordinary Settings content does not use them.

### 4. Color system

The dark/cold foundation remains, but color roles become stricter.

- Near-black and blue-gray neutrals carry canvas, navigation, surfaces, borders,
  primary text, and secondary text.
- Signal cyan is the sole interaction accent: selected navigation, active tab
  indicator, focus ring, primary link, and deliberate primary action.
- Cyan is not a section-heading ornament and is not a generic “technology” glow.
- Green means a fresh successful observation or completed success.
- Yellow means degraded, stale, pending, or caution.
- Red means actionable failure, destructive action, blocked state, or danger.
- Neutral/muted means unknown, unavailable without an active failure, disabled,
  not applicable, or historical context.
- No status is communicated by color alone; use text, icon/shape, position, or
  programmatic labeling as the second channel.

Large filled semantic backgrounds are exceptional. Prefer colored text,
indicator, or border on a neutral surface to keep sustained monitoring calm.

### 5. Typography

IBM Plex becomes the productive product voice.

- **IBM Plex Sans**: page titles, category titles, section titles, labels,
  descriptions, actions, tabs, navigation, and long-form interface copy.
- **IBM Plex Mono**: comparable data and machine identity only.
- **Space Grotesk**: optional for the Aletheia wordmark or a future expressive
  brand surface, not for productive Settings/workstation hierarchy.

Target type roles:

| Role | Size / line | Weight | Notes |
| --- | --- | --- | --- |
| Page title | 24 / 32 | 600 | One per destination |
| Category title | 20 / 28 | 600 | One per Settings category/workbench |
| Section title | 16 / 24 | 600 | Clear group boundary; no accent bar |
| Row label | 14 / 20 | 500 | Configuration object or question |
| Body | 14 / 20 | 400 | Primary explanations and ordinary copy |
| Helper | 12 / 18 | 400 | Consequence, format, provenance |
| Metadata | 12 / 18 | 400/500 | Mono only when values benefit from it |

Adjacent hierarchy levels must differ materially through size, weight, spacing,
or placement. A sub-pixel or one-pixel difference is not a hierarchy.

Use sentence case for product copy. Uppercase is limited to compact navigation
groups, small measurement labels, and short metadata where scanning benefits.
Do not use uppercase mono as a general substitute for hierarchy.

### 6. Grid and geometry

All content aligns to a shared page grid. Alignment is a stronger organizing
signal than a border.

- Page gutters use existing spacing tokens and canonical breakpoints.
- Values in the same semantic column align across neighboring rows.
- Control widths are selected from a small set: compact, standard, and full.
- Inputs in the same group share a left edge and size class.
- A setting row consumes the full available width even when its control is
  compact.
- Compact controls, including switches, align to the far-right action edge.
- Do not center arbitrary controls inside empty columns.

### 7. Settings information architecture

Settings is a configuration workbench, not a vertical feed of cards.

#### Wide screens (`xl` and above)

```text
Settings                                           [Search settings]
────────────────────────────────────────────────────────────────────
Category rail                Detail work surface
  Agent & Models             Runtime & System
  Execution                  Process identity, updates, data, network
  Risk & Universe            ────────────────────────────────────────
  Health & Alerts            Section
  Runtime & System             rows / editor / status
  Signals                    ────────────────────────────────────────
  Advanced                   Section
                                rows / editor / status
```

- The application sidebar is the first persistent navigation level.
- The category rail is the second and last persistent navigation level.
- The category rail is flat navigation, not a rounded card.
- Category descriptions may appear on hover/focus or in the detail header; they
  do not need to occupy two lines in every navigation item.
- Search is a page-level tool and retains the current category/section deep link.

#### Below `xl`

- Replace the category rail with a horizontally scrollable category bar.
- At the smallest width, expose the categories through one labeled select/menu.
- Do not retain a second icon-only vertical rail beside the application sidebar.
- Tabs never wrap; they scroll horizontally or become the labeled compact
  selector.

This design keeps at most two persistent navigation levels at every width and
prevents the 1083px content corridor observed in the current implementation.

#### Local choices inside a category

Use one line-tab row for peer views, such as:

```text
API   CLI   Model Routing
```

Do not place `Profiles / Model Routing` above a second `API / CLI` segmented
switch. A segmented control is reserved for a local display/filter mode, not a
third navigation hierarchy.

### 8. Settings content grammar

Each category uses the same grammar:

```text
Category title                                      optional category action
Short scope/consequence statement

Section title                                      optional observed status
Section description
────────────────────────────────────────────────────────────────────
Setting label                                      control / value / action
Persistent helper text                             aligned to action edge
────────────────────────────────────────────────────────────────────
Setting label                                      control / value / action
Persistent helper text
```

Rules:

1. Remove the cyan left border from `.settings-section-head`.
2. Use a 16px semibold IBM Plex Sans section title and neutral description.
3. Use one divider either below the section header or between rows; do not add a
   card boundary around the same ordinary content.
4. Every row owns the complete width of the detail surface.
5. On desktop, copy stays left and the compact control/action aligns to the far
   right. Standard/full-width inputs align to a stable control column.
6. On narrow screens, rows stack copy above controls; controls become full width
   unless intrinsically compact.
7. Helper text describes consequence, provenance, format, or recovery. It does
   not restate the label.
8. Section-level actions live in a stable header/action edge or footer; they do
   not float in an arbitrary flex line.
9. A dirty section exposes one clear Save/Discard boundary. Do not scatter
   “Save config” buttons through visually unrelated cards.

### 9. Software Updates reference composition

The current composition is specifically rejected. The target is:

```text
Software updates                   Up to date · master · 504b215f  [Check now]
On-demand update inspection. Updates never run automatically.
────────────────────────────────────────────────────────────────────────────
<exact label for update.enabled>                                  [ switch ]
<helper that states its real persistence and runtime consequence>
────────────────────────────────────────────────────────────────────────────
Branch                                                      [ master       ]
Code branch checked when the owner requests an update.

                                                      [Discard] [Save changes]
```

Before implementation, the backend meaning of `update.enabled` must be named.
The current unlabeled switch is not acceptable, and the UI must not invent a
meaning from the field name.

The status, branch/commit, refresh action, staged configuration, and save result
remain distinct facts. They can share a section because they describe one
update facility, but they must not be collapsed into an undifferentiated card.

### 10. Control semantics

#### Switch

A switch is used only for a binary, reversible setting that applies immediately.
The row label names the object; the switch sits at the far-right action edge.
Visible `Enabled`/`Disabled` prose is not added when it only repeats the switch,
but an accessible name is mandatory.

If a value is staged until the user presses Save, either:

- use a checkbox/select within a form and a clear dirty-state boundary; or
- deliberately change the product behavior to immediate persistence in a
  separately reviewed backend contract.

The visual system must not imply immediate application when persistence is
deferred.

#### Checkbox

Use for selection, inclusion, acknowledgement, or a staged boolean inside a
form. The label sits next to the checkbox and both form one click target.

#### Select and Combobox

Use Select for a bounded known set. Use a dropdown-first editable Combobox when
observed options exist but an exact custom value remains valid. Keep a visible
label and expose loading, cached/stale, unavailable, and refresh states.

#### Read-only and disabled

Read-only means the value remains important and copyable but cannot be edited.
It keeps readable contrast and displays a lock/read-only cue. Disabled means the
action is unavailable because of a dependency or permission and explains why.
Neither may look identical to an editable field.

#### Button hierarchy

- one primary action per local decision boundary;
- secondary for safe alternatives and refresh/re-check;
- ghost only for low-priority inline actions;
- destructive styling only when the action is actually destructive;
- button placement remains stable between loading/success/error states.

### 11. State and feedback semantics

The visual system treats the following as separate facts:

- configured;
- enabled;
- reachable;
- verified;
- routed/selected;
- stale;
- unavailable;
- unknown.

No component derives one from another unless a backend contract explicitly
defines that derivation. Green requires a fresh successful observation. Unknown
and unavailable never render as healthy or as a numeric zero.

Save, validation, test, and asynchronous status messages are announced
programmatically. Trust-sensitive failures remain inline and persistent.
Auxiliary success can use a short status message, but not a disappearing signal
that leaves the persisted state ambiguous.

### 12. Accessibility contract

- Every form control has a visible label or a programmatic accessible name.
- An empty `LabelText` or unnamed checkbox/switch is a release-blocking defect.
- Heading levels match the information architecture; visual size does not
  contradict semantic rank.
- Text meets WCAG AA contrast; controls and meaningful state/focus indicators
  meet 3:1 non-text contrast.
- Focus is visible with at least a clear 2px-perimeter-equivalent indicator.
- Color is never the only carrier of status or selection.
- Target size and click area include the associated label where appropriate.
- Keyboard order follows the visual reading order.
- Responsive reflow does not hide labels, consequences, or actions.

### 13. Component adoption rules

The reference Settings implementation should establish or replace these shared
primitives before broad migration:

- `SettingsLayout` / responsive category navigation;
- `SettingsCategoryHeader`;
- flat `SettingsSection`;
- full-width `SettingsRow` with named control alignment modes;
- labeled `Switch` with mandatory accessible name;
- `Field` states for editable/read-only/disabled/loading/error;
- dropdown-first editable `Combobox`;
- `ObservedStatus` with freshness and accessible labeling;
- shared loading/empty/unavailable/error state;
- canonical Tabs and local segmented control with distinct semantics;
- stable section action/footer and dirty-state treatment.

New one-off classes for these concepts are prohibited after the reference
primitives are accepted. Existing dialects migrate page by page and are removed
only after their last consumer passes validation.

## Trust and complexity review

This Proposal spans every frontend page and therefore meets the cross-domain
threshold even though it does not alter the trading trust boundary.

The main risks are:

- a visual rewrite accidentally changing configuration persistence or action
  semantics;
- a new component system existing beside the old one and increasing drift;
- status components inventing confidence where backend evidence is absent;
- responsive redesign hiding a safety control or consequence;
- visual migration producing one large, unreviewable PR.

Controls:

- visual PRs do not change backend behavior unless a separate issue and contract
  explicitly require it;
- Settings is migrated by section behind shared primitives, not by global CSS
  replacement;
- semantic and accessible-name tests accompany primitive changes;
- old and new primitives do not remain open-ended alternatives;
- every page migration preserves its current behavioral tests and adds focused
  interaction coverage where the visual structure changes;
- #689 remains the umbrella; focused defects and migrations use child issues.

A smaller “fix the two CSS rules” change is insufficient because the accent
heading, card misuse, control alignment, unnamed controls, navigation depth,
type hierarchy, and state semantics all originate from the same missing visual
contract. A new framework is also unnecessary; the existing React/CSS stack can
express the proposed system.

## Verification contract

Before the design can move from Proposal to current UI authority:

1. The Settings reference implementation demonstrates all target primitives.
2. The `Software Updates`, `Learning`, and AI Provider sections demonstrate
   ordinary rows, staged forms, entity lists, status, validation, read-only
   state, disabled state, and asynchronous verification.
3. Rendered browser review covers at least:
   - 1370 x 1086;
   - 1083 x 890;
   - 1024-class width;
   - 768-class width;
   - 390-class mobile width.
4. At every width, there are no more than two persistent navigation levels.
5. Section title and setting label hierarchy remains visible in grayscale.
6. Switches align to the far-right action edge on desktop and remain associated
   with their labels on narrow screens.
7. Browser accessibility snapshots expose meaningful names for every form
   control; no empty `LabelText` remains in migrated sections.
8. Keyboard navigation, focus visibility, reflow, overflow, zoom, and reduced
   motion are checked.
9. Status tests cover loading, fresh success, stale, unknown, unavailable,
   validation failure, save failure, and genuine empty states.
10. `cd frontend && npm run build` passes for implementation PRs.
11. `uv run python scripts/check_docs.py` passes whenever the durable design
    authority or Proposal index changes.
12. Before/after screenshots and computed-style checks demonstrate actual
    hierarchy and token use; class names alone are not evidence.

## Rollout and rollback

### Phase 0 — contract and primitive freeze

- Accept or revise this Proposal.
- Stop adding new Settings/card/tab/status dialects.
- Define tokens, productive type roles, state semantics, and responsive category
  navigation in shared primitives.

### Phase 1 — Settings reference workbench

- Migrate the Settings shell and category navigation.
- Migrate Software Updates as the simple row/form reference.
- Migrate AI Provider Settings as the entity/status/Combobox reference.
- Repair accessible names and control alignment in every touched section.

### Phase 2 — Settings completion

- Migrate remaining categories section by section.
- Remove card wrappers that do not represent independent entities.
- Consolidate save/dirty/error behavior and delete replaced primitives.

### Phase 3 — application convergence

- Apply renewed typography, surface, state, and data-state primitives to
  Overview, Learning, Command Center, and then remaining pages.
- Use Blotter as a density reference while removing any page-specific visual
  behavior that conflicts with the accepted system.

Each phase is independently reviewable and reversible through its own PRs.
Rollback restores the prior component for the affected section/page; it does
not require reverting unrelated migrated surfaces. No database or backend
rollback is introduced by this visual Proposal.

## Resolution

Pending owner review.

If accepted and implemented, move the normative visual language, typography,
surface, Settings, component, accessibility, and verification rules into
`docs/design/frontend/ui_design_system.md`. Preserve this Proposal as the
decision record and mark it `implemented`. GitHub issue #689 remains the sole
execution and closure tracker.
