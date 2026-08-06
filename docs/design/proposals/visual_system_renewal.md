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

#### NoFluff: deletion and interrogation as design tests

NoFluff states that writing and interface design share the same obligation:
every included part needs a reason to exist. It proposes two tests. Removing a
part must cause a meaningful loss, and asking what a part concretely means must
produce a factual or logical answer.

Its rendered page reinforces the argument without turning it into a component
catalog. At the review viewport it uses one 720px content column, a clear
32px/19.2px/16px heading-to-body hierarchy, a repeated 40px section rhythm, no
cards around ordinary sections, no section shadows, and color emphasis on the
primary action rather than on every boundary. The page is a single-column
writing guide, so its density and light palette are not Aletheia patterns. Its
editing discipline is directly applicable.

Aletheia adopts these tests:

- **Deletion test**: if removing an element loses no fact, action, relationship,
  or necessary hierarchy, remove it.
- **Interrogation test**: every color, label, icon, badge, border, surface,
  helper, and component must be justified by a product fact or interaction
  rule that can be stated directly.
- **Duplication test**: if the surrounding navigation or control already
  communicates the same fact, retain one signal instead of decorating both.
- **Removal before repair**: do not add explanation, state handling, or styling
  to make an unnecessary control or container look intentional.

Reference: <https://nofluff.0x01.me/>

#### W3C: perceivable controls and programmatic relationships

Color cannot be the only carrier of meaning. Controls need programmatically
associated labels, meaningful non-text boundaries/state indicators need at
least 3:1 contrast, and focus must remain clearly visible.

References:

- <https://www.w3.org/WAI/tutorials/forms/labels/>
- <https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html>
- <https://www.w3.org/WAI/WCAG22/Understanding/use-of-color>

### 2. Visual thesis

Design decisions pass through three filters in order:

| Filter | Question | Result |
| --- | --- | --- |
| First principles | Which fact must be understood or which decision must be made? | Derive the required information and action boundary |
| Occam's razor | What is the smallest consistent grammar that expresses it safely? | Prefer existing type, grid, row, and state primitives |
| NoFluff tests | Which remaining parts fail deletion, interrogation, or duplication? | Remove them before visual polish |

Every product surface must satisfy four adjectives:

- **Calm**: low chroma by default; no decorative urgency.
- **Dense**: compact information with disciplined alignment, not cramped text.
- **Professional**: neutral typography, predictable controls, stable geometry.
- **Truthful**: visual confidence is proportional to observed evidence.

The interface should look credible in grayscale. Color then adds interaction or
semantic meaning; it must not be required to discover the hierarchy.

#### Visual constitution

The following rules decide whether a visual treatment belongs in the product:

1. **Hierarchy before decoration.** Type, alignment, order, and spacing must
   explain the page before accent color, border, fill, or shadow is added.
2. **One fact, one primary signal.** Do not repeat the same state in a tab,
   badge, helper, icon, and control label.
3. **Evidence before confidence.** Healthy semantic color requires a current
   observation; configured, enabled, reachable, verified, and routed remain
   separate facts.
4. **Alignment before containment.** Prefer a shared grid and whitespace over a
   card or border whose only purpose is to hold nearby content together.
5. **Delete before styling.** A component that fails the deletion test is
   removed, not made more polished.
6. **Action beside consequence.** Controls live with the object and explanation
   they affect, and their persistence timing is explicit.
7. **Density follows relevance.** Frequently compared evidence stays visible;
   rare detail moves to progressive disclosure without hiding safety facts.

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

#### Application page archetypes

Every route chooses one dominant content grammar. The archetype names are
internal design vocabulary, not labels shown to users.

| Archetype | Typical routes | Dominant unit | Required behavior |
| --- | --- | --- | --- |
| Monitoring | Overview, Health | Current fact, alert, authorized action | Stable placement, freshness, severity, and drill-down |
| Ledger | Blotter, trades, positions | Comparable row and value column | Dense table, fixed semantics, filters, and contextual detail |
| Investigation | Learning, Command Center, Agent Activity | Evidence, event, result, relationship | Master-detail or aligned evidence views with preserved context |
| Configuration | Settings | Category, section, setting, validation result | Explicit labels, consequences, persistence, and recovery |

A page may embed a secondary unit, such as a small ledger table inside
Monitoring, but it keeps one dominant grammar. It must not become a gallery of
cards merely because its data types differ.

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

#### Visual complexity budget

The budget prevents a locally reasonable component from creating global noise:

- at most two persistent navigation levels at any viewport;
- one page title and one active category title for the current destination;
- one primary action per local decision boundary;
- one dominant grouping device per ordinary boundary: spacing, a divider, or a
  justified entity container, not all three;
- one primary status representation per observed fact;
- no elevation for ordinary sections and rows;
- a badge only for an attribute the current location does not already imply;
- an icon only when it identifies an action, type, or state faster than text,
  with an accessible name where its meaning is not visible;
- helper text only when it adds consequence, provenance, required format,
  freshness, or recovery information.

Exceeding a budget item requires a concrete information or interaction reason
in review. Visual preference alone is not sufficient.

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

<!-- CLAUDE-CODE-REVIEW:BEGIN r1 search-placement-conflict -->
> **Review addendum (Claude Code, 2026-08-06) — the wide-screen sketch above
> conflicts with a standing rule that was already fixed once.** The sketch places
> `[Search settings]` on the `Settings` title line, i.e. outside the two-column
> shell. `docs/design/frontend/ui_design_system.md` §3 states the opposite as an
> iron rule ("容器嵌套铁律"): an interactive component must live inside the grid
> column it aligns to, and must not float outside `.settings-shell` simulating
> alignment with `max-width`/`margin`. That document carries a WRONG/CORRECT code
> pair for exactly this component, and change-history entry `2026-06-29 bcdcebd`
> records moving the search box from outside the shell into the panel. An
> implementer following the sketch literally would reintroduce the bug.
>
> This Proposal must state which of the two wins. Two admissible resolutions:
>
> 1. **Keep search inside the detail surface** (honours the existing rule with no
>    new mechanism): search is the first child of the detail column, its left edge
>    aligning naturally with section content. The sketch should be redrawn.
> 2. **Promote the page header into the shared grid** (permits the sketch, but
>    only with a real mechanism): the header participates in the *same* grid
>    template as the rail and detail surface, so `[Search settings]` sits in the
>    detail column's track rather than being positioned by `max-width`/`margin`.
>    This is a genuine change to the standing rule and must be recorded as such in
>    the deprecation table (see the §Resolution addendum), not left implicit in an
>    ASCII sketch.
>
> Option 1 is recommended: it costs nothing, and the existing rule exists because
> this precise layout was already shipped, found broken, and repaired.
<!-- CLAUDE-CODE-REVIEW:END r1 -->

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

<!-- CLAUDE-CODE-REVIEW:BEGIN r3 update-enabled-resolved -->
> **Review addendum (Claude Code, 2026-08-06) — this is no longer a blocking
> prerequisite.** The backend meaning was read directly from the source:
> `aletheia/update/applier.py` gates both `check_for_update()` and
> `apply_update()` on `cfg.update.enabled`, returning
> `{"ok": false, "reason": "update_disabled", "mode": "development"}` when it is
> off. It is therefore one shared gate over inspection *and* application, not an
> auto-update scheduler and not an application-only switch.
>
> Derived copy, to be confirmed by the owner rather than re-researched:
>
> - label: `Allow update checks and applies`
> - helper: `When off, both update checks and applies are refused. The running
>   version is unaffected.`
>
> Two independent facts remain to settle during implementation, and neither
> blocks the visual work:
>
> 1. this field's persistence is currently staged behind a separate
>    `Save config` button (see the §10 addendum), which the §10 Switch rule
>    forbids;
> 2. packaged builds take a different path (`_is_packaged()` short-circuits
>    before the gate), so the helper must not promise development-mode
>    semantics on a packaged install.
<!-- CLAUDE-CODE-REVIEW:END r3 -->

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

<!-- CLAUDE-CODE-REVIEW:BEGIN r4 switch-rule-blast-radius -->
> **Review addendum (Claude Code, 2026-08-06) — measured blast radius.** This
> rule is correct but is currently written as if the staged switch were a local
> `Software Updates` defect. It is not. Measured across every `<Switch>`/`<Toggle>`
> JSX site in `frontend/src` at review time, classifying each by whether its
> change handler calls a mutation (immediate) or only local `setState` (staged):
>
> | File | Total | Immediate | Staged |
> | --- | --- | --- | --- |
> | `features/settings/tabs/AgentModelsTab.tsx` | 19 | 19 | 0 |
> | `features/settings/execution/PaperTradingSettings.tsx` | 10 | 0 | 10 |
> | `features/settings/tabs/SignalsTab.tsx` | 10 | 1 | 9 |
> | `features/settings/tabs/RuntimeSystemTab.tsx` | 8 | 6 | 2 |
> | `features/settings/tabs/HealthNotificationsTab.tsx` | 4 | 0 | 4 |
> | `features/settings/telegram/TelegramSection.tsx` | 4 | 0 | 4 |
> | `features/settings/model-providers/ModelProvidersSection.tsx` | 3 | 2 | 1 |
> | `features/settings/tabs/ExecutionTab.tsx` | 3 | 0 | 3 |
> | `features/settings/tabs/RiskTab.tsx` | 2 | 0 | 2 |
> | `components/ui/SettingsSection.tsx` (`SwitchGrid` body) | 1 | 0 | 1 |
> | `pages/SerenityLens.tsx` | 1 | 0 | 1 |
> | **Total** | **65** | **28** | **37** |
>
> So **57% of all existing switches violate this rule**, and whole surfaces are
> uniformly staged: `PaperTradingSettings`, `HealthNotificationsTab`,
> `TelegramSection`, `ExecutionTab`, and `RiskTab` have zero immediate switches.
>
> Two amplifiers the raw count hides:
>
> - `SwitchGrid` (`components/ui/SettingsSection.tsx`) is one JSX site that renders
>   a switch **per field descriptor**, and its `set` callback is local state in
>   every consumer. Its five call sites (`RiskTab` ×3 via `boolFields`,
>   `POLICY_BOOLS`, `RISK_BOOLS`; `RuntimeSystemTab` ×1; `HealthNotificationsTab`
>   ×1) render **24 further staged switches** at runtime. The true staged
>   population is therefore ~60, not 37.
> - `SignalsTab.tsx:387` toggles a feed inside a local array (`toggleFeed` →
>   `setFeeds`), so a list of switches is staged as a batch — reverting one requires
>   knowing which of N rows was touched.
>
> Consequences for the plan:
>
> - Do not treat "switch implies immediate" as a cosmetic fix. Per staged switch
>   there are three honest resolutions — convert the control to a checkbox in a
>   form, make persistence immediate, or keep the switch inside an explicit
>   dirty/Save boundary — and the choice is per-setting, not global.
> - Risk-affecting settings should not be converted to immediate persistence as a
>   side effect of a visual PR. For `RiskTab` and `PaperTradingSettings` the
>   default resolution is checkbox-in-form, not immediate.
> - `SwitchGrid` is the highest-leverage single decision in this section: choosing
>   checkbox-in-form there resolves ~24 controls in one primitive change, exactly
>   as the accessible-name fix does in Phase 0.5. Decide `SwitchGrid` before
>   migrating any individual staged surface.
> - Otherwise the migration order follows this table: `AgentModelsTab` is already
>   compliant and can serve as the immediate-persistence reference, while
>   `ExecutionTab` (3) and `TelegramSection` (4) are the cheapest staged surfaces
>   to convert first.
<!-- CLAUDE-CODE-REVIEW:END r4 -->

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

#### Labels, terminology, and supporting copy

- Use the shortest common term that preserves the domain meaning. A new
  metaphor or branded term must improve understanding and expand into a precise
  definition when questioned.
- Name objects with nouns and actions with verbs. Describe effects and recovery
  directly instead of using promotional or emotional language.
- Do not repeat context already established by navigation. An API Profile card
  inside the API tab does not need an `API` badge merely to restate its tab.
- Do not repeat a binary control with adjacent `Enabled` text when the row label,
  control position, and accessible name already establish its meaning.
- Do not display `Available` as decoration. A compact indicator may summarize a
  real probe result, but its accessible label or detail must expose the observed
  state, freshness, and failure when relevant.
- Do not show target counts, badges, or metadata in a collapsed summary unless
  they affect identification, comparison, urgency, or the next decision.
- `Profile`, `AI Target`, and `Model Routing` keep one definition across UI,
  API, tests, and documentation. Copy does not introduce synonyms merely for
  variety.

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

<!-- CLAUDE-CODE-REVIEW:BEGIN r5 eight-state-backend-audit -->
> **Review addendum (Claude Code, 2026-08-06) — four of these eight facts do not
> exist in the backend today.** The rule "no component derives one fact from
> another" is correct, but a visual system cannot render a fact the API never
> sends. Without this column the rule is unimplementable and will be silently
> violated by whoever migrates the surface. Measured against the model-provider
> surface, which is the proposal's own reference for status:
>
> | Fact | Backend provides today | Evidence |
> | --- | --- | --- |
> | configured | Yes | `GET /api/model` → `providers[n].api_key.configured`, `.oauth.configured` (`aletheia/server/routes/model.py`, `_key_status`) |
> | enabled | Yes | `GET /api/model` → `providers[n].enabled` (config-backed) |
> | reachable | Conflated | `GET /api/model/health` → `available: bool` only |
> | verified | Conflated | same single `available` boolean; the per-action `POST` probe (`testModelCandidate` → `test.available`) is never persisted |
> | routed/selected | Yes | `GET /api/model` → `default_provider` / `deep_provider` / `fast_provider` |
> | stale | **No** | `ModelHealth` (`aletheia/model_provider/base.py`) has no observation timestamp; `/api/model/health` is served from a 30 s TTL cache (`_HEALTH_TTL_SEC = 30.0`) whose age is never returned |
> | unavailable | Conflated | `available: false` + `error` string; indistinguishable from "not configured" and from "excluded from the probe set" |
> | unknown | **Absence only** | `_refresh_health_all()` skips providers that are disabled, or that have no credential *and* a default URL — such a provider is simply missing from the array |
>
> Consequences that the proposal should state rather than discover in Phase 1:
>
> 1. **`ObservedStatus with freshness` (§13) cannot be built yet.** There is no
>    `observed_at` on the endpoint Settings and Overview read, and no
>    `ObservedStatus` component exists in `frontend/src` at all. The freshness
>    facts *do* exist in-process — `GET /api/economics/model-health` returns
>    `last_success_at`, `last_failure_at`, `consecutive_failures`,
>    `cooling_down`, `cooldown_remaining_sec` from
>    `aletheia/model_provider/fallback.py::get_model_health_snapshot()` — but on a
>    different route family, consumed only by the Usage page. The cheapest honest
>    fix is to add the observation timestamp to `/api/model/health`, not to make
>    the component guess.
> 2. **"unknown" is currently rendered correctly by accident.** In
>    `frontend/src/pages/Overview.tsx` the Model row reads
>    `dph?.available ? 'green' : dph ? 'red' : 'yellow'` — the yellow arm fires
>    because `find()` returned `undefined`. That satisfies §11's letter today, but
>    it is a coincidence of a ternary, not a contract; any refactor that defaults
>    a missing entry to `{available: false}` turns unknown into red.
> 3. **Green is not yet defensible.** §11 requires "green requires a fresh
>    successful observation". With no timestamp on the response, a green light can
>    represent an observation up to 30 s old — and after a restart, an
>    unobserved-but-cached state. Either the endpoint gains freshness, or §11 must
>    weaken this sentence to what the API can support.
>
> Recommended disposition, consistent with #689's own rule ("show unknown and open
> a backend contract issue" rather than inventing a value): keep all eight facts as
> the target vocabulary, add this column to the section, and open one backend
> contract issue per missing fact — freshness/`observed_at`, and the
> reachable-vs-verified split. Do not block the visual work on them; render
> `unknown` until they land.
<!-- CLAUDE-CODE-REVIEW:END r5 -->

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

### 14. Design review method

Review follows these gates in order. A failed early gate stops cosmetic work on
the affected element until its purpose or structure is corrected.

| Gate | Review question | Required response when it fails |
| --- | --- | --- |
| Deletion | What fact, action, relationship, or hierarchy is lost if this is removed? | Remove or combine it |
| Interrogation | Which product fact or interaction rule justifies it? | State the rule, rename it, or remove it |
| Duplication | Is the same meaning already communicated nearby? | Keep one primary signal |
| Hierarchy | Is reading order clear in grayscale and without container decoration? | Fix type, order, alignment, or spacing |
| Truth | What backend or persisted fact supports the displayed confidence? | Show unknown/unavailable or expose freshness |
| Action | Does the user know the effect, timing, persistence, and recovery? | Rewrite or reposition the control boundary |
| Responsive | Does reflow preserve labels, consequences, state, and action order? | Restructure instead of hiding meaning |

The review artifact for a migrated surface records the purpose of every new
primitive and the removal of redundant dialects. Before/after screenshots show
the result, while semantic snapshots, computed styles, and focused interaction
tests prove the contract.

<!-- CLAUDE-CODE-REVIEW:BEGIN r6 gates-vs-existing-redundancy-table -->
> **Review addendum (Claude Code, 2026-08-06) — these gates duplicate an existing
> table, and the gates themselves must pass the Duplication gate.** The current
> authority `docs/design/frontend/ui_design_system.md` §5「信息冗余（信息密度）」
> already carries a six-row redundancy table with a decision criterion per row
> (双重编码 / 方向双重编码 / 标签回声 / 控件 + 状态复述 / 区域内重复 / Context 回
> 声), a list of legitimate dual-channel exceptions (data-table `StatusBadge`,
> dashboard dot + value, serious-action confirmation), and a three-question audit
> checklist. Shipping the seven gates beside it without merging produces two
> overlapping review procedures — the exact failure the Duplication gate exists to
> catch.
>
> Two things this addendum asks for:
>
> **1. Merge, don't append.** The gates are the better frame (ordered, with a
> required response on failure); the existing table is the better content (each row
> names a concrete pattern and its decision rule). One decidable checklist should
> carry both: gate order and required response from here, pattern rows and the
> exceptions list from §5. Critically, the existing table's **exceptions** must
> survive the merge — without them the Duplication gate reads as "never
> double-encode", which would delete legitimate dual channels such as per-cell
> `StatusBadge` in data tables (each cell must be independently readable) and
> deliberate redundancy in destructive-action confirmations.
>
> **2. Anchor each gate to a live counter-example.** A gate whose failure mode
> cannot be pointed at will not be applied. Verified in the tree at review time:
>
> | Gate | Live counter-example | Where |
> | --- | --- | --- |
> | Deletion | `<label className="model-editor-enabled">` wraps a `Switch` that already renders its own `<label>` — the outer element adds no fact and produces non-conforming nested `<label>` | `ModelProvidersSection.tsx:360` |
> | Interrogation | `label=""` passed to `Toggle` in six places: reads at the call site as "name handled", renders nothing | `SignalsTab.tsx:139, 142, 218, 221, 230, 233` |
> | Duplication | `{ key: 'enabled', label: 'Enabled' }` — a switch inside a section whose own title already names the feature, so the row label restates the control | `RiskTab.tsx:72`, `RuntimeSystemTab.tsx:73` |
> | Hierarchy | section `h3` at `0.88rem` vs row label at `0.82rem` — under one pixel of separation between two hierarchy levels | `index.css` `.settings-section-head h3` |
> | Truth | `not available` / `up to date` / `N behind` badges are derived from `data?.ok` and `has_update` with no observation timestamp, so a stale success still renders green | `RuntimeSystemTab.tsx:343-346` |
> | Action | `update.enabled` toggle flips local state, while persistence waits on a separate `Save config` button 9 lines later | `RuntimeSystemTab.tsx:370` and `:379` |
> | Responsive | the §7 wide-screen sketch places `[Search settings]` outside `.settings-shell`, which the container-nesting rule forbids (see the §7 addendum) | `ui_design_system.md` §3 |
>
> **3. One gate conflict must be resolved before the gates are adopted.** The
> existing §5 table rules that `▲▼` is a redundant channel and must be deleted
> (`去掉三角，保留 ± 号`, recorded 2026-07-19, `dd6425ad`). Live code disagrees in
> its own docstring: `frontend/src/lib/format.ts:219`'s `fmtDelta` prepends `▲`/`▼`
> and justifies it as *"WCAG 1.4.1 — not color-only"* (added 2026-06-21,
> `3dc168d3`). Both positions cite accessibility and reach opposite conclusions;
> the renewal must pick one and record it in the deprecation table, because
> whichever way it goes, code or doc is currently wrong.
>
> The blast radius is small but not confined to one function, and the arrow
> glyph carries three unrelated meanings that must not be swept together:
>
> | Use | Sites | In scope for the §5 rule? |
> | --- | --- | --- |
> | `fmtDelta` — direction of a signed number | `CommandCenter.tsx:467`, `Charts.tsx:65` | **Yes** — this is the redundant channel the rule names |
> | `MarketTicker.tsx:129` — same pattern, inlined instead of calling `fmtDelta` | 1 | **Yes**, and it shows the rule cannot be enforced in `format.ts` alone |
> | Sort direction (`DataTable.tsx:138`), disclosure state (`Overview.tsx:446,492`, `ActivityTimeline.tsx:112`), chart legend markers (`PriceChart.tsx:224-227`) | 8 | **No** — the arrow *is* the sole carrier of state here, not a second channel |
>
> So the decision costs three edits, not twelve. The value of recording it is
> not the edit count: it is that the next implementer to open `format.ts` reads a
> WCAG citation defending exactly what the design system forbids, and has no way
> to tell which is current.
<!-- CLAUDE-CODE-REVIEW:END r6 -->

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

<!-- CLAUDE-CODE-REVIEW:BEGIN r7 verification-split -->
> **Review addendum (Claude Code, 2026-08-06) — split this list by what the
> current stack can actually run, or the rules will drift back.** Items 3-9 are
> written as browser work. The frontend test stack cannot perform any of it:
> `frontend/package.json` devDependencies contain **no Playwright, no axe-core, and
> no `@testing-library/*`**, and `vitest.config.ts` sets `environment: 'node'` with
> no DOM. The 25 existing test files work by `renderToStaticMarkup` plus string
> assertions on the returned HTML, with `import CSS from "../../index.css?raw"` for
> stylesheet facts.
>
> That matters because of *when* each kind of check runs. A manual browser pass
> proves the design once, at review time; a test proves it on every future PR. If
> every item here is manual, the rules regress the first time someone adds a
> `<Toggle>` without a label — which is precisely how the 34 unnamed switches
> accumulated under an authority doc that already required accessible names
> (`ui_design_system.md` §6「无障碍」). Splitting the list is not bureaucracy; it
> decides which of these rules survive contact with the next contributor.
>
> **Automatable today, on the existing stack — should be tests, not review notes:**
>
> | Check | Mechanism already in the repo |
> | --- | --- |
> | No `<Switch>`/`<Toggle>` renders an empty accessible name (item 7) | `renderToStaticMarkup` + assert the rendered `<input type="checkbox">` carries `aria-labelledby`/`aria-label`/a non-empty associated `<label>`; `SettingsPrimitives.test.tsx` already asserts exactly this shape for `Field` |
> | No call site passes `label=""` or omits `label` outside a labeled Row | source sweep via `import.meta.glob('../features/settings/**/*.{ts,tsx}', {query:'?raw'})` — the idiom `lib/cssTokens.test.ts` already uses |
> | At most one `h1` per page; heading levels do not skip (item 5's semantic half) | string assertions on rendered markup |
> | At most two persistent navigation levels (item 4) | assert the rendered Settings shell's nav structure |
> | Only canonical breakpoints appear (`sm 640 / md 768 / lg 1024 / xl 1280`) | regex over `index.css?raw` for `@media` widths, rejecting legacy 700/900/1100 |
> | Section `h3` and row label font sizes differ by a stated minimum | `index.css?raw` + the declared-token check in `cssTokens.test.ts` |
> | Every `var(--x)` used by new rules is declared | `cssTokens.test.ts`, already enforcing this |
>
> **Genuinely manual (or requires new devDependencies) — keep as review gates:**
> rendered-viewport review at the five widths (item 3), grayscale hierarchy
> judgement (item 5's visual half), far-right switch alignment (item 6), keyboard
> order/focus visibility/reflow/zoom/reduced-motion (item 8), computed-style and
> before/after screenshots (item 12), and true contrast-ratio measurement.
>
> Recommendation: state this split in the section, and adopt the automatable rows
> during Phase 0.5 (its item 6) so the very first PR of the renewal is also the one
> that makes the accessible-name rule self-enforcing. Adding Playwright/axe-core is
> a legitimate follow-up but should be its own issue with its own justification —
> not an unstated prerequisite hidden inside a visual-design proposal.
<!-- CLAUDE-CODE-REVIEW:END r7 -->

## Rollout and rollback

### Phase 0 — contract and primitive freeze

- Accept or revise this Proposal.
- Stop adding new Settings/card/tab/status dialects.
- Define tokens, productive type roles, state semantics, and responsive category
  navigation in shared primitives.

<!-- CLAUDE-CODE-REVIEW:BEGIN r2 phase-0.5 -->
### Phase 0.5 — accessible-name contract and hierarchy repair (review addendum)

> **Review addendum (Claude Code, 2026-08-06) — inserted because the current
> ordering defers the highest-leverage, lowest-risk work.** As written, Phase 1
> starts with the responsive navigation rewrite (the riskiest visual change) and
> treats accessible names as per-section cleanup ("in every touched section").
> The unnamed-switch defect is not per-section: it is one contract gap in two
> shared primitives, and one PR closes all of it.
>
> Root cause, read from the tree at review time:
>
> - `frontend/src/components/ui/SettingsSection.tsx` — `SettingsRow` renders its
>   label as `<div className="settings-row-label">`, with no `<label for>`, no
>   `id`, and no `aria-labelledby` handed to the control it wraps.
> - `frontend/src/components/ui/Switch.tsx` — the wrapping `<label>` contains only
>   an `aria-hidden` track plus an **optional** `label` prop. Settings call sites
>   generally omit that prop, because `SettingsRow` already displays the text.
>
> Neither primitive is individually wrong; the *pair* produces an empty
> accessible name. So the audit's "`update.enabled` switch has no accessible name"
> is one visible instance of a systemic gap, not a local bug. Measured across
> `frontend/src` at review time (`Toggle` is an import alias of `Switch`):
>
> | Population | Count | a11y status |
> | --- | --- | --- |
> | `<Switch>`/`<Toggle>` JSX sites | 65 | — |
> | …with a non-empty `label` | 25 | named |
> | …with `label=""` (falsy, so `{label && …}` renders nothing), inside `<Row label=…>` | 6 | **empty name** |
> | …omitting `label`, inside `<Row label=…>` | 28 | **empty name** |
> | …omitting `label`, not inside a Row | 6 | empty name, needs individual handling (below) |
> | **Subtotal fixed wholesale by this phase** | **34** | **the payload** |
> | `<Row>`/`<SettingsRow>` sites | 82 | the label channel being repaired |
> | Extra switches rendered at runtime via `SwitchGrid` field arrays | 24 | named (`SwitchGrid` forwards `label`) |
>
> The 34-site figure is the payload: one contract change in two files names 34
> currently-unnamed switches without touching a single call site. `SwitchGrid`
> (`SettingsSection.tsx`) is already compliant and is the proof that the intended
> convention was `label`-forwarding all along; the 34 are the sites that opted into
> `Row` for visual layout and thereby lost the name.
>
> The `label=""` cluster (`SignalsTab.tsx:139, 142, 218, 221, 230, 233`) deserves
> separate note: passing an explicit empty string reads at the call site as "the
> name is handled" while producing exactly the same empty accessible name as
> omitting the prop. `Switch` should reject or warn on `label=""` rather than
> silently accept it, otherwise the sweep test in item 6 is the only thing standing
> between this idiom and its return.
>
> The remaining 6 need explicit decisions, and two of them are worse than merely
> unnamed:
>
> - `RuntimeSystemTab.tsx:370` — the audit's own `update.enabled` switch, sitting
>   bare in a `<div className="flex items-center gap-2 mb-3">` with no label
>   element at all. Give it the §9 label text.
> - `RiskTab.tsx:228` — `<label className="settings-field">` wraps `<span>Snapshot
>   TTL</span>` plus **both** a `Toggle` and a number input. The label's first
>   labelable descendant is the switch's checkbox, so "Snapshot TTL" is captured by
>   the switch and the number input is left unnamed. This is mis-association, not
>   absence; splitting the two controls is required.
> - `ModelProvidersSection.tsx:361` — `<label className="model-editor-enabled">`
>   wraps a `Switch`, which itself renders an inner `<label>`. Nested `<label>` is
>   non-conforming HTML and the resulting accessible name is
>   implementation-dependent. Pass `label="Enable after verification"` to the
>   `Switch` and drop the outer wrapper.
> - `ModelProvidersSection.tsx:490`, `:657` — bare switches in
>   `.model-card-controls`; their meaning currently comes from card position only.
> - `SignalsTab.tsx:387` — per-feed switch whose name lives in a sibling
>   `<div className="news-feed-info">`; wire `aria-labelledby` to the feed name.
>
> The correct pattern already exists in the same directory:
> `SettingsPrimitives.tsx`'s `Field` generates a `controlId` and wires
> `for`/`id`/`aria-describedby`/`aria-invalid`, and it has tests
> (`SettingsPrimitives.test.tsx`). Phase 0.5 extends that established pattern to
> `SettingsRow`/`Switch` rather than inventing anything.
>
> Scope — one PR, no visual change, no backend change:
>
> 1. `SettingsRow` derives a stable id from its label, renders the label as a real
>    `<label for>` (or exposes `aria-labelledby`), and passes that association to
>    its control.
> 2. `Switch` accepts and applies that association, keeping the current
>    `aria-hidden` track markup.
> 3. `frontend/src/index.css` — in `.settings-section-head`, drop
>    `border-left: 2px solid var(--accent-dim)` (§8 rule 1). Leave the visually
>    similar `.dream-session` accent border alone: that one is a deliberate
>    Learning-page signature, not Settings chrome.
> 4. `frontend/src/index.css` — in `.settings-section-head h3`, change
>    `font-family: var(--display)` to `var(--sans)` and `font-size: 0.88rem` to
>    `1rem`. This is what closes the audit's sub-pixel hierarchy finding:
>    `0.88rem` computes to 14.08px against a `.settings-row-label` of `0.82rem`
>    = 13.12px, which is the measured ~0.96px separation.
> 5. Name the 6 non-Row switches listed above, including the `RiskTab.tsx:228`
>    mis-association and the `ModelProvidersSection.tsx:361` nested-`<label>`
>    defect.
> 6. Add one repository-wide sweep test asserting that no rendered Settings
>    checkbox/switch has an empty accessible name (see the §Verification
>    addendum for why a sweep, not per-section assertions).
>
> Why this ordering: items 3 and 4 are deletions and token swaps, item 1-2 change
> only ARIA wiring, so the whole phase is reviewable in one sitting and carries no
> layout risk. It turns two acceptance criteria green — "hierarchy remains visible
> in grayscale" and "no empty `LabelText` remains" — before any navigation or card
> restructuring begins. If later phases stall, this value is already banked.
<!-- CLAUDE-CODE-REVIEW:END r2 -->

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

<!-- CLAUDE-CODE-REVIEW:BEGIN r8 phase-3-unsized -->
> **Review addendum (Claude Code, 2026-08-06) — Phase 3 is 5× the size of Phases
> 0-2 and is currently two bullet points.** Measured in `frontend/src` at review
> time:
>
> | Quantity | Count |
> | --- | --- |
> | Navigation destinations in `App.tsx`'s `NAV` | 14 |
> | Routes registered in `App.tsx` | 23 (14 nav + legacy redirects/details) |
> | Non-test `.tsx` files | 74 |
> | Non-test `.tsx` lines | 21,897 |
> | `pages/*.tsx` lines (the Phase 3 target) | 10,029 across 14 files |
> | `index.css` lines | 4,717 |
> | `<Card>` element usages | 116 |
> | Additional `className="card …"` sites | 73 |
>
> Phases 1-2 together cover `pages/Settings.tsx` (480 lines) plus
> `features/settings/**`. Phase 3 covers the other 9,549 lines of page code, and
> the three pages it names first are among the largest in the codebase:
> `CommandCenter.tsx` (1,611), `Learning.tsx` (1,126), `Overview.tsx` (601). The
> 189 combined card sites are the concrete work item behind "remove card wrappers
> that do not represent independent entities" — that rule alone is a 189-site audit.
>
> This matters for closure, not just estimation. Issue #689 is the sole execution
> and closure tracker, and a phase whose scope is unstated cannot be closed: there
> is no definition of "done" for "and then remaining pages". Two options, both
> acceptable:
>
> 1. **Preferred** — mark Phase 3 explicitly as *to be re-decomposed after Phase 2
>    ships*, with the re-decomposition itself as a deliverable of Phase 2. This is
>    honest about the fact that the per-page cost is unknown until the primitives
>    are proven on Settings, and it keeps #689 closable in stages.
> 2. Decompose now into one child issue per nav destination, accepting that the
>    estimates will be revised.
>
> What should **not** happen is Phase 3 remaining a single open-ended bullet while
> Phases 0-2 merge, because then the renewal's stated goal ("no whole-frontend
> rewrite", Non-goals) becomes indistinguishable from an unbounded one, and the
> two-visual-systems-in-parallel risk this Proposal names in its own Trust review
> is exactly what materializes.
<!-- CLAUDE-CODE-REVIEW:END r8 -->

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

<!-- CLAUDE-CODE-REVIEW:BEGIN r9 deprecation-table -->
> **Review addendum (Claude Code, 2026-08-06) — "move into" is not enough; six
> standing rules are contradicted and must be explicitly retired.** This section
> describes an additive merge, but several rules in this Proposal do not extend
> `ui_design_system.md` — they reverse it. Without a retirement list, both texts
> stay normative and the next implementer can cite the old rule to reject the new
> work, or the reverse. Worse, every conflict below is *live code*, so an
> incomplete retirement leaves the tree in a state that violates whichever
> document is consulted.
>
> All coordinates verified in the tree at review time.
>
> | # | Old rule (authority) | New rule (this Proposal) | Live code | Disposition |
> | --- | --- | --- | --- | --- |
> | D1 | `--display` (Space Grotesk) is for "Section 标题、品牌" — §2 token table, `ui_design_system.md:54` | §5: Space Grotesk is brand-only, "not for productive Settings/workstation hierarchy" | 11 `var(--display)` sites in `index.css` | **Retire in part.** Keep for `.brand-name:235`; retire for the 6 productive titles (`.settings-section-head h3:876`, `.settings-config-title:1015`, `.provider-form-title:1178`, `.drawer-title:1295`, `.cc-inspector-title:2149`, `.status-popover-title:403`). Three page-signature sites need an explicit call, not silent inclusion: `.page-title:562`, `.risk-console-word:3547`, `.learn-pulse .stat-value:3819` — these are expressive, not Settings hierarchy. `.crumb-current:291` is a fourth judgement call. |
> | D2 | `Section` provides grouping via "标题 + 描述 + `border-left: 2px solid var(--accent-dim)`" — `ui_design_system.md:147` | §8 rule 1: "Remove the cyan left border from `.settings-section-head`"; §5: section title has "no accent bar" | `index.css:873` | **Retire.** Note the identical declaration at `index.css:3884` (`.dream-session`) is a *different* component and out of scope — the retirement must name the selector, not the declaration, or a grep-driven implementer will strip the Learning page signature too. `.markdown-body blockquote:1451` (3px) is likewise unrelated. |
> | D3 | Section title size is undefined in the doc; the implementation ships `0.88rem` for `h3` against `0.82rem` row labels | §5: section title 16/24, row label 14/20; "A sub-pixel or one-pixel difference is not a hierarchy" | `.settings-section-head h3:876` | **Supersede.** The old *doc* says nothing, so nothing is retired — but the old *implementation* is the counter-example §5 was written against (14.08px vs 13.12px ≈ 0.96px). Recording it here prevents the gap being read as already-compliant. Executed by Phase 0.5 (see the §Phase 0.5 addendum). |
> | D4 | "统一网格 — 所有表单卡片用 2 列 form-grid，不自造列数不一的内联 grid" — core principle 6, `ui_design_system.md:14`, restated `:172` | §6: control widths from a small set (compact/standard/full); inputs in a group share a left edge; §8 rule 4: every row owns the full detail-surface width | 16 `className="form-grid"` sites across 8 files; `.form-grid` is hard-coded `grid-template-columns: 1fr 1fr` (`index.css:1167`) | **Unresolved — this Proposal does not say.** Two rules coexist without a stated relationship: is a 2-column form grid still the default inside a section, or does the full-width row model replace it? `AgentModelsTab.tsx:745` already overrides to `repeat(3, 1fr)` inline, which principle 6 forbids and `:172` grudgingly permits. Decide before Phase 1, because the answer determines whether Phase 1 touches 16 sites or zero. |
> | D5 | 容器嵌套铁律 — an interactive component lives inside the grid column it aligns to; a WRONG/CORRECT pair is given for the Settings search box specifically — `ui_design_system.md:124`, change history `2026-06-29 bcdcebd1` ("align search box with panel content column") | §7 wide-screen sketch places `[Search settings]` on the title line, outside `.settings-shell` | `bcdcebd1` is the fix currently in the tree | **Keep the old rule** (recommended), and redraw the sketch — see the §7 addendum. If option 2 is chosen instead, this row becomes a genuine retirement and must say so, because the rule exists as the record of a shipped-then-repaired bug. |
> | D6 | 方向双重编码 — "`▲▼` 是重复通道…**去掉三角，保留 `±` 号**" — §5 redundancy table, `dd6425ad` 2026-07-19 | §14 Duplication gate inherits this, but code carries a contrary WCAG justification | `format.ts:219` docstring cites *"WCAG 1.4.1 — not color-only"* (`3dc168d3`, 2026-06-21); 3 in-scope sites, 8 out-of-scope | **Pick one and record which.** Whichever way it goes, one side is currently wrong. Scope is 3 edits — see the §14 addendum for the classification that keeps sort/disclosure/legend arrows out of it. |
>
> Two further items belong in this section and are not conflicts, only gaps:
>
> **D7 — Document language is undecided.** The receiving authority
> `ui_design_system.md` is written in Chinese; this Proposal is in English. #689
> requires one language per rendered page, which the merge target would violate
> the moment English rules land in a Chinese document. Options: translate on
> merge (highest cost, cleanest result), keep the merged document English with a
> one-time full translation, or split normative-English from
> lessons-learned-Chinese. This must be decided before the merge, not during it.
>
> **D8 — The footguns must survive the merge.** `ui_design_system.md`'s most
> valuable content is not its principles but its record of specific failures, each
> of which cost a debugging session and none of which this Proposal restates:
> `.form-grid label`'s `flex-direction: column` splitting a Toggle from its text
> onto two lines (`:164-169`, and the reason `:309` forbids Toggles in form-grid
> labels); `column-count` masonry failing; `flex: 1 1 0` in a column-flex
> container collapsing the Command Center heartbeat zone to a 143px strip
> (`:256`); the `minmax(0, 1fr)` shadcn footgun; `color-scheme: dark` for
> scrollbars. A merge that keeps the abstractions and drops the traps is a net
> loss of institutional knowledge. Carry them across verbatim, including their
> WRONG/CORRECT code pairs.
<!-- CLAUDE-CODE-REVIEW:END r9 -->

<!-- CLAUDE-CODE-REVIEW:BEGIN r10 addendum-index -->
> **Review index (Claude Code, 2026-08-06).** Ten review addenda were inserted
> into this Proposal, each wrapped in `<!-- CLAUDE-CODE-REVIEW:BEGIN … -->` /
> `<!-- CLAUDE-CODE-REVIEW:END … -->` and rendered as blockquotes. They are
> commentary on the Proposal, not part of its normative text; strip the marked
> blocks to recover the owner's original document.
>
> | id | Section | Subject | Asks for |
> | --- | --- | --- | --- |
> | r1 | §7 | Search placement contradicts the container-nesting rule | Redraw the sketch (option 1) or record a real retirement |
> | r2 | Phase 0.5 | Accessible-name root cause is a two-primitive contract gap; 34 switches fixed wholesale | Insert Phase 0.5 ahead of Phase 1 |
> | r3 | §9 | `update.enabled` semantics resolved from source | Drop it as a blocking prerequisite; use the stated label |
> | r4 | §10 | Switch-rule blast radius measured: 65 sites / 28 immediate / 37 staged, plus 24 via `SwitchGrid` | Decide `SwitchGrid` first — it is the single highest-leverage call |
> | r5 | §11 | 4 of 8 state facts do not exist in the backend; "unknown" works only by ternary accident | Add a backend-provides column; open one backend issue |
> | r6 | §14 | Gates duplicate the existing §5 table | Merge rather than append; keep the exceptions; anchor each gate |
> | r7 | Verification | Browser-based requirements are unrunnable on current devDeps | Split automatable from manual; reuse the `?raw` sweep idiom |
> | r8 | Phase 3 | 9,549 lines / 189 card sites behind one sentence | Re-decompose as a Phase 2 deliverable |
> | r9 | Resolution | Six standing rules are contradicted, none retired | Adopt the deprecation table; decide D4 and D7 |
> | r10 | Resolution | — | This index |
>
> Reading order for an implementer: **r2 → r4 → r9 → r1 → r6 → r5 → r7 → r3 → r8.**
> r2 is a zero-risk PR that can ship the same day and unblocks the a11y half of
> the verification contract; r4 and r9 are decisions that change what Phase 1
> touches and should be settled before any code moves.
<!-- CLAUDE-CODE-REVIEW:END r10 -->

