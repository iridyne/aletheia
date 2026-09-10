# Aletheia 交易终端视觉设计、色彩系统与信息传达系统规范

> **文档性质**：本文档为 Aletheia 交易终端（Trading OS）在**整体视觉语言、系统级色彩模型、金融信息传达机制（Information Conveyance System）与数据可解释性可视化**方面的系统性设计方案与全量缺陷记录。

---

## 目录
1. [现存视觉、色彩与传达系统缺陷清单（全量问题记录）](#一-现存视觉色彩与传达系统缺陷清单全量问题记录)
2. [高保真暗黑终端色彩系统（Color System Architecture）](#二-高保真暗黑终端色彩系统color-system-architecture)
3. [金融交易信息传达系统（Information Conveyance System）](#三-金融交易信息传达系统information-conveyance-system)
4. [终端数据墨水比与视觉降噪规范（Data-to-Ink Ratio）](#四-终端数据墨水比与视觉降噪规范data-to-ink-ratio)
5. [金融图表与多元数据可视化配色标准（Chart Palette）](#五-金融图表与多元数据可视化配色标准chart-palette)
6. [落地实施与 Token 映射清单](#六-落地实施与-token-映射清单)

---

## 一、 现存视觉、色彩与传达系统缺陷清单（全量问题记录）

通过对全站源码、样式与真实运行环境的深度走查，归纳出当前系统在**视觉、色彩与信息传达**方面的具体问题：

### 1. 色彩系统缺陷
1. **饱和交互色与辅助信息色冲突（Cyan vs Blue 视觉混淆）**：
   - `--accent: #22d3ee`（Signal Cyan）与 `--blue: #4aa3ff` 在深黑背景下色相与明度极其接近。在 `StatusCluster`、`AgentFeed` 和 `TradesPositions` 中，经常出现同时并存蓝徽标与青徽标的混乱情况，操作员无法一眼区分哪个代表“交互强调”、哪个代表“信息链接”。
2. **缺乏语义状态背景微透光（Semantic Background Tints 缺失）**：
   - 现行系统仅定义了文本颜色（`--red`, `--green`, `--yellow`），缺少与各语义色对应的微透光背景（如 `rgba(255, 93, 108, 0.12)`）。各组件为了渲染警告卡片，不得不各自用行内样式临时硬编码，曾引发深色终端上浮出刺眼浅白背景的严重缺陷。
3. **图表调色盘硬编码散落**：
   - 在 `TradeInsightsPanel.tsx:574` 中，饼图与柱状图颜色数组被硬编码在组件内（`const PIE_COLORS = ['#22d3ee', '#2ee6a8', '#f5b942', ...]`），未接入全局设计系统 Token，无法统一维护。
4. **色弱（Colorblindness）双重无障碍保障缺口**：
   - 盈亏展示（PnL）与 K 线涨跌过分依赖红绿区分。虽然 `pnlCell` 附带了 `+`/`-` 符号，但在热力图（Heatmap）、散点图（Scatter）和点阵图（DotMatrix）中，纯靠颜色区隔胜负，缺乏形状和纹理辅助通道。

### 2. 信息传达（Conveyance）缺陷
1. **紧急度梯队缺乏空间与形式的层次化（Urgency Hierarchy Compression）**：
   - 致命级故障（如 Kill Switch 触发、网关掉线）与普通告警（如市场数据延迟 10 秒）在界面上均使用红色标签，缺乏“阻断式置顶面板”与“次要提示”的尺寸和位置区隔。
2. **时效性与“双时钟”（Two-Clock Freshness）传达不直观**：
   - 交易系统严格依赖“交易所撮合时间戳（`source_observed_at`）”与“本地 SQLite 入库时间戳（`acquired_at`）”。目前前端页面仅以单一时间戳展示，无法直观告知操作员当前数据是“交易所慢了”还是“本地收集器卡了”。
3. **AI 推演不确定度（Uncertainty）未可视化表达**：
   - Agent 提议的置信度（Conviction）目前只是一串数字（如 `63/100`），缺乏置信度区间（Confidence Interval）和博弈分歧度（Debate Divergence）的图形化表达。
4. **因果链路断点隐蔽**：
   - 在 `CausalChain` 中，当某个上游阶段未落库时，虽然渲染了 `not recorded`，但缺乏“断点定位警示”，操作员很难一眼看出整条链路是在哪一环断裂的。

---

## 二、 高保真暗黑终端色彩系统（Color System Architecture）

### 1. 物理色阶与表面层级（Surface Elevation Hierarchy）
为暗黑终端建立 5 级明确的物理表面深度，拉大明度反差，防止夜间护眼模式下的暗态粘连：

```css
:root {
  /* 基础物理底色 (Global Base) */
  --slate-950: #080b0e;  /* 终极黑 (视窗最底层) */
  --slate-900: #0d1217;  /* 应用基底背景 */
  --slate-850: #131920;  /* 凹陷内容区 (主视窗) */
  --slate-800: #182029;  /* 基础卡片表面 (Surface 1) */
  --slate-750: #202b37;  /* 悬浮/二级输入表面 (Surface 2) */
  --slate-700: #2a3848;  /* 顶层覆盖/弹窗抽屉 (Surface 3) */

  /* 语义表面绑定 */
  --bg-canvas:       var(--slate-900);
  --bg-inset:        var(--slate-850);
  --surface-card:    var(--slate-800);
  --surface-raised:  var(--slate-750);
  --surface-popover: var(--slate-700);

  /* 边框层级 (Borders) */
  --border-subtle:   rgba(255, 255, 255, 0.05); /* 弱分割 */
  --border-default:  #24303e;                   /* 卡片标准边框 */
  --border-strong:   #344458;                   /* 突出卡片/表头分割 */
  --border-focus:    var(--cyan-400);           /* 键盘聚焦高亮 */
}
```

### 2. 状态语义光谱矩阵（Semantic Tone Matrix）
将每一个状态色扩充为包含 **Text、Border、Bg-Tint、Hover** 的四元组，彻底杜绝行内临时写死透明度：

```css
:root {
  /* 核心交互色：Signal Cyan */
  --cyan-400: #22d3ee;
  --cyan-500: #06b6d4;
  --cyan-900: rgba(6, 182, 212, 0.12);  /* 交互激活微底色 */

  /* 成功 / 正常启用：Emerald Green */
  --green-400: #2ee6a8;
  --green-500: #10b981;
  --green-900: rgba(46, 230, 168, 0.12); /* 成功状态微底色 */

  /* 危险 / 严重阻断：Coral Red */
  --red-400:   #ff5d6c;
  --red-500:   #ef4444;
  --red-900:   rgba(255, 93, 108, 0.14); /* 危险警示微底色 */

  /* 注意 / 警告降级：Amber Yellow */
  --yellow-400:#f5b942;
  --yellow-500:#f59e0b;
  --yellow-900:rgba(245, 185, 66, 0.12); /* 降级注意微底色 */

  /* 信息 / 外部参考：Indigo Blue (拉开与 Cyan 的视觉差距) */
  --blue-400:  #60a5fa;
  --blue-500:  #3b82f6;
  --blue-900:  rgba(59, 130, 246, 0.12); /* 辅助信息微底色 */

  /* 未知 / 停用中性：Slate Muted */
  --muted-text: #7d8997;
  --muted-bg:   rgba(125, 137, 151, 0.10);
}
```

### 3. 色弱安全模式（High-Contrast & Colorblind Palette）
针对红绿色弱（占男性群体约 8%），提供可切换的色彩无障碍替代映射：
* **多通道区分准则**：在呈现正负盈亏时，严禁仅使用字体颜色，必须强制配对符号：
  - 正收益：`+` 符号 + `--green-400`（色弱模式下映射为 Sky Blue `#38bdf8`）
  - 负收益：`-` 符号 + `--red-400`（色弱模式下映射为 Vivid Amber `#fb923c`）

---

## 三、 金融交易信息传达系统（Information Conveyance System）

为了保证操作员在极端行情与瞬时故障下能够光速研判，建立 5 级严格的**信息传达层级体系**：

```
┌────────────────────────────────────────────────────────────────────────┐
│                        金融信息传达 5 级阶梯                            │
├───────┬──────────────┬────────────────────────┬────────────────────────┤
│ Level │ 语义级别     │ 触发场景               │ 视觉与交互呈现形态     │
├───────┼──────────────┼────────────────────────┼────────────────────────┤
│ L4    │ 致命阻断     │ Kill Switch 激活       │ 顶部强置顶红色横幅     │
│       │ Critical     │ 网关离线 / 连续心跳丢失 │ 按钮下方永久内联报错   │
│       │              │ 账户濒临爆仓           │ 触发系统级原生桌面通知 │
├───────┼──────────────┼────────────────────────┼────────────────────────┤
│ L3    │ 策略拦截     │ 风控引擎主动拒单       │ 琥珀黄警告卡片         │
│       │ Blocked      │ 候选集效用未达标       │ 带具体 reason_code     │
│       │              │ 实盘模式配置冲突       │ 明确指出下一步解除指引 │
├───────┼──────────────┼────────────────────────┼────────────────────────┤
│ L2    │ 降级运行     │ 行情数据局部陈旧       │ Muted 灰黄微标         │
│       │ Degraded     │ 收集器落后于调度       │ 显示陈旧秒数 (如 +45s) │
│       │              │ 模型单价未配置         │ 数据保留显示，不阻断   │
├───────┼──────────────┼────────────────────────┼────────────────────────┤
│ L1    │ 正常受控     │ 调度器平稳巡航         │ 纯净翠绿小圆点         │
│       │ Operational  │ Agent 运行中           │ 极简数字，无闪烁干扰   │
├───────┼──────────────┼────────────────────────┼────────────────────────┤
│ L0    │ 事实中性     │ 历史已平仓记录         │ Slate 冷灰文字         │
│       │ Informational│ 静态只读元数据         │ 纯等宽字体排版         │
└───────┴──────────────┴────────────────────────┴────────────────────────┘
```

### 1. 时效性传达：双时钟新鲜度渐变（Two-Clock Freshness Gradient）
交易数据不可信往往源于时延，界面必须严格传达双时钟真相：
* **源端时间（Exchange Timestamp）**：标的在交易所发生的物理时间；
* **捕获时间（Acquisition Timestamp）**：Aletheia 本地入库时间。
* **传达规则**：
  - 时延 $\le 3\text{s}$：正常显示，不显示时延后缀；
  - 时延 $3\text{s} \sim 15\text{s}$：轻量显示 `(stale 8s)`；
  - 时延 $> 15\text{s}$：整个数据行文字适度降低透明度至 `0.6`，并点亮黄色警告图标。

### 2. 决策可解释性与不确定度表达（Uncertainty & Evidence Conveyance）
在 `Overview`、`Universe` 及 `Learning` 页面，彻底革新目前的纯数值呈现：
* **置信度仪表（Conviction Meter）**：
  不仅展示 `63/100`，而是采用双重进度条：
  - 底色横条展示系统基准容许阈值（Threshold Floor，如 50）；
  - 高亮前景色展示 Agent 实时评估值；
  - 在右侧以轻量标签标注辩论分歧度：`Bull 82 vs Bear 78 (High divergence)`，直观告知操作员该决策属于“一致看好多头”还是“高分歧险胜”。
* **因果断裂显式化（Causal Gap Indicators）**：
  在 `CausalChain` 中，当从 `Order` 找不到关联的 `Position` 时，连接轨道的虚线变为红色断裂线，并附带明确提示：“撮合完成但账本未对齐（Awaiting reconciliation）”，杜绝用户对资金去向的焦虑。

---

## 四、 终端数据墨水比与视觉降噪规范（Data-to-Ink Ratio）

遵循 Edward Tufte 的**数据墨水比最大化原则**，在保证极高信息密度的同时彻底去除无意义视觉噪声：

### 1. 字体排版强化（Typography System）
* **斜线零（Slashed Zero）强制开启**：
  在加密货币与金融场景中，`0` 与大写字母 `O` 极易混淆（如 `SOL0` vs `SOLO`，订单 ID `0x...`）。
  全站数值与等宽代码必须开启 OpenType 高级特性：
  ```css
  .mono, table, .tnum {
    font-family: var(--font-mono);
    font-feature-settings: 'tnum' 1, 'zero' 1; /* 表格等宽数字 + 带斜杠的数字0 */
  }
  ```
* **标签与数据视觉落差**：
  - 标签（Label）：字号采用 `--fs-xs` (0.68rem)，大写字母，字符间距 `0.05em`，颜色为 `--muted-text`；
  - 数据（Value）：字号采用 `--fs-sm` 或 `--fs-base`，加粗，纯白 `--text`。
  - 严禁标签与数据使用相同字重和颜色。

### 2. 边框降噪与“阴影代边框”
* **去除双重边框**：外层 Card 已经拥有 `1px solid var(--border-default)` 时，其内部包裹的表格或栅格禁止再画完整的全包围边框，改用轻量的底部分割线 `border-bottom: 1px solid var(--border-subtle)`。
* **背景交替行（Zebra Striping）规范**：
  长表格（大于 20 行）启用极其微弱的奇偶行色差，隔行底色明度仅增加 1.5%，帮助交易员横向扫视多列数据时不串行：
  ```css
  .dt-table tr:nth-child(even) {
    background: rgba(255, 255, 255, 0.015);
  }
  ```

---

## 五、 金融图表与多元数据可视化配色标准（Chart Palette）

针对 `Charts.tsx`、`PnLChart.tsx`、`Usage.tsx` 以及各类时序面板，确立中心化的图表专属 Token，严禁在页面内私自写死颜色：

```css
:root {
  /* 基础 K 线配色 */
  --chart-candle-up:    #2ee6a8; /* 涨 */
  --chart-candle-down:  #ff5d6c; /* 跌 */
  --chart-wick-up:      #2ee6a8;
  --chart-wick-down:    #ff5d6c;

  /* 技术指标专属配色 (彼此色相夹角 > 45°，多线叠加清晰分明) */
  --chart-indicator-ema20:   #22d3ee; /* EMA 20: Signal Cyan */
  --chart-indicator-ema50:   #f5b942; /* EMA 50: Amber */
  --chart-indicator-bb-upper:#4aa3ff; /* 布林上轨: Soft Blue */
  --chart-indicator-bb-mid:  #7d8997; /* 布林中轨: Slate */
  --chart-indicator-bb-lower:#4aa3ff; /* 布林下轨: Soft Blue */
  --chart-indicator-rsi:     #a78bfa; /* RSI: Purple */
  --chart-indicator-macd:    #38bdf8; /* MACD 线: Sky Blue */
  --chart-indicator-signal:  #fb923c; /* MACD 信号线: Orange */

  /* 成交量剖面 (Volume Profile) */
  --chart-vp-poc:            #ff5d6c; /* POC 控制线: 高亮红色 */
  --chart-vp-value-area:     rgba(34, 211, 238, 0.25); /* 价值区: 半透明青 */
  --chart-vp-outside:        rgba(125, 137, 151, 0.15); /* 价值区外: 半透明灰 */

  /* 分类饼图/多维度对比离散色板 (Discrete 8-Color Palette) */
  --series-1: #22d3ee; /* Cyan */
  --series-2: #2ee6a8; /* Emerald */
  --series-3: #f5b942; /* Amber */
  --series-4: #4aa3ff; /* Blue */
  --series-5: #a78bfa; /* Purple */
  --series-6: #fb923c; /* Orange */
  --series-7: #f472b6; /* Pink */
  --series-8: #34d399; /* Mint */
}
```

---

## 六、 落地实施与 Token 映射清单

| 优化维度 | 涉及核心文件 | 落地动作 | 预期设计收益 |
|---|---|---|---|
| **色彩矩阵** | `frontend/src/index.css` | 注入 `--surface-raised`、`--red-900` 等四元组 | 解决输入框与卡片暗态粘连；提供规范的微透光背景 |
| **色弱安全** | `frontend/src/components/DataTable.tsx`<br>`frontend/src/lib/format.ts` | 强制数值正负伴随字符与形状双通道 | 满足金融无障碍 WCAG 2.1 AA 级合规标准 |
| **传达阶梯** | `frontend/src/components/StatusCluster.tsx`<br>`frontend/src/components/ui/` | 实现 L4 至 L0 的 5 级严重度视觉组件 | 极端突发故障 0.5 秒内引起操作员高度警觉 |
| **时效双时钟** | `frontend/src/pages/Overview.tsx`<br>`frontend/src/pages/Universe.tsx` | 细化渲染交易所时间与本地采集时间 | 彻底消除网络延迟与本地调度卡顿的研判盲区 |
| **图表色板** | `frontend/src/charts/theme.ts`<br>`frontend/src/charts/TradeInsightsPanel.tsx` | 清理硬编码 `PIE_COLORS`，统一引用 `--series-*` | 图表配色中心化治理，主题联动零偏差 |
| **排版降噪** | `frontend/src/index.css` | 启用 `'zero' 1`、清除卡片多余内框线、增加微弱隔行色 | 提升长数据扫视舒适度，杜绝 `0` 与 `O` 混淆 |

---

*（本文档已完整保存至本地：`docs/design/frontend/guidelines/visual_and_conveyance_system_design.md`，可作为 Aletheia 视觉与传达系统正规化的基石规范）*
