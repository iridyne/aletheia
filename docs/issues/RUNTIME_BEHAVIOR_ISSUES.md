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



---

## §24 2026-07-09~10 机器迁移后系统重启 — 全面审计记录

> **审计背景**: 系统在另一台机器上运行了 26 天后，于 2026-07-09 迁移到 UM790Pro（新机器）并重启。本节记录迁移后发现的所有问题、已修复项和待处理项。

---

### 🔴 [FIXED] IB-24-01: 系统长期完全暂停（26 天数据断层）

**现象**: `config.paused=True`，gateway 进入完全静止状态，所有 job（含数据采集）全部停止。  
**根因**:
1. 旧机器 Databricks LLM key 未迁移 → LLM 不可用 → startup gate 触发 pause
2. 代理（127.0.0.1:7890）在新机器上未运行 → exchange 连接失败
3. 全局 pause（blanket pause）将数据采集 job 一并停掉

**影响**: 26 天无 OHLCV/市场快照，opportunities 无法生成，knowledge 停止积累。  
**修复**:
- 禁用代理配置（`data/config.yaml: proxy: null`）
- 修改 `aletheia/agent_runtime/scheduler.py`：新增 `_ESSENTIAL_JOBS` 集合，layered pause 模式下仅暂停 AI 依赖 job（observer/trader/operator），保留数据采集 job 持续运行
- 新增 `HealthConfig.layered_pause_enabled: bool = True`
- **commit**: `fix/layered-pause` → merged to master

---

### 🔴 [FIXED] IB-24-02: LLM auto-resume 永久卡死无告警

**现象**: `auto_resume` 每 5 分钟轮询 LLM，永远失败但没有升级/告警，系统静默。  
**根因**: 缺少连续失败计数和告警升级逻辑；secret 缺失属于永久性失败，非瞬时网络问题。  
**修复**:
- `aletheia/agent_runtime/auto_resume.py`：新增 `_classify_stall()`（分类 secret_missing/auth_error/network_unreachable/unknown）、consecutive_failures 计数、指数退避、`_escalate_stall()` 升级告警
- `aletheia/agent_runtime/connectivity.py`：同样增加 exchange stall 升级逻辑
- 新增配置字段：`auto_resume_stall_threshold=6`、`auto_resume_stall_backoff_factor=2.0`、`auto_resume_max_interval_sec=1800`、`exchange_stall_threshold=30`
- **commit**: `fix/pause-stall-detection` → merged to master

---

### 🔴 [FIXED] IB-24-03: Windows Credential Manager 跨 session 隔离（WinError 1312）

**现象**: `doctor` 报告 `creds.bitget_demo=MISSING/BLOCKER`，但 Web UI 显示 Bitget Demo 已连接并能看到余额。  
**根因**: Windows Credential Manager 是 session 隔离的。Gateway 进程（interactive logon session）可访问 keyring；CLI/bash 进程（SSH 或非交互 session）获取 WinError 1312，`get_secret()` 返回 None。  
**影响**: doctor 误报 BLOCKER，CI/非交互脚本无法读取凭据。  
**修复**:
- `aletheia/config/secrets.py`：`set_secret()` 改为双写（encrypted file 始终写入，keyring 仅 best-effort）
- 新增 `migrate_keyring_to_file()`：gateway 启动时将 keyring 中已有的所有 secret 同步到 file store
- `aletheia/server/app.py`：lifespan 启动时调用 `migrate_keyring_to_file()`
- **commit**: `acbf7c6`

---

### 🔴 [FIXED] IB-24-04: 方向集中度熔断过严（23 个有效决策被系统性拦截 18 小时）

**现象**: 2026-07-06T15:27 起，25 个 `action=trade` PI 决策的 `proposal_id` 全部为 None。  
**根因**: `_proposal_direction_overheated()`（`trading_decision.py`）参数过严：
- `min_sample=5`：仅 5 个历史提案即触发检查，极易被少量同向提案触发
- `max_share=0.80`：80% 同向即拦截，趋势行情下完全正常的方向性集中被误判
- `override_conviction=85`：豁免门槛过高，PI 正常 conviction（55-65）无法绕过
- 自锁机制：提案被拦截 → 24h 窗口永远保持 100% 单向 → 所有后续决策持续被拦截

**影响**: 23 个有效多头决策（BTC/ETH/SOL/LTC/MON 等）在 2026-07-06 整个下午到次日凌晨被静默丢弃，错过实际上涨行情。  
**修复**:
- `min_sample` 5→15，`max_share` 0.80→0.95，`override_conviction` 85→75
- **commit**: `acbf7c6`

---

### 🟠 [FIXED] IB-24-05: NATGAS/USDT 无实时价格却被标记 execution_eligible=True

**现象**: PI Agent 对 NATGAS/USDT 产生 conviction=70 的 trade 决策，但 `proposal_id=None`。  
**根因**: `market_snapshots` 表里 NATGAS/USDT 当时无数据（price feed 未采集该标的），但 `allocator.py` 和 `signal_emitter.py` 无条件将 `execution_eligible=True`。`submit_decision_as_proposal()` 查价格得 None → price=0 → 静默返回 None。  
**影响**: PI 消耗 LLM tokens 做出决策，最终静默丢弃，无任何错误提示。  
**修复**:
- `aletheia/universe/allocator.py`：分配阶段检查 `market_snapshots` 2h 内是否有数据，无则 `execution_eligible=False` + WARNING 日志
- `aletheia/universe/signal_emitter.py`：创建 Opportunity 时做同样检查
- **commit**: `acbf7c6`
- **注**: NATGAS/USDT 等 Bitget 独有品种在 Binance/Bybit 无 OHLCV 历史，无法 backfill

---

### 🔴 [FIXED] IB-24-06: Databricks LLM endpoint URL 指向旧机器

**现象**: Resume API 返回 HTTP 403，gateway 拒绝 resume。  
**根因**: `data/config.yaml` 中 `base_url` 仍指向 `adb-5678659344564033.13`（旧机器）而非 `adb-8450873647930949.9`（当前机器）。  
**修复**: 更新 `data/config.yaml` endpoint URL，重启 gateway。**已解决。**

---

### 🟢 [FIXED] IB-24-07: FTS 知识库索引严重不足（89/5011 = 1.8%）

**现象**: doctor 报 `fts.coverage=89/604 (15%)`（实际 DB 查询：89/5011 = 1.8%）。  
**根因（已确认）**: `aletheia/knowledge/retrieval.py:32` 中 `_ALLOWED_CONFIDENCE = "high_confidence"`（单字符串）。  
`reindex_all` 过滤条件 `row.get("confidence") != _ALLOWED_CONFIDENCE` 排除了所有非 `high_confidence` 条目。  
实际上有 **1,405 条** `medium_confidence / active` 有效 content 被完全跳过：
- `system_action`: 1,362 条
- `reasoning_feedback`: 15 条
- `trading_memo`: 15 条
- `calibration_report`: 13 条

同时 `retrieve_lessons` SQL 也用 `AND confidence = ?`（单值查询），即使已索引的 medium 条目也无法被检索。

**影响**: PI Agent knowledge retrieval 几乎失效，experience loop 严重降级，无法检索 95% 的历史案例。  
**修复**:
- `aletheia/knowledge/retrieval.py`：`_ALLOWED_CONFIDENCE` 改为 tuple `("high_confidence", "medium_confidence")`；`reindex_all` 过滤改为 `not in`；`retrieve_lessons` SQL 改为 `AND confidence IN (?, ?)`
- `aletheia/cli/knowledge_cmd.py`：更新 echo/log 提示文字
- **修复后**: `ato knowledge reindex` 索引 **1,507 条**（原 89 条，提升 16.9 倍）
- **commit**: `4bc3707` — "fix: FTS reindex includes medium-confidence and all active items (IB-24-07)"

**备注**: 仍有 ~1,030 条 content 为空（文件丢失，非本次引入）属已知限制（IB-24-10 关联）。

---

### 🟢 [FIXED] IB-24-08: signal_settlement stall（12,827 条逾期 forward outcome 未结算）

**现象**: doctor 报 `content.signal_settlement: 78 forward outcomes past due_at, oldest 19min ago`；DB 实查：12,827 条 `settled_at IS NULL AND due_at <= now`。  
**根因**: `settle_due()`（`outcome_recorder.py`）是单次调用，每次仅处理 500 行。12,827 行积压 + 15 分钟调度间隔 = 需要 6.5 小时才能清空，而新数据不断流入，实际永远无法追上。  
**影响**: `data.signal_win_rate` 等指标基于已结算数据计算，大量未结算会导致统计偏差。  
**修复**:
- `aletheia/universe/outcome_recorder.py`：`settle_due()` 改为批次循环直到队列清空，每批 500 行，100 秒墙时预算（低于 scheduler 120s 超时），每次调用最多处理 `max_rows=2000` 行
- `aletheia/diagnostics/doctor.py`：修正过期注释（间隔是 15min 非 10min，阈值改为 20min）
- **commit**: `6729aa13`

---

### 🟢 [FIXED] IB-24-09: volume_scanner 停止运行（2.6 天无更新）

**现象**: doctor 报 `data.volume_ranking stale: 2.6d ago`，最新 `volume_ranking_snapshots.observed_at = 2026-07-07T02:40`。  
**根因**: `_async_data_collectors()` Phase B 共享 `_DC_DEADLINE_S=40s` 预算，Phase A（微结构 18s + 衍生品 18s = 36s）消耗大部分预算后，Phase B 剩余时间不足 4s。`volume_scanner` 排在 Phase B 最末，CoinGecko（15s 上限）+ macro（15s 上限）先耗尽剩余预算，`_bounded()` 检测到剩余≤2s 时静默跳过 volume_scanner，无错误日志。  
**影响**: universe 排序退化为静态列表，ranker 无法按实时成交量排序候选标的。  
**修复**:
- `aletheia/agent_runtime/scheduler.py`：Phase B 中 volume_scanner 调整为**最优先**执行（在 CoinGecko/macro 之前），保证其 12s 上限预算始终可用
- **commit**: `6729aa13`

---

### 🟡 [KNOWN LIMITATION] IB-24-10: Bitget 独有标的无 OHLCV 历史

**现象**: `data backfill` 对 META/USDT、AMD/USDT、NATGAS/USDT、GOOGL/USDT、SOXL/USDT、SAMSUNG/USDT、BZ/USDT 等全部失败（400 Bad Request）。  
**根因**: 这些是 Bitget 的 RWA/股票镜像品种，Binance/Bybit 无对应 symbol，OHLCV 数据源不覆盖。  
**影响**: 这些标的无法进行技术分析（RSI/SMA 等），OHLCV 依赖的 scanner family 无法产生有效信号。  
**缓解**: `execution_eligible` 修复（IB-24-05）已确保这些标的在 scanner 层被标记为不可执行。doctor `data.ohlcv` warning 持续存在但属于已知限制。  
**长期方案**: 接入 Bitget 历史数据 API 作为补充数据源（待规划）。

---

### ✅ [FIXED] IB-24-11: debate 信号提示偏向 LONG 导致 SHORT 机会被误判

**现象**: flow audit 报 `debate_impact: missed_alpha_suspect`，24h 内 2 个机会被 debate 降级后 TTL 到期：
- LDO/USDT short（score=51，15m 收益 +0.42%）
- SLX/USDT short（score=53，15m 收益 +0.60%）

**根因（最终确认）**: `_build_bull_prompt` / `_build_bear_prompt` 内的市场信号解读提示硬编码为 LONG 视角：
- bull prompt 中：`FR<-0.01% = crowded shorts → bullish`（LONG 视角正确，SHORT 视角反了）
- bear prompt 中：`FR>+0.01% = crowded longs → bearish`（LONG 视角正确，SHORT 视角反了）
- 当评估 SHORT 机会时，LLM 收到错误的信号解读方向 → bull/bear confidence 被反向校准 → debate adjustment 偏负 → conviction 被不合理地压低

**注**: debate 公式 `advantage = bull_confidence - bear_confidence` 本身是正确的（prompt 已承载方向，bull = "trade 会成功"，bear = "trade 会失败"），公式无需改变。

**修复**:
- `_build_bull_prompt`：根据 `direction` 选择正确的信号解读提示（SHORT：FR>+0.01%/taker<0.8 → 支持做空）
- `_build_bear_prompt`：SHORT 时提示做空风险（crowded short squeeze / buyer aggression 危险）
- 新增 `tests/filter_eval/scenarios/debate_direction.py` 5 个评估场景，覆盖 short/long × bullish/bearish debate 组合
- **commit**: `(本次 reverted-fix commit)`

---

### ✅ [FIXED] IB-24-12: ranker_excluded 大量 neutral_or_missing_direction（~14-30%）

**现象**: 7 天内 ~14-30% 的 opportunity 因 `neutral_or_missing_direction` 被 ranker 过滤。按 source_agent 细分：全部来自 `market_observer`（LLM 路径），signal_emitter 从不产生 neutral（direction=None 时直接跳过，不入库）。

**根因**: `market_observer` 在没有 universe 对应的现有 opp 时，会创建 `direction_bias='neutral'` 的独立 opportunity。这些 opp 在 ranker 的 direction gate（`paper_trader_ranker.py:148`）被立即过滤，从未进入信号链。

**数据（7天）**:
- momentum neutral: 284/2231 = 12.7%（全部来自 market_observer 标注/创建）
- mean_reversion neutral: 55/317 = 17.3%
- volatility neutral: 46/214 = 21.5%
- equity_observer（scanner_family=None）: 40 neutral，全部因 equity_shadow_track 跳过

**修复**: `aletheia/agents/market_observer.py` — 在创建独立 opportunity 前检查 `direction == "neutral"`，若是则记录 step 并直接返回（不入库）。标注现有 deterministic opp 时已有此保护（`if direction != "neutral": updates["direction_bias"] = direction`），standalone 路径补齐了这一检查。

**commit**: 本次修复

---

### ✅ [FIXED] IB-24-13: ttl_expired 是最大漏斗损耗（45-58% for momentum/mean_reversion）

**现象**: 7 天内 momentum 58%、mean_reversion 52% 的 opportunity 最终 status='expired'。

**根因**: 双层 TTL 设计不同步：
1. Universe member TTL（momentum=30min, mean_reversion=60min, volatility=2h）由 `allocator._FAMILY_TTL` 设置，写入 `universe_members.expires_at`。
2. Ranker 检查 `expires_at < now` 并返回 `skip_reason="ttl_expired"`，但**不修改** opportunity 的 status。
3. Opportunity 的 `valid_until` 字段从 `m.get("expires_at")` 继承 universe member 的 TTL，**但 `expire_stale_opportunities()` 只看 `created_at < now - 48h`**，完全忽略 `valid_until`。
4. 结果：30 分钟 TTL 的 momentum opp 入库后最多等待 48 小时，才被 cleanup job（3:17am 每日一次）标记为 expired。这些 opp 对 ranker 和 PI daemon 均不可用（已过 valid_until），却长期占据 status='open'。

**数据验证**:
- momentum: avg_ttl_min=29，1295 个 expired（全部 TTL=29min，cleanup 前一直是 open）
- mean_reversion: avg_ttl_min=59，164 个 expired
- volatility: avg_ttl_min=119，78 个 expired
- `valid_until IS NOT NULL` 的 opps 中 expired 比例远高于 `valid_until IS NULL` 的

**修复**: `aletheia/storage/repositories.py` — `expire_stale_opportunities()` 的 UPDATE 语句增加 `OR (valid_until IS NOT NULL AND valid_until < now)` 条件，使已过 valid_until 的 opp 在下次 cleanup 时立即被标记 expired，不再等 48h。

**注**: pi_operator_loop 每 4 小时（cron: 0,4,8,12,16,20:15 CST）运行，pi_daemon 为事件驱动（信号即触发）。TTL 过期问题不影响 daemon 的实时评估，但会造成 DB 中大量虚假 open 状态，影响漏斗统计分析。

**commit**: 本次修复

---

### 📋 待办汇总（按优先级）

| 优先级 | ID | 问题 | 状态 |
|--------|----|----|------|
| P0 | IB-24-07 | FTS 索引 89→1507，medium_confidence 过滤 bug 已修复 | ✅ 已修复 `4bc3707` |
| P0 | IB-24-08 | signal_settlement 12,827 条逾期未结算 | ✅ 已修复 `6729aa13` |
| P0 | IB-24-09 | volume_scanner 2.6 天未运行 | ✅ 已修复 `6729aa13` |
| P1 | IB-24-11 | debate 方向无关 bug — SHORT 被系统性压制 | ✅ 已修复 |
| P1 | IB-24-12 | 30% neutral_or_missing_direction | ✅ 已修复（market_observer 跳过 neutral standalone opp） |
| P1 | IB-24-13 | 45% TTL 到期 | ✅ 已修复（expire_stale_opportunities 增加 valid_until 检查） |
| P2 | IB-24-10 | Bitget 独有标的无 OHLCV 数据源 | 🗓 长期 |

---

### ✅ 本期已修复（2026-07-09~10）

| ID | 修复内容 | commit |
|----|---------|--------|
| IB-24-01 | Layered pause — 数据采集 job 在 AI 暂停期继续运行 | `fix/layered-pause` |
| IB-24-02 | auto_resume stall 检测与告警升级 | `fix/pause-stall-detection` |
| IB-24-03 | WinError 1312 keyring 跨 session 隔离修复（dual-write + migrate） | `acbf7c6` |
| IB-24-04 | 方向熔断参数放宽（min_sample=15, max_share=0.95, override=75） | `acbf7c6` |
| IB-24-05 | execution_eligible 无实时价格门控（allocator + signal_emitter） | `acbf7c6` |
| IB-24-06 | Databricks endpoint URL 更新（旧机器→新机器） | config 更新 |
| IB-24-07 | FTS reindex 包含 medium_confidence（89→1507 条索引） | `4bc3707` |
| IB-24-11 | debate 信号提示方向感知 — SHORT 评估不再使用 LONG 视角信号 | `a747f19` |
| IB-24-12 | market_observer 跳过 neutral standalone opp（不入库）| `96f6275` |
| IB-24-13 | expire_stale_opportunities 增加 valid_until < now 条件 | `96f6275` |
| IB-24-08 | settle_due() 改为批次循环（max_rows=2000, budget=100s），消除 12k 行积压 | `6729aa13` |
| IB-24-09 | volume_scanner 调整为 Phase B 最优先（Phase B 独立 40s 预算首位保证） | `6729aa13` |

---

## §MO Token Economics — market_observer 上下文 token 优化（2026-07-14）

> 背景：MON-030 观测发现 universe 扩至 16 个标的后每次 LLM 调用约 18s（Databricks gateway），
> 主要时间消耗在 input token 处理。P0 修复已提交（glossary 移至 system prompt、memo compact 化），
> P1/P2 以下待后续实现。

---

### ✅ [FIXED] MO-TOK-01（P0）：Indicator glossary 每个 symbol 重复注入

**现象**：`build_market_context()` 在输出末尾追加静态 `_INDICATOR_GLOSSARY`（760 chars / ~190 tokens）。
market_observer 对每个 symbol 各调用一次，16 symbols = **~3,040 tok/run 浪费**。

**修复**：
- `aletheia/trading/market_context.py`：增加 `include_glossary: bool = True` 参数，默认保持向后兼容
- `aletheia/agents/market_observer.py`：调用时传 `include_glossary=False`
- `aletheia/agents/market_observer.py`：将 glossary 内容（精简版）并入 `_SYSTEM_PROMPT`

**commit**：worktree-token-opt-p0

---

### ✅ [FIXED] MO-TOK-02（P0）：Trading memo 完整内容随每个 symbol 发送

**现象**：`render_memo_for_observer()` 生成约 244 tokens 的 memo 块，在 run 级别构建后随
每个 symbol 的 LLM 调用完整发送。16 symbols = **~3,900 tok/run 浪费**。完整版包含
`prior_corrections`（verbose meta-learning）、`timing_windows`、`memo_weight` 等与
即时 market read 相关性低的内容。

**修复**：
- `aletheia/analytics/insight_synthesizer.py`：`render_memo_for_observer()` 增加 `compact: bool = False`
  参数。compact 模式保留：direction bias、top-2 regime edges、top-2 anti-patterns；
  跳过：prior_corrections、timing_windows、memo_weight、key_insight。节省 ~136 tok/call。
- `aletheia/agents/market_observer.py`：调用时传 `compact=True`

**commit**：worktree-token-opt-p0

---

### 🗓 [PENDING] MO-TOK-03（P1）：期货专属字段注入 spot symbol

**现象**：`render_enriched_context_md()` 对所有 symbol 渲染 derivatives 表（funding rate、OI、LSR）、
cross-venue funding（Hyperliquid）、perp_structure，共约 130 tokens。spot-only 标的（如 BTC/USDT spot）
完全用不到这些内容。

**影响**：约 130 tok × N_spot_symbols/run 浪费；内容对 spot 的 market read 无实际帮助，
可能引入 LLM 混淆（空字段被解读为"无期货结构"而非"不适用"）。

**建议修复**：
- `aletheia/agents/market_observer.py` 的 `_analyze_symbol()` 中，`render_enriched_context_md()` 调用
  已有 `perp_structure=...` 参数；对 `market_type not in ("perp", "usdt_futures")` 的 instrument，
  同时将 `leverage`、`xvenue_funding`、`perp_structure` 参数传入空 dict，或在
  `render_enriched_context_md()` 内部按 market_type gate。

**文件**：`aletheia/agents/market_observer.py:340-398`，`aletheia/data/context_builder.py`（`render_enriched_context_md`）

---

### 🗓 [PENDING] MO-TOK-04（P2）：Macro + on-chain 全量随每 symbol 重复

**现象（macro）**：全局宏观信号（Fear & Greed、BTC.D、Nasdaq、DXY 等，~150 tokens）作为 enriched context
的一部分随每个 symbol 发送，内容完全相同。16 symbols = ~2,400 tok/run。

**现象（on-chain）**：DefiLlama stablecoin cap / TVL / DEX volume（~50 tokens）同样全量随每 symbol 发送。
16 symbols = ~800 tok/run。

**影响**：两者均为 global 信号，不含 symbol 维度，per-symbol 重复注入无信号增量，
仅增加 input 长度和 TTFT。

**建议修复**：在 `run_market_observer()` 中单独构建一次 `global_context_block`（macro + on-chain），
在 `_analyze_symbol()` 的 user prompt 中只注入当前 symbol 的微观数据，
global block 作为 system prompt 的一部分或 run-level prefix 注入一次（需评估是否影响 cache）。

**注意**：`render_enriched_context_md()` 目前把 macro/on-chain 与 per-symbol 衍生品数据混合渲染，
需要拆分函数签名或增加 `include_global=True/False` 参数。

**文件**：`aletheia/data/context_builder.py:render_enriched_context_md()`，`aletheia/agents/market_observer.py:392-399`

---

## §25 2026-07-13 Stability Baseline Qualification 期间发现(记录,未修)

> 候选 commit `c95b2be3`(首个稳定基线资格验证)期间在真实运行节点观测。

### 🟠 SBQ-25-01 — resume 端点不查 Startup Behavior Gate 的 durable blocker

- **观测**:资格验证中注入了一个 active `reconciliation_integrity_incident`(`c2eded75d057`),
  Gateway 于 07:10:46 UTC 正确进入 Startup Behavior Gate 阻断(layered pause,21 暂停/19 essential,
  durable blocker `paused_by_gate=true`)。但 07:12:17 UTC 一个外部调用
  (audit `actor=human, source=dashboard, event_type=gateway_resumed`,极可能是本机开着的 dashboard 标签页)
  命中 `POST /api/gateway/resume`,**在该 incident 仍 active、门 blocker 仍有效时**清除了暂停,
  随后门的 recheck 循环见 `runtime.paused=False` 便退出并清掉 durable blocker。
- **根因**:`aletheia/server/routes/health.py::resume`(`/gateway/resume`)与
  `aletheia/server/routes/operator.py::_set_paused(False)`(`/operator/resume`、telegram、agent_shell resume)
  只经 `resume_gate.resume_if_llm_available`(仅校验 LLM),**不调用
  `startup_behavior_gate.evaluate_startup_gate()` / 不检查 durable blocker**。因此人工/dashboard/telegram
  的一次 resume 可越过 Startup Gate,把门刻意保持暂停的系统重新放行(即使 DB 完整性/对账事故等 blocker 仍在)。
- **影响**:不违反门自身契约(门的 recheck 只恢复自暂停、不覆盖人工 kill/pause,已由
  `tests/test_startup_behavior_gate.py::test_recheck_does_not_resume_user_pause` 等 22 项单测证实);
  但削弱「Gate 阻断期保持暂停直到检查通过」的运行保证——一个残留的浏览器标签页即可静默解除门。
- **建议修复(未授权施工)**:让 resume 授权链在 Startup Gate 有 active blocker 时也复跑
  `evaluate_startup_gate()`(通过则清 blocker 并恢复,不通过则 409 拒绝),即把 Startup Gate 纳入
  `resume_if_*` 的判定,与 LLM gate 并列。属信任/行为边界,修复需 focused 回归测试。
- **状态**:🕒 记录待办(owner 指示暂不修)。资格验证其余 9 条标准全绿;#67 热循环实机增量已归零。
