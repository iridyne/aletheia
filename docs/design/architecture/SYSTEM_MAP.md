# System Map — 全模块概念地图

> 系统职责与调用关系导航；表中的历史实现备注不等于当前运行状态。
> 下方路由区的源码链接与符号由 `scripts/ci/check_docs.py` 的 navigation 检查守；这不是全量运行审计。
> 各表「管道」列的 ✅/⚠️ 只表示源码调用路径是否连通，不表示已部署、盈利或运行健康已验证。

本图是导航，不授予运行或交易权限；当前支持范围以 [Capability Ground Truth](../../product/CAPABILITY_GROUND_TRUTH.md) 和 [Active Runtime Scope](../../product/ACTIVE_RUNTIME_SCOPE.md) 为准，任务与 Agent 行为以 [Agent Contracts](../contracts/agent_contracts.md) 为准。管道连通不代表已部署或经济资格已验证。

## Task → owner → source → tests

先沿一条真实调用链定位，再读取该责任的契约；不要为普通修改遍读全仓。
下表是源码导航，不是运行健康或交易能力证明。测试命令使用隔离数据根。

<!-- navigation-check:start -->

| 问题 | 首读 owner / 符号 | 一个直接调用者 | focused tests | 必要契约 |
|---|---|---|---|---|
| Candidate 有事实却未准入 | [candidate_selection_utility.py](../../../core/aletheia/universe/candidate_selection_utility.py)：`build_candidate_selection_utility` / `select_admitted_candidates` | [candidate_selection_shadow.py](../../../core/aletheia/universe/candidate_selection_shadow.py)：`build_candidate_selection_shadow_bundle` | [test_candidate_selection_utility.py](../../../tests/test_candidate_selection_utility.py) | [Candidate Selection Utility](../contracts/candidate_selection_utility_contract.md)；无样本、实测拒绝与损坏不可混同 |
| Planner 未运行或没有新 Plan | [entry_planner.py](../../../core/aletheia/agent_shell/entry_planner.py)：`run_entry_planner_batch` / `plan_next_candidate` | [agent_runtime/pi_daemon.py](../../../core/aletheia/agent_runtime/pi_daemon.py)；scheduler 的 `pi_trading_decision` 也调用同一批处理入口 | [test_entry_planner.py](../../../tests/test_entry_planner.py)、[test_scheduler_timeouts.py](../../../tests/test_scheduler_timeouts.py) | [Entry Plan](../contracts/entry-plan-contract.md)、[Decision Lifecycle](../contracts/decision_lifecycle_contract.md) |
| Risk 日损检查或禁用状态不符 | [risk/engine.py](../../../core/aletheia/risk/engine.py)：`evaluate_proposal` | [execution/gateway.py](../../../core/aletheia/execution/gateway.py)：`_execute_proposal` | [test_daily_loss_breaker.py](../../../tests/test_daily_loss_breaker.py) | [Risk Engine](../contracts/risk_engine_contract.md)；禁用检查不是把未知日损当零 |
| Entry Plan 的仓位/杠杆与其意图不符 | [entry_plan_compiler.py](../../../core/aletheia/trading/entry_plan_compiler.py)：`compile_entry_plan`；[entry_sizing.py](../../../core/aletheia/trading/entry_sizing.py)：`resolve_entry_sizing`；[leverage_resolution.py](../../../core/aletheia/trading/leverage_resolution.py)：`resolve_execution_financing`；事实入口 [venue_margin.py](../../../core/aletheia/trading/venue_margin.py)：`load_venue_margin_envelope` | [execution/entry_plan_execution.py](../../../core/aletheia/execution/entry_plan_execution.py)：`build_entry_plan_order_intent`；Risk 复核见 [risk/engine.py](../../../core/aletheia/risk/engine.py)：`_entry_plan_financing_violations` | [test_entry_sizing.py](../../../tests/test_entry_sizing.py)、[test_leverage_resolution.py](../../../tests/test_leverage_resolution.py)、[test_venue_margin.py](../../../tests/test_venue_margin.py)、[test_risk_intent.py](../../../tests/test_risk_intent.py) | [Entry Sizing and Financing](../contracts/entry_sizing_and_financing_contract.md)、[Risk Engine](../contracts/risk_engine_contract.md)（§5、关卡 #13/#18/#20/#21/#22） |
| ACK / Fill / Position / Close 不一致 | [exchange_materialization.py](../../../core/aletheia/execution/exchange_materialization.py)：`materialize_observed_close_chain`；[reconciliation.py](../../../core/aletheia/execution/reconciliation.py) | [portfolio/outcome_closure.py](../../../core/aletheia/portfolio/outcome_closure.py) 调用 close-chain materializer | [test_outcome_closure.py](../../../tests/test_outcome_closure.py)（close-chain 物化）、[test_reconciliation.py](../../../tests/test_reconciliation.py)、[spec_pipeline_exchange_materialization.py](../../../tests/specs/spec_pipeline_exchange_materialization.py) | [Execution](../contracts/execution_contract.md)；已关闭暴露不等于成本完整 Outcome |
| 入场订单长期非终态 / 入场闸门拒绝全部新入场 / 计划多而无成交 | [order_states.py](../../../core/aletheia/storage/order_states.py)（唯一的非终态定义）；[stale_order_cleanup.py](../../../core/aletheia/execution/stale_order_cleanup.py)（入场侧有界终止）；[liveness.py](../../../core/aletheia/execution/liveness.py)（只读观测） | [trading.py](../../../core/aletheia/diagnostics/probes/trading.py)：`_probe_execution_liveness`；[health.py](../../../core/aletheia/agent_runtime/health.py)：`compute_health` 的 `execution_*` 条目 | [test_order_state_liveness.py](../../../tests/test_order_state_liveness.py)、[test_identity_liveness.py](../../../tests/uta/test_identity_liveness.py)、[test_execution_liveness.py](../../../tests/uta/test_execution_liveness.py) | [Execution §3.0b / §6.1](../contracts/execution_contract.md) |
| Position 故事或诊断展示不符 | [service.py](../../../core/aletheia/analytics/position_lifecycle_story/service.py)：`build_position_lifecycle_story`（装配与经济解释）；[readers.py](../../../core/aletheia/analytics/position_lifecycle_story/readers.py)（精确读取与 cutoff 遮罩）、[events.py](../../../core/aletheia/analytics/position_lifecycle_story/events.py)（纯事件投影）、[render.py](../../../core/aletheia/analytics/position_lifecycle_story/render.py)：`render_position_lifecycle_story`；公共包入口不变 | [cli/inspect_cmd.py](../../../core/aletheia/cli/inspect_cmd.py)；Opportunity Story 也消费该公共入口 | [test_position_lifecycle_story.py](../../../tests/test_position_lifecycle_story.py)、[test_position_lifecycle_story_readonly.py](../../../tests/audit/test_position_lifecycle_story_readonly.py) | [Profit Loop Observability](../contracts/profit_loop_observability_contract.md)；Story 是只读解释，不产生交易或 Alpha 权限 |
| 公开衍生品 / order-flow 事实 | [derivatives_facts.py](../../../core/aletheia/data/derivatives_facts.py)；[positioning_evidence.py](../../../extensions/data/positioning_evidence.py)：`read_positioning_evidence` | [cli/data_cmd.py](../../../core/aletheia/cli/data_cmd.py) 消费 positioning reader；source collector 归 Extensions | [test_positioning_evidence.py](../../../tests/test_positioning_evidence.py)、[test_derivatives_facts_contract.py](../../../tests/test_derivatives_facts_contract.py) | [Derivatives Economic Semantics](../contracts/derivatives_economic_semantics.md)；公共事实不同于私有账户 UTA |

<!-- navigation-check:end -->

当前入口主线：Candidate → Planning Generation → Entry Planner / Compiler → Entry Plan
→ Gateway / Risk → Order / Fill / Position → verified Close / cost-complete Outcome。
Ranker/PaperTrader 入场主线及 Market Observer 已退役；历史 signal_source、归因查询和
历史数据读取仍可保留，不能据此重新注册生产者。

职责同名不等于重复：gateway/ 负责进程托管，execution/gateway.py 负责交易执行入口；
Core data/analytics 定义当前事实与完整性语义，Extensions 中同名域承载可选采集或研究。
合并前须证明责任相同；不按目录名强行合并。

日期化分析与反例见 [2026-09-28 收敛结论](../../research/audits/CONVERGENCE_FINDINGS_2026-09-28.md)：
包含已复核的发布事务边界、历史计数误读和测试隔离经验；不替代现行契约或当前运行检查。


---

## 零、运行托管层

| 层 | 平台/频率 | 责任 | 边界 |
|---|---|---|---|
| OS session service | macOS launchd / Linux systemd --user / Windows Task Scheduler，按需 | `gateway start` 托管 gateway runner；关闭终端/ORCA 不会终止它 | `gateway stop` 停止并删除定义；无登录触发、间隔、KeepAlive 或 Restart |
| legacy OS watchdog | Windows Task Scheduler / macOS launchd（迁移清理） | 仅识别旧版本的无人值守恢复注册 | `gateway stop` 解除注册，防止显式 stop 后复活 |
| gateway supervisor | 常驻 | 管理 frontdoor:8787 + backend:8788，内部子进程异常时重生 | 唯一 pidfile/lifecycle 入口；不由 launchd 分别管理子进程 |

---

## 一、数据采集层（IO Executor）

| Job ID | 频率 | 写入表 | 下游消费者 | 管道 |
|--------|------|--------|-----------|------|
| market_data | 5min | market_snapshots | price_feed, universe_refresh, risk_engine(data freshness) | 源码调用路径；运行状态另查 |
| `price_feed` | 30s | (内存缓存 watched_prices；输入保留 venue+spot/perp) | position_manager(mark-to-market), portfolio_snapshot | ✅ |
| data_collectors | 2min | orderbook_snapshots, funding_rates, oi_snapshots | universe/service | 主所采集按 InstrumentId 路由；只做实时 orderbook/Funding/OI |
| data_collector_context | 10min | liquidation_events, macro_snapshots, volume ranking, cross-venue OI, CoinGecko/Hyperliquid/DefiLlama context | universe/service | 独立 55s 时间盒；与主所 CCXT 串行，不让慢上下文继承核心预算 |
| `exchange_sync` | 5min | positions(sync), portfolio_snapshots(side-effect) | risk_engine(exposure), position_manager, UI | ✅ |
| `portfolio_snapshot` | 10min | portfolio_snapshots | risk_engine(drawdown scale), UI, Telegram | ✅ |
| `position_path_observer` | 5min | append-only `position_path_observations`（完整入场后的 expected 5m buckets；精确 instrument + provider `is_closed` 证明，冻结 OHLCV + uPnL/cost/ExitPlan snapshot；缺失原因显式保留）+ durable `position_path_observation_deferred_buckets` | `aletheia inspect position-path`、`aletheia inspect stop-loss-path`、`aletheia control status`；事后区分 accounting mismatch、cost flip、profit giveback、never-favorable candidate，证据不全保持 unknown | ✅ 纯观测；每轮从最早未 materialize bucket 恢复，每 Position 最多执行两个 bounded catch-up pass，按 exact instrument 分组、有界并发、单请求 timeout；重试耗尽落 unavailable；不信任无 closed proof 的缓存；不改 Position、ExitPlan、Risk、Execution、Learning 或 Live |
| `universe_refresh` | 10min | universe_members, universe_feature_snapshots，snapshot-scoped Candidates/Planning Generations，Learning treatment/control exposures，filter_decision_batches/filter_decisions/filter_candidate_outcomes；每55分钟至多另写一批前瞻 `universe_twin_shadow_batches/records`，并只为 assigned-policy selected 候选登记 4h outcome | Candidate Selection Utility、Entry Planner exact-candidate context、filter controller、risk live-evidence、doctor、guard candidate；Twin 记录完整 scanner-run assignment/evidence、0.5 propensity 与 observed-arm cluster ITT utility，未选候选记 0；其 evaluator 不写 Learning verdict | ✅ step-v1 仍是唯一权威；Twin 写入失败不回滚 snapshot/Candidate，且不能影响 Candidate/Risk/Execution/Learning；availability/lineage/NI/anytime-valid 仍为 blocker |
| `outcome_settler` | 10min | 更新 filter_candidate_outcomes（UFO 已于 v94 删除，#3701） | 净收益校准、filter controller、alpha attribution、doctor | ✅ |
| `outcome_hook_replay` | 1min | outcome_hook_jobs 状态、shadow_trades 结果、serenity_post_mortems | 合格平仓后反馈；每次重读 Position，缺价或部分失败留待重试 | ✅ |
| cleanup | cron 03:17 | 分析/遥测行保留窗口由 config/schema.py 和 scheduler 清理入口共同定义；开放实验保留保护 | SQLite 稳定性 + data_pruned audit | 不触碰交易因果表，不把不可证明的清理结果当作已完成 |
| `ohlcv_nightly_backfill` | cron(可配) | market_ohlcv | (历史 OHLCV 存储;backtest 消费者随引擎删除 Position C) | ✅ |
| `ohlcv_active_backfill` | 5–60min(可配，默认10min) | market_ohlcv + ohlcv_backfill_state | 活动 Universe 指标、结果结算、doctor；单轮1–10个精确 instrument/timeframe，失败退避 | ✅，不阻塞 universe_refresh |
| `news_fetch` | 15min(可配) | `news_items(status=new)` | news_analyze | ✅ RSS I/O 与 LLM 分离 |
| news_analyze | 5min | 更新 news_items sentiment/relevance/status | Research Worker / 当前上下文消费者 | 单批3条；失败退避/终止，不伪造 analyzed；独立 timeout worker 不阻塞 RSS |
| owner Position X aggregate/sanitized-holder import | explicit CLI only; sanitized batches compute an exact cross-sectional universe before storage | whitelisted perp_structure_snapshots + source/batch hash + coverage/universe audit; no holder rows or identities; seeds perp_structure_shadow_outcomes | System Operator advisory context + 1h/4h/24h shadow settlement + read-only multi-baseline gross evaluation | optional observation; no automatic endpoint/scheduler; cost-aware unavailable; decision influence disabled |
| `okx_demo_symbols` | 4h | `execution.venue_capabilities` 内存 capability 缓存 | Gateway 精确 market 的 symbol availability 检查 | ✅ 保留 sandbox 产品事实刷新；不是独立 broker 或当前交易准入 |
| `venue_margin_facts` | 5min | settings(`venue_margin_envelope.<venue>.<symbol>.<market_type>`) | Entry Compiler 的确定性杠杆解析、Risk submit-time 复核（#3729） | ✅ 只维护 Planner 当前可认领队列读到的 instrument（`list_candidate_planning_demand`）；新鲜行跳过、缺失/过期才取公开 tier API；exact venue 是持仓场馆（`execution_mode_venue`），失败留 typed unavailable 且永不默认 MMR |

---

## 二、Agent 决策层（Agents Executor）

Market Observer 不再注册为调度任务；历史归因与读取不代表活动生产者。

| Job ID | 频率 | 输入数据 | 输出数据 | 下游 | 管道 |
|--------|------|---------|---------|------|------|
| `pi_trading_decision` | ~15min + committed Candidate wake | 当前 Candidate Planning Generation、冻结 source/context、精确 market windows、typed capability/profile facts | Planner hypothesis → deterministic Compiler → immutable Entry Plan，或 typed blocked/unavailable/no-plan outcome | fast_trigger_watch / Entry Workbench → Proposal → Risk/Gateway | ✅ 事件唤醒与 heartbeat 共用 `run_entry_planner_batch()`；持久 claim/retry/CAS 防止重复模型和计划 |
| `position_manager` | 30s | open positions + exact-instrument marks + exit rules | close proposals / TP-SL updates | ✅ |
| `position_reconcile` | 5min | exchange positions vs local | reconciliation_reports | ✅ |
| `tpsl_fill_check` | 90s | pending TP/SL orders | fill records | ✅ |
| `post_trade_reviewer` | 4h | closed positions | knowledge_items(post_trade_review) | dreaming_agent, retrieve_lessons() | ✅ |
| `risk_critic` | 6h | proposal / risk_decisions history | medium-confidence `research_note`（subtype=risk_lesson） | advisory note / audit；不进入 `retrieve_lessons()` 的 high-confidence 池 | ✅ 不写 RiskDecision 或改硬风控 |
| `equity_observer` | 工作日 09:30-16:00 America/New_York /30min | yfinance + bitget xStocks | opportunities(equity) | research/analytics（observation only） | ✅ |
| `prediction_observer` | 30min | polymarket/kalshi API | prediction_market_runs + opportunities | divergence_scanner / research analytics（observation only） | ✅ |
| `guard_promotion` | cron 04:00 CST | learned guards / candidates 的 expires_at | 仅 TTL 到期 deprecated | 显式 owner review 才可晋级；定时任务不自动晋级或按历史亏损率降级 | ✅ 保留任务身份及暂停/Learning gates |
| `dreaming_daily` | cron 02:00 CST | knowledge_items + positions | knowledge_items(dreaming_log), serenity_* tables | 合格研究经 `publish_serenity_candidates` 发布 Candidate / PlanningGeneration，再由 Planner / Compiler 生成 EntryPlan | ✅ 每日批次键防重；不恢复 PiDecision 入场 |
| `serenity_startup_catchup` | gateway 启动后4min一次 | 最近成功 Serenity run + 当前活动/批次记录 | 缺失时创建一个 scheduled research run | ✅ 补 02:00 漏跑；fresh/active/attempted 均 no-op，暂停恢复后仍可执行 |
| `learning_startup_catchup` | gateway 启动后5min一次 | learning_job_health 每日成功水位 | 只补最新漏跑的 dreaming/memo/reviewer 日期窗口 | ✅ 成功窗口只跑一次；失败不认领，可在下次启动重试 |
| `insight_synthesizer` | cron 02:30 CST + durable boot catch-up | knowledge_items + market_snapshots | trading_memo(knowledge_items) | retrieve_lessons() | ✅ |
| `confidence_calibration` | 6h | predictions + outcomes | calibration reports | UI(analytics) | ⚠️ |
| `filter_controller_observe` | 6h（配置开启时） | 逐 filter/version/family/direction/market/horizon 的 pass/reject 净结果与未来首次样本 | controller_recommendations + filter_shadow_experiments/evaluations | CLI/API 诊断；只登记和评估 shadow，不写 config | ✅ 只读 |
| Historical Python Operator assessments | retired under #3134 | persisted history only | operator_assessment / audit records | read-only inspection; no scheduler or fallback writer | archived |
| Current Operator runtime (`operator-shell/`) | Gateway-supervised subprocess, no scheduler row | agent_shell_heartbeats / tasks / sessions / messages / events / artifacts | 状态面板（经 `/api/operator/status` 派生）、operator 自身的任务与 recommendation | 仅经 least-privilege operator surface 读写；不跑自己的模型循环；见 [The Operator runtime](../architecture/OPERATOR_SHELL.md) 与 [Status Surface Contract](../contracts/status_surface_contract.md) | ✅ 由 `OperatorShellMonitor` 守护 |

Learning 行为生产任务受 `agent.autonomous_learning` 控制；结果结算、funding、durable outcome replay 与 Observation Lab 保留各自独立边界。旧 contribution evaluator、measurement catch-up 和 lifecycle controller/reconciler 已退役，历史任务记录不代表当前调度或晋级权限；见 [Agent Contracts](../contracts/agent_contracts.md#1-scheduler-job-overview)。

### Opportunity 生命周期身份

| 身份 | 含义 | 权威出口 |
|---|---|---|
| `Candidate + Planning Generation` | execution-eligible Candidate 的当前规划注意力；冻结 source/context、cycle lineage、软/硬期限与 claim | `universe.service` 发布；`entry_planner` 原子认领、重试或终结 |
| `Entry Plan` | 一个经 Planner/Compiler 验证的不可变执行意图，绑定 Candidate 与 exact instrument | `entry_planner`/Compiler 创建；fast monitoring 与 Gateway 只读消费 |
| `planning_blocked/unavailable/failed` | 规划证据、权限、模型或编译失败的显式 typed 结果；不伪造 NO_PLAN 或 Proposal | 原因持久化；满足条件后恢复同一 cycle，或由 owner 重新规划 |

`Opportunity`、`signal_pending`、旧 Ranker/Funnel 记录仍可被历史分析读取，但不再是
当前入场队列、System Operator claim 或 Proposal authority。研究与观察 Opportunity
也不能绕过 Candidate Planning Generation、Entry Plan、Risk 或 Gateway。

Owner-facing Overview、API 和 CLI 使用同一只读 projection：`emitted_count` 是 generations，
`setup_count` 是 `symbol + market + direction + scanner_family` 唯一 setup，
`terminal_breakdown` 区分 snapshot supersede、true TTL、Operator 与 deterministic exits。

---

## 三、风控与执行层（IO Executor）

| 模块 | 触发方式 | 输入 | 输出 | 管道 |
|------|---------|------|------|------|
| risk_engine.evaluate_proposal() | 被 gateway.submit_proposal() 调用 | TradeProposal + config | RiskDecision(持久化) | ✅ |
| execution/gateway.submit_proposal() | 被 Entry Plan execution / position_manager 调用 | symbol/direction/qty/price | proposal→decision→order→fill 因果链 | ✅ |
| execution/reconciliation.reconcile() | reconciliation job(2min)；覆盖 Bitget demo/live 与其他 UTA 交易所账户模式（`bitget_demo` 也在内） | 未决 Order vs 交易所精确 Order/Fill 报告 | Order 状态、已证私有 Fill 物化、`order_reconciled` / `order_reconciliation_inconclusive` 审计事件 | ✅ |
| kill_switch | risk_engine check#8(日损触发) | daily_loss > threshold | kill_switch_active=True | ✅ |

---

## 四、知识与学习层

| 模块 | 写入路径 | 读取路径 | 管道 |
|------|---------|---------|------|
| knowledge_items + knowledge_fts | post_trade_reviewer, risk_critic, dreaming_agent, insight_synthesizer | 仅符合 active + high-confidence 与文档完整性门槛的项可由 `retrieve_lessons()` 提供 advisory context；Risk Critic 的 medium-confidence note 不在该池 | ✅ 存储研究记录不等于交易上下文准入 |
| trading_guards + guard_candidates | universe_refresh(Tier1 auto-gen), dreaming_agent | advisory candidate context；automatic lesson block → shadow-only candidate（历史自动晋级跳过） | ✅ 不伪装成 Risk Engine lesson |
| ps_lessons (LessonStore) | dreaming_agent → lesson_reviewer | Entry Planner learning context / System Operator review → bounded advisory use | ✅ 晋级/推翻只认同标的、同方向、同 scanner 的干净净收益；全局样本只作背景，不能代替窄范围门槛 |
| historical Ranker case/memo Learning | trade_case_study + latest trading_memo | 只读历史 funnel/attribution reports | 🟡 不创建当前 Entry exposure，不改变 Candidate/Risk/Execution |
| persona sizing Learning | clean realized regime cohort + causal opportunity outcome | trading/sizing → System Operator proposal rationale | 🟡 可显示静态/学习建议/实际金额，但 measurement-unsupported；实际金额保持静态 baseline，不创建 formal exposure |
| ps_skills (SkillStore) | lesson_reviewer(applied lesson→draft skill), persistence API | persistence API / manual review | ✅ draft producer 已接入;不会自动 activate(见 knowledge_contract §6.A2) |
| measured Learning ledger / `lco-v1` | Universe calibration 写受支持 exposure；Operator bundle 与其他 hooks 返回 static fallback；canonical full-arm filter outcomes + selected-member Universe mirror/fallback | 内部 inspection/doctor + cached `/api/analytics/learning-contribution` summary + Learning measured-path selector | 🟡 当前仅提供有界只读 summary；旧 evaluator、startup measurement catch-up 与 lifecycle controller 已退役，不运行、不提供 admission mutation、不改 Candidate/Risk/Execution/Live |

---

## 五、配置层（前端覆盖状态）

| Config 类 | 字段数 | 前端覆盖率 | 缺口 |
|-----------|-------|-----------|------|
| ExchangeConfig | 5 | 100% | — |
| RiskConfig | 18 | 94% | allowed_exchange(immutable,合理) |
| TelegramConfig | 8 | 100% | — |
| UniverseConfig | 13 | 100% | — |
| TradingConfig | 17 | 88% | — |
| MarketDataConfig | 14 | 50% | 7字段无UI |
| OperatorConfig | 9 | 11% | 8字段无UI(多为内部参数) |
| **RegimeConfig** | 4 | **0%** | 完全无控制 |
| **GuardConfig** | 5 | **0%** | 完全无控制 |
| MarketAwareExitsConfig | 9 | ~56% (5/9) | enabled/urgent/tighten/collapse/atr 已接入(Settings.tsx:1648);max_signal_age 等 4 字段无 UI |

---

## 六、历史管道断裂的当前核准（历史证据见 [PIPELINE_GAPS](../../archive/legacy/issues/archive/audits/PIPELINE_GAPS.md)）

| 级别 | 问题 | 影响 |
|------|------|------|
| A1 | ~~ps_lessons↔FTS 脱钩~~ → **已澄清为有意双轨** | 经验经 LessonStore→System Operator prompt→risk 影响决策;不入 FTS 是设计选择(knowledge_contract §6.A1) |
| Historical A2 (Knowledge gap) | ~~ps_skills 无生产者~~ | 已修: lesson_reviewer 产 draft skill;人工审核后才可 activate；与 `learning_contribution_observability` 无关 |
| A3 | liquidation_events 不进风控 | 清算数据采集浪费 |
| B1 | detect_contradictions() | 已有保守 support-vs-block 检测；UI 只呈现"需复核"数量，不自动裁决 |
| B2 | Serenity 研究经 canonical Candidate publication 消费 | 真实 PA/资格证据通过后发布 Candidate / PlanningGeneration；后续必须经 Planner / Compiler / EntryPlan / Risk / Gateway，见 [Serenity Contract](../contracts/serenity_contract.md) |
| C1 | 6个analytics端点无前端UI | 已有能力不可达 |
| C2 | Regime/Guard Config 无前端控制(MarketAwareExits 已部分接入) | regime/guard 只能手编 yaml |

> Historical A1/A2/B1 现状（双轨已澄清 / draft skill producer 已接入 / contradiction 保守检测已实现）详见 [knowledge_contract.md §6](../contracts/knowledge_contract.md)。这里的 historical A2 不是新的 Learning Contribution Observability feature；旧 C1/A3 只作为 [`BACKEND_ISSUES.md`](../../archive/legacy/issues/domain/BACKEND_ISSUES.md) 历史证据保留。**Last reviewed 2026-07-28**。
