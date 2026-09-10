# Aletheia 全站标的瞬态穿透（Symbol Teleportation）与微观数据真实性设计规范

> **核心原则**：
> 1. **全站标的即时穿透（Symbol Everywhere is a Link）**：全站任何页面、表格、卡片中只要出现具体币种，均可 1 步无阻跳转至 `/charts` 市场研判页。
> 2. **数据可用性第一性原理（Grounded Realism）**：严禁臆造后端不存在的实时 500 档全量 L2 盘口阶梯。彻底基于 `microstructure.py` 和 `derivatives.py` 的真实落库结构，做最扎实、最诚实的高价值微观体征展示。

---

## 目录
1. [后端微观市场数据的真实边界（为什么不做全量 L2 深度阶梯？）](#一-后端微观市场数据的真实边界为什么不做全量-l2-深度阶梯)
2. [全站 8 大页面标的展示与跳转缺失盘点（全量清查）](#二-全站-8-大页面标的展示与跳转缺失盘点全量清查)
3. [奥卡姆剃刀解法：统一定义 `<SymbolLink>` 原语](#三-奥卡姆剃刀解法统一定义-symbollink-原语)
4. [真实可行的“微观结构画像”落地规范（不臆造不存在的数据）](#四-真实可行的微观结构画像落地规范不臆造不存在的数据)
5. [重构实施清单与影响面对齐](#五-重构实施清单与影响面对齐)

---

## 一、 后端微观市场数据的真实边界（为什么不做全量 L2 深度阶梯？）

用户指出的这一点切中了系统的核心物理约束：**“我们把它做成现在这个样子是有一定原因的”**。

### 1. 后端采集器事实追踪 (`aletheia/data/collectors/microstructure.py`)
经过对后端 `MicrostructureCollector` 源码的深入审计：
```python
# microstructure.py:56
ob = await exchange.fetch_order_book(fetch_sym, limit=20)
```
* **物理事实**：
  - 后端并非通过长时间保持高吞吐的 WebSocket 挂靠 500 档盘口 L2 深度流；
  - 而是为了**防止触发交易所 API 频次风控、避免 Fedora 本地 SQLite 产生数 GB 的写入放大（Write Amplification）**，采用了受控的周期性轻量抽样（`limit=20`）；
  - 在落库模型 `OrderbookSnapshot` 中，后端提炼并保存的核心指标仅为：
    - `spread_bps`（买卖价差基点，如 `2.21 bps`）
    - `bid_depth_10bps_usd` / `ask_depth_10bps_usd`（离中间价 10bps 以内的买卖盘真实承载金额，如 `$118k`）
    - `total_depth_10bps_usd`（10bps 总深度）
    - `imbalance_ratio`（买卖盘倾斜比：$\frac{bid - ask}{bid + ask}$，介于 `-1.0` 到 `+1.0` 之间）
    - `fetch_latency_ms`（抓取耗时）

### 2. 设计裁决（Design Verdict）：
* **严禁臆造**：前端绝对不能试图去画一个 TradingView 式的“全量 500 档动态滑动盘口阶梯（DOM Ladder）”，因为底层根本没有持久化那几百个档位的实时订单流。
* **诚实表达**：前端应当完整展示后端**真正提炼出来的 4 个高价值衍生指标**：
  1. **点差**（`Spread: X bps`）
  2. **10bps 紧密流动性深度**（`10bps Depth: $Xk`）
  3. **订单簿失衡比**（`Orderbook Imbalance: +0.24 (Bid Skew)`）
  4. **合约资金费率与持仓增减**（`Funding: +0.01% · OI 1h: +1.8%`）
  这 4 个指标完全基于已有数据，0 新增网络开销，且直击核心交易员关切！

---

## 二、 全站 8 大页面标的展示与跳转缺失盘点（全量清查）

全面排查全站 13 个页面后，发现以下 **8 个页面、17 处标的展示**存在跳转缺失或链接断裂：

```
┌────────────────────────┬──────────────────────────┬───────────────────────────────┐
│ 页面 / 模块            │ 具体渲染位置             │ 当前现状与缺陷诊断            │
├────────────────────────┼──────────────────────────┼───────────────────────────────┤
│ 1. Universe.tsx        │ 候选池表格第一列         │ 纯文本 <strong>，不可点击     │
│                        │ CandidateDrawer 抽屉     │ 仅展示只读字段，无跳转按钮    │
│                        │ Planning History 表格    │ 纯文本 <span>，不可点击       │
├────────────────────────┼──────────────────────────┼───────────────────────────────┤
│ 2. TradesPositions.tsx │ 存量活跃持仓表格         │ 纯文本 <span>，不可点击       │
│                        │ 历史已平仓表格           │ 纯文本 <span>，不可点击       │
│                        │ 活跃委托订单表格         │ 纯文本 <span>，不可点击       │
│                        │ 对账差异表格 (Reconcile) │ 纯文本 <td>，不可点击         │
├────────────────────────┼──────────────────────────┼───────────────────────────────┤
│ 3. AgentActivity.tsx   │ 独立机会列表 (Opps Tab)  │ 纯文本 <span>，不可点击       │
│                        │ Opportunity 详情抽屉     │ 有 "View on Chart"，但未传    │
│                        │                          │ 参数，永远死板跳到 BTC/USDT ! │
├────────────────────────┼──────────────────────────┼───────────────────────────────┤
│ 4. RiskControls.tsx    │ 每币种风险暴露量条       │ 纯文本 <span>，不可点击       │
│                        │ (Exposure by Symbol)     │ 看到巨额敞口想看盘却点不进去  │
├────────────────────────┼──────────────────────────┼───────────────────────────────┤
│ 5. CommandCenter.tsx   │ Top Symbols 盈亏小表     │ 纯文本 <td>，不可点击         │
│                        │ AI 核心假设 (Theses) 列表│ 纯文本 <span>，不可点击       │
├────────────────────────┼──────────────────────────┼───────────────────────────────┤
│ 6. Overview.tsx        │ 资产持仓分布列表         │ 纯文本 <span>，不可点击       │
│    (PnLZone.tsx)       │ (Asset breakdown)        │                               │
├────────────────────────┼──────────────────────────┼───────────────────────────────┤
│ 7. NewsIntel.tsx       │ 新闻卡片关联币种标签     │ 纯文本 Badge，没有关联图表    │
│                        │ (symbols_mentioned)      │ 看到利好新闻无法一键看 K 线   │
├────────────────────────┼──────────────────────────┼───────────────────────────────┤
│ 8. SerenityLens.tsx    │ 宏观卡点标的列表         │ 纯文本 <td>，不可点击         │
│                        │ (Chokepoint items)       │                               │
└────────────────────────┴──────────────────────────┴───────────────────────────────┘
```

---

## 三、 奥卡姆剃刀解法：统一定义 `<SymbolLink>` 原语

为了避免在 17 个地方各自手写繁复的 `onClick={() => navigate('/charts?symbol=...')}`，按照奥卡姆剃刀原则，**封装 1 个极简原语组件（约 20 行代码）**：

### 1. 原语实现 (`frontend/src/components/ui/SymbolLink.tsx`)
```tsx
import { Link } from 'react-router-dom'
import { fmtSymbol } from '../../lib/format'

interface SymbolLinkProps {
  symbol: string | null | undefined
  marketType?: string | null
  exchange?: string | null
  className?: string
  bold?: boolean
}

export function SymbolLink({ symbol, marketType = 'spot', exchange, className = '', bold = false }: SymbolLinkProps) {
  if (!symbol) return <span>—</span>

  const params = new URLSearchParams()
  params.set('symbol', symbol)
  if (marketType && marketType !== 'spot') params.set('market_type', marketType)
  if (exchange) params.set('exchange', exchange)

  const formatted = fmtSymbol(symbol, marketType)

  return (
    <Link
      to={`/charts?${params.toString()}`}
      className={`symbol-link mono ${bold ? 'font-bold' : ''} ${className}`}
      title={`Inspect ${formatted} on Markets`}
      onClick={e => e.stopPropagation()} // 阻止向外冒泡展开表格行
    >
      {formatted}
    </Link>
  )
}
```

### 2. 交互与微样式规范 (`index.css`)
```css
/* 沉静而清晰的标的超链接质感 */
.symbol-link {
  color: var(--text);
  text-decoration: none;
  border-bottom: 1px dotted var(--border-strong);
  transition: color 80ms ease, border-color 80ms ease;
}

.symbol-link:hover {
  color: var(--accent); /* 悬浮唤醒 Signal Cyan */
  border-bottom: 1px solid var(--accent);
}
```

---

## 四、 真实可行的微观结构遥测规范（严禁臆造，杜绝解释性废话）

在 `Charts.tsx` 顶栏遥测区中，严禁添加任何“（物理落库）”、“（OHLCV计算）”、“（20档汇总）”等侮辱专业交易员智商的解释性废话。直接以 Bloomberg / TradingView 标准的紧凑金融指标呈现：

```
┌───────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ UNI/USDT PERP · BINANCE   $4.5262  ▲+2.41%   FR 0.0100% (10.9% ANN)   OI 1H +1.8%   IMB +0.14        │
│ POC 4.5240   VAL 4.4100   VAH 4.7500   SPREAD 2.2 bp   DEPTH 10BP $118k   24H VOL $101.8M             │
└───────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

所有字段均直接对应真实指标，绝不堆砌冗余修饰词：
* `FR`：资金费率（Funding Rate）；
* `OI 1H`：1小时持仓量增减率；
* `IMB`：盘口失衡度（Imbalance Ratio）；
* `POC / VAL / VAH`：筹码分布关键价位；
* `SPREAD`：盘口点差（基点）；
* `DEPTH 10BP`：10个基点深度金额。

### 数据源真实映射表（保证每一项都有真实字段支撑）：
1. **POC / VAL / VAH**：
   - 数据源：`priceChartModel.ts` 内置的 `estimateVisibleVolumeProfile(bars)`。直接消费已有 K 线柱，0 后端依赖。
2. **10bps 紧密深度与点差**：
   - 数据源：后端 `orderbook_snapshots` 表中的 `total_depth_10bps_usd` 和 `spread_bps`。
3. **买卖盘失衡（Imbalance）**：
   - 数据源：`orderbook_snapshots` 表中的 `imbalance_ratio`。大于 0 表示买盘厚，小于 0 表示卖盘压制。
4. **资金费率与持仓量（Funding & OI）**：
   - 数据源：后端 `derivatives_snapshots` 表中的 `funding_rate` 与 `oi_delta_1h_pct`。

---

## 五、 重构实施清单与影响面对齐

| 模块 | 原有缺陷 | 改造后效果 |
|---|---|---|
| **全站标的展现** | 8 个页面、17 处散落的纯文本 `fmtSymbol(...)` | 全量替换为 `<SymbolLink />`，任意位置点击 100ms 直达行情与筹码分布 |
| **AgentActivity 修复** | Opportunity Drawer 的 `navigate('/charts')` 缺失参数，死板跳到 BTC | 修复为传递 `selected.symbol` 和 `selected.market`，解决老 Bug |
| **微观结构展示** | 试图想象不存在的全量 L2 订单簿阶梯 | 聚焦呈现 10bps 深度、点差、失衡比与衍生品资金费，符合系统物理真实 |
| **代码与依赖增量** | 0 外部包安装，0 大规模排版重构 | 仅引入 1 个 20 行通用组件 `<SymbolLink>` 与微量 CSS，保持系统极简 |

---

*（本文档已完整保存至本地：`docs/design/frontend/architecture/cross_page_symbol_teleportation_and_realistic_market_data.md`，确立了全站标的秒级穿透与微观市场数据诚实表达的技术标准）*
