# 实施计划：文档定义 & 整体设计修复

> 上游来源：[`docs/research/audits/PLATFORM_DOC_DESIGN_REVIEW_2026-06-30.md`](../../research/audits/PLATFORM_DOC_DESIGN_REVIEW_2026-06-30.md)（L4 审查报告）。
> 本文是 L3 计划：把报告的"该做什么"落成"具体怎么做"，文件级、带 spec claim id、排好批次与依赖。
> Date: 2026-06-30 · 性质：L3 计划（执行前需 owner 批准触信任边界/改运行态行为的批次）

---

## 0. 总原则与非目标

### 总原则（强制）
1. **SDD spec-first**：每个改动先在对应 `spec_*.py` 声明 `SpecClaim(status="missing")`（必要时 `@pytest.mark.xfail(strict=True)` 当 RED tripwire）→ 跑 RED → 改文档/代码 → 翻 GREEN → 改 `status="implemented"`。
2. **文档为准**：契约是"该是什么样"，代码追文档；本计划中"文档追代码"的几处（风控 16→20、guard 脱钩）是**让契约追平既成事实**，属诚实化，不是改设计。
3. **触信任边界走 change-control**：改 `risk_engine_contract` / `execution_contract` / `live_unlock` 走 [`trust_and_change_control §6`](../../governance/policies/trust_and_change_control.md)；改 `scheduler.py` 运行态行为（批次 C）虽不在受控模块清单，但因影响持仓生命周期，**spec-first 强制 + 改后 `aletheia doctor` 验证**。
4. **收集陷阱**：任何新 `spec_*.py` 必须加进 [`tests/test_spec_conformance.py:_import_all_specs()`](../../../tests/test_spec_conformance.py)，否则 pytest 收集 0 个（`spec_*.py` 不匹配 `python_files`）。新增 `REQUIRED_CONTRACTS` 条目必须 ≥1 个 L2a claim，否则 `test_every_required_contract_has_claims` / `test_all_levels_represented` 爆红。

### 非目标（明确不做 — 防过度工程化）
- ❌ 真 DSL parser / rule evaluator（`execution_path_honesty §1.3` 死字符串）—— 只做命名诚实化锁定，不实现。
- ❌ 真 contradiction detection engine（`manager.py:305` stub）—— 二选一删或加 tripwire，不实现引擎。
- ❌ TS operator-shell 大改 —— 除批次 E3（轻量加一个学习质量信号，可选）外不动。
- ❌ OKX/Binance 实盘对账 —— 进 backlog（实盘只 Bitget），仅在契约注明敞口排除。
- ❌ 不为补接缝新建一堆契约文件 —— **只新建 1 份**《决策生命周期契约》（它横跨所有子系统，无现成的家），其余全部复用现有契约补节。

### 总验证命令
```bash
uv run pytest tests/specs/ tests/test_architecture_fitness.py -q          # 全 spec claims (<2s)
uv run pytest tests/test_spec_conformance.py -v -s                         # Conformance Matrix
uv run pytest tests/test_position_manager.py tests/specs/spec_pipeline_position_manager.py -q   # 批次C
uv run aletheia doctor                                                     # 批次C改后必跑
```

---

## 1. 批次分解

| 批次 | 主题 | 类型 | 触信任边界 | 杠杆 | 工作量 |
|------|------|------|-----------|------|--------|
| **A** | 决策脊柱契约化（新建生命周期契约 + 中间载体定义） | 文档 + spec | 否 | 高 | M |
| **B** | 两环形式化（pi_decisions.action 命名空间 + signal_source） | 文档 + spec | 否 | 高 | S |
| **C** | 平仓升一等公民 + `paused` 对齐 `kill_switch` | 文档 + spec + **代码** | 否（但改运行态行为） | 高 | S–M |
| **D** | 风控契约追平代码（16→20 关）+ guard advisory 澄清 | 文档 + spec | **是** | 中 | S |
| **E** | 应该做：sizing 决策树 / 学习闭环诚实归类 / 监管环学习质量信号 | 文档（E3 含 TS 代码） | 否 | 中 | M |
| **F** | 文档卫生：data_layer 重归类 / 漂移标注治理规则 | 文档 | 否 | 低 | S |

**推荐执行顺序**：先 **C 的代码修复**（唯一安全相关、最便宜的真修复，关掉 paused 地雷）→ **A**（脊柱锚点，后续都挂它）→ **B** → **D** → **E** → **F**。
A/B/D/E/F 之间无硬依赖，可任意穿插；B 的文字引用 A 的生命周期契约，故 B 在 A 之后。

---

## 2. 批次 A — 决策脊柱契约化

> 关闭报告 §3.1（环 1 无单一治理文档 + 中间载体无契约）。这是整个计划的锚点。

### A1 新建《决策生命周期契约》`docs/design/contracts/decision_lifecycle_contract.md`（~70 行）
**目标**：用一份文档把横线 `opportunity → PiDecision → TradeProposal → RiskDecision → Order → Fill → Position → Exit → Review` 串起来，**只定义每个接缝的前置/后置条件 + 守卫位置 + 中间载体字段**，不重复子系统细节（指向已有契约）。

**内容骨架**：
1. §1 脊柱全景图（10 跳，引用 report §2 的"两环+账+墙"模型）。
2. §2 中间载体契约：
   - `Opportunity`（`storage/models.py:45`）：`direction_bias ∈ {long,short,neutral}`、`confidence` 语义（核实是 0–1 还是 0–100 后写死）、`market` 字段值域与到 `market_type` 的映射规则、`status` 状态机（open→reviewed）。
   - `TradingDecision`（`agent_shell/trading_decision.py:56`）：必填 `action/conviction/reasoning`；`action ∈ {trade,skip,wait,scale_in,flip}`；可选 `market_type/leverage/opportunity_id/signal_source/bear_case`。
   - `signal_pending` PiDecision（接缝载体）：写入方 `paper_trader.py:143/235`，消费方 `trading_context.py:459-472`（TTL cutoff），`signal_source ∈ {market_observer,serenity}`。
3. §3 关键不变量：`conviction ≥ 55`（`_MIN_CONVICTION_TO_TRADE`）才提案；debate 触发区间 `[40,80]`；guard chain advisory（不硬拦）；唯一硬门 = Risk Engine。
4. §4 接缝守卫位置表（每个 seam → 守卫 file:line → 对应 spec）。

**SDD**：新建 `tests/specs/spec_decision_lifecycle.py`，contract 名 `"decision_lifecycle_contract"`：
- `DL-S01` (L2a)：`Opportunity` 模型含 `direction_bias/confidence/market/status` 字段（`model_fields` 断言）。
- `DL-S02` (L2a)：`TradingDecision` dataclass 含 `action/conviction/signal_source` 字段。
- `DL-S03` (L2b)：`trading_context` 读 `pi_decisions WHERE action='signal_pending'`（AST/源码扫描 `:459-472` 存在该查询）。
- `DL-C01` (L2c)：`_MIN_CONVICTION_TO_TRADE == 55`（值锁定，改阈值即爆红，强制同步契约）。
- `DL-S04` (L2a)：`signal_source` 默认值 `"market_observer"`，合法值含 `"serenity"`（与 `spec_serenity_attribution` 不重复，仅锁默认+枚举）。

**注册**：
- `tests/test_spec_conformance.py:_import_all_specs()` 加 `spec_decision_lifecycle`。
- `REQUIRED_CONTRACTS` 加 `"decision_lifecycle_contract"`（已有 DL-S01/S02/S04 三个 L2a，满足 `test_all_levels_represented`）。
- `docs/design/README.md` 契约表"数据与接口"区加一行：`decision_lifecycle_contract | 开仓脊柱端到端 seam 契约 | ✅ 金标准（新建）`。

**验证**：`uv run pytest tests/specs/spec_decision_lifecycle.py tests/test_spec_conformance.py -q`
**风险/回滚**：纯新增，无既有行为改动；回滚 = 删文件 + 撤 3 处注册。
**待核实**（实现时先 grep 确认再写死）：`Opportunity.confidence` 的实际量纲；`Opportunity.market` 实际存的字符串格式（`models.py:45` 附近 docstring）。

### A2 修 `agent_contracts.md` 的 Phase 4a 漂移（3 处，纯文档）
- `paper_trader` output：`Execute paper trades from pending signals` → `writes PiDecision(action='signal_pending'); PI Agent 是唯一 proposal 提交者（Phase 4a），不直接下单`。
- `position_manager` exit triggers（:151）：删 `PI exit decision`（全库无实现）→ 改 `PI 经 review_portfolio_state 独立触发，与自动循环解耦`。
- `risk_critic` output（:185）：`item_type=risk_lesson` → `knowledge_items(item_type=research_note, metadata.subtype=risk_lesson)`（`knowledge_contract §2.1` 已指出，至今未改）。

**SDD**：`spec_agent_contracts.py` 若有对应 claim 同步；否则纯文档。
**验证**：`uv run pytest tests/specs/spec_agent_contracts.py -q`

---

## 3. 批次 B — 两环形式化

> 关闭报告 §3.2（两环无形式化边界 = PI 混称的文档根因）。**比报告更准**：真正的区分键是 `pi_decisions.action` 命名空间，多生产者共享总线。

### B1 `PI_OPERATOR_DESIGN_DECISIONS.md §问题2` 下补"### pi_decisions 共享总线：action 命名空间不变式"（~12 行，纯文档）
明确写出**谁写哪些 action**（实现时 grep `insert_pi_decision` 全量核对后写死）：

| 写入方 | file:line | action 命名空间 | 归属环 |
|--------|-----------|----------------|--------|
| `paper_trader` | `:143/:235` | `signal_pending` | 环1 交易（信号收集） |
| `trading_decision`(PI) | trade/skip/wait/scale_in/flip | 环1 交易（决策） |
| `operator_loop` | `:596` | `recommend_retire` / `recommend_demote` | 环2 监管 |
| `strategy_designer` | `:615/:679` | `recommend_promote` | （策略生成，非 operator！需澄清归属） |
| `reflection` | `:59` | （核实 action 值） | 环1 写回 |

不变量：(1) 环2(operator) 写入方**绝不**写 `signal_pending/trade`；(2) `signal_source` 仅在环1 有意义，值域 `{market_observer,serenity}`。

### B2 spec 锁死 action 命名空间归属
**SDD**：在 `spec_operator_loop.py`（contract `operator_layer`）加：
- `OL-Sxx` (L2c)：AST 扫 `operator_loop.py`，断言其 `insert_pi_decision` 调用的 `action` 只在 `{recommend_retire, recommend_demote}`，不含 `signal_pending`/`trade`（防 operator 越界写交易决策）。
- signal_source 枚举已由 `spec_serenity_attribution` (SA-*) 守护，**不重复**，仅在 B1 文档引用它。

**验证**：`uv run pytest tests/specs/spec_operator_loop.py tests/specs/spec_serenity_attribution.py -q`
**风险**：低（纯文档 + 一个只读 AST 断言）。
**待澄清**（owner 一句话即可）：`recommend_promote` 由 `strategy_designer` 写，归"策略生成"还是"监管环"？影响 B1 表归类，不影响代码。

---

## 4. 批次 C — 平仓升一等公民 + paused 地雷修复 ★最高价值

> 关闭报告 §3.3（退出二等公民 + paused 冻结止损）。**唯一安全相关的真修复，建议最先做。**

### C1【代码】`paused` 对齐 `kill_switch`：保护性平仓不被暂停冻结
**核实结论**：`evaluate_open_positions()`（`position_manager.py:299-388`）**从不开仓**——只遍历 open 仓位跑退出规则（SL/TP/max_hold/trailing/market-aware）并 `close_position_routed()`。当前 `scheduler.py:970-971` `if cfg.runtime.paused: return` 纯粹抽掉保护。

**清晰不变量（写进契约）**：`paused` 与 `kill_switch` 对持仓生命周期语义**必须一致**——**都挡"开新仓"，都不挡"管理/平掉已有仓"**。`kill_switch` 现状正确（风控 check#0 只拦提案=开仓；position_manager 明确绕过它，三处锁定）；`paused` 现状错误（门住了 position_manager）。

**最小改动**：`scheduler.py:_run_position_manager()` 删除 `if cfg.runtime.paused: return`（:970-971）。开仓侧的 paused 拦截保持不变（`_run_paper_trader` 仍受 paused 门 + 下游 risk engine）。

**`_run_position_reconcile`（:982-983）判断**：它做"认领外部持仓 + 关 demo 孤儿仓"。关孤儿仓是保护性的应继续；认领外部仓是记账可暂停。**建议**：同样移除 paused 门（保护优先，认领仓只是记账无害）；若 owner 想更保守，可只在 reconcile 内跳过"认领"保留"关孤儿"。→ **owner 决策点**。

**SDD（spec-first 强制）**：
1. 先在 `spec_pipeline_position_manager.py` 加 `PL-PM-07`（status="missing" 起步，RED）：
   `test_paused_does_not_block_stop_loss` — 构造一个越过 stop_price 的 open paper 仓位，设 `cfg.runtime.paused=True`，跑 `evaluate_open_positions()`，断言仓位被关闭（`status=closed`, `close_reason` 含 stop）。
2. 跑 RED（当前实现下应失败或需经 scheduler 路径）→ 改 `scheduler.py` → 翻 GREEN → `status="implemented"`。
3. 可选 `PL-PM-08`：退出触发器优先级（SL/TP > ruleset > market_aware > max_hold）的纯函数断言。

### C2【文档】`execution_contract.md` 新增 `## 10. 平仓/退出通路`（不重排现有 §，避免破坏 spec §-引用）
内容：
- §10.1 退出触发器优先级：SL/TP（`_decide_exit`）> ruleset_exit > market_aware（`enabled` 时，仅机械退出未触发后）> max_hold；PI `review_portfolio_state` 路径独立、与自动循环解耦。
- §10.2 `close_position_routed`（`position_manager.py:28-79`）路由契约：唯一平仓中枢；按 `execution_mode` 分派；`allow_live=False` 守卫（:59）阻自动循环误平实盘。
- §10.3 **paused / kill_switch 语义**（核心不变量）：两者都只挡开仓，不挡平仓；引用 PL-PM-07 + `test_position_manager.py:147`(kill_switch)。
- §10.4 stop/tp 兜底公式（`:92-115`）：position 行无记录时回退 config 默认百分比。
- §10.5 已知状态：`allow_live=True` 全库无调用点 → live 仓位当前无代码平仓路径，须手动在交易所操作（标 BACKLOG，**触信任边界**，未来加 `/api/positions/{id}/close` 需 change-control）。

**SDD**：execution_contract 是信任边界 → 走 change-control（本批改动为"补充退出侧定义 + 锁定既有保护行为"，无放松，低风险）。新 spec claim PL-PM-07 即对应 §10.3。

**验证**：`uv run pytest tests/specs/spec_pipeline_position_manager.py tests/test_position_manager.py -q && uv run aletheia doctor`
**风险/回滚**：C1 改运行态——风险点是"暂停时仍会平仓"是否符合 owner 预期（这正是要修的，但需 owner 确认语义）。回滚 = 恢复 :970-971 一行。**仅影响 paper/demo**（live_limited 本就被自动循环过滤）。

---

## 5. 批次 D — 风控契约追平代码 + guard 澄清（触信任边界 → change-control）

> 关闭报告 §3.4。**无行为改动**，纯"让契约追平既成代码 + 锁定既有真相"。

### D1 `risk_engine_contract.md §2` 补 4 关 + 标题 16→20
表格补 4 行（核实 `engine.py` 行号后写）：
- `6b` cross_broker_dedup（`:145-152`）
- `17` account_leverage_ratio（`:276-278`，已有 `spec_futures_leverage.py FL-S08`）
- `18` liquidation_buffer（`:280-283`，已有 `FL-S09`）
- `19` per_symbol_cumulative_notional（`:289-309`）
标题 `检查关卡(代码实际 16 关)` → `(代码实际 20 关，含 6b 子关)`；删除/更新"以下 16 关为金标准"措辞。

**SDD**：`spec_risk_engine.py` 的 `RE-S04`（现断言 `>=16 violation tokens`）改为 `>=20`；若 6b/19 无独立 spec，补 `RE-Sxx` L2a 结构断言（函数/check 存在）。

### D2 guard advisory 语义澄清（消除"两个 guard 都脱钩"的混淆）
在 `risk_engine_contract.md`（或 `execution_contract`）加一段：
> Guards 有两套且**都不参与执行裁决**：(1) `trading_decision` 守卫链 → 写 `decision_meta['guards']`，advisory（spec PI-S02 守）；(2) `trust/guard_chain.py:run_guards()` → **gateway 从不调用**（grep 确认），其 `block` 裁决无执行力，属观测性基础设施。**唯一硬门 = `risk/engine.py:evaluate_proposal()`。**

**SDD**：`spec_execution.py` 加 `EX-Cxx` (L2c)：AST 扫 `gateway.py`，断言**不 import** `guard_chain`/不调 `run_guards`（把"脱钩是有意的 advisory"锁成不变量，防未来误以为是 bug 去接线、或误接线引入硬拦）。

**验证**：`uv run pytest tests/specs/spec_risk_engine.py tests/specs/spec_execution.py tests/specs/spec_futures_leverage.py -q`
**change-control**：按 `trust §6` 记录"契约追平代码，无放松，无新限制松动"。
**风险**：极低（doc + 只读 AST 断言）。

---

## 6. 批次 E — 应该做（中杠杆）

### E1 投资组合 sizing 决策树 → `execution_contract.md §5` 补节（纯文档）
声明两条并存 sizing 路径何时用哪条：`paper_trader.py:124` 固定 $25 notional vs `trading/sizing.py:19-64` Kelly scaling；Kelly cap 参数来源（`schema.py:329`）。不引入新算法。

### E2 学习闭环诚实归类（文档 + SYSTEM_MAP 同步）
- `knowledge_contract.md`：明确 `ps_lessons` 双轨为**设计选择**（人工策略层 operator 消费 vs 机器产出层 FTS 检索）——或决定补一条 `mark_applied` 后写回 `knowledge_item(item_type='trade_lesson')` 进 FTS。→ **owner 二选一**。
- `ps_skills` 标 `aspirational-infrastructure`（当前无生产者无消费者，勿删，防审计误报）。
- `detect_contradictions()` stub（`manager.py:305`）：二选一——(A) 删接口调用方直接返回 []；(B) `spec_insight_feedback_loop.py` 加 `IFL-B04` xfail tripwire 绑 Tier 2。→ **owner 二选一**。
- `SYSTEM_MAP.md` 同步（~30% 过期）：`ps_lessons ❌→⚠️`；`B2 serenity ❌→⚠️`（promote→PiDecision 已闭环，原始 4 表仍不进 FTS）；`MarketAwareExitsConfig 0%→5/9 前端覆盖`；更新 `last synced` 日期。同步清理 `PIPELINE_GAPS.md` D2 条目。

**SDD**：`SYSTEM_MAP`/`PIPELINE_GAPS` 是 L4 状态，无 spec；`knowledge_contract` 改动若动 item_type 枚举需同步 `spec_*`（本批只加说明，不改枚举）。

### E3【可选，含 TS 代码】监管环补"学习质量"信号
让环2 从"管学了没有"升到"管学得好不好"：
- `operator-shell/src/schemas.ts:DreamingSummary` 加 `memo_accuracy_score?: number` / `overturned_rule_count?: number`。
- Python DreamingSummary 端点从最新 `trading_memo` 的 `tags`（`memo_effectiveness.py` 写入）返回这两值。
- `recommendation_engine.ts`：`memo_accuracy_score < 0.4` → 触发 `knowledge_quality_warning` recommendation（proposal_only）。
- 轻量只读路径，**不改学习链**。可 defer 到有数据后。

---

## 7. 批次 F — 文档卫生（低）

- **F1**：`docs/design/README.md` 把 `data_layer.md` 从 L2 契约移到"过程产物（L3 参考）"区（其内容是 OpenBB 重构计划，非契约）；把已实现的 3 条不变式（symbol 格式、per-call provider）提炼进 `decision_lifecycle_contract` 或 `execution_contract`。
- **F2**：`docs/design/README.md` 加治理规则一行：凡契约内"⚠️ 漂移/免责"标注，必须在 `docs/issues/domain/BACKEND_ISSUES.md` 有对应条目（带 file:line），让 ≥5 处漂移标注有统一落点。

---

## 8. owner 决策点汇总（执行前需拍板）

1. **批次 C 语义确认**：同意"`paused` 不阻止保护性平仓（对齐 kill_switch）"吗？`_run_position_reconcile` 是否也一并放行（建议是）？
2. **批次 B**：`recommend_promote`（strategy_designer 写）归"策略生成"还是"监管环"？
3. **批次 E2**：`ps_lessons` 双轨——明确为设计选择，还是补写回 FTS？`contradiction stub`——删还是加 tripwire？
4. **批次顺序/范围**：是否按推荐顺序（C 代码→A→B→D→E→F）逐批执行？E3 是否纳入本轮？

---

## 9. 工作量与依赖速览

```
C(代码修复, S) ──★最先, 关地雷, 仅 paper/demo
A(脊柱契约, M) ──锚点
   └─ B(两环, S) ──引用 A
D(风控追平, S) ──独立, 触信任边界走 change-control
E(应该做, M) ──独立; E3 含 TS 可 defer
F(卫生, S) ──独立, 可顺带
```
全部完成后总验证：`uv run pytest tests/specs/ tests/test_architecture_fitness.py -q && uv run pytest tests/test_spec_conformance.py -v -s && uv run aletheia doctor`
