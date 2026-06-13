# AI Handoff

> **本文件是 append-only 变更史**(记录"做了什么改动 + 根因",机器无关)。
> 其中任何"当时机器状态"(gateway 在跑 / 观察期第 N 小时 / 凭证已存 / demo 单已落)
> 都是**写入时刻的快照,不代表当前**。要知道当前真实状态,运行 **`aletheia doctor`**。
> 双机意图/事实分离的金标准见 `docs/DEV_PROTOCOL.md`。

## Last Updated

2026-06-13 (Persistence / Sync / Recovery 子系统完整实现:EventStore, ArtifactStore, Gateway
StateMachine + StateStore + Lock + Session + Recovery + Reconciliation, Agent Checkpoints,
Export/Import, Backup/Restore, Lesson/Skill/PolicyVersion stores, API endpoints. 15 new `ps_*`
tables, 18 new modules, `/api/persistence/*` route family. No existing tests broken.)

## Recent Change (Persistence / Sync / Recovery — Full Implementation — 2026-06-13)

**1. 子系统概述**:
完整实现了 Aletheia 持久化/同步/恢复子系统,覆盖交易状态安全恢复、事件溯源、
内容寻址存储、网关状态机、领导锁、对账协议、Agent 检查点、导出/导入、备份/恢复、
经验循环持久化(Lesson/Skill/PolicyVersion)。

**2. 新增模块(18 个文件)**:
- `aletheia/storage/hashing.py` — canonical JSON + SHA-256
- `aletheia/storage/event_store.py` — append-only 双写(DB + JSONL)
- `aletheia/storage/artifact_store.py` — 内容寻址文件存储
- `aletheia/gateway/state_machine.py` — 7 状态网关 FSM
- `aletheia/gateway/client_order_id.py` — 确定性 order ID 生成
- `aletheia/gateway/state_store.py` — order/fill/position 持久层
- `aletheia/gateway/locks.py` — lease-based 领导锁
- `aletheia/gateway/session.py` — gateway session 跟踪
- `aletheia/gateway/exchange_adapter.py` — 交易所适配器协议
- `aletheia/gateway/reconciliation.py` — fill/order/position 对账
- `aletheia/gateway/recovery.py` — 启动恢复协议
- `aletheia/agents/checkpoints.py` — Agent 运行检查点
- `aletheia/sync/exporter.py` — 多模式导出
- `aletheia/sync/importer.py` — dry-run + merge 导入
- `aletheia/sync/conflicts.py` — 策略冲突检测
- `aletheia/backup/service.py` + `verify.py` + `restore.py` — 备份/恢复
- `aletheia/learning/lessons.py` + `skills.py` — 经验循环
- `aletheia/policies/store.py` + `diff.py` — 版本化策略管理
- `aletheia/server/routes/persistence.py` — API 路由

**3. Schema 变更(additive overlay)**:
15 张 `ps_*` 前缀新表追加到 `migrations.py`,不修改任何已有表。

**4. 安全不变量**:
- 密钥永不导出(`_SECRET_PATTERNS` 排除)
- Skill `FORBIDDEN_LAYERS` 阻止触碰执行/风控/凭证层
- Policy 版本不可变(只能追加新版本)
- 网关 SAFE_FREEZE 在对账异常时阻止交易

**5. 已有测试未破坏**,新系统等待专用测试覆盖。

## (历史) 2026-06-12 (账户级仓位发现 + 纳管 Phase 1-2 落地)

## (历史原文) Recent Change (账户级仓位发现 + 纳管:Phase 1-2 — 2026-06-12)

**1. 背景与立场**:
- 所有者决策:Aletheia 接管账户后,全账户仓位(含系统外开出的)统一交由 AI 管理("接管即纳管")。
- 这把 ROADMAP Phase 6 的 live-read reconciliation / position-mismatch 思想提前、以 demo 优先落地。

**2. 顺带修的两个真实 bug**:
- `total_value_usdt` 之前是持仓名义市值(口径错,随每笔 fill 跳变);现在交易所缓存可用时
  用真实账户权益覆盖,与同一 summary 其它字段一致(`portfolio/pnl.py`)。
- `client.get_positions()` 读的入场价字段 `averageOpenPrice` 在 Bitget v2 mix all-position
  里**不存在** → 一直静默为 0;真实字段是 `openPriceAvg`。现已读对,并补 `markPrice`/`leverage`。
  无真实入场价就无法合成诚实因果链,这是纳管前置。

**3. Phase 1 发现(只读)**:`portfolio/position_reconciler.py` `classify_positions()` 把交易所
缓存仓位与本地 open 仓位按 `(canonical symbol, direction)` 配对(hedge 模式按"对"匹配),分类
managed/external/orphaned;`GET /api/positions/reconciliation`;`compute_portfolio` 增
`exchange_external_summary`;前端 `TradesPositions.tsx` 新增 Account Reconciliation 卡片。

**4. Phase 2 纳管(改动受控)**:`source` 列(native/adopted)加到 positions/orders/trade_proposals;
`adopt_position()` 单事务合成 proposal→order(filled)→position 完整 `source=adopted` 链,入场价
取自交易所,零值拒绝纳管;`execution_mode` 按账户模式映射(demo→bitget_demo / live→
bitget_live_limited),使 `position_manager` 自动管 demo、跳过 live。

**5. 治理(trust §1.1 修订)**:旧"外部仓位永不入表"→"可入表但必带诚实合成链";红线测试拆为
`test_sync_alone_never_writes_local_positions` + `test_adopted_positions_carry_synthetic_chain`;
DECISIONS.md 记两条(含 demo 自动纳管**默认开启**的行为警告)。

**6. Phase 3-4 设计在册未实现**:P3 定时 reconcile 任务(demo 自动纳管默认开启)+ operator
命令(live 需人工确认)+ 手动触发;P4 live 人工确认平仓 + Full 模式接缝(默认关闭)。

**7. 验证**:`uv run pytest -q` → **777 passed**;前端 `tsc` 干净,bundle 重建。**尚未 commit**。

## (历史) 2026-06-11 (Operator runtime 已补到 enabled extension slash dispatch + extension sandbox 第一层 + inspectable working-memory files + budgeted recovery folding + recovery-contract recent-error/heartbeat-intent fallback + staged queue review cadence + explicit 5-minute subsystem review tier + `market_scan_refresh_due`/`run_market_scan` + `failed_agent_rerun_due`/`rerun_failed_agent_task` + `daily_review_refresh_due`/`create_daily_review` + `portfolio_snapshot_refresh_due`/`run_portfolio_snapshot`/`portfolio_agent` + `pause_paper_strategy_due`/`pause_paper_strategy` + `strategy_design_due`/`run_strategy_design` + `risk_critique_due`/`run_risk_critique` + `extension_enablement_proposal_due`/`propose_extension_enablement` + permission-aware task materialization + persisted active-branch heartbeat/recovery truth + active-branch-frozen session parent inheritance + active-branch-outside-fetch-window detail recovery + session-targeted multi-turn command continuation + session-kind-scoped continuation/preview context + persisted branch context/result trace folded into continuation summaries + parent-branch trace folded into nested continuation summaries + nested session-tree branch create/select + descendant-branch recovery folding + user-driven branch workspace create/select + branch bootstrap context seeding + strategy proposal ladder 扩到 `paper_passed -> live_candidate` / `live_candidate -> live_limited` + authoritative command preview + structured result cards / command trace + short-lived operator mode truth(`Repairing/Trading/Reviewing/Dreaming` 不再在同一 loop 内被统一抹回 `Observing`，同时保持 recovery 不直接进 `Trading`)；文档同步到当前实现边界)

## Recent Change (Authoritative active branch now recovers its own detail even outside the default recent-branch window — 2026-06-11 第四十六段)

**1. 这次补的是 persisted active branch recovery 的一个真实漏口**：
- 之前 `active_branch_id` 虽然已经是 authoritative truth
- 但 `recoverState()` 只会抓固定窗口内的 recent branch detail
- 如果当前 active branch 比那批 recent branch 更旧,恢复回来就只剩 branch title / summary 壳

**2. 现在恢复会优先保住当前 active branch 的 detail**：
- `OperatorLoop.recoverState()` 仍保持有限的 branch-detail 抓取窗口
- 但会先把 authoritative `active_branch_id` 提前到 detail fetch 优先级最前
- 所以当前 branch 即使掉出默认 recent-window,也会把它自己的 assistant/result/artifact 语义拉回

**3. 这次修的是 PI-style branch runtime 的一致性,不是文案润色**：
- 当前 active branch 代表“正在工作的那条分支”
- 如果 crash recovery 只记住它的标题,而忘了它自己的最近结论,那就不是真正的 branch continuation
- 现在这条边界至少已经收住:active branch 不会因为 recency slicing 被恢复成空壳

**4. 当前验证结果（本轮后）**：
- `operator-shell npm test -- --test-name-pattern "authoritative active branch detail even when it falls outside the recent branch detail window"` → PASS
- `operator-shell npm test` → PASS（76 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Selected child-session continuation now carries session-kind-specific context instead of a generic summary — 2026-06-11 第四十七段)

**1. 这次补的是 selected-session continuation 里的一个真实 runtime 失真**：
- 之前 child session 虽然已经可以被真正继续
- 但 Python compatibility runtime 与 `/api/operator/command-preview` 仍然一律拼通用 `system / market / agents / trading` 摘要
- 结果就是 `diagnostic / dreaming / strategy / review` branch 虽然“写回到对的 session”，却没有真的带着 branch 自己的 context lane 继续

**2. 现在 continuation 与 preview 都开始按 session kind 选 context profile**：
- `strategy_session / review_session` 会吃 `trading_context`
- `diagnostic_session` 会吃 `repair_context`
- `dreaming_session` 会吃 `knowledge_context`
- 这条选择同时进入 `AgentShellRuntime.handle()` 与 `/api/operator/command-preview`

**3. 这次修的是 PI-style multi-turn branch runtime 的第一层一致性**：
- preview 和实际执行现在不再是两套上下文
- selected-session continuation 也不再只是“复用 session_id”
- branch continuation 开始真正尊重 branch 自己的上下文类型

**4. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_agent_shell.py -q -k "session_kind_context_profile_for_selected_branch"` → PASS
- `uv run pytest tests/test_operator_routes.py -q -k "selected_session_kind_context_profile"` → PASS
- `uv run pytest tests/test_agent_shell.py tests/test_operator_routes.py tests/test_operator_command_route.py -q` → `113 passed`

## Recent Change (Selected child-session continuation now also reads persisted branch context/result trace — 2026-06-11 第四十八段)

**1. 这次补的是 branch bootstrap context 仍然停在“只给 UI 看”的真缺口**：
- 之前 branch 创建时已经会 seed `context` message
- branch 内上一轮 assistant/result 也已经会被持久化
- 但 Python runtime 与 `/api/operator/command-preview` 做下一轮分类时，并不会把这些已存下来的 branch 线索读回来

**2. 现在 continuation summary 会 deterministic 地吸收这批 session trace**：
- 会优先读取该 session 最近的 `context` message
- 也会折入最近一条 assistant/result 结论
- 如有需要，还会带回最近 user note
- 这条拼接同时进入 `AgentShellRuntime.handle()` 与 `/api/operator/command-preview`

**3. 这次修的是更深一层的 PI-style multi-turn branch continuity**：
- 上一轮已经补了“按 session kind 选 profile”
- 这轮继续补“把 branch 已记录的上下文与最近结论真正带入下一轮 summary”
- 所以 branch bootstrap context 不再只是 inspector 可见，而开始成为 continuation 的真实输入

**4. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_agent_shell.py -q -k "recent_session_context_and_trace_in_branch_summary"` → PASS
- `uv run pytest tests/test_operator_routes.py -q -k "includes_recent_session_context_and_trace"` → PASS
- `uv run pytest tests/test_agent_shell.py tests/test_operator_routes.py tests/test_operator_command_route.py -q` → `115 passed`

## Recent Change (Nested child-session continuation now also inherits parent-branch trace — 2026-06-11 第四十九段)

**1. 这次补的是 nested session tree 里的上游语义继承缺口**：
- 上一轮已经补了“本 session 的 context/result trace 会进入 continuation summary”
- 但如果当前 branch 本身很薄、真正的工作背景在 parent strategy/diagnostic branch，上下文仍然会断层
- 结果就是 nested branch 虽然挂在对的树上，实际 continuation 还是缺上游语义

**2. 现在 runtime / preview 会沿 `parent_session_id` 折回有限层数 ancestor trace**：
- 会顺着 parent chain 读取上游 session
- 提取 parent branch 最近的 context/result 摘要
- 然后把这批 ancestor trace 折进当前 nested branch 的 continuation summary

**3. 这次修的是更深一层的 PI-style branch orchestration**：
- nested branch 不再只依赖“自己这一层”的 message
- parent strategy/root branch 的最近结论开始真正传递到 child review/diagnostic continuation
- session tree 的层级关系因此不再只是 UI 结构,而开始成为 runtime 语义

**4. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_agent_shell.py -q -k "parent_branch_trace_for_nested_session_summary"` → PASS
- `uv run pytest tests/test_operator_routes.py -q -k "parent_branch_trace_for_nested_session"` → PASS
- `uv run pytest tests/test_agent_shell.py tests/test_operator_routes.py tests/test_operator_command_route.py -q` → `117 passed`

## Recent Change (Specialized 1–5 minute health diagnostics now land as dedicated diagnostic reports — 2026-06-11 第五十段)

**1. 这次补掉的是 1–5 分钟 health review 仍然只剩一个总括 system-health 报告的缺口**：
- backend `build_recommendations()` 与 Node `RecommendationEngine` 现在会继续把 health issue 拆成三条专门 truth：
  - `worker_health_review_due`
  - `model_runtime_health_review_due`
  - `data_source_health_review_due`
- 触发条件直接绑定现有真实信号，而不是新造状态：
  - `agent_failure_ratio:*`
  - `llm_unavailable:*` / `code_updated_pending_restart:*`
  - `market_data_stale:*`

**2. 这三条 review 现在都是完整闭环，而不是 recommendation-only**：
- `create_diagnostic_report` 已支持 `worker_health / model_runtime_health / data_source_health` 三种 report type
- `OperatorLoop` 会在 `Research+` 下把它们物化成 typed `create_diagnostic_report` task
- 会创建 dedicated diagnostic session、写专门 Markdown、落 `diagnostic_report` artifact，并把 result/task completion 回写到 operator truth layer

**3. 这次边界要说准**：
- 已接通：
  - 1–5 分钟层的 specialized health diagnostics
  - specialized report type → typed command → artifact → diagnostic session
- 仍未接通：
  - 更广的 scheduled scan/review opportunities
  - 更深的 PI-style session runtime

**4. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_events.py tests/test_agent_shell.py tests/test_operator_routes.py tests/test_operator_command_route.py -q` → `154 passed`
- `operator-shell npm test` → PASS（80 tests）
- `operator-shell npm run build` → PASS

## Historical Note (Phase 2A planning folded back into ROADMAP — 2026-06-11)

- `phase-2a` 不再保留为单独的规划分支/文档面。
- `docs/ROADMAP.md` 现为 Phase 2A（structured executable strategy signals）的权威规划源。

## Recent Change (Sandbox-passed extensions now turn into real enablement proposals — 2026-06-11 第四十五段)

**1. 这次补掉的是 extension sandbox 还停在“手动三段命令”的真缺口**：
- `draft_extension_manifest / test_extension_sandbox / propose_extension_enablement` 这三条 typed command 之前都已经存在
- 但 operator runtime 并不会主动发现“某个 extension 已经 sandbox 通过、现在该进入 proposal”
- 结果就是 extension sandbox 虽然能手动跑通，但没有 recommendation truth，也没有 proposal-only materialization

**2. 现在 backend 与 Node recommendation truth 都会显式识别这类机会**：
- 只要 extension registry 里存在 `sandbox_passed` extension
- 且它还没有 enablement proposal artifact
- 就会产出 `extension_enablement_proposal_due`
- recommendation metadata 会带回 `extension_id / extension_name / slash_command / target_command / latest_sandbox_run_id`

**3. 这条 recommendation 现在已经接成真实 proposal-only 链路**：
- `RepairEngine` 会把它转成 `create_repair_proposal` decision
- `operator-shell` 会执行 typed `propose_extension_enablement`
- 最终落出 `extension_enablement_proposal` artifact，并写 `proposal_created` 事件，而不是直接启用 extension

**4. 这条链路的边界是收敛的**：
- 它不会绕过人类审批直接 enable extension
- 也不改变 worker/trading permission、execution gateway 或 live 语义
- 它只是把“sandbox 已通过、值得进入审批”这一步拉进 operator runtime 的真实闭环

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_events.py -q -k "extension_enablement_proposal_due"` → PASS
- `uv run pytest tests/test_operator_events.py tests/test_operator_routes.py tests/test_operator_command_route.py tests/test_agent_shell.py -q` → `142 passed`
- `operator-shell: npm test -- --test-name-pattern "extension enablement proposals"` → PASS
- `operator-shell: npm test` → `75 passed`
- `operator-shell: npm run build` → PASS

## Recent Change (Open positions without a fresh portfolio snapshot now turn into a real specialist review task — 2026-06-11 第四十四段)

**1. 这次补掉的是一个 recommendation 已经该存在、但 runtime 之前根本不会做事的真缺口**：
- operator loop 之前已经能看到 market scan、daily review、risk critique、strategy design 这些 review cadence
- 但对 open positions 来说，portfolio 视图是否 stale 还没有进入 recommendation truth，更没有 task materialization 或 specialist delegation

**2. 现在 backend 与 Node recommendation truth 都会显式判断 portfolio snapshot 是否过期**：
- 只要存在 open positions，且没有 recent portfolio snapshot，或者最新 snapshot 已经老化到约 15 分钟
- 就会产出 `portfolio_snapshot_refresh_due`
- recommendation metadata 也会带回 `open_positions_count / last_snapshot_id / stale_minutes`

**3. 这条 recommendation 现在已经接成真实运行链路**：
- `TaskRuntime` 会把它分配给 `portfolio_agent`
- `OperatorLoop` 会在 `Research+` 下把它物化为 `run_portfolio_snapshot`
- 然后创建 `review_session` child session，写入 `portfolio_snapshot_started / portfolio_snapshot_completed` 事件，并完成 task

**4. 这条链路的边界是收敛的**：
- 它只刷新 operator 需要的 portfolio 观察视图
- 不触碰 execution gateway、risk approval 或 live trading 语义
- heartbeat mode 也会把这类 fresh task 归到 `Reviewing`，而不是误报成 `Trading`

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_events.py -q -k "portfolio_snapshot_refresh_due"` → PASS
- `operator-shell: npm test -- --test-name-pattern "portfolio snapshot refresh"` → PASS
- `operator-shell: npm run build` → PASS

## Recent Change (Under-review paper strategies now materialize into real pause control, and nested active branches no longer flatten new loop sessions — 2026-06-11 第四十三段)

**1. 这次补了两条仍然会让 runtime“看起来对、实际不对”的小缺口**：
- 一条是 nested active branch 明明已经持久化了，但同一轮 operator loop 新开的 task session 还是会错误地 parent 回 `operator-main`
- 另一条是 strategy governance 虽然已经有 `under_review` / `paper_trading_paused` 真字段和 typed `pause_paper_strategy` 命令，但 recommendation truth 还不会把“under review 但仍在跑 paper”变成真实 operational-control task

**2. parent 继承这次已经收口成 frozen-per-run truth**：
- `OperatorLoop.runOnce()` 现在会先同步 persisted active branch，再冻结本轮的 parent session
- 所以本轮内新开的 `run_market_scan` / repair decision / 其他 loop-created side-task session 都会继承当时的 active branch
- 同时又不会在同一轮里被新长出来的 child branch 反过来污染 parent，避免 session tree 在一个 loop 内不断漂移

**3. `pause_paper_strategy_due` 现在也接成真实链路了**：
- backend recommendation truth 与 Node `RecommendationEngine` 都会识别：`paper_running / paper_passed` strategy 已被标成 `under_review`，但还没 `paper_trading_paused`
- 在 `PaperAuto+` 下，`operator-shell` 会把它物化成 `pause_paper_strategy` task
- 然后创建 `command_session` child session，执行 typed `pause_paper_strategy`，写回 result / event / task completion

**4. 这条新链路的效果是确定性的**：
- 不会直接改 live 状态
- 只会暂停该 strategy 的 paper trading，并把仍然挂着该 strategy 的 open opportunities 收口为 reviewed
- recommendation 一旦转成 paused truth，就会自然消失，不会继续重复提示同一件事

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_events.py -q` → `26 passed`
- `operator-shell: npm test` → `68 passed`
- `operator-shell: npm run build` → PASS

## Recent Change (Nested session-tree branches now work as real operator runtime state — 2026-06-11 第四十二段)

**1. 这次补掉的是“UI 看起来像 session tree，但 runtime 其实还是单层”的真缺口**：
- Command Center 之前虽然能递归显示 session tree
- 但 public branch create 固定只会把新 branch 挂到 `operator-main`
- active-branch route 也只接受 `operator-main` 的 direct child
- `operator-shell recoverState()` 读回 branch context 时也只抓 direct child detail

**2. 现在 nested branch 已经进入真实运行面**：
- `POST /api/operator/branches` 新增 `parent_session_id`
- 用户从当前选中的 session 再开 branch 时，新 branch 会真正挂到这个 parent 下，而不是被悄悄扁平化回 main
- `POST /api/operator/active-branch` 现在接受整个 operator session tree 内的 descendant session，不再只接受 direct child

**3. Node shell 的恢复语义也一起补上了**：
- `SessionRuntime.restore()` 不再只恢复 main 下第一层 branch
- `recoverState()` 现在会把整个 descendant session tree 的 recent branch detail 一起纳入 recovery folding
- 所以 nested review/diagnostic/strategy branch 的真实总结，终于能重新进入 heartbeat `current_intent`

**4. 前端也接上了这层 parent truth**：
- Command Center 创建 branch 时，现在会把当前 `selectedSessionId` 作为 `parent_session_id`
- UI 提示文案也从“开 side-task session”改成了“挂到当前选中 session 下的 side-task session”

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_routes.py -q` → `24 passed`
- `operator-shell: npm test` → `65 passed`
- `operator-shell: npm run build` → PASS
- `frontend: npm run build` → PASS

## Recent Change (Operator heartbeat mode now preserves real short-lived task modes — 2026-06-11 第四十一段)

**1. `OperatorLoop` 的 mode state 这次终于不再被短任务吞掉了**：
- 之前 fresh task 即使真的在这一轮 loop 里执行成功，heartbeat 也常常在收尾阶段被统一擦回 `Observing`
- 结果就是 `Repairing / Trading / Reviewing / Dreaming` 这些 mode 很多时候根本不会稳定出现在 heartbeat 真值里

**2. 现在的规则更接近 spec 想要的 operator mode 语义**：
- fresh `create_repair_proposal` 会显式报 `Repairing`
- fresh `run_market_scan / rerun_failed_agent_task / evaluate_strategy_state / set_symbol_observe_only / create_paper_experiment / pause_paper_strategy` 会显式报 `Trading`
- fresh `create_daily_review / create_review_report / generate_review` 会保留 `Reviewing`
- fresh `run_dreaming` 会保留 `Dreaming`

**3. 恢复红线没有被破坏**：
- 这次不是简单地“只要 task type 是 trading 就报 Trading”
- recovered in-flight task 仍然会在恢复轮次里回到 `Observing / Paused / Degraded`
- 所以原本那条 crash recovery 永远不直接恢复进 `Trading` 的约束仍然成立

**4. 实现落点**：
- `operator-shell/src/operator_loop.ts` 新增 task-type -> mode 映射
- 同时把“fresh task 完成后的 mode 收口”和“recovered task 的 fail-safe 回落”显式拆开

**5. 当前验证结果（本轮后）**：
- `operator-shell: npm test` → `64 passed`
- `operator-shell: npm run build` → PASS

## Recent Change (Command Center now renders structured result cards from real operator message truth — 2026-06-11 第四十段)

**1. result cards 这次终于不是 UI 想象物了**：
- `/api/operator/command` 现在会把 structured response metadata 一起写进 assistant response message
- 包括 `command / command_meta / risk_level / needs_confirmation / permitted / action`
- 所以 Command Center 不再只能把 response 当一段普通文本塞进消息流

**2. 这条链接的是 backend 真值，不是前端本地推断**：
- direct natural-language operator command 的 response message 现在会明确记录是否被 permission gate 拦下
- side-effect command 若真的执行，还会把 structured `action` 一起落回 session
- internal typed command 原本已有 `command / permitted / result`，现在前端也开始统一消费这层结构

**3. Command Center 的消息流现在开始像真正的 operator workspace**：
- `command` message 会显示 trace card
- `response` message 会显示 result card
- 用户现在能在长消息流里直接看见“执行了什么 / 被什么权限拦下 / 是否需要 confirmation / 有什么 structured action”

**4. 当前边界要说准**：
- 已接通：
  - structured response metadata persistence
  - command trace card
  - result card
  - blocked vs executed visual distinction
- 仍未接通：
  - 更深的 multi-step approval choreography
  - 更丰富的 result artifact decomposition

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_routes.py -q` → `21 passed`
- `uv run pytest tests/test_operator_routes.py tests/test_operator_events.py -q` → `46 passed`
- `frontend npm run build` → PASS

## Recent Change (Command Center now shows authoritative command preview — 2026-06-11 第三十九段)

**1. 输入框现在不再是“发出去才知道会发生什么”**：
- backend 新增 `POST /api/operator/command-preview`
- 它会在 **不创建 session side effect、不执行 command、不写 audit** 的前提下，返回当前输入会被解释成什么
- preview 会显式给出 `intent / classification / command metadata / parsed parameters / current permission truth`

**2. 这次 preview 不是前端猜测，而是 backend authoritative truth**：
- 它复用现有 intent classifier、extension slash resolution 与双层 permission 检查
- 所以不只是 operator ladder，worker/global safety 也会在 preview 阶段被显式判定出来
- read/query、blocked command、forbidden input、slash extension 都会得到一致的 backend 判定
- Command Center 现在直接展示 `Ready / Blocked / Query / Forbidden` 等状态，而不是等命令真正发出后才暴露权限问题

**3. UI 现在会把最关键的执行前信息直接摊开**：
- typed command 名称
- risk class
- 当前 operator permission vs required permission
- proposal-only / confirmation 标志
- 已解析 parameters

**4. 当前边界要说准**：
- 已接通：
  - authoritative command preview route
  - Command Center composer preview card
  - permission denial surfaced before execution
- 仍未接通：
  - 更深的 multi-step preview / approval choreography
  - richer command trace/result-card decomposition

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_routes.py -q` → `19 passed`
- `uv run pytest tests/test_operator_routes.py tests/test_operator_events.py -q` → `44 passed`
- `frontend npm run build` → PASS

## Recent Change (Strategy proposal ladder now reaches live_candidate + live_limited, and user-created branches seed bootstrap context — 2026-06-11 第三十八段)

**1. strategy proposal ladder 不再只停在 `paper_running -> paper_passed`**：
- backend `/api/strategies/summary` 现在会区分 promotion target，而不是把所有 proposal 都混成一类
- `paper_running -> paper_passed` 与 `paper_passed -> live_candidate` 都会通过 typed `propose_strategy_promotion` 落真实 proposal artifact
- `live_candidate -> live_limited` 也已接通 recommendation truth、repair-decision materialization 与 typed `propose_live_limited_enablement`

**2. 这次补上的是真实运行链，而不是只多了 command 名字**：
- backend recommendation truth 与 Node recommendation truth 现在都会显式产出相应 due signal
- `operator-shell` 会把这些 recommendation 物化成 `create_repair_proposal` task
- 最终写回 proposal artifact + `proposal_created` event，而不是偷偷改 strategy lifecycle state

**3. human-created branch 也不再只是“空 session”**：
- `POST /api/operator/branches` 现在创建 branch 后会立即 seed 一份 context snapshot
- 同时会为新 branch 预写 opening user message
- 所以新的 direct-child workspace 从创建时就带着可继续的上下文，而不是只有一条孤立 session 记录

**4. 当前边界要说准**：
- 已接通：
  - strategy proposal ladder 三段真实路径
  - live-limited enablement proposal materialization
  - user-created branch bootstrap context seeding
  - opening user message seed for new branch workspaces
- 仍未接通：
  - 更深的 PI-style branch orchestration
  - 更广的 scheduled scan/review opportunity 面

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_events.py -q` → `25 passed`
- `npm test -- operator_loop.test.ts` → `64 passed`
- `uv run pytest tests/test_agent_runtime.py -q` → `10 passed`
- `uv run pytest tests/test_agent_shell.py tests/test_operator_events.py tests/test_acceptance.py -q` → `120 passed`
- `uv run pytest -q` → `696 passed`
- `operator-shell npm run build` → PASS
- `frontend npm run build` → PASS

## Recent Change (Operator branch workspace is now user-driven — 2026-06-11 第三十七段)

**1. Command Center 现在终于能主动开 branch 了**：
- backend 新增公开 `POST /api/operator/branches`
- 它会创建 direct-child operator session，而不是要求 UI 走 internal route 或等 operator loop 自动长出 child session
- 当前支持五类 branch：`command_session / diagnostic_session / dreaming_session / strategy_session / review_session`

**2. 这次不是只“建了一条 session 记录”，而是把 branch 真值也一起补齐了**：
- 创建 branch 后会写 `branch_created` event
- 若 `activate=true`，会立即把新 session 回写成 authoritative active branch
- 所以同一个动作还会产生 `active_branch_selected` event，并更新 `/api/operator/status`

**3. 前端也已经接上这条能力**：
- Command Center 的 Sessions 卡片现在有显式 branch title + kind 控件
- 用户可以直接新建一个 persistent side-task session，并立刻切到这条 branch
- 这让“persistent workspace / session tree / branching side-task sessions”不再只是 inspect-only 或 loop-generated trace

**4. 当前边界要说准**：
- 已接通：
  - public branch creation
  - five explicit session kinds for human-created branches
  - branch-created event + authoritative active-branch selection
  - Command Center branch-create UI
- 仍未接通：
  - 更深的 PI-style branch seeding / richer branch bootstrap context
  - 更深的 PI-style multi-turn branch orchestration

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_routes.py -q` → `17 passed`
- `frontend npm run build` → PASS

## Recent Change (Operator selected-session continuation is now real — 2026-06-11 第三十六段)

**1. Command Center child sessions 现在不再只是 inspect-only 了**：
- 前端发送 operator command 时，现在会把当前 `selectedSessionId` 一起带给 `/api/operator/command`
- backend route 也开始显式接收 `session_id`
- 所以长文本输入现在可以继续当前选中的 child session，而不是永远只写回 `operator-main`

**2. Python compatibility runtime 这次也一起收口了**：
- `AgentShellRuntime.handle()` 现在支持显式 `session_id`
- 如果给的是已有 session，它会继续这条 session，写 command audit、更新 latest intent/response，而不是再偷偷新建一个无关的 command session
- 如果 session 不存在，则只做最小创建后继续，不会阻塞当前 turn

**3. 这次解决的是真实产品行为缺口**：
- 之前 UI 上虽然已经有 session tree / selected session / child-session inspector
- 但真正发消息时并没有进入选中的 branch，导致 child session 更像只读 trace
- 现在至少“选中哪个 session，就继续哪个 session”这条主交互已经成立，更接近 design 里 persistent session workspace 的要求

**4. 当前边界要说准**：
- 已接通：
  - selected-session command continuation
  - route-to-runtime `session_id` propagation
  - runtime session reuse instead of hidden new-session fanout
- 仍未接通：
  - UI 侧显式“新建 branch / 切 active branch”控制
  - active-branch selection 回写为 operator authoritative state
  - 更深的 PI-style multi-turn branch orchestration

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_command_route.py tests/test_agent_shell.py -q` → `87 passed`
- `frontend npm run build` → PASS

## Recent Change (Operator persisted active-branch heartbeat + recovery truth — 2026-06-11 第三十五段)

**1. branch/session runtime 这条线又往 PI 设计靠近了一步**：
- `agent_shell_heartbeats` 现在不再只记 main session / current intent / current task
- 还会把 `active_branch_id / active_branch_kind / active_branch_title` 一起持久化
- `/api/operator/status`、CLI `aletheia agent status`、Command Center operator state 现在都能看到这份真状态

**2. crash recovery 不再默认“最新 branch = 当前 branch”**：
- `SessionRuntime.restore()` 现在会优先使用 heartbeat 持久化下来的 active branch
- 如果当前 recovery fetch window 里没有这条 branch，也会根据 heartbeat 元数据补出一个最小 branch state，而不是直接丢失
- `OperatorLoop` 恢复后再发 heartbeat 时，会继续把这条 active branch 真值带回去，形成闭环

**3. 这次修的是设计上的一个具体失真点**：
- 之前 child sessions 虽然已经存在，branch summaries 也能折进 recovery intent
- 但“我现在到底停在哪个 branch 上”其实没有 authoritative truth，只是在恢复时猜最近的 branch
- 现在这层真值已经补上，Command Center 至少能诚实显示当前 active branch，Node shell 也不会在重启后把当前 branch 漂移成最近创建的另一条

**4. 当前边界要说准**：
- 已接通：
  - persisted active-branch heartbeat truth
  - recovery prefers persisted active branch over newest-branch guess
  - operator status / CLI / Command Center surfacing
- 仍未接通：
  - 更细粒度的“用户在 UI 中切换 active branch 后反写为新的 authoritative branch selection”
  - 更深的 PI-style multi-turn branch runtime

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_agent_shell.py tests/test_operator_store.py tests/test_operator_routes.py tests/test_operator_events.py tests/test_operator_compaction.py -q` → `125 passed`
- `operator-shell npm test -- operator_loop.test.ts` → PASS（61 tests）
- `operator-shell npm run build` → PASS
- `frontend npm run build` → PASS

## Recent Change (Operator specialist delegation expands to strategy_designer + risk_critic — 2026-06-11 第四十四段)

**1. 这次补的是两条真正的 specialist delegation 缺口，不是再多一条普通 report refresh**：
- `strategy_designer` 和 `risk_critic` 这两个 agent 之前都已经存在
- 但 operator runtime 里没有真正把它们接成 recommendation → task → delegated run 的闭环
- 结果就是 design package 里写着有 strategy architect / risk critic，实际 operator loop 只能调 dreaming、review、strategy evaluation 三条链

**2. `strategy_designer` 现在进入了真实 delegation 链**：
- backend 与 Node recommendation truth 现在都会检查：
  - recent `research_note` / `strategy_memory` 输入是否存在
  - 当前是否没有 active paper strategy
  - 最近 24h 是否没有 fresh `strategy_designer` run
- 满足时会产出 `strategy_design_due`
- `operator-shell` 会把它物化成 `run_strategy_design` task，委托 `strategy_designer`
- loop 会创建 `strategy_session` child session，写 delegation message，记录 `strategy_design_started / strategy_design_completed` event，并完成 task

**3. `risk_critic` 也接上了真实 delegation 链**：
- backend 与 Node recommendation truth 现在都会检查 recent rejected `risk_decisions`
- 若最近 6h 没有 fresh `risk_critic` run，就会产出 `risk_critique_due`
- `operator-shell` 会优先把它物化成 `run_risk_critique` task，委托 `risk_critic`
- 这条 specialist 现在会先于 non-critical `risk_review_report_refresh_due` 执行，避免 summary report 抢占更有价值的 critique pass

**4. operator mode / permission / task routing 也一起收口了**：
- `run_strategy_design` 现在按 `PaperAuto` 权限 gating，并在 heartbeat mode 上记为 `Trading`
- `run_risk_critique` 现在按 `Research` 权限 gating，并在 heartbeat mode 上记为 `Reviewing`
- `TaskRuntime` 也已经把它们分别路由到 `strategy_designer` / `risk_critic`

**5. 这轮验证**：
- `uv run pytest tests/test_operator_events.py -q -k "strategy_design_due or risk_critique_due"` → `2 passed`
- `uv run pytest tests/test_operator_events.py tests/test_operator_routes.py tests/test_agent_runtime.py tests/test_risk_routes.py -q` → `72 passed`
- `operator-shell npm test` → PASS（72 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator observe-only trigger + create-paper-experiment runtime 收口 — 2026-06-11 第三十五段)

**1. `set_symbol_observe_only` 现在终于不是孤立命令了**：
- backend `/api/risk/summary` 现在会基于 recent rejected `risk_decisions` 计算 `needs_operator_observe_only_symbols`
- 触发条件不是拍脑袋：
  - 最近 24h 同一 symbol 至少两次 `paper_*` execution mode rejection
  - 且把该 symbol 从 `risk.allowed_symbols` / `exchange.tradable_symbols` 拿掉不会把当前 universe 清空
- backend 与 Node recommendation truth 会把这层真值提升为 `symbol_observe_only_due`
- `operator-shell` 会在 `PaperAuto+` 下把它物化成 `set_symbol_observe_only` command-session task，并执行 typed command 回写 event/task completion

**2. `create_paper_experiment` 这条 task type 之前有个真实空洞，现在已经补上**：
- 之前它虽然被归类为 `PaperAuto` / `Trading` task type，但 `OperatorLoop` 没有实际 execution branch
- 结果就是 recovered/manual in-flight `create_paper_experiment` task 会挂着不动，只能停留在“名字被识别了”
- 现在 loop 已新增 `create_paper_experiment` command-session path：会开 child session、写 command/response message、执行 typed command，并把 `strategy_id / state / strategy_memory_id` 写回 task completion

**3. 这轮验证**：
- `uv run pytest tests/test_risk_routes.py tests/test_operator_events.py -q` → `36 passed`
- `uv run pytest tests/test_operator_routes.py tests/test_agent_runtime.py tests/test_risk_routes.py tests/test_operator_events.py -q` → `70 passed`
- `operator-shell npm test -- --test-name-pattern "operator loop re-enters a recovered in-flight create_paper_experiment task and executes the typed command"` → PASS
- `operator-shell npm test` → PASS（70 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator failed-agent rerun + daily-review refresh 接线 — 2026-06-11 第三十四段)

**1. 15–60 分钟层现在不再只有 market scan 一条线了**：
- 除了已经接通的 stale `market_observer` → `market_scan_refresh_due` → `run_market_scan`
- 现在又补了两条：
  - recent failed worker run → `failed_agent_rerun_due` → `rerun_failed_agent_task`
  - no same-day UTC daily review + recent activity → `daily_review_refresh_due` → `create_daily_review`

**2. failed-agent rerun 这条链路的真实边界**：
- backend 与 Node recommendation truth 现在都会检查 recent agent runs
- 如果某个 agent 最近一次 run 是 `failed / error / completed_with_errors`，约 15 分钟后仍无更新恢复 run，且还没进入 repeated-failure prompt proposal 路径，就会显式产出 `failed_agent_rerun_due`
- `operator-shell` 会在 `PaperAuto+` 下把它物化成 `rerun_failed_agent_task`，创建 `command_session` child session，执行 typed rerun，并把 result/event/task completion 写回

**3. non-critical report refresh 现在也终于不是空白了**：
- backend `dreaming/summary` 现在会把 recent `daily_review` 一起带回
- 如果当天 UTC 还没有 `daily_review`，但近 12 小时已经有真实 agent activity，backend 与 Node truth 都会产出 `daily_review_refresh_due`
- `operator-shell` 会在 `Research+` 下把它物化成 `create_daily_review` review-session task，并基于当前 gateway/health/review/strategy/risk/operator attention 真值写一份确定性的 operator summary report
- 现在 richer 的专门报告模板也已经接线：backend 与 Node truth 都会额外产出 `system_health_report_refresh_due / trade_review_report_refresh_due / risk_review_report_refresh_due / agent_performance_report_refresh_due`
- shell 会把这几类 recommendation 物化成 `create_diagnostic_report` 或 `create_review_report` task，落到 `diagnostic_session` / `review_session` child session，写 `data/reports/system_health|trade_review|risk|agent_performance/*.md`，并同时写 `diagnostic_report / review_report` artifacts

**4. 当前边界要说准**：
- 已接通：
  - stale market-scan refresh
  - one-off failed agent rerun
  - first non-critical report refresh path (`daily_review`)
  - dedicated non-critical report refresh paths for `system_health / trade_review / risk_review / agent_performance`
  - permission-aware task materialization now also covers Research-level report refresh
- 仍未接通：
  - 更广的 scheduled scan/review opportunities
  - 更深的 PI-style session runtime

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_events.py -q` → `23 passed`
- `operator-shell npm test` → PASS（60 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator stale market-scan refresh + permission-aware task materialization — 2026-06-11 第三十三段)

**1. 15–60 分钟层现在终于有了第一条真实 operational-control 接线**：
- backend `build_recommendations()` 与 Node `RecommendationEngine` 现在都会检查 `market_observer` 最近活动
- 如果最近一次 run 缺失，或最近活动已经老化到约 45 分钟，就会显式产出 `market_scan_refresh_due`
- 也就是说，“scheduled scan refresh” 不再只是 spec 里的概念词

**2. `run_market_scan` 现在已经是完整 task/runtime 链路**：
- `operator-shell` 会在 `PaperAuto+` 下把这类 recommendation 物化成 `run_market_scan` task
- 它会创建 `command_session` child session，附着 context snapshot，执行 typed `run_market_scan`
- 成功时写 `market_scan_refreshed` event、task completion summary 与 run metadata；失败时写 `market_scan_refresh_failed`

**3. 这次顺手补上的另一个关键边界是 permission-aware task sync**：
- recommendation 可以继续对用户可见
- 但 `TaskRuntime.sync()` 现在不会再在 `ReadOnly/Research` 下把 `create_repair_proposal` / `run_market_scan` / `rerun_failed_agent_task` 这类需要更高 operator permission 的动作提前物化成 task
- 结果就是：高权限动作不会再在低权限模式里生成一堆注定卡死的 queued backlog

**4. 当前边界要说准**：
- 已接通：
  - stale market-observer detection → `market_scan_refresh_due`
  - `run_market_scan` command-session execution path
  - permission-aware task materialization for higher-permission recommendations
- 仍未接通：
  - 其余 15–60 分钟层的 scheduled scan/review opportunities
  - non-critical report refresh
  - 更深的 PI-style session runtime

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_events.py -q` → `17 passed`
- `operator-shell npm test` → PASS（50 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator 1–5 minute subsystem review cadence — 2026-06-11 第三十二段)

**1. spec 里的 1–5 分钟层现在终于不是文档句子了**：
- `operator-shell` 现在不再每个 10 秒 heartbeat 都去拉 dreaming/review/strategy/risk/agent-runs
- 这些 subsystem review 输入已经被拆到单独的 **5 分钟 cadence** 缓存层
- 10 秒心跳仍然即时读取 gateway status / health，30 秒层仍然刷新 session/task/command 真值

**2. 当前真实分层**：
- 10 秒：
  - gateway status
  - gateway health
  - heartbeat write
- 30 秒：
  - operator session detail
  - operator command audit
  - operator task truth
- 5 分钟：
  - dreaming summary
  - review summary
  - strategy summary
  - risk summary
  - recent agent runs

**3. 这次补上的价值不是“优化”而是 runtime 语义对齐**：
- 之前这些 subsystem review 输入实际上仍然跟着 10 秒 loop 全量重扫
- 现在才真正符合 design 里的 supervision tiering，而不是“所有层都挂在 heartbeat 上”

**4. 当前剩余边界也要说准**：
- 已接通：
  - 10 秒 critical heartbeat tier
  - 30 秒 supervision scan tier
  - 5 分钟 subsystem review tier
- 仍未接通：
  - 15–60 分钟层的 scheduled scan/review opportunities
  - non-critical report refresh
  - 更深的 PI-style session runtime

**5. 当前验证结果（本轮后）**：
- `operator-shell npm test` → PASS（47 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator queue review staged cadence — 2026-06-11 第三十一段)

**1. `queue_review_needed` 现在不再只是“触发了”**：
- backend `build_recommendations()` 与 Node `RecommendationEngine` 现在都把 queue review 分成两档：
  - `attention`
  - `urgent`
- 也就是说 queue review 现在不再只有一个平面的 “yes/no” 信号，而开始带有 backlog 深度与复查频率语义

**2. 当前落地的判据**：
- `attention`：
  - queued backlog 约 3 条及以上
  - 或 oldest queued age 约 15 分钟及以上
  - 建议 review cadence = 300 秒
- `urgent`：
  - queued backlog 约 5 条及以上
  - 或 oldest queued age 约 30 分钟及以上
  - 或 high-priority queued task 老化到约 10 分钟及以上
  - 建议 review cadence = 60 秒

**3. 现在 recommendation truth 会显式带回**：
- `review_stage`
- `suggested_review_cadence_sec`
- `high_priority_queued_count`
- `oldest_high_priority_age_minutes`

**4. 当前边界要说准**：
- 已接通：
  - staged queue review truth (`attention` / `urgent`)
  - backlog depth + age + high-priority aging 三类 review evidence
  - Python backend 与 Node shell recommendation 口径对齐
- 仍未接通：
  - 更广的 1–5 分钟 / 15–60 分钟 supervision tiering
  - 更深的 PI-style session runtime

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_events.py -q` → `16 passed`
- `operator-shell npm test` → PASS（46 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator recovery contract recent-error fold-in + heartbeat intent fallback — 2026-06-11 第三十段)

**1. recovery contract 现在终于补到 spec 明列的“recent errors + latest heartbeat state”了**：
- `operator-shell recoverState()` 现在不再只看 failed task / unresolved proposal / branch summary / compacted memory
- 它还会把 recent operator error events 一起折回 recovery intent
- 如果恢复当下没有更“新鲜”的 backlog/错误上下文，也会回收 latest heartbeat 里的 `current_intent`

**2. 这次补上的真实行为边界**：
- 已接通：
  - recent `repair_failed / operator_degraded / heartbeat_stale / command_rejected / task_failed` 这类错误事件的 recovery fold-in
  - severity/事件类型驱动的 degraded recovery 判定
  - “没有 fresher backlog 时，回退到 latest heartbeat intent” 的恢复兜底
- 不再出现：
  - 错误明明刚发生过，但 restart 后恢复 intent 只剩空白 `monitoring system state`
  - 上一次 heartbeat 已经明确在跟进什么，但健康重启后完全丢失这个 operator intent

**3. 这让本地 spec 里的 recovery contract 基本项已经收口**：
- latest heartbeat state
- recent session summary
- active tasks
- unresolved proposals
- recent errors
- recover into `Observing` or `Degraded`, never directly `Trading`

**4. 当前剩余主缺口也要说准**：
- recovery contract 这条 spec 主线现在已经基本接通
- 下一批主要缺口进一步收窄到：首层 `queue_review_needed` 之上的更深 backlog policy / multi-stage review cadence

**5. 当前验证结果（本轮后）**：
- `operator-shell npm test` → PASS（45 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator enabled extension slash dispatch 接线 — 2026-06-11 第二十九段)

**1. extension 现在不再只是“被启用过”，而是真的能跑了**：
- `AgentShellRuntime` 现在会在自然语言意图分类之前先解析 slash command
- `/api/operator/command` 也因此真正吃到了 enabled extension
- 当前 enabled extension 会被分发到它绑定的 typed target command，而不是启用后依然只能停在 registry 里

**2. 当前真实链路**：
- 已启用 extension 现在可以：
  - 识别 slash command
  - 合并 manifest 默认参数
  - 读取可选 `text_parameter`，把 slash command 尾部文本映射进 target params
  - 执行对应 typed command
  - 写 `extension_invoked` event
- 未启用 extension 现在也不会再假装自己是普通自然语言命令；会明确返回“还没 enabled”
- Command Center 还补了 enabled extensions 卡片，让 slash command 有显式入口，不再要求用户背命令

**3. 当前边界要说准**：
- 已接通：
  - enabled extension slash dispatch
  - text-tail → typed param mapping
  - Command Center enabled extension surfacing
  - extension invocation event trace
- 仍未接通：
  - 更深的 backlog triage / queue review cadence
  - recovery contract 其余深水区

**4. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_agent_shell.py tests/test_operator_command_route.py -q` → `81 passed`
- `uv run pytest tests/test_agent_shell.py tests/test_operator_command_route.py tests/test_operator_routes.py tests/test_operator_store.py -q` → `101 passed`
- `uv run pytest tests/test_operator_store.py tests/test_operator_routes.py tests/test_operator_events.py tests/test_operator_command_route.py tests/test_operator_permissions.py tests/test_operator_compaction.py tests/test_agent_shell.py tests/test_acceptance.py -q` → `138 passed`
- `npm run build`（`frontend/`）→ PASS
- `python -m compileall aletheia/agent_shell aletheia/server/routes/operator.py` → PASS

## Recent Change (Operator extension sandbox 第一层接线 — 2026-06-11 第二十八段)

**1. extension sandbox 现在终于不是“设计里说以后再做”**：
- backend 现在新增了两张真表：
  - `extension_registry`
  - `extension_runs`
- `data/agent_shell/...` 这层也新增了两类真实工件：
  - `scratch/*.md`
  - `extensions/*.json`

**2. 现在已经接通的真实链路**：
- typed command 已新增三条：
  - `draft_extension_manifest`
  - `test_extension_sandbox`
  - `propose_extension_enablement`
- 也就是说 extension 方向现在已经能走完：
  - draft manifest
  - sandbox-only validation
  - proposal artifact
  - human enable gate
- backend 还补了 `/api/operator/extensions` inspect 路径，以及 `/api/operator/extensions/{id}/enable` 的显式人工启用门

**3. 当前边界要说准**：
- 已接通：
  - extension manifest persistence
  - sandbox run persistence
  - scratch Markdown / extension JSON file artifacts
  - `extension_enablement_proposal`
  - human approval before enablement
- 仍未接通：
  - 更深的 backlog triage / queue review cadence
  - recovery contract 其余深水区

**4. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_store.py tests/test_agent_shell.py tests/test_operator_routes.py -q` → `93 passed`
- `uv run pytest tests/test_operator_store.py tests/test_operator_routes.py tests/test_operator_events.py tests/test_operator_command_route.py tests/test_operator_permissions.py tests/test_operator_compaction.py tests/test_agent_shell.py tests/test_acceptance.py -q` → `134 passed`
- `python -m compileall aletheia/agent_shell aletheia/server/routes/operator.py` → PASS

## Recent Change (Operator inspectable working-memory 文件层接线 — 2026-06-11 第二十七段)

**1. `data/agent_shell/...` 终于不再只是设计文档里的目录名**：
- 当前已经接通三类真实文件工件：
  - session append-only JSONL
  - operator task Markdown
  - `create_diagnostic_todo` 产出的 todo Markdown
- 这意味着 operator 的一部分工作记忆终于变成“DB + 可直接打开检查的文件”，而不是只剩 SQLite 行和 knowledge side effects

**2. 当前接线位置**：
- `operator_store` 现在会把 session message / event 写进 session JSONL
- command audit 现在也会把 command 记录附加进对应 session JSONL
- operator task create/update 会同步刷新任务 Markdown
- `create_diagnostic_todo` 现在除 knowledge item 外，还会额外写 todo Markdown

**3. 当前边界要说准**：
- 已接通：
  - session JSONL
  - task Markdown
  - diagnostic todo Markdown
- 仍未接通：
  - scratch / extension file layer 的更深 runtime 接线
  - 更深的 backlog triage / queue review cadence
  - recovery contract 其余深水区

**4. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_store.py tests/test_operator_command_route.py tests/test_agent_shell.py -q` → `79 passed`
- `uv run pytest tests/test_operator_store.py tests/test_operator_routes.py tests/test_operator_events.py tests/test_operator_command_route.py tests/test_operator_permissions.py tests/test_operator_compaction.py tests/test_agent_shell.py tests/test_acceptance.py -q` → `129 passed`
- `operator-shell npm test` → PASS（42 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator budgeted long-history recovery folding 接线 — 2026-06-11 第二十六段)

**1. recovery 现在终于不再是“只拿两条 branch / 两条 summary”这种硬编码短记忆了**：
- `operator-shell recoverState()` 现在会抓更长时间窗里的 recent child sessions，而不是只盯非常窄的一层
- `buildRecoveryContext()` 现在会在一个 bounded recovery intent budget 内持续吸收更多 recent branch / compacted-memory 线索
- 也就是说，这层已经从“多条折叠”进一步变成“预算化折叠”，不再在两端失真：
  - 不是只记住最近两条
  - 也不是把 recovery intent 无限膨胀

**2. 当前真实效果**：
- 如果 recent branch / recent daily summaries 都比较短，recovery intent 现在能带回超过两条的上下文
- 如果 backlog 很长，recovery intent 仍然会被控制在稳定上限内
- 当前验证里已经覆盖：
  - 超过两条 child-session branch 仍能折叠进 intent
  - 更长 recovery history 仍会被压在 bounded budget 内

**3. 当前边界也要说准**：
- 已接通：
  - recent multi-branch recovery folding
  - recent multi-day compacted-memory folding
  - bounded recovery intent budget
- 仍未接通：
  - 更深的 backlog triage / queue review cadence
  - recovery contract 其余深水区

**4. 当前验证结果（本轮后）**：
- `operator-shell npm test` → PASS（42 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator queue review 第一层真值接线 — 2026-06-11 第二十五段)

**1. queue review 现在不再只停在“看见有 queued task”**：
- backend `build_recommendations()` 现在除了 `queued_tasks_present`，还会给出 `queue_review_needed`
- Node `RecommendationEngine` 也同步补上同一类 recommendation
- 当前第一层判据很明确：
  - 任一 queued task 等待约 15 分钟以上
  - 或 queued backlog 累积到 3 条及以上

**2. 这让 queue supervision 至少开始有了“需要人工复查”的真实触发条件**：
- Command Center 推荐面板不再只能看到“队列存在”
- shell 本地 loop 也能区分“只是有 backlog”与“backlog 已经开始老化/堆积”
- 当前 recommendation 还会带回 `task_ids / queued_count / oldest_age_minutes` 这类 review 线索

**3. 当前边界还是要说准**：
- 已接通：
  - `queued_tasks_present`
  - 第一层 `queue_review_needed`
  - priority queued task promotion
- 仍未接通：
  - 更深的 backlog triage / aging policy
  - 多阶段 queue review cadence
  - 更长时间窗/预算化的 long-history folding
  - recovery contract 其余更深水区

**4. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_events.py -q` → `15 passed`
- `uv run pytest tests/test_operator_store.py tests/test_operator_routes.py tests/test_operator_events.py tests/test_operator_command_route.py tests/test_operator_permissions.py tests/test_operator_compaction.py tests/test_agent_shell.py tests/test_acceptance.py -q` → `129 passed`
- `operator-shell npm test` → PASS（40 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator priority queue backlog 接线 — 2026-06-11 第二十四段)

**1. queue 现在不只是“有 backlog”，而且开始有优先级语义了**：
- `TaskRuntime` 现在会保留 queued backlog
- 当前 task 清掉后，runtime 会优先提升 higher-priority queued task，而不是纯 FIFO
- 这让高严重度 operator work 不会被更早但更轻的 backlog 长时间压住

**2. 当前 queue truth 边界**：
- 已接通：
  - queued backlog persistence
  - queued task promotion
  - high-priority queued task precedence
  - `queued_tasks_present` recommendation truth
- 仍未接通：
  - 更深的 queue review / backlog policy
  - 更长时间窗/预算化的 long-history folding
  - recovery contract 其余更深水区

**3. 当前验证结果（本轮后）**：
- `operator-shell npm test` → PASS（39 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator task queue + queued backlog truth 接线 — 2026-06-11 第二十三段)

**1. `TaskRuntime` 终于不再只是“一个 running task 的壳”**：
- 额外的 actionable recommendation 现在会被物化成真正的 `queued` task
- recovery 也会把 queued backlog 一起拉回
- 当前 active task 清掉后，runtime 会把下一条 queued task 提升为 running

**2. queue review 现在也不再只是 spec 文案**：
- backend `build_recommendations()` 现在会显式给出 `queued_tasks_present`
- Node `RecommendationEngine` 也同步给出 `queued_tasks_present`
- 所以 Command Center 与 shell loop 现在都能看到 backlog queue，而不是只能看到 active task / failed task

**3. 当前边界要说准**：
- 已接通：
  - queued backlog persistence
  - queued task promotion
  - queued backlog recommendation truth
- 仍未接通：
  - queue prioritization policy
  - 更深的 queue review cadence
  - 更长时间窗/预算化的 long-history folding
  - recovery contract 其余更深水区

**4. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_events.py -q` → `14 passed`
- `operator-shell npm test` → PASS（38 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator failed/stuck task recommendation truth 接线 — 2026-06-11 第二十二段)

**1. spec 里的 failed/stuck task scan 终于不只是 roadmap 文字了**：
- backend `build_recommendations()` 现在会显式给出：
  - `failed_tasks_present`
  - `stuck_tasks_present`
- Node `RecommendationEngine` 也同步补上了同样两类 recommendation truth

**2. 这意味着 Command Center 与 shell loop 终于开始覆盖 operator 自己的 task supervision**：
- Command Center 推荐面板因为读 backend truth，现在能真实看到 stuck running task
- shell 本地 recommendation synthesis 也不再只盯 gateway health / dreaming / review / proposal specialists
- 当前 stuck task 判据是 running task 超过 30 分钟仍未结束

**3. 当前边界也要说准**：
- 已接通：
  - failed task truth
  - stuck task truth
- 仍未接通：
  - queue review 的更深 cadence
  - 更长时间窗/预算化的 long-history folding policy
  - recovery contract 其余更深水区

**4. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_events.py -q` → `13 passed`
- `operator-shell npm test` → PASS（36 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator in-flight task re-entry + multi-branch/multi-day recovery folding 接线 — 2026-06-11 第二十一段)

**1. crash recovery 已经不再只会“看见一个 running task id”，现在会真的再进去跑**：
- `TaskRuntime.restore()` 现在会标记 recovered running task
- `sync()` 在恢复后的第一轮不会把它直接当成“condition cleared”清掉
- 当前已验证能自动再进入两类真实 in-flight task：
  - `evaluate_strategy_state`
  - `create_repair_proposal`

**2. proposal task 现在不再依赖 recommendation 还在现场**：
- 如果 restart 后 recommendation 已经不在当轮列表里，shell 仍会从 task payload 重建 recommendation/decision 语义
- 当前已验证 recovered `create_repair_proposal` 能直接继续执行 typed proposal command，而不是留下一个永远 running 的空壳 task

**3. recovery folding 也从“最新一条”扩到了“最近多条”**：
- child-session branch context 不再只取 latest branch
- compacted memory 不再只取 latest `operator_daily_summary`
- 当前 recovery intent 会折叠 recent multi-branch context + multi-day compacted summary sections，而不是只记住最近一句话

**4. 当前边界依然要说准**：
- 已接通：
  - recovered in-flight strategy/proposal task re-entry
  - recent multi-branch recovery folding
  - recent multi-day compacted-memory folding
- 仍未接通：
  - 更长时间窗/预算化的 long-history folding policy
  - spec 里 `failed task scan / stuck task scan / queue review` 的更深 supervision cadence
  - recovery contract 其余更深水区

**5. 当前验证结果（本轮后）**：
- `operator-shell npm test` → PASS（35 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator branch/session resume + folded compacted memory 接线 — 2026-06-11 第二十段)

**1. recovery 已经不再只恢复主 session 的一句话**：
- `operator-shell recoverState()` 现在会额外拉回 `operator-main` 下最近的 child sessions
- `SessionRuntime` 现在开始持有 branch state，而不是只记一个 main session intent
- crash recovery 的 heartbeat intent 现在可以带回类似 `Strategy Evaluation` 这样的 branch context

**2. compacted memory 的读回也更接近 design 里的 long-history folding**：
- recovery 不再只读 `operator_daily_summary.summary_excerpt`
- 现在还会从 `sections` 里折叠最近一条：
  - `major_user_instructions`
  - `important_repair_decisions`
  - `proposal_outcomes`
  - `strategy_state_changes`
  - `unresolved_issues`
  - `follow_up_recommendations`
- 这让 recovery intent 不再只是一句摘要，而是开始带回 compacted memory 里的结构化重点

**3. 当前边界依然要说准**：
- 已接通：
  - latest child-session branch context resume
  - latest compacted summary section folding
- 仍未接通：
  - crash 后 in-flight branch/task 的自动再进入
  - 超出 `latest branch + latest compacted summary` 的多日/多分支 long-history folding
  - recovery contract 其余更深水区

**4. 当前验证结果（本轮后）**：
- `operator-shell npm test` → PASS（31 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Command Center behavior proposal specialist 接线 — 2026-06-11 第十九段)

**1. proposal-only specialist 的最后一条明确缺口已经补上**：
- backend 现在会从 `agent_shell_commands` 真值里识别 recent denied `command_session` commands
- 当前判据是：最近 24h 内同一命令被拒绝 2 次及以上，且最近还没有对应 `command_center_behavior_proposal`
- 当前命中的 recommendation kind 是 `command_center_behavior_change_proposal_due`

**2. 这条 recommendation 也已经能走完整 proposal_only path**：
- TaskRuntime 会把它物化成 `create_repair_proposal` task
- RepairEngine 在 `AllowEditing / Auto` 权限下会给出 `proposal_only` decision
- 当前执行的 typed command 是 `propose_command_center_behavior_change`
- 参数来自 command audit 真值：target command、rejection count、sample inputs / responses、requested behavior

**3. 这条链现在用的是真正的 operator evidence，而不是拍脑袋的 UI 想法**：
- `list_commands()` 现在会把 `session_kind / session_user_input / session_title` 一起带出来
- backend 只看 `command_session` 的 denied commands，避免把 child session 内部命令执行噪音误判成用户侧 UX 问题
- recommendation 与 proposal 都会按 target command 做 recent artifact 幂等去重

**4. 结果同样是可审计真值**：
- shell 会先写 `repair_decision` artifact
- 然后执行 typed `propose_command_center_behavior_change`
- 成功后写 `proposal_created` event，并把 task 标成 completed
- proposal artifact 是 `command_center_behavior_proposal`

**5. 当前 proposal-only specialist 边界**：
- 已接通：
  - `propose_strategy_promotion`
  - `propose_prompt_change`
  - `propose_schedule_change`
  - `propose_risk_boundary_change`
  - `propose_command_center_behavior_change`
- 剩余硬缺口已经不在 proposal-only specialist，而在更深的 PI runtime：
  - branch/session resume
  - 超出当前 summary-excerpt readback 的 long-history folding
  - recovery contract 其余深水区

**6. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_store.py tests/test_risk_routes.py tests/test_operator_events.py tests/test_operator_routes.py tests/test_operator_command_route.py tests/test_operator_permissions.py tests/test_operator_compaction.py tests/test_agent_shell.py tests/test_acceptance.py -q` → `134 passed`
- `operator-shell npm test` → PASS（29 tests）
- `operator-shell npm run build` → PASS
- `frontend npm run build` → PASS

## Recent Change (Risk boundary proposal specialist 接线 — 2026-06-11 第十八段)

**1. proposal-only specialist 又补上了一条真实 risk 路径**：
- backend 新增 `/api/risk/summary`
- 它会从 recent rejected `risk_decisions` 中识别“最近 24h 内同一可调整风险边界被命中 2 次及以上，且最近还没有对应 `risk_boundary_change_proposal`”的情况
- 当前命中的 recommendation kind 是 `risk_boundary_change_proposal_due`

**2. 这条 recommendation 已经能走完整 proposal_only path**：
- TaskRuntime 会把它物化成 `create_repair_proposal` task
- RepairEngine 在 `AllowEditing / Auto` 权限下会给出 `proposal_only` decision
- 当前执行的 typed command 是 `propose_risk_boundary_change`
- 参数来自 backend 真值摘要：`changes`、boundary key、recent rejection evidence，而不是 Node 侧猜风险边界

**3. 这条 risk summary 现在是真正可查询的后端事实层**：
- recent rejected decisions 会带 proposal/risk 相关证据
- recent `risk_boundary_change_proposal` 会按 `changes` 中的 boundary keys 做幂等去重
- 当前已接的可调整边界包括 demo/live position notional、open positions、short allow、daily loss、live exposure、single-trade risk、futures leverage

**4. 结果同样是可审计真值**：
- shell 会先写 `repair_decision` artifact
- 然后执行 typed `propose_risk_boundary_change`
- 成功后写 `proposal_created` event，并把 task 标成 completed
- proposal artifact 是 `risk_boundary_change_proposal`

**5. 当前 proposal-only specialist 边界**：
- 已接通：
  - `propose_strategy_promotion`
  - `propose_prompt_change`
  - `propose_schedule_change`
  - `propose_risk_boundary_change`
- 仍未接通：
  - Command Center behavior proposal path

**6. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_risk_routes.py tests/test_operator_events.py tests/test_operator_routes.py tests/test_operator_command_route.py tests/test_operator_permissions.py tests/test_operator_compaction.py tests/test_agent_shell.py tests/test_acceptance.py -q` → `125 passed`
- `operator-shell npm test` → PASS（26 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Schedule proposal specialist 接线 — 2026-06-11 第十七段)

**1. proposal-only specialist 又补上了一条真实路径**：
- backend recommendation truth 与 `operator-shell` 现在都会基于 recent `agent_runs` 检测重复 manual run
- 当前判据是：同一 agent 在最近 24h 内出现 2 次及以上 manual run，且还没有 recent `schedule_change_proposal`
- 命中的 recommendation kind 是 `agent_schedule_change_proposal_due`

**2. 这条 recommendation 已经能走完整 proposal_only path**：
- TaskRuntime 会把它物化成 `create_repair_proposal` task
- RepairEngine 在 `AllowEditing / Auto` 权限下会给出 `proposal_only` decision
- 当前执行的 typed command 是 `propose_schedule_change`
- 参数来自真实调度证据：agent 名称、当前默认 cadence、建议 cadence，以及 recent manual run IDs

**3. 结果现在也是可审计真值**：
- shell 会先写 `repair_decision` artifact
- 然后执行 typed `propose_schedule_change`
- 成功后写 `proposal_created` event，并把 task 标成 completed
- proposal artifact 是 `schedule_change_proposal`

**4. 这条链同样补了幂等性**：
- recommendation engine 现在会读取 recent `schedule_change_proposal`
- 如果同一个 agent 已有 recent schedule proposal，就不会每个 loop 周期重复打一份

**5. 当前 proposal-only specialist 边界**：
- 已接通：
  - `propose_strategy_promotion`
  - `propose_prompt_change`
  - `propose_schedule_change`
- 仍未接通：
  - `propose_risk_boundary_change`
  - Command Center behavior proposal path

**6. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_acceptance.py tests/test_operator_command_route.py tests/test_operator_routes.py tests/test_operator_events.py tests/test_operator_permissions.py tests/test_operator_compaction.py tests/test_agent_shell.py -q` → `116 passed`
- `operator-shell npm test` → PASS（24 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Prompt/playbook proposal specialist 接线 — 2026-06-11 第十六段)

**1. proposal-only specialist 现在不只剩 strategy promotion 一条**：
- backend recommendation truth 与 `operator-shell` 现在都会基于 recent `agent_runs` 检测重复失败的 agent
- 当前判据是：同一 agent 在最近 24h 内出现 2 次及以上 failed run，且还没有 recent `prompt_change_proposal`
- 命中的 recommendation kind 是 `agent_prompt_change_proposal_due`

**2. 这条 recommendation 已经能走完整 proposal_only path**：
- TaskRuntime 会把它物化成 `create_repair_proposal` task
- RepairEngine 在 `AllowEditing / Auto` 权限下会给出 `proposal_only` decision
- 当前执行的 typed command 是 `propose_prompt_change`
- 参数来自真实失败 run：target = agent 名称，rationale / content 会带 repeated failure summary 与 error samples

**3. 结果现在是可审计真值，而不是“建议你改 prompt”这种空话**：
- shell 会先写 `repair_decision` artifact
- 然后执行 typed `propose_prompt_change`
- 成功后写 `proposal_created` event，并把 task 标成 completed
- proposal artifact 是 `prompt_change_proposal`

**4. 这条链还补了一个必要的幂等性点**：
- recommendation engine 现在会读取 recent proposal artifacts
- 如果已经存在 recent `prompt_change_proposal` 指向同一个 agent，就不会每个 loop 周期重复打一份 proposal

**5. 当前 proposal-only specialist 边界**：
- 已接通：
  - `propose_strategy_promotion`
  - `propose_prompt_change`
- 仍未接通：
  - `propose_schedule_change`
  - `propose_risk_boundary_change`
  - Command Center behavior proposal path

**6. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_acceptance.py tests/test_operator_command_route.py tests/test_operator_routes.py tests/test_operator_events.py tests/test_operator_permissions.py tests/test_operator_compaction.py tests/test_agent_shell.py -q` → `115 passed`
- `operator-shell npm test` → PASS（21 tests）
- `operator-shell npm run build` → PASS

## Recent Change (SSE-first feed + command search + compaction readback + backend recommendation parity — 2026-06-11 第十五段)

**1. Command Center 不再主要靠 polling 维持 operator timeline 了**：
- frontend `CommandCenter` 现在通过 `/api/operator/events/stream` 走 **SSE-first** event feed
- 收到 `operator_event` 后会即时刷新 operator status / sessions / tasks / recommendations / session detail / context
- 这意味着 timeline 与 inspector drill-down 现在是事件驱动优先，而不是每 5 秒捞一次

**2. 长历史 command search 现在不再只限当前 session**：
- backend 新增 `GET /api/operator/commands`
- 支持 `session_id / query / limit`
- Command Center 的 history filter 现在在有搜索词时会调用这条 backend route，做跨 session 的 full-history command audit search
- 因此“更强 command search/filter”这条不再停留在 roadmap 上

**3. compaction summary 不再只是一个文件路径**：
- `build_operator_daily_summary()` 现在除了写 markdown 文件，还会产出 `summary_excerpt + sections`
- `/api/operator/internal/compact` 写入的 `operator_daily_summary` artifact 也会带这两段结构化内容
- `operator-shell` crash recovery 现在会把 recent compacted memory excerpt 读回 recovery intent
- 也就是说，“compaction summary readback” 已经从纯 TODO 变成真实恢复逻辑的一部分

**4. backend recommendation truth layer 现在和 Node shell 的 specialist work 对齐了**：
- `build_recommendations()` 不再只给 `operator_offline / operator_paused / failed_tasks_present / command_rejections_present`
- 现在也会从真实 DB 状态产出：
  - `dreaming_due`
  - `review_due`
  - `strategy_review_due`
  - `strategy_promotion_proposal_due`
- 这样 Command Center 推荐面板终于能看见 shell 实际在编排的 specialist work，而不只是基础告警

**5. canonical loopback operator API surface 也补齐了**：
- backend 现有：
  - `/api/operator/internal/commands/execute`
  - `/api/operator/internal/decisions`
  - `/api/operator/internal/repair-decisions`
  - `/api/operator/internal/command-intents`
- `operator-shell` 已改用 `/internal/commands/execute`
- 这让 typed command / decision / intent 不必再借道自然语言兼容层

**6. 现在还剩下什么没关**：
- 更广的 proposal-only specialists 仍未接通：prompt / schedule / risk / command-center behavior proposal path
- 更深的 PI-style session runtime 仍未完成：branch/session resume 更细粒度恢复、超出当前 summary-excerpt 的 long-history folding
- 所以仍然**不能**声称 Track B Stage 1 已全部完成

**7. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_acceptance.py tests/test_operator_command_route.py tests/test_operator_routes.py tests/test_operator_events.py tests/test_operator_permissions.py tests/test_operator_compaction.py tests/test_agent_shell.py -q` → `114 passed`
- `operator-shell npm test` → PASS（18 tests）
- `operator-shell npm run build` → PASS
- `frontend npm run build` → PASS

## Recent Change (Proposal-only strategy promotion specialist 接线 — 2026-06-11 第十四段)

**1. proposal-only repair / proposal specialist 不再是纯设计条目**：
- backend `/api/strategies/summary` 现在除了 `needs_operator_evaluation_strategy_ids`，还会计算 `needs_operator_promotion_proposal_strategy_ids`
- 判据是：最近 24h 有 `strategy_evaluation` 显示某个 `paper_running` strategy 的 `promotion_gates.passed = true`，但最近还没有对应的 `strategy_promotion_proposal`

**2. operator-shell 已经会把这类情况转成真实 proposal path**：
- RecommendationEngine 会生成 `strategy_promotion_proposal_due`
- TaskRuntime 会把它物化成 `create_repair_proposal` task
- RepairEngine 在 `AllowEditing / Auto` 权限下会给出 `proposal_only` decision，而不是直接改 strategy lifecycle
- 当前执行的 typed command 是 `propose_strategy_promotion`

**3. proposal-only path 的结果现在是可审计真值**：
- shell 会先写 `repair_decision` artifact
- 然后执行 typed `propose_strategy_promotion`
- 成功后写 `proposal_created` event，并把 task 标记为 completed
- 最终人类看到的是 `strategy_promotion_proposal` artifact，而不是系统偷偷把 strategy 从 `paper_running` 改成 `paper_passed`

**4. 这次关闭的是“proposal-only specialists 完全没接通”的说法，但还不是全量完成**：
- 已接通第一条：strategy promotion proposal specialist
- 仍未接通：prompt/playbook change specialist、schedule change specialist、risk-boundary change specialist、Command Center behavior proposal specialist
- 另外从权威 spec 回看，SSE-first event feed 也还没接上，当前 Command Center 仍以 polling 为主

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_acceptance.py -q -k "strategy_summary_reports_promotion_ready_strategies_needing_proposal"` → `1 passed`
- `uv run pytest tests/test_acceptance.py tests/test_operator_command_route.py tests/test_operator_routes.py tests/test_operator_events.py tests/test_operator_permissions.py tests/test_operator_compaction.py tests/test_agent_shell.py -q` → `109 passed`
- `operator-shell npm test` → PASS（17 tests）
- `operator-shell npm run build` → PASS
- `frontend npm run build` → PASS

## Recent Change (Context model 真链路 + strategy evaluation specialist + Command Center context 实看验证 — 2026-06-11 第十三段)

**1. strategy governance 不再只停在 recommendation 文案**：
- backend 新增 `/api/strategies/summary`
- `operator-shell` 会把 flagged strategy 缺 fresh evaluation 的情况转成 `evaluate_strategy_state` task
- 该 task 会创建 `strategy_session` child session，执行 typed command `get_strategy_lifecycle`，并写回 `strategy_evaluation` artifact
- 这让 strategy review 不再只是“建议你去看一下”，而是有真实 session trace、真实 artifact、真实 task completion

**2. Python truth layer 现在真的提供了分级安全 context，不再靠 UI/Node 自己猜**：
- backend 新增 `/api/operator/internal/context/query`
- 会按 session kind 自动选 `operational_context / trading_context / knowledge_context / repair_context`
- 返回的是 curated backend snapshot，而不是让 Node shell 默认去读任意文件
- 当前 context payload 已扩到 `system / agents / trading / proposals / strategies / risk / errors / operator_state`

**3. Node shell 也开始真实消费这条 context 了**：
- dreaming / review / strategy / diagnostic child session 创建后，会先写一条 `context` message
- 这条 message 带 profile、sections、summary 与 structured context snapshot
- 因此 “session-level context selection” 现在不只是设计条目，而是 child session trace 里的真实数据

**4. Command Center 已补上 Context Inspector，并做了 live verification**：
- 前端新增 `Context Inspector` card，直接读取 `/api/operator/internal/context/query`
- 先在静态 preview 上确认了页面结构，再通过 `http://127.0.0.1:5173/command-center` 做了代理到真实 `/api` 的 live 验证
- 这次也再次确认开发约束：backend `.py` 改完后如果本地 gateway 还没重启，前端会一直停在 loading；重启 gateway 后 context inspector 即恢复真实数据

**5. 当前剩余硬缺口**：
- proposal-only repair specialists
- 更深的 branch resume / compaction summary readback / long-history folding
- 跨长历史的更强 command search/filter

**6. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_routes.py -q -k "context_query_defaults_to_session_kind_profile"` → `1 passed`
- `uv run pytest tests/test_acceptance.py tests/test_operator_command_route.py tests/test_operator_routes.py tests/test_operator_events.py tests/test_operator_permissions.py tests/test_operator_compaction.py tests/test_agent_shell.py -q` → `108 passed`
- `operator-shell npm test` → PASS（16 tests）
- `operator-shell npm run build` → PASS
- `frontend npm run build` → PASS
- Browser live verification → PASS（`http://127.0.0.1:5173/command-center` 上 `Context Inspector` 已脱离 loading，能看到 profile / sections / summary / real context payload）

## Recent Change (Typed internal command 通道 + repair decision 首条真实路径 + Command Center 实看验证 — 2026-06-11 第十二段)

**1. Node shell 不再只能靠自然语言桥接 Python command route**：
- backend 新增 `/api/operator/internal/commands/{command_name}`
- 该入口会直接复用现有 registry / permission gate / backend handler
- 命令执行结果会真实写回 `agent_shell_commands` 审计，同时把 command/result message 与 `command_received / command_executed / command_rejected` 事件落到指定 session

**2. operator truth layer 也补上了 repair decision 的物化入口**：
- backend 新增 `/api/operator/internal/artifacts`
- `operator-shell` 在 `Research+` operator 权限下，遇到高严重度 health recommendation 时会生成第一类真实 repair decision
- 当前这条 repair path 会创建 `diagnostic_session` child session、写 `repair_decision` artifact、再落 `repair_attempted` 与 `repair_executed / repair_failed` 事件
- 当前 auto-executed 的 low-risk repair 是 `create_diagnostic_todo`，因此 repair 不再只是 timeline 文案，而是能真实把诊断事项写进 operator memory

**3. 这次也把 Command Center 做了 live 实看，不只停在测试**：
- 本地 `http://127.0.0.1:5173/command-center` 已实开验证
- session tree 现在能真实显示 `diagnostic_session` child session 与现有 `dreaming_session`
- 点击 session 与 timeline event 都会把 inspector 切到对应对象，并显示真实 facts + raw evidence JSON
- 这次顺手确认了一个开发约束：frontend HMR 不会自动带上 Python route 变更，backend `.py` 改完后必须 `gateway restart`

**4. 当前 repair / runtime 边界**：
- 已接通：typed internal command path、diagnostic child session、repair decision artifact、第一条 low-risk repair execution path
- 仍未接通：strategy-state evaluation specialist、proposal-only repair specialists、更深的 branch resume / long-history folding / session-level context selection

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_command_route.py tests/test_operator_routes.py -q` → `12 passed`
- `uv run pytest tests/test_agent_shell.py tests/test_operator_command_route.py tests/test_operator_events.py tests/test_operator_routes.py tests/test_operator_permissions.py tests/test_operator_compaction.py -q` → `94 passed`
- `operator-shell npm test` → PASS（14 tests）
- `operator-shell npm run build` → PASS
- `frontend npm run build` → PASS
- Browser live verification → PASS（`/command-center` session tree + inspector drill-down）

## Recent Change (Session-kind / child-session skeleton 接线 — 2026-06-11 第十一段)

**1. `agent_shell_sessions` 不再只是一个扁平 command list**：
- session 表现在开始承载 `kind / parent_session_id / title`
- backend 新增 `/api/operator/internal/sessions`
- 这让 operator shell 可以把 specialist work 写成真正的 child session，而不是只剩 task/event

**2. 当前已经接通到两类 delegation session**：
- dreaming delegation 会创建 `dreaming_session`
- post-trade review delegation 会创建 `review_session`
- 两者都会挂在 `operator-main` 下作为 child session

**3. delegation trace 不再只在主 timeline 里一闪而过**：
- delegation 开始时会先写 child session metadata
- 然后把 start message 与 result message 都写进对应 child session
- 主 session 仍保留 operator 总览；child session 承载 specialist trace

**4. 这次补的是 design 里 session-kind / branching-session 的第一层骨架**：
- 已接通：`dreaming_session` / `review_session` child sessions
- 仍未接通：child-session resume、session tree 浏览、long-history folding、按 session kind 的上下文裁剪

**5. 当前验证结果（本轮后）**：
- `operator-shell npm test` → PASS（13 tests）
- `operator-shell npm run build` → PASS
- `uv run pytest tests/test_operator_store.py tests/test_operator_routes.py -q` → `12 passed`
- `uv run pytest tests/test_acceptance.py -q -k "review_summary_reports_missing_post_trade_review_coverage"` → `1 passed`

## Recent Change (Post-trade review specialist delegation 接线 — 2026-06-11 第十段)

**1. specialist delegation 不再只停在 dreaming**：
- Python backend 新增 `/api/reviews/summary`
- 该摘要会返回 recent closed positions、recent post-trade reviews、recent reviewer runs
- 同时给出 `missing_review_strategy_ids`，让 operator loop 能直接判断哪几个 closed strategy 还缺 review 覆盖

**2. `operator-shell` 已经开始调度 `post_trade_reviewer`**：
- RecommendationEngine 现在会把 `missing_review_strategy_ids` 转成 `review_due`
- `review_due` 会创建真实 `generate_review` task
- 该 task 会分配给 `post_trade_reviewer`，并通过已有 `/api/agent-runs/start` 真正调起

**3. review delegation 的事件与完成回写已经闭环**：
- delegation 开始时写 `review_started`
- delegation 完成时写 `review_completed`
- task 本身继续通过 operator task route 写 `task_started / task_completed`

**4. 当前 specialist delegation 边界**：
- 已接通：`dreaming_agent` + `post_trade_reviewer`
- 仍未接通：strategy-state evaluation、repair proposal specialists、child-session / session-kind 更深 runtime

**5. 当前验证结果（本轮后）**：
- `operator-shell npm test` → PASS（13 tests）
- `operator-shell npm run build` → PASS
- `uv run pytest tests/test_acceptance.py -q -k "ac08_knowledge_base or review_summary_reports_missing_post_trade_review_coverage"` → `2 passed`
- `uv run pytest tests/test_runtime.py -q -k "risk_critic_and_post_trade_reviewer_run"` → `1 passed`

## Recent Change (Dreaming specialist delegation 接线 — 2026-06-11 第九段)

**1. `operator-shell` 不再只有自说自话的 recommendation/task 壳**：
- 现在会额外读取 `/api/dreaming/summary`
- 如果最近 24h 看不到 dreaming run/log，就生成 `dreaming_due` recommendation
- 该 recommendation 会创建真实 `run_dreaming` task，而不是只停在 UI 文案层

**2. 这条 task 已经能真实委托 specialist agent 执行**：
- `run_dreaming` task 会分配给 `dreaming_agent`
- `operator-shell` 会通过已有 `/api/agent-runs/start` 调起 `dreaming_agent`
- 任务完成后会把 `run_id / status / outputs / errors` 回写到 operator task

**3. event model 也补上了 design package 里要求的 dreaming 事件**：
- delegation 开始时写 `dreaming_started`
- delegation 完成时写 `dreaming_completed`
- task 自身仍会通过 operator task route 写 `task_started / task_completed`

**4. 这次补的是 design 里缺的“specialist delegation first real path”**：
- 之前虽然已有 `dreaming_agent` 与 `/api/dreaming/summary`
- 但 Operator Layer 并没有把它纳入 PI-style loop / recommendation / task / event 闭环
- 现在第一条从 recommendation -> task -> delegated agent -> completion 的真链路已经接通
- 但这还不是 delegation 方向的终点；review / strategy / repair proposal 仍未接入同样模式

**5. 当前验证结果（本轮后）**：
- `operator-shell npm test` → PASS（11 tests）
- `operator-shell npm run build` → PASS
- `uv run pytest tests/test_acceptance.py -q -k "ac08_knowledge_base"` → `1 passed`
- `uv run pytest tests/test_agent_runtime.py -q -k "dreaming_agent_creates_log"` → `1 passed`

## Recent Change (Operator auto session compaction 接线 — 2026-06-11 第八段)

**1. session compaction 不再只是手动 CLI 工具**：
- Python backend 新增内部 compact 入口，会把指定 UTC 日期压成
  `data/knowledge/operator_memory/daily_summary_YYYYMMDD.md`
- 同时写 `operator_daily_summary` artifact 和 `session_compacted` event
- 该入口对同一天是幂等的，不会重复刷 artifact / event

**2. `operator-shell` 已经开始自动补前一天的 summary**：
- 每次 loop 会检查前一 UTC 日是否已有 `operator_daily_summary`
- 如果没有，就自动触发一次 compaction
- 如果已经有了，就不会重复 compact 同一天

**3. 这次补的是 design package 里明确缺失的 contract**：
- 之前只有 `aletheia agent compact`
- 现在已经有 **manual + automatic** 两条路径
- 但更深的 long-history folding / PI-style session memory 还没完成，所以这不是 compaction 方向的终点

**4. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_operator_config.py tests/test_operator_compaction.py tests/test_operator_permissions.py tests/test_operator_command_route.py tests/test_operator_events.py tests/test_operator_routes.py tests/test_operator_store.py tests/test_operator_cli.py tests/test_operator_shell_supervision.py tests/test_agent_shell.py tests/test_paper_autoloop.py tests/test_strategy_causal_chain.py -q` → `115 passed`
- `operator-shell npm test` → PASS（9 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator recovery backlog 接线 — 2026-06-11 第七段)

**1. restart recovery 不再只恢复一个空 session 壳**：
- `operator-shell` 在 `recoverState()` 里现在会额外读取
  - recent failed operator tasks
  - unresolved proposal artifacts
  - persisted `last_error`
- 然后把这些 backlog 合成为恢复后的 current intent，而不是健康重启后一上来就退回
  `"monitoring system state"`

**2. 这次修的是真实 design gap，不是表面文案**：
- 如果系统本身已经 healthy，但仍有 recent failed tasks，operator 会以 `Degraded` 恢复
- 如果没有错误、但还有 unresolved proposals，operator 会继续把这些 proposal 保持在当前意图里
- 仍然保持原有红线：**永远不会恢复进 `Trading`**

**3. 当前 recovery 边界**：
- 已接进：session/task shell + recent failed tasks + unresolved proposal artifacts + `last_error`
- 自动化 session compaction 已在后续第八段接通；更深的 long-history memory folding 与更强的恢复后任务再编排仍未完成

**4. 当前验证结果（本轮后）**：
- `operator-shell npm test` → PASS（7 tests）
- `operator-shell npm run build` → PASS

## Recent Change (Operator typed-command completeness + strategy/recommendation real write-back — 2026-06-11 第六段)

**1. 剩下那 3 条空壳 typed commands 已经补成真实 backend 语义**：
- `archive_low_value_notification`
- `mark_strategy_under_review`
- `pause_paper_strategy`

registry 总数从 **28** 扩到 **31**，不再只是“设计里提过但 backend 没 handler”。

**2. 这次不是只把命令名接进去，而是把背后的状态也做实了**：
- `strategy_records` 新增 operator-facing 字段：
  `under_review / review_notes / review_updated_at / paper_trading_paused / paper_pause_reason / paper_paused_at`
- `mark_strategy_under_review` 会真实写这些字段，并落 `strategy_under_review` event
- `pause_paper_strategy` 会真实写 pause 字段，并把仍然挂在这个 strategy 上的 open opportunities 收口为 `reviewed`

**3. strategy pause 已经接进真实行为链**：
- `market_observer / prediction_observer / stock_observer` 现在只会绑定 **未 paused** 的 active paper strategy
- `paper_trader` 如果碰到 `linked_strategy_id` 对应 strategy 已 paused，会跳过该 opportunity 并把它标成 `reviewed`
- 这意味着“pause paper strategy”不再只是 UI 标签，而是真的阻断新的 strategy-linked paper flow

**4. recommendation archive 也不再是假动作**：
- archived notification 现在会写 `notification_archive` artifact + `notification_archived` event
- backend `build_recommendations()` 会读 archive artifacts，把已归档的 low-value recommendation 从 `/api/operator/recommendations` 真正滤掉
- high-severity recommendation 仍不允许 archive，避免把真正危险项静音掉

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_agent_shell.py -q` → `68 passed`
- `uv run pytest tests/test_agent_shell.py tests/test_operator_compaction.py tests/test_operator_permissions.py tests/test_operator_command_route.py tests/test_operator_events.py tests/test_operator_routes.py tests/test_operator_store.py tests/test_operator_cli.py tests/test_operator_shell_supervision.py tests/test_paper_autoloop.py tests/test_strategy_causal_chain.py -q` → `112 passed`

## Recent Change (Operator Layer 实现基础落地 + 文档同步 — 2026-06-11)

**1. 设计阶段已经结束,实现已开始**：此前本地文档里仍保留了"只到设计 / 不写实现代码 / Track B 未开始"
的旧叙述,现在统一修正。`ROADMAP.md` / `CURRENT_TASK.md` / `PIPELINE_STATUS.md` 已改为反映
真实状态：**dual-process 设计已冻结,`worktree-dev` 正在按该设计推进实现**,但合并/上线边界
仍是 `Phase 0 门全绿 + 显式授权`。

**2. Python truth-layer 基础已经落地**：
- `operator` 配置块与独立 `operator.permission_mode`
- SQLite 持久化: heartbeat / sessions / messages / tasks / artifacts / events
- `/api/operator/*` 路由与 CLI `agent status/pause/resume/wake/session`
- `/api/operator/command` 兼容桥会把命令流进现有 Python `AgentShellRuntime`
- Command Center 推荐与时间线改读 backend `recommendations/events`,不再靠前端本地推导

**3. Node `Operator Shell` 不再只有 heartbeat 空壳**：
- gateway supervision 仍由 Python 侧 monitor 负责
- shell 现在会读取 `/api/status` + `/api/health`
- degraded health 会生成 recommendation → diagnostic task → operator event
- heartbeat 会反映 busy/current_intent/current_task,问题消失后任务会自动收口回 observing

**4. 当前未完成的真实缺口**：
- 还没有真正的 PI-style session runtime / context compaction / restart recovery
- specialist delegation 仍未落地
- knowledge/dreaming/lifecycle 已有 typed-command 基础,但还没有调度化 dreaming / strategy-governance proposal 深化
- Command Center 已有基础 search/filter/quick-action,但长历史检索和更强 workflow shortcuts 还没补完

**5. 当前验证结果**：
- `uv run pytest tests/test_operator_permissions.py tests/test_operator_command_route.py tests/test_operator_events.py tests/test_operator_routes.py tests/test_operator_store.py tests/test_operator_cli.py tests/test_operator_shell_supervision.py -q` → `26 passed`
- `frontend npm run build` → PASS
- `operator-shell npm test` → PASS
- `operator-shell npm run build` → PASS

## Recent Change (Operator Layer ask/explain + minimal recovery/lifecycle — 2026-06-11 第二段)

**1. Stage 1 explain 面又往前推了一段**：
- `get_system_status` 不再只是吐一组平字段,现在会把 gateway health、operator mode/intent、recent runs、open positions、pending proposals、failed operator tasks 合成确定性 summary
- `explain_proposal` 不再只回 proposal + risk_decisions 原始结构,现在会优先给出具体的 risk-block explanation: symbol / direction / risk score / violations / risk message / execution chain count
- intent fallback 也修了 `"why did this proposal fail"` 这种句子,会优先落到 `explain_proposal`,不再误判到 `explain_agent_run`

**2. Node `Operator Shell` 有了最小恢复与生命周期事件**：
- 启动时会拉 `operator/status`、主 session、running tasks,恢复 session intent 与 active task 轮廓
- 恢复后永远只回到 `Observing` / `Paused` / `Degraded`,不会把旧 heartbeat 的 `Trading` 模式带回来
- 启停会写 `operator_started` / `operator_stopped` 事件

**3. 这当时还不是完整 recovery contract**：
- 当时还没有把 unresolved proposals / recent errors / long-history compaction 全接进来
- 目前其中 `unresolved proposals / recent failed tasks / last_error` 已接进 restart recovery；自动 compaction 与更深恢复仍未完成

**4. 当前验证结果（扩展后）**：
- `uv run pytest tests/test_agent_shell.py tests/test_operator_permissions.py tests/test_operator_command_route.py tests/test_operator_events.py tests/test_operator_routes.py tests/test_operator_store.py tests/test_operator_cli.py tests/test_operator_shell_supervision.py -q` → `53 passed`
- `operator-shell npm test` → PASS
- `operator-shell npm run build` → PASS
- `frontend npm run build` → PASS

## Recent Change (Operator Layer typed-command expansion + Command Center filter/actions — 2026-06-11 第三段)

**1. typed command 面继续补到了 design spec 指向的下一层**：
- registry 从 15 条扩到 **17 条**，新增 `get_operator_status` 与 `create_dreaming_log`
- 现有新增命令链现在包括：`get_operator_status / get_strategy_lifecycle / get_failed_tasks / get_recommendations / create_daily_review / create_dreaming_log`
- operator-surface 仍受双权限边界约束：先过 operator ladder，再过 worker/global safety，不存在“operator Research 绕过 worker Observe”的洞

**2. fallback intent 和回答文本做了两个关键修正**：
- forbidden 关键词不再用裸 `live`，避免把 `live candidate` 这类只读生命周期查询误判成危险命令
- `suggestions` 不再误落到 `create_improvement_proposal`
- `get_recommendations` / `get_failed_tasks` 现在不只报数量，而会把 failed task / active recommendation 的具体摘要直接说出来

**3. Command Center 补了当前最缺的交互深化**：
- 新增 **Quick Actions**：一键预填 operator heartbeat / system status / recommendations / failures / strategy lifecycle
- 新增 **Command History** 面板与 filter 输入框
- filter 会联动 session message stream、command audit、recommendations、tasks、timeline，不再只能肉眼扫长列表

**4. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_agent_shell.py tests/test_operator_compaction.py tests/test_operator_permissions.py tests/test_operator_command_route.py tests/test_operator_events.py tests/test_operator_routes.py tests/test_operator_store.py tests/test_operator_cli.py tests/test_operator_shell_supervision.py -q` → `70 passed`
- `operator-shell npm test` → PASS
- `operator-shell npm run build` → PASS
- `frontend npm run build` → PASS

## Recent Change (Operator low-risk + operational typed commands — 2026-06-11 第四段)

**1. command registry 从 17 条继续扩到 23 条**：
- 新增 low-risk write:
  `create_strategy_draft`
  `mark_task_done`
- 新增 controlled operational:
  `run_market_scan`
  `rerun_failed_agent_task`
  `set_symbol_observe_only`
  `create_paper_experiment`

**2. 这些命令全部走现有真值层原语,没有新开旁路**：
- `create_strategy_draft` 走 `strategy_lifecycle.create_strategy(...)`，可选写 `strategy_memory`
- `mark_task_done` 走 operator task store，真实把任务标记为 `completed`
- `run_market_scan` / `rerun_failed_agent_task` 走既有 `run_agent(...)`
- `set_symbol_observe_only` 只改 config：从 `risk.allowed_symbols` / `exchange.tradable_symbols` 移除，但保留市场观测
- `create_paper_experiment` 走 strategy lifecycle 正向推进到 `paper_candidate`

**3. 这轮还顺手修了一个很隐蔽的测试/运行一致性问题**：
- `agent_shell.commands` 原来在模块 import 时直接绑定了 `load_config`
- 某些 runtime 测试会在 patch `aletheia.config.loader.load_config` 的上下文里首次 import 这个模块，导致 handler 之后一直拿着被 patch 过的旧引用
- 现在改成模块级动态调用 `config_loader.load_config()` / `save_config()`，消除了这类污染

**4. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_agent_shell.py tests/test_operator_permissions.py -q` → `58 passed`
- `uv run pytest tests/test_agent_shell.py tests/test_operator_compaction.py tests/test_operator_permissions.py tests/test_operator_command_route.py tests/test_operator_events.py tests/test_operator_routes.py tests/test_operator_store.py tests/test_operator_cli.py tests/test_operator_shell_supervision.py -q` → `82 passed`
- `operator-shell npm test` → PASS
- `operator-shell npm run build` → PASS
- `frontend npm run build` → PASS

## Recent Change (Operator proposal-only command layer — 2026-06-11 第五段)

**1. proposal-only command 层已经接进 Python backend 了**：
- 新增 5 条 proposal-only typed commands：
  `propose_strategy_promotion`
  `propose_prompt_change`
  `propose_schedule_change`
  `propose_live_limited_enablement`
  `propose_risk_boundary_change`
- registry 总数从 **23** 扩到 **28**

**2. 它们的执行边界是保守的**：
- 不直接改策略 state
- 不直接改 scheduler cadence
- 不直接改 live state
- 不直接改 risk config
- 只创建 **proposal artifact + operator event**
- strategy/live-limited proposal 会附带 gate preview，至少能看见“当前为什么还不能直接推进”

**3. 权限与 safety 侧保持一致**：
- operator ladder: proposal-only 这组命令要求 `AllowEditing`
- worker/global safety: 仍然要过 worker permission 检查，不存在 operator 层绕过全局安全边界
- 命令的存在本身就是 backend 决策：Node shell 只能分类，真正的 proposal-only 语义还是 Python registry 决定

**4. 顺手修掉的一个运行一致性问题**：
- `agent_shell.commands` 之前的 `load_config` 直接 import 绑定，会被早期测试 patch 污染
- 现在统一改成 `config_loader.load_config()` / `save_config()` 动态读取，避免 handler 拿到过期引用

**5. 当前验证结果（本轮后）**：
- `uv run pytest tests/test_agent_shell.py tests/test_operator_permissions.py -q` → `68 passed`
- `uv run pytest tests/test_agent_shell.py tests/test_operator_compaction.py tests/test_operator_permissions.py tests/test_operator_command_route.py tests/test_operator_events.py tests/test_operator_routes.py tests/test_operator_store.py tests/test_operator_cli.py tests/test_operator_shell_supervision.py -q` → `92 passed`
- `operator-shell npm test` → PASS
- `operator-shell npm run build` → PASS
- `frontend npm run build` → PASS

## Recent Change (Operator session compaction 基础落地 — 2026-06-11 第三段)

**1. `session compaction` 不再只是设计条目**：
- 新增 Python 侧 compaction utility,会把当天 operator 会话/事件/任务压成
  `data/knowledge/operator_memory/daily_summary_YYYYMMDD.md`
- 当前 summary 至少覆盖设计要求的 6 个 section:
  - major user instructions
  - important repair decisions
  - proposal outcomes
  - strategy-state changes
  - unresolved issues
  - follow-up recommendations

**2. 先给了人工可执行入口**：
- CLI: `aletheia agent compact [--date YYYY-MM-DD]`
- 这意味着 compaction 现在是**manual but real**,还不是自动调度/自动触发

**3. 当前边界**：
- 已有 artifact 路径与内容骨架
- 仍未把 compaction 自动接进 operator loop / scheduler / restart recovery
- 所以它解决的是"能压缩并落盘",还没解决"何时自动压缩"

**4. 当前验证结果（再扩展后）**：
- `uv run pytest tests/test_agent_shell.py tests/test_operator_compaction.py tests/test_operator_permissions.py tests/test_operator_command_route.py tests/test_operator_events.py tests/test_operator_routes.py tests/test_operator_store.py tests/test_operator_cli.py tests/test_operator_shell_supervision.py -q` → `55 passed`
- `operator-shell npm test` → PASS
- `operator-shell npm run build` → PASS
- `frontend npm run build` → PASS

## Recent Change (Operator Layer 双进程设计收口 — 2026-06-10 第五段)

**1. 本地设计源已收口**：不再让后续实现依赖外部 Downloads 文档或更早的 Stage 1 bootstrap 草案。
当前 Operator Layer / Pi Agent 的本地 source of truth 分三层：
- `docs/OPERATOR_LAYER_DESIGN.md` = 概念层
- `docs/superpowers/specs/2026-06-10-pi-operator-agent-dual-process-design.md` = repo-specific 双进程详细蓝图
- `docs/superpowers/specs/2026-06-10-pi-operator-agent-design-crosswalk.md` = 外部参考文件覆盖矩阵
- `docs/superpowers/specs/2026-06-10-pi-operator-agent-design-audit.md` = 设计完成审计

**2. 关键设计结论已经钉死**：
- 最终形态是 **dual-process**，不是把现有 `POST /api/command` 继续堆成 Python 单进程壳
- Python gateway 继续做交易真相层 / 命令执行权威；新增 Node/TypeScript `Operator Shell`
  负责 PI 式常驻 session runtime、operator loop、任务/修复/推荐编排
- `Ctrl+K` 只保留为 launcher；主产品面是独立 **Command Center** 页面
- 权限拆成 **worker/trading** 与 **operator** 两层；新增 `operator.permission_mode`
- 现有 `aletheia/agent_shell` 保留并重构定位为 Python 侧 operator substrate / compatibility layer

**3. 当前设计阶段的下一步**：不是直接写代码，而是基于上述 spec 写 implementation plan，
把 Track 0-9 拆成逐文件、逐测试、逐提交任务。设计未审阅前，不进入实现。

## Recent Change (双机职责分离 + worktree-dev 上的 Operator Layer 研发线 — 2026-06-10 第四段)

**1. 双机职责正式拆开**:Phase 0 的 `72h` 观察门继续由**另一台观测机**承担;本 checkout 不再把
"gateway 是否在跑 / 观察窗到了第几小时 / demo 单是否落交易所"当作自己应维护的 git 事实。
任何关于观察窗是否达标的判断,都应回观测机用 `aletheia doctor` / gateway status 取证。

**2. 本 checkout 的当前职责**:切到 `worktree-dev` 作为**并行研发线**继续推进 roadmap 中被 gate
住但已获显式讨论授权的工作,优先是 **Operator Layer / Pi Agent** 的 Stage 1(只读监督)方案与原型。
这允许开发,**不等于允许合并/上线**,后两者仍受 `Phase 0 门全绿 + 显式授权` 约束。

**3. UI 方向收敛**:现有 `Ctrl+K` Command Palette 可以保留,但它只是快捷入口,**不是 PI agent 的主
形态**。PI agent / Apex Agent Shell 的主表面应是一个专门的 **Command Center 页面**:承载持久
会话、证据引用、命令 trace、权限状态与 session 切换;`Ctrl+K` 只负责快速导航 / 快速发问 / 打开
该页面。

## Recent Change (CLI 分层 + 运营命令软弃用 — 2026-06-10 第三段)

人类交互开始收口到 Web。评估"是否完全去掉 CLI"后定论:**不去掉,分三层**——
(1) bootstrap 内核 `gateway start/restart/enable-autostart` + `doctor` + `version`(Web
无法替代,UI 由 gateway 进程伺服、看门狗也依赖 `gateway start`);(2) 无 Web 平价的运维
工具 `bitget sync` / `knowledge reindex` / `knowledge search`;(3) 已有 `/api/*` 平价的运营
命令 `permission`/`model`/`risk`/`strategy`/`update` + `secrets` + `bitget test` → **软弃用**。

软弃用机制(commit `16efd5b`):新增 `cli/_deprecated.py::warn_deprecated`,弃用命令运行时往
**stderr** 打一行指向 Web 位置的提示,**stdout 与功能不变** → 538 测试全过(无回退)。整组弃用
用 Typer `@callback`,`bitget test` 用内联(保留 `sync`)。`main.py` help 分 bootstrap /
kept-tools / `[deprecated]` 三档。`doctor` 的 kill_switch / demo-creds fix_hint 改为 Web 首选、
CLI 备选(避免"删命令→提示成谎话")。文档同步(commit `2217510`):RUNBOOK/README 标 dashboard
为 primary、CLI 为 deprecated;`implementation_guide`(冻结规格,§号交叉引用)与本 changelog 历史
条目不改。架构决策详见 `docs/DECISIONS.md`(2026-06-10 CLI 分层条)。

**未做(刻意)**:运营命令的物理删除——软弃用是渐进第一步,留待将来 Web 路径用顺手后一次性收尾。

---

## Recent Change (LLM 接通 + Phase 0 全闭环验证 — 2026-06-10 第二段)

> 接上一条。分支 `fix/restore-data-package` 现 5 个 commit。

**1. LLM 已接通**（用户提供 Databricks AI-Gateway Anthropic 端点）：实测确认该端点是 Anthropic Messages API (`/v1/messages`) + `Authorization: Bearer`（非原生 `x-api-key`，后者 401），且 `claude-opus-4-8` **硬拒 `temperature`**（400 "temperature is deprecated"）。给 `anthropic_compatible` 加可配置 `auth_scheme`（默认 `x-api-key`，gateway 用 `bearer`）+ bearer 时省略 temperature（对齐 databricks.py）。`ProviderConfig.auth_scheme` 新字段，router 透传。端到端实测 healthcheck/complete/structured 全通；commit `aebe405`，model 测试 12 passed。**URL 进本地 gitignored config.yaml，token 进加密 secret store，repo 里无凭证。**

**2. Phase 0 全闭环首次跑通**（本 checkout 历史上第一次真正交易）：observer(LLM 真分析，产出有方向/置信/分数的 opportunity，非全 neutral) → paper_trader → proposal → risk 批准 → **order → fill → open position**（SOL/USDT short qty0.39@64.15）→ position_manager(checked=1) → post_trade_reviewer + risk_critic 各产出 1 条 knowledge。**0 error。**

**3. 与 roadmap 字面 gate 的唯一差距**：执行走 `paper_internal`（本地撮合）而非 `bitget_demo`（Bitget 真实模拟单），因为**本 checkout 无 Bitget demo 凭证**（`bitget.demo.{api_key,api_secret,passphrase}` 均 NOT SET）。按项目既定策略（demo 不可用→`paper_internal` 秒切兜底）这是正确兜底。当前 `permission_mode=Paper`、`paper_execution_mode=paper_internal`。

**停在此处的原因（需人工决定，AI 不擅自做）**：
- (a) 是否要等 Bitget demo 凭证就位（满足 "demo orders visible on Bitget" 这条 gate），还是先用 `paper_internal` 跑 72h；
- (b) 启动无人值守 gateway（持久 supervised 进程 + 登录看门狗）是对外、难撤销的状态变更，按规矩须先确认。
全套 515 passed。

---


## Recent Change (检出态真相 + aletheia/data/ 重建 + DNS resolver 修复 — 2026-06-10)

> 分支 `fix/restore-data-package`（未合并），3 个 commit。**关键：本 checkout 与文档描述的状态完全不同。**

**1. 本 checkout 从未运行过**（审计发现，与下方 06-09 条目的"作者机器"状态对立）：
无 `data/config.yaml`、无 `.secret.key`/`secrets.enc`、`app.db` 仅 2 条 audit + 1 条 strategy。
所以本文档下方"Phase 0 观察期运行中 / 06-09 真实 demo 单 / 504 测试通过"全部是**原作者机器**的状态，不是这里。**每次会话须用 `data/` + `gateway status` 核实实况，勿信旧文档叙事。**

**2. `aletheia/data/` 整个源码包缺失且从未提交**（`.gitignore` 的裸 `data/` 规则在每一级目录都匹配，连源码包一起吞掉）→ 克隆后 risk/execution/trading/agents 全部 `ModuleNotFoundError`，pytest 在 collection 阶段就 5 个 error。
按**测试即契约**重建全部 7 个文件（committed tests + 17 处调用点完全锁定行为），`.gitignore` 改为根锚定 `/data/`。全套 **515 passed**（原文档称 504）。未改任何信任边界语义。

**3. DNS resolver bug**（本 host 致命，Phase 0 基础设施）：`aiodns` 已装 → aiohttp 默认 c-ares `AsyncResolver`，本机读不到 DNS 配置 → **每个 ccxt 请求都 `ClientConnectorDNSError`**（urllib/OS resolver 却正常 200，所以症状伪装成"交易所不可用")。修复：`CCXTProvider` 注入 `ThreadedResolver`(OS getaddrinfo) 的 aiohttp session，并在 `close()` 里关闭它（注入 session 时 ccxt `own_session=False` 不会自己关，顺带消除 "Unclosed client session" 泄漏）。

**4. Boot-prove 通过**（无持久进程、无 autostart、完全可逆）：config bootstrap（安全默认 `Observe`/`live_disabled`/kill_switch off）→ 15 个 scheduler job 全部构建 → 重建的数据层拉到 binance/bitget 实时 ticker+OHLCV → `collect_market_snapshots` 7 个标的 0 error → `market_observer` 无 LLM 时优雅降级产出 7 个 neutral/speculative opportunity（不崩）。

**Phase 0 观察门卡在仅人能提供的输入**（CLAUDE.md 安全红线，AI 不得伪造）：
(a) LLM provider + API key（Settings→Model Provider 或 `aletheia model set`）——没有它 opportunity 全 neutral/speculative，paper_trader 不会开仓；
(b) Bitget demo API 凭证（`aletheia secrets set bitget.demo.*`）——没有它 `bitget_demo` 单到不了交易所（`paper_internal` 本地撮合仍可兜底）。
两者就位后：`permission_mode=Paper` → 启动 gateway → 跑满 72h 观察窗口。

---


## Recent Change (database is locked 真根因修复 — 2026-06-09)

**这是整个交易管道实际停摆的真根因**(此前误以为是 LLM 不通 / agent 不触发 / 调度延迟）。

**现象**：errors.log 累计 `database is locked` **1652 次**，所有写 job（ticker / exchange_sync / portfolio_snapshot / paper_trader / observer）失败，市场数据停更 90+min。

**根因**（`storage/repositories.py` 的 `_insert`/`_update`/`_delete_old_rows`）：提交条件 `if not was_in_tx and not db.in_transaction: commit()`。但 Python sqlite3 默认 `isolation_level=''` 下，INSERT/UPDATE/DELETE 会**自动开启事务**，写完后 `db.in_transaction` 恒为 True → 条件恒 False → **commit 从不执行**。写事务悬挂、持有 WAL 写锁，每线程独立连接下其他写线程 `busy_timeout=10s` 后全部 locked。bug 自 `d403fcf` 存在，只在持续高频并发写下爆发。

**修复（commit `a33dc86`，504 测试通过）**：三处改为 `if not was_in_tx: commit()`（看写**之前**的状态，非之后）。`transaction()` 嵌套路径不受影响（实证两种场景）。纯修复不改交易语义。

**实证**：复现脚本确认 INSERT 后 in_transaction=True、旧条件求值 False；临时还原旧条件后新回归测试失败 → 证明测试真能抓 bug。新增 `test_storage.py` 两个测试用**真实 file DB + 第二个独立连接**验证写已提交、锁已释放（共享注入连接会掩盖此 bug）。

**教训**：Python sqlite3 隐式事务反直觉——"写后判断要不要提交"必须看写**之前**的状态。测试陷阱:单一共享连接会掩盖锁 bug,要用真实 file DB + 独立第二连接。

---

## Recent Change (确定性自愈层 + Phase 0 观察期重置 — 2026-06-09)

**审计发现**："72h 观察期进行中"是记账假象——网关进程活着，但功能已停摆。

| 表象 | 实情 |
|---|---|
| 网关在跑 | 跑的是旧代码 `1aa1feb`，落后 HEAD **7 个 commit** |
| 文档称 healthy | `/api/health` = **degraded**：`code_updated_pending_restart` + `market_data_stale:1114min` |
| 观察期进行中 | `paper_trader` 已 **11h 未触发**；调度 cadence 残缺 |
| 系统在交易 | 294 opportunity → **0** proposal / 0 order / 0 fill |

**根因**：`run_health_check()`（每 10min）**已经算出 drift 信号、已经告警**，但**没有任何代码消费它去重启**。系统喊了 11 小时"我在跑旧码"，没有耳朵在听。这正是 ROADMAP "False confidence" 失败模式：进程活着 ≠ 系统在运行。

**修复（commit `8a6e008`，501 测试通过）**：给 `run_health_check()` 加确定性（零 LLM）自愈分支。
- `pidfile.py` — `supervisor_owns_pid()`：`read_pid()!=getpid()` 即有 supervisor 兜底（线程上下文等价于 route 的 `app.state._owns_pid`，零 subprocess）
- `config/schema.py` — `HealthConfig` 加 `self_heal_{enabled,min_interval_sec,max_per_hour,code_drift,market_data,port_cleanup}`；`min_interval`(1800s) 刻意 > supervisor 窗口(600s)，自愈单独永不耗尽 supervisor 的 5/600 give-up 预算
- `health.py` — `_trigger_restart`/`_can_self_restart`/`_alert_suppressed`/`_heal_port_cleanup` + `run_health_check()` 决策流。code drift / stale → 守卫+限频 `os._exit(42)` 重生；孤儿端口 → `kill_pid_tree`（**显式排除 self+supervisor**，否则误杀自己的 supervisor → 新僵尸）；LLM/kill_switch/agent_failure → 仅告警
- `tests/test_self_heal.py` — 12 个回归测试（全部 patch `_os_exit` 不真退出）

**端到端验证通过（2026-06-09）**：空 commit 制造 drift → `/api/health` 检测到 `code_updated_pending_restart` → 下个 health 周期自愈自动重启 → supervisor 重生新码 → drift 清除（audit `self_heal_restart: code_drift`）。今天那类僵尸态以后在 ≤10min 内自动消除。

**端到端测试当场抓到一个严重 bug，已修（commit `abd7773`，502 测试通过）**：观察到两次 `self_heal_restart` 正好间隔 10min（一个 health 周期），30min cooldown 没拦住。根因——限频计数器是【内存】list，而自愈动作是 `os._exit(42)`，重启清空内存，新进程永远看不到"刚重启过" → cooldown/ceiling 永不触发 → 任何"重启修不了"的持久 issue（如下方 DB 锁致 `market_data_stale`）会让网关**每 10min 无限重启**（比僵尸态更糟）。修复：限频改从【持久化 audit.jsonl】读 `self_heal_restart` 事件计数（`_recent_self_heal_restarts()`），跨重启有效。已验证：冷启动新进程读到旧进程写的 restart 记录、正确返回 cooldown 拒绝。**教训：任何"动作会重启进程"的状态必须持久化，绝不放内存。**

**两个独立的新发现**（均已在本会话解决，见下方更早条目）：
1. `database is locked` → **真根因，已修**（`a33dc86`，见顶部条目）。这是"重启后 agent 不触发 / 数据停更"的真因。
2. `gateway restart` 早先一次启动失败 → **诊断为误判**：当时网关跑旧码 `1aa1feb` + 测试抢 DB 锁，叠加造成 `do_start` 静默失败假象；三修复到位后实测 CLI restart 干净成功（stop→新 supervisor 重生→healthy→boot 对齐→drift 清除）。restart 无 bug。

**关于 Pi/Agent Shell 设计文档**：用户提供的 `aletheia_pi_agent_shell_integration_design.md` 主张顶层 LLM 操作员 agent。结论：方向对、v0 范围对，但**应分两层**——(A) 确定性自愈 supervisor（即本次交付，无 LLM，是稳定性根基）先做；(B) LLM Apex Agent Shell 作为"Operator Layer"排到 Phase 0/1 稳跑证明之后的平行线。本次完成了 A 的核心切片。

---

## Recent Change (Gateway 稳定性修复 — ghost-socket 彻底消灭 — 2026-06-08)

**问题现象**：前端顶栏状态显示 "Gateway Stopped"（红灯），但页面正常访问。
`gateway stop` 报 "port still in use"，`gateway start` 报 "foreign process holding port"。

**根因链**：
1. 网关 supervisor 被 kill 后，uvicorn 的 multiprocessing worker 子进程变孤儿
2. 孤儿 worker cmdline 是 `multiprocessing.spawn.spawn_main(...)`，不含 `aletheia.server.app:app` 或 `_gateway_runner` 标记
3. `find_gateway_orphans()` 靠 cmdline 标记识别，永远找不到这些 worker
4. Worker 继承了父进程的 listening socket，持续占端口并用旧代码响应请求
5. `/api/status` 用 `pidfile.is_running()` 判断状态，而 PID 文件不存在 → 返回 `false`

**修复（5 个文件，"one stop all" 三层策略）**：
- `pidfile.py` — 新增 `find_port_holders(port)`：用 netstat/lsof 直接按端口找 PID，兜底所有逃逸进程
- `lifecycle.py` — `do_start` + `do_stop` 加入端口兜底：cmdline 扫描 → port holder 扫描 → 强制 tree-kill
- `health.py` — `/api/status` 返回 `gateway_running: True`（能响应请求 = 在运行，不依赖 PID 文件）
- `app.py` — lifespan 启动时自动补写 PID 文件（任何启动方式都有 PID）；追踪 ownership，shutdown 只清自己写的
- `gateway.py` — restart 端点检查 supervisor/autostart 是否活跃，无恢复机制时拒绝自杀

**验证**：46 tests passed；重启后 `gateway_running: true`，PID 文件正确指向 supervisor。

---

## Recent Change (Markets K线动态加载 — 2026-06-08)

**目的**：Markets 页 K 线从「固定一段」改为主流交易所式动态加载 —— 向左滚到边缘自动续加更早历史，右沿 10s 轮询保持实时。

**后端**：`ccxt_provider.py` 加 `_TF_MS` + `timeframe_ms()` + `get_ohlcv(since=)`；`/api/market/ohlcv` 加 `before`（unix 秒）参数做历史分页（`since=before-limit*tf`，过滤 `>=before` 防重叠）；缓存只服务默认最新视图。

**前端**：`getOhlcv` 加 `before` 参；`PriceChart` 重写为命令式 store + refs，`subscribeVisibleLogicalRangeChange` 触发 `loadOlder()`（前插后平移逻辑区间保持视窗），`setInterval` 10s `series.update()` 实时尾部。

**测试/验证**：488 passed（+2）；后端 urllib + Vite 视觉 E2E 均确认三行为触发、零重叠、零 console error。

---

## Recent Change (Pipeline Gap Analysis — 2026-06-08)

**全自动交易闭环现状分析**：对照"全市场扫描→分析→策略→开仓→平仓→评估→学习→总结"
完整闭环，逐环节代码核实。新文档 `docs/PIPELINE_STATUS.md` 记录完整结果。

**核心发现**：
- 管道骨架 80% 接通（扫描→分析→开仓→平仓→总结 均自动运行）
- 三大结构性差距：经验闭环只闭 30%、agent 无协作流、策略是文字非可执行规则
- 两个 agent (`post_trade_reviewer` / `risk_critic`) 代码完整但未排入 scheduler
- 系统从未跑过真正的 unattended paper trading session（stage-0 尚未开始）

**新增文档**：`docs/PIPELINE_STATUS.md`（逐环节现状 + 差距 + scheduler 全景 + 北极星阶段）

---

## Recent Change (xStocks 接入 + 管道 bug fix — 2026-06-08)

**Bitget xStocks (ON-series tokenized equities) — observation-only:**
- `config/schema.py` — `observed_xstocks: list[str]` 默认 `["AAPLON/USDT","TSLAON/USDT","NVDAON/USDT","SPYON/USDT"]`
- `trading/instruments.py` — `xstocks_instruments_from_config()` 返回 `exchange="bitget"` InstrumentId 列表
- `agents/market_observer.py` — xStocks 分析 loop，独立 Bitget provider，`asset_class="equity"`；paper_trader gate 门控 skip（observation-only）
- `trading/market_data.py` — xStocks ticker 写入 market_snapshots；提取 `_fetch_and_store()` helper
- `tests/test_xstocks_observer.py` — 4 个新测试

**管道 bug fix (三项):**
1. `models.py` — `Opportunity.confidence` 默认 `"medium_confidence"` → `"speculative"`（对齐 paper_trader _CONFIDENCE_RANK）
2. `paper_broker.py` — 移除 naive `datetime.utcnow()`；`Order.filled_at` 默认 None
3. `market_data.py` — xStocks 也写入 market_snapshots（position_manager 定价依赖）

**测试**：474 passed，1 flaky（test_write_audit_jsonl，既存，单跑通过）

---

## Recent Change (Chart blackout root-cause + market-data hardening — 2026-06-08)

**Symptom**: all lightweight-charts components blank. **Root cause**: the gateway
process was running stale code (booted before the `CCXTProvider` switch was committed),
so `/api/market/ohlcv` kept hitting legacy `BitgetClient` with a symbol Bitget rejects.
`/api/health` showed healthy throughout — the failure was invisible.

**Immediate fix**: `aletheia gateway restart` — charts restored (verified real candles
for BTC/ETH/SOL).

**Hardening to prevent recurrence:**
- **`aletheia/agent_runtime/build_info.py`** (NEW) — records boot commit; `code_drift_issue()`
  flags when disk HEAD moves past the booted commit. Wired into `lifespan()` (records
  boot commit before scheduler starts) and `compute_health()` (appends
  `code_updated_pending_restart:<old>..<new>` → `/api/health` degraded).
- **`get_market_data_provider()`** in `aletheia/data/ccxt_provider.py` is now the single
  market-data entry point (per-call, never cached). Deleted the dead `get_default_provider()`
  singleton. Routed `routes/data.py`, `trading/market_context.py`, `trading/market_data.py`
  through it. The 2 Bitget-only xStocks sites stay direct `CCXTProvider(exchange="bitget",
  fallback_exchange="bitget")` with deliberate-exception comments.
- **CLAUDE.md** Development Rules: market-data-via-factory rule added.

See DEVLOG `## 2026-06-08 (later³)` for full detail.

## Recent Change (InstrumentId + Symbol Format Unification — 2026-06-08)

Full implementation of `exchange:symbol:market_type` unique instrument identity across the pipeline.

**Core new type — `aletheia/data/symbols.py`:**
- `InstrumentId` frozen dataclass: `exchange`, `symbol` (canonical `BTC/USDT`), `market_type` (`spot`|`perp`|`futures`)
- `str(instrument)` → `"binance:BTC/USDT:spot"`. `InstrumentId.parse("binance:BTC/USDT:spot")` round-trips cleanly.
- `instrument.to_ccxt_symbol()` → `"BTC/USDT"` (spot) or `"BTC/USDT:USDT"` (perp, settle=quote directly, no USD mapping)
- `instrument.to_bitget_symbol()` → `"BTCUSDT"` (execution-boundary only)
- `to_ccxt_perp(symbol)` helper added

**Config (`aletheia/config/schema.py`):**
- `MarketDataConfig.observed_instruments: list[str]` — e.g. `["binance:BTC/USDT:spot"]`
- Legacy `observed_symbols` kept as fallback when `observed_instruments` is empty

**Storage (additive migrations — zero downtime):**
- `exchange TEXT` column added to `market_snapshots` and `market_ohlcv`
- `aletheia/storage/repositories.py` — all query functions accept optional `exchange=` param

**Pipeline — all now canonical `BTC/USDT` end-to-end:**
- `aletheia/trading/instruments.py` — `observed_instruments_from_config()` returns `list[InstrumentId]`
- `aletheia/trading/market_data.py` — passes `InstrumentId` to `CCXTProvider`, stores `exchange=instrument.exchange`
- `aletheia/agents/market_observer.py` — iterates `InstrumentId` objects; `market_type` from instrument
- `aletheia/data/ccxt_provider.py` — accepts `InstrumentId | str`; `_resolve_ccxt_symbol()` handles both
- `aletheia/agents/paper_trader.py` — `allowed_symbols = {to_canonical(s) for s in tradable_symbols_from_config(cfg)}`

**API + frontend:**
- `aletheia/server/routes/data.py` — `/market/ohlcv` and `/market/snapshots` accept `exchange=` + `market_type=`
- `aletheia/server/routes/settings.py` — `GET /settings` now includes `market_data` block; `POST /settings/symbols` syncs `observed_instruments`
- `frontend/src/api/client.ts` — `getOhlcv()` uses `URLSearchParams` (auto-encodes `/` in `BTC/USDT`)
- `frontend/src/pages/Settings.tsx` — read-only "Observed instruments" chip row in Risk & Symbols tab (renders when `observed_instruments` non-empty; requires gateway restart to show after first symbols save)

**Tests:**
- `tests/test_instrument_id.py` — 45 new tests
- `tests/test_entry_logic.py` — fixtures + assertions updated to canonical format
- Final: 1 failed (pre-existing `test_strategy_causal_chain` LLM dependency) / 470 passed

**Invariant verified:**
```
binance:BTC/USDT:spot  ≠  bybit:BTC/USDT:spot  (different exchange)
binance:BTC/USDT:spot  ≠  binance:BTC/USDT:perp (different market type)
```

**Architecture decision:** `docs/DECISIONS.md` 2026-06-08 "InstrumentId" entry.

---

## Prior Change (Full-Market Analyzer + Paper Trading Pipeline Fix — 2026-06-08)

Three-bug paper trading fix + full-market analyzer expansion.

**Paper trading pipeline bugs fixed:**
- `data/config.yaml` — `autonomous_paper_trading: false → true` (main switch was off)
- `aletheia/agents/market_observer.py` — per-symbol loop (one LLM call + one Opportunity per symbol); previously only symbols[0] got an opportunity
- `aletheia/agents/paper_trader.py` — `allowed_symbols` now uses `to_canonical()` so Bitget-format config entries match canonical opportunity symbols
- `aletheia/portfolio/pnl.py` — `_latest_price` now uses `latest_snapshot()` dual-format lookup instead of direct DB query

**Full-market analyzer — new agents:**
- `aletheia/agents/stock_observer.py` — US equity observer; yFinance daily OHLCV + indicators (SMA/RSI/ATR/Bollinger/momentum); `asset_class="equity"`, observation-only; scheduled Mon–Fri 14:30–21:00 UTC
- `aletheia/agents/prediction_observer.py` — Polymarket prediction market observer; Gamma API (public REST); `asset_class="prediction"`, observation-only; scheduled every 30 min
- `aletheia/data/polymarket_provider.py` — Polymarket Gamma API provider (httpx + urllib fallback)

**Config expanded:**
- `observed_symbols`: BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, DOGE/USDT
- `observed_stocks`: SPY, QQQ, NVDA, AAPL
- `observed_prediction_markets`: crypto, politics

**Schema + migrations:**
- `aletheia/config/schema.py` — `MarketDataConfig` extended with `observed_stocks`, `observed_prediction_markets`
- `aletheia/storage/models.py` + `migrations.py` — `asset_class` column on `opportunities`

**Tests fixed:**
- `tests/test_entry_logic.py` — 2 tests updated for per-symbol loop (assert BTC row specifically)
- `tests/test_paper_autoloop.py` — seed symbol changed `BTCUSDT → BTC/USDT` (canonical format)
- `tests/test_runtime.py` — job count updated `11 → 13` (+ stock_observer + prediction_observer)

**Test result:** 398 passed, 19 failed (all 19 are pre-existing; net +4 fixed vs prior baseline).

**Scope decision:** user confirmed system is post-MVP; Polymarket and additional exchanges (xStocks etc.) are now in scope. See `docs/DECISIONS.md` 2026-06-08 entry.

---

## Prior Change (Symbol Format Unification + Data Audit — 2026-06-08)

Comprehensive data audit and symbol format unification across the full agent pipeline.

**Root problem:** CCXTProvider writes snapshots/OHLCV with canonical `BTC/USDT` keys, but
`market_observer.py` was converting to Bitget format (`BTCUSDT`) before storing opportunities,
causing paper_trader's filter to silently drop everything.

**Fixes applied:**
- `aletheia/agents/market_observer.py` — removed `to_bitget()` call; opportunities now stored in canonical `BTC/USDT` format (matching snapshot keys)
- `aletheia/agents/paper_trader.py` — `allowed_symbols` now built as `{to_canonical(s) for s in tradable_symbols_from_config(cfg)}` so Bitget-format config entries still match canonical opportunities
- `aletheia/risk/engine.py` — symbol check normalises both sides with `to_canonical()` before comparing
- `aletheia/server/routes/settings.py` — `/settings/symbols` endpoint now syncs `market_data.observed_symbols` (canonical) whenever tradable symbols are updated
- `frontend/src/api/client.ts` — `getOhlcv()` and `getMarketSnapshots()` both use `encodeURIComponent(symbol)` so `BTC/USDT` `/` doesn't split the URL path
- `frontend/src/components/MarketTicker.tsx` — ticker symbol keys changed from `BTCUSDT` to `BTC/USDT` to match snapshot map
- `frontend/src/pages/Charts.tsx` — opportunities filter uses exact canonical match (no `|| replace('/', '')` workaround)
- `tests/test_entry_logic.py` — 6 tests updated: assertions and fixture data changed from Bitget to canonical format; test renamed `test_observer_stores_canonical_format_symbol`

**Test result:** 22 pre-existing failures only (same as baseline). 0 new failures.

---

## Prior Change (Data Layer Refactor + Markets UI — 2026-06-08)

Complete data source decoupling from Bitget. Market data (OHLCV, tickers) now goes
through CCXT/yFinance; `BitgetClient` is execution-context only.

**New files:**
- `aletheia/data/__init__.py` — package
- `aletheia/data/providers.py` — `MarketDataProvider` Protocol, `Ticker`/`Candle` models (incl. `high_24h`, `low_24h`, `provider`)
- `aletheia/data/symbols.py` — canonical `BTC/USDT` format + per-provider converters
- `aletheia/data/ccxt_provider.py` — CCXT Binance (+ Bybit fallback), auto-persists OHLCV
- `aletheia/data/cache.py` — in-memory TTL cache (10 min default)
- `aletheia/data/yfinance_provider.py` — macro data (SPY/VIX/DXY/Gold), default OFF

**Changed files (backend):**
- `aletheia/config/schema.py` — added `MarketDataConfig` (provider, observed_symbols, cache TTL, yfinance flag)
- `aletheia/trading/market_data.py` — uses CCXTProvider; `latest_snapshot()` dual-format lookup
- `aletheia/trading/market_context.py` — uses provider protocol + cache; optional macro block
- `aletheia/trading/instruments.py` — added `observed_symbols_from_config()`; BITGET_GRANULARITY annotated execution-only
- `aletheia/storage/migrations.py` — new `market_ohlcv` table; `high_24h`/`low_24h`/`provider` columns on `market_snapshots`
- `aletheia/storage/repositories.py` — `upsert_ohlcv_candles()` + `get_ohlcv_candles()`
- `aletheia/server/routes/data.py` — `/api/market/ohlcv` uses CCXTProvider
- `aletheia/agents/market_observer.py` — uses `observed_symbols_from_config()`
- `aletheia/exchanges/bitget/client.py` — `get_ticker`/`get_ohlcv` annotated `# execution-context only`

**Changed files (frontend — FRONTEND_ISSUES §5.1–5.4 resolved):**
- `frontend/src/pages/Charts.tsx` — canonical `BTC/USDT` symbols; `ProviderChip` data-source label; `refetchInterval: 30_000`; H/L display; freshness indicator

**Unchanged:** `execution/`, `risk/`, `portfolio/exchange_sync.py`, `bitget/auth.py`

**Test result:** 424 passed (0 failed). 3 existing tests updated to mock CCXTProvider.

**Open next:** enable `market_data.enable_yfinance_macro: true` in config to activate macro block.

---

## Prior Change (2026-06-08 — SQLite 并发锁 + 核实)

**SQLite 并发锁**：`aletheia/storage/sqlite.py` 改为 `threading.local()` 每线程独立连接。`timeout=30`；`check_same_thread=True` 恢复。

**BACKEND_ISSUES §10 核实**：8 条高风险条目（strategy_id / live-order / asyncio / paper 风险 / clientOid / kill-switch / paper_trader Gate A / commit_all）全部 grep 验证已真修。

---

## Recent Change (WebUI redesign — frontend-only session)

Complete structural redesign of the React dashboard, addressing the owner's pain points
(information density / layout / transparency / workflow — "everything uncomfortable").
**No backend / API contract changes.** Diagnosis: the UI was "14 flat DB-table pages";
the framework wants §23.1's 6 roles organized around the causal chain + a workflow flow.

**Result: 14 pages → 10 role-organized destinations**, grouped by a 5-phase IA
(Observe / Decide / Execute / Evolve / Operate):
- **Cockpit** (already redone earlier): PnL zone + agent feed + right rail.
- **Approval Desk** (Proposals elevated): pending queue as decision cards w/ inline risk
  verdict + confirm-to-approve; decision history table.
- **Trades & Positions** (Positions + Paper Trades merged): open positions → orders/fills
  ledger → closed positions, hosting the **causal-chain inspector** (`CausalChain.tsx`).
- **Agent Activity** (Agent Runs + Opportunities merged): Runs/Opportunities tabs; run
  drawer has the Reasoning-Steps mount point.
- **Learning** (Dreaming + Knowledge Base + Reports merged): 3 tabs; §4 KB taxonomy kept.
- Keepers (Markets/Strategies/Risk & Controls/Settings/News) got a `PageHeader` with a
  one-line role descriptor; News got the spec-mandated "manual entry / ingestion not
  configured" honesty banner.

New shared components: `CausalChain`, `Tabs`, `PageHeader`. Deleted 7 merged page files.
Legacy routes redirect (no dead links). Verified live via preview MCP (nav, tabs, honest
empty states, zero console errors). tsc + vite build green.

**Docs:** `WEBUI_PAGE_MAP.md` (per-page verdicts + build log), `OVERVIEW_REDESIGN.md`
(Cockpit decisions), **`WEBUI_BACKEND_HANDOFF.md`** (parked backend gaps for the backend
agent), cross-referenced in `BACKEND_ISSUES.md §12`. Backend gaps (UI degrades honestly
until built): position→proposal FK, `GET /api/agent-runs/{id}/steps`, real exchange equity.

## Recent Change (feat/strategy-causal-chain)

Two passes on this branch (373 tests pass; branch not yet merged):

**1. Strategy↔trade causal chain (gap #2).** Connected the four-way break in the
`strategy_id` chain (the 2026-06-06 §1.1 "fix" had only wired the downstream fallback).
Now: `create_strategy` persists `parameters`; `strategy_designer` produces a parameter
spec, auto-advances a strategy to `paper_running` (ungated states only — live gates
untouched), and is scheduled daily; `market_observer` binds opportunities to an active
strategy. Promotion-gate `paper_trades` no longer stuck at 0. Makes paper evidence
trustworthy — prerequisite for north-star stage 1/2.

**2. Re-audit of BACKEND_ISSUES §10.** The "all fixed 2026-06-06" archive is ~75%
true, ~25% claimed-but-not. Eight falsely-archived issues really fixed (live-order
ordering, health-alert delivery, paper/demo risk bounds, clientOid, kill-switch
TOCTOU lock, paper_trader autonomous gate, commit_all scheduling, gateway log
rotation). §10 now carries a correction banner. **Lesson: never trust a "fixed"
claim without grepping current code** — see DEVLOG 2026-06-07.

Change-controlled modules touched (risk/, execution/, exchanges/): all changes are
hardening (tighter bounds, idempotency, serialization) — no live gate weakened, no
hard limit relaxed. Each has a focused regression test.

## Current State Summary

Post-MVP trading system with 8+ agents, 474 tests. Full-market observation pipeline operational (crypto + xStocks + US equities + prediction markets). Paper trading pipeline functional end-to-end. Key gap: experience loop (learning→decision feedback) only 30% closed.

## What Was Completed

- **All BACKEND_ISSUES §1 (trust invariants)** — Fixed: execution gateway transaction isolation, git pull ff-only, update confirm gate, strategy_id causal chain, promotion gates
- **All BACKEND_ISSUES §2 (data integrity)** — Fixed: ~25 items including PnL fees, knowledge status filtering, CORS, audit symmetry, risk evaluation, health endpoints, scheduler error handling, telegram bot hardening, model provider healthcheck, codex auth regex, markdown file safety, knowledge cleanup-on-failure
- **All BACKEND_ISSUES §3 (dead code/cosmetic)** — Fixed: ~15 items including orchestrator traceback, run_logger null fields, pidfile validation, redact format, market observer config, bitget orderId check
- **All FRONTEND_ISSUES §3 (engineering)** — Fixed: shared types, DataTable, DetailDrawer, chart theme, markdown styles, marker click callback
- **All FRONTEND_ISSUES §1-§2** — Fixed: proposal risk_decision display, KB markdown reading, autonomous_paper_trading visibility, effective config card, promotion gates display, chart-trade bidirectional linking, risk boundaries with units, dreaming 6-section layout, overview sparkline + run timeline, cross-page navigation, agent run structured tool calls, performance metrics in Reports

## What Is In Progress

Nothing currently in progress.

## Important Files

- `AGENTS.md` — Governance rules and safety boundaries (primary reference)
- `docs/BACKEND_ISSUES.md` — Backend issue tracker (§10 has fixed items)
- `docs/FRONTEND_ISSUES.md` — Frontend issue tracker (§10 has fixed items)
- `docs/aletheia_trading_os_implementation_guide.md` — Full design reference
- `docs/RUNBOOK.md` — Operations guide

## Important Decisions

See docs/DECISIONS.md and the existing AGENTS.md governance framework.

## Known Issues

Code-layer items in BACKEND_ISSUES.md and FRONTEND_ISSUES.md §1-§3 have been worked through. Two **architectural / north-star gaps** remain explicitly open and unresolved — they are not bugs to flip into §10, they are direction-of-travel work:

- **portfolio-sync-gap** — Portfolio dashboard does not reflect real exchange account state (only locally-created positions). Filed as BACKEND_ISSUES.md §2.3 last bullet. Touches change-controlled `aletheia/exchanges/bitget/client.py`; requires explicit authorization before work begins.
- **experience-loop-gap** — knowledge-write-only loop; agents rarely read prior research/reviews back into decisions. Tracked in `docs/north_star_references.md §6`. Stage-2 work per north-star sequencing; do NOT start before stage 0 (stable run) and stage 1 (learning quality) are proven.

Recent:
- 2026-06-07 cockpit merge resolved (`ace780a`); local 5-zone shell discarded after explicit review against AGENTS.md priorities. See DECISIONS.md and `docs/superpowers/specs/2026-06-07-merge-postmortem-design.md`.
- 2026-06-07 webui lightweight optimization: `.brand`/`.topbar` baseline aligned; `<AccountSnapshot/>` (Total Value with portfolio-sync-gap caveat / Today PnL / Open Positions / Pending Proposals) added to Overview + Paper Trades + Positions. Five-zone shell / Right Inspector / global Command Center deferred to stage 1+. See DECISIONS.md.

## Next Recommended Step

Per `docs/PIPELINE_STATUS.md` 北极星阶段图，当前进入**双轨推进**：

**观测主线（观测机）**：
1. 继续跑 Phase 0 的 72h unattended paper trading
2. 用 `doctor` / gateway status 审计 observation gate 是否全绿
3. 若 gate 失败,先修观测机的稳定性问题,再重置计时

**并行开发线（本 checkout / `worktree-dev`）**：
4. 把 Operator Layer / Pi Agent 收敛到 Stage 1(read-only supervisor) 的明确规格
5. 把 Command Center 专页与 `Ctrl+K` launcher 的职责边界钉死
6. 仅在 branch 上推进原型;不把 branch 进展当作 Phase 0 已完成

**后续（仍需 gate + 授权）**：
7. Phase 0 门全绿后再决定合并/上线 Operator Layer
8. Phase 1/2 的学习质量与经验闭环按 ROADMAP 继续推进

详见 `docs/PIPELINE_STATUS.md`。

## Commands to Run

```bash
uv sync
cd frontend && npm run build && cd ..
pytest tests/
uv run aletheia gateway start
```

## Warnings

- Live trading is disabled by default. Never enable without explicit user confirmation.
- Trust boundary modules (risk/, execution/, auth.py, secrets.py) require extra caution.
- Frontend must be rebuilt after source changes: `cd frontend && npm run build`

## Context for Next AI Session

All documented issues have been addressed. The system is ready for integration testing. Key architectural changes: execution gateway no longer wraps order placement in an outer transaction (§1.2 fix), git pull uses ff-only merge (§1.2 fix), promotion gates API exposed for frontend consumption.
