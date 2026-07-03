# 文档地图（按功能域）— Documentation Atlas by Functional Domain

> **用途**：以**功能域**为切口索引全部文档——与 [README](README.md)（按契约对象）、[SYSTEM_MAP](SYSTEM_MAP.md)（按模块/管道）、[DOC_SYSTEM_PLAN](../product/DOC_SYSTEM_PLAN.md)（按金字塔层 L0–L4）互补，是第四种导航视角。
> **状态标记**：✅金标准 / 🟡契约(部分实现) / 🔄in-flight / ⏳planned / ⛔open-gap / 📦process-artifact。状态已并入 [REMAINING_WORK_2026-06-30](../status/REMAINING_WORK_2026-06-30.md) 的对账结论。
> Last built: 2026-06-30

---

## 功能域总览（12 域）

| # | 功能域 | 一句话 | 契约成熟度 |
|---|--------|--------|-----------|
| D1 | 数据采集与处理 | 行情/衍生品/链上/宏观 → 快照表 → 上下文 | ✅ 齐全 |
| D2 | 开仓决策链 | opportunity → PI 定夺 → proposal | ✅ 齐全(本会话补脊柱契约) |
| D3 | 平仓与持仓管理 | stop/TP/trailing/max-hold/退出 | ✅ 齐全(本会话补 §10) |
| D4 | 风控与执行（信任边界） | 20 关风控 + Gateway 唯一通路 + 审计链 | ✅✅ 最强 |
| D5 | 策略生命周期 | 策略池 → 晋级门 → 退役 | 🟡 有契约，**核心执行缺口(2A)** |
| D6 | 知识·学习·自进化 | lesson/guard/memo → 反哺决策 | 🟡 有契约，闭环大部分活 |
| D7 | 系统监管 / Operator | MAPE-K 控制面 + 命令注册表 | 🟡 概念契约，TS 实现领先文档 |
| D8 | 持久化·同步·恢复 | 重启不丢单/仓/经验 | ⛔ **无 L2 契约**，上层未接通 |
| D9 | 运行时与稳定性 | 调度/自愈/连通性/Provider | ✅ scheduler 契约，余为 ops/plan |
| D10 | 前端·Command Center·配置 | Web UI 唯一入口 + Config↔UI | ✅ 设计系统契约，有诚实债 |
| D11 | 子系统观察者 | Serenity / xStocks / 预测市场 | 🟡 契约，Serenity 已大量实现 |
| D12 | 治理与文档体系（meta） | VISION/红线/SDD/金字塔 | ✅ 强 |

---

## D1 · 数据采集与处理

**Scope**：collectors（volume/macro/derivatives/hyperliquid/microstructure/defillama/coingecko）→ `ccxt_provider` → `market_snapshots / funding / oi / microstructure / macro` 表 → `regime` 检测 + `universe` 选标的 + `analytics` 因子。

- **L2 契约**：[`data_model`](data_model.md) ✅(~80 表+52 Pydantic) · [`data_utilization`](data_utilization.md) ✅(铁律:无消费者数据=负债) · [`data_layer`](data_layer.md) 🟡(CCXT provider/symbol 格式；README 标 L2 但内容偏 L3 计划)
- **Spec**：`spec_data_model`(18) · `spec_data_utilization` · `spec_data_layer`(10) · `spec_macro_signals` · `spec_hyperliquid` · `spec_defillama` · `spec_pipeline_market_data`
- **L3 计划**：`round2_immediate_data_integration_plan`✅done · `free_data_sources_implementation_plan`✅mostly(Hyperliquid/DefiLlama 已接) · `UNIVERSE_EDGE_PLAN` · `universe_generator_report`
- **研究/issue**：`DATA_FLOW_AUDIT` · `SCANNER_ARCHITECTURE_RESEARCH` · `market_context_eval` · `JUNK_DATA_FULL_SWEEP`(数据质量清扫)
- **当前状态**：健康。CoinGlass/Glassnode 实测全 paywalled→observation-only(memory)；data_utilization 契约锁死每个数据域必有消费者或显式 observation-only。

## D2 · 开仓决策链

**Scope**：`market_observer`(LLM→Opportunity) → `paper_trader_ranker`(确定性打分) → `signal_pending` → `pi_daemon` → 3 层 Context → `trading_decision`(LLM 定夺，conviction≥55) → proposal。

- **L2 契约**：[`decision_lifecycle_contract`](decision_lifecycle_contract.md) ✅**(本会话新建——横切脊柱 seam + 中间载体字段)** · [`agent_contracts`](agent_contracts.md) ✅(market_observer/paper_trader/PI 行) · [`PI_OPERATOR_DESIGN_DECISIONS`](PI_OPERATOR_DESIGN_DECISIONS.md) 🟡
- **Spec**：`spec_decision_lifecycle`(5,新) · `spec_pi_decision_chain` · `spec_pipeline_paper_trade` · `spec_pipeline_entry_enrichment` · `spec_agent_contracts`(8)
- **L3 计划**：`pi_agent_sole_decision_maker`(Phase 4a 已落地) · `pi_agent_context_enhancement`
- **当前状态**：决策权 = PI LLM(已确认)；脊柱本会话前无单一契约、现已补。无遗留大缺口。

## D3 · 平仓与持仓管理

**Scope**：`position_manager.evaluate_open_positions`(stop/TP/trailing/max-hold) + `exit_rules`(market-aware) + `close_position_routed`(唯一平仓中枢) + `kill_switch`/`paused` 对平仓语义。

- **L2 契约**：[`execution_contract §10`](execution_contract.md) ✅**(本会话新建——退出触发器优先级/路由/paused 不变量)**
- **Spec**：`spec_pipeline_position_manager`(7,含 PL-PM-07 paused 不挡平仓) · `spec_pipeline_stop_propagation`
- **当前状态**：本会话前是二等公民、现已补；paused 已对齐 kill_switch(都挡开仓不挡平仓)。⛔ **live 人工平仓路径未实现**(`allow_live=True` 无调用点，BACKLOG，触信任边界)。

## D4 · 风控与执行（信任边界）★最强

**Scope**：`risk/engine`(20 关) + `risk/kill_switch` + `execution/gateway`(唯一下单通路) + 4 brokers + `execution/reconciliation` + `trust/guard_chain`(advisory) + `server/routes/live`。

- **L2 契约**：[`risk_engine_contract`](risk_engine_contract.md) ✅(本会话 16→20 关+guard 澄清) · [`execution_contract`](execution_contract.md) ✅ · [`live_unlock_contract`](live_unlock_contract.md) ✅ · [`audit_chain_contract`](audit_chain_contract.md) ✅ · [governance/`trust_and_change_control`](../governance/trust_and_change_control.md)(权威红线) · [governance/`execution_path_honesty`](../governance/execution_path_honesty.md)(三分诚实)
- **Spec**：`spec_risk_engine`(含 RE-S04) · `spec_execution`(含 EX-C03 guard 脱钩锁) · `spec_live_unlock` · `spec_audit_chain` · `spec_pipeline_risk_gate` · `spec_pipeline_execution_chain` · `spec_pipeline_live_unlock` · `spec_futures_leverage`(18,守 check#17/18) · `spec_pipeline_adopted_position`(含 TaG hash)
- **L3 计划**：`futures_integration_plan`/`_groundwork`/`_round2_deferred` · `TAG_BORROW_BACKLOG`(P0-1/P1-2✅已合并；P0-2/P1-1/P0-3⏳待 owner，触信任边界)
- **当前状态**：金标准最齐。⛔ CR-EX-01 OKX/Binance live 不对账(确认 open)；Phase 6 live 需 owner 显式授权。

## D5 · 策略生命周期

**Scope**：`strategy/`(dsl/evaluator/backtest/orchestrator/performance_ledger) + `strategy_designer` + 晋级门(paper→demo→live)。

- **L2 契约**：[`promotion_gates_contract`](promotion_gates_contract.md) ✅(8 阈值+状态机+双门)
- **Spec**：`spec_promotion_gates`(7) · `spec_strategy_pool`(SP-S01~03；2A+ 任务记为 missing tripwire) · `spec_evaluator_contracts`
- **L3 计划**：`PLAN_REGIME_STRATEGY_SHORT` · research `strategy_lifecycle_analysis`
- **当前状态**：⛔⛔ **头号结构缺口 = Phase 2A 可执行策略**：`entry_rule/exit_rule` 死字符串，paper_trader 只按 direction+confidence 交易→晋级门衡量"方向运气"非"规则验证"(`execution_path_honesty §1.3`)。2A+ 方向性 schema 已加、scanner 原生 direction 未做。

## D6 · 知识·学习·自进化

**Scope**：`knowledge/`(manager/retrieval/guards/router/FTS5) + `learning/`(lessons/skills) + `analytics`(insight_synthesizer/memo_effectiveness/reasoning_feedback/confidence_calibration) + 自进化 guard_candidate→trading_guard。

- **L2 契约**：[`knowledge_contract`](knowledge_contract.md) ✅(item_type 枚举/FTS/双轨/§6 断裂现状)
- **Spec**：`spec_learning_pipeline` · `spec_lesson_guards` · `spec_insight_feedback_loop`(Module J 可推翻进化)
- **L3 计划**：`self_evolution_plan`(Tier1/2/2.5✅done，Tier3⏳2026-08-17 gated) · `insight_feedback_loop` · `round2_negative_feedback_loop_closure`✅已合并(`dd559a7`)
- **issue**：`LEARNING_LOOP_AUDIT`
- **当前状态**：核心闭环活(Module J memo 自评→推翻→喂下一份)；ps_lessons 双轨=有意设计(§6.A1)；⚠️ ps_skills aspirational(无生产者)；contradiction 已有保守 support-vs-block 检测，UI 只提示需复核数量。

## D7 · 系统监管 / Operator

**Scope**：`operator-shell/`(TS, MAPE-K: heartbeat→recommendation→repair) + `agent_shell/operator_loop`(Python, 收敛为 config 调参) + `agent_runtime/pi_daemon`(交易决策 daemon)。三组件别混。

- **L2 契约**：[`operator_layer`](operator_layer.md) 🟡(概念设计+3组件警告框) · [`PI_OPERATOR_DESIGN_DECISIONS`](PI_OPERATOR_DESIGN_DECISIONS.md) 🟡(本会话补 §问题2 action 命名空间不变式)
- **Spec**：`spec_operator_loop`(含 OL-S12 两环判别锁) · `spec_operator_contract_parity` · `spec_operator_single_source` · `spec_pipeline_operator_loop`(1 partial claim)
- **L3 计划**：`operator_self_supervision`(✅已合 master) · `aletheia_pi_agent_implementation_plan` · `ai_native_bugfix_path`(Phase1/2✅done，3/4⏳deferred)
- **design/specs/(冻结)**：`2026-06-10-pi-operator-agent-{design-audit,crosswalk,dual-process-design}` · `2026-06-07-merge-postmortem`
- **issue**：`PI_AGENT_SDD_AUDIT`(A5 cadence/§C 两环本会话已 reconcile)
- **当前状态**：TS operator-shell 是真正 apex(领先 Python 文档)；两环边界本会话已形式化。⚠️ TS 行为无 spec、Stage 1 未正式"完成"(更深 PI runtime 待补)。

## D8 · 持久化·同步·恢复

**Scope**：`gateway/`(state_machine/state_store/locks/session/reconciliation/recovery) + `storage/`(event_store/artifact_store) + `agents/checkpoints` + `sync/` + `backup/` + `policies/`。

- **L2 契约**：⛔ **无专门 L2 契约**(部分被 `audit_chain_contract`/`execution_contract §6` 覆盖；这是一个文档缺口)
- **L3 计划**：[`Aletheia_Persistence_Sync_Recovery_IMPLEMENTATION_PLAN`](../plans/Aletheia_Persistence_Sync_Recovery_IMPLEMENTATION_PLAN.md)(18 模块代码完成)
- **当前状态**：⛔ 底层(SQLite/JSONL/reconcile)实际在跑，但上层协调(RECOVERY_MODE 状态机/SAFE_FREEZE 禁开仓/Checkpoint 跨机迁移/Policy versioning)**未接进 gateway**，需显式授权。"文档比代码雄心大"。

## D9 · 运行时与稳定性

**Scope**：`agent_runtime/`(scheduler/orchestrator/health/connectivity/stability_monitor/startup_gate/resume_gate/auto_resume) + `gateway/`(lifecycle/frontdoor/tunnel) + `model_provider/`(router/多 provider) + 确定性自愈(无 LLM)。

- **L2 契约**：[`scheduler_contract`](scheduler_contract.md) ✅(job 清单/频率/执行器分组/故障隔离)
- **Spec**：`spec_gateway_stability`(market_observer wall-clock 预算+有界重试) · scheduler 锚点在 `spec_agent_contracts`
- **L3/ops**：ROADMAP Cross-Cutting「确定性自愈 A」✅done + 「Model Provider Fallback」⏳规划中(未实施) · operations:[`RUNBOOK`](../operations/RUNBOOK.md) · [`STABILITY_TARGET_ARCHITECTURE`](../operations/STABILITY_TARGET_ARCHITECTURE.md) · [`PI_RUNTIME_CONTINUITY_SOP`](../operations/PI_RUNTIME_CONTINUITY_SOP.md)
- **issue**：`RUNTIME_BEHAVIOR_ISSUES`
- **当前状态**：自愈层 done；Windows SelectorEventLoop/frontdoor 卡死/market_observer 线程泄漏等顽疾已根治(memory)；⏳ Model Provider 热切换待做(现 LLM outage→autopause)。

## D10 · 前端·Command Center·配置

**Scope**：`frontend/`(React/Vite,单端口 8787) + Command Center(operator 交互面) + `config/schema` + Config↔UI 铁律。

- **L2 契约**：[`ui_design_system`](ui_design_system.md) ✅(设计语言/密度) · [`api_reference`](api_reference.md) ✅(REST 端点/trust 义务)
- **design 过程产物**📦：`overview_redesign` · `webui_page_map` · `webui_backend_handoff`
- **L3 计划**：`frontend_ui_redesign_plan`
- **issue**：`FRONTEND_ISSUES` · `P3_UI_GAPS` · `_ai/UX_AUDIT_FULLAPP` · `_ai/UX_AUDIT_LESSONS`
- **当前状态**：⚠️ UI 诚实性债(硬编码色/text slice 截断/timer/WebSocket viz 等，P3d) · ⛔ RegimeConfig/GuardConfig 前端覆盖 0%(Config↔UI 破口)；MarketAwareExits 已 5/9。

## D11 · 子系统观察者

**Scope**：Serenity(深研产业链长持流派) · xStocks/美股(equity_observer) · 预测市场(polymarket/kalshi)。均"观察+经 PI/风控执行"，不绕过。

- **L2 契约**：[`EQUITY_INTEGRATION_DESIGN`](EQUITY_INTEGRATION_DESIGN.md) 🟡(xStocks) · [`PREDICTION_MARKET_DESIGN`](PREDICTION_MARKET_DESIGN.md) 🟡(观察阶段)
- **Spec**：`spec_subsystem_observers`(8) · `spec_spot_parity` · **Serenity 8 件**：`spec_serenity_{evidence,attribution,promote,holding,live_readiness,verdict,researchability,multi_lens}`(全注册+绿)
- **L3 计划**：`serenity_execution_path`✅已实现(5 phase) · `serenity_aiberkshire_integration_plan`✅P0/S1/P2 · `serenity_lens_implementation_plan` · `serenity_instrument_roadmap` · `prediction_market_lens_report`
- **研究**：`SERENITY_PHILOSOPHY_AND_INTEGRATION_ANALYSIS` · `DEMO_LIVE_SPOT_PARITY_EVENT` · `AI_BERKSHIRE_INTEGRATION_ANALYSIS`(issue)
- **当前状态**：Serenity 大量已实现(本会话对账纠正了"无代码"误报)。⏳ spot-parity 分支 2 commit 待 owner 审核合并。

## D12 · 治理与文档体系（meta）

**Scope**：愿景/红线/度量/SDD 方法/金字塔/协作流程——统领其余 11 域。

- **L0–L1**：[`product/VISION`](../product/VISION.md) · `PRD` · `METRICS` · `PHASE_GATES` · `DOC_SYSTEM_PLAN`(5 层归属) · `EXPERIMENT_{PROTOCOL,MVP_GATE,BACKLOG}`
- **L4 状态**：[`status/ROADMAP`](../status/ROADMAP.md) · `WORLD_MAP`(架构愿景) · `PIPELINE_STATUS` · `REMAINING_WORK_2026-06-30`(本会话)
- **方法/导航**：[`README`](README.md)(契约索引) · [`SDD_METHODOLOGY`](SDD_METHODOLOGY.md) · [`SYSTEM_MAP`](SYSTEM_MAP.md) · **本文(域索引)**
- **治理**：[governance/`trust_and_change_control`](../governance/trust_and_change_control.md) · `implementation_guide`
- **协作过程**📦：`_ai/`(DECISIONS/AI_HANDOFF/CURRENT_TASK/DEVLOG/CONVENTIONS/DEV_PROTOCOL/AI_WORKFLOW/PROJECT_CONTEXT/DEBUG_AUDIT_PLAYBOOK/GIT_WORKTREE_PLAYBOOK)
- **参考系**：`references/`(north_star/competitors_and_papers/agent_framework_research/FRAMEWORK_MODULE_MAP/INDEX/prism_extraction/openalice_prism_review)
- **issue 总账**：`issues/`(README/BACKEND/FRONTEND/HUMAN_ISSUES/LOW_VALUE_TESTS/FIX_PLAN/MASTER_FIX_LEDGER/_RAW_SWEEP_LEDGER + archive/)
- **当前状态**：金字塔结构强(L0–L4+Spec-as-Code)。⛔ **元缺口=ledger 只进不出**导致状态失真(REMAINING_WORK §0/§5)；本会话已部分对账。

---

## 跨域观察（从这张地图看出的结构性事实）

1. **契约覆盖不均**：交易核心(D2/D3/D4)金标准齐全；**D8 持久化无 L2 契约、D5 有契约但核心执行(2A)缺失**——这两处是"文档形态"上最该补的。
2. **文档↔代码方向倒挂的两类**：D7(TS operator 代码领先文档) 与 D11(Serenity 代码领先文档/plan)——都是"代码做了文档没追"，与 D12 的 ledger-lag 同源。
3. **跨域 seam 已补**：本会话的 `decision_lifecycle_contract` 是唯一横切 D1→D2→D3→D4 的契约，补上了此前"按域竖切、接缝无人管"的结构空白。
4. **每个域的"真相源"**：状态看 `aletheia doctor` + conformance matrix；意图看本域 L2 契约；历史看 issues ledger(**不可当当前状态读**)。
</content>
