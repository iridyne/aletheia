# 真实剩余工作 + Ledger 对账 — 2026-07-01

> 性质：L4 状态快照（易腐）。承接 [`REMAINING_WORK_2026-06-30.md`](REMAINING_WORK_2026-06-30.md)（已 SUPERSEDED）。
> 状态用 **git + SDD conformance matrix + 逐文件代码审计**三个"不会撒谎"的源核出，**不照搬 `docs/issues/` 与 `docs/plans/` 的表面读数**。
> 本次审计方法：21 个并行 agent 对 ROADMAP Phase 2A（6 项）+ Phase 2A+（12 项）逐条读代码定 verdict + 3 个 ledger 交叉扫描。

---

## 0. 本次收口做了什么

owner 决定「先跑再说，更新 roadmap 收口」。本次：
1. **审计** Phase 2A / 2A+ 每个子任务的真实代码状态（不信勾、只信 file:line）。
2. **更新** `ROADMAP.md`：Phase 2A 标 CLOSED；Phase 2A+ 逐条给 DONE/PARTIAL/PENDING + 证据列。
3. **踩到一个假 DONE**（见 §1.1）：上一会话 `7506a04` 提交的 SP-S05 spec 是弱 spec，代码是死码。诚实记录，不当已完成。

---

## 1. Phase 2A / 2A+ 收口结论

### 1.0 Phase 2A（可执行策略）：✅ CLOSED（代码层面）

VISION 核心赌注的结构性大缺口已闭合。6 项：**5 DONE + 1 PARTIAL(设计使然)**。

| ID | verdict | 一句话 |
|---|---|---|
| 2A.1 DSL 模型 | DONE | `strategy/dsl.py` Comparator/Condition/RuleExpr/RuleSet 全链 |
| 2A.2 词表锁 | DONE | `_VALID_INDICATORS` 编译期拒绝 + evaluator 运行时兜底 |
| 2A.3 designer 产出 entry_conditions | DONE | `parameters["ruleset"]`(编译 DSL) 与 `entry_rule/exit_rule`(文本) 共存 |
| 2A.4 paper_trader 求值 entry_conditions | **PARTIAL** | 仅对**带 RuleSet 的策略**成立；无 ruleset 策略只有 meta-gate。且最终开仓在 PI daemon（PI-central 架构使然）——非 bug，是架构 |
| 2A.5 evaluator + market snapshot | DONE | 14 indicator + 6 comparator + AND/OR + 多 TF；EV-S01/EV-S02 spec 锁 |
| 2A.6 晋级门验证规则被跑过 | DONE (`7506a04`) | `ruleset_never_evaluated:0_path_a_fills`；**PG-B06** spec GREEN |

**下一步 = 让它跑，攒干净窗证据**（观察门见 ROADMAP，不是再写码）。

### 1.1 Phase 2A+（方向性）：~85% CLOSED — 8 DONE / 3 PARTIAL / 1 PENDING（2A+.8 + 2A+.12 已修）

**✅ 假 DONE 已修复（`04ef582`，2026-07-01）**：`7506a04` 曾在 `signal_emitter._infer_direction` 加 `family == "short_bias"` 分支 + SP-S05 spec，看似 short scanner 做完——**实际是死码**（`run_all_scanners()` 从未注册 short_bias，`scanner_family` 永不等于 "short_bias"），SP-S05 只 `assert 字符串 in src` 故**假 GREEN**。已修：scanners.py 加 `score_short_bias()` 权威 scorer + 注册进 `run_all_scanners()`；删 signal_emitter 冗余死码分支；**SP-S05 重写为 L2b 行为可达性 spec**（验证 scorer 产出 + all_scores 注册）。反向验证：临时移除注册后 spec 立即变红并精确报"死码"，证明防假绿有效。此项从 PENDING→DONE，并解锁 2A+.10 端到端 short path（剩「端到端验证」本身是运行观察项）。

**教训已存记忆** [[aletheia-weak-spec-fake-green-trap]]：这正是 06-30 版 §0 "ledger 只进不出致状态失真"的升级版——连 SDD spec 都能造假绿。今后"加了新分支/family/handler"类 claim 的 spec 必须查**可达性**而非字符串存在。

**真实剩余（3 个 PENDING/被阻塞 + 收口路径）**：

| ID | verdict | gap |
|---|---|---|
| 2A+.2 designer 写 applicable_regimes | PARTIAL | direction_affinity 已写；**applicable_regimes 永远默认 "all"**，regime 过滤对 designer 产出策略是 no-op |
| 2A+.4 Dashboard 方向标签 | PARTIAL | 后端全通；前端 `types.ts` 无字段、`StrategyLifecycle.tsx` 无展示 |
| 2A+.8 专用 short scanner family | ✅ **DONE** (`04ef582`) | `score_short_bias()` + 注册 `run_all_scanners()`；SP-S05 改 L2b 行为可达性 spec；删死码分支 |
| 2A+.9 family_quota 按 regime | PENDING | 无 per-regime 结构，纯静态 dict（增强项，非收口必需） |
| 2A+.10 端到端 short 验证 | 代码就绪·待观察 | 2A+.8 已解锁；剩「端到端验证」本身是运行观察项（需 bear regime 真实出现） |
| 2A+.11 atr_4h/bb_width_4h 接 scorer | PARTIAL | rsi_4h 已消费；另两个收集未消费（US-16 defer，增强非 bug） |
| 2A+.12 UniverseMember 持久化 all_scores | ✅ **DONE** (`8d3bb93`) | model 字段 + migration 列 + service 填值；UP-14 真实 DB round-trip spec；迁移已在真实 app.db 生效 |

**剩余收口路径（2A+.8 + 2A+.12 已完成）**：
- [x] **2A+.8 真修（DONE `04ef582`）**：`score_short_bias()` + 注册 + SP-S05 行为可达性 spec + 解锁 2A+.10。
- [x] **2A+.12（DONE `8d3bb93`）**：models + migration + service 三处 + UP-14 round-trip spec。
- [ ] **2A+.4（0.25 天）**：`types.ts` + `StrategyLifecycle.tsx` 加两字段展示。
- [ ] **2A+.2（0.25 天）**：designer 推导 applicable_regimes；SP-S04 spec 扩展。

2A+.9 / 2A+.11 = 增强项，"证据出来再看要不要做"。Phase 2A+ 现 ~85%(8 DONE/3 PARTIAL/1 PENDING)。

---

## 2. SDD 真实 RED tripwire（8 个 xfail，明确声明的未完成）

conformance matrix：**493 claims / 485 implemented / 98%**。剩 8 个 xfail 是**故意声明的未做**（strict=True，做完会 XPASS 提醒去掉）：

| Claim | 文件:行 | 内容 | 分量 |
|---|---|---|---|
| DU-T01 | spec_data_utilization.py:329 | CoinalyzeCollector 未接 scheduler，OI 表无 coinalyze 行 | S（接 scheduler） |
| D8-B03 | spec_kill_conditions.py:261 | `personal_stop_loss_usdt`/`personal_stop_line` 未入 RiskConfig | S（触风控边界，需授权） |
| KL-B01 | spec_learning_pipeline.py:936 | trading_memo 时间衰减推翻路径未实现（Phase 2 defer） | M |
| LU-C05 | spec_live_unlock.py:466 | serenity_executor market_type 隔离未在 permissions 强制（Phase 5 defer） | — |
| PL-OL-05 | spec_pipeline_operator_loop.py:345 | `trigger_optuna_sweep` 只记日志无 side effect，缺 durable optuna_jobs 队列 | M |
| PCI-G05 | spec_pi_decision_chain.py:209 | B 路 council 多专家辩论零实现（owner 定调单 LLM，可能永不做） | — |
| D2-S02 | spec_risk_engine.py:1176 | `serenity_executor.py` 未创建（owner 定 Option A，不建独立 executor） | — |
| D2-B02 | spec_risk_engine.py:1234 | serenity school 平仓 PnL 归因隔离未实现 | M |

**其中 PCI-G05 / D2-S02 是 owner 已拍板不做的**（Option A / 单 LLM），属"故意永久 xfail 占位"，不是待办。

---

## 3. Ledger 陈旧项（应标 done / 已确认落地但文档未收口）

> 交叉扫描 `docs/issues/` + `docs/plans/` 发现下列已落地但文档仍标 OPEN/partial。**建议在各自文件就地标 done/archive，遵循 06-30 版 §5 收口纪律**。

| 文件 | 条目 | 实际 | 证据 |
|---|---|---|---|
| `_RAW_SWEEP_LEDGER.md` | frozen-backlog/D10 direction_affinity 链 | direction_affinity 部分 DONE（升级 partial→done）；`regime_tags`/KPI 暴露仍 OPEN | `7506a04` + ranker 消费 |
| `frozen_optimization_backlog.md` | A1/A2/A8 Layer3 lessons/guards 过滤 | 已修 | `3e042d0` |
| `frozen_optimization_backlog.md` | A13 paper_trader opportunity_id | 已修 | `b9ad957` |
| `frozen_optimization_backlog.md` | D21 market_observer 240s 挂起 | 已修 | gateway-stability `2009885` |
| `round2_negative_feedback_loop_closure.md` | Module J | 已合 master | `dd559a7`；`spec_insight_feedback_loop` 绿 |

**仍确实 OPEN（区别于上表，不收口）**：
- `round2_week1_decision_chain_fixes.md`：Fix S-Bug-C（strategy win_rate JOIN kpi）无完成证据；Fix L-Bug-D（guard 三重死锁 B0/B2/B3）scanner_observer MO-2 仍列 P0；C 类系统性重构 deferred。
- `frozen_optimization_backlog.md`：A9（operator win_rate 恒 0%）、A10（calibration_feedback 未注入 prompt）、A11（regime 重启默认值）、D14（RegimeConfig/GuardConfig 前端控件——注：RegimeConfig 面板本次核实**已做**，GuardConfig 待核）。
- `round2_immediate_data_integration_plan.md`：MO-3 runtime path（fng/top_movers 组装漏填）已进 scanner_observer P1；Step 5 CoinGlass 实质被 paywall 阻断。
- 当前活跃分支 `fix/scanner-observer-audit-fixes` 的 `scanner_observer_audit_fix_plan_2026-07-01.md`：B1 批次 P0 五项 + B2-B5 约 26 项，全 OPEN（本分支正在做）。

> ⚠️ `frozen_optimization_backlog.md` 维护规则要求完成加 `[完成 hash]` 前缀，但**当前无一条加了**，整个文件状态已严重失真——建议单独做一次该文件的收口扫描。

---

## 4. 当前分支状态提醒

本次审计在 `fix/scanner-observer-audit-fixes` 分支进行，工作区有 scanner/observer 审计相关的未提交改动（见 git status）。**Phase 2A 收口的代码（DSL/PG-B06/direction_affinity/short_bias）已在 master（`7506a04` 等）**，本次只改文档（ROADMAP + 本文 + MEMORY）。

---

## 5. 如果只挑 3 件事（更新版）

1. **让 Phase 2A 管道跑起来攒干净窗**（owner 已选此路）——用 `aletheia doctor` 观察 ROADMAP Phase 2A 观察门：ruleset 策略有没有产出 Path-A signal_pending？带 ruleset 的 Path-B 候选是否被 `ruleset_entry_not_met` 而非方向运气挡下？这是判断"要不要继续堆 phase"的唯一诚实依据（对应 Round 1 干净窗 PF 0.06 的教训）。
2. **修 2A+.8 假 DONE**（0.5 天）——不是新功能，是**堵一个会误导未来判断的假绿**：short scanner 现在是死码 + 假 GREEN spec，会让人以为 bear 市能产出做空信号（实际不能）。修完顺带解锁 2A+.10 端到端。
3. **`frozen_optimization_backlog.md` 收口扫描**（半天）——该文件零 done 标记、状态全失真，是下一个"还剩什么"误导源。
