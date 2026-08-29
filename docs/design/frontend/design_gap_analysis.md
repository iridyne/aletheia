# 设计差距分析：Aletheia vs 顶级 SaaS 控制台

> 参考对象：akrouter.com（公开首页与模型市场）及同类顶级 developer console 设计语言
> （Vercel / Linear / Resend / Clerk / Neon / Stripe Dashboard）
> 写于 2026-08-29

---

## 一、参考站点 akrouter 的设计语言提取

从公开页面截图中提炼的核心语言：

### 视觉特征

| 维度 | akrouter 做法 |
|---|---|
| 背景层次 | 纯黑底（#0a0a0a 级）+ 卡片 #111/113 的极薄高差，层次感来自**内容密度**而非色阶堆叠 |
| 排版层级 | 大标题用 **无衬线 + 超重字重（700–800）**，正文行高宽松（1.6–1.7）；数据值字号与标题比接近 1:3 |
| 强调色 | 单一主色（蓝/青），**只在主操作按钮和 CTA 上完全填充**；其他地方用极低透明度背景（5–8%）+ 轻描边 |
| 数据表格 | 行间距宽松（约 56px 行高），每行分隔仅靠颜色区分悬浮态，**不用横线分隔**；数据对齐严格右对齐 |
| Badge/chip | 极小字重、极低背景（3–6% opacity）、圆角趋向于**全 pill（999px）**，而非方形 chip |
| 状态指示 | 绿点 = online/healthy，带轻微 glow；搭配简短状态词，无冗余说明 |
| 实时数据 | 进度/成功率用 **细条进度条（高度 4–6px）**，视觉密度低但信息承载高 |
| 负空间 | 卡片之间有足够的 gap（约 16–20px），**内部 padding 慷慨**（约 24px）；不堆叠内容 |
| 图标 | 极简线框 SVG，1–1.5px stroke，与文字同色（不单独着色） |

### 文案/信息架构特征

- 页面顶部 tagline 直接是结论（"多条渠道自动兜底，坏一条你几乎无感"），不解释技术实现
- 数据卡片格式严格：**大数字 + 小标签**，无多余上下文；标签全大写小字
- 表格列设计：只保留**决策必需列**，不展示 ID、创建时间等操作维度数据

---

## 二、Aletheia 与顶级参考的差距清单

差距按视觉影响力排序，从最大到最小。

---

### 差距 1 ★★★★★ — 字体视觉层次过于平坦

**现状**

页面内字号集中在 0.68–0.85rem 的窄区间内，page title 用 1.25rem (Space Grotesk 600)，但 card 内 h3 是 0.78rem uppercase，metric 值是 0.72–0.82rem mono——三级内容的字号差异只有约 0.1rem，视觉层次基本感知不到。

```
page title   → 1.25rem / Space Grotesk 700    ✓
card h3      → 0.78rem / IBM Plex Sans 600     ← 与正文区分不明显
metric value → 1.5rem  / IBM Plex Mono 700     ✓（master state 等有正确）
table cell   → 0.8rem  / IBM Plex Mono 400     ← 太接近 h3
label        → 0.62–0.72rem uppercase           ← 正确
```

**参考做法**

akrouter 首页：metric value（32/49/393/99.6%）约为 2.5–3rem，是标签的 4–5 倍。
Stripe Dashboard：KPI 值 2rem+，紧接 0.75rem label，视觉冲击力来自这个比率。

**修改方向**

- 所有核心指标值（PnL 金额、仓位数、成功率等）统一用 `--fs-2xl`（1.25rem）或更大，搭配 font-weight 700
- card 内 h3 升到 `--fs-base`（0.8125rem）并去掉 uppercase + letter-spacing（uppercase 不是标题，是标签）
- `MetricTile`、`HoverTile` 中数字字号目前约 0.85–1rem，至少升到 1.25rem

---

### 差距 2 ★★★★★ — 卡片内容密度失控：信息层级混乱

**现状**

大量卡片将多个无关信息平铺在同一密度层：标签 / 值 / 来源说明 / 操作按钮 / hint 文字共存，每个元素字号差距极小，操作者视线没有自然落点。

典型案例：Overview 的 status-popover（系统状态弹窗）把 10+ 行信息塞进一个列表，视觉权重完全均匀。

**参考做法**

akrouter 模型表格：每行严格三层——模型名（大/粗）+ 渠道描述（小/灰）+ 实时数据（右对齐/等宽）。
Linear 的 issue 列表：title 大，metadata 极小且隐藏在 hover 后，首屏不展示噪声数据。

**修改方向**

- 每张卡片明确一个"首屏核心值"，其他信息收入 context/detail 层
- status-popover 内的 debug 行（已有 opacity: 0.45）可以进一步：默认 `display: none`，展开才显示
- 表格次要列（uPnL source、pnl_status 等）默认隐藏，列 toggle 开关已有基础设施

---

### 差距 3 ★★★★☆ — Badge 和 chip 语义过于均质

**现状**

`.badge` 系列用方形 chip（`--r-sm` = 5px）+ 细边框，所有类型（状态/方向/模式/来源）视觉权重相同，只靠颜色区分。一行表格里出现 4–5 个同样大小的 badge 时，视觉噪音极大。

**参考做法**

akrouter 模型市场中的 badge：
- 稳定 / 低价 / 高速 这三个是真正的 **pill（border-radius: 999px）**，背景是极浅的语义色（约 8%）
- 在线状态点不用 badge，直接用 4–6px 的圆点（带 glow）
- 没有"多颜色 badge 扎堆"的情况——一行最多 1 个核心状态 badge

**修改方向**

- 方向 badge（Long/Short）→ pill + 极浅背景（已有 `.badge-green` / `.badge-red`，但 border-radius 不够）
- 状态类 badge（filled / pending / error）→ 去掉 border，只用色块背景 + 文字
- 来源类标签（paper_internal / live_limited）→ 降级为纯文字 muted，不用 badge 包裹

---

### 差距 4 ★★★★☆ — 表格行高太紧，视线无法停留

**现状**

DataTable 的表格行由 `padding: 6px 10px` 控制（`.bl-cand-table`），普通表格约 `padding: 8px 12px`。行高约 32–36px。

**参考做法**

akrouter 模型市场表格行高约 52–56px。Vercel deployments 列表约 48px。
这不是浪费空间——较宽松的行高让视线能在行内横向扫视，数据更易读。

**修改方向**

- 主要 blotter 表格（Orders/Positions）行高升至 `min-height: 44px`，通过 `padding: 10px 12px` 实现
- bl-cand-table（候选表格）保持现有紧凑（决策流需要密度），但至少 36px

---

### 差距 5 ★★★★☆ — 进度条 / 数据 bar 实现不统一

**现状**

全站至少有 5 种不同的进度条实现：

```
scorecard-bar    → height: 6px, border-radius: 2px
lf-bar-track     → height: 8px, border-radius: 4px
conviction-bar   → height: 6px, border-radius: 3px
exposure-bar     → height: 6px, border-radius: 3px
lf-bar-track (learning) → height: 8px
```

每一种都单独写了 CSS，没有统一的 `data-bar` primitive。

**参考做法**

akrouter 成功率列：统一的窄 bar（4px 高），全部用同一 CSS 类，通过 `--bar-fill: X%` CSS 变量控制填充。

**修改方向**

抽取 `.data-bar` primitive：
```css
.data-bar { height: 4px; background: var(--surface-2); border-radius: var(--r-sm); overflow: hidden; }
.data-bar-fill { height: 100%; border-radius: var(--r-sm); transition: width 0.3s; }
```
所有现有的 bar 类改为使用这个 primitive，差异通过 `max-width` 或 wrapper class 控制。

---

### 差距 6 ★★★☆☆ — 空状态和加载态缺乏品质感

**现状**

空状态通常是一段 `<p className="text-muted text-sm">No orders yet.</p>`，无图标、无具体原因、无动作引导。加载态是 `Loading…` 纯文字或骨架屏（`.route-skeleton`），骨架屏只在路由级别有，组件级别没有。

**参考做法**

Clerk / Resend 的空状态：小图标 + 一句话说明 + 主操作按钮（即使操作在其他地方也 deep-link 过去）。
Neon 的加载态：组件级骨架屏，保持与真实内容相同的几何形状。

**修改方向**

- `DataTable` 的 `emptyMessage` prop 升级为接受 `{ icon, title, hint, action }` 对象
- 组件级加载态（`state="loading"`）显示与行数量匹配的骨架行，而非 spinner 或纯文字

---

### 差距 7 ★★★☆☆ — 交互反馈精度不足

**现状**

`button:active { transform: scale(0.97); }` 和 `button:hover { filter: brightness(1.12); }` 是全局统一的，没有按按钮语义区分。危险按钮（Close Position）和普通按钮（Export CSV）有相同的 hover 体验。

表格行点击（click → drawer）没有任何视觉确认，用户无法感知"这行已被选中"——drawer 打开前有一帧空白。

**参考做法**

Linear 的 issue 点击：行立刻高亮（background 变化），drawer 打开后高亮持续。
akrouter 表格行：hover 背景有明显变化（约 `rgba(255,255,255,0.03)`），选中态持续。

**修改方向**

- `DataTable` 的 `onRowClick` 行在点击后添加 `is-selected` 状态，`background: rgba(34,211,238,0.035)`
- 危险按钮 hover 使用 `brightness(1.0)` + `background: rgba(255,93,108,0.12)`（不用 brightness 增亮）

---

### 差距 8 ★★★☆☆ — 响应式降级缺少设计意图

**现状**

移动端（< 767px）主要靠 `flex-direction: column` 堆叠，没有针对窄屏的布局重设计。表格在窄屏仍然渲染全列，只有横向滚动。

**参考做法**

akrouter 公开页面在窄屏：导航收起为 hamburger，metrics 卡降格为 2 列而非 4 列 inline flex，不变成滚动条。

**修改方向**

- Overview 的 PnL tile 在 < 640px 已经是 2 列，这个正确
- Blotter 表格在 < 768px 应隐藏次要列（uPnL source / pnl_status / leverage），让核心列可读
- 这属于 D1 行为变化，需要独立 PR

---

### 差距 9 ★★☆☆☆ — 颜色系统自洽但缺少"呼吸感"

**现状**

整个 UI 非常克制、正确，但结果是"太均匀"——每个区域的颜色密度相同，视线没有被引导到重要信息上。比如 Overview 页面上，"系统状态 OPERATIONAL" 与 "今日 PnL" 与 "候选数量" 的视觉权重几乎相同。

**参考做法**

Stripe Dashboard：正向数字（收入、成功率）用明显的大字 + 绿色，非关键指标（事务 ID、时间戳）退到 faint。整页有明显的"首屏英雄区"概念。
akrouter：99.6% 成功率是页面上最大的数字，是因为这是用户最关心的指标。

**修改方向**

在不破坏现有信息架构的前提下，对每个页面识别"最高价值指标"，给它的 value 字体升档（+0.25rem）。这是微调，不是重设计。

---

### 差距 10 ★★☆☆☆ — 图标不统一

**现状**

Lucide React 是引入的图标库，但全站混用了 Unicode emoji（🔧、🌙、📊）、SVG 内联图标和 Lucide 组件三种形式。在 AgentFeed 里 `post_trade_reviewer: '🔧'`，在 StatusCluster 里用 SVG，在 Button 里偶尔用 Lucide。

**参考做法**

akrouter / Linear / Vercel：全站统一一套 1.5px stroke 线框 SVG 图标，大小 16 × 16 或 20 × 20，从不混用 emoji。

**修改方向**

- 系统 Agent 的 emoji 图标（`AGENT_ICON` map in AgentFeed.tsx）替换为对应语义的 Lucide icon
- 这是渐进式工作，优先改首屏可见 Agent feed

---

## 三、差距优先级矩阵

| # | 差距 | 影响面 | 实现成本 | 优先级 |
|---|---|---|---|---|
| 1 | 字体层次太平 | 全站所有页面 | 低（MetricTile 字号改 2 处） | **P0** |
| 2 | 卡片信息层级混乱 | Overview / Status popover | 中 | **P0** |
| 3 | Badge 语义均质 | 全站表格 | 低 | **P1** |
| 4 | 表格行高太紧 | Blotter / Orders | 低 | **P1** |
| 5 | 进度条实现不统一 | 跨多页面 | 中（抽 primitive） | **P1** |
| 6 | 空状态无品质感 | DataTable / 全站 | 中 | **P2** |
| 7 | 交互反馈精度 | 表格行选中 | 低 | **P2** |
| 8 | 响应式缺设计意图 | 移动端 | 中 | **P2** |
| 9 | 颜色无引导层次 | Overview 等关键页 | 低（逐步升档） | **P2** |
| 10 | 图标不统一 | AgentFeed 等 | 低（逐步替换） | **P3** |

---

## 四、akrouter 设计可直接借鉴的具体做法

1. **模型/渠道表格的行设计**：图标 + 粗名称 + 浅灰子标题 + 右侧数据右对齐，是 Blotter 候选表格的理想蓝本
2. **Metrics bar（成功率显示）**：用窄 bar（4px）+ 竖排 dots 显示多渠道状态，比单个数字更直观
3. **"稳定 / 低价 / 高速" pill badge**：`border-radius: 999px` + 极浅背景 + 无 border，比方形 chip 噪声低
4. **页面顶部 hero 区的 metric strip**：供给商 32 / 在架渠道 393 / 在架模型 49 / 近24h成功率 99.6%——4 个大数字 + 小标签，直接对应 Aletheia Overview 的 PnL / 仓位 / 候选数 / 成功率，可以直接套用这个视觉格式
5. **状态点 with glow**：在线渠道用 `box-shadow: 0 0 6px var(--green)` 的绿点，比纯色点更有"活着"的感觉

---

*本文件配合 [ui_design_system.md](./ui_design_system.md) 使用，具体实现参照各差距条目的"修改方向"。*
