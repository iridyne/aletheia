# 文档地图（按功能域）— Documentation Atlas by Functional Domain

> **Frozen evidence**: this is a point-in-time documentation snapshot, not a
> current status map or work queue. Verify current behavior through Product
> Truth, current Contracts, code, GitHub, and runtime facts.

> **用途**：以**功能域**为切口索引全部文档——与 [README](../README.md)（按契约对象）、[SYSTEM_MAP](../architecture/SYSTEM_MAP.md)（按模块/管道）、[DOC_SYSTEM_PLAN](../../product/documentation/DOC_SYSTEM_PLAN.md)（按金字塔层 L0–L4）互补，是第四种导航视角。
> **状态标记**：✅金标准 / 🟡契约(部分实现) / 🔄in-flight / ⏳planned / ⛔open-gap / 📦process-artifact。历史状态对账见归档的 [REMAINING_WORK_2026-06-30](../../archive/legacy/status/snapshots/REMAINING_WORK_2026-06-30.md)。
> Last synced for D2/D6/D10/D12: 2026-07-19. Other domain status remains point-in-time and must be verified against its contract and `aletheia doctor`.

---

## 功能域总览（12 域）

| # | 功能域 | 一句话 | 契约成熟度 |
|---|--------|--------|-----------|
| D1 | 数据采集与处理 | 行情/衍生品/链上/宏观 → 快照表 → 上下文 | ✅ 齐全 |
| D2 | 开仓决策链 | Opportunity generation → durable carrier → System Operator → Proposal | ✅ 契约与可执行 claims 对齐 |
| D3 | 平仓与持仓管理 | stop/TP/trailing/max-hold/退出 | ✅ 齐全(本会话补 §10) |
| D4 | 风控与执行（信任边界） | 20 关风控 + Gateway 唯一通路 + 审计链 | ✅✅ 最强 |
| D5 | 策略生命周期 | 策略池 → 晋级门 → 退役 | 🟡 有契约，**核心执行缺口(2A)** |
| D6 | 知识·学习·自进化 | outcome → hypothesis/exposure/verdict + lesson/guard/memo → 反哺决策 | 🟡 A1 测量事实已落地，owner observability 待实现 |
| D7 | 系统监管 / Operator | MAPE-K 控制面 + 命令注册表 | 🟡 概念契约，TS 实现领先文档 |
| D8 | 持久化·同步·恢复 | 重启不丢单/仓/经验 | 🟡 有 L2 契约，恢复 FSM 尚未接 runtime |
| D9 | 运行时与稳定性 | 调度/自愈/连通性/Provider | ✅ scheduler 契约，余为 ops/plan |
| D10 | 前端·Command Center·配置 | Web UI 唯一入口 + Config↔UI | ✅ 设计系统契约，有诚实债 |
| D11 | 子系统观察者 | Serenity / xStocks / 预测市场 | 🟡 契约，Serenity 已大量实现 |
| D12 | 治理与文档体系（meta） | VISION/红线/SDD/金字塔 | ✅ 强 |

---

## D1 · 数据采集与处理

**Scope**：collectors（volume/macro/derivatives/hyperliquid/microstructure/defillama/coingecko）→ `ccxt_provider` → `market_snapshots / funding / oi / microstructure / macro` 表 → `regime` 检测 + `universe` 选标的 + `analytics` 因子。

- **L2 契约**：[`data_model`](../data/data_model.md) ✅(~80 表+52 Pydantic) · [`data_utilization`](../data/data_utilization.md) ✅(铁律:无消费者数据=负债) · [`data_layer`](../data/data_layer.md) 🟡(CCXT provider/symbol 格式；README 标 L2 但内容偏 L3 计划)
- **Spec**：`spec_data_model`(18) · `spec_data_utilization` · `spec_data_layer`(10) · `spec_macro_signals` · `spec_hyperliquid` · `spec_defillama` · `spec_pipeline_market_data`
- **L3 计划**：`round2_immediate_data_integration_plan`✅done · `free_data_sources_implementation_plan`✅mostly(Hyperliquid/DefiLlama 已接) · `UNIVERSE_EDGE_PLAN` · `universe_generator_report`
- **研究/issue**：`DATA_FLOW_AUDIT` · `SCANNER_ARCHITECTURE_RESEARCH` · `market_context_eval` · `JUNK_DATA_FULL_SWEEP`(数据质量清扫)
- **当前状态**：健康。CoinGlass/Glassnode 实测全 paywalled→observation-only(memory)；data_utilization 契约锁死每个数据域必有消费者或显式 observation-only。

## D2 · 开仓决策链

**Scope**：producer 生成 snapshot-scoped `open Opportunity` → `paper_trader_ranker` 确定性打分/过滤 → 原子认领为 `queued Opportunity + signal_pending` 耐久载体 → `pi_daemon` → 3 层 Context → `trading_decision`（System Operator 唯一定夺）→ Proposal。`open` generation 与 `queued` carrier 是不同生命周期身份。

- **L2 契约**：[`decision_lifecycle_contract`](../contracts/decision_lifecycle_contract.md) ✅（横切脊柱、载体与出口权威） · [`filter_evidence_contract`](../contracts/filter_evidence_contract.md) ✅（逐关事实与 typed terminal reason） · [`agent_contracts`](../contracts/agent_contracts.md) ✅（producer/Ranker/Operator 责任） · [`api_reference`](../api/api_reference.md) ✅（owner-facing projection） · [`PI_OPERATOR_DESIGN_DECISIONS`](../operator/PI_OPERATOR_DESIGN_DECISIONS.md) 🟡
- **Spec**：`spec_decision_lifecycle` · `spec_opportunity_lifecycle_continuity` · `spec_operator_low_conviction_defer` · `spec_filter_evidence` · `spec_pipeline_paper_trade` · `spec_pi_decision_chain` · `spec_api_reference`。全部由 `test_spec_conformance` 注册；不再维护易漂移的手工 claim 数量。
- **L3 计划**：`pi_agent_sole_decision_maker`(Phase 4a 已落地) · `pi_agent_context_enhancement`
- **研究证据**：[`OPPORTUNITY_END_TO_END_LIFECYCLE_AUDIT_2026-07-18`](../../research/audits/OPPORTUNITY_END_TO_END_LIFECYCLE_AUDIT_2026-07-18.md) 解释旧 snapshot-coupled carrier 根因；研究报告不覆盖上述 L2 契约。
- **当前状态**：决策权 = System Operator LLM；Ranker 无 LLM。Universe supersede 只结束未认领 `open` generation；`queued` carrier 由自身 `valid_until`/Operator 终局结束。Overview/API/CLI 分开报告 generation、unique setup、snapshot supersede 与 true TTL。

## D3 · 平仓与持仓管理

**Scope**：`position_manager.evaluate_open_positions`(stop/TP/trailing/max-hold) + `exit_rules`(market-aware) + `close_position_routed`(唯一平仓中枢) + `kill_switch`/`paused` 对平仓语义。

- **L2 契约**：[`execution_contract §10`](../contracts/execution_contract.md) ✅**(本会话新建——退出触发器优先级/路由/paused 不变量)**
- **Spec**：`spec_pipeline_position_manager`(7,含 PL-PM-07 paused 不挡平仓) · `spec_pipeline_stop_propagation`
- **当前状态**：本会话前是二等公民、现已补；paused 已对齐 kill_switch(都挡开仓不挡平仓)。⛔ **live 人工平仓路径未实现**(`allow_live=True` 无调用点，BACKLOG，触信任边界)。

## D4 · 风控与执行（信任边界）★最强

**Scope**：`risk/engine`(20 关) + `risk/kill_switch` + `execution/gateway`(唯一下单通路) + 4 brokers + `execution/reconciliation` + `trust/guard_chain`(advisory) + `server/routes/live`。

- **L2 契约**：[`risk_engine_contract`](../contracts/risk_engine_contract.md) ✅(本会话 16→20 关+guard 澄清) · [`execution_contract`](../contracts/execution_contract.md) ✅ · [`live_unlock_contract`](../contracts/live_unlock_contract.md) ✅ · [`audit_chain_contract`](../contracts/audit_chain_contract.md) ✅ · [governance/`trust_and_change_control`](../../governance/policies/trust_and_change_control.md)(权威红线) · [governance/`execution_path_honesty`](../../governance/policies/execution_path_honesty.md)(三分诚实)
- **Spec**：`spec_risk_engine`(含 RE-S04) · `spec_execution`(含 EX-C03 guard 脱钩锁) · `spec_live_unlock` · `spec_audit_chain` · `spec_pipeline_risk_gate` · `spec_pipeline_execution_chain` · `spec_pipeline_live_unlock` · `spec_futures_leverage`(18,守 check#17/18) · `spec_pipeline_adopted_position`(含 TaG hash)
- **历史研究**：[`TAG_BORROW_BACKLOG`](../../archive/legacy/issues/backlog/TAG_BORROW_BACKLOG.md) 记录 OpenAlice TaG 借鉴取舍；已采纳的 Proposal hash chain 与 adopted-position 审计链以当前 data/audit/trust 契约为准，其余快照、reject 复制链和 Guard 结构重排不进入当前 roadmap。
- **当前状态**：demo/paper 是当前支持面；OKX/Binance/Bitget 私有成交对账代码链已实现并有 focused tests，live execution 整体仍保持 Experimental，启用需 owner 显式授权。

## D5 · Legacy Strategy / Promotion

**Scope**：legacy `strategy_records` / `backtest_*` 表、promotion gate 文档和历史归因展示。规则引擎、`strategy_designer`、`backtest_orchestrator`、RuleSet 扫描均已删除(Position C, 2026-07-02)。

- **历史契约**：[`promotion_gates_contract`](../history/promotion_gates_contract.md)（legacy-only；无生产路径 wire 到晋级）
- **Spec**：`spec_promotion_gates` · `spec_strategy_pool`(Position C 后保留 legacy/negative tripwires)
- **L3 计划**：历史 `PLAN_REGIME_STRATEGY_SHORT` / `strategy_lifecycle_analysis` 仅作背景，不代表当前开发方向
- **当前状态**：规则引擎删除后，交易方向来自 System Operator 对 opportunity 的决策；`strategy_id` 只作可空历史归因标签。不要恢复策略 DSL/backtest/RuleSet 路径，除非 owner 明确开启新设计。

## D6 · 知识·学习·自进化

**Scope**：`knowledge/` artifact/retrieval/guard paths + measured `learning/` registry,
hypothesis/experiment/exposure/verdict/lifecycle/health paths + canonical candidate outcomes +
observer/ranker/Operator/sizing consumers + owner-facing API/UI projection。

- **当前契约与未来设计**：[`learning_contract`](../contracts/learning_contract.md) current behavior ·
  [`learning_evolution`](../proposals/learning_evolution.md) Proposal ·
  [`learning_observability`](../proposals/learning_observability.md) Proposal ·
  [`knowledge_contract`](../contracts/knowledge_contract.md)
  artifact taxonomy/FTS/two-store paths.
- **Spec**：`spec_learning_{registry,attribution,ledger,contribution,authority,lifecycle,continuity,verdict_runtime,final_consumers}`
  + `spec_learning_pipeline` + adjacent lesson/guard/factor/outcome specs. Owner projection is
  `spec_learning_observability` (`LCO-*`, strict xfail/missing). The direct conformance matrix must
  import this complete family; do not rely on incidental collection order.
- **历史实施证据**：归档的
  [`learning_positive_contribution_implementation_plan_2026-07-12`](../../archive/legacy/plans/learning/learning_positive_contribution_implementation_plan_2026-07-12.md)
  与
  [`universe_family_calibration_execution_plan_2026-07-24`](../../archive/legacy/plans/learning/universe_family_calibration_execution_plan_2026-07-24.md)
  保存交付顺序和当时的运行 checkpoint；当前语义只由上述 contracts/proposals
  决定。`self_evolution_plan` 和更早修复计划同样只是历史证据。
- **Issue**：`LCO-BE-01` / `LCO-FE-01` are the current owner-projection gaps; older
  `LEARNING_LOOP_AUDIT` remains historical evidence.
- **Current state**：A1 has permanent ledger, full-arm exact-key complete-cluster measurement,
  append-only metric history, lifecycle recommendation, one evaluator + catch-up and durable
  failure identity. Public UI/API still shows artifacts/activity rather than those measured facts.
  `paper_influence_enabled` and lifecycle automation remain default-off. A POST admission route
  exists in code, but it is not yet aligned with the authenticated-owner contract/spec boundary;
  it remains unavailable for an authorized experiment until that drift is repaired and proven.

## D7 · 系统监管 / Operator

**Scope**：`operator-shell/`(TS, MAPE-K: heartbeat→recommendation→repair) + `agent_shell/operator_loop`(Python, 收敛为 config 调参) + `agent_runtime/pi_daemon`(交易决策 daemon)。三组件别混。

- **L2 契约**：[`operator_layer`](../operator/operator_layer.md) 🟡(概念设计+3组件警告框) · [`PI_OPERATOR_DESIGN_DECISIONS`](../operator/PI_OPERATOR_DESIGN_DECISIONS.md) 🟡(本会话补 §问题2 action 命名空间不变式)
- **Spec**：`spec_operator_loop`(含 OL-S12 两环判别锁) · `spec_operator_contract_parity` · `spec_operator_single_source` · `spec_pipeline_operator_loop`(1 partial claim)
- **历史实施证据**：归档的 `operator_self_supervision`、
  `aletheia_pi_agent_implementation_plan` 和 `ai_native_bugfix_path`；当前职责、命名和
  安全边界只由 `operator_layer` 与可执行 specs 决定。
- **design/specs/(冻结)**：`2026-06-10-pi-operator-agent-{design-audit,crosswalk,dual-process-design}` · `2026-06-07-merge-postmortem`
- **历史审计**：归档的
  [`PI_AGENT_SDD_AUDIT_2026-06-28`](../../archive/legacy/issues/audits/PI_AGENT_SDD_AUDIT_2026-06-28.md)
  记录三组件误判及 cadence / Python 控制链收敛过程；不能作为当前 Issue 清单。
- **当前状态**：TS operator-shell 是真正 apex(领先 Python 文档)；两环边界本会话已形式化。⚠️ TS 行为无 spec、Stage 1 未正式"完成"(更深 PI runtime 待补)。

## D8 · 持久化·同步·恢复

**Scope**：`gateway/`(state_machine/state_store/locks/session/reconciliation/recovery) + `storage/`(event_store/artifact_store) + `agents/checkpoints` + `sync/` + `backup/` + `policies/`。

- **当前契约与未来设计**：[`persistence_recovery_contract`](../contracts/persistence_recovery_contract.md) 仅覆盖当前持久化行为 · [`gateway_recovery`](../proposals/gateway_recovery.md) 保存未接通恢复设计 · `audit_chain_contract` / `execution_contract §6` 覆盖相邻信任边界
- **历史实施证据**：归档的
  [`Aletheia_Persistence_Sync_Recovery_IMPLEMENTATION_PLAN`](../../archive/legacy/plans/engineering/Aletheia_Persistence_Sync_Recovery_IMPLEMENTATION_PLAN.md)
  保存早期总体设计；当前行为看 `persistence_recovery_contract`，尚未接通的安全目标看
  `gateway_recovery`，不得从旧计划的模块清单推断完成。
- **当前状态**：⛔ 底层(SQLite/JSONL/reconcile)实际在跑，但上层协调(RECOVERY_MODE 状态机/SAFE_FREEZE 禁开仓/Checkpoint 跨机迁移/Policy versioning)**未接进 gateway**，需显式授权。"文档比代码雄心大"。

## D9 · 运行时与稳定性

**Scope**：`agent_runtime/`(scheduler/orchestrator/health/connectivity/stability_monitor/startup_gate/resume_gate/auto_resume) + `gateway/`(lifecycle/frontdoor/tunnel) + `model_provider/`(router/多 provider) + 确定性自愈(无 LLM)。

- **L2 契约**：[`scheduler_contract`](../contracts/scheduler_contract.md) ✅(job 清单/频率/执行器分组/故障隔离)
- **Spec**：`spec_gateway_stability`(market_observer wall-clock 预算+有界重试) · scheduler 锚点在 `spec_agent_contracts`
- **L3/ops**：ROADMAP Cross-Cutting「确定性自愈 A」✅done + 「Model Provider Fallback」⏳规划中(未实施) · operations:[`RUNBOOK`](../../operations/runbooks/RUNBOOK.md) · [`STABILITY_TARGET_ARCHITECTURE`](../../operations/architecture/STABILITY_TARGET_ARCHITECTURE.md) · [`PI_RUNTIME_CONTINUITY_SOP`](../../operations/runbooks/PI_RUNTIME_CONTINUITY_SOP.md)
- **issue**：`RUNTIME_BEHAVIOR_ISSUES`
- **当前状态**：自愈层 done；Windows SelectorEventLoop/frontdoor 卡死/market_observer 线程泄漏等顽疾已根治(memory)；⏳ Model Provider 热切换待做(现 LLM outage→autopause)。

## D10 · 前端·Command Center·配置

**Scope**：`frontend/`(React/Vite,单端口 8787) + Command Center(operator 交互面) + `config/schema` + Config↔UI 铁律。

- **L2 契约**：[`ui_design_system`](../frontend/ui_design_system.md) ✅(设计语言/密度) · [`api_reference`](../api/api_reference.md) ✅(REST 端点/trust 义务)
- **历史设计证据**：旧 redesign/page-map/UX audit/本地 issue ledger 统一保存在 [`docs/archive/legacy/`](../../archive/legacy/)；不再作为当前 roadmap。
- **当前问题跟踪**：UI honesty 与 Config↔Runtime 断链按单项进入 GitHub Issues（当前集中在 #393–#410），不维护第二套本地 backlog。
- **当前状态**：Overview/Blotter 共用 current-entry read model；Learning Contribution 已有 bounded API、typed UI consumer 与 focused tests。共享 types、DataTable、DetailDrawer、状态人性化等基础设施已落地；具体页面能力仍以 Capability Ground Truth 与当前 Issue 为准。

## D11 · 子系统观察者

**Scope**：Serenity(深研产业链长持流派) · xStocks/美股(equity_observer) · 预测市场(polymarket/kalshi)。均"观察+经 PI/风控执行"，不绕过。

- **L2 契约**：[`EQUITY_INTEGRATION_DESIGN`](../market-systems/EQUITY_INTEGRATION_DESIGN.md) 🟡(xStocks) · [`PREDICTION_MARKET_DESIGN`](../market-systems/PREDICTION_MARKET_DESIGN.md) 🟡(观察阶段)
- **Spec**：`spec_subsystem_observers`(8) · `spec_spot_parity` · **Serenity 8 件**：`spec_serenity_{evidence,attribution,promote,holding,live_readiness,verdict,researchability,multi_lens}`(全注册+绿)
- **L3 计划**：`serenity_execution_path`✅已实现(5 phase) · `serenity_aiberkshire_integration_plan`✅P0/S1/P2 · `serenity_lens_implementation_plan` · `serenity_instrument_roadmap` · `prediction_market_lens_report`
- **研究**：`SERENITY_PHILOSOPHY_AND_INTEGRATION_ANALYSIS` · `DEMO_LIVE_SPOT_PARITY_EVENT` · `AI_BERKSHIRE_INTEGRATION_ANALYSIS`(issue)
- **当前状态**：Serenity 大量已实现(本会话对账纠正了"无代码"误报)。⏳ spot-parity 分支 2 commit 待 owner 审核合并。

## D12 · 治理与文档体系（meta）

**Scope**：愿景/红线/度量/SDD 方法/金字塔/协作流程——统领其余 11 域。

- **L0–L1**：[`product/VISION`](../../product/definition/VISION.md) · `PRD` · `METRICS` · `PHASE_GATES` · `EXPERIMENT_{PROTOCOL,MVP_GATE,BACKLOG}`；`DOC_SYSTEM_PLAN` 是已执行迁移计划，不是当前状态页
- **历史状态证据**：[`ROADMAP`](../../archive/legacy/status/roadmap/ROADMAP.md) · `WORLD_MAP` · `PIPELINE_STATUS` · `REMAINING_WORK_2026-06-30`；均已归档为 point-in-time evidence
- **方法/导航**：[`docs/README`](../../README.md)(总入口) · [`design/README`](../README.md)(契约索引) · [`SDD_METHODOLOGY`](SDD_METHODOLOGY.md) · [`SYSTEM_MAP`](../architecture/SYSTEM_MAP.md) · **本文(域索引)**
- **治理**：[governance/`trust_and_change_control`](../../governance/policies/trust_and_change_control.md) · `implementation_guide`
- **协作过程**📦：外部开发编排器持有当前 session/task/handoff/worktree 状态；可复用 playbook 位于 `governance/` 与 `operations/`，旧 Agent 记录统一归档在 `archive/legacy/ai-context/`
- **参考系**：`references/`(north_star/competitors_and_papers/agent_framework_research/FRAMEWORK_MODULE_MAP/INDEX/prism_extraction/openalice_prism_review)
- **问题跟踪**：GitHub Issues 是唯一 live tracker；仓库内旧 Backend/Frontend/Human/Fix/Ledger 文档统一进入 `docs/archive/legacy/issues/`，只作历史证据
- **当前状态**：权威链统一为 `AGENTS → governance/product → design contract → tests/specs → conformance`；运行事实独立由 `doctor`/日志/DB 提供，计划、issue、audit 和旧 agent ledger 不得覆盖当前契约或运行事实。

---

## 跨域观察（从这张地图看出的结构性事实）

1. **契约覆盖不均**：交易核心(D2/D3/D4)金标准齐全；D8 已有部分实现契约但恢复 FSM 未接 runtime，D5 有 legacy 契约但无生产晋级路径。两者都是“契约存在、能力未闭环”，不能再误报成“没有文档”。
2. **文档↔代码方向倒挂的两类**：D7(TS operator 代码领先文档) 与 D11(Serenity 代码领先文档/plan)——都是"代码做了文档没追"，与 D12 的 ledger-lag 同源。
3. **跨域 seam 已补**：`decision_lifecycle_contract` 是横切 D1→D2→D3→D4 的权威契约；Opportunity carrier continuity 由其中 DL-INV-25 与对应可执行 claims 锁定。
4. **每个域的"真相源"**：状态看 `aletheia doctor` + conformance matrix；意图看本域 L2 契约；历史看 issues ledger(**不可当当前状态读**)。
</content>
