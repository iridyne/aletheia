# 交易流程合同审查（trading_pipeline）

## scheduler_contract.md（403 行）

**角色**：Contract 为主，但混入 Proposal（§3.3 "Owner-facing projection (target…)" 描述 `learning_observability.md` 的目标 read model，scheduler_contract.md:197-215）、元评论（INV-SCH-11 后的 "Contract Delta classification. This is D1"，:241）以及 Storage 合同的复制品（§5 SQLite 四支柱，:296-336，归 storage 合同所有）。

**真相核对（job 表 vs `core/aletheia/agent_runtime/scheduler.py` 的 `id="..."`）**：
- 代码 43 个 `id=`（scheduler.py:1393-1739），合同 §3 表 43 行，集合**完全一致**（diff 为空）。抽查 trigger/executor/misfire/timeout：market_data、pi_trading_decision（guard 名为 `pi_trading_heartbeat`，scheduler.py:1478，合同 §4.2 已说明）、position_manager、universe_refresh、outcome_settler、venue_margin_facts、tpsl_fill_check 全部吻合；`_ASYNC_JOB_TIMEOUT_SEC`（scheduler.py:203-245）与 timeout 列吻合。
- `_EXECUTOR_WORKERS`（scheduler.py:724）8 池与 §2 一致；`_ESSENTIAL_JOBS`（scheduler.py:134-160）存在，但合同 §1 只用一句"以实际注册为准"带过，**没有列出成员**——layered pause 是安全语义，essential 集合应是可检查清单。
- 存在：`_SYNC_LANE_DRAIN_SEC`、`_FAILURE_ALERT_THRESHOLD`、`scheduler_timeout_metrics_snapshot`、`_ensure_sync_lanes_ready`、`_PersistentAsync/SyncTimeoutWorker`、`_bounded`、`init_scheduler/stop_scheduler`（server/app.py:485,568,754）、`tests/test_layered_pause.py`、`tests/test_scheduler_timeouts.py`（含 INV-SCH-11 回归 :1569）。
- 不一致：§4.4 写 "`data_collector_context` 有独立的 55 秒时间盒"，而 `_ASYNC_JOB_TIMEOUT_SEC["data_collector_context"]=60.0`、`_DC_CONTEXT_DEADLINE_S=55.0`（scheduler.py:219,4256）——两个数是不同层，合同混写。
- 退役/冻结机制当作现行描述：§4.4 "Binance 永续行情使用 `binanceusdm`… 主行情和 xStock 各自批量落库"——Binance 已冻结、xStock（代币化股票）属 RWA 黑名单范围；代码仍有 xStock 路径（core/aletheia/trading/market_data.py:61-152），合同把它写成现行行为。
- 与产品范围冲突的 job 仍在表中：`equity_observer`（美股时段）、`prediction_observer`（预测市场）——CAPABILITY_GROUND_TRUTH 明确"Ordinary equities and prediction markets outside active instrument scope"；它们只在 research producer 组合时注册，但合同未标注 out-of-scope。
- 与 spec 0001 冲突：`lesson_reviewer`、`dreaming_daily`（ps_lessons 抽取）、`insight_synthesizer`（memo）、`confidence_calibration`（calibration feedback）、`guard_promotion`（learned guards）全部属于 spec 0001 REMOVED 清单。`guard_promotion` 的代码注释写 "candidate guards → active"（scheduler.py:1689），实际函数只 expire（scheduler.py:3912-3925）——名字与行为不符。

**具体性**：§4 失败隔离、§6 关停写得可检查（触发→响应→失败码），质量好。§3 表格是代码镜像，脆弱且有日期快照"整理（2026-10-09）"（:62）。不变量编号缺 INV-SCH-2、跳到 INV-SCH-11（且 INV-SCH-11 不在 §7 表中）。

**合理性**：调度引擎本身是主干支撑，必须留。但表里约 1/4 的 job（equity/prediction observer、lesson_reviewer、dreaming_daily、insight_synthesizer、confidence_calibration、guard_promotion、persona_rollout、filter_controller_observe、news_analyze）不在 Universe→Entry→Close→Outcome 主干上，是化石或可选 research 面。

**膨胀**：issue 号作内容（#2933、#2945+commit 2e69baae9、#3704+384cd0cb2、#1456、#1072、#90、#3741）；§3.1/§3.2 退役说明重复；§5 SQLite 复制 storage 合同。

**Verdict：SPLIT + REWRITE**

**具体修改**：
1. §3 表：保留 job_id/trigger/executor/timeout 四列，删 "整理（2026-10-09）"；增加一列 `scope`：`mainline` / `support` / `optional-research`，把 equity_observer、prediction_observer 标 `optional-research, out of instrument scope`；lesson_reviewer、dreaming_daily、insight_synthesizer、confidence_calibration、guard_promotion 标 `retiring per spec 0001 (REMOVED)`。
2. §1 增加 `_ESSENTIAL_JOBS` 成员清单（25 个，照 scheduler.py:134-160），并写成 EARS：`While layered pause is active, the scheduler shall dispatch exactly the jobs in _ESSENTIAL_JOBS.`
3. 删除 §3.3 "Owner-facing projection (target…)" 整段（:197-215）→ 归 `proposals/learning_observability.md`，合同里留一行 "Learning observability adds no job"。
4. 删除 INV-SCH-11 后的 "Contract Delta classification…D1" 段；把 INV-SCH-11 并入 §7 表；补齐或重编号 INV-SCH-2。
5. §4.4 删 Binance `binanceusdm` 与 xStock 句子（改为 "market_data persists per (venue, market_type) source at stage end"），并开 issue 删 xStock 代码路径（RWA 黑名单）。
6. §4.4 data_collector_context 改为 "job timeout 60s；内部 collector deadline 55s"。
7. §5 SQLite 四支柱压缩为一句链接到 storage 合同（`core/aletheia/storage/AGENTS.md` / persistence 合同），只保留"job 前后 rollback + close_db"这一调度器独有的不变量。
8. 删 "已退役、不再注册的 job" 行与 §3.1/§3.2 中的 issue/commit 号，改链 `docs/design/history/`。
9. guard_promotion 行注明 "expires learned guards only (no promotion)"，与 spec 0001 一起退役。

## agent_contracts.md（522 行）

**角色**：混杂。§1 是 scheduler_contract §3 的**第二份 job 表**（43 行，集合与代码完全一致，但列与描述两份各自维护）；§1 中段塞进 OS Session-service / Service-lifecycle / Supervisor outcome 三个**网关运维合同**（agent_contracts.md:85-139，与 agent IO 无关）；§2 是 agent IO 合同（本职）；§2 中大量 "retired/removed/historical" 段落（market_observer :147-157、stock_observer :171-178、Ranker/PaperTrader :211-217、Historical TradingDecision :272-280、§C Strategy Agents :334-345、Learning contribution :413-424）属 history；§3 "SQLite Concurrency/Job Timeouts/LLM Fallback/Permission Modes" 复制 storage、scheduler、llm_inference_harness、live_unlock 四份合同的事实；§4 末段 "approved future portions, not delivered claims"（:518-521）是 Proposal 语言。

**真相核对**：
- §1 job 集合 = 代码 43 个 `id=`（diff 为空）。
- 过时/错误：`reconciliation | 2min (live only)`（:32）——`_run_reconciliation`（scheduler.py:2456）无 live 门控，按 configured identities 对账；"live only" 错。`price_feed | (venue + spot/perp)`（:33）、`position_path_observer` 的 "spot/perp market"（:298）——现货出范围。`equity_observer | xStocks/equity market scan`（:15, :159-169，输入 "Bitget xStocks"）——股票/RWA 出范围。§3 "Scheduler `max_workers=3` (do not raise)"（:450）——已被 8 个命名池取代（scheduler.py:724），与 scheduler_contract §2 矛盾。`_record_model_failure:116`（:477）——实际在 fallback.py:177，行号失效。
- 存在：`complete_with_fallback`（model_provider/fallback.py:472）、`FORBIDDEN_INTENTS`（agent_shell/registry.py:350）、`agent.autonomous_learning`（config/schema.py:445）、Worker `Observe/Paper/Full`（agent_shell/permissions.py:6）、trailing（position_manager.py:659-683）、lesson_reviewer 的 draft_skill（extensions/research/lesson_reviewer.py:546）。`pi_operator_loop` 在 core/extensions 中 0 命中（确已删除）。
- Worker 权限名 `Paper`（"default Paper"）沿用已退役模拟盘的词汇；当前唯一执行路是 bitget_demo，名称语义误导（不是本次文档能改的，但合同应解释 `Paper` = demo 执行权限）。

**对照 OPERATOR_SHELL.md**：OPERATOR_SHELL.md:§7 声称 "Agent Contracts — the operator's behavioural contract"，但 agent_contracts §E（:430-436）只有 5 行"Python 循环已退役"，**没有** operator-shell 的任何行为合同（heartbeat、read/write surface、权限阶梯、task 物化、失败语义）。这些可检查的行为实际只写在 architecture 文档 OPERATOR_SHELL.md §2-§6（且其 §4 读路径与 `operator-shell/src/backend_client.ts` 的 `/api/...` 完全吻合，由 `tests/test_operator_service_auth.py`、`tests/test_api_consumer_paths.py` 锁定）。即：合同指向架构文档、架构文档指回合同，**operator 的合同实际不存在于 contracts/**。另外 operator-shell 在 `recommendation_engine.ts:125-127` 硬编码 post_trade_reviewer/risk_critic/dreaming_agent 的频率，是 job 频率的第三份拷贝；`task_runtime.ts:130-138` 把任务派发给 dreaming_agent/post_trade_reviewer/risk_critic——都是 spec 0001 要替换的学习面。

**与 spec 0001 冲突**：§D 把 post_trade_reviewer、dreaming_daily（lessons、draft-skill）、lesson_reviewer（"Overturn/renew accepted lessons"，:25）、insight_synthesizer（reasoning_feedback）、confidence_calibration、guard_promotion 写成现行学习主干；spec 0001 REMOVED 了 ps_lessons 生命周期、lesson_reviewer forward-return 验证、learned guards、memo/reasoning-feedback/calibration-feedback/draft-skill 路径。

**合理性**：§B Entry Planner 段是本文最有价值的部分（LLM 只出 qualitative hypothesis、Compiler 拥有几何、watcher 不调 AI——完全符合"AI 像人一样判断、确定性执行"），但它重复 entry-plan-contract.md。其余大部分是 research 可选面或化石。

**膨胀**：issue 号（#2933、#3701、#3702/#3703、#3134、#1454、#3741）；"Last updated: 2026-10-09"；"⚠️ Corrected:"（:488）；"Position C, 2026-07-02"；6 段 retired 叙述。

**Verdict：SPLIT（+ 新建 operator 合同）**

**具体修改**：
1. 删除 §1 整张 job 表，改为一行："Job registry, cadence, executor and timeout: [scheduler_contract §3](scheduler_contract.md#§3-任务注册表)." scheduler_contract 是唯一 job 表。（scheduler_contract §8 "两份契约通过 job_id 一一对应"同步删除。）
2. 把 :85-139 三段 OS/Supervisor 合同移到 gateway 生命周期的合同或 `docs/operations/deployment/CROSS_PLATFORM_SERVICE_ENVIRONMENT.md` 的合同部分（它们是 gateway 行为，不是 agent）。
3. 把所有 retired 段（market_observer、stock_observer、Ranker/PaperTrader、Historical TradingDecision、§C、Learning contribution measurement、§E 的退役叙述）压成文末一张 "Retired agents" 表（name | replaced by | history link），≤10 行。
4. §B Entry Planner 段压缩为 IO 摘要（trigger/inputs/LLM/outputs/authority 各一行）+ 链接 entry-plan-contract.md；删 "Position X authority" 一整句（研究导入，不是 Planner 行为）。
5. §3 删 SQLite/Job Timeouts/LLM Fallback 三小节，各换成一行链接（storage 合同、scheduler_contract §3/§4、llm_inference_harness_contract）；删 "max_workers=3"。
6. §D 每个学习 agent 加 `Status: retiring per spec 0001 (LL REMOVED)` 或直接让 spec 0001 的实施 PR 删除；在应用 delta 前至少不要称其为 "learning mainline"。
7. 新增 §E "Operator (operator-shell)" 合同：把 OPERATOR_SHELL.md §4（读写面）、§5（权限阶梯）、§6（失败→症状）改写为 EARS 要求，例如 `The operator-shell shall reach the backend only through the allowlisted routes in operator_service_auth.py.`、`If a required backend route returns 404, then the operator-shell shall record gateway_request_failed:<status>:<path> and degrade its mode.`；OPERATOR_SHELL.md 保留为 architecture/诊断说明并链接回这里。
8. 修正：`reconciliation` 去掉 "live only"；`price_feed`/position_path_observer 去掉 spot；`equity_observer` 标 out-of-scope；`_record_model_failure:116` 删行号；Worker 权限 `Paper` 补一句"= demo-account execution permission; no simulator"。
9. 删 §4 末段 "UTA-only external accounts… approved future portions" 句（Proposal 语言）。

## serenity_contract.md（317 行）

**角色**：以 Contract 为主（§2-§9 有较好的不变量），混入 History（§7 "历史（退役 Option A）" :216-219、§12 "历史 Spec 映射" 明说"不宣称这些旧文件或 claim 仍存在/通过" :276）、Proposal（§9 "UTA 唯一私有账户切换；那是批准的后续部分" :242）、进度声明（"本次文档编辑没有运行这些测试" :295；"MVP 阶段不暴露 (owner 待定)" :301）。

**真相核对**：
- 存在：`core/aletheia/trading/serenity_evidence.py`、`serenity_scoring.py`、`serenity_publication.py:9 publish_serenity_candidates`、`extensions/research/research_worker.py:93 _auto_promote_serenity_opportunities`、`_ICT_METHODOLOGY_SYSTEM`（:538）、`SerenityConfig.max_hold_days=90/auto_promote=False/min_score_for_pi=72`（config/schema.py:2088-2093）、position_manager 的 `trade_school=="serenity"` 分支（position_manager.py:702,910,989,1020）、9 个 `tests/specs/spec_serenity_*.py`。
- **不存在**：`_is_tradeable_crypto_symbol()`（不变量 #5 与 §10 引用；core/extensions/tests 全仓 0 命中，实际函数是 `_is_allowed_serenity_symbol`，research_worker.py:41）——合同称其为"别名"也是错的（没有别名，按收敛原则也不该有）。`promotion_gates_contract.md`（§14 :317）不存在。
- 产品范围冲突：§10 "股票代币执行 (xstock)"——`asset_class='equity_tradeable'` 仍在代码中且注释写 "can execute"（research_worker.py:58-70, :914）。xStocks 是代币化股票，CAPABILITY_GROUND_TRUTH 规定 RWA 永续黑名单、普通股票出范围。`_ALLOWED_QUOTE_CURRENCIES={USDT,USDC,BTC,ETH,BUSD}`（research_worker.py:38）放行 `ETH/BTC` 这类现货交叉对，与"只做 USDT 永续"冲突；合同 §10 把"放行 BTC/USDT、ETH/BTC"写成正确行为。
- 生产消费者：`SerenityConfig.enabled=False`、`auto_promote=False`（默认关闭）；scheduler 仅在 `cfg.serenity.enabled and _enqueue_research_run is not None` 时入队（scheduler.py:1277,2382）。唯一现行发布入口是手动 route `server/routes/data.py:2896-2917` 与可选 research_worker。合同全文没写 "Experimental / default disabled"，而 CAPABILITY_GROUND_TRUTH 把它列为 Experimental。
- §2 不变量 #6 说 `risk/engine.py` live guard 不引用 trade_school——其 live guard 确实不引用，但 engine.py:335（plan/proposal trade_school 一致性）与 :1306（shared pool 注释）有引用，合同应精确到"live-disabled guard"而非文件级。

**具体性**：§3-§4 评分卡、§5.1 run 状态机表、§6 归因规则是可检查要求，质量好。§8 holding 段混了多个数（4h/24h/60/240 分钟/60 秒/2160h），对读者难懂但都是事实。

**合理性**：Serenity 是"研究→论点→长周期持有→论点失效退出"，从产品角度它**最接近人类主观交易者**（有 thesis、有 invalidation、有复盘 PostMortem），与 owner 目标方向一致；但它是第二条 Candidate 来源、默认关闭、Experimental，且其 90 天持有对永续合约的资金费成本未在合同中讨论。D2 生成者≠评估者、来源天花板是值得保留的信任设计。xStock 部分是化石+范围违规。

**膨胀**：§12 历史映射整表、§7 历史段、§4.3 "别名健壮性说明"（实现瑕疵笔记）、"EVO 评估报告 L3 已核实"、日期 "Last updated 2026-09-07"、"历史事故: MSTR/SPY…"。

**Verdict：REWRITE（并删除 §10）**

**具体修改**：
1. 头部加状态行：`Status: Experimental; SerenityConfig.enabled=false and auto_promote=false by default; the only current publication entry is publish_serenity_candidates (manual route data.py:promote_serenity_theme or optional research_worker).`
2. 删除 §10 整节；不变量 #5 改为 `If a research symbol does not resolve to a registered USDT-margined crypto perpetual (non-RWA), then publish_serenity_candidates shall reject it before persistence.`——并开 issue 删除 `equity_tradeable` 分支与 `_ALLOWED_QUOTE_CURRENCIES` 中 BTC/ETH/USDC/BUSD（D0：代码不符合产品范围）。
3. 把 `_is_tradeable_crypto_symbol` 全部改为 `_is_allowed_serenity_symbol`，删"别名"说法。
4. 删 §12 历史表与 §7 历史段，改为一行链接 `docs/design/history/`；§12 只保留"当前验证"列表（spec 文件 + 4 个 test 文件），删 "本次文档编辑没有运行这些测试"。
5. 删 §9 "本入场切片不宣称…后续部分"两行；§9 不变量改为"`risk/engine.py` 的 live-disabled guard 不读取 trade_school"。
6. §14 删 `promotion_gates_contract.md` 行；删 §4.3 别名说明、§13 L1 的 "owner 待定"。
7. §8 增一条要求：Serenity 持仓的 funding 成本按 cost-complete Outcome 计入（链接 opportunity_outcome/derivatives_economic_semantics），否则 90 天持有的经济结果不可比。
8. 与 spec 0001：§8 末条 "`_close_serenity_feedback` 写 SerenityPostMortem" 是第二条复盘写路径；spec 0001 LL-R01 要求统一 decision record——在合同中标注 "subject to spec 0001 LL-R01 uniform decision record"。

## trading_mandate_contract.md（166 行）

**角色**：形式上是 Contract，但它描述的能力"生产者已退役"（:16-18, :116-119）——剩下的是配置校验、纯函数、只读检查面，以及一节 "Explicit deferrals"（:151-162，Proposal 语言：multiple mandates/enforcement/prompt injection 全是 deferred）。实质是一份"已退役能力的残余说明"。

**真相核对**：
- 存在：`core/aletheia/trading/trading_mandate.py`（864 行）、`core/aletheia/analytics/trading_mandate_compliance.py`（580 行）、`POST /api/settings/trading-mandate`（server/routes/settings.py:450）、CLI `aletheia inspect trading-mandate` / `mandate-compliance`（cli/inspect_cmd.py:180,197）、`storage/proposal_hypothesis.py:218-233` 拒绝新 Proposal 携带 mandate 证据（"trading mandate provenance production is retired"）。
- **`assess_trading_mandate_fit` 在 core/extensions 中零生产调用者**，只有 `tests/test_trading_mandate.py:100,359` 调用。合同 §Fit assessment 把它写成现行能力，但没有任何路径会在新决策上执行它。
- 合同的 YAML 示例 `allowed_physical_market_types: [spot, perp]`（:41）——现货出范围；示例 `allowed_symbols: [BTC/USDT, ETH/USDT]` 是现货写法而非永续符号。
- Entry Planner/Compiler 不读取 mandate（entry_plan*/entry_planner* grep 0 命中）——mandate 不进入 AI 决策上下文（合同也自承 "prompt injection (deferred)"）。

**具体性**：配置校验、hash identity、fit 状态枚举写得精确、可检查——但精确地描述了一个没有调用者的函数。

**合理性**：概念上"交易授权书（可交易品种/方向/持有周期）"贴近人类交易员的 trading plan，但当前实现 observe-only、不进 prompt、不阻断、生产者已退役、无新数据产生。它与 Active Runtime Scope `instrument_profiles` 和 Universe registry（真正决定能交易什么的权威）重叠，是第二个"范围声明"。约 1444 行代码 + Settings 控件 + 2 个 CLI 维护一份零新增数据的能力——典型化石。按"一个能力一份实现、删掉失败者"，应退役。

**膨胀**：重复的"不是策略/不是权威/不改变行为"声明出现 4 次（:5-14, :64-66, :108-112, :146-149）；deferrals 清单。

**Verdict：ARCHIVE**（移到 `docs/design/history/trading_mandate.md`，与已有的 `history/local_simulated_mandate.md` 合并）

**具体修改**：
1. 整份移入 `docs/design/history/`，开头写：`Retired: no producer writes mandate provenance for new Proposals (storage/proposal_hypothesis.py rejects it); assess_trading_mandate_fit has no production caller.`
2. 若 owner 需要保留历史只读：在 `persistence_recovery_contract.md` 或 position replay 相关合同中留一句 `Archived trading_mandate_identity/fit on trade_proposals are read-only and never backfilled`，不保留独立合同。
3. 开 issue（需 owner 确认，属删除能力）：删除 `POST /api/settings/trading-mandate`、`TradingMandateConfig`、`assess_trading_mandate_fit`、`inspect trading-mandate`；`mandate-compliance` 是否保留取决于 Fedora 上是否存在带 mandate 证据的历史 Proposal（未验证，需一次只读查询 `SELECT count(*) FROM trade_proposals WHERE ...trading_mandate_identity...`）。
4. 若 owner 反而想要"交易授权书"进入 AI 决策（人类交易员式的 plan），应走新 spec（docs/design/specs/NNNN），而不是保留这份 observe-only 合同；示例必须改为 `allowed_physical_market_types: [perp]`。

## opportunity_outcome_contract.md（150 行）

**角色**：Contract（`filter_candidate_outcomes` 经济公式、结算状态、消费规则），但头部 "状态：阶段 D 权威设计，2026-07-10"（:3）是 Proposal/进度措辞；§9 后追加 "#3086 amendment"（:142-150）是补丁式叠加，改写 §4 funding 公式却没有改 §4 正文。

**真相核对**：
- 存在：`filter_candidate_outcomes`（50 个文件引用）、`perp_structure_shadow_outcomes`（storage/repositories*.py、migrations.py）、`horizon_ohlcv_local`/`near_due_ticker`（universe/outcome_recorder.py、analytics/outcome_semantics.py）、`min_cost_covering_edge_pct`（storage/models.py）、`missed_alpha_suspect`/`positive_gross_move_only`（analytics/outcome_semantics.py）、`cost_model_version`。`universe_forward_outcomes` 已删（与 memory #3701 一致）。
- 冲突 1（与 filter_evidence_contract §4 互相矛盾）：本文 §2 "`direction`… 未知方向只能记录毛价格变化"（:22）暗示可建 unknown 方向行；filter_evidence_contract.md:218-221 规定"事前无方向时不得创建结果行"。代码 `analytics/filter_evidence.py:212` 只在 `side in _VALID_DIRECTIONS` 时建 outcome——filter_evidence 对，本文过时。
- 范围：§2 `market_type: spot / perp / futures`（:21）、§4 "现货为 0"（:46）、"与 Paper 执行相同的 canonical 域"（:57）——现货与 Paper 模拟器均已出范围/退役。
- 消费者过时：§8 "scanner 校准、family edge、doctor signal health、lesson evidence 和自动 guard"（:120）——lesson evidence/自动 guard 是 spec 0001 REMOVED 的路径；"不进入 ranker/System Operator alpha 基线"（:124）——ranker 已退役；`observer_pre_filter` 回放规则（:128-136）——Market Observer 已退役，代码中只剩 reader（analytics/filter_evidence.py:616、extensions/observation_lab/filter_shadow.py），**没有任何 writer 写 `filter_name='observer_pre_filter'`**（grep 生产写入者仅 ticker_availability、ticker_volume_prefilter、tradability_spread、universe_discovery_benchmark、prediction_divergence）。

**具体性**：§4 收益字段公式、§6 `pending/settled/failed` 语义（"场所不可达不是测量失败"、"failed 且两列 NULL = 从未观测"）是全组最好的可检查规则，应原样保留。

**合理性**：这是"候选在固定持有期后的反事实净收益"——支撑 Universe 选择质量测量，属于主干的测量支撑（memory：FCO 毛边缘≈0 正是由它测出）。但与 spec 0001 冲突：spec 0001 MODIFIED `learning_contract §11`——学习结果单位改为 R 计的 judgment label，不再是 24h/4h forward return。本文 §8 仍把 FCO 定义为 lesson/guard 的输入。

**膨胀**：issue 号（#3701、#3483/#3486、#3086）、迁移 ID 原文、"owner 2026-10-09" 夹在公式里、§5 `horizon_ohlcv_local` 一大段解释为什么分两个值。

**Verdict：REWRITE**

**具体修改**：
1. 删头部 "状态：阶段 D 权威设计，2026-07-10"，改为 `Owner of filter_candidate_outcomes economics and settlement status.`
2. 把 #3086 amendment 合并进 §4 `estimated_funding_pct` 条目（"candidate_selection_utility 用 `funding_evidence_json` 中证明过的 interval；其余 8h 线性估算"），删除文末 amendment 小节。
3. §2 改 `market_type: perp`（历史行可有 spot），删 "未知方向只能记录毛价格变化"，改为 `If the pre-decision direction is not long or short, then the recorder shall not create an outcome row.`（与 filter_evidence §4 一致并只在一处陈述——建议本文拥有，filter_evidence 链接）。
4. §4 删"现货为 0"、"与 Paper 执行相同"——改为"与 Execution 成本配置同一 canonical 域"。
5. §8 删 lesson evidence / 自动 guard / ranker / observer_pre_filter 四条；改为一句 `Learning consumers are defined by spec 0001 (judgment labels in R); FCO net_return_pct is selection-quality evidence only.`
6. §6 Position X `perp_structure_shadow_outcomes` 段移到其自己的 shadow 合同（`universe_twin_shadow_contract.md` 或 market_structure 合同），这里只留"共用 status 取值"。
7. 删 issue 号与迁移 ID 原文，§5 `horizon_ohlcv_local` 解释压到一句。

## filter_evidence_contract.md（363 行）

**角色**：严重混杂。头部即声明"下文 Ranker、PaperTrader、signal_pending、旧 Operator debate/claim… 仅解释切换前的历史证据"（:7）——即文档自己承认大半是 History。§3 :72-137（PiDecision skip、operator_action_assessment、skip_halt、skip-halt-v2、system_cascade、queue eviction、Opportunity TTL CAS）、§3.1 B2 debate evidence（:172-192）、§5 表中 Observer/Ranker/System Operator debate 行、§8 "Trade Funnel… ranker 历史候选" 全是退役入场链。头部 "Stage E 证据基础已实施；交易行为调参仍关闭"、"Last updated 2026-09-07" 是进度状态。

**真相核对**：
- 存在：`record_filter_decision`/`record_filter_decision_batch`/`decode_filter_decision_batch`（analytics/filter_evidence.py:113,238,325）、`refresh_active_snapshot_ttls`（storage/repositories.py:3237）、`get_entry_workbench_summary`（repositories.py:4833）、`list_entry_workbench_candidates`（repositories_universe.py:907）、`tradability_stablecoin`（universe/filters.py:38）、`awaiting_cadence/stale_overdue`（diagnostics/probes/trading.py:1184）、`docs/design/market-systems/filter_replay.md`、`history/ranker_paper_trader_filter_evidence.md`。
- 错误：§8 "`outcome_settler` 每 15 分钟运行"（:325）——代码 `minutes=10`（scheduler.py:1655），scheduler_contract 也写 10。
- 范围冲突：§5 "`XAUT/PAXG` 是黄金 RWA… 不得在 `tradability_stablecoin` 中提前排除"（:276-278）——产品现规定 RWA 永续黑名单（Bitget `isRwa=YES`，core/aletheia/data/symbols.py:306,472）；代码中已无 XAUT/PAXG 字样，该条既过时又与产品边界相反。§2.1 "同一 symbol 的现货和永续合约必须是不同候选"（:34）在只有永续时无意义。
- `guard_candidate_block`（:62）只剩 `extensions/learning/guard_routing.py:335` 读取——learned guard 是 spec 0001 REMOVED 项。
- 当前生产写入的 filter 只有 ticker_availability、ticker_volume_prefilter、tradability_spread、universe_discovery_benchmark（universe/features.py:528,540、filters.py:495、discovery_benchmark.py:489）及可选 prediction_divergence——§5 表 13 行中约 8 行没有当前生产者。

**具体性**：§3 记录字段、§3 batch 编码、§5 `authority_class` 四分类、§6 统计门槛（20 样本/70% 覆盖/5bp/单 version）都是可检查规则。但读者必须跳过约 60% 的历史文本才能找到它们。

**合理性**："每个关卡的放行/拒绝都留痕，并与事后净结果连接"作为审计/诊断是合理支撑；但 §6 的负反馈统计 + shrunk mean + gate-only replay + preregistered shadow experiment 是量化调参机器——与 owner"AI 像人一样交易，不是量化挖因子"的方向相悖，且其主要输入（Ranker/Observer/Operator 关卡）已无生产者。当前 Universe 关卡全是 `fact_or_execution` 类（"不得因收益证据自动放宽"），能做负反馈的 strategy_judgment 关卡在新主干上几乎不存在（Entry Planner 的判断由 spec 0001 的 judgment label 承接）。

**Verdict：SPLIT**

**具体修改**：
1. 把 §3 :66-137、§3.1 全部、§5 表中 Observer 前置判断 / Observer advisory / LLM neutral / Ranker / open-position-cooldown-batch / System Operator 七行、§8 Trade Funnel 条、§3 :141-162 中 "legacy NULL-generation carrier" 与 "已有 queued 是已认领的耐久 Operator carrier" 句，整体移入 `docs/design/history/ranker_paper_trader_filter_evidence.md`（已存在，追加）。
2. 删头部 "状态/Last updated/入场边界" 三行，改为 `Owner of filter_decisions / filter_decision_batches identity, immutability and retention. Producers today: Universe registry/ticker/tradability gates, discovery benchmark, Risk final evidence.`
3. §2.1 candidate_key 列表只留 `universe_member:`、batch、`proposal:`；删 Observer/Ranker/pi_decision（移历史）。
4. 删 XAUT/PAXG 段，改为 `RWA perpetuals (venue isRwa) are blacklisted at registry admission and never reach tradability gates.`
5. §4 FCO 创建规则只保留一句链接 opportunity_outcome_contract（由其拥有，见上文）；§4 结算优先级与 §8 Lab 优先级合并为一处。
6. §8 改 "每 15 分钟" 为 "每 10 分钟（scheduler_contract §3）"。
7. §6 负反馈统计：交 owner 决定冻结或删除；若保留，标明 `applies only to authority_class=strategy_judgment gates; no current mainline gate has this class`（需先 grep registry 核实）并链接 spec 0001 说明 Planner 判断质量由 judgment label 衡量。
8. §9 "Stage E 历史实施记录" 一行删除（§7 已链接同一历史文件）。

> filter_evidence 补充核实：`extensions/analytics/filter_registry.py` 只有 3 个 `authority_class="strategy_judgment"` 项——`direction_present`（:175）、`factor_hard_block`（:205）、`observer_pre_filter`（:453），三者在 core/extensions 中**均无生产写入者**（grep 仅 registry 自身）。即 §6 负反馈统计当前没有任何可评价对象。registry 共 ~40 项，其中 ranker_*、signal_*、operator_*、debate_downgrade、guard_candidate_block、committee_direction_conflict 等约 20 项属已退役入场链——registry 本身也需要按 §5 同样的方式清理（另开 issue）。

## candidate_selection_utility_contract.md（317 行）

**角色**：Contract 核心（§Boundary、6 步 current chain、Typed Candidate Opportunity handoff）+ 大量补丁式 amendment（"Acquisition and publication amendment — 2026-09-05" :203、"Amendment — 2026-10-08" :226、"Pre-capacity actionability amendment — 2026-09-10" :256、"Perp T0 Funding evidence — #3086" :290）+ History（"Historical fixed-look qualification" :158-181、Twin/Relative-Rank 回放 :21-28、A-batch provenance #3056 :168-174）+ 证据免责声明（"Observation isolation" :281-288 "does not itself prove profitability… deployment on Fedora"）。头部 "Authority: GitHub Issue #2729 and the accepted proposal"（:3-4）——把 Issue 当合同权威，违反 AGENTS.md "Issue 不授予范围"。

**真相核对**（全部 grep 命中，除注明外）：`candidate_selection_utility_v1`（config/schema.py）、`universe-hard-gate/v1`（universe/candidate_eligibility.py）、`universe-opportunity-evidence/v2`（universe/opportunity_evidence.py）、`universe-economic-utility/v2`（universe/economic_utility.py，`_SHRINKAGE_K = 50` :23，`parent_min_clusters: 50` :513）、`candidate-selection-utility/v2`（universe/candidate_selection_utility.py）、`candidate-opportunity-context/v1`（storage/models.py、universe/candidate_opportunity.py）、`admission_basis`/`startup_reason`/`pessimistic_strength`、`CANDIDATE_FEATURE_ACQUISITION_FINGERPRINT`（universe/features.py）、`net_clears_tradable_edge_sql`、`classify_counterfactual`、`candidate-perp-funding/v1`（universe/candidate_funding.py）、`perp_funding_historical_incompatible`、`universe_twin_shadow_batches`、CLI `aletheia evidence candidate-selection status`（cli/evidence_cmd.py:16-23）。
- **不一致**：合同写 actionability manifest `supported-mechanism-and-fast-route-before-capacity/v2`（:274），代码已是 `/v3`（universe/candidate_selection_authority.py:175）；只有测试 fixture 仍是 v2（tests/test_candidate_selection_utility.py:112）。合同落后于一次 epoch 轮换。
- 范围：多处保留 spot 语义（"Spot history are unchanged" :307、"Cross-market deduplication… preferred market" :99）——现货出范围后 cross-market dedup 只剩 perp，应注明。
- 运行事实（memory `project_selection_never_measured_2026-10-08`，非本次重新验证）：Fedora 上 100% 为 Startup admission，measured 路径从未触发。合同 Status 段（:6-10）写"Measured economic admission has priority"——描述正确，但读者无从得知现实中只有 Startup 在工作。

**具体性**：6 步链条是本组最精确的选择规则（Startup 的 5 种合法 reason、"not measured yet vs measured and refused" 区分、unknown 不等于 0、tail harm 不可绕过）。但它是长段落而非 EARS 句，trigger→response 需要读者自己拆。

**合理性**：作为"给 AI 交易员挑选值得看的标的（稀缺 Candidate 槽）"是主干第一段，必要。但 measured 部分（K=50 父级收缩、非重叠 4h cluster、SE 惩罚、50 clusters/10 dates/5 symbols/80% coverage）是一整套量化统计机器，而它在实际运行中从未达到样本门槛——事实上 Candidate 是按 `pessimistic_strength`（启发式信号强度）选出来的。owner 目标是"AI 像人一样判断"，选择层只需"给 AI 足够好、可解释的候选"，此统计门的复杂度与实际收益不成比例。建议 owner 决定：保持（等待样本成熟）还是简化为 Startup-only + 显式标注 measured 路径为 dormant。不由文档审查擅自定。

**膨胀**：4 个日期/issue 命名的 amendment 小节；#2729/#2556/#3056/#3492/#3786/#3483/#3486/#2917/#3086/#3073 共 10+ 个 issue 号；"Observation isolation" 的免责声明；SHA-256 digest 原文写进合同（:233）；重复 "historical JSON/hashes are never rewritten" 至少 5 次。

**Verdict：SPLIT + REWRITE**

**具体修改**：
1. 删头部 "Authority: GitHub Issue #2729…"，改为 `Owner of Candidate slot admission. Implementation: core/aletheia/universe/{candidate_eligibility,opportunity_evidence,economic_utility,candidate_selection_utility,candidate_opportunity}.py.`
2. Status 段改为不带进度的事实：`Admission basis is measured when the estimator has support, otherwise startup (signal-strength ordering); both use the same Planner path.` 运行占比写进 GitHub/evidence，不写合同。
3. 将 6 步链条每步改写为 1-3 条 EARS 句（例：`If a Hard Gate input is missing, then the gate shall return unavailable, not ineligible.`；`When the estimator reports no_history | parent_sample_insufficient | child_sample_insufficient | history_economics_incomplete | settlement_overdue, the selector shall allow Startup admission only for an eligible, actionable instrument with explicit direction and finite positive pessimistic_strength.`）。
4. 把 4 个 amendment 并入对应步骤：acquisition identity（2026-09-05 + 2026-10-08）→ 新小节 "Feature acquisition identity"，只保留"常量 `CANDIDATE_FEATURE_ACQUISITION_FINGERPRINT`；仅在计算值改变时手动轮换；legacy/missing 排除"三条规则，删 digest 原文；actionability → 步骤 1/6，并把 manifest 改为 `/v3`；Perp Funding → 链接 opportunity_outcome §4（单一拥有者）。
5. 移入 `docs/design/history/`：§"Historical fixed-look qualification"、Twin/Relative-Rank 回放段（:21-28）、A-batch provenance、"Observation isolation" 免责声明、"Historical filtered/assets_passed" 段。
6. 删所有 issue 号与 "owner decision 2026-10-09" 括注（决策归 DECISIONS.md）。
7. 现货相关句改为 `market_type is perp for all current Candidates; historical spot records remain readable.`

## Cross-doc

**矛盾**
- job 表两份：scheduler_contract §3 与 agent_contracts §1 各自维护同一 43 个 job（目前集合都与代码一致，但描述已分叉：agent_contracts 写 `reconciliation (live only)`、`max_workers=3`；scheduler 写 8 池）。operator-shell `recommendation_engine.ts:125-127` 硬编码 post_trade_reviewer/risk_critic/dreaming_agent 频率，是第三份拷贝。→ 只留 scheduler_contract §3。
- `outcome_settler` 频率：filter_evidence_contract.md:325 写 15 分钟；scheduler_contract 与代码（scheduler.py:1655）为 10 分钟。
- 无方向 outcome：opportunity_outcome_contract.md:22 允许"只记毛价格变化"；filter_evidence_contract.md:218-221 禁止建行；代码（analytics/filter_evidence.py:212）随后者。
- RWA：filter_evidence_contract.md:276-278 要求 XAUT/PAXG（黄金 RWA）不得被提前排除；CAPABILITY_GROUND_TRUTH 规定 RWA 永续登记即黑名单（data/symbols.py:472 `isRwa`）。serenity_contract §10 与 scheduler_contract §4.4 仍把 xStock（代币化股票）写成现行执行/行情路径。
- operator 合同循环引用：OPERATOR_SHELL.md §7 称 agent_contracts 是 operator 的行为合同；agent_contracts §E 只有 5 行退役说明——operator-shell 的行为合同实际不存在于 `docs/design/contracts/`。
- 现货：trading_mandate 示例 `[spot, perp]`、opportunity_outcome `spot/perp/futures` 与"现货为 0"、agent_contracts price_feed "spot/perp"、candidate_selection "Spot history unchanged"——均与"只做 USDT 永续"冲突（历史读取可保留，但不应写成现行行为）。

**重复**
- SQLite 四支柱：scheduler_contract §5 与 agent_contracts §3 各一份，且都复制自 storage/data_model。
- Entry Planner 行为：agent_contracts §B 与 entry-plan-contract.md 重复。
- Perp funding 公式：opportunity_outcome 文末 #3086 amendment 与 candidate_selection "Perp T0 Funding evidence" 各写一遍。
- FCO 创建/结算优先级：opportunity_outcome §2/§6 与 filter_evidence §4/§8 交叠。

**缺失合同（有代码、在主干上、无合同）**
- **operator-shell 行为合同**（见上）。
- **Instrument registry 准入**（USDT 永续 only、`isRwa` 黑名单、spot 永不登记）：只在 CAPABILITY_GROUND_TRUTH 产品层与 core/aletheia/data/symbols.py:306-475 中存在，`docs/design/contracts/` 无任何文件提到 `isRwa`。这是"只做 USDT 永续"在代码里的唯一执行点，应有合同。
- **news_fetch / news_analyze**：`news.enabled` 默认 True（config/schema.py:2077），`news_analyze` 每 5 分钟在 `agents` 池上做 LLM 调用（extensions/data/news_analyzer.py:83，scheduler.py:1576 无条件注册），而它唯一的业务消费者是默认关闭的 Serenity research_worker（research_worker.py:165-191）；Entry Planner 不读 news（agent_shell/entry_planner.py、trading_context.py grep 0 命中）。没有合同、默认开启、与 `pi_trading_decision` 共用单 worker 的 `agents` 池——成本与延迟风险，且不在主干上。

**与 spec 0001 冲突**
- scheduler_contract 与 agent_contracts 把 `dreaming_daily`（ps_lessons 抽取）、`lesson_reviewer`（forward-return 验证）、`insight_synthesizer`（memo/reasoning_feedback）、`confidence_calibration`（calibration feedback）、`guard_promotion`（learned guards）作为现行 job 描述——全部在 spec 0001 REMOVED 清单。
- opportunity_outcome §8 把 FCO `net_return_pct` 定为 lesson evidence 与自动 guard 输入；spec 0001 把学习单位改为 R 计的 judgment label。
- filter_evidence `guard_candidate_block`（只剩 extensions/learning/guard_routing.py:335 读取）——learned guards 被 spec 0001 并入 Playbook `AVOID` 规则。
- serenity `_close_serenity_feedback` → SerenityPostMortem 是独立于 LL-R01 uniform decision record 的第二条复盘写路径。
- operator-shell 派发 dreaming_agent/post_trade_reviewer/risk_critic 任务（task_runtime.ts:130-138），spec 0001 实施后这些 agent 的语义会变，operator 侧需同步。

## 总表

| doc | lines | role | verdict | top problem |
|---|---|---|---|---|
| scheduler_contract.md | 403 | Contract + target 段 + storage 拷贝 | SPLIT + REWRITE | job 表与代码 43=43 一致，但约 1/4 job 是出范围/spec 0001 REMOVED 的化石且未标注；essential 集合未列出；§3.3 target 段混入 |
| agent_contracts.md | 522 | Contract + Ops(gateway) + History + 4 份拷贝 | SPLIT（+新建 operator 合同） | 第二份 job 表；operator-shell 无行为合同（与 OPERATOR_SHELL.md 循环引用）；`reconciliation (live only)`、`max_workers=3` 已错 |
| serenity_contract.md | 317 | Contract + History + Proposal | REWRITE（删 §10） | xStock `equity_tradeable` 与 BTC/ETH 计价放行违反 USDT 永续/RWA 边界；未标 Experimental/默认关闭；引用不存在的 `_is_tradeable_crypto_symbol`、`promotion_gates_contract.md` |
| trading_mandate_contract.md | 166 | 退役能力残余 | ARCHIVE | `assess_trading_mandate_fit` 零生产调用者，生产者已退役，~1444 行代码只服务只读检查；示例含 spot |
| opportunity_outcome_contract.md | 150 | Contract | REWRITE | 消费者列表含已退役 ranker/observer_pre_filter 与 spec 0001 删除的 lesson/guard；与 filter_evidence 在无方向建行上矛盾；#3086 补丁未并入正文 |
| filter_evidence_contract.md | 363 | ~60% History + Contract | SPLIT | 大半描述已退役 Ranker/PiDecision/Observer 链；3 个 strategy_judgment 关卡全无生产者，§6 负反馈统计无评价对象；XAUT/PAXG 条与 RWA 黑名单相反；settler 频率写错 |
| candidate_selection_utility_contract.md | 317 | Contract + 4 个补丁 amendment + History | SPLIT + REWRITE | 规则精确但散在日期补丁里；actionability manifest 写 v2 实为 v3；measured 统计门在运行中从未触发（100% Startup），复杂度与收益不成比例待 owner 定 |
