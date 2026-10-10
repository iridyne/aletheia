# Platform contracts 审计报告（docs/design/contracts/ 平台类 18 份）

审查者：platform_contracts。基线：docs/design/specs/README.md + spec 0001；产品真相：CAPABILITY_GROUND_TRUTH / ACTIVE_RUNTIME_SCOPE。
术语：**生产 writer** = 运行时默认配置下真的会写该表的代码路径；**fossil（化石）** = 文档或代码描述的机制已退役但仍被写成"当前"。

## Observation Lab 调用链实测（4 份 Lab 文档共用证据）

- 调度：`core/aletheia/agent_runtime/scheduler.py:1644-1649` 每 1 分钟注册 `observation_lab_bridge`；`:3487-3515` 若 `observation-lab/lab.db` 不存在直接 return（不建库），否则调 `extensions/observation_lab/runner.py:325 settle_observation_continuity()` → `canonical.import_delivery_receipts` + `mirror_lab_audit_outbox` + prune + compaction + `checkpoint.verify_joint_checkpoint`。
- 观测周期：`scheduler.py:1553-1567` 仅 `observation.execution_owner=='main'`（`config/schema.py:2146` 默认 main）时注册 `filter_controller_observe`；`:3418-3460` 在 `controller.shadow_enabled`（默认 False，`schema.py:2106`）时跑 `extensions/analytics/filter_controller.py:696 run_controller_observe`，否则只跑 `run_lab_windows()`。
- 已注册 window（`runner.py:478-532`）：仅 `filter_shadow_evaluate` 与 `durable_observation_windows`；**没有** UFC equivalence window（`ufc_shadow.py` 只剩只读解码，38 行）。
- **真正的生产 writer 在入场主线上**：`core/aletheia/main.py:363-370` 绑定 `extensions.learning.entry_context.load_entry_learning_context` → `core/aletheia/trading/entry_analysis.py:1044 resolve_entry_learning_context` → `extensions/learning/entry_context.py:255-297 resolve_entry_learning_treatment` → `extensions/observation_lab/canonical.py:1124 resolve_observation_treatment` → `:1832 append_delivery_receipt`（写 app.db `observation_delivery_outbox`，`persist_receipt=True` 默认）。另有 `extensions/learning/experiments.py:183/197`（`apply_numeric_treatment`，被 `knowledge/guards.py:352`、`learning/sizing_evidence.py:25`、`learning/economic_knowledge.py:143` 调用）。
- filter-shadow 注册仅在 `extensions/counterfactual/filter_experiments.py:146`（经 `run_controller_observe`，需 `shadow_enabled=True`），默认关。
- 规模：`extensions/observation_lab/` 21 个文件 12,671 行（canonical.py 3,321 行）；lab.db 约 25 张表（`db.py`、`canonical_schema.py`）。
- spec 0001 依赖它：`design.md:24` "reuses … the Observation Lab manifest/receipt/assignment machinery"，`spec.md:212` LL-R16 需要"dedicated Observation Lab adapter mutually exclusive with entry_learning adapter"。
- 结论：Lab **有**生产 writer（每次 Entry Planner 解析 learning context 都写一条 capture receipt），且是 spec 0001 的 A/B 分配底座，不能整体删；但其中 Worker 拆分（`deploy/systemd/aletheia-observer.*`）、filter-shadow（默认关）、`ranker_confidence_gate`/`observer_pre_filter`（Ranker、Market Observer 已退役）、UFC equivalence、`universe_family_calibration`/`ranker_case_score` handler（`runner.py:415-431`）是化石或休眠面。12.6k 行服务于"每次入场写一条 receipt + 跨库对账"属于明显过度工程化，但这是代码层问题，文档层先如实描述。

## observation_lab_contract.md（171 行）

**角色**：Contract，但混入"V2 migration boundary"迁移叙事（:18-20）和"Current deliberate boundaries"非目标清单（:142-154）。
**真相核查**：
- :48 "For the current ranker-confidence adapter" —— Ranker 已退役（`docs/design/history/ranker_paper_trader_filter_evidence.md`）；代码仍有 `extensions/observation_lab/filter_shadow.py:25,390` 的 `ranker_confidence_gate`，但没有生产 ranker 产出候选，属化石。
- :59-63 "legacy `filter_shadow_frozen_units` remains … while the authority row is `legacy`" —— 与 `observation_experiment_ownership_v2.md:64` 及代码 `canonical.py:437-438`（`set_authority_source` 只接受 `lab`）矛盾；legacy 已不可回退，`units.py` 仍是该表唯一 writer，属死表。
- :76-77 filter-shadow 支持 `observer_pre_filter`（Market Observer 已退役）和 `ranker_confidence_gate`（Ranker 已退役）。
- :97 "Current default windows are filter-shadow evaluation and durable Observation windows" —— 与 `runner.py:518-532` 一致（存在）。
- :99/:169 "ordinary Paper operation" —— 本地模拟盘已退役，应为"trading mainline / Bitget Demo entry path"。
- :82-91 main/worker 双 owner —— 代码存在（`execution_owner.py`、`deploy/systemd/aletheia-observer.service`），但 owner 已确认 Fedora 单实例、"只保留 master HEAD 一个版本"，Worker 拆分（两个 checkout 跑不同 commit）无现役用途。
- :112-121 `mechanism-aware-entry-learning/v1` adapter —— 存在（`extensions/learning/entry_context.py`），这是唯一触碰入场主线的部分，却埋在"Execution manifests"小节里。
- 缺失：文档完全没说"入场主线每次 planning 都经 `resolve_observation_treatment` 写 receipt"，也没说 lab.db 不存在时 bridge 静默 no-op（`scheduler.py:3496`）——这是最重要的当前行为。
**具体性**：多为原则性 prose（"must not create a second source of truth"），无编号要求、无 trigger→response、无失败语义表。Verification 小节是清单而非可映射到测试的 claim。
**有道理吗**：作为 spec 0001 A/B 分配与 receipt 账本的支撑功能有道理；filter-shadow/ranker/UFC/worker 部分无产品理由。
**Bloat**：:18-20 V2 迁移段、:142-154 "不暗示"清单、:27 引用冻结审计；issue 号 #2737。
**Verdict**：REWRITE（并 MERGE 另外 3 份 Lab 文档进来，成为唯一 Lab Contract）。
**具体改动**：
1. 新结构：Purpose → Entry points（`observation_lab_bridge` job、`resolve_observation_treatment` 消费者列表、`aletheia observe` CLI）→ Durable state（app.db `observation_delivery_outbox`；lab.db 表清单，标注哪些只读历史）→ 编号要求 `OL-01..` + 失败语义表（lab.db 缺失 → baseline + reason `lab_unavailable`、不建库；outbox 满 → `outbox_backpressure` baseline；authority 非 lab → baseline；checkpoint mismatch → behavior consumer 回 baseline）。
2. 删 :48、:76-77 中 ranker/observer 适配器为"current"的表述，改为"historical adapters `ranker_confidence_gate`/`observer_pre_filter` are decode-only; no producer"（若代码随后删除则整句删）。
3. 删 :59-63 legacy 兼容段，写"`filter_shadow_frozen_units` is historical read-only; authority is one-way `lab`"。
4. 全文 "Paper operation" → "the entry mainline"。
5. 删 :18-20、:142-154、:27；Worker owner 段标注为"supported but not deployed"或随代码删除（建议开 D2 issue 删除 worker 模式）。
6. 加一节"Spec 0001 consumers"：LL-R16 的 playbook arm adapter 将走同一 assignment/receipt 机制，与 `mechanism-aware-entry-learning/v1` 互斥。

## observation_lab_v1_design.md（204 行）

**角色**：自称"Current Design"，实为 Contract 的复述 + 实现形状；放在 `contracts/` 下是错位（contracts/ 只放 Contract；design 应属 Spec 的 `design.md` 或 architecture/）。且与 Contract 大段重复（Adapter causal identity :100-111 ≈ contract :44-55；Frozen units :113-124 ≈ contract :57-73；Manifests :126-136 ≈ contract :101-110；Verification 两份几乎一样）。
**真相核查**：
- :18-23 mermaid 与 :96-97 "Current registered work includes … UFC equivalence observation" —— **错**：`runner.py:478-532` 只注册 `filter_shadow_evaluate` 与 `durable_observation_windows`；Contract :97 自己也说 UFC equivalence 已退役。v1_design 与 Contract 互相矛盾。
- :18 `run_controller_observe` —— 存在（`extensions/analytics/filter_controller.py:696`）。
- :61 `aletheia.storage.sqlite.open_readonly_snapshot` —— 存在（`core/aletheia/storage/sqlite.py:845`）。
- :49 `ufc_shadow_equivalence` 表 —— 存在（`db.py:167`），只被 `observe_cmd.py:1345` 读、无 writer。
- :46-52 只列 5 张 lab 表，实际 canonical_schema.py 另有 13 张（`observation_generation_contracts`、`observation_results`、`lab_audit_outbox` 等），清单过时。
- :30/:93/:161 "ordinary Paper operation/trading" —— 模拟盘已退役。
- :142 "#1193" issue 号作内容。
**有价值的独有内容**：:150-152 `aletheia observe overview` 只读健康入口语义（不建库、exit 0、无合成新鲜度阈值）——这是可检验的当前行为，应保留进 Contract。
**Verdict**：MERGE into `observation_lab_contract.md`（仅迁 :150-161 的 overview 与 failure semantics），其余 DELETE；文件本身移除出 contracts/。
**具体改动**：把 :150-152 改写为 Contract 中的 `OL-xx` 要求（"When `aletheia observe overview` runs, it shall open both DBs read-only, never create lab dir/db/schema, and exit 0 for healthy|degraded|unavailable"）；把 :156-161 合进 Contract 失败语义表；删除整个文件并更新 `observation_lab_contract.md:26`、`observation_lab_backup_contract.md`、`docs/design/architecture/SYSTEM_MAP.md` 中对它的链接（`check_docs.py` 会查死链）。

## observation_lab_backup_contract.md（137 行）

**角色**：Contract，但开头 :3-14 是 Issue #1072 "Cluster 5 addition"、"verbatim" 引用 issue 正文；:22-24 写"future active manifest cache … Packet 2"——目标态混进 Contract。
**真相核查**：
- `compute_joint_checkpoint`/`save_joint_checkpoint`/`verify_joint_checkpoint` 存在（`extensions/observation_lab/checkpoint.py:408/469/541`）。
- `tests/test_joint_checkpoint.py`、`tests/test_update_settlement_hot_path.py` 存在。
- `observation_settlement_barrier` 被 `ops/update/auto.py:784`、`ops/update/continuity.py:1070` 使用——更新/发布路径确实依赖它（不是死代码）。
- `aletheia observe checkpoint --acknowledge` 存在（`core/aletheia/cli/observe_cmd.py:334`）；runbook `docs/operations/deployment/UPDATE_OBSERVER_CONTINUITY.md` 存在。
- :3 "Referenced from observation_lab_contract.md §5" —— 该 Contract 无编号 §5（它在 :25 用名称链接），引用失真。
**具体性**：本组里最好的一份——指纹公式、生命周期步骤、mismatch 爆炸半径都可检验。但 :80-95 "Owner-approved #3096 adds one bounded applied-delivery admission" 是一段 PR 叙事式 prose（一句 90 词），需拆为编号要求；`OBC-UPD-04` 是唯一编号且全文未定义其他 OBC 号。
**有道理吗**：为一个"每次入场写 capture receipt"的旁路账本，设计 10 字段跨库指纹 + 后缀重放恢复 + 更新期全局锁，代价远大于收益；更新路径（`ops/update/auto.py`）被这套对账绑架，一旦 mismatch 会阻塞部署。属于过度工程化的高风险点：建议 owner 判断"Lab 证据不一致时是否应阻塞交易系统更新"（D2）。文档层先如实写。
**Bloat**：:3-14 issue 摘录；:22-24 未来 Packet 2；:80-95 #3096 叙事。
**Verdict**：MERGE into `observation_lab_contract.md` 的 "Backup and update cut" 一节（保留 §2 公式、§3 生命周期、§5、§6、§7），或 KEEP 为独立文件但 REWRITE。推荐 MERGE，减少 Lab 文档从 4 份到 1 份。
**具体改动**：删 :3-14 与 :22-24（第三行"future cache"整行删）；把 :80-95 改写成 3 条 EARS：`If the strict checkpoint mismatches only by an applied Main receipt suffix whose replay reproduces the trusted Lab fingerprint and ledger, then update settlement shall admit the cut without saving a new baseline.` / `If any experiment, window, result or manifest evidence exists, then the admission shall refuse.` / `When the checkpoint matches and the frontier is caught up, activation settlement shall be a read-only no-op.`；修正 :3 的 §5 引用。

## observation_experiment_ownership_v2.md（106 行）

**角色**：自称 "Migration Contract"，记录 #2737 迁移边界——迁移已完成（`set_authority_source` 只收 `lab`，`canonical.py:437`；"old evaluator … scheduler jobs … removed" :66-67），剩余内容是迁移历史 + 一次性离线命令。混合 Contract/History。与同名 Proposal `docs/design/proposals/observation_experiment_ownership_v2.md`（status accepted）重复。
**真相核查**：
- :36-44 `observation_delivery_outbox` / `import_delivery_receipts` —— 存在（`canonical.py:1639`、`:2272`）。这是当前行为，但属于 Lab Contract 的内容。
- :46-51 `observation_results` —— 存在（`canonical_schema.py:146`）。
- :72 `aletheia observe migrate-learning-ledger` —— 一次性迁移命令；迁移完成后它是 history。
- :88-89 "#470/#447 historical UFC evidence"、:93-97 "#1072/#2614/#1135/#1008 remains current" —— issue 号当权威，违反"Issue 不授予语义"。
- :22-23 "Learning owns research, lessons, knowledge and pre-registration nominations" —— 与 spec 0001（Playbook 单一 store 取代 lessons/knowledge）冲突，需随 0001 落地修改。
**有道理吗**：迁移已完成，作为 Contract 不再有独立职责。
**Verdict**：SPLIT —— :36-60（receipt 传输语义、results 只读投影、evaluator 读 Lab exposure）MERGE 进 `observation_lab_contract.md`；:62-79 迁移命令与 :91-97 issue 兼容表 ARCHIVE 到 `docs/design/history/observation_experiment_ownership_v2_migration.md`；删除本文件；Proposal 标 `status: implemented`/superseded 指向 Lab Contract。
**具体改动**：在 Lab Contract 中新增 "Delivery transport" 要求：`When a production transaction writes a treatment/capture domain fact, it shall write the delivery receipt in the same transaction.` / `The importer shall consume receipts in sequence order and acknowledge Main only after the Lab transaction commits; a malformed present row stops the prefix and stays retryable.` / `A positive observation_results verdict shall never change a Main behavior policy.`；更新 `docs/design/data/data_model.md`、`RETENTION_POLICY.md`、`contracts/README.md`、`proposals/README.md` 链接。

## trade_decision_rationale_contract.md（59 行）

**角色**：Contract（单一），但开仓部分描述的是已退役机制。
**真相核查（关键）**：
- :8 "System Operator 唯一可执行的入场意图 `trade` 必须带有入场价、止损、止盈、名义价值和杠杆五项理由 … `scale_in`/`flip` 不属于当前活跃能力" —— 这是已退役的 PiDecision/`pi_trading_decision` 入场路径（commit 356d346a4 刚把同一路径从 `decision_lifecycle_contract.md` 移到 history）。当前入场是 Entry Plan：`core/aletheia/execution/entry_plan_execution.py:387` 写 `agent_rationale=plan.thesis`；理由来源是 `EntryPlan.thesis`（`trading/entry_plan.py:219`）+ `entry_hypothesis_json/hash`（`:283-284`）。
- :10/:16-24 `entry_parameters` 五项 JSON —— **全仓 Python 零 writer**（`grep -rn entry_parameters --include=*.py` 仅命中 `tests/test_trade_decision_rationale_ui.py:10` 一个源码字符串断言）；只有前端 `frontend/src/components/CausalChain.tsx:104` 读取。即：生产不再产出，UI 对新交易永远显示缺失。
- :24 `not_applicable_spot` —— 现货已出范围。
- :56 `PI-S05` 引用 —— 实际 `tests/specs/spec_pi_decision_chain.py:301` 的 PI-S05 是"native limit 四要素门"，且测试本身用 `market_type="spot"`；`decision_lifecycle_contract.md §2.3 parameter_rationale` 已不存在（grep 无命中，已移 history）。死链。
- 平仓侧当前有效：`positions.exit_reasoning` 写入 `core/aletheia/portfolio/pnl.py:649`（`trade_decision_rationale.v1`），`orders.close_reasoning`（`storage/models.py:501`）+ carrier 校验 `execution/exchange_materialization.py:294-310` 均存在，§1.4、§3 基本属实。:45 `max_hold`、"市场规则退出保存已有信号理由"需核对；:46 "System Operator 的组合复核"对应 `review_portfolio_state`（`agent_shell/registry.py` 存在）。
**具体性**：平仓侧较好（有 carrier fail-closed、NULL=未记录）；开仓侧描述的字段不存在。
**有道理吗**：入场理由可追溯是复盘→教训（spec 0001 LL-R06 process review 需要 frozen input + hypothesis）的前提，主线必需。但当前真相是"入场理由 = Entry Hypothesis + Plan thesis + compiler 来源"，不是五项 LLM 参数理由。
**与 spec 0001 冲突/缺口**：LL-R01 要求每个 `EntryHypothesis` 增加 `p_target_first` 与 `lessons_considered`；本 Contract 应声明入场理由载体是 `entry_hypothesis_json`，供 LL-R01 字段落位。
**Verdict**：SPLIT + REWRITE —— 开仓 §1.1-1.3、§2 移 `docs/design/history/`（与 `decision_lifecycle_retired_entry.md` 同处），重写当前开仓理由；平仓 §1.4-1.5、§3 保留。
**具体改动**：
1. §1.1-1.3、§2 整段 → history；替换为："Entry rationale for a Universe-linked entry is the immutable `entry_hypothesis_json` (+`entry_hypothesis_hash`) and `EntryPlan.thesis` persisted with the Entry Plan; `trade_proposals.agent_rationale` carries `plan.thesis`. Stop/target/size/leverage provenance is the deterministic compiler output (`trading/entry_plan_compiler.py`), never attributed to the model unless the hypothesis field states it."
2. 删 :24 `not_applicable_spot`。
3. §4 Dashboard：说明 `entry_parameters` 对 Entry Plan 交易不存在，UI 应读 hypothesis/plan（或开 D0 issue 修 `CausalChain.tsx:101-104`，删掉死读取 + `tests/test_trade_decision_rationale_ui.py:10` 的源码字符串断言）。
4. §5 删 `PI-S05` 与 `decision_lifecycle_contract.md §2.3` 引用；改为编号要求 `TDR-01..` 列表并映射到现存测试。

## live_unlock_contract.md（192 行）

**角色**：Contract，混入实现状态（:7 "Last updated 2026-06-21"、:94 "当前实现状态（2026-07-21 更新）"、:104 "当前产品事实（2026-07-28）"）、待办（:190 "批次5核实并统一"）。
**真相核查**：
- 入口 `server/routes/live.py`：存在，路由是 `POST /api/settings/live-unlock|live-lock|live-preflight`（`live.py:15,27,33,182`）；`compute_scorecard` 门在 `live.py:82-91`（存在）；`first_live_confirmation_done` `live.py:60,167`；unlock 设 `operator.permission_mode="Auto"` `live.py:172`——均属实。
- :14 `config/schema.py:111-113` 行号过时（实际 `schema.py:435-440`）；:159/:165 `schema.py:116/118` 过时（实际 `:444/:457`）；:180 `registry.py:415-419` 过时（实际 `:350`），且 FORBIDDEN_INTENTS 已多出 `deactivate_kill_switch`、`modify_permissions` 等，文档清单不全。
- :69 审计 message "PI operator elevated to Auto" —— 代码已改为 "System Operator elevated to Auto"（`live.py:151`）；且代码 before/after 含 `operator_permission_mode`，文档示例缺。
- **§4b :107-108、:131 "当前生产 Proposal 入口仍读取 `exchange.paper_execution_mode`"** —— 字段名仍存在且仍是事实：`trading/entry_plan_compiler.py:133` `execution_account_mode = str(cfg.exchange.paper_execution_mode)`，即 Entry Plan 永远走 demo 通道，live 路由不存在。所以语义正确，但 (a) "paper" 一词在模拟盘退役后误导（该键现装 `bitget_demo`，`schema.py:303`）；(b) 说的"Proposal 入口"已不是 PI 而是 Entry Plan Compiler，应点名 `entry_plan_compiler.py:133`。
- :79 "真实下单 (`bitget/okx/binance_live_limited`)" —— OKX/Binance 已冻结，应只写 Bitget 或写 "<selected broker>"。
- :135-136 `_configured_paper_execution_mode` 已删的历史说明 —— bloat。
- :171 `PaperAuto` "触发 paper 交易" —— 模拟盘已退役，语义需改为 demo。
- :190 路径偏差警告：`docs/governance/policies/trust_and_change_control.md:499,583` 仍写 `agent_runtime/permissions.py`，代码在 `agent_shell/permissions.py`——警告属实但应修 trust 文档而非在此留 TODO。
**具体性**：§2 不变式、§4a scorecard 门（70 分/green/无 blocker/净收益>0）可检验，好；spec `tests/specs/spec_live_unlock.py`、`spec_kill_conditions.py` 存在。
**有道理吗**：Live 在 Scope 外但解锁门是安全边界，KEEP 合理；把 operator 提升到 Auto 是有风险的耦合，但属既定语义。
**Verdict**：REWRITE（同角色，去日期/状态叙事，改编号要求）。
**具体改动**：删 :7、:94-96 日期段、:104 日期；§4b 首段改为 "Live unlock persists `live_limited` and sets `operator.permission_mode=Auto`; it does not route Entry Plans to live: `entry_plan_compiler.py` sets `execution_account_mode` from `exchange.paper_execution_mode` (the demo account mode, e.g. `bitget_demo`)."，并注明键名是历史名、值是 demo 模式；:79 改为 `<selected broker>_live_limited`；更新所有行号或改为符号引用；§6 FORBIDDEN_INTENTS 改为"见 `agent_shell/registry.py FORBIDDEN_INTENTS`，至少包含…"不再复制清单；删 :135-136、:190（并在 trust 文档修路径）；:69 message 同步代码；:171 PaperAuto 描述改 demo。

## template.md（33 行）

**角色**：Contract 模板（单一）。
**现状**：8 个标题 + 每节一句提示；无编号要求、无失败语义表、无"unavailable vs zero"、无 SpecClaim 映射、无 front matter。对比 `docs/design/specs/README.md` 规则 2-4（`<PREFIX>-R<NN>` + EARS + WHEN/THEN）与 `specs/_template/`，Contract 模板明显更弱——这正是本目录多数 Contract 写成原则性 prose 的根因之一。
**有道理吗**：Spec 的 `Contract Delta` 需要把 `ADDED/MODIFIED/REMOVED` 落到 Contract 的具体条目上；Contract 没有条目编号就无法精确 delta，也无法让 `SpecClaim.claim_id` 一一对应（README :25-51 自己承认 claim↔Contract 对齐靠人工）。
**Verdict**：REWRITE（升级为带编号要求 + 失败语义表的形式，对标 specs）。
**具体改动**（新模板骨架，可直接替换）：
1. Front matter：`prefix: <XX>`（与 `tests/specs/spec_*.py` 的 claim 前缀一致）、`owner_code:`（SYSTEM_MAP 中的源路径）、`specs:`（已 implemented 的 spec 列表）。
2. `## Scope` 一段：负责什么、不负责什么（链接其他 Contract，不复制）。
3. `## Entry points` 表：`trigger | entry symbol (path:symbol) | caller`。
4. `## Durable state` 表：`table/file | key | writer | consumers`。
5. `## Requirements`：每条 `### <XX>-C<NN> 标题` + 一句 EARS（Ubiquitous/Event/State/Unwanted/Optional 之一）+ `WHEN … THEN …` 场景 + `Claim:` 指向 `tests/specs/…::<claim_id>`；编号永不复用，退役条目保留编号标 `RETIRED → history/<file>`。
6. `## Failure semantics` 表：`condition | response | visible as (reason token / status) | unavailable vs zero | retry/recovery`。
7. `## Authority` 一行：是否能影响 Risk/Gateway/Live/credentials（默认 "none"）。
8. 删 `## Known gaps`（指向 Issue 即可，不在 Contract 中维护；或保留但限定"只放 Issue 链接，无描述"）；明确禁止日期、"当前实现状态"、PR/issue 叙事。
同时在 `scripts/ci/check_docs.py` 增加对新 Contract 的 front matter / 编号唯一性检查（复用 specs 的检查逻辑），旧 Contract 在下次修改时迁移。

## README.md（135 行）

**角色**：目录索引 + 写 claim 的流程指南 + 零散"谁拥有什么"段落，混合 Contract 索引与开发流程（:25-75 属 `tests/AGENTS.md` 或 DEVELOPMENT_WORKFLOW）。
**真相核查**：
- :77 "until the explicit authority-cutover gate is satisfied, legacy Experiment authority remains active" —— **错**：`extensions/observation_lab/canonical.py:437` 只接受 `lab`，ownership_v2.md:64 也说回退被拒；README 与被引 Contract 矛盾。
- :15-16 "Future targets … belong in `../proposals/`" —— 与 `docs/design/specs/README.md:5-7`（新设计进 specs/）冲突，过时。
- 索引不全：目录 42 份 .md，README 未提及 24 份（本地 `for f in *.md; grep -q` 计数）。
- :34-51 举例 `LU-C07`、`#418`、`AC-S13`、`DM-S06`、`RE-S99` 和日期——案例叙事属 DEBUG_AUDIT_PLAYBOOK（:73-75 已链接），重复。
**Verdict**：REWRITE。
**具体改动**：(1) :15-16 改为 "Future targets go to ``../specs/`` (new) or existing `../proposals/`"；(2) 删 :77 整段；(3) 把 :79-135 零散段落替换为一张完整表 `Contract | prefix | owns | owner code`，覆盖全部 42 份（或由 `check_docs.py` 生成/校验）；(4) :25-75 移到 `tests/AGENTS.md`（或 DEVELOPMENT_WORKFLOW），README 只留一行链接；(5) 加一句"Contract 条目编号与 Spec Contract Delta 对齐，见 template.md"。

## macro_source_time.md（21 行）

**角色**：Contract（单一），短小。
**真相核查**：`fresh_macro_row` 存在（`core/aletheia/data/freshness.py:166`），`source_times_json` 列存在（`storage/migrations.py:8390`），claim `MS-C09` 存在（`tests/specs/spec_macro_signals.py`），`docs/design/market-systems/decision_context_freshness.md` 存在。消费方实际在主线：`trading/entry_snapshot.py`、`data/context_builder.py`、`trading/persona.py`、`extensions/regime/collector.py`；采集在 scheduler 十分钟 context lane（`scheduler.py:4216`）。
- :17 "Market Context/Observer" —— Market Observer 已退役，删 "Observer"。
- :3-4 "Owner-approved #1465 supersedes … within #3065" —— issue 号当内容；:16 "Additive schema v83" 迁移版本是历史。
**具体性**：要点式，trigger/consumer 不明确（未列 `fresh_macro_row` 的调用方，未写字段被拒时 consumer 看到什么）。
**Verdict**：KEEP（小改），或 MERGE into `decision_context_freshness.md`（后者已拥有时钟限值，见 :19-20；一事一处）。推荐 MERGE。
**具体改动**：删 :3-4 issue 叙事与 :16 "v83"；:17 改为 "current repository/API, Market Context and Regime use the projection"；补一条 Unwanted 要求："If a TradFi field's source time exceeds the context gate, then `fresh_macro_row` shall return the field as unavailable and the regime classifier shall recompute from admitted inputs only (never neutral-filled)."

## configuration_contract.md（74 行）

**角色**：Contract（单一）。
**真相核查**：`load_config`/strict unknown-field 行为、`config.validation` Doctor blocker、proxy userinfo 拒绝——测试文件 `tests/test_config.py`、`tests/test_doctor.py`、`tests/specs/spec_configuration.py`、`tests/test_network_cli.py` 均存在。legacy 解码 `observed_stocks`→`observed_equities`、`observed_xstocks`→`observed_stock_tokens` 存在（`config/schema.py:333-345`）；`POST /api/settings/equities` 存在（`server/routes/settings.py:2113`）。
- :9-11 断句错误："The loader raises a / Pydantic validation errors retain …" 半句话丢失。
- :24-49 equity / tokenized-equity 观测设置：普通股票与代币化股票均在产品范围外（CAPABILITY_GROUND_TRUTH "Ordinary equities … outside active instrument scope"；RWA perp 黑名单）。但代码仍在跑：`scheduler.py:1585-1591` 注册 `equity_observer` job，`extensions/research/equity_observer.py`，`trading/market_data.py:130-140`、`trading/instruments.py:37` 读 `observed_stock_tokens`。这是 fossil 观测面：Contract 如实描述了它，但应标注"outside product scope, frozen"，并开 D2 issue 退役 equity_observer 与两个 workset（spot/equity 已出范围，保留即违背"一事一实现、删掉输家"）。
- :35/:53 "#3134"、"#1642, owner decision 2026-10-09" —— 后者是决策引用，可保留为 DECISIONS 链接。
**具体性**：proxy 段写得好（trigger→422→不改存盘→不回显值）；前段 prose 化。
**Verdict**：KEEP（小改）+ 对 equity 段标冻结。
**具体改动**：修 :9-11 断句为 "The loader raises a Pydantic `ValidationError`; errors retain the dotted rejected path but never include the rejected input value…"；:24-49 两节合并为一节 "Frozen equity observation worksets (outside product scope)"，保留 legacy 解码规则，删 "Scheduler, observer and Settings use only the canonical list" 之外的叙事；按模板加编号 `CFG-C01..`。

## dashboard_data_presentation_contract.md（72 行）

**角色**：Contract（单一），结构已按 template.md 写，是本组格式最规整的一份。
**真相核查**：`frontend/src/components/DataState.tsx`、`DataTable.tsx`、`pages/Learning.tsx`、`DataTable.test.tsx`、`lib/learningWorkbench.test.ts` 均存在。
**有道理吗**：五态（loading/available/empty/stale/unavailable）是全局"unavailable ≠ zero"原则的前端落点，有价值；但范围只覆盖 Learning Knowledge 表——而 Learning Knowledge 将被 spec 0001 的 Playbook 取代（Knowledge 表的前途未定）。核心价值在交易页（Positions/Orders/Outcomes）却未覆盖（:67-68 自认）。
**Bloat**：:65-72 "Current scope boundary" 列出未被保证的分支，可压成一句。
**Verdict**：KEEP（小改）；建议把 scope 从 "Learning Knowledge" 改为 "shared `DataTable` state vocabulary" 并在 spec 0001 落地时把 consumer 换成 Playbook 视图。
**具体改动**：:5-8 改 scope 为"shared DataTable state vocabulary; current audited consumer: Learning Knowledge tables"；表格行加编号 `DDP-C01..05`；:45-46 compatibility 规则保留（这是真实行为）；:65-72 压为一句。

## status_surface_contract.md（135 行）

**角色**：Contract（单一），按 template.md 写，结构好。
**真相核查**：派生顺序 1-15 与 `frontend/src/lib/systemState.ts:149-200` 逐条一致；`_TRADE_BLOCKING_MODES = {Repairing, Degraded, Paused}`（`:82`）一致；`core/aletheia/server/operator_service_auth.py`、`tests/test_api_consumer_paths.py`、`tests/test_operator_service_auth.py`、`frontend/src/lib/systemState.test.ts`、`frontend/src/components/StatusCluster.test.tsx` 均存在（:121 "where present" 可删）。Producer 为 TS operator-shell，与 "sole Operator" 一致。
**具体性**：本组最接近 spec 形式——顺序即合同、失败语义按场景列、coupling rule 有测试守护。缺编号。
**有道理吗**：主线必需（运维判断能否交易）。
**Bloat**：:9-14 与 :94-96、:103-109、:126-129 反复讲 `#3702/#3763` 事故叙事（4 处），属 history/research。
**Verdict**：KEEP（小改）。
**具体改动**：删 :11-14 事故叙事（保留 "derived in the browser from independent backend facts" 一句）；:96、:103、:128 的 `#3763` 去掉，改为通用描述；派生顺序 1-15 每条加编号 `SS-C01..C15` 并在 `systemState.test.ts` 用同 id；:121 删 "(where present)"；:124-129 "Current scope boundary" 第一条是待办（"tracked in #3763"）→ 删，只留一句 "The panel shows mode, not `last_error`"。

## settings_control_contract.md（130 行）

**角色**：Contract（前端控件持久化语义）+ 63 项清单（inventory）。:35-43 Webhook 投递真相与 Switch 持久化无关，是混入的第二主题。
**真相核查**：
- 清单项真实存在于前端，但包含多处**已退役/出范围能力的控件**：`features/settings/tabs/ExecutionTab.tsx:237` "Spot" 市场开关（现货已出范围）；`AgentModelsTab.tsx:310` "Enforce in paper ranker"（Ranker 已退役）；`SignalsTab.tsx:273-316` "Prediction observer"（预测市场出范围，但 `scheduler.py:1595-1601` 仍注册 `prediction_observer` job）；`AgentModelsTab.tsx:112` `updatePiDaemonConfig`（PI 命名遗留）；`features/settings/execution/PaperTradingSettings.tsx`（paper 命名遗留，实为 demo 参数）；:126 "Legacy risk limits" 一组。Contract 只是如实盘点；问题在代码与产品范围不符。
- :32 "Risk, execution, and paper-trading controls remain staged" —— paper-trading 已退役，应改 "demo trading"。
- 63 处计数：我粗略 grep `<(Switch|Toggle)` 得 62（多行标签可能漏计），以源码测试为准，未作结论。
**具体性**：Switch API 规则清晰（必须显式 `persistence`、immediate 保留确认值直到成功、staged 走 Save + discard 确认）。但 63 行清单是代码事实的复制（"one owner per fact"：测试已保持计数与 prop），维护成本高且每次加控件都要改 Contract。
**有道理吗**：持久化语义有价值（防止误以为已保存）；清单可由测试承担。
**Verdict**：REWRITE —— 保留 :8-28 规则（编号 `SC-C01..`），清单移除或缩为"测试 `<test file>` 维护站点清单与计数"；:35-43 Webhook 投递 MOVE 到 notification/webhook 相关 Contract（若无，则独立一小节并标前缀 `WH-`）。
**具体改动**：删 :45-126 清单（或移 `frontend/` 测试 fixture）；:32 paper→demo；开 D1/D2 issue：删除 Spot 开关、"Enforce in paper ranker"、Prediction observer 开关 + `prediction_observer` job（出范围能力的控制面不应留在 Settings）；`updatePiDaemonConfig`/`PaperTradingSettings.tsx` 改名属代码清理。

## audit_chain_contract.md（365 行）

**角色**：Contract（L2 信任边界），混入实现代码复制（§1-§5 大段 Python 片段）、已知缺陷（§7 本质是 Issue 内容）、改动控制流程（§8 复述 trust 文档）、日期（:9 "最后更新 2026-08-07"、:271 "2026-06-29"）。
**真相核查**：
- `core/aletheia/files/jsonl.py`：`_GENESIS_HASH`(:17)、`_rotate_jsonl_if_needed`(:129)、`_hash_record`(:149)、`write_audit`(:386)、`verify_audit_chain`(:497) 存在；`_compute_proposal_hash` 实际在 `storage/repositories_execution.py:48`（文档 :7 写 `repositories.py`，错位）；`insert_trade_proposal` 在 `repositories.py:1099`；`adopt_position` 在 `portfolio/position_reconciler.py:1086`；`tests/test_audit_hashchain.py` 存在。
- **§5 :142-159 doctor 代码片段与现实不符**：`diagnostics/probes/storage_trust.py:150-208` 现按 `EFFECTIVE_CHAIN_FIX_TS="2026-07-03T08:00:00+00:00"` 分三档——全部断点在修复前 → `info`；修复后断点 → `warn`；无法定时 → `warn`。文档 §5 表、§6.2、§7.1、§7.2 都说"链断即 warn"，过时。`verify_audit_chain` 返回值还多 `error_timestamps`（:501），文档 :178 缺。
- §6.3 表 :220、§6.3c :249-258 列 `paper_position_funding_settled/economics_repaired/corrected` 与 "paper cash"——本地模拟盘已退役，`funding_audit.py:17-19` 仅保留这些类型以读/镜像历史行，全仓无新 producer（grep 无写入点）。且漏列 `exchange_position_fee_completed`（`funding_audit.py:14`）。
- §6.3 :219、§6.1 :195、§6.3a 引用 Observation Lab lifecycle outbox 作为实验审计源——与 Lab Contract 一致，但 Lab 的 `lab_audit_outbox` → JSONL 镜像机制（`canonical.py:2965 mirror_lab_audit_outbox`）在本文未定义 event_type。
- :308 设计参考 `OPENALICE_TAG_DEEP_DIVE`、"TaG P0-1/P1-2" 项目代号作内容。
**具体性**：链算法、跨轮转、两链对照表、§6.3b stable id、§6.3c outbox 状态机都可检验，内容扎实；但 365 行中约 40% 是代码复制与历史。
**有道理吗**：主线必需（审计 = 安全不变量）。
**Verdict**：REWRITE（同角色，删代码复制、改编号要求，§7 缺陷 → Issue）。
**具体改动**：
1. 删 §1-§5 中所有 Python 片段（:25-30、:34-38、:81-84、:114-128、:142-159），改为符号引用 + 一句行为要求（例："`write_audit()` shall hold the in-process lock and the cross-process file lock across read-tail → inject `_prev_hash` → append"）。
2. §5 报告表改为三档：`no records → info`；`breaks all before EFFECTIVE_CHAIN_FIX_TS → info`；`any post-fix or undateable break → warn`；删 §7.1 "症状/可能根因"叙事与 §7.2，改为一条 `AC-Cxx`："Doctor shall not block startup on a chain break (severity ≤ warn)."
3. §7.3 proposal-chain 无探针 → 开 Issue，Contract 只留"No runtime probe verifies the proposal chain; only DM-C02 does"。
4. §6.3 表删 paper funding 三类为 current producer，改为 "historical event types, readable/mirrored only"；补 `exchange_position_fee_completed`；§6.3c 删 "paper cash"。
5. :7 修 `_compute_proposal_hash` 路径；删 :9、:271 日期、:308 参考；§8 改为一行链接 `trust_and_change_control.md §6`，保留"禁止的改动类型"清单作为 `AC-C` 不变量。

## persistence_recovery_contract.md（190 行）

**角色**：Contract，但标题是 "Persistence Contract"（文件名含 recovery）；内容混合 4 个主题：持久化总论、managed backup/restore + update rollback、research bundle import、CSV export。另含大量"已退役/不保证什么"的免责（:7-8、:10-24、:59-64、:73-83）。
**真相核查**：
- `/api/persistence/*` 存在（`server/routes/persistence.py:18`）：`/backups`、`/backups/verify`、`/export`、`/import`（`:246/261/284/300`）均在；`EventStore`（`storage/event_store.py:87`）在；`migration_preset_removed`（`server/routes/export.py:275`）在；测试 `tests/specs/spec_persistence.py`、`tests/test_export_routes.py`、`tests/test_exit_trailing_qualification.py`、`tests/test_exit_breakeven_shadow.py` 均在。
- 同一路由前缀下仍挂着化石：`/api/persistence/gateway/status` 恒返回 `not_available`（`persistence.py:77-80`），`/gateway/sessions`、`/gateway/reconciliation-reports` 读退役 `ps_gateway_sessions`/`ps_reconciliation_reports`；`/lessons*`、`/skills`、`/policies*`（`:324-447`）是 PS lesson/policy 路由——与 spec 0001（Playbook 取代 ps_lessons）冲突。Contract :23-24 只泛称 "read-only history endpoints remain"。
- :130-131 bundle import 允许 `ps_lessons`、`ps_skills`（实现在 `extensions/sync/importer.py`）——spec 0001 落地后 lessons 不再是 store，import 允许清单需随之改。
- :14-17 "Schema v12 retains … `ufc-fixed-look/v1/v2` … `learning_inference_decisions`" —— 属 Learning/UFC 历史，放错文件。
- :41-42 `position_exit_plan_versions`、`exit_breakeven_shadow_*` 等 read-only 历史表——如实。
**具体性**：backup 两档（online vs authoritative）、in-place rollback 保留规则（:100-125）、config 三种 disposition、audit family 不拼接——写得精确可检验，是本文件的核心价值。前半总论空泛（"SQLite repositories … provide durable application state"）。
**有道理吗**：backup/rollback 是部署安全必需；bundle import 研究数据、CSV export 是支撑功能；PS 历史端点是化石。
**Bloat**：:10-24 三段退役/schema 历史；:81-83 "This retirement does not claim…"；:101 "(owner decision 2026-10-09, #3765)" 可保留为 DECISIONS 链接。
**Verdict**：SPLIT —— (a) `backup_restore_contract.md`（:65-68、:85-125，编号 `BR-C..`，并吸收 `observation_lab_backup_contract.md` 的 "update activation cut" 交叉引用）；(b) 本文件保留 bundle import + CSV export + 持久化不变量；(c) :10-24 → 一行链接 history。
**具体改动**：删 :10-17；:19-24 压为一句 "Retired PS recovery: see history/gateway_recovery.md; `/api/persistence/gateway/*` returns `not_available` or reads archived `ps_*` rows only"；:130-131 加注 "the `ps_lessons`/`ps_skills` allowance changes when spec 0001 replaces lessons with the Playbook"；开 D0/D1 issue 删除 `/api/persistence/gateway/*` 三个化石端点（`gateway/status` 恒 not_available 无消费价值）。

## llm_inference_harness_contract.md（338 行）

**角色**：Contract（单一主题：逻辑调用身份/提示/上下文/工具/输出/观测），但 :15-65 混入 Provider Settings 路由、SDK 传输、凭证投影等"模型端点管理"内容（应属 model-provider/settings Contract）。
**真相核查**：
- `llm_complete/llm_structured/llm_stream` 在 `core/aletheia/agents/helpers.py:279/690/1099`；`CALLER_SPECS` 在 `core/aletheia/model_provider/inference_contract.py`；`agent_shell.entry_planner` caller 在 `agent_shell/entry_planner.py:670`；`technical-context-v1` adapter 在 `helpers.py:99,174-204`，被 `trading/market_context.py:286` 使用——存在。生成的 caller 矩阵由 spec 校验（`tests/specs/spec_llm_inference_harness.py`），可信。
- :136 "`technical-context-v1` … identifies the Market Observer's dynamic closed-bar block"、:199-200 "Market Observer and News Analyzer select `none`" —— Market Observer 已退役（:333-338 自己也这么说），自相矛盾；实际 `reasoning_effort="none"` 调用点在 `agent_shell/commands.py:2047`。
- :113 Environment 层 "paper execution mode" —— 字段来自 `helpers.py:160` `execution_mode=cfg.exchange.paper_execution_mode`，值是 demo 模式；措辞应改 "selected demo execution mode"。
- 矩阵中 `equity_observer.market_read`、`prediction_observer.market_read`（出产品范围但仍注册）、`dreaming.lesson_extraction`、`lesson_reviewer.judge`、`dreaming.nightly_consolidation`（spec 0001 将以 process review/Playbook 替代）——Contract 如实反映代码，但这些 caller 是 fossil/待替换候选。
- :37-38 "does not claim completion of the remaining HTTP-only endpoint/routing retirement work" —— 进度状态。
**具体性**：失败语义（cancel→error 再抛、schema 失败不成功、fallback 不隐式发现候选、telemetry fail-open）写得具体可测；不变量 5 条清晰。缺编号；长段落（:20-38 一段 19 行）难以对照测试。
**有道理吗**：主线必需（Entry Planner 的可审计性、spec 0001 的 offline re-run LL-R20 要按 prompt/template 版本对比）。
**与 spec 0001**：LL-R01 新字段（`p_target_first`、`lessons_considered`）会改 `agent_shell.entry_planner` 的 schema → 需新版本行（如 `v3`）；Playbook arm 应作为 Context 层 `ContextArtifact`（content-free）记录，本 Contract 是落点，应在 spec 0001 的 Contract Delta 中列出（spec.md:271-277 目前只列 learning/entry-plan/knowledge 三份，漏了本文件）。
**Verdict**：REWRITE（保留内容，编号化 + 拆出 provider 管理）。
**具体改动**：(1) :28-65 移到 model provider/Settings Contract（或新建 `model_endpoint_contract.md`），本文件留一句链接；(2) 删 :136 与 :199-200 中 "Market Observer"，改为当前调用者；(3) :113 "paper execution mode" → "selected demo execution mode (`exchange.paper_execution_mode`)"；(4) 删 :37-38；(5) Failure 小节每条编号 `LIH-C01..`；(6) 在 spec 0001 的 Contract Delta 补本文件。

## profit_loop_observability_contract.md（449 行）

**角色**：Contract，但塞了 6 个子能力：Position replay、Position Lifecycle Story、candidate facts 分母、固定反事实注册表（first_60_minutes）、profit-gradient v3 七层门、Position Path Observer（**一个调度 writer**）、lifecycle diagnosis。另含运行时读数与进度。
**真相核查**：
- CLI `aletheia inspect position-replay|position-story|candidate-facts|profit-counterfactual|profit-gradient|lifecycle` 存在（`core/aletheia/cli/inspect_cmd.py:328,373` 等）；分析代码 `core/aletheia/analytics/{profit_gradient*,profit_loop_*,position_replay,candidate_facts}.py` 合计约 6,370 行；`position_hypothesis/v1`（`storage/proposal_hypothesis.py:15`）存在；evidence anchor 在启动时由 `core/aletheia/server/app.py:528` → `extensions/learning/evidence_epoch.py:132` 建立（属实）。
- "Durable state: No replay … table exists"（:72），但 :294-317 描述的 Position Path Observer 是 scheduler job `position_path_observer`（`agent_runtime/scheduler.py:1466`），持久写 5m 路径观测与 deferred queue——同一文件内 "read-only" 与 "durable writer" 并存，角色混乱。
- :26 "does not change Ranker, System Operator…"——Ranker 已退役。
- :326-331 "The Fedora Paper evidence checked during implementation contained 327 closed Positions … 1,414 retained ADA perpetual one-minute rows" —— 运行时读数 + paper 字样，违反 "no runtime readings in Contract"。
- :443-449 Known gaps：部署 cohort、"#964/#974"、"2026-09-12 cutoff" —— 进度状态。
- :10-13 "The owner + ChatGPT decide the next optimization direction manually" —— 把外部工具写进 Contract，无意义。
- :88-93 Serenity/Opportunity-origin 祖先链仍作为现行路径（Serenity 为 Experimental，CAPABILITY_GROUND_TRUTH 表）——可保留但需标 experimental。
**有道理吗**：Position replay + Lifecycle Story（复盘一笔交易的完整路径）直接服务"复盘→教训"主干，且 spec 0001 LL-R06 process review 需要它，应保留；Path Observer 是其数据底座，保留。但 profit-gradient 七层归因 + 反事实注册表 + "unique_max_direction" 判决 是"量化因子挖掘"式工具，与 owner 目标（AI 像人一样交易，不挖因子）不符，且结果单位（24h net return / 60 分钟反事实）与 spec 0001 统一的 R 单位冲突（spec.md:277 明确 learning 单位改为 judgment label R）。
**Verdict**：SPLIT —— (a) `position_replay_story_contract.md`：replay、story、path observer（作为唯一 writer 写明 durable state + 失败语义）、lifecycle diagnosis；(b) candidate-facts / counterfactual / profit-gradient 并入 `profit_gradient_evidence_contract.md` 一份，标 "frozen analysis tool; not on mainline"；(c) 运行时读数与 known gaps 删除。
**具体改动**：删 :10-13 ChatGPT 句、:26 "Ranker"、:326-331 Fedora 读数、:443-449；:72 改为 "Read-only analytics persist nothing; the `position_path_observer` job is the sole writer of path observations (see §Path Observer)"；Path Observer 段编号化：`When a bucket provider fails transiently, the observer shall keep it in the deferred queue up to N retries, then materialize an append-only unavailable fact.`；在 Contract 中声明与 spec 0001 judgment label 的关系（judgment R 由 `analytics/signal_replay.py` 产出，不由本 replay 产出），避免两套 outcome 单位并列。

## profit_gradient_evidence_contract.md（113 行）

**角色**：Contract（单一：`profit_gradient_evidence/v2` 编译器）。:3 "Status: implemented" 是状态字样。
**真相核查**：`build_profit_gradient_evidence`（`core/aletheia/analytics/profit_gradient_evidence.py:1847`，文件 2,028 行）、`_LAYER_PATH_IDS`（`:76`）、CLI `inspect profit-gradient-evidence`/`profit-gradient`（`cli/inspect_cmd.py:373/328`）存在。
- :10-11 "owner + ChatGPT" 同上。
- :93-97 "No confirmatory layer-path mapping is currently registered. Empty mappings are valid" —— 即七层 layer_samples 当前**恒为空**，确认性分析无输入；只有 exploratory 输出（`promotion_eligible: false`）。2,028 + 1,067 + 522 行代码产出一个空的确认层。
**具体性**：cutoff/身份/OHLCV 可见性规则（:54-71）严谨可测。
**有道理吗**：与 profit_loop 同理——严谨但不在主线、确认层为空、单位与 spec 0001 不一致。属"过度工程化的分析工具"，建议冻结（保留 CLI，不再扩展），由 owner 决定是否删除（D2）。
**Bloat**：与 `profit_loop_observability_contract.md` :10-13、:140-200（profit-gradient v3 comparison method）重复同一主题，两份文档拥有同一事实。
**Verdict**：MERGE —— 接收 profit_loop 中 candidate-facts/counterfactual/gradient v3 部分，成为唯一 "Profit gradient (frozen analysis)" Contract；开头声明 "frozen: no mainline consumer; confirmatory layer mapping empty"。
**具体改动**：删 :3 "Status: implemented"、:10-11 ChatGPT；新增 Scope 一句 "No runtime behavior owner imports this module; outputs never feed Entry Planner, Risk or Learning"；把 profit_loop :140-280 的 v3 comparison/reconciliation 迁入并删重复。

## Cross-doc（跨文档）

**矛盾**
1. Lab authority 是否可回 legacy：`observation_lab_contract.md:59-63` 与 `README.md:77` 说 legacy 仍可存在/仍生效；`observation_experiment_ownership_v2.md:64` 与代码 `extensions/observation_lab/canonical.py:437-438` 说只接受 `lab`。以代码为准，前两处错。
2. UFC equivalence 是否在线：`observation_lab_v1_design.md:22-25,96-97` 说在线；`observation_lab_contract.md:97` 与 `runner.py:518-532` 说已退役。
3. Doctor 审计链级别：`audit_chain_contract.md` §5/§6.2/§7 说断链即 warn；`diagnostics/probes/storage_trust.py:150-208` 对修复前断点报 info。
4. `profit_loop_observability_contract.md:72` 说无持久表，同文 :294-317 与 `scheduler_contract.md:86`、`SYSTEM_MAP.md:64` 说 `position_path_observer` 持久写入。
5. `trade_decision_rationale_contract.md:56` 指向 `decision_lifecycle_contract.md §2.3 parameter_rationale`，该节已被 commit 356d346a4 移到 history。

**重复**
- Lab 四份文档反复写同一组不变量（causal identity、frozen units、manifests、零授权、verification 清单），一个事实四个 owner → 合并为一份。
- profit-gradient 语义同时在 `profit_loop_observability_contract.md`（v3 comparison/reconciliation）和 `profit_gradient_evidence_contract.md` 两处。
- macro freshness 同时在 `macro_source_time.md` 与 `market-systems/decision_context_freshness.md`。
- FORBIDDEN_INTENTS、permission mode 在 `live_unlock_contract.md` §5-6 复制代码清单，且已过时。

**缺失的 Contract（有主线代码、没有当前 Contract）**
- **Entry rationale（当前）**：Entry Plan 的 `thesis` + `entry_hypothesis_json/hash` 作为入场理由载体，没有任何 Contract 描述（trade_decision_rationale 还在描述退役的五项理由）。
- **入场主线上的 Lab treatment 解析**：`trading/entry_analysis.py:1044` → `extensions/learning/entry_context.py` → `canonical.resolve_observation_treatment`（每次 planning 写一条 receipt）——Lab Contract 只在 "Execution manifests" 一段顺带提到 adapter，未写成入场主线的 entry point / 失败语义。
- **更新路径被 Lab checkpoint 绑定**：`ops/update/auto.py:784`、`ops/update/continuity.py:1070` 在部署时持 Lab 周期锁并要求 strict checkpoint——这是部署行为，部署 runbook 有，但 update Contract 中未声明"Lab 证据不一致会阻塞更新"这一耦合。

**与 spec 0001 的冲突/待改点**
- `observation_experiment_ownership_v2.md:22-23` "Learning owns research, lessons, knowledge…" —— 0001 改为单一 Playbook。
- `persistence_recovery_contract.md:130-131` bundle import 允许 `ps_lessons`/`ps_skills`；`/api/persistence/lessons*` 路由（`server/routes/persistence.py:324-401`）是 PS lesson 生命周期 —— 0001 落地时需改。
- `llm_inference_harness_contract.md` 未列入 0001 的 Contract Delta，但 LL-R01 会改 `agent_shell.entry_planner` schema/版本、Playbook arm 需作为 ContextArtifact 记录。
- 0001 `design.md:24` 与 LL-R16 复用 Lab manifest/receipt/assignment → 合并后的 Lab Contract 必须保留 assignment/receipt 部分，删除 filter-shadow/ranker/UFC/worker 才不伤 0001。
- `profit_loop/profit_gradient` 使用 24h net return / 60 分钟反事实单位，0001 把 learning 单位统一为 judgment label R（spec.md:277）——两套单位并列，需在 Contract 中声明 profit-gradient 不是 learning 输入。
- `trade_decision_rationale` 应声明 LL-R01 字段（`p_target_first`、`lessons_considered`）落在 `entry_hypothesis_json`。

**出范围但仍在跑的代码面（文档如实，但应开 D2 退役 issue）**
`equity_observer`（`scheduler.py:1585-1591`）、`prediction_observer`（`:1595-1601`）、Settings 的 Spot/"Enforce in paper ranker"/Prediction observer 开关、`observed_equities`/`observed_stock_tokens` worksets、Lab Worker 模式（`deploy/systemd/aletheia-observer.*`）、filter-shadow `ranker_confidence_gate`/`observer_pre_filter` 适配器、`/api/persistence/gateway/*`。

## 总表

| doc | lines | role | verdict | top problem |
|---|---|---|---|---|
| observation_lab_contract.md | 171 | Contract + 迁移叙事 | REWRITE（吸收另 3 份 Lab 文档） | 把 ranker/observer 适配器和 legacy authority 写成当前；漏掉入场主线上的 receipt writer |
| observation_lab_v1_design.md | 204 | 伪 Contract 复述（放错目录） | MERGE 进 Lab Contract 后 DELETE | 说 UFC equivalence window 在线，与代码和 Contract 矛盾；约 70% 重复 |
| observation_lab_backup_contract.md | 137 | Contract + issue 摘录 | MERGE 进 Lab Contract（或 REWRITE） | #1072/#3096 叙事、"future Packet 2"；10 字段跨库指纹会阻塞部署，过度工程化 |
| observation_experiment_ownership_v2.md | 106 | 迁移 Contract（已完成）+ 历史 | SPLIT（传输语义并入 Lab；迁移 → history） | 迁移已完成，issue 号被当作权威 |
| trade_decision_rationale_contract.md | 59 | Contract | SPLIT + REWRITE | 开仓仍是退役 PiDecision 五项理由；`entry_parameters` 没有任何 Python writer |
| live_unlock_contract.md | 192 | Contract + 日期/状态 | REWRITE | `paper_execution_mode` 说法仍属实（`entry_plan_compiler.py:133`），但 paper/PI/OKX/Binance 措辞、行号和清单都过时 |
| template.md | 33 | 模板 | REWRITE（对标 specs） | 没有编号要求、EARS、失败语义表，claim 无法一一对应 |
| README.md | 135 | 索引 + 流程指南 | REWRITE | 说 legacy Lab authority 仍生效（错）；把 future 指向 proposals/ 而不是 specs/；42 份里漏列 24 份 |
| macro_source_time.md | 21 | Contract | MERGE 进 decision_context_freshness.md（或 KEEP） | 提到已退役的 "Observer"；把 issue 号和 schema 版本当正文 |
| configuration_contract.md | 74 | Contract | KEEP（小改） | :9-11 断句；equity/stock-token worksets 已出范围仍写成运行面 |
| dashboard_data_presentation_contract.md | 72 | Contract | KEEP（小改） | 只覆盖 Learning Knowledge（将被 Playbook 替代），交易页没覆盖 |
| status_surface_contract.md | 135 | Contract | KEEP（小改） | 事故叙事 #3702/#3763 重复 4 次；缺编号 |
| settings_control_contract.md | 130 | Contract + 63 行清单 + Webhook | REWRITE | 清单是复制的代码事实，并收录 Spot / paper ranker / prediction 等出范围控件 |
| audit_chain_contract.md | 365 | Contract + 代码复制 + 缺陷 | REWRITE | doctor 级别描述过时（info/warn 分档）；paper funding 事件写成当前 producer |
| persistence_recovery_contract.md | 190 | 4 个主题混合 | SPLIT（backup/restore 独立） | schema/PS 退役叙事；化石端点 `/gateway/*`、`/lessons*` 未说明 |
| llm_inference_harness_contract.md | 338 | Contract + provider 管理 | REWRITE（编号化，拆出 provider 段） | 仍引用已退役的 Market Observer；未列入 spec 0001 的 Contract Delta |
| profit_loop_observability_contract.md | 449 | 6 个子能力 + 运行时读数 | SPLIT（replay/story 留；gradient 并走） | 写"无持久表"但含 path observer writer；Fedora Paper 读数和进度 |
| profit_gradient_evidence_contract.md | 113 | Contract | MERGE（接收 profit_loop 的 gradient 部分，标 frozen） | 确认层映射为空；量化归因工具不在主线，单位与 spec 0001 的 R 冲突 |
