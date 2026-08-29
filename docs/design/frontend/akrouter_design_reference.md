# AKRouter 设计系统逆向分析与 Aletheia 优化方案

> 来源：对 akrouter.com 公开页面的 CSS/HTML 完整抓取与逆向分析（2026-08-29）
> 目标：将 akrouter 的设计语言应用到 Aletheia Web UI，给出可直接实施的优化方案

---

## 一、AKRouter 设计系统完整解析

### 1.1 颜色系统（从源码 CSS Custom Properties 提取，LAB → hex 转换）

AKRouter 使用 **OKLAB / CSS LAB 色彩空间**定义所有语义颜色，这是比 sRGB hex 更感知均匀的表示。
以下是经过逆向计算的实际 hex 值：

#### 背景层次（5 层）

| Token | Hex | 用途 |
|---|---|---|
| `--background` | `#0e0f11` | 应用底色（极深，带冷蓝调） |
| `--surface` | `#16181b` | 卡片、侧边栏 |
| `--surface-muted` | `#1f2125` | 表格 header、次级卡片 |
| `--surface-hover` | `#24262a` | hover 状态 |
| `--border` | `#2a2c30` | 默认边框 |
| `--border-strong` | `#3e4046` | 强分隔线 |

**5 层背景高差比 Aletheia 多一层**，层次过渡更细腻。

#### 文字（3 级）

| Token | Hex | 用途 |
|---|---|---|
| `--foreground` | `#f0f1f2` | 主文本（近白） |
| `--muted-foreground` | `#95989f` | 次要文本、标签 |
| `--subtle-foreground` | `#797c82` | 极次要（表头、hint） |

表格 `<th>` 实测 color 为 `--subtle-foreground`（`#797c82`），没有 uppercase/letter-spacing。

#### 主色与语义色

| Token | Hex | 角色 |
|---|---|---|
| `--primary` | `#528af6` | **蓝**（主操作 CTA，非 Cyan） |
| `--primary-foreground` | `#0a0c11` | 按钮上的文字 |
| `--primary-subtle` | `#1b2b49` | 蓝色低透明背景 |
| `--success` | `#4eb76b` | 成功/在线/正向 |
| `--success-subtle` | `#16321d` | 成功低透明背景 |
| `--warning` | `#ecb546` | 警告 |
| `--warning-subtle` | `#3a2c0f` | 警告低透明背景 |
| `--danger` | `#f75d57` | 错误/危险 |
| `--danger-subtle` | `#48201c` | 危险低透明背景 |
| `--info` | `#41aadd` | 信息/链接 |
| `--info-subtle` | `#112f3e` | 信息低透明背景 |

**关键发现：主色是蓝色（`#528af6`），不是 Cyan**。每个语义色都有配套的 `*-subtle` 深色背景变体。

#### 成功率多级着色（rate 系列）

| Token | Hex | 含义 |
|---|---|---|
| `--rate-ok` | `#4eb76b` | 100% — 健康 |
| `--rate-warn` | `#e8c94b` | 轻微降级 |
| `--rate-bad` | `#f99242` | 明显异常（橙） |
| `--rate-crit` | `#f75d57` | 严重故障 |
| `--rate-empty` | `#2f3033` | 无数据 |

这是一个专门针对成功率/健康度的 5 色渐变语义系统。

#### Chart 色板（6 色）

```
--chart-1: #528af6  (蓝，主色)
--chart-2: #4eb76b  (绿)
--chart-3: #ecb546  (黄)
--chart-4: #b97de2  (紫)
--chart-5: #07b7bf  (青绿)
--chart-6: #f75d57  (红)
```

---

### 1.2 字体系统

```
--font-sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, …
--font-mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, …
```

**使用系统字体栈，不加载自定义字体**。基础字号 14px（`--text-base: 0.875rem`），行高 1.5（21px）。

没有 Display 字体，使用相同 sans-serif 字族通过字重（400/500/600）区分层次。

---

### 1.3 间距与尺寸系统

基于 **Tailwind v4** 的 `0.25rem`（4px）单元格，使用实测数据：

| 规格 | 值 | 用途 |
|---|---|---|
| `--spacing-topbar` | `3.5rem = 56px` | 顶栏高度 |
| `--spacing-row` | `2.75rem = 44px` | **标准数据行高** |
| `h-row-compact` | `2.25rem = 36px` | 紧凑行 |
| `--radius-sm` | `5px` | 小圆角 |
| `--radius-md` | `7px` | 中圆角（按钮）|
| `rounded-lg` | `10px` | 大圆角（卡片）|
| `rounded-full` | `9999px` | pill |
| `rounded-xs` | `3px` | 最小圆角（bar、mark） |

**标准行高 44px**（`h-row`）是一个精心定义的 token，直接映射到 `--spacing-row`。

---

### 1.4 组件原语（从 HTML 结构逆向）

#### 成功率可视化（率柱组）

```html
<!-- 每根柱：3px 宽 × 16px 高 × 3px 圆角，颜色取 --rate-* -->
<div class="h-4 w-[3px] rounded-xs bg-rate-ok"></div>
<div class="h-4 w-[3px] rounded-xs bg-rate-ok"></div>
<!-- ... 10 根组成一个成功率可视化 -->
```

这是 akrouter 成功率可视化的实现：**10 根竖柱 × (3px × 16px)**，根据值依次着色。比单一 bar 能表达更多信息（精确值 + 趋势 + 健康级别）。

#### 数值 Pill（数值展示）

```html
<span class="inline-flex h-5 items-center rounded-full px-2 text-xs font-medium bg-success-subtle text-success">
  0.0112
</span>
```

- `rounded-full` → border-radius: 9999px（真正的 pill，不是方角）
- 高度固定 `h-5 = 20px`
- padding `px-2 = 8px` 左右
- 字号 12px（`text-xs`）
- **无 border**，只用背景区分

#### 在线状态点

```html
<div class="size-2 rounded-full bg-health-0"></div>
```

- 尺寸 `size-2 = 8px × 8px`
- `rounded-full` → 正圆
- 颜色直接用 `--health-0`（等于 `--success` 绿色）
- **无 box-shadow glow**（公开首页无 glow，实际 console 可能有）

#### 表格 header

```
font-size: 12px（非 uppercase, 非 letter-spacing）
color: --subtle-foreground (#797c82)
font-weight: 500
padding: 8px 12px
text-transform: none
background: --surface-muted (#1f2125)
```

**没有 uppercase + letter-spacing 的 header**——这和 Aletheia 当前 `.card h3` 的 uppercase 做法正好相反。

#### 模型图标容器

```html
<span class="flex size-7 shrink-0 items-center justify-center rounded-md bg-surface-muted text-foreground">
  <!-- SVG icon 18×18 -->
</span>
```

`size-7 = 28px × 28px`，`rounded-md = 7px`，背景 `surface-muted`。

#### 表格数据行

```html
<tr class="border-border border-b last:border-b-0">
  <td class="px-3 py-2">...</td>
</tr>
```

- 实测行高 **79px**（主要由内容撑开，包含图标行 + 子标题行）
- `py-2 = 8px` 上下内边距
- `border-b` 底部分隔线，不用横向 stripe
- **无 hover 背景** on `<tr>` 本身

---

### 1.5 交互规范

从 CSS 中提取：

```css
/* 过渡时长统一 0.15s */
--default-transition-duration: .15s;
--default-transition-timing-function: cubic-bezier(.4, 0, .2, 1);

/* 导航链接 hover */
.text-muted-foreground hover:text-foreground { transition-duration: 120ms; }
```

**比 Aletheia 的 80ms 更长**，感觉更"弹性"而非"脆"。

---

## 二、与 Aletheia 当前设计系统的逐项对比

| 维度 | AKRouter | Aletheia 现状 | 差距说明 |
|---|---|---|---|
| 背景层次 | 5 层（bg/surface/surface-muted/surface-hover/border） | 4 层（bg/bg-2/surface/surface-2） | 缺少 `surface-hover` 语义层；`bg-2` 概念不如 `surface-muted` 清晰 |
| 主色 | 蓝 `#528af6` | Cyan `#22d3ee` | 色调不同，各有风格，Aletheia 的 Cyan 更冷峻 |
| 语义色配套 | 每色有 `*-subtle` 深背景 | 无 subtle 变体，用 rgba 透明度代替 | 透明度叠加在不同背景上呈现不一致；solid subtle 更稳定 |
| 成功率语义 | 5 级 rate-* 渐变系统 | 无，只有 green/yellow/red | 缺少 orange（轻度异常）级别；无专用 rate-empty |
| Badge/Pill | `rounded-full`（9999px），无 border，`h-5/20px` | `rounded-sm`（5px），有 border，约 22px | AKRouter pill 更轻盈，无 border 减少噪音 |
| 表格 header | 12px，500 weight，非 uppercase | 0.78rem uppercase + letter-spacing | Uppercase header 增加扫视成本；AKRouter 用颜色+粗细区分 |
| 行高 | 44px 标准行（`--spacing-row`），实测 79px 含子行 | ~32-36px | 行高不足，数据密集时视觉拥挤 |
| 数值展示 | pill 包裹数值，`bg-success-subtle text-success` | `badge` 方角 + border | AKRouter pill 对数值更语义化，视觉更干净 |
| 成功率可视化 | 10 根 3×16px 竖柱（rate-* 着色） | 无（只有文字百分比） | 缺少快速视觉感知 |
| 字体 | 系统字体栈（无自定义字体加载） | Google Fonts（Space Grotesk/IBM Plex） | 自定义字体加载有 FOUT 风险，AKRouter 零字体请求 |
| 过渡时长 | 120–150ms | 80–120ms | Aletheia 稍快，AKRouter 更柔和 |
| 圆角规范 | xs(3)/sm(5)/md(7)/lg(10)/full | sm(5)/r(8)/full | AKRouter 层次更细（7px 按钮 vs Aletheia 8px，接近但 AKRouter 更精确） |

---

## 三、完整 UI 优化方案

### 3.1 颜色系统升级

#### 新增 `*-subtle` 语义变体 token

在 `:root` 中为每个语义色添加 solid 深色背景变体，替换现有 `rgba()` 透明度方案：

```css
:root {
  /* 现有语义色保留，新增 subtle 变体 */
  --green-subtle:  #13301d;   /* --green(#2ee6a8) 的深色底 */
  --red-subtle:    #3d1518;   /* --red(#ff5d6c) 的深色底 */
  --yellow-subtle: #352810;   /* --yellow(#f5b942) 的深色底 */
  --blue-subtle:   #0f2240;   /* --blue(#4aa3ff) 的深色底 */
  --accent-subtle: #0a2535;   /* --accent(#22d3ee) 的深色底 */
  
  /* 成功率专用 5 级（对应 Aletheia 的 health/rate 概念） */
  --rate-ok:    #2ee6a8;   /* = --green，健康 */
  --rate-warn:  #e8c94b;   /* 新增：轻度降级 */
  --rate-bad:   #f99242;   /* 新增：中度异常（橙） */
  --rate-crit:  #ff5d6c;   /* = --red，严重 */
  --rate-empty: #2d3540;   /* 无数据 */
  
  /* surface-hover（新增第5层） */
  --surface-hover: #1f262f;  /* 介于 surface-2 和 border 之间 */
}
```

#### 现有 badge 样式迁移

```css
/* 旧：rgba 透明度叠加 → 新：solid subtle */
.badge-green  { background: var(--green-subtle);  color: var(--green);  border: none; }
.badge-red    { background: var(--red-subtle);    color: var(--red);    border: none; }
.badge-yellow { background: var(--yellow-subtle); color: var(--yellow); border: none; }
.badge-blue   { background: var(--blue-subtle);   color: var(--blue);   border: none; }
```

---

### 3.2 Badge → Pill 语义升级

新增 `.pill` 原语，用于数值展示和紧凑状态标签：

```css
/* 新增：pill 原语（对标 AKRouter 的 inline-flex h-5 rounded-full） */
.pill {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: 9999px;
  font-size: 0.75rem;     /* 12px */
  font-weight: 500;
  font-family: var(--mono);
  white-space: nowrap;
  border: none;           /* 无 border，靠背景区分 */
}
.pill-green  { background: var(--green-subtle);  color: var(--green); }
.pill-red    { background: var(--red-subtle);    color: var(--red); }
.pill-yellow { background: var(--yellow-subtle); color: var(--yellow); }
.pill-blue   { background: var(--blue-subtle);   color: var(--blue); }
.pill-muted  { background: var(--surface-2);     color: var(--muted); }
```

使用场景：
- 方向标签（Long/Short）→ `pill-green` / `pill-red`
- 数值 badge（0.0112 倍率等）→ `pill-green`
- 状态文字（filled/pending）→ 对应语义 pill
- 现有方角 `.badge` 保留，用于需要 border 的场景（如 market type、mode）

---

### 3.3 成功率可视化组件

新增 `.rate-bar` 组件，对标 AKRouter 的竖柱成功率可视化：

```css
.rate-bar {
  display: inline-flex;
  align-items: flex-end;
  gap: 2px;
  height: 16px;
}
.rate-bar-unit {
  width: 3px;
  height: 16px;
  border-radius: 3px;
  background: var(--rate-empty);
  flex-shrink: 0;
}
.rate-bar-unit.ok   { background: var(--rate-ok); }
.rate-bar-unit.warn { background: var(--rate-warn); }
.rate-bar-unit.bad  { background: var(--rate-bad); }
.rate-bar-unit.crit { background: var(--rate-crit); }
```

React 组件：

```tsx
// 10 根柱可视化成功率（>99% = 10根绿，95-99% = 9根+1橙...）
function RateBar({ rate }: { rate: number | null }) {
  if (rate === null) return <span className="text-muted">—</span>;
  const filled = Math.round(rate / 10);
  const tone = rate >= 99 ? 'ok' : rate >= 95 ? 'warn' : rate >= 90 ? 'bad' : 'crit';
  return (
    <span className="rate-bar" title={`${rate.toFixed(1)}%`}>
      {Array.from({ length: 10 }, (_, i) => (
        <span key={i} className={`rate-bar-unit ${i < filled ? tone : ''}`} />
      ))}
    </span>
  );
}
```

**应用场景**：Blotter 候选表格的成功率列、Universe 成员行。

---

### 3.4 表格规范升级

#### 行高

```css
/* 标准数据行高 → 44px（对标 AKRouter --spacing-row） */
.data-table-row { min-height: 44px; }
.data-table-row td { padding: 10px 12px; }

/* 紧凑行（候选表格等决策密集场景） */
.data-table-row-compact td { padding: 6px 12px; min-height: 36px; }
```

#### 表格 header

```css
/* 移除 uppercase + letter-spacing，改为颜色 + 字重区分 */
table thead th {
  font-size: 0.75rem;           /* 12px */
  font-weight: 500;
  color: var(--muted);          /* 用颜色区分，不用大写 */
  text-transform: none;         /* 去掉 uppercase */
  letter-spacing: 0;            /* 去掉 letter-spacing */
  padding: 8px 12px;
  background: var(--surface-2); /* 或 surface-hover */
  border-bottom: 1px solid var(--border);
}
```

---

### 3.5 在线状态点升级

```css
/* 对标 AKRouter size-2 rounded-full bg-health-* */
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 9999px;
  flex-shrink: 0;
}
.status-dot-ok     { background: var(--rate-ok);   box-shadow: 0 0 6px var(--rate-ok); }
.status-dot-warn   { background: var(--rate-warn);  box-shadow: 0 0 6px var(--rate-warn); }
.status-dot-crit   { background: var(--rate-crit);  box-shadow: 0 0 6px var(--rate-crit); }
.status-dot-muted  { background: var(--faint);      box-shadow: none; }
```

---

### 3.6 字体层次优化

不更换字体（保留 IBM Plex），但修正字重使用：

```css
/* 核心指标值：大 + 粗（对标 AKRouter 大数字区） */
.metric-value-hero {
  font-size: var(--fs-2xl);      /* 1.25rem */
  font-weight: 700;
  font-family: var(--mono);
  font-feature-settings: 'tnum' 1;
  color: var(--text);
}

/* 卡片标题：去掉 uppercase，改用字重 + 颜色 */
.card h3 {
  font-size: var(--fs-base);     /* 0.8125rem，略升 */
  font-weight: 600;
  color: var(--text);            /* 改为主文本色，不是 muted */
  text-transform: none;          /* ← 关键：去掉 uppercase */
  letter-spacing: 0;             /* ← 去掉 letter-spacing */
}

/* 标签/眉题（eyebrow）：这才是 uppercase 该用的地方 */
.eyebrow {
  font-size: 0.625rem;           /* 10px */
  font-weight: 700;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.10em;
}
```

---

### 3.7 间距 token 新增

```css
:root {
  /* 新增：对标 AKRouter 的语义化行高 token */
  --row-height: 44px;         /* 标准数据行 */
  --row-height-compact: 36px; /* 紧凑行 */
  --topbar-height: 52px;      /* 现有 --header-height，补充语义名 */
}
```

---

### 3.8 圆角精化

```css
:root {
  --r:      8px;   /* 现有，卡片、dialog */
  --r-sm:   5px;   /* 现有，按钮、input */
  --r-xs:   3px;   /* 新增：bar、mark、mini-indicator（对标 rounded-xs: 3px）*/
  /* rounded-full = 9999px，不需要 token */
}
```

---

## 四、优先级排序与实施路线图

### P0 — 立即可做（低风险，视觉改善明显）

1. **Pill 原语**：新增 `.pill` 及 5 色变体，让 Long/Short 方向标签改用 pill
2. **成功率语义扩展**：新增 `--rate-warn`（橙黄）、`--rate-bad`（橙）token
3. **表格 header 去 uppercase**：card h3 和 `<th>` 去掉 `text-transform: uppercase` + `letter-spacing`
4. **subtle 变体 token**：在 `:root` 新增 6 个 solid subtle 背景，让 badge 背景稳定

### P1 — 本周可做（中等工作量）

5. **RateBar 组件**：实现 React 竖柱成功率可视化，替换 Blotter 里的纯数字
6. **行高升级**：标准表格行 → 44px
7. **在线状态点 glow**：status dot 加 box-shadow glow
8. **`--surface-hover` token**：新增第5层背景

### P2 — 后续迭代

9. **MetricTile 数字升档**：hero value 用 1.25rem+ 字号
10. **图标统一**：AgentFeed 等组件的 emoji 替换为 Lucide icon

---

## 五、AKRouter CSS 关键片段存档

### 原始 :root token（LAB 色彩空间）

```css
/* 完整 token 体系，供参考比对 */
--background: lab(4.27% -.055 -1.3);     /* = #0e0f11 */
--surface: lab(8.1% -.102 -2.25);        /* = #16181b */
--surface-muted: lab(12.74% -.12 -2.62); /* = #1f2125 */
--primary: lab(57.69% 7.8 -60.75);       /* = #528af6 (蓝) */
--success: lab(67.07% -44.61 29.32);     /* = #4eb76b (绿) */
--warning: lab(77.50% 12.55 62.20);      /* = #ecb546 (黄) */
--danger: lab(61.01% 59.58 36.85);       /* = #f75d57 (红) */
--rate-ok: lab(67.07% -44.61 29.32);     /* = #4eb76b */
--rate-warn: lab(81.98% 1.53 64.17);     /* = #e8c94b */
--rate-bad: lab(70.80% 35.66 57.96);     /* = #f99242 */
--rate-crit: lab(61.01% 59.58 36.85);    /* = #f75d57 */
--spacing-row: 2.75rem;                  /* = 44px 标准行高 */
--radius-sm: 5px; --radius-md: 7px;
```

### 成功率竖柱实现

```html
<!-- 10 根 3px×16px 竖柱，颜色按 rate-* 着色 -->
<div class="h-4 w-[3px] rounded-xs bg-rate-ok"></div>  <!-- 3px 宽 × 16px 高 -->
```

### Pill 数值展示

```html
<!-- 无 border，rounded-full，h-5(20px)，bg-success-subtle text-success -->
<span class="inline-flex h-5 items-center rounded-full px-2 text-xs font-medium bg-success-subtle text-success">
  0.0112
</span>
```

### 表格 header

```html
<!-- 12px, weight 500, subtle-foreground, 无 uppercase -->
<th class="px-3 py-2 text-left font-medium">商家 / 模型</th>
```

---

*本文档基于对 akrouter.com 公开页面（无登录）的完整 CSS/HTML 抓取，2026-08-29。*
