# Strategy 引擎删除（Position C）执行计划

> 2026-07-02 · owner 决定：选 C（整套规则引擎删掉）+ 现在做 + 放行 P3（风控 #14），**代码与文档全程同步**。
> 承接 `strategy_module_position_b_refactor_2026-07-02.md`（P0/P1 已在分支 `refactor/strategy-position-b-p0p1` / commit 8f55261）。

## 0. 真实 ripple（比 workflow 估计大 —— 必须诚实）

`strategy_lifecycle.py` 的 `is_active_paper_strategy` 被 **5 个 observer** + ranker 用于挑 `linked_strategy_id`；`create_strategy`/`promote_strategy`/gates 被 **commands.py（operator 命令面）+ routes/data.py + doctor** 用。前端除 `StrategyLifecycle` 页外，**Learning 页**也调 `getStrategyGates`。36 个测试文件引用这些模块。

**结论**：不能一刀切。分两层删，每层结束都 import-clean + 测试绿 + 提交。

## 1. 删除边界（保留 vs 删除）

**删除（"引擎"=规则/回测/生成机器）**：
- `aletheia/strategy/`：dsl, evaluator, backtest, walk_forward, param_sweep, cost_model, review, orchestrator, baseline_strategies, models, repositories, performance_ledger, feedback（整包）
- `aletheia/trading/ruleset_scanner.py`
- `aletheia/agents/strategy_designer.py`（注册 agent）
- `aletheia/analytics/strategy_evidence.py`
- `aletheia/cli/strategy.py` 的 backtest/orchestrator 命令
- scheduler：backtest_orchestrator + strategy_designer job
- server routes：`/strategies/{id}/promote|retire|gates`、`/analytics/backtest/*`、`/analytics/strategy-evidence`、`/analytics/backtest-paper-drift`
- 前端：`pages/StrategyLifecycle.tsx` + route + nav + 相关 api client 方法；Learning 页去掉 gates 面板

**保留（附着物 + 历史）**：
- `strategy_records` / `backtest_runs` / `backtest_trades` 表 → **只读 legacy 保留**（不 DROP，历史可查；完整性缺口 #4）
- `positions/orders/trade_proposals/opportunities.strategy_id` / `linked_strategy_id` 列 → 保留为可空归因标签
- `strategy_lifecycle.py`：**保留最小 attribution 助手**（`is_active_paper_strategy`/`is_paper_trading_paused` 查 legacy 表；无活跃策略时观察者自然写 NULL → 归因回落到 scanner_family）；FSM 晋级函数按 Phase B 处理

## 2. 分阶段执行（每阶段 import-clean + 测试绿 + commit）

### Phase A — 删规则/回测/生成核心（决策链解耦）
1. ranker：去 `_check_ruleset_entry`+dsl/evaluator import、strategy_evidence 门（`_classify_evidence_status`）、`strategy_has_ruleset` +15、`_check_strategy_regime_fit`（regime 已归 scanner-family P1b）。strategy 解析降为可选 attribution。
2. paper_trader：删 Path A（ruleset entry→signal_pending）+ ruleset_scanner import；只留 Path B（opportunity-driven）。
3. scheduler：删 backtest_orchestrator + strategy_designer 两个 job。
4. dreaming_agent / lifecycle_automation：去 performance_ledger / strategy_evidence 依赖。
5. cli/strategy.py：删 backtest/orchestrator 命令。
6. 删 `strategy_designer.py`、`analytics/strategy_evidence.py`、`trading/ruleset_scanner.py`、`aletheia/strategy/` 整包。
7. import-check + 删对应测试（test_strategy_dsl/evaluator? /backtest/walk_forward/param_sweep*/orchestrator/evidence/backtest_gate/backtest_paper_drift/strategy_causal_chain 等）。

### Phase B — FSM + 命令面 + 路由 + 前端 + 风控 #14（P3）
1. commands.py：删 create_strategy/promote_strategy/gates 命令处理（operator 命令面）。
2. routes/data.py + analytics.py：删 strategy/backtest 端点。
3. doctor.py：`content.strategy_health` 探针改为 legacy-only 或移除；去 `_has_pi_approval` 依赖。
4. risk/engine.py **#14 闸（信任边界，SDD-first）**：`strategy 须 live_candidate/live_limited` → `scanner_family 在干净窗有 ≥N 证据且 WR≥门槛`（复用 P1a `family_direction_edge`）。先改 spec 再改代码。
5. 前端：删 StrategyLifecycle 页/route/nav + api client 方法；Learning 页去 gates。
6. strategy_lifecycle.py：收缩为 attribution 助手（保留 is_active_paper_strategy 查 legacy）。

### Phase C — 文档全同步（owner 红线）+ SDD
- 契约：trust_and_change_control §6.1、decision_lifecycle_contract、agent_contracts、promotion_gates_contract（删/标记废止）、risk_engine_contract（#14 改写）、api_reference（删端点）、data_model（表标记 legacy）、SYSTEM_MAP（scheduler job 表）、design/README。
- SDD：删/改 spec_strategy_pool、spec_promotion_gates、spec_evaluator_contracts、spec_backtest*；更新 test_spec_conformance 导入；保留 SP-S05(short_bias 可达)/SP-S06/S07(scanner regime) 迁到 scanner spec。

### Phase D — 终验
全量 pytest + aletheia doctor（无 dangling/假警）+ 前端 build + conformance matrix + 完整性复核。

## 3. 操作风险（gateway 重启）
本分支在主 checkout；watchdog（5min）/git hooks 可能把 gateway 重启到半删状态 → 崩。缓解：**消费者先解耦、模块后删**（任一保存态不留 dangling import）；每 Phase 结束 import-check + commit（重启落到绿 commit）。若中途 owner 观察到 gateway 异常，属预期，Phase 完成即恢复。

## 4. 不变量（守住）
- 交易状态一致性 > 一切。删除不碰 `positions/orders/fills` 数据。
- `strategy_id` 列保留可空；legacy 表不 DROP。
- 风控其余 19 关不动；只改 #14。
- 每阶段结束系统可 import + 测试绿。
