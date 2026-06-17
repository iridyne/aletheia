# AI Handoff

> **本文件是 append-only 变更史**(记录"做了什么改动 + 根因",机器无关)。
> 其中任何"当时机器状态"(gateway 在跑 / 观察期第 N 小时 / 凭证已存 / demo 单已落)
> 都是**写入时刻的快照,不代表当前**。要知道当前真实状态,运行 **`aletheia doctor`**。
> 双机意图/事实分离的金标准见 `docs/_ai/DEV_PROTOCOL.md`。

## Last Updated

2026-06-17 (US Equity 集成 Phase 1-4 + Settings UI 全部完成)

---

## Recent Change (US Equity 集成 Phase 1-4 + Settings UI — 2026-06-17)

> **背景**:xStocks（Bitget ON-series）和 yfinance 两条路径孤立，symbol 格式不兼容，
> knowledge 循环断路，equity 机会被 ranker 硬杀。

**改动(10 个文件，1 个新文件)**:

1. `aletheia/data/symbols.py` — 新增 `_XSTOCK_BASE_TO_TICKER` 映射表 + `xstock_to_ticker()` / `ticker_to_xstock()` 工具函数（10 个已知 ON-series token）
2. `aletheia/config/schema.py` — `MarketDataConfig` 加 `observed_equities: list[str]`（主入口）+ `observed_stocks`（向后兼容）
3. `aletheia/storage/models.py` — `Opportunity` 加 `asset_subclass: str = ""` + `canonical_ticker: str = ""`
4. `aletheia/storage/migrations.py` — `_ADDITIVE_COLUMNS` 追加两条 equity 字段（`TEXT NOT NULL DEFAULT ''`）
5. `aletheia/agents/equity_observer.py` — **新文件**：统一 equity agent（yfinance 日线 + Bitget xStock 实时价 + deviation_pct 监控 + equity 专属 LLM prompt）；`execution_eligible=False`；deviation > 2% premium_alert；deviation > 5% data_anomaly（排除 LLM）
6. `aletheia/agent_runtime/scheduler.py` — `equity_observer` cron job（替换 `stock_observer`，相同 Mon–Fri 14:30–21:00 UTC 窗口）
7. `aletheia/knowledge/router.py` — `AgentRole` 加 `equity_observer`；`_ITEM_TYPES_BY_ROLE` 加 equity role（research_note / trade_case_study / post_trade_review）
8. `aletheia/agents/market_observer.py` — 移除 xStocks 分支（防止与 equity_observer 重复写入）
9. `aletheia/server/routes/settings.py` — 新增 `POST /settings/equities` endpoint（`EquitiesConfigUpdate`，normalize+去重保存到 `market_data.observed_equities`）
10. `frontend/src/api/client.ts` — 新增 `updateEquities(tickers)` API 方法
11. `frontend/src/pages/Settings.tsx` — 新增 `EquitiesCard` 组件（chip-based 增删，save mutation）+ 在 RiskTab 加 "Observed US Equities" Section；前端静态文件已重新构建
12. `aletheia/agents/paper_trader_ranker.py` — **Phase 4 shadow track**：equity 机会改为计分（direction + confidence_score）但标记 `skip_reason="equity_shadow_track"`，不再 `return None` 硬排除；对应 2 个测试用例同步更新

**关键设计决策**:
- `deviation_pct` = tracking error 监控，**不是套利信号**（所有执行从 Bitget 出发，xStocks 是唯一执行渠道）
- equity 不进 Universe 主流水线（crypto 特征对 equity 无意义）
- equity shadow track 会出现在 paper_trader 的 skip outputs 里，方便观察 equity 信号质量，但永远不会被选为执行候选
- `stock_observer` 留在 registry（向后兼容），但不再由 scheduler 调度

**验证**: 前端 tsc+vite build 无错误；25 项相关测试全绿（paper_trader_ranker 25 + xstocks_observer 4）

---

## Last Updated (previous)

2026-06-16 (根治 scheduler deadlock / DB lock — isolation_level=None autocommit)

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
