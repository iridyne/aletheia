# Aletheia UI Design System

设计语言、布局模式、组件规范与经验教训。目标读者：接手前端任务的 AI Agent 和人类开发者。

---

## 1. 设计原则

1. **信息密度优先于留白** — 交易终端，不是营销站点。
2. **单一强调色** — signal cyan (`--accent`) 是唯一饱和色，用于交互/正向状态。红/黄/绿保留给语义状态。
3. **数据用等宽字体** — 任何会变化或需要比较的值（数字、ID、key、代码）使用 `--mono`。
4. **Secret 只写不读** — 不显示 API key，只显示 `··last4` 或 "set"/"not configured"。
5. **渐进式披露** — 折叠卡片显示状态摘要，展开才编辑，不一次塞满所有字段。
6. **统一网格** — 所有表单卡片用 2 列 form-grid，不自造列数不一的内联 grid。

---

## 2. Token 参考

### 颜色

| Token | Hex | 用途 |
|-------|-----|------|
| `--bg` | `#0a0e12` | 应用背景（近黑，冷色调） |
| `--bg-2` | `#0f141a` | 主内容区（内嵌感） |
| `--surface` | `#141a21` | 卡片、侧边栏、弹窗 |
| `--surface-2` | `#1a212a` | Hover 状态、嵌套面 |
| `--border` | `#232c36` | 默认边框 |
| `--border-2` | `#2e3946` | 较重分隔线（section header） |
| `--text` | `#e6edf3` | 主文本 |
| `--muted` | `#7d8997` | 标签、提示、次要文本 |
| `--faint` | `#4a5562` | 仅装饰（分隔点、时间戳后缀） |
| `--accent` | `#22d3ee` | Signal cyan — 唯一强调色 |
| `--accent-dim` | `#0e7490` | 强调背景、激活态 |
| `--green` | `#2ee6a8` | 成功、已连接、已启用 |
| `--red` | `#ff5d6c` | 错误、危险、已禁用 |
| `--yellow` | `#f5b942` | 警告、待处理 |
| `--blue` | `#4aa3ff` | 信息链接 |

**颜色语义铁律：** 红 = 破坏性/不可撤销/紧急；黄 = 降级/注意；绿 = 正常/active；muted = 未知/不适用。

**`var()` 使用规则：** 写 `var(--xxx)` 前先 grep `:root` 确认令牌存在，否则 CSS 静默回退到继承色，className 看着正确但颜色已错。

| 概念 | Canonical token | ❌ 误用（会静默回退） |
|------|----------------|----------------------|
| 文字色阶 | `--text` / `--muted` / `--faint` | ~~`--text-dim`~~ / ~~`--text-faint`~~ |
| 字体 | `--mono` / `--sans` / `--display` | ~~`--font-mono`~~ |
| 强调色衰减 | `--accent-dim` 或字面量 `rgba(34,211,238,0.10)` | ~~`--accent-glow`~~ |

### 字体

| Token | 字族 | 用途 |
|-------|------|------|
| `--display` | Space Grotesk | Section 标题、品牌 |
| `--sans` | IBM Plex Sans | 正文、标签 |
| `--mono` | IBM Plex Mono | 数据值、输入框、代码 |

所有数字显示启用等宽数字（`font-feature-settings: 'tnum'`）。

### Spacing

所有间距必须引用 CSS var，禁止硬编码 px。

| Token | Value | 典型用途 |
|-------|-------|---------|
| `--space-1` | 4px | 最小间距（icon gap、pill 内距） |
| `--space-2` | 8px | 行内元素 gap、feed-row padding |
| `--space-3` | 12px | card padding-block、section gap |
| `--space-4` | 16px | main 区域 padding-inline、cockpit grid gap |
| `--space-5` | 20px | main padding-inline（宽屏）、sidebar padding |
| `--space-6` | 24px | settings grid column gap |
| `--space-8` | 32px | settings-section bottom margin |

### Border Radius

| Token | Value | 适用范围 |
|-------|-------|---------|
| `var(--r)` | 8px | 卡片、弹窗、dialog、textarea |
| `var(--r-sm)` | 5px | 按钮、badge、input、select、小 chip |
| `999px` | pill | 仅用于 switch track、pill badge |

**铁律：** 禁止写 `border-radius: 10px/12px/14px` 等硬编码值。

---

## 3. 布局模式

### Learning Contribution Observability (target)

The `/learning` target uses the same dense terminal language as Blotter rather than a marketing
dashboard:

```text
five independent status tokens
full-width contribution-by-path ledger
current cohort (7/12) | read-only recommendation (5/12)
full-width append-only verdict history
bounded DetailDrawer drill-down
```

Desktop keeps the 7/5 band; 700-1100px stacks it; below 700px status tokens become two columns,
top tabs scroll horizontally, toolbars wrap and the drawer becomes full width. Use shared `Tabs`,
`DataTable`, `DetailDrawer`, `Card`, `Badge`, `StatusText` and `Meter`; do not fork equivalents.

Every query has distinct loading, genuine-empty, unavailable, stale and partial states. A failed
request must not default to `[]`, `0`, `Never`, or a healthy token. Activity panels carry the label
"Activity, not measured contribution"; portfolio PnL carries "Portfolio context, not Learning
attribution". Future information architecture and response semantics are
proposed in [`learning_observability`](../proposals/learning_observability.md) §8.

### Settings Shell — Section + Row

Settings 页面采用两层嵌套布局：

```
.settings-shell (2-col grid: nav 280px | panel 1fr)
  ├── .settings-nav  (左列：tab 导航)
  └── .settings-panel (右列：内容 + 搜索框)
       ├── <SettingsSearch />   ← 搜索框是面板第一个子元素
       └── <Section>
             └── <Row label hint>  ← 2-col: label/hint | control
```

**容器嵌套铁律：** 任何交互组件（搜索框、筛选器、操作区）的视觉对齐目标是哪列，就放进那列的容器里。不能悬浮在 `.settings-shell` 外部靠 `max-width`/`margin` 模拟对齐。

```tsx
// WRONG — 搜索框在 shell 外，max-width:480px 从最左侧开始，横跨 nav+gap+panel
<div>
  <PageHeader />
  <SettingsSearch />        {/* ← 悬浮在两栏之间，哪里都不靠 */}
  <div className="settings-shell">...</div>
</div>

// CORRECT — 搜索框作为 panel 的第一个子元素
<div>
  <PageHeader />
  <div className="settings-shell">
    <aside className="settings-nav">...</aside>
    <div className="settings-panel">
      <SettingsSearch />    {/* ← 左边缘与内容自然对齐 */}
      ...
    </div>
  </div>
</div>
```

`Section` 提供语义分组（标题 + 描述 + `border-left: 2px solid var(--accent-dim)`）。`Row` 是 2 列 grid（`label/hint | control`），用于 toggle、select、只读值。

### Form Grid (`.form-grid`)

```css
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.7rem; }
.form-grid label { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.75rem; color: var(--muted); }
.form-grid input, .form-grid select { width: 100%; }
```

**规则：**
1. **每个 `<label>` 包裹一个字段** — `<span>` 放标签文字，然后是 `<input>` 或 `<select>`。
2. **输入框填满列宽** — 不要 `width: 90px`，grid 列已定宽，让 input 自然填充。
3. **单位写进 label 文字** — 写 `Default stop-loss (%)`，不在 input 旁放独立 `<span>%</span>`。
4. **全宽字段** — URL、API key 等用 `style={{ gridColumn: '1 / -1' }}`。
5. **label 文字必须是单个 `<span>`** — `flex-direction: column` 让每个子元素独占一行，裸文本 + 子 span 会分裂到两行。

**`flex-direction: column` 陷阱：** `.form-grid label` 内每个直接子元素都是独立行。常见错误：

| 问题 | 修法 |
|------|------|
| 标签文字 + `<span>(count)</span>` → 分两行 | 合并为单个 `<span>Label (count)</span>` |
| Toggle 在 label 内 → checkbox 一行、文字下一行 | 把 Toggle 移到 form-grid 外 |
| badge 在标签文字后 → badge 换行 | 把 badge 信息写入标签字符串 |

**禁止内联 grid：** 不要写 `display: grid; gridTemplateColumns: repeat(auto-fill, minmax(200px, 1fr))` 内联，用 `.form-grid`（始终 2 列）。特殊情况可内联覆盖 `gridTemplateColumns`，但基础 label 样式、gap、input 宽度由 class 免费提供。

### Cards

```
card
  ├── 连接状态行（可选）
  ├── .form-grid（输入字段）
  ├── 操作行（按钮 + 保存反馈）
  └── hint 说明段落
```

### Grid 列数规则

**已知固定列数 → 写死 N 列 + 媒体查询 fallback**，不用 `auto-fit`：

```css
/* CORRECT — 4 个 PnL tile */
.pnl-tiles {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));  /* minmax(0,1fr) 防内容撑破 */
  gap: var(--space-3);
}
@media (max-width: 700px) { .pnl-tiles { grid-template-columns: repeat(2, 1fr); } }
```

`auto-fit + minmax(Xpx, 1fr)` 仅用于"列数本质不固定"的场景（chip 列表、品类瓦片）。容器变窄时 auto-fit 列数会突变（4 列掉 3 列 → 3+1 错位）。

### 两栏垂直对齐

1fr/1fr 两栏左右内容高度不等时，两套解法按场景选：

**A. 内容充实（Substantiveness）** — 短卡信息密度不够导致短，把内容做实，不靠 CSS 兜底。  
**B. 主从不对称（Bento）** — 数据本身极不均衡时，用 `grid-template-columns: 2fr 1fr`，让视觉不对称成为有意的设计语言。

```css
.bento-asym { grid-template-columns: 2fr 1fr; }
@media (max-width: 900px) { .bento-asym { grid-template-columns: 1fr; } }
```

搭配列表 `max-height: 380px; overflow-y: auto` 防超长卡片失控。**禁止 Masonry**（Pinterest 砖墙）— 阅读顺序变 Z 字形，密度型 UI 大忌。

### 响应式布局蓝本（Overview 参考）

Overview 是全站"main + auxiliary column"布局的**规范样板**。新页面凡是要做类似结构，参考 `pages/Overview.tsx` + `.cockpit-main` / `.cockpit-rail`，不要另发明。

**断点尺度（canonical，与 Tailwind 对齐）**

| Name | Value | 触发的布局变化 |
|------|-------|----------------|
| `sm` | 640px | 手机 vs 平板边界；PnL tiles 4 → 2；`.metric-context` 释放 nowrap 换两行 |
| `md` | 768px | Blotter summary strip `flex-wrap: wrap`；PnL tiles 单行密度上限 |
| `lg` | 1024px | **`main + rail` 分栏的唯一门槛**；小于此值一律纵向堆叠 |
| `xl` | 1280px | 舒适宽屏；rail 上限展开到 380px 的 `clamp()` 极值 |

**全站禁止再引入 700 / 900 / 1100 等自定义阈值**。个别历史遗留会随维护逐步收敛到上表；新代码走 sm/md/lg/xl 无例外。（例外：卡片自身跨断点行为不同 → 用**容器查询** `container-type: inline-size` + `@container`，见本节末段。）

**"Main + rail" 主布局 canonical CSS**

```css
.cockpit-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr) clamp(300px, 26vw, 380px);
  gap: var(--space-3);
  align-items: start;
}
.cockpit-rail { display: flex; flex-direction: column; gap: var(--space-3); min-width: 0; }
@media (max-width: 1023px) {
  .cockpit-main { grid-template-columns: 1fr; }
  /* rail 已经是 flex column，无需再改；DOM 顺序 = 阅读顺序 = 堆叠顺序。 */
}
```

关键约束：
- 主列**必须** `minmax(0, 1fr)` —— 缺了它，rail 里长表格/长 ticker 会把主列推出 grid，rail 消失屏幕外（shadcn 官方 footgun 榜首）。
- rail 用 `clamp(300px, 26vw, 380px)`：既不让 rail 在 xl 屏膨胀到不成比例，也不在临界 lg 处崩塌。
- rail 从始至终是 `flex column`，**不要**在窄屏切成 `column-count` 或 `grid`。

**反模式（禁止）**

1. **`column-count` / CSS multicol 做卡片网格** —— 破坏 DOM 阅读顺序，任一可折叠卡片一展开就把兄弟卡片挤到另一列（Aletheia System 卡真实翻车过）。用 `flex` / `grid` 代替。
2. **固定宽度 rail 而 main 未加 `minmax(0, 1fr)`** —— 长内容会把 rail 推出屏幕。
3. **rail 塌陷后再横向切成 grid** —— Bloomberg mobile / TradingView / IBKR 三方一致做法是**纵向堆叠**（`flex column`），不是横排。只有当 rail 卡是同质 + 无可折叠区 + ≤200px 高时才允许 `repeat(auto-fit, minmax(320px, 1fr))`。
4. **主壳（page-level）用 `md` 断点做主/rail 拆分** —— 主列在 `md` 只剩 ~380px，图表不可读。**主壳只在 `lg` 分栏。**
5. **`flex: 1 1 0` 在 column-flex 容器里做子行高** —— `flex-basis: 0` 把高度预算压到 0，子内容溢出/被裁剪。Command Center heartbeat zone 真实翻车：三张 zone 卡被压到 143px 条带。**修法：改用 `display: grid; grid-template-columns: repeat(N, minmax(0, 1fr))`**，grid 子项保留 intrinsic height。

**grid vs flex 决策表**

| 用途 | 选择 | 理由 |
|------|------|------|
| 卡片矩阵、tiles、明确二维 | `grid` | `grid-template-columns` 显式，`minmax(0, 1fr)` 防溢出 |
| 主壳 main + rail | `grid` | 两列宽度差异大，`grid` 精确控制 |
| rail 内部堆叠 | `flex-direction: column` | 一维列表，`grid: 1fr` 是浪费 |
| Strip / toolbar / 行内元素 | `flex` | 天然对齐 + `margin-left: auto` 语义清晰 |
| 断点内响应式列数 | `grid` + `@media` | 明确列数变化优于 `auto-fit` 突变 |

**容器查询（Container Queries）**

当组件在不同父容器下要有不同行为时（例：同一 tile 组件出现在主列宽 700px 的 wide 布局里，也出现在 rail 宽 340px 的塌陷布局里），优先使用容器查询而非全局媒体查询——组件对**自己的宽度**响应，不依赖 viewport：

```css
.some-tile { container-type: inline-size; }
@container (max-width: 300px) { .some-tile .label { font-size: 0.7rem; } }
```

Overview 当前没有此需求，但作为下一批 tile 组件的默认写法记录在此。

**验收标准**

- 在 640 / 768 / 1024 / 1280 四个宽度截图，每张都要"看起来是设计好的"，不是"能读"。
- 断点前后 1px 拖动窗口，不能出现闪烁、跳列、内容抖动。
- rail 卡任一 collapsible 展开时，兄弟卡片位置**不移动**（column-count masonry 会移动 → 禁止的直接检验点）。

---

## 4. 组件模式

### Toggle / Switch

**Rule 1 — Settings 中必须用 `<Row>` 包裹。** 标签在左、Toggle 在右对齐；hint 常驻可见；禁止裸放在 `flex items-center` div 里（裸放没有标签，hint 无处归位）。

```tsx
// WRONG — 无标签，hint 条件显示
<div className="flex items-center gap-3 mb-3">
  <Toggle checked={enabled} onChange={...} />
  {!enabled && <span className="hint">Agents use static list.</span>}
</div>

// CORRECT — Row 提供标签 + hint 常驻
<Row
  label="Enable Universe Scanner"
  hint="When on, dynamically scores USDT pairs each cycle. When off, agents use the static fallback list."
>
  <Toggle checked={enabled} onChange={...} />
</Row>
```

**Rule 2 — 不能放在 `.form-grid label` 里。** `flex-direction: column` 会把 checkbox 和文字拆到两行。

**Rule 3 — Switch 自身即状态，不加 badge 复述。** Switch 的 track 颜色 + thumb 位置已完整表达 on/off，不要再放 badge（`ON`/`ACTIVE`/`DISABLED`）。冗余 badge 带来视觉噪声、维护成本（需与 switch 值同步）、语义歧义（badge 本义是外部状态）。

```tsx
// WRONG — badge 复述 switch 状态
<Toggle checked={enabled} onChange={...} />
<span className="badge badge-green">ACTIVE</span>

// CORRECT — switch 自说明
<Toggle checked={enabled} onChange={...} />

// CORRECT — 仅在关闭时给出后果说明（非状态复述）
<Toggle checked={enabled} onChange={...} />
{!enabled && <span className="hint">Agents use static symbol list when disabled.</span>}
```

`label` prop 标识控制对象（如 "Spot"、"Telegram delivery"），不用于复述开关状态。

### Selection Controls（互斥选项）

| 场景 | 推荐控件 |
|------|---------|
| 选项任意数量 | `<select>` in `<Row>` — 默认首选，不随选项数膨胀 |
| 2-3 个且需高频切换 | `.seg-toggle` segmented button（timeframe、feed mode） |
| 多选项 + 模糊搜索 | Combobox（见下节） |

**禁止 tile card 网格做互斥选择。** 原因：选项增加 → 卡片膨胀、auto-fill 列数不可预测、自定义激活态 CSS 维护成本高、不符行业惯例（VS Code/macOS Settings 都用 select/segmented）。

```tsx
// WRONG
<div className="exchange-tiles">
  {EXCHANGES.map(ex => <button className={`exchange-tile${active===ex.id?' active':''}`}>...</button>)}
</div>

// CORRECT
<Row label="Primary exchange" hint="Atomically updates data source, execution mode, and risk allowlist.">
  <select value={active} onChange={e => handleSelect(e.target.value)} className="w-auto">
    {EXCHANGES.map(ex => <option key={ex.id} value={ex.id}>{ex.label}</option>)}
  </select>
</Row>
```

**`confirm()` 在 controlled `<select>` 的 `onChange` 里有效。** React controlled select（`value={active}`）在 `confirm()` 返回 false 时，因 `active` 未更新，下次 render 自动回退，无需额外 reset 逻辑。

### Combobox（输入 + 过滤建议）

当字段既有预设选项又允许自定义输入时，用 combobox，不用带 "(custom)" fallback 的 `<select>`。

```tsx
<label style={{ position: 'relative' }}>
  <span>Label</span>
  <input onFocus={showAll} onChange={filter} onBlur={hide} />
  {showSuggestions && (
    <div style={{ position: 'absolute', top: '100%', ... }}>
      {items.map(item => <div onMouseDown={select}>{item}</div>)}
    </div>
  )}
</label>
```

关键行为：focus（无输入）→ 显示全列表；输入 → 过滤；点建议 → 设值关闭；`onBlur` 延迟 150ms 让 `onMouseDown` 先触发。

### Tab 系统

全站 tab 组件统一使用 `components/Tabs.tsx`，**不允许手写 tab bar**（即使外观一样也是分歧源：键盘/ARIA/badge 易漂移）。

| 变体 | 用途 | 规格 |
|------|------|------|
| `.tab-bar` + `.tab` | 页面内切换 | font-size 0.78rem, weight 600, border-bottom 2px |
| `.seg-toggle` + `.seg-btn` | 紧凑内联切换 | font-size 0.7rem, weight 600, bg: accent-dim active |

**铁律：** font-size 不超过 0.78rem，font-weight 固定 600。

双模式：不传 `value`/`onChange` → 内部 state；传 `value` + `onChange` → 外部 controlled（适合 deep-link / 跨组件触发）。

### Badges

状态指示器：`.badge`、`.badge-green`、`.badge-red`、`.badge-yellow` — 小圆角、大写。  
不放在 form-grid label 里作为裸文字的兄弟节点（split-line bug）。

### Connection Status

`<ConnectionStatus label="..." state="green|red|yellow|muted" text="..." />` — 圆点 + 等宽状态文字。用于凭证/连接卡片顶部。

### Blotter primitives (`.bl-*`) — 交易台账工作站专用

引入自 2026-07-18 Blotter 迁移（`pages/Blotter.tsx`；参考 Bloomberg EMSX + IBKR TWS）。这套 primitives 只在决策工作站上下文使用，不应扩散到其他页面。CSS 定义在 `frontend/src/index.css`（`bl-token-strip` 段起）。

| Class | 用途 | 关键 tokens |
|---|---|---|
| `.bl-token-strip` | 顶部 5 列状态计数条，`grid-template-columns: repeat(5, 1fr)`。窄屏（<1100px）3 列，<640px 2 列 | `--space-2` gap |
| `.bl-token` + `.bl-token-{muted\|accent\|green\|yellow\|red}` | 单个状态方块。大写小 label + 大号 tabular-nums 数字。tone 只染数字，不染整块 —— 保持扫视稳定 | `--r`, 1.5rem count |
| `.bl-mid-band` | 中段两栏 `grid-template-columns: 7fr 5fr`，`<1100px` 折叠单列 | — |
| `.bl-panel` + `.bl-panel-ledger` (`margin-bottom` 单独设) | 面板容器，等同 `.rail-card` 视觉重量 | `--surface` |
| `.bl-panel-head` | 面板头部：`h3` + 副标题；`border-bottom` 分隔 | 0.9rem h3 |
| `.bl-side` + `.bl-side-{l\|s}` (span 包裹) | L/S 方向 chip。**必须**放在 `<td class="bl-cell-side">` 内的 `<span>` 里，不要直接给 td 加 `.bl-side`（会破坏 table-cell 布局） | mono, 0.72rem |
| `.bl-cand-table` | Candidates 表格，行高 ~28px、cell padding 6/10 | — |
| `.bl-gate` + `.bl-gate-{ok\|block}` | Pre-trade check chip。PASS 用紧凑绿 chip；block 用红色正文（无 badge） + `.bl-gate-tip` hover popover | mono 0.7rem (ok) / sans 0.78rem (block) |
| `.bl-signoff-list` / `.bl-signoff-row` (+ `-traceable`) | Awaiting Sign-off 每行 3 行布局：symbol/meta/actions。整行 click → `DecisionTraceDrawer` | `--surface-2` |
| `.bl-toolbar` + `.bl-facet` / `.bl-side-btn` / `.bl-time-btn` / `.bl-reset-btn` | Ledger toolbar：Search / 6 outcome facet chips / L-S 切换 / 1h-6h-24h-7D 切换 / Reset | uppercase label 0.66rem |
| `.bl-outcome-{green\|yellow\|red\|muted}` | Ledger 行 outcome chip。tone 语义: green=Filled, red=UserReject/AutoReject, yellow=Watch/RejectUnknown, muted=OpSkip | — |
| `.bl-cand-skeleton{-row,-diag,-footer}` | 冷启动 shimmer 骨架屏。`@keyframes bl-shimmer` + `prefers-reduced-motion` 保护 | — |
| `.ov-blotter-strip` | Overview 上的 1 行 Blotter 摘要（不在 Blotter 页面内，故用 `.ov-` 前缀）。`onMouseEnter` 触发 Blotter queryKey prefetch | — |

**反模式**：不要给同一 tone 用完整方块底色（会让扫视疲劳）——只染数字/文字/边框。

---

## 5. 规则 & 反模式

### 信息冗余（信息密度）

每个视觉元素必须"付得起房租"。参考 Nielsen H8（极简设计）、Tufte 数据墨水比。

| 冗余类型 | 示例 | 判断标准 |
|----------|------|---------|
| 双重编码 | 绿点 + 文字 "active" | 去掉文字色盲用户无法理解 → 保留；否则去掉重复通道 |
| 方向双重编码 | `▲+12.5` / `▼-3.2`（符号 + 三角） | `+`/`-` 已表达方向；`▲▼` 是重复通道，且违反 WCAG 1.4.1 非颜色辅助通道独立性。**去掉三角，保留 `±` 号。** |
| 标签回声 | Section "Open Positions" + badge "3 open" | 去掉 "open" 信息仍完整 → 只显示数字 |
| 控件 + 状态复述 | Switch + badge "ON" | Switch 视觉即状态 → 永远不加状态 badge |
| 区域内重复 | source bar 已有数据来源，chart controls 又重复 | 同 viewport 内同一信息出现两次 → 保留信息层级更高的 |
| Context 回声 | label="Today PnL" + context="today" | context 应补充 label 未覆盖的维度（%、数据源、时间范围） |

**保留双通道的合理场景：** 数据表格 StatusBadge（每个 cell 必须独立可读）、Dashboard 圆点 + 内容值（圆点给快扫，文字给详情）、严肃操作确认区（冗余是刻意的摩擦）。

审计 checklist：①同区域是否有两个元素传达完全相同的事实？②去掉其中一个，用户能否 <2s 内获取信息？③保留的那个对色盲/低视力用户仍可达？

### 表格内容策略：横向滚动 > 单元格换行

**`DataTable` 的 `noWrap` 默认值是 `true`（全局）。** 不需要显式传 prop；仅当某列确实是段落文本且折行可读性更高时，才显式传 `noWrap={false}` opt-out。

**为什么 nowrap + 横向滚动是默认正确答案：**

| 场景 | 换行（旧默认） | nowrap + 横滚（新默认） |
|------|--------------|----------------------|
| 移动端 9 列数据表 | 行高膨胀 3-5 倍，视觉噪音极大 | 左右滑，每行一个高度，整洁 |
| 宽屏 3 列表 | 文字撑满，但空白列 | 列缩到 natural width，浪费感可接受 |
| 数字/日期/代码列 | 换行破坏对齐感 | 单行，自然对齐 |
| 自由文本描述列 | 折行可读 | 横滚可读，但稍难 → 这类表 opt-out |

**实现机制**（`DataTable.tsx`）：
```tsx
// 默认即 nowrap
<div className={noWrap ? 'table-wrap table-wrap-nowrap' : 'table-wrap'}>
```
```css
.table-wrap { overflow-x: auto; min-width: 0; }
.table-wrap-nowrap th,
.table-wrap-nowrap td { white-space: nowrap; }
```

**scrollbar 暗色主题：**
```css
html {
  scrollbar-width: thin;
  scrollbar-color: var(--border-2) transparent;
  color-scheme: dark;   /* 让浏览器原生 scrollbar 跟随暗色主题 */
}
```
缺少 `color-scheme: dark` 时，浏览器默认渲染浅色滚动条，在暗色 UI 里突兀（charts 水平滚动条真实翻车过）。

### 错误 & 反馈处理

**位置规则：** 操作结果反馈（错误/成功）显示在触发按钮**下方独立 block**，不与按钮同行右侧并排（会挤压按钮、触发 flex-wrap 换行不可预期）。

```tsx
// WRONG
<div className="flex gap-2 items-center">
  <button onClick={save}>Save</button>
  {error && <span style={{ color: 'var(--red)' }}>{error}</span>}
</div>

// CORRECT
<div className="flex gap-2">
  <button onClick={save}>Save</button>
</div>
{error && <div className="settings-save-msg err mt-2">{error}</div>}
```

`SaveMsg` 组件（`SettingsSection.tsx`）已渲染为 `<div>` block，放在按钮行**之后**即可。

**inline vs toast 选择规则：**
- **信任 / destructive 路径**（kill switch、proposals approve/reject、pause/resume、live unlock）→ **inline error**，显示在按钮下方，sticky 直到下次操作。**不能用 toast** — toast 自动消失，操作员可能错过失败信号。
- **辅助路径**（start agent、news create、refresh、send chat）→ **toast** — 不阻塞，辅助通知即可。

Command Palette 中的 mutation command 也必须返回并 `await` 其 Promise；等待期间禁止重复触发，成功后才关闭
palette 并 invalidate 对应事实 query，失败则保持 palette 打开并显示带命令名的 sticky inline error。禁止用关闭
palette 或丢弃 Promise 的方式暗示命令已经生效。

`.lever-err` CSS pattern（信任路径用）：
```css
.lever-err {
  margin-top: var(--space-2); padding: 6px 8px;
  background: rgba(255,93,108,0.10); border: 1px solid rgba(255,93,108,0.40);
  border-radius: var(--r-sm); color: var(--red); font-size: 0.74rem;
  word-break: break-word;
}
```
配 `role="alert"` 让 screen reader 通知。

### 状态推导单一来源

同一原始信号在不同 surface 必须显示同一颜色 + 同一标签。任何"surface 各自从原始 status 算 tone"的代码 = 跨页分歧来源。

`lib/systemState.ts` 导出 `deriveMasterState()` 统一推导，所有 surface（topbar StatusBar / Risk console / Overview System 卡）调用同一函数。

优先级（严重性降序）：!gatewayRunning → OFFLINE red → killed → HALTED red → riskState=blocked → BLOCKED red → operator=offline → OFFLINE red → operator=stale → STALE yellow → paused → PAUSED yellow → riskState=warning → CAUTION yellow → riskState=healthy → OPERATIONAL green → default → STANDBY blue。

### Unicode 符号视觉等宽

行内 chevron / arrow 类 Unicode 符号有"小号变体"和"全号变体"，大小差距肉眼明显，**同一行内混用会导致视觉不整齐**。

| ❌ 小号（勿用于 UI 主元素） | ✓ 全号 |
|--------------------------|--------|
| `▸` U+25B8 SMALL RIGHT-POINTING TRIANGLE | `▶` U+25B6 |
| `▾` U+25BE SMALL DOWN-POINTING TRIANGLE | `▼` U+25BC |
| `›` U+203A SINGLE RIGHT-POINTING ANGLE QUOTATION MARK | `→` U+2192 |

**规则：** chevron / expand indicator 选 `▶`/`▼`；action arrow 选 `→`；同一行里的符号高度应一致。小号变体（25B8/25BE）只用于角标、下标等刻意降调的场景。

### Settings 敏感字段展示

API key 等凭证字段**禁止用 badge 标注"已配置"**（颜色 badge 像状态，不像值，且会与其他 badge 争抢视线）。

**正确做法：** 用 `<input type="password" placeholder="•••••• configured (ends ··xxxx)" readOnly />` 展示掩码占位符，末 4 位帮助用户核对版本。未配置时 placeholder 为 "Not configured"。

```tsx
// CORRECT
<input
  type="password"
  readOnly
  value=""
  placeholder={apiKey ? `•••••• configured (ends ··${apiKey.slice(-4)})` : 'Not configured'}
/>
```

长表单（如 Inference Admission 参数组）用 `collapsible` 折叠——`useState(false)` toggle，点击标题展开，默认收起，防止界面在未配置该功能时暴露一堆空字段。

### Symbol 显示竞态

`fmtSymbol(sym, marketType)` 在 `marketType` 为 undefined 时保留 canonical 斜杠。若 `marketTypeBySymbol`（来自 positions query）尚未加载，exposure 列表会短暂显示错误形式。

**修法：** 等 `positionsLoading || portfolioLoading` 都为 false 才渲染列表，期间显示 skeleton。绝不 fallback 猜测（`|| 'usdt_futures'` 对 spot 持仓会显错）。

### Primitive 优先采纳

新增/重构页面时，先 grep `components/ui` 和 `components/` 确认有没有现成 primitive：

| 需求 | Primitive |
|------|-----------|
| 数字（一般） | `fmtNum` / `fmtSigned` / `fmtPct` |
| 数字（空间受限 tile，~7 字符预算） | `fmtCompact` / `fmtCompactSigned`（SI 后缀 k/M/B/T，sub-1 三位有效数字，下标零压缩）；**不要**在 tile 上用 `toLocaleString` 千分位 |
| 时间戳 | `fmtTimestamp` |
| Symbol | `fmtSymbol(sym, marketType)` |
| snake_case enum | `humanize()` |
| 状态 badge | `<StatusBadge status=…>` |
| 状态色文字 | `<StatusText tone=…>` |
| Card | `<Card><CardHeader title=… meta=… />…</Card>` |
| Confirm | `useConfirm()`（非原生 `window.confirm()`） |
| Toast | `useToast()`（非 `console.error()`） |
| Tab | `<Tabs>`（非手写 tab bar） |
| Blotter 决策工作站 | `.bl-*` 类族（见 §4 Blotter primitives），不要重造 status token 条 / faceted filter chip / candidate 表 |
| Blotter 行点击 → trace | `<DecisionTraceDrawer opportunityId=… />`（`components/DecisionTraceDrawer.tsx`，Overview + Blotter 共用） |

不要用 `.toFixed(0)` / 裸 `e.symbol` / 裸 `e.source` / 手写 lens-tabs — 每一项都违反一条铁律。

### 验证：Computed Style vs className

涉及 `var(--xxx)` 令牌、tone/variant prop 驱动的 class，最终判活死的标准必须是 `getComputedStyle(el).color` / `.backgroundColor` 真实值，不是元素是否有对应 className。

两类静默缺陷只有 computed style 能抓：①未定义 `var()` 静默回退（CSS 不报错，直接回退到继承色）；②死 class（`.stat-value-green` 规则根本不存在）。

单看 `toneClass: 'stat-value-green'` 这类字段是**有害的伪验证**。

### 多 viewport 验收

涉及 grid/flex/auto-fit 的改动，验收必须覆盖至少 3 个尺寸：
- **主流笔记本** ~1080–1440px
- **窄屏** ~900–1024px（media boundary 附近最易翻车）
- **移动** ~375–480px

auto-fit grid 在容器变窄时列数会突变（4 列→3 列→3+1 错位），单一 viewport 不算验收。

---

## 6. 动效 & 视觉细节

### 交互反馈

```css
button { transition: filter 80ms, transform 60ms; }
button:hover { filter: brightness(1.12); }
button:active:not(:disabled) { transform: scale(0.97); filter: brightness(0.95); }
button:focus-visible { outline: 1px solid var(--accent); outline-offset: 2px; }
.card.clickable:hover { border-color: var(--border-2); background: var(--surface-2); }
.card.clickable:active { transform: scale(0.995); }
.feed-row:hover { background: var(--surface-2); }
.feed-row:active { background: var(--bg); }
```

统一规律：hover → lighten（surface-2）；active → darken（bg）或 scale（卡片类 scale，行类 darken）；transition 均 ≤100ms。

### 无障碍 (Accessibility)

**对比度 WCAG AA（最低要求）：**

| 前景 → 背景 | 比值 | 判定 |
|-------------|------|------|
| `--text` on `--bg` | 16.39:1 | AA ✓ |
| `--muted` on `--bg` | 5.44:1 | AA ✓ |
| `--muted` on `--surface-2` | 4.55:1 | AA ✓ 临界 |
| `--faint` on `--bg` | 2.55:1 | **仅装饰** |
| `--accent` on `--bg` | 10.72:1 | AA ✓ |

`--faint` 只能用于装饰元素（分隔点、时间戳后缀、placeholder）。承载可读文字必须升到 `--muted`。

键盘导航：所有 `button` / `tr.clickable` 有 `focus-visible` outline；Tab 顺序跟随 DOM（无 tabindex 跳跃）。

### 告警规范 (Actionable Warnings)

**原则：每条告警必须附带操作指引。** `hint` 一句话（<50 字），直接说"你应该做什么"，不解释技术原因。

| Issue key | 操作提示 |
|-----------|---------|
| `market_data_stale` | Feed 每 5 分钟自动刷新；超 30 分钟未恢复则重启 Gateway |
| `trading_paused` | 点击顶栏 Resume 按钮恢复交易循环 |
| `llm_unavailable` | 检查 Settings → Model Provider 配置，再点 Resume |
| `kill_switch_active` | 进入 Settings → Risk → Kill Switch 手动解除 |
| `agent_failure_ratio` | 查看 Gateway 日志定位失败 Agent |
| `operator stale` | 通常 1–2 分钟自动恢复；若持续则重启 Gateway |

添加新 health issue 时，必须同步在 `App.tsx parseIssue()` 里加对应 `hint`。`hint` 样式：`.sp-issue-hint`，`color: var(--muted)`，`font-size: 0.62rem`，换行显示在 label 下方。

### Tick Flash 动画

价格变化时施加 600ms background pulse（绿=涨、红=跌）：

```css
@keyframes tick-up   { from { background: rgba(46,230,168,0.18); } to { background: transparent; } }
@keyframes tick-down { from { background: rgba(255,93,108,0.18); } to { background: transparent; } }
.tick-flash-up   { animation: tick-up 600ms ease-out; }
.tick-flash-down { animation: tick-down 600ms ease-out; }
```

React：`useRef<Record<string, number>>` 存上次价格；`useEffect` 比较 prev/next；650ms `setTimeout` 清空 flash state。应用位置：`MarketTicker.tsx`（topbar）、`Charts.tsx`（watchlist cards）。

### Watchlist Sparkline

`Sparkline.tsx` — 卡片内嵌 24h 趋势线（SVG polyline），从 `/api/market/ohlcv?symbol=X&timeframe=1h&limit=24` 拉 close 数据，归一化到 SVG 坐标。尺寸 100×20px，`opacity: 0.85`，`aria-hidden="true"`，60s refetch。

---

## 7. 验收清单（强制）

UI 改动 commit 前必须过完：

- [ ] **tsc**: `cd frontend && node node_modules/typescript/bin/tsc -b` 退出 0（`npm run build` = `tsc -b && vite build`，纯 `vite build` 跳过类型检查）
- [ ] **build**: `vite build` 通过
- [ ] **令牌**: 新写的 `var(--xxx)` 在 `:root` 有定义（grep 确认）
- [ ] **多 viewport**: 至少 3 个尺寸（主流笔记本 / 窄屏 / 移动）无错位无溢出
- [ ] **computed style**: 涉及 var/tone 的元素，用 `getComputedStyle()` 抽样确认颜色/字体非默认回退
- [ ] **fmtSymbol**: 新 symbol 渲染都走 `fmtSymbol(sym, market_type)`
- [ ] **fmt 数字**: 新百分数/金额走 `fmtPct` / `fmtNum` / `fmtSigned`，不用 `.toFixed()`；空间受限 tile（≤~8 字符宽）走 `fmtCompact` / `fmtCompactSigned`，不用千分位格式
- [ ] **humanize enum**: 新 snake_case 值显示走 `humanize()`
- [ ] **status badge**: 新 status 渲染走 `<StatusBadge>` 或 `<StatusText>`，不自造色映射
- [ ] **onError**: 新 `useMutation` 有 onError（信任路径 inline / 辅助路径 toast）
- [ ] **空态诚实**: 区分 loading / error / empty 三态，不用 empty 文案掩盖 error
- [ ] **inline style**: 仅动态计算值（`width: ${pct}%`），静态值走 CSS class
- [ ] **Toggle in Row**: Settings 中新 Toggle 用 `<Row label hint>` 包裹，不裸放 flex div
- [ ] **Selection control**: 互斥选项用 `<select>` in `<Row>`，不新增 tile card 网格
- [ ] **容器嵌套**: 新 Settings 交互组件放进正确的网格列容器内，不悬浮在 shell 外部

---

## 附录 A：死代码清理记录

经 `grep -r "class-name" frontend/src/ --include="*.tsx"` 确认无引用后删除：

| 类名 | 原用途 | 删除 commit |
|------|--------|------------|
| `.cockpit-grid`, `.cockpit-panel` | 旧 2-col 布局 | 6fa0f69 |
| `.cockpit-hero`, `.hero-zone`, `.hero-label` 等 | 旧 v2 hero section | 6fa0f69 |
| `.cockpit-grid-3` + media | 旧 3-col grid | 6fa0f69 |
| `.market-watch`, `.market-watch-*` | 旧 mini-watchlist | 6fa0f69 |
| `.panel-divider`, `.recent-runs-strip` | 旧面板分割线/badge strip | 6fa0f69 |
| `.pill-*`, `.ticker-token-pending` | 旧 topbar pill | 早期 commit |
| body grid texture `background-image` | 旧网格纹理 | 6d2f557 |
| `.card-head-space{margin-bottom:0}` (重复定义) | 被 @2120 完整定义覆盖 | UI review |
| `.signals-row-long/short` + `td` 修饰 | 旧 Signals tab 行着色 | UI review |
| `.exchange-tiles`, `.exchange-tile*`（7 行） | 交易所 tile 卡片（改为 select 下拉） | 3b9ffff |
| `.entry-*`, `.human-approval-*`, `.funnel-*`, `.approval-list`, `.desk-*`, `.proposal-*`, `.dir-*`（~241 行） | Overview 决策卡 → 迁移至 Blotter | Blotter migration |

---

## 附录 B：变更历史

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
| 2026-06-26 | (多次) | UI 重构：风险控制 / Learning / Overview / 多页一致性 |
| 2026-06-29 | bcdcebd | Settings 搜索框从 shell 外移入 panel 内 |
| 2026-06-29 | 3b9ffff | Universe Board Toggle 加 Row 标签；Exchange Connection tile→select；删 exchange-tile* CSS |
| 2026-07-18 | (多次) | Blotter 工作站：Overview 决策卡迁移 + Proposals 合并；`fmtCompact` PnL tile 格式化；响应式布局精修（cockpit-rail 禁 column-count → grid/flex）；Settings API key 掩码 + 折叠；cc-heartbeat-zone flex→grid 修复坍塌；全局暗色 scrollbar（color-scheme:dark）；全站表格 `noWrap` 默认开启 |

---

## 附录 C：优化 Backlog

1. **响应式断点精修** — 700-900px 区间在 density pass 后可能过挤，需实机验证
2. **暗模式色彩微调** — `--surface-2` hover 在 OLED 上可能与 `--surface` 不可区分
3. **动画 reduced-motion** — 尚未添加 `prefers-reduced-motion` 媒体查询
4. **Command Center terminal 化** — message body 已切 monospace，可进一步加 prompt prefix / syntax highlighting
5. **表格 sticky header** — 长列表 `position: sticky` 列头
6. **Code-split** — JS bundle ~900KB，应拆分 lightweight-charts 为 dynamic import
7. **键盘快捷键提示** — 按钮旁显示 shortcut badge（`⌘K` 等）
8. **侧栏 icon-only 折叠** — 折叠为 48px icon rail，给 chart 更多空间
9. **Volume 子图** — PriceChart 下方联动 volume 柱图，lightweight-charts 原生支持
10. **PnL 归因热力图** — treemap 按持仓权重 + PnL 着色
