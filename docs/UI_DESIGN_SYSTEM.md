# Aletheia UI Design System

Design language, layout patterns, and lessons learned for the Aletheia Trading OS dashboard.

---

## 1. Visual Identity

### Color Palette

| Token | Hex | Usage |
|-------|-----|-------|
| `--bg` | `#0a0e12` | App background (near-black, cool tint) |
| `--bg-2` | `#0f141a` | Main content area (recessed) |
| `--surface` | `#141a21` | Cards, sidebar, modals |
| `--surface-2` | `#1a212a` | Hover states, nested surfaces |
| `--border` | `#232c36` | Default borders |
| `--border-2` | `#2e3946` | Heavier separators (section headers) |
| `--text` | `#e6edf3` | Primary text |
| `--muted` | `#7d8997` | Labels, hints, secondary text |
| `--faint` | `#4a5562` | Barely-visible guides |
| `--accent` | `#22d3ee` | Signal cyan — the ONE sharp accent |
| `--accent-dim` | `#0e7490` | Accent backgrounds, active states |
| `--green` | `#2ee6a8` | Success, connected, enabled |
| `--red` | `#ff5d6c` | Error, danger, disabled |
| `--yellow` | `#f5b942` | Warning, pending |
| `--blue` | `#4aa3ff` | Informational links |

### Typography

| Token | Family | Usage |
|-------|--------|-------|
| `--display` | Space Grotesk | Section headings, brand |
| `--sans` | IBM Plex Sans | Body text, labels |
| `--mono` | IBM Plex Mono | Data values, inputs, code |

- Tabular figures (`font-feature-settings: 'tnum'`) on all numeric displays.
- Antialiased rendering (`-webkit-font-smoothing: antialiased`).

### Atmosphere

- Faint cyan grid texture (64px) on body background — terminal aesthetic.
- `border-radius: var(--r)` (8px) for cards, `var(--r-sm)` (5px) for small elements.
- Minimal shadows: `--shadow` only on floating overlays (dropdowns, tooltips).

---

## 2. Layout Patterns

### Section + Row (Settings-style)

```
Section (title + description + accent-dim left bar)
  └── Row (2-col grid: label | control)
```

- `Section` provides semantic grouping with a heading, description, and `border-left: 2px solid var(--accent-dim)`.
- `Row` is a `display: grid; grid-template-columns: 1fr 1fr` two-column layout with label/hint on the left, controls on the right.
- Use `Row` for simple toggles, selects, and read-only values.

### Form Grid (`.form-grid`)

```css
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.7rem; }
.form-grid label { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.75rem; color: var(--muted); }
.form-grid input, .form-grid select { width: 100%; }
```

Use for multi-field forms inside cards. Rules:

1. **Each `<label>` wraps one field** — a `<span>` for the label text, then the `<input>` or `<select>`.
2. **Inputs fill their column** — never set `width: 90px` or similar fixed widths.
3. **Units go in the label text** — write `Default stop-loss (%)`, not a separate `<span>%</span>` beside the input.
4. **Full-width fields** — use `style={{ gridColumn: '1 / -1' }}` for URLs, API keys, or any field that benefits from the full width.
5. **Label text must be a single `<span>`** — because `flex-direction: column` makes each child its own row. Never put bare text + a sibling `<span>` inside a label; they will split across lines.

### Cards

Cards (`.card` or inline `border + border-radius + background: var(--surface)`) contain form-grids, status lines, and action rows. Structure:

```
card
  ├── status/connection line (optional)
  ├── .form-grid (inputs)
  ├── action row (buttons + save feedback)
  └── hint paragraph
```

---

## 3. Component Patterns

### Combobox (input + filtered suggestions)

When a field has both predefined options AND allows custom input, use a **combobox** — NOT a `<select>` with a "(custom)" fallback.

```
<label style={{ position: 'relative' }}>
  <span>Label</span>
  <input onFocus={showAll} onChange={filter} onBlur={hide} />
  {showSuggestions && (
    <div style={{ position: 'absolute', top: '100%', ... }}>
      {filteredItems.map(item => <div onMouseDown={select}>{item}</div>)}
    </div>
  )}
</label>
```

Key behaviors:
- **Focus (no typing)** — show the full list.
- **Typing** — filter the list by input.
- **Click suggestion** — set value, close dropdown.
- **Custom value** — user can type any value, suggestions are optional.
- **onBlur delay** — 150ms `setTimeout` so `onMouseDown` on suggestions registers before blur closes the dropdown.

### Toggle (checkbox)

The `Toggle` component renders `<label><input type="checkbox" />{text}</label>` as inline-flex.

**Critical rule:** Never place a Toggle inside a `.form-grid label`. The form-grid label uses `flex-direction: column`, which stacks the Toggle's checkbox and text vertically across multiple lines.

Instead, render toggles as standalone elements outside/below the form-grid:

```tsx
// WRONG — checkbox stacks vertically inside form-grid label
<div className="form-grid">
  <label>
    <span>Enabled</span>
    <Toggle ... />   {/* breaks onto multiple lines */}
  </label>
</div>

// CORRECT — toggle outside form-grid, inline-flex row
<div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
  <Toggle ... />
  <span className="badge ...">ON</span>
</div>
```

### Badges

Status indicators: `.badge`, `.badge-green`, `.badge-red`, `.badge-yellow`.
- Small, rounded, uppercase. Use next to toggles or in status lines.
- Never inside a form-grid label as a sibling to bare text (split-line bug).

### Connection Status

`<ConnectionStatus label="..." state="green|red|yellow|muted" text="..." />` — a dot + monospace status line. Use at the top of credential/connection cards.

---

## 4. Lessons Learned

### The `flex-direction: column` trap

`.form-grid label` uses `flex-direction: column` so that label text stacks above the input. This means **every direct child becomes its own row**. Common mistakes:

| Problem | Fix |
|---------|-----|
| `Label text` + `<span>(count)</span>` → split across lines | Wrap in single `<span>Label (count)</span>` |
| Toggle/checkbox inside label → checkbox on one line, text below | Move toggle outside `.form-grid` |
| `<span>Badge</span>` after label text → badge on next line | Include badge info in label text string |

### Fixed-width inputs don't scale

Never use `width: 90px` or `width: 100px` on inputs inside form-grid. The grid columns already define width — let inputs fill with `width: 100%` (the default from `.form-grid input`).

### Units belong in labels, not beside inputs

Instead of:
```tsx
<input style={{ width: '90px' }} /> <span>%</span>
```

Write:
```tsx
<label><span>Max daily loss (%)</span><input /></label>
```

This eliminates the need for a flex-row wrapper, keeps the grid simple, and works at any viewport width.

### Inline grids vs. `.form-grid`

If you're writing `display: grid; gridTemplateColumns: repeat(auto-fill, minmax(200px, 1fr))` inline, stop. Use `.form-grid` (always 2 columns). For special cases, override with `gridTemplateColumns` inline on the form-grid div — but the class gives you consistent label styling, gap, and input width for free.

---

## 5. File Reference

| File | Role |
|------|------|
| `frontend/src/index.css` | All CSS variables, base styles, `.form-grid`, typography |
| `frontend/src/pages/Settings.tsx` | Reference implementation of Section/Row/form-grid patterns |
| `frontend/src/components/ConnectionStatus.tsx` | Connection dot + status text |
| `frontend/src/components/PageHeader.tsx` | Page title + breadcrumb + role text |

---

## 6. Design Principles

1. **Information density over whitespace** — this is a trading terminal, not a marketing site.
2. **One accent color** — signal cyan (`--accent`) is the only saturated hue for interactive/positive states. Red/yellow/green are reserved for semantic status.
3. **Monospace for data** — any value that changes or can be compared (numbers, IDs, keys, code) uses `--mono`.
4. **Write-only secrets** — never display API keys; show only `··last4` or "set"/"not configured".
5. **Progressive disclosure** — collapsed cards show status summary; expand to edit. Don't overwhelm with all fields at once.
6. **Consistent grid** — 2-column form-grid for all form cards. No ad-hoc inline grids with varying column counts.
