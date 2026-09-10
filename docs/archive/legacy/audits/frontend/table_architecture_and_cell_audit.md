# Aletheia 全站表格、列定义与单元格内容深度审计与重构验收报告

> **审计范围**：Web APP 全站 13 个页面中的全部 **26 个数据表格**（涵盖 `@tanstack/react-table` 定义的列与原生 `<table>` / CSS Grid 模拟表）。  
> **审计准则**：**内容重复性、内容必要性（No Fluff）、第一性原理与奥卡姆剃刀（拒绝过度拆分与奇怪拼接）**。  
> **重构验收状态**：**核心交易类、活动类与指标类表格均已完成高信息密度复合列重组与收敛，并已合并至 master（86f492cd1 / d54b10cf3）**。
---

## 目录
1. [全站表格排查总览（26 个表格清单）](#一-全站表格排查总览26-个表格清单)
2. [第一类硬伤：序号、Rank、Cycle 强塞进单元格（违背奥卡姆剃刀）](#二-第一类硬伤序号rankcycle-强塞进单元格违背奥卡姆剃刀)
3. [第二类硬伤：同一类属性被生硬拆分为多个冗余独立列（过度碎片化）](#三-第二类硬伤同一类属性被生硬拆分为多个冗余独立列过度碎片化)
4. [第三类硬伤：大杂烩式中点拼接字符串（Mega-String 单元格）](#四-第三类硬伤大杂烩式中点拼接字符串mega-string-单元格)
5. [第四类硬伤：重复后缀噪声与空间浪费（Redundant Unit Repetition）](#五-第四类硬伤重复后缀噪声与空间浪费redundant-unit-repetition)
6. [26 个表格逐一诊断与重构对比清单（Before vs After）](#六-26-个表格逐一诊断与重构对比清单before-vs-after)
7. [通用表格重构工程规范与原子组件演进](#七-通用表格重构工程规范与原子组件演进)

---

## 一、 全站表格排查总览（26 个表格清单）

经过全局代码排查，Aletheia 前端共拥有 26 处表格结构：

| 编号 | 页面 / 模块 | 表格用途 | 实现方式 | 当前列数 | 主要问题概述 |
|---|---|---|---|---|---|
| **T01** | `Universe.tsx` | 入场计划表 (`planColumns`) | TanStack Table | 8 列 | 止盈止损复合列命名混乱 |
| **T02** | `Universe.tsx` | 候选集评选表 (`candidateColumns`) | TanStack Table | 7 列 | **Rank 硬塞文字**，超长大杂烩拼接 |
| **T03** | `Universe.tsx` | 候选集工作台 (`currentCandidateColumns`)| TanStack Table | 6 列 | 来源与 Rank 挤在同一列 |
| **T04** | `Universe.tsx` | 规划历史表 (`historyColumns`) | TanStack Table | 5 列 | **cycle 轮次硬塞文字** |
| **T05** | `TradesPositions.tsx` | 存量活跃持仓 (`openColumns`) | TanStack Table | 12 列 | **标的/交易所/市场拆 3 列**；uPnL 与来源拆 2 列；止盈止损拆 2 列 |
| **T06** | `TradesPositions.tsx` | 历史已平仓 (`closedColumns`) | TanStack Table | 8 列 | PnL 与 PnL 状态拆 2 列 |
| **T07** | `TradesPositions.tsx` | 活跃委托订单 (`orderColumns`) | TanStack Table | 7 列 | 订单类型与意图展示混乱 |
| **T08** | `TradesPositions.tsx` | 历史已成交 (`fillsColumns`) | TanStack Table | 6 列 | 费用与费率展示冗余 |
| **T09** | `AgentActivity.tsx` | 智能体运行日志 (`runColumns`) | TanStack Table | 6 列 | 输出数量直接显示裸数字 |
| **T10** | `AgentActivity.tsx` | 独立机会记录 (`oppColumns`) | TanStack Table | 7 列 | 来源与市场属性拆分生硬 |
| **T11** | `AgentActivity.tsx` | 抽屉工具调用表 | 原生 `<table>` | 4 列 | **表头直接写 `#` 序号** |
| **T12** | `AgentActivity.tsx` | 抽屉执行步骤表 | 原生 `<table>` | 4 列 | **表头直接写 `#` 序号** |
| **T13** | `Learning.tsx` | 知识库条目表 (`kbColumns`) | TanStack Table | 6 列 | 生产者 ID 截断过短 |
| **T14** | `Learning.tsx` | 提取教训表 (`lessonColumns`) | TanStack Table | 5 列 | 摘要文本溢出无气泡 |
| **T15** | `Learning.tsx` | 风控防线候选 (`guardColumns`) | TanStack Table | 5 列 | 基准惩罚为空时缺乏语义 |
| **T16** | `Usage.tsx` | 智能体消耗表 (`agentColumns`) | TanStack Table | 7 列 | 调用次数与运行次数并列冗余 |
| **T17** | `Usage.tsx` | 模型维度消耗 (`modelColumns`) | TanStack Table | 8 列 | **Input/Output Tokens 拆为 2 列** |
| **T18** | `Usage.tsx` | 提供商维度消耗 (`providerColumns`)| TanStack Table | 7 列 | 统计维度与模型表重复 |
| **T19** | `Usage.tsx` | 工具调用消耗 (`toolColumns`) | TanStack Table | 6 列 | 拒绝次数与通过次数可合并 |
| **T20** | `Usage.tsx` | 预算配额表 (`budgetColumns`) | TanStack Table | 5 列 | 周期与限额展示不紧凑 |
| **T21** | `PredictionLens.tsx`| 预测市场异动表 | 原生 `<table>` | 6 列 | YES/24h 变化应复合展示 |
| **T22** | `PredictionLens.tsx`| 预测扫描运行历史 | 原生 `<table>` | 6 列 | **Started 与 Completed 拆 2 列时间** |
| **T23** | `SerenityLens.tsx` | 长期体制运行记录 | 原生 `<table>` | 7 列 | **Started 与 Finished 拆 2 列时间** |
| **T24** | `Charts.tsx` | 活跃交易信号表 | 原生 `<table>` | 6 列 | 缺乏当前价格参考 |
| **T25** | `TradeInsightsPanel`| 分层离散分布表 (`sdm-table`) | 模拟表格 | 4 列 | **表头直接写 `<span>#</span>`** |
| **T26** | `AdvancedTab.tsx` | 系统配置全量只读表 | 原生 `<table>` | 3 列 | Key 与表面重叠 |

---

## 二、 第一类硬伤：序号、Rank、Cycle 强塞进单元格（违背奥卡姆剃刀）

### 1. 典型违规代码事实
* **案例 1：表头直接写大写的 `#`（毫无意义的行占位符）**
  - `TradeInsightsPanel.tsx:505, 575`：
    ```html
    <div className="sdm-header">
      <span>Bucket</span><span>#</span><span>WR%</span><span>Avg%</span>
    </div>
    ```
  - `AgentActivity.tsx:429`：
    ```html
    <thead><tr><th>#</th><th>Action</th><th>Input</th><th>Output</th></tr></thead>
    ```
  - **违背第一性原理**：对于表格数据而言，除非序号是全局具备业务主键性质的凭证编号，否则单纯的行号（`1, 2, 3...`）对交易研判毫无价值，纯属浪费横向空间的视觉废话。
* **案例 2：把 Rank、Cycle 拼成纯文字硬塞在指标列中**
  - `Universe.tsx:126`：
    ```typescript
    candidateCol.accessor(row => `Rank ${row.rank ?? '—'} · ${selectionMetric(row)} · Liquidity ${row.liquidity_score ?? '—'} · Spread ${row.spread_bps ?? '—'} bps`, ...)
    ```
  - `Universe.tsx:138`：
    ```typescript
    currentCandidateCol.accessor(row => `Rank ${row.rank} · ${selectionMetric(row)} · ${humanize(row.scanner_family)}`, ...)
    ```
  - `Universe.tsx:150`：
    ```typescript
    historyCol.accessor(row => `${humanize(row.outcome_class ?? row.status)} · cycle ${row.cycle_number ?? 1}`, ...)
    ```
  - **违背奥卡姆剃刀**：如果 Rank（排名）在业务上足够重要，它应当作为第一列的紧凑徽标（例如带圆形背景的 `1`, `2`, `3`）；如果它不重要，它就不该与流动性、点差拼成一长串字符串挤在同一个格子里。

---

## 三、 第二类硬伤：同一类属性被生硬拆分为多个冗余独立列（过度碎片化）

在桌面宽屏上开发时，很多开发者喜欢“有几个字段就建几个列”，导致在笔记本或平板上表格总宽度超过 1400px，被迫横向滚动。

### 1. `TradesPositions.tsx` 存量持仓表（8 列膨胀为 12 列）
* **现状代码** (`TradesPositions.tsx:157-221`)：
  - 第 1 列：`Symbol`（如 `BTC/USDT`）
  - 第 2 列：`Exchange`（如 `binance`）
  - 第 3 列：`Market`（如 `perp`）
  - 第 9 列：`Stop (USDT)`（止损价）
  - 第 10 列：`TP (USDT)`（止盈价）
  - 第 11 列：`uPnL (USDT)`（未实现盈亏数字）
  - 第 12 列：`uPnL source`（未实现盈亏来源，如 `local_estimate`）
* **第一性原理审查**：
  1. `Exchange` 和 `Market` 是 `Symbol` 的**从属修饰属性**。没有任何交易员会把交易所单独当成一列来筛选。将它们拆成 3 列，直接浪费了 240px 黄金宽度；
  2. `Stop`（止损）和 `TP`（止盈）是同一组**括号包络风控几何**（Bracket Orders）。分开成两列导致操作员需要左右晃头比对盈亏比；
  3. `uPnL source` 仅仅是一个次级审计标志，专门占一列极为浪费。
* 💡 **奥卡姆剃刀重构方案（12 列收敛为 7 列）**：
  - **合并为复合标的列**：主标题 `BTC/USDT`（等宽粗体） + 下方微注 `binance · perp`；
  - **合并为风险括号列**：`Bracket (SL / TP)` $\to$ `4.41 / 4.75`；
  - **合并盈亏列**：数值 `+$10.17 (+2.1%)` + 浮动角标或微标注明 `local`。

### 2. `PredictionLens.tsx` 与 `SerenityLens.tsx`：时间列生硬对半拆分
* **现状代码**：
  - `PredictionLens.tsx:305-309`：`<th>Started</th> ... <th>Completed</th>`
  - `SerenityLens.tsx:272-273`：`<th>Started</th> ... <th>Finished</th>`
* **第一性原理审查**：
  在运行日志表格中，操作员最关心的两件事是：**“什么时候跑的”** 以及 **“跑了多久”**。分别用 20 个字符展示两个绝对时间戳（如 `2026-09-04 16:42:00` 和 `2026-09-04 16:42:18`），逼迫操作员在大脑中心算做减法（18 - 0 = 18秒）。
* 💡 **奥卡姆剃刀重构方案**：
  - 合并为单一时间列：**`Execution (Started · Duration)`**  
    例如：`16:42:00 (18s)` 或 `Yesterday 14:00 (2m 10s)`。直接把时长算给操作员，消灭一整个 160px 的冗余时间列。

### 3. `Usage.tsx` Token 消耗拆分
* **现状代码** (`Usage.tsx:40-41`)：
  `accessorKey: 'input_tokens'`（单独一列）与 `accessorKey: 'output_tokens'`（单独一列）。
* 💡 **奥卡姆剃刀重构方案**：
  合并为 **`Tokens (Total · In / Out)`**：例如主显 `12.5k`，下方浅色显示 `10.2k / 2.3k`。

---

## 四、 第三类硬伤：大杂烩式中点拼接字符串（Mega-String 单元格）

### 1. 典型违规：`Universe.tsx` 候选集表格
* **代码定位**：`Universe.tsx:126`
  ```typescript
  candidateCol.accessor(
    row => `Rank ${row.rank ?? '—'} · ${selectionMetric(row)} · Liquidity ${row.liquidity_score ?? '—'} · Spread ${row.spread_bps ?? '—'} bps`, 
    { id: 'quality', header: 'Selection metric / market quality' }
  )
  ```
* **现场现象**：这个单元格被渲染为极其刺眼的超长单行文本：
  `Rank 1 · Utility 60.3% · Liquidity 60.7 · Spread 2.2 bps`
* **缺陷分析**：
  1. **不可检索与不可排序**：TanStack Table 无法单独针对点差（Spread）或效用（Utility）进行数字升降序排序；
  2. **阅读阻力极大**：四个毫不相干的物理维度（名次、效用、流动性得分、基点点差）用 `·` 强行缝合，完全违反了数据表格的基本对齐原则。
* 💡 **重构方案**：
  - 彻底拆分或结构化呈现：
    - 列 1：`Candidate (Rank · Symbol)` $\to$ 前置徽标 `[1]` + `UNI/USDT`
    - 列 2：`Economic Quality` $\to$ `Utility 60.3%`（高亮）
    - 列 3：`Liquidity / Spread` $\to$ 上层 `Score 60.7`，下层 `2.2 bps`

---

## 五、 第四类硬伤：重复后缀噪声与空间浪费（Redundant Unit Repetition）

### 1. 典型违规：表头带单位，单元格每一行还要重复塞单位
* **代码定位**：`TradesPositions.tsx:184-214`
  - 表头写了：`Entry (USDT)`、`Current (USDT)`、`uPnL (USDT)`、`Stop (USDT)`、`TP (USDT)`
  - 但在有的实现中，单元格又写了 `${fmtNum(v)} USDT`
* **第一性原理**：既然表头已经明确定义了计量货币为 `(USDT)`，单元格内部绝对不能再重复渲染 `USDT` 字符。
* **反面典型**：`PaperTradingSettings.tsx:143`
  `{event.action} · {Number(event.amount_usdt).toFixed(2)} USDT`，在一行里反复出现 `USDT`，降低了数字本身的可扫视性。

---

## 六、 26 个表格逐一诊断与重构对比清单（Before vs After）

以下是对全站 26 个表格的穷尽式优化建议表：

### 1. 核心交易与资产类表格

#### 【T05】`TradesPositions.tsx` 存量持仓表 (`openColumns`)
* **当前列（12 列）**：Symbol \| Exchange \| Market \| Dir \| Qty \| Entry \| Current \| Lev \| Stop \| TP \| uPnL \| uPnL source
* **重构后列（6 列，消灭 50% 冗余）**：
  1. **`Instrument` (标的)**：`BTC/USDT` (粗体) + 换行下沉 `binance · perp · 2x`
  2. **`Side / Qty` (方向与规模)**：`LONG` (带色) + 换行下沉 `0.250` (名义本金)
  3. **`Price (Entry / Current)` (价格对)**：入场价 + 现价纵向对齐
  4. **`Risk Bracket (SL / TP)` (风控括号)**：`4.41 / 4.75` (带盈亏比 R:R)
  5. **`Unrealized PnL` (浮动盈亏)**：`+$120.40 (+4.8%)` (角标注明 `live` 或 `local`)
  6. **`Actions` (操作)**：平仓/市价减仓快捷入口

#### 【T06】`TradesPositions.tsx` 历史已平仓表 (`closedColumns`)
* **当前列（8 列）**：Symbol \| Dir \| Qty \| Entry \| PnL \| PnL status \| Close reason \| Closed at
* **优化点**：
  - 删除独立的 `PnL status` 列（只有 `complete` 和 `partial` 两种状态，完全作为微标融合在 `PnL` 数值下方）；
  - `Closed at`（关闭时间）与 `Duration`（持仓时长）合并为同一列。

#### 【T01】`Universe.tsx` 入场计划表 (`planColumns`)
* **当前列（8 列）**：Exact instrument \| Side \| Entry \| TriggerSpec \| Stop / Target / R:R \| Execution / tactic \| Status \| Expiry / countdown
* **优化点**：
  - `TriggerSpec` 与 `Execution / tactic` 合并为 **`Execution Tactic`** 列（例如：`Maker (Post-only) · Momentum Breakout`），减少一列冗余。

#### 【T02】`Universe.tsx` 候选集评选表 (`candidateColumns`)
* **当前列（7 列）**：Candidate \| Family / why selected \| Selection metric / market quality \| Entry lifecycle \| Last requalified \| Soft / hard deadline \| Terminal reason
* **致命优化**：
  - 彻底解体 `Selection metric / market quality` 这个包含 4 个中点的大杂烩列；
  - 拔除强塞进字符串里的 `Rank 1`，将 Rank 作为第一列原生紧凑索引。

---

### 2. 智能体活动与认知类表格

#### 【T11 & T12】`AgentActivity.tsx` 抽屉工具调用与执行步骤表
* **当前表头**：`<tr><th>#</th><th>Action</th><th>Input</th><th>Output</th></tr>`
* **优化点**：
  - **彻底删除 `<th>#</th>` 列**！表格自带顺序排列，根本无需显式占用第一列展示 `1, 2, 3`；
  - 若需表达步骤序列，直接在 `Action` 前加精简数字前缀（如 `1. detect_guards`），为右侧长文本 `Input` 与 `Output` 腾出至少 50px 宽度。

#### 【T09】`AgentActivity.tsx` 智能体运行日志 (`runColumns`)
* **当前列（6 列）**：Agent \| Status \| Trigger \| Provider \| Outputs \| Started
* **优化点**：
  - `Trigger` 与 `Provider` 合并（例如：`Scheduled · openai`）；
  - `Outputs` 列的裸数字改为带语意胶囊（如 `3 artifacts`），若为 0 显示灰色的 `none`。

#### 【T13】`Learning.tsx` 知识库条目表 (`kbColumns`)
* **当前列（6 列）**：Title \| Type \| Confidence \| Status \| Producer \| Created
* **优化点**：
  - `Confidence`（置信度）目前占用一整列显示 `high/medium`，建议作为微标下沉在 `Title` 标题右侧，收缩表格横向宽度。

---

### 3. 消耗核算与透镜类表格

#### 【T17】`Usage.tsx` 模型维度消耗表 (`modelColumns`)
* **当前列（8 列）**：Model \| Provider \| Calls \| Success \| Input \| Output \| Cost \| P95
* **优化点**：
  - `Input` 与 `Output` 两列合并为一列 **`Tokens (In / Out)`**；
  - `Calls` 与 `Success` 合并为一列 **`Invocations (Count · Success%)`**。

#### 【T22】`PredictionLens.tsx` 预测扫描历史表
* **当前列（6 列）**：Started \| Status \| Markets \| Opps Created \| Completed \| Errors
* **优化点**：
  - **消灭双重绝对时间列**：`Started` 与 `Completed` 强行拆为两列，改用 `Started · Duration`（如 `16:42:00 (12s)`）。

#### 【T23】`SerenityLens.tsx` 体制运行历史表
* **当前列（7 列）**：Status \| Trigger \| Mode \| Started \| Finished \| Themes \| Claims
* **优化点**：
  - 同上，消灭独立的 `Finished` 列，改为 `Started (Duration)` 复合表现。

#### 【T25】`TradeInsightsPanel.tsx` 离散分布表 (`sdm-table`)
* **当前表头**：`<span>Bucket</span><span>#</span><span>WR%</span><span>Avg%</span>`
* **优化点**：
  - 废除无意义的 `<span>#</span>` 表头，改为明确的 `Trades`（交易笔数）。

---

## 七、 通用表格重构工程规范与原子组件演进

为了彻底防止“强塞序号、粗暴中点拼接、生硬分列”在后续开发中反复回潮，确立以下 4 条铁律：

### 1. 复合单元格标准排版模板（Composite Cell Pattern）
同一实体的次级属性必须采用“上下结构”，严禁采用“左右中点字符串拼接”：
```tsx
// ❌ 错误示范 (大杂烩拼接)
<td>Rank 1 · BTC/USDT · binance · perp</td>

// ✅ 正确示范 (上下结构紧凑对齐)
<td>
  <div className="flex items-center gap-1.5">
    <span className="badge-rank">1</span>
    <strong className="mono">{fmtSymbol(sym)}</strong>
  </div>
  <div className="text-muted text-xs mt-0.5">binance · perp</div>
</td>
```

### 2. 绝对时间与相对耗时合并原则（Temporal Consolidation）
任何历史运行记录、扫描日志表格，**严禁同时并列两列完整的 ISO 日期时间**。
* 统一规范为：**`Started (Duration)`**。

### 3. 表格墨水比优化原则
* 删除一切纯计数的 `<th>#</th>` 排占位列；
* 表头标明单位后（如 `PnL ($)`），单元格内部必须是纯粹干净的等宽数字，严禁每一行都重复缀带 `USDT` 字符。

---

*（本文档已归档保存至本地：`docs/archive/legacy/audits/frontend/table_architecture_and_cell_audit.md`）*
