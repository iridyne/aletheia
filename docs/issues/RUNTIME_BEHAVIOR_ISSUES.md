# Runtime Behavior Issues — 数据收集与 Agent 行为模式问题

> 本文件记录系统**运行期间观察到的行为异常、数据收集断裂、pipeline 不闭环**等
> 结构性问题。区别于 `BACKEND_ISSUES.md`（代码层 bug / contract 偏移）和
> `FRONTEND_ISSUES.md`（UI 层）：本文件关注**时间累积型行为的健康度**——市场数据
> 采集、知识生成、经验闭环、策略进化等需要长期运行才能暴露的问题。
>
> 每条给出观测数据、影响分析、初步根因推断。**不构成施工授权**——修复前需评估
> 优先级并制定方案。
>
> **图例**：
> - 🔴 直接阻断核心能力（策略进化/风险学习/数据完整性）
> - 🟠 降低数据质量或延迟学习周期（可容忍但应修复）
> - 🟡 效率/覆盖率问题（优化项）
>
> **首次观测**: 2026-06-17 00:20 UTC (系统已连续运行 >24h)
> **数据基线**: orders=70, fills=55, positions=18(open=3), opportunities=1270, knowledge=696(DB)/2205(disk)

---

## §1 risk_critic 成功率异常 🟠

**观测数据**:
- 近 6h 运行 12 次，仅 7 次标记为 completed（58%）
- 其余 5 次状态未知（非 completed 也非 failed）

**影响**:
- 风险评审覆盖不完整——部分交易可能在缺乏 risk_critic 二次审视的情况下通过
- 长期运行中若持续掉成功率，风险控制的"第二道防线"形同虚设

**初步根因推断**:
- LLM 响应超时导致 agent run 挂起（status 停在 running/pending）
- 或 risk_critic 依赖的上游数据（positions/market_snapshots）在部分时段不可用

**排查方向**:
- `SELECT status, COUNT(*) FROM agent_runs WHERE agent_name='risk_critic' GROUP BY status`
- 检查 ended_at 为 NULL 的 runs 是否集中在特定时段
- 查看 agent_runs.errors 字段是否有 timeout/LLM 相关信息

---

## §2 prediction_observer + divergence_scanner 停止运行 🟠

**观测数据**:
- prediction_observer: 6h 内 7 次运行，最后触发 21:27 UTC（已 3h 未触发）
- divergence_scanner: 同上，最后触发 21:27 UTC
- 同时段其他 agent（market_observer, paper_trader）正常运行

**影响**:
- 预测追踪数据断档——无法验证市场预测准确性
- 分歧扫描停止——可能错过结构性分歧信号（如链上/链下数据矛盾）

**初步根因推断**:
- 这两个 agent 可能绑定到不同的调度周期（如 8h/12h 而非 30min）
- 或它们的触发条件（特定市场事件/特定时段）在夜间不满足
- 需确认 scheduler job 注册状态

**排查方向**:
- 检查 `aletheia/agent_runtime/scheduler.py` 中这两个 agent 的注册间隔
- `SELECT started_at FROM agent_runs WHERE agent_name='prediction_observer' ORDER BY started_at DESC LIMIT 20` 看历史模式

---

## §3 stock_observer 全部失败 🟠

**观测数据**:
- 近 6h 运行 5 次，0 次标记为 completed
- 最后触发 20:30 UTC（之后未再运行）

**影响**:
- 股票类资产（ON-series xStocks on Bitget）数据完全缺失
- 如果用户启用 xStock 交易对，系统无法提供有效的市场观察

**初步根因推断**:
- xStock 使用 Bitget 专有接口（非标准 CCXT），可能 API 变更或权限问题
- 或 stock_observer 依赖的 symbol list 配置不正确
- 注意 CLAUDE.md 特别提到 xStock 需要 `CCXTProvider(exchange="bitget", fallback_exchange="bitget")` 的特殊处理

**排查方向**:
- `SELECT errors FROM agent_runs WHERE agent_name='stock_observer' ORDER BY started_at DESC LIMIT 5`
- 检查 stock_observer 的 symbol 配置是否在 config.yaml 中正确设置

---

## §4 学习系统未产出 (ps_lessons = 0) 🔴

**观测数据**:
- `ps_lessons` 表为空（0 条记录）
- 同时 knowledge_items 有 696 条（含 227 post_trade_review）
- 磁盘上 `data/knowledge/post_trade_reviews/` 有 435 个 markdown 文件

**影响**:
- **策略进化闭环断裂**：系统能"记录经验"但无法"提炼教训"
- risk_engine 的 lesson-based constraints（§15 check）永远为空——学习型风险控制完全失效
- 策略 promotion/demotion 缺乏量化依据

**初步根因推断**:
- `ps_lessons` 属于 Serenity/新一代系统的表，当前主循环可能仍写入 knowledge_items
- 或 lesson extraction agent 未被注册到 scheduler
- knowledge_items → lesson 的转化 pipeline 可能是手动触发而非自动

**排查方向**:
- Grep `ps_lessons` 写入点——哪个模块负责 INSERT
- 确认 lesson extraction 是否有 agent 或 scheduled job
- 检查 `aletheia/learning/` 目录结构及其与 scheduler 的集成

---

## §5 prediction_events 无记录 🟠

**观测数据**:
- `prediction_events` 表为空（0 条）
- `prediction_observer` 运行了 7 次（全部 completed）
- `prediction_market_claims` / `prediction_probability_snapshots` 未检查

**影响**:
- 预测市场观察模块在运行，但未记录结构化事件
- 无法做预测准确性回测和预测能力评估

**初步根因推断**:
- prediction_observer 可能将结果写入其他表（claims/snapshots）而非 events
- 或 events 表是为"预测已验证/到期"设计的，尚未有足够时间产出

**排查方向**:
- `SELECT COUNT(*) FROM prediction_market_claims` / `prediction_probability_snapshots`
- 检查 prediction_observer agent 的输出写入逻辑

---

## §6 策略产出极低 (2/1270 = 0.16% 转化率) 🟠

**观测数据**:
- 1270 个 opportunities 仅催生 2 个 strategy_records（1 paper_running, 1 retired）
- 近 24h 新增 461 opportunities，仅 1 个新策略

**影响**:
- 策略多样性严重不足——系统在"寻找机会"但几乎不"形成策略"
- paper_trader 可能绕过策略层直接执行 opportunities（无策略追踪 = 无法评估策略有效性）
- promotion gate 无足够策略候选人，live 上线路径受阻

**初步根因推断**:
- paper_trader 可能直接接收 opportunities 并下单，不经过 strategy_records 注册
- 或策略生成有极高门槛（多个 agent 共识 + 高 confidence score）
- 需确认 paper_trader 的工作流：opportunity → strategy_record → order, 还是 opportunity → order

**排查方向**:
- 追踪 paper_trader agent 的代码：是否调用了 strategy 创建逻辑
- 检查 opportunities 表是否有 strategy_id 关联字段
- `SELECT strategy_id, COUNT(*) FROM opportunities WHERE strategy_id IS NOT NULL GROUP BY strategy_id`

---

## §7 知识类型分布不均 🟡

**观测数据**:
- 磁盘文件分布:
  - case_studies: 1244 (56%) — "发生了什么"
  - post_trade_reviews: 435 (20%) — "交易后怎样"
  - dreaming_logs: 301 (14%) — "联想到什么"
  - research_notes: 210 (10%) — "为什么发生"
  - daily_reviews: 10 (0.5%) — "今天总结"
  - operator_memory: 5 (0.2%) — "运营者经验"

**影响**:
- 系统擅长"记事"(case_study) 但弱于"归因"(research) 和"总结"(daily_review)
- daily_reviews 10 天仅 10 条是正常的（1条/天），但 research_notes 相对不足
- 知识质量取决于深度研究，而非数量堆积

**优化方向**:
- 不是 bug，更像是 agent 行为权重问题
- 可调节 research_agent 触发频率或降低 case_study 的重复记录阈值
- daily_review 应检查是否每天定时生成（确认 scheduler 注册）

---

## §8 market_observer 有 1 次未完成 🟡

**观测数据**:
- 28 次运行，27 次 completed，1 次异常
- 市场快照最大间隔 8.4min（正常应为 ~5min）

**影响**:
- 偶发性丢失 1 个采集窗口——对数据连续性影响极小
- 8.4min 最大间隔说明有短暂的采集延迟

**可能原因**:
- 单次 LLM 调用超时或网络抖动
- 属于正常的分布式系统抖动，不需要特别修复
- 但如果持续观察到 max gap 扩大，则需关注

---

## 优先级排序

| 优先级 | 条目 | 核心诉求 |
|--------|------|----------|
| P0 | §4 学习系统断裂 | 恢复 knowledge → lesson 闭环 |
| P1 | §6 策略转化率极低 | 确认 opportunity → strategy 管道设计意图 |
| P1 | §1 risk_critic 不稳定 | 排查 timeout/挂起原因 |
| P2 | §2 prediction+divergence 停运 | 确认调度设计 |
| P2 | §3 stock_observer 全失败 | 确认 xStock 接口状态 |
| P2 | §5 prediction_events 空 | 确认数据写入路径 |
| P3 | §7 知识类型不均 | agent 权重调优 |
| P3 | §8 market_observer 偶发失败 | 监控即可 |

---

## 监测日志

### 2026-06-17 00:20 UTC — 首次观测
- 系统健康: all clear, gateway running, kill_switch inactive
- Agent 平均间隔 2.3min, 无 >15min gap
- 市场数据采集正常 (avg 0.8min gap)
- 主要发现: 学习闭环断裂(§4)、策略转化极低(§6)、3 个 agent 异常(§1-§3)
