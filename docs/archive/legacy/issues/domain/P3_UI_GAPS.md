# P3 UI Gaps — 待后续修复

> 来源：竞品分析 (Bitget/OKX/3Commas/Pionex) RY 加权优先级排序
> 创建日期：2026-06-21

## P3.11 — Strategy runtime duration timer

**Gap**: 策略运行时间显示（如 Pionex "运行 23 天 4 小时"）。当前只有 created_at/updated_at 静态时间戳。
**竞品参考**: Pionex bot card 顶部的实时运行时长。
**实现思路**: `StrategyLifecycle` 页面添加 "Running for" 列，用 `Date.now() - created_at` 实时计算 + `useReducer` 强制刷新。

## P3.12 — Grid/Entry-Exit visualization

**Gap**: 网格策略的价格区间可视化（上下边界 + 网格线）和入场/出场价位标注。
**竞品参考**: Pionex Grid Bot 的网格图；3Commas 的 deal entry/TP/SL 标线。
**实现思路**: `lightweight-charts` `addPriceLine()` API，在 PriceChart 上按策略参数绘制水平线。需要策略 → 参数映射。

## P3.13 — Asset breakdown table (with pie chart)

**Gap**: 资产构成饼图 / 表格（各币种持仓占比）。
**竞品参考**: Bitget/OKX 资产总览的 pie chart + allocation table。
**实现思路**: 从 `portfolio_snapshots` 或 positions 聚合各 symbol 占比，用 recharts `PieChart` 渲染。数据结构需要后端增加 per-symbol equity breakdown。

## P3.14 — WebSocket replacing polling

**Gap**: 用 WebSocket 替代当前 5s~30s 轮询，实现实时 push（价格/订单/agent 状态）。
**竞品参考**: 所有专业交易平台均使用 WS push。
**实现思路**: 后端已有 `/ws/events` 路由占位。前端需要 `useWebSocket` hook 替代部分 `refetchInterval` 逻辑。优先级较低因为当前轮询在本地网络足够快。

## P3.15 — Stop condition collapsible area

**Gap**: 策略的停止条件（最大亏损 / 最大持仓时间 / trailing stop）可视化折叠区域。
**竞品参考**: 3Commas "Safety Orders" / OKX "Stop Conditions" 折叠面板。
**实现思路**: 在策略 Drawer 中用 `CollapsibleSection` 展示 stop/exit 条件参数。需要后端策略 schema 暴露这些字段。
