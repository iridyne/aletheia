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

### Switch 控件自身即状态——不要重复显示

Switch（滑块开关）的视觉设计天然表达 on/off 状态：track 颜色、thumb 位置、动效全部在传达当前值。**不要**在旁边再放一个 capsule badge（`ON`/`OFF`/`ACTIVE`/`DISABLED`）或文字标签来复述相同信息。

冗余指示器带来的问题：
- 视觉噪声——用户需要两次确认同一个信息
- 维护成本——状态文案需跟 Switch 值同步，容易出 bug
- 语义歧义——badge 在设计体系中用于*外部状态*（连接状态、服务健康度），滥用会稀释其含义

**规则:**
- Switch 旁的 `label` prop 用于标识*控制对象*（如 "Spot"、"Telegram delivery"），不用于复述开关状态
- 如果只有一个 Switch 控制单一布尔值，Row 的 `label` 已经说明了含义，Switch 不需要额外 label
- 仅当 Switch 关闭后需要给出额外上下文（如 "Agents use static symbol list when disabled"）时，用灰色 hint 文字——这不是状态指示器，而是后果说明

```tsx
// WRONG — badge 复述 Switch 状态
<Toggle checked={enabled} onChange={...} />
<span className="badge badge-green">ACTIVE</span>

// CORRECT — Switch 自说明，不需要 badge
<Toggle checked={enabled} onChange={...} />

// CORRECT — 关闭后的后果说明（非状态复述）
<Toggle checked={enabled} onChange={...} />
{!enabled && <span className="hint">Agents use static symbol list when disabled.</span>}
```

### 信息冗余审计原则 (Information Redundancy)

每个视觉元素必须"付得起房租"——占据屏幕空间就必须贡献独特信息或辅助任务完成。参考 Nielsen H8（极简设计）、Tufte 数据墨水比、NNG 认知负荷理论。

**五类常见冗余（应消除）：**

| 类型 | 示例 | 判断标准 |
|------|------|---------|
| 双重编码 | 绿色圆点 + 文字 "active"（同一信息用两个通道） | 如果去掉文字后，色盲用户无法理解 → 保留（accessibility）。否则 → 去掉重复通道 |
| 标签回声 | Section "Open Positions" + badge "3 open" | 如果去掉 badge 里的 "open"，信息依然完整 → 只显示数字 |
| 控件 + 状态复述 | Switch + badge "ON"/"Enabled" | Switch 视觉本身即状态表达 → 永远不加状态 badge |
| 区域内重复 | 数据来源在 source bar 显示，又在 chart controls 里重复 | 同一 viewport 内同一信息出现两次 → 保留信息层级更高的那个 |
| Context 回声 | label="Today PnL" + context="today" | context 字段应补充 label 未覆盖的维度（%、时间范围、数据源） |

**保留双通道的合理场景：**
- 数据表格中的 `StatusBadge`（每个 cell 必须独立可读）
- `StatusText` 纯色字（文字 IS 主要信息载体，color 只是辅助 tint）
- Dashboard 圆点 + 内容值（如 "Exchange: bitget · connected"——圆点给快扫，文字给详情）
- 严肃操作的确认区域（删除按钮旁红色 warning 文案——冗余是刻意的摩擦）

**审计 checklist（每次改 UI 过一遍）：**
1. 同一视觉区域内，是否有两个元素传达完全相同的事实？
2. 如果去掉其中一个，用户能否依然在 <2 秒内获取该信息？
3. 保留的那个是否对色盲/低视力用户仍然可达？
4. 保留的编码方式是否与设计系统其他地方一致？

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
