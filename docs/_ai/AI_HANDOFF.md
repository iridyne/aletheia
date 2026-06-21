# AI Handoff

> **本文件是 append-only 变更史**(记录"做了什么改动 + 根因",机器无关)。
> 其中任何"当时机器状态"(gateway 在跑 / 观察期第 N 小时 / 凭证已存 / demo 单已落)
> 都是**写入时刻的快照,不代表当前**。要知道当前真实状态,运行 **`aletheia doctor`**。
> 双机意图/事实分离的金标准见 `docs/_ai/DEV_PROTOCOL.md`。

## Last Updated

2026-06-21 (PI Agent 信号增强 + Liquidation 采集修复 + Operator ICT 策略研究)

---

## Recent Change (Exchange 统一选择 — 2026-06-20)

> **目标**：消除三轴分裂（market_data.ccxt_exchange / exchange.paper_execution_mode / exchange.name 各指不同交易所）。
> 新增 `POST /api/settings/primary-exchange`，一次调用原子同步全部 4 个字段。

**根因**：系统原本允许用户在不同地方单独配置数据源和执行交易所，导致产生三轴不一致的状态（如 name=bitget，ccxt=okx，paper_mode=okx_demo）。

**修改文件**：
- `aletheia/server/routes/settings.py`：新增 `_EXCHANGE_UNIFICATION_MAP`（okx/bitget/binance 三个映射）+ `PrimaryExchangeRequest` + `POST /primary-exchange` endpoint；paper_internal 选项只改 paper_execution_mode，不动 name/ccxt；直接写 `cfg.risk.allowed_exchange`（绕过 `_RISK_IMMUTABLE`，这是设计）
- `aletheia/diagnostics/config_consistency.py`：扩展 `_DEMO_CRED_MAP` 覆盖 okx_demo + binance_testnet；新增 `_check_exchange_consistency` 检查 name≠ccxt 时输出 info warning
- `frontend/src/api/client.ts`：新增 `setPrimaryExchange()` API 方法
- `frontend/src/pages/Settings.tsx`：新增 `PrimaryExchangeCard`（4-tile 选择器，放在 ExchangeConnectionCard 前）；ExchangeModeRow hint 改为动态；Trading Mode → "Trading Mode — Advanced"
- `frontend/src/index.css`：新增 `.exchange-tiles` / `.exchange-tile` / `.exchange-tile-active` 等 CSS

**验证**：
- `npm run build` 0 error（777 modules）
- `pytest -k "config or settings"` 79/79 pass
- API smoke test：POST `{"exchange_id":"okx"}` → 4 字段全部变为 okx；切回 bitget → 全部变为 bitget

**xStocks 例外**：3 处 `CCXTProvider(exchange="bitget")` 硬编码（ON-series 代币仅在 Bitget 上市），未动。

---

## Recent Change (P1 竞品 Gap 三功能 — 2026-06-20, c1e67a5)

> **目标**：竞品差距分析 P1 — 加速策略迭代：(A) 本地历史数据避免每次回测拉 API；
> (B) Optuna Bayesian 搜索替换暴力 param_sweep；(C) 事件驱动 30s 价格/量监测 + 触发。

**Feature A: 历史数据管理**
- `aletheia/data/history_downloader.py` (新建)：`download_symbol_history()` 增量下载 + `check_coverage()`；分页 500 bars，0.35s 限速，检测 `get_ohlcv_newest_ts()` 决定增量起点
- `aletheia/storage/repositories.py`：`upsert_ohlcv_candles` 新增 `window=None` 动态裁剪；新增 `get_ohlcv_coverage()` + `get_ohlcv_newest_ts()`
- `aletheia/strategy/backtest.py`：`load_bars_from_db(limit=None)` 读 config `max_bars_per_pair`
- `aletheia/agent_runtime/scheduler.py`：`_run_ohlcv_nightly_backfill()` + cron 注册（CST 03:30）+ price_feed timeout 25s→45s
- `aletheia/cli/data_cmd.py` (新建)：`aletheia data download SYMBOL` + `aletheia data coverage SYMBOL`
- `aletheia/main.py`：挂载 `data_app` typer

**Feature B: Optuna 智能参数优化**
- `pyproject.toml`：新增 `optuna>=3.6`
- `aletheia/config/schema.py`：`StrategyFactoryConfig` 新增 `sweep_use_optuna=False`、`sweep_n_trials=60`、`sweep_optuna_timeout_sec=120.0`、`sweep_top_k=10`
- `aletheia/strategy/param_sweep.py`：`_run_optuna_sweep()` 用 `TPESampler(seed=42)` + `MedianPruner`；`run_parameter_sweep()` 按 flag 派发；`_run_brute_force_sweep()` 提取原逻辑
- Settings 前端 + route：4 个新字段复用 `POST /strategy_factory`

**Feature C: 事件驱动触发**
- `aletheia/config/schema.py`：新增 `EventTriggerConfig`（enabled / price_move_pct_threshold / volume_surge_multiplier 等）
- `aletheia/data/price_feed.py`：缓存结构扩展 `prev_price` + `volume_24h`；tick-gating（universe 每 3 tick/90s）；`_check_event_triggers()` 检测价格/量突破；`_notify()` 封装
- 新增 `POST /settings/event_triggers` route

**测试**：17 new tests 全绿（test_history_downloader.py × 5、test_param_sweep_optuna.py × 3、test_event_triggers.py × 9）

---

---

## Recent Change (Exchange-native TPSL bracket — 2026-06-20, f9e8dfb)

> **目标**：竞品 Gap 分析后，实施最高 ROI 改进：在 Bitget 交易所层面挂 SL + TP trigger
> orders，让止损/止盈在价格触及时毫秒级响应，不再依赖 scheduler 5-min 轮询。

**设计决策**：
- 两个独立 trigger（`pos_loss` + `pos_profit`）而非 combined `profit_loss` planType，
  因为 Bitget OCO 联动语义文档不明确，独立挂单行为确定。
- 本地 position_manager 轮询保留为 fallback（belt-and-suspenders）。
- 失败不阻塞开仓：TPSL 挂单失败仅 log warning，position 仍正常创建。
- 功能开关 `exchange_tpsl_enabled: bool = False`，需用户在 Settings 前端显式开启。
- Exchange-triggered close 检测：`_sync_exchange_closed()` 在每次 evaluate 周期开头
  调用 `get_positions()` 对比，发现本地持仓已不在交易所 → 以 `exchange_bracket_triggered` 关仓。

**文件变动**：
- `aletheia/exchanges/bitget/client.py`：新增 place_tpsl_order / cancel_plan_order / get_pending_plan_orders
- `aletheia/execution/bitget_broker.py`：_try_place_tpsl / _try_cancel_tpsl / replace_exchange_stop；
  execute_live_order 新增 stop_price + take_profit_price 参数；平仓前取消 bracket
- `aletheia/execution/gateway.py`：demo + live bitget 分支均计算 TP 并传 broker
- `aletheia/portfolio/position_manager.py`：_sync_exchange_closed；trailing stop 联动 replace_exchange_stop；
  close_position 改为 top-level import（修复 patch 路径以支持测试）
- `aletheia/storage/migrations.py + models.py`：exchange_tpsl_order_id TEXT 列（additive）
- `aletheia/config/schema.py`：exchange_tpsl_enabled: bool = False
- `aletheia/server/routes/settings.py`：TradingConfigUpdate 新增字段
- `frontend/src/pages/Settings.tsx`：Exchange-native TPSL Toggle UI
- `tests/test_tpsl.py`：11 tests，全部通过

**也修复了 4 个 pre-existing test failures**（commit e069d56）：
- Universe.tsx Scanner Calibration 表：6 个 inline style → CSS class
- Settings.tsx UniverseConfigCard：去除多余 onDirty prop
- index.css：新增 button:active { transform: none; }
- docs/_ai/PROJECT_CONTEXT.md：补充 :8787 端口

---

## Recent Change (Trade Analytics 面板 + Batch 2 bug 修复 — 2026-06-19, 0b3b1a6)

> **目标**：在 Trades & Positions 页面增加 Analytics sub-tab，可视化展示
> 交易模式差异（RuleSet vs Opportunity）、scanner family 表现、持仓时间分布。

**Batch 2 Code-Review 修复（4 个 bugs，优先于 analytics 实施）**：
- **CRITICAL** `aletheia/storage/migrations.py`：`_ADDITIVE_COLUMNS` 补入 `("atr_pct", "universe_members", "REAL")`；
  缺失导致现有 DB 升级后 ATR 仓位定规模链路静默失败（`_atr_adjusted_notional()` 读到 None）。
- **HIGH** `aletheia/agents/strategy_designer.py`：`_covered` 集合改为仅含 active 状态策略；
  原代码把 retired/failed 策略也排除，导致 ranker 优先给已失败币种生成新策略。
  同时用 `to_bitget()` 标准化符号，消除 `BTC/USDT` vs `BTCUSDT` 比较总为 False 的 bug。
- **MEDIUM** symbol 格式：`strategy_designer.py` 原用 `.replace("/","")` 手动转换，
  违反 CLAUDE.md canonical symbol 规则，改为 `to_bitget()` 统一入口。
- **INFO** `aletheia/universe/allocator.py`：`get_family_multipliers()` 文档字符串更正
  （"momentum/derivatives → 4h" → 实际 "momentum → 4h, derivatives/mean_reversion/volatility → 1h"）。

**Trade Analytics 面板**：
- `aletheia/analytics/trade_stats.py`（新建）：`build_trade_stats()` 聚合已平仓
  持仓，按 source type / family / hold-time 分桶。RuleSet 判断用
  `json_extract(s.parameters, '$.ruleset') IS NOT NULL`（`strategy_evidence.py` 同标准）。
  family 数据复用 `compute_family_scorecard()`。
- `aletheia/server/routes/analytics.py`：追加 `GET /api/analytics/trade-stats`。
- `frontend/src/charts/TradeAnalyticsPanel.tsx`（新建）：4 区块 CSS bar chart 面板，
  `useQuery(staleTime=60s)`，零新依赖。
- `frontend/src/api/client.ts`：追加 `getTradeStats()` 及 TypeScript 接口。
- `frontend/src/pages/TradesPositions.tsx`：用 `<Tabs>` 拆出 Blotter / Analytics 两个 tab。
- `frontend/src/index.css`：追加 `.analytics-*` CSS 类。
- `aletheia/dashboard_static/`：重新构建（203 modules）。

**验证**：`GET /api/analytics/trade-stats` 返回正确 JSON（59 trades，by_source/by_family/
hold_distribution/totals 字段完整）；前端构建 0 error；gateway 已重启。

---

## Last Updated (前次)

2026-06-17 (Self-Evolution Tier 1 实施完毕；worktree 待合入 master)

---

## Recent Change (Self-Evolution Tier 1 — 2026-06-17, worktree-self-evolution-tier1)

> **目标**：关闭 `universe_forward_outcomes` → knowledge base 反馈回路。
> 此前坏结果有数据却从未反哺规则；Tier 1 是零 LLM 成本的最低风险起点。

**核心逻辑**（`aletheia/universe/outcome_recorder.py`）：
- `settle_due()` 结算后调用 `_maybe_generate_guard_candidates(settled_rows)`
- 只处理 `horizon=4h`；方向感知阈值：long `return < -3%`，short `return > +3%`
- 7 天内同 `symbol+scanner_family` 去重（`repositories.guard_candidate_exists_recent()`）
- 生成 `medium_confidence` guard_candidate（不进 FTS 检索，需人工确认升级）
- 失败只 `logger.warning`，不影响 `settle_due()` 返回值

**修改文件**：
- `aletheia/universe/outcome_recorder.py`：`_GUARD_HORIZON` / `_LOSS_THRESHOLD_*` 常量 + `_maybe_generate_guard_candidates()` + `_build_guard_candidate_content()`
- `aletheia/storage/repositories.py`：`guard_candidate_exists_recent()` dedup 查询
- `tests/test_self_evolution_tier1.py`（新建）：10 个单元测试，全通过

**文档整理**（第二个提交）：
- `docs/_ai/SELF_EVOLUTION_DESIGN.md` → `docs/plans/self_evolution_plan.md`（移入 plans/，加明确日期）
- `docs/status/ROADMAP.md`：新增 Self-Evolution 横切轨道（Tier 1/2/3 状态表）
- `docs/_ai/CURRENT_TASK.md`：待办第 0 项写入 Tier 2（2026-07-01）、Tier 3（2026-08-17）

**验证**：用 master `.venv` 跑全量 suite，**1311 passed, 0 failed**。

**待操作**：worktree 分支 `worktree-self-evolution-tier1` 尚未合入 master，
因 master 有未提交的 in-progress 工作（equity observer、futures integration 等）。
合并命令：`git merge worktree-self-evolution-tier1 --no-ff`（处理完 master 未提交变更后执行）。

---

## Last Updated (前次)

2026-06-16 (项目级 code-review:契约漂移 + 运行态正确性,15 findings → 修复)

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

### 2026-06-21 — PI Agent 信号增强 + Liquidation 修复 + Operator ICT 研究 (ce0360d)
- Task A: UniverseFeatureSnapshot 新增 4 列 (taker_buy_ratio/vol_ratio/relative_strength_24h/oi_price_divergence)
  features.py 写入; trading_context.py Layer 1 读取+crowding_signal; trading_decision.py prompt 增强+_SIGNAL_RULES
- Task B: derivatives.py 日志提升 debug→warning; 时间窗口 1h→4h; "0 in window" 诊断; scheduler persist 计数日志
- Task C: operator_loop.py trigger_strategy_research action (每日限频1次); research_worker.py _ICT_METHODOLOGY_SYSTEM +
  mode="methodology" 分支; trading_context.py Layer 3 注入 serenity_opportunities; trading_decision.py 渲染 Research-sourced 小节

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
