# 审计报告：docs/design/proposals/（不含 learning_* / tokenomics_*）+ docs/research/ 角色错位

> 审查对象：worktree `docs-spec-cleanup`（HEAD 356d346a4）。只读，不改仓库文件。
> 判定口径：已实现 → 标 `implemented` 并确认规范内容已在 Contract；仍是目标 → CONVERT 到 `docs/design/specs/NNNN/`；过时 → superseded / ARCHIVE。

## A. Slim Core 12 份小提案（slim_core_*.md，统一 status: accepted）

共同问题：全部是 #3134 的"有界 D2 批次"说明，写法是 PR 说明体（"This bounded change… Verification…"），状态仍 `accepted`，但代码已经全部落地。按 proposals/README 生命周期，应批量改 `implemented`，并核对规范句是否已进对应 Contract；没有 Contract 承接的，把 1-3 句不变式搬进 Contract 后提案冻结。12 份合计约 580 行，可整体移到 `docs/design/history/slim_core/`（或保留原地仅改状态）。

| 文件 | 行 | 代码证据（grep） | 判定 |
|---|---|---|---|
| slim_core_candidate_publication | 61 | `core/aletheia/storage/repositories.py:3120 publish_candidate_snapshot` 唯一入口；`insert_candidate`/`insert_universe_member` 已无定义；`core/aletheia/trading/serenity_publication.py:9 publish_serenity_candidates` 已迁出 | 已实现 → implemented；`decision_lifecycle_contract.md:65` 已链接，确认该处写明"唯一 snapshot/member 写入口" |
| slim_core_http_model_transport | 203 | `extensions/model_endpoints/{openai_compatible,anthropic_compatible,http_runtime,credential_*}.py` 存在；`pyproject.toml:16-17 openai>=2.54.0,<3 / anthropic>=0.125.0,<1`；Databricks/Codex/Claude CLI provider 类已无（`config/schema.py:488` 只剩注释） | 已实现 → implemented；正文 8 个小节是 8 个 PR 的叠加，规范已在 `llm_inference_harness_contract.md` 与 CAPABILITY_GROUND_TRUTH"Retired CLI/native/OAuth" 行。ARCHIVE |
| slim_core_legacy_gateway_retirement | 58 | `core/aletheia/gateway/` 下已无 recovery/state/session_lock 模块；`GatewayRecoveryService` 全仓 0 命中 | 已实现 → implemented；`persistence_recovery_contract.md:21` 已承接。顶部 ArtifactStore 注释可删 |
| slim_core_research_bundle_import | 49 | `extensions/sync/importer.py:18 _IMPORT_ALLOWED_TABLES` = 4 张 serenity + `ps_lessons` + `ps_skills`；`trade_proposals` 已不在 | 已实现 → implemented（`persistence_recovery_contract.md:129` 承接）。**与 spec 0001 冲突**：0001 REMOVED `ps_lessons` 生命周期，Playbook 替代后导入白名单里的 `ps_lessons/ps_skills` 需随 0001 一起改，提案中"lessons and skills"不能再当现行 |
| slim_core_partial_reduce_retirement | 29 | `def reduce_position`（`portfolio.pnl`）全仓 0 命中，仅剩 `portfolio/position_manager.py:434 reduce_position_routed` | 已实现 → implemented；`execution_contract.md` 承接 |
| slim_core_macro_projection | 29 | 分类器在 `core/aletheia/data/freshness.py:152-160`（VIX>25→risk_off）；`extensions/data/collectors/macro.py:183` 反向 import Core | 已实现 → implemented |
| slim_core_native_data_collectors | 27 | `extensions/data/collectors/{microstructure,derivatives,volume_scanner}.py` 存在；`agent_runtime/scheduler.py:4396-4525` 由调度器组合导入 | 已实现 → implemented |
| slim_core_news_compartment | 25 | `extensions/data/{news_collector,news_analyzer,news_routes}.py` 存在 | 已实现 → implemented |
| slim_core_options_data_compartment | 24 | DTO 留 Core：`core/aletheia/data/options_volatility.py:13 OptionsVolatilitySnapshot`；采集在 `extensions/data/collectors/options_structure.py` | 已实现 → implemented |
| slim_core_planner_data_boundary | 28 | `core/aletheia/agent_shell/entry_planner.py` 不再 import `MicrostructureCollector`（只在 scheduler 组合层出现） | 已实现 → implemented；`entry-plan-contract.md` 应写入"Planner 不导入可选采集器、2s 新鲜度/5s 超时、不可用不调模型"这一句 |
| slim_core_public_context_adapters | 24 | `extensions/data/collectors/{coingecko_top,defillama,hyperliquid}.py` 存在 | 已实现 → implemented |
| slim_core_scoped_data_collectors | 27 | `extensions/data/collectors/{macro,coinalyze}.py` 存在；`macro.py:46 get_secret` 只取自身引用 | 已实现 → implemented |

具体改法：12 份 front matter `status: accepted` → `implemented`；README "Current proposals"里 Slim Core 一整段移到 "Implemented proposals"；正文"Verification and non-goals"类段落是 PR 自检清单，冻结即可不必改写。Data 7 份没有一个 Contract 写"Core 不得 import 可选采集器、调度器是唯一组合者"——应在 `docs/design/data/data_utilization.md` 或 SYSTEM_MAP 补一句不变式（这是缺失的现行规则）。旁注：`data_utilization.md:52` 仍把 `market_observer` 列为 macro 消费者（已退役）。

## B. 退役三份 + UTA

### retire_local_simulated_exchange.md（50 行，implemented）——退役已完成
- 写入路径 grep：`INSERT|UPDATE paper_account_events` 在 core/extensions/ops 除 `storage/migrations.py` 外 **0 命中**；无 `PaperAccount`/`simulate_fill` 类函数。
- 残留 `paper_internal` 共约 26 处，全部是历史读过滤：`risk/engine.py:491,579,766`（把历史 paper 仓位排除出风控敞口）、`portfolio/outcome_hooks.py:34,161`、`portfolio/account_loss.py:133-156`（读 `paper_account_events`）、`agent_shell/reflection.py:85`、`analytics/exit_trailing_qualification.py:322`、`config/exchanges.py:55 "paper_internal": "local"`（持久化身份分类表）。符合提案"历史记录保留原 identity"。
- 小毛刺：正文第 13 行 `Status: accepted.` 与 front matter `implemented` 矛盾 → 删该行。`config/schema.py:297` 注释仍写 "Where autonomous paper trades execute"，`schema.py:285 enabled_markets` 默认 `{"spot": True}`（与 futures-only 冲突，不在本提案范围，但值得另报）。
- 判定：KEEP（implemented）。规范内容已在 CAPABILITY_GROUND_TRUTH "Retired simulator" 行 + `history/local_simulated_*`。可再缩为 history 文件（正文已无现行规范）。

### retire_market_observer.md（153 行，implemented）——退役已完成，但兄弟 observer 仍活着
- `extensions/research/market_observer.py` 不存在；scheduler/main/health/doctor 中 `market_observer` 0 命中。
- 残留：历史读 `COALESCE(signal_source,'market_observer')`（`analytics/skip_diagnosis.py:137-183`、`diagnostics/probes/trading.py:170,215`）、`thesis_lineage.py:57,582-589`、`alpha_attribution.py:953`、`broker.py:862` 优先级——均为提案明确保留项。
- **化石**（提案未列、现无消费者）：`extensions/knowledge/router.py:26 AgentRole = Literal["market_observer"]` 及 :38 路由表（唯一角色是已删 agent）；`extensions/learning/prompt_bundles.py:130-202 consumer="market_observer"`；`extensions/learning/opportunity_information_policy.py:31`；陈旧注释 `data/context_builder.py:3`、`trading/market_context.py:4,24`、`collectors/coinalyze.py:21`、`config/schema.py:289-292`。
- **重要**：提案 non-goals 明确保留 `equity_observer` / `prediction_observer` / `divergence_scanner`。代码中它们仍注册（`core/aletheia/main.py:143-178`）且被调度（`agent_runtime/scheduler.py:1584-1603`，equity 美股盘中每半小时，prediction 每 30 分钟；prediction 受 `prediction_enabled=False` 门控（`schema.py:402`），equity 只要 `observed_equities` 非空就跑）。与 CAPABILITY_GROUND_TRUTH "Ordinary equities and prediction markets: Outside active instrument scope" 冲突——应另起退役（D2，需 owner）。
- 判定：KEEP（implemented）→ 可 ARCHIVE 到 history（含行号快照，已过时）。正文 "Resolution" 段重复两遍"Normative statement moves to ACTIVE_RUNTIME_SCOPE"，实际承接者是 `agent_contracts.md:147-156`；ACTIVE_RUNTIME_SCOPE 无 market_observer 条目 → 改为指向 agent_contracts。

### retire_uta_qualification.md（42 行，implemented）
- 正文后半（"After merge, inspect… deploy… Return to #3031"）是运维步骤/进度，不是设计。规范已在 `execution_contract.md`。判定：ARCHIVE 到 history（`history/uta_qualification.md` 已存在，可合并进去）。

### unified_trading_account.md（108 行，accepted）——已是现行实现
- 代码：`core/aletheia/uta/contract.py:56 class UTAAdapter(Protocol)`、`extensions/exchanges/ccxt/adapter.py:61 CCXTUTAAdapter`、`extensions/exchanges/composition.py:157 build_uta_registry`、`extensions/exchanges/bitget/profile.py:339 BitgetUTAV3Profile` / `:451 BitgetUTAV3LiveProfile`；`classic_v2` 全仓 0 命中（头注属实）。
- "Current fact" 段已错：仍写 "Binance, OKX, Bitget Classic-v2 and UTA-v3 profiles" 和"六个外部执行模式"为现行；OKX/Binance 现为 frozen。
- 规范已大部分在 `execution_contract.md:868-876`（身份先落库、UTA 不写 DB、CAS、ambiguous 不重试），但该处最后一句"UTA 边界见 unified_trading_account.md"把权威反指回提案。
- 判定：标 `implemented`。编辑：①把 Goals 段的 UTA 覆盖面（identity/generation、balances、positions、orders、fills、funding、verified-exit、`not_sent/venue_rejected/acknowledged/ambiguous`、Decimal-safe、incomplete≠empty）搬进 `execution_contract.md` 一节；②`execution_contract.md:876` 改指 Contract 自身；③"Adding a profile" 段移到 `extensions/AGENTS.md`（已有 profile 说明 :10-13）；④提案 ARCHIVE 或冻结。

## C. 其余已标 implemented 的提案

| 文件 | 行 | 代码/合同证据 | 判定与编辑 |
|---|---|---|---|
| canonical_entry_lifecycle | 95 | `storage/migrations.py:3523 cycle_number`；合同 `entry-plan-contract.md`（774 行）含 Canonical Entry hypothesis authority、`EntryHypothesis`、Fast Monitoring；路由 `bitget_demo`+Full 由 `ACTIVE_RUNTIME_SCOPE.md:71` 持有 | implemented 属实。①第 16-17 行 "Acceptance is not an implementation… claim" 与 implemented 矛盾，删；②"Approved future portions" 段：UTA/coordinator 已完成，Lab 迁移仍 OPEN（#2737/#3808），Linux 源部署另有提案——这是进度，删或移入 Issue。→ ARCHIVE 到 history |
| entry-plan-contract（提案） | 170 | 合同同名文件承接；`trading/entry_capture.py` 实现 PriceFirst/MakerPreferred/FillFirst；两腿捕获在合同 :654-770 | implemented。正文是 6 次 amendment 叠加（含 "entry-planner/v3"、"PI daemon"、"paper-account net cash within 1e-8" 等已退役/已被取代表述）。规范已在合同 → ARCHIVE；与合同同名易混淆，建议改名 `history/entry_plan_contract_decision_2288.md` |
| derivatives_facts | 210 | `core/aletheia/data/derivatives_facts.py` 存在；合同 `derivatives_economic_semantics.md` | implemented。表中 "Binance ratio populations / current Binance public requests" 写作现行获取源，Binance 现 frozen，需核对是否仍采集（不在本范围深挖）。→ ARCHIVE |
| position_replay_profit_gradient | 166 | `cli/inspect_cmd.py:163 position-replay`；`storage/proposal_hypothesis.py`（position_hypothesis/v1）；`analytics/profit_loop_diagnosis.py`；两份合同已承接 | implemented。non-goals 提 "Ranker, System Operator… UFC cohort"（已退役名词）。→ ARCHIVE |
| ohlcv_provider_observation_result | 185 | 合同 `ohlcv_provider_result_contract.md` 已承接（提案自述） | implemented，KEEP 冻结或 ARCHIVE；"Current fact" 段是改前事实，标题应改 "Pre-change fact" |
| serenity_research_attempt_truth | 121 | `cancel_requested` 在 `storage/repositories_research.py`、`extensions/research/research_worker.py`、`server/routes/data.py`；合同 `serenity_contract.md:154-155` 有状态表 | implemented。同上 "Current fact" 是改前事实。→ ARCHIVE。Serenity 本身在 CAPABILITY_GROUND_TRUTH 为 Experimental（可选面） |
| usage_v2_workspace | 162 | `economics/usage_insights.py:695 contract_version: 2`；`api/api_reference.md:352` 与 `data/data_utilization.md:70` 承接计价规则 | implemented，KEEP 冻结（已用 "Pre-change fact" 正确写法，可作模板）。|
| gateway_service_lifecycle | 436 | `cli/gateway.py:14-257 service install/uninstall/status` 存在；但第 34-35 行仍写 "This draft does not claim that `gateway service install…` exists"——自相矛盾 | implemented 但正文 1/3 是 Windows machine service/UAC（§6-7），而头注说只维护 Linux。规范应在 `docs/operations/deployment/GATEWAY_SESSION_SERVICE.md`。→ ARCHIVE；另：`core/aletheia/gateway/windows_*.py` + `_win32_proc.py` 共 1038 行仍在树中且被 `service_control.py:7,79-279` 导入，`history/platform_specific_gateway_services.md:3` 自认"retained, not deleted, not maintained"——违反 AGENTS.md "delete the loser"，应另起删除 |
| universe_fast_trigger_watch | 17 | 已是指向 history 的墓碑 | superseded，KEEP（或直接删墓碑，README 链 history） |

## D. 仍为 accepted / draft 的其余提案

### mechanism_aware_grounded_entry.md（107 行，accepted）——实际已实现
- 代码：`universe/candidate_opportunity.py:16 candidate-opportunity-context/v1`、`storage/models.py:1255`、`trading/entry_learning.py:13 entry-learning-context/v1`、`trading/entry_analysis.py`、`extensions/learning/registry.py:68 mechanism_entry_learning`、`observation_lab/runner.py:435`。合同 `entry-plan-contract.md:204-218` 已有 "Mechanism-aware Grounded Entry" 一节。
- 正文仍写 "Candidate Planning V2 → Entry Analysis Context V2 → Entry Hypothesis V2" 等版本标签（已被 canonical lifecycle 取代，头注自认）。
- **与 spec 0001 冲突**：0001 `spec.md:212`/`design.md:105-106,172` 新增 `playbook_bundle` Lab 适配器并与 `entry_learning` 适配器"互斥共存"——Planner 将有两条学习上下文注入路径，违反"一个能力一份实现"；0001 落地时应删除 `entry-learning-context/v1` 路径，而非并存。
- 判定：改 `implemented`（或 superseded_by 0001 的学习部分）→ ARCHIVE。

### lifecycle_capability_map.md（124 行，accepted）——已实现
- `docs/product/CAPABILITY_MAP.md` 存在；盘点流程已成 skill `capability-review`；路线图 issue #3883。
- "当前事实"段含 2026-10-09 实测 GitHub 计数（615 issue/52 now/242 soon）——进度状态，违反 stable-content 规则。
- 判定：改 `implemented` → ARCHIVE；如需保留流程定义，并入 CAPABILITY_MAP.md 头部 5 行。

### entry_order_risk_sizing.md（309 行，draft）——角色错位最严重的提案
- 内容是 owner 讨论纪要：checkbox `[x]/[ ]/[~]`（:198-288）、"已执行：Fedora demo 策略升到 revision 9/10"（运行时操作日志）、"PR-A 实施中"、"#3780 暂缓合并"、"Current fact (2026-10-09, master 1cea7366)" 实测数字；Selected design / Verification / Resolution 全空（:289-309）。
- 违反 governance：进度/勾选框/运维记录不得进文档。已决部分（H4 原生止损必须、H5 止损先于强平、模拟盘每笔风险 3%、杠杆上限 10/5/3x、胜率定义、转实盘门槛 ≥100 笔且 95% 下界>0）是 owner 决策 → 应进 `DECISIONS.md`；当前值已写入 Fedora 策略 revision，属运行时事实。
- 判定：CONVERT 到 `docs/design/specs/0002-entry-sizing-leverage/`（spec.md：H1-H6 写成 EARS 需求；Decision log 收已决项；`[NEEDS CLARIFICATION]` 标未决 7/8/9）；几何（决定 5/6/7）按 owner 归 #3850 另立 spec。原文件删除或 superseded。

### fedora_immutable_runtime_updates.md（132 行，draft）
- 无实现（签名制品/feed/headless activation host 全仓无对应代码）；仍引用 "passed existing CI"（远程 CI 已停用，AGENTS.md）与 "Fedora Paper runtime / Paper mode"（模拟器已退役，Fedora 现跑 bitget_demo）。
- 与 owner "尽快上线、不过度工程化"、"模拟盘阶段从简（部署一条 SSH 同步）"相悖。
- 判定：ARCHIVE（建议 owner 标 `rejected`，或保持 draft 但移出"Current proposals"）。不值得转 spec。

### observation_experiment_ownership_v2.md（446 行，accepted）——部分实现、双权威
- 代码：`storage/migrations.py:4439` 初始化 `authority_source='legacy'`；`migrations.py:9413-9424` 仅**全新安装**切到 `lab`；已有库（Fedora）仍 legacy。合同 `contracts/observation_experiment_ownership_v2.md` 写 "Fresh databases initialize Lab authority"——对新库正确，但"Observation Lab is the sole online Experiment authority after the single-mainline cutover"对现网未成立。
- 正文 §11 是 Phase 门/issue 清单（#1386…#2616）——执行计划，属 Issue。
- spec 0001 依赖 Lab adapter 机制（`design.md:24,105`），故剩余 cutover 是 0001 的前置。
- 判定：SPLIT——已实现条款（Lab-only 写入守卫 `extensions/counterfactual/filter_experiments.py:_require_lab_authority`、fresh=lab）留合同；剩余"现存库 legacy→lab cutover + legacy 表删除"CONVERT 成小 spec（≤1 页，EARS：When an existing database…）；§2-§10 词汇/模型 ARCHIVE。

### universe_continuous_scoring_safety.md（454 行，accepted）——已被取代
- `universe/continuous_scoring.py:1-5` 自述"whole-candidate scoring… serve historical Twin/Relative-Rank evidence only"；`universe-step-v2`/`universe-continuous-shadow-v1` 全仓 0 命中（现为 `-shadow-v2`）。当前选择由 `candidate_selection_utility_contract.md` 持有。
- 四条 safeguard 针对"因子级 Learning 实验"，与 owner "AI 像人一样交易、不是量化挖因子"方向冲突，也被 spec 0001 的学习设计取代。
- `candidate_selection_utility_contract.md:3-4` 把该提案列为 "Authority"——权威反指。
- 判定：标 `superseded`（superseded_by: candidate_selection_utility_contract + specs/0001）→ ARCHIVE；合同 :3-4 改为只引 #2729 与 DECISIONS。

### cn-equity-market-kernel.md（59 行，accepted）
- 代码 grep `cn_equity|a_share|CNEquity` 在 core/extensions/ops/legacy **0 命中**，从未实现。`product/gates/PHASE_GATES.md:35-38` 仍有 "Gate CN" 授权实现。
- 与 DECISIONS 2026-10-09"产品只做加密 USDT 永续"及 CAPABILITY_GROUND_TRUTH "Ordinary equities… outside active instrument scope" 冲突；正文还引用已退役的 "System Operator boundaries"。
- 判定：ARCHIVE，建议 owner 改 `rejected`（D2 范围收窄，需 owner 一句话确认）；同步删 PHASE_GATES "Gate CN"。

### template.md（47 行）+ README.md（125 行）
- `specs/README.md` 已声明新设计一律走 specs/，`specs/_template/` 存在；但 `proposals/README.md:26` 仍写 "Use template.md"。→ DELETE template.md；README 删 "Use template.md"、"Current proposals"段按本报告重排（大部分移入 Implemented/Superseded），并删 "#2971 明确排除清单"等进度话术。

## E. docs/research/ 角色错位清单（只判角色，不评内容；不含 counterfactual/、agents-learning/）

判据：正文在陈述"当前行为（应归 Contract / SYSTEM_MAP / Operations）"或"目标设计 / 决策（应归 Spec / DECISIONS）"，而不是日期化证据。共扫 124 份，以下 22 份错位。

**写"当前行为"的（应归 Contract / SYSTEM_MAP / Operations）**
1. `llm_harness/provider_usage_coverage.md` — "This record defines the token telemetry contract"（规范化字段表）→ 应在 `llm_inference_harness_contract.md`。
2. `llm_harness/token_observation_policy.md` — "Decision: default token policy is `integrity-v1`"，已实现于 `core/aletheia/economics/tracing.py:200 observe_token_integrity`；合同中 0 命中 → 合同/DECISIONS 缺这条。
3. `audits/SLIM_CORE_OWNERS_2026-09-13.md` — "The package disposition table below is current"→ 与唯一代码地图 SYSTEM_MAP 重复。
4. `integrations/cloudflare_tunnel_integration.md` — "功能完整，默认禁用"+用户配置流程 → Operations runbook（代码 `core/aletheia/gateway/tunnel.py`）。
5. `universe-scoring/README.md:9-16` — "accepted future design is …continuous_scoring_safety"、"Current runtime authority remains `universe-step-v1`"——两句都已失真（现为 Candidate Selection Utility）。
6. `audits/PERFORMANCE_CONCURRENCY_SQLITE_AUDIT_2026-07-17.md` — 头部"状态：整改已实现，正在完成全量回归"，§7-8"持续记录后续实现"→ 活的进度记录。
7. `audits/CODE_REVIEW_LESSONS.md` — 审查方法论（无日期主体）→ 属开发指南（DEBUG_AUDIT_PLAYBOOK）。
8. `tokenomics/route_registration_conventions_2026-07-30.md` — "新路由按此模式挂载"的开发约定 → 开发指南（轻度）。

**写"目标设计 / 决策"的（应归 Spec / Proposal / DECISIONS）**
9. `llm_harness/environmental_awareness_context_contract.md` — 名为 Contract，含 "Contract layers / Acceptance criteria before default use"。
10. `llm_harness/unified_inference_harness.md` — "Target contract / Decisions / Initial implementation path"。
11. `llm_harness/agent_pyramid_and_role_topology.md` — "Proposed roles / Implementation sequence"，且以 System Operator/PIAgent 为现行角色。
12. `llm_harness/context_adapters_and_header_experiments.md` — 带 feature flag 现状与默认化条件（半设计半状态）。
13. `universe-scoring/02_continuous_curve_specification_2026-08-07.md` — "Status: accepted curve evidence for implementation"（规格本体，已被取代）。
14. `universe-scoring/relative-rank-candidate/UNIVERSE_RELATIVE_RANK_SHADOW_V1_DRAFT.md` — "Design Draft / owner-approved; implemented"→ 应在 history。
15. `audits/SLIM_CORE_SETTLEMENT_OWNERSHIP_2026-09-12.md` — 带 `status: draft` 提案 front matter（从 proposals 迁来，未改形）。
16. `audits/DUAL_MAINLINE_CONVERGENCE_PLAN_2026-09-07.md` — 执行计划 + "Execution status" 进度。
17. `audits/ENTRY_REJECT_ATTRIBUTION_14D_2026-10-09.md` — 标题即"解决方案草案"；方案部分应进 entry sizing spec。
18. `audits/EXIT_AUTHORITY_ANALYSIS.md` — "做出产品裁决… 分阶段"（无日期）→ 裁决应进 DECISIONS / spec。
19. `trading-strategy/POSITION_SIZING_AND_PERSONAS_2026-07-01.md` — "分析报告 + 实施计划（未实施）"。
20. `trading-strategy/VERGEX_PLATFORM_GAP_AND_IMPL_CANDIDATE_REPORT_2026-08-11.md` — "Role: … candidate contract"，"Decision state: P0-P3 approved and implemented"。
21. `tokenomics/phase2_design_decisions_2026-07-31.md` — 两条 owner 决策 → DECISIONS.md。
22. `audits/PI_AGENT_EVOLUTION_ANALYSIS.md` — "架构设计方案 / 当前 vs 目标"（已标历史归档，可直接移 archive）。

边界项（不计入，但名字像规范）：`trading-theory/04_evidence_and_ai_injection_policy.md`、`09_technical_context_evaluation_protocol.md` 自声明 "Role: Research evidence"，可保留；`audits/OPPORTUNITY_LIFECYCLE_CAPABILITY_TRACKING_2026-10-09.md` 写"应该能干什么"，与 `product/CAPABILITY_MAP.md` 重叠，但已自声明为日期化证据。

## F. 跨文档

- **权威反指**（合同把权威指回提案）：`execution_contract.md:876` → unified_trading_account；`candidate_selection_utility_contract.md:3-4` → universe_continuous_scoring_safety；`contracts/observation_experiment_ownership_v2.md` 开头 → 同名提案；`CAPABILITY_GROUND_TRUTH.md:81` → retire_local_simulated_exchange。提案冻结/归档后这些链接要改为指 DECISIONS 或 history。
- **范围冲突**：cn-equity 提案 + `PHASE_GATES.md` Gate CN、仍在运行的 `equity_observer`/`prediction_observer`，与"只做加密 USDT 永续"矛盾；`config/schema.py:285 enabled_markets` 默认 `spot: True`。
- **与 spec 0001 冲突**：`extensions/sync/importer.py:18` 白名单中的 `ps_lessons`/`ps_skills`（0001 REMOVED ps_lessons 生命周期）；`entry_learning` 与 `playbook_bundle` 两个 Lab 适配器并存（0001 design.md:105-106,172），违反单一实现；`extensions/knowledge/router.py:26` 只为已删 market_observer 路由 lessons/guards。
- **缺合同**："Core 不 import 可选 Data 采集器、scheduler 是唯一组合者"（7 份 slim_core Data 提案的共同不变式）无现行 Contract 承接；token integrity-v1 策略无合同。
- **未删的输家**：`core/aletheia/gateway/windows_*.py` + `_win32_proc.py`（1038 行）"保留但不维护"，仍被 `service_control.py` 导入。

## 总表

| 文档 | 行 | 现状态 | 实际 | 判定 | 头号问题 |
|---|---|---|---|---|---|
| slim_core_* ×12 | 24-203 | accepted | 全部已实现 | 改 implemented → ARCHIVE | 状态过时；PR 说明体 |
| retire_local_simulated_exchange | 50 | implemented | 退役完成（0 写入路径） | KEEP/ARCHIVE | 正文 "Status: accepted" 自相矛盾 |
| retire_market_observer | 153 | implemented | 退役完成；化石残留 | ARCHIVE | equity/prediction observer 仍运行 |
| retire_uta_qualification | 42 | implemented | 已实现 | MERGE 入 history/uta_qualification.md | 后半是运维步骤 |
| unified_trading_account | 108 | accepted | 现行实现 | 改 implemented，规范搬入 execution_contract | Current fact 仍列 Classic-v2/OKX/Binance |
| canonical_entry_lifecycle | 95 | implemented | 已实现 | ARCHIVE | "not an implementation claim" 未删 |
| entry-plan-contract（提案） | 170 | implemented | 已实现 | ARCHIVE + 改名 | 与合同同名；6 次 amendment 叠加 |
| derivatives_facts | 210 | implemented | 已实现 | ARCHIVE | Binance 采集写作现行 |
| position_replay_profit_gradient | 166 | implemented | 已实现 | ARCHIVE | 引用 Ranker/System Operator |
| ohlcv_provider_observation_result | 185 | implemented | 已实现 | KEEP 冻结 | "Current fact" 应为 Pre-change |
| serenity_research_attempt_truth | 121 | implemented | 已实现 | ARCHIVE | 同上 |
| usage_v2_workspace | 162 | implemented | 已实现 | KEEP 冻结 | 无（写法可作样板） |
| gateway_service_lifecycle | 436 | implemented | Linux 部分实现 | ARCHIVE | 1/3 篇幅是不维护的 Windows |
| universe_fast_trigger_watch | 17 | superseded | 墓碑 | KEEP/DELETE | — |
| mechanism_aware_grounded_entry | 107 | accepted | 已实现 | 改 implemented → ARCHIVE | 与 0001 双学习适配器 |
| lifecycle_capability_map | 124 | accepted | 已实现（CAPABILITY_MAP.md） | 改 implemented → ARCHIVE | 含 GitHub 计数快照 |
| entry_order_risk_sizing | 309 | draft | 讨论纪要 | CONVERT → specs/0002 | checkbox+运维日志+空设计段 |
| fedora_immutable_runtime_updates | 132 | draft | 未实现 | ARCHIVE（建议 rejected） | 引用远程 CI/Paper，过度工程 |
| observation_experiment_ownership_v2 | 446 | accepted | 部分（新库 lab，旧库 legacy） | SPLIT | 双权威仍在；§11 是执行计划 |
| universe_continuous_scoring_safety | 454 | accepted | 已被 Utility 取代 | superseded → ARCHIVE | 因子级学习与产品方向冲突 |
| cn-equity-market-kernel | 59 | accepted | 0 代码 | ARCHIVE（建议 rejected） | 违反 futures-only |
| template.md | 47 | — | 被 specs/_template 取代 | DELETE | README 仍指向它 |
| README.md | 125 | — | 索引 | REWRITE | 分组与实际状态不符 |
