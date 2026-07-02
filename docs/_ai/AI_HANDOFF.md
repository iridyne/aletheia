# AI Handoff

> **本文件是 append-only 变更史**(记录"做了什么改动 + 根因",机器无关)。
> 其中任何"当时机器状态"(gateway 在跑 / 观察期第 N 小时 / 凭证已存 / demo 单已落)
> 都是**写入时刻的快照,不代表当前**。要知道当前真实状态,运行 **`aletheia doctor`**。
> 双机意图/事实分离的金标准见 `docs/_ai/DEV_PROTOCOL.md`。

## Last Updated

2026-07-02 (Position C **全部完成** A+B+C — 规则引擎删除 + 决策链解耦 + 信任边界 + 全 operator/UI 面移除 + 文档/spec 同步)

---

## Recent Change (Position C Phase C 完成 — 文档/spec 同步 → Position C 全部收官 — 2026-07-02)

> **Position C(规则引擎删除)A+B+C 全部完成。** 分支 `refactor/strategy-position-b-p0p1`,
> 全量绿,未 merge master。上线路径见文末。

### Phase C 交付
- **SDD spec 诚实性**(`commit`):spec_strategy_pool 删 SP-S04(测已删 strategy_designer 码);
  spec_model_routing MR-S04 六→五 deep-tier(去 strategy_designer);spec_promotion_gates 不改
  (Option B 保留 check_promotion_gates)。conformance 537 绿。
- **8 契约文档**:scheduler/agent_contracts/SYSTEM_MAP job 表(去 designer+backtest 加 guard_promotion +
  agent 段/timeout/数据流图);api_reference(删 strategies gates/promote/retire + backtest + strategy-evidence
  端点);data_model(4 表标 legacy read-only);promotion_gates_contract + README(LEGACY 横幅);
  execution_contract §9(死字符串诚实性缺口 → 标记已由删除解决);trust_and_change_control §6.1 +
  decision_lifecycle 无需改(引擎非受控信任边界/无引用)。
- **整洁项**:删孤儿 backtest_paper_drift.py + 其单测(路由已删)。
  遗留(无害 optional,已记):ranker always-None strategy_evidence 死参、operator_recommendations
  喂已删分支的死计算(2 处 list_artifacts,操作控制面敏感故未动)、router.py + knowledge_contract 的
  strategy_designer 知识路由(dead-but-consistent 配置,Phase A 有意保留)。

### Position C 全景(A+B+C)
- **A**:`aletheia/strategy/` 整包 + strategy_designer/strategy_evidence/ruleset_scanner/lifecycle_automation
  物理删;ranker/paper_trader/scheduler 解耦;guard_promotion 重挂保命。
- **B**:风控 #14 → scanner-family 证据门(信任边界,SDD-first);决策链/analytics landmine 清;前端页删 +
  #14 Settings UI;doctor strategy_health 改 legacy-only;operator 命令面(6 命令 + 推荐 + 路由 + 动作)移除。
- **C**:spec + 契约文档同步(本条)。
- **保留(Option B)**:create_strategy/promote_strategy/check_promotion_gates 内部 legacy 函数;
  strategy_records/backtest_* 表只读 legacy;strategy_id 可空归因列;GET /strategies + venue 端点。

### 上线路径
Position C 已闭环,可整体 FF-merge 回主仓 + 主仓 restart + HTTP 实测(worktree data/ 空不代表运行态)。

---

## Recent Change (Position C Phase B 全部完成 — 2026-07-02)

> 承接下方「Phase A 后半」。同一 worktree/分支。**Phase A + B 全部完成,12 commit 领先 master,全量 1702 绿。**
> owner 已定 **Option B**。**唯一剩余 = Phase C(文档/spec 同步)**。

### 已交付(全绿)
- **`49c580f` 风控 #14 闸改写(信任边界,owner 批「家族证据门·沿用旧量级」)**:strategy FSM 准入 →
  scanner_family 干净窗证据门(经 opportunity 解析 family → `family_direction_edge` 要 n≥
  `risk.live_family_min_samples`(30) 且 win_rate≥`risk.live_family_min_win_rate_pct`(50));fail-closed;
  删死码 `_LIVE_ELIGIBLE_STATES`;token `family_not_live_eligible:*`。SDD-first:RE-S06 + RE-S04/S04b 前缀
  加 "family_" + risk_engine_contract §2 #14 改写 + 3 行为测试。
- **`49b9cf3` 决策链 + analytics 路由 landmine 清理**:trading_decision 去 ruleset_signals;
  commands.evaluate_trading_opportunity 去 scan_active_rulesets;routes/analytics 删 strategy-evidence +
  backtest 端点。**至此 aletheia/ 内无任何对已删模块的 import**。
- **`0df5016` 前端 + #14 Config↔UI 闭合**:App.tsx 去 StrategyLifecycle import/route/nav;删
  `pages/StrategyLifecycle.tsx`;client.ts 删死方法(getStrategyEvidence/getBacktestPaperDrift/
  getBacktestRuns/Detail/Compare);Learning.tsx 去 "Strategy promotion" 板+PromoCard;**#14 两 config 字段
  Settings UI 补齐**(settings.py RiskConfigUpdate+_RISK_BOUNDS / client.ts 类型 / Settings.tsx
  RISK_FAMILY_GATE 段)。`npm run build` 通过(tsc 无错 + 780 模块)。
- **`6541502` doctor content.strategy_health 改 legacy-only**:引擎删后 strategy_records 冻结,旧 zombie WARN
  会误报;改为报 legacy 计数 info(probe-honesty);test_operator_loop content_health 捕获测试的 WARN 源
  换成 content.proposal_bias。
- **`0df5016` 前端 + #14 Config↔UI 闭合**(见上)。**`6541502` doctor legacy-only**(见上)。**`ac8a7d6` handoff**。
- **`8b8b7ba` operator 命令面移除(#7,Phase B 收官)**:6 个 strategy-FSM 命令(get_strategy_lifecycle/
  create_strategy_draft/create_paper_experiment/mark_strategy_under_review/pause_paper_strategy/
  propose_strategy_promotion)+ dispatch + registry CommandDef + intent + keyword;operator_recommendations
  5 条指向已删命令/agent 的推荐分支(保留 live_limited_enablement_proposal_due);operator_loop
  trigger_strategy_designer OperatorAction(+ 自主发射 + _action + MAX 常量);routes/data promote/retire/gates
  端点;client.ts promoteStrategy/retireStrategy/getStrategyGates。retire/demote 动作保留(已收敛 TS)。
  底层 create_strategy 等按 Option B 保留。命令级测试 ~19 删/改(6 文件),全量 1702 绿。

### ✅ owner 已定 = Option B
`create_strategy`/`promote_strategy`/`check_promotion_gates` **作内部 legacy 函数保留**(写保留的 legacy 表,
~15 测试 fixture 零改);strategy_lifecycle.py 不收缩 FSM 函数(promote_strategy 仍用 _has_pi_approval)。已只删 operator/UI 面。

### ⏳ 唯一剩余 = Phase C(文档/spec 同步,纯文档为主,无 runtime/测试影响)
- **spec 诚实性**:`spec_strategy_pool` SP-S04(strategy_designer regime targeting,已删)标废 + 迁 SP-S05/S06/S07
  到 scanner spec;`spec_model_routing` 去 strategy_designer 引用。(注:spec 测试体不被 pytest 收集,故不报错,
  但 conformance matrix 计数偏高——诚实性问题。)
- **8 契约文档**:trust_and_change_control §6.1 受控模块清单(去 strategy 引擎)、decision_lifecycle、
  agent_contracts(去 strategy_designer job + backtest_orchestrator,加 guard_promotion)、promotion_gates 标废、
  api_reference(去 strategies/backtest 端点)、data_model(strategy_records/backtest_* 标 legacy-only)、
  SYSTEM_MAP(scheduler job 表:去 strategy_designer_daily/eu/us + backtest_orchestrator,加 guard_promotion)、
  design/README。risk_engine_contract §2 #14 已在 49c580f 改。
- **整洁项(无害,可选)**:backtest_paper_drift.py 孤儿死码(路由已删,只剩单测)可删;paper_trader_ranker
  always-None 的 strategy_evidence 死参可清;operator_recommendations 留了喂已删分支的死计算(未用变量 +
  2 处 list_artifacts(500))可清。

### 上线路径
Phase C 完成后一次性把这些 commit FF-merge 回主仓 + 主仓 restart + HTTP 实测(worktree data/ 空,不代表运行态)。

---

## Recent Change (Position C Phase A 后半 — 引擎包物理删除 + guard_promotion re-home — 2026-07-02)

> 承接下方「Position C Phase A 前半」。本次在 worktree `.claude/worktrees/strategy-deletion`
> (基于分支 `refactor/strategy-position-b-p0p1`)完成 Phase A 后半。用 worktree 隔离而非在

> 承接下方「Position C Phase A 前半」。本次在 worktree `.claude/worktrees/strategy-deletion`
> (基于分支 `refactor/strategy-position-b-p0p1`)完成 Phase A 后半。用 worktree 隔离而非在
> 主 checkout 切分支 —— gateway 全程跑主仓 master 稳定完整引擎,auto-restart git hook 有主工作树
> 守卫不会从 worktree 触发,把计划 §3「半删状态被重启崩溃」的风险从"缓解"变成"消除"。

### 已交付(本次 commit,Phase A 后半)
- **scheduler 解耦**:删 3 个 strategy_designer cron job + backtest_orchestrator job +
  `_run_strategy_designer` / `trigger_strategy_designer_on_demand` / `_run_backtest_orchestrator` /
  `_backtest_orchestrator_body` + 两个 `_ASYNC_JOB_TIMEOUT_SEC` 条目。
- **3 个热路径调用点解耦**:signal_emitter(event-driven design 块 + `_high_conf_emitted` 死变量)、
  price_feed(significant-move designer 触发块)、post_trade_reviewer(P3 review→design 事件链 +
  `_trigger_strategy_adaptation` 函数)全部移除。
- **dreaming_agent**:去 performance_ledger 的 factory_block(Strategy Factory metrics 段)。
- **cli/strategy**:删 backtest 命令(留 list/status 读 legacy 表)。
- **物理删除**:`aletheia/strategy/` 整包(14 文件)+ `agents/strategy_designer.py` +
  `analytics/strategy_evidence.py` + `trading/ruleset_scanner.py` + `analytics/lifecycle_automation.py`。
- **测试**:删 9 个纯引擎 test_*(dsl/backtest_gate/param_sweep*/walk_forward/orchestrator/evidence/
  causal_chain/event_driven_design)+ spec_evaluator_contracts(去 test_spec_conformance 导入);
  调整 test_runtime(job 集 + 删 designer 测试)、test_event_triggers(删 2 designer 测试)、
  test_experience_loop(删 designer 测试)、test_learning_loop_integration(删 TestRegimeAwareFiltering/
  TestRuleSetBonus/TestPostTradeEventChain 三类,留 TestLessonAwareScoring —— 前两类本是 b9f733f 就该清
  的漏网,上会话只跑 ranker 子集没发现)。

### ⚠️ 计划外的关键 ripple(计划 §2 未预料 —— 已妥善闭合)
删 backtest_orchestrator 会**静默掐断 `guard_promotion`(活跃学习子系统,doctor 13 active guards)**:
它唯一触发链是 backtest_orchestrator → `run_lifecycle_automation` → `run_promotion_check`,无其它 scheduler
入口。**处置**:`analytics/lifecycle_automation.py` 三函数中 `apply_strategy_evidence_actions`(用被删的
strategy_evidence)、`apply_exit_policy_feedback`(写 suggested_exit_params,消费者是被删的 strategy_designer,
前端不读)均已死 → 整文件删;`run_promotion_check` **重新挂成独立 scheduler job `guard_promotion`**
(daily 04:00 CST,executor=io,gated by runtime.paused —— 精确保留原节拍与门控,零行为变化)。
test_runtime required job 集加 `guard_promotion` 锁死防回归。

### 验证(证据)
- 20 个入口模块(app/scheduler/operator_loop/commands/routes/doctor/agents/cli 等)全部 import OK ——
  **物理删包不破坏系统 import**(所有外部引擎引用都是函数内 lazy)。
- 受影响 8 个测试文件 112 passed;spec conformance 537 claims / 99%。
- 全量套件:**1721 passed / 3 skipped / 0 failed**(182s;总数较 8f55261 的 1842 减少 = 删除 ~100+ 引擎测试)。

### 仍留待 Phase B/C(未合并 master 前必须清完 —— 分支合并前这些不会 live)
- **Phase B 运行时地雷**(lazy import,import-安全,被调时降级,非 hot path):
  `commands.py:1880` ruleset_scanner;`routes/analytics.py:12/164/176/191/219` strategy_evidence +
  strategy.repositories(backtest 端点)。连同 doctor `content.strategy_health` / `_has_pi_approval`、
  **风控 #14 闸(SDD-first,owner 已放行)**、前端 StrategyLifecycle 页 + Learning gates 一并 Phase B 删。
- **Phase C spec 诚实性**(claim 注册表,不被 pytest 执行故不报错但 matrix 略虚高):
  `spec_strategy_pool` SP-S04(strategy_designer regime targeting,已删)标废 + 迁 SP-S05/S06/S07 到 scanner
  spec;`spec_model_routing` 去 strategy_designer 引用;+ 8 份契约文档同步 + agent_contracts/SYSTEM_MAP
  的 scheduler job 表(去 strategy_designer_daily/backtest_orchestrator,加 guard_promotion)。
- **附带遗留**(harmless dead,Phase C 清):`knowledge/router.py` AgentRole Literal 含 "strategy_designer"
  路由项;`config/schema.py`/`trading/instruments.py` 注释;`config.strategy_factory.*` / event_triggers
  designer 字段(schema 保留不影响 import)。
- **保留不删**:`strategy_records`/`backtest_runs`/`backtest_trades` 表(只读 legacy,不 DROP);
  `strategy_id`/`linked_strategy_id` 列。

### 上线注意
本分支仍**未 merge master**;主仓 gateway 跑的是含完整引擎的 master。合并须在 Phase B/C 完成后一次性
FF-merge 到主仓(彼时运行时地雷已清),再主仓 restart + HTTP 实测(worktree 的 data/ 为空不能代表)。

---

## Recent Change (Strategy 模块存废 → Position B 交付 + Position C 删除进行中 — 2026-07-02)

### 背景 / 判决
多 agent 存废分析（6-lens 证据 + 3 立场 + 红队 + 综合）审视 Strategy 模块（规则引擎 vs
Agentic 决策的范式冲突）。**owner 拍板：选 C —— 整套规则引擎删掉；现在做；放行 P3（风控 #14 闸）；
代码与文档必须全程同步。** 分析报告与推理链见两份计划：
- `docs/plans/strategy_module_position_b_refactor_2026-07-02.md`（B 的 P0/P1，已交付）
- `docs/plans/strategy_engine_deletion_position_c_2026-07-02.md`（C 的删除计划 + 真实 ripple + 分阶段）

关键认知：RuleSet 在代码里是**真·因果件**（硬入场闸 + 绕过 PI 的自动平仓 + 每日 backtest 退役），
但活体效果为零（208 笔平仓 ruleset_exit=0，84 策略 0 曾到 paper_running）= "范式冲突代码上真实、
行为上休眠"的已布线未引爆层。完整性核查发现 `universe_forward_outcomes`+`calibration_stats`+Tier-1
guard **已是** scanner-family thesis 存储，别重建。

### 已交付（commit，全绿）
- **Position B P0/P1**（`8f55261`，全量 1842 passed）：P0 拆引信（移除 RuleSet 绕过 PI 的自动平仓，
  spec PL-PM-08）；P1a per-family thesis 证据喂 PI（`family_direction_edge`，DU-S10/S11）；
  P1b regime 归位 scanner-family（`cfg.universe.scanner_regime_scoring` no-op 抽取，SP-S06/S07）。
- **Position C Phase A 前半**（`b9f733f`，−457 行）：ranker + paper_trader **完全从引擎解耦**
  （去 strategy 解析/regime-fit/ruleset-entry/evidence 门/Path A；所有 `aletheia.strategy` import 移除）。
  引擎包**暂留未删** = 系统仍可完整 import = 安全中间态。ranker 10 / guards+ranker 24 绿。

### ⏳ 未完成（owner 选"A：新会话专注续做" —— 下一会话从这里接）
分支 `refactor/strategy-position-b-p0p1`（**注意：主 checkout 当前 HEAD 就在此分支**，非 master；
含 B+C 两批 commit，未 merge master）。剩余按 C 计划分阶段：
- **Phase A 后半**：scheduler（3 designer job + backtest job + `_run_strategy_designer` /
  `trigger_strategy_designer_on_demand` / `_run_backtest_orchestrator` / `_backtest_orchestrator_body`）；
  3 个 `trigger_strategy_designer_on_demand` 调用点（signal_emitter:182 / price_feed:196 /
  post_trade_reviewer:171，均 lazy import = 运行时地雷）；dreaming_agent（performance_ledger）；
  lifecycle_automation（strategy_evidence）；cli/strategy。**然后物理删** `aletheia/strategy/` 整包 +
  strategy_designer.py + analytics/strategy_evidence.py + trading/ruleset_scanner.py + ~10 测试文件。
- **Phase B**：commands.py（operator create/promote 命令面）、routes/data.py+analytics.py（strategies/
  backtest 端点）、doctor 探针（content.strategy_health / `_has_pi_approval`）、**风控 #14 闸（信任边界，
  SDD-first，owner 已放行）**、前端 StrategyLifecycle 页 + Learning 页 gates。
- **Phase C**：8 份契约文档同步（trust_and_change_control §6.1 / decision_lifecycle / agent_contracts /
  promotion_gates 废止 / risk_engine #14 / api_reference / data_model / SYSTEM_MAP / design README）
  + SDD spec 删改（spec_strategy_pool / spec_promotion_gates / spec_evaluator_contracts / backtest*）
  + test_spec_conformance 导入更新。
- **保留不删**：`strategy_records`/`backtest_runs`/`backtest_trades` 表（只读 legacy，历史可查，不 DROP）；
  `strategy_id`/`linked_strategy_id` 列（可空归因标签）。

### 不变量
交易状态一致性 > 一切；每阶段结束 import-clean + 测试绿 + commit（gateway 重启落绿 commit）；
风控其余 19 关不动只改 #14。

---

## Recent Change (动态 sizing / persona / learning 系统 P0–P4 — 2026-07-02)

### 背景
owner 定方向:仓位/杠杆/加仓/止损在风控硬边界∩persona区间内由 Operator 动态决 + Learning 拟合 +
渐进解冻。设计 `docs/design/dynamic_sizing_operator_persona_design.md`(IMPLEMENTED P0-P4)。

### 三层决策盒 + 五期(全默认 OFF = 零行为变化)
- **P0**(`3e8015f`)`active_broker_view_complete()` 视图完整性门:修 #1(sizing 读错权益)+
  #2(reconciler thrash 的 unified-新鲜但缺 broker 变体)。PL-ADOPT-19/20/21。
- **P1**(`ad580a1`)persona 区间 schema + `trading/persona.py` resolver seam(conservative==扁平配置)。
- **P2**(`9c27dc4`)`build_sizing_context()`→risk_appetite;上下文决策(仅 operator_decidable 参数)。
- **P3**(`3fbdd79`)`trading/sizing_policy.py` regime 干净窗奖励学习 appetite;doctor loop.sizing_policy。
- **P4**(`884e035`)progressive unlock(ladder+distress refreeze)+ pyramid_plan(硬顶 clamp)。
- specs PS-S01..S12(spec_persona_sizing.py);conformance 510→522;full suite 1817/0。

### 未接(owner-gated)
金字塔**实盘 scale-in 下单**(policy 已实现,下单触 execution 边界,需 owner 授权+给 max 数值)。

### 顺带
合并 PR#35(demo/live 费率等价性)后 6 个费率漂移用例失败,已对齐测试期望 0.1%→0.06%
(`d8aedeb`+`149774d`,只改测试不改 PR#35 代码)。

### 边界/开启
风控硬上限 immutable / live 人工 / Learning 只吃干净窗 / 渐进解冻+refreeze。owner 分步:
persona_sizing→跑干净窗→unlock→learning→(授权)金字塔;每步 demo+doctor 观察。

---

## Recent Change (reconciler 假孤儿 thrash 修复 — 2026-07-01)

### 背景
观察 Phase 2A+.10 做空链路时监控抓到:6 空仓 2s 内全平(reconciled_gone,均未碰 stop/tp),
5min 后以相同入场价全部 source=adopted 重开。追到代码核心 → 自维持 thrash 循环。根因分析
报告 `docs/research/ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md`(commit 3ea0005/5d6bb89)。

### 根因
非 Bitget 账户(okx_demo)上 `position_reconciler.classify_positions` 在 unified 多 broker
缓存过期(≥600s)时回退读 legacy Bitget-only 缓存 → 该缓存新鲜但空(0 持仓,status=ok)→
诚实性守卫因 status=ok 不触发 → 所有 OKX 持仓误判 orphaned → reconcile 平仓(仅本地记账不动
交易所)→ 持仓仍在 OKX → 下周期 auto-adopt 重认领 → 无限 thrash。触发窗口=exchange_sync
连续 2 次 OKX 同步超时使 unified age>600s,撞 5min reconcile 节拍。

### 损益评估
41% 仓位 reconciled_gone、56% source=adopted;PnL 数字未被粉饰(reconciled_gone 净 -0.72,
≈平),但**strategy 归因被毁**:仅 10/110 真实平仓带 strategy_id,0/116 adopted 带 strategy_id,
64% 真实平仓在 adopted 碎片上(默认 2%/4% 价位非策略价位)。owner 干净窗基本是 thrash 产物。

### 修复(SDD,commit 5d6bb89)
- **P0** classify_positions 回退改 broker 感知:仅 Bitget/live 才回退 legacy 缓存;非 Bitget +
  unified 过期 → bail 全 managed。spec PL-ADOPT-10/11/12。实测真配置 stale 路径 0 false-orphan。
- **P1** go_live_scorecard 四项交易指标排除 synthetic 平仓(新建 analytics/close_reasons.py 单一
  真源;trade_case_builder 复用)。spec AN-S03。
- 穷尽排查:缓存错配只坑 reconciler 一处(其它消费者 mode=None 默认路径已被 profit-loop 保护)。

### P2(owner 授权已实施,commit da38fbf)
- P2-1 adopted 仓交易所保护单:核实更大——OKX broker 从不下交易所 TP/SL(只 Bitget),
  `exchange_tpsl_enabled` 默认 False(休眠)。新增 OKXClient.place_algo_order + okx_broker
  place_okx_protective_orders + stops.place_exchange_protective_orders 分发 + adopt 调用。
  双门控 flag(休眠零生产影响)+ DEMO-only(不给 live 自动下单)。PL-ADOPT-13/14。
- P2-2 归因:P0 已解根问题;防御性补 adopt._recover_strategy_link(紧配近 15min reconciled_gone
  本地平仓继承 strategy_id,否则 NULL 不误归因)。PL-ADOPT-15/16。
- P2-3 factor_feedback 过滤 synthetic 平仓(is_synthetic_close),信号门不再被误教。LP-S15。
- 验证:conformance 505 全绿;full suite 1815/0;gateway restart P2 live。

### 验证
conformance 496→500 全绿;full suite 1815 passed/0 failed;gateway restart 后真配置模拟 stale 路径
0 false-orphan;doctor health.rollup healthy(reconciled_ratio 是 trailing 窗,会自愈)。

---

## Recent Change (Phase 2A+ 剩余四项收口 — 2026-07-01)

### 背景
owner 指令「尽量把 2a 完全完成,不要再遗留任何项目」。承接 Phase 2A CLOSED（`7506a04`）
与 2A+.8/.12 已修（`04ef582`/`8d3bb93`），本次把 2A+ 剩余四项 PARTIAL/PENDING 一次做完，
Phase 2A + 2A+ 代码层面全部 CLOSED（唯一剩 2A+.10 = 纯运行观察，非代码）。
commit：`f093e36`（实现）+ `8cea488`（文档收口），已 push master。

### 关键修复
- **2A+.2 designer 写 applicable_regimes**：新增 `_derive_regime_targeting(direction)`
  同时推导 direction_affinity + applicable_regimes（long→排除 bear / short→排除 bull /
  neutral→all，与 affinity gate 一致而非双重限制）。**关键陷阱**：applicable_regimes 必须写成
  **JSON 数组**——`paper_trader_ranker._check_strategy_regime_fit` 用 `json.loads`+`isinstance(list)`
  解析，逗号串会 json.loads 失败被静默丢弃使过滤失效。primary + sweep-variant 两个 create_strategy
  调用点都传。SP-S04 升级 L2a→L2b（含 ranker 真过滤断言）。
- **2A+.4 Dashboard 方向标签**：`types.ts` 加两字段；`StrategyLifecycle.tsx` 加 Direction 列
  （affinity badge）+ drawer（`fmtRegimes()` 解析 JSON 数组，malformed 不抛）。后端 GET /strategies
  已返全列。
- **2A+.9 family_quota 按 regime**：`config/schema.py` 加 `family_quota_by_regime`（4 regime 默认：
  bear 提 short_bias/derivatives、starve momentum）；`allocator.resolve_family_quota(cfg, regime_state)`
  命中覆盖、None 回退静态（向后兼容）；`service.py` 传 `current_regime.state`；settings 路由 +
  Settings UI 每 regime 一行 + 逐行 sum=1.0 前后端双校验。UP-16 L2b。
- **2A+.11 atr_4h/bb_width_4h 接 scorer**：`score_volatility` 加 4h 多时间框架确认（4h ATR>4%
  持续放量 +12 / 4h BB<3% 挤压蓄能 +12），区分 1h 噪声与真实波动 regime。UP-15 L2b（反向验证防死码）。

### 未做（有理由,非遗漏）
- **2A+.10 端到端 short 验证**：非代码——需 bear/volatile regime 真实出现观察全链路；且「bull regime
  下 short 被 gate 过滤」需 bull 出现才能验。运行观察证据留在 `data/observation/`（gitignored）。

### 验证
conformance 493→496 claims 全绿；full suite 1815 passed / 0 failed（确定序两遍）。前端 build 通过。
`aletheia doctor` health.rollup healthy。**运行态状态一律以 `aletheia doctor` 为准。**

---

## Recent Change (Scanner + Observer 审计修复 P0–P3 — 2026-07-01)

### 背景
基于两份审计报告（`docs/research/{UNIVERSE_SCANNER,MARKET_OBSERVER}_AUDIT_2026-07-01.md`）制定并实施 `docs/plans/scanner_observer_audit_fix_plan_2026-07-01.md`。宽 fan-out 核实 workflow 因 workspace rate-limit 全灭，改手动逐条 grep+读源码核实，查出报告 **6 处根因偏差**（见报告 §0）。分支 `fix/scanner-observer-audit-fixes`，spec `UP-01~13` GREEN。

### 关键修复
- **US-4（最高杠杆，一处解三处）**：`service.py:256` forward-outcome direction 改用 `scanner_direction`（与 signal_emitter 同源）+ `_infer_direction` 加 event 分支 → 消解 momentum 54% direction=None + event 0 条 forward outcome + 胜率统计双重失真。
- **US-1**：`compute_total_score` 消费 `scanner_weights`（PI 手调权重此前完全不生效）。
- **US-3/US-8**：inserted_members 补 market_type + feature 按 (symbol, market_type) 复合 key（perp 不再错标 spot）。
- **US-9**：short_enabled=False 硬拦 short（不再被 score≥66 绕过）。**US-5/US-6**：BB squeeze O(n²)→O(n)；price_precision tick-size 保真。
- **MO-1**：key_observations 进 System Operator layer1（+渲染）。**MO-2**：guard advisory 改 per-symbol（闭合学习回路断口 F）。
- **MO-3/4/6/7/8/9/11/12/13/14**：F&G 填充、scanner_family hint、RS rank 单symbol省略、crowding n/a、fallback DEGRADED 标注、news 48h 过滤、prediction 流动性过滤、load_config 移出循环。

### 未做（有理由，非遗漏）
- **US-7**（sync 失败）：`get_current_universe` 读侧已 fallback 静态池 + `run.status=failed` 已记录，无需重建。
- **US-12（all_scores 入库）/US-16（4h 特征接入 scorer）**：无消费者/属增强 → defer `ROADMAP.md`。
- **MO-5/MO-10/MO-15**：MO-1 已补信息 / FTS MATCH 已保证相关性 / 每 cycle 一次开销可忽略。

### 验证
`tests/specs/spec_universe_pipeline.py`（13 claims GREEN）+ 全量回归。**运行态状态一律以 `aletheia doctor` 为准。**

---

## Recent Change (System Operator 重命名 + earendil-works/pi 结论 + 双层模型路由 — 2026-07-01)

### 背景

本次会话完成三项工作：(1) 双层模型路由上线；(2) earendil-works/pi 研究与结论；(3) 术语统一。

### 1. 双层模型路由（commit e5a8010）

**问题**：所有 agent 共用单一 `default_provider`，顶层决策 agent 和批量数据提取 agent
用同一个模型（成本/质量都难优化）。

**实现**：
- `aletheia/config/schema.py`：`ModelsConfig` 加 `deep_provider` / `fast_provider` 两个可选字段
- `aletheia/model_provider/router.py`：新增 `get_provider_for_tier(tier)` 翻译函数
- `aletheia/agents/helpers.py`：`llm_complete` / `llm_structured` / `llm_stream` 加 `provider=` 参数
- deep 层（顶层决策）：`trading_decision.py`、`strategy_designer.py`、`risk_critic.py`、
  `market_observer.py`、`dreaming_agent.py`、`lesson_reviewer.py` 共 11 个 call site
- fast 层（批量提取）：`research_worker.py` 5 个 call site
- 前端 Settings：Routing Role 下拉（Default / Deep tier / Fast tier）
- `tests/specs/spec_model_routing.py`：5 个 MR-* SDD claims 全 GREEN

**向后兼容**：`deep_provider=None` / `fast_provider=None` → 自动降级 `default_provider`，
现有配置零迁移。

### 2. earendil-works/pi 研究结论

**结论**：earendil-works/pi（`@earendil/pi` npm 包）是**终端编程助手 CLI**（RPC/SDK/Skills
框架，让 AI 辅助编码），与 Aletheia System Operator（交易决策 + 系统监管）**完全不同职责**。

**关键核查**：
- `operator-shell/package.json`：无 `@earendil` 依赖
- `operator-shell/src/pi_runtime_adapter.ts`：调用的是 Aletheia Python `/api/operator/internal/llm-session`，
  不调 earendil npm API
- 历史"crosswalk 文档"对照的是内部计划文档 vs 双进程设计规格，不是 earendil 代码映射

**决定**：不引入 earendil-works/pi，operator-shell 完全自建（~6k TS + ~6k Python）。

### 3. 术语统一：PI Agent → System Operator

所有活文档统一用"System Operator"；`pi_daemon.py` 文件名保留（改名触 trust boundary）；
历史归档文档（`docs/design/specs/2026-06-10-pi-*.md` 等）只加文件头标注不全量改写。
决策详见 `DECISIONS.md` § 2026-07-01。

---

## Previous Change (P3d 债务核实 — 2026-06-30)

### 核实结果

grep 逐条核实 P3d MASTER_FIX_LEDGER 中标记为"未核实"的条目：

| 条目 | 结论 |
|------|------|
| §1.3 supervisor restart window | ✅ **ALREADY FIXED** — `_gateway_runner.py:383` 已用 `time.monotonic()`，原 ledger 指向旧代码 |
| BIN-XLOOP BinanceClient eager-init | ✅ **ALREADY FIXED** — `_ensure_exchange()` lazy-init 已在 `binance/client.py` |
| F6 symbol_map KeyError | ✅ **ALREADY FIXED** — `to_coingecko_id()` 已改 `.get()` 返回 None |
| dreaming_log 孤儿 | ✅ **MYTH** — 实际 0 孤儿（top 20 active check） |
| F2 POST /policy 缺失 | 低优先 — 前端不调用，走 `POST /settings/universe`；dead repo fn，无影响 |
| E5 retire 无超时 | **设计态** — recommend_retire → pi_decisions 等 PI 确认，是有意流程 |
| E3 volume_scanner 内存态 | 仍 OPEN — 重启重建，低危 |
| CR-PI-01 scanner weight 不持久化 | 仍 OPEN — 需 schema + 前端 + 调用改动，中优先 |

### 文档更新

- `docs/issues/BACKEND_ISSUES.md`: §1.3/BIN-XLOOP/F6 改为 ALREADY FIXED
- `docs/issues/_RAW_SWEEP_LEDGER.md`: BIN-XLOOP/F6 状态 → `fixed:2026-06-30-verified`
- `docs/issues/MASTER_FIX_LEDGER_2026-06-27.md`: P3d 一般 bug 列表标注三项 ALREADY FIXED

### SDD spec 状态

`spec_learning_pipeline.py` 20 claims 全 GREEN（19 passed，1 xfailed OL-2 optuna）

---

## Previous Change (Evolve 模块数据清理 + 根因修复 — 2026-06-30)

### 背景

检查 Evolve 模块（post_trade_reviewer / dreaming_agent / lesson pipeline）的数据库写入质量，
发现两个独立 bug 导致大量幻觉/无效内容写入 knowledge_items 和 ps_lessons。

### Bug 1: post_trade_reviewer flood (LP-IDEM)

**根因**: 每 4h 运行一次，每次 `list_fills(limit=200)` 取相同静态 fill 池，无幂等保护。
`knowledge_hash_exists` 拦不住（LLM 输出文本随机略变），导致 2012 条冗余 post_trade_review。

**修复** (`aletheia/agents/post_trade_reviewer.py` + `aletheia/knowledge/manager.py`):
- `_fills_window_key(strategy_id, fills)` — SHA-256 of sorted fill IDs → 16-char hex
- `_review_already_written(strategy_id, window_key)` — `tags LIKE '%window:KEY%'`
- 写入时 `tags=["window:{window_key}"]`，相同 fill 集合跳过 LLM

### Bug 2: dreaming_agent evidence void (LP-EVIDENCE)

**根因**: `_extract_lessons` 调 `store.create(evidence={})` 写空 evidence_json，
`lesson_reviewer` 拿不到数值证据 → 每次 defer → 246 条 lessons 永卡 proposed。

**修复** (`aletheia/agents/dreaming_agent.py`):
- 从 trade_case_study frontmatter 构建确定性聚合证据：
  `outcome_counts`, `avg_pnl_pct`, `factor_keys`, `scanner_families`, `case_count`
- 确定性证据与 LLM 输出 merge，确定性优先

### 数据清理（历史存量）

- 433 条重复 `trade_case_study` → archived（保留最早一条，dedup 按 content_hash）
- 246 条空 evidence `ps_lessons` (proposed, 2026-06-28 前) → archived

### SDD 更新

- `tests/specs/spec_learning_pipeline.py`: 新增 LP-IDEM + LP-EVIDENCE（共 20 claims，全 GREEN）
- `docs/issues/BACKEND_ISSUES.md`: §2.2 末尾追加两条 ✅ FIXED 记录

### 验证

```
uv run pytest tests/specs/spec_learning_pipeline.py -q
→ 19 passed, 1 xfailed
```

---

## Previous Change (issue triage + event scanner — 2026-06-30)

### Issue triage 结论

三文件全量审计结论：
- **RUNTIME_BEHAVIOR_ISSUES.md**: 基本干净，22 条全闭，残余仅 1 条 aiohttp unclosed session（监测即可）
- **BACKEND_ISSUES.md**: APP-HMAC（hmac.compare_digest）、kill-switch confirm_release、OP-WINRATE（fills→positions）均已在较早 commit 实现；CUM-ADD 被 risk engine check #6b `symbol_already_open` 覆盖，无需额外修复
- **F5 event scanner**: 唯一本会话修复（见下）

### F5 `score_event()` 实现

**位置**: `aletheia/universe/scanners.py:328`

**根因**: `score_event()` 是永远返回 `(0.0, [], None)` 的空壳，整个 event 家族的机会评分静默为 0。

**修复**: 实现基于可用 feat 字段的冲击事件检测：
- 核心信号：1h 价格跳空（+4% / +8% 两档，direction-intrinsic）
- 量能确认：`vol_ratio >= 4x/2x/1.5x` 三档加分，低于 1.5x 折半
- Taker 方向确认：`taker_buy_ratio` > 1.5 或 < 0.65
- OI 爆升协助（> 15% delta）
- 延伸 RSI 折价（> 80 / < 20 打 0.7）
- 4h 趋同小幅加分

**验证**: 强冲击（ret_1h=12%，vol×6，taker=2.0）→ event 家族 90 分成为 best_family

---

## Previous Change (xfail 全量闭合 — 2026-06-30, adc5fd7)

### 10 个 xfail claims 升级为 implemented

**代码实现（6 处）**:
- `operator_loop.py`: `_request_pi_approval_for_selfheal()` — L1 自愈前写 recommend_l1_selfheal 审批记录 (OL-C08/SH-C02)
- `go_live_scorecard.py`: `count_consecutive_zero_fill_days()` (D8-S04)、`check_kill_condition_consecutive_rounds()` (D8-S02)、`compute_pnl_by_trade_school()` (D2-S03)、`_check_profit_factor()` kill-floor guard PF<0.5→fail (D8-B01)
- `server/routes/live.py`: live_unlock 加 scorecard gate，composite_score<70 返回 HTTP 403 (D8-B02/D8-C01)
- `portfolio/position_manager.py`: `adjust_stop(position_id, new_stop_price)` — PI stop 调整路径 (RE-S05)
- `risk/engine.py`: check #6 加 trade_school 共池决策注释 (D2-C02)

**Spec 层**: 三个文件 xfail 全移除，status missing→implemented
**验证**: 63 PASS / 3 XFAIL（正确 defer: D8-B03/D2-S02/D2-B02）/ 0 FAIL
**Conformance**: 440 claims | 432 implemented | 98%

---

## Previous Change (产品定义对齐 + SDD 代码校准 — 2026-06-30)

### 八域产品定义对话
与 owner 完成 8 个功能域深度确认（结果落 `docs/plans/product_definition_2026-06-30.md`）：

- **PI Operator**：A路单 LLM 主环路（ReAct 风格），B路 council 模式 Phase 2 延后；六参考实现均确认 A路为生产标准
- **交易品种**：perp + spot 同一 PI；Serenity Lens 为独立第二赛道（xStocks + dreaming_agent）
- **数据层**：免费替代 CoinGlass/Glassnode → Coinalyze；技术指标尽量多采集；新源须在 data_utilization.md 预登记
- **学习/进化**：Ranker 权重调整 + trading_memo 两路低/中风险学习；三阶段研究计划（`docs/plans/learning_mechanism_research_plan.md`）
- **风控执行**：risk engine 保持独立最终审核；动态调整 defer Phase 2
- **自愈层**：L0/L1/L2 三档；L1 可并行交易但 PI 批准前置；L2 须人工确认
- **人机界面**：Command Center 心跳三宫格设计（`docs/plans/command_center_heartbeat_design.md`）
- **失败退出**：当前 MVP 全 demo 阶段，kill 机制等 demo 达标再讨论

### SDD 契约文档更新（5 份）
- `live_unlock_contract.md §4b`：PI 全权 + UI 警示墙三要素不变式
- `PI_OPERATOR_DESIGN_DECISIONS.md`：产品基线基准段
- `knowledge_contract.md §8`：学习安全边界（Ranker / trading_memo / 风控不可修改）
- `operator_layer.md`：自愈合同（L0/L1/L2 + PI 批准前置）
- `data_utilization.md`：Coinalyze 规划 + DU-C01/C02 登记

### GAP 修复（代码，commit 9c9862a）
- `live.py`：live_unlock → operator.permission_mode=Auto；live_lock → ReadOnly（对称；GAP-LU-01）
- `derivatives.py`：删除 `_fetch_liquidations_ccxt` + `_CCXTLiquidationsUnsupported` 死代码 87 行（GAP-DU-C02）
- `coinalyze.py`（新建）：CoinalyzeCollector，清算+OI，key-guard 模式（GAP-DU-C01）
- `operator_loop.py`：L1 自愈三动作 stub + OL-C08 xfail 守卫 PI 批准门（GAP-C08）
- `Settings.tsx`：LiveUnlockWarningWall 三要素警示墙替换单行 confirm()（GAP-LU-03）
- `registry.py`：Auto 权限模式补全

### 新增 spec claims（5 个 spec 文件，~150+ claims）
- `spec_live_unlock.py`：LU-B07/C04/C05/C06/C01b（§4b 全权 + 对称）
- `spec_operator_loop.py`：OL-S11/S12/C07/C08
- `spec_data_utilization.py`：DU-C01/C02/S04/S05
- `spec_learning_pipeline.py`：LP-S12/S13 + KLS-G02 修正
- `spec_insight_feedback_loop.py`：IFL-* 三处审计缺口

### 验证
408 claims / 99% 通过；1700 业务测试通过（Charts.tsx UI 规范违规为预先存在，非本次引入）

### 剩余（defer）
- CoinalyzeCollector scheduler 接入（config + `_async_data_collectors`）
- OL-C08 PI 批准门接通（L1 自愈动作当前为 stub）
- Command Center 心跳三宫格实现
- 学习机制三阶段研究（Phase 1 学术论文）
- B路 council 模式（Phase 2）

---

## Last Updated (prev)

---

## Recent Change (OpenAlice TaG 借鉴 P0-1 + P1-2 + 文档全量同步 — 2026-06-29)

分支 `worktree-openalice-tag-research`,5 commit:`b8ed990`(P0-1)+ `2359971`(Vibe Trading 交叉核查)+ `31eb1a9`(P1-2)+ `67ed379`(L2 design 契约同步)+ `cb4a303`(L4 status 同步)。

**研究**(13-agent workflow,797k tokens):三层 ground truth(博客原文 / 本地 `_refs/OpenAlice` 真实 TS 源码 / Aletheia 自身对照)+ 5 镜头分析 + 5 轮对抗核查。报告 `docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md`(含 Vibe Trading 帖交叉核查 §八点五)。核心发现:**TaG 真正值得借鉴的是工程纪律(reject 一等审计 + 执行后 state_after 快照),不是 git 比喻包装本身**;博客 "1 月崩盘预判" 自标 backtest 逻辑自相矛盾零证据值;"Trade Primitive auto-learn effect" 经源码 grep 命中零是纯营销话术。

**P0-1**(`b8ed990`):TradeProposal 加 `commit_hash` + `parent_hash`(16-char SHA-256 prefix,排除 `status` 与 `commit_hash` 自身后哈希所有不可变字段)+ migrations 加列 + 索引 + `insert_trade_proposal()` 事务内自动填且全局单链(按 `rowid DESC` 选 parent)。SDD:DM-S08/S09 L2a + DM-B06 L2b + DM-C02 L2c(re-compute 确定性 + 链完整性 + **status 免疫**)。不触 trust boundary,改 4 文件 +241 行。

**P1-2**(`31eb1a9`):`position_reconciler.adopt_position()` 写 `position_adopted` audit event 时,在 `after_state` 加 `commit_hash` + `parent_hash` 两字段(直接读 insert_trade_proposal 事务内填充的 proposal 对象,无需重算)。SDD:PL-ADOPT-09 L2b。改 2 文件 +99 行。**价值闭合**:P0-1 给提案打了内容指纹,P1-2 让 reconcile audit 第一个用上这个指纹——审计消费者从此单看 `audit.jsonl` 行就能还原 hash 链关联。

**A FORBIDDEN_INTENTS hardening 核查发现是 no-op**:4-agent explore workflow 发现 `deactivate_kill_switch` + `modify_permissions` **早在 2026-06-23 就加进** `agent_shell/registry.py:415-426` 并配 "Defense-in-depth double-lock" 注释,OL-C03 spec status=implemented 无 xfail,BACKEND_ISSUES §15.1 已标 ✅。`MASTER_FIX_LEDGER_2026-06-27.md` 本就没列(verifier 误指控);典型 memory `aletheia-backend-issues-archive-unreliable` 应用场景——文档标"未修"≠代码真未修,grep 优先。

**文档全量同步**(5-agent parallel workflow + 1 个 adversarial verifier):L2 design 契约(`data_model.md` 加 trade_proposals.commit_hash/parent_hash blockquote + `audit_chain_contract.md` §1 范围扩展 + §6.3 表加 position_adopted 行 + §6.4 两链对照表 + §7.3 doctor 探针 gap + `trust_and_change_control.md §1.1` 末尾加 hash chain 锁定 + `design/README.md` spec 表纠正 spec_data_model.py 8→18 + 补齐 spec_audit_chain.py / spec_pipeline_adopted_position.py 漏列);L4 status(本会话 4 文档 prepend);docs/references 同步(INDEX §3 加新研究报告 + openalice_prism_review.md 标 ✅);新建 `docs/issues/TAG_BORROW_BACKLOG.md` 列剩余借鉴清单状态。

**验证**:DM-S08/S09/B06/C02 + PL-ADOPT-09 全 GREEN;邻近 57 specs passed;全量 1802 passed / 3 skipped / 0 failed(2:08,与 baseline 一致零回归);conformance 360→361 / 99% coverage 维持;doctor `trust.audit_chain` 通过。Merge 进 master 时与 master 同日的 lesson guard fields 加列 (`activated_at`/`dry_run_until`/`expires_at` 进 ps_lessons,见下一章) 在 `_ADDITIVE_COLUMNS` 列表末尾共存,无逻辑冲突。

**剩余 TaG 借鉴清单**(`docs/issues/TAG_BORROW_BACKLOG.md`):
- P0-2 执行后 state_after 账户全量快照(`gateway.py` 已有 audit 写入点加字段;触 trust boundary,需 owner 显式批准)
- P0-3 Guard 责任链拆分(`risk/engine.py:77-332` 20 个 check 拆 `risk/guards/`;trust boundary 红线最严,需 SDD 全套行为零变化)
- P1-1 reject 与 push 同结构(三路拒绝 audit 补 hash 字段;触 trust boundary)

memory:`aletheia-tag-borrow-plan` 已同步落地状态。

---

## Recent Change (Evolve 模块 — Learning 修复 + Guards 子标签 — 2026-06-29)

**背景**：全量 Debug 批次完成后，审计 Evolve 模块下所有子功能的健康状况。

**d85f7b3 — fix(learning): 5-bug Learning module fix — settler/guard/spec/docs**：
1. Signal Settler 挂起 — `settle_due()` 缺独立 scheduler job；新增 `outcome_settler` 15min 定时任务（unconditional）。
2. Guard UI 缺失 — 新增 `POST /settings/guard` 端点 + Settings 页面 "Guard Promotion" 区域含 auto_promote_enabled 开关。
3. `spec_lesson_guards.py` — master 已有完整 4 claims（LG-S01~S04）。
4. LP spec 状态标注 — LP-S01~S06 加 explicit `status="implemented"`。
5. `docs/design/knowledge_contract.md §6.A1` — 更新为 L-Bug-B 已修复后的真实状态。

**7c21813 — fix(ui): post-trade lessons — click-to-expand drawer + trim ellipsis**：
Lessons 列表行可点击展开 DetailDrawer，正文超 120 字符显示 "…"。

**6df3999 — feat(evolve): Guards sub-tab + LessonRow TTL fields + stale comment cleanup**：
1. `client.ts:199` 陈旧注释（称 L-Bug-B 未接通）已移除。
2. `LessonRow` 类型补齐 `activated_at/dry_run_until/expires_at`；lesson 抽屉渲染 TTL 字段。
3. Knowledge Base 新增 "Active Guards" 子标签 — 接通后端 `GET /api/knowledge/guards`（之前零前端消费者）。

**Ledger 核实**（大量"待修"项实际早已修复，ledger 未同步）：
- show_errors yfinance bug、B1 ranker post_trade_review、A9 operator win_rate、L-Bug-D B2/B3 — 均已修复（代码注释明确标注）

---

## Recent Change (负反馈闭环：高维 + 可推翻 self-evolve — 2026-06-28)

分支 `worktree-neg-feedback-loop-fix`。审计发现"用交易结果调整未来交易"的 4 条通路 3 条坏；owner 定调（记忆 [[aletheia-negfeedback-high-dimensional-evolve]]）：**要高维模式归因 + 可被后续证据推翻的 self-evolve + 全自动，摒弃低维 symbol/direction/scanner 硬拦截**。设计见 `docs/plans/round2_negative_feedback_loop_closure.md`。

**根因 + 修复（5 项核心）**：
- **Path 6 反思**：`reflection.py:62/65` 重复 `opportunity_id=` kwarg → 整模块 `SyntaxError`，被调用方 `try/except` 静默吞 → record_decision/get_last_reflection 双失效、doctor 不报警。提交 5be7826（A13）引入。修=删重复行 + `test_agent_shell::test_reflection_module_imports_cleanly` import smoke 锁回归。
- **缺陷②脏数据带偏**：三学习模块用滚动 30/90 天全量、含 Round1 脏数据 → 学出"全 contra"。修=新增 `analytics/learning_window.clean_window_cutoff`（floor=2026-06-25T15:22Z），insight_synthesizer/confidence_calibration/reasoning_feedback 三处取数接入。
- **缺陷③不会自我纠错（owner 最在意）**：高维 memo 系统已全自动注入决策链，但 **Module J 缺失**——无回溯评估、memo 仅 72h 时间衰减、`MAX_DIRECTION_BIAS_STRENGTH` 死常量。修=新增 `analytics/memo_effectiveness.evaluate_prior_memo`（回溯评估上一 memo regime_playbook 预测 vs 实际→accuracy 写回 tags→overturned 注入新 memo `prior_corrections`→render 进 observer/PI prompt）+ enforce bias cap（单边占比>0.75 force balanced）。
- **缺陷①高维退化**：`current_regime`/`_current_regime` 读 macro `risk_regime` 而非 `market_regimes.state` → positions.regime_tag 全 neutral/unknown，regime_playbook 名存实亡。修=两函数优先读 market_regimes.state（fallback macro→unknown），所有 broker 经 enrichment 受益；reconciler adopt 旁路（38 NULL）留后续。
- **不做**：低维硬拦截（guard predicate / risk engine block_*），按 owner 定调。

**SDD**：新增 `tests/specs/spec_insight_feedback_loop.py`（IFL-S01/B01/B03/S02/B04/B05/S03，7 claims 全绿）+ 接入 test_spec_conformance；ROADMAP Self-Evolution Tier2/2.5 标记 DONE（修文档-代码背离）。

**附带修复（owner 追加，4 个 pre-existing 全量失败，与上面无关）**：
- `settings.py` GET /settings 漏 `serenity` section（schema+POST+前端控件已就绪，仅 GET 漏）→ 加一行。
- `repositories.py` 缺 `prune_agent_shell_{heartbeats,events,messages,artifacts}`（scheduler 调用但 ba80a62 不完整重构未实现 → cleanup AttributeError 被吞、连带 data_pruned audit 没写）→ 加 4 函数（was_in_tx 模式）。
- `test_serenity_workbench::test_promote_theme` 过时（断言旧 `draft`/`proposalCandidateId`，代码已是 Phase3 `submitted_to_pi`）→ 改测试匹配契约（代码对、测试漂移）。

**验证**：全量 1762 passed / 0 failed / 3 skipped（修复前 1758+4 failed）；architecture_fitness 11、spec_conformance 全绿；零回归。
**已合并主线**：3 commit（feat(learning) 负反馈 / fix 4 测试 / fix(operator,pi) PI 审计）+ merge master。待主仓 `aletheia gateway restart` + `doctor` 生效（worktree data/ 为空）。

---

## Recent Change (AI Berkshire 整合 — 2026-06-28)

**目标**：把 GitHub xbtlin/ai-berkshire 的价值投研框架模式吸收进 Serenity Lens。

**5 个子功能（SDD first，全部 GREEN）**：

- **S1 多透镜研究** (`087ec9d`)：4 个视角(technical/flow/sentiment/macro)顺序 LLM 调用
  替换原单次 Stage 1 调用；每条 evidence 带 `agent_lens` 字段标记来源视角。
  顺序设计因 Databricks rate limit（并发 → 429 全灭）。

- **S2 可研究性评级** (`cdad0c3`)：`assess_researchability_grade()` 确定性算法产出 A/B/C
  评级；A=≥2 high_confidence+URL，B=≥3 likely，C=其余；C 级上限封顶 score≤40，
  防止低质证据撑出高分机会。

- **S3 来源可靠性天花板** (`372fda3`)：`SOURCE_RELIABILITY_TIER` 软天花板；news/social→
  likely，market_data→high_confidence，official→disclosed；阻止来源本身可信度不足
  的证据被评为 disclosed（e.g. "看了新闻就说已公开披露"）。

- **S4 Forced Verdict** (`cdad0c3`)：每个 opportunity 必须带 `verdict_json`（含
  recommendation/bull_case/bear_case/key_assumption/invalidation）；防止 AI 空谈
  不下判断。

- **P1 key_assumptions** (`ddad0cb`)：`TradingDecision` 新增 `key_assumptions: list[str]`
  字段；PI 被要求写出≤3 条核心前提假设；落库至 `pi_decisions.key_assumptions`（JSON
  数组）；reflection.py 同步持久化。

- **U1/U2 前端显示** (`c14469e`)：SerenityLens 机会表格新增 Verdict(彩色徽章)/Grade(A/B/C)
  /可展开 bear_case；证据表格新增 Lens 列（agent_lens 标签）。

**spec 覆盖**：303 claims GREEN（ML-S01~S03/B01~B02 + SE-S06/I06 + SV-S03/S04/B02 + 迁移修正）。

**待合并主仓**：分支 `worktree-lazy-drifting-clarke`。合并后须在主仓 `gateway restart`
才能生效（worktree data/ 为空，见记忆 [[aletheia-worktree-gateway-empty-data-trap]]）。

**剩余 P3 项（数据积累后再推）**：
- D1: xStock 可研究性数据质量管理（等 researchability_grade 数据积累后再做）
- D2: evidence URL 提取增强（LLM prompt 改善，等运行数据后迭代）

---

## Recent Change (Equity Curve 误报 + 诊断代理陷阱勘误 — 2026-06-24)

**前端 bug**:`PnLChart.tsx` useQuery 未处理 `isError`,请求失败时 data 为空 →
chartData.length===0 → 走"还没平仓"空态文案,与 DB 里 68 笔平仓事实矛盾。
违反"onError 必须处理"。修复 dbde229:isError 分支显示 "Failed to load equity
curve." + Retry 按钮。

**勘误**:dbde229 commit body 把诊断时 curl 探活的 502 错误地外推为"浏览器
走系统代理被转坏",并建议用户"代理软件绕过列表加 127.0.0.1"。用户当场指出
后实测纠正:WinINET 系统代理 `ProxyEnable=0` 且 127.* 已 DIRECT,浏览器直连
无碍;只是**环境变量代理** `HTTP_PROXY=127.0.0.1:7897`+NO_PROXY 空,导致 curl
误把 localhost 丢给代理产生 502 假象。记忆 [[aletheia-localhost-probe-proxy-trap]]
已写入。以后诊断:curl 探活 localhost 必须 `--noproxy '*'`;后端死活以 gateway
status + /api/health 的 status 字段为准。

不 amend dbde229 历史,以 DEVLOG 2026-06-24 条勘误为准。

---

## Recent Change (最小赚钱循环修复 — 2026-06-23→24)

> **背景**：用户观察到"系统不怎么开仓"。逐层排查发现 PI Agent(Phase 4a 后唯一
> proposal 创建者)被**五层叠加阻碍**卡死,20h+ 零开仓。每修一层暴露下一层。
> 最终全部清除后 PI 恢复正常多空交易(02:00+ 5 trade/2 skip,3 笔持仓)。

**五层阻碍 + 修复(按发现顺序)**：
1. **OKX contractSize 单位混淆** (`650f608`)：CCXT swap `amount`/`contracts` 单位是
   合约张数非币数,放大 contractSize 倍(TRX 1000×)且绕过风控 notional。
   okx/client.py 边界换算 coins↔contracts。
2. **PI 余额幻觉破产** (`6c006cb` 冻结例外#1)：8 个 context-injection bug 喂 LLM
   错误事实(余额读 bitget 闲置$167 vs 真实 okx_demo $88k / 统计被 cleanup 污染 /
   kill_switch AttributeError / 策略 status-vs-state typo / win_rate 误读为 drawdown /
   bias 标签 / debate 公式丢 initial)。trading_context + trading_decision 修。
3. **OKX 跨 loop 回归** (`d6fc427`)：`_contract_size` 的 `load_markets` 在 `_run_coro`
   新 loop 跑,但 exchange 在 __init__ 旧 loop 建 → 跨 loop Future → contractSize
   回退 1.0 → contractSize 修复破功。OKXClient 改 lazy-init(`_ensure_exchange`)。
4. **日损 $1 误熔断** (`5831bd1` CR-RE-03)：demo 日损门控误用 `max_live_limited_cap`
   ($100×1%=$1)作基数,清账 -23.26 损失触发熔断阻断当日全部交易。engine.py:154
   按 `_is_live()` 分流,demo 用 `starting_capital_usdt`。config starting_capital→88000。
5. **signal 重复→"数据损坏"skip** (`edc15b2` 冻结例外#2 + `079bb9d`)：paper_trader 写
   signal_pending 后不标 opportunity=reviewed → 每 5min 重复入选 → PI 读重复判
   "data integrity compromised"。paper_trader.py:153 加 update_opportunity(reviewed);
   清理 1676 条历史重复;新增 prune_pi_decisions(7d,保护决策行)防堆积。

**修改文件(生产代码)**：okx/client.py / okx_broker.py / exchange_sync.py / pnl.py /
risk/engine.py(+models/migrations proposal_execution_mode 列) / strategy_lifecycle.py /
reconciliation.py(OKX/Binance live 订单告警) / paper_trader.py / trading_context.py /
trading_decision.py / instruments.py(tradable_symbols canonical) / fallback.py(_MODEL_HEALTH 锁) /
manager.py(daily_review 质量门) / market_data.py(xStock infra_event) / jsonl.py(tz 后缀+guard) /
doctor.py(_probe_profit_loop 探针) / repositories.py(prune serenity/shadow/pi_decisions) /
settings.py(HealthCard 422 修) / analytics.py(equity-curve uPnL) / 前端 Settings.tsx+client.ts

**冻结例外(3 次,用户授权,登记 EXPERIMENT_PROTOCOL)**：#1 profit-loop 8 bug /
#2 PT-1 signal 去重 / #3 CR-RE-03 日损基数。Round 1 起点保留 6/22,**所有修复
生效前的数据不计入策略评估**,6/24 起为干净观察窗口。

**监测固化**：doctor `loop.profit` 探针(opportunities 流动但 proposal 停滞 6h+ 即告警) +
`spec_pipeline_profit_loop.py` E2E。这类静默断流再不会无声发生。

**验证**：全量 1651 tests passed;生产路径 E2E(DOGE demo 单 notional $11.84 正确);
loop.profit ✓ healthy;PI 恢复 trade 决策(SOL/ETH/BTC short,notional 规格正确)。

---

## Recent Change (SDD 体系扩张 + 全面代码审计 — 2026-06-23)

> 在修 bug 之外,本会话还做了 SDD 文档体系的大规模加固 + 一次全面代码审计。

**SDD 扩张**：spec claims 45→114;新建 4 份 spec(api_reference/data_layer/operator_loop/
subsystem_observers) + 3 份契约(knowledge/scheduler/audit_chain);修 12 份契约漂移;
扩 6 份现有 spec 到 L2b/L2c。详见 `docs/research/DOC_CONTROL_ANALYSIS_2026-06-23.md`。

**代码审计**：16 契约 spec↔code 对账 + 7 管道 E2E + git log 挖掘 → 74 findings,
Top 25 修复优先级。详见 `docs/research/CODE_AUDIT_REPORT_2026-06-23.md`。已修 trust 项
(OL-C03 双锁/CR-RE-01 proposal_execution_mode/CR-EX-01 reconcile 告警/CR-DM-01 ID 决议)。
未决项分类登记 BACKEND_ISSUES §15-18(trust 待授权/frozen 待 Round2/卫生下 sprint)。

**Code review(11 角度)**：修 2 个 get_cached_state 改动遗漏(exchange_sync None 防御 +
inspect_cmd 兜底);否决 6 个 agent 误报(混淆历史 diff)。新发现 OP-WINRATE
(operator_loop fills 无 realized_pnl → win_rate 恒 0,pre-existing,Round 2) + APP-HMAC
(tunnel auth timing side-channel,待修)。

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
