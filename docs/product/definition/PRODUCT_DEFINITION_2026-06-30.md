# Aletheia 产品定义

来源：2026-06-30 owner 深度对话，8 个领域全量确认
状态：已定案，后续修改须 owner 明确授权

---

## 1. 决策主体 — PI Operator

- **架构**：单 LLM 主循环（A 路，ReAct 风格）
- **下层 observer**：降级为工具（tool calls），PI 自主编排调用顺序和深度
- **自主度**：Live 模式开启后全权操作交易，UI 有醒目警示
- **宪法**（风控阈值 / prompt / 执行边界）：人类独占修改权，PI 不可自改
- **B 路 council**：保留为 Phase 2 条件触发升级路径——方向信号互相打架或 risk_critic 拒绝率突然飙升时，临时召开 bull/bear 两视角比对（2 次额外 LLM 调用）

参考依据：OpenAlice（ReAct 单 agent）、hermes-agent、AlphaQuanter 均为 A 路；TradingAgents 是唯一 B 路但为研究框架无实盘 PnL 证据。

---

## 2. 交易标的与市场

- **主赛道**：永续合约（perp）+ 现货（spot）并轨，同一个 System Operator 管理
  - market_type 字段区分，执行层 adapter 分叉
  - 全局风控（risk_engine）单点否决，不因品类分离而出现盲区
- **第二赛道**：xStocks（ON 系列）独立隔离，与 Serenity executor 捆绑
  - 独立风险池，不混入主赛道 PnL
  - PI 是主赛道唯一决策者；Serenity executor 是第二赛道唯一决策者

---

## 3. 数据面

**必须有**：OHLCV（多周期：1m/5m/1h/4h/1d）、资金费率、订单簿微结构、跨 venue OI、清算数据、技术指标预计算（RSI/MACD/BB 等）

**免费替代替换死路**：
| 死路 | 替代 | 消费者 |
|------|------|--------|
| CoinGlass（paywall） | Coinalyze 免费 tier | OI + 清算 → PI context |
| Glassnode（无免费） | DefiLlama | TVL + 资金流向 → 宏观情绪模块 |
| 跨 venue 资金费率孤儿 | Hyperliquid API（骨架已有） | PI context 套利/方向信号 |

**数据利用契约（强制前置）**：新数据源必须先在 `docs/design/data/data_utilization.md` §3 登记表补行（来源/消费者/状态）+ 加 `spec_data_utilization.py` DU-* claim，再写采集代码。死代码（清算死代码、OI Bitget 吞数据）直接删。

---

## 4. 学习 / 进化机制

- **学习输入**：早期两路并行——个例 reflection（每笔平仓后）+ 批次模式归纳（定期）
- **可改范围**：
  - Ranker 权重（低风险）：可自动修改
  - PI system prompt 片段（中风险）：可修改
  - 风控阈值：暂不开放，Phase 2 后评估
- **推翻机制**：方案1（置信度更新）× 方案2（时间衰减 half-life）× 方案3（PI 决策时主动过滤）三路融合，设计待深度研究后确定
- **研究计划**：`docs/archive/legacy/plans/learning/learning_mechanism_research_plan.md`

---

## 5. 风控与执行

- **关卡**：现状保持（16→20 关），等 Round 2 数据说话再调松紧
- **执行链**：PI → signal_pending → risk engine 审核 → 执行，不可绕过，risk engine 最终否决权不变
- **动态止损**：允许，PI 在持仓 review 时顺带输出"建议调整止损到 X"，position_manager 接收执行；不单独建模块，不占 PI 主循环核心注意力

---

## 6. 监管 / 自愈

- **开放档位**：L1（自动修数据）+ L2（自动修配置/prompt 片段）全开放
- **前置条件**：任何自愈操作执行前必须经 PI 评估批准（PI 读诊断报告 → 输出批准/拒绝+理由）
- **日志要求**：每次操作强制写结构化日志（操作类型/触发原因/执行结果/前后状态对比）
- **操作分级**：
  | 操作 | 级别 | 策略 |
  |------|------|------|
  | 清理过期 heartbeat/日志 | L1 自动 | PI 批准后直接执行 |
  | 重启卡死的 collector | L1 自动 | PI 批准后直接执行 |
  | 重建 SQLite 索引 | L1 自动+日志 | PI 批准后执行，写前后对比 |
  | 清除 signal_pending 队列 | L2 人批 | PI 评估后推送通知，等人确认 |
  | 修改 prompt/配置 | L2 人批 | 同上 |
- **隔离要求**：自愈操作可在交易中执行，但必须先过 PI 评估

---

## 7. 人机界面

- **Overview**：一眼判生死，对标 cockpit 行业最佳实践优化布局
- **Trades/Positions**：查证溯源保留在侧边栏下钻，现有设计继承
- **Command Center**（独立页面）：
  - 查询对话框（向 PI 提问，非下指令）
  - 状态指标面板（PI 运行状态数字）
  - 推理链可视化（当前快照 / 历史时间线可切换，内容大时可拖拽）
  - **心跳区域**：
    - 第一行：发光绿点（呼吸动画）+ `THINKING` eyebrow + 相对时间戳 + PI 最新思考摘要
    - 第二行：每个持仓一张子卡片，左列 symbol + PnL（`fmtSigned()` + `fmtDelta()`），右列持仓理由（3 行截断 + accent 竖线）
    - 复用：`StatusDot.tsx`、`AgentFeed.tsx` 结构、`index.css` 颜色变量
    - 动效克制：只有绿点呼吸 + PnL 颜色切换过渡

---

## 8. 失败与撤退

- **Kill 触发条件**：
  - 连续 2 轮干净窗 PF < 0.5
  - 单轮最大回撤超 30%
  - LLM 成本超账户盈利 50%
  - 发现无法修复的安全漏洞
  - （原有）3 轮实验均未 Go
- **收尾姿势**：持仓自然平（不强制市价）；审计日志永久保留；代码仓库打 tag 归档不删
- **个人终点线**：时间窗口从 Round 2 正式开始算；指标不变（PF ≥ 1.5 / 胜率 ≥ 50% / 回撤 < 15%）

---

## 待研究 / 待实施（不丢，按序推进）

| 事项 | 优先级 | 文档 |
|------|--------|------|
| 学习推翻机制深度研究（Phase 1-3） | 高 | `docs/archive/legacy/plans/learning/learning_mechanism_research_plan.md` |
| Command Center 心跳区域实现 | 中 | 本文档 §7 + `CommandCenter.tsx` |
| 数据源替换（Coinalyze/DefiLlama/Hyperliquid） | 中 | 需先登记 `data_utilization.md` |
| spot-parity 分支 owner 审核入 master | 中 | 分支 `worktree-demo-live-spot-parity-event` |
| Serenity 第二赛道边界细化 | 低 | 待 owner 确认"Serenity Len"定义 |
| B 路 council 条件触发（Phase 2） | 低 | 等 Round 2 稳跑后评估 |
| 风控关卡松紧调整 | 低 | 等 Round 2 数据 |
| 动态止损轻量实现 | 低 | PI 持仓 review 附带输出 |
