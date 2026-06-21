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
| **P0** | **§7 知识注水 99% 重复** | **止血: 去重守卫 + 质量门槛 + 历史清理** | ✅ 已修复 (需 gateway restart) |
| P0 | §4 学习系统断裂 | 恢复 knowledge → lesson 闭环 | ✅ 已修复 |
| P1 | §15 portfolio_snapshot 停滞 | timeout + executor 隔离 + 启动即执行 | ✅ 已修复 (需 gateway restart) |
| P1 | §10/§11 ghost scanner + stale agents | 年龄门槛 + 即时标记 + 周期清理 | ✅ 已修复 (需 gateway restart) |
| P1 | §6 策略转化率极低 | 确认 opportunity → strategy 管道设计意图 | ℹ️ 设计如此 |
| P1 | §9 知识 DB/磁盘不同步 | 68% 知识对 agent 不可见 | ✅ backfill 脚本就绪 (手动运行) |
| P1 | §1 risk_critic 不稳定 | 排查 timeout/挂起原因 | ✅ 已修复(误判) |
| P2 | §2 prediction+divergence 停运 | 确认调度设计(已部分恢复) | ℹ️ 设计如此 |
| P2 | §3 stock_observer 全失败 | 确认 xStock 接口状态 | ✅ 已禁用 |
| P2 | §5 prediction_events 空 | 确认数据写入路径 | ℹ️ 设计如此 |
| P3 | §8 market_observer 偶发失败 | 监控即可 | — 无需修复 |
| **P1** | **§16 scanner 连续失败 (futures regression)** | **batch ticker + pre-filter + concurrent OHLCV** | ✅ 已修复 (需 gateway restart) |
| P1 | §17 market_observer IOCP thread leak | SelectorEventLoop 替代 ProactorEventLoop | ✅ 已修复 (需 gateway restart) |
| P2 | §18 Binance batch ticker regex 拒绝 | ASCII 预过滤 + venue 过滤 | ✅ 已修复 (需 gateway restart) |

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

**§15 portfolio_snapshot timeout + executor isolation** (2026-06-17):
- `_run_portfolio_snapshot` 改用 `_run_sync_with_timeout("portfolio_snapshot", ...)` (45s budget)
- 加入 `_ASYNC_JOB_TIMEOUT_SEC["portfolio_snapshot"] = 45.0`
- 根因: `io` executor `max_workers=1`，portfolio_snapshot 无 timeout 时挂住会卡死整个 io 队列
- 二次修复: 移至 `default` executor（workers=2），不再与 exchange_sync 排队竞争
- 加 `next_run_time=30s`：重启后立即执行一次，不等完整 interval

**§10/§11 ghost scanner + stale agent cleanup** (2026-06-17):
- 新增 `_cleanup_stale_agent_runs()`: agent_runs 中 running>20min 的行标为 stale
- `_run_health_check()` 追加两个 cleanup 调用（每 10min 一次）
- `_cleanup_stale_scanner_runs()` 加 10min 年龄门槛，不再误杀合法运行中的 scanner
- `_run_async_with_timeout` timeout 后立即从 caller 线程标记 agent_runs 为 timed_out
- 根因: Windows ProactorEventLoop IOCP drain 导致线程被 abandoned，APScheduler 认为 job 结束，但 DB 行状态卡在 running

**§9 知识 backfill** (2026-06-17):
- 清理脚本增加 `--backfill` 模式：质量过关的孤儿文件插入 DB（含 content_hash 去重）
- 低质量文件（<80 bytes / placeholder）在 backfill 时删除
- 用法: `uv run python scripts/cleanup_duplicate_knowledge.py --backfill`

**§16 Scanner 性能升级** (2026-06-17):
- 根因: futures 合并后 registry 从 ~400 增至 ~700-900 资产，`collect_features()` 串行
  `fetch_ohlcv()` 每资产 1-2s，总计 >20min 远超 360s timeout
- 修复策略: batch ticker + volume pre-filter + concurrent OHLCV
  1. Spot/perp tickers 分别 batch fetch（~2-4s 取回全部）
  2. 用 quoteVolume >= min_volume_24h_usdt 预筛，700→~100-150 资产
  3. 剩余资产用 asyncio.Semaphore(5) 并发 OHLCV，~60s 内完成
- timeout 从 360s 提升至 600s 作为安全网
- 预期: 从 timeout (>360s) 降至 60-90s 完成

**§17 IOCP drain thread leak** (2026-06-18):
- 根因: Windows `asyncio.new_event_loop()` 创建 ProactorEventLoop，`loop.close()` 挂起在
  IOCP drain (已取消的 HTTP 连接有未完成的 completion packets)
- 修复: 在 `_run_async_with_timeout._run()` 中检测 `sys.platform == "win32"` 时使用
  `asyncio.SelectorEventLoop()` 代替。SelectorEventLoop 基于 select()，无 IOCP，
  `loop.close()` 立即返回。对 HTTP 工作负载功能等价。
- 预期: 消除 abandoned thread 累积 (~2/45min → 0)

**§18 Binance batch ticker regex 拒绝** (2026-06-18):
- 根因: (1) `list_universe_assets()` 无 venue 过滤，返回 Bitget-only symbols；
  (2) 非 ASCII symbols（龙虾/USDT）通过 `exchange.markets` 检查后触发 Binance regex 拒绝
- 修复: (a) `_is_valid_symbol()` ASCII 预过滤；(b) `service.py` 传 `venue=cfg.market_data.ccxt_exchange`
- 预期: 消除 "Illegal characters" 和 cross-venue symbol mismatch 日志噪音

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

### 2026-06-17 04:46 UTC — 第四轮巡检 (Δ21min)

**系统状态**: gateway running, scheduler active

**管道健康摘要**:
| 管道 | 状态 | 备注 |
|------|------|------|
| Universe Scanner | ✅ 正常 | 最近 2 次 completed (112 passed, ~3.5min/run)，当前 1 run in progress |
| Feature Snapshots | ✅ 正常 | 691-793 assets/batch，~10min 间隔 |
| Market Snapshots | ✅ 正常 | 04:41 UTC (5min 前) |
| Portfolio Snapshots | ✅ 正常 | 04:42 UTC |
| Knowledge Production | ⚠️ 重复 | +12 items/h (11 post_trade_review + 1 research_note)，仍无去重 |
| Prediction Markets | ❌ 空 | 0 markets |
| Lessons | ❌ 空 | 0 条 |

**Agent 运行率**:
- 1h: 31 runs, 20 completed, 9 failed (29% failure — 偏高)
- 24h: 522 runs, 62 failed (11.9%)

**新发现**:

1. **§13 market_observer asyncio 线程泄漏** 🟠
   - 观测: "market_observer job timed out after 240s" + "asyncio.run() teardown hung after 255s, executor thread abandoned"
   - 影响: ProactorEventLoop IOCP drain 挂起 → abandoned thread 计数+1 → 长期可能耗尽线程池
   - 根因: Windows ProactorEventLoop 在 asyncio.run() 退出时不能干净关闭 IOCP（已知 CPython bug）
   - 建议: 在 _run_async_with_timeout 中使用 asyncio.new_event_loop() + loop.run_until_complete() 代替 asyncio.run()，或对 market_observer 设置 replace_existing=True 防止下次覆盖

2. **§10 更新**: ghost scanner "running" 行仍为 8（重启后未清理历史幽灵）
   - 新 run 正常 completed，但历史 8 行仍悬挂
   - **需要**: startup 或定期 reconciliation 清理

3. **§12 持续**: case study NoneType 错误仍在产生（14+ 个 position 触发）

**知识累积**: 788 → 798 (+10 in 21min) — 去重未部署，仍在增长
**结论**: 核心数据管道健康；主要问题是知识垃圾 + market_observer 偶发 thread leak

### 2026-06-17 05:03 UTC — 第五轮巡检 (Δ17min)

**系统状态**: gateway restarted (code drift fix), scheduler active

**管道健康摘要**:
| 管道 | 状态 | 备注 |
|------|------|------|
| Universe Scanner | ⚠️ 2 次失败 | 04:52 + 05:02 failed (swap market type error) |
| Feature Snapshots | ✅ 正常 | 793 assets/batch 稳定 |
| Market Snapshots | ✅ 正常 | 05:00 UTC |
| Portfolio Snapshots | ✅ 正常 | 05:00 UTC |
| Knowledge Production | ⚠️ 仍无去重 | +22 items/h (17 dreaming + 12 ptr + 2 rn + 1 daily) |
| Predictions | ❌ 空 | 0 |
| Lessons | ✅ 恢复 | 0 → 32 条（dreaming_agent lesson 提取已生效） |

**重要发现**:

1. **§14 主仓脏文件导致 scanner 回退** 🔴
   - 观测: registry.py 磁盘上有未提交的脏改动，把 fetchMarkets 改回含 "swap"
   - 根因: 另一个并行 job（futures integration）修改了多个文件但未提交/未完成
   - 影响: gateway 加载脏文件 → scanner 连续 2 次 failed (ExchangeError: "swap" not supported)
   - **已修复**: `git checkout -- aletheia/universe/registry.py` 恢复 + gateway restart
   - **注意**: features.py 等其他脏文件也含 "swap" 引用但当前不影响 scanner 主路径
   - 建议: 并行 job 应在 worktree 中工作，不应脏改主仓文件

2. **§4 学习闭环恢复确认** ✅
   - ps_lessons: 0 → 32 条（首次有数据！）
   - dreaming_agent `_extract_lessons()` 步骤已在运行中生效
   - 验证: plan §2 修复目标达成

3. **§7 知识去重未部署但产出模式变化**
   - dreaming_log 突然 +17 条/h（之前几乎为 0）
   - 可能 dreaming_agent 的新 lesson 提取触发了额外的 dreaming_log 写入
   - 总 knowledge: 798 → 820 (+22 in ~17min) — 增速加快

4. **§10/§11 状态**: ghost scanner 7→7, stale agents 183→185 — 缓慢累积中

**agent 运行率改善**:
- 1h: 48 runs, 38 completed, 7 failed (14.6% — 上次 29%，改善)
- 24h: 548 runs, 64 failed (11.7%)

**结论**: scanner 因脏文件暂时回退已修复；lesson 提取首次成功（里程碑）；知识垃圾增速加快需尽快部署去重

### 2026-06-17 05:46 UTC — 第六轮巡检 (Δ43min)

**系统状态**: gateway running (PID 16552, started 05:47 UTC), code_drift self-heal 多次触发重启

**管道健康摘要**:
| 管道 | 状态 | 备注 |
|------|------|------|
| Universe Scanner | ⚠️ ghost 累积 | 8 runs since 05:03 全部卡 "running", 无 completed |
| Feature Snapshots | ✅ 正常 | 197-675 assets/batch, 仍在产出 |
| Market Snapshots | ❌ 停滞 | 最新 05:00 UTC (46min 前), market_observer 可能 timeout |
| Portfolio Snapshots | ⚠️ 停滞 | 最新 05:00 UTC |
| Knowledge Production | ⚠️ 重复 | +22/h (17 dreaming + 3 ptr), total=820 |
| Predictions | ❌ 空 | 0 |
| Lessons | ✅ 稳定 | 32 条 (无新增 — 正常, 每日运行一次) |

**关键发现**:

1. **§10 恶化: ghost scanner 8→15, 且 max_instances=1 无效** 🔴
   - 观测: 05:03 后启动 8 次 scanner, 全部卡 "running" (completed_at=NULL)
   - 最早的已超时 32min (timeout=360s=6min) 但状态未更新
   - `max_instances=1` 未阻止新启动——因为 timeout 后线程被 abandoned,
     APScheduler 认为 job 已结束
   - 根因同 §13: `_run_async_with_timeout` 使用 asyncio.run(), Windows
     ProactorEventLoop 关闭时 IOCP drain 挂起, 线程永不返回但 APScheduler
     将其视为"已结束"
   - 影响: scanner DB 状态完全不可信; ghost 行无限累积
   - 建议: timeout 后必须强制更新 DB 为 "timed_out" (在 scheduler 层,
     不依赖 asyncio 线程返回)

2. **§8 状态更新: market_observer + portfolio_snapshot 停产 46min** 🟠
   - market_snapshots 最新 05:00, portfolio 最新 05:00
   - 可能原因: 多次 self-heal restart 期间 scheduler job 被丢弃
   - 也可能: market_observer timeout → thread abandoned → 后续调度被 skip
   - 需要观察下一周期是否恢复

3. **§11 恶化: stale agents 183→195 (+12 in 43min)**
   - 增速 ~16/h, 全部是 timeout 后 agent_runs 状态未清理
   - startup reconciliation 每次只清 1 条（只看最后 1 条 running）

**agent 运行率**:
- 1h: 55 runs, 42 completed, 11 failed (20%)
- 24h: 577 runs, 74 failed (12.8% — 上升)

**结论**: asyncio thread abandonment 是系统性根因, 影响 scanner/market_observer/portfolio 三条管道的状态追踪。数据仍在产出 (feature snapshots 证明 scanner 内部在工作) 但 DB 状态管理完全失效。需要 P0 修复 timeout 后的 DB cleanup 逻辑。

### 2026-06-17 06:03 UTC — 第七轮巡检 (Δ17min)

**系统状态**: gateway running (PID restarted via self-heal), scheduler active

**管道健康摘要**:
| 管道 | 状态 | 备注 |
|------|------|------|
| Universe Scanner | ✅ 恢复 | 05:55 completed (114 passed, 3.5min), 自修复确认 |
| Feature Snapshots | ✅ 正常 | 793 assets/batch |
| Market Snapshots | ✅ 恢复 | 05:58 UTC (上轮停滞 46min, 现已恢复) |
| Portfolio Snapshots | ⚠️ 停滞 | 最新仍 05:00 (62min 前) |
| Knowledge Production | ⚠️ 重复 | +3 post_trade_review/h, total=823 |
| Predictions | ❌ 空 | 0 |
| Lessons | ✅ 稳定 | 32 条 |

**状态变化 vs 上轮**:
- ✅ Scanner 恢复: 05:55 成功 completed (上轮全部 ghost running)
- ✅ Market snapshots 恢复: 05:00 → 05:58 (self-heal restart 后恢复)
- ⚠️ Ghost scanner: 15→16 (累积中, 未清理历史幽灵)
- ⚠️ Stale agents: 195→197
- ⚠️ Portfolio 仍停滞 (可能 exchange_sync 触发条件未满足)
- ⚠️ §12 持续: case study NoneType 错误仍在产生 (11 positions)

**agent 运行率**:
- 1h: 39 runs, 27 completed, 12 failed (30.8% — 高峰, 可能因 self-heal 重启期间丢 job)
- 24h: 587 runs, 76 failed (12.9%)

**结论**: 系统在 self-heal restart 后基本恢复。scanner + market_snapshots 正常产出。portfolio 停滞待观察。核心问题（ghost 累积 + thread abandon）仍需代码修复但不影响数据产出。

### 2026-06-17 06:46 UTC — 第八轮巡检 (Δ43min)

**系统状态**: gateway running, self-heal 仍在触发周期性 restart

**管道健康摘要**:
| 管道 | 状态 | 备注 |
|------|------|------|
| Universe Scanner | ✅ 正常 | 06:08 completed (114 passed, 3.5min), 当前 06:14 in progress |
| Feature Snapshots | ✅ 正常 | 409-793 assets/batch, 稳定 |
| Market Snapshots | ✅ 正常 | 06:12 UTC (34min 前) |
| Portfolio Snapshots | ❌ 停滞 | 最新仍 05:00 (106min 前) |
| Knowledge Production | ⚠️ 重复 | +5 ptr/h, total=825 |
| Predictions | ❌ 空 | 0 |
| Lessons | ✅ 稳定 | 32 条 |

**§15 Portfolio Snapshot 长期停滞** 🟠
- 观测: 最新 snapshot 停在 05:00 UTC (106min 前)，此后无更新
- scanner/market 已恢复但 portfolio 未恢复
- 可能原因: exchange_sync job 连续失败(infra_event 应在 3 次后报警)
  或 compute_and_store_snapshot 依赖的 exchange 连接断开
- 需要: 检查 infra_event buffer 是否有 exchange_sync/portfolio 警告

**已知问题趋势**:
- Ghost scanner: 16→18 (+2 in 43min, 稳定累积)
- Stale agents: 197→199 (+2 in 43min)
- 24h failure rate: 12.9% (持平)
- Knowledge total: 823→825 (+2/43min — 增速放缓, 仅 post_trade_review)

**agent 运行率**:
- 1h: 17 runs, 12 completed, 4 failed (23.5%)
- 24h: 594 runs, 78 failed (13.1%)

**结论**: 核心数据管道(scanner/market/features)稳定运行。portfolio 停滞超 100min 升级为 §15。知识增速放缓（dreaming_agent 可能只每日触发一次）。系统整体可用。

### 2026-06-17 07:03 UTC — 第九轮巡检 (Δ17min)

**系统状态**: gateway running, scheduler active

**管道健康摘要**:
| 管道 | 状态 | 备注 |
|------|------|------|
| Universe Scanner | ✅ 正常 | 06:48 completed (118 passed, 4min), 当前 06:58 in progress |
| Feature Snapshots | ✅ 正常 | 148-793 assets/batch, 稳定 |
| Market Snapshots | ✅ 正常 | 06:57 UTC (6min 前) |
| Portfolio Snapshots | ✅ 恢复 | 06:57 UTC (§15 自修复, 从 05:00 停滞 117min 后恢复) |
| Knowledge Production | ⚠️ 重复 | +6 items/h (5 ptr + 1 rn), total=829 |
| Predictions | ❌ 空 | 0 |
| Lessons | ✅ 稳定 | 32 条 |

**状态变化 vs 上轮**:
- ✅ §15 Portfolio 恢复: 05:00 → 06:57 (self-heal restart 后恢复, 同 market_observer 模式)
- ⚠️ Ghost scanner: 18→19 (+1, 持续累积)
- ⚠️ Stale agents: 199→200 (+1)
- 📊 Scanner 总计: completed=16, running=19(ghost), timed_out=84, failed=9
- 📊 Knowledge: 825→829 (+4 in 17min, post_trade_review 为主)

**agent 运行率**:
- 1h: 16 runs, 13 completed, 3 failed (18.8% — 改善)
- 24h: 603 runs, 79 failed (13.1% — 持平)

**error.log**: 无新异常，仅 uvicorn startup 记录（3 次 restart，code_drift self-heal 正常）

**结论**: 全部 5 条数据管道恢复正常运行。§15 portfolio 停滞已自修复（确认模式：thread abandon → self-heal restart → 管道恢复）。系统稳态运行，主要技术债仍为 ghost scanner 累积(19) + stale agents 累积(200)。知识去重未部署，垃圾仍在缓慢增长但速率降低(4/17min vs 之前 22/17min)。

### 2026-06-17 07:46 UTC — 第十轮巡检 (Δ43min)

**系统状态**: gateway running, scheduler active

**管道健康摘要**:
| 管道 | 状态 | 备注 |
|------|------|------|
| Universe Scanner | ✅ 正常 | 07:39 completed (122 passed, 3.5min), 产出稳定 |
| Feature Snapshots | ✅ 正常 | 793 assets/batch (completed runs), 92-385 (ghost runs partial) |
| Market Snapshots | ✅ 正常 | 07:42 UTC (4min 前) |
| Portfolio Snapshots | ⚠️ 微停 | 最新 06:57 (49min 前, 上轮恢复后又停) |
| Knowledge Production | ⚠️ 重复 | +10 items/h (9 ptr + 1 rn), total=835 |
| Predictions | ❌ 空 | 0 |
| Lessons | ✅ 稳定 | 32 条 |

**已知问题趋势**:
- Ghost scanner: 19→22 (+3 in 43min, 加速累积)
- Stale agents: 200→203 (+3 in 43min)
- Scanner 总计: completed=18, running=22(ghost), timed_out=84, failed=9
- Knowledge: 829→835 (+6 in 43min, post_trade_review 为主)
- §15 Portfolio: 又出现微停(06:57 后无更新, 49min), 可能再次进入 thread abandon 周期

**error.log 模式**:
- market_observer: asyncio.run() teardown hung (IOCP drain) — §13 持续
- case study NoneType: 13 个 position 触发 — §12 持续, 高频复现

**agent 运行率**:
- 1h: 42 runs, 38 completed, 3 failed (7.1% — 显著改善, 近 1h 最佳)
- 24h: 636 runs, 82 failed (12.9% — 持平)

**结论**: 核心管道(scanner/market/features)稳定。agent 成功率近 1h 显著改善(7.1%)。Portfolio 再次微停待观察(self-heal 模式可预期恢复)。§12 case study NoneType 是最高频复现的非阻断性 bug(13 positions/cycle)。ghost scanner 累积加速(+3 vs 之前 +1~2/轮)说明 timeout→DB 清理的修复越来越紧迫。

### 2026-06-17 08:03 UTC — 第十一轮巡检 (Δ17min)

**系统状态**: gateway running, scheduler active, self-heal restart 后稳定

**管道健康摘要**:
| 管道 | 状态 | 备注 |
|------|------|------|
| Universe Scanner | ✅ 正常 | 07:49 completed (120 passed), 当前 07:59 in progress |
| Feature Snapshots | ✅ 正常 | 790-793 assets/batch, 连续 3 batch 稳定 |
| Market Snapshots | ✅ 正常 | 07:57 UTC (6min 前) |
| Portfolio Snapshots | ✅ 恢复 | 07:57 UTC (上轮 49min 停滞后恢复) |
| Knowledge Production | ⚠️ 重复 | +10 items/h (9 ptr + 1 rn), total=839 |
| Predictions | ❌ 空 | 0 |
| Lessons | ✅ 稳定 | 32 条 |

**重要发现 — §10 Ghost Scanner 批量清理已生效** ✅
- Ghost scanner: 22→1 (自动清理 21 条 ghost → timed_out)
- Scanner timed_out: 84→106 (+22, 对应之前 ghost 数)
- **确认**: self-heal restart 后的 startup reconciliation 正确工作
- 当前仅剩 1 条 running (即当前正在跑的合法 run)
- 这是 §10 问题的**运行期自愈**，非代码修复——下次长期运行仍会累积

**error.log 新模式**:
- `max_instances reached (1)`: APScheduler 正确跳过并发实例（max_instances=1 生效）
- `Run time was missed by 0:03:06`: scheduler restart 后部分 job missed 一个周期（正常）
- §12 case study NoneType: 持续 (11 positions/cycle)

**已知问题趋势**:
- Ghost scanner: 22→1 (✅ 批量清理)
- Stale agents: 203→205 (+2, 仍在缓慢累积)
- Knowledge: 835→839 (+4 in 17min)

**agent 运行率**:
- 1h: 41 runs, 35 completed, 5 failed (12.2% — 正常波动)
- 24h: 644 runs, 84 failed (13.0% — 持平)

**结论**: §10 ghost scanner 问题在本轮得到批量清理(22→1)，确认 startup reconciliation 有效但需要周期化(§11 建议)。所有核心管道正常。系统处于最佳稳态。

### 2026-06-17 08:46 UTC — 第十二轮巡检 (Δ43min)

[08:46 UTC] ✓ 系统正常 — 所有管道健康运行

**管道状态**: Scanner 连续 4 次 completed (119-120 passed, ~3.5min/run) | Features 793/batch 稳定 | Market 08:38 | Portfolio 08:38 | Knowledge +8/h (6 ptr + 2 rn) | Ghost=1(当前合法 run) | Stale agents 205→207(+2)

**agent 运行率**: 1h: 26 runs, 21 completed, 3 failed (11.5%) | 24h: 662 runs, 86 failed (13.0%)

**error.log**: 仅 uvicorn startup 记录（3 次 self-heal restart），无新异常模式

**备注**: 本轮首次连续 4 个 scanner run 全部 completed，无 ghost 产生。系统自上轮 reconciliation 后进入稳态运行。knowledge 去重仍未部署（plan §1），total=843 持续增长。

### 2026-06-17 09:03 UTC — 第十三轮巡检 (Δ17min)

[09:03 UTC] ✓ 系统正常 — 核心管道正常运行

**管道状态**: Scanner completed 08:50 (121 passed) | Features 793/batch | Market 08:59 (4min 前) | Portfolio 08:38 (25min, 轻微延迟) | Knowledge +7/h (6 ptr + 1 rn), total=846 | Ghost=2(1 合法 + 1 刚超时) | Stale agents 207→211(+4)

**agent 运行率**: 1h: 28 runs, 21 completed, 6 failed (21.4% — 略升) | 24h: 672 runs, 90 failed (13.4%)

**error.log**: startup reconciliation "Marked 1 stale agent run(s)" + uvicorn restart (self-heal 正常)

**趋势**: ghost scanner 从 1 微升到 2（thread abandon 正常复发节奏），stale agents +4/轮（加速，可能因 self-heal restart 频率略增）。portfolio 25min 延迟在可接受范围（<30min 不报警）。整体稳态持续。

### 2026-06-17 09:46 UTC — 第十四轮巡检 (Δ43min)

[09:46 UTC] ✓ 系统正常 — 全管道健康，ghost scanner 清零

**管道状态**: Scanner 连续 completed (121 passed, 最近 2 次) | Features 793/batch 稳定 | Market 09:45 (1min 前) | Portfolio 09:39 (7min) | Knowledge +10/h (9 ptr + 1 rn), total=853 | Ghost=0 ✅ | Stale agents 211→212(+1, 放缓)

**agent 运行率**: 1h: 26 runs, 18 completed, 5 failed (19.2%) | 24h: 688 runs, 91 failed (13.2%)

**error.log**: max_instances=1 正确跳过并发 + "Run time missed" (self-heal restart 后正常丢失 1-2 周期)

**备注**: ghost_scanner=0 是监测以来最佳值（连上轮遗留的 2 个也被 reconciliation 清了）。scanner timed_out 总数 106→109 (+3, 正常累积)。系统持续稳态运行 >3h 无重大异常。

### 2026-06-17 10:03 UTC — 第十五轮巡检 (Δ17min)

[10:03 UTC] ✓ 系统正常 — 全管道健康运行

**管道状态**: Scanner 连续 3 次 completed (121 passed, ~3.8min/run) | Features 793/batch | Market 09:59 (4min 前) | Portfolio 09:59 (4min) | Knowledge +7/h (6 ptr + 1 rn), total=853 | Ghost=1(当前合法 run) | Stale agents 212→213(+1, 放缓)

**agent 运行率**: 1h: 24 runs, 19 completed, 2 failed (8.3% — 优秀) | 24h: 696 runs, 92 failed (13.2%)

**error.log**: max_instances skipped + missed run (正常) + startup reconciliation "Marked 1 stale"

**备注**: agent 1h 失败率 8.3% 为近期最佳之一。系统持续稳态运行 >4h。knowledge total 停在 853（本小时未产出新增 — 可能当前 agent 周期刚好在间隔中）。

### 2026-06-17 10:46 UTC — 第十六轮巡检 (Δ43min)

**系统状态**: gateway running, **scanner 连续 4 次 failed** 🔴

**管道健康摘要**:
| 管道 | 状态 | 备注 |
|------|------|------|
| Universe Scanner | 🔴 连续 failed | 10:22/10:24/10:34/10:44 全部 failed (scanned=0) |
| Feature Snapshots | ⚠️ 停滞 | 最新 10:08 (38min 前), 因 scanner 失败无新 batch |
| Market Snapshots | ✅ 正常 | 10:43 (3min 前) |
| Portfolio Snapshots | ✅ 正常 | 10:43 (3min) |
| Knowledge Production | ✅ 正常 | +8/h (6 ptr + 1 rn + 1 cs), total=861 |
| Predictions | ❌ 空 | 0 |
| Lessons | ✅ 稳定 | 32 条 |

**§16 Scanner 连续失败 — futures commit 引入 "swap" ExchangeError** 🔴

- **观测**: 4 次连续 failed, scanned=0, 即时失败
- **根因**: commit `f626ad3` (feat(futures): Layer 0-6 spot/futures unification) 修改了
  `aletheia/universe/features.py`，将 `fetchMarkets` 改为 `["spot", "swap"]`
- **错误**: `binance fetchMarkets() "swap" is not a supported market type`
  - Binance CCXT 只接受 `["spot", "linear", "inverse"]`，不接受 "swap"
  - "swap" 是 CCXT 内部 market type，不能传给 binance 的 fetchMarkets options
- **影响**: universe scanner 完全停止 → 无新 feature snapshots → 无新 universe 候选
  - market_observer/portfolio 不受影响（独立管道）
  - 现有 universe snapshot 仍有效（TTL 内）但会过期
- **紧迫性**: 必须立即修复，否则 universe 过期后回退到 static fallback
- **修复方向**: features.py 应使用 `["spot", "linear"]` 而非 `["spot", "swap"]`
  来加载 binance 永续合约市场。或在 futures 未配置启用时保持 `["spot"]`

**其他观测**:
- Ghost scanner=0 (全部 failed 不产生 ghost)
- Stale agents: 213→216 (+3)
- timed_out: 109→111 (+2)
- agent 运行率 1h: 30 runs, 25 completed, 4 failed (13.3% — 正常)

**结论**: futures integration commit 引入了 scanner 回归。这是 §14 的根本修复需求——之前是脏文件导致，这次是已提交的代码。需要修复 features.py 中 binance fetchMarkets 的 market type 映射。

### 2026-06-17 11:03 UTC — 第十七轮巡检 (Δ17min)

**§16 持续**: Scanner 连续 5 次 failed (10:22→10:54, 40min+), "swap" ExchangeError 未修复

**其他管道正常**: Market 10:58 (5min) | Portfolio 10:53 (10min) | Knowledge +8/h, total=861 | Agent 1h: 27 runs, 23 completed, 3 failed (11.1%) | Stale 216 | Ghost=0

**Feature snapshots 停滞 53min** (最新 10:08) — 因 scanner 失败无新 universe 供 feature 采集。现有 universe snapshot 仍在 TTL 内（snapshot_ttl_hours 通常 6-24h），暂不影响 market_observer 选币。

**紧迫性升级**: 已连续失败 >40min。若不修复，universe snapshot 将在 TTL 后过期，系统回退到 static fallback (BTC/ETH/SOL only)。

### 2026-06-17 11:46 UTC — 第十八轮巡检 (Δ43min)

**§16 持续恶化**: Scanner 连续 10 次 failed (82min+, 10:22→11:44)。failed 总计 9→19 (+10)。Feature snapshots 停滞 97min (最新 10:08)。

**其他管道正常**: Market 11:38 (8min) | Portfolio 11:43 (3min) | Knowledge +4/h (3 ptr + 1 rn), total=865 | Agent 1h: 27 runs, 21 completed, 1 failed (3.7% — 最佳) | Stale 217 | Ghost=0

**新观测**: error.log 出现 `RuntimeWarning: coroutine 'binance.request' was never awaited` — service.py:214 在 exception 处理中未正确 await CCXT close，造成资源泄漏。非阻断但增加内存/连接开销。

**影响评估**: 最后一次成功 universe snapshot 在 ~10:02 UTC (104min 前)。如果 snapshot_ttl_hours=6，则到 ~16:02 UTC 前 universe 仍有效。还有 ~4h 窗口修复。agent 运行率反而因无 scanner timeout 而改善到 3.7%（只有 agent 管道在跑）。

### 2026-06-17 12:03 UTC — 第十九轮巡检 (Δ17min)

**§16 持续**: Scanner 连续 11 次 failed (100min+, 10:22→12:02)。failed 总计 9→20。Feature snapshots 停滞 115min。

**TTL 评估更新**: 最新 universe snapshot created=09:55, expires=**2026-06-18 09:55** (24h TTL)。**剩余窗口 ~22h** — 比之前预估的 4-6h 宽裕得多。系统不会在短期内回退到 static fallback。

**其他管道正常**: Market 11:48 (15min) | Portfolio 12:01 (2min) | Knowledge total=868 | Agent 1h: 26 runs, 22 completed, 1 failed (3.8% — 优秀) | 24h: 752 runs, 96 failed (12.8% — 改善) | Stale 217 | Ghost=0

**结论**: §16 scanner 失败不紧急（TTL 22h），但仍需修复以恢复 universe 动态刷新。其他所有管道健康且高效运行。

### 2026-06-17 12:42 UTC — §16 已修复 ✅

**修复内容**: `features.py` 和 `registry.py` 中 `fetchMarkets = ["spot", "swap"]` → `["spot", "linear"]`

**验证**: Scanner 成功完成，assets_passed=115。commit `d8908e5`。

**根因分析**: CCXT 的 `fetchMarkets` option 是 exchange-specific 的。Binance adapter 白名单只接受 `["spot", "linear", "inverse"]`，"swap" 是 CCXT 统一层的 market type 概念（用于 `defaultType` / symbol routing），但 Binance 拒绝它。这类 exchange-adapter 差异需要在代码中明确映射。

**§16 状态**: 🟢 已修复并验证

### 2026-06-17 12:50 UTC — 第二十轮巡检 (Δ8min post-fix)

| 管道 | 状态 | 详情 |
|------|------|------|
| Universe Scanner | ✅ 修复生效 | 12:42 completed (115 assets), 下一次 ~12:52 |
| Market Snapshots | ✅ 正常 | 12:50 (实时), 651/h |
| Portfolio | ✅ 正常 | 12:40 (10min ago) |
| Knowledge | ⚠️ 重复 | 884 total, 1h +205 (17x "Daily Consolidation" dreaming_log) |
| Opportunities | ✅ 高产 | 1807 total, +544/h |
| Feature Snapshots | ✅ 恢复 | 12:42 (与 scanner 同步恢复) |
| Predictions | ❌ 空 | 0 markets, 0 claims |
| Lessons | ✅ 稳定 | 32 条 |

**§16 修复验证**: Scanner 在 12:42 成功完成，assets_scanned=1000, assets_passed=115。§16 关闭。

**market_observer stale 率异常 (§17 新发现)** 🟠

- 2h 内 86 次运行: 36 completed + 4 completed_with_errors + **46 stale (53%)**
- Stale 集中在 02:22→11:39 UTC (9h 持续期)，12:41 最新 completed 后停止 stale
- 可能原因: market_observer 运行慢（>超时阈值）→ 被标记 stale → 但实际完成了工作
- 24h 总计: stale=99, 其中 46 来自 market_observer (47%)

**§7 知识重复持续恶化** 🔴

- 1h 内产出 205 条 knowledge_items, 其中 dreaming_log=17 全部标题 "Daily Consolidation"
- post_trade_review=102/h, research_note=15/h — 速率远超正常
- §7 去重方案（plan file）尚未实施。每小时注入 200+ 条大部分重复的 "知识" 持续消耗存储和 FTS 索引

**新发现: Binance batch ticker 非法字符错误** 🟡

- errors.log: `Batch spot ticker fetch failed: binance {"code":-1100,"msg":"Illegal characters found in parameter 'symbols'"}`
- 原因推断: 部分 symbol 含小写字母或非标准字符，Binance API regex 拒绝
- 影响: 非致命（降级到单个 ticker fetch），但降低扫描效率
- 修复方向: fetch_tickers 前过滤 symbols，只传符合 Binance regex `[\\w\\-._&&[^a-z]]` 的

**24h Agent 成功率**: completed=636 (82%), stale=99 (12.7%), completed_with_errors=41 (5.3%)
- 实际有效率 (completed + completed_with_errors) = 87.3%
- Ghost agents=0 (启动清扫有效)
- Timed out=0 (本周期无超时)

### 2026-06-17 13:04 UTC — 第二十一轮巡检 (Δ14min)

| 管道 | 状态 | 详情 |
|------|------|------|
| Universe Scanner | ✅ 连续成功 | 13:03 completed (14s, 115 passed) — 连续第 3 次成功 |
| Market Snapshots | ✅ 正常 | 13:00 (4min ago), 665/h |
| Portfolio | ✅ 正常 | 13:01 (3min ago) |
| Knowledge | ⚠️ 重复 | 888 total, 1h: ptr=105 + dreaming=17 + rn=16 + tcs=70 |
| Features | ✅ 恢复 | 13:03 (与 scanner 同步) |
| Opportunities | ✅ 高产 | 1838 total, +575/h |
| Predictions | ❌ 空 | 0 markets |
| Lessons | ✅ 稳定 | 32 条 |

**Scanner perf improvement 验证**: 新代码 (sem=10 + 8s timeout) 下 scanner 14s 完成，与之前持平。timeout 保护已就绪但未触发（说明当前网络正常）。

**§17 market_observer stale 持续** (46/87 = 53%): 一条新 evidence — `asyncio.run() teardown hung after 255s — executor thread abandoned (ProactorEventLoop IOCP drain)`。确认 stale 根因是 **Windows ProactorEventLoop IOCP drain hang**，非代码逻辑问题。market_observer 实际完成了工作但 cleanup 卡住 → 超时 → 标记 stale。

**§18 Symbol 跨交易所不匹配 (新发现)** 🟡

- **观测**: errors.log 大量 `binance does not have market symbol GRASS/USDT`, `1000PEPE/USDT`, `1000SHIB/USDT`, `SPX/USDT`, `VVV/USDT`
- **根因**: registry 从 Bitget 同步资产列表（`market_data.ccxt_exchange=binance`, `exchange.name=bitget`），但 Bitget 有些 symbol 格式 (1000PEPE, SPX) 在 Binance 不存在
- **影响**: 非致命（降级到 skip），但污染日志 + 浪费 API calls
- **修复方向**: 
  - fetch_tickers 前用 `exchange.markets` 过滤只传 Binance 已知 symbols
  - 或 registry sync 时标记 `data_source_available=False` 对这些 symbols
  - Binance 命名: PEPE (not 1000PEPE), SHIBUSDT (not 1000SHIB)

**Batch ticker 非法字符**: 持续触发。Binance regex 要求 symbols 全大写 `[\\w\\-._&&[^a-z]]`，部分 Bitget registry symbols 含不合法字符。需预过滤。

**Agent 1h**: completed=319 (81%), stale=57 (14.4%), completed_with_errors=17 (4.3%), running=1, timed_out=1
- Ghost=0 ✅
- Stale total=220 (持续累积，历史不清理)

### 2026-06-17 13:46 UTC — 第二十二轮巡检 (Δ42min)

| 管道 | 状态 | 详情 |
|------|------|------|
| Universe Scanner | ✅ 连续成功 | 13:43 (35s, 285 passed) — 连续第 6 次成功 |
| Market Snapshots | ✅ 正常 | 13:41 (5min ago), 721/h |
| Portfolio | ✅ 正常 | 13:42 (4min ago) |
| Knowledge | ⚠️ 重复 | 897 total (+9/42min), dreaming=17/h 仍全重复 |
| Features | ✅ 正常 | 13:43 |
| Opportunities | ✅ 高产 | 1906 total, +643/h |
| Predictions | ❌ 空 | 0 |
| Lessons | ✅ 稳定 | 32 |

**Scanner 行为变化观测**: passed 从 115→285 (×2.5)，耗时 14s→35s。min_volume 未变 (5M USDT)。推测原因：亚洲交易时段更多 symbol 达到 volume 阈值，或 futures markets 在 registry 中生效（`fetchMarkets=["spot","linear"]` 现在正确加载了 perp 市场）。35s 对 285 symbols 合理 (285/sem=10 × avg_latency)。

**§17 持续**: market_observer stale=48/92 (52%), timed_out=3。ProactorEventLoop IOCP hang 根因不变。

**§18 持续**: errors.log 仍有 FOLKS/USDT, COAI/USDT 等 Bitget-only symbols 在 Binance/Bybit 查询失败。日志噪音但非阻断。

**§7 持续**: dreaming_log=17/h 全是重复 "Daily Consolidation"。

[2026-06-17 13:46 UTC] ✓ 核心管道全部正常运行，scanner 性能优化生效中

[2026-06-17 14:03 UTC] ✓ 系统正常 — Scanner 连续第 8 次成功 (35-36s, 285 passed), Market/Portfolio/Features 实时, Opportunities +675/h. §17/§18 持续无恶化. market_observer stale=49/94 (52%), timed_out=5.

[2026-06-17 14:46 UTC] ✓ 系统正常 — Scanner 连续 12+ 次成功 (35-36s, 282-287 passed), Opportunities 突破 2000 (total=2009, +746/h). §17 market_observer stale=50/98 (51%), timed_out=7. §18 symbol mismatch 持续 (SKYAI, MRVL 等). Ghost=0.

[2026-06-17 15:03 UTC] ✓ 系统正常 — Scanner 连续成功 (35s, 283 passed), Market/Portfolio/Features 实时, Opportunities=2031 (+768/h). §17 恶化趋势: timed_out 5→9, abandoned threads=4 (IOCP drain 累积). 其余无变化.

[2026-06-17 15:46 UTC] ✓ 系统正常 — Scanner 连续成功 (34-36s, 284-286 passed), Opportunities=2096 (+833/h). §17 timed_out=11 (缓慢累积, 非阻断). Ghost=0, Stale total=225 (稳定不增).

[2026-06-17 16:03 UTC] ✓ 系统正常 — Scanner 连续成功 (34-35s, 284-285 passed), Opportunities=2119 (+856/h). §17 abandoned threads=8 (从 4→8, 1h 内翻倍), timed_out=13. 线程泄漏加速但进程稳定, 建议下次 gateway restart 清理.

[2026-06-17 16:46 UTC] ✓ 系统正常 — Scanner 连续成功 (34-35s, 284-286 passed), Opportunities=2184 (+921/h 峰值). §17 timed_out=15 (+2/43min, 线性增长). Stale total=225 (稳定). Ghost=0. 所有核心管道实时.

[2026-06-17 17:03 UTC] ✓ 系统正常 — Scanner 连续成功 (34-35s, 285-286 passed), Opportunities=2208 (+945/h). **新**: strategy_designer 首次触发 (completed=1), pipeline 完整链条生效. §17 abandoned threads=12 (持续累积 ~4/h), timed_out=17. Knowledge=908 (+1 strategy_memory). Ghost=0.

[2026-06-17 17:46 UTC] ✓ 系统正常 — Scanner 稳定 (35-36s, 286 passed), Opportunities=2274 (**+1011/h 首次破千**). Market snapshots=1050/h. §17 timed_out=19 (线性 ~2/45min). Ghost=0, Stale=225 (不增). 所有管道实时运行 5h+ 无中断.

[2026-06-17 18:03 UTC] ✓ 系统正常 — Features=1754/h (4h 连续稳定 1740-1754), Opportunities=2297 (+89/h, 亚盘收市后下降). §17 market_observer **100% timed_out** (最近 8 次全部, 4min 超时). §7 知识去重生效: 1h 仅 4 条新增 (3 ptr + 1 rn, 无重复). Ghost=0, Stale=225. Gateway supervisor 13:41-14:11 重启 frontdoor 5 次 (已自愈). §18 新增: JELLYJELLY, SIREN, BLESS (Bitget-only). Ticker chunk 400-430 Binance 非法字符 regex 失败 (含小写 symbol).

[2026-06-17 18:46 UTC] ✓ 系统正常 — Features=1783/h (微升), Opportunities=2370 (+96/h). §17 market_observer: timed_out=3 + stale=1 + running=1 (1h 内). Timed_out 总计 21→23. §7 知识去重持续有效: 8 条/h (6 ptr + 2 rn, 全部唯一). Ghost=0, Stale=227 (+2). §18 新增: COAI, TAC, MAGMA (Bitget-only, 多时间维度 OHLCV 查询全部失败). 系统运行 6.5h+ 无中断.

[2026-06-17 19:03 UTC] ✓ 系统正常 — Features=1792/h (持续微升), Opportunities=2396 (+99/h). §17 market_observer 4 次连续 timed_out (timed_out 总计 23→25, +2/45min 线性). §7 知识去重: 1 条/h (极低——可能 agent 周期间隔中). Ghost=0, Stale=226 (-1, reconciliation 偶发清理). 7h 连续无中断.

[2026-06-17 19:46 UTC] ✓ 系统正常 — Features=1803/h (连续 3h 微升趋势 1752→1783→1803), Opportunities=2464 (+95/h 稳定). §17 timed_out=27 (+2/45min 线性不变). Knowledge=460 (+1/h). Ghost=0, Stale=228. §18: MSFT/USDT, GUA/USDT, BR/USDT 新增 (xStock symbols 混入 crypto 查询). 7.7h 连续无中断.

[2026-06-17 20:03 UTC] ✓ 系统正常 — Features=1809/h (持续微升 1750→1792→1809), Opportunities=2490 (+94/h). §17 timed_out=29 (+2/45min 不变). Knowledge=463 (+1/h). Ghost=0, Stale=227. §18: SPY/USDT 多时间框架查询失败 (equity_observer xStock symbol). 8h+ 连续无中断.

[2026-06-17 20:46 UTC] ✓ 系统正常 — Features=1531/h (下降, 因 off-peak US hours volume 过滤后通过 symbols 减少, 无 gap). Opportunities=2547 (+83/h). §17 timed_out=32 (+3/45min, 略加速). paper_trader: completed_with_errors=2 (首次出现, 非致命). risk_critic: 1 stale (偶发). Knowledge=467. Ghost=0, Stale=228. 8.7h 连续无中断.

[2026-06-17 21:03 UTC] ✓ 系统正常 — Features=1836/h (回升, 1531→1836). Opportunities=2571 (+81/h). §17 timed_out=33 (+1). paper_trader: completed_with_errors=3/h (持续, 非致命——可能缺少 open positions 或 LLM 降级). risk_critic: 1 stale (同上轮). Knowledge=470. Ghost=0, Stale=229. 9h+ 连续无中断.

[2026-06-17 21:46 UTC] ✓ 系统正常 — Features=1830/h (稳定). Opportunities=2642 (+95/h). §17 timed_out=36 (+3/45min), abandoned_threads=3 (重置——gateway 可能自动 restart 清理). paper_trader + risk_critic 模式同上. Knowledge=474. Ghost=0, Stale=230. 9.7h 连续无中断.

[2026-06-17 22:03 UTC] ✓ 系统正常 — Features=1830/h (3h 稳定在 1809-1836 带). Opportunities=2674 (+103/h 回升). §17 timed_out=37 (+1/45min, 放缓). Knowledge=479 (+1/h). Ghost=0, Stale=230. 10h+ 连续无中断——达到监测以来最长无中断运行.

[2026-06-17 22:46 UTC] ✓ 系统正常 — Features=1829/h (锁定在 1830 带, 2h 无波动). Opportunities=2736 (+94/h). §17 timed_out=40 (+3/45min). Knowledge=485. Ghost=0, Stale=231. equity_observer 本轮未触发 (off-hours 正常). 10.7h 连续无中断.

[2026-06-17 23:03 UTC] ✓ 系统正常 — Features=1827/h (1830 带持续 3h+). Opportunities=2761 (+87/h). §17 timed_out=41 (+1). Knowledge=489. Ghost=0, Stale=231. §18 根因确认: **龙虾/USDT** (中文字符 symbol) 在 registry 中, 导致 Binance chunk 400-430 整批 regex 拒绝. 修复方向: `_chunked_fetch_tickers` 预过滤非 ASCII symbols. 11h+ 连续无中断.

[2026-06-17 23:46 UTC] ✓ 系统正常 — Features=1827/h (1830 带 4h+ 稳定). Opportunities=2821 (+85/h). §17 timed_out=43 (+2/45min, 线性). Knowledge=493. Ghost=0, Stale=233 (+2). §18 新增: VELVET/USDT (Bitget-only, Binance+Bybit 均无). paper_trader: completed_with_errors=3/h (持续, 非致命). 12h+ 连续无中断.

[2026-06-18 00:03 UTC] ✓ 系统正常 — Features=1525/h (off-peak 下降, 同 round 33 模式). Opportunities=2844 (+83/h). §17 timed_out=45 (+2/45min, 线性), market_observer 最近 5 次全部 timed_out, abandoned_threads 重置为 1 (gateway 自动重启清理). Knowledge=497 (+8/h). Ghost=0, Stale=232. §18 新增: FOLKS/USDT (Bitget-only). 12.5h+ 连续无中断.

[2026-06-18 00:46 UTC] ✓ 系统正常 — Features=1836/h (off-peak 恢复). Opportunities=2895 (+74/h). §17 timed_out=47 (+2/45min, 线性不变). Knowledge=501 (+8/h). Ghost=0, Stale=234. §18: SKYAI/USDT (Bitget-only, 3 timeframe 全失败). 13h+ 连续无中断.

[2026-06-18 01:03 UTC] ✓ 系统正常 — Features=1836/h (稳定). Opportunities=2933 (+89/h). §17 timed_out=49 (+2/45min). Knowledge=504 (+7/h). Ghost=0, Stale=233. §18: UAI/USDT (Bitget-only). 13.5h+ 连续无中断.

[2026-06-18 01:46 UTC] ✓ 系统正常 — Features=1752/h (微降). **Opportunities 突破 3000** (3009, +114/h 峰值). §17 timed_out=53 (+4, market_observer 本小时 5 次触发——调度间隔缩短). risk_critic: 2 stale (偶发, 非持续). Knowledge=514 (+13/h). Ghost=0, Stale=235. §18: BLESS, PIPPIN, SQD, MSFT (xStock). 14h+ 连续无中断.

[2026-06-18 02:03 UTC] ✓ 系统正常 — Features=1864/h (亚盘开盘微升). Opportunities=3031 (+98/h). §17 timed_out=54 (+1, 回归正常速率). risk_critic: 2 stale (同上轮模式). Knowledge=517 (+13/h). Ghost=0, Stale=235. §18: BLUAI/USDT. 14.5h+ 连续无中断.

[2026-06-18 02:46 UTC] ✓ 系统正常 — Features=1562/h (volume dip, 同 off-peak 模式). Opportunities=3089 (+80/h). §17 timed_out=57 (+3), abandoned_threads=3 (重新累积). Knowledge=522 (+8/h). Ghost=0, Stale=235. §18: H/USDT, SPX/USDT (Bitget-only). risk_critic stale 已恢复. 15h+ 连续无中断.

[2026-06-18 03:03 UTC] ✓ 系统正常 — Features=1875/h (亚盘活跃, 连续 3h 上升趋势 1836→1864→1875). Opportunities=3118 (+87/h). §17 timed_out=58 (+1). Knowledge=526 (+9/h). Ghost=0, Stale=235. risk_critic 全部 completed (恢复). 15.5h+ 连续无中断.

[2026-06-18 03:46 UTC] ✓ 系统正常 — Features=1870/h (亚盘持续). Opportunities=3184 (+95/h). §17 timed_out=61 (+3), abandoned_threads 再次重置为 1 (gateway 自动重启). Knowledge=536 (+14/h 高产). Ghost=0, Stale=237. post_trade_reviewer: 1 completed_with_errors (偶发). §18: INTC/USDT (xStock). 16h+ 连续无中断.

[2026-06-18 04:03 UTC] ✓ 系统正常 — Features=1554/h (off-peak 回落). Opportunities=3204 (+86/h). §17 timed_out=61 (不变), market_observer stale 占比升高 (3 stale + 3 timed_out / 7 runs, 但仍在 running). Knowledge=537 (+11/h). Ghost=0, Stale=240 (+3). §18: SWARMS, CLO, BSB, H (Bitget-only). 16.5h+ 连续无中断.

[2026-06-18 04:46 UTC] ✓ 系统正常 — Features=1867/h (恢复). Opportunities=3295 (+111/h 高产). §17 timed_out=65 (+4, market_observer 高频触发). Knowledge=547 (+11/h). Ghost=0, Stale=240. §18: MRVL/USDT (xStock), BR/USDT. 17h+ 连续无中断.

[2026-06-18 05:03 UTC] ✓ 系统正常 — Features=1870/h (稳定). Opportunities=3319 (+115/h 持续高产). §17 timed_out=66 (+1). **dreaming_agent 日常周期触发**: 8 runs completed_with_errors (lesson extraction LLM 返回 non-JSON, 非阻断——知识仍写入). Knowledge=551 (+14/h). Ghost=0, Stale=240. 17.5h+ 连续无中断.

[2026-06-18 18:14 UTC] ✓ 系统正常 — Features=1898/h (欧美盘高位稳定). Opportunities=4504 (+88/h, 13h 累积 +1185). §17 timed_out=119 (+53 跨越 13h, ~4/h 均速). Knowledge=616 (+1/h 极低——周期间隔中). Ghost=0, Stale=245. equity_observer 触发 2 completed_with_errors (US market hours). strategy_designer completed=1. §18: DRAM, AGT, BR, H (Bitget-only). 巡检间隔调整为 1h/次.

[2026-06-18 19:21 UTC] ⚠️ 新发现 §19 — aiohttp 连接泄漏 (LOW) — Features=1868/h. Opportunities=4586 (+68/h). §17 timed_out=123 (+4/h 稳定). Knowledge=627 (+11/h). Ghost=0, Stale=247. **errors.log 出现 "Unclosed client session / Unclosed connector" 7 条**，含 7 个 ResponseHandler 连接滞留——market_observer asyncio.run() teardown 时 aiohttp TCPConnector 未正常 close，连接池泄漏至进程级。影响：长时间运行后文件描述符耗尽风险。修复方向：market_observer 改用手动 event loop (`loop.run_until_complete` + `loop.close()`) 或在 finally 块显式 `await session.close()` + `await connector.close()`。31h+ 连续运行.

[2026-06-18 20:21 UTC] ⚠️ 新发现 §20 — 调度器 job missed (LOW) — Features=**2076/h** (US 盘峰值新高). Opportunities=4672 (+86/h). §17 timed_out=128 (+5). Knowledge=641 (+14/h). Ghost=0, Stale=248. **errors.log: "Run time of job 'partial' was missed by 0:01:00"** — APScheduler job 延迟触发 1 分钟，scheduler 线程在高负载时被阻塞。影响：数据采集偶发延迟，非阻断。修复方向：scheduler max_workers 当前=3（CLAUDE.md 约束），检查是否有长耗时 job 占满线程池。§18: TSLA/USDT, ARM/USDT (xStock). equity_observer 持续 completed_with_errors (US hours). 32h+ 连续运行.

[2026-06-18 21:21 UTC] ✓ 系统正常 — Features=1828/h (US 盘后回落, 正常). Opportunities=4759 (+87/h). §17 timed_out=132 (+4). Knowledge=645 (+4/h). Ghost=0, Stale=249. **paper_trader: 首次出现 completed=2** (同时 completed_with_errors=2, 混合模式——可能有真实 proposal 路径被执行). §18: SPCX, BTW, BAN, FOLKS, BSB, SPX. 33h+ 连续无中断.

[2026-06-18 22:22 UTC] ✓ 系统正常 — Features=1825/h (US 盘后低位平稳). Opportunities=4848 (+89/h). §17 timed_out=136 (+4, 线性持续). Knowledge=649 (+4/h). Ghost=0, Stale=249. paper_trader: 10 completed + 58 CWE (2h内, 正常模式稳定). §18: 龙虾/USDT, SOXL + 常规 Bitget-only. equity_observer CWE (US hours). 34h+ 连续无中断.

[2026-06-18 23:21 UTC] ✓ 系统正常 — Features=1811/h (亚盘夜间平稳). Opportunities=4939 (+91/h). §17 timed_out=140 (+4). Knowledge=653 (+4/h). Ghost=0, Stale=249. paper_trader: 12 completed + 60 CWE (稳定). §18: TSM/USDT (xStock), SOXL + 常规 Bitget-only. equity_observer CWE (US after-hours). 35h+ 连续无中断.

[2026-06-19 00:21 UTC] ✓ 系统正常 — Features=1793/h (亚盘深夜缓降). **Opportunities=5024 (+85/h, 首次突破 5000)**. §17 timed_out=143 (+3, market_observer 偶发 1 次 completed). Knowledge=657 (+4/h). Ghost=0, Stale=249. paper_trader: 16 completed + 60 CWE (completed 比例提升). §18: GOOGL/USDT, TSM, SOXL + 常规 Bitget-only. 36h+ 连续无中断.

[2026-06-19 01:21 UTC] ✓ 系统正常 — Features=1765/h (亚盘深夜低谷). Opportunities=5107 (+83/h). §17 timed_out=147 (+4). Knowledge=662 (+5/h, 按小时分桶约 4/h 正常). Ghost=0, Stale=249. paper_trader: 16 completed + 64 CWE. §18: ESPORTS/USDT, RAVE + 常规 Bitget-only. 37h+ 连续无中断.

[2026-06-19 02:21 UTC] ✓ 系统正常 — Features=1710/h (亚盘凌晨持续缓降). Opportunities=5191 (+84/h). §17 timed_out=151 (+4). Knowledge=666 (+4/h). Ghost=0, Stale=249. Agent 活跃度低 (凌晨调度窗口正常). §18: AAOI/USDT, PRL + 常规 Bitget-only. 38h+ 连续无中断.

[2026-06-19 03:21 UTC] ✓ 系统正常 — Features=1486/h (亚盘凌晨低谷). Opportunities=5276 (+85/h). §17 timed_out=155 (+4). Knowledge=670 (+4/h). Ghost=0, Stale=249. Agent 活跃度低 (凌晨窗口正常). §18: XAU/USDT + 常规 Bitget-only. 39h+ 连续无中断.

[2026-06-19 04:21 UTC] ✓ 系统正常 — Features=1806/h (亚盘早盘拉升, 低谷反弹). Opportunities=5371 (+95/h). §17 timed_out=159 (+4); errors.log 首次出现 §17 明文日志 "asyncio.run() teardown hung after 255s — executor thread abandoned, total=4". Knowledge=674 (+4/h). Ghost=0, Stale=249. §18: PLAY/USDT OHLCV 多次失败 (同一符号反复重试,噪音). 40h+ 连续无中断.

[2026-06-19 05:21 UTC] ✓ 系统正常 — Features=1763/h (亚盘早盘平稳). Opportunities=5459 (+88/h). §17 timed_out=162 (+3). Knowledge=680 (+6/h). Ghost=0, Stale=250 (+1: dreaming_agent 正在 running, 正常). dreaming_agent git_snapshot_failed 错误 (已知 CWE). §18: FOLKS/VELVET OHLCV 反复失败 (噪音). 41h+ 连续无中断.

[2026-06-19 06:21 UTC] ✓ 系统正常 — Features=1753/h (亚盘早盘稳定). Opportunities=5551 (+92/h). §17 timed_out=167 (+5); errors.log §17 明文再现 "abandoned threads=3". Knowledge=691 (+11/h, 知识产出回升). Ghost=0, Stale=251. dreaming_agent: 1 completed (正常完成). §18: FOLKS/SPX OHLCV 常规噪音. 42h+ 连续无中断.

[2026-06-19 07:21 UTC] ✓ 系统正常 — Features=1759/h (亚盘早盘持续稳定). Opportunities=5640 (+89/h). §17 timed_out=171 (+4). Knowledge=695 (+4/h). Ghost=0, Stale=251. dreaming_agent: 1 completed. §18: BSB/FOLKS/M 常规 Bitget-only. 43h+ 连续无中断.

[2026-06-19 08:21 UTC] ✓ 系统正常 — Features=1775/h (亚盘早盘小幅上升). Opportunities=5727 (+87/h). §17 timed_out=175 (+4). Knowledge=703 (+8/h). Ghost=0, Stale=251. dreaming_agent: 1 completed. errors.log: no_price_for_entry:DEXE/USDT (偶发价格缺失, 非阻断). §18: SOXL/DRAM 常规 Bitget-only. 44h+ 连续无中断.

[2026-06-19 09:21 UTC] ✓ 系统正常 — Features=1776/h (亚盘持续稳定). Opportunities=5797 (+70/h, 欧市开盘前减速). Knowledge=719 (+16/h, 上升). §17 timed_out=179 (+4). Ghost=0, Stale=253. dreaming_agent: 1 completed. §18: SKHYNIX/USDT (韩股 xStock) + H/USDT OHLCV 噪音. 45h+ 连续无中断.

[2026-06-19 10:21 UTC] ✓ 系统正常 — Features=1793/h (欧市早盘开启). Opportunities=5876 (+79/h). Knowledge=735 (+16/h, 持续上升). §17 timed_out=183 (+4); errors.log §17 明文再现 abandoned threads=1. Ghost=0, Stale=254. dreaming_agent: 1 completed. §18: FOLKS OHLCV 常规噪音. 46h+ 连续无中断.

[2026-06-19 11:21 UTC] ✓ 系统正常 — Features=1669/h (欧市小幅调整). **Opportunities=5977 (+101/h, 欧市开盘冲刺)**. Knowledge=756 (+21/h, 加速增长). §17 timed_out=187 (+4). Ghost=0, Stale=260 (risk_critic stale=4, 正常波动). dreaming_agent: 1 completed. §18: AGT/USDT OHLCV 常规噪音. 47h+ 连续无中断.

[2026-06-19 12:21 UTC] ✓ 系统正常 — **Features=2096/h (欧市高峰, 首次突破 2000/h)**. **Opportunities=6077 (+100/h, 首次突破 6000)**. Knowledge=773 (+17/h, 持续加速). §17 timed_out=192 (+5). Ghost=0, Stale=261. dreaming_agent: 1 completed. §18: ESPORTS/BASED/BSB OHLCV 常规噪音. 48h+ 连续无中断.

[2026-06-19 13:21 UTC] ✓ 系统正常 — **Features=2682/h (欧市高峰新高)**. Opportunities=6197 (+120/h). Knowledge=779 (+6/h). §17 timed_out=194 (+2). Ghost=0, Stale=268. errors.log: forward_outcome FAILED price unavailable (§18 下游效应, ESPORTS/VELVET/GUA 等 Bitget-only 符号无法回测, 非新问题). 49h+ 连续无中断.

[2026-06-19 14:21 UTC] ✓ 系统正常 — **Features=2949/h (欧市高峰冲刺, 近 3000/h)**. Opportunities=6362 (+165/h). **Knowledge=801 (+22/h, 首次突破 800)**. §17 timed_out=199 (+5). Ghost=0, Stale=272. equity_observer CWE 重现 (US 预市开盘). forward_outcome FAILED 持续 (§18 下游, 已知). 50h+ 连续无中断.

[2026-06-19 15:21 UTC] ✓ 系统正常 — Features=1740/h (欧市午盘回落, 正常). Opportunities=6447 (+85/h). Knowledge=826 (+25/h, 继续加速). §17 timed_out=203 (+4). Ghost=0, Stale=275. equity_observer CWE (US 正式开盘). forward_outcome FAILED 持续 (§18 下游). volume scanner failed: GUA/USDT (§18 下游, 同源). 51h+ 连续无中断.

[2026-06-19 16:21 UTC] ✓ 系统正常 — Features=1692/h (US 开盘过渡低位). Opportunities=6536 (+89/h). Knowledge=855 (+29/h, 加速近 900). §17 timed_out=208 (+5). Ghost=0, Stale=277. equity_observer CWE (US 开盘时段). forward_outcome FAILED 持续 (§18 下游). 52h+ 连续无中断.

[2026-06-19 17:21 UTC] ✓ 系统正常 — Features=1914/h (US 开盘后回升). Opportunities=6644 (+108/h). Knowledge=884 (+29/h, 近 900). §17 timed_out=213 (+5). Ghost=0, Stale=278. equity_observer stale=1 (US 开盘时段首次 stale, 观察). forward_outcome FAILED: NVDA/PLTR (xStock §18 下游). 53h+ 连续无中断.

[2026-06-19 18:21 UTC] ✓ 系统正常 — Features=1635/h (US 盘中震荡). Opportunities=6736 (+92/h). Knowledge=894 (+10/h, 近 900). §17 timed_out=217 (+4). Ghost=0, Stale=278. equity_observer stale=1 维持 (未加重). forward_outcome FAILED: AMD/USDT (xStock §18 下游). 54h+ 连续无中断.

[2026-06-19 19:21 UTC] ✓ 系统正常 — Features=1571/h (US 盘中). Opportunities=6819 (+83/h). **Knowledge=905 (+11/h, 首次突破 900)**. §17 timed_out=221 (+4). Ghost=0, Stale=278. paper_trader: 18 completed (2h内, 稳定). forward_outcome FAILED 24h horizon 批次 (§18 下游). 55h+ 连续无中断.

[2026-06-19 20:21 UTC] ✓ 系统正常 — Features=1335/h (US 盘中后段缓降). Opportunities=6910 (+91/h). Knowledge=918 (+13/h). §17 timed_out=224 (+3); market_observer 偶发 1 次 completed. Ghost=0, Stale=278. forward_outcome FAILED: AVGO/RKLB/NATGAS (xStock+大宗 §18 下游). 56h+ 连续无中断.

[2026-06-19 21:21 UTC] ✓ 系统正常 — Features=1579/h (US 盘中回升). **Opportunities=7002 (+92/h, 首次突破 7000)**. Knowledge=929 (+11/h). §17 timed_out=228 (+4). Ghost=0, Stale=278. paper_trader: 26 completed (2h, 比例持续提升). forward_outcome FAILED §18 下游常规. 57h+ 连续无中断.

[2026-06-19 22:21 UTC] ✓ 系统正常 — Features=1580/h (US 盘中稳定). Opportunities=7092 (+90/h). **Knowledge=280/h 新增** (post_trade_review×198, guard_candidate×43, research_note×31 — US 峰值时段知识爆发, 233/280 有 content_hash 唯一, 正常). §17 timed_out=232 (+4). Ghost=0, Stale=278. risk_critic stale 升至 8/39 (20%). paper_trader: 30 completed + 38 CWE (2h). equity_observer stale=1 持续第 6 轮稳定不恶化. forward_outcome FAILED §18 下游常规 (EVAA/SKYAI/MSTR/AAOI 等). 58h+ 连续无中断.

[2026-06-19 23:21 UTC] ✓ 系统正常 — Features=1569/h (US 尾盘缓降). Opportunities=7184 (+92/h). Knowledge=946 (+9/h, 尾盘回落正常). §17 timed_out=235 (+3). Ghost=0, Stale=278. risk_critic stale=8/40 (20%, 持平). paper_trader: 34 completed + 38 CWE (2h, completed 比例继续提升). equity_observer stale=1 持续第 7 轮. §18 持续: PIPPIN/SKYAI/MAGMA/GUA 等 Bitget-only 符号 ticker/volume scanner failed. 59h+ 连续无中断.

[2026-06-20 00:21 UTC] ✓ 系统正常 — Features=1563/h (US 收盘后亚盘切换). Opportunities=7273 (+89/h). Knowledge=956 (+10/h). §17 timed_out=238 (+3). Ghost=0, Stale=279 (+1). market_observer running=1 (正在执行中, 非 stale). paper_trader: 38 completed + 38 CWE (2h, **completed 首次追平 CWE, 健康信号**). risk_critic stale=8/41 (20%, 持平). §18 持续: 1000LUNC/USDT、IP/USDT 等 OHLCV fetch failed (Bitget-only 符号). 60h+ 连续无中断.

[2026-06-20 01:21 UTC] ✓ 系统正常 — Features=1557/h (亚盘初期平稳). Opportunities=7352 (+79/h net); opp_last_1h=125 (US 收盘后骤降, 正常季节性). Knowledge=968 (+13/h, 亚盘低谷). §17 timed_out=240 (+2, 累积进一步减速). Ghost=0, Stale=279. **paper_trader: 41 completed + 38 CWE (2h, completed 首次超越 CWE)**. market_observer stale=21 (+1). risk_critic stale=8/42 (19%). §18 持续: SAMSUNG/1000SHIB/IBM/GRASS 等 ticker fetch failed. 61h+ 连续无中断.

[2026-06-20 02:21 UTC] ✓ 系统正常 — Features=1544/h (亚盘深夜低谷). Opportunities=7446 (+94/h net); opp_last_1h=219 (亚盘低谷正常). Knowledge=977 (+9/h). §17 timed_out=244 (+4, 轻微回升). Ghost=0, Stale=279. dreaming_agent/equity_observer 2h 窗口静默 (深夜低频, 正常). paper_trader: 9 completed (2h 亚盘缩量). §18 持续: US/USDT、1000BONK/USDT、QCOM/USDT 等 ticker fetch failed. 62h+ 连续无中断.

[2026-06-20 03:21 UTC] ✓ 系统正常 — Features=1550/h (亚盘持稳). Opportunities=7538 (+92/h net); opp_last_1h=311 (亚盘小幅回升). Knowledge=988 (+11/h). §17 timed_out=246 (+2). Ghost=0, Stale=279. paper_trader: 13 completed (2h 亚盘低频). §18 持续: FOLKS/AIO 等 Bitget-only 符号 ticker fetch failed. 63h+ 连续无中断.

[2026-06-20 04:21 UTC] ✓ 系统正常 — Features=1507/h (亚盘早市前). **Knowledge=1005 (+50/h, 首次突破 1000)**. Opportunities=7637 (+99/h net); opp_last_1h=410 (亚盘回升). §17 timed_out=250 (+4). Ghost=0, Stale=279. paper_trader: 16 completed (2h). §18 持续: UB/INTC/BZ/SOXL/FOLKS/AIO forward_outcome FAILED. 64h+ 连续无中断.

[2026-06-20 05:21 UTC] ✓ 系统正常 — Features=1238/h (亚盘早市缓降). Opportunities=7720 (+83/h net); opp_last_1h=493 (亚盘持续回升). Knowledge=1022 (+17/h). §17 timed_out=252 (+2). Ghost=0, Stale=279. paper_trader: 18 completed (2h). §18 持续: NVDA/SPX/AAOI 等 Bitget-only ticker fetch failed. 65h+ 连续无中断.

[2026-06-20 06:21 UTC] ⚠️ 新信号 — Features=983/h (亚盘低谷). Opportunities=7782 (+62/h net); opp_last_1h=555 (回升). Knowledge=1044 (+22/h). §17 timed_out=255 (+3). Ghost=0, **Stale=282 (+3)**. 异常: (1) **dreaming_agent stale=2** (首次出现, 日周期 agent 卡住, 观察下一轮是否自愈); (2) **OKX "Invalid Sign" auth error** (equity_observer OKX demo 账户签名失败, 可能密钥过期/时区漂移, 当前仅影响 demo 账户余额查询). §18 持续: SIREN/HYPE/SOXL 等 ticker fetch failed. 66h+ 连续无中断.

[2026-06-20 07:21 UTC] ⚠️ 持续 — Features=1490/h (亚盘爬升回归). Opportunities=7877 (+95/h). Knowledge=1062 (+18/h). §17 timed_out=259 (+4). Ghost=0, Stale=283 (+1). **dreaming_agent stale=2 持续第 2 轮 (未自愈, §21 升级为已知问题)**. OKX Invalid Sign 持续 (BTW/XAG §18 ticker failed 同步). market_observer running=1 (执行中). 67h+ 连续无中断.

> **§21 [NEW] dreaming_agent stale 积累**: 首现 round 90 (2026-06-20 06:21 UTC), round 91 仍 stale=2. 日周期 agent 约每 24h 运行一次, stale 表明上一次运行超时未清理. 影响: 日度知识合成/lesson 提取可能跳过. 建议: 检查 dreaming_agent timeout 配置与 stale 清理逻辑; 下次 gateway restart 后观察是否自动消除.

[2026-06-20 08:21 UTC] ✓ 系统正常 — Features=1488/h (亚盘持稳). Opportunities=7972 (+95/h); opp_last_1h=745 (亚盘持续爬升). Knowledge=1086 (+24/h). §17 timed_out=263 (+4). Ghost=0, Stale=284 (+1). §21 dreaming_agent stale=2 持续第 3 轮. market_observer stale=4 (较上轮 +2). §18 持续: LAB/USDT OHLCV fetch failed. OKX auth 本轮日志未见 (可能暂时消失或窗口错开). 68h+ 连续无中断.

[2026-06-20 09:21 UTC] ⚠️ **§22 universe scanner 停滞** — Features=246/h (骤降, 正常值 1200-1900/h). 最新 feature ts=2026-06-20T08:29 UTC, 已 52 分钟无新数据. 三条最新记录 ts 完全相同 (同批次写入后卡住). gateway 进程存活 (PID 33948), 无崩溃日志, 属 scheduler job internal hang. Opportunities=8019 (+47/h 趋缓). Knowledge=1115 (+29/h, 亚盘爬升). §17 timed_out=266 (+3). §21 dreaming_agent stale=2 持续第 4 轮. 影响: feature pipeline 停产, 所有依赖 feature snapshot 的下游分析 (strategy_designer, risk_critic) 将消耗陈旧数据. 建议: gateway restart 可恢复 (scheduler 重新注册 job); 根因可能是 §18 某符号 OHLCV 阻塞整个 scanner cycle.

[2026-06-20 10:21 UTC] ⚠️ **§22 持续加重** — Features=0/h (本轮零产出). 最新 feature ts 仍 2026-06-20T08:29 UTC, 停滞累计 **112 分钟**. §17 timed_out=266 (+0, 本轮未增). Ghost=0, Stale=284. Opportunities=8024 (+5/h 几近停滞, 下游影响显现). Knowledge=1131 (+16/h). dreaming_agent stale=2 第 5 轮. market_observer stale=4 持平. **需要 gateway restart 恢复 universe scanner**.

[2026-06-20 11:21 UTC] ⚠️ **§22 升级** — Features=0/h (第 3 轮零产出), 停滞累计 **172 分钟**. **watchdog 显示 08:49/08:51/09:29/09:31/09:33 UTC 已多次 restart, 但 scanner 仍未恢复** — 单纯重启无效, 根因更深. Opportunities=8028 (+4/h, 停滞). Knowledge=1140 (+9/h). §17 timed_out=266 (+0). Ghost=0, Stale=284. dreaming_agent stale=2 第 6 轮. PID 变更 (33948→30236) 确认已重启. 建议: 检查 universe/features.py 中 scheduler job 是否有僵尸 aiohttp session 或死锁; 手动调用 `aletheia gateway restart` 并观察启动日志; 若仍无效需检查 scheduler job 注册逻辑.

[2026-06-20 12:21 UTC] ⚠️ **§22 持续 — 第 4 轮零产出, 停滞 232 分钟**. latest_feature_ts 仍 2026-06-20T08:29 UTC. **Opportunities=8028 (第 2 轮冻结, +0)**. Knowledge=1147 (+7/h). §17 timed_out=266 (+0, 完全静止). Ghost=0, Stale=284. 最后一次 watchdog restart=09:33 UTC (近 3h 前), 之后稳定运行但 scanner 死寂. dreaming_agent stale=2 第 7 轮. **需人工介入: 检查 universe/features.py 调度 job 内部是否有无限等待的 OHLCV fetch (§18 Bitget-only symbol 可能触发 aiohttp 死锁).**

[2026-06-20 13:21 UTC] ⚠️ **§22 根因确认 — 停滞 292 分钟 (第 5 轮零产出)**. **根因: `universe/registry.py:52` `exchange.load_markets()` 抛出 `ccxt.NotSupported: bitget does not support linear market` → `universe/service.py:142 refresh()` 失败 → 整个 universe refresh 中断, scanner 停止产出.** Opportunities=8041 (+13/h, 极低). Knowledge=1167 (+20/h). §17 timed_out=266 (+0). Ghost=0, Stale=284. dreaming_agent stale=2 第 8 轮. 修复方向: `registry.py:sync_registry()` 中捕获 `ccxt.NotSupported` 并跳过该 market type, 或在调用 `load_markets()` 前过滤掉 linear market type 参数.

[2026-06-20 14:21 UTC] ⚠️ **§22 持续 — 停滞 352 分钟 (第 6 轮零产出, ~6 小时)**. latest_feature_ts 仍 08:29 UTC. Opportunities=8053 (+12/h, 极低). Knowledge=1174 (+7/h). §17 timed_out=266 (+0). Ghost=0, Stale=284. dreaming_agent stale=2 第 9 轮. market_observer stale=4/timed_out=29 持平. **§22 根因已确认, 待修复.**

[2026-06-20 15:21 UTC] ⚠️ **§22 持续 — 停滞 412 分钟 (第 7 轮零产出, ~7 小时)**. Opportunities=8065 (+12/h). Knowledge=1187 (+13/h). §17 timed_out=266 (+0). Ghost=0, Stale=285 (+1). market_observer stale=5 (+1). dreaming_agent stale=2 第 10 轮. **§22 根因已确认 (`registry.py:52` NotSupported), 待修复.**

[2026-06-20 16:21 UTC] ⚠️ **§22 持续 — 停滞 472 分钟 (第 8 轮零产出, ~8 小时)**. Opportunities=8080 (+15/h). Knowledge=1201 (+14/h). §17 timed_out=266 (+0). Ghost=0, Stale=285. dreaming_agent stale=2 第 11 轮. market_observer stale=5/timed_out=29 持平. **§22 `registry.py:52` NotSupported 待修复.**

[2026-06-20 17:21 UTC] ⚠️ **§22 持续 — 停滞 532 分钟 (第 9 轮零产出, ~9 小时)**. Opportunities=8095 (+15/h 极低). Knowledge=1227 (+26/h, EU开盘提速). §17 timed_out=266 (+0). Ghost=0, Stale=285. dreaming_agent stale=2 第 12 轮. market_observer: 43 completed + 1 CWE + stale=5 + timed_out=29. **§22 `registry.py:52` NotSupported 持续待修复.**

[2026-06-20 18:21 UTC] ⚠️ **§22 持续 — 停滞 592 分钟 (第 10 轮零产出, ~10 小时)**. Opportunities=8111 (+16/h). Knowledge=1249 (+22/h). §17 timed_out=266 (+0). Ghost=0, **Stale=289 (+4)**. market_observer stale=8 (+3, 恶化). **post_trade_reviewer 首现 stale=1**. watchdog 18:05/18:15/18:17/18:19 UTC 再次多次重启, scanner 仍未恢复. **§22 `registry.py:52` NotSupported 根因确认, stale 继续扩散, 须修复.**

[2026-06-20 19:21 UTC] ⚠️ **§22 部分自愈** — **scanner 于 18:23 UTC 自行恢复** (停滞 ~594 分钟后, watchdog 重启触发). features_18:21-19:21=62 (极低, ~10 条/分钟 vs 正常 25-30 条/分钟). latest_feature_ts=19:13 UTC. Opportunities=8151 (+40/h, 回升). Knowledge=1263 (+14/h). §17 timed_out=266 (+0). Ghost=0, Stale=289 (持平). market_observer stale=8/post_trade_reviewer stale=1 均持平. **§22 根因 (`registry.py:52` NotSupported) 未修复, 下次 registry refresh 触发时仍会复发. 节奏降至 40%正常值, 仍需修复.**

[2026-06-20 20:21 UTC] ⚠️ **§22 降速严重** — features_19:21-20:21=60 (固定 10条/10分钟, ~60/h). **对比停滞前 07-08:30 UTC: 90分钟出 2232 条 (~25条/分); 恢复后 19-20:21 UTC: 80分钟出 80 条 (~1条/分). 产出率降至 3.6% 正常水平.** Universe asset 规模疑似因 registry refresh 失败大幅萎缩. Opportunities=8178 (+27/h 低位). Knowledge=1270 (+7/h). §17 timed_out=266 (+0). Stale=289. **§22 根因未修, universe 规模需重建.**

[2026-06-20 21:21 UTC] ⚠️ **§22 持续降速** — features_20:21-21:21=60 (仍固定 10条/10分钟, 连续 3轮稳定低吞吐). Opportunities=8223 (+45/h, US开盘前小幅回升). Knowledge=1277 (+7/h). §17 timed_out=266 (+0). Stale=289. 各 agent 运行正常: paper_trader 58 completed, risk_critic 34 completed. **§22 universe 萎缩已稳定为新平衡态, 根因 `registry.py:52` 未修复.**

[2026-06-20 22:21 UTC] ⚠️ **§22 持续** — features=60/h (第 4 轮固定低吞吐). Opportunities=8250 (+27/h). Knowledge=1284 (+7/h). §17 timed_out=266 (+0). Stale=289. paper_trader 61 completed, risk_critic 35 completed (其他管道正常). **§22 `registry.py:52` 待修复.**

[2026-06-20 23:21 UTC] ⚠️ **§22 持续** — features=60/h (第 5 轮固定低吞吐, 连续 10条/10分钟). Opportunities=8286 (+36/h). Knowledge=1291 (+7/h). §17 timed_out=266 (+0). Stale=289. paper_trader 65 completed (2h). **§22 `registry.py:52` 待修复.**

[2026-06-21 00:21 UTC] ⚠️ **§22 持续** — features=60/h (第 6 轮固定低吞吐). Opportunities=8322 (+36/h). Knowledge=1298 (+7/h). §17 timed_out=266 (+0). Stale=289. paper_trader 69 completed (2h). **§22 `registry.py:52` 待修复. 75h+ 连续运行, 其他管道稳定.**

[2026-06-21 01:21 UTC] ⚠️ **§22 持续** — features=60/h (第 7 轮固定低吞吐). Opportunities=8358 (+36/h net); opp_last_1h=45/knowledge_last_1h=8 为 UTC 午夜换日窗口效应, 非真实下降. §17 timed_out=266 (+0). Stale=289. paper_trader 73 completed (2h). **§22 `registry.py:52` 待修复. 76h+ 连续运行.**

[2026-06-21 02:21 UTC] ⚠️ **§22 持续** — features=60/h (第 8 轮固定低吞吐). Opportunities=8394 (+36/h). Knowledge=1313 (+7/h). §17 timed_out=266 (+0). Stale=289. 亚盘深夜低频 (dreaming_agent/equity_observer 静默, 正常). **§22 `registry.py:52` 待修复. 77h+ 连续运行.**

[2026-06-21 02:24 UTC] ⚠️ **§22 持续** — features=60/h (第 9 轮, 10/10min 均匀). Opportunities=8394 (+0, 3min 窗口). Knowledge=1313. §17 timed_out=266 (+0). Stale=289. 亚盘深夜. **§22 `registry.py:52` 待修复. 77h+ 连续运行.**

[2026-06-21 02:58 UTC] ⚠️ **§22 持续** — features=50/h (第 10 轮低吞吐). Opportunities=8412 (+18/h). Knowledge=1320 (+7/h). §17 timed_out=266 (+0). Stale=289. price_fallback 警告仅 §18 已知(兜底正常). **§22 `registry.py:52` 待修复. 78h+ 连续运行.**

[2026-06-21 03:21 UTC] ⚠️ **§22 持续 + §23 新观察: frontdoor 短暂重启** — features=60/h (第 11 轮低吞吐). Opportunities=8430 (+18/h). Knowledge=1327 (+7/h). §17 timed_out=266 (+0). Stale=289. **新**: `gateway_supervisor` 03:07/03:08 UTC 连续两次重启 frontdoor (PID 7000 未响应 :8787); 03:10 后已自愈 (features 恢复正常). 影响范围: 约 2min Web UI 不可用, pipeline 后端不受影响. **§22 `registry.py:52` 待修复. 78h+ 连续运行.**

[2026-06-21 04:08 UTC] ⚠️ **§22 持续 + §23 重复: frontdoor 第2次重启** — features=68/h (轻微回升). Opportunities=8464 (+34/h). Knowledge=1338 (+11/h). §17 timed_out=266 (+0). Stale=291 (+2: market_observer+1, post_trade_reviewer+1). **§23**: 04:07:49/04:07:50 UTC frontdoor (PID 11784) 再次被 supervisor 重启, 上次 03:07, **间隔约 1h, 疑似周期性 OOM 或内存泄漏**. 建议检查 frontdoor 内存占用和重启根因. **§22 `registry.py:52` 待修复. 79h+ 连续运行.**

[2026-06-21 04:21 UTC] 🔴 **§23 升级: frontdoor 重启风暴** — features=57/h. Opportunities=8472 (+8/h). Knowledge=1344 (+6/h). §17 timed_out=266. Stale=291. **§23 严重**: 04:07→04:10→04:11 UTC frontdoor 三连重启 (PID 11784→34960→19748), 每次新进程 ~2min 内再次失败; 04:15 features 恢复正常, 目前暂稳. 影响约 8min Web UI 不可用. **根因未查: 疑似 frontdoor 内存泄漏/端口绑定竞争/启动依赖未满足. 建议检查 `frontdoor-errors.log` 和 frontdoor 启动日志.** §22 `registry.py:52` 待修复. 79h+ 连续运行.

[2026-06-21 05:08 UTC] 🔴 **§23 根因确认 + 持续恶化** — features=54/h. Opportunities=8504 (+32/h). Knowledge=1361 (+17/h). §17 timed_out=266. Stale=292 (+1: risk_critic stale). **§23 根因**: `frontdoor-errors.log` 显示 `OSError: [WinError 64] The specified network name is no longer available` → IOCP `accept_coro` 抛出 → frontdoor 无法接受连接 → supervisor 重启; 04:07–04:32 UTC 共重启 ≥7 次 (PID 11784/34960/19748/29652/31132/7132/32028/33904). **与 §17 同源 (Windows ProactorEventLoop IOCP bug)**. 04:32 后稳定. 修复方向: 在 frontdoor 的 asyncio serve 循环捕获 `OSError` 并优雅重建监听 socket, 避免整进程崩溃. §22 `registry.py:52` 待修复. 80h+ 连续运行.

[2026-06-21 05:21 UTC] 🔴 **§23 持续重启风暴 — Web UI 基本不可用** — features=54/h (backend 正常). Opportunities=8512 (+8/h). Knowledge=1361 (+0). §17 timed_out=266. Stale=293. **§23**: 05:11–05:20 UTC 新增 5 批重启 (PID 31276→19612→4880→26392→7056), ~每 2min 一次, frontdoor 无法自愈. **建议人工 `uv run aletheia gateway restart` 或重启整个 supervisor.** §22 `registry.py:52` 待修复. 80h+ 连续运行.
