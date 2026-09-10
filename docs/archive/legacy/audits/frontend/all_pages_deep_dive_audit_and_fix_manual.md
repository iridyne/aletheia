# Aletheia Web UI 全 13 页面深度缺陷排查与修复验收手册（100% 已修复）

> **文档定位**：针对 Aletheia Web APP 全站 13 个核心页面进行的**逐页穿透式排查与缺陷修复验收手册**。  
> **核心目标**：查漏补缺，不遗漏任何一处潜在崩溃、数据割裂、导航断链、可访问性缺失与排版瑕疵，并为每一个缺陷提供**代码级、立即可实施的专业修复方案**。  
> **验收状态**：**全站 13 个页面的全部缺陷排查项均已 100% 完成修复并合并入库 master（86f492cd1 / d54b10cf3）**。
---

## 目录
1. [Overview.tsx（驾驶舱概览页）](#1-overviewtsx驾驶舱概览页)
2. [CommandCenter.tsx（交互式终端与指令中枢）](#2-commandcentertsx交互式终端与指令中枢)
3. [Charts.tsx（市场研判与图表工作站）](#3-chartstsx市场研判与图表工作站)
4. [Universe.tsx（入场工作站与候选池）](#4-universetsx入场工作站与候选池)
5. [TradesPositions.tsx（统一台账与持仓监控）](#5-tradespositionstsx统一台账与持仓监控)
6. [RiskControls.tsx（风控刹车操纵台）](#6-riskcontrolstsx风控刹车操纵台)
7. [Learning.tsx（认知演进与复盘工作台）](#7-learningtsx认知演进与复盘工作台)
8. [Usage.tsx（大模型消耗与调用核算）](#8-usagetsx大模型消耗与调用核算)
9. [NewsIntel.tsx（新闻情报流）](#9-newsinteltsx新闻情报流)
10. [PredictionLens.tsx（预测市场透镜）](#10-predictionlenstsx预测市场透镜)
11. [SerenityLens.tsx（长周期体制透镜）](#11-serenitylenstsx长周期体制透镜)
12. [AgentActivity.tsx（智能体运行目录）](#12-agentactivitytsx智能体运行目录)
13. [Settings.tsx（全局配置中心）](#13-settingstsx全局配置中心)

---

## 1. Overview.tsx（驾驶舱概览页）

### 缺陷排查 1.1：`EntrySummaryStrip` 跳转目标缺乏意图指向
* **代码定位**：`frontend/src/pages/Overview.tsx:298`
* **问题现状**：简报条写了 `role="link" onClick={() => navigate('/universe')}`。当简报条中高亮提示 `Needs Planning: 1 CANDIDATE` 时，操作员点击整条，却只能盲目跳到默认的 `/universe?tab=plans`。操作员本来是为了解决待规划候选币，结果跳过去面对的是现存计划列表，还得手动切到候选 Tab。
* **专业修复方案**：
  ```tsx
  // 改造后：根据当前待办权重精准路由
  onClick={() => {
    if (needsPlanning && needsPlanning > 0) {
      navigate('/universe?tab=candidates')
    } else {
      navigate('/universe?tab=plans')
    }
  }}
  ```

### 缺陷排查 1.2：`GoLiveScoreCard` 展开折叠字形（Chevron）不统一
* **代码定位**：`frontend/src/pages/Overview.tsx:347`
* **问题现状**：全站所有折叠组件（`StatusRow`、`App.tsx` 等）统一使用 `▶`（收起）与 `▼`（展开）。而 `GoLiveScoreCard` 却使用了 `▲`（展开）与 `▼`（收起），字形语义颠倒且全站不统一。
* **专业修复方案**：
  ```tsx
  // 替换前：{expanded ? '▲' : '▼'}
  // 替换后：统一全站标准字符
  <span className="text-muted text-xs" aria-hidden="true">{expanded ? '▼' : '▶'}</span>
  ```

---

## 2. CommandCenter.tsx（交互式终端与指令中枢）

### 缺陷排查 2.1：SSE 实时事件流无保护 `JSON.parse`（高危崩溃 Bug）
* **代码定位**：`frontend/src/pages/CommandCenter.tsx:728-730`
  ```tsx
  const handleOperatorEvent = (event: MessageEvent<string>) => {
    const parsed = JSON.parse(event.data) as OperatorEvent // ◀── 裸写，无 try/catch
    startTransition(() => {
      qc.setQueryData<OperatorEvent[]>(['operator-events'], current =>
        mergeEvents(current || [], parsed),
      )
    })
  }
  ```
* **潜在危害**：服务端在使用 SSE（Server-Sent Events）时，为了维持代理心跳，常会发送 `: ping`、空帧或纯文本注释。一旦这类数据到达，`JSON.parse` 会立即抛出未捕获的 `SyntaxError`，导致事件监听器中断，整页实时控制流挂死！
* **专业修复方案**：
  ```tsx
  const handleOperatorEvent = (event: MessageEvent<string>) => {
    if (!event.data || event.data.startsWith(':')) return
    try {
      const parsed = JSON.parse(event.data) as OperatorEvent
      startTransition(() => {
        qc.setQueryData<OperatorEvent[]>(['operator-events'], current =>
          mergeEvents(current || [], parsed),
        )
      })
    } catch {
      // 忽略心跳或脏数据，保护控制台不挂死
    }
  }
  ```

### 缺陷排查 2.2：切换会话分支（Session）后输入焦点丢失
* **代码定位**：`frontend/src/pages/CommandCenter.tsx:608-614`
* **问题现状**：操作员在左侧点击切换不同会话（如从 `Command` 切到 `Review`）后，光标焦点停留在列表按钮上。操作员想立即打字下达新指令时，键盘输入无响应，必须再次用鼠标点击输入框。
* **专业修复方案**：
  在 `selectedSessionId` 变更的 `useEffect` 中注入自动重聚焦：
  ```tsx
  useEffect(() => {
    inputRef.current?.focus()
  }, [selectedSessionId])
  ```

---

## 3. Charts.tsx（市场研判与图表工作站）

### 缺陷排查 3.1：时间周期溢出菜单负坐标隐藏黑客（`-9999px`）
* **代码定位**：`frontend/src/pages/Charts.tsx:253`
  ```tsx
  style={pos ? { top: pos.top, left: pos.left, visibility: 'visible' } : { top: -9999, left: -9999, visibility: 'hidden' }}
  ```
* **问题现状**：利用负 9999 像素将菜单踢到视窗外部。窗口滚动或多屏缩放时，菜单会偶现微小布局跳动。
* **专业修复方案**：
  改为条件渲染：
  ```tsx
  {timeframeMenuOpen && (
    <TimeframeMenu
      anchorRef={timeframeOverflowRef}
      current={timeframe}
      onSelect={v => { setTimeframe(v); setTimeframeMenuOpen(false); }}
      onClose={() => setTimeframeMenuOpen(false)}
    />
  )}
  ```

### 缺陷排查 3.2：48 柱成交量分布直方图在计算后被丢弃
* **代码定位**：`frontend/src/charts/priceChartModel.ts:95`
* **问题现状**：算法耗费 CPU 计算了完整的 48 柱成交量分布，但 `return { poc, vah, val }` 丢弃了数组。
* **专业修复方案**：在返回值中补全 `bins: Array<{ price, volume, isPoc, isValueArea }>`，并在 `PriceChart.tsx` 画布右侧绘制半透明水平横条。

---

## 4. Universe.tsx（入场工作站与候选池）

### 缺陷排查 4.1：候选币抽屉（`CandidateDrawer`）缺失看盘跳转
* **代码定位**：`frontend/src/pages/Universe.tsx:412-458`
* **问题现状**：只有已生成计划的 `PlanDrawer` 有 `onCharts` 按钮；正在选拔的候选币抽屉没有任何去往图表的出口。
* **专业修复方案**：
  在 `CandidateDrawer` 抽屉顶部增加直达按钮：
  ```tsx
  <Button
    variant="secondary"
    className="btn-sm"
    onClick={() => navigate(`/charts?symbol=${encodeURIComponent(candidate.symbol)}&market_type=${candidate.market_type || 'perp'}`)}
  >
    Inspect on Markets ↗
  </Button>
  ```

### 缺陷排查 4.2：超长中点大杂烩单元格
* **代码定位**：`frontend/src/pages/Universe.tsx:126`
  `Rank 1 · Utility 60.3% · Liquidity 60.7 · Spread 2.2 bps` 缝合在单列中，无法排序。
* **专业修复方案**：将 Rank、Utility、Liquidity/Spread 拆解为标准等宽表格列。

---

## 5. TradesPositions.tsx（统一台账与持仓监控）

### 缺陷排查 5.1：存量持仓表列过度碎片化（12 列膨胀）
* **代码定位**：`frontend/src/pages/TradesPositions.tsx:157-221`
* **问题现状**：`Symbol`、`Exchange`、`Market` 拆占 3 列；`Stop` 与 `TP` 拆占 2 列；`uPnL` 与 `uPnL source` 拆占 2 列。笔记本屏幕被撑到 1400px 宽，被迫横向滚动。
* **专业修复方案**：
  收敛为 6 组复合列：
  1. `Instrument`（标的代码 + 换行下沉 `exchange · market · leverage`）
  2. `Side / Qty`（方向带色 + 数量）
  3. `Price`（入场价 + 现价垂直对齐）
  4. `Bracket`（`Stop / TP` 复合区间 + R:R 盈亏比）
  5. `Unrealized PnL`（数值带微标注明来源）
  6. `Actions`（平仓按钮）

### 缺陷排查 5.2：滚动丢表头（Header Drift）
* **代码定位**：`frontend/src/components/DataTable.tsx`
* **问题现状**：向下滚动 50 行后，`<thead>` 滚出屏幕上方。
* **专业修复方案**：在 `DataTable.tsx` 增加 `th { position: sticky; top: 0; z-index: 2; background: var(--surface); }`。

---

## 6. RiskControls.tsx（风控刹车操纵台）

### 缺陷排查 6.1：每币种风险暴露条标的不可点击
* **代码定位**：`frontend/src/pages/RiskControls.tsx:476-488`
* **问题现状**：操作员看到 `ETH/USDT` 敞口高达 `$15,000`，想点进去看盘，点击无任何反应。
* **专业修复方案**：将 `<span className="exposure-sym mono">{fmtSymbol(sym)}</span>` 替换为 `<SymbolLink symbol={sym} marketType={marketTypeBySymbol[sym]} />`。

### 缺陷排查 6.2：只读限制卡片缺乏直接编辑入口
* **代码定位**：`frontend/src/pages/RiskControls.tsx:512`
* **问题现状**：写着 `meta="Read-only — edit in Settings → Risk."`，但没有超链接。
* **专业修复方案**：右侧加入 `<Link to="/settings?tab=risk&section=risk_limits">Edit in Settings ↗</Link>`。

---

## 7. Learning.tsx（认知演进与复盘工作台）

### 缺陷排查 7.1：`ActivityTimeline` 无保护 `JSON.parse`
* **代码定位**：`frontend/src/components/ActivityTimeline.tsx:24`
  ```tsx
  subtitle: r.outputs_created ? `outputs: ${JSON.parse(r.outputs_created || '[]').length}` : undefined,
  ```
* **问题现状**：未用 try-catch 包裹，遇到非法字符串组件直接崩溃。
* **专业修复方案**：复用 `Learning.tsx` 中成熟的 `parseJsonArray` 辅助函数。

---

## 8. Usage.tsx（大模型消耗与调用核算）

### 缺陷排查 8.1：费用为 null 时呈现刺眼的 `$N/A` 缺乏指引
* **代码定位**：`frontend/src/pages/Usage.tsx:84`
* **问题现状**：当模型未配置 token 单价时，卡片展示 `$N/A`，柱状图展示 `N/A for selected window`，未解释原因。
* **专业修复方案**：增加提示文本：`"Pricing unconfigured in Settings"`，并引导前往配置。

---

## 9. NewsIntel.tsx（新闻情报流）

### 缺陷排查 9.1：新增新闻表单控件缺乏无障碍属性
* **代码定位**：`frontend/src/pages/NewsIntel.tsx:146-150`
* **问题现状**：表单中的 `<input>` 裸写，没有 `<label>` 或 `aria-label`，仅依赖 `placeholder`，违背可访问性规范。
* **专业修复方案**：为每个输入项绑定 `aria-label="News Title"`、`aria-label="Source"`。

### 缺陷排查 9.2：新闻卡片中提及的币种标签不可点击
* **代码定位**：`frontend/src/pages/NewsIntel.tsx:219`
* **问题现状**：新闻提及 `BTC`、`ETH` 时仅渲染纯文本 Badge。
* **专业修复方案**：将币种标签替换为可跳转行情的 `<SymbolLink symbol={sym} />`。

---

## 10. PredictionLens.tsx（预测市场透镜）

### 缺陷排查 10.1：扫描历史表将时间拆为双列
* **代码定位**：`frontend/src/pages/PredictionLens.tsx:305-309`
* **问题现状**：`Started` 与 `Completed` 分占两列长日期字符串，逼迫用户心算耗时。
* **专业修复方案**：合并为 `Execution (Started · Duration)`，直接输出 `16:42:00 (18s)`。

### 缺陷排查 10.2：停用状态缺乏直接激活入口
* **代码定位**：`frontend/src/pages/PredictionLens.tsx:110`
* **问题现状**：观察器停用时只显示灰色 `disabled`，操作员必须跳到 Settings 去开。
* **专业修复方案**：右侧加入 `<Link to="/settings?tab=signals&section=prediction_markets">Enable in Settings ↗</Link>`。

---

## 11. SerenityLens.tsx（长周期体制透镜）

### 缺陷排查 11.1：`Started` 与 `Finished` 双列冗余
* **代码定位**：`frontend/src/pages/SerenityLens.tsx:272-273`
* **问题现状**：并列两列绝对时间。
* **专业修复方案**：合并为 `Started (Duration)` 复合列。

---

## 12. AgentActivity.tsx（智能体运行目录）

### 缺陷排查 12.1：抽屉中 "View on Chart" 漏传参数 Bug（经典老缺陷）
* **代码定位**：`frontend/src/pages/AgentActivity.tsx:328`
  ```tsx
  <Button variant="secondary" onClick={() => navigate('/charts')}>View on Chart</Button>
  ```
* **问题现状**：未携带任何查询参数，操作员点击任何机会，永远死板跳到 `BTC/USDT`！
* **专业修复方案**：
  ```tsx
  <Button
    variant="secondary"
    onClick={() => navigate(`/charts?symbol=${encodeURIComponent(selected.symbol)}&market_type=${encodeURIComponent(selected.market || 'spot')}`)}
  >
    View on Chart
  </Button>
  ```

### 缺陷排查 12.2：抽屉步骤表头直接写 `#` 序号
* **代码定位**：`frontend/src/pages/AgentActivity.tsx:429`
* **问题现状**：表头为 `<tr><th>#</th><th>Action</th><th>Input</th><th>Output</th></tr>`。
* **专业修复方案**：彻底剔除 `<th>#</th>` 列，将步骤编号直接内联至 `Action` 前缀（如 `1. detect_guards`），腾出 50px 宽度。

---

## 13. Settings.tsx（全局配置中心）

### 缺陷排查 13.1：35+ 处 `as any` 类型逃逸
* **代码定位**：`AgentModelsTab.tsx:73`、`ExecutionTab.tsx:50`、`RiskTab.tsx:55` 等。
* **问题现状**：全站明明有自动生成的 `settings.d.ts`，各 Tab 却大量强转 `as any`，破坏类型保护。
* **专业修复方案**：全面声明 `settings: SettingsResponse`，删除所有 `as any`。

### 缺陷排查 13.2：全站 80+ 处硬编码行内静态 `style={{ width: '80px' }}`
* **代码定位**：`SignalsTab.tsx:143`、`RuntimeSystemTab.tsx:555` 等。
* **问题现状**：违反 `ui_design_system.md §7`。
* **专业修复方案**：机械替换为 Tailwind v4 原子类（`w-20`、`w-28`、`mb-4`）。

---

*（本文档已归档保存至本地：`docs/archive/legacy/audits/frontend/all_pages_deep_dive_audit_and_fix_manual.md`）*
