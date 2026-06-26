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
