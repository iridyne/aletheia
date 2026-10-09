# 断线清扫总账（2026-10-09）

> 冻结证据，不是工作队列。方法：机械扫描（332 处静默宽异常、184 个零引用顶层定义、表读写配对）播种 → 6 个子系统审计员逐条读代码判定 → 每组一名反方复核 → 汇总。全程只读，没有改代码、没有写 Fedora。修复进度和优先级在 GitHub 跟踪，不在本文。

## 先说几个词

- **主线**：一笔交易从头到尾要经过的环节。依次是：选币（Universe）→ 规划 → 入场计划（Entry Plan）→ 触发 → 提案（Proposal，交给风控的下单请求）→ 风控 → 执行 → 持仓 → 平仓 → 结果记录 → 学习。
- **五类断线**：
  - **P1 只单测、没接通**：各部分都有测试，但没有任何证据说明它们连在了一起。
  - **P2 有写没读，或有读没写**：一边在写，另一边没人读；或者一边在读，却没人写。
  - **P3 出错时装作正常**：出了错不报，返回一个看起来正常的值。
  - **P4 没跑过的路径**：这条路从来没被走过，而且里面有能指出来的具体缺陷。
  - **P5 没人用的功能**：做出来了，但主线上没有任何东西用它的输出。
- **五种处理方式**：
  - **DELETE 删掉**。
  - **FAIL_CLOSED 出错要说出来**：记日志，并显示"不可用"。
  - **WIRE_D0 接上**：合同已经要求这样，接上只是让代码符合现有约定，不改语义。
  - **NEEDS_OWNER 你来定**：处理它会改变产品语义或范围。
  - **KEEP 保留**。
- **合同**：`docs/design/contracts/` 下的设计约定，是"正确行为"的依据。

---

## 1. 结论

六个子系统一共核实了 **68 条**断线。其中 2 对是重复的（SP-11 和 MS-08 是同一件事；SP-04 和 MS-06 是同一件事），实际是 **66 件**。另外大约 300 个机器扫出来的候选项，逐个读过之后判为 KEEP。

**按类型**：
- P2 有写没读/有读没写：**34**，最普遍
- P5 没人用的功能：**25**
- P3 出错装正常：**6**（MP-09 复核后其实应算 P3，算上是 7）
- P4 没跑过的路径：**2**
- P1 只单测没接通：**1**

**按主线环节**：

| 环节 | 条数 |
|---|---|
| 选币 | 9 |
| 规划 | 5 |
| 提案 | 2 |
| 风控 | 1 |
| 执行 | 4 |
| 持仓 | 7 |
| 平仓 | 4 |
| 结果 | 3 |
| 学习 | 13 |
| 基础设施 | 6 |
| 不在主线上 | 14 |

**按处理方式**：

| 处理方式 | 条数 |
|---|---|
| 删掉 | 30 |
| 你来定 | 23 |
| 接上 | 9 |
| 出错要说出来 | 6 |

总的来看，从"执行"往后（成交 → 持仓 → 平仓 → 复盘），几乎每一段都有一个关键字段没写进去：
- 成交被记成取消（MP-01）
- 滑点被清空（MP-02）
- 移动止损没人写（MP-03）
- 入场时的市场环境没采集（MP-05）
- 开仓假设没冻结（SP-01）

所以复盘和学习拿到的输入大多是空的。学习这一段问题最多，大多是旧机制退役后留下的读取方和死代码。

---

## 2. 最要紧的 25 条

| id | 类型 | 位置 | 问题 | 影响 | 处理 | 修法 |
|---|---|---|---|---|---|---|
| MP-01 | P2 | trading/entry_execution_projection.py:52,79；execution/entry_plan_execution.py:580；agent_runtime/fast_trigger_watch.py:477 | 交易所下单量按最小步长向下取整，全部成交后仍剩一点零头，于是被判成"cancelled" | Fedora 上 42 个已成交的入场计划，40 个显示"已取消"；成交率和漏斗都被低估 | 接上 | 剩余量不到一个步长就算成交满；两份映射代码合成一个函数（另一份少了 expired 分支） |
| SP-01 | P2 | storage/proposal_hypothesis.py:244 | 只有 opportunity_id 非空才冻结开仓假设，而入场计划生成的提案这个字段永远为空 | 2026-08-27 以后 640 条提案、42 笔仓位一条假设都没有，方向假设复盘永远出不了结果 | 接上 | 有 entry_plan_id 也允许冻结，内容取计划里已冻结的假设 |
| MP-03 | P2 | portfolio/position_manager.py:659；execution/exchange_materialization.py:1078 | 移动止损和保本只认仓位上的 trailing 字段，但没有任何生产代码写这个字段 | 41 个交易所仓位全部没有移动止损，盈利单不会上移止损 | 你来定 | 见第 4 节 A 组 |
| RP-01 | P3 | agent_runtime/scheduler.py:598 → health.py:279 | 定时任务连续失败只发一条事件，/api/health 不看失败计数 | position_manager（执行止损止盈的任务）一直失败时，首页仍是绿色 | 你来定 | 见 A 组 |
| MP-05 | P2 | execution/entry_plan_execution.py:388 | 入场时的 BTC 价格、市场状态、仓位清单只有已经没人调用的旧入口会采集 | 41/41 个仓位 btc_entry_price 为空，regime 全是 unknown，"相对 BTC 的 alpha"算不出来 | 接上 | 构造提案时合并 capture_attribution_snapshot 的结果 |
| MP-02 | P2 | portfolio/pnl.py:1031 | 平仓时把入场滑点硬写成 None | 41/41 个已平仓位没有滑点数据，分不清是策略亏还是成交亏 | 接上 | 只删 close_position 里这一个键；行政关闭（outcome_closure.py:95）按合同保留 None |
| MP-06 | P3 | risk/engine.py:2132,1281 | 回撤读取出错、快照不足、快照过期时都返回 1.0，即"没有回撤" | 真实回撤期间风控 #5 照样按满额仓位放行，而且不留痕迹 | 出错要说出来 | 记 warning，留下 drawdown_evidence_unavailable 标记；要不要直接拒单你来定 |
| MP-04 | P2 | portfolio/outcome_closure.py:247；disappearance_settlement.py:255 | 交易所原生止损先成交、仓位消失后，一律记成"行政关闭" | 目前 1/41 笔受影响：被学习排除，风控"24 小时内止损后反向开仓"的拦截也看不到这次止损 | 你来定 | 见 A 组 |
| MS-01 | P3 | diagnostics/doctor.py:90,422,504 | 任何探针出错都记成 info，而 healthy 只看 blocker 和 warn | 体检本身坏了，doctor 和喂给 AI 的上下文仍显示"健康" | 出错要说出来 | 探针出错记成 warn；空库缺表仍记 info |
| MS-03 | P2 | analytics/go_live_scorecard.py:172,205 | 合同里的终止条件 K1/K2（连续亏损、连续零开仓）对应的函数没有任何调用方 | 已经连续 10 天零开仓，系统没有任何告警 | 你来定 | 见 D 组 |
| LO-01 | P2 | gateway/operator_shell.py:787 | Operator 心跳过期时，supervisor 自己代写一条"在线"心跳，把过期信号盖掉了 | Operator 卡死后永远不会被重启，界面显示黄色而不是 STALE；今天已触发 2 次 | 你来定 | 见 A 组 |
| MP-07 | P4 | portfolio/reconciliation_integrity.py:137 | 对账事故一旦打开，没有任何入口能把它解除（CLI、API、operator 都没有） | 第一次触发后，demo 对账和学习会一直冻结，只能手改数据库 | 接上 | 加 owner 用的 CLI 解除命令，必须带 actor 和 reason |
| RP-02 | P2 | server/routes/data.py:1451 | 平仓被交易所限频（40725）卡住后，界面写着"需要 owner 解锁"，但没有按钮 | 第一次发生时，这个仓位就没法平仓 | 接上 | 加一个必须填原因的解锁按钮，调用现有路由 |
| MP-09 | P3 | extensions/exchanges/bitget/client.py:272 | 按单号查成交只取 100 条，不翻页，也不检查是否被截断 | 入场对账和成交补缺都在用，大单拆成超过 100 笔成交时数量会算错 | 出错要说出来 | 满页时报错，或者按 cursor 翻页 |
| LR-01 | P5 | extensions/learning/guard_routing.py 等 | 学到的 guard 规则（自动拦截规则）没有执法点，消费方写的是已删除的模块 | 256 条假设没人读；界面能"晋升"规则，但实际不影响任何交易 | 你来定 | 见 B 组 |
| SP-02 | P2 | data/derivatives_facts.py:299；extensions/data/ccxt_provider.py:465 | OI（持仓量）、主动买卖比、多空比只实现了 OKX 和 Binance，没有 Bitget | Bitget 上这 6 个特征 100% 为空，"衍生品"这一族实际只靠资金费率 | 你来定 | 见 C 组 |
| SP-03 | P1 | extensions/learning/entry_context.py:664 | 规划阶段的"历史表现反馈"每次都回退到基线 | 最近 3000 次规划全部"不可用"，这条反馈环从来没接通过 | 你来定 | 见 B 组 |
| LR-11 | P2 | config/schema.py:1428 | 反事实数据源（filter_candidate_outcomes）只保留 45 天，没有任何东西能把它留得更久 | "跨版本累积证据"做不到 | 你来定 | 见 B 组 |
| LR-04 | P2 | cli/control_cmd.py:266 | `control status` 读的是已冻结的旧实验账本，却标成"可用" | 以后 Lab 里真有实验，这里也永远显示 0 个 | 接上 | 改读 Lab，读不到就显示不可用 |
| RP-03 | P2 | config/schema.py:279 | 设置页的"自动重载"开关没有任何代码读取 | 跟安全相关的设置在说假话（live 状态下本来就会关掉重载，所以有兜底） | 删掉 | 删掉开关，改成只读显示真实状态 |
| RP-04 | P2 | config/schema.py:1376 | 界面上调的 run_steps 保留天数（30）不是清理任务实际用的那个（14） | 界面写 30 天，实际 14 天就删 | 接上 | 界面改为指向 agent_run_steps_days |
| RP-05 | P2 | config/schema.py:1873,1915,2162 | 三个设置项只保存、没人读 | owner 调了等于没调 | 删掉 | 删字段和控件，加载配置时丢掉旧 key |
| MS-02 | P2 | diagnostics/probes/content.py:226 | shadow_trades 探针让你去跑一个已经无法运行的脚本 | 照提示操作会直接报错，而且这条 warn 让 healthy 永远是 False | 删掉 | 删探针和脚本 |
| MP-08 | P5 | execution/gateway.py:178 | submit_proposal 是第二条入场入口，可以不经过入场计划直接下单 | 双主线，以后有人调用就能下出"没有计划"的单 | 删掉 | 先做 MP-05，再删；16 个测试文件和 2 份合同要同步改 |
| MS-04 | P2 | analytics/opportunity_flow.py:861 等 3 处 | 三处在读 operator_evaluation_events，这张表没有任何写入方 | "Operator 推迟"的计数永远是 0 | 你来定 | 见 D 组 |

---

## 3. 修复批次

每个批次是一个 PR，按主线顺序和影响大小排列。每个 PR 开工前先跑 `uv run python scripts/ci/dev_check.py`。

| # | 标题 | 包含 | 改动文件 | 处理方式 | 唯一验收 | 大小 | 冲突提示 |
|---|---|---|---|---|---|---|---|
| 1 | 入场计划：全额成交不再记成取消 | MP-01 | trading/entry_execution_projection.py，execution/entry_plan_execution.py，agent_runtime/fast_trigger_watch.py | 接上 | 测试：按步长取整的订单全部成交后，计划状态为 filled，两个调用方走同一个函数 | S | **正在做**：进行中的 "entry-plan filled→cancelled" 工作流就是这件事。不要另开 PR，把"合成一个函数"和"补上 expired 分支"这两点转给它 |
| 2 | 平仓保留入场滑点 | MP-02 | portfolio/pnl.py | 接上 | 测试：有入场滑点的仓位平仓后 slippage_cost 不变 | S | **正在做**：pnl.py 滑点工作流。提醒它 outcome_closure.py:95 要保留 |
| 3 | 提案补齐复盘输入 | SP-01，MP-05 | storage/proposal_hypothesis.py，execution/entry_plan_execution.py | 接上 | 测试：入场计划生成的提案写库后，attribution_snapshot 里同时有 position_hypothesis 和 btc_entry_price/regime_tag，而且都在算 hash 之前写入 | M | 要等批次 1 合入，因为改的是同一个 entry_plan_execution.py；也和 "risk entry-policy freeze" 有关，开工前先和它对一下提案 hash |
| 4 | 删掉第二条入场入口 | MP-08 | execution/gateway.py，16 个测试文件，execution_contract.md，risk_engine_contract.md | 删掉 | 在 core、extensions、ops、scripts、tests 里搜 `rg submit_proposal`，结果为 0 | L | 依赖批次 3 |
| 5 | 风控：回撤证据不可用时要说出来 | MP-06 | risk/engine.py | 出错要说出来 | 测试：快照读取报错、不足 3 个、过期这三种情况，RiskDecision 都带 drawdown_evidence_unavailable 标记，并记 warning | S | 可能和 "risk entry-policy freeze" 改同一个文件 |
| 6 | Bitget 成交分页 | MP-09 | extensions/exchanges/bitget/client.py | 出错要说出来 | 测试：模拟返回满 100 条时报 truncated（或者继续翻页） | S | 无 |
| 7 | 对账事故解除入口 | MP-07 | cli 里新增一个命令，调用 portfolio/reconciliation_integrity.py | 接上 | CLI 测试：干净观察次数不足时拒绝解除；条件满足后带 actor/reason 能解除成功 | S | 无 |
| 8 | 限频平仓解锁按钮 | RP-02 | frontend 仓位详情页（后端不改） | 接上 | 前端测试：pending_close_status=venue_trade_rate_limited 时显示按钮，不填原因不能提交 | S | 和 "close_convergence identity park" 工作流是同一块，开工前先对一下 |
| 9 | doctor 不再把探针出错算成健康 | MS-01，SP-12 | diagnostics/doctor.py，probes/*，trading/sizing_policy.py | 出错要说出来 | 测试：某个探针抛异常时 healthy=False；空库缺表仍是 info。auto.py 的部署门不动 | M | 无 |
| 10 | 设置页去掉假开关 | RP-03，RP-04，RP-05，RP-14 | config/schema.py，配置加载器，server/routes/settings.py，前端 RuntimeSystemTab/AgentModelsTab，settings_control_contract.md | 删掉 + 接上 | 测试：含旧 key 的配置能正常加载并把旧 key 丢掉；run_steps 控件写入的是 agent_run_steps_days | M | **冲突**："config proxy userinfo" 工作流也改配置加载器 |
| 11 | 候选血缘如实显示 | SP-09 | universe/candidate_selection_shadow.py，universe/candidate_opportunity.py | 接上 | 测试：候选上下文里的 feature_snapshot_id 等于实际的 row id | S | 会让规划指纹换代，PR 里要写明；和 "universe admission floor" 工作流同属 universe |
| 12 | 学习：旧账本读取方 | LR-04，LR-05，LR-08，LR-09 | cli/control_cmd.py，extensions/learning/{ledger,models,b2_evidence_policy,prompt_bundles,contribution,contrastive_evidence,memory_assets,economic_knowledge,skip_policy,...}，server/routes/data.py:2001 的死分支 | 接上 + 删掉 | 测试：Lab 权威下没有活跃窗口时，control status 读的是 Lab；数据库报错时 available=False | M | 可能碰到 "lesson reviewer" 工作流（同在 extensions/learning） |
| 13 | shadow_trades 退役收尾 | MS-02，LR-02，LR-07 | diagnostics/probes/content.py，scripts/maintenance/backfill_shadow_trades.py，extensions/counterfactual/{trades,filter_experiments}.py，counterfactual_contract.md | 删掉 + 出错要说出来 | 测试：family scorecard 读库出错时返回 unavailable，不再返回 all_null | S | 无 |
| 14 | 规划唤醒探针出错时按"有待办"处理 | RP-15 | agent_runtime/pi_daemon.py | 出错要说出来 | 测试：探针抛异常时返回 True，并记 warning | S | #3888 可能改到 pi_daemon，先确认一下 |
| 15 | 执行/持仓死代码 | MP-10，MP-11，MP-12，LO-02 | execution/{oco,broker_utils,broker_funding,verified_exit}.py，reconciliation.py，position_manager.py，position_reconciler.py，risk/demo_entry_quality.py，ccxt/submission_errors.py，economics/*，gateway/client_order_id.py，tests/specs/spec_persistence.py（PR-S02 改为断言真实下单路径），TRUST_BOUNDARY_COVERAGE.md，execution_contract.md:463 | 删掉 | 每个被删的名字在生产代码和测试里 `rg -w` 都是 0；PR-S02 断言 client_order_id == proposal_id | M | "overfill flag" 工作流可能改 reconciliation.py |
| 16 | 选币/数据/交易死代码 | SP-05，SP-08（扣掉 3 条要你定的），以及 MS-10 里的 latest_options_structure | data/context_builder.py，trading/*，data/*，universe/{market_structure,fast_stream,service,features}.py，PARTIAL_BROKEN_REVIEW.md | 删掉 | 被删名字 `rg -w` 为 0 | M | **冲突**："universe/registry.py spot exclusion" 和 "admission floor" 工作流同在 universe/ |
| 17 | 分析层死代码 | MS-09，MS-10（保留 signal_replay 今天新加的两个辅助函数和 3 个合同点名项），MS-11 | storage/models.py，analytics/*，extensions/analytics/*，execution_contract.md:644 | 删掉 | 被删名字 `rg -w` 为 0 | M | "selection-alpha table script" 可能读 analytics，先确认没在用 |
| 18 | 没人用的 API 和 CLI | RP-09，RP-10，RP-11，RP-17，SP-10，SP-11（即 MS-08） | server/routes/{analytics,data,settings,operator,health,universe}.py，stability_monitor.py，cli/flow.py，main.py:436，frontend/src/types.ts，api.d.ts（重新生成），api_reference.md | 删掉 | 在 frontend、operator-shell、cli、ops、scripts 里搜被删路由路径都是 0，api.d.ts 重新生成后没有差异 | M | 无 |
| 19 | 运行时/运维死代码 | RP-13，RP-16，LO-04（扣掉 historical_accounting_summary，它是 KEEP），LO-05 | model_provider/*，sync/conflicts.py，files/paths.py，dev_reload.py，wake_events.py（CATASTROPHIC），maintenance/*，gateway/preflight.py，ops/maintenance/* | 删掉 | 被删名字 `rg -w` 为 0 | M | `_gateway_runner.py` 里 vite watch 那部分要等 **#3887 / #3888** 合入后再删，本批先不碰这个文件 |

---

## 4. 需要你定的（一次答完）

### A 组：AI 持仓管理

你之前说这块要细细讨论，但一时想不起有哪些边界情况。下面就是这次清扫翻出来的边界情况，建议单独开一次讨论，逐条过。

| # | 问题 | 选项 | 建议默认 |
|---|---|---|---|
| A1 | 移动止损和保本的参数从哪来？（MP-03、RP-06） | a) 入场时由计划或策略写入并冻结；b) 由 AI 持仓管理通过 adjust_position 显式写入；c) 不做，连同合同描述一起归档 | **b**，同时删掉全局 trail_enabled 开关。只有一个地方能决定 trailing，没有藏起来的默认值 |
| A2 | 接近止损或止盈时要不要唤醒 AI？（STOP_LOSS_NEAR/TAKE_PROFIT_NEAR 这两种唤醒已定义，但从没发出过） | 接上 / 删掉 | 跟着 A1 走：选 b 就接上，因为 AI 要及时调整就得被唤醒；选 a 或 c 就删掉 |
| A3 | 交易所原生止损先成交、仓位消失后，算策略止损还是行政关闭？（MP-04） | 改记为 strategy/stop_loss（这是语义变更）/ 维持行政关闭并删掉没人用的参数 | **改**，但只限"成交单号能对上我方原生 SL/TP 单"这一种情况，因为风控 #14b 需要看到这次止损。历史 41 笔都没有单号，只对 #3848 之后的单生效 |
| A4 | 保护性任务（position_manager、对账、tpsl_fill_check、exchange_sync）连续失败时，首页要不要变红？（RP-01） | 是 / 否 | **是**，就这 5 个任务，写进 scheduler 合同 |
| A5 | Operator 进程还活着但已经卡住：要不要重启？显示什么？（LO-01） | 立即重启 / 只显示 STALE / 维持现状 | **显示 STALE，暂时不自动重启**。supervisor 自己代写的心跳不能再当作存活证据。长任务期间要不要重启以后再定 |
| A6 | 回撤读不到时，风控要不要直接拒单？（MP-06，批次 5 只负责留下标记） | 拒单 / 只留标记 | **只留标记**，观察一段时间再说 |
| A7 | 出场分析（exit-policy-advice、trade-paths）要不要喂给 AI 做持仓复核？（RP-08） | 接上 / 删掉 / 停放 | **停放**，等 A1 定了再处理 |
| A8 | AI 要不要能部分减仓？目前 reduce_position_routed 只是一个只会拒绝的桩，批次 15 会删掉它 | 要 / 不要 | 先删桩；以后需要就重新写 |
| 相关 | 进行中的 "agent_shell/commands.py hold/exchange_synced" 工作流也属于这一块，讨论时一起看 | | |

### B 组：学习和研究的范围

| # | 问题 | 建议默认 |
|---|---|---|
| LR-01 + LR-06 | 学到的 guard 规则链：整条退役，还是给它一个风控执法点？ | **退役**。给它执法点等于扩大风控权限，这是 D2。同时改 learning_contract §5/§7.2 和 knowledge_contract §4.4。在此之前，先把 hypothesis_research.py:535 那条错误的 v93 注释改掉 |
| SP-03 | 规划时的历史表现反馈从没生效过：激活 manifest，还是停放？ | **停放**。规划输入先不删，免得指纹换代。Lab 目前 0 个窗口，filter_controller_observe 从 08-11 起就没成功过，需要部署侧先查清楚 |
| LR-11 | "反事实证据跨版本累积"要不要包括保留期？现在只留 45 天 | **先确认这条决定**并写进 DECISIONS.md。要延长到 180 天的话，先测一下数据库体积（以前出过数据库膨胀导致写锁的事） |
| LR-03 | 反事实的校准/倾向/OPE（离线策略评估）模块：改用新数据源，还是删掉？ | **停放**（合同标成"仅历史"）。改数据源是一次大的语义变更，等 OPE 路线图（#527）排上了再说 |
| MS-07 | 每天一次 LLM 调用的 insight_synthesizer，产出没人用：停掉，还是接进 prompt？ | **停掉 job**，删掉 render_memo_for_observer 和 memo_quality 探针 |
| LR-10 | 四个研究任务从没跑过：停放还是删除？ | **停放**，从 scheduler 摘掉。prediction_* 表保留 |
| SP-04 + MS-06 | relative-rank 对照实验没有写入方，但它的合同仍然是现行的 | **退役合同并删代码**。它依赖 Binance 的多空比，Bitget 上没有这个数据。表保留 |
| MS-05 | Alpha 审计里 6 层的数据源已经停产 | 先给这些层**标上 retired_producer 和最后写入时间**，各层是删还是留，跟着上面几条一起定 |

### C 组：数据和选币

| # | 问题 | 建议默认 |
|---|---|---|
| SP-02 | Bitget 衍生品特征全部为空 | **补上 Bitget 的 OI 单位映射**（大约 1 行）；Bitget 没有原生接口的主动买卖比和多空比，明确标成 venue_unsupported |
| SP-06 | GEX、DefiLlama、Hyperliquid 一直在采集，但没人用 | **停掉采集**，只保留 IV（期权隐含波动率），它有研究用的 CLI 在用 |
| SP-07 | 按"家族"校准的乘数和价差容忍度没有调用方 | **退役**，同时改 learning_contract.md:64 |
| SP-08 中 3 项 | assess_trading_mandate_fit、MarketStructureProvider、hard_gate_decision 都是零调用，但合同点了名 | **删掉，同时改这两份合同**。它们只是为将来预留的接口 |

### D 组：观察面和运维

| # | 问题 | 建议默认 |
|---|---|---|
| MS-03 | 合同里的终止条件 K1/K2：接上，还是把合同改成"未接线"？ | **接上**，作为 go-live scorecard 的 blocker。现在已经连续 10 天零开仓 |
| MS-04 | operator_evaluation_events 的读取方 | 跟着"opportunities 生命周期退役"的决定走。建议**退役**这三处读取 |
| RP-07 | 成交通知功能从没接上 | **删掉**。Fedora 上两个通知通道都没开 |
| RP-12 | PS 旧表的读接口（表都是 0 行） | **删掉读接口并改合同**。没有历史数据，留着没意义 |
| LO-03 | knowledge/ 目录的文件清理：文档说有，函数从没被调用 | **不做文件层清理**，删掉函数，并改 RETENTION_POLICY §4 |
| MS-10 | signal_replay 今天新加的两个辅助函数 | **保留**，确认一下有没有计划中的使用方 |

---

## 5. 防止复发：三道机械检查

已有的 `tests/audit/test_no_silent_except_pass.py` 只抓 `except: pass`。下面三道检查都沿用它"带基线"的做法：已有的问题记进基线放行，只拦新增的。

1. **出错装正常检查（P3）**：扩展上面那个现有测试。在 `core/aletheia/{risk,execution,portfolio,diagnostics}` 和 `agent_runtime/health.py` 里，凡是 `except Exception:` 的处理体里只有 `return <常量>`（1.0、0、[]、True/False、"all_null" 之类）、又没有 logger 调用的，就报失败。MP-06、MS-01、LR-02 这类就能抓到。
2. **零引用检查（P5 和配置字段的 P2）**：
   - 顶层函数和类：如果在 core、extensions、ops、scripts、frontend/src、operator-shell 里，除了定义所在的文件就没有任何 `-w` 命中，而且没有 typer、FastAPI 或 scheduler 装饰器，就报失败。
   - pydantic 配置字段：如果在 schema.py 和 settings.py 之外没有 `.field` 或 `"field"` 引用，也报失败。
   - 这道检查能覆盖大约 30 条"删掉"和 RP-03/04/05/14。
3. **表读写配对检查（表的 P2）**：扫描生产代码里的 SQL 字符串。凡是出现在 FROM/JOIN 里的表，必须在生产代码里也有 INSERT、UPDATE，或者被通用的 `_insert(table)` 这类函数写入；做不到的，必须登记在"已退役表"基线里。shadow_trades、universe_policies、operator_evaluation_events、universe_setups、relative rank 表、ps_* 都能抓到。

P1（只单测、没接通）和 P4（没跑过的路径）没有便宜的机械检查办法。只能靠每条主线接缝补一个跨两端的测试，比如批次 3 的验收测试那样。

---

## 6. 这次没覆盖到的

- **风控**：#6 到 #16 关卡和相关性敞口没有逐行审查。
- **执行**：reconciliation.py、pending_close_recovery.py、stale_order_cleanup.py、close_convergence.py 没做端到端追踪；#3848 新加的 native_protection 在调度线程里能不能正常运行没有验证。
- **大文件**：agent_shell/commands.py（2368 行）、server/routes 下 data.py 和 settings.py 只看了扫描命中的位置；storage/repositories.py（6000 多行）没有逐表核对。
- **学习**：observation_lab 的大模块（filter_shadow、canonical、kernel、evaluator）、lesson_reviewer、dreaming、post_trade_reviewer、risk_critic 内部的 LLM 逻辑都没审。
- **下游消费**：regime 结果（market_regimes 表）、新闻链路、各个 scheduler job 的产出（confidence_calibration、reasoning_feedback、persona_rollout）有没有下游读者，没有追。
- **路由扫描**：是按路径字符串匹配的启发式扫描，动态拼出来的路径可能漏掉。
- **运行时**：没有拿到 Fedora 上 /api/health 和 doctor 的实时输出（端口或鉴权的问题），MS-01 只靠读代码。
- **部署侧异常，只记录、没追查**：filter_controller_observe 从 08-11 起一直因为 cycle_lock_contended 失败；aletheia-observer.timer 今天 09:28 被停掉，可能是并行的会话在操作。
- **已核实并修正的审计误判**：
  - historical_accounting_summary 其实有生产调用，判 KEEP。
  - LR-07 的 register CLI 存在，但它不经过被删的那个函数。
  - LR-09 的函数本身有调用方，只删函数体里的死分支。
  - MP-09 其实有运行流量，影响面比原来写的大。
  - MP-04 实际只影响 1/41 笔。
  - LR-02 的 shadow_count 在界面上没有显示。