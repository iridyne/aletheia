# Aletheia 市场研判页（Markets / Charts）与跨页面联动深度整改方案

> **文档定位**：针对操作员从 `Universe`（入场工作站/候选池）选拔出候选标的（Candidate）后，无法顺畅跳转至 `Markets`（`/charts`）查看交易所一手微观结构数据（POC、VAL、VAH、成交量分布 Volume Profile、盘口深度、点差及持仓量），以及当前 `Charts.tsx` 页面自身设计缺陷的专项整改与演进蓝图。

---

## 目录
1. [用户真实诉求与核心心智模型](#一-用户真实诉求与核心心智模型)
2. [现状深度解剖：四大断裂与结构性缺陷](#二-现状深度解剖四大断裂与结构性缺陷)
3. [整改方向 1：Universe $\to$ Markets 跨页面全链路无缝联动](#三-整改方向-1universe--markets-跨页面全链路无缝联动)
4. [整改方向 2：顶栏遥测条升级为“微观市场结构 HUD”（POC/VAL/点差）](#四-整改方向-2顶栏遥测条升级为微观市场结构-hudpocval点差)
5. [整改方向 3：重构 Volume Profile（绘制真正的水平成交量分布柱）](#五-整改方向-3重构-volume-profile绘制真正的水平成交量分布柱)
6. [整改方向 4：新增“标的微观体征与因果背景”专属面板（Market Structure Tab）](#六-整改方向-4新增标的微观体征与因果背景专属面板market-structure-tab)
7. [分阶段落地实施清单（极简代码改动矩阵）](#七-分阶段落地实施清单极简代码改动矩阵)

---

## 一、 用户真实诉求与核心心智模型

在 Aletheia 的自主交易闭环中，**Universe 页面负责回答“AI 选了什么标的（Candidate）以及为什么选”**，而 **Markets 页面负责回答“该标的在真实交易所中的物理微观结构与量价分布究竟如何”**。

```
[Universe 入场工作站] 
  候选集评选: UNI/USDT (Utility 60.3%, 突破动量)
         │
         ▼ 【一键点击: Open on Chart ↗】
[Markets 市场研判页]
  1. 瞬间载入该币种在币安/OKX 的实时 K 线
  2. 顶栏立即展示: POC (最大成交价) / VAL (价值区下轨) / VAH (价值区上轨) / 当前价格相对位置
  3. 顶栏或侧边立即可见: 10bps 深度、点差、资金费率、1h 仓位变化 (OI Delta)
  4. K 线画布右侧直接呈现真实的水平成交量分布柱状图 (Volume Profile Histogram)
```

操作员作为一个“监督者与审计者”，需要在这个流转过程中瞬间对 AI 的选拔做出定性核验，而不是在多个不相关的页面之间迷失。

---

## 二、 现状深度解剖：四大断裂与结构性缺陷

深入排查 `Universe.tsx`、`Charts.tsx`、`PriceChart.tsx` 与 `priceChartModel.ts` 源码后，发现当前实现存在以下四大严重脱节：

### 1. 跨页面导航断裂：候选池抽屉中根本没有去往图表的按钮！
* **代码事实**：
  - 在 `Universe.tsx:344` 中，只有当一个计划已经生成了 `EntryPlan` 时，`PlanDrawer` 才绑定了 `onCharts` 跳转按钮；
  - 而当用户点击正在评选的候选币（`selectedCandidateId`）打开 `CandidateDrawer`（`Universe.tsx:412-458`）时，**里面完全没有“View on Chart”或“Open in Markets”按钮**！
  - 在 `candidateColumns` 和 `currentCandidateColumns` 的数据表格行中，也没有提供点击标的快速跳转图表的快捷通道。操作员看到一个优质候选币，想看它的 K 线，必须自己记住名字，然后手动切到 `/charts`，再点开搜索框输入代码！

### 2. 顶栏上下文遥测条（Context Bar）严重贫血
* **代码事实** (`frontend/src/pages/Charts.tsx:729-738`)：
  当前顶部 `market-workbench-context` 仅渲染了极其寡淡的 6 个元素：
  ```tsx
  [Symbol 代码] [切换按钮] [当前价格] [24h涨跌幅] [交易所名称] [数据刷新时间]
  ```
  操作员最渴望看到的 **POC 价格、VAL/VAH 价值区包络、盘口深度、点差、资金费率**在顶部导航条中**完全不可见（0 呈现）**！

### 3. Volume Profile 算法计算了 48 个 Bin，却在最后把柱状图直接扔掉了！
* **代码事实** (`frontend/src/charts/priceChartModel.ts:53-96` & `PriceChart.tsx:343-354`)：
  在 `priceChartModel.ts` 中，`estimateVisibleVolumeProfile` 经过精密的二分查找和柱线重叠积分，**在内存中计算出了完整的 48 个价位区间的成交量数组 `volumes[48]`**；
  **然而在返回时**，它仅仅提取了：
  ```typescript
  return { poc: ..., val: ..., vah: ... }
  ```
  **将算好的整个 48 柱成交量分布直方图彻底丢弃了！**
  而在 `PriceChart.tsx` 中，它只是在 Canvas 上画了三根极其微弱的水平虚线（`Est. POC`, `Est. VAH`, `Est. VAL`）。操作员根本看不到真实的**水平成交量轮廓（Volume Profile）**，看不到高成交量节点（HVN）和低成交量节点（LVN）形成的阻力与支撑真空带！

### 4. 下方面板与候选池因果背景完全割裂
* **代码事实** (`frontend/src/pages/Charts.tsx:777-781`)：
  K 线下方目前放置了三个 Tab：`Signals`（历史老信号）、`News`（新闻流）、`Predictions`（预测市场）。
  而操作员从 Universe 跳转过来时最需要的**“当前候选币的选拔证据”、“AI 为什么挑中它”、“交易所盘口体征与深度”**没有任何位置呈现。

---

## 三、 整改方向 1：Universe $\to$ Markets 跨页面全链路无缝联动

彻底打通从选拔到研判的链路，消除任何操作断层：

### 1. 候选币抽屉（`CandidateDrawer`）增加置顶研判入口
在 `Universe.tsx` 的 `CandidateDrawer` 标题栏或操作区注入明确的直达按钮：
```tsx
// frontend/src/pages/Universe.tsx 中的 CandidateDrawer
<div className="drawer-actions">
  <Button 
    variant="secondary"
    onClick={() => navigate(`/charts?symbol=${encodeURIComponent(candidate.symbol)}&exchange=${encodeURIComponent(candidate.venue || 'binance')}&market_type=${encodeURIComponent(candidate.market_type || 'perp')}&candidate_id=${encodeURIComponent(candidateId)}`)}
  >
    <ChartCandlestick size={14} />
    <span>Inspect on Chart & Profile ↗</span>
  </Button>
</div>
```

### 2. 候选池表格第一列标的名支持点击秒跳
在 `candidateColumns` 中，将标的代码包装为可点击的语义链接：
```tsx
candidateCol.accessor('symbol', {
  header: 'Candidate',
  cell: info => (
    <Link 
      to={`/charts?symbol=${encodeURIComponent(info.getValue())}&market_type=${info.row.original.market_type}`}
      className="mono font-bold hover:underline"
      title="Open market chart and volume profile"
    >
      {fmtSymbol(info.getValue(), info.row.original.market_type)}
    </Link>
  ),
})
```

---

## 四、 整改方向 2：顶栏保持极简沉静，拒绝把 POC/VAL 堆砌成枯燥文本

### 1. 顶栏定位纠偏（克制、高信噪比）：
* **原则**：顶栏**绝不堆砌过多内容**。POC、VAL、VAH 这种与价格高度绑定的微观结构数据，以纯文字形式脱离 K 线单独写在顶栏是无意义的，因为缺乏空间价格感。
* **顶栏终态（单行纯粹态势条）**：
  ```
  [BTC/USDT PERP · BINANCE]   $63,420.50  ▲+2.41%   FR 0.0100%   VOL $1.28B
  ```
  仅保留标的、当前成交价、24h 涨跌、资金费率和成交量 5 项核心生命体征，零视觉负担。

---

## 五、 整改方向 3：POC 与 Market Profile 全量在图表画布内原生可视化

POC、VAL、VAH 以及成交量分布（Volume Profile）**最自然、最高效的归宿就是 K 线图表本身**。操作员视线聚焦在 K 线柱上，一目了然看清筹码峰与当前价格的相对空间位置：

### 1. 图表内原生层级设计（TradingView / Sierra Chart 标杆形态）：
1. **水平成交量柱（Volume Profile Histogram）**：
   - 在图表视窗右侧（价格刻度内侧），以轻量半透明水平横条绘制每个价位的成交量；
   - 价值区（Value Area 70%）使用微弱 Cyan 区分，筹码密集峰（HVN）与真空区（LVN）一览无余；
2. **POC 控制线切入图表**：
   - 在真实的 POC 价位上横向穿过一条高对比度水平实线，右侧价格刻度上标注醒目的 `POC` 胶囊；
3. **VAL / VAH 价值区包络线**：
   - 价值区上下界限直接作为虚线标注入图，无需操作员在顶栏看文字“心算”现价是否处于区间内。
### 1. 数据模型增强 (`priceChartModel.ts`)
保留并输出计算好的 48 个 bin 的直方图数据：
```typescript
export interface VisibleVolumeProfile {
  poc: number
  vah: number
  val: number
  bins: Array<{
    priceLow: number
    priceHigh: number
    volume: number
    isPoc: boolean
    isValueArea: boolean
  }>
}
```

### 2. 画布右侧轻量直方图渲染 (`PriceChart.tsx`)
* **第一性原理与奥卡姆剃刀**：不需要引入复杂的外部图表插件！
  利用一个轻量的透明 Canvas 覆盖层（Overlay Canvas），在主 K 线的右侧边界（或价格轴内侧）绘制 48 根半透明水平横条：
  - 普通价位柱：半透明浅灰 `rgba(125, 137, 151, 0.18)`；
  - 价值区内部（Value Area 70%）：半透明青色 `rgba(34, 211, 238, 0.25)`；
  - POC 最大成交柱：醒目的珊瑚红/亮绿 `rgba(46, 230, 168, 0.65)`。
* **效果**：操作员扫视 K 线右侧，立刻能看到“筹码峰在哪里，筹码真空区在哪里”，直观洞悉主力资金的成交密集带。

---

## 六、 整改方向 4：新增“标的微观体征与因果背景”专属面板（Market Structure Tab）

在图表下方的 `Tabs` 中，现有 `Signals`、`News`、`Predictions` 之外，**新增第 1 优先级的专属选项卡：`Structure & Profile`（微观结构与画像）**。

### 面板内容布局（不破坏现有排版，填补空白）：
1. **流动性与订单簿剖面（Liquidity & Orderbook Ladder）**：
   - 10bps / 20bps 买卖盘厚度；
   - 实时点差与近期冲击成本预估；
2. **衍生品杠杆拥挤度（Derivatives Crowding Metrics）**：
   - 资金费率（Funding Rate）与年化对比；
   - 1小时 / 24小时 未平仓合约（Open Interest）变化率；
   - 多空持仓比（Long/Short Ratio）；
3. **候选集选拔证据回溯（Candidate Selection Context）**：
   - 如果该币是从 Universe 跳转过来的，在此直接展示当时的选拔评分（Utility %、Rank、Family、入选原因），无需操作员再切回 Universe 查看。

---

## 七、 分阶段落地实施清单（极简代码改动矩阵）

| 阶段 | 实施任务 | 涉及代码文件 | 改动规模 |
|---|---|---|---|
| **Phase 1 (跳转联动)** | 1. `Universe.tsx` 候选币抽屉与表格增加 `/charts` 跳转按钮<br>2. `Charts.tsx` 接收 `candidate_id` 自动设置标的 | `Universe.tsx`<br>`Charts.tsx` | ~25 行代码 |
| **Phase 2 (顶栏 HUD)** | 1. 在 `market-workbench-context` 呈现 POC / VAL / VAH 数值<br>2. 补充显示点差（Spread）与 10bps 深度 | `Charts.tsx`<br>`PriceChart.tsx` | ~30 行代码 |
| **Phase 3 (直方图实体化)** | 1. `priceChartModel.ts` 导出 48-bin `bins` 数组<br>2. 在图表右侧 Canvas 层轻量绘制水平柱 | `priceChartModel.ts`<br>`PriceChart.tsx` | ~45 行代码 |
| **Phase 4 (专属选项卡)** | 在图表下方新增 `Structure & Profile` 选项卡，整合微观结构 | `Charts.tsx` | ~60 行代码 |

$$\textbf{总计：仅约 160 行纯原生代码，完全不需要大改现有页面排版，即可让 Market 页面成为真正合格的金融级微观研判终端！}$$

---

*（本文档已完整保存至本地：`docs/design/frontend/market_page_and_cross_linking_rectification.md`，作为后续实施 Markets 页面重构与跨页面联动的权威技术指南）*
