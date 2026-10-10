# Debug & Audit Playbook（SOP）

> 从实战中固化的方法论：高频陷阱 + 可操作的检查步骤。**查 bug / 做审计 / 改代码前过一遍。**
> 每条都带真实案例（注明日期），方便以后对号入座。新教训随时往这里加（append）。
> 关联：[`ui_design_system`](../../design/frontend/ui_design_system.md)（UI 规则）、
> GitHub Issues（当前 bug ledger）、[`SDD_METHODOLOGY`](../../design/process/SDD_METHODOLOGY.md)
>（spec 流程）。

---

## SOP-1 ｜ "未实现/缺失"结论必须反查"是不是在别处实现了"

**陷阱**：并行审计/调研 agent（以及人）会把"我没看到"当成"不存在"。只看一个语言/模块/进程就下"未实现/死代码/no-op"的判断，极易误报。

**真实案例（2026-06-28 PI agent 审计）**：
- audit agent 拿 `docs/design/history/operator_layer.md`（愿景）对照 **Python** 代码，报"8 modes / 多级 cadence / Auto Repair 未实现"。真相：**全部已在 TS `operator-shell/` 实现且正在生产运行**（3000+ 行，gateway spawn 的 node 子进程，21577 条 heartbeat 实时刷新）。
- 同源误报 A2/A4：把三套组件（**TS operator-shell** / Python `operator_loop.py` / Python `pi_daemon.py`）混为一谈 → "pi_daemon 不写 heartbeat 导致判 offline"（其实 heartbeat 是 TS shell 写的）、"guard 审批旁路"（其实 dispatcher 正确分级）。

**SOP**：
1. 任何 **未实现 / 缺失 / 死代码 / no-op** 结论，采信前**跨语言（TS↔Python）、跨模块、跨进程**反查同名/同义功能。
2. 一个能力可能有多个实现载体（Python + TS + daemon）→ 先画清"**谁负责什么 + 谁在跑**"，再判缺失。运行态证据（DB 行数 + 最新时间戳、进程心跳）最硬。
3. **将驱动大改/大重构的高影响结论，亲自核实 file:line + 跑一下**，别全信单个 agent（agent 善于定位、不善于判断"够不够"）。
4. 发现 agent 误报后，**回头重审同源的其它结论**（误报往往成批，根因相同）。

---

## SOP-2 ｜ 静默失效：try/except 吞掉 import/语法错误

**陷阱**：`lazy import + 裸 except Exception` 会把 `SyntaxError`/`ImportError` 一起吞掉 → 功能彻底死了，但日志不报错、`doctor` 不报警，看起来一切正常。

**真实案例（2026-06-28）**：`reflection.py` 一处重复 `opportunity_id=` kwarg → 整模块 `SyntaxError` → 两个调用方都是 `try: from ... import ...; except Exception: pass` → `record_decision`/`get_last_reflection` 双双静默失效，没人发现。提交 `5be7826`（本意是修 attribution）引入。

**SOP**：
1. **"功能没生效但不报错"类 bug，第一步先验模块能否 import**：`uv run python -c "import aletheia.xxx.yyy"`。
2. 凡是 `lazy import + 裸 except` 的模块，**加 import smoke test** 锁回归（毫秒级、不依赖 DB）：`importlib.import_module(...)` + `assert hasattr(...)`。本次新增 `test_agent_shell::test_reflection_module_imports_cleanly` 即此模式。
3. 改完代码**别假设它能 import**——Edit/Write 不校验语法，跑一次 import 或针对性测试。

---

## SOP-3 ｜ 死写 / phantom：追"生产端 ↔ 消费端"完整链路

**陷阱**：(a) 数据写了没人读（死写）；(b) 动作记成 `status="executed"` 但没有任何实际副作用（phantom no-op）。两者都"看起来在工作"。

**真实案例（2026-06-28）**：
- `operator_loop` 写 `recommend_retire/demote` 到 `pi_decisions`，但 `trading_context` 的待执行队列只查 `action='signal_pending'` → 永远没人消费（死写）。
- Level-1 动作 `adjust_scanner_weight`/`request_backfill` 调 `_record_action("executed")` + log，但**不写任何 config/allocator/队列**（phantom）。

**SOP**：查"功能实现了但没效果"，**追完整链路**：生产端写入 → 存储表 → 消费端读取（注意 WHERE 过滤条件！）→ **真实副作用**。验证两端都接通、副作用真发生（写了 DB/config/触发了任务），不只是写了 log 或一行审计。

---

## SOP-4 ｜ 测试红了：先判断"改代码"还是"改测试"

**陷阱**：测试失败就条件反射改代码（或反之），不先判断哪个才是对的。

**真实案例（2026-06-28）**：
- `test_serenity_workbench::test_promote_theme` 断言旧的 `'draft'` → **测试过时**（代码已是 Phase3 `submitted_to_pi`，且 spec SP-B01 已这么测）→ 改测试。
- `test_config_routes` 报 serenity 字段未覆盖 → **代码 bug**（GET 漏暴露）→ 改代码。
- `test_operator_loop` 锁旧的"产生 retire_strategy" → 收敛后行为变了 → **改测试**对齐新契约。

**SOP**：测试失败先问——**契约文档 / 最新 owner 决策 / spec 说什么是对的？** 测试锁的是"旧行为"（已被有意改变 → 改测试 + 注明原因）还是"真缺陷"（→ 改代码）。判断依据优先级：owner 最新决策 > L2 契约文档 > spec > 测试现状。

---

## SOP-5 ｜ 学习/统计数据质量：过滤脏窗 + 安全阀必须真 enforce

**陷阱**：(a) 学习/归因吃滚动全量数据，被历史脏数据带偏；(b) 声明了安全阀常量但没有 enforce 代码（死常量）。

**真实案例（2026-06-28 负反馈闭环）**：
- 三个学习模块用滚动 30/90 天全量（含 Round1 脏数据）→ memo 学出"做什么都 contra（反指）"。修：统一过滤到干净窗 floor（`learning_window.clean_window_cutoff`，2026-06-25T15:22Z）。
- `MAX_DIRECTION_BIAS_STRENGTH=0.75` 只在注释里提到、`_compute_direction_summary` 从不用它（死常量）→ 防单边偏向的安全阀根本没生效。修：真 clamp（单边占比>0.75 force balanced）+ spec 验证。

**SOP**：
1. 学习/统计/归因取数，**显式过滤已知脏数据窗口**（别默认"全量=更好"）。
2. 任何声明的安全阀（cap / threshold / max_*）**必须有 enforce 代码 + 行为 spec 验证真生效**——用 `grep <CONST>` 数它出现几次，只出现在定义处 = 死常量。

---

## SOP-6 ｜ worktree → 主线 合并（并发会话存在）

**陷阱**：本仓可能有**并发会话**不断推进 `master` → FF 反复失败、merge 期间被超车。

**真实案例（2026-06-28）**：merge 期间 master 连续前进（ae382d1 → d728aa4），FF 失败两次。

**SOP**：合并与 push 的现行步骤（jj workspace、`jj rebase -d master@origin`、复读
`jj op log`、人工 `gh pr merge --rebase`）只在
[JJ_WORKSPACE_PLAYBOOK §4](../../operations/development/JJ_WORKSPACE_PLAYBOOK.md#4-land-a-change) 维护。
合并后才在**主仓** `aletheia gateway restart` + `aletheia doctor` 验运行态——workspace 的
`data/` 为空，不要在 workspace 验运行态。

---

## SOP-7 ｜ owner 决策跨轮矛盾 → surface，别默默选

**陷阱**：owner 在不同轮给的指令冲突，闷头按其中一个做。

**真实案例（2026-06-28）**：上一轮"补 Python 执行端" vs 这一轮"重叠收敛到 TS" —— 方向相反。做法：明确把矛盾摆出来让 owner 一句话定，按**最新 + 最知情**的决策（这轮是在我呈现"两套 operator 并存"新信息后给的，更知情）。

**SOP**：
1. owner 跨轮决策冲突时，**surface 矛盾**（"你上轮说 A、这轮说 B，二者相反，按哪个？"），不默默选一个。
2. **新信息出现后（如审计基线被纠正）主动重新对齐范围**——owner 之前的决策可能建立在已被推翻的前提上（本次"全做含概念愿景"就是基于"愿景未实现"的错误前提）。

---

## SOP-8 ｜ 信任边界附近改动：先确认"是不是真的安全语义"

**真实案例（2026-06-28）**：`trading_decision` 的 guard 链看似"硬拦截安全门"，但读 `guard_chain` docstring 发现它自我定义是 **pre-risk observability 层**（不该拦），真正的安全门是下游 Risk Engine。所以"改成建议性"是回归设计本意、不降安全。

**SOP**：动 `risk/`/`execution/`/guard 等信任边界附近代码前，**先读该模块自己的 docstring/契约**确认它的真实职责（observability vs 安全门）。别凭名字假设。改动只碰消费方式、不动 trust 模块本身时风险更低。

---

## SOP-9 ｜ 工具证据与当前工作树冲突时，先重新确认事实

**陷阱**：任何索引、缓存、旧对话或历史报告都可能属于另一个提交或工作树。
如果直接拿它们做 bug 诊断，会产生“幽灵 bug”。

**真实案例（2026-07-22）**：
- codegraph 报告 `aletheia/server/routes/live.py` 是 210 行，包含 `_readiness_scorecard`、`_check_credentials` 等函数；`readiness.py` 包含读进程内存的 `_market_data_row()` 和 `build_scorecard()`。
- 基于这批幻象数据，诊断出"split-process 部署下 `market_data` 探针永远假红、`execution_gateway`/`operator` 永远假绿"，并建立了 GitHub Issue #315，花费整个调试 session。
- 用 `Read` 直接读后发现：真实 `live.py` 是 104 行，`readiness.py` 根本不存在，`live_unlock` 用的是 `go_live_scorecard.py`（纯 SQLite 计算）。`/api/health` 返回 `{"status": "healthy"}`，`live_unlock` 被拦是因为真实的 drawdown/PnL 问题。Issue #315 立即关闭。

**SOP**：
1. 先确认仓库、工作树和 HEAD；不要把另一个 checkout 的证据套到当前代码。
2. CodeGraph 返回的当前源码可以直接作为静态事实；若它与 Git 状态、近期改动或运行
   结果冲突，重新查询当前目标并用最小的源码/测试/endpoint 证据消除冲突。
3. **提 GitHub Issue 前必须确认 bug 路径存在于当前代码**，并区分静态推断与已复现行为。
4. `/api/health`、`/api/status` 和 focused test 可以快速推翻错误的静态诊断链。

---

## SOP-10 ｜ 静默劣化要查容量、吞吐和消费者，不只查“进程活着”

**陷阱**：系统不崩溃，但索引覆盖缩水、共享时间预算饿死后排任务、队列生产速度
超过结算速度，或过期字段未被消费。普通 uptime/最近窗口检查仍可能显示正常。

**SOP**：
1. 对索引或镜像检查**源表应覆盖数 vs 实际索引数**，不能只看索引非空。
2. 对周期批处理比较生产速率与最大消费速率；单批上限追不上积压时，使用有界循环，
   直到队列清空或时间预算耗尽。
3. 共享 deadline 下按产品重要性排序任务，并为每个任务暴露最后成功时间；超时跳过也
   必须留下可观测事实。
4. 积压健康检查使用权威全量条件（例如所有已到期未结算行），不要用最近 N 条或短窗口
   冒充总积压。
5. 存在 `valid_until` 等权威到期字段时，清理和漏斗统计必须消费该字段，不能只依赖
   `created_at` 兜底。
6. LLM prompt 中任何 long/short、支持/反对语义都要按本次方向验证；不要因为最终公式
   看似对称，就假设 prompt 本身没有方向偏置。

---

## SOP-11 ｜ 关闭状态词表必须从**读路径**枚举，而非只看写路径

**陷阱**：只看当前写路径（`INSERT`/`UPDATE` 的 `status=...`）会漏掉"历史代码曾写入、
现已停止写入"的值。这些值仍然存在于数据库行中；给 `CHECK` 约束或 `Literal` 加上这些值
时若缺漏，迁移在含历史数据的 Fedora 生产库会直接失败。

**真实案例（2026-09-07 #2953/#2954 AgentRun/Order 词表关闭）**：
- 只扫写路径列出了 9 个状态值；加上 CHECK 约束后，spec 套件爆出 8 个新失败。
- 缺漏的 6 个值全部来自**读路径**：
  - `error`、`success`：来自 `run_view.FAILED_AGENT_RUN_STATUSES` / `SUCCESS_AGENT_RUN_STATUSES`
  - `cancel_requested`：来自 `health._FAILED_STATUSES`
  - `executed`、`failed`、`rejected`：来自 `trade_stats.completed_fill_statuses` /
    `definitive_failure_statuses`
- 每一个都是"某段历史代码曾写入，现已演化或删除，但数据库里仍有旧行"。

**SOP**：
1. 先 `grep -r "status.*['\"]" --include="*.py"` 找所有写路径（完整，不抽样）。
2. **强制**：再 `grep -r "STATUSES\|status_set\|status_list\|fill_statuses\|FAILED_STATUS" --include="*.py"` 找所有读路径聚合常量，逐一展开其值。
3. 检查聚合统计模块（如 `trade_stats.py`）——它们往往是历史终态的唯一残存引用。
4. 把两批结果合并为候选词表；带 `# historical terminal` 注释标注仅出现在读路径的值。
5. 对写路径和读路径各写一个 `assert set(WRITE_STATUSES).issubset(FULL_VOCAB)` 断言，
   放在迁移函数同文件，防止词表再次悄悄缩水。

---

## SOP-12 ｜ 把 `None` 返回改成 `raise` 前，追踪所有调用方的控制流

**陷阱**：一个返回 `None` 的函数可能被上游用作 **fail-closed 哨兵**（`if x is None: return`
/ `except Exception: return`）。把它改成 `raise` 会让 fail-closed 退化为**永久阻塞**或
fail-open，效果完全相反。

**真实案例（2026-09-07 A3 learning ledger interlock 被正确放弃）**：
- 审计报告指出 `ledger.list_open_experiments` 返回 `None` 应改为 `raise`，以"强制执行
  interlock"（判定为 P1）。
- 追踪调用方发现：唯一消费方是 `candidate_selection_overlap_block_reason`，它用
  `except Exception: return BLOCK_REASON` 包裹，任何异常都会让候选选择**永久拒绝所有入场**。
- 改成 `raise` = 生产宕机，不是修复。正确答案是**放弃 A3**，记录为设计债，不在当次改动。

**SOP**：
1. 找到函数的所有调用方（`grep` + codegraph）。
2. 逐一追踪：`None` 返回时控制流走哪条分支？是 early return、还是向上传播？
3. 特别警惕 `except Exception: return <block_reason>` 模式——这是 fail-closed 的标准写法，
   任何 `raise` 改造都会把 fail-closed 变成永久阻塞。
4. 若调用方已经 fail-closed，`None` 返回是**正确的信号契约**，不是 bug。需要 interlock
   时应在**调用方**加显式逻辑，而非改变被调用方的异常语义。
5. 高影响判断（"这是 P1"）在采纳前必须走完 SOP-1 的跨模块反查。

---

## SOP-13 ｜ 接手大 PR 留下的混乱前，先量红色基线

**陷阱**：在不知道当前红色基线的情况下开始修复，会把"原本就存在的失败"误计为"我引入
的回归"，导致修复目标不清、反复返工。

**真实案例（2026-09-07 #2945 收尾）**：
- PR #2945 是一个跳过完整 CI 的大型 squash-merge，合入后 master 处于 55 spec 失败
  （之前基线是 29）。
- 若不先量基线，贸然动手，每次新增失败都无法判断是"#2945 的债"还是"本次改动引入的"。
- 先跑 `uv run pytest tests/specs/ -q` 拿到精确数字（55 failed/1337 passed），
  之后每批修复都能用数字验证方向正确。

**SOP**：
1. 接手任何"已知混乱"或大型 squash-merge 之后的分支前，**先跑一次基线测量**：
   ```sh
   uv run pytest tests/specs/ -q 2>&1 | tail -5
   ```
2. 记录数字（failed/passed），作为后续每批修复的参照锚点。
3. 每批修复后立即重跑同一命令，确认数字在向好方向移动；若反向，立即停止找根因。
4. 基线数字落进 PR 描述或 commit message，让 reviewer 看到起点和终点。

## SOP-14 ｜ 判定"删除是回归"之前，先读合同层

**陷阱**：只读被删掉的调用点，会得出与事实**相反**的结论。缺失的调用边看起来永远像
半途而废的删除，因为证据链（函数没了、调用方 0 引用、没有后继）确实完整——缺的那一环
是"这件事本来就该被删掉"，而它只写在 `docs/design/contracts/`。

**真实案例（2026-09-07 #2945 收尾）**：查出 7 条按需唤醒边有 5 条被删
（`equity_observer_opportunity` / `prediction_observer_opportunity` /
`divergence_scanner_opportunity` / `guard_set_changed` / `ranker_config_changed`
全部 0 引用，原来都是 `notify_paper_trader(source=...)`，#2945 删了该函数却没把调用方
改指向 `notify_pi_daemon`）。据此**改了 5 个生产文件**。随后读到
`agent_contracts.md:204`「Historical Ranker / PaperTrader (retired entry)」：

> neither job, queue admission, TTL writer nor **on-demand notification** remains
> an executable entry path.

另有 `:174`「does not wake Ranker」、`:191`、`:468` 三处一致表述；三个 observer 提交的
都是 `execution_eligible=False` 的 Opportunity，唤醒入场规划器永远产不出入场。
**5 个文件全部回退。**

同一天另有三例同型：
- `refresh_active_snapshot_ttls` 签名 `tuple[int,int]` → `int`，像删了 opportunity
  续期（疑似 GAP-2 回归）。实际被删那段只作用于 `generation_version IS NULL` 的行，而
  `insert_opportunity` 对 execution-eligible 生产者强制 v2 否则 raise ⇒ 删对了。
- `spec_agent_contracts` AC-S13 不是"要决策的断言"，是 spec 落后于已记录的退役。
- `spec_live_unlock` LU-C07 我曾判定"改测试会掩盖真缺口"，但
  `live_unlock_contract.md:45/106-114` 早已写明新 Proposal 不路由到实盘、能力
  Experimental、缺失的 join 追踪于 #418 ⇒ 缺口是**已记录并追踪的**，不是隐藏的。

**SOP**：
1. 顺序固定：**合同文档 → 生产代码 → 被删调用点**。反过来必错。
2. 先 `grep -rn "<被删符号|能力名>" docs/design/contracts/ docs/design/history/`。
   命中"retired / 不再 / 已退役 / Experimental / 追踪于 #NNN"即为有意删除。
3. 合同没写但代码删了 ⇒ 才可能是回归；此时仍需确认后继者是否换名存在。
4. 把"a reported defect is a hypothesis until the calling code is read"补一句：
   **直到合同层被读。**

## SOP-15 ｜ 扫源码文本的 spec 会烂，且烂法是静默的

**陷阱**：断言"某字符串出现在某生产文件里"的 spec，在重构、改名、把硬编码换成派生之后
必然红，而它红的原因与它想保护的不变式无关。更坏的是它可能**空过**：字符串还在，但
语义早已不同。

**真实案例（2026-09-07）**四例：
- `AC-S13` 断言 7 个 `notify_*` token 出现在 6 个文件里 + 一个 3 分钟 job。
- `DU-S18` 断言字面量 `"okx_demo"` 出现在 `exchange_alignment.py`——把硬编码 map 改成
  从 `EXCHANGE_UNIFICATION_MAP` 派生后立刻红，而派生是更强的安排。
- `DM-S06` 断言三张表的 DDL 在 `SCHEMA_SQL` 里；DDL 文本确实还在源文件，但 #2945 在
  import 后重绑了该常量把整块切掉，所以**文本为真、运行时为假**。
- `DCLS-S01` 用前缀扫描 `runner.py` 源码禁四个包，被当成"不可达性保证"，实测完全不是
  （见 SOP-16）。

**SOP**：
1. **优先断言行为**：调函数、看返回值/异常/落库结果。
2. 必须扫文本时，在测试 docstring 里写清**它保证什么、不保证什么**，否则下一个读者会
   把它当更强的保证。
3. 扫"某字符串必须存在"时，同时断言该字符串背后的行为；只有"某字符串必须不存在"
   （反向断言）可以单独成立。
4. 用 AST 而非子串：`from a.b import c` 与 `from a.b.c import d` 是不同模块，
   `split()` 分不开（本次踩过）。

## SOP-16 ｜ 前缀/导入 denylist 是卫生检查，不是可达性保证

**陷阱**：把"某文件源码不得出现 `aletheia.execution`"读成"该文件到不了 Execution"。
在耦合密的包里这两件事没有关系。

**历史案例（2026-09-07 DCLS-S01；相关机制已[归档](../../design/history/deterministic_control_loop_simulation.md)）**：当时的模拟器合同把 Execution 列在边界外，#2945 却把
合成入场接到了 `execution.paper_broker`。在"回退代码"和"改合同"之间选择前先测传递闭包：

| 入口 | aletheia 闭包 | exchanges | secrets | risk | gateway |
| --- | --- | --- | --- | --- | --- |
| `portfolio.paper_account`（#2945 之前的 seam） | 359 | 5 | 1 | 5 | 13 |
| `execution.paper_broker`（当时的 seam） | 359 | 5 | 1 | 5 | 13 |
| `simulation.control_loop.runner` | 366 | 5 | 1 | 5 | 13 |

**闭包完全相同** ⇒ 回退只买到文本卫生，一点真隔离都买不到；该前缀规则从未执行它看起来
在执行的意图。当时结论是留代码、把 spec 收窄到它真正执行的东西（`runner.py` 自身源码的直接
导入卫生），并把测量写进合同作为 Contract Delta 的依据。

**SOP**：
1. 任何"不得导入 X"的规则，先测传递闭包再相信它。脚本见本 SOP 案例（AST 遍历 +
   BFS，含函数内延迟导入，故为"可达"而非"import 时加载"）。
2. 闭包相同 ⇒ 该规则只是直接导入卫生。仍值得保留（防止有人顺手加 venue broker /
   读凭证 / 调 Risk），但**必须在 docstring 与合同里写明它不是可达性保证**。
3. 真要隔离，靠的是进程边界、虚拟时钟、显式注入，不是前缀。

## SOP-17 ｜ 精确清单断言在债务**缩小**时同样会红

**陷阱**：`assert allowed == discovered` 这类"精确债务上限"双向严格。债增长时红是设计
意图；债缩小时也红，而这时人的第一反应是"我弄坏了什么"，实际是别人把债还了。

**真实案例（2026-09-07）**：`spec_observation_experiment_ownership` 两个测试红，
`unexpected = discovered - allowed` **都是空**（没有新增耦合，被守卫的不变式完好），
而 `stale = allowed - discovered` 分别有 17 和 10 项——#2945 把 legacy Experiment 表
的引用从 12 个存活文件里清空、另删了 5 个文件。修法是删 allowlist 的 27 行，零生产改动，
零保护损失。

**SOP**：
1. 读这类失败先分清是 `unexpected` 还是 `stale`。**只有 `unexpected` 是真信号。**
2. 断言消息里必须同时打印两侧，否则下一个人分不清方向（本次两个测试都做对了这点）。
3. 收窄 allowlist 时在注释里写清"为什么这次是缩小"，并说明 `unexpected` 仍是守卫的那半。
4. 测试名写 `cannot_grow` 而断言是精确相等，属于命名误导，改名或改断言。

## SOP-18 ｜ 绿 ≠ 对：一行改测试可能掩盖真缺口

**陷阱**：spec 红是因为它调了一个被删的 wrapper，把它改指向存活的等价实现就绿了。但
"等价实现存在"不等于"该能力存在"——被删的可能是整条接线。

**历史案例（2026-09-07）**：`spec_live_unlock` 两个测试改一行即绿；但有效的 live unlock
**当时确实无法**把新 Proposal 路由到实盘（entry-plan compiler 裸读
`paper_execution_mode` 且只接受 `{paper_internal, bitget_demo}`，gateway
`submit_proposal` 已无生产调用方）。我据此把它列为"待所有者决策"并搁置——**这也是错的**，
因为合同早已记录并追踪（#418）。正确动作是：改测试 + 把 claim 文本改成合同真实承诺的
范围 + 在合同里新增小节说明"本节为绿不代表实盘路由存在"。

**SOP**：
1. 改测试前问：**被删的是实现，还是能力？** 后者要先读合同（SOP-14）。
2. claim 的 `invariant` 文本必须只承诺被测的那件事。原 LU-C07 承诺
   "before Proposal persistence"，而合同说这个 join 不存在 ⇒ claim 文本才是错的那半。
3. 合同若缺少描述被测行为的小节，**补小节**，并显式写清它不蕴含什么。
4. 既不能改测试装作没事，也不能把可读出答案的歧义推给所有者。

## SOP-19 ｜ 守卫测试必须被证明会红

**陷阱**：新加的守卫看着合理、跑起来绿，但它其实什么都没测。

**真实案例（2026-09-07）**三次都做了这一步并有一次救了场：
- `RE-S09`（flat/nested 镜像）：临时撤掉 `schema.py` 的修复后该测试
  `assert 10.0 == 7.0` 红 ⇒ 非空。
- 裸 `UPDATE positions` 守卫：塞回一条裸 SQL 后红并点名文件 ⇒ 非空。
- `DCLS-S01` 收窄后：加一条 `execution.coordinator` 导入后红 ⇒ 非空。

**SOP**：
1. 每加一个守卫，**先把被守卫的修复临时撤掉，确认它红**，再恢复。
   在独立 jj workspace 里 `jj new` 后临时改回 / 临时插一行违规代码都可以，验完
   `jj abandon` 该临时提交或 `jj restore <file>`（不要用共享的 `git stash` 栈）。
2. 在 commit message 写明"verified non-vacuous by ..."。
3. 这一步不可省。绿色的空测试比没有测试更坏，因为它让人以为这块有覆盖。
