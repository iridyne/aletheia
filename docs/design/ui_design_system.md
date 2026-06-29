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

### Toggle (Switch)

The `Toggle` component renders `<label><input type="checkbox" />{text}</label>` as inline-flex.

**Rule 1 — 必须放在 `<Row>` 里。** Settings 页面中的每个 Toggle 都必须用 `<Row label="..." hint="...">` 包裹，让标签在左、Toggle 在右对齐。**不能**裸放在 `flex items-center gap-3` div 里——这样没有标签说明含义，hint 也无处归位。

```tsx
// WRONG — 裸 Toggle，没有标签，hint 只在 disabled 时出现
<div className="flex items-center gap-3 mb-3">
  <Toggle checked={enabled} onChange={...} />
  {!enabled && <span className="hint">Agents use static list.</span>}
</div>

// CORRECT — Row 提供标签 + hint 常驻，Toggle 右对齐到控件列
<Row
  label="Enable Universe Scanner"
  hint="When on, dynamically scores USDT pairs each cycle. When off, agents use the static fallback list."
>
  <Toggle checked={enabled} onChange={...} />
</Row>
```

**Rule 2 — 不能放在 `.form-grid label` 里。** The form-grid label uses `flex-direction: column`, which stacks the Toggle's checkbox and text vertically across multiple lines.

```tsx
// WRONG — checkbox stacks vertically inside form-grid label
<div className="form-grid">
  <label>
    <span>Enabled</span>
    <Toggle ... />   {/* breaks onto multiple lines */}
  </label>
</div>
```

**Rule 3 — Switch 自身即状态，不加 badge 复述。** 见 §4 "Switch 控件自身即状态"节。

### Selection Controls（互斥选项）

当用户从一组互斥选项中选一个时，遵循下列优先级：

| 场景 | 推荐控件 | 说明 |
|------|---------|------|
| 选项 ≤ 4 且标签短 | `<select>` in `<Row>` | 默认选择，不随选项数膨胀 |
| 选项 2-3 个且需频繁切换 | `.seg-toggle` segmented button | 仅用于高频内联切换（timeframe、feed mode） |
| 选项多 + 需模糊搜索 | Combobox（见上节） | 用户可自定义输入时 |

**禁止使用 tile 卡片（button grid）做互斥选择。** tile 模式的问题：
- 选项增加 → 卡片行数/列数增加 → 布局不可预测（`auto-fill` 宽度变化）
- 已选中状态需要自定义 CSS，维护成本高
- 与行业惯例不符（VS Code、macOS Settings、Figma 都用 select/radio/segmented）

```tsx
// WRONG — 每个交易所一个 tile 卡片，随数量膨胀
<div className="exchange-tiles">
  {EXCHANGES.map(ex => (
    <button className={`exchange-tile${active === ex.id ? ' active' : ''}`} ...>
      {ex.label}
    </button>
  ))}
</div>

// CORRECT — select + Row，不随选项数膨胀
<Row
  label="Primary exchange"
  hint="Atomically updates data source, paper execution mode, and risk allowlist."
>
  <select value={active} onChange={e => handleSelect(e.target.value)} className="w-auto">
    {EXCHANGES.map(ex => <option key={ex.id} value={ex.id}>{ex.label}</option>)}
  </select>
</Row>
```

**confirm() 对 select onChange 是有效的安全门。** React controlled `<select value={active}>` 在 `confirm()` 返回 false 时，因 `active` 未更新，下次 render 会自动回退到原值。无需额外的 reset 逻辑。

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

### 错误/成功反馈信息的位置（下方弹出，不同行并排）

**规则：操作结果反馈信息（错误或成功）必须显示在触发按钮的下方，而不是与按钮同行右侧。**

同行并排的问题：
- 按钮区域固定宽度有限，错误文本会挤压按钮或被截断
- 触发 flex-wrap 时，元素跳行方式不可预期
- 与现代 UI 规范不符（输入反馈应在控件下方，符合视线从上到下的扫描方向）

**实现方式：**

```tsx
// WRONG — 错误信息与按钮同行，挤占空间
<div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
  <button onClick={save}>Save</button>
  {error && <span style={{ color: 'var(--red)' }}>{error}</span>}
</div>

// CORRECT — 按钮行独立，反馈信息在下方独立 block
<div style={{ display: 'flex', gap: '0.5rem' }}>
  <button onClick={save}>Save</button>
</div>
{error && <div className="settings-save-msg err">{error}</div>}
```

**`SaveMessage` / `SaveMsg` 组件**（`SettingsSection.tsx`）已经渲染为 `<div>` block 元素。只需把它放在按钮 flex 行**之后**，不要放在行内。

**CSS 定义（`index.css` — `.settings-save-msg`）：**
- `display: block`，独占一行
- `padding: 0.28rem 0.55rem`，`border-radius: var(--r-sm)`
- `.ok`：绿色文字 + 淡绿背景 + 绿色边框
- `.err`：红色文字 + 淡红背景 + 红色边框

这一模式也适用于 `SettingsRow` children 中出现的错误（如 `LiveStateRow`）：把 warning 提到 `<Row>` 外部，用 wrapper `<div>` 包住 `<Row>` 和 `<div className="settings-save-msg err">`。

### Inline grids vs. `.form-grid`

If you're writing `display: grid; gridTemplateColumns: repeat(auto-fill, minmax(200px, 1fr))` inline, stop. Use `.form-grid` (always 2 columns). For special cases, override with `gridTemplateColumns` inline on the form-grid div — but the class gives you consistent label styling, gap, and input width for free.

### 组件必须放在正确的网格列内（容器嵌套规则）

**规则：跨列的交互组件必须放进它所属的网格子容器，不能悬浮在网格外部。**

Settings 页面的 `.settings-shell` 是一个 2 列网格（左 nav 280px + 右 panel 1fr）。搜索框曾被放在 `.settings-shell` 的**外面**（作为 `<PageHeader>` 的下一个兄弟），而不是放在右侧 `.settings-panel` 内部。结果：

- 搜索框以页面最左边缘（nav 列的左边缘）为起点
- `max-width: 480px` 横跨 nav 列 + gap + panel 部分区域
- 视觉上悬浮在两栏之间，哪里都不靠

**修法：** 把 `<SettingsSearch>` 从 shell 外移入 `.settings-panel` 内部作为第一个子元素。左边缘自然对齐面板内容，宽度受面板约束。

```tsx
// WRONG — 搜索框悬浮在 2 列网格外部
<div>
  <PageHeader ... />
  <SettingsSearch />          {/* ← 在 shell 外，宽 480px 从最左侧开始 */}
  <div className="settings-shell">
    <aside className="settings-nav">...</aside>
    <div className="settings-panel">...</div>
  </div>
</div>

// CORRECT — 搜索框作为面板第一个子元素
<div>
  <PageHeader ... />
  <div className="settings-shell">
    <aside className="settings-nav">...</aside>
    <div className="settings-panel">
      <SettingsSearch />       {/* ← 在面板内，左边缘与内容对齐 */}
      ...
    </div>
  </div>
</div>
```

**通用原则：** 任何 Settings 页面内的交互组件（搜索、筛选、操作按钮区），其视觉对齐目标是哪列，就放进那列的容器里——不要靠 `max-width` 或 `margin-left` 去"模拟"对齐。

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

---

## 7. Spacing & Density Tokens (Bloomberg-density pass)

所有 spacing 必须引用 CSS var，禁止硬编码 px。

| Token | Value | 用途 |
|-------|-------|------|
| `--space-1` | 4px | 最小间距(icon gap, pill 内距) |
| `--space-2` | 8px | 行内元素 gap, feed-row padding |
| `--space-3` | 12px | card padding-block, card margin-bottom, section gap |
| `--space-4` | 16px | main 区域 padding-inline, cockpit grid gap |
| `--space-5` | 20px | main padding-inline(宽屏), sidebar padding |
| `--space-6` | 24px | settings grid column gap |
| `--space-8` | 32px | settings-section bottom margin |

### 密度决策记录

| 元素 | 旧值 | 新值 | 理由 |
|------|------|------|------|
| `.card` padding | 16px / 16px | 12px / 16px (block/inline) | 垂直密度提升 ~25% |
| `table td/th` padding | 8px | 5px inline-start | 行高更紧凑 |
| `th` font-size | — | 0.66rem | 列头不争夺视觉权重 |
| `.metric-tile` min-height | 84px | 68px | 减少 PnL zone 垂直占用 |
| `.metric-value` font-size | 1.45rem | 1.3rem | 与整体密度匹配 |
| `button` padding | 0.42/0.85rem | 0.32/0.7rem | 与 28px min-height 对齐 |
| `.pnl-hero-value` | 2.4rem | 2rem | hero 不过分膨胀 |
| `.cockpit-main` gap | 16px | 12px | rail 与 feed 更紧 |
| `.cockpit-rail` width | 320px | 310px | 窄 10px 给 feed 更多空间 |
| `sidebar nav a` padding | 0.45rem | 0.35rem | 更多 nav items 可见 |

---

## 8. Border Radius 规范

| Token | Value | 适用范围 |
|-------|-------|---------|
| `var(--r)` | 8px | 卡片、弹窗、dialog、textarea、inspector |
| `var(--r-sm)` | 5px | 按钮、badge、input、select、小 chip |
| `999px` | pill | 仅用于 switch track、pill badge |

**铁律：** 禁止写 `border-radius: 10px/12px/14px` 等硬编码值。Command Center 已统一纠正。

---

## 9. 交互反馈 (Interaction Feedback)

### 按钮

```css
button { transition: filter 80ms, transform 60ms; }
button:hover { filter: brightness(1.12); }
button:active:not(:disabled) { transform: scale(0.97); filter: brightness(0.95); }
button:focus-visible { outline: 1px solid var(--accent); outline-offset: 2px; }
button.secondary:active { background: var(--bg); }
```

- 过渡时长 80ms (hover) / 60ms (press) — 快于动画，接近即时响应
- `scale(0.97)` 给触觉反馈但不分散注意力
- `focus-visible` 仅键盘导航时出现，不影响鼠标用户

### 可点击卡片

```css
.card.clickable:hover { border-color: var(--border-2); background: var(--surface-2); }
.card.clickable:active { transform: scale(0.995); }
```

### Feed 行 / Table 行

```css
.feed-row:hover { background: var(--surface-2); }
.feed-row:active { background: var(--bg); }
tr.clickable:active { background: var(--bg); }
```

### Charts 看板卡

```css
.charts-watchlist-card:hover { background: var(--surface-2); }
.charts-watchlist-card:active { transform: scale(0.98); }
```

**统一规律：**
- hover → lighten background (`surface-2`)
- active → darken (`bg`) 或 scale (卡片类用 scale, 行类用 darken)
- transition 均 ≤100ms

---

## 10. 无障碍 (Accessibility)

### 对比度 (Contrast Ratios, WCAG AA)

| 前景 → 背景 | 比值 | 判定 |
|-------------|------|------|
| `--text` on `--bg` | 16.39:1 | AA ✓ |
| `--text` on `--surface` | 14.82:1 | AA ✓ |
| `--muted` on `--bg` | 5.44:1 | AA ✓ |
| `--muted` on `--surface` | 4.92:1 | AA ✓ |
| `--muted` on `--surface-2` | 4.55:1 | AA ✓ (临界) |
| `--faint` on `--bg` | 2.55:1 | 仅装饰 |
| `--faint` on `--surface` | 2.31:1 | 仅装饰 |
| `--accent` on `--bg` | 10.72:1 | AA ✓ |
| `--green` on `--bg` | 11.99:1 | AA ✓ |
| `--red` on `--bg` | 6.49:1 | AA ✓ |
| `--yellow` on `--bg` | 10.97:1 | AA ✓ |

**规则：**
- 承载有意义文字的元素（table headers, section labels, nav group labels）必须使用 `--muted` 或更亮
- `--faint` 仅可用于：separator dots, breadcrumb separators, timestamp suffixes, unit labels, placeholder text
- 如果 `--faint` 要承载可读文字 → 提升为 `--muted`

### 键盘导航

- 所有 `button` 有 `focus-visible` outline
- `tr.clickable` 有 `focus-visible` outline
- Tab 顺序跟随 DOM 结构(无 tabindex 跳跃)

---

## 11. Tab 系统

全站 tab 组件必须视觉统一:

| 变体 | 用途 | 样式规格 |
|------|------|---------|
| `.tab-bar` + `.tab` | 页面内切换(Learning, Agent Activity) | font-size: 0.78rem, weight: 600, border-bottom: 2px |
| `.seg-toggle` + `.seg-btn` | 紧凑内联切换(Feed mode, Chart timeframe) | font-size: 0.7rem, weight: 600, bg: accent-dim active |
| `.lens-tab` / `.command-center-tabs` | 工具面板内切换 | font-size: 0.78rem, weight: 600, border-bottom: 2px |

**铁律：** 不论哪种 tab 变体，font-size 不超过 0.78rem，font-weight 固定 600。

---

## 12. 已删除的死代码 (Cleanup Log)

以下 CSS 类经 grep 确认无任何 .tsx 引用，已安全删除：

| 类名 | 原用途 | 删除 commit |
|------|--------|------------|
| `.cockpit-grid`, `.cockpit-panel` | 旧 2-col 布局 | 6fa0f69 |
| `.cockpit-hero`, `.hero-zone` | 旧 v2 hero section | 6fa0f69 |
| `.hero-label`, `.hero-sub`, `.hero-sparkline` | 旧 hero 子元素 | 6fa0f69 |
| `.hero-controls`, `.hero-pills` | 旧 hero 按钮区 | 6fa0f69 |
| `.hero-value` + modifiers | 旧数值显示(PnLZone 用 .pnl-hero-value) | 6fa0f69 |
| `.cockpit-grid-3` + media | 旧 3-col grid | 6fa0f69 |
| `.market-watch`, `.market-watch-*` | 旧 mini-watchlist | 6fa0f69 |
| `.panel-divider` | 旧面板分割线 | 6fa0f69 |
| `.recent-runs-strip` | 旧 agent run badge strip | 6fa0f69 |
| `.pill-*`, `.ticker-token-pending` | 旧 topbar pill | 早期 commit |
| body grid texture `background-image` | 旧网格纹理 | 6d2f557 |
| `.card-head-space{margin-bottom:0}` (重复定义) | 被 @2120 完整定义覆盖的死规则 | UI review |
| `.signals-row-long/short` + `td` 修饰 | 旧 Signals tab 行着色，0 .tsx 引用 | UI review |
| `.exchange-tiles`, `.exchange-tile`, `.exchange-tile:hover/disabled`, `.exchange-tile-active`, `.exchange-tile-label`, `.exchange-tile-desc` | 交易所 tile 卡片选择器（改为 `<select>` 下拉，7 行共删除） | 3b9ffff |

**验证方法：** `grep -r "class-name" frontend/src/ --include="*.tsx"` — 0 结果即可安全删除。

---

## 13. 变更历史 (Style Changelog)

| 日期 | Commit | 摘要 |
|------|--------|------|
| 2026-06-15 | 88a46be | Command Center radius 统一 (12/14px → var(--r)) |
| 2026-06-15 | 74bf57a | WCAG AA contrast: th/labels faint→muted |
| 2026-06-15 | 33a47d4 | Charts timeframe: dropdown→segmented toggle |
| 2026-06-15 | 311f5c2 | lens/CC tabs 字号对齐 0.78rem |
| 2026-06-15 | de7a9b4 | 微交互扩展: card/row/watchlist press feedback |
| 2026-06-15 | 6fa0f69 | 删除 ~70 行 dead CSS |
| 2026-06-15 | 56ae379 | button press/focus/transition 80ms |
| 2026-06-15 | 6d2f557 | tables/nav/bg/buttons 终极打磨 |
| 2026-06-15 | 08dc3b8 | drawer/tabs/scrollbar/color discipline |
| 2026-06-15 | 675dc80 | controls/metrics/sections 紧凑 |
| 2026-06-15 | 2ac849c | Bloomberg-density pass 1 |
| 2026-06-15 | (pending) | Tick flash + sort indicator + watchlist sparkline |
| 2026-06-17 | (pending) | UI code review: 定义缺失的 conviction/guard 颜色(→tokens)、button:active 对齐§9、10px→var(--r)、删 2 处死 CSS、sp-issue-hint faint→muted(§10)、修复 §7 重号→§17、PnL/TTL/confidence null 守卫 |
| 2026-06-29 | bcdcebd | Settings 搜索框从 shell 外移入 settings-panel 内（§4 容器嵌套规则） |
| 2026-06-29 | 3b9ffff | Universe Board Toggle 加 Row 标签；Exchange Connection tile→select；删除 7 行 exchange-tile* 死 CSS |

---

## 14. 后续优化方向 (Backlog)

1. **响应式断点精修** — 700-900px 区间在 density pass 后可能过挤，需实机验证
2. **暗模式色彩微调** — `--surface-2` hover 在 OLED 上可能与 `--surface` 不可区分
3. **动画 reduced-motion** — 尚未添加 `prefers-reduced-motion` 媒体查询
4. **Command Center terminal 化** — message body 已切 monospace，可进一步加 prompt prefix / syntax highlighting
5. **表格 sticky header** — 长列表 `position: sticky` 列头
6. **Code-split** — JS bundle 815KB，应拆分 lightweight-charts 为 dynamic import
7. **键盘快捷键提示 (Keyboard hints)** — 按钮旁显示 shortcut badge（`⌘K` 等），数字键选 watchlist
8. **侧栏 icon-only 折叠 (Sidebar collapse)** — 折叠为 48px icon rail，给 chart 更多空间
9. **Volume 子图 (Volume sub-pane)** — PriceChart 下方联动 volume 柱图，lightweight-charts 原生支持
10. **PnL 归因热力图 (Heatmap)** — treemap 按持仓权重+PnL着色，多 coin 场景有意义

---

## 15. Tick Flash 动画

价格变化时对价格元素施加 600ms background pulse（绿=涨、红=跌），让用户感知"数据活着"。

**CSS:**
```css
@keyframes tick-up   { from { background: rgba(46,230,168,0.18); } to { background: transparent; } }
@keyframes tick-down { from { background: rgba(255,93,108,0.18); } to { background: transparent; } }
.tick-flash-up   { animation: tick-up 600ms ease-out; }
.tick-flash-down { animation: tick-down 600ms ease-out; }
```

**React 实现模式：**
- `useRef<Record<string, number>>` 存上次价格
- `useEffect` 比较 prev/next，变化时 `setFlashes({[sym]: 'up'|'down'})`
- 650ms `setTimeout` 清空 flash state（略长于动画以避免闪烁中断）

**应用位置：** `MarketTicker.tsx`（topbar）、`Charts.tsx`（watchlist cards）

---

## 16. Watchlist Sparkline

卡片内嵌 24h 趋势线（SVG polyline），让用户无需进入详情即可判断走势形态。

**组件：** `Sparkline.tsx`
- 从 `/api/market/ohlcv?symbol=X&timeframe=1h&limit=24` 获取 close 数据
- 归一化到 SVG 坐标，绿色=涨趋势、红色=跌趋势
- 60s refetch、30s staleTime，不增加 API 压力
- 尺寸：100×20px（watchlist 内），可配置

**设计约束：**
- `opacity: 0.85` 不抢夺价格文字的视觉权重
- `aria-hidden="true"` — 辅助技术不播报装饰图形


---

## 17. 告警与错误提示规范 (Actionable Warnings)

**原则：每条告警必须附带操作指引。** 用户看到 ⚠ 时，下一步应该立刻清晰，不需要去查文档或猜测。

### Health Issue 告警（Status Bar 弹出层）

每个 health issue 在标题行下方显示一行 `hint`，说明原因和操作方法。当前映射（`App.tsx parseIssue()`）：

| Issue key | 操作提示 |
|-----------|---------|
| `market_data_stale` | Feed 每 5 分钟自动刷新；如超 30 分钟未恢复，重启 Gateway |
| `trading_paused` | 点击顶栏 Resume 按钮恢复交易循环 |
| `llm_unavailable` | 检查 Settings → Model Provider 配置，再点 Resume |
| `kill_switch_active` | 进入 Settings → Risk → Kill Switch 手动解除 |
| `agent_failure_ratio` | 查看 Gateway 日志定位失败 Agent |
| `operator stale` | 通常 1–2 分钟自动恢复；若持续则重启 Gateway |

### 实现约束

- `hint` 必须是一句话（<50 字），直接说"你应该做什么"，不解释技术原因
- 不在 hint 里重复 label/value 已有的信息（信息冗余原则）
- `hint` 样式：`.sp-issue-hint`，`color: var(--muted)`，`font-size: 0.62rem`，换行显示在 label 下方
  （告警 hint 是用户必须读的可操作文字 → 受 §10 约束，用 `--muted`(≥4.55:1) 而非 `--faint`(2.31:1，仅装饰)）
- InfraEvent 的 `action` 字段已有类似机制（显示在 event 下方），health issue hint 遵循同一视觉语言

### 新增 health issue 的 checklist

添加新的 health issue 时，必须同步在 `parseIssue()` 里加对应的 `hint`，否则用户只能看到裸 key 字符串和一个 ⚠。

---

## 18. UI 重构教训汇总（2026-06-26 会话）

本节汇总一次密集 UI 重构会话（Risk & Controls / Learning / Overview / 多页一致性）暴露出的**系统性教训**，作为后续 UI 修改的强制参考。每条都带可执行结论与失败实例。

### L1. 验证必看 computed style，不看 className

**规则**：凡涉及 `var(--xxx)` 令牌、tone/variant prop 驱动的 class、骨架/loading 状态，最终判活死的标准必须是 `getComputedStyle(el).{color,backgroundColor,fontFamily,fontSize}` 真实值，而不是元素是否被设置了对应 class。

**为什么**：两类静默缺陷只能靠 computed style 抓到——
1. **未定义 `var()` 静默回退**：CSS 不报错，直接回退到父继承色/初始值。本会话实例：误用 `--text-faint`/`--text-dim`/`--font-mono`/`--accent-glow`（canonical 是 `--faint`/`--muted`/`--mono`），className 看着完美但 computed color 是父亮色。
2. **死 class**：渲染了 `stat-value-green` 但 `.stat-value-green` 规则压根不存在。同次 commit 中 PulseStat 的 tone prop 是 4 个 stat 的"死功能"，全部默认色。

**实施**：
```js
// 验证脚本必须读真值，至少抽样关键元素
const cs = getComputedStyle(el)
return { color: cs.color, bg: cs.backgroundColor, font: cs.fontFamily }
```
单看 `toneClass: 'stat-value-green'` 这种字段是**有害的伪验证**——会让人以为通过实际没过。

### L2. 多 viewport 强制横扫，单一 viewport 不算验收

**规则**：涉及 grid / flex / auto-fit 的改动，验收必须横扫至少 3 个 viewport：
- **主流笔记本** ~1080–1440px
- **窄屏** ~900–1024px（media boundary 附近最易翻车）
- **移动** ~375–480px

**为什么**：本会话用户实测 1082×826 时,5 个 Overview tile 排成 3+1 错位、Risk "Pause All" 文字换行——这些在 1440 测试时全部正常。auto-fit grid 在不同容器宽度下列数会突变。

**预防**：
- 验证流程模板:截图 + computed measurements 在 3 个 viewport 各一遍
- 当某项不易在自动化里看,至少 preview_eval 量 `getBoundingClientRect()` 确认元素排版

### L3. Grid 列数确定时写死 N 列,不用 `auto-fit`

**规则**：当卡片数量是已知固定数(如 4 个 PnL tile),用 `grid-template-columns: repeat(N, minmax(0, 1fr))` + 显式媒体查询 fallback。`auto-fit + minmax(Xpx, 1fr)` 仅在"列数本质不固定"(品类瓦片、chip 列表)时用。

**为什么**：auto-fit 的列数由 `(容器宽 + gap) / minmax 最小值` 决定，容器变窄时可能突然从 4 列掉到 3 列,产生 3+1 留空。本会话 `.pnl-tiles` 用 `minmax(150px, 1fr)`,在 1024px 容器只有 498px 时装不下 4 列(需 516px),掉成 3 列 → 错位。

**写死 + fallback 范例**:
```css
.pnl-tiles {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}
@media (max-width: 700px) { .pnl-tiles { grid-template-columns: repeat(2, 1fr); } }
```

`minmax(0, 1fr)` 而非 `1fr`：防止内容超长撑破 1fr(intrinsic min-content)。

### L4. 两栏垂直空缺 — 主从 Bento + 内容充实

**问题**：1fr/1fr 两栏布局,左右内容长度不等 → 短卡下方大段空白,视觉很丑。`align-items: stretch` 让短卡被拉等高但内部内容靠上(下半空白带边框);`align-items: start` 让短卡保留自然高度(长卡旁边大段无背景空白)。

**两套解法,按场景选**:

**A. 内容充实(Substantiveness Pattern)** — 当短卡是因为"信息密度不够"而短时,把它做"实",**不靠 CSS 兜底**。范例: Risk Active Risk 健康态下原本只显示"No active violations"(60px),改造为含 3 段 section:
- **24h Activity**: 系统在过去 24h 的工作数据(approved/rejected count + 主因 + fills)
- **Headroom**: 当前与每条限额的距离(positions / largest position / daily loss 各自百分比 + bar)
- **Latest Check**: 最近一次风险审计的结果

效果: 60px → 367px,与右列 Live Exposure 293px 几乎齐(差 74px)。**额外收益**: headroom 第一次暴露出"持仓 $153.6 vs cap $26.84"这种隐藏违规,UI 充实成了 honesty 工具。

**B. Bento 主从不对称(Asymmetric Pairing)** — 当数据本身极不均衡(Research notes 20 条 vs Deprecated memories 0 条)无法靠内容充实补齐时,用 `grid-template-columns: 2fr 1fr` 把"长卡群"放主区、"短卡群"放辅区,让"视觉不对称"成为有意的设计语言而非失败的对称。范例: Learning Dreaming 4 卡 → 主区 2fr 堆 Research + Hypotheses,辅区 1fr 堆 Consolidations + Deprecated。

```css
.learn-grid.asym { grid-template-columns: 2fr 1fr; }
.learn-col-main, .learn-col-aside { display: flex; flex-direction: column; gap: var(--space-4); }
@media (max-width: 900px) { .learn-grid.asym { grid-template-columns: 1fr; } }
```

并搭配列表 `max-height: 380px; overflow-y: auto` 防超长卡片把主区拉到 800px+ 让不对称比例失控。

**禁忌**: 不要用 Masonry(Pinterest 砖墙)在仪表盘上 — 阅读顺序变 Z 字形,密度型 UI 大忌。

### L5. CSS `var()` 未定义令牌静默回退陷阱

**Aletheia canonical token 清单**(`:root` 第 9-55 行):

| 概念 | canonical token | ❌ 误用 |
|---|---|---|
| 强度文字色阶 | `--text` / `--muted` / `--faint` | ~~`--text-dim`~~ / ~~`--text-faint`~~ |
| 字体 | `--mono` / `--sans` / `--display` | ~~`--font-mono`~~ |
| 强调色衰减 | `--accent-dim` 或 `rgba(34,211,238,0.10)` 字面值 | ~~`--accent-glow`~~ |

**规则**: 写 `var(--xxx)` 之前先 grep `:root` 确认令牌存在; CI 可以加一个 lint 检查所有 `var(--*)` 引用都能在 `:root` 找到定义。

### L6. 信任路径 onError 必须 inline,辅助路径用 toast

**规则**:
- **信任 / destructive 路径**(kill switch / proposals approve+reject / pause+resume / live unlock):**inline error**,显示在按钮下方,sticky 直到下次操作。**不能用 toast** — toast 自动消失,操作员可能错过失败信号。
- **辅助路径**(start agent / news create / refresh / send chat):**toast** — 不阻塞,辅助通知即可。

**为什么**: trading 系统里"操作员认为急停成功实际请求失败"是最高危的 honesty 缺陷。silent failure 对资金状态不可控。

**inline error CSS pattern** (`.lever-err`):
```css
.lever-err {
  margin-top: var(--space-2);
  padding: 6px 8px;
  background: rgba(255, 93, 108, 0.10);
  border: 1px solid rgba(255, 93, 108, 0.40);
  border-radius: var(--r-sm);
  color: var(--red);
  font-size: 0.74rem;
  word-break: break-word;
}
```
配 `role="alert"` 让 SR 通知。

### L7. 状态推导单一来源,避免 N 面色分歧

**规则**: 同一原始信号在不同 surface 必须显示同一颜色 + 同一标签。任何"surface 各自从原始 status 算 tone"的代码 = 跨页分歧来源。

**实施**: `lib/systemState.ts` 导出 `deriveMasterState({gatewayRunning, killed, paused, riskState, operatorStatus})`,返回 `{word, tone, detail}`。所有 surface(topbar StatusBar / Risk console / Overview System 卡)调用同一函数。

**Priority ladder**(严重性优先):
1. !gatewayRunning → OFFLINE red
2. killed → HALTED red
3. riskState=blocked → BLOCKED red
4. operator=offline → OFFLINE red
5. operator=stale → STALE yellow
6. paused → PAUSED yellow
7. riskState=warning → CAUTION yellow
8. riskState=healthy → OPERATIONAL green
9. (default) → STANDBY blue

### L8. Symbol 显示竞态 — 等数据齐再渲染

**问题**: `fmtSymbol(sym, marketType)` 在 marketType 为 undefined 时按"never guess"原则保留 canonical 斜杠 — 但若 marketTypeBySymbol 来源(positions query)尚未加载,exposure 列表(来自更早 settle 的 portfolio query)会短暂显示带斜杠的错误形式(`ETH/USDT` for a perp,应该 `ETHUSDT`)。

**修法**: 等 `positionsLoading || portfolioLoading` 都为 false 才渲染列表,期间显示 skeleton。**绝不 fallback 猜测**(`marketTypeBySymbol[sym] || 'usdt_futures'` 是有害的,某些 spot 持仓会显错)。

### L9. Tab 系统单一实现 — controlled + uncontrolled 双模式

**规则**: 全站只一份 Tab 实现 = `components/Tabs.tsx`。手写 tab-bar / pred-tabs / lens-tabs 全部应迁移过来。

**双模式**:
- 不传 `value`/`onChange` → 内部 state (`initial` 设置默认)
- 传 `value` + `onChange` → 外部 state(父组件控制切换,适合 deep-link / 跨组件触发)

```tsx
// uncontrolled
<Tabs tabs={[...]} initial="overview" />
// controlled
<Tabs value={tab} onChange={setTab} tabs={[...]} />
```

不允许新增页面手写 tab bar — 即使外观一样也是分歧源(键盘/ARIA/badge 易漂移)。

### L10. Primitive 优先采纳,新页面不重写已有组件

**规则**: 新增 / 重构页面时,**先 grep `components/ui` 和 `components/` 看有没有现成 primitive**:
- 数字 → `fmtNum` / `fmtSigned` / `fmtPct`
- 时间戳 → `fmtTimestamp`
- Symbol → `fmtSymbol(sym, marketType)`
- Enum → `humanize(snake_case)`
- 状态 badge → `<StatusBadge status=… />`
- 状态色文字 → `<StatusText tone=…>…</StatusText>`
- Card → `<Card><CardHeader title=… meta=… />…</Card>`
- Confirm → `useConfirm()` 而非原生 `window.confirm()`
- Toast → `useToast()` 而非 `console.error()`
- Tab → `<Tabs>` 而非手写 tab-bar

**Anti-pattern**: 800+ 行单文件 grab-bag(SerenityLens 原状),内部用 `.toFixed(0)` / 裸 e.symbol / 裸 e.source / 手写 lens-tabs 五件事各违反一条铁律。重整时不需要重写 IA,**只需逐项采纳已有 primitive**,代码量净减少同时一致性归位。

### L11. 构建必走 `npm run build`,不用 `vite build` 兜底

**规则**: 项目 build 命令是 `tsc -b && vite build`,纯 `vite build` 会**跳过类型检查**。`node node_modules/vite/bin/vite.js build` 是用来绕开 npm cache 权限问题的兜底,**仅当 `npm` 本身坏掉时用**,且必须**先单独跑 `tsc -b`**。

**实例**: commit `5e654e2` 引入 `tone="cyan"`(StatusTone 没 cyan)+ `size="sm"`(Button 没 size prop)两处 TS 错误,我用纯 `vite build` 通过了,推上去之后并发会话才用 `tsc -b` 抓到错误并修(`57cc5c8`)。

---

## 19. 验收清单(强制 checklist)

任何 UI 改动 commit 前必须过完这份清单:

- [ ] **tsc**: `cd frontend && node node_modules/typescript/bin/tsc -b` 退出 0
- [ ] **build**: `vite build` 通过
- [ ] **令牌**: `grep "var(--" frontend/src/index.css | grep -oE "var\\(--[a-z0-9-]+\\)" | sort -u` 对每个值能在 `:root` 找到定义
- [ ] **多 viewport**: 至少 3 个尺寸(主流笔记本 / 窄屏 / 移动)实测无错位 + 无溢出
- [ ] **computed style**: 涉及 var/tone 的元素,用 `getComputedStyle()` 抽样确认真实色彩 / 字体不是默认回退
- [ ] **fmtSymbol**: 任何新 symbol 渲染都走 `fmtSymbol(sym, market_type)`
- [ ] **fmt 数字**: 任何新百分数/金额都走 `fmtPct` / `fmtNum` / `fmtSigned`,**不** `.toFixed(0)` / `(x*100).toFixed`
- [ ] **humanize enum**: 任何新机器码 snake_case 显示都走 `humanize()`,**不**裸字符串
- [ ] **status badge**: 任何 status 渲染都走 `<StatusBadge>` 或 `<StatusText>`,**不**自造色映射
- [ ] **onError**: 任何新 `useMutation` 必须有 onError(信任路径 inline / 辅助路径 toast),**不**只 `console.error`
- [ ] **空态文案诚实**: 区分 loading / error / empty 三态,**不**用 empty 文案掩盖 error
- [ ] **inline style**: 仅当值是动态计算结果(`width: ${pct}%`),静态值必须走 CSS class
- [ ] **Toggle in Row**: Settings 页面任何新 Toggle/Switch 都用 `<Row label hint>` 包裹，不裸放 flex div
- [ ] **Selection control**: 互斥选项用 `<select>` in `<Row>`，**不**新增 tile card 网格
- [ ] **容器嵌套**: 任何新增的 Settings 交互组件（搜索、筛选、操作区），确认放进了正确的网格列容器内，不是悬浮在 shell 外部
