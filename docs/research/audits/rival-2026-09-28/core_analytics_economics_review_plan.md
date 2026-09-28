# Core analytics / economics：分析与修复方案

日期：2026-09-28。源码基线：`0318e5efc6bca6ac5be3393c68fcd90cc5195d70`，分支 `iridite/rival_analysis`。本文件是主 Agent 独立静态审查结果；没有修改业务代码、执行行为测试、创建 issue 或验证 Fedora。Issue 状态是本日 GitHub 只读查询结果，不能据此推断部署状态。

## 覆盖与结论

范围为 analytics 54 个 Python 文件、28,517 物理行、229 个 AST except，以及 economics 9 个文件、3,929 行、47 个 except。所有文件经过 AST 结构与宽异常入口扫描；重点阅读下列发现及 outcome integrity、trade attribution、trade stats、budgets、tracing、pricing refresh 的相关路径。**不声称 32,446 行逐行深审完成，也不把未列出的问题视为不存在。**

确认四项当前源码问题，其中三项复用已有 issue，一项在限定查重中未找到对应 issue。不按旧 brief 强行凑 top-5；函数超过 300 行仅作为阅读顺序依据。

| 项 | 根因与影响 | 证据位置（仓库相对路径） | 状态 / 分类 |
|---|---|---|---|
| A1 | 窗口汇总复用最多 5,000 条的明细读取，完整窗口被静默缩成最近后缀 | `core/aletheia/economics/context_artifacts.py:132,161`；API `server/routes/economics.py:229`；CLI `cli/audit_cmd.py:396` | 本轮确认；D0 |
| A2 | schema 完整性哨兵漏掉自身后续使用的表 | `core/aletheia/economics/schema.py:344`，required_tables 与后续 context ALTER/settings 读取 | [#1725](https://github.com/iridyne/aletheia/issues/1725) OPEN；D0 |
| A3 | Risk 24h 指标只读最近 20 条；读失败仍贡献 50 分；reconciliation 仍查询单一旧事件名 | `core/aletheia/analytics/go_live_scorecard.py:365,432` | [#1696](https://github.com/iridyne/aletheia/issues/1696) OPEN；事实修复 D0，新的准入政策单独决策 |
| A4 | 模型健康 producer 返回 list，scorecard 按 dict 读取 `.get`，必然进入 50 分异常分支 | `core/aletheia/model_provider/fallback.py:208`；`core/aletheia/analytics/go_live_scorecard.py:421` | [#1698](https://github.com/iridyne/aletheia/issues/1698) OPEN；形状修复 D0，健康准入规则须核准 |

### A1：完整窗口聚合

`summarize_context_artifacts()` 固定调用 `list_context_artifacts(limit=5_000)`，然后以该列表计算 artifacts、by_kind、rendered_chars 和 freshness。API 的 days/run_id 与 CLI 的 `--days` 都直接展示这些数字，没有 total/truncated 信息。窗口有 5,001 条时总数最多显示 5,000；仅存在于最旧部分的 kind 会完全消失。这是确定的静态数据流结论，本轮没有构造数据库运行复现。

修法：在现有 economics 模块直接使用同一时间边界和 run_id 条件做 SQL COUNT / 分组聚合，不读取全文、metadata_json，也不把 LIMIT 单纯调大。列表 API 保留分页上限；汇总独立覆盖整个窗口。多个聚合查询需共享一个读取快照或合为一次结果，避免并发插入令总数与分组数不一致。保持当前输出键，明确 rendered_chars 异常值处理，不能因 SQLite 的宽松 CAST 静默改变原有拒绝规则。

验收：内存数据集覆盖 0、5,000、5,001 条，最旧 kind、跨 run_id、窗口边界和 malformed rendered_chars；总数等于各 kind 之和，freshness 覆盖全窗口，API/CLI 明细 limit 不改变汇总。只使用合成数据。查重查询包含 `summarize_context_artifacts`、context artifacts、context truncation；“未命中”仅指这些查询，不承诺全历史无重复。

### A2：初始化必须证明自己承诺的 schema

当前 required_tables 有六张表；历史 #1725 的“五张”数字已过时，但遗漏仍在。若这六张存在而 `llm_context_artifacts` 缺失，完整 DDL 被跳过，随后空 PRAGMA 结果触发对不存在表的 ALTER；缺 `economics_settings` 也会进入后续查询失败。不能照搬旧 issue 的表数量或已退休对象。

修法：以当前 ECONOMICS_DDL 及真实消费者为准补齐必需对象检查，复用现有幂等 DDL、加列和触发器路径；在返回前确认必需对象存在。保留 caller transaction 内拒绝 DDL 的边界，不能让 executescript 提交调用方事务。不为每个路由增加一份 schema 修补，也不趁此改变成本归因或历史记录。

验收：分别删除当前遗漏表但保留六张哨兵表，ensure 后恢复可读取；反复 ensure 幂等；已有 caller transaction 不被提交；缺列、触发器升级和完整库路径维持原语义。若损坏状态不可安全修补，返回明确不可用，不能先宣称初始化完成。

### A3 / A4：先恢复证据，再确定准入映射

scorecard 的消费者包括展示 API 和 `server/routes/live.py:82` 的实际 Live unlock 门。后者还有 selected-account preflight、综合分、green 和 blockers 检查；本报告**不声称单个异常就能无条件解锁 Live**。问题是未知证据贡献正分且 warning 不进入 blockers，可能影响临界结果。A3 的旧 reconciliation 事件名与当前权威事件集合的对应关系仍需实施前核对全部 writer，不能以名称相似就替换。

A3 修法：去掉 Risk 24h 完整性指标的 LIMIT 20，计算完整选定账户窗口；将读取失败、空窗口、已验证零违规分别表达。对账查询先列出当前实际 writer 的事件名、账户身份和成功/失败含义，再统一读取事实。A4 修法：删除对 cooldown list 的伪 providers/available 解读，复用现有 ModelHealth 的 configured/enabled/reachable/verified/时间证据。由边界传入健康事实或读取已有快照；保持 scorecard 纯读取，不在计算分数时新增网络探测。仅把 `.get` 改成遍历 list 不够，因为 list 元素本身没有 available 字段。

实施前要对齐当前准入 Contract 与源码中“未知分值待 owner 决定”的注释：事实读取和形状纠错可独立实施；未知是否硬阻断、要求哪个模型以及 freshness 阈值不能从历史 issue 自动获得授权。这里给出的推荐是未知不算已满足，但不修改 Risk 策略、Live 权限或冻结模块。

验收：第 21 条存在违规时不得显示 clean；数据库读失败与真实零违规可区分；健康空集、过期、不可达、可达且验证、错误形状均有确定输出；临界综合分案例说明未知证据的实际映射。Live 路由验证只能用替身和隔离配置，断言失败时无配置/权限变化。权威事件映射未确认前不能将 A3 整项标完成。

## 实施顺序与未确认项

先 A1 / A2 两个独立 D0 根因，再处理 A4 的数据契约，A3 的准入映射按现行权威单独收口；每个根因一个可审阅改动，互不捆绑。完成要求为源代码修复、最小回归证据、调用方复核和明确 commit；这份分析不满足实现完成条件。

outcome_integrity 的 typed unavailable、trade_attribution 的读错误传播、缺失成本保持 None 都是保留真相的防御，不按 except 数量清理。三个 >300 行函数位于 opportunity_lifecycle_story、position_lifecycle_story/service、position_replay；目前只有规模信号，没有可证明的等价重复或独立行为缺陷，不建议机械拆分。analytics 其余 cohort、身份、时间/并发快照路径仍可继续深审，旧 #1968 等搜索命中未在本轮逐项重核。

## 全文件结构扫描清单

口径：物理行包含空行/注释；except 为 AST ExceptHandler；宽指 bare except 或捕获 Exception/BaseException。该清单证明结构扫描覆盖，不代表每个文件都完成调用链深审。

| 文件 | 物理行 | except / 宽 |
|---|---:|---:|
| `core/aletheia/analytics/__init__.py` | 12 | 0 / 0 |
| `core/aletheia/analytics/alpha_attribution.py` | 1555 | 10 / 2 |
| `core/aletheia/analytics/attribution.py` | 114 | 1 / 0 |
| `core/aletheia/analytics/candidate_facts.py` | 825 | 9 / 1 |
| `core/aletheia/analytics/candidate_supply.py` | 105 | 0 / 0 |
| `core/aletheia/analytics/causal_controls.py` | 259 | 4 / 4 |
| `core/aletheia/analytics/debate_impact.py` | 342 | 2 / 0 |
| `core/aletheia/analytics/entry_exit_geometry.py` | 1047 | 14 / 8 |
| `core/aletheia/analytics/execution_mode.py` | 74 | 1 / 1 |
| `core/aletheia/analytics/exit_attribution.py` | 127 | 0 / 0 |
| `core/aletheia/analytics/exit_breakeven_shadow.py` | 124 | 1 / 0 |
| `core/aletheia/analytics/exit_driver_comparison.py` | 185 | 2 / 0 |
| `core/aletheia/analytics/exit_policy_advisor.py` | 190 | 0 / 0 |
| `core/aletheia/analytics/exit_trailing_qualification.py` | 430 | 5 / 0 |
| `core/aletheia/analytics/factor_feedback.py` | 197 | 0 / 0 |
| `core/aletheia/analytics/family_scorecard.py` | 181 | 1 / 0 |
| `core/aletheia/analytics/filter_evidence.py` | 1532 | 19 / 11 |
| `core/aletheia/analytics/go_live_scorecard.py` | 484 | 13 / 13 |
| `core/aletheia/analytics/holding_time.py` | 30 | 0 / 0 |
| `core/aletheia/analytics/learning_window.py` | 80 | 2 / 0 |
| `core/aletheia/analytics/momentum_geometry_replay.py` | 493 | 8 / 0 |
| `core/aletheia/analytics/opportunity_flow.py` | 1294 | 11 / 5 |
| `core/aletheia/analytics/opportunity_lifecycle_story.py` | 888 | 7 / 0 |
| `core/aletheia/analytics/opportunity_progress.py` | 16 | 0 / 0 |
| `core/aletheia/analytics/outcome_integrity.py` | 796 | 9 / 2 |
| `core/aletheia/analytics/outcome_semantics.py` | 285 | 0 / 0 |
| `core/aletheia/analytics/performance_summary.py` | 143 | 0 / 0 |
| `core/aletheia/analytics/pi_evidence.py` | 188 | 1 / 0 |
| `core/aletheia/analytics/position_lifecycle_story/__init__.py` | 24 | 0 / 0 |
| `core/aletheia/analytics/position_lifecycle_story/events.py` | 207 | 1 / 0 |
| `core/aletheia/analytics/position_lifecycle_story/readers.py` | 535 | 4 / 0 |
| `core/aletheia/analytics/position_lifecycle_story/render.py` | 209 | 0 / 0 |
| `core/aletheia/analytics/position_lifecycle_story/service.py` | 1740 | 3 / 0 |
| `core/aletheia/analytics/position_path_observations.py` | 552 | 1 / 0 |
| `core/aletheia/analytics/position_replay.py` | 860 | 7 / 0 |
| `core/aletheia/analytics/profit_gradient.py` | 1067 | 4 / 0 |
| `core/aletheia/analytics/profit_gradient_estimators.py` | 522 | 0 / 0 |
| `core/aletheia/analytics/profit_gradient_evidence.py` | 2028 | 21 / 3 |
| `core/aletheia/analytics/profit_gradient_evidence_utils.py` | 100 | 3 / 0 |
| `core/aletheia/analytics/profit_loop_counterfactual.py` | 572 | 8 / 2 |
| `core/aletheia/analytics/profit_loop_diagnosis.py` | 427 | 1 / 0 |
| `core/aletheia/analytics/ranker_objective_audit.py` | 869 | 6 / 0 |
| `core/aletheia/analytics/sizing_counterfactual.py` | 308 | 4 / 1 |
| `core/aletheia/analytics/sizing_trace.py` | 712 | 5 / 0 |
| `core/aletheia/analytics/skip_diagnosis.py` | 409 | 1 / 1 |
| `core/aletheia/analytics/streaks.py` | 97 | 0 / 0 |
| `core/aletheia/analytics/temporal.py` | 89 | 0 / 0 |
| `core/aletheia/analytics/thesis_lineage.py` | 1257 | 9 / 0 |
| `core/aletheia/analytics/trade_attribution.py` | 460 | 7 / 3 |
| `core/aletheia/analytics/trade_funnel.py` | 948 | 3 / 0 |
| `core/aletheia/analytics/trade_insights.py` | 633 | 4 / 4 |
| `core/aletheia/analytics/trade_path.py` | 952 | 8 / 4 |
| `core/aletheia/analytics/trade_stats.py` | 356 | 1 / 0 |
| `core/aletheia/analytics/trading_mandate_compliance.py` | 588 | 8 / 2 |
| `core/aletheia/economics/__init__.py` | 5 | 0 / 0 |
| `core/aletheia/economics/agent_roles.py` | 39 | 0 / 0 |
| `core/aletheia/economics/budgets.py` | 458 | 7 / 3 |
| `core/aletheia/economics/context_artifacts.py` | 175 | 4 / 1 |
| `core/aletheia/economics/inference_calls.py` | 352 | 6 / 4 |
| `core/aletheia/economics/pricing_sync.py` | 657 | 11 / 6 |
| `core/aletheia/economics/schema.py` | 431 | 0 / 0 |
| `core/aletheia/economics/tracing.py` | 1049 | 12 / 3 |
| `core/aletheia/economics/usage_insights.py` | 763 | 7 / 3 |
