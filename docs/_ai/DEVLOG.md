# Development Log

## 2026-06-17 — Self-Evolution Tier 1 实施(worktree-self-evolution-tier1)

> 研究 → 三层设计 → Tier 1 全量实现 + 文档整理，一次会话完成。

### 背景
Universe forward outcomes（4h/15m/1h/24h 收益追踪）数据已有，但从未反哺 scanner 规则。
Tier 1 目标：无 LLM 成本地关闭这个反馈回路，将负面 4h outcome 自动转化为 guard_candidate。

### 实现内容
- `aletheia/universe/outcome_recorder.py`：`settle_due()` 后扫描 4h settled 行，方向感知
  阈值判断（long < -3%，short > +3%），调用 `create_guard_candidate()`，7 天内去重
- `aletheia/storage/repositories.py`：`guard_candidate_exists_recent(symbol, family, days=7)`
- `tests/test_self_evolution_tier1.py`（新建）：10 个单元测试

### 文档
- `docs/plans/self_evolution_plan.md`（从 `docs/_ai/SELF_EVOLUTION_DESIGN.md` git mv）
- `docs/status/ROADMAP.md`：Self-Evolution 横切轨道
- `docs/_ai/CURRENT_TASK.md`：Tier 2（2026-07-01）、Tier 3（2026-08-17）日期

### 验证
1311 passed, 0 failed（master `.venv`，全量 suite）

### 状态
两个提交在 `worktree-self-evolution-tier1`（`1a75dc7`, `89df0be`），待合入 master。
Master 当前有未提交的 equity observer / futures integration 工作，需先处理再 merge。

---

## 2026-06-16 — 项目级 Code-Review 修复(A/B/C/D 设计级,uncommitted)

> 来源:`/code-review max` 全项目审查(契约对应 + 运行态正确性 + 潜在 bug)。
> 15 条确认 findings,7 条机械修复先行,4 条设计级本轮完整实现。

### 实现内容
- **A** `signal_emitter.py`: `_HIGH_CONF_THRESHOLD` 70→60 —— allocator 总分天花板 ≈68.5,
  原阈值令 "high" 永不可达。
- **B** `outcome_recorder.py`: `settle_due`/`_batch_fetch_prices` 改 async,共享 CCXT provider
  给全部 universe symbol 取 live ticker,snapshot 兜底 —— 此前只有静态 3 标的能定价,
  轮动成员全标 `failed`,校准攒不到数据。
- **C** 方向感知校准:`UniverseForwardOutcome` 加 `direction` 列(`models.py` + `migrations.py`
  CREATE TABLE 与 `_ADDITIVE_COLUMNS` 双路);`service.refresh` 经 `_infer_direction` 写方向,
  `register_pending` 落库;`repositories.calibration_stats` 胜率 SQL 改方向感知
  (`positive_count`→`win_count`,short 赢在负收益),`routes/universe.py` 同步。
- **D** `scheduler.py`: 新增 `reschedule_universe_refresh()`(锁内 `reschedule_job`);
  `routes/settings.py` 在 `refresh_interval_min` 变化时调用,使保存即时生效(原须重启)。

### 验证
- `tests/test_universe_calibration.py`(NEW,3 方向用例) + signal_emitter 阈值用例
- ABCD 受影响测试单独跑 36/36 全绿,ABCD 未新增 DB writer / scheduler job
- 顺带修 `test_startup_gate::test_empty_response_not_usable`(测试桩 `_FakeResp` 缺 `.raw`、与
  `startup_gate.py:64` 漂移;加 `raw=None` 对齐真实 `ModelResponse`,零生产改动;BACKEND_ISSUES §6.5 ✅),
  修复后全量基线恢复全绿
- 所有改动均非 trust-boundary 文件(risk/execution/auth/secrets/live 未触碰)

---

## 2026-06-14 — 全局代理配置实现(commit 28eb357)

> 根因:Windows Task Scheduler 启动的 watchdog 进程没有 HTTPS_PROXY env var → CCXT/Binance
> 连接超时 → market_data 写入失败 → market data 永远 stale。

### 实现内容
- `aletheia/net.py`(NEW): `httpx_client()` / `aiohttp_session()` / `apply_proxy_env()` 三个工厂函数
- `aletheia/config/schema.py`: 新增 `NetworkConfig(proxy_url, no_proxy)`
- `aletheia/server/app.py`: lifespan startup 调 `apply_proxy_env()`
- 13 个文件 30+ 处 httpx/aiohttp 调用点统一替换为 net.py 工厂
  (最关键:ccxt_provider.py 的 aiohttp.ClientSession → aiohttp_session())
- `aletheia/server/routes/settings.py`: `POST /api/settings/network` + `/test` 端点
- `frontend/src/pages/Settings.tsx`: NetworkCard 组件 (Infrastructure tab)
- `aletheia/diagnostics/doctor.py`: `_probe_network()` 诊断维度

### 验证
- `npm run build` 通过(187 modules, 803 kB)
- `uv run pytest -q` 通过(同预存失败数,无新增失败)
- 用户可通过 Settings → Infrastructure → Network Proxy 配置代理 URL 并点 Test 验证连通性

---

## 2026-06-14 — Edge Lab 全量落地(commits 9fe5c5f / 8532280 / c90d2eb / ab2ba5c / ab4a522)

> 来源:Clustr100 社区交易群知识提炼 → 用户批准 implementation plan → 两个并发 session 分工落地。

### 实现内容
- **P0**: `Opportunity` 新增 `valid_until / entry_zone / stop_loss_hint / tp_hint`(schema + migration)
- **P0.5**: `paper_broker` 可配置滑点(`slippage_bps=5bps`, 多头高填 / 空头低填) + `execution_fee_pct` 去硬编码
- **P1**: `ShadowTrade` 模型 + `shadow_trades` 表 + `persist_shadow_batch()` + `paper_trader` 每 tick 入库 Top-5
- **P1.5**: `Position` 新增 `btc_entry_price / btc_exit_price`；开仓时从 latest_snapshot 抓 BTC 价写入；平仓时 pnl.py 写 exit
- **P2**: `Position` 新增 `regime_tag`；开仓时从 `latest_macro_snapshot()` 读 `risk_regime`；risk_off 惩罚 -20→-35
- **P2.5**: `compute_family_scorecard()` + `GET /api/analytics/family-scorecard`
- **tests**: `test_edge_lab.py`(11 个新测试全绿) + 回归修复 2 处精确断言(`pytest.approx`)

### 关键修复(本 session 负责)
- `family_scorecard.py` SQL bug：`positions` 无 `scanner_family`，原查询空返回。改为 LEFT JOIN `trade_proposals→opportunities`。
- `models.py` 重复 `ShadowTrade` class：并发 session 各写一份，移除重复定义。
- `test_paper_broker_stops.py` + `test_risk_engine.py`：slippage 导致填价偏移，精确断言失败。改 `pytest.approx(rel=0.01)`。

### 测试状态
`uv run pytest -q`：仅 3 个预存失败留存(test_ac12_risk_blocks, test_self_heal x2)，均与本批次无关。1170+ 通过。

## 2026-06-13 — 执行路径语义诚实文档化(纯文档,不动代码)

> 用户要求学习吸收外部 Agentic Trading 框架经验。核实后发现外部框架研究已做完(三份参考文档齐备),
> 真正净增量是通读当前代码、把"LLM 在决策回路精确位置"的一手事实钉死。用户明确:补文档/认知,不改代码。

### 核实结论(一手,2026-06-13 通读代码)
- **下单机制确定性**:`paper_trader.py:124` 固定 notional/价、`:126-130` 固定止损%、Risk Engine 硬限。
- **但交易方向 = LLM**:`market_observer.py:125-152` LLM 输出 `direction_bias` → `paper_trader.py:104-108` 直接照做。
- **strategy rule 悬空**:`strategy_designer.py:102-113` 产出 entry_rule/exit_rule 文本存 `parameters` JSON,
  全仓无 DSL parser,`paper_trader.py:136-149` 从不读 rule——策略层对交易零影响。
- **验证仅 paper 一层**:`strategy_lifecycle.py:184-273` gates 严格但基于真实 paper 统计,无 backtest/walk-forward/PBO。
- **audit 无 hash 链**:`jsonl.py:25-69` append-only 但每条独立,无 parent_hash。

### Added
- `docs/EXECUTION_PATH_HONESTY.md`:三类拆解(确定性/LLM/悬空,file:line)+ 原始设计 v1 差距对照 +
  Prism"反向"对照 + 验证层/审计链/经验闭环现状 + 三条诚实化路径(命名/验证 gate/真 DSL,仅记录)。

### Changed
- `docs/north_star_references.md`:更正 §0/§2/§6——"market_observer 不读历史"已过时(现注入 memory block);
  经验闭环从"只写不读"更正为"软闭环已通,缺参数化反馈";交叉引用新文档。
- `docs/BACKEND_ISSUES.md`:新增 §4(2026-06-13 结构性发现,未修复待授权)——strategy rule 悬空、
  验证层仅 paper、audit 无 hash 链;header/阅读顺序标注 §4。

### 不做(留痕)
- 不改任何代码;不执行 openalice_prism 蓝图 P0/P1/P2(待授权);不引入 DSL 引擎/回测框架(红线)。

## 2026-06-13 — Gateway 静态文件启动可靠性(commit db2423f)

> 用户访问 WebUI 得到 JSON 404。根因:后台启动时 Vite watch 与 uvicorn 竞态——uvicorn 模块导入时
> 检查 `dashboard_static/index.html` 并 mount,但 Vite 首次构建未完成,文件缺失 → mount 跳过 → 404。

### Changed
- `aletheia/_gateway_runner.py`:禁用内嵌 Vite watch(后台启动依赖预构建静态文件;开发用 dev.ps1)。
- `aletheia/server/app.py`:静态 mount 从模块顶层移到 lifespan startup,新增 `_mount_static_dashboard()`。
- `aletheia/gateway/lifecycle.py`:`do_start` 集成 preflight(fail-fast)+ `_verify_webui_accessible` 启动后验证。
- `aletheia/diagnostics/doctor.py`:新增 `webui.static` 诊断维度。
- `CLAUDE.md`:补 WebUI 静态文件构建规则。

### Added
- `aletheia/gateway/preflight.py`:启动前预检(静态文件/数据库/配置),失败给修复建议并拒绝启动。

## 2026-06-13 — 外部框架综述参考分析(纯文档,不动代码)

> 用户提供外部《Agent Framework Research Report 2025–2026》并要求结合项目更新 roadmap。
> 判断:报告是验证镜非新需求源,其重型栈处方命中红线 → 按参考文件先例只做文档分析。

### Added
- `docs/REFERENCE_agent_framework_research_2025_2026.md`:报告摘要 + 收敛最佳实践 vs Aletheia
  逐条核对 + §7 重型栈(Postgres/向量库/Redis/LangGraph)逐条拒绝附红线 + 净增量 + 治理红线。

### Changed
- `docs/ROADMAP.md`:Sources 引该报告(validation only);Common Failure Modes 补 Lookahead bias +
  Execution/slippage mismatch 两行;File Relationships 加新参考文件。
- `docs/north_star_references.md` §5 末加外部综述独立佐证指针(Hermes ≡ §4、重型栈已拒绝)。

### 不做(留痕)
- 不引入报告推荐的任何依赖/框架(Postgres/vector/Redis/NATS/LangGraph 均违反红线)。
- 不新增 phase/能力,不改 Phase 0 优先级,不触 trust 边界。

## 2026-06-13 — DX 收口 + 能力批次(18 提交)+ 机器交接

> 本日批次紧跟 `1075c8e`(Persistence/Sync/Recovery 子系统,已在 origin/master)。会话结束时
> 在本机停机、准备切换到另一台开发机前推送。逐条根因见 `docs/AI_HANDOFF.md` 同日条目。

### Changed — Dev 体验收口到 single-port :8787
- dev.ps1 不再起独立 Vite dev server(:5173);gateway :8787 同时服务 API + 静态资源。
  `vite build --watch` + backend `uvicorn --reload`(+ `WATCHFILES_FORCE_POLLING` Windows 兼容)
  接管热重载;SSE 推 `frontend-built` 事件,`dev-reload.ts` 自动刷新浏览器。
- 所有 dev-reload 行为门控 `ALETHEIA_DEV_RELOAD=1` + `live_disabled`,生产恢复 supervisor 崩溃保护。
  sentinel 路径统一为 `RELOAD_SENTINEL` 常量。新增 `scripts/watch_backend.py`、`/api/version`。
- "Restart" → "Cold Restart"(明确冷启 vs 热重载)。状态浮层从扁平 table 改为四区 flex 布局。

### Added
- LLM startup gate 重试(默认 3 次,可配 `health.startup_gate_max_retries` /
  `startup_gate_retry_delay_sec`)+ 暂停时 Windows toast,治 Databricks 端点偶发误暂停。
- 原生 Anthropic + OpenAI provider(SSE 流式 + 富元数据 + `llm_stream()` + Settings UI),36 单测。
  本项目默认 LLM 仍是 Databricks AI-Gateway,native 为新增能力非默认切换。
- Universe:`quote_volume_24h` / `spread_bps` 列、enriched detail drawer(features + linked opportunities)。
- `docs/plans/`(5 篇 implementation plan / 设计报告)+ `frontend/public/favicon.svg` 入库。
- Clustr100 社区知识库 10 篇(`docs/research/clustr100_community_kb/`,direction-only 非施工授权)。

### Changed — 风控
- symbol 过滤统一为单个 `symbol_list` + `symbol_filter_mode`(whitelist/blacklist)开关决定语义;
  默认 blacklist + 空列表,移除旧的 BTC/ETH/SOL 硬编码白名单。

### Fixed — design-doc 合规(2026-06-11 评审 4 条)
- paper_trader 记录 skip 原因;prediction_observer 显式 `execution_eligible=False`(+ migration);
  event/catalyst scanner 接进 `run_all_scanners` 参与配额;`depth_10bps_usd` gap 注释澄清。

### Operator
- 模式 `Investigating` → `Working`;`current_intent` 现于浮层 + Command Center strip 显示。

## 2026-06-12 — 账户级仓位发现 + 纳管(Phase 1-2 落地,Phase 3-4 设计在册)

**背景**:所有者立场——Aletheia 启动接管账户后,人难以再与 AI 并行管仓,故全账户仓位
(含系统外开出的)应统一交由 AI 统一管理("接管即纳管")。这是把 ROADMAP Phase 6 的
"live-read reconciliation / position-mismatch"思想**提前、且以 demo 优先**落地。

**修复(顺带,先于一切)**:`total_value_usdt` 之前返回持仓名义市值(口径错,会随每笔
fill 跳变),现在交易所缓存可用时用真实账户权益覆盖,与同一 summary 里其它字段一致
(`pnl.py`)。另修 `client.get_positions()`:Bitget v2 mix all-position 的入场价字段是
`openPriceAvg`,旧代码读的 `averageOpenPrice` 根本不存在 → 一直静默为 0;现已读对,并
补 `markPrice`/`leverage`。无真实入场价就无法合成诚实因果链,这是纳管的前置。

**Phase 1 — 发现(只读,已完成)**:新增 `portfolio/position_reconciler.py`
`classify_positions()`,把交易所缓存仓位与本地 open 仓位按 `(canonical symbol, direction)`
配对(hedge 模式下同标的多空并存,故必须按"对"匹配),分类为 managed / external / orphaned。
新增 `GET /api/positions/reconciliation`;`compute_portfolio` 增 `exchange_external_summary`
(并把旧的、hedge 模式下会算错的 external 计数改为按对精确)。前端 `TradesPositions.tsx`
新增 "Account Reconciliation" 卡片(此前 drift 信号无任何前端消费者)。

**Phase 2 — 纳管(已完成,改动受控)**:新增 `source` 列(`native`/`adopted`)到
positions/orders/trade_proposals(CREATE TABLE + `_ADDITIVE_COLUMNS` 双覆盖)。
`adopt_position()` 在单事务内合成 proposal→order(filled)→position 完整链,全部
`source="adopted"`,入场价/数量取自交易所,零入场价/零数量则拒绝纳管(不伪造)。关键设计:
纳管仓位的 `execution_mode` 按账户模式映射(demo→`bitget_demo`,live→`bitget_live_limited`),
于是 `position_manager` 自动管理 demo、跳过 live——**demo 自动 / live 人工的拆分由
`execution_mode` 一处如实设置后被既有守卫免费强制**,无需特殊判断。

**治理(trust §1.1 修订)**:旧铁律"外部仓位永不入本地表"修订为"可入表,但必须带
`source=adopted` 的诚实合成链"。红线测试 `test_external_positions_not_written_to_local_table`
拆为 `test_sync_alone_never_writes_local_positions`(sync 仍只读)+
`test_adopted_positions_carry_synthetic_chain`(纳管必带诚实链)。决策记录见 DECISIONS.md。

**Phase 3-4(设计在册,未实现)**:P3 = 定时 reconcile 任务(demo 自动纳管 + 孤儿平仓,
**默认开启**,见 DECISIONS.md 行为警告)、operator 命令(`reconcile_positions`/`adopt_position`/
`close_position`,live 需人工确认)、手动触发 API。P4 = live 人工确认平仓路径
(`close_live_position`,过 Execution Gateway)+ Full 模式接缝(默认关闭)。

**测试**:`uv run pytest -q` → **777 passed**。前端 `tsc` 干净,bundle 已重建。

---

## 2026-06-10 — CLI 分层 + 运营命令软弃用（人类交互收口到 Web）

**背景**：评估是否完全去掉 CLI、人类交互全走 Web。结论：不去掉，分三层。

**硬约束（为什么不能全删 CLI）**：Web UI 由 gateway 进程伺服，进程没起就没 UI——无法用"还不存在的 UI"启动那个进程；且 Windows 看门狗任务写死 `aletheia.main gateway start`，`doctor` 最需要时正是 gateway 挂掉、UI 够不着时。

**三层划分**：
- **bootstrap 内核（保留）**：`gateway start/restart/enable-autostart`、`doctor`、`version`。
- **运维/调试工具（保留，无 Web 平价）**：`bitget sync`、`knowledge reindex`、`knowledge search`。
- **运营命令（软弃用，有 `/api/*` 平价）**：`permission`/`model`/`risk`/`strategy`/`update` 整组 + `secrets` + `bitget test`。

**软弃用机制（commit `16efd5b`）**：
- `cli/_deprecated.py`（新）：`warn_deprecated()` 往 **stderr** 打一行指向 Web 的黄色提示；**stdout 不变**。
- 整组弃用用 Typer `@callback`（子命令执行前触发）；`bitget test` 用内联提示，`sync` 保持干净。
- `main.py`：`add_typer` 重排为 bootstrap / kept-tools / `[deprecated]` 三档，help 文本加 `[deprecated → …]` 标记。
- `diagnostics/doctor.py`：kill_switch / demo-creds 的 fix_hint 改为 Web 首选、CLI 备选——避免"删命令后提示成谎话"。

**文档同步（commit `2217510`）**：RUNBOOK / README 标 dashboard 为 primary、弃用 CLI 为 deprecated；`implementation_guide`（冻结规格）与 AI_HANDOFF 历史条目不改。

**决策**：软弃用而非硬删——零回退、零功能损失、保留逃生口；物理删除留待将来 Web 路径用顺手后一次性收尾。架构决策见 `docs/DECISIONS.md`。

**测试**：`uv run pytest -q` → **538 passed**（软弃用零行为变更，无回退）。

---

## 2026-06-08 (later⁷) — Gateway 稳定性修复（ghost-socket 根治）

**问题**：前端状态栏显示 "Gateway Stopped" 但页面正常访问；`gateway stop` 无法释放端口。

**根因**：uvicorn multiprocessing worker 在 parent 被 kill 后变孤儿，继承 listening socket 并用旧代码运行。其 cmdline (`multiprocessing.spawn.spawn_main`) 不含 orphan detector 的标记字符串，逃脱所有清理。

**修复（commit `a90ea84`）**：
- `pidfile.py` — `find_port_holders(port)`：netstat/lsof 直接按端口查 PID（兜底）
- `lifecycle.py` — `do_start`/`do_stop` 三层清理：PID tree-kill → cmdline orphan sweep → port holder kill
- `health.py` — `/api/status` 自报 `gateway_running: True`（能响应 = 在运行）
- `app.py` — lifespan 启动补写 PID + ownership 追踪（只清自己写的）
- `gateway.py` — restart 拒绝无恢复机制时的自杀

**决策**：网关端口清理改为 "one stop all" 策略——最终兜底不看进程身份，只看端口占用。
**测试**：46 gateway/supervisor/pidfile tests 全过。

---

## 2026-06-08 (later⁶) — Markets K线动态加载（scroll-back history + live tail）

**目的**：Markets 页（`/charts`）的 K 线图原本只加载固定一段（最新 N 根），向左滚动看不到更早历史，右沿也不自动更新。改成主流交易所体验：向左滚到边缘 → 自动续加更早历史；右沿保持实时刷新。

**后端**（`aletheia/data/ccxt_provider.py` + `server/routes/data.py`）：
- 新增 `_TF_MS` 映射 + `timeframe_ms(tf)` helper（tf→毫秒的单一真源）
- `get_ohlcv`/`_fetch_ohlcv` 增加 `since: int|None` 参数，透传给 CCXT `fetch_ohlcv(since=)`
- `/api/market/ohlcv` 增加 `before`（unix 秒）参数：`since_ms = before*1000 - limit*tf_ms`，并把 `>= before` 的 candle 过滤掉，保证续加页与左沿不重叠
- 缓存策略收紧：只有默认最新视图（`before` 未设 且 `limit==100`）走 TTL 缓存；历史页与实时尾部一律 fetch fresh

**前端**（`api/client.ts` + `charts/PriceChart.tsx`）：
- `getOhlcv` 增加 `before` 参数（第 4 位）
- `PriceChart` 重写为命令式 candle store（refs 持久化，图表不随数据重建）：
  - 初次加载最新 `INITIAL_LIMIT=300` 根
  - 向左滚到 `SCROLL_TRIGGER_BARS=10` 内触发 `loadOlder()`：`before=<最左根>` 取下一页、去重、前插、`setData` 后用 `setVisibleLogicalRange` 平移逻辑区间保持视窗不跳
  - `LIVE_POLL_MS=10s` 轮询最新 2 根，`series.update()` 替换在成形/追加已收盘的 bar
  - 守卫：`loadingOlderRef`/`exhaustedRef`/`initializedRef`；markers 在独立 effect 重挂不重建图表

**验证**：488 passed（+2 新测试：`before` 过滤 + `since` 窗口数学、unsupported timeframe 干净报错）；后端 urllib E2E 确认续加页与最新视图 gap 恰为 1 个 tf、零重叠；Vite :5173 视觉 E2E 确认三种行为（初次 `limit=300`、续加 `before=...`、实时 `limit=2`）全部触发、零 console error。

---

## 2026-06-08 (later⁵ cont.) — Deep Research + ROADMAP v1

**目的**：博采众长制定按周迈步路线图。

**Deep Research 执行**：102 agent / 20 sources / 25 claims → 19 verified (6 killed)

**核心发现（adversarially verified）**：
- 经验闭环有 4 层：无学习 → 记忆累积 → walk-forward 策略修改 → RL 端到端（我们在 1→2）
- 策略必须结构化（Pydantic schema / signal+confidence 数值对），不能 free-text
- Agent 协作主流：fan-out/fan-in + 硬风控门控；对抗辩论是加分非必须
- walk-forward validation 是防噪音固化的唯一可靠方法（SHARP 论文 2026.05）
- 单 agent RL vs 多 agent 辩论之争未定——我们选多 agent + 可解释性
- TradingAgents 明确说"treat as research scaffold"——这是诚实定位

**产出**：`docs/ROADMAP.md`（12 周路线图 + 4 个观测门 + 红线 + 失败模式表）

**来源（核实度最高的）**：
- TradingAgents (github.com/TauricResearch) — structured debate + Pydantic output
- ai-hedge-fund (github.com/virattt) — fan-out/fan-in + signal/confidence
- SHARP (arXiv 2605.06822) — walk-forward validated policy edits
- TradingGroup (arXiv 2508.17565) — chain-based + 20-day outcome labeling
- AlphaQuanter (ACL Findings 2026) — single RL agent, tool-augmented
- FinRL (github.com/ai4finance-foundation) — ensemble DRL baseline
- AutoGen/Magentic-One — actor model + stall detection

---

## 2026-06-08 (later⁵) — Pipeline gap analysis + 文档统一

**目的**：对照"全市场扫描→分析→策略→开仓→平仓→评估→学习→总结"完整预期，
逐环节代码核实当前实现与预期的差距。

**核心发现**：
- 管道骨架 80% 接通：5 crypto + 4 xStocks + 4 stocks + prediction markets 自动扫描；
  market_observer→paper_trader→position_manager 全链路每 15min 自动运行
- 三大结构性差距明确定位：
  1. **经验闭环只闭 30%**：6 agent 写 knowledge，只 2 agent（observer/designer）读回
  2. **Agent 无协作流**：13 个 scheduler job 各自 cron，无方向性数据传递
  3. **策略文字非规则**：designer 产出 entry_rule 自然语言，trader 不读
- 两个 agent 代码完整但未排入 scheduler：`post_trade_reviewer` / `risk_critic`

**产出文档**：
- `docs/PIPELINE_STATUS.md`（新建）— 逐环节现状表 + 差距分析 + scheduler 全景 + 北极星阶段图
- `docs/AI_HANDOFF.md` — 更新 current state + next steps
- `docs/CURRENT_TASK.md` — 更新状态为 PLANNING（stage 0.5 措施待确认）
- Memory `aletheia-mvp-status.md` — 更新 pipeline completion 表

**无代码变更**。纯分析+文档整理 session。

---

## 2026-06-08 (later⁴) — xStocks 接入 + 管道 bug fix 三项

**本次 session 两阶段工作：**

### 阶段一：Bitget xStocks（ON-series 代币化股票）接入

- `aletheia/config/schema.py` — 新增 `observed_xstocks: list[str]`（默认 AAPLON/TSLAON/NVDAON/SPYON）
- `aletheia/trading/instruments.py` — 新增 `xstocks_instruments_from_config()`，返回 `exchange="bitget"` 的 `InstrumentId` 列表
- `aletheia/agents/market_observer.py` — 新增 xStocks 分析 loop：独立 Bitget provider，`asset_class="equity"`，Opportunity 写入但 paper_trader gate 门控跳过（observation-only）
- `aletheia/trading/market_data.py` — 新增 xStocks ticker 采集 loop，写入 `market_snapshots`（position_manager 定价所需）；提取 `_fetch_and_store()` helper 复用
- `tests/test_xstocks_observer.py` — 4 个新测试覆盖 xStocks 路径

### 阶段二：管道 bug fix（全链路审计发现）

1. **`Opportunity.confidence` 默认值** (`aletheia/storage/models.py:51`)
   - `"medium_confidence"` → `"speculative"`
   - 原因：paper_trader `_CONFIDENCE_RANK = {"speculative":0, "medium":1, "high":2}` 不含 `"medium_confidence"`，任何默认值 opportunity 被 rank KeyError 当 0 静默跳过

2. **`paper_broker.py` datetime 时区不一致**
   - 移除 `from datetime import datetime` 和 `filled_at=datetime.utcnow().isoformat()`（naive）
   - `Order.filled_at` 默认 None；`Fill.filled_at` 用 `_utcnow()`（带 tzinfo）

3. **xStocks ticker 未写入 market_snapshots**（顺带修复）
   - position_manager 调 `_latest_price(symbol)` 时因无 snapshot 返回 None → 持仓永不触发 stop/TP
   - 修复：`market_data.py` xStocks loop 调 `_fetch_and_store()` 同等写入

**测试结果**：474 passed，1 flaky（`test_write_audit_jsonl` 全量并发路径泄漏，单跑通过，既存问题）

**commit**: `0303907 fix(pipeline): confidence default, datetime tz, xStocks ticker snapshots`
**commit**: `4f59d70 feat(xstocks): Bitget tokenized equity observation via market_observer`

## 2026-06-08 (later³) — Chart blackout root-cause: stale gateway process + provider consolidation

**Symptom**: Every lightweight-charts component (equity curve + the 3 markets
K-line charts) rendered blank. `/api/market/ohlcv` returned
`{"error":"market_data_unavailable","candles":[]}` and the frontend's
`Array.isArray(data) ? data : []` guard silently turned that into an empty array.

**Root cause was NOT missing abstraction** — the data layer (`MarketDataProvider`
protocol, `CCXTProvider`, `OHLCVCache`, `symbols.py`) was already implemented and
correct on disk. Two real causes:

1. **Stale process (the actual blackout).** The gateway booted at 11:16; the commit
   that switched `data.py` to `CCXTProvider` landed at 13:26. The running process
   held the *old* `data.py` in memory — which called legacy `BitgetClient.get_ohlcv()`
   with canonical `BTC/USDT`, a symbol Bitget REST v2 rejects (`Parameter BTC/USDT
   does not exist`). **Completely invisible**: `/api/health` still reported healthy.
   Fixed immediately by `aletheia gateway restart`.
2. **Construction drift.** `CCXTProvider(...)` was hand-constructed from the same
   "read config → build" template in 3 places, inviting copy-paste in new components.
   A dead, uncalled singleton `get_default_provider()` also lingered — exactly the
   cross-event-loop singleton trap we want to forbid.

**Hardening landed (so this class of bug surfaces honestly next time):**

- **Version-drift health check.** New `aletheia/agent_runtime/build_info.py` records
  the source commit at boot (`record_boot_commit()` in `lifespan()`, before any
  scheduler thread starts). `code_drift_issue()` compares disk HEAD to the booted
  commit; `compute_health()` now appends `code_updated_pending_restart:<old>..<new>`
  when they diverge → `/api/health` goes degraded, telling us to restart. Reuses
  `update/git_ops.current_commit()`. Non-repo / git-unavailable → stores None → never
  false-alarms. Known limit (accepted): uncommitted working-tree edits don't move
  HEAD so aren't flagged — but the real failure here was "committed, not restarted",
  which is caught.
- **Single provider factory.** Deleted the dead singleton; added
  `get_market_data_provider(exchange=None)` as the one market-data entry point
  (per-call, never cached — documented why: CCXT binds aiohttp to the creating loop,
  scheduler uses a fresh loop per job). Routed 3 sites through it: `routes/data.py`,
  `trading/market_context.py`, `trading/market_data.py`. Left the 2 Bitget-only
  xStocks sites (`market_data.py`, `agents/market_observer.py`) constructing
  `CCXTProvider(exchange="bitget", fallback_exchange="bitget")` directly — both now
  carry a comment marking the deliberate exception (factory would force the config
  fallback `bybit`, which doesn't list ON-series tokens).
- **CLAUDE.md** Development Rules gained a market-data rule pointing at the factory
  and forbidding `BitgetClient` for market data.

Note: the equity curve being flat at 0.0 is *honest*, not this bug — `PnLChart`
reads `/api/market/portfolio-history` (DB), which has snapshots but no positions yet.
Out of scope.

## 2026-06-08 (later²) — Market context polish (thesis fallback + score persisted)

After the market context layer landed, real-run inspection surfaced two
rough edges:

1. **codex_cli's structured output is not 100% reliable.** On ~1 in 3 runs
   it returns a valid JSON shape but with empty `rationale` and empty
   `key_observations` (the JSON parse drops fields, or the model returns a
   minimal stub). Old code then fell back to `thesis = context[:500]`,
   dumping the raw markdown snapshot (`## Verified market data snapshot`,
   table headers etc.) into the dashboard thesis cell — looked broken.
2. **`confidence_score` only lived inside the thesis text** (`score=65/100`
   at the end). The dashboard couldn't surface it as a real column.

Both fixed:

- **`opportunities.confidence_score INTEGER`** column added (additive
  migration; nullable so older rows stay valid). `Opportunity` Pydantic
  model + frontend `Opportunity` type updated.
- **Thesis fallback rewritten**: when rationale + observations are both
  empty, thesis is a one-line stub citing the failure mode
  (`"No structured rationale produced (direction=X, confidence=Y). Likely
  cause: LLM unavailable or structured-output parse failure — see
  agent_runs/errors."`). Raw snapshot never leaks through.
- **Charts page Markets table** — added a `Score` column showing
  `confidence_score/100` with a tooltip explaining what it means and why
  it can be empty. Side-by-side with categorical confidence so the model's
  full calibration is visible at a glance.

Real e2e (3 fresh runs after migration applied):
- `long / medium / 64/100` — full structured shape ✅
- `neutral / speculative / 50/100` — model genuinely on the fence,
  honestly committed a midpoint ✅
- `neutral / speculative / —` + thesis "No structured rationale..." —
  Codex CLI timed out, fallback stub kicked in cleanly ✅

New test `test_observer_thesis_fallback_avoids_raw_snapshot`. Full suite
**423 passed** (the 2 failures in `test_exchange_sync.py` predate this
work — verified by stashing other working-tree mods and re-running).
Zero change-controlled module touched.

## 2026-06-08 (later) — Market context layer (fixes "all neutral / speculative")

The dashboard Markets page had been showing every market_observer opportunity
as `direction=neutral, confidence=speculative` — symptom that read like "AI is
not running". Diagnosis: AI is running fine (codex_cli healthy), but the LLM
was being fed exactly **one line per symbol** ("BTCUSDT: price=X 24h_change=
0.05%"). With a 1-D input the model can only reply at the base rate; the
system prompt's "be conservative — default to neutral" hardened that into
permanence.

Fix is the data-supply layer, not the prompt. Borrowed the **TradingAgents**
pattern (multi-timeframe pre-computed indicators + short close series +
markdown verified-snapshot tables + structured output schema) using stdlib
only — no numpy/pandas/ta-lib. Research memo and design rationale in
`docs/research/market_context_eval.md` (covers OpenAlice tool-as-DSL pattern,
TradingAgents predigest pattern, why VergeX is unavailable as a reference).

What got built:

- **`aletheia/trading/indicators.py`** (new, stdlib-only) — SMA / EMA / RSI
  (Wilder's smoothing) / ATR / Bollinger / momentum / volume z-score / cross-
  asset relative-strength rank. Pure functions, oldest→newest series, None on
  too-short windows.
- **`aletheia/trading/market_context.py`** (new) — `build_market_context()`
  pulls 1h × 60, 4h × 50, 1day × 30 OHLCV per symbol via the existing
  `BitgetClient.get_ohlcv` (no creds, no new endpoints), computes the
  indicator pack per timeframe, ranks the symbols by 24h return, and renders
  a ~2.5kb markdown block: per-symbol indicator table + last-10 1h closes +
  cross-asset RS table + indicator glossary. Falls back to the legacy 1-line
  context on any failure (red line: agents must never crash).
- **`aletheia/agents/market_observer.py`** — feeds the new context block in,
  upgrades `MarketRead` schema to add `confidence_score` (0-100, forces the
  model to commit a number) and `key_observations` (list of specific data
  citations — empty list signals "no concrete evidence"). System prompt
  rewritten: removed the "default to neutral" hard nudge, replaced with
  calibration ladders (high/medium/speculative criteria, when long vs short
  vs neutral is justified). Adds two `record_step()` breadcrumbs
  (`market_read_input` with the full snapshot, `market_read_output` with the
  structured read) so the dashboard step trace shows the model actually saw
  data and reasoned over it.

Real e2e: first run after the change produced
`direction=long, confidence=medium` with the thesis citing
"all three above 1h SMA20 and SMA50, positive 1h+4h Mom10, ETH leads cross-
asset RS" — vs the previous N runs which were unanimously
`neutral / speculative`. The ground truth (BTC/ETH/SOL all up 2–6% intraday
with multi-timeframe agreement) finally reaches the model.

Red lines kept: no funding/OI/orderbook (would extend client.py surface), no
news/sentiment (AGENTS.md ban), no numpy/pandas (dependency discipline), no
change-controlled module touched. New `tests/test_market_context.py` (17
tests covering indicators, builder render, fallback path, observer integration
and structured-output extraction). Full suite **423 passed**.

What this *doesn't* do: deepen the perception layer (funding regime / OI /
orderbook / news) — those remain north-star-deferred until stage-0 evidence
accumulates. This pass just ensures the data we already collect actually
reaches the model. The Learning Ledger's `activation_ratio` now has a real
input signal (vs being permanently 0% because no opportunity ever advanced).

## 2026-06-08 (latest) — Three remaining real gaps closed

After WEBUI_BACKEND_HANDOFF was done, three real gaps remained that I hadn't yet
addressed (everything else was either already fixed, recorded as a future-scope
north-star item, or banned by AGENTS.md). All three closed in one pass — they're
small once the §1.1 causal chain and §6 experience-loop scaffolding are in place:

- **Gap 1 — agent_run_steps now actually populated.** The `/api/agent-runs/{id}/steps`
  endpoint shipped in the prior pass returned an empty array because nothing was
  writing to the table. Hooked the write in `agents/helpers.py::log_tool` (every
  tool call now produces a step) plus a new `record_step()` for non-tool reasoning
  nodes. Per-run step counters live in-process and are released by the orchestrator
  at end-of-run so the dict stays bounded. **Zero changes to the 8 agents** — they
  already use `log_tool` everywhere, so the trace fills in automatically.

- **Gap 2 — Stability monitor + `GET /api/stability`.** Bridges the gap between
  "process alive" (existing `health.py`) and "data internally consistent"
  (north-star §7 stage-0 evidence). New `agent_runtime/stability_monitor.py`
  reports orphan counts (fills with no order, executed proposals with no order,
  etc.), strategy_id drift (paper proposals without a strategy — direct §1.1
  causal-chain signal), 24h agent failure ratio, uptime hours, and a 0-100
  composite score with explicit reason codes. Read-only — inconsistencies are
  REPORTED, never silently repaired. Operator can poll the endpoint through the
  7-14 day stage-0 run and watch the score numerically.

- **Gap 3 — Learning Ledger gets a quality block.** `dreaming_agent` was already
  emitting a Learning Ledger but it only counted volume ("research_note: 12,
  strategy_memory: 3"). Volume can be noise. Added stage-1 quality signals:
  activation ratio (% strategies that reached paper_running or beyond),
  retire/deprecate ratio (noise being filtered — non-zero is healthy),
  post-trade-review coverage (%), and lessons-consulted count over 24h
  (`retrieve_lessons` + `load_playbooks` calls — non-zero means the experience
  loop is actually closing). This is the evidence north-star §7 stage 1 wants.

Wiring is intentionally minimal: no new tables, no schema migration, no agent
behaviour changes. Just three new read paths that surface signals already implied
by data we already record. New `tests/test_three_gaps.py` (10 tests) including a
red-line test that orphan fills get DETECTED, never silently fixed. Full suite
406 passed.

What this leaves on the gap list: none of the *immediate* "real" gaps. Remaining
items are all north-star architectural work (Sophia-class long-term identity,
RL feedback loop, cross-agent debate workflow, deeper market perception) that
the project's stage sequencing explicitly defers until 7-14 days of clean stage-0
evidence accumulates. The stability + ledger numbers above are the receipts that
let "stage 0 done" become a yes/no rather than a vibe.

## 2026-06-08 — Exchange Account Sync (closes WEBUI_BACKEND_HANDOFF #3 / portfolio-sync-gap)

The Portfolio dashboard was off by an entire reality: it only saw locally-managed
positions, so `total_value_usdt = 0` even when the demo account held real balance.
Now closed:

- **`portfolio/exchange_sync.py`** (new) — `sync_exchange_account()` calls the existing
  read-only Bitget client methods (`get_balances`/`get_positions`/`get_open_orders`)
  and caches the result as a JSON blob in the `settings` table (no schema change). Status
  classifier `ok | stale | unconfigured | error`. Never raises; degrades to None and the
  UI shows the existing caveat. **Note (turned out client.py was already exposing
  everything — no change-controlled module touched).**
- **`compute_portfolio()`** — extended with `exchange_equity_usdt`,
  `exchange_state_age_sec`, `exchange_external_positions` (drift signal), and
  `exchange_sync_status`. The local managed `total_value_usdt` field is preserved so the
  UI can show both side-by-side and reconcile drift.
- **`GET /api/exchange/account`** — read-only cached view; HTTP path never blocks on
  Bitget. Scheduler refreshes the cache every 5 min via a new `_run_exchange_sync` job.
- **`aletheia bitget sync`** CLI — manual one-shot for debugging.

Red lines kept: read-only against the exchange; external positions stay out of the
local `positions` table (they have no proposal back-link → would violate trust §1.1);
`exchanges/bitget/client.py` untouched. New `tests/test_exchange_sync.py` (9 tests
including a red-line test that asserts the local positions table stays empty after a
sync). Full suite 396 passed. Real e2e: CLI sync against the live demo account
returned `status: ok` and the cached view flowed into `/api/exchange/account` and
`/api/portfolio` after a gateway restart.

Cross-process visibility caveat: SQLite WAL means a CLI write doesn't immediately
appear to a long-running gateway process holding its own connection. In practice the
scheduler refreshes the cache from inside the gateway process every 5 min, so the API
sees the truth on its own timeline — no extra wiring needed for the production path.

## 2026-06-07 — WebUI backend handoff: position FK + steps endpoint

Implemented the two actionable, in-scope gaps from `WEBUI_BACKEND_HANDOFF.md`:

- **#1 position → proposal/order causal link** (⭐ highest value): `positions` gained
  nullable `proposal_id`/`order_id` FK columns (CREATE TABLE + additive migration).
  Both brokers populate them when a fill opens a position; averaging into an existing
  position preserves the *original* opener. Makes the position→upstream direction of the
  trust §1.1 causal chain real (CausalChain.tsx no longer needs "not recorded"). Touched
  change-controlled `execution/bitget_broker.py` — additive only, focused regression tests.
- **#2 `GET /api/agent-runs/{id}/steps`**: read-only endpoint exposing `agent_run_steps`
  (new `list_run_steps`/`insert_run_step` repo fns). Honest caveat: the table is not yet
  written by any agent, so it returns `[]` today — populating it is a separate, larger
  change (every agent) beyond the handoff's "expose the table" ask.

Not done (by design): **#3** exchange equity touches change-controlled `client.py` → needs
explicit owner authorization; **#4** news ingestion is banned MVP scope (AGENTS.md).

`tests/test_webui_backend_handoff.py` (6 tests); full suite 387 passed.

## 2026-06-07 — Experience loop (north-star stage 2, minimal closed loop)

### Added — knowledge that reads back into decisions (§6 gap closed, minimally)

The knowledge base was write-only (the central north-star gap). It now feeds prior
lessons back into decisions, using frontier-but-constrained patterns (Generative-Agents
retrieval scoring, Hermes-style memory block, RAG provenance) on **SQLite FTS5 — no
vectors, no new deps**:

- `knowledge/retrieval.py` (new): FTS5 index + `retrieve_lessons()` scored by
  BM25(relevance) × recency-decay × importance. **Quality gate: only `high_confidence`
  + `active` items are retrievable** — speculative/archived noise can't become "experience".
  `format_memory_block()` renders results as an **explicitly advisory, provenance-tagged**
  block. Degrades to empty on any failure (FTS absent / no match) so agents never break.
- `migrations.py`: `knowledge_fts` virtual table, created out-of-band so a SQLite build
  without FTS5 can't break other migrations.
- `knowledge/manager.py`: `create_*` index high-confidence items; archive/deprecate
  remove them from the index (archived ⇒ not recalled).
- `trading/playbooks.py`: `load_playbooks()` / `format_playbook_block()` — playbooks are
  now **read** into the designer prompt (were seed-only, 0 readers). Read-only; agents
  never write playbooks.
- `agents/market_observer.py` + `agents/strategy_designer.py`: inject the memory block
  (and playbooks for the designer) into the LLM prompt; retrieval is logged as a
  `retrieve_lessons` tool call (observable). Injection is pure prompt text — it cannot
  touch risk/execution/config.
- `cli/knowledge_cmd.py` (new): `aletheia knowledge reindex` (backfill) + `search` (preview).

Governance held: read-only, quality-gated, advisory-not-instruction, no change-controlled
module touched, no live gate weakened. New `tests/test_experience_loop.py` (8 tests);
full suite 381 passed. End-to-end smoke confirmed a high-confidence lesson reaches the
observer's decision prompt with its provenance tag.

## 2026-06-07 — WebUI structural redesign (frontend-only)

### Changed — 14 dashboard pages → 10 role-organized destinations

Complete structural redesign of the React frontend to fix the owner's pain points
(density / layout / transparency / workflow). Diagnosis: UI was a flat page-per-DB-table;
the framework wants §23.1's 6 roles around the causal chain + a workflow flow. **Zero
backend / API changes** — pure frontend composition over existing endpoints.

5-phase IA (Observe / Decide / Execute / Evolve / Operate). Merges:
- Positions + Paper Trades → **Trades & Positions** (`TradesPositions.tsx`) + new
  **`CausalChain.tsx`** inspector (proposal→risk→order→fill→position; position-anchored
  upstream shown as "not recorded" — honest re trust §1.1, never symbol-matched).
- Agent Runs + Opportunities → **Agent Activity** (`AgentActivity.tsx`, tabs via new
  `Tabs.tsx`); run drawer "Reasoning Steps" block = mount point for the unbuilt
  `GET /api/agent-runs/{id}/steps`.
- Dreaming + Knowledge Base + Reports → **Learning** (`Learning.tsx`, 3 tabs; §4 KB
  taxonomy preserved, no type laundering).
- Proposals → **Approval Desk** (decision cards + inline risk verdict + confirm-to-approve).

New `PageHeader.tsx` (title + role descriptor) on all 10 pages — directly answers the
"transparency insufficient" complaint. News Intel got the §17.3 honesty banner
("manual entry / ingestion pipeline not configured"). RiskControls grid → responsive.

Deleted 7 merged page files. Legacy routes redirect (no dead links). CommandPalette +
cross-links updated. Verified live (preview MCP): nav structure, tab switching, honest
empty states, legacy redirects, zero console errors. tsc + vite build green.

Docs: `WEBUI_PAGE_MAP.md`, `OVERVIEW_REDESIGN.md`, `WEBUI_BACKEND_HANDOFF.md` (parked
backend gaps), `BACKEND_ISSUES.md §12`. Branch: feat/strategy-causal-chain (not merged).

## 2026-06-07 (later)

### Fixed — re-audit of BACKEND_ISSUES §10 + 8 falsely-archived issues

Re-verified the §10 "all fixed 2026-06-06" archive against current code (40+ items):
~75% true, ~25% claimed-fixed-but-not. Eight confirmed false/half-true, now really
fixed (each with a focused regression test; full suite 373 passed):

- §1.2 live-order: insert a `pending` order row BEFORE `place_order` (was place-then-
  insert → exchange order with no local trace if DB write fails). `bitget_broker.py`
- §1.3 health alert: run the Telegram coroutine to completion in a worker thread (was
  `loop.create_task` fire-and-forget → alert silently dropped from a sync caller). `health.py`
- §2.3 paper/demo risk: added `max_demo_position_notional_usdt` + `max_demo_single_trade_risk_pct`
  with distinct violation tokens (was unbounded → promotion evidence built from unbounded
  trades). `risk/engine.py`, `config/schema.py`
- §2.3 clientOid: `place_order` now sends a proposal-derived `clientOid` (idempotency on
  retry). `exchanges/bitget/client.py`
- §2.3 kill-switch TOCTOU: `evaluate_proposal` serialized via `_eval_lock` so a daily-loss
  trip is visible to the next proposal. `risk/engine.py`
- §2.5 paper_trader: autonomous-OFF gate duplicated into the agent body (CLI/API trigger
  bypassed the scheduler gate). `agents/paper_trader.py`
- §3 commit_all: dreaming agent calls `commit_all` after consolidation (Trading-as-Git was
  wired but never invoked). `agents/dreaming_agent.py`
- §3 gateway logs: size-cap rotation (`rotate_if_needed`) + tail-only reads (was unbounded
  growth + whole-file read → OOM). `gateway/logs.py`, `_gateway_runner.py`

Also added `starting_capital_usdt` as a real config field (was read via getattr fallback).
The §10 archive now carries a correction banner; lesson recorded: never trust a "fixed"
claim without grepping current code.

### Fixed — strategy↔trade causal chain (gap #2, north-star stage 0/2 prerequisite)

The `strategy_id` causal chain was broken in **four** places, not one (the
2026-06-06 §1.1 "fix" only wired paper_trader's fallback, leaving the upstream
producer side dead). All four now connected:

- `strategy_lifecycle.create_strategy()` accepts `parameters` → persisted to
  `StrategyRecord.parameters` (was always `"{}"`; trust §4.2 audit snapshot)
- `strategy_designer` produces a structured parameter spec and auto-advances the
  strategy through the **ungated** lifecycle states to `paper_running` (stops there;
  live gates untouched), so a tradable strategy actually exists
- `strategy_designer` registered as a daily scheduler job (was never scheduled →
  no strategies were ever produced autonomously)
- `market_observer` binds new opportunities to an active strategy via
  `linked_strategy_id` (honest None if none active; was never set, zero coverage)

Result: promotion-gate `paper_trades` count is no longer structurally stuck at 0.
New `tests/test_strategy_causal_chain.py` (5 tests) locks the chain end-to-end.
Suite: 357 passed.

### Changed

- Documentation housekeeping: archived all fixed items in BACKEND_ISSUES.md (§10) and FRONTEND_ISSUES.md (§10.1–10.24); removed root-level IMPLEMENTATION_GUIDE.md duplicate (canonical copy in docs/)
- Gateway: replaced all Windows subprocess calls (wmic/tasklist/powershell/taskkill) with pure ctypes Win32 API — fully invisible operation, zero console window flashes

### Decision

- Process introspection on Windows uses kernel32/ntdll ctypes instead of spawning external executables (see DECISIONS.md)

## 2026-06-06

### Added

- CLAUDE.md (project overview for Claude Code)
- .claude/ directory (settings, commands: start-session, end-session, update-handoff, project-audit)
- .codex/config.toml (Codex project config)
- docs/PROJECT_CONTEXT.md, docs/CURRENT_TASK.md, docs/AI_HANDOFF.md, docs/DECISIONS.md, docs/DEVLOG.md, docs/AI_WORKFLOW.md

### Fixed (major pass)

- **Backend (80+ fixes)**: All items in BACKEND_ISSUES.md §1-§3 — trust invariants (strategy_id causal chain, execution gateway isolation, git ff-only, promotion gates), data integrity (PnL fees, KB status filtering, CORS, audit symmetry, risk evaluation, scheduler error handling, telegram hardening), dead code cleanup
- **Frontend (24 fixes)**: All items in FRONTEND_ISSUES.md §1-§3 — shared types/DataTable/DetailDrawer components, risk_decision display, KB markdown reading, autonomous_paper_trading toggle, effective config card, promotion gates UI, chart-trade linking, dreaming 6-section layout, overview sparkline, cross-page navigation, agent run tool calls

### Notes

AI workspace initialization. Existing AGENTS.md preserved as-is (already comprehensive).
