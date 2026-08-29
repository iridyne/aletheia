# Charts 页面图表库研究报告

> 写于 2026-08-29，基于 /charts 页面分隔线位置 bug 的根因分析展开的技术调研。

---

## 一、现状：multi-instance 架构与其问题

### 当前版本

`lightweight-charts` **v4.2.3**（2023 年版本），`frontend/package.json` 锁定。

### 架构模型

当前的多 pane 效果**不是库原生支持的**，而是用多个独立 `createChart()` 实例加 CSS Grid + 手工 DOM 拼装实现的：

```
market-chart-stage (CSS grid, --market-chart-grid-rows)
├── market-price-chart-wrap   → IChartApi 实例 #1（K 线 + EMA/BB/Volume 叠加）
├── market-chart-divider      → 纯 DOM div，手写 pointerdown/move/up 拖拽逻辑
└── market-subchart-region
    ├── market-indicator-pane → IChartApi 实例 #2（RSI）
    └── market-indicator-pane → IChartApi 实例 #3（MACD，若启用）
```

关键文件：
- `frontend/src/charts/PriceChart.tsx` — 主组件，约 715 行
- `frontend/src/charts/priceChartModel.ts` — 指标数学计算（RSI/EMA/MACD/Stochastic/ATR/BB），约 300 行，有完整单元测试
- `frontend/src/index.css` — `.market-chart-stage`、`.market-chart-divider`、`.market-subchart-region` 等布局 CSS

### 已确认的缺陷

| 缺陷 | 根因 | 严重程度 |
|---|---|---|
| 分隔线默认落在上方 15-30%，K 线空间被压缩 | `useLayoutEffect` 在父容器（`--charts-shell-height`）设置完成前测量 stage 高度；`minSub = 80px` 被固化；最终 grid 行是 `411px 6px 80px` 而非预期的 70/30 | 高 |
| crosshair 跨 pane 不同步 | 不同 `IChartApi` 实例之间无原生通道，需要额外监听和转发 | 中 |
| 时间轴同步抖动风险 | 多个 `subscribeVisibleLogicalRangeChange` 互相触发，用 `isSyncingRangeRef` 打补丁防止无限递归 | 中 |
| 分辨率/DPR 不一致 | 各实例各自独立管理 `ResizeObserver`，高 DPR 屏容易 1px 错位 | 低 |

### 分隔线 bug 精确数据（实测，1159×779 viewport）

```
stage.height        = 497px
gridTemplateRows    = "411.109px 6px 80px"   ← 硬算出的值
divider.y           = 676   （距页面顶部）
stage.y             = 264.89
(divider.y - stage.y) / stage.height = (676 - 264.89) / 497 ≈ 0.827
```

分隔线实际落在 stage 的 **82.7%** 处，RSI 只有 80px（约 16%），不是预期的 70/30。

---

## 二、市场方案调研

### 2.1 TradingView Advanced Charting Library

成熟度最高，币安/Bitget/OKX 等主流交易所均在用。功能完整（内置全套指标、Drawing Tools、多品种比较等）。

**结论：不可行。** 商业授权，需申请白名单，无公开价格，面向机构用户。

### 2.2 TradingView Lightweight Charts v5

与当前 v4.2.3 为同一库，Apache 2.0 开源，TradingView 官方维护。

**v5 核心变化（2025 年 3 月发布）：**

原生多 pane——一个 `createChart()` 实例即可管理所有 pane：

```ts
const chart = createChart(container, {
  layout: {
    panes: {
      enableResize: true,          // 库内置可拖拽分隔线
      separatorColor: '#223344',
      separatorHoverColor: '#64748b',
    }
  }
})

// pane 0 = K 线（默认）
const candles = chart.addSeries(CandlestickSeries, {}, 0)
const volume  = chart.addSeries(HistogramSeries, {}, 0)

// pane 1 = RSI（自动创建该 pane）
const rsiLine = chart.addSeries(LineSeries, { color: '#a78bfa' }, 1)

// pane 2 = MACD
const macdLine = chart.addSeries(LineSeries, {}, 2)

// ★ 比例控制 — K 线 70%，RSI 30%，stretch factor 按比例自动分配
chart.panes()[0].setStretchFactor(0.70)
chart.panes()[1].setStretchFactor(0.30)
```

pane 管理 API（`IPaneApi`）：

```ts
chart.panes()                   // 获取所有 pane
chart.addPane()                 // 新增 pane
pane.setHeight(px)              // 固定高度
pane.setStretchFactor(ratio)    // 按比例（推荐）
pane.moveTo(index)              // 重排
series.moveToPane(index)        // 跨 pane 移动 series
chart.removePane(index)         // 删除 pane（同时删除其中的 series）
chart.swapPanes(a, b)           // 交换位置
```

API breaking change（v4 → v5）：

```ts
// v4 旧 API（不兼容）
chart.addLineSeries()
chart.addCandlestickSeries()
chart.addHistogramSeries()

// v5 新 API
chart.addSeries(LineSeries, options, paneIndex)
chart.addSeries(CandlestickSeries, options, paneIndex)
chart.addSeries(HistogramSeries, options, paneIndex)
```

此外 `IPriceLine`、`ISeriesApi` 部分方法签名有变化，需查迁移文档。

**指标**：v5 仍然**不内置指标**——RSI/MACD 等需要自己计算后 feed 给 series。这正是当前 `priceChartModel.ts` 在做的事，代码可以全量复用。

Bundle size：约 35 KB gzip（v4 约 43 KB）。

### 2.3 KLineChart v10

中国开发者 liihuu 主导，Apache 2.0，社区维护。**指标全内置**（MA/EMA/BOLL/MACD/RSI/KDJ/VOL/OBV/CCI 等），多 pane 原生支持。

```ts
const chart = init('container')

// K 线主图
chart.applyNewData(ohlcvData)

// 独立 pane，内置 RSI 计算
chart.createIndicator('RSI', false, {
  id: 'rsi_pane', height: 120, minHeight: 60, dragEnabled: true
})

// 独立 pane，内置 MACD 计算
chart.createIndicator('MACD', false, {
  id: 'macd_pane', height: 140, minHeight: 60, dragEnabled: true
})
```

v10.0.0 于 **2026-07-11** 正式发布（此前经历 alpha1–beta3 约 14 个月 breaking change）。

v9 → v10 主要 breaking：
- `applyNewData()` → `setDataLoader(loader)` 统一数据模型
- `setCustomApi()` → `setFormatter()`
- 自定义指标 `calc` 返回值从数组改为 timestamp 键对象
- `getIndicatorByPaneId()` → `getIndicators(filter)`
- tooltip `values` → `legends`，`icons` → `features`

Bundle size：约 40 KB gzip（含所有内置指标）。无 runtime dependency。

**React 封装**：无官方 wrapper，第三方 `react-klinecharts` 活跃度一般，可以用 `useEffect` 封装原始 API。

### 2.4 简要对比

| | **lw-charts v5** | **KLineChart v10** | **lw-charts v4（现状）** |
|---|---|---|---|
| 原生多 pane | ✅ | ✅ | ❌（手工模拟）|
| 内置指标 | ❌（需自算）| ✅ 全套 | ❌（需自算）|
| 指标数学可复用 | ✅ | ❌（完全替换）| — |
| pane 比例控制 | `setStretchFactor` | `paneOptions.height` | CSS Grid hack |
| 自带可拖拽分隔线 | ✅ | ✅ | ❌（手工实现）|
| 官方维护 | TradingView | 社区 | TradingView |
| API 稳定性 | 高（有规范迁移文档）| v10 刚发布 2 个月 | — |
| 迁移成本估计 | **1.5–2 天** | **3–5 天** | — |
| Bundle size | ~35 KB gzip | ~40 KB gzip | ~43 KB gzip |

---

## 三、KLineChart 尝试记录

在此次调研中对 KLineChart v9/v10 做了评估，**未实际集成**，记录如下以备将来参考。

### 为什么评估过 KLineChart

- 内置指标意味着可以删除 `priceChartModel.ts` 约 300 行自维护代码
- pane 高度由库内部管理，分隔线 bug 从架构上消失
- v9 的 React 用法可以封装为简单的 `useEffect` wrapper

### 为什么放弃

1. `priceChartModel.ts` 有完整单元测试、逻辑正确——这是已验证的投资，KLineChart 会强制废弃它
2. KLineChart v10 于 2026-07-11 才稳定，距调研仅 7 周，API 成熟度存疑
3. 没有 TradingView 官方背书，长期维护风险更高
4. 迁移成本约为路线 A 的 2–3 倍

### KLineChart v9 多 pane 用法摘要（供将来参考）

```ts
import { init } from 'klinecharts'

const chart = init('container', {
  layout: [
    { type: 'candle', content: ['MA', 'BOLL'], options: { id: 'candle_pane' } },
    { type: 'indicator', content: ['VOL'], options: { id: 'vol_pane', height: 80, minHeight: 40, dragEnabled: true } },
    { type: 'indicator', content: ['MACD'], options: { id: 'macd_pane', height: 120, minHeight: 60, dragEnabled: true } },
    { type: 'xAxis', options: { position: 'bottom' } }
  ]
})

chart.applyNewData(bars)

// 运行时动态添加指标
chart.createIndicator('RSI', false, {
  id: 'rsi_pane', height: 100, minHeight: 40, dragEnabled: true
})
```

### KLineChart v10 与 v9 主要 API 差异

```
applyNewData()          → setDataLoader(loader)
applyMoreData()         → setDataLoader(loader)  （统一数据模型）
setCustomApi()          → setFormatter()
getIndicatorByPaneId()  → getIndicators({ paneId })
tooltip.values          → tooltip.legends
tooltip.icons           → tooltip.features
clearData()             → resetData()
```

---

## 四、决策结论

**采用路线 A：升级 lightweight-charts v4.2.3 → v5，利用原生 pane API 重构 `PriceChart.tsx`。**

### 预期删除

- `paneChartsRef`（`Map<SubchartIndicatorId, IChartApi>`）及全部多实例管理代码
- `isSyncingRangeRef` + 手工 LogicalRange 同步（`subscribeVisibleLogicalRangeChange` 跨实例转发）
- `IndicatorPane` 组件中的 `createChart()` 调用
- `market-chart-divider` 自定义 pointerdown/move/up 拖拽逻辑
- `useLayoutEffect` 里的 CSS grid 行高计算（约 20 行）
- CSS `.market-chart-divider`、`.market-subchart-region` 相关布局规则

### 预期保留

- `priceChartModel.ts` 全部指标数学计算（直接 feed 给 v5 series）
- 数据加载、历史分页 (`loadOlder`)、实时轮询 (`pollLatest`) 逻辑
- `ChartSettings`、toolbar、settings modal、instrument navigator
- 所有 `priceChartModel.ts` 单元测试

### 默认 pane 比例目标

K 线主图 **72%**，副指标合计 **28%**，通过 `setStretchFactor` 控制，支持用户拖拽调整但受 28% 上限约束。

### 迁移工作量估计

约 1.5–2 天，风险点：
- `IPriceLine` API 签名变化（stop/entry/tp level lines）
- `IndicatorPane` 内部 series 注册需改为主图 chart 的 pane index
- `PriceChart.test.ts` 中涉及 `IChartApi` mock 的部分需更新

---

*本文件由调研对话生成，应与实际迁移 PR 同步更新。*
