# Debug & Audit Playbook（SOP）

> 从实战中固化的方法论：高频陷阱 + 可操作的检查步骤。**查 bug / 做审计 / 改代码前过一遍。**
> 每条都带真实案例（注明日期），方便以后对号入座。新教训随时往这里加（append）。
> 关联：`UX_AUDIT_LESSONS.md`（UI 审计教训）、`docs/issues/`（具体 bug ledger）、`docs/design/process/SDD_METHODOLOGY.md`（spec 流程）。

---

## SOP-1 ｜ "未实现/缺失"结论必须反查"是不是在别处实现了"

**陷阱**：并行审计/调研 agent（以及人）会把"我没看到"当成"不存在"。只看一个语言/模块/进程就下"未实现/死代码/no-op"的判断，极易误报。

**真实案例（2026-06-28 PI agent 审计）**：
- audit agent 拿 `operator_layer.md`（愿景）对照 **Python** 代码，报"8 modes / 多级 cadence / Auto Repair 未实现"。真相：**全部已在 TS `operator-shell/` 实现且正在生产运行**（3000+ 行，gateway spawn 的 node 子进程，21577 条 heartbeat 实时刷新）。
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

**SOP**：
1. worktree 分支按功能**分段 commit**（Conventional + 详细 body）。
2. 在 worktree：`git merge master --no-edit` 把最新 master 合进来，解决冲突（在熟悉自己改动的 worktree 里解最稳）。
3. 主仓 FF：`git -C <主仓绝对路径> merge --ff-only <worktree分支>`（不 `cd` 主仓，用 `-C`）。
4. **FF 失败 = master 又被超车** → 回第 2 步重复（重核 `git -C <主仓> log --oneline -1`）。
5. 合并后**主仓** `aletheia gateway restart` + `aletheia doctor` 才生效——worktree 的 `data/` 为空，不要在 worktree 验运行态（见 memory: worktree-gateway-empty-data-trap）。
6. 冲突解决后用 `git diff --check` 确认无残留 `<<<<`/`====`/`>>>>` 标记 + 跑 import/针对性测试。

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
