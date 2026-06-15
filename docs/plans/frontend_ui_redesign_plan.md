# Frontend UI Redesign Plan

Date: 2026-06-15

## Scope

This plan covers the dashboard implementation layer only:

- React page layout and local UI components.
- `frontend/src/index.css` design tokens and interaction rules.
- Settings information architecture and form controls.
- Static dashboard bundle under `aletheia/dashboard_static/`.

Out of scope:

- Trading execution, risk decisions, live trading gates, exchange clients, signer logic, and secrets handling.
- Backend API semantics except when a frontend control already maps to an existing safe settings route.

## Current gaps

1. Global `button:active` movement made list rows and recommendation items feel draggable.
2. Settings used four broad top tabs that no longer matched the size of the configuration surface.
3. Visible native checkboxes were mixed with badge capsules, especially in Settings.
4. Badge/capsule styling carried too much color weight and was used for too many meanings.
5. Desktop settings rows wrapped too easily instead of aligning controls to the right.
6. Native number-input spinners stood out against the dark UI.
7. Settings implementation accumulated many inline styles and duplicated field-layout patterns.

## Design system direction

Use local shadcn/Radix-inspired primitives without a full Tailwind migration in the first pass:

- Button: actions only; no global press displacement.
- List item button: row selection semantics; no drag/press effect.
- Badge: low-emphasis outline/status text by default.
- Switch: replaces visible checkbox controls.
- Field row: label/description on the left, control/action area right-aligned on desktop.
- Field grid: numeric/text inputs aligned on a repeatable responsive grid.
- Settings nav: vertical desktop navigation, horizontal scroll on compact screens.

Radix/shadcn adoption remains incremental. Candidate primitives for later adoption:

- Switch, Tabs, Select, Tooltip, Dialog, Popover, Scroll Area.

## Phase plan

### P0 — Global interaction and visual hygiene

- Remove global button translate/drag effect.
- Ensure list buttons override hover/active behavior.
- Hide native number-input spinners.
- Reduce badge background opacity and visual weight.
- Convert Command Center recommendation severity from colored capsule to compact severity text.

Acceptance:

- Recommendation severity no longer looks like a draggable capsule.
- Numeric inputs do not show bright native spinners.
- Buttons still have clear hover/focus states.

### P1 — Settings information architecture

- Replace four top tabs with six sections:
  1. Agent & Models
  2. Execution
  3. Risk & Universe
  4. Health & Alerts
  5. Runtime & System
  6. Advanced
- Keep backward-compatible URL aliases:
  - `tab=intelligence` -> `agent`
  - `tab=infrastructure` -> `runtime`
- Keep `/settings?tab=risk&section=universe` working for Universe Board.
- Move Configuration Matrix to Advanced.

Acceptance:

- Settings feels like a structured settings app, not a four-tab dump.
- Direct Universe Board setting link remains valid.
- Existing settings API coverage remains visible.

### P2 — Settings control cleanup

- Replace visible checkboxes with switch controls.
- Use `settings-toggle-grid` for related boolean controls.
- Use `settings-action-row` for save buttons and messages.
- Continue reducing inline style blocks in Settings.

Acceptance:

- Settings no longer visually mixes checkbox + capsule status for the same boolean.
- Control areas align consistently on desktop and stack only on narrow screens.

### P3 — Page-by-page layout pass

Priority order:

1. Command Center
2. Settings follow-up cleanup
3. Risk & Controls
4. Overview
5. Trades & Positions
6. Prediction Lens / Serenity Lens
7. Charts / Universe / News / Learning

Checks per page:

- Card header consistency.
- Badge usage limited to true status/alerts.
- Row actions right-aligned.
- No accidental flex-wrap on desktop.
- Tables wrapped in scroll containers where needed.
- Lists use row components instead of action button styling.

### P4 — Component extraction

Create or consolidate local UI primitives:

- `components/ui/Button`
- `components/ui/Card`
- `components/ui/Badge`
- `components/ui/Switch`
- `components/ui/Field`
- `components/ui/SettingsSection`
- `components/ui/StatusDot`

Acceptance:

- Page files stop re-implementing repeated layout primitives.
- Inline styles continue to trend down.

## Regression checks

- `npm run build` in `frontend/`.
- Static bundle rebuilt into `aletheia/dashboard_static/`.
- Frontend source tests assert:
  - Settings six-section IA exists.
  - Switch styling is used.
  - Native number spinners are hidden.
  - Global button active transform is disabled.
  - Command Center recommendation severity uses text style, not badge capsule.

