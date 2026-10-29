# 执行层合同审查报告

范围：`docs/design/contracts/` 下 6 份合同。审查标准见 `/tmp/docaudit/CRITERIA.md`，基线是 SDD spec 格式（`docs/design/specs/README.md`、spec 0001）、`CAPABILITY_GROUND_TRUTH.md`、`ACTIVE_RUNTIME_SCOPE.md`。所有"代码里不存在"的结论都来自实际 grep/ls。

## 一、总表

| 文档 | 行数 | 角色 | 判决 | 最大问题 |
|---|---|---|---|---|
| execution_contract.md | 876 | 混合：Contract + 大量 Evidence/Decision 叙事 | SPLIT + REWRITE | 单文件塞了 6 个子系统（下单、TPSL、对账、平仓、Outcome 完整性、Funding），PR/Issue 编号当正文；§3 仍把 okx/binance/spot 写成现役能力 |
| entry-plan-contract.md | 774 | 混合：Contract + 一份未标注的 DRAFT 提案 + 已退役 Momentum 测量 | SPLIT | "Entry hypothesis authority"表（PASSIVE/BOUNDED_URGENT、resting-maker）与后文 Capture 章节自相矛盾，且 `execution_urgency` 在代码中 0 命中 |
| entry_sizing_and_financing_contract.md | 225 | 单一角色 Contract，最接近 spec 形态 | KEEP（小改） | 头部 Evidence baseline 写死 commit，§Margin 末段含运维命令；spot 分支残留 |
| risk_engine_contract.md | 263 | Contract，含历史叙事与已删对象的墓志铭 | REWRITE | 22 关表里夹着"偏差/历史"提示块；#15 把 LessonStore 写成 Risk 现役消费者，与 spec 0001 冲突；行号锚点已全部漂移 |
| decision_lifecycle_contract.md | 396 | 横切 Contract + 130 行"退役入场历史"附录 | SPLIT（附录归档 history/） | 附录（L266-396）全是已退役的 TradingDecision/PiDecision/paper_trader/admission slot，不是合同 |
| kill_conditions_contract.md | 94 | Contract，但两个条件无生产消费者 | REWRITE 并缩小（K1/K2 降级或删） | K1、K2 对应函数只被测试调用，无任何生产调用方；只有 K3 在生产路径上 |

## 二、代码声明抽查（共 6 项，实测）

| # | 文档声明 | 实测结果 |
|---|---|---|
| 1 | risk_engine §1：`evaluate_proposal` 在 `engine.py:790`，`_eval_lock` 在 `:85` | 函数存在但在 `engine.py:955`；`_eval_lock` 在 `:87`。行号锚点漂移，函数名正确 |
| 2 | entry_sizing：`trading/risk_intent.py`、`entry_sizing.py`、`leverage_resolution.py`、`execution/margin_mode_policy.py` | 四个文件全部存在 |
| 3 | execution §4：`place_protection` 链已删除；`replace_protective_stop` 原地修改 | `place_protection` 全仓 0 命中（确认已删）；`replace_protective_stop` 在 `execution/coordinator.py:946` |
| 4 | execution §10.2：`pnl.reduce_position` 已退役、不保留别名 | `portfolio/pnl.py` 无 `def reduce_position`（确认已删）。但 `position_manager.py:434` 仍有 `reduce_position_routed`，全仓无任何生产调用方（只有定义），与"严格自主 partial-reduce 仍不获授权"一致，但这是一个无人调用的死函数 |
| 5 | entry-plan：Hypothesis 表用 `grounded-anchor-resting-maker` + `PASSIVE/BOUNDED_URGENT` urgency | `entry_profiles.py` 注册表只有 `grounded-anchor-direct` 与 `grounded-anchor-retrace-reclaim` 两个；`execution_urgency`、`BOUNDED_URGENT` 在 core/extensions 中 0 命中；现行字段是 `capture_priority`（`PRICE_FIRST/MAKER_PREFERRED/FILL_FIRST`，`trading/entry_capture.py:4`）。`entry_plan.py:289` 的 Literal 仍保留 `grounded-anchor-resting-maker` 作历史可读 |
| 6 | kill_conditions：`check_kill_condition_consecutive_rounds()`、`count_consecutive_zero_fill_days()`、`compute_scorecard()` | 三者都存在于 `analytics/go_live_scorecard.py`（`:205`、`:172`、`:49`）。但前两者在 core/extensions 里没有任何生产调用方，只出现在 `tests/specs/spec_kill_conditions.py` 和 `tests/test_retired_execution_surfaces.py`；`compute_scorecard` 被 `server/routes/live.py:82,91` 调用 |

附加核实：`guard_chain.py` 确已删除（risk_engine 所述属实）；`serenity_executor*` 全仓不存在（属实）；`paper_trader*.py`、`trading_decision*.py`、`market_observer*.py` 全仓 0 个文件；`stop_floor_policy`/`stop_floor_atr_multiple_draft` 在 `config/schema.py:1624,1627` 与 `trading/entry_plan.py:123` 存在（DRAFT 开关是真的）；`IncompleteReadError` 在 `execution/errors.py:49`；`run_entry_planner_batch` 在 `agent_shell/entry_planner.py:718`；文档引用的 10 余个测试文件全部存在。

## 三、逐文档分析

### 1. execution_contract.md（876 行）

**角色**：声称是 L2 合同，实际混了六件事：下单序列（§1-5）、对账（§6）、审计（§7-8）、平仓/退出（§10）、Outcome 完整性与 Funding 会计（§11）、UTA 迁移边界（§12）。其中大量段落是"某天某 Issue 的修复叙事"，不是可检验要求。

**真实性问题**
- §3 表（L81-84）把 `okx_demo`/`binance_testnet`/`*_live_limited` 当现役模式列出。按 Ground Truth，OKX/Binance 冻结，只有 `bitget_demo` 是资格路线。表应改成"现役：bitget_demo；冻结：okx_demo、binance_testnet（仅历史识别与敞口恢复）；Live：默认关闭"。
- §3.1（L142-151）把 spot 作为 canonical market type 之一，还保留"OKX demo 现货已实测"这段 2026-06-28 的实验叙事并引用 `spec_spot_parity.py`。spot 已于 2026-10-09 出范围，整段应删除，只留"仅 `usdt_futures`"。
- §6 末段（L306-318）整段讲 spot/futures 身份区分、spot 的 `1x + not_applicable_spot`；§11.6 结尾 spot funding 句；§10.2 L460 `spot/perp`。全部应缩成"仅 USDT 永续"，spot 分支描述删除（代码里的 spot 防御分支是否保留是另一个决定，合同不应继续为它背书）。
- L12-15 入口正确（`execute_existing_proposal`、`submit_proposal` 仅 `dev test-order`），与 ACTIVE_RUNTIME_SCOPE 一致。
- §3.4 的 "worktree preview runtime" 是开发运维规则，不是执行合同，应移到 Operations 文档。
- §9 "策略规则引擎（已删除）"只有墓志铭，应删，进 DECISIONS/history。
- L58 等多处行号锚点（`models.py:102/125/136/155/165`）已全部失效：`TradeProposal` 现在在 `models.py:321`，`RiskDecision :399`，`Order :454`，`Fill :510`，`Position :538`。`gateway.py:183` 现在是函数参数列表。`_is_live` 在 `engine.py:564` 而不是 `:35`。

**具体性**
- 好的部分很具体：§5 的"先写 Order(pending) 再调交易所"序列、§10.2.4 的 24h 终止界限（有阈值、有禁止项、有人工解锁路径）、§6.1 的入场侧有界终止（三条同时满足的条件）、IncompleteReadError 的类型化要求。这些是真正的 trigger→response 形式，应原样保留。
- 差的部分：§4 TPSL 表的"收紧/追踪"一行约 1500 字塞进一个表格单元；§10.2 有 20 多条 bullet，每条都带 `（#xxxx，owner 日期）`，读起来是变更日志。

**合理性**
- 主干上的：下单序列、租约、对账、`close_position_routed`、close claim 状态表、verified-vs-administrative close。这些是"交易 → 持仓 → 验证平仓 → 成本完整 Outcome"主线必需的。
- 值得质疑的：§10.2 里 "Bitget demo 分片平仓"与"可成交限价回退"两条是针对 demo 撮合引擎缺陷的 workaround（2026-08-22 实测），其价格规则（最新价 ±1%）是行为参数。它们属于已实现行为，但应标注为"demo 专属 workaround"，且不应与通用平仓合同并列；若 demo 缺陷消失应删除。
- §11.5 `reconciliation_integrity_incidents` 事故断路器，叙事式；需核实其是否仍在主线（未抽查，建议下一轮用 grep 验证）。
- L401 与 L405-406 的 market-aware review："只唤醒 Operator，不直接平仓"，并承认 Settings 文案有"已知产品偏差（EX-09/#417）"。这是一个把已知缺陷写进合同的位置，应移到 Issue，合同只写"evaluator 仅产生 wake，不授予执行权限"。

**冗余**
- 至少 25 处 `#NNNN` / `owner 2026-xx-xx` / `PR #xxxx` 作为正文。典型：L106 "Cancel 收敛(2026-09-27, #3507)"、L118 "账户身份关系…(2026-10-08, #3798)"、L217 "(#3848 owner D1, 2026-10-09,取代 PR #3849 的…)"、L521 "(#2084，owner 2026-10-09)"、L628 "Limited limitation (narrowed 2026-09-27, #3564)"。
- L5、L6 "Last updated: 2026-08-27" 已落后于文中 2026-10-09 的内容，自相矛盾。
- INV-EX-1（L618-626）与 §6 L279-287 与 §10.2.4 L577-589 三处重复描述 `IncompleteReadError`，应只留一处。
- L8 开头说"代码为底，冲突时文档为准"，两句互相冲突。

**判决：SPLIT + REWRITE**

**具体修改清单**
1. 头部：删 `Last updated`，删"基线：代码为底"，改为"本合同描述当前行为；偏差进 Issue"。
2. §3 表：重写为"现役 `bitget_demo`；冻结 `okx_demo`/`binance_testnet`（不接受新入场，保留历史识别）；Live 默认关闭"。删 §3.1 的 OKX 行和 spot 现货实测引用块，删对 `spec_spot_parity` 的引用（先确认该测试是否仍有意义，若仅覆盖 spot 则该测试也该删）。
3. 删所有 `spot` 相关句：§6 L303-312 的 spot/futures 区分、§10.2 L460 的 `spot/perp`、§11.6 L854-855 的 spot funding。统一成"market_type 恒为 `usdt_futures`；出现其他值视为 integrity issue"。
4. 拆分：
   - 保留 `execution_contract.md` = §1、§2、§3（精简）、§5、§6、§7-8。
   - 新建 `exit_and_close_contract.md` = §10 + §11.1-11.3 + close claim 状态表（这是一个独立子系统，且 §10.2 已长到需要自己的目录结构）。
   - §11.4-11.6（outcome_integrity 三种读取口径、事故断路器、Funding 会计）并入 `opportunity_outcome_contract.md` 或新建 `outcome_economics_contract.md`，因为它们回答的是"什么算有成本完整的 Outcome"，不是"怎么下单"。
   - §3.4 worktree preview、§12 UTA 迁移边界（只是指向 proposal 的段落）移出。
5. 每条 bullet 改写成 EARS 一句话 + 失败语义；把 `（#NNNN, owner 日期）` 全部挪到一张末尾的 "Decision references" 表，正文不留编号。
6. 重复项：`IncompleteReadError` 只在 §6 保留一次，删除 INV-EX-1 与 §10.2.4 中的重述，只留"见 §6"。
7. 删 §9（规则引擎墓志铭）。删 L405-406 的"已知产品偏差"提示块，改成 Issue。
8. 所有 `file:line` 锚点替换为 `file::symbol`（该文件自己已经在部分地方这样做，统一即可）。

---

### 2. entry-plan-contract.md（774 行）

**角色**：声称 Contract，但含：（a）真正的当前合同；（b）一份"DRAFT: ATR-relative stop-floor policy"（L554-588，本质是 Proposal/Decision）；（c）已退役 Momentum 几何测量（L481-514）；（d）带 PR 号的叙事段落（"Bounded execution resolver (#3111)"、"Capture compilation (#3111 Stage 2)"、"Plan authority (#3111 Stage 3)"、"(#3096)"、"(#3729)"）。

**真实性问题（最严重）**
- L359-363 "Entry hypothesis authority and failure semantics"表：三行 profile × `PASSIVE`/`BOUNDED_URGENT` urgency → mode/tactic。实测：`execution_urgency` 与 `BOUNDED_URGENT` 在 core/extensions 全部 0 命中；`grounded-anchor-resting-maker` 不在 `entry_profiles.py` 注册表中（只有 `grounded-anchor-direct` 与 `grounded-anchor-retrace-reclaim`）。而同文件 L680-701 自己写着"`execution_urgency` is retired"，并说 profile literal "absent from the current model-facing registry"。同一文件前后矛盾，读者无法判断哪张表是现行。L357、L197-199 同样错误：把 `anchor-resting-maker` 叫"deterministic compiler Profile"。
- L704-710 说 Plan 不再写 `risk_reserved`/`ordered`/`partially_filled` 状态，但 L98 起大量前文（L624-625 "risk_reserved returns to waiting"、L458 "an `ordered` or `partially_filled` plan"、L341-344）仍按这些状态写。代码里这三个状态仍在 status 集合（`repositories_execution.py:879`、`models.py:1442`）作历史可读。合同需要统一：现行只有 `waiting/active/…终态`，其余是历史。
- L22-35 与 L52 把 Serenity 作为第二个现役来源。ACTIVE_RUNTIME_SCOPE 没有把 Serenity 放进 active profile，Ground Truth 称 "Serenity research and preferences: Experimental"。合同把 Serenity 与 Universe 并列为"唯一入场主线的两个发布者"，与产品真相有落差，需要标注"Serenity 为实验来源"并去掉其"holding 2160h"这类细节，或至少移到 serenity_contract。
- L546-548 "Planner blocks `spot_short_not_allowed`"：spot 已出范围。代码里 `trading/entry_plan.py` 还有此 token，合同应改成"仅 usdt_futures；其他 market_type 在 Planner 之前阻断"，不再为 spot short 专写一节。
- L523-530 "Realized Outcome handoff (#3031)" 在 "Non-goals" 小节之内，章节归属错位。

**具体性**
- 好：Entry anchor registry（L138-179）规则明确到阈值（`0.5 × ATR14`、26 个上限、选择顺序、`NO_INVALIDATION_STRUCTURE` 的 60 秒重试且不耗模型次数），这是该文件里最像 spec 的部分。Model-visible quantitative fields（L100-136）也是可检验的公式。
- 差：L204-261 "Mechanism-aware Grounded Entry"和 L263-323 "Contract identity and planning cycles"是长段落连续散文，每段塞 5-8 个独立要求，无法逐条对应测试。

**合理性**
- Planner → Hypothesis → Compiler → immutable Plan → Risk → Gateway 是主线，必须保留。
- 值得删：L481-514 Momentum geometry measurement（`momentum-geometry-measurement/v2` 在 `analytics/momentum_geometry_replay.py` 仍有代码，但章节自己最后一句承认是"Archived Momentum geometry experiment"并指向 history）。这是已归档实验的测量协议，不属于现行合同。
- DRAFT stop-floor（L554-588）：默认关闭、有回滚方案、有 Decision issue #3743。这是 Spec/Decision 的内容，放在 Contract 里违反"Contract = current behavior only"。代码里开关真实存在，但默认行为是 `vol_target_pct`，所以合同只需一句"`trading.stop_floor_policy` 默认 `vol_target_pct`；`atr_multiple_draft` 未启用，见 spec NNNN"。
- 与 spec 0001：见第四节。

**冗余**：大量 "owner-accepted #3111 D1 exception"、"(#3096)"、"(#3850 item 5)" 作正文；"Historical rows and hashes are not migrated/rewritten" 这句话以各种形式重复出现至少 8 次（L16、L163、L234、L241、L295、L698、L713-714、L742-743），应统一成文件头一条通则。

**判决：SPLIT**

**具体修改清单**
1. 删除或改写 L357-368 的 Hypothesis→execution 表，改成现行的 `capture_priority × profile → capture_policy_id/mode/tactic` 映射（取自 `entry_capture.py` 与 `entry_profiles.py`），并删除所有 `PASSIVE`/`BOUNDED_URGENT`/`resting-maker` 作为现行词汇的句子（L197-199、L359-363）。
2. 把 Plan 状态机只写一次：现行 `waiting/active/terminal`；其余状态列一行"历史行可读"。同步改 L341-344、L458、L624-625 等引用。
3. L554-588 DRAFT stop-floor 整段移到 `docs/design/specs/NNNN-atr-stop-floor/`（或 DECISIONS 条目），合同留一行默认值声明。
4. L481-514 Momentum 测量与 L510-514 归档指针整段删除，保留 history 里的现有归档即可。
5. 把 L523-530、L737-758（sizing 指针）移出 "Non-goals"；Non-goals 只留 L516-521 一小段。
6. 文件头增加一条通则"历史行/hash 不重写，历史 label 只读"，并删掉各处重复句。
7. 所有 `(#NNNN)` 小节标题改成功能名（"Pre-broker submission authority"、"Bounded execution resolver"…），编号放进末尾引用表。
8. L204-261、L263-323 按"一条要求一句 EARS"重排。
9. 加 Serenity 的实验性标注，或把 Serenity 细节（L22-35、L64-77 的 holding 计算）迁入 `serenity_contract.md`，entry-plan 只保留"来源无关"的字段（`source_kind/source_id/trade_school/holding_horizon_hours`）。
10. spot：L546-548 改写为仅 futures。

---

### 3. entry_sizing_and_financing_contract.md（225 行）

**角色**：单一 Contract，结构清晰（Scope → Contract A/B/C → Margin mode → Schema → Risk convergence → Failure reasons → Non-goals → Verification），公式用代码块给出，失败 token 完整枚举，不变式用粗体声明（"with the same facts snapshot, a compiled Plan must not be rejected by Risk for leverage, margin, or stop-risk feasibility"）。这是六份里最接近 SDD 要求的一份。代码文件全部存在（见抽查 #2）。

**问题**
- 头部 L4-8 的 `Evidence baseline: master@1d653f632 (2026-09-29)` 和 Issue 链接是进度/证据状态，违反"no progress state"。
- L35 "Contract A" 中 `risk_intent`：当前 `EntryHypothesis.risk_intent` 的模型不可靠假设（"live System Operator model is not reliable at emitting a nested object"）是一条产品运行观察，不应写进合同，改成规则："缺失或部分 intent 规范化为 BASELINE，并写日志"。
- L144-161 "Margin mode" 小节末尾混入运维命令 `uv run aletheia data venue-margins …` 与调度 job 名 `venue_margin_facts`；命令属 Operations，job 名属 scheduler_contract。
- L143-144 Spot 分支："Spot is structurally 1x with no liquidation solver" —— spot 出范围，删。L213-217 非目标里的"legacy conviction/Kelly/persona sizing"是墓志铭，只留一句。
- L139-142 Cross margin 的"MUST NOT 复用 isolated 估算"是正确的安全规则，但 V1 里只有 isolated 被资格认证；建议把 Cross 段落标为"未资格化：一律 fail closed"，避免读者以为有 cross 路径。
- 与 risk_engine_contract 有部分重复：risk_engine §5（L187-211）把同一组公式又写了一遍，应在 risk_engine 里改为只指针。

**合理性**：整个文件属主线（sizing → leverage → Plan → Risk 复核），而且"AI 只表达定性风险姿态、不碰数值"符合 Owner 目标，应保留。

**判决：KEEP（小改）**

**具体修改清单**
1. 删 L4-8 的 Evidence baseline 与 Issue 链接，改成"Related contracts"。
2. 删 spot 相关句（L143 的 Spot 一行、Non-goals 中涉及 spot 的部分）。
3. L152-159 里的 CLI 命令与 job 名移走，只保留"read path 同步无网络；缺失即 typed unavailable"。
4. L39-47 的"live System Operator model is not reliable…"改成纯规则。
5. 在 Cross 段落加一行"V1 未资格化：任何 cross margin 路径一律以 `CROSS_MARGIN_SAFETY_UNAVAILABLE` 拒绝"。
6. 为未来对接 spec：每个 Contract 分点可以编号（A1…C7），便于 `SpecClaim` 引用。

---

### 4. risk_engine_contract.md（263 行）

**角色**：Contract，但表格与注释夹带大量历史/偏差说明，且包含与现行产品真相不一致的条目。

**真实性问题**
- 表格 #2（L70）仍把 `spot_market_disabled` 作为关卡；代码确实在 `engine.py:1141-1144`。spot 出范围后，这一关应改成"market_type 必须是 usdt_futures，否则拒"，而不是"spot/futures 开关"。同样 L117-122 两大段 spot 杠杆身份与 spot NULL leverage 例外（RE-B23~28）都是为 spot 服务的分支。
- #9a/#9b（L79-80）保留 OKX/Binance 的 `SELF_CONTAINED` 豁免；#14b（L86）的 demo entry quality 对 `bitget_demo/okx_demo/binance_testnet` 三者同时适用。冻结路线不应再作为合同关卡的一等对象。
- #15（L87）"Lesson Constraints"：把 `LessonStore.recommendation_json` + "Learning path authority" 写成 Risk 的现役消费者，包括"已接受的 manual/owner/human block 规则保持静态硬约束；自动来源默认 advisory"。代码确实有 `_check_lesson_constraints`（`engine.py:1957`），`LessonStore` 在 `extensions/learning/lessons.py:18`。但 spec 0001 的 REMOVED 清单明确移除 `ps_lessons` 生命周期，并规定 Playbook 只在 advisory 层，"The Playbook shall not block a candidate… or bypass any Risk check"（LL-R13），阻断权限只能经 LL-R19 + owner 准入。因此 #15 与 spec 0001 冲突：spec 一旦实施，#15 需要重写成"Authority tier 的 AVOID 规则由 LL-R19 授权后，作为 Risk 的一个检查"，而不是 LessonStore。现在应在合同里标注"待 spec 0001 实施后替换"，或者直接按 Delta 预先在 spec 里列出（spec 0001 的 Contract Delta 当前只列了 learning/entry-plan/knowledge 三份合同，漏了 risk_engine_contract #15，这是 spec 自身的缺口）。
- #14 "Scanner-Family Evidence"（L85）引用 `live_family_min_samples`；#16（L88）仍写"部门集中度/family 解析器"。这些 family 概念属于旧 scanner 世界；现行 Entry Plan 保留 `candidate_family`，所以还能工作，但应核实是否仍有生产价值。Live 默认关闭，#14 实际是 Live-only 且 fail-closed，实际不会在现路线生效。
- L56-63 "⚠️偏差 … ARCH_GUIDE 的'五关'待该文档批次3标注"：这是对另一份文档的待办，不应出现在合同里。
- 行号锚点普遍失效（`engine.py:790` → 实际 `:955`，`:85` → `:87`，`:54-62`，`:1638-1700`，`:159`，`:356` 等），表格"位置"列大量用 `:83`、`:93`、`:99` 这样裸行号，已无信息量。

**具体性**：22 关表给出了判断/config 字段/失败 token/位置，是 trigger→response 的良好形态，也是六份文档里最适合改写成 EARS 的素材。问题在每格内容过长（#5、#13、#17 单元格上百字）。

**合理性**：Risk Engine 是"唯一硬门"，属于最高优先级主线，必须保留；但"22 关"里有明显的叠床架屋：#5/#12/#13/#17/#18/#19/#20/#21 全是不同侧面的容量/杠杆/风险上限，部分在 sizing 阶段已经保证（entry_sizing 合同明确说"同一 facts 下 Plan 不应被 Risk 拒绝"）。#19 和 #20 是同一约束的新旧两版（#19 对非融资 Plan，#20 对融资 Plan）。既然历史 Plan 只能"执行至完成"，新入场只走融资路径，#19 对新入场实际已是死分支，应标注为"仅历史 Plan"并计划删除。

**冗余**：L58 "历史：本表曾止于 16 关…2026-06-30 补齐契约"、L138 "~~trust/guard_chain.py~~ 已于 2026-09-07 删除"、L228-230 "§7 已知缺陷: 截至 2026-06-23, 无已知缺陷待修"、L234-237 "目标态差距"、L241-263 Serenity isolation（含 `serenity_executor.py 不存在` 的负向 tripwire 说明）都是墓志铭或空节。L240-263 的 "evaluate_proposal 唯一入口不变式" 与 §1 L20 完全重复。

**判决：REWRITE**

**具体修改清单**
1. 删除 L56-63、L138（guard_chain 墓志铭）、L228-237（§7、§8）、L241-263（整节 Serenity isolation 与重复的入口不变式）。`D2-S02` tripwire 如需保留放进 execution_contract 或测试说明。
2. #2 改写为"market_type 必须是 `usdt_futures`"；删除 L117-122 的 spot leverage 例外与相关 token（`invalid_spot_leverage`），在代码里对应分支是否同步删除另议，但合同不再描述。
3. #9a/#9b/#14b 的模式枚举改成"当前已注册外部模式"（由 `ACTIVE_RUNTIME_SCOPE.execution_modes` 与冻结列表决定），不再把 okx/binance 作为一等路径；SELF_CONTAINED 豁免若对应冻结路线则注明"冻结"。
4. #15 重写：现状改成"当前由 LessonStore 的人工/owner block 规则触发；自动来源 advisory"，并加一句"该关将由 spec 0001 的 Authority tier（LL-R19）取代"；同时回头在 spec 0001 的 Contract Delta 里补 `risk_engine_contract.md #15`。
5. #19 标注"仅历史 Plan（未融资）"，计划随 legacy 路径退役一起删；#20 为现行关卡。
6. §5 sizing 公式块（L190-196）删，改成指针"见 entry_sizing_and_financing_contract"；保留 L208-211 的"sizing 结果不是最终下单量，仍须过各关"。
7. 位置列全部改成 `file::symbol`（如 `engine.py::_effective_position_margin_cap`），删裸行号；删 `Last updated`。
8. 头部 L5 的 `BACKEND_ISSUES.md` 引用（指向 archive）删除。
9. 每个关卡的"判断"列拆成一句 EARS 语句，并为每关标出 spec claim id（RE-B…、PL-…），让它可以作为 `SpecClaim` 来源。

---

### 5. decision_lifecycle_contract.md（396 行）

**角色**：声称横切合同，实际由两部分组成：（a）L1-264，现行主线 Candidate→PlanningGeneration→Plan→Risk 脊柱及 DL-INV 不变式；（b）L266-396 "附录：退役入场历史（非执行契约）"，130 行。

**真实性问题**
- 附录（b）全部是已退役机制：`TradingDecision`、`PiDecision`、`signal_pending`、`paper_trader`（磁盘上无此文件）、`_PENDING_SIGNAL_QUEUE_CAPACITY=5`、admission slot、`DecisionUnavailable`、B1.5 blind 反事实确认、Operator defer 120 分钟窗口。文本自己承认"不再是当前不变量…不能执行"，但仍放在 `contracts/`。按 documentation_governance，这类内容应在 `docs/design/history/`。
- §2.1（L90-104）"历史 Opportunity 载体"表同理：字段行里 `trading_decision.py:909`、`paper_trader_ranker` 都是已删对象；开头第一句"以下字段仅解释历史记录"。
- DL-INV-36（L253）仍描述 `stock_linked_perp` analysis context；ACTIVE_RUNTIME_SCOPE 说"formerly registered stock-linked profile are frozen"。属冻结能力，不应出现在现行不变量里。
- DL-INV-35 market-structure/v2 Shadow assessment、"hard-gate consumer seam … 生产 allowlist 为空"：这是一个"为空的扩展点"，没有任何生产消费者，应该问是否值得保留。
- 编号 DL-INV 不连续（1、4、11、19、20、27、7、8、30-34、38-42、35-37、29），顺序也乱，说明大量编号被删/迁移，读者无法知道缺失的是否被替代。
- §1 的 mermaid 与 entry-plan-contract 同一主线，但 L35-37 的 "Stale → Audit only"、"Planning Generation owned?" 等节点与 entry-plan-contract 的 Capture/Activation 术语不完全对齐（mermaid 仍用 `Approved Order Tactic → Maker/Urgent`，这与 entry-plan 中 PRICE_FIRST/MAKER_PREFERRED/FILL_FIRST 不同）。

**具体性**：DL-INV 条目多数是可检验的（"单一开仓决策权"、"归因不可改写"、"Fast Monitoring 精确场地与新鲜度"），但 DL-INV-37 的"每轮可用 3–15 个并发 worker"这种实现参数写进不变式；L133-167 的 "Candidate business progress (accepted 2026-09-05)" 是一段 35 行的健康监控规格（TTL 1200 秒、480 秒、180 秒、900 秒阈值），属于 `profit_loop_observability_contract` 或 `status_surface_contract` 的内容。

**合理性**：作为"横切脊柱"合同有价值，但它和 entry-plan-contract、execution_contract §10、risk_engine_contract 在多个点上重复同一事实（Plan 不可变、source 出生冻结、holding_horizon 冻结等在两份文件里各写了一遍，L52-57 vs entry-plan L26-40、L198-204 vs entry-plan L62-80）。建议把本文件瘦身为"接缝守卫表（§3）+ DL-INV（§4）+ 一张脊柱图"，其余全部指针到 owner 合同。

**判决：SPLIT**（附录与 §2.1 → `docs/design/history/`；§2.1a 的健康监控段 → observability 合同；其余瘦身）

**具体修改清单**
1. 删除 L266-396 整个附录，原样迁到 `docs/design/history/decision_lifecycle_retired_entry_2026-09.md`（保留原文即可）。
2. 删 §2.1（L90-104）与 L72-82 中对 PiDecision/ranker 的重复叙述，改成一行"旧 Opportunity/PiDecision 入场行只读，见 history"。
3. 把 L133-167 Candidate business progress 迁到 `profit_loop_observability_contract.md`；本文件只留一句链接。
4. L169-204（Plan 字段冻结、source/holding 规则）与 entry-plan-contract 重复，删除，改指针。
5. DL-INV 重新连续编号，每条改成 EARS 单句；删 DL-INV-36（冻结 stock_linked_perp）、DL-INV-35（空 allowlist 的 hard-gate seam，若确认无消费者）、DL-INV-37 里的 worker 数量。
6. 更新 mermaid 以匹配 entry-plan-contract 的 capture_priority 词汇。
7. 头部 L8 的日期/研究证据链接删，`Last updated` 删。
8. §5 "验证边界"的测试清单保留，但去掉"文档同步不表示这些测试已在此执行…"这类免责声明（治理文档已有统一规则）。

---

### 6. kill_conditions_contract.md（94 行）

**角色**：看似单一 Contract，但：
- K1（PF<0.5 连续两轮终止实验）与 K2（连续 3 天零开仓）是**实验协议**的阈值，不是系统行为；
- K3（live unlock 须 scorecard ≥ 70）是真正的 Live 门禁行为，应属 `live_unlock_contract`。

**真实性问题（实测）**
- `check_kill_condition_consecutive_rounds()` 与 `count_consecutive_zero_fill_days()` 虽然存在于 `analytics/go_live_scorecard.py`（`:205`、`:172`），但在 `core/`、`extensions/`、`ops/` 全部 `.py` 中**没有任何调用方**，只有 `tests/specs/spec_kill_conditions.py` 和 `tests/test_retired_execution_surfaces.py` 引用。合同声称"防止系统在已证明无效或危险状态下持续运行"，但实际没有任何机制在运行时执行这两条 Kill。文档自己的 K2 小节也承认对应 doctor 探针 `_probe_profit_loop()` 已于 #2945 删除。也就是说 K1/K2 是"测试里有函数，运行时无人消费"的纸面条件。
- 角色描述"demo 阶段三条系统级 Kill 判决"：Demo 阶段（Bitget Demo 资格路线）现行，措辞可用，但"系统级 Kill"过强，因为没有触发任何行为。
- L18-20 "历史背景：Round 1 协议曾使用 0.85"属历史叙事。
- L66-72 "个人止损线 Deferred" 是对一个不存在功能的 tripwire 说明，D8-B03 是 "missing_tripwire"：合同在描述一个未来可能出现的东西的占位。违反"Contract = current behavior only"。
- K3 描述与代码一致：`server/routes/live.py:82-91` 调用 `compute_scorecard`。

**合理性**
- K3 属 Live 解锁安全边界，保留（但应并入 live_unlock_contract，避免两份文档各说一半）。
- K1/K2：Owner 目标是 AI 像人类交易员一样交易、尽快上线 Bitget Demo；"PF 连续两轮 <0.5 则终止实验"可以作为 Operations 里的实验停止规则，不需要成为"Contract"。若没有运行时执行者，就是一个 Evidence/Protocol 文档的内容。

**判决：REWRITE 并缩小**——K3 并入 `live_unlock_contract.md`；K1/K2 移入 `EXPERIMENT_PROTOCOL.md`（该文件已被引用为来源），并在其中明确"目前无自动执行者，由人在观察窗口手动评估"；本文件删除。若 Owner 想保留自动 Kill，则应另立 spec，要求一个运行时消费者（EARS："While the 14-day trailing PF is below 0.5 for two consecutive windows, the system shall pause new entries and notify the owner."），而不是继续让合同声称它已存在。

**具体修改清单**
1. 删 K1、K2 两节与"延伸：个人止损线"；其内容进入 EXPERIMENT_PROTOCOL，注明"无运行时消费者"。
2. K3 迁入 `live_unlock_contract.md`，保留不变式 D8-C01 和阈值 70，并补充失败语义（scorecard 不可用时如何处理，现文未写）。
3. 同步更新 `tests/specs/spec_kill_conditions.py`：K1/K2 的 claim 若不再有合同依据，应降级为普通单测或删除，避免"spec 守着一个无人调用的函数"。
4. 删 "Last aligned with code"、L18-20 历史背景、"契约执行命令"一节（pytest 命令属开发工作流）。

---

## 四、跨文档问题

### 与 spec 0001 的冲突
1. **entry-plan-contract**：spec 0001 的 Contract Delta ADDED 要求 `EntryHypothesis.p_target_first`、`lessons_considered` 以及 Playbook 上下文段。当前 entry-plan-contract 对 `p_target_first|lessons_considered|playbook` 为 0 命中，合同尚未更新（符合"spec 是目标，合同只写现行"）。但合同中有两处会与 spec 实施冲突：L244-261 的 `entry-learning-context/v1` 与 `mechanism_entry_learning` 测量，与 spec 的 LL-R16 "Playbook adapter 与 entry_learning adapter 互斥"相关，spec 要求把 entry-learning 作为互斥处理；L680-686 "Learning alone cannot supply [capture] authority" 需与 LL-R13 对齐。实施 spec 时这两处必须同步改。
2. **risk_engine_contract #15**：如上，Lesson Constraints 与 spec 0001 REMOVED 清单（`ps_lessons`、auto-generated guards、`lesson_reviewer` 驱动的降级）冲突；spec 的 Contract Delta 没有列出 `risk_engine_contract.md`，这是 spec 的缺漏，应补充 MODIFIED：`risk_engine_contract.md #15` 由 LessonStore 改为 Authority tier（LL-R19）。
3. **ACTIVE_RUNTIME_SCOPE.md** 里的 `learning_authority_gate: extensions.learning.experiments.apply_numeric_treatment` 与 `learning_influence_default: enabled_owner_gated` 同样是 spec 0001 要替换的机制，不在本批范围，但需提示负责 Learning 的审查员。
4. **decision_lifecycle_contract** 里没有对 learning 的引用，无冲突。

### 文档间矛盾与重复
- sizing 公式在 `entry_sizing_and_financing_contract`、`risk_engine_contract §5`、`entry-plan-contract` 的 "Entry sizing and financing authority" 三处出现；应只保留 sizing 合同为 owner。
- Plan 不可变/source 出生冻结/holding 冻结在 `entry-plan-contract` 与 `decision_lifecycle_contract` 重复出现。
- `IncompleteReadError` 在 `execution_contract` 内重复三次。
- 平仓 `close_position_routed` 路径同时被 `execution_contract §10` 与 `entry-plan-contract`（Position review 部分）、`decision_lifecycle §1` 引用，但只有 execution_contract 是 owner。
- Spot：五份文档中有四份（execution、entry-plan、entry_sizing、risk_engine）仍有 spot 分支描述，与 Ground Truth "Spot out of scope" 冲突。`config/schema.py:285` 的默认 `enabled_markets={"spot": True, "usdt_futures": True}` 说明代码层也还没收敛，这属于 D0 conformance 问题：合同不应继续为它背书，同时要另开 Issue 让代码默认值改为 spot=False 或删除该分支。
- OKX/Binance：execution_contract §3、risk_engine #9/#14b 把冻结路线当现役。

### 缺失的合同（主线有代码、无合同）
- **平仓/退出**：`execution_contract §10` 约占其篇幅 45%，实际已是一个独立子系统，需要独立合同（见 execution 修改清单第 4 点）。
- **Position/Outcome 经济完整性**（verified vs administrative close、Funding 会计、三种读取口径）目前挤在 execution_contract §11，应并入 `opportunity_outcome_contract` 或独立。
- **Entry Capture policy**（`trading/entry_capture.py`：PRICE_FIRST/MAKER_PREFERRED/FILL_FIRST 与 capture_policy_id）只存在于 entry-plan-contract 的 "Capture compilation contract (#3111 Stage 2)" 小节，没有独立权威，也与上方旧表矛盾。
- **Kill switch / paused**：`risk_engine §4` 与 `execution_contract §10.3` 各写一半，没有独立合同来定义"谁能触发、谁能解除、对开仓/平仓的区别"。

### 模式观察
六份合同的共同毛病不是"错误"，而是"积层"：每次修复都往原合同里追加一段带 Issue 号和日期的叙事，没有人回头把它改成要求形式。执行层 6 份合计 2628 行，其中估计 35-40% 是此类段落。建议按 SDD 方式处理：现行行为用 EARS 写成短条款并编号，叙事与证据移到 DECISIONS/history，编号 `#NNNN` 统一放进一张引用表。
