# Aletheia Trading OS — 产品定义汇总
日期：2026-06-30
来源：产品深度对话（8个领域全覆盖）
状态：owner 确认，待逐步实施

---

## 领域 1：决策主体 — PI Operator

- **架构**：A 路，单 LLM 主循环（ReAct 风格），下层 observer 降级为工具，PI 自主编排调用顺序和深度
- **对标**：OpenAlice（点名标杆）、hermes-agent、AlphaQuanter 均为 A 路；唯一 B 路实现（TradingAgents）是研究框架无实盘证据
- **自主度**：开启 Live 模式后 PI 全权操作交易，UI 有醒目警示（操作不可预测、可能亏损、需人类承担后果）
- **宪法**：风控阈值 / 执行边界 / prompt 章程 — 人类专有修改权，PI 在宪法内全权，不能松自己的绑
- **人格化**：defer，早期目标只是能自主跑 + 正向盈利
- **B 路升级路径**：保留为 Phase 2 条件触发选项——平时 A 路，当方向信号互相打架或 risk_critic 拒绝率突然飙升时，临时召开 bull/bear 两视角比对（2次额外 LLM 调用）。目前没有任何参考实现做过这个，是差异化点

---

## 领域 2：交易标的与市场品类

- **主赛道**：永续合约（perp）+ 现货（spot）并轨，同一个 PI agent 决策，market_type 字段区分，执行层 adapter 分叉，全局风控单点否决
- **依据**：分 agent 会破坏 risk_engine 全局头寸上限，造成合计敞口盲区；perp/spot 同属短线 crypto，不满足"时间尺度根本不同"的分 agent 触发条件
- **第二赛道**：xStocks 独立隔离，与 Serenity Len 捆绑，独立风险池，独立执行者（serenity_executor），不混入主赛道 PnL
- **Serenity Lens**：xStocks 第二赛道的研究工作台（Research Workbench）。分工：Serenity Lens 研究+发现机会 → PI 评估 → serenity_executor 执行。不直接交易，不混入主赛道 PnL

---

## 领域 3：数据面

- **必须有**：多周期 OHLCV（1m/5m/1h/4h/1d）、资金费率、订单簿微结构、跨 venue OI、清算数据、跨 venue 资金费率对比、技术指标预计算（RSI/MACD/BB 等）
- **原则**：技术分析相关数据尽量全收集
- **死路源替换**：
  - CoinGlass（paywall）→ Coinalyze 免费 tier（OI + 清算 → PI context）
  - Glassnode（无免费）→ DefiLlama（TVL + 资金流向 → 宏观情绪模块）
  - 跨 venue 资金费率孤儿 → Hyperliquid API（骨架已有，接通）
- **死代码**：清算死代码 + OI Bitget 吞数据 — 直接删除
- **数据利用契约**：每个新数据源必须先在 `spec_data_utilization` 登记（来源 / 消费者模块 / 影响决策方式），再写代码；文档登记是前置条件

---

## 领域 4：学习 / 进化机制

- **输入单元**：早期两路并行 — 个例 reflection（每笔平仓后）+ 批次模式归纳（定期）；后期根据噪音/信号比决定是否砍个例路
- **可改范围**：Ranker 权重（低风险）+ PI system prompt 片段（中风险）；风控阈值暂不开放
- **推翻机制**：方案1（置信度更新）× 方案2（时间衰减 half-life）× 方案3（PI 决策时主动过滤）三路融合；需深度研究后设计
- **研究计划**：`docs/plans/learning_mechanism_research_plan.md`，三阶段（学术层 → 实践层 → 融合设计），产品对话完成后启动

---

## 领域 5：风控与执行

- **关卡现状**：20关保持不动，等 Round 2 数据说话后再判断哪些在误杀好交易
- **执行链**：PI → signal_pending → risk engine 审核 → 执行，链条不变；risk engine 最终否决权，PI 无法绕过
- **动态止损**：允许，轻量实现 — PI 持仓 review 时顺带输出"建议调整止损到 X"，position_manager 接收执行；不单独建模块，不占 PI 主循环核心注意力

---

## 领域 6：监管 / 自愈

- **档位**：L1（自动修数据）+ L2（自动修配置/prompt）全开放
- **前置条件**：任何自愈操作执行前，必须经 PI 评估批准（PI 读诊断 → 输出批准/拒绝+理由）
- **日志要求**：每次操作强制写结构化日志（操作类型 / 触发原因 / 执行结果 / 前后状态对比），可审计可回溯
- **分级操作**：
  - L1 自动（PI批准后执行）：清理过期 heartbeat/日志、重启卡死 collector、重建 SQLite 索引
  - L2 人工确认：清除 signal_pending 队列、修改 prompt 片段/配置

---

## 领域 7：人机界面

- **Overview**：保留判生死功能，参考行业最佳实践优化布局
- **Trades/Positions**：查证溯源留在侧边栏下钻，现有设计保留
- **Command Center**：独立页面，三层叠加：
  1. 查询对话框（向 PI 提问，PI 解释决策；不是下指令）
  2. 状态指标面板
  3. 推理链可视化（当前快照 / 今日时间线可切换）
- **心跳区域**（顶部全宽三格）：
  - 左格：呼吸绿点 + PI 当前 intent 摘要（Thinking/Idle badge）
  - 中格：持仓实时 PnL，fmtDelta + tick-flash 动效
  - 右格：前3个持仓的 symbol + 一句 thesis 理由
  - 完全复用现有 CSS 变量和动画，不引入新颜色
  - 设计文档：`docs/plans/command_center_heartbeat_design.md`
- **设计目标**：让 owner 感觉 PI 是活物——真的在处理事情，有思考、决策和记录

---

## 领域 8：失败与撤退

- **Kill 条件**（简洁版，适合当前 demo 阶段）：
  - Demo 干净窗 PF 连续两轮 < 0.5（最低风险环境下都无正向期望，live 无意义）
  - 系统连续 3 天零开仓（决策链僵死）
- **核心哲学**：demo 表现是 live 的唯一入场券；MVP 在 demo 水平做不好，永远不会到达实盘阶段
- **当前阶段**：Round 2 准备 — 聚焦 bug 清零，让系统跑出有判决价值的信号
- **B（代码处置）/ C（个人止损线）**：暂缓，等进入实盘讨论阶段再定

---

## 待启动的研究任务

| 任务 | 文档 | 优先级 |
|------|------|--------|
| 学习/进化机制深度研究（3阶段） | `docs/plans/learning_mechanism_research_plan.md` | Round 2 稳跑后启动 |
| Command Center 心跳区域实现 | `docs/plans/command_center_heartbeat_design.md` | UI 迭代时 |
| B 路条件触发 council 设计 | 无文档，Phase 2 | 验证正向盈利后 |
| Serenity Len 第二赛道边界定义 | 待 owner 确认 Len 含义 | 近期 |

---

## 未解悬挂

1. ~~**Serenity Lens**：已确认，是 xStocks 研究工作台，分工三层（Lens→PI→executor）~~  已解决
2. **风控 20 关松紧**：Round 2 数据回来后逐条审查哪些在误杀好交易
3. **个人止损线**：实盘阶段再定
