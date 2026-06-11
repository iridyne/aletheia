# Current Task

> **本文件只写意图(计划 / 待办 / 阶段目标),不写运行态事实。**
> Gateway 是否在跑、凭证是否就位、观察期跑了多久、demo 单是否落交易所——这些是
> **每台机器本地的、随时间变化的事实**,写进 git 必然在另一台机器上过期。
> **要知道真实状态,运行 `aletheia doctor`**(只读、零副作用,是事实的唯一来源)。
> 双机切换标准流程见 `docs/DEV_PROTOCOL.md`。
> **2026-06-10 当前协作分工**:权威的 Phase 0 72h 观察窗口在另一台观测机上运行;本 checkout
> 的职责是沿 `worktree-dev` 推进后续开发与文档收敛,尤其是 Operator Layer / Pi Agent 的
> 方案冻结后实现。**不要用本 checkout 的进程状态推断观察窗是否达标。**

---

## 当前阶段:Phase 0 — 跑通并稳定一个 72h 无人值守 paper 观察窗口

**目标(机器无关,长期有效)**:让闭环在 `bitget_demo`(真实模拟盘)形态下连续无人值守
运行 72h,证明"闭环在运转"(不是盈利)。

**Phase 0 观察门(72h 后逐条对照;判据稳定,事实用 `doctor` 取):**
- [ ] 网关 72h+ 无崩(health 非 dead)且窗口内无 `code_drift` 自愈重启
      (有 drift 重启 = 期间有人 commit 了 → 计时作废)
- [ ] market_observer 每 ~15min 产 opportunity(>70% 非 error)
- [ ] paper_trader 累计 **fill ≥ 5**(计 fill 不计 position 行——同标的同向合并只是展示口径)
- [ ] position_manager 累计平仓 ≥ 2
- [ ] post_trade_reviewer + risk_critic 有产出(knowledge_items 持续增长)
- [ ] demo 真单在 Bitget 侧可见(`get_open_orders`/`get_positions` 能查到)

> **判据性质**:衡量"闭环在运转",**不衡量盈利**(盈利是 Phase 1+ 的独立问题)。
> **观察项(非判据)**:持仓集中在单一标的是 Phase 0 正常现象,多标的分散是 Phase 2A。

**铁律**:观察窗口期间不 commit(任何 commit 移动 HEAD → 触发 code_drift 自愈重启 →
计时作废)。唯一例外是"观察形态本身升级"(如 paper_internal → bitget_demo),
那是主动的更高保真度替换,不是噪音污染——升级落地后新窗口内仍守此律。

## 待办(下一步可做的事 — 不含"现在状态如何")

1. **观测主线继续**:Phase 0 观察门仍以观测机为准;凡涉及"72h 跑到哪了 / 是否达标"的判断,
   一律去那台机器用 `doctor`/gateway status 取事实。
2. **并行开发线 = `worktree-dev`**:可继续推进 post-Phase-0 能力的实现/验证,但不把这些开发
   行为当作 Phase 0 达标证据,也不提前合并回 `master`。
3. **Windows 无人值守加固**(观测机 Task 3):autostart 看门狗 schtasks 注册可靠性、
   gateway 重启/注销/整机重启后的自动复活。这是 72h 真正无人值守的前提。
4. **经验闭环质量门**(已在工作区,待提交):post_trade_reviewer / risk_critic 仅在
   LLM 真产出 + 有真实活动时升 `high_confidence`,防噪音进检索。

## 下一阶段(Phase 0 门全绿后,需显式授权才启动)

见 `docs/ROADMAP.md`。Phase 1 = 稳跑验证 + 学习质量诊断;Phase 2 = 经验闭环。
**不在 Phase 0 稳跑证明前启动任何 Phase 1+ 工作。**

---

## Pi Agent (Operator Layer Track B) — 当前设计源

> 在 worktree `worktree-dev` 开发;合并 `master` 仍等 Phase 0 门全绿 + 显式授权。
> **本地设计源已收口**:
> 1. `docs/ROADMAP.md` § Cross-Cutting Track: Operator Layer
> 2. `docs/OPERATOR_LAYER_DESIGN.md` (概念层)
> 3. `docs/superpowers/specs/2026-06-10-pi-operator-agent-dual-process-design.md` (repo-specific 详细蓝图)
> 4. `docs/superpowers/specs/2026-06-10-pi-operator-agent-design-crosswalk.md` (参考文件覆盖矩阵)
> 5. `docs/superpowers/specs/2026-06-10-pi-operator-agent-design-audit.md` (设计完成审计)

### 当前结论(已定)

- **最终形态是双进程**,不是把现有 `POST /api/command` 继续堆成 Python 单进程壳。
- **Python gateway 继续做交易真相层与命令执行权威**;新增 Node/TypeScript `Operator Shell`
  负责 PI 式常驻 session runtime、operator loop、任务/修复/推荐编排。
- **`Ctrl+K` 只保留为 launcher**,主表面是独立的 `Command Center` 页面。
- **权限拆两层**:现有 worker/trading 权限继续保留;新增独立 `operator.permission_mode`
  (`ReadOnly/Research/PaperAuto/AllowEditing/Auto`)。
- **现有 `aletheia/agent_shell` 不推倒重来**,而是重构定位为 Python 侧 operator substrate /
  compatibility layer。

### 当前实现线待完成动作

1. **设计阶段已结束**:后续实现一律以本地 dual-process spec + crosswalk + audit 为准,不再回退到更早的 Stage 1 bootstrap 草案。
2. **当前已在写实现**:继续沿 Stage 1 顺序补齐 Python truth layer、Command Center、Node `Operator Shell` 的只读监督与诊断闭环。
3. **下一批实现重点**:ask/explain、low-risk write、operational control 与 proposal-only typed commands 已落到 37 条;recovery contract 现在已经补到 spec 明列的六项：会带回 latest heartbeat state、recent session summary、active tasks、unresolved proposal artifacts、recent failed tasks/last_error、recent operator error events，以及 recent `operator_daily_summary` 的 compacted memory。现在不再只读 `summary_excerpt`：Node shell 恢复时会把 recent child-session branches 一起拉回、支持 recovered in-flight `evaluate_strategy_state` / `create_repair_proposal` / `create_paper_experiment` task 自动再进入，并把多日 daily summary 的 `major_user_instructions / important_repair_decisions / proposal_outcomes / strategy_state_changes / unresolved_issues / follow_up_recommendations` 折叠进 recovery intent。更进一步，recovery 现在不再硬编码只看两条 branch / 两条 summary，而是按更长时间窗抓取 recent child sessions，并在有上限的 intent budget 内持续折叠更多 branch 与 compacted-memory 线索，避免“只记住最近两条”或“上下文无限膨胀”这两头失真；如果恢复时没有更“新鲜”的 backlog/错误上下文，也会回收最近一次 heartbeat 的 `current_intent`，而不是直接退回空白 `monitoring system state`。这次又进一步把 **active branch** 本身变成 persisted truth：`agent_shell_heartbeats` 现在会带上 `active_branch_id / active_branch_kind / active_branch_title`，`SessionRuntime.restore()` 会优先恢复这条 authoritative branch，而不是再猜“最新 branch = 当前 branch”；Command Center 与 CLI 也开始直接显示这份状态。与此同时，Command Center 里当前选中的 session 现在也终于能被真正继续了：前端会把 `selectedSessionId` 带给 `/api/operator/command`，backend route 会把它透传给 `AgentShellRuntime.handle()`，runtime 也会复用这条 session 而不是偷偷新建一条无关 session，所以 child session 不再只是 inspect-only trace，而开始具备真实 multi-turn continuation。现在用户也终于能直接开 branch 了：backend 新增公开 `POST /api/operator/branches`，会创建五类 direct-child session、写 `branch_created` 事件、在 branch 内 seed `context_snapshot` + opening user message，并把新 branch 直接回写为 authoritative active branch；Command Center 也新增了 branch title + kind 控件，因此“persistent workspace / session tree / branching side-task session”不再只靠 operator loop 自动长出。现在这条能力又往 PI-style session tree 真正推进了一步：public branch create 不再强制 flatten 回 `operator-main`，而是支持 `parent_session_id`；active-branch 也接受整个 operator session tree 的 descendant session；`SessionRuntime.restore()` 与 `recoverState()` 也开始把 descendant branch detail 真正拉回，所以 nested review/diagnostic/strategy branch 不再只是 UI 上的树状视觉效果，而是成为真实可恢复的 operator state。现在同一轮 loop 内的新 task session 也终于不会再偷偷 flatten 回 main：`runOnce()` 会先同步 persisted active branch，再冻结本轮 parent session，因此 `run_market_scan` / repair decision / 其他 loop-created side-task session 会稳定继承这条 active branch，而不会在一个 heartbeat 内被新 child branch 反向漂移。输入区这次也终于接上了 **authoritative command preview**：backend 新增 `POST /api/operator/command-preview`，会在不执行命令的前提下返回 intent / classification / command metadata / parsed parameters / permission truth；而且这份 truth 现在不只看 operator ladder，也会把 worker/global safety 一起判定出来；Command Center composer 会直接显示 `Ready / Blocked / Query / Forbidden`、risk class、operator/worker 两层 required permission 与 parsed parameters，不再只有“发出去以后才知道会发生什么”。现在消息流本身也开始真正承载 **tool/command trace + result cards**：`/api/operator/command` 会把 `command / command_meta / risk_level / needs_confirmation / permitted / action` 一起写进 response message；Command Center 会对 structured `command` message 渲染 trace card，对 structured `response` message 渲染 result card，并明确区分 executed vs blocked，而不再把所有 operator 输出都扁平成普通文本。`data/agent_shell/...` 这层 inspectable working memory 继续深化：当前已接通 session JSONL、task Markdown、`create_diagnostic_todo` 生成的 todo Markdown，以及 extension sandbox 产出的 scratch Markdown、extension manifest JSON、以及落到 `data/reports/...` 的 typed report Markdown；底层也新增了 `review_report / diagnostic_report` artifact 工件，不再只有 SQLite/knowledge side effects 而没有可读报告。extension sandbox 现在已经不只是 draft/proposal：enabled extension 也真正进入运行面了。当前 `/api/operator/command` 与 `AgentShellRuntime` 都会先解析 slash command，再把已启用 extension 分发到对应 typed target command；Command Center 也会直接展示 enabled extensions，让 slash command 不再只是隐藏能力。backend 与 Node recommendation truth 现在都会显式给出 `failed_tasks_present / stuck_tasks_present / queued_tasks_present / queue_review_needed / market_scan_refresh_due / failed_agent_rerun_due / daily_review_refresh_due / pause_paper_strategy_due / system_health_report_refresh_due / trade_review_report_refresh_due / risk_review_report_refresh_due / agent_performance_report_refresh_due / strategy_design_due / risk_critique_due`；backend `/api/risk/summary` 现在还会显式给出 `needs_operator_observe_only_symbols`，而 backend 与 Node recommendation truth 会进一步合成为 `symbol_observe_only_due`；`TaskRuntime` 也开始持有真正的 queued backlog，并会优先提升 higher-priority queued task，而不是只有一个 running task 壳。除了第一层阈值信号之外，Node `OperatorLoop` 现在已经把 spec 里的前两层 cadence 真正拆开了：每 10 秒仍即时读取 gateway status / health 与 heartbeat；每 30 秒读取 session/task/command 真值；每 **5 分钟** 才刷新 dreaming/review/strategy/risk/agent-runs 这类 subsystem review 数据，不再每个 10 秒 heartbeat 都全量重扫。当前 `queue_review_needed` 也已经不再只有一档：`attention` 会在 queued backlog 累积到 3 条或 oldest age 约 15 分钟时触发，并建议约 300 秒 review cadence；`urgent` 会在 queued backlog 累积到 5 条、oldest age 约 30 分钟，或 high-priority queued task 老化到约 10 分钟时触发，并建议约 60 秒 review cadence。15–60 分钟层现在已经开始有十一条真实接线：一条是 `market_observer` 最近一次活动缺失或老化到约 45 分钟时，backend 与 Node recommendation truth 会产出 `market_scan_refresh_due`，而 `operator-shell` 会在 `PaperAuto+` 下把它物化成 `run_market_scan` task，创建 `command_session` child session，执行 typed `run_market_scan`，再把 result/event/task completion 写回；一条是某个 worker 最近一次 failed/error/completed_with_errors run 已经悬了约 15 分钟、且没有更新的恢复 run、同时还没进入 repeated-failure prompt proposal 路径时，会产出 `failed_agent_rerun_due`，并在 `PaperAuto+` 下物化为 `rerun_failed_agent_task` command-session task；还有一条是当天 UTC 还没有 `daily_review`、但近 12 小时已有实际 agent activity 时，会产出 `daily_review_refresh_due`，并在 `Research+` 下物化为 `create_daily_review` review-session task，写一份确定性的 non-critical operator summary report。这次又补上两条真正的 specialist delegation：当 recent research note / strategy hypothesis 输入存在、但当前没有 active paper strategy，且没有 recent `strategy_designer` run 时，会产出 `strategy_design_due`，并在 `PaperAuto+` 下物化为 `run_strategy_design` specialist task，委托 `strategy_designer` 生成 fresh paper strategy；当 recent risk rejections 存在且没有 fresh `risk_critic` run 时，会产出 `risk_critique_due`，并在 `Research+` 下物化为 `run_risk_critique` specialist task，委托 `risk_critic` 写新的 risk lesson note。risk-governance operational-control 也已经接上：当最近 24 小时同一 symbol 连续命中至少两次 paper-side risk rejection、且将其从 `allowed/tradable` universe 移除不会把 universe 清空时，backend `/api/risk/summary` 会产出 `needs_operator_observe_only_symbols`，backend 与 Node recommendation truth 会进一步产出 `symbol_observe_only_due`，并在 `PaperAuto+` 下物化为 `set_symbol_observe_only` command-session task，把 symbol 收回到 observe-only。strategy-governance operational-control 也已经接上：当 `paper_running / paper_passed` strategy 已被标为 `under_review` 但尚未 `paper_trading_paused` 时，backend 与 Node recommendation truth 会产出 `pause_paper_strategy_due`，并在 `PaperAuto+` 下物化为 `pause_paper_strategy` command-session task，调用 typed pause 命令暂停 paper trading 并收口该 strategy 挂着的 open opportunities。现在 richer 的专门报告也已经接线：gateway health 出现真实 issues 且最近无 fresh artifact 时，会产出 `system_health_report_refresh_due`，并由 `create_diagnostic_report` 落出 system-health Markdown + `diagnostic_report` artifact；recent closed positions / risk rejections / recent agent activity 满足条件且最近无 fresh report artifact 时，会分别产出 `trade_review_report_refresh_due / risk_review_report_refresh_due / agent_performance_report_refresh_due`，并在 `Research+` 下物化为 `create_review_report` task，落出 trade/risk/agent-performance Markdown + `review_report` artifacts。与此同时，task materialization 也开始按 operator permission mode 过滤，高权限 recommendation 在 `ReadOnly/Research` 下不会再被提前物化成注定卡死的 task；现在就连 Research 权限的 report refresh，也不会再在 `ReadOnly` 下误生成 backlog。specialist delegation 现在已经有五条真实链路：`operator-shell` 会在 dreaming 缺席时创建 `run_dreaming` task 委托 `dreaming_agent`，会在 recent closed strategy 缺 post-trade review 覆盖时创建 `generate_review` task 委托 `post_trade_reviewer`，会在 flagged strategy 缺 fresh evaluation 时创建 `evaluate_strategy_state` task，落到 `strategy_session` child session，执行 `get_strategy_lifecycle` 并写回 `strategy_evaluation` artifact；也会在 recent research/hypothesis 输入存在但无 active paper strategy 时创建 `run_strategy_design` task 委托 `strategy_designer`，并在 recent risk rejections 缺 fresh critique 时创建 `run_risk_critique` task 委托 `risk_critic`。strategy proposal ladder 现在已扩成三段、proposal-only specialist 现在已接通八条真实路径：`paper_running -> paper_passed` 与 `paper_passed -> live_candidate` 都会走 `propose_strategy_promotion`；`live_candidate -> live_limited` 会走 `propose_live_limited_enablement`；recent failed agent runs 重复堆积时，会生成 `agent_prompt_change_proposal_due` recommendation，物化 `proposal_only` repair decision，并执行 typed `propose_prompt_change`；recent manual runs 重复指向同一 agent 时，会生成 `agent_schedule_change_proposal_due` recommendation，物化 `proposal_only` repair decision，并执行 typed `propose_schedule_change`；recent rejected `risk_decisions` 在最近 24h 内重复命中同一可调整风险边界、且尚无 recent `risk_boundary_change_proposal` 时，会由 backend `/api/risk/summary` 产出 `risk_boundary_change_proposal_due` recommendation，物化 `proposal_only` repair decision，并执行 typed `propose_risk_boundary_change`；recent denied `command_session` commands 在最近 24h 内对同一命令重复失败、且尚无 recent `command_center_behavior_proposal` 时，会由 command audit 真值产出 `command_center_behavior_change_proposal_due` recommendation，物化 `proposal_only` repair decision，并执行 typed `propose_command_center_behavior_change`；extension sandbox 方向则已有 `draft_extension_manifest / test_extension_sandbox / propose_extension_enablement` 三段 typed path，外加 enabled slash dispatch，能够落 manifest、跑 sandbox-only validation、生成 `extension_enablement_proposal`，并在人工启用后真正进入 command runtime。与此同时 Python truth layer 也补上了真正可查询的 context model 与完整 loopback API：backend 现有 `/api/operator/internal/context/query`、`/api/operator/internal/commands/execute`、`/api/operator/internal/decisions`、`/api/operator/internal/repair-decisions`、`/api/operator/internal/command-intents`，以及新的 `/api/operator/extensions` inspect/enable 路径；Node shell 会把 context snapshot 写进 child session，Command Center 也已经切到 **SSE-first** operator event feed，并支持跨 session 的 full-history command search。现在连 operator heartbeat 的 mode state machine 也不再把 fresh short-lived task 一律抹回 `Observing`：fresh `create_repair_proposal` 会显式报 `Repairing`，fresh `run_market_scan / rerun_failed_agent_task / run_strategy_design / evaluate_strategy_state / set_symbol_observe_only / create_paper_experiment / pause_paper_strategy` 会显式报 `Trading`，fresh `run_risk_critique` 与其他 review/dreaming task 也会保留 `Reviewing / Dreaming`；但 recovered in-flight task 仍然只会回到 `Observing / Paused / Degraded`，不会违反 crash recovery 不直接进 `Trading` 的红线。现在 `create_paper_experiment` 这条 task 也不再只是名义上的 task type：recovered/manual in-flight `create_paper_experiment` 会真正进入 command-session execution path，执行 typed command 并把 strategy/result/task completion 写回。下一批更深的缺口现在主要收敛到：更广的 scheduled scan/review opportunities，以及更深的 PI-style multi-turn branch runtime。
4. **合并边界不变**:即使 `worktree-dev` 上实现继续推进,合并 `master` / 上线 / 宣称完成仍受 Phase 0 门全绿 + 显式授权约束。

---

## 历史交接

详细的逐次变更记录见 `docs/AI_HANDOFF.md` 与 `docs/DEVLOG.md`。本文件只保留
**当前阶段意图**;已完成的诊断/修复叙事不在此堆积。
