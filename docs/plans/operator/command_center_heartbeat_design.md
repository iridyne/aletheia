# Command Center 心跳区域设计方案

状态：待实现
来源：2026-06-30 产品深度对话 第7领域

## 定位

`CommandCenter.tsx` 顶部 PageHeader 下方、两栏布局之前，全宽插入。
平行于 Overview 页 PnLZone 的视觉地位：先给眼睛落脚点，再进入操作区。

## 布局：三格横排卡片

`display: flex; gap: var(--space-4)` 三列等宽，≤1000px 单列堆叠。

### 左格 — PI 正在思考
- 呼吸绿点 `.status-light.light-green.heartbeat` + Badge `Thinking/Idle`
- `operatorStatus?.current_intent` 全文，mono 字体 0.82rem，3行 line-clamp
- 底部 `last_seen_at` 右对齐 text-muted
- 状态：`mode==='Working'` 绿点；否则 dot-muted + Idle

### 中格 — 当前持仓 PnL
- 复用 `useQuery(['portfolio'])` unrealized_pnl + unrealized_by_symbol
- `fmtDelta` + `fmtPct` 并列，`pnl-positive/negative` 颜色
- 每个持仓一行 status-board 表格样式，最多 5 行
- 数字变化触发 `.tick-flash-up/.tick-flash-down`（600ms 淡出）

### 右格 — 持仓理由
- 前 3 个持仓：symbol + 一句 thesis 摘要
- 字段来源：`opportunity.thesis`（参考 AgentFeed.tsx）
- 无持仓：`text-muted text-sm` 占位

## 动效原则
- 活物感靠节奏（呼吸点），不靠颜色
- 不用 spinning loader（等待语义，不合这里）
- refetchInterval: 10000；SSE 推送时直接 invalidateQueries
- 不引入任何 inline style 新颜色，完全用现有 CSS 变量

## 关键文件
- `frontend/src/pages/CommandCenter.tsx` — 插入位置
- `frontend/src/pages/Overview.tsx` — 布局参考
- `frontend/src/components/PnLZone.tsx` — PnL 数据用法
- `frontend/src/components/AgentFeed.tsx` — thesis 字段来源
- `frontend/src/index.css` — `.heartbeat`(L2396) / `@keyframes pulse`(L2397) / `.tick-flash-*`(L2577)
