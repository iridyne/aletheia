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

## §7 知识产出严重注水——99% 重复垃圾 🔴

**观测数据** (2026-06-17 00:50 UTC 内容级审计):

| 目录 | 文件数 | 唯一内容 | 重复率 | 最多重复 |
|------|--------|----------|--------|----------|
| case_studies/ | 1251 | **8** | **99.4%** | x186 (同一条反复写) |
| research_notes/ | 211 | 22 | 89.6% | x190 (占位符 "Content here") |
| dreaming_logs/ | 301 | 99 | 67.1% | x190 (一句话 "Synthesized 3 research notes.") |
| post_trade_reviews/ | 438 | 377 | 13.9% | x28 (空壳仅标题) |
| daily_reviews/ | 10 | 8 | 20.0% | x3 |

**核心证据**:
- `research_notes/`: 190 个文件内容是 `"# BTC Breakout Analysis\nconfidence: high\nContent here"` — **字面占位符**
- `dreaming_logs/`: 190 个文件内容是 `"# Daily Consolidation\nSynthesized 3 research notes."` — **一句无意义总结**
- `case_studies/`: 1251 个文件只有 8 种内容，每种被写入 ~186 次（同一笔交易反复记录）
- `post_trade_reviews/`: 28 个空壳（58 bytes，仅标题无正文）；14 个 "LLM unavailable" 回退内容

**影响 — 这不只是浪费存储，是在主动污染决策链**:
1. **FTS 搜索被稀释**: agent 查 "SOL stop_loss" 返回 186 条相同结果，有效信息被淹没
2. **知识引用噪声**: 任何基于知识库做的统计/汇总都是假象（"1251 case studies" 实际只有 8 个交易的记录）
3. **LLM context 浪费**: 如果 agent 读取知识做决策，拉出来的是垃圾内容
4. **false confidence**: 系统对外展示"696 knowledge items"给人积累了大量经验的错觉，实际有效知识极少

**根因分析**:
- **重复写入无去重**: post_trade_reviewer / market_observer 每次运行都为同一批已关闭持仓重新生成 case_study，不检查是否已存在
- **LLM 失败回退写空壳**: LLM 不可用时仍然创建文件，内容是 placeholder 或一句话 fallback
- **占位符内容泄入**: `research_notes` 中 "Content here" 说明模板/占位符直接被存为正式知识
- **无质量门槛**: 写入流程没有最小内容长度或内容验证检查

**修复方案建议**:
1. **去重守卫(写入时)**: 写入前对 (item_type + position_id/symbol/date) 做唯一性检查，已存在则 skip 或 update
2. **质量门槛(写入时)**: 内容 < 100 bytes 或匹配已知 placeholder 模式则拒绝写入
3. **LLM 失败不写入**: 当 LLM 不可用导致内容生成失败时，不创建文件，仅记录 skip 事件
4. **历史清理(一次性)**: 批量删除重复文件，仅保留每个唯一内容的第一份
5. **写入审计**: 每次写入记录 content_hash，定期报告重复率供监控

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

## §9 知识 DB 与磁盘文件不同步 🟠

**观测数据** (2026-06-17 00:45 UTC):
- DB `knowledge_items`: 713 条
- 磁盘 `data/knowledge/` 总文件: 2205 个 .md
- FTS 索引: 438/438（仅覆盖 DB 记录子集）
- 差距: 1492 个磁盘文件未入 DB（68% 遗失）

**影响**:
- 全文搜索（FTS）只能检索到 1/3 的知识——agent 查询知识库时大量历史经验不可见
- 依赖 DB query 的分析（如 post_trade_review 汇总、case_study 关联）遗漏大量数据
- 知识"存了但找不到"——学习系统有效容量远低于实际积累量

**初步根因推断**:
- 早期知识生成可能只写文件不入 DB（后来加了 DB 记录逻辑）
- 或 DB 有清理/rotation 机制删除了旧记录但保留了文件
- FTS 索引 reindex 只对 DB 中存在的记录建索引

**排查方向**:
- 对比磁盘最早文件时间 vs DB 最早 created_at，确认是"早期未入 DB"还是"后来被删"
- 检查知识写入代码是否同时写 DB + 文件
- 评估是否需要做一次历史文件回填（backfill into DB + FTS）

---

## 优先级排序与修复状态

| 优先级 | 条目 | 核心诉求 | 状态 |
|--------|------|----------|------|
| **P0** | **§7 知识注水 99% 重复** | **止血: 去重守卫 + 质量门槛 + 历史清理** | ✅ 已修复 |
| P0 | §4 学习系统断裂 | 恢复 knowledge → lesson 闭环 | ✅ 已修复 |
| P1 | §6 策略转化率极低 | 确认 opportunity → strategy 管道设计意图 | ℹ️ 设计如此 |
| P1 | §9 知识 DB/磁盘不同步 | 68% 知识对 agent 不可见 (注: §7 清理后需重评) | ✅ 清理脚本就绪 |
| P1 | §1 risk_critic 不稳定 | 排查 timeout/挂起原因 | ✅ 已修复(误判) |
| P2 | §2 prediction+divergence 停运 | 确认调度设计(已部分恢复) | ℹ️ 设计如此 |
| P2 | §3 stock_observer 全失败 | 确认 xStock 接口状态 | ✅ 已禁用 |
| P2 | §5 prediction_events 空 | 确认数据写入路径 | ℹ️ 设计如此 |
| P3 | §8 market_observer 偶发失败 | 监控即可 | — 无需修复 |

### 修复说明 (2026-06-17)

**§7 + §9 止血**: content-hash 去重 + 80-byte 质量门槛 + placeholder 拦截 + LLM 失败不写入。
历史垃圾清理脚本 `scripts/cleanup_duplicate_knowledge.py` 待手动运行。

**§4 学习闭环**: dreaming_agent 新增 `_extract_lessons()` 步骤，每日扫描 high-confidence
post_trade_reviews 提取结构化 lessons → `ps_lessons` 表。

**§1 误判**: `completed_with_errors` 实际是"成功但有警告"，stability_monitor 已修正为
只计 `failed`/`stale` 为失败。

**§3 禁用**: stock_observer 依赖的 yfinance 列结构不稳定，且无策略在用 xStock。
`observed_stocks` 默认改为空列表，scheduler guard 跳过。用户可在 UI 手动配置启用。

**§2/§5/§6 设计如此**:
- §2: prediction_observer 30min 周期正常，divergence_scanner 链式跟随
- §5: 数据写入 `prediction_markets` + `prediction_market_claims`，非 `prediction_events`
- §6: opportunities→proposals (paper_trader) 和 strategies (strategy_designer) 是独立管道

---

## 监测日志

### 2026-06-17 00:20 UTC — 首次观测
- 系统健康: all clear, gateway running, kill_switch inactive
- Agent 平均间隔 2.3min, 无 >15min gap
- 市场数据采集正常 (avg 0.8min gap)
- 主要发现: 学习闭环断裂(§4)、策略转化极低(§6)、3 个 agent 异常(§1-§3)

### 2026-06-17 00:45 UTC — 第二轮观测 (Δ25min)
- 系统健康: all clear, kill_switch inactive
- **增量**: knowledge +17 (case_study +14, post_trade_review +3), opportunities +14, orders +1, fills +1
- **磁盘增量**: case_studies 1244→1251(+7), post_trade_reviews 435→438(+3)
- 市场采集: avg 0.7min gap, max 5.1min — 改善(上次 max 8.4min)
- **§2 状态更新**: prediction_observer + divergence_scanner 本轮各运行 1 次(completed) — **已恢复**。推断为大周期调度（~30min），非故障
- **§1 状态更新**: risk_critic 本轮未触发（30min 内 0 runs）— 调度间隔可能 >30min 或按需触发
- **§3 持续**: stock_observer 本轮 0 runs — 仍未恢复
- **§6 持续**: strategies 仍为 2 个，无新增
- **§4 持续**: ps_lessons 仍为 0
- **新发现**: knowledge DB(713) vs disk(2205) 差距 3:1 — DB 记录仅为磁盘文件的子集，FTS 索引 438/438 仅覆盖 DB 内记录。历史知识文件未回填入 DB/FTS
- **产出速率**: ~40 knowledge/h, ~34 opportunities/h（稳定）

### 2026-06-17 00:50 UTC — 数据质量审计
- **严重发现**: 知识产出 99%+ 为重复垃圾内容（§7 升级为 🔴 P0）
- case_studies 1251 文件仅 8 种唯一内容（同一笔交易重复写入 186 次）
- research_notes 190/211 是占位符 "Content here"
- dreaming_logs 190/301 是一句话 "Synthesized 3 research notes."
- post_trade_reviews 28 个空壳(58 bytes)、14 个 "LLM unavailable" 回退
- **结论**: 系统在大量"生产"知识但绝大部分是无信息量的重复/占位/回退内容
- **根因**: 写入流程无去重检查 + LLM 失败仍写空壳 + 占位符模板泄入
- **紧迫性**: 每 30min 继续注入 ~17 条重复内容，持续污染 FTS 索引和下游决策

### 2026-06-17 04:25 UTC — 第三轮观测 (error visibility 修复后)

**系统状态**: gateway running (PID 20152), kill_switch inactive, bitget_demo mode

**数据收集管道**:
| 管道 | 状态 | 最新时间 | 数据量 |
|------|------|----------|--------|
| Universe Scanner | ⚠️ 部分异常 | 04:23 UTC | 1000 scanned, 0 passed (当前 run) |
| Feature Snapshots | ✅ 正常 | 04:23 UTC | 236→423→793 assets/batch |
| Market Snapshots | ✅ 正常 | 04:17 UTC | 按周期采集 |
| Orderbook/Funding/OI | ✅ 正常 | 活跃 | 多品种 |
| Portfolio Snapshots | ✅ 正常 | 03:27 UTC | bitget_demo |
| Knowledge Production | ⚠️ 重复严重 | 03:47 UTC | 5 post_trade_review/h |
| Prediction Markets | ❌ 完全空 | — | 0 markets, 0 claims |
| Lessons (ps_lessons) | ❌ 空 | — | 0 条 |

**新发现**:

1. **§10 Universe Scanner 幽灵 "running" 行** 🟠
   - 观测: 8 行 status="running", completed_at=NULL, assets_passed=0
   - 历史统计: completed=9, running=8(ghost), timed_out=84, failed=7
   - 根因: timeout 杀进程后 DB 状态未回写为 timed_out
   - 影响: dashboard 可能误判 scanner 为"正在运行"；stability 统计偏差
   - 建议: startup reconciliation 扫描 running 且 started_at > 20min 的行，标记为 timed_out

2. **§11 Agent Stale 累积** 🟠
   - 观测: 182 行 agent_runs status="stale"，总失败率 11.9% (61/513 24h)
   - 启动时仅 reconcile 了 1 条（"Marked 1 stale agent run(s)"）
   - 根因: stale 检测仅在启动时做一次，运行期间挂起的 run 不会被 mark
   - 影响: health 报告的 failure ratio 偏高（历史 stale 拉高分母）
   - 建议: 周期性 stale 检测（每 health check 时顺带扫描 >15min 的 running runs）

3. **§12 Case Study 创建 NoneType 错误** 🟡
   - 观测: errors.log 大量 "Failed to create case study for position xxx: 'NoneType' object has no attribute 'id'"
   - 根因: post_trade_reviewer 尝试对已关闭/reconciled 仓位创建 case study，但 lookup 返回 None
   - 影响: case_study 产出中存在失败尝试；不影响核心交易但浪费日志空间
   - 建议: 加 None 守卫在 case study 创建前

4. **§2/§5 状态更新**: Prediction Markets 管道确认完全为空
   - prediction_markets=0, prediction_market_claims=0
   - 归类为"已知限制"（Polymarket API 可能不可达），非紧急

**已修复项生效确认**:
- ✅ infra_event 机制工作（emit 后 buffer 正常）
- ✅ _FAILED_STATUSES 含 "timed_out"（health + stability_monitor）
- ✅ max_instances=1 生效（重启后不再并发 scanner）
- ✅ bitget.py 走 httpx_client() 代理链路

**知识重复统计恶化**:
- post_trade_review: 258 条全部同标题 "Post-Trade Review"
- trade_case_study: 单个 case 最高重复 157 次
- 总 knowledge_items=788（上次 713，+75 in ~4h）
- **结论**: 去重修复（plan §1）尚未部署，垃圾仍在持续累积
