# Extensions 去屎山专项扫描

本轮建议 **1 张新问题卡、1 张旧修复遗漏的跟进卡**，另复核 3 张开放卡仍成立。最值得修的是候选模型验证把无完成证据的响应记为 verified，以及 controller 把注册/整轮执行异常折叠成成功；sync 的不完整导出和同秒目录碰撞已经有卡，不重复报新。

## 范围、基线与证据口径

源码基线为 `ed9d8bc330cbdc29eb02006337e24424141fbd29`，分支 `iridite/rival_analysis`；审计开始时工作树干净。主 Agent 独立完成，未派发其他 Agent。按约定覆盖 **analytics、research、exchanges、knowledge、telegram、model_endpoints、sync、counterfactual、regime** 九组，共 **78 个 Python 文件、25,125 物理行、325 个 AST ExceptHandler、199 个宽捕获**。原提议约 35k 是旧估算，不作为本轮实测。宽捕获为裸捕获或类型表达式含 Exception/BaseException；不是缺陷数。

对每个文件做结构、异常分支和函数尺度扫描，对下表根因做真实调用链复核。**这不等于 25,125 行逐行人工签署，也不代表九组扩展均已证明无缺陷。** 下游 Core、observation_lab 只为解释发现读取，不把它们计为本轮全面覆盖；extensions/data、learning、observation_lab 等不在这次约定的九组内。没有运行故障注入、测试套件或网络请求，没有读取实际凭证/运行数据库，也没有执行导出、导入、模型探测、研究任务或交易操作。只写审计文档，未改实现、建卡或改变冻结 Risk。

本轮使用当前源码、AST 和只读 GitHub 查重（2026-09-28），仓库无 `.codegraph/`。分类沿用五类标签；D0 指恢复已有真值/失败语义、Contract Delta NONE，不是变更授权。**扩展可选不等于没有生产职责**：Research 的候选产出和 Exchanges 的适配器有实际接线，不能按目录名判死代码。

| 模块 | 文件数 | LOC | except / 宽捕获 |
|---|---:|---:|---:|
| analytics | 15 | 6,666 | 76 / 47 |
| research | 11 | 5,242 | 72 / 48 |
| exchanges | 18 | 3,811 | 58 / 32 |
| knowledge | 8 | 2,087 | 22 / 17 |
| telegram | 4 | 630 | 18 / 16 |
| model_endpoints | 6 | 1,378 | 17 / 10 |
| sync | 4 | 826 | 10 / 6 |
| counterfactual | 9 | 3,898 | 40 / 16 |
| regime | 3 | 587 | 12 / 7 |

## 发现表

路径默认相对 `extensions/`；行号绑定上述基线。下列前五项是缺陷/旧卡复核，M 项是清理候选，不能混入新缺陷数量。

| # | file:line | 屎点类型 | 语义影响 | 建议动作 | D 级 |
|---|---|---|---|---|---|
| E1 · P2 · 新 | `model_endpoints/credential_routes.py:226–332, 560–594` | 契约漂移 | candidate probe 只要 broker 返回就记录 success/verified；没有文本且 completion_confirmed=False 也能通过 Verify & Save，兄弟 Ping 会拒绝 | 新卡：候选探测消费已有 ModelResponse 完成证据；保留合法 reasoning-only completion | D0，复用既有验证判定 |
| E2 · P2 · 遗漏 | `analytics/filter_controller.py:859–916` | 权威路径宽异常 / 契约漂移 | replay 注册或整个 runner 调用抛异常时只记 warning，默认计数仍为零，最终 pass_status 可为 success；CLI/调度器据此接受成功 | 跟进闭卡 [#1240](https://github.com/iridyne/aletheia/issues/1240) 的外围异常遗漏；在已有 pass result 传播失败阶段和原因 | D0 |
| E3 · P2 · 已存在 | `analytics/routes.py:29–32`；`confidence_calibration.py:171,287–310` | 契约漂移 | GET 缓存未命中会调用持久化 report builder，读取页面可插入新的 calibration knowledge item | 复用开放卡 [#1772](https://github.com/iridyne/aletheia/issues/1772)，保持读取无写入，持久化仍归已有显式刷新职责 | D0 |
| E4 · P1 · 已存在 | `sync/exporter.py:280–298` | 权威路径宽异常 | 必需表 execute/fetchone 失败被 continue，缺失表没有文件，最终仍能生成 checksum/manifest 和成功归档 | 复用开放卡 [#1406](https://github.com/iridyne/aletheia/issues/1406)，必需表失败阻断发布；可选表需显式标状态 | D0 |
| E5 · P2 · 已存在 | `sync/exporter.py:93–122` | 契约漂移 | 同 mode、同秒导出共享 staging 和默认 archive 名；可覆盖产物，finally 还可能删除另一调用的 staging | 复用开放卡 [#1407](https://github.com/iridyne/aletheia/issues/1407)，一个调用一个唯一 ID，复用现有 UUID 做法 | D0 |
| M1 · 低 | `sync/conflicts.py:1–97` | 死代码候选 | 旧 policy conflict detector 无仓内调用/注册；当前 importer 另有研究数据主键冲突处理，不调用这套策略比较器 | 同文件后续维护时确认删除整块；不新增兼容层、不借清理开放 policy 导入 | D0，等价清理 |
| M2 · 低 | `analytics/filter_registry.py:746–747`；`analytics/reasoning_feedback.py:310–346`；`exchanges/bitget/profile_config.py:27–29` | 死代码候选 | 三个顶层辅助函数在当前生产源码和测试中没有精确调用；存在孤立 API 表面 | 删除前复核动态注册/外部入口；随相关维护清理，不单独立卡 | D0，等价清理 |
| M3 · 低 | `research/divergence_scanner.py:263–706`；`research/research_worker.py:637–1006` | 巨型函数 >300 行 | run_divergence_scanner 444 行，_run_pipeline_inner 370 行；数据准备、模型调用、产出持久化交织 | 只在具体根因修复触及时拆已有职责，不为长度新建框架 | D0，等价整理 |

## 关键证据与最小验收

**E1：把成功 HTTP 往返当作有效推理验证。** 取 HTTP 200 body `{"choices":[{"message":{"content":""}}]}`：OpenAI adapter 的 `_extract_chat_completion_text` 接受 message 字典并返回空文本；`http_runtime.http_response_facts` 给出 completion_confirmed=False。`openai_compatible.py:197–258` 仍构造合法 ModelResponse，brokered_complete 仅委托 provider.complete，不替消费者验证内容。candidate route 随即记录 success，再 persist verified=True；`_verify_profile_candidate` 只看 verified，因此创建配置、修改配置以及精确模型路由验证都可消费这份错误证据。

这不是要求所有模型必须输出 OK：有明确 completion 证据的 reasoning-only 响应仍应成功。`server/routes/model.py:292` 已按“有文本或 completion_confirmed”判定；[Inference Harness Contract](../../../design/contracts/llm_inference_harness_contract.md) 区分 Ping、startup、structured 消费者策略。建议复用设置探测的已有完成判定，不能把 startup 的 token 条件或 structured 的 JSON 条件混入。#1961 是已关闭的 Ping 正向兼容修复，#1944 是已关闭的普通 TEXT 调用空响应修复，二者不覆盖这个 candidate 消费者。最小验收覆盖空内容/无终止标记拒绝、有效终止标记但空文本通过、正常文本通过，并检查失败不得记录成功验证或完成配置写入。上述是源码路径推导，未实际向模型发请求。

**E2：内层错误统计已修，外围 catch 仍漏。** 推荐持久化失败已设置 persist_failed；普通 window.fn 异常由 `observation_lab/runner.py:131–141` 统计 windows_errored，这些已能进入 partial/failed，不应重复报成“所有失败都成功”。剩下的问题是 `build_bounded_filter_replays` / `register_replay_ready_experiments` 以及整个 run_lab_windows 调用的 catch：只写日志，未设置任何失败状态。推荐持久化成功、注册查询失败、Lab 无单窗口错误这一组合最终返回 success；整轮 runner 若因导入或外围锁等原因抛错，同样保留零错误默认值。不能把“没有执行”编码成“执行了零个且没有错误”。

调用方已经依赖该结果：`cli/observe_cmd.py:573–594` 从 pass_status 推退出码（成功 0，否则 4），`agent_runtime/scheduler.py:3331–3341` 从它决定是否记录 job failure。修复应补齐现有结果，最小验收只需分别让 replay 构建、注册、runner 调用抛异常，断言非 success，并覆盖真正无待执行窗口的成功空轮；保留既有 Worker ownership/lock 语义。此项属于 #1240 同一失败传播契约的遗漏跟进，不另造框架，也不声称发生了生产 checkpoint 污染。

**E3：当前入口已经搬到 extensions，但副作用仍在。** app.py:1510 挂载 analytics_reports_router；route 缺报告时调用 build_calibration_report，满足样本条件并完成计算时执行 `_persist_report`。后者 insert_knowledge_item；GET 返回值会因此变成新的持久化报告。样本不足的 no_data 分支不会必然写入，不能说每次 GET 都会写。最小验收是有足够合格样本但缓存缺失时，反复 GET 不产生 knowledge 行，显式刷新仍由原有职责写入。只读临时计算和持久化刷新不必引入新调度框架。

**E4/E5：完整性和身份是两个独立根因。** `_export_db_tables` 把查询失败与空表都表示为“没有文件”；外层随后写 checksums/manifest，`server/routes/persistence.py` 把返回 archive 当作 status ok。归档能通过已有文件校验并不能证明所需表齐全。E4 的验收应让一张必需表查询失败，断言不发布成功产物，同时可读空表有明确结果。E5 的 bundle_name 精度只有秒，bundle_dir.mkdir(exist_ok=True)，默认 archive 用同名 w:gz 打开，finally 删除 staging。验收在固定时钟下连续/并发调用同一 mode，确认路径、staging、manifest 身份互不共享，并保留显式 output 参数的既有语义。未在本轮创建任何真实 bundle。

## 各组复核结论与保留边界

| 模块 | 重点复核与结果 | 本轮没有作出的结论 |
|---|---|---|
| analytics | 报告写入、controller 失败传播、promotion/insight 的异常出口；E2/E3；historical twin/relative-rank 读写职责分开 | 不把所有 report 缺失返回 None 当作静默缺陷，不按文件名删除历史读模型 |
| research | agent 权限、LLM helper、候选产出、pipeline 状态、savepoint、post-trade 失败汇总；列 M3 | 不把 Serenity/research worker 当死主线，不评估研究经济效果或改阈值 |
| exchanges | composition、CCXT 提交/读状态分类、Bitget 账务解析、credential probe 的边界 | CCXT 38 个 catch 不是 38 个错误；NOT_SENT/AMBIGUOUS/VENUE_REJECTED 及 PARTIAL/UNAVAILABLE 是必要区分；未接交易所验证 |
| knowledge | FTS/retrieval/router、guard 失败与 authority、归档/恢复异常；候选死辅助函数需区分历史文档 | 检索返回空的旧 #1909 尚需逐消费者重新确认，不在本轮计新卡；不能删除仍被内部调用的 _build_guard_advisory |
| telegram | health、allowlist 每消息重读、kill 二次确认、resume 委托、发送结果及去重重试、poll/update 错误隔离 | 未发现可仅凭 except 密度新立卡的根因；未实际发消息或触发控制命令 |
| model_endpoints | 三种协议事实归一、probe、身份绑定、Verify & Save、broker 调用；E1 | 不把已存在 broker deadline 忽略，也不把凭证配置存在等同模型可用 |
| sync | export、import 预检/DB 写入/文件复制、冲突识别；E4/E5/M1 | transport bundle 不等于可恢复备份；旧 #1404 的 full-backup 导入描述不能直接沿用，当前已拒绝 .db payload |
| counterfactual | 成本不可用、mixed epoch、Lab authority、replay 截断/实验资格、propensity/estimators 与 trades 失败出口 | 不改变 OPE、收益归因、实验门槛；源码扫描不能证明样本统计有效或产生 alpha |
| regime | collector 输入、新鲜度、持久化异常，detector 的 read-published 与 UI fallback 区分 | 显式 UI neutral fallback 不能直接当成交易授权；无 runtime 数据新鲜度结论 |

`knowledge/router.build_guard_advisory` 与 `seed_guards.ensure_seed_guards` 也无当前精确生产调用，但历史操作文档引用后者、旧审计引用前者；暂保留为待核入口，未计确定死代码。Twin/Relative-Rank 生成器在当前 Contract 中保留历史 fixture 重建用途，不能只因无调度调用就删；[#2906](https://github.com/iridyne/aletheia/issues/2906) 已有清理任务且明确保护历史评估器。post_trade_reviewer 当前已把 case builder 失败加入 errors，因此没有照搬旧 #1246 的“完全静默”描述。sync importer 的 DB error 会提前返回，旧 #1405 中这部分描述也不能原封不动复用；DB commit 后文件复制的原子性仍是后续核对点，本轮未完成隔离复现，不计新增卡。

## 全文件结构覆盖清单

下表列出实际纳入的全部文件；仅表示结构/异常扫描覆盖，不把未报告的文件标为“已证明安全”。人工深入的流程见上两节。

| 文件（相对 extensions/） | LOC | except / 宽捕获 |
|---|---:|---:|
| `analytics/__init__.py` | 1 | 0 / 0 |
| `analytics/confidence_calibration.py` | 310 | 6 / 3 |
| `analytics/filter_controller.py` | 1,406 | 20 / 18 |
| `analytics/filter_registry.py` | 754 | 0 / 0 |
| `analytics/guard_effectiveness.py` | 226 | 2 / 0 |
| `analytics/guard_impact.py` | 513 | 5 / 1 |
| `analytics/guard_promotion.py` | 527 | 10 / 9 |
| `analytics/insight_synthesizer.py` | 663 | 9 / 5 |
| `analytics/memo_effectiveness.py` | 229 | 6 / 2 |
| `analytics/reasoning_feedback.py` | 372 | 5 / 4 |
| `analytics/relative_rank_experiment.py` | 162 | 0 / 0 |
| `analytics/routes.py` | 60 | 0 / 0 |
| `analytics/trade_case_builder.py` | 445 | 7 / 5 |
| `analytics/twin_evaluation.py` | 783 | 6 / 0 |
| `analytics/twin_experiment.py` | 215 | 0 / 0 |
| `research/__init__.py` | 1 | 0 / 0 |
| `research/divergence_scanner.py` | 706 | 8 / 4 |
| `research/dreaming_agent.py` | 743 | 16 / 11 |
| `research/equity_observer.py` | 528 | 5 / 4 |
| `research/lesson_reviewer.py` | 568 | 7 / 5 |
| `research/post_trade_reviewer.py` | 601 | 4 / 3 |
| `research/prediction_observer.py` | 519 | 4 / 3 |
| `research/research_agent.py` | 93 | 1 / 1 |
| `research/research_worker.py` | 1,275 | 24 / 16 |
| `research/risk_critic.py` | 91 | 2 / 1 |
| `research/round_robin.py` | 117 | 1 / 0 |
| `exchanges/__init__.py` | 1 | 0 / 0 |
| `exchanges/binance/__init__.py` | 1 | 0 / 0 |
| `exchanges/binance/profile.py` | 162 | 0 / 0 |
| `exchanges/bitget/__init__.py` | 1 | 0 / 0 |
| `exchanges/bitget/auth.py` | 92 | 1 / 1 |
| `exchanges/bitget/client.py` | 326 | 2 / 0 |
| `exchanges/bitget/native_protection.py` | 41 | 0 / 0 |
| `exchanges/bitget/profile.py` | 399 | 3 / 2 |
| `exchanges/bitget/profile_config.py` | 29 | 0 / 0 |
| `exchanges/ccxt/__init__.py` | 1 | 0 / 0 |
| `exchanges/ccxt/adapter.py` | 1,329 | 38 / 22 |
| `exchanges/ccxt/order_rules.py` | 145 | 5 / 2 |
| `exchanges/ccxt/profile.py` | 539 | 3 / 1 |
| `exchanges/ccxt/submission_errors.py` | 30 | 0 / 0 |
| `exchanges/composition.py` | 244 | 4 / 2 |
| `exchanges/credential_routes.py` | 270 | 2 / 2 |
| `exchanges/okx/__init__.py` | 1 | 0 / 0 |
| `exchanges/okx/profile.py` | 200 | 0 / 0 |
| `knowledge/__init__.py` | 0 | 0 / 0 |
| `knowledge/guards.py` | 577 | 7 / 4 |
| `knowledge/maintenance.py` | 345 | 0 / 0 |
| `knowledge/manager.py` | 497 | 6 / 6 |
| `knowledge/recovery.py` | 189 | 3 / 2 |
| `knowledge/retrieval.py` | 202 | 3 / 2 |
| `knowledge/router.py` | 123 | 3 / 3 |
| `knowledge/seed_guards.py` | 154 | 0 / 0 |
| `telegram/__init__.py` | 1 | 0 / 0 |
| `telegram/bot.py` | 452 | 13 / 12 |
| `telegram/delivery.py` | 138 | 4 / 3 |
| `telegram/routes.py` | 39 | 1 / 1 |
| `model_endpoints/__init__.py` | 1 | 0 / 0 |
| `model_endpoints/anthropic_compatible.py` | 156 | 4 / 2 |
| `model_endpoints/credential_cli.py` | 71 | 0 / 0 |
| `model_endpoints/credential_routes.py` | 681 | 6 / 4 |
| `model_endpoints/http_runtime.py` | 136 | 2 / 1 |
| `model_endpoints/openai_compatible.py` | 333 | 5 / 3 |
| `sync/__init__.py` | 1 | 0 / 0 |
| `sync/conflicts.py` | 97 | 0 / 0 |
| `sync/exporter.py` | 349 | 6 / 4 |
| `sync/importer.py` | 379 | 4 / 2 |
| `counterfactual/__init__.py` | 25 | 0 / 0 |
| `counterfactual/advisories.py` | 296 | 2 / 1 |
| `counterfactual/calibration.py` | 339 | 5 / 2 |
| `counterfactual/estimators.py` | 202 | 0 / 0 |
| `counterfactual/family_scorecard.py` | 20 | 1 / 1 |
| `counterfactual/filter_experiments.py` | 513 | 8 / 1 |
| `counterfactual/filter_replay.py` | 1,087 | 7 / 0 |
| `counterfactual/propensity.py` | 466 | 1 / 1 |
| `counterfactual/trades.py` | 950 | 16 / 10 |
| `regime/__init__.py` | 5 | 0 / 0 |
| `regime/collector.py` | 222 | 10 / 6 |
| `regime/detector.py` | 360 | 2 / 1 |

## 建议立卡与验证状态

建议 **E1 新卡 1 张，E2 关联 #1240 的遗漏跟进卡 1 张**；E3/E4/E5 复用已有 3 张开放卡。优先 E4 的导出完整性、E1 的验证真值，再处理 E2/E5/E3；M1–M3 随实际维护清理，不单独立卡。server 的 Ping 身份错绑与 E1 的候选响应判断是两个根因，应各自可审阅，但可以共享既有验证工具和回归样本。

只读查重包含 candidate/probe/empty、verification/identity、controller/runner/exception、sync/export/import、calibration GET、knowledge retrieval 和 retired twin 等组合；阅读 #1240、#1772、#1406、#1407、#2906、#1961、#1944 的相关正文。未命中不保证全库无其他措辞重复项。所有“最小验收”都是后续修复要求，本轮未执行这些行为测试，也没有资格、经济有效性、部署或合并结论。

文档检查：`dev_check.py --no-tests --no-types` 与 `check_docs.py` 通过；Python/测试未改动，未运行测试套件。
