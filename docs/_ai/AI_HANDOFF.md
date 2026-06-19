# AI Handoff

> **本文件是 append-only 变更史**(记录"做了什么改动 + 根因",机器无关)。
> 其中任何"当时机器状态"(gateway 在跑 / 观察期第 N 小时 / 凭证已存 / demo 单已落)
> 都是**写入时刻的快照,不代表当前**。要知道当前真实状态,运行 **`aletheia doctor`**。
> 双机意图/事实分离的金标准见 `docs/_ai/DEV_PROTOCOL.md`。

## Last Updated

2026-06-19 (Code review fixes — a550b62)

---

## Recent Change (Code review bug fixes — 2026-06-19, commit a550b62)

> **背景**: Batch 2 code review 发现 4 个 bug，上批次 Batch 2 实现后跟进修复。

**4 个修复**:
1. **CRITICAL** `migrations.py`: `universe_members` 缺 `atr_pct` 列 → ATR 仓位缩放在生产 DB 静默失效。补 `_ADDITIVE_COLUMNS` 条目，ALTER TABLE 幂等修复。
2. **HIGH** `strategy_designer.py`: `_covered` 集合包含 retired/failed 策略 → 已死亡 symbol 误标"已覆盖"，新策略设计被阻断。改为只过滤活跃状态。
3. **MEDIUM** `strategy_designer.py`: `.replace("/","")` 违反 CLAUDE.md 符号规范 → 改用 `to_bitget()` from `aletheia.data.symbols`。
4. **INFO** `allocator.py`: `get_family_multipliers()` docstring 仍写 `derivatives→4h`，已更新为 `derivatives→1h`。

**验证**: 31 tests passed (test_universe_allocator + calibration + paper_trader_ranker)。

---

## Recent Change (Strategy Pool 前期调研 — 2026-06-17，纯研究无代码改动)

> **背景**: team-lead 委派，为 Strategy Pool 功能规划做代码结构前置调研。
> 梳理了策略从生成到退役的完整路径，供后续设计 direction_affinity + regime_awareness 扩展时参考。

**调研覆盖文件**：
- `aletheia/agents/dreaming_agent.py` — 夜间巩固 + 学习账本（非策略生成）
- `aletheia/storage/models.py:193` — `StrategyRecord`（核心数据模型）
- `aletheia/storage/repositories.py:432` — CRUD（insert/update/get/list_strategies）
- `aletheia/analytics/lifecycle_automation.py` — 自动暂停/退役（无 LLM）
- `aletheia/analytics/strategy_evidence.py` — 策略健康分类
- `aletheia/agents/paper_trader_ranker.py` — 策略证据对得分的影响
- `aletheia/universe/outcome_recorder.py` — Universe 前向收益追踪（非策略级）
- `aletheia/trading/strategy_lifecycle.py` — 状态机 + 晋升门（10 态，前向单向）
- `aletheia/strategy/performance_ledger.py` — 元评估层

**关键结论**：

1. **StrategyRecord 不含 direction_affinity / regime 字段**：`parameters`（JSON blob）内只有
   `entry_rule/exit_rule/timeframe/direction_bias/symbol/ruleset`。需扩展 `parameters` schema
   或加新列（走 `_ADDITIVE_COLUMNS` 迁移路径）。

2. **10 态前向状态机**（`aletheia/trading/strategy_lifecycle.py:27`）：
   `draft → research_note → strategy_hypothesis → paper_candidate →
   paper_running → paper_passed → live_candidate → live_limited → retired → deprecated`
   晋升门要求 ≥14d paper + ≥30 笔交易 + 正 expectancy。

3. **dreaming_agent 不生成策略**：负责夜间活动巩固 + 学习账本 + lesson 提取。
   策略生成入口在 strategy_designer agent（本轮未探索）。

4. **ranker 对策略的使用**：`backtest_killed` 是唯一硬阻断；其余通过 bonus/penalty 调分（+15/-10/-30）。
   `direction_bias` 从 `opportunity` 独立推断，与 `strategy.parameters.direction` 无强绑定。

5. **forward outcome 是 Universe 级，非策略级**：策略级 PnL 来自 positions 表的 `strategy_id` 外键。

**无代码改动**；研究记录已写入 DEVLOG。

---

## Last Updated (previous)

2026-06-17 (调研：开源算法交易框架；Scanner §16 修复 + 测试基础设施强化)

---

## Recent Change (开源算法交易框架调研 — 2026-06-17，纯研究无代码改动)

> **背景**: team-lead 委派基础研究任务，为 Aletheia 下一步架构（做空对称、策略发现、
> 信号流水线）决策提供参考。

**调研范围**: Jesse、Freqtrade/FreqAI、QuantConnect LEAN、Zipline-reloaded、Backtrader、
Nautilus Trader、Hummingbot、OctoBot、FinRL；Alpha Factory 概念；LLM 策略生成
（FinGPT、GPT-InvestAR）。

**关键结论（对 Aletheia 有参考价值）**:
- **做空对称性**: Nautilus Trader（OrderSide enum）、Jesse（`go_short()` 与 `go_long()` 对称）
  最完整；Freqtrade 做空是事后补丁（`can_short=True` + futures 限制）
- **最佳流水线架构**: LEAN 五层分离（Universe → Alpha → Portfolio Construction → Risk →
  Execution）是机构标准；Aletheia 当前管道吻合，但 ranker + paper_trader 混合承担了
  Portfolio Construction 职责——未来拆分有价值
- **ML 信号路径**: FreqAI 最完整（周期重训练 + 不确定性过滤 + 特征自动扩展）；LLM
  目前只作特征提取工具，无端到端 LLM 生成可部署策略的生产案例
- **Alpha Factory**: Aletheia 的 signal_emitter + universe calibration 走的是 WorldQuant
  Alpha Factory 路线（小 alpha 并行 + 组合），方向正确

**无代码改动**；研究记录已写入 DEVLOG。

---

## Recent Change (Scanner §16 修复 + 测试基础设施 — 2026-06-17, commits d8908e5..e053d5a)

> **背景**: §16 scanner 持续超时（82min+，10 次连续失败）。根因是 futures 集成引入了
> `"swap"` 作为 Binance fetchMarkets 的市场类型键，但 Binance 适配器白名单不认这个值，
> 导致 ExchangeError 静默杀死整个扫描 pipeline。同时修复了测试套件因 quality gate 引入导致
> 的 28+ 测试失败，并强化了测试基础设施（in-memory SQLite + Hypothesis 属性测试）。

**改动（5 个文件 + 1 个新测试文件）**:

1. `aletheia/universe/features.py` — `fetchMarkets` `["spot", "swap"]` → `["spot", "linear"]`；
   `_OHLCV_CONCURRENCY` 5→10；`asyncio.wait_for(timeout=8.0)` per-symbol
2. `aletheia/universe/registry.py` — `market_types.append("swap")` → `append("linear")`
3. `tests/conftest.py` — `_inmemory_db` autouse fixture；`real_db` marker；hypothesis profile
4. `tests/test_hypothesis_properties.py` — **新文件**：19 个属性测试（symbol converter、quality gate、content hash）
5. 多个测试文件 — 测试种子内容扩展 ≥80 字符；`commands.py` None guard；`test_openai_healthcheck` mock TCP；`test_xstocks_observer` typo 修复

**验证**: 1338 tests passed（+19 新增）；scanner `assets_passed=115` 完成；OHLCV ~7s→~3.5s

**Git 状态**: 本地 master 领先 origin/master 46 个提交；origin 有 2 个 docs 提交未合并。
建议接手后先 `git pull --rebase origin master` 解决 diverge。

---

## Recent Change (根治 scheduler deadlock + DB lock — 2026-06-16)

> **根因**:Python sqlite3 默认 `isolation_level=""` 在每条 INSERT/UPDATE/DELETE 前自动
> 发起 BEGIN。APScheduler daemon 线程被 `_run_sync_with_timeout` 超时放弃后，thread-local
> connection 永远持有 `in_transaction=True`→WAL writer slot 被永久锁住→所有后续写操作
> "database is locked"→market_observer 僵死、无新 opportunity。

**修复(7 commits, d11fcf5..187e201)**:
1. `scheduler.py`: 缩窄全局 RLock(只包前后 DB cleanup,不包 job 本体)
2. `scheduler.py`: daemon thread + hard timeout 防 asyncio.run teardown 挂死
3. `scheduler.py`: close_db() 在超时后清理 daemon-thread 连接
4. `knowledge/retrieval.py`: reindex_all() 改显式 BEGIN/COMMIT
5. `portfolio/exchange_sync.py`: _store() 用独立 autocommit 连接 + _run_async 55s timeout
6. **核心修复 `storage/sqlite.py`**: `isolation_level=None`(真 autocommit),消灭整类
   隐式事务泄漏 bug；DML 自动提交,不再需要 was_in_tx+commit 模式
7. `storage/repositories.py`: 移除 _insert/_update 中的 was_in_tx+commit(多余);
   `transaction()` context manager 保持显式 BEGIN/COMMIT 用于批量原子操作

**验证**: DB 75 秒连续可写(轮询 BEGIN IMMEDIATE 无 lock);market_observer 3 轮正常
产出(11:10/11:18/11:25 UTC);position_reconciler/post_trade_reviewer 正常循环。

---

## Recent Change (项目级 Code-Review 修复 — 2026-06-16, uncommitted)

> 全项目 code-review(契约对应 + 运行态正确性 + 潜在 bug)。15 条确认 findings:
> 7 条机械修复已先行落地,4 条设计级(A/B/C/D)在本轮完整实现。

**A — universe high-confidence 标签不可达**
- 根因:`signal_emitter._HIGH_CONF_THRESHOLD=70`,但 allocator 总分天花板 ≈68.5
  (opportunity 0.35×100 + tradability 0.25×100 + liquidity 0.15×90 − min cost 5.0),
  所以每个 deterministic opportunity 都被强制 "medium","high" 永不出现。
- 修复:阈值降到 60,保留有意义的 medium=[45,60) / high=[60,ceiling] 区分。

**B — forward outcome 只能给 BTC/ETH/SOL 定价**
- 根因:`settle_due` 只读存量 market snapshot,而 snapshot 仅覆盖静态观测的
  3 个标的;轮动 universe 成员全部 None → 标 `failed`,校准永远攒不到数据。
- 修复:`settle_due`/`_batch_fetch_prices` 改 async,用共享 CCXT provider 给全部
  symbol 取 live ticker(provider 关闭一次),snapshot 仅作兜底。

**C — calibration 胜率对 short 方向算反了**
- 根因:`calibration_stats` 把 `forward_return_pct > 0` 一律算赢,但 short 仓在
  价格下跌时才赢 → derivatives/mean_reversion 的 short 选择被系统性误判。
- 修复:`UniverseForwardOutcome` 加 `direction` 列(model + 迁移:CREATE TABLE
  与 `_ADDITIVE_COLUMNS` 双路,startup-only,幂等);`service.refresh` 用
  `_infer_direction` 写入方向;`register_pending` 落库;胜率 SQL 改方向感知
  (long/NULL: return>0 赢;short: return<0 赢),`positive_count`→`win_count`
  (语义诚实,short 赢在负收益),`routes/universe.py` 同步改名。

**D — 设置保存后刷新间隔不生效**
- 根因:APScheduler IntervalTrigger 添加后不可变,保存的 `refresh_interval_min`
  要等下次重启才生效。
- 修复:新增 `scheduler.reschedule_universe_refresh(interval_min)`(锁内
  `reschedule_job`,job 不存在/异常返回 False 不阻断保存);`settings.py` 在
  `refresh_interval_min` 变化时调用,返回 `rescheduled` 标志。

**Tests**: 新增 `tests/test_universe_calibration.py`(3 方向感知用例);改 signal_emitter
阈值用例。ABCD 受影响测试单独跑 36/36 全绿,且未新增 DB writer / scheduler job。

**顺带修复(同批)**:`test_startup_gate::test_empty_response_not_usable` 原失败——测试桩
`_FakeResp` 缺 `.raw`、与生产 `startup_gate.py:64`(`resp.raw` 兜底判定)漂移。给
`_FakeResp.__init__` 加 `raw=None` 参数对齐真实 `ModelResponse`,零生产代码改动
(详见 `docs/issues/BACKEND_ISSUES.md` §6.5,已标 ✅)。修复后 `test_startup_gate` 14/14 绿,
全量基线恢复全绿。

## Recent Change (Phase 0 节奏调整 + 文档统一 — 2026-06-16)

> **节奏调整**:取消专门隔离 72h 不 commit 的观察窗口,改为边开发边持续观察闭环健康。
> `code_drift` 自愈重启是预期行为;Phase 间不再硬性串行;Phase 6 Live 仍需显式授权。

- `docs/_ai/CURRENT_TASK.md`:重写 Phase 0 段(binary gates → continuous trends),Pi Agent 段压缩为摘要
- `docs/status/ROADMAP.md`:Phase 0 / Track B / 红线调整,去掉"Phase 0 门全绿"前置
- `docs/_ai/PROJECT_CONTEXT.md`:全面重写(777→1295 tests, 新增 operator/edge lab/universe 等里程碑)
- `docs/_ai/DEV_PROTOCOL.md` §4:删除"战略冻结"段,替换为"边开发边观察"
- `docs/_ai/AI_HANDOFF.md`:压缩 2447→<500 行(50+ Operator 增量条目合并为一段摘要)
- `docs/_ai/REPAIR_PLAN.md`:标记归档(全部 Wave 完成)

> **Model provider thinking-mode fix** (uncommitted):
> `openai_compatible.py:145` + `routes/model.py:259`: `max_tokens` 5/10→200,
> thinking-mode 模型把 budget 花在推理上产出空 text → 误判 unavailable。

## Recent Change (P1 FTS5 + Universe + UI — 2026-06-16, commits cb9692e..5f2433f)

- **FTS5 experience-loop fix** (cb9692e): startup `reindex_all()` backfill + doctor `fts.coverage` probe + 5 tests
- **Universe forward outcomes**: 15m/1h/4h/24h 收益追踪 + `GET /api/universe/outcomes`
- **Deterministic opportunity path** (a0c7cfa): `signal_emitter.py` — universe members → Opportunity (无 LLM); 32 tests
- **Test fixture refactor** (ed1015d, f113230): LLM-skip behavior alignment + startup_gate sleep skip (21min→9min)
- **Universe snapshot TTL** (d0dc48d, 8f69a44, 5b61fe5): 24h TTL + stale markers + Settings TTL control
- **Model provider**: thinking-mode content extraction + inference probe fallback (14321f1, 540c0ab)
- **Settings UI**: real values instead of placeholder hints (5f2433f)
- Tests: 1295/1295 pass

## Recent Change (决策层重构 — 2026-06-15, commits ef3e765, 73703ef)

- **RuleSet-driven trading**: `ruleset_scanner.py` 从 OHLCV 直接生成 entry/exit 信号
- **PI Agent conviction sizing**: structured LLM 评估 → adversarial debate → fractional Kelly
- **paper_trader v3**: Path A (RuleSet multi-strategy) + Path B (opportunity fallback)
- **Strategy lifecycle automation**: auto-promote / auto-pause

## Recent Change (Edge Lab — 2026-06-14, commits 9fe5c5f..ab4a522)

- Shadow trades (Top-5 反事实), configurable slippage (5bps), BTC benchmark, regime tags
- Family scorecard API (`GET /api/analytics/family-scorecard`)
- 11 tests; SQL fix (`scanner_family` JOIN path)

## Recent Change (全局代理配置 — 2026-06-14, commit 28eb357)

- `aletheia/net.py`: httpx/aiohttp 统一代理工厂
- 13 文件 30+ 处调用点替换; Settings UI NetworkCard; doctor `network.proxy` probe

## Recent Change (9域数据收集层 — 2026-06-14, commit 84a66fd)

- microstructure/derivatives/macro collectors + normalizer + context_builder
- 5 DB tables, scheduler 2min tick, 16 tests
- market_observer 自动追加 enriched context block

---

## Operator Layer Track B — 累积实现摘要 (2026-06-11, 50+ 增量)

> 以下是 2026-06-11 期间在 `operator-shell/` + `aletheia/agent_shell/` + `aletheia/server/routes/operator.py`
> 上完成的大批量增量工作的压缩摘要。逐条原始记录已归档;详见 git log 该日期范围。

**Python truth layer**:
- `/api/operator/internal/context/query` (分级安全 context)
- `/api/operator/internal/commands/execute` (typed command loopback)
- `/api/operator/internal/decisions` + `/repair-decisions` + `/command-intents`
- `/api/operator/extensions` (inspect/enable)
- `/api/risk/summary` (boundary change detection)
- `/api/strategies/summary` (promotion/evaluation detection)
- `/api/reviews/summary` + `/api/dreaming/summary`
- Session-kind child sessions (diagnostic/dreaming/strategy/review/command)
- Command audit persistence + inspectable working-memory files (JSONL/Markdown)

**Node Operator Shell**:
- `OperatorLoop` with 3-tier cadence (10s/30s/5min)
- `RecommendationEngine`: 15+ recommendation types
- `TaskRuntime`: priority queue, in-flight task re-entry, permission-aware materialization
- `RepairEngine`: proposal-only decisions (9 specialist paths)
- `SessionRuntime`: budgeted recovery folding, active branch persistence, parent trace inheritance
- Heartbeat mode state machine (Observing/Trading/Reviewing/Dreaming/Repairing/Degraded)
- Extension sandbox: draft → test → propose → enable → slash dispatch

**Command Center frontend**:
- SSE-first event feed
- Authoritative command preview (Ready/Blocked/Query/Forbidden)
- Structured trace/result cards
- Context inspector
- Session tree with nested branch create/select
- Cross-session command search
- Enabled extensions panel

**Tests**: 154 Python operator tests + 80 Node tests; builds pass.

---

## Historical Changes (2026-06-13 and earlier)

### 2026-06-13 — Gateway 静态文件启动可靠性 (db2423f)
- preflight.py 启动预检 + lifespan mount + doctor webui.static 诊断

### 2026-06-13 — DX 收口 (18 commits)
- Single-port :8787 dev (vite build --watch + uvicorn --reload)
- LLM startup gate 3次重试 + Windows toast
- Native Anthropic + OpenAI provider (SSE 流式)
- symbol_list 双模式过滤
- Clustr100 社区知识库

### 2026-06-13 — Persistence/Sync/Recovery (1075c8e)
- 18 新模块: EventStore, StateStore, ArtifactStore, GatewayFSM, Reconciliation, Recovery, Checkpoints, Export/Import, Backup/Restore, Learning/Policies
- 15 张 `ps_*` 前缀表(additive overlay)

### 2026-06-12 — 账户级仓位发现 + 纳管 (P1-P2)
- position_reconciler.classify_positions() + adopt_position()
- source 列(native/adopted) + 诚实合成链
- trust §1.1 修订

### 2026-06-10 — CLI 分层 + 运营命令软弃用
- bootstrap 内核(gateway/doctor/version)保留
- 运营命令(permission/model/risk/strategy)指向 Web UI

### 2026-06-08 — 多轮基础设施修复
- Gateway ghost-socket 根治 (a90ea84)
- Markets K线动态加载 (scroll-back + live tail)
- Deep Research → ROADMAP v1
- Market context layer (multi-TF indicators → LLM)
- Exchange account sync
- Experience loop (FTS5 retrieval → agent prompts)

### 2026-06-07 — WebUI 结构性重设计
- 14→10 pages (5-phase IA)
- CausalChain inspector
- Strategy↔trade causal chain fix (4 places)
- §10 re-audit (8 false-positive fixes)

### 2026-06-06 — 项目初始化
- CLAUDE.md + docs/_ai/ 全套
- Backend 80+ fixes + Frontend 24 fixes (§1-§3)

---

## Commands to Run

```bash
uv sync
uv run pytest -q                              # 1295+ tests
uv run aletheia gateway start --no-browser    # 启动闭环
uv run aletheia doctor                        # 取真实事实
```

## Warnings

- Live trading is disabled by default. Never enable without explicit user confirmation.
- Trust boundary modules (risk/, execution/, auth.py, secrets.py) require user authorization.
- Frontend must be rebuilt after source changes: `cd frontend && npm run build`
- After backend .py changes: `uv run aletheia gateway restart --no-browser`
