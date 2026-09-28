# 项目收敛分析结论 — 2026-09-28

## 使用范围与证据身份

本文件是日期化的 **Evidence / Archive**，用于避免后续 Agent 重复调查已查明的边界，
不是现行 Contract、任务看板或实时健康证明。优先级、审批及交付状态以
[#3438](https://github.com/iridyne/aletheia/issues/3438) 和关联 Issue/PR 为准。

- 源码核对基线：本地集成提交 `8d604eecf1dd0bee0eb4e4d245be509424ca41f6`。
  可发布追溯点包括 `5426e5222e5d4a761caefc1c3d89c42877baba24`、
  [PR #3608](https://github.com/iridyne/aletheia/pull/3608) 的
  `88c0234c3326279808f1f54c240a3ec5306f03e4` 和
  [PR #3609](https://github.com/iridyne/aletheia/pull/3609) 的
  `e10af70eaf0b75e9f26dc54593eea3b9615ab269`。不得把本地集成 SHA 当成已部署版本。
- Fedora 运行观察基线：`a8dc0613956bb3a5a35aaf8e43dddb09ec488cc6`，
  Bitget Demo、schema 90。以下运行数值均有明确观察时段，不表示阅读本文时仍然如此。
- 子 Agent 提供的下游发布分析已由主 Agent 读取关键实现并运行回滚用例复核。
  本记录不把尚未复核的上游拆分建议写成结论。
- 不记录凭据、完整配置、模型原始载荷或整段调试日志。临时日志路径只是辅助定位，
  不能代替本文的结论、复现入口和关联 Issue。

## 1. Candidate 发布到 Planning：保留现有原子边界

**结论：在本次核对的交接范围内，没有证明存在重复状态所有者；不新增协调层，
不为了缩短 `refresh()` 把发布事务拆开。** 多个写入函数承担不同职责，不等于多个主线。

| 实现 | 已确认的责任 / 不可丢失的边界 |
|---|---|
| `universe/service.py::_atomic_universe_publication` | 无外层事务时获取 writer；已有事务时用 SAVEPOINT。失败必须回滚整个发布单元。 |
| `storage/repositories.py::publish_candidate_snapshot` | 写 snapshot 与 members；拒绝 member 指向其他 snapshot。不是 Planning 状态机。 |
| `ensure_candidate_planning_generation` | Generation 的创建、重资格与 CAS 生命周期；Universe 与 Serenity 是不同获准生产入口。 |
| `prepare_candidate_requalification` | 在发布 writer 事务外冻结证据；实现明确拒绝在事务内调用。不能把这项准备工作顺手搬进事务。 |
| `reconcile_candidate_attention` | 按行的原始 producer 身份限定 sweep，不能只相信调用方传入的 scope。 |
| `expire_opportunities_from_superseded_snapshots` | 旧 analytical generation 的级联过期及相关证据同样属于原子发布。 |
| `agent_shell/entry_planner.py` | 消费已发布的 Generation，拥有自己的 claim/defer/complete 责任；不是第二个 snapshot 发布者。 |

主 Agent 实际运行以下选择，**9 项通过**：

```bash
uv run pytest \
  tests/test_candidate_opportunity_persistence.py::test_candidate_snapshot_publication_rolls_back_partial_members \
  tests/test_candidate_opportunity_persistence.py::test_serenity_publication_rolls_back_all_rows_on_failure \
  tests/test_opportunity_funnel_integrity.py::test_superseded_cascade_rolls_back_status_when_evidence_fails \
  tests/specs/spec_universe_pipeline.py::test_target_publication_failure_preserves_previous_authority \
  -q --tb=short
```

**复用边界：**这不是整个 Storage 域的“已完成”结论。若修改发布顺序、事务位置、
Generation 身份或 producer scope，重读上述 owner 并重跑对应失败回滚用例。
单纯看到 facade/seam、多函数或较大文件，不足以推翻这个结论。

## 1a. 已删除的重复实现：twin replay 去重

**结论：`twin_persistence.py::_dedupe_for_replay` 是
`service.py::_dedupe_by_symbol_preferred_market` 的私有复制，已删除并通过既有 seam 复用主线实现。**

两者的 preferred/enabled_markets 读取、`market_rank`、`(rank, -total_score)` 比较与最终按
`total_score` 降序排序一致；注入接缝早已存在（`twin_allocation.py::replay_twin_shadow_allocations`
与 `relative_rank_shadow` 接受 `dedupe=` 参数），且既有测试本就传入主线函数，
只有生产重放路径使用克隆。删除后 `grep -rn "_dedupe_for_replay"` 在本仓库无剩余引用。

**重要更正（先前的表述不准确）：**`_dedupe_by_symbol_preferred_market` **不是**当前主线在用的去重。
全仓 grep 显示它只有定义与测试引用（`spec_market_type_dedup.py`、两个 twin 测试）；引入它的提交
`db3c3f867` 当时确实由 live 路径调用，但今天 live 的同 symbol 市场偏好发生在
`candidate_selection_shadow.build_candidate_selection_shadow_bundle`（`service.py:203` →
`_build_candidate_selection_bundle_owned:220` → refresh:790），用 Utility `decision_rank` + `market_rank`。
因此本轮只是把 replay 侧的**逐行相同副本**换成同一个函数（行为等价），**没有**改变 replay 与 live 的一致性；
后者是一个独立的保真度问题，已记录为 [#3617](https://github.com/iridyne/aletheia/issues/3617)
（现有批次里是否真的存在同 symbol 双市场样本、replay 是否应以 Utility 规则复现），不靠猜测改动。

**不要一起改掉的相邻实现：**`candidate_selection_shadow.py` 的市场择优使用的是 Utility
`decision_rank`，回答的是另一个问题（经济效用排序，而非市场偏好 + 历史分数），不构成重复。
判断两个实现是否同一能力必须读函数体，不能按名字或“都叫 dedupe”合并。

**复验：**`uv run pytest tests/test_universe_twin_persistence.py tests/test_universe_twin_experiment.py
tests/test_universe_relative_rank_shadow.py tests/test_candidate_selection_utility.py
tests/specs/spec_market_type_dedup.py -q`（本次 76 passed）。
若重放与权威分配结果出现 mismatch，先比较上述两个函数的语义，而不是重新引入本地副本。

## 1b. 已知受阻的结构切片（不要重复尝试）

`UniverseService.refresh()` 的候选成员组装循环（约 `service.py:975-1080`：构造
`UniverseMember` 与 `inserted_members`）本身是纯组装，看起来适合独立成 owner，但
**当前被源码文本断言阻塞**：

- `tests/specs/spec_universe_pipeline.py:590-593` 用
  `inspect.getsource(service.UniverseService.refresh)` 断言三段字面量
  （`regime_tag = current_regime.state if current_regime else None`、`regime_tag=regime_tag`、
  `"regime_tag": regime_tag`），这三段都在该循环内。
- `tests/specs/spec_entry_plan_contract.py:21-24` 同样按 `refresh()` 源码断言
  `_ensure_planning_generations` 的唯一 owner。

因此把该循环移出 `refresh()` 会先弄红 spec，而不是改行为。解除方式属于
[#3561](https://github.com/iridyne/aletheia/issues/3561)（把源码文本断言改成行为断言），
Owner 已将其降为低优先级。**在该项完成前，不要再把“缩短 refresh()”当作零风险重构**；
也不要在同一批次里既改行为又改断言。

同样地，`test_universe_routes.py` 通过 patch `UniverseService.refresh` 打桩，
移动 `refresh` 的公开入口或改名会破坏该 seam。

## 1c. Portfolio：同一问题两处实现且排序已经分歧

**结论：已收敛为一个 owner（seam reader），修复的是真实分歧而非命名重复。**

- `agent_shell/reflection.py` 原用 `ORDER BY opened_at DESC`（原始文本比较）。
- `analytics/sizing_trace.py` 原用 `ORDER BY datetime(opened_at) DESC`（标准化比较）。

问题相同（“该 proposal 的最新 Position”）、谓词相同，但排序表达式不同：当历史行里
`opened_at` 格式不一致时，两者可能选中不同的行，于是同一 proposal 的 outcome 注记与
sizing trace 会互相矛盾。统一为 `repositories_positions.py::get_latest_position_for_proposal`
（`__all__` 已登记，facade 才真正重导出）。sizing_trace 的 `_table_exists` 守卫保留，
因为那是该诊断“缺 schema 时降级”的自身契约，不是重复实现。

**没有一起改掉的相邻实现：**`analytics/opportunity_flow.py::_opportunity_by_id`
与 `get_opportunity` 的 SQL 相同，但它对整个模块统一采用 `_table_exists` 降级
（`trace_opportunity_flow` 返回 `_missing_trace` 而不是抛错）。收敛它要么引入一个带守卫的
seam 变体，要么改变缺 schema 行为，因此本轮保持不动并记录理由。

**可复用教训（代价很小但容易踩）：**seam 模块有显式 `__all__`，facade 用
`from <seam> import *` 重导出。**新增 seam 函数必须同时登记 `__all__`**，否则
`from aletheia.storage.repositories import <name>` 直接 ImportError；而调用方若有宽泛
`except Exception`，这个 ImportError 会被吞成“不可用”文案，静默降级而不是报错。
本次由 sizing_trace 的测试立即暴露，才没有漏进 reflection 的语义。验证方式是真实 import，
不是读文件。

**复验：**`uv run pytest tests/test_sizing_trace_audit.py tests/test_repositories_positions.py
tests/test_experience_loop.py tests/test_opportunity_flow_ledger.py -q`（本次 47 passed）。

## 1d. Operator：同一“心跳是否过期”判定三处实现，其中一处分支永不可达

**结论：这是真实行为缺陷，不只是重复代码。** 三个读面用同一行 + 同一阈值各自判断：

| 位置 | 原行为 |
|---|---|
| `agent_shell/commands.py::_format_operator_snapshot` | 内联比较 age/threshold，只改 `status` |
| `server/routes/operator.py::_apply_stale_status` | 再次比较，另加 `stale`/`stale_after_sec`/`heartbeat_age_sec` |
| `agent_shell/operator_recommendations.py::build_recommendations:1619` | 读 `heartbeat.get("stale")` —— **没有任何生产者写过这个键**，分支永不可达 |

即：一个 `status="online"` 但 `last_seen_at` 已超过阈值的心跳，会被 CLI/API 报成 `stale`，
却不会产生 `operator-stale` 建议，Command Center/压缩/聊天里看不到任何 Operator 问题。

**修复与验证：**唯一 owner 落在心跳行自己的模块
`agent_shell/operator_store.py::apply_heartbeat_staleness`，三个读面统一读取。同一份种子数据
（online 心跳、超阈值 30 秒）的实际前后对比：

| | 状态读面 | 建议 id |
|---|---|---|
| 修复前 | `stale` | `dreaming-due`, `system-health-report-refresh` |
| 修复后 | `stale` | `dreaming-due`, **`operator-stale`**, `system-health-report-refresh` |

同时补了两条永久回归（过期→一致；新鲜→不产生该建议）。169 项 operator/heartbeat 测试通过。

**方法教训（两条，都很贵）：**

1. 把生产改动 stash 后跑新测试只会得到 ImportError，那只证明新符号不存在，
   **不能作为“修复前失败”的证据**。有效替代：`git checkout <commit>^ -- <file>` 只回退被测
   调用点，保留 owner，然后断言真实行为。本次用这个方法确认回归守卫在“只回退
   `build_recommendations` 调用点”时以 `operator-stale` 缺失而失败（而不是因 import 失败）。
   回归测试应尽量只用既有 API，避免把“符号存在”当成通过条件。
2. `git stash push <已提交文件>` 不会 stash 任何东西，`pop` 会直接报 “No stash entries found”，
   而在此之前的那次测试跑的是**修复后的代码**——很容易被误读成“已证明修复前失败”。
   回退已提交文件请用 `git checkout <rev> -- <path>`（读完记得 `git checkout` 回 Restore）。

**共享判定的副作用：**该 verdict 现在被三个读面共用，因此必须**先复制再标注**；
否则 `get_latest_heartbeat()` 返回的行会被就地修改，一个调用者的结论会泄漏给下一个。

**相邻但未改：**`analytics/opportunity_flow.py::_opportunity_by_id` 与 `get_opportunity`
的 SQL 相同，但该模块约 15 个读取点统一用 `_table_exists` 降级、并在缺 schema 时返回
`_missing_trace`（CLI payload 里的 `schema_errors: []` 就是为此存在）。
`get_opportunity` → `_select_one` 没有守卫，会抛 `sqlite3.OperationalError`。
只换其中一处还会让模块一半用 `db`、一半用 `get_db()`，因此记录为**不改**。

## 1e. Portfolio：成本完整 PnL 算术收敛，以及“等价性证明”本身怎么作假

`outcome_integrity.assess_position_closure` 的 economics 段与 `pnl.calculated_realized_pnl` /
`_realized_economics_value` 曾各自实现同一段算术（方向相关 gross − entry/exit fee − 已验证 funding，
容差 `max(1e-8, |expected|*1e-8, entry*quantity*1e-10)`）。收敛为共享 owner
`outcome_integrity.cost_complete_expected_pnl` + `reconciles_persisted_pnl`；closure 评估保留自己的门
（持久值必须存在、`market_type in {spot, usdt_futures}`、eligibility 与 reason 映射）。

**要保留的差异（不是重复）：**`exit_fill_verified=false` 的 legacy close 在 closure 上应判
`unverified_exit_fill`（不合格），而数值本身仍可对账——eligibility 与算术是两个问题。
`position_path_observations._stop_loss_decomposition` 也不是同一问题（止损归因分解，另有
`price_verified` 门）。

**方法论教训（最贵的一条）：**第一版等价性矩阵**是空的**——所有行都没带
`close_category`/`outcome_eligible`/`verified fill`，在 `unknown_close_category` 处提前返回，
根本没执行被改的那段代码；"旧新一致"因此毫无意义。修正版必须**自证覆盖**：
`assess_position_closure` 至少要有一个变体达到 `eligible=True` 且 `net_return_pct` 非空，
并且分别命中 `incomplete_realized_economics` 与 `incomplete_funding_economics`，
否则 harness 直接断言失败。**以后任何"提取前后等价"的证明都要带这种覆盖断言。**

30 个变体的差分结果：**29 个逐字节一致，恰好 1 个有意不同** —— `direction=" LONG "`：
旧的 `pnl` 用 `.lower()`（不 strip）→ 返回 `None`；而 closure 一直用 `_normalize`（strip+lower），
早已判 `eligible=True`。即两处此前**互相矛盾**，收敛后一致。当前写入方不会产生带空格的
`direction`，因此无生产行受影响。

**下一批候选（已由只读审计给出证据，未改代码）：**`execution/pending_close_recovery.py`
（5 处）、`execution/reconciliation.py`（1 处两连读）、`execution/stale_order_cleanup.py`、
`risk/engine.py` 在 `repo.transaction(immediate=True)` 内手写
`SELECT * FROM orders|positions WHERE id=?`，而 seam 已提供 `get_order`/`get_position`（同一连接、
同一 `_select_one`）。属于"同一问题两处实现"，但位于平仓/恢复关键路径，必须单独批次、单测覆盖后再动。

## 1g. Runtime：portfolio snapshot 新鲜度判定的共享与"不该合并"的部分

`agent_runtime/scheduler.py` 导出的 `PORTFOLIO_SNAPSHOT_STALE_AFTER_SECONDS` 注释就写明"两个消费者应引用
同一真相"；`risk/engine.py` 用它作为 sizing 的前置（`0 <= age <= bound` 才信任），而
`agent_shell/operator_recommendations.py` 曾**自己硬编码 15 分钟**并用不同边界操作符比较。差异在边界
（恰好 900 秒）以及"时间戳不可信"时两个消费者的相反结论上体现。PR #3633 只收敛**边界与信任谓词**。

**明确不当作"同一问题的两种答案"：**naive 时间戳的处理是**信任策略**差异，不是重复实现：
`utils/dt.parse_utc` 会把 naive 当作 UTC（`dt.py:14-15`），而 sizing 守卫**直接拒绝** naive
（`risk/engine.py`：`if latest_at.tzinfo is None: return 1.0, False`）。本次让建议侧**跟从严的那一侧**
（无法解析／naive／模糊都视为"不新鲜"），把这 4 个用例的行为变化在 PR 中列出并配测试，而不是沿用
`parse_utc` 的假设把模糊行报成新鲜。

**未来时间戳的处理选择：**会触发同一条建议，但 `stale_minutes: null` + 中性措辞——负的"分钟数"不是
真实的面向操作员的年龄；行身份仍在 `last_snapshot_id` 中可见。若日后需要区分"时钟偏移"，应新增显式字段，
而不是复用 `stale_minutes`。

**守卫强度：**边界回归用"同一年龄在 bound=1 时触发、在 bound=900 时不触发"的翻转断言；变异检查确认把共享
常量换回私有字面量 `900` 会让该测试失败，因此它证明的是"常量是唯一来源"，而不是"恰好 15 分钟也能过"。

## 1h. Fedora 分层：tier 标记是精确划分（安全结论），耗时才是成本问题

**收集口径（本地 macOS，`eca9e4e2d` 之后的 master + 两个待合并分支树）：**

| marker | collected |
|---|---|
| `tier_unit` | 1953 |
| `tier_integration` | 8248 |
| `tier_spec` | 1327 |
| `tier_e2e` | 107 |
| 四个 tier 并集 | **11635** |
| `tests/` 全量 | **11635** |
| **未分类 remainder** | **0** |

结论：四个 tier 标记**精确划分**整个套件，因此把 Fedora 步骤表达为 tier 子集**不会漏跑**；
"本地未覆盖层"= 8248+1327+107 = **9682 项**（`tier_unit` 1953 项为本地层）。

**`domain_runtime` 不是分区成员**：它是域标记（`ci_plan.py` 的 runtime lane，1680 项），与 tier 重叠。
任何"并集等于全量"的检查都必须只用四个 tier 标记，否则会把重叠误当覆盖。
（本次更正来自一次外部提醒：先前的脚本把 `domain_runtime` 混进并集。）

**仍未测量**：各 marker 在 Fedora 沙箱内的**墙钟**。这决定"本地未覆盖层"能省多少时间，
属于成本数据，不是安全边界——安全性已由上面的精确划分给出。测量脚本
（四个 tier + domain_runtime 分别计时，并把无 tier 标记的 case 显式列为 remainder）在候选目录内串行运行，
避免与全量争用 CPU 污染读数。

**流程未变之前**：默认 Fedora 步骤仍是整个 `tests/`；只在拿到耗时表之后才收窄，并需用"同一次真实全量"
回归对照证明没有漏跑。

## 1i. 逐域结论表（2026-09-28 批次收敛情况）

本表记录本周期内**每个已核对领域**的状态：已收敛 / 无需改造（附理由）/ 仅缺外部证据。
它不表示全仓审计结束，也不把"未审查"当作"无需改造"。

| 领域 | 结论 | 依据（本文件 / PR） |
|---|---|---|
| Candidate 发布 → Planning | **无需改造**：单一写者、原子发布、证据分离已就位 | §1（9 项回滚用例实测） |
| Universe replay 去重 | **已收敛**：删除逐行克隆与零调用重放，仅剩一个实现 | §1a（#3614） |
| Position 故事读取边界 | **已收敛**：readers/events/service/render 四责分离（上一批 #3604） | #3604 |
| Portfolio / 结算算术 | **已收敛**：cost-complete PnL 算术单一 owner；已知经济与资格判定明确不合并 | §1e（#3623） |
| Portfolio 读取（同 proposal 最新持仓） | **已收敛**：排序分歧（文本 vs `datetime()`）合并为 seam reader | #3615 |
| Storage 单行 id 查询 | **已收敛**：8 处事务内手写查询改走 `get_order`/`get_position` | #3630 |
| Runtime 心跳新鲜度 | **已收敛**：判定 owner 唯一，三个读面共用；进程存活 ≠ 自身成功 | §1d（#3616） |
| Runtime snapshot 新鲜度 | **已收敛（边界+信任谓词）**；naive/未来时间戳策略差异显式记录 | §1g（#3633） |
| Analytics 持有期时长 | **已收敛**：三处实现合一；反向区间由负值改为不可用 | #3635（§本文件 1j 待补表） |
| Analytics/API 漏斗与历史计数 | **无需改造**：`assets_passed` 等为历史口径，当前准入读 `selection_reason_counts` | §2 |
| Core/Extensions 可选边界 | **已收敛（一处孤儿）**：删除零调用的 leverage-policy epoch；未发现"core 硬依赖可选扩展" | #3636 |
| diagnostics 只读 | **无需改造**：`_table_exists` 降级是该模块的声明契约，不是重复 | §1d 相邻说明 |
| Operator shell 心跳生产端 | **仅缺证据**：`operator-shell/src/heartbeat.ts` 无覆盖测试（codegraph 标记），是 #3602 类故障的源头侧 | 待排批次 |
| 结算自然样本 / 人工证据 | **受外部阻塞**：#3135 需自然平仓样本；KORU/UNI/DOT 人工确认不由 Agent 代做 | §5 |
| R20 站点 | **受外部阻塞**：站点不可达；源码审计已完成 | COMPETITIVE_ANALYSIS_R20_2026-09-24 |

**仍未审查**：`execution/gateway._execute_proposal`（516 行）、`risk/engine._evaluate_proposal_locked`（595 行）、
`agent_shell/commands.review_portfolio_state`（638 行）等巨型入口——它们在本周期内只做了**局部**收敛，
未被整体判为"无需改造"。

## 2. 旧扫描计数与历史漏斗不能代表当前准入

**结论：`assets_passed=0` 不能读作零准入；历史 Opportunity 漏斗也不是当前 Entry 主线。**

- [Candidate Selection Utility Contract](../../design/contracts/candidate_selection_utility_contract.md)
  明确保留 `filtered` / `assets_passed` 的历史过滤含义，不把它们作为当前 admission count。
- 2026-09-28T01:36–01:47Z 的完成扫描中，原始 `assets_passed` 为 0，
  同行 `candidate_path.selection_reason_counts` 却包含 `admitted: 7`。
  不以这个旧列的零值发起删除历史数据、放宽筛选或新增准入逻辑。
- 同期 `opportunity audit --view overview` 的 funnel 声明
  `scope=historical_signal_pipeline`；其 `opportunities_created=0` 不能证明
  Candidate → Planning → Execution 停摆。
- 当前口径应读取 `aletheia inspect entry-status --json`、实际 Generation/Plan，
  以及扫描 `metrics.candidate_path`；仍需区分 admitted、selected、Planning 与执行。

**不改决定：**本轮不删历史数据库列，不恢复 Ranker/Market Observer 来填旧漏斗。
若日后证明某个当前 API/UI 消费者错误使用旧列，应修正那个消费者，而不是改历史含义。

## 3. 运行快照：主链有新产出，但持仓路径证据存在缺口

2026-09-28T01:41–01:57Z 的只读核对记录：

- Gateway 运行，running/disk/recorded SHA 为上述 Fedora 基线；无 kill switch 或 pause blocker。
- 当前 Entry 报告没有待规划 attention；存在新的 Planning 结果。
- `7b1a0ec12e5d`（LTC/USDT、Bitget Demo）在 2026-09-28T01:09:25.146Z 开仓，
  证明该部署之后仍有实际 Candidate/Entry/执行产出，不能称整条链已停止。
- 新的编译拒绝包括 `stop_distance_below_vol_target` 和
  `INVALIDATION_ANCHOR_WRONG_SIDE`。观察到拒绝不等于证明阈值错误，不能为了成交而放松它们。
- 同一 LTC Position 的 `position_path_observations` 查询当时为 0 行。
  observer 控制读模型另显示 27 个 deferred buckets、历史 coverage 不完整。
  这是已观察到的证据缺口，**根因及严重程度尚未证明**；历史相关调查见
  [#984](https://github.com/iridyne/aletheia/issues/984)。不据此强制 backfill 或改写历史。

复核使用项目只读 CLI，先发现 schema，再做精确身份查询。对 `entry-status --json`
应先投影 summary 与必要字段；完整 Plan/历史输入 JSON 很大，重复全文输出没有调查收益。

## 4. Operator 心跳：必须区分进程存活与自身成功

**已验证根因：**Node 原生 `fetch` 带单独的 `Sec-Fetch-Mode: cors`，旧 HTTP guard
将其误判为不可信浏览器来源，返回 403。监督器不断刷新 `last_seen_at`，并不意味着
Operator 自身 `last_success_at` 正常。见 [#3602](https://github.com/iridyne/aletheia/issues/3602)
及 [PR #3603](https://github.com/iridyne/aletheia/pull/3603)。

修复保留 hostile Origin/Referer/跨站 metadata 拒绝、服务认证与路由白名单。
真实 Node → ASGI → 隔离 SQLite 验证得到：心跳 200 并落库、恶意 Origin 403、
服务凭据访问 owner 路由 401、无身份心跳 401。没有伪造 same-origin 或扩大权限。

Fedora 基线上的冻结观察窗为 **2026-09-27T21:57:31.167841Z 至 22:27:36.264718Z**：

| 证据 | 观察结果 |
|---|---|
| 自身心跳与运行身份 | 121 次采样均通过，121 个不同成功时间戳，最大年龄 14.472 秒（检查阈值 45 秒） |
| `universe_refresh` | 窗口内 3 次 succeeded，0 次 failed |
| `reconciliation` | 窗口内 15 次 succeeded，0 次 failed |
| `price_feed` | 窗口内 60 次 succeeded，0 次 failed |

日志按已保存起始 offset 和 Asia/Shanghai 时间戳限定到该窗口，不能把窗口后的成功混入。
辅助原始收据位于 Fedora `/var/tmp/aletheia-a8dc06139-window/job-receipt.json`。
该结果不是新的源码版本、实时运行状态或经济资格证明。

## 5. 结算：多个步骤不等于多套最终写者

本次独立审计及主 Agent 复核确认：`portfolio/pnl.py::_commit_close_economics` 是
初始关闭和延迟成本补齐共享的完整关闭经济写入边界；调用者保留各自的证据校验和事务。
已知经济分量的只读展示，不等于最终 `realized_pnl` 或 Outcome eligibility。

`portfolio/account_loss.py::_partial_close_realized_today` 确实消费 partial-close ledger：
部分减仓时 Position 仍开放，不能删除全部 close events 并只读最终 Position PnL。
未知 funding/fee 不得补零，已有最终金额不能覆盖。见
[Execution Contract §11.4/§11.6](../../design/contracts/execution_contract.md) 和
[#3135](https://github.com/iridyne/aletheia/issues/3135)。

主 Agent 运行 closure、funding economics、read model、writer ownership、Outcome feedback
共 **306 项通过**。这支持现有代码边界，不代替自然平仓的实机经济验收。
本次部署后查询尚无新关闭样本；人工成交/拒单/平仓证据仍需由人类提供，不能代为结案。

## 6. 测试诊断：先区分 fixture、资源观测与生产根因

### 并发 SQLite fixture

[#3607](https://github.com/iridyne/aletheia/issues/3607) 的原全量失败日志不是单纯提交迟到：
watcher 的 `_capture_facts` 读到 `row=None` 后失败。测试让多个 offload 线程共享一个
`check_same_thread=False` 内存 connection，生产则使用每线程文件连接。
本次受控实验在共享连接下观察到错误/缺失行及 InterfaceError，而每线程文件连接对照没有这些错误。
这不应被泛化为“SQLite 都不线程安全”，更不构成给生产加 missing-row fallback 的理由。

[PR #3609](https://github.com/iridyne/aletheia/pull/3609) 使用已有 `real_db` 与
`api_only_runtime` fixture；文件库暴露缺失外键种子后补齐真实 registry asset，未关闭外键。
六种 cold/restored/reconnect × tactic 情况保留原断言和超时；24 项 activation、
93 项 activation/fast-trigger 组合通过。最终集成完整证明另计。

### Connection ResourceWarning

[#3598](https://github.com/iridyne/aletheia/issues/3598) 的 44 条警告通过
`-X tracemalloc=3` 定位到 evidence/replay 测试的分配点，不是警告出现的 GC 位置。
在分配后注册 pytest finalizer 后，同一 53 项测试在 ResourceWarning/unraisable warning
视为错误时通过；原 435 项 coverage 集合通过、11 个 floor 未改。没有生产代码修复或警告屏蔽。

### 子进程导入超时

[#3610](https://github.com/iridyne/aletheia/issues/3610) 记录了隔离全量中的四个 20 秒导入超时。
对相同 child scripts 加诊断 watchdog、保留原断言和 20 秒期限后，25 个模块用例通过，
13 个 child probe 用时 0.141–1.512 秒，没有 watchdog 堆栈。
**这没有证明原失败是 flaky，也没有证明资源压力就是根因。** 不据此改生产或放宽期限；
若复现，应捕获当时子进程堆栈和资源证据，而不是只重复跑到绿。

## 7. 门禁和基准的可复用限制

- [#3561](https://github.com/iridyne/aletheia/issues/3561) 的旧“十个新文件”要求已由 Owner
  同意按现行契约收敛。storage-only SQL 禁令、退役 simulator 扫描、不存在的 reason 枚举要求
  不适用；不要为满足旧计数复活它们。有效门禁范围见
  [Audit Gates Registry](../../governance/audit_gates_registry.md)。
- 已复现的三个静态检查陷阱：合法的 `write_audit(event={...})` 也要检查；写者不止
  `insert_/update_/transition_` 前缀；跨模块同名函数不能靠字典最后一次覆盖决定 owner。
  已有 authority checker 是按表的 writer 校验，不是通用 public-mutator 分类器。
- 静态检查只证明声明的识别范围，不代替实际回滚、只读、失败传播等行为测试。
  不为每个新调用形状建设通用调用图框架。
- canonical pytest classifier 决定 tier；手工 `tier_unit` 标签不能绕过准入。
  `tests/audit/` 仍由 changed-file audit 步骤执行，不能用标记争议删除检查。
- 重分类与同集合加速必须分开报告；测试/生产代码比例、门禁文件数量不证明工程质量或盈利。


批次收尾快照（2026-09-28 本地，集成候选 `ca2d506c2` = master 合并 #3608/#3609/#3611/#3614/#3615/#3616）：

- 本地 `tier_unit` 三次串行：各 **1950 passed / 1 skipped**，墙钟 **56.37 / 55.53 / 56.35 秒**（≤60 秒达标）。
- `tests/test_data_retention.py` 在本机同时于 `origin/master` 与集成候选上报 **同样 12 项失败**
  （含 `ExperimentStateReadError: Observation reader unavailable`）；这是 master 既有红灯，
  不是本批引入，仍需由隔离 Fedora 全量判定是否环境专属后再定性。
- 隔离 Fedora 全量以该候选的独立沙箱为准；**在拿到退出码前不得声称全绿**。

冻结证明示例（不能改标为较新源码的结果）：

| 源码 | 实际证明与限制 |
|---|---|
| `22bfaab0a`、`475cef7ed` | 各自隔离 Fedora 完整测试 11,623 项、0 failure/error、20 skip/xfail。 |
| `5426e5222` | 完整测试 11,623 项有 1 个 #3607 失败，不能称全绿。 |
| `58190c3f3` | 完整测试 11,625 项有 5 个失败：手工 tier 覆盖及四个导入超时；不能只引用较早的绿结果。 |
| `6525cee5a` | 三次串行 unit 各 1947 passed、1 skipped；墙钟 55.87 / 54.42 / 53.76 秒。不是完整套件加速比例，也不覆盖后加的回归用例。 |

## 1j. Fedora 分层墙钟与"是否收窄默认 Fedora 步骤"的决定

测量：在隔离沙箱内对 `tests/` 的四个分区 marker 分别取全量墙钟（candidate `85ef7a86a`，
与批次 3 收据同一棵树），Fedora、串行。

| marker | 墙钟 | passed | skipped | deselected |
|---|---:|---:|---:|---:|
| `tier_unit` | 92.5 s | 1952 | 1 | 9682 |
| `tier_integration` | 495.5 s | 8240 | 8 | 3387 |
| `tier_spec` | 74.6 s | 1316 | 6 | 10308 |
| `tier_e2e` | 30.3 s | 107 | 0 | 11528 |
| 四分区合计 | 692.9 s | **11615** | **15** | — |
| 参考：同树单次全量（批次 3 收据） | 646.1 s | 11615 | 15 | 0 |

两点结论：

1. **分区收据被实测确认**：四分区 passed 之和 `1952+8240+1316+107 = 11615` 等于同树全量
   的 passed 数，分区没有漏跑一个用例（此前的结论只由收集数支持）。
2. **拆开跑比整跑贵 7%**（692.9 s vs 646.1 s）：每个 lane 的启动/收集开销重复付出。
3. `domain_runtime`（118.6 s，1675 passed）是**重叠域标记**，不是第五个分区；它不得并入
   分区检查。

本地对照（macOS，同集合）：`tier_unit` 50 s、`tier_spec` 51.3 s —— 本地约为 Fedora 的
0.55–0.69 倍。

**决定：默认 Fedora 步骤保持整份 `tests/`，不改为"本地未覆盖层集合"。** 依据同上三点：
本地已经默认跑的那一层只占 Fedora 墙钟的 13.4%（92.5 s / 692.9 s），排掉它只能省
约 1.5 分钟一次的后台运行，却把"没有漏跑"的证明从**一份制品**变成"本地收据 + Fedora
收据"两份、且分母换成另一个操作系统。真正的成本在 `tier_integration`：495.5 s，占
Fedora 全量的 71%，它也同时是本地最贵的一层。要提速就压这一层，而不是压门禁覆盖。

运行快照的两个真实缺陷（均在本次会话中修复，见 PR 与运行时证据）：

| 表面 | 症状 | 根因 | 修复与验证 |
|---|---|---|---|
| TS operator shell | `run_market_scan` 每分钟失败 1 次，累计 531 次，最新 2026-09-28 05:34:58Z | Market Observer 退役（#2933）只收敛了 Python 侧；shell 仍在按永久为空的 observer 活跃度发 `market_scan_refresh_due` 并执行后端已不存在的命令 | 删除 shell 中的退役机制表面（[#3641](https://github.com/iridyne/aletheia/pull/3641)）；修复后用同一运行时对照：修复前的重启（05:20:55Z）后 14 分钟内仍有 14 次失败/14 个 high 事件，带修复的重启（05:36:09Z）后为 0 |
| Observation Worker（systemd oneshot + timer） | 每 6 小时一次 pass 全部 `status=2`，fail-closed 拒绝运行 | 兄弟 checkout `/home/admin/aletheia-observer` 停在 2026-09-12、落后 master **848** 个提交，共享配置的新键（`universe.allowlist`、`models.fast`）与新语义（`max_daily_loss_pct` 允许 0）被旧 schema 拒绝 | 更新该 checkout 到 master 并 `uv sync`，一次 pass `status=0`，`aletheia observe overview --json` 报 `last_service_pass.status=success` |

后者的教训值得留档：worker 漂移只有 `aletheia observe drift` 这条**建议性**通道，没有进
operator 建议或部署验证，于是"每 6 小时失败一次"连续存在了数天而无人处理。是否把漂移接进
建议面或部署验证属产品决策（D1），本轮只修复运行态并记录事实。

## 1l. 本地 `tier_integration` 墙钟：本地并不总是更快

同一 marker、同一天的两侧数据：

| 环境 | 墙钟 | passed | skipped |
|---|---:|---:|---:|
| 本机 macOS（串行，`--timeout=300`） | **1233.8 s（20:33）** | 8243 | 8 |
| Fedora 隔离沙箱（同一 marker） | **495.5 s（8:15）** | 8240 | 8 |

即：`tier_unit` / `tier_spec` 上本地约为 Fedora 的 0.55–0.69 倍，但 `tier_integration`
反过来——**本地比 Fedora 慢 2.5 倍**。"本地一定更快"不是可依赖的假设。

本机这 20 分钟里有 **900 s（73%）来自 5 个测试**，每个恰好约 180.0 s：

```
180.15s call tests/test_serenity_workbench.py::test_research_worker_success[asyncio]
180.05s call tests/test_serenity_workbench.py::test_pipeline_full_stages[asyncio]
180.04s call tests/test_serenity_workbench.py::test_research_worker_generic_failure[asyncio]
180.04s call tests/test_serenity_workbench.py::test_research_worker_fails_run_on_required_theme_persistence_error[asyncio]
180.04s call tests/test_serenity_workbench.py::test_research_worker_rate_limit[asyncio]
```

对照事实（同样本机、同样代码）：

- 只跑该文件：**18.1 s 全绿**，这 5 个测试分别 2.5–5.3 s。
- 只跑其中一个（`test_research_worker_rate_limit`）：**19.4 s**。

所以 180 s 只在**整个 marker 一起跑**时出现 —— 这是**跨文件干扰**，不是单测本身的成本。

**INFERENCE（未被证明，需要一次二分才能定名）**：180 s 正好等于 LLM 结构化调用共享的总墙钟预算
（见 agent 契约里 `llm_structured` 的 180 s 总预算）。`fast_tier` fixture 只替换了
`model_provider.router.get_provider_for_tier`；若前面某个测试留下可解析的 provider/配置缓存，
这些用例就会走**真实**推理尝试：Fedora 沙箱的网络命名空间让连接立即失败
（probe 实测 `external_tcp_error: 101`），而本机经由代理的连接是**黑洞**（既不拒绝也不响应），
于是每个用例都耗满 180 s 预算后才降级——用例仍然"通过"，因为它断言的是降级路径。

要定名必须做二分：按 `--durations` 给出的顺序，把 `tests/` 里排在 `test_serenity_workbench.py`
之前的文件分组，逐步缩小到泄漏缓存的那个文件。本轮不猜、不改生产代码，只记录事实与复现方式：

```sh
uv run pytest tests -m tier_integration -q --timeout=300        # 本地：20+ 分钟，5 个 180s
uv run pytest tests/test_serenity_workbench.py -q               # 本地：18 秒
```

## 1k. 运行态修复（续）与尚未决的产品问题

### 已修复，附运行时证据

| 表面 | 症状 | 根因 | 修复与验证 |
|---|---|---|---|
| TS operator shell 专家委派 | `run_portfolio_snapshot` 失败 44 次、`run_risk_critique` 8 次，错误是 `Cannot read properties of undefined (reading '0')`；`run_dreaming` 20 次 409 | 后端 `POST /api/agent-runs/start` 的契约是 **202 + `job_id`（排队）**（`agent_contracts.md`、`api_reference.md`），shell 却按"同步返回已完成 run"读 `result.errors[0]` | 按排队契约收敛（[#3647](https://github.com/iridyne/aletheia/pull/3647)）：入队即委派结果，任务以 `run_status: queued` 完成；`409 agent_already_queued` 视为同一结果。对照证据：被判失败的同一秒，`agent_runs.f2f9ba24bf9e`（portfolio_agent）实际 `completed`；失败任务让建议持续到期，于是每分钟重复委派一次 |
| `post_trade_reviewer` 复盘 | 最近 3 天 35 次运行里 32 次 `completed_with_errors`，全部 `llm_unavailable: empty_response`；建议面报"应改 prompt/playbook" | 路由到的是思考模型（qwen35），其响应可能只有 reasoning 块；失败调用**恰好用满 600 token 预算**（min=max=600），而成功调用平均 251/max 295 token。同类复盘调用 `agent_shell.portfolio_review` 用 2048 token 一直正常 | 预算 600 → 2048（[#3648](https://github.com/iridyne/aletheia/pull/3648)）；同时让 `llm_complete` 的空响应分支记录 `finish_reason`/`output_tokens`/`output_limit`（结构化路径早已记录，信封也早已允许），否则"供应商宕机"和"预算耗尽"在记录里无法区分 |

### 需要 Owner 决定（本轮只记录，不擅自改产品行为）

1. **空响应是否应该触发模型回退**：`complete_with_fallback` 只对传输类瞬时错误回退；空内容是终态。上述复盘因此静默丢证据。是否把"空/截断响应"纳入回退，属契约变更（D1）。
2. **`failed_tasks_present` 的时效**：建议只取最新 10 条 failed 任务，但这些任务不会自动消解（本轮修复前的历史失败仍在），于是 high 级建议会长期挂着。要不要给失败任务一个显式确认/收敛路径，或在建议里按 error 签名聚合，属产品决策。
3. **对已不存在命令的行为变更建议**：`command_center_behavior_change_proposal_due` 由历史命令拒绝推导（如 `run_market_scan` 被拒 155 次）。命令已退役，建议仍在。是否要求候选命令必须存在于注册表，属产品决策。
4. **Observation Worker 漂移可见性**：worker 落后 master **848** 个提交、每 6 小时 fail-closed 拒绝运行数天，只有建议性的 `aletheia observe drift` 能发现。是否把漂移接入 operator 建议或部署验证，属产品决策。
5. **能力清单仍有已删除机制的引用**：`CAPABILITY_INVENTORY.md` 引用的路径里有 12 个无法解析（含 DC-01 的 `aletheia/universe/signal_emitter.py`、`extensions/research/market_observer.py`——两者都已在 #2945/#2933 删除）。DC-01 的"确定性 Opportunity 准入"是否仍是产品承诺，必须由 Owner 裁量；本轮不改写清单结论。

### 已核实为"无需处理"的近似项

- `agent_shell/reflection.py` 里 `signal_source` 默认 `market_observer`、`storage`/`server` 里 `COALESCE(p.signal_source, 'market_observer')`：这是**历史持久化行**的读取默认值，不是活机制的生产者；数据层保留历史是允许的。
- `diagnostics/probes/trading.py` 里的 `market_observer` 只出现在解释"该路径已退役、机会表按设计不再前进"的注释与既有 SQL 默认值中。
- 旧 Orca worktree（`.orca-worktrees/aletheia/{data_fetch,rival_analysis}`）里仍有 `market_scan_refresh_due` 的旧副本；它们不是 master，属本地工作副本，不参与交付。

## 1m. `spec`/`e2e` 层：分类已做，但没有廉价的提速空间

对 `tests/specs/` 的 105 个文件复用 `tier_unit` 的分类方法（只看模块自身的顶层 import 与
repo 扫描调用）：

| 判定 | 文件数 | 依据 |
|---|---:|---|
| 跨边界（storage / server / httpx / sqlite / subprocess / extensions） | 84 | 自身 import 越界 |
| 仓库扫描型（`inspect.getsource`、`rglob`、`read_text` 等） | 12 | 干净的 import，成本来自全仓扫描 |
| 两者都不是 | 9 | |

墙钟（本地 macOS）：`tier_spec` 51.3 s / 1316 passed + 6 skipped + 5 xfailed；Fedora 74.6 s。
`tier_e2e` 在 Fedora 只有 30.3 s / 107 tests。

**结论：不要把 `tier_unit` 的"重分类"配方原样套到 specs 上。** 对 `tier_unit` 而言
`RECLASSIFY` 意味着"这条用例本来就不该占着快速层"；但 specs 是**可执行的契约载体**，
把其中 84 个判定为"跨边界"的用例移出闸门，等于删除契约证据——那是降低验证强度，不是提速。
唯一正当的候选是那 12 个仓库扫描型文件（可考虑跨用例缓存 AST/文件读取），本轮不改。

至于局部热点：本地 `tier_spec` 的 51 s 里有约 19 s 来自同一个 parametrize 的 5 个用例
（`spec_universe_pipeline.py::test_target_publication_failure_preserves_previous_authority`，
每个约 3.8 s）。实测 `_setup_db` 冷启动 0.49 s、热 0.00 s，因此这 19 s 花在**真实的
`UniverseService.refresh()` 流水线**上，而不是测试脚手架；它是"发布失败不得改写既有权威"
的原子性证明，削它就是在削弱证据。故不改。

于是 spec/e2e 这一层的结论是：**没有既合法又显著的收益**；墙钟的大头在
`tier_integration`（Fedora 495.5 s、本地 1233.8 s，见 §1l）。

## 8. 如何复用，而不是从头再查

1. 先读对应现行 Contract，再确认相关源码 owner 是否变化；未变化的边界复用本记录，
   不重新扫全仓。源码或假设变化时，仅重验受影响路径。
2. 保留“没有证明重复 / 不改”的结果。不要为了继续去屎山化，把必要的事务步骤当成重复主线。
3. 对运行结论重新取当前精确身份和窗口；不把本文件的日期化样本当成今天的状态。
4. 新证据若推翻结论，引用原结论并记录新的 SHA、条件和反例；不要覆盖历史证据假装一直正确。
5. Issue/PR 负责动态任务状态；本目录负责长期有用的分析、否定结论、失败模式与复现入口。
   不存放聊天流水、完整 payload、重复 TODO 或未经复核的子 Agent 裁决。

其他已归档分析：
[前一日结构批次](DESHITIFY_BATCH_2026-09-27.md)、
[unit 分类与性能测量](tier_unit_reclassification_2026-09-27.md)、
[R20 对照分析](COMPETITIVE_ANALYSIS_R20_2026-09-24.md)。这些同样是各自时点的证据，
不是当前能力或竞品收益优越性的证明。
