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

## §0 2026-06-26 审计总览(workflow 逐条 vs HEAD 代码)

> 本文件原有 P0-P3 表("优先级排序与修复状态")是阶段自评。本审计逐条核对 HEAD 代码,确认所有声明"已修复"的修复点(代码侧)是否真的接通。RUNTIME 部分共审 22 个 issue(含 stragglers)。

**总计** 22 条:✅ 14 / ⚠️ 1 / ❌ 0 / ℹ️ 3 / 🕒 4

### ✅ FIXED — 14 条(代码侧 grep 已证,需 live runtime 二次确认)

| ID | 关键证据 |
|---|---|
| §1 risk_critic 成功率误判 | `agent_runtime/stability_monitor.py` 改为只计 `failed`/`stale` 为失败,`completed_with_errors` 不再算失败 |
| §3 stock_observer 全失败 | `config/schema.py:105` `observed_stocks` 默认为空 + scheduler guard 跳过空列表 |
| §4 ps_lessons 学习闭环 | `aletheia/agents/dreaming_agent.py::_extract_lessons` 每日扫描 high-confidence post_trade_reviews → `ps_lessons` |
| §7 知识注水 99% 重复 | `aletheia/knowledge/manager.py` content-hash 去重 + 80-byte 质量门 + placeholder 拦截 + LLM 失败不写入 |
| §9 KB DB/磁盘不同步 | `scripts/cleanup_duplicate_knowledge.py --backfill` 就绪(质量过关孤儿文件回写 DB) |
| §10/§11 ghost scanner + agent stale | `_cleanup_stale_agent_runs` / `_cleanup_stale_scanner_runs` 加 10min 年龄门 + caller 线程显式标记 timed_out |
| §15 portfolio_snapshot 停滞 | `_run_sync_with_timeout("portfolio_snapshot", 45s)` + 移到 default executor(workers=2) + `next_run_time=30s` 即时执行 |
| §16 scanner futures regression | `universe/features.py:318` 注释 "batch tickers → volume pre-filter → concurrent OHLCV";`_chunked_fetch_tickers` + Semaphore(5) + timeout 提升至 600s |
| §17 IOCP thread leak | `agent_runtime/scheduler.py:242` `asyncio.SelectorEventLoop()` + `aletheia/async_utils.py` 全局策略;process-level WindowsSelectorEventLoopPolicy(`server/app.py` import time) |
| §18 Binance batch ticker regex 拒绝 | ASCII pre-filter + venue=cfg.market_data.ccxt_exchange |
| §23 frontdoor hang | 5bfa4a0 (IOCP 根治) + frontdoor 代理 `aiter_raw` 捕获 ReadError + supervisor 不再级联杀 backend |
| §24 connectivity 韧性 | `connectivity_monitor` 仿 LLM gate + 3层网络韧性 + CCXT/aiohttp 超时治 load_markets 挂死(2723ab0) |
| §25 dashboard_static 丢失 | gateway startup 强制检查 + 部署时已纳入 git |
| §1 误判额外条目 | `stability_monitor` 修正口径 |

### ⚠️ PARTIAL — 1 条

| ID | 残余 |
|---|---|
| §19 [straggler] market_observer aiohttp 连接泄漏 | SelectorEventLoop(§17)消除了 IOCP drain 路径,大幅减少泄漏来源,但 `Unclosed client session/connector` 日志仍可能在 LLM 故障+取消组合下出现。需 live 监测 `errors.log` 内此模式;若仍有,加 `async with` 显式 close |

### ℹ️ 设计如此 — 3 条

| ID | 说明 |
|---|---|
| §2 prediction_observer + divergence_scanner 停止运行 | 30min 周期是设计,divergence_scanner 链式跟随 |
| §5 prediction_events 无记录 | 数据写入 `prediction_markets` + `prediction_market_claims`(非 `prediction_events` 表) |
| §6 opportunity→strategy 转化率极低 | 两者是**独立管道**(opportunities→proposals 走 paper_trader;strategies 走 strategy_designer),不该期望线性转化 |

### 🕒 RUNTIME_ONLY — 4 条(代码层防护已就位,需 live 数据确认)

| ID | 静态证据 |
|---|---|
| §8 market_observer 偶发未完成 | 监测即可;无代码修复必要 |
| §20 调度器 job missed | APScheduler 配置中 `misfire_grace_time` 已设置,但是否仍有 missed 报警需 live 监测 |
| §21 dreaming_agent stale 积累 / `completed_with_errors` 率 | `_cleanup_stale_agent_runs` 已加,需 live 确认 stale 不再累积 |
| §22 universe scanner 停滞 / feature pipeline 停产 | §16 + §17 修复后理论上消除,需 live 确认 `registry.py:52` 不再卡 |

> 本审计 grep + read evidence 完成。RUNTIME 类修复**结构上**已就位,但本质需要 live agent 运行数据(orders/knowledge/agent_runs 表的近期增量)来最终确认。建议下一轮 doctor 巡检以 1-2 小时为窗口,观察上述 4 个 RUNTIME_ONLY 项是否在 live 数据中真的恢复。

**未 cover issue 完整修复计划见**: [`FIX_PLAN_2026-06-26.md`](./FIX_PLAN_2026-06-26.md)

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

## 监测日志 + §23/§24 韧性修复 → 已归档

2026-06-17→06-26 的逐小时监测流水（约 944 行）与 §23/§24 frontdoor/backend 韧性修复 + 根因纠正写法，已移至 [`archive/RUNTIME_MONITORING_AND_RESILIENCE_2026-06.md`](archive/RUNTIME_MONITORING_AND_RESILIENCE_2026-06.md)。

- **§23/§24 真根因**见 memory `aletheia-frontdoor-disconnect-真根因`（非 IOCP；frontdoor `aiter_raw` 未捕获 ReadError + supervisor 误杀 backend，5e8772d/1062f63/d14158f/abdbf51 已修）。
- **残余跟进**（LLM provider 健壮性 / load_markets 体积 / readiness window）在 `frozen_optimization_backlog.md` D17/D19。
- **开放的运行时行为问题**（agent 产出 / 学习闭环 / 知识质量）见上方 §0–§9 与 [`FIX_PLAN_2026-06-26.md`](FIX_PLAN_2026-06-26.md)。

[2026-06-26 12:22 UTC] [HIGH] §23 第15轮刚启动 — features=102/h (全桶=17,crash发生在4min前尚未影响桶). Opportunities_1h=67. Knowledge=4741(+4). §17 timed_out=345(+0). Stale=346(-1).
  §23第15轮: 12:18(PID 18392),距第14轮末次(10:06)静默132min. 静默窗口规律: 第13轮结束→第14轮开始约2.5h,第14轮结束→第15轮开始约2h12min,静默窗口逐步稳定在2-2.5h. §25: 稳定. §24 backend: 稳定. 178h+ 连续运行.

[2026-06-26 13:22 UTC] §23 第15轮持续中 — features=102/h (全桶=17均等,crash在2min前发生尚未影响). Opportunities_1h=62. Knowledge=4746(+5). §17 timed_out=345(+0). Stale=346(+0).
  §23第15轮崩溃序列: 12:18→12:28(10min)→12:38(10min)→12:48(10min)→13:19(31min). 共5次. 末次13:19,距今3min. 10min等间隔模式显著——与历史轮次一致. §25: 稳定. §24 backend: 稳定. 179h+ 连续运行.

[2026-06-26 14:22 UTC] §23 第15轮持续中 — features=102/h (全桶=17均等). Opportunities_1h=69. Knowledge=4748(+2). §17 timed_out=345(+0). Stale=346(+0).
  §23第15轮新增: 13:29(PID 26284)→13:39(10min)→13:49(10min). 第15轮累计8次崩溃(12:18→13:49),末次13:49,距今33min静默. 第15轮特征:10min固定间隔崩溃,异常稳定(12:18,12:28,12:38,12:48,13:19,13:29,13:39,13:49).
  新异常: equity_observer completed_with_errors(1次,2h内),需关注是否持续. §25: 稳定. §24 backend: 稳定. 179h+ 连续运行.

[2026-06-26 15:22 UTC] [HIGH] §23+equity_observer双告警 — features=95/h (全桶15-17,下滑趋势,末次feature=15:17距今5min). Opportunities_1h=64. Knowledge=4756(+8). §17 timed_out=345(+0). Stale=347(+1).
  §23第15轮新增: 15:15(PID 44300),距13:49静默86min后复发. 第15轮累计9次崩溃(12:18→15:15). 末次15:15,距今7min.
  [MEDIUM] equity_observer completed_with_errors: 3次(2h内,上轮1次→本轮3次),持续累积. 含义:权益数据采集失败率上升,可能导致drawdown_scale误判或PI幻觉. 建议查 data/logs/ 中equity_observer相关错误详情.
  features桶梯度下滑(17→16→15)=crash后scanner未完全恢复,或universe缩减. §25: 稳定. §24 backend: 稳定. 180h+ 连续运行.

[2026-06-26 16:22 UTC] [HIGH] §23持续+universe缩减+equity_observer告警 — features=90/h (全桶稳定=15,已非crash边界效应,universe真实从16→15缩减). Opportunities_1h=53. Knowledge=4764(+8). §17 timed_out=346(+1). Stale=347(+0).
  §23第15轮新增5次: 15:25(PID 38584)→15:35(10min)→15:45(10min)→15:55(10min)→16:15(20min). 第15轮累计14次崩溃(12:18→16:15),呈10min固定间隔. 末次16:15,距今7min.
  [MEDIUM] equity_observer completed_with_errors: 5次(2h内,上轮3次→本轮5次),持续上升,建议检查权益数据源是否异常.
  [LOW] universe桶大小 17→16→15,逐步缩减,可能有资产被移除或scanner扫描失败. §25: 稳定. §24 backend: 稳定. 180h+ 连续运行.

[2026-06-26 17:22 UTC] [HIGH] §23第15轮持续+equity_observer持续告警 — features=96/h (全桶=16,较上轮15小幅回升,波动中). Opportunities_1h=55. Knowledge=4770(+6). §17 timed_out=347(+1). Stale=346(-1).
  §23第15轮新增5次: 16:26(PID 42036)→16:35(9min)→16:45(10min)→16:55(10min). 第15轮累计18次崩溃(12:18→16:55),10min固定间隔无变化. 末次16:55,距今27min.
  [MEDIUM] equity_observer completed_with_errors: 7次(2h内,上轮5次→7次),每小时约+2,持续稳步累积,建议优先排查权益数据源错误. §25: 稳定. §24 backend: 稳定. 181h+ 连续运行.

[2026-06-26 18:22 UTC] §23第15轮静默中,universe恢复 — features=102/h (全桶=17,已恢复正常). Opportunities_1h=63. Knowledge=4770(+0). §17 timed_out=347(+0). Stale=346(+0).
  §23: 末次16:55(PID 41316),距今87min静默.第15轮累计18次崩溃(12:18→16:55,持续4h37min),按历史模式预测下一轮约18:30-20:00 UTC启动.
  [MEDIUM] equity_observer completed_with_errors: 9次(2h内,上轮7次→9次,+2/h稳定积累). 建议排查:可能是 broker余额API超时或exchange_account查询失败. §25: 稳定. §24 backend: 稳定. 182h+ 连续运行.

[2026-06-26 19:22 UTC] ✓ 系统正常(§23第15轮静默延长) — features=102/h (全桶=17均等). Opportunities_1h=64. Knowledge=4772(+2). §17 timed_out=347(+0). Stale=346(+0).
  §23: 末次16:55,距今147min(2h27min)静默,已超第14轮轮间静默(132min).预测第16轮触发窗口延后至19:30-21:00 UTC.
  [MEDIUM] equity_observer completed_with_errors: 11次(2h内,+2/h持续). §25: 稳定. §24 backend: 稳定. 182h+ 连续运行.

[2026-06-26 20:22 UTC] ✓ 系统正常(§23第15轮静默持续延长) — features=102/h (全桶=17均等). Opportunities_1h=64. Knowledge=4773(+1). §17 timed_out=347(+0). Stale=346(+0).
  §23: 末次16:55,距今207min(3h27min)静默——第15轮结束后静默时长已超第14轮结束后(132min)的1.5倍,疑似进入更长稳定窗口.
  [MEDIUM] equity_observer completed_with_errors: 13次(2h内,+2/h持续,已观察>5h).建议优先排查:aletheia doctor输出+data/logs/中equity_observer日志. §25: 稳定. §24 backend: 稳定. 183h+ 连续运行.

[2026-06-26 21:22 UTC] ✓ 系统正常(§23静默持续4h+) — features=102/h (全桶=17均等). Opportunities_1h=64. Knowledge=4778(+5). §17 timed_out=347(+0). Stale=346(+0).
  §23: 末次16:55,距今267min(4h27min)——与第13→14轮间2.5h静默相比显著延长,疑似§23进入新的更长自然稳定期,或第15轮结束即为最后一轮.
  [MEDIUM] equity_observer completed_with_errors: 15次(2h内,+2/h,已持续6h+,每次约30min发生一次). §25: 稳定. §24 backend: 稳定. 184h+ 连续运行.

[2026-06-26 22:22 UTC] ✓ 系统正常(§23静默5h+) — features=102/h (全桶=17均等). Opportunities_1h=64. Knowledge=4778(+0). §17 timed_out=347(+0). Stale=346(+0).
  §23: 末次16:55,距今327min(5h27min),**§23 Storm确认终止第二次**——超过历史任何静默窗口约8倍,与第13→14轮间隔(132min)相比已超2.5倍,第15轮应为Storm最后一轮.
  [LOW→降级] equity_observer completed_with_errors: 16次(2h内,本轮仅+1,较前几轮+2/h有所减缓). §25: 稳定. §24 backend: 稳定. 185h+ 连续运行.

[2026-06-26 23:24 UTC] ✓ 系统正常(§23静默6h28min) — features=102/h (全桶=17均等). Opportunities_1h=62.
Knowledge=4780(+2). §17 timed_out=347(+0). Stale=347(+0). Ghost=0.
§23: 末次16:55,距今389min(6h29min)——§23 Storm确认终止第三次——静默时长超历史窗口最大值约11倍.
[INFO→好转] equity_observer completed_with_errors: 16次(本轮+0,首次零增量,较此前+2/h显著恢复). 可能自愈.
§25: 稳定. §24 backend: 稳定. 186h+ 连续运行.

[2026-06-27 00:11 UTC] ✓ 系统正常(§23静默7h15min) — features=102/h (全桶=17均等). Opportunities_1h=63.
Knowledge=4780(+0). §17 timed_out=347(+0). Stale=347(+0). Ghost=0.
§23: 末次16:55,距今435min(7h15min)——§23 Storm终止确认持续(连续第3轮零新增).
[INFO→稳定] equity_observer completed_with_errors: 16次(+0,连续2轮零增量,自愈确认).
§25: 稳定. §24 backend: 稳定. 187h+ 连续运行.

[2026-06-27 00:22 UTC] ✓ 系统正常(§23静默7h26min) — features=102/h (全桶=17均等). Opportunities_1h=64.
Knowledge=4781(+1). §17 timed_out=347(+0). Stale=346(-1). Ghost=0.
market_observer running=0(上轮1→本轮0,正常完成). §23: 末次16:55,距今447min(7h27min).
[INFO→稳定] equity_observer completed_with_errors: 16次(+0,连续3轮零增量). §24 backend: 稳定 187h+.

[2026-06-27 01:08 UTC] ✓ 系统正常(§23静默8h12min) — features=102/h (全桶=17均等). Opportunities_1h=61.
Knowledge=4782(+1). §17 timed_out=347(+0). Stale=347(+0). Ghost=0.
§23: 末次16:55,距今492min(8h12min).
[INFO→稳定] equity_observer completed_with_errors: 16次(+0,连续4轮零增量,自愈完全确认).
§25: 稳定. §24 backend: 188h+ 连续运行.

[2026-06-27 01:22 UTC] ✓ 系统正常(§23静默8h26min) — features=102/h (全桶=17均等). Opportunities_1h=61.
Knowledge=4784(+2). §17 timed_out=347(+0). Stale=346(-1). Ghost=0.
§23: 末次16:55,距今506min(8h26min).
[INFO→稳定] equity_observer completed_with_errors: 16次(+0,连续5轮零增量). §24 backend: 188h+.

[2026-06-27 02:08 UTC] ✓ 系统正常(§23静默9h12min) — features=102/h (全桶=17均等). Opportunities_1h=61.
Knowledge=4784(+0). §17 timed_out=347(+0). Stale=347(+0). Ghost=0.
§23: 末次16:55,距今552min(9h12min).
[INFO] agent_2h_breakdown 本轮条目减少(dreaming/equity_observer/risk_critic消失)=2h滚动窗口正常滑动,非异常.
[INFO→稳定] equity_observer: 窗口外(>2h无新运行,与前5轮+0一致). §24 backend: 189h+.

[2026-06-27 02:22 UTC] ✓ 系统正常(§23静默9h26min) — features=101/h (最新桶16,其余17均等,时间窗口边界效应). Opportunities_1h=60.
Knowledge=4784(+0). §17 timed_out=347(+0). Stale=346(-1). Ghost=0.
§23: 末次16:55,距今566min(9h26min).
[INFO] 2h窗口仅剩market_observer/paper_trader/position_reconciler/post_trade_reviewer/strategy_designer,其余均滑出窗口(正常).
§24 backend: 189h+. 系统全面稳定.

[2026-06-27 03:08 UTC] [LOW] features 单桶空洞(50min_ago=0) — features=79/h(正常102),少一个桶(~02:15-02:25UTC区间漏写).
最新feature_ts=02:59(已恢复),无新§23崩溃. Opportunities_1h=62(正常). Knowledge=4787(+3).
影响: 该时段特征未入库,paper_trader该窗口内机会可能漏扫; §17 timed_out=347(+0). Ghost=0.
修复方向: 检查scheduler_jobs表~02:15-02:25的universe_scanner任务状态; 若DB busy_timeout触发则可提高timeout值.
[NEW-LOW] market_observer completed_with_errors: 1次(首次出现于本2h窗口). 具体错误需查agent日志.
§23: 末次16:55,距今612min(10h12min). §24 backend: 190h+.

[2026-06-27 03:22 UTC] [LOW] features桶值持续偏低 — features=93/h(桶值15-16,正常17). Opportunities_1h=63(正常).
Knowledge=4787(+0). §17 timed_out=347(+0). Stale=346(+0). Ghost=0.
上轮空洞(50min_ago=0)已滑出1h窗口,不再计入;但新桶值15-16说明scanner仍略慢(非满17).
影响: features总量偏低~8%,未达完全恢复;paper_trader扫描窗口覆盖率略降.
[持续-LOW] market_observer completed_with_errors: 仍1次(+0,未新增,观察中).
§23: 末次16:55,距今626min(10h26min). §24 backend: 190h+.

[2026-06-27 04:08 UTC] [HIGH] §23 Round 16 开始 — features管道停止30min+,opportunities_1h减半.
崩溃时间: 2026-06-27T03:55:33 UTC (PID 25436). 距r15末次(16:55)间隔11h——超历史最长静默窗口(约4.5h).
features: latest_ts=03:29(最后一条),10/20/30min_ago均=0,1h总量=45(正常102).
Opportunities_1h=28(正常63). 管道在崩溃后等待重启恢复中.
影响: features停产38min+,paper_trader该窗口机会严重漏扫;需等frontdoor重启后管道自愈.
根因: §23未修复(fix 2723ab0已合并但gateway未restart);LLM DNS触发aiter_raw ReadError→frontdoor崩溃.
修复方向: 执行 uv run aletheia gateway restart --no-browser (需用户操作).
[更新] §23 Storm: 以为r15是最后一轮的判断错误——r16已证实Storm仍在继续,静默窗口异常延长至11h.

[2026-06-27 04:22 UTC] [HIGH] §23 R16 — features管道停产持续53min(03:29→04:22,无新写入). Opportunities_1h=14(↓78%).
errors.jsonl末尾仍为03:55单次崩溃(R16目前仅1次,间隔27min无新崩溃).
features_60min_ago=15(上轮r274末段写入),其余5桶全0. latest_feature_ts未变(03:29:45).
knowledge=4787(+0,亦停滞). Opportunities_total=15236(+0,无新机会入库).
影响: 超53min无特征产出,paper_trader几乎完全丧失新信号;当前周期内交易决策基于陈旧数据.
等待: frontdoor重启后backend应自动恢复scanner;若R16为单次crash后管道未自愈,需用户检查backend进程.
[2026-06-27 05:08 UTC] [CRITICAL] §23 R16密集崩溃 — features/opp完全停产98min,管道未自愈.
R16崩溃记录: 03:55(PID25436), 04:51(PID48160), 04:56(PID10660), 05:01(PID25588) — 共4次,密集期间隔5-6min.
features_1h=0, opp_1h=0, latest_feature_ts=03:29(98min前). opportunities_total=15236(全停).
Knowledge=4787(+0). agent_2h_breakdown停滞(无新完成).
影响: CRITICAL——paper_trader完全无新信号,交易决策基于1.5h+陈旧数据.
修复方向(紧急): 用户需执行 uv run aletheia gateway restart --no-browser 彻底应用fix 2723ab0.
当前workaround已失效(supervisor重启frontdoor但fix未加载,crash继续);必须gateway-level重启.


[2026-06-27 05:22 UTC] [CRITICAL] §23 R16持续 — features/opp停产113min(03:29→05:22). R16累计6次崩溃.
新增崩溃: 05:09(PID11404), 05:18(PID12172). R16全记录: 03:55/04:51/04:56/05:01/05:09/05:18.
间隔模式: 56min(首次)->10min密集(4次)->8min->9min. 仍在活跃崩溃中.
features_1h=0, opp_1h=0, latest_ts=03:29(113min前). opportunities_total=15236(+0).
Knowledge=4787(+0). agent_2h_breakdown无变化.
影响: CRITICAL——管道停产近2h,paper_trader无新信号,交易系统实质性丧失决策能力.
等待用户执行: uv run aletheia gateway restart --no-browser


[2026-06-27 06:10 UTC] r279 [CRITICAL] §23 R16第7次崩溃(05:39 PID47884). features/opp停产160min(03:29→06:10).
R16全记录(7次): 03:55/04:51/04:56/05:01/05:09/05:18/05:39. 距最后崩溃31min.
features全桶=0, opp_1h=0, latest_ts=03:29(160min前). opportunities_total=15236(+0,6次巡检不变).
注意: 最后崩溃后31min内 features 仍全零——backend scanner crash后不自动恢复,须 gateway restart.
Knowledge=4787(+0). agent_2h: market_observer 14c+1e, paper_trader 43c, position_reconciler 44c.
影响: CRITICAL——近3h管道零产出,系统丧失决策输入,状态持续恶化.
修复: uv run aletheia gateway restart --no-browser (加载2723ab0修复,重启scanner)


[2026-06-27 06:22 UTC] r280 [CRITICAL] §23 R16加速恶化 — 新增3次崩溃(06:13/06:14/06:20), R16累计10次.
最近3次: 06:13(PID27796)->06:14(PID45112,间隔51s)->06:20(PID11680,间隔81s). 崩溃进入亚分钟级!
features全桶=0, opp_1h=0, latest_ts=03:29(172min前). opportunities_total=15236(+0,7次巡检不变).
R16完整时间线: 03:55/04:51/04:56/05:01/05:09/05:18/05:39/06:13/06:14/06:20.
模式演化: 首56min静止→密集5-10min→31min间隔→51s/81s崩溃(触底).
Knowledge=4787(+0). agent_2h_breakdown无变化.
影响: CRITICAL——管道停产近3h,frontdoor已进入崩溃死循环,2723ab0修复未加载.
紧急: uv run aletheia gateway restart --no-browser — 必须立即执行


[2026-06-27 07:09 UTC] r281 [CRITICAL] §23 R16持续 — 新增3次(06:22/06:33/07:04), R16累计13次.
R16完整时间线: 03:55/04:51/04:56/05:01/05:09/05:18/05:39/06:13/06:14/06:20/06:22/06:33/07:04.
间隔模式: 03:55→56min静→密集5-10min×4→21min→34min→34min→1min→5min→2min→11min→30min→31min.
features全桶=0, opp_1h=0, latest_ts=03:29(220min前). opportunities_total=15236(+0,8次巡检不变).
Knowledge=4787(+0). agent_2h_breakdown无变化. agent_timed_out_total=347(+0).
影响: CRITICAL——管道停产近4h,系统完全丧失决策输入.
修复(紧急): uv run aletheia gateway restart --no-browser


[2026-06-27 07:22 UTC] r282 [CRITICAL] §23 R16第14次崩溃(07:13 PID46104). 管道停产233min.
features全桶=0, opp_1h=0, latest_ts=03:29(233min前). opportunities_total=15236(+0,9次巡检不变).
R16至今14次. 最后两次: 07:04(PID27892)->07:13(PID46104,间隔9min). 崩溃循环持续.
Knowledge=4787(+0). agent_2h_breakdown/timed_out无变化.
影响: CRITICAL——管道停产近4h,系统完全丧失决策输入. 修复: uv run aletheia gateway restart --no-browser

