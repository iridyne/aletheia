# Charts 图表库迁移方案：lightweight-charts v4 → v5

> 状态：**待实施**  
> 决策背景：见 [charts_library_research.md](./charts_library_research.md)

---

## 目标

用 lightweight-charts v5 的**原生多 pane API** 替换当前多实例模拟方案，一次性解决：

1. 分隔线默认位置错误（K 线被压在上方 15–30%）
2. crosshair 跨 pane 不同步
3. 时间轴同步的 `isSyncingRangeRef` 补丁
4. 自定义拖拽分隔线代码

---

## SDD Depth

**D1** — 外部可见行为变化：

- ADDED：`layout.panes.enableResize`（库原生拖拽分隔线）
- MODIFIED：分隔线默认位置从非确定值改为 K 线 72% / 副图 28%
- REMOVED：`market-chart-divider` DOM 元素及其交互逻辑（用户无感知，行为等价）
- UNCHANGED：所有指标计算逻辑、数据加载、toolbar、ChartSettings、instrument navigator

---

## 受影响文件

| 文件 | 变化类型 |
|---|---|
| `frontend/package.json` | `lightweight-charts` 版本 `^4.2.3` → `^5.x` |
| `frontend/src/charts/PriceChart.tsx` | 大幅重写（架构变化） |
| `frontend/src/index.css` | 删除 `.market-chart-divider`、`.market-subchart-region` 布局规则 |
| `frontend/src/charts/priceChartModel.ts` | **不变** |
| `frontend/src/charts/PriceChart.test.ts` | 更新 mock 和类型引用 |

---

## v5 多 pane API 速查

### 创建图表（一个实例管全部 pane）

```ts
import { createChart, CandlestickSeries, HistogramSeries, LineSeries } from 'lightweight-charts'

const chart = createChart(container, {
  layout: {
    panes: {
      enableResize: true,
      separatorColor: chartTheme.border,
      separatorHoverColor: chartTheme.accent,
    },
    background: chartTheme.layout.background,
    textColor: chartTheme.layout.textColor,
  },
  width: container.clientWidth,
  height: container.clientHeight,
  timeScale: { visible: true, timeVisible: true, rightOffset: 4 },
})
```

### 添加 series 到指定 pane

```ts
// pane 0（默认，K 线主图）
const candles = chart.addSeries(CandlestickSeries, {
  upColor: chartTheme.upColor,
  downColor: chartTheme.downColor,
  borderVisible: false,
}, 0)

const volume = chart.addSeries(HistogramSeries, {
  priceFormat: { type: 'volume' },
  priceScaleId: 'vol',
}, 0)

// pane 1（RSI）——addSeries 时若 pane 不存在会自动创建
const rsiLine = chart.addSeries(LineSeries, { color: chartTheme.accountEquityRaw }, 1)
```

### 设置默认比例（核心 fix）

```ts
// 调用时机：数据加载完成后，chart.panes() 长度确定后
const panes = chart.panes()

if (panes.length === 1) {
  // 无副图，无需设置
} else {
  panes[0].setStretchFactor(0.72)
  // 剩余副图平分 0.28
  for (let i = 1; i < panes.length; i++) {
    panes[i].setStretchFactor(0.28 / (panes.length - 1))
  }
}
```

### 时间轴同步（v5 原生，无需手工）

v5 单实例内所有 pane 的时间轴**自动同步**，不需要 `subscribeVisibleLogicalRangeChange` 跨实例转发。crosshair 也自动跨 pane 显示。

### pane 管理方法

```ts
chart.panes()                       // IPaneApi[]
chart.addPane()                     // 手动新增（通常不需要，addSeries 自动创建）
pane.setHeight(px)                  // 固定高度（px）
pane.setStretchFactor(ratio)        // 比例（推荐）
pane.moveTo(index)                  // 重排 pane 顺序
series.moveToPane(index)            // 将 series 移到另一个 pane
chart.removePane(index)             // 删除 pane 及其 series
chart.swapPanes(a, b)               // 交换两个 pane 的位置
```

---

## 迁移步骤

### Step 1 — 升级依赖

```bash
cd frontend
npm install lightweight-charts@^5
```

确认 `package.json` 中 `lightweight-charts` 版本更新，`fancy-canvas`（v5 新增 runtime dep）被自动安装。

### Step 2 — 修复 `PriceChart.tsx` 导入

v5 series 类型需要显式导入：

```ts
// 旧（v4）
import { createChart, type IChartApi, type ISeriesApi, type LogicalRange, type Time } from 'lightweight-charts'

// 新（v5）
import {
  createChart,
  CandlestickSeries, HistogramSeries, LineSeries,
  LineStyle, CrosshairMode, PriceScaleMode,
  type IChartApi, type ISeriesApi, type IPaneApi,
  type LogicalRange, type Time,
} from 'lightweight-charts'
```

### Step 3 — 重构主图创建

**删除** `priceContainerRef` + `paneChartsRef` + 多实例逻辑。改为单实例多 pane：

```ts
// 旧：每个 IndicatorPane 组件各自 createChart()
// 新：在 PriceChart useEffect 内，createChart() 一次

const chart = createChart(container, {
  layout: {
    ...chartTheme.layout,
    panes: {
      enableResize: true,
      separatorColor: chartTheme.border,
      separatorHoverColor: chartTheme.accent,
    }
  },
  // ... 其他选项不变
})

// pane 0：K 线
const candles = chart.addSeries(CandlestickSeries, { ... }, 0)
const volume  = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'vol' }, 0)
volume.priceScale().applyOptions({ scaleMargins: { top: 0.76, bottom: 0 } })

// EMA / BB overlay series 同 pane 0，不变

// 副图指标：根据 subcharts 列表按顺序分配 pane 1, 2, ...
// （在 `subcharts` 变化时通过 removePane/addSeries 动态管理）
```

### Step 4 — 删除 IndicatorPane 组件

`IndicatorPane` 组件负责每个副图的 `createChart()`，v5 后**完全删除**。

副图 series 的注册、数据更新逻辑迁移到主 `useEffect` 内，按 `subcharts` 数组顺序映射到 pane 1, 2, ...

### Step 5 — 设置默认比例

在数据首次加载完成后（`chart.timeScale().fitContent()` 之后）：

```ts
const applyDefaultStretch = () => {
  const panes = chart.panes()
  if (panes.length <= 1) return
  panes[0].setStretchFactor(0.72)
  const subPaneRatio = 0.28 / (panes.length - 1)
  for (let i = 1; i < panes.length; i++) {
    panes[i].setStretchFactor(subPaneRatio)
  }
}
```

### Step 6 — 删除手工拖拽逻辑

以下代码**全部删除**：
- `subchartRegionPx` state
- `draggingRef`
- `onDividerPointerDown` handler
- `useLayoutEffect` 中的 `--market-chart-grid-rows` CSS 变量写入
- `useEffect(() => { setSubchartRegionPx(null) }, [subcharts.length])`

库原生的 `enableResize: true` 替代。

### Step 7 — 精简 CSS

删除：
```css
.market-chart-divider { ... }           /* 删除 */
.market-chart-divider::after { ... }    /* 删除 */
.market-chart-divider:hover { ... }     /* 删除 */
.market-subchart-region { ... }         /* 删除 */
```

`.market-chart-stage` 的 CSS grid 相关规则也不再需要，可简化为：
```css
.market-chart-stage {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  background: var(--surface);
}
```

### Step 8 — 时间轴同步清理

**删除**：
- `paneChartsRef`（`useRef<Map<SubchartIndicatorId, IChartApi>>(new Map())`）
- `isSyncingRangeRef`
- `registerPane` / `unregisterPane` / `onPaneRange` 函数
- `onMainRange` 中的跨实例 range 同步循环
- `getInitialRange` props 传给 IndicatorPane 的逻辑

v5 单实例内时间轴同步由库自动处理。

### Step 9 — 更新 priceScale 引用

v5 中为特定 pane 的 price scale 指定选项时需要带 pane index：

```ts
// v4
chart.priceScale('right').applyOptions(...)

// v5（针对特定 pane 的 right price scale）
chart.priceScale('right', 0).applyOptions(...)  // pane 0
chart.priceScale('rsi-scale', 1).applyOptions({ scaleMargins: { top: 0.1, bottom: 0.1 } })
```

### Step 10 — 更新测试

`PriceChart.test.ts` 中涉及：
- `IChartApi` mock 增加 `panes()` 方法
- 删除对 `IndicatorPane` 单独 chart 实例的引用
- 更新 `marketDataMetaState` 测试（函数本身不变，只确认导出路径正确）

---

## 验证清单

- [ ] `npm --prefix frontend run build` 通过（tsc + vite build 无报错）
- [ ] `npm --prefix frontend test` 全绿
- [ ] `/charts` 页面加载后，K 线区域占 stage 高度约 72%，RSI 约 28%
- [ ] 鼠标在 K 线区域和 RSI 区域移动，crosshair 跨 pane 正常显示
- [ ] 时间轴缩放/平移，两个 pane 同步滚动
- [ ] 拖拽 pane 分隔线可调整比例
- [ ] 缩小浏览器窗口，两个 pane 按比例缩放，不出现最小高度卡死
- [ ] 切换副图指标（如 RSI → MACD），比例重置为默认值
- [ ] 副图设为 0 个时，主图填满，无残留 pane

---

## 风险与注意点

1. **`IPriceLine` API 变化**：stop/entry/tp level line 的 `createPriceLine` 参数在 v5 有细节调整，迁移时需对照文档验证
2. **`setMarkers` 方法**：v5 仍存在，但签名有变化，确认 marker 数组格式兼容
3. **Volume series 叠加在主图**：v4 中 volume 用 `priceScaleId: ''` 绑定到隐藏 scale，v5 中机制相同但需确认 `scaleMargins` 仍有效
4. **历史回滚**：若 v5 迁移出现阻塞问题，`priceChartModel.ts` 完全不变，可随时回退到 v4 架构

---

*本文件为迁移执行参考，完成后在此处更新状态为 DONE 并记录实际 PR 号。*
