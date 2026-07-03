# System Map — 全模块概念地图

> 一页俯瞰系统所有活动部件、数据流向、管道状态。
> 断管道标记: ✅ 闭环 | ⚠️ 窄消费（仅API/CLI） | ❌ 断裂（写入无消费）
> Last synced with code: 2026-06-23

---

## 一、数据采集层（IO Executor）

| Job ID | 频率 | 写入表 | 下游消费者 | 管道 |
|--------|------|--------|-----------|------|
| `market_data` | 5min | market_snapshots | price_feed, market_observer, paper_trader_ranker, risk_engine(data freshness) | ✅ |
| `price_feed` | 30s | (内存缓存 watched_prices) | position_manager(mark-to-market), portfolio_snapshot | ✅ |
| `data_collectors` | 2min | orderbook_snapshots, funding_rates, oi_snapshots, liquidation_events, macro_snapshots, volume_profiles | market_observer(context_builder 部分消费) | ⚠️ liquidation_events 不进风控 |
| `exchange_sync` | 5min | positions(sync), portfolio_snapshots(side-effect) | risk_engine(exposure), position_manager, UI | ✅ |
| `portfolio_snapshot` | 10min | portfolio_snapshots | risk_engine(drawdown scale), UI, Telegram | ✅ |
| `universe_refresh` | 10min | universe_members, universe_feature_snapshots, universe_forward_outcomes | paper_trader_ranker(universe_score), risk_engine(beta/correlation), guard_candidate generation | ✅ |
| `ohlcv_nightly_backfill` | cron(可配) | ohlcv_bars | (历史 OHLCV 存储;backtest 消费者随引擎删除 Position C) | ✅ |
| `okx_demo_symbols` | 4h | (内存 set 缓存) | okx_broker(filter) | ✅ |

---

## 二、Agent 决策层（Agents Executor）

| Job ID | 频率 | 输入数据 | 输出数据 | 下游 | 管道 |
|--------|------|---------|---------|------|------|
| `market_observer` | 15min | market_snapshots + context_builder(微结构) | opportunities, knowledge_items(research) | paper_trader_ranker | ✅ |
| `paper_trader` | 5min | opportunities + PiDecision + guards | trade_proposals → risk_engine → orders → fills | ✅ 核心链路 |
| `pi_trading_decision` | ~15min | market context + guards + lessons | PiDecision(pi_decisions 表) | paper_trader(消费 decision) | ✅ |
| `position_manager` | 5min | open positions + market prices + exit_rules | close proposals / TP-SL updates | ✅ |
| `position_reconcile` | 5min | exchange positions vs local | reconciliation_reports | ✅ |
| `tpsl_fill_check` | 90s | pending TP/SL orders | fill records | ✅ |
| `post_trade_reviewer` | 4h | closed positions | knowledge_items(post_trade_review) | dreaming_agent, retrieve_lessons() | ✅ |
| `risk_critic` | 6h | risk_decisions history | knowledge_items(lesson) | retrieve_lessons() → LLM prompts | ✅ |
| `equity_observer` | 工作日 14-21UTC /30min | yfinance + bitget xStocks | opportunities(equity) | paper_trader_ranker | ✅ |
| `prediction_observer` | 30min | polymarket/kalshi API | prediction_market_runs + opportunities | paper_trader_ranker, divergence_scanner | ✅ |
| `guard_promotion` | cron 04:00 CST | guard_candidate knowledge_items | trading_guard(active) | ✅ 学习任务(引擎删除后从 backtest_orchestrator 重挂,Position C) |
| `dreaming_daily` | cron 02:00 CST | knowledge_items + positions | knowledge_items(dreaming_log), serenity_* tables | ⚠️ serenity 4表仅API消费 |
| `insight_synthesizer` | cron 02:30 CST | knowledge_items + market_snapshots | trading_memo(knowledge_items) | retrieve_lessons() | ✅ |
| `confidence_calibration` | 6h | predictions + outcomes | calibration reports | UI(analytics) | ⚠️ |
| `pi_operator_loop` | cron 每4h:15 CST | system health + strategies + positions | operator_actions, PiDecision(recommendations) | pi_daemon(wake signal) | ✅ |

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
| trading_guards + guard_candidates | universe_refresh(Tier1 auto-gen), dreaming_agent | paper_trader_ranker(evaluate_guards), risk_engine(check#15) | ✅ |
| ps_lessons (LessonStore) | dreaming_agent → lesson_reviewer | trading_context Layer3 → System Operator prompt → risk engine #15(applied, non-expired) | ✅ 双轨:**影响决策**但不进 knowledge_fts(有意设计,见 knowledge_contract §6.A1) |
| ps_skills (SkillStore) | lesson_reviewer(applied lesson→draft skill), persistence API | persistence API / manual review | ✅ draft producer 已接入;不会自动 activate(见 knowledge_contract §6.A2) |

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

## 六、已确认的管道断裂（详见 [PIPELINE_GAPS](../issues/archive/PIPELINE_GAPS.md)）

| 级别 | 问题 | 影响 |
|------|------|------|
| A1 | ~~ps_lessons↔FTS 脱钩~~ → **已澄清为有意双轨** | 经验经 LessonStore→PI prompt→risk 影响决策;不入 FTS 是设计选择(knowledge_contract §6.A1) |
| A2 | ~~ps_skills 无生产者~~ | 已修: lesson_reviewer 产 draft skill;人工审核后才可 activate |
| A3 | liquidation_events 不进风控 | 清算数据采集浪费 |
| B1 | detect_contradictions() | 已有保守 support-vs-block 检测；UI 只呈现"需复核"数量，不自动裁决 |
| B2 | serenity 4表 → promote→PiDecision **已闭环** | 原始4表仍不进 FTS,但 promote 路径已建(spec_serenity_promote) |
| C1 | 6个analytics端点无前端UI | 已有能力不可达 |
| C2 | Regime/Guard Config 无前端控制(MarketAwareExits 已部分接入) | regime/guard 只能手编 yaml |

> A1/A2/B1 现状(双轨已澄清 / draft skill producer 已接入 / contradiction 保守检测已实现)详见 [knowledge_contract.md §6](knowledge_contract.md)。C1/A3 记录于 [BACKEND_ISSUES.md](../issues/BACKEND_ISSUES.md)。**Last reviewed 2026-07-03**(本表 §四/§五/§六 已对齐当前代码与契约)。
