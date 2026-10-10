# 文档审计：frontend / api / data / market-systems / operator / architecture / decisions / operations

（逐份追加；总表在文件末尾）

## docs/design/architecture/SYSTEM_MAP.md（177 行）

**角色**：Navigation（导航），但混入 Evidence（§六 历史管道断裂、"Last reviewed 2026-07-28"）与过期覆盖率统计（§五）。

**真实性抽查**
- 调度 job：scheduler.py 真实注册 45 个 id（`grep -o 'id="…"' core/aletheia/agent_runtime/scheduler.py`）。漏列 11 个（已知事实，不复述）。
- `equity_observer` / `prediction_observer`（SYSTEM_MAP.md:91-92 标 ✅）：
  - 只要 `extensions.research` 可导入就**无条件 add_job**（scheduler.py:1584-1603；注册在 main.py:143-150/167-178 与 server/app.py:1108-1145 **两处重复**）。
  - 运行时默认 no-op：`_run_equity_observer` 在 `observed_equities` 为空时直接 return（scheduler.py:3008-3010，schema.py:410 默认 `[]`）；`_run_prediction_observer` 在 `prediction_enabled=False` 时 return（scheduler.py:3028，schema.py:402 默认 False）。
  - 产物是 `insert_opportunity`（equity_observer.py:456、prediction_observer.py:400）——Opportunity 生命周期已无生产消费者；表名写错：文档写 `prediction_market_runs`，真实表是 `prediction_observer_runs`（migrations.py:1537）。
  - Capability Ground Truth 明确"Ordinary equities and prediction markets: Outside active instrument scope"；且 `observed_stock_tokens` 默认值（schema.py:416-420）是 rNVDA/AAPLON 这类**现货 RWA 代币**，同时违反 spot 出范围与 RWA 黑名单。
  - **判断：化石**。文档应删这两行；代码侧建议单独 Issue 删除 equity_observer/prediction_observer/divergence_scanner（1724 行）+ PredictionLens 页 + 相关 config 字段（D2 产品范围收缩，已被 CGT 覆盖，属 D0 级收敛）。
- §五 配置层覆盖率表（SYSTEM_MAP.md:150-161）**整表失真**：真实字段数 Exchange 9（文档 5）、Risk ≥50（文档 18）、Universe 23（13）、Trading 31（17）、MarketData 24（14）；`Settings.tsx:1648` 不存在（文件仅 650 行，设置已拆到 `frontend/src/features/settings/tabs/`）；GuardConfig 文档写 0%，实际 AgentModelsTab.tsx:300/311 已有开关，且其文案仍写"paper ranker / paper trader"（已退役）。`GuardConfig.enforce_in_paper_ranker`（schema.py:1916）本身是 Ranker 化石，仍被 routes/data.py:2133 读取。
- 其他抽查：`retrieve_lessons`（extensions/knowledge/retrieval.py:98）存在；`detect_contradictions`（extensions/knowledge/manager.py:362）存在；reconciliation 2min（scheduler.py:1448）属实；PIPELINE_GAPS / BACKEND_ISSUES 归档链接有效。

**与 spec 0001 冲突**：§四（:134-144）把 `ps_lessons`→lesson_reviewer→"Entry Planner learning context"、guards、trading_memo、`retrieve_lessons()` 写成 ✅ 当前主线；spec 0001 以 Playbook + R 标签 + advisory/authority 两级取代。§六 A1 "经验经 LessonStore→System Operator prompt→risk 影响决策"描述的是已退役 Python Operator 路径。

**合理性**：§零-三的 job 表作为"job→表→消费者"导航有价值；§四-六是学习设计与历史审计的复制，属别处 owner 的事实。

**冗余**：§六整节是 2026-07 历史审计；`#3701`、`#3729`、`#3134` 作为正文内容；"Position C" 等术语无定义。

**结论：REWRITE**

**具体修改**
1. §一/§二 job 表：补齐 11 个漏列 job；删除 `equity_observer`、`prediction_observer` 两行（或在代码删除前临时改为"默认 no-op，出产品范围，待删"一行合并说明，不标 ✅）。
2. `price_feed` 行删"spot/perp"中 spot 字样；`ohlcv_nightly_backfill` 行删"backtest 消费者随引擎删除 Position C"。
3. §四 整节改为一句话 + 链接：学习层当前行为见 learning/knowledge contract，目标设计见 specs/0001；不在导航图复制 lesson/guard/memo 管道状态。
4. §五 整节删除（覆盖率统计是易腐快照，无 owner、无 check）。若要保留，只能由脚本从 schema+settings registry 生成。
5. §六 整节删除，改一行"历史管道审计见 archive/…/PIPELINE_GAPS.md"；删"Last reviewed 2026-07-28"。
6. 第 38-39 行 2026-09-28 收敛结论链接移到 research 索引，不放导航顶部。

## docs/design/architecture/OPERATOR_SHELL.md（118 行）

**角色**：单一 Contract/Reference（当前事实），开头明确"不是设计历史"。角色清晰。
**真实性抽查**（全部存在）：`operator-shell/src/{index,heartbeat,recommendation_engine,repair_engine,pi_runtime_adapter,task_runtime}.ts`；`core/aletheia/gateway/operator_shell.py`；`server/operator_service_auth.py`；`tests/test_operator_service_auth.py`、`tests/test_api_consumer_paths.py`；`ReadOnly` 默认（config.ts:26）；`pi-shell` 默认 provider（config.ts:28）；`dreaming_due`/`daily_review_refresh_due`/`worker_health_review_due`（recommendation_engine.ts:343/606/731）。
**问题**
- §6 "Worked example (2026-09-29, #3763)"（:98-106）是事故叙事，属 history/postmortem；保留最后一句"test_api_consumer_paths 强制每个路径存在且列入 allowlist"作为不变量即可。
- §5 权限梯 `PaperAuto(2)` 名称残留已退役 paper 语义（operator_loop.ts:49），文档应注明"名称历史遗留，含义=可执行低风险运维命令"，或推动代码改名（另立 Issue）。
- §3 `dreaming_due` 推荐把 dreaming 当成 operator 驱动的当前学习主干；spec 0001 REMOVED 了 ps_lessons 生命周期，实施后该推荐类型需同步删除（标注依赖 spec 0001）。
- 缺失败语义里"backend 不可达"与"credential 失效"两行；`modelProvider=pi-shell` 与"CLI provider 已退役"关系未解释（是标识符还是真实子进程？需一句澄清）。
**结论：KEEP**（小改：删 worked example 叙事、补 PaperAuto 命名说明、加 spec 0001 依赖注）。

## docs/design/operator/PI_OPERATOR_DESIGN_DECISIONS.md（427 行）

**角色**：Decision 记录，但正文几乎全部是**已退役 Python Operator assessment loop** 的设计；顶部两段退役横幅 + "[必看] 两组件别混"说明是补丁式免责声明。
**回答问题：是否描述已退役 Python Operator？是，约 90%。**
- 问题 1 调度池表（:57-63）列 `observer: market_observer`、`agents: pi_decision, paper_trader`——Market Observer、PaperTrader 均已退役；"28 个 scheduled job"失实（现 45）。
- 问题 2 `pi_decisions` 共享总线表引用 `paper_trader.py:143/235`、`agent_shell/operator_loop.py`——`core/aletheia/agent_shell/operator_loop.py` **不存在**（已 grep）。
- 问题 4-6 "六大日常职责（每 4h）"、`id="pi_operator_loop"` 注册片段（:283-290）、`_run_operator_loop(trigger=…)`——grep `pi_operator_loop|_run_operator_loop` core 零命中。
- "[产品定义] PI 架构基线"：observer tool registry、`run_market_scan`（grep core 零命中）、"主赛道（perp + spot）全权""第二赛道 xStocks/serenity_executor"——spot、xStocks 违反当前产品范围。
- "问题 N 低置信度语义"（:380-427）：引用 `agent_shell/trading_decision.py`、`skip_policy.py`——**均不存在**；`_MIN_CONVICTION_TO_TRADE`/`evaluate_trading_opportunity` grep core 零命中。但 `tests/specs/spec_operator_low_conviction_defer.py` 仍在仓库，断言已删除的 PiDecision/Opportunity queued 语义（需另查它是 skip 还是在测别的东西——疑似死 spec）。
- `PCI-G02` 在 tests/specs/spec_insight_feedback_loop.py:332 存在，但含义已变成"learning 写路径只写 DB"，与本文"observer 命名注册"不是同一约束——ID 语义漂移。
- 被 `operator_layer.md:84` 引为"更高层权威文档"——反向权威链，必须断开。
**合理性**：当前 TS operator-shell 的边界已由 OPERATOR_SHELL.md + agent_contracts.md 承担；"只能收紧不能放松""宪法人类专有"这几条原则若仍有效，应在 agent_contracts 里已有（一句链接即可），无需本文。
**结论：ARCHIVE**（整份移到 `docs/design/history/operator/`）
**具体修改**
1. `git mv` 到 `docs/design/history/operator/PI_OPERATOR_DESIGN_DECISIONS.md`，顶部换为一行"Retired Python Operator design (retired #3134); current: OPERATOR_SHELL.md, agent_contracts.md"。删掉现有三段横幅。
2. `operator_layer.md:84` 的"更高层权威文档"链接改指 agent_contracts.md。
3. 若"宪法人类专有"四条在 agent_contracts.md 中缺失，作为 D0 补进 agent_contracts（只补 TS operator-shell 适用的那部分）。
4. 另开 Issue：核查 `tests/specs/spec_operator_low_conviction_defer.py` 与 decision_lifecycle_contract DL-S47..50 是否测已删除路径；是则随化石删除。

## docs/design/operator/dynamic_sizing_operator_persona_design.md（255 行）

**角色**：Proposal（目标设计）+ 实施日志（P0-P4 commit hash、#409 修复叙事、owner 开启顺序）混合。
**真实性**：前提"System Operator 按上下文在 persona 区间内决定仓位"（:30-56、§6）依附已退役的 Python `operator_loop`（§13 引 `agent_shell/operator_loop.py:819-874`，不存在）；§4.1 "活跃 paper broker（okx_demo→broker_id 含 okx）"是 paper/OKX 语义。
**关键冲突**：当前 [Entry Sizing and Financing](../contracts/entry_sizing_and_financing_contract.md) Non-goals 明文"No legacy conviction/Kelly/persona sizing enters this path"（:216）。即 persona sizing **不在入场主线**。但代码仍跑：`persona_rollout` 调度 job（scheduler.py:1540，8h）、`trading/persona.py`、`persona_rollout.py`、`sizing_policy.py`（含无调用者的 `pyramid_plan`，:223）、exchange_materialization.py:528 仍 import persona_rollout、Settings/doctor 读其状态。这是"一个能力两份实现"的典型输家。
**合理性**：owner 目标"不过度工程化、AI 像人交易"；persona 区间 + 渐进解冻 + 学习 sizing 被 spec 0001 Non-goal "No automatic position-size or leverage learning" 直接否决。
**结论：ARCHIVE**（移 `design/history/`），并另开收敛 Issue：删除 persona_rollout job/状态机/pyramid helper 及 Settings 入口，或由 owner 裁定保留静态 persona 作为 entry_sizing 的输入（那是新 D1，需写进 entry_sizing contract）。
**具体修改**：整份移入 history；顶部一行指向 entry_sizing_and_financing_contract.md 与 spec 0001 Non-goals。SYSTEM_MAP §四 "persona sizing Learning" 行同步删除。

## docs/operations/development/GIT_WORKTREE_PLAYBOOK.md（70 行）

**角色**：Operations（操作手册），单一。标题与正文已是 jj workspace 版本。
**与 jj 工作流是否矛盾：基本一致，3 处小矛盾。**
- 一致项（已核）：`.claude/worktrees/` 在 .gitignore:30；"Git worktree 跑不了门禁"属实（scripts/ci/vcs.py:32-44 拒绝非 jj workspace root）；合并方式 `gh pr merge --rebase` 与 PR_REVIEW_LIFECYCLE.md:6 一致；commit/push 命令与 DEVELOPMENT_WORKFLOW.md:54-83 一致。
- 矛盾 1：§2（:20）"工作区一律放在 `.claude/worktrees/`，不建到仓库外"，但 DEVELOPMENT_WORKFLOW.md:206-223 的基线对比配方用 `jj workspace add /tmp/baseline`、`/tmp/parent`。两处须统一（建议 DEVELOPMENT_WORKFLOW 改为 `.claude/worktrees/baseline-<id>`，或 Playbook 明示"一次性只读基线允许 /tmp"）。
- 矛盾 2：文件名 `GIT_WORKTREE_PLAYBOOK.md` 与内容（jj workspace）不符；§3/§6/§7 仍通篇称 "Worktree"，与 §2 "Orca/EnterWorktree 的 Git worktree 不能跑门禁"混用同一词，读者分不清。建议改名 `JJ_WORKSPACE_PLAYBOOK.md`，正文统一用 workspace，Git worktree 只在 Orca 段出现。
- 矛盾 3：§4 第 1-2、5 步"停止所有并发写者 / 多次读 master 确认 stable / 隔几秒复读"是 2026-06 本地 master 被并发改写时代的防御；现在 jj + 推 bookmark + PR rebase 合并，不直接写本地 master，这些步骤多余且与 DEVELOPMENT_WORKFLOW 的简洁配方不一致。
- §5 "回执可能被伪造…marker 自验证 echo 随机 token"是一次性事故经验，属 history，不是操作规则。
**冗余**：顶部 2026-06-28 事故叙事（:3-4）。
**结论：REWRITE**（缩到 ~35 行）
**具体修改**：改名 JJ_WORKSPACE_PLAYBOOK.md（同步 DEVELOPMENT_WORKFLOW.md:69 链接与 check_docs 引用）；删 :3-4、§1 第二段以外叙事、§5 整节；§4 压成 "fetch → rebase -d master@origin → jj commit <paths> → jj git push --bookmark → PR → owner `gh pr merge --rebase`"；新增一行"一次性基线 workspace 允许放 /tmp 且用完即 forget"或改 DEVELOPMENT_WORKFLOW 对齐。

## docs/design/frontend/entry_workbench_design.md（72 行）

**角色**：Contract（页面行为），单一。**真实性**：4 个读接口均存在（server/routes/universe.py:716 `/workbench`、:808 `/entry-plans/{plan_id}`、:820 `/planning-history`、:945 `/candidates/{candidate_id}`）；`/blotter`、`/proposals` 重定向属实（frontend/src/App.tsx:216-217）。null-vs-[] 失败语义写得具体，是本批最接近 spec 规范的文档。
**问题**：:3 "current owner-facing contract for issue #2288" 用 Issue 号当状态；"compatibility redirects" 与收敛目标冲突（无独有 UI 的旧路由应删）；`/members/{id}` "remains compatibility"（:25）同理。
**结论：KEEP** —— 删 :3 Issue 号；把 `/members/{id}` 与两个重定向标为"待删兼容别名"并开收敛 Issue，或直接删除（需确认无外部书签依赖，D0）。

## docs/design/frontend/no_fluff_ui.md（84 行）

**角色**：Policy/准则，单一，可检查。与 `dashboard_data_presentation_contract.md` 的 unavailable-vs-zero 规则部分重复。:45 "Guard candidates 430" 例子与 :51-57 Learning 信息归属表依附现 Learning 页结构，spec 0001 实施后（Playbook 取代 guards/lessons）需同步。
**结论：KEEP** —— 示例换成主线指标（如"Active Entry Plans 3 / Risk rejects 24h"）；归属表加注"随 spec 0001 改"。

## docs/design/frontend/ui_design_system.md（717 行）

**角色**：Design-system reference（token + 组件 + 反模式 + 验收清单），基本单一；历史附录已于 2026-10-09 移出（:716）。
**真实性**：token 与 frontend/src/index.css:65-67 一致（`--bg #0a0e12`、`--surface #141a21`）。§4 "Blotter primitives (`.bl-*`) — legacy"（:412-418）**失真**：index.css 中 `.bl-` 选择器已为 0（`grep -c '\.bl-[a-z]'` = 0），只剩 :3206-3215 两段注释残骸。§3 Learning Workbench（:105-119）列"lessons, guard candidates, contradictions"为首屏区——与 spec 0001（Playbook）冲突，届时需改。
**冗余**：717 行偏长，§5 多条反模式（Symbol 显示竞态、Computed Style 验证）是事故经验，可压缩；"#2288"作内容。
**结论：KEEP**（小改）—— 删 §4 Blotter primitives 整节（并删 index.css:3206-3215 孤儿注释，代码侧小 D0）；Learning Workbench 节改为一句"页面结构见 Learning contract / spec 0001"；§5 压缩到规则句，不留事故叙事。

## docs/design/frontend/webui_page_map.md（80 行）

**角色**：Navigation，单一。**真实性**：路由表与 App.tsx:208-230 全部一致（含 7 个 LegacyRedirect）。
**问题**：`/prediction-markets`（Prediction Lens）列为当前页面，但预测市场在 CGT 中"Outside active instrument scope"，观察者默认关闭（schema.py:402）——这是出范围化石页面；`/agent-activity` 的"legacy Opportunity activity"tab 同理（Opportunity 生命周期无生产者）。7 个兼容重定向违反"no alias mirror"。
**结论：KEEP** —— 文档如实即可；同时把 Prediction Markets、Opportunities tab、7 个重定向列入收敛删除 Issue；导航 IA 段 Research 组去掉 Prediction Markets 后同步。

## docs/design/api/api_reference.md（704 行）

**角色**：Reference（端点索引）。自称"authoritative endpoint index"（:44）但混入大量 Contract 级语义段落（`API-S09`、OI-C04、#2181、Future Learning Contribution API proposal 段）。
**路由抽查（全量脚本 + 8 个手工）**：脚本把文档 289 行 `METHOD path` 与 core/aletheia/server + extensions 中 314 个装饰器做精确比对：**文档→代码 0 缺失**（4 个 `/_infra/*` 只是挂在非 `/api` 前缀，frontdoor.py:122/154 证实存在）；**代码→文档 30 个未登记**，含 `/api/universe/planning-generations/{id}`、`/api/orders/{id}/risk-decision`、`/api/positions/{id}/recover-parked-close`（POST，交易相关变更端点！）、`/api/settings/trading-mandate`、`/api/settings/fast-trigger`、`/api/settings/controller`、`/api/economics/*` 5 个、`/api/ui/*-bootstrap` 等。手工核 8 个：`POST /risk/kill-switch`（routes/risk.py:141）、`/settings/live-preflight`（live.py:27）、`/risk/brakes/{scope}`（risk.py:170）、`/settings/risk-policies/copy`（settings.py:1940）、`/update/apply`（update.py:80）、`/capital/net-deposits`（capital.py:19）、`/analytics/strategy-performance`（analytics.py:786）、`/analytics/learning-contribution`（analytics.py:2321）均存在。
**真实性问题（描述错）**
- :488 `POST /serenity/themes/{id}/promote` 写"writes signal_pending PiDecisions"——**错**，代码 data.py:2893-2917 走 `publish_serenity_candidates`（canonical Candidate 路径）。
- :190 `/analytics/opportunity-funnel` 一大段描述 Ranker snapshot、`ranker.last_run`、`ranker.wake`、Operator trade/skip/watch——全是已退役 Ranker/Python Operator 语义，路由仍在提供（化石端点）。:192 `/analytics/operator-decisions`、:193 `opportunity-trace` 同类。
- :338-341 `/settings/learning` "Paper-only… paper influence path… `paper_10` successor"——paper 已退役，语义需重述或端点随 spec 0001 删除。
- :367 `/settings/guard` 仍写 `enforce_in_paper_ranker`。
- :403-414 OKX/Binance 凭证 8 个端点作为当前 API 列出，无"frozen"标注；`/prediction/*` 15 个端点（出范围）无标注。
- :5 "Last updated: 2026-07-22"、:280 backtest 删除叙事、"(2026-07-18)"等日期散落。
**合理性**：端点索引本身有用，但"手写 + 部分语义"必然漂移；已有 `export_openapi_schema.py`（:29）可生成。
**结论：REWRITE** —— 改成"生成为主、手写只留 trust-boundary 表"。
**具体修改**
1. 删除逐行端点表（:67-704 大部分），改为：由 `scripts/ci/export_openapi_schema.py` 生成 `docs/_generated/openapi.json` 为唯一端点清单；check_docs 或测试保证 trust-boundary 表中每个路径存在于 OpenAPI。
2. 保留并扩充 Conventions、状态码语义、Trust-boundary 表（:9-63）；把 `POST /positions/{id}/recover-parked-close`、`/settings/trading-mandate`、`/orders/{id}/risk-decision` 判断后加入 trust-boundary 表（前者是平仓恢复变更）。
3. `API-S09`、OI-C04、#2181 语义段移入对应 contract（profit_loop_observability / outcome integrity / execution-mode identity）。"Future Learning Contribution API"段删除（proposal 已有 owner）。
4. 单列"Frozen / out-of-scope surfaces"小节：OKX/Binance 凭证、`/prediction/*`、`/analytics/opportunity-*`、`/settings/learning` paper 语义——每项标"待删除，见收敛 Issue"。
5. 修正 :488 Serenity promote 描述为 canonical Candidate publication。

## docs/design/data/active_universe_ohlcv_catchup.md（63 行）

**角色**：Contract（子系统行为），单一，格式接近 spec（Problem/Design/Failure/Controls）。**真实性**：`ohlcv_active_backfill` job、`ohlcv_backfill_state`（migrations.py:2805）、`active_universe_backfill_enabled`、`min_bars_for_backtest`（schema.py:1892-1893）均存在。
**问题**：:40-41 "Raw provider errors are not persisted"——**与项目惯例冲突**：SAFETY_AND_TRUST.md:49-52（#3880）要求外部交互保留原始错误（脱敏截断）+ 有界 reason token 两列。需核对代码现状并改写（若代码也只留 token，则是 D0 缺口）。`min_bars_for_backtest` 字段名残留已删除的 backtest 产品。:9 "opportunity emission" 旧词。
**结论：KEEP** —— 改 :40-41 为两字段语义；术语 opportunity→Candidate；建议 config 改名（另立小 Issue）。

## docs/design/data/data_collector_health.md（135 行）

**角色**：Contract，但与 market_data_observability.md 及 `contracts/market_data_coverage_contract.md` 三处重叠（connectivity attribution、collector-health/data-coverage 两接口描述重复）。**真实性**：`collector_health_state`（migrations.py:2824）、`/market/collector-health`、`/market/data-coverage`（routes/data.py:1910/1919）存在；`connectivity_monitor` job 存在。
**问题**：:43 "Stored error values are short codes, never … exception messages" 同样违反 #3880 原始错误惯例；:122-124 "a local fix … does not reset a Fedora observation window" 是流程提醒，不属契约；:5-10 lane 描述与 SYSTEM_MAP §一重复。
**结论：MERGE into `contracts/market_data_coverage_contract.md`**（该 contract 已声明自己是规范 owner；本文 State model 表 + 失败语义并入，其余删除）。

## docs/design/data/market_data_observability.md（79 行）

**角色**：自称"explains implementation"（:3-5），规范已归 market_data_coverage_contract。与 data_collector_health.md 内容大面积重复（"Two different questions"、connectivity attribution）。OHLCV `X-Aletheia-Data-Source` 头部节是独有内容；"spot/perp market"（:55）需改为 perp-only。
**结论：MERGE into `contracts/market_data_coverage_contract.md`**（只迁 OHLCV provenance 头与 CLI diagnostics 两节）。

## docs/design/data/data_utilization.md（186 行）

**角色**：自称"L2 契约"，实为数据域登记表（Registry）+ 多个子契约（Positioning Evidence、Options volatility 公式全文）+ DU claim 表，**混合**。
**真实性**：大量行写 `market_observer`、`paper_trader`、`ranker`、`trading_decision` 为消费者（§3 "现货价/OHLCV" 行、微观结构、资金费率、宏观、Hyperliquid、DefiLlama、新闻、预测市场、regime 行）——**Market Observer / PaperTrader / Ranker 均已退役**；grep `market_observer*` 文件零命中，`render_enriched_context_md`（data/context_builder.py:168）在生产代码中**无调用者**（仅 tests/test_enriched_data_freshness.py 调用）。即 Hyperliquid/DefiLlama/xvenue funding "✅ 已消费" 是假的——按本文自己的 R1，它们现在是 ❌ orphan。`tests/specs/spec_data_utilization.py` DU-S02/S03 文案仍以 market_observer 为消费者（:577、:651、:844、:864 断言文档里出现 "market_observer" 字样——**测试在断言文档包含已退役组件名**，只验文字不验行为）。美股/xStocks、r-series 113 tokens、xStock 上市状态（spot）三行违反 spot 出范围 + RWA 黑名单。"Binance USD-M fast-market"作为当前通道，与 Bitget-only 产品边界需核实。
**合理性**：R1-R5"每个数据域要有消费者"原则非常契合"一份实现、删输家"目标——**原则保留，登记表必须按当前 Entry Planner 上下文（entry_snapshot.py → context_builder.build_market_context_from_db）重做**。
**结论：REWRITE**
**具体修改**
1. §1-2 原则压缩保留。§3 表每行"消费模块"只允许写当前生产调用者（Entry Planner context via `trading/entry_snapshot.py:186`、Universe features、Risk、doctor）；凡只剩 market_observer/paper_trader/ranker 的行改 ❌ orphan 并列入删除 Issue（Hyperliquid、DefiLlama、CoinGecko top movers、预测市场、equity/xStocks、r-series）。
2. 删除 xStocks/r-series/xStock 上市状态三行（出范围），对应代码删除另立 Issue。
3. §4 "Positioning Evidence as-of contract"、"Options volatility observation contract" 两大段迁到各自 contract 文件（或 fred_liquidity.md 同级的 research-facts 文档），本表只留链接。
4. §4 Learning Contribution Observability 段（#614/#470）删除——与 spec 0001 冲突且是进度。
5. 修 spec_data_utilization.py：去掉"文档包含 market_observer"类文字断言，改断言真实调用路径（另立 Issue）。

## docs/design/data/exchange_source_alignment.md（87 行）

**角色**：Contract，单一，具体（状态模型、409 code、mutation guard）。**真实性**：`config/exchange_alignment.py` 存在；409 code `exchange_source_divergence_confirmation_required`（routes/settings.py:548）存在；`market_data_exchange` 无账户时回落 `"binance"`（exchange_alignment.py:83）属实。
**问题**：执行模式表把 `binance_testnet`、`okx_demo` 与 `bitget_demo` 并列（:31-35），无 frozen 标注；无账户时默认 `binance` 与"Bitget-only 产品"矛盾（应 fail-closed `not_configured` 或默认 bitget）——这是代码层值得 owner 判断的小 D1。`exchange.paper_execution_mode` 键名残留 paper（memory 记录"剩 exchange.name/allowed_exchange 两个身份键"正在收敛）。
**结论：KEEP** —— 表中 OKX/Binance 行加 "frozen, historical identity only"；注明 `binance` 回落默认值为待修问题（另立 Issue）。

## docs/design/data/fred_liquidity.md（102 行）

**角色**：Contract（研究数据源），单一，非常具体（series、单位、cutoff、公式）。**真实性**：`data/macro_liquidity.py`、`fred_source_batches`（migrations.py:8437 v86）存在。
**合理性**：observation/research-only，无交易消费者（:4-6 自述）。对"AI 像人一样交易、快速上线"目标是**可选支线**；按 data_utilization R1 是显式 observation-only，合规。:1-6 "Owner: #3101"、"inspected 2026-09-11"、fixture 字节测量（:63-64）属证据，可删。
**结论：KEEP**（低优先；若做产品收缩，整组 FRED/options/positioning 研究事实可冻结）。

## docs/design/data/RETENTION_POLICY.md（349 行）

**角色**：Contract（保留窗口 + 永不裁剪集合）+ Runbook（§12 一次性 backfill）+ 历史（§1 Phase 2 审计、"paths as of 2026-07-17"、CASI 2026-08-06 测量）混合。
**真实性**：`_PRUNABLE`（repositories.py:2494）、`prune_market_regimes`（:6009）、`prune_universe_snapshots`（:6133）、`prune_controller_recommendations`（repositories_research.py:987）、`ops/maintenance/data_retention_backfill.py` 存在。**§3.5 `backtest_runs`/`backtest_trades` 已于 v91 DROP**（migrations.py:7760-7770），文档仍列为可裁剪表——失真。§2.1 永不裁剪集合含 `ps_orders/ps_fills/ps_positions`（paper simulator 表，仍存在 migrations.py:1018，作历史保留合理但应注明）。§2.2 "9 tables" 含 `learning_hypotheses` 等——spec 0001 REMOVED `LearningHypothesis` 12 态生命周期，实施后需同步。§12.1 "Working copy on `iridite/sprat` branch" 是过期分支名。:8 Issue #2737 段属进度。
**结论：SPLIT** —— §2-§6 留作 Contract（删 backtest 行、ps_* 标 historical、§5 "paths as of" 改符号）；§12 runbook 移 `docs/operations/runbooks/`（删 sprat 分支行）；§1 审计叙事与 :6-8 删除。

## docs/design/data/RUNTIME_COST_ACCOUNTING.md（81 行）

**角色**：Contract，单一、具体（unknown≠zero、无手工价格 writer）。
**问题**：:22、:52 "event-driven System Operator daemon … `logical_agent=pi_trading_decision`"——把 Entry Planner daemon 称为 "System Operator"，与 CGT"TS operator-shell 是唯一 Operator"术语冲突，应改为 "Entry Planner daemon"。
**结论：KEEP**（只改术语）。

## docs/design/market-systems/decision_context_freshness.md（99 行）

**角色**：Contract（freshness 策略），单一、具体（各域年龄上限表、fail-closed 规则、两时钟）。**真实性**：`core/aletheia/data/freshness.py` 存在；`build_market_context_from_db` 存在且被 `trading/entry_snapshot.py:186` 调用。
**问题**：§Runtime paths（:84-86）"`market_observer` delegates that assembly…separately freshness-gates on-chain, cross-venue, perp-structure"——Market Observer 已退役（`find -name 'market_observer*'` 零命中），这三类块现无消费者。:43 "Context, Market Observer, Regime … share this projection" 同。:30 #1465 Issue 链接作权威依据。
**合理性**：主线必需（Entry Planner 上下文与 Universe features 的数据新鲜度门）。
**结论：KEEP** —— Runtime paths 改为 "Entry Planner context (`trading/entry_snapshot.py`) via `build_market_context_from_db()`；`universe/features.py`"；删 market_observer 两处；DefiLlama/cross-venue/perp-structure 三行若无当前消费者则从表删除。目录 `market-systems/` 只两份文档，建议本文移入 `docs/design/contracts/`（或 data/）。

## docs/design/market-systems/filter_replay.md（95 行）

**角色**：Contract（反事实 L3 有界回放 + 前瞻 shadow 实验）。**真实性**：实现在 `extensions/counterfactual/filter_replay.py`（文档未给路径）；`counterfactual_contract.md:38` 已把它列为 L3 并由该 contract 拥有。
**问题**：与 counterfactual_contract 职责重复；"spot/perp"（:39）出范围；`family_ttl` 适配器服务旧 Opportunity allocator `_FAMILY_TTL`（routes/analytics.py:1342），需确认在 Candidate 主线是否仍有生产 TTL gate。
**结论：MERGE into `docs/design/contracts/counterfactual_contract.md`**（该 contract 不在我的范围，只给位置判断；内容合并后本文删除，`market-systems/` 目录随之消失）。

## docs/design/decisions/README.md（50 行）+ template.md（31 行）+ source_first_python_runtime_convergence.md（156 行）

**角色**：Decision（ADR）。README/template 结构合理。ADR-0001 内容有效且已有 "Outcome (2026-09-28)"（native/、packaging/ 已删，`ls` 确认不存在）。
**问题**：决策现有**三个位置**——`docs/governance/decisions/DECISIONS.md`（时间线日志）、`docs/design/decisions/`（ADR，仅 1 份）、spec 的 Decision log（新）。仅一份 ADR 撑起一个目录 + 模板，属过度结构。ADR-0001 "Candidate retirement surface (deferred to R1–R6 under #657)" 段已因 Outcome 段完成，属进度残留。
**结论**：README/template **MERGE**（ADR 规则并入 documentation_governance.md 的 Decision 行，模板可删——新设计走 specs/ Decision log）；ADR-0001 **KEEP**（删 "deferred to R1–R6" 段，Outcome 留一行）。若删目录需同步 check_docs.py:93 与 documentation_governance.md:21。

## docs/design/subsystems/causal_counterfactual_research_guidance.md（77 行，只判位置）

方法论指引（L0-L5 证据阶梯），被 counterfactual_contract.md:9 引用。`subsystems/` 目录仅此一份。**位置判断：MOVE 到 `docs/research/counterfactual/`**（它是研究方法指引，不是 subsystem 设计；且该域的 owner 是 counterfactual contract）。`subsystems/` 目录随之删除。

## docs/operations/README.md（33 行）

**角色**：Navigation，单一。问题：Subfolders 表 `architecture/` 列为"Stability target architecture"——那是目标设计（见下）不是操作文档；:17 指向 TRADING_DIAGNOSTICS 作为 "Alpha, opportunity-funnel" 诊断入口（legacy 视角）。**结论：KEEP**（随 architecture/ 迁出与 TRADING_DIAGNOSTICS 改写同步更新链接；加一行 "Candidate→Entry Plan→Order 链路诊断入口"）。

## docs/operations/development/TRUST_BOUNDARY_COVERAGE.md（148 行）

**角色**：Operations/Policy（覆盖率门）。**真实性**：`.coveragerc` 实际 include 11 个文件，但文档表列 12 行且路径错：`aletheia/learning/policy_epochs.py`（不存在，.coveragerc 无此项）、`aletheia/learning/evidence_epoch.py`（实为 `extensions/learning/evidence_epoch.py`）、`aletheia/counterfactual/filter_replay.py`（实为 `extensions/counterfactual/filter_replay.py`）；.coveragerc 自身注释说"ten trust-boundary files"而列 11 个。§How the gate runs（:55-）写"`review` job in `.github/workflows/ci.yml`""fails CI"——**远程 CI 已退役**（AGENTS.md；ci.yml 仅 `workflow_dispatch`，:9-10），门实际由 `scripts/ci/ci_plan.py:35/167/193` 本地触发。
**合理性**：只门控信任边界而非全仓 % 很合理；但 11 个里 4 个是 profit_gradient 系列（研究/学习面），主线 Risk/Gateway/entry_plan_compiler/entry_sizing 反而**不在**覆盖门内——与"State consistency > Safety"优先级倒挂。
**结论：REWRITE** —— 表改为由 .coveragerc 生成或只写"见 .coveragerc"；所有 CI 措辞改为 `ci_plan.py`/`ci_local.py` 本地门；本地复现命令改走 casi-pytest；向 owner 建议把 `risk/engine.py`、`execution/gateway.py`、`trading/entry_plan_compiler.py`、`trading/entry_sizing.py` 纳入（D1，需批准），profit_gradient 系列随 spec 0001 重评。

## docs/operations/development/DEVELOPMENT_PREVIEW.md（234 行）

**角色**：Operations，单一、具体（端口、数据根、fail-closed 列表）。**真实性**：`is_preview_mode` + `_reject_preview_execution`（execution/gateway.py:11/57）存在；dashboard_static 输出路径与 frontend/vite.config.ts:52 一致。
**问题**：:113 "paper-approval route returns `403 preview_execution_blocked`"——grep `preview_execution_blocked` core/extensions **零命中**，paper approval 路由随模拟盘退役；:118-120 "stable gateway's explicitly authorized paper workflow" 同为退役语义。§Parallel remote previews 与 QUALIFICATION §7.2 重复。
**结论：KEEP** —— 删 :113 行与 :118-120 paper 字样（改为 "Bitget Demo 账户行为只在 qualification runtime 验证"）；§Parallel 只保留一处（本文），QUALIFICATION 链接过来。

## docs/operations/development/ASYNC_PERFORMANCE.md（195 行）

**角色**：Guidance（规则 1-5）+ 事故根因叙事（§1-2，2026-07-02 commit `0c46da3`）+ 待办（§5 "已知遗留慢点"）混合。§5 是进度/backlog，违反"no progress state in docs"。§6-8（测试替身、TestClient、诊断开关）是有效规则。
**结论：REWRITE** —— 删顶部事故横幅与 §2 叙事；§5 转 GitHub Issue 后删除；规则 1-5 + §6-8 保留，约 80 行；或 MERGE 进 STATE_AND_DATA.md 的 async API 段（该 guide 已覆盖 "async API" 领域，见 AGENTS 表）。

## docs/operations/development/ORCA_CLI_WORKFLOW.md（185 行）

**角色**：Operations，单一。raw git 用于 Orca Git worktree 符合 AGENTS "raw git only where the surface is git by construction"；合并 `--rebase`、禁 `--auto` 与 PR_REVIEW_LIFECYCLE 一致。
**问题**：:4-5 "Git, GitHub PRs, and CI remain the durable proof"——CI 已退役；:88-91 "PR #3506 retired… 2026-09-26 decision" 叙事，留规则即可。与 GIT_WORKTREE_PLAYBOOK 的关系应写清"Orca worktree 不能跑本地门禁"（Playbook 已写）。
**结论：KEEP**（删 CI 字样与 PR 叙事）。

## docs/operations/development/QUALIFICATION_DEVELOPMENT_PROTOCOL.md（320 行）

**角色**：Operations/Policy（机器角色、实例所有权、窗口冻结）。§2 "运行事实不进 Git"表很好。
**与 AGENTS.md 矛盾（重要）**：§4（:81-83、:94）"首选 GitHub Actions self-hosted Runner；Runner 不可用时在 Fedora 回退执行；Windows 不属于验证依赖或回退路径"——AGENTS.md 现规定 **pytest 在 CASI WSL 跑、不在 Mac 或 Fedora、远程 CI 退役**。两条直接冲突。§3 "不同 Agent 的非平凡修改通过独立 Git Worktree 隔离"与 jj workspace 规则冲突；§3 "Tray" 已随 native/ 删除（ADR-0001 Outcome）；§11 "多个临时 CI/Test process"同。:8 链接 `product/experiments/EXPERIMENT_PROTOCOL.md` 存在。
**结论：REWRITE** —— §4 改为三机：Mac=开发控制、Fedora=qualification runtime（不跑测试）、CASI WSL=pytest（链接 casi-pytest skill/DEVELOPMENT_WORKFLOW）；全文 Git Worktree→jj workspace；删 Tray、self-hosted Runner、CI 字样；§7.2 并入 DEVELOPMENT_PREVIEW；目标 ~200 行。

## docs/operations/development/PYTEST_OPTIMIZATION_GUIDE.md（415 行）

**角色**：Guidance + 大量测量证据（§六 2026-09-28 Fedora 实测、§七 已修复失败根因、§八 实现细节）。
**矛盾**：:155、:294 "全量只在 Fedora 测试环境跑（见根 AGENTS.md）"——**AGENTS.md 现在说 CASI WSL、Fedora 不跑**，引用反了；:363/:394/:413 把 `scripts/dev/test_ai.ps1`（Windows PowerShell，存在）列为 AI 默认入口，与 casi-pytest 不一致；:8-14 "历史基线 1340 个测试"。§六 `-n 2` 结论是 Fedora 16CPU/14GB 的测量，不能直接迁到 CASI。
**结论：REWRITE** —— 删 §历史基线、§七；§六 测量移 research/evidence 或删，只留"并行失败先串行复跑"规则；所有"Fedora 跑全量"改 CASI WSL；test_ai.ps1 标历史或删除；目标 ≤200 行。

## docs/operations/recovery/OUTCOME_HISTORY_REMEDIATION.md（32 行）

**角色**：Runbook，单一。命令 `knowledge remediate-outcomes` 在 cli/knowledge_cmd.py 存在。问题：通篇 "paper-cash compensation"、"paper cash ledger"——服务于已退役模拟盘的历史数据；knowledge 派生项归档与 spec 0001（Playbook 取代 knowledge lessons）相关。
**结论：ARCHIVE**（模拟盘历史修复工具；若命令仍需保留，加一行"仅适用于 retired simulator 历史记录"）。

## docs/operations/runbooks/LEARNING_QUALIFICATION_CLI.md（35 行）

**角色**：Runbook。`learning nominations/knowledge/yield/meta-recommendations` 命令在 cli/learning_cmd.py；"do not admit Paper" 等 paper 语义。spec 0001 REMOVED `LearningHypothesis` 生命周期（nominations/hypothesis 即其载体）。
**结论：ARCHIVE/待 spec 0001 实施后删除**（现在先加一行 "superseded by spec 0001 on implementation"）。

## docs/operations/runbooks/LLM_HARNESS_TELEMETRY.md（97 行）

**角色**：Runbook，单一、具体（命令 + Fedora 为运行事实源）。:12-16 "Fedora runtime host is source of truth"与 reference_fedora_u7p 一致。README 称其 "CASI-authoritative"（operations/README.md:19）——**README 与本文矛盾**（CASI 是测试机，非运行时），改 README。
**结论：KEEP**。

## docs/operations/runbooks/manual_close_reconciliation.md（129 行）

**角色**：Runbook，单一、主线（close 侧 submission_unknown 人工处置）。`manual_reconciliation_pending`（execution/close_convergence.py:26）、`scripts/maintenance/manual_close_diagnostic.py` 存在。写法具体（触发、可观测、两步 fingerprint）。
**结论：KEEP**（本批最好的 runbook）。

## docs/operations/runbooks/TRADING_DIAGNOSTICS.md（80 行）

**角色**：Runbook。**问题**：围绕 legacy Opportunity funnel 设计——`opportunity audit` 视图含 `ranker-objective`、`controller`（cli/opportunity.py:12-15）、stage 词表 `signal_pending/operator_skip/debate_downgraded`（Python Operator/Ranker 时代）；"Hidden compatibility aliases" 表（funnel/sizing/alpha 旧命令）违反"无 alias"。**当前主线 Candidate→Entry Plan→Risk→Order→Fill→Position 没有对应诊断 runbook**（实际可用：`inspect candidates`（inspect_cmd.py:681）、`inspect position-story`（:2181）、`inspect position-path`、Entry Workbench API）。
**结论：REWRITE** —— 以主线链路重写："计划多无成交"→ execution liveness（SYSTEM_MAP 导航行）、"有 Candidate 无 Plan"→ `inspect candidates`、Position 故事→`inspect position-story`；legacy `opportunity audit` 降为"历史数据"一节；alias 表删除并开 Issue 删 alias 代码与 ranker-objective/controller 视图。

## docs/operations/architecture/STABILITY_TARGET_ARCHITECTURE.md（221 行）

**角色**：自称 "plan"（:3），Phase 0-5 路线图 + "Implemented/required controls" 混写——是 Proposal + 进度，不是 Operations。Exit checks 用 PowerShell（Windows 时代）；"Windows/Telegram notifications"。
**结论：ARCHIVE**（已实现部分由 GATEWAY_SESSION_SERVICE.md + SYSTEM_MAP §零 描述；未实现 Phase 3-5 若仍要做 → CONVERT 到 specs/）。

## docs/operations/architecture/RUNTIME_COMMUNICATION_LANES.md（50 行）

**角色**：Contract（前端轮询分道规则）。`/api/ready`（routes/health.py:201）存在。内容是前端/API 行为规则，放 operations/architecture 位置不对。
**结论：MERGE into `docs/design/contracts/status_surface_contract.md`**（或 dashboard_data_presentation_contract），`operations/architecture/` 目录随之清空删除。

## docs/operations/deployment/GATEWAY_SESSION_SERVICE.md（108 行）

**角色**：Operations，单一、具体（三平台映射、无 KeepAlive）。与 SYSTEM_MAP §零 一致。§Windows LAN dashboard access、§Windows machine service（no longer installable）——Windows 已"never a fallback"，这两节应压到一行历史说明。
**结论：KEEP**（压缩 Windows 段）。

## docs/operations/deployment/UPDATE_OBSERVER_CONTINUITY.md（158 行）

**角色**：Operations/Contract（更新时 Observation Lab 连续性检查）。`observation.execution_owner`（schema.py:2146）存在。:3 "Issue #1135 extends" 叙事开头。Observation Lab 属可选研究面（CGT: research optional）；本文把它作为部署硬门的一部分，复杂度与"ship fast"目标不匹配——但不在本审计决定范围。
**结论：KEEP**（删 Issue 开头；顶部加一句"仅当 observation.execution_owner=worker 时适用"）；建议 owner 评估 Observation Lab worker 拆分是否值得保留（若冻结研究面，本文整体 ARCHIVE）。

## docs/operations/deployment/CROSS_PLATFORM_SERVICE_ENVIRONMENT.md（64 行）

**角色**：Operations。:3 "runs as a long-lived background service on both Windows and macOS"——**与事实相反**：运行时在 Fedora Linux（systemd --user），macOS 只开发、Windows 永不回退（AGENTS.md）。§Node operator shell 解析顺序（`resolve_node_executable`）是有用事实。
**结论：REWRITE** —— 首段改为"Fedora (systemd --user) 是运行平台；macOS 仅开发"；Windows 段删除；Node 解析顺序保留；或整份 MERGE 进 GATEWAY_SESSION_SERVICE.md（二者同为 service 环境）。

---

## 跨文档问题（Cross-doc）

**矛盾**
1. **测试在哪跑**：AGENTS.md（pytest 在 CASI WSL，不在 Mac/Fedora，远程 CI 退役）vs QUALIFICATION_DEVELOPMENT_PROTOCOL §4（self-hosted Runner 首选、Fedora 回退、"Windows 不属于验证路径"）vs PYTEST_OPTIMIZATION_GUIDE :155/:294（"全量只在 Fedora 跑，见根 AGENTS.md"）vs TRUST_BOUNDARY_COVERAGE（`review` job in ci.yml）。四份文档三种说法。AGENTS.md 自身也同时写"Fedora the verification target, Windows never a fallback"与"Run pytest on CASI's WSL"，需 owner 一句话裁定后统一。
2. **workspace 位置**：GIT_WORKTREE_PLAYBOOK §2 "一律 .claude/worktrees/" vs DEVELOPMENT_WORKFLOW.md:206-223 `/tmp/baseline`。
3. **"System Operator" 一词**：RUNTIME_COST_ACCOUNTING、data_utilization、SYSTEM_MAP §四/§六 仍把 Entry Planner daemon 或已退役 Python loop 叫 System Operator；CGT 与 OPERATOR_SHELL 规定 TS operator-shell 是唯一 Operator。
4. **Serenity promote**：api_reference.md:488 写 signal_pending PiDecision；SYSTEM_MAP B2 与代码（routes/data.py:2893）是 canonical Candidate publication。
5. **原始错误保留**：active_universe_ohlcv_catchup.md:40、data_collector_health.md:43 "不持久化原始错误" vs SAFETY_AND_TRUST.md:49-52（#3880 保留原始错误 + reason token）。
6. operations/README.md:19 称 LLM telemetry "CASI-authoritative"，runbook 本身说 Fedora 是运行事实源。
7. operator_layer.md:84 把 PI_OPERATOR_DESIGN_DECISIONS 称为"更高层权威文档"——反向权威链。

**重复**
- data_collector_health.md / market_data_observability.md / contracts/market_data_coverage_contract.md 三份写同一 collector-health + data-coverage 语义。
- filter_replay.md 与 counterfactual_contract L3；causal_counterfactual_research_guidance 与 counterfactual_contract L0-L5 阶梯。
- DEVELOPMENT_PREVIEW §Parallel 与 QUALIFICATION §7.2。
- 决策三处：governance/decisions/DECISIONS.md、design/decisions/（1 份 ADR）、specs/*/Decision log。

**代码侧化石（文档如实描述了它们，但它们本身应删 —— 每项建议独立 D0 收敛 Issue）**
- `equity_observer` / `prediction_observer` / `divergence_scanner`（extensions/research，~1724 行）+ 两处重复注册（main.py:143-178、server/app.py:1108-1145）+ PredictionLens 页 + `/prediction/*` 15 端点 + `observed_stock_tokens` 现货 RWA 默认值。出 CGT 范围，默认 no-op。
- persona sizing：`persona_rollout` job（scheduler.py:1540）、persona.py、persona_rollout.py、sizing_policy.py（`pyramid_plan` 无调用者）——entry_sizing contract 明文不进主线。
- `render_enriched_context_md`（context_builder.py:168）无生产调用者；Hyperliquid/DefiLlama/top movers 等域按 data_utilization R1 已成 orphan。
- `GuardConfig.enforce_in_paper_ranker`、AgentModelsTab.tsx:300/311 "paper ranker" 文案、`PaperAuto` 权限名、`exchange.paper_execution_mode`、`min_bars_for_backtest`。
- Legacy `/analytics/opportunity-funnel|operator-decisions|opportunity-trace`、`opportunity audit` 的 ranker-objective/controller 视图与 3 个 hidden CLI alias、7 个前端 LegacyRedirect、`/universe/members/{id}`。
- `tests/specs/spec_data_utilization.py` 断言文档文本含 "market_observer"（:577/:864）——测试在保护过期文字。
- `tests/specs/spec_operator_low_conviction_defer.py` 疑似测已删除的 PiDecision/Opportunity-queued 路径（需核）。
- index.css:3206-3215 Blotter 孤儿注释。

**缺失 contract / 文档（主线有代码无文档）**
- 30 个已注册但 api_reference 未登记的路由，其中 `POST /api/positions/{id}/recover-parked-close`、`POST /api/settings/trading-mandate` 是交易相关变更端点，应进 trust-boundary 表。
- 当前主线（Candidate→Entry Plan→Order→Close）的诊断 runbook 不存在；TRADING_DIAGNOSTICS 仍是 legacy funnel。
- 覆盖率门不含 risk/engine.py、execution/gateway.py、entry_plan_compiler.py、entry_sizing.py。
- SYSTEM_MAP 漏 11 个 job（已知）。

**与 spec 0001 冲突**
- SYSTEM_MAP §四 把 ps_lessons→lesson_reviewer→Entry Planner、learned guards、trading_memo、retrieve_lessons、persona sizing Learning 写成 ✅ 当前；spec 0001 REMOVED ps_lessons 生命周期、lesson_reviewer 验证、learned guards 独立存储；Non-goal 禁止自动 sizing 学习（直接否决 dynamic_sizing_operator_persona_design）。
- RETENTION_POLICY §2.2 永久表含 `learning_hypotheses` 等 LearningHypothesis 生命周期表（spec 0001 REMOVED）。
- LEARNING_QUALIFICATION_CLI（nominations/hypothesis）、ui_design_system §Learning Workbench、no_fluff_ui 的 Learning 归属表、OPERATOR_SHELL `dreaming_due` 推荐，均依附将被 Playbook 取代的结构。
- api_reference `/settings/learning` "paper influence path / paper_10" 语义。

---

## 总表

| 文档 | 行数 | 角色 | 结论 | 首要问题 |
|---|---|---|---|---|
| design/architecture/SYSTEM_MAP.md | 177 | Navigation+Evidence 混合 | REWRITE | 漏 11 job；equity/prediction_observer 标 ✅ 实为出范围默认 no-op；§五 配置覆盖率表整表失真；§四 与 spec 0001 冲突 |
| design/architecture/OPERATOR_SHELL.md | 118 | Contract | KEEP | §6 事故叙事；`PaperAuto` 命名残留 |
| design/operator/PI_OPERATOR_DESIGN_DECISIONS.md | 427 | Decision（已退役对象） | ARCHIVE | ~90% 描述退役 Python Operator/Ranker/PaperTrader/spot；引用的 operator_loop.py、trading_decision.py、skip_policy.py 均不存在 |
| design/operator/dynamic_sizing_operator_persona_design.md | 255 | Proposal+实施日志 | ARCHIVE | 前提依附退役 operator_loop；entry_sizing contract 明文排除 persona sizing；persona_rollout 代码成输家 |
| design/frontend/entry_workbench_design.md | 72 | Contract | KEEP | Issue 号作状态；兼容别名 |
| design/frontend/no_fluff_ui.md | 84 | Policy | KEEP | 示例依附 guard/Learning 旧结构 |
| design/frontend/ui_design_system.md | 717 | Design system | KEEP | `.bl-*` 节失真（CSS 已为 0）；Learning Workbench 节需随 spec 0001 改 |
| design/frontend/webui_page_map.md | 80 | Navigation | KEEP | 如实，但列出出范围 Prediction Markets 与 7 个兼容重定向 |
| design/api/api_reference.md | 704 | Reference+Contract 混合 | REWRITE | 289 条全部存在但 30 条未登记（含交易变更端点）；Serenity promote 描述错；Ranker/paper 语义 |
| design/data/active_universe_ohlcv_catchup.md | 63 | Contract | KEEP | "不存原始错误"违反 #3880 惯例 |
| design/data/data_collector_health.md | 135 | Contract（重复） | MERGE → market_data_coverage_contract | 三份重复；原始错误冲突 |
| design/data/market_data_observability.md | 79 | Explainer（重复） | MERGE → market_data_coverage_contract | 与上同；spot/perp |
| design/data/data_utilization.md | 186 | Registry+子契约混合 | REWRITE | 消费者大量写退役 market_observer/paper_trader/ranker；render 函数无生产调用者；spec 测试断言旧文字 |
| design/data/exchange_source_alignment.md | 87 | Contract | KEEP | OKX/Binance 未标 frozen；无账户默认 binance |
| design/data/fred_liquidity.md | 102 | Contract（研究） | KEEP | 可选支线；Issue/测量证据可删 |
| design/data/RETENTION_POLICY.md | 349 | Contract+Runbook+历史 | SPLIT | backtest 表已 v91 DROP 仍列；sprat 分支；learning_hypotheses 与 spec 0001 |
| design/data/RUNTIME_COST_ACCOUNTING.md | 81 | Contract | KEEP | "System Operator daemon" 术语错 |
| design/market-systems/decision_context_freshness.md | 99 | Contract | KEEP（移 contracts/） | Runtime paths 写退役 market_observer |
| design/market-systems/filter_replay.md | 95 | Contract（重复） | MERGE → counterfactual_contract | 与 L3 重复；spot/perp |
| design/decisions/README.md + template.md | 81 | Decision 索引 | MERGE → documentation_governance | 三处决策位置，仅 1 份 ADR |
| design/decisions/source_first_python_runtime_convergence.md | 156 | Decision | KEEP | 已完成的 "deferred R1–R6" 进度段 |
| design/subsystems/causal_counterfactual_research_guidance.md | 77 | Research guidance | MOVE → research/counterfactual/ | 目录仅此一份，位置错 |
| operations/README.md | 33 | Navigation | KEEP | "CASI-authoritative" 错；链接需同步 |
| operations/development/GIT_WORKTREE_PLAYBOOK.md | 70 | Operations | REWRITE（改名 JJ_WORKSPACE_PLAYBOOK） | 基本与 jj 一致；/tmp 基线矛盾、名实不符、并发防御步骤过时 |
| operations/development/TRUST_BOUNDARY_COVERAGE.md | 148 | Policy | REWRITE | 3 个路径错/不存在；写成远程 CI 门；主线 Risk/Gateway 不在门内 |
| operations/development/DEVELOPMENT_PREVIEW.md | 234 | Operations | KEEP | `preview_execution_blocked` paper-approval 路由已不存在 |
| operations/development/ASYNC_PERFORMANCE.md | 195 | Guidance+叙事+backlog | REWRITE / MERGE → STATE_AND_DATA | §5 是待办清单 |
| operations/development/ORCA_CLI_WORKFLOW.md | 185 | Operations | KEEP | "CI remain proof" |
| operations/development/QUALIFICATION_DEVELOPMENT_PROTOCOL.md | 320 | Policy | REWRITE | 测试位置与 AGENTS 直接矛盾；Git Worktree/Tray/Runner 残留 |
| operations/development/PYTEST_OPTIMIZATION_GUIDE.md | 415 | Guidance+证据 | REWRITE | "全量只在 Fedora（见 AGENTS）"与 AGENTS 相反；test_ai.ps1 |
| operations/recovery/OUTCOME_HISTORY_REMEDIATION.md | 32 | Runbook | ARCHIVE | paper-cash 模拟盘历史修复 |
| operations/runbooks/LEARNING_QUALIFICATION_CLI.md | 35 | Runbook | ARCHIVE（spec 0001 实施时） | hypothesis/nomination 生命周期被 spec 0001 移除 |
| operations/runbooks/LLM_HARNESS_TELEMETRY.md | 97 | Runbook | KEEP | — |
| operations/runbooks/manual_close_reconciliation.md | 129 | Runbook | KEEP | —（最佳范例） |
| operations/runbooks/TRADING_DIAGNOSTICS.md | 80 | Runbook | REWRITE | 全是 legacy Opportunity/Ranker funnel；主线无诊断 runbook；alias 表 |
| operations/architecture/STABILITY_TARGET_ARCHITECTURE.md | 221 | Proposal+进度 | ARCHIVE（未完成 Phase→spec） | 目标路线图放在 operations；Windows/PowerShell |
| operations/architecture/RUNTIME_COMMUNICATION_LANES.md | 50 | Contract | MERGE → status_surface_contract | 位置错 |
| operations/deployment/GATEWAY_SESSION_SERVICE.md | 108 | Operations | KEEP | Windows 段可压缩 |
| operations/deployment/UPDATE_OBSERVER_CONTINUITY.md | 158 | Operations | KEEP | 只适用 worker 模式；Observation Lab 是否保留待 owner |
| operations/deployment/CROSS_PLATFORM_SERVICE_ENVIRONMENT.md | 64 | Operations | REWRITE / MERGE → GATEWAY_SESSION_SERVICE | 首句"Windows 与 macOS 长驻服务"与 Fedora 事实相反 |
