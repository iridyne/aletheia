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
