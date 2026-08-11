# System Map — 全模块概念地图

> 一页俯瞰系统所有活动部件、数据流向、管道状态。
> 断管道标记: ✅ 闭环 | ⚠️ 窄消费（仅API/CLI） | ❌ 断裂（写入无消费）
> Last synced with code/design: 2026-08-08 (Universe Twin Task 6)

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
| `market_data` | 5min | market_snapshots | price_feed, market_observer, paper_trader_ranker, risk_engine(data freshness) | ✅ |
| `price_feed` | 30s | (内存缓存 watched_prices；输入保留 venue+spot/perp) | position_manager(mark-to-market), portfolio_snapshot | ✅ |
| `data_collectors` | 2min | orderbook_snapshots, funding_rates, oi_snapshots | market_observer + universe/service | ⚠️ 主所采集按 InstrumentId 路由；只做实时 orderbook/Funding/OI |
| `data_collector_context` | 10min | liquidation_events, macro_snapshots, volume ranking, cross-venue OI, CoinGecko/Hyperliquid/DefiLlama context | market_observer + universe/service | ⚠️ 独立 55s 时间盒；与主所 CCXT 串行，不让慢上下文继承核心预算 |
| `exchange_sync` | 5min | positions(sync), portfolio_snapshots(side-effect) | risk_engine(exposure), position_manager, UI | ✅ |
| `portfolio_snapshot` | 10min | portfolio_snapshots | risk_engine(drawdown scale), UI, Telegram | ✅ |
| `universe_refresh` | 10min | universe_members, universe_feature_snapshots, universe_forward_outcomes，snapshot-scoped open Opportunities，Learning treatment/control exposures，filter_decision_batches/filter_decisions/filter_candidate_outcomes；每55分钟至多另写一批前瞻 `universe_twin_shadow_batches/records`，并只为 assigned-policy selected 候选登记 4h outcome | paper_trader_ranker、System Operator exact-candidate hydration/family edge、filter controller、risk live-evidence、doctor、guard candidate；Twin 记录完整 scanner-run assignment/evidence、0.5 propensity 与 observed-arm cluster ITT utility，未选候选记 0；其 evaluator 不写 Learning verdict | ✅ step-v1 仍是唯一权威；Twin 写入失败不回滚 snapshot/Opportunity，且不能影响 Paper/Risk/Execution/Learning；availability/lineage/NI/anytime-valid 仍为 blocker |
| `outcome_settler` | 10min | 更新 universe_forward_outcomes + filter_candidate_outcomes；同步新 shadow 净结果 | 净收益校准、filter controller、alpha attribution、doctor | ✅ |
| `outcome_hook_replay` | 1min | outcome_hook_jobs 状态、shadow_trades 结果、serenity_post_mortems | 合格平仓后反馈；每次重读 Position，缺价或部分失败留待重试 | ✅ |
| `learning_contribution_evaluator` | `learning.evaluation_interval_minutes`（默认60min） | learning_contribution_verdicts, learning_job_health | paper lifecycle read model、operator inspection | ✅ 只测量：即使自主学习关闭或系统暂停也可运行；不改阶段、不启用影响 |
| `learning_measurement_startup_catchup` | gateway 启动后2min一次 | learning_job_health 当前小时水位 | contribution evaluator | ✅ 只补当前缺失测量窗口，已完成则 no-op |
| `cleanup` | cron 03:17 | 删除超保留期的分析/遥测行；filter batch 30d、decision/outcome 90d | SQLite 稳定性 + `data_pruned` audit | ✅，不触碰交易因果表 |
| `ohlcv_nightly_backfill` | cron(可配) | market_ohlcv | (历史 OHLCV 存储;backtest 消费者随引擎删除 Position C) | ✅ |
| `ohlcv_active_backfill` | 5–60min(可配，默认10min) | market_ohlcv + ohlcv_backfill_state | 活动 Universe 指标、结果结算、doctor；单轮1–10个精确 instrument/timeframe，失败退避 | ✅，不阻塞 universe_refresh |
| `news_fetch` | 15min(可配) | `news_items(status=new)` | news_analyze | ✅ RSS I/O 与 LLM 分离 |
| `news_analyze` | 5min | 更新 `news_items` sentiment/relevance/status | Market Observer + Research Worker | ✅ 单批3条；失败退避/终止，不伪造 analyzed；独立 timeout worker 不阻塞 RSS |
| owner Position X aggregate/sanitized-holder import | explicit CLI only; sanitized batches compute an exact cross-sectional universe before storage | whitelisted `perp_structure_snapshots` + source/batch hash + coverage/universe audit; no holder rows or identities; seeds `perp_structure_shadow_outcomes` | Market Observer/System Operator advisory context + 1h/4h/24h shadow settlement + read-only multi-baseline gross evaluation | 🟡 no automatic endpoint/scheduler; retrospective imports fail evaluation; cost-aware unavailable; decision influence disabled |
| `okx_demo_symbols` | 4h | (内存 set 缓存) | okx_broker(filter) | ✅ |

---

## 二、Agent 决策层（Agents Executor）

| Job ID | 频率 | 输入数据 | 输出数据 | 下游 | 管道 |
|--------|------|---------|---------|------|------|
| `market_observer` | 10min | 已通过 deterministic emitter 的 open Opportunity + exact-instrument context | namespaced `metadata.observer_assessment` advisory enrichment | 无阻塞下游；scanner 已独立唤醒 Ranker | ✅ exact snapshot/member/instrument + emitter pass fact；不新建 Opportunity |
| `paper_trader` | 3min fallback + on-demand | open Opportunity generations + PiDecision + guards | 每轮最多 3 个新 `signal_pending`，全局最多 5 个 unresolved queued carriers；随后由 System Operator 决定是否形成 Proposal | ✅ 新 Opportunity/config/Guard/queue-slot/Universe 事实提交后合并唤醒；active carrier 与 30min cooldown 分开；队列满时 generation 保持 open 重试 |
| `pi_trading_decision` | ~15min | market context + one Learning bundle（reflection/memo/reasoning/calibration/cases/lessons） | PiDecision（含 opportunity/proposal 因果 id + learning_manifest_json） | paper_trader(消费 decision) | ✅ |
| `position_manager` | 5min | open positions + market prices + exit_rules | close proposals / TP-SL updates | ✅ |
| `position_reconcile` | 5min | exchange positions vs local | reconciliation_reports | ✅ |
| `tpsl_fill_check` | 90s | pending TP/SL orders | fill records | ✅ |
| `post_trade_reviewer` | 4h | closed positions | knowledge_items(post_trade_review) | dreaming_agent, retrieve_lessons() | ✅ |
| `risk_critic` | 6h | risk_decisions history | knowledge_items(lesson) | retrieve_lessons() → LLM prompts | ✅ |
| `equity_observer` | 工作日 14-21UTC /30min | yfinance + bitget xStocks | opportunities(equity) | paper_trader_ranker | ✅ |
| `prediction_observer` | 30min | polymarket/kalshi API | prediction_market_runs + opportunities | paper_trader_ranker, divergence_scanner | ✅ |
| `guard_promotion` | cron 04:00 CST | guard_candidate knowledge_items + reviewer health/backlog | trading_guard(active) | ✅ Learning 总开关关闭或审核失败/过期/积压时仅冻结新自动晋级，不改变现有 guard |
| `dreaming_daily` | cron 02:00 CST | knowledge_items + positions | knowledge_items(dreaming_log), serenity_* tables | ✅ Serenity 可经 promote→PiDecision 进入评估；每日批次键防重 |
| `serenity_startup_catchup` | gateway 启动后4min一次 | 最近成功 Serenity run + 当前活动/批次记录 | 缺失时创建一个 scheduled research run | ✅ 补 02:00 漏跑；fresh/active/attempted 均 no-op，暂停恢复后仍可执行 |
| `learning_startup_catchup` | gateway 启动后5min一次 | learning_job_health 每日成功水位 | 只补最新漏跑的 dreaming/memo/reviewer 日期窗口 | ✅ 成功窗口只跑一次；失败不认领，可在下次启动重试 |
| `insight_synthesizer` | cron 02:30 CST + durable boot catch-up | knowledge_items + market_snapshots | trading_memo(knowledge_items) | retrieve_lessons() | ✅ |
| `confidence_calibration` | 6h | predictions + outcomes | calibration reports | UI(analytics) | ⚠️ |
| `filter_controller_observe` | 6h（配置开启时） | 逐 filter/version/family/direction/market/horizon 的 pass/reject 净结果与未来首次样本 | controller_recommendations + filter_shadow_experiments/evaluations | CLI/API 诊断；只登记和评估 shadow，不写 config | ✅ 只读 |
| `learning_lifecycle_controller` | `learning.evaluation_interval_minutes`（默认60min） | 当前阶段测量结果、实验/策略版本、Learning 设置 | learning_lifecycle_events + 后继纸面实验或冻结状态 | ✅ 与测量分离；仅在完整安全开关、路径白名单和无完整性事故时改变纸面状态 |
| `pi_operator_loop` | cron 每4h:15 CST + boot 2min catch-up（assessment 3h45m 防重） | system health + strategies + positions | operator_assessment, operator_actions, PiDecision(recommendations) | pi_daemon(wake signal) | ✅ |

上表中会产出、审核、晋级或自动应用 Learning 状态的任务统一受
`agent.autonomous_learning` 控制；结果结算、shadow 结果核验和 contribution measurement
不受该开关影响。生命周期控制仍同时要求未暂停、无完整性事故、已启用生命周期与纸面影响，
并且路径在白名单中。

### Opportunity 生命周期身份

| 身份 | 含义 | 权威出口 |
|---|---|---|
| `open Opportunity` | 一次 Universe/Observer snapshot 的分析 generation，可继续被 Ranker 处理 | snapshot supersede、Ranker hard TTL/global retention、确定性永久拒绝，或原子认领为 queued |
| `queued Opportunity + signal_pending` | 已通过 Ranker admission 的耐久 Operator carrier；来源 snapshot/member id 保持不可变归因 | Opportunity `valid_until`、System Operator terminal decision，或原子 Proposal claim |
| `reviewed/expired` | 终态，不得倒退 | 必须由 PiDecision 或 typed filter evidence 解释；raw `expired` 不能直接等同 TTL |

Owner-facing Overview、API 和 CLI 使用同一只读 projection：`emitted_count` 是 generations，
`setup_count` 是 `symbol + market + direction + scanner_family` 唯一 setup，
`terminal_breakdown` 区分 snapshot supersede、true TTL、Operator 与 deterministic exits。

---

## 三、风控与执行层（IO Executor）

| 模块 | 触发方式 | 输入 | 输出 | 管道 |
|------|---------|------|------|------|
| risk_engine.evaluate_proposal() | 被 gateway.submit_proposal() 调用 | TradeProposal + config | RiskDecision(持久化) | ✅ |
| execution/gateway.submit_proposal() | 被 paper_trader/position_manager 调用 | symbol/direction/qty/price | proposal→decision→order→fill 因果链 | ✅ |
| execution/reconciliation.reconcile() | reconciliation job(2min,仅live) | exchange state vs local | convergence_records | ✅ |
| kill_switch | risk_engine check#8(日损触发) | daily_loss > threshold | kill_switch_active=True | ✅ |

---

## 四、知识与学习层

| 模块 | 写入路径 | 读取路径 | 管道 |
|------|---------|---------|------|
| knowledge_items + knowledge_fts | post_trade_reviewer, risk_critic, dreaming_agent, insight_synthesizer | retrieve_lessons() → LLM prompts (市观/paper_trader_ranker/trading_decision(PI)) | ✅ |
| trading_guards + guard_candidates | universe_refresh(Tier1 auto-gen), dreaming_agent | active guard → paper_trader_ranker；automatic lesson block → shadow-only candidate（历史自动晋级跳过） | ✅ 不伪装成 Risk Engine lesson |
| ps_lessons (LessonStore) | dreaming_agent → lesson_reviewer | trading_context Layer3 → System Operator prompt → risk engine #15(applied, non-expired) | ✅ 晋级/推翻只认同标的、同方向、同 scanner 的干净净收益；全局样本只作背景，不能代替窄范围门槛 |
| ranker case/memo Learning | trade_case_study + latest trading_memo | paper_trader_ranker score | 🟡 consumer hook 已接，但 measurement-unsupported；实际增量固定为 0，不创建 formal exposure |
| persona sizing Learning | clean realized regime cohort + causal opportunity outcome | trading/sizing → System Operator proposal rationale | 🟡 可显示静态/学习建议/实际金额，但 measurement-unsupported；实际金额保持静态 baseline，不创建 formal exposure |
| ps_skills (SkillStore) | lesson_reviewer(applied lesson→draft skill), persistence API | persistence API / manual review | ✅ draft producer 已接入;不会自动 activate(见 knowledge_contract §6.A2) |
| measured Learning ledger / `lco-v1` | Universe calibration 写受支持 exposure；Operator bundle 与其他 hooks 返回 static fallback；evaluator/lifecycle 写 7 个永久 Learning 表；canonical full-arm filter outcomes + selected-member Universe mirror/fallback | 内部 inspection/doctor + cached `/api/analytics/learning-contribution` summary + Learning measured-path selector | 🟡 有界只读 summary、registry-backed 选择、cache/timing 与当前 surface 响应式无障碍已实现；明细/分页与完整 workbench 仍缺失；不运行 evaluator/lifecycle、不提供 admission mutation、不改 Paper/Risk/Execution/Live |

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
| B2 | serenity 4表 → promote→PiDecision **已闭环** | 原始4表仍不进 FTS,但 promote 路径已建(spec_serenity_promote) |
| C1 | 6个analytics端点无前端UI | 已有能力不可达 |
| C2 | Regime/Guard Config 无前端控制(MarketAwareExits 已部分接入) | regime/guard 只能手编 yaml |

> Historical A1/A2/B1 现状（双轨已澄清 / draft skill producer 已接入 / contradiction 保守检测已实现）详见 [knowledge_contract.md §6](../contracts/knowledge_contract.md)。这里的 historical A2 不是新的 Learning Contribution Observability feature；旧 C1/A3 只作为 [`BACKEND_ISSUES.md`](../../archive/legacy/issues/domain/BACKEND_ISSUES.md) 历史证据保留。**Last reviewed 2026-07-28**。
