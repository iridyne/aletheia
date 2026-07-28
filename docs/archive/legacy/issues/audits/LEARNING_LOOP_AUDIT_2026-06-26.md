# Learning / 反思系统全生命周期审计 + 修复计划

> **触发**：2026-06-26 大额亏损日，用户质疑"learning/反思环节到底有没有学到有用东西"。
> **方法**：3 路并行 subagent（源头/流转/消费）+ 主审计实测 `data/app.db` 运行态，全部 file:line + DB 交叉验证，只读未改。
> **结论一句话**：**learning 系统在勤奋地产出噪音，唯一真正影响交易的反馈闭环喂的是幻觉，而且这条幻觉链正好穿过实验冻结的 ranker——当前 Round 评估的不是"干净方向"，是"方向 + 幻觉随机噪声"。**
> **维护者**：Aletheia Dev · 审计日 2026-06-26

---

## 0. 严重性定级

| 维度 | 判定 |
|---|---|
| 数据失真 | 🟠 真实交易案例被系统性降权（76% archived / medium），幻觉被升 high_confidence 全量进检索池 |
| 决策污染 | 🔴 ranker `_lesson_penalty` 实测 top3 = 100% 幻觉模板，对随机标的+方向产生不可控的虚假惩罚（最高 -25 分） |
| 因果链断裂 | 🔴 归因链从下单源头即断（`opportunity_id=None`），下游 4 类产物连锁失效 |
| 闭环失效 | 🟠 guard 升级链三重死锁（开关关 + 目录 bug + schema 不符），104 条候选自 6-19 起静止堆积 |
| 实验有效性 | 🔴 唯一活闭环穿过冻结 ranker → 冻结观察的"干净方向"前提被幻觉污染 |
| 潜在风险 | 🟡 240 条幻觉派生 ps_lessons 可被 risk engine 用于 block 交易，目前休眠（0 accepted），一旦人工 accept 即激活 |

---

## 1. 全生命周期链路图（源头 → 流转 → 结尾）

```
[源头·生成]                      [流转·存储/加工/升级]              [结尾·反馈进决策]
                                  ┌─ FTS 质量门: confidence==high_confidence (retrieval.py:31)
post_trade_reviewer ─幻觉─► post_trade_review(1890 high)──┼─► retrieve_lessons ─► ranker._lesson_penalty(±25) ✅唯一活闭环→污染排序
   输入只有 fills/orders 计数      │                        ├─► market_observer / strategy_designer prompt(冻结) ✅进LLM上下文
   提示问"execution quality"      │                        └─► dreaming._extract_lessons ─► ps_lessons(240,proposed)─► risk engine 可block ⏸休眠
                                  │
risk_critic ───幻觉─────► research_note/risk_lesson(high)─┘ (反复"批准率太高")
                                  │
trade_case_builder ─残缺─► trade_case_study(76% archived/medium)──✗ high+active 仅16条,几乎进不了检索池
   entry-context 全 unknown
                                  │
outcome_recorder ──残缺─► guard_candidate(104, medium)
                                  ├─✗ medium → FTS 不索引(断点B1,设计如此)
                                  └─► guard_promotion.run_promotion_check
                                        ├─✗ config auto_promote=false → 直接 skipped (断点B0)
                                        ├─✗ 读 guards/ 目录,候选写 guard_candidates/,且 guards/ 不存在 (断点B2)
                                        └─✗ 候选无 predicates/status/guard_id frontmatter (断点B3)
                                              → active trading_guard 恒=0 → evaluate_guards / guard_effectiveness 空转
                                  │
dreaming_agent ──残缺─► dreaming_log/daily_review(纯统计,只看 agent runs,不看交易盈亏)──✗ 不反馈交易(断点D)

trading_context.py:382 ─► 过滤 item_type in ("lesson","accepted_lesson","trading_guard")
                          DB 根本没有这三种类型 → PI Agent「长期智慧层」恒返回 [] ✗死过滤器(无报警)
```

**真正影响交易行为的只有一条：`post_trade_review → FTS → ranker._lesson_penalty`，而它喂的是幻觉。** 其余链路全部断裂或休眠。

---

## 2. 核心原因（按杠杆排序）

### R0（总根因 · 单一最高杠杆）：下单时切断归因链
`agent_shell/trading_decision.py:250-251` —— `create_paper_proposal(strategy_id=None, opportunity_id=None)` 硬编码 None。

market_observer 明明已把 `scanner_family / universe_member_id / confidence_score` 写进 opportunity（`market_observer.py:307-314`），但 PI Agent 下单这一步把 `opportunity_id` 丢弃，全仓库无任何回填（已 grep 确认）。后果连锁：

- proposal 无 `opportunity_id` → `trade_attribution._build_row` 算出 `opportunity=None` → `confidence/scanner_family/universe_rank/late_entry_penalty` 全 None；`factor_key` 退化为 `unknown:{sym}:{dir}:low:unknown`（`trade_attribution.py:130-137`）。
- → trade_case_study 的 entry-context 全 unknown（你看到的现象）。
- → reasoning_feedback 的 scanner 维度恒 unknown（它从 `agent_rationale` 的 `---` 后 JSON 提取，但 `decision_meta`（`trading_decision.py:225-233`）**根本不含 scanner_family 键**）。
- → guard_candidate 谓词构造需 `scanner!="unknown"` 或 `late_bucket∈{late,very_late}`，因维度全缺省 → 谓词恒空 → 几乎产不出有效 guard。
- proposal 无 `strategy_id` → post_trade_reviewer 把所有 fills 归到 `strategy_id=None`（"global"），无法按策略复盘。

**修通这一处，下游 4 类产物同时从"空/unknown"变有效。**

### R1：质量门方向反了 —— 系统性降权真实、放行幻觉
`_ALLOWED_CONFIDENCE = "high_confidence"`（`retrieval.py:31`）是检索池的唯一门。

- post_trade_reviewer：只要有任意成交就升 `high_confidence`（`post_trade_reviewer.py:85`）→ 1890 条幻觉**全部**进池。
- trade_case_builder：`high_confidence` 需 `has_full_chain`（proposal+opportunity+strategy 三 id 俱全），因 R0 恒 False → 真实案例几乎全落 medium，再叠加 76% 被 archived → **high+active 仅 16 条**。

**实测检索池比值：post_trade_review 1890 : trade_case_study 16 ≈ 118:1。** 质量门非但没挡住噪音，反而把信号挡在门外。

### R2：幻觉的生成是输入与提示词共同决定的必然
- **post_trade_reviewer**：喂 LLM 的 ctx 只有 `Fills:N; orders:M`（`:68`），无 PnL/平仓原因/滑点/成交价对照；系统提示（`:71`）却要它评 "execution quality"。加密策略机器人用市价单打 Bitget，没有 VWAP/路由/做市分层的概念 → LLM 用训练语料里的股票交易台术语（VWAP/全球路由/currency pair）填空。"93% fill rate (186/200)" 里的 **200 是 `list_fills(limit=200)` 的查询上限，不是真成交率**。
- **risk_critic**：ctx 只有 proposal 数/decision 数/rejection 数三个标量（`risk_critic.py:23`）→ LLM 唯一能算的"模式"就是批准率 → 必然反复产出"批准率太高像橡皮图章"。

### R3：唯一活闭环的惩罚机制是随机噪声
`paper_trader_ranker.py:400 _lesson_penalty` 对检索回的 24-token snippet 做字面匹配 `loss/failed/stopped/drawdown`，命中扣 `-8 × relevance`（最高 -25）。但这些词在幻觉模板里是**小节标题**（"Risk/**drawdown** metrics"、"**stop**-loss orders"）。1890 条里 12%(231) 含负面词，**是否触发取决于 BM25 把哪段切出来，完全随机不可控** → 对随机 symbol+direction 产生虚假惩罚，直接进 `_compute_trade_score` 影响选单。

---

## 3. 逐生成器评级（影响原因）

| 生成器 | 产量 | 输入完整度 | 进检索池? | 本质 | 根因 file:line |
|---|---|---|---|---|---|
| post_trade_review | 1890 | 幻觉(无盈亏对照) | ✅ high全进 | **噪音·危害最大** | `post_trade_reviewer.py:68,71,85` |
| risk_lesson(research_note) | 312 | 幻觉(只有计数) | ✅ high全进 | 噪音 | `risk_critic.py:23-29,41` |
| dreaming_log/daily_review | 65/17 | 残缺(只看agent runs) | ✅ high进 | 散文噪音+ledger真实 | `dreaming_agent.py:244-271` |
| trade_case_study | 565 | 残缺(entry全unknown) | ✗ 仅16条 | Metrics/Price真实,context空 | `trade_case_builder.py:71,187`+R0 |
| guard_candidate | 104 | 残缺(谓词恒空) | ✗ medium | 真实统计但死水 | `outcome_recorder.py:265-330`+R0 |
| strategy_memory | 41 | 完整 | ✅ | **真实**(策略定义快照) | `strategy_designer.py:530` |
| reasoning_feedback | 5 | 数字真实/scanner失效 | — | 部分真实 | `reasoning_feedback.py:82-92` |
| calibration_report | 1 | 完整 | — | **最干净**(纯SQL ECE) | `confidence_calibration.py` |
| trading_memo | 5 | 完整(真实closed PnL) | ✅(ranker±45) | **干净** | `insight_synthesizer.py:158` |
| operator_assessment | 32 | 完整(运维聚合) | ✅ | 真实 | `operator_loop.py:1149` |

---

## 4. 闭环断点详表（流转 / 结尾）

| 断点 | file:line | 类型 | 原因 |
|---|---|---|---|
| **B0** | `config.yaml` guard.auto_promote_enabled=**false** (schema 默认 True `schema.py:480`) | 开关关 | run_promotion_check 直接 skipped (`guard_promotion.py:29-30`) |
| **B2** | `guards.py:97,105-107` vs `files/markdown.py:59` | 目录 bug | 升级器读 `knowledge/guards/`，候选写 `knowledge/guard_candidates/`，且 `guards/` 不存在 → candidates 恒 [] |
| **B3** | `outcome_recorder.py:265-276` vs `guards.py:115` | schema 不符 | 104 候选 frontmatter 无 status/guard_id/predicates → 即使读对目录也全被跳过 |
| **B1** | `manager.py:267,274` + `retrieval.py:31` | 设计如此 | guard_candidate=medium → FTS 不索引（有意，但配合 B0/B2/B3 成死水）|
| **C** | `post_trade_reviewer.py:105` ← `trade_case_builder.py:124` | 条件永不满足 | strategy adaptation 触发需 case+guard≥2，上游 loss_rate≥0.7/skip_existing 恒近空 |
| **D** | `dreaming_agent.py:24-173` | 机制不存在 | consolidation 纯统计，无去重、无可执行结论 |
| **死过滤器** | `trading_context.py:382` | 隐藏 bug | 过滤 DB 不存在的 item_type → Layer3 恒空，且 try/except 静默无报警 |
| **guard window** | `trading_context.py:401` | 隐藏 bug | list_knowledge_items 按 rowid DESC 取 30，guard_candidate 被挤出窗口；且读 `metadata`(KnowledgeItem 无此字段)/`content`(空) |
| ps_lessons 悬空→激活 | `dreaming_agent.py:217`→`risk/engine.py:309` | 休眠炸弹 | 240 条幻觉派生 lesson，全 proposed/0 accepted；人工 accept 后 risk engine 即可 block 交易 |

---

## 5. 影响范围量化

1. **决策污染（最严重）**：ranker 每次选单都被 1890 条幻觉的随机惩罚干扰，真实 16 条案例几乎检索不到。
2. **实验有效性塌方**：唯一活闭环穿过冻结 ranker → Round 1 "干净窗"评估的是"方向 + 幻觉噪声"的叠加，PF/胜率不能干净归因到方向本身。
3. **数据资产浪费**：565 条真实交易案例因 R0 沦为无 context 的残片，无法用于归因/复盘/guard。
4. **存储膨胀**：app.db 388 MB，post_trade_review(1890)+system_action(2156) 占主体，多为零信息量记录。
5. **自欺式监控**：daily_review 在巨亏日仍写 "Strategy Factory IMPROVING"，因为它只看 agent run 成功率，不看盈亏。

---

## 6. 修复计划

> **冻结红线（CLAUDE.md EXPERIMENT FREEZE，2026-06-22 → Round 结束）**：判断标准 = "改动会不会让同一市况下 AI 做出不同决策？是 = 禁止"。
> **关键事实**：唯一活闭环穿过冻结的 `paper_trader_ranker.py` —— 因此几乎所有"改善检索质量"的修复都会改变排序、即改变决策、即破冻。这迫使修复必须二分。

### A 类 · 冻结期内可安全执行（不改变当前 AI 决策）

| # | 修复 | 文件(均不在冻结列表) | 为何不破冻 | 验证 |
|---|---|---|---|---|
| A1 | **修 guard 升级链 B2/B3，但不开 B0 开关** | `guards.py`、`outcome_recorder.py`/`trade_case_builder` 候选 schema | 开关仍 false → 0 升级 → 0 active guard → 不改决策。只是让机制就绪 | 升级器读对目录后 dry-run 输出候选数>0 |
| A2 | **R0a：透传 opportunity_id/strategy_id 到 proposal/position 记录，但显式钉住下游 case_study confidence=medium** | `trading_decision.py`(⚠在冻结列表,见下) + `trade_case_builder.py` confidence 逻辑 | 归因数据开始正确积累，但 confidence 仍 medium → 不进检索池 → 不改当前决策 | 新建仓 case_study 的 factor_key 不再 unknown，且 confidence=medium |
| A3 | **加监控告警（只读，不改逻辑）**：死过滤器恒空、guard 0 升级、检索池失衡比 | `doctor`/监控脚本 | 纯观测 | doctor 输出新指标 |
| A4 | **记录进 spec/契约**：把 B0-B3、死过滤器、质量门反向编码为 spec_*.py 的 `SpecClaim(status="missing")` | `tests/specs/` | 不碰运行代码 | pytest 收集到新 claim |

> **A2 的冻结判定**：`trading_decision.py` 在冻结列表内。但透传 id **不进** `_build_assessment_prompt`（LLM 决策输入），`decision_meta` 也不含它 → **不改变当前这一笔的 AI 决策**。风险点是下游 case_study 若因此升 high_confidence 进检索池会间接改 ranker —— A2 用"钉住 medium"消除该风险。**仍建议向用户显式破冻确认**（碰冻结文件）。价值：归因数据是时间的函数，越早修越早积累干净样本，Round 结束即可一键开通检索。

> **处理状态（2026-06-27 本会话，依用户 goal「非冻结即处理，冻结相关存档待 Round 1 后」）**：
> - **A3 监控** ✅ **已做** — `diagnostics/doctor.py` 新增只读探针 `_probe_learning_retrieval`，
>   输出 `loop.retrieval_pool`（实测 review:case 118.2:1 → warn）+ `loop.guard_promotion`
>   （105 candidate / 0 promoted → warn）。已注册进 `build_report()`。
> - **A4 spec** ✅ **已做** — LP-S05(R0 opportunity_id) / LP-S06(guard 升级死路) 已写为
>   xfail(strict) RED claim；并修复 SDD 收集陷阱(`spec_learning_pipeline`/`spec_strategy_pool`
>   现已被 `test_spec_conformance` 收集)。
> - **A2(R0a 透传 id)** ⏸ **存档待 Round 2** — 触及冻结文件 `trading_decision.py`,
>   审计自己也标注"仍建议显式破冻确认"。已落 `round2_week1_decision_chain_fixes.md` Fix L-Bug-C。
> - **A1(guard 链 B2/B3,不开关)** ⏸ **存档待 Round 2** — 文件虽不在冻结列表字面清单,
>   但此机制唯一目的是喂冻结的 ranker,且开关关着时**冻结窗内价值≈0、改 candidate schema 有扰动风险**;
>   LP-S06 spec 已据此把它归 Round 2。已落 `round2_week1_decision_chain_fixes.md` Fix L-Bug-D。
>   **若用户希望解冻前就把机制铺好(零决策影响、纯就绪)可单独指示,A1 可提前执行。**

### B 类 · 需破冻 / Round 结束后执行（会改变 AI 决策）

| # | 修复 | 文件 | 改变决策的方式 | 优先级 |
|---|---|---|---|---|
| B1 | **ranker 检索去幻觉**：`retrieve_lessons(item_types=...)` 去掉 `post_trade_review`，只留 `trade_case_study`（+ R0b 开通真实案例进池） | `paper_trader_ranker.py:412`(冻结) | 改 `_lesson_penalty` 输入→改排序→改选单 | 🔴最高(止血) |
| B2 | **重构 _lesson_penalty**：废弃字面匹配负面词，改用结构化 outcome 字段（real PnL/close_reason），杜绝标题误命中 | `paper_trader_ranker.py:400`(冻结) | 改惩罚值→改排序 | 🔴高 |
| B3 | **修死过滤器**：`trading_context.py:382` item_type 改为 DB 实际存在的类型（或建立映射） | `trading_context.py`(冻结) | Layer3 有内容→改 PI prompt→改决策 | 🟠中 |
| B4 | **激活 guard**：开 `auto_promote_enabled=true`（前置 A1 已修通链路） | `config.yaml` | 产生 active guard→ranker penalty/block→改决策 | 🟠中 |
| B5 | **R0b**：开通真实 trade_case_study 进检索池（撤销 A2 的 medium 钉住） | `trade_case_builder.py` | 真实案例进检索→改排序 | 🟠中 |

### C 类 · 治本（生成端，建议 Round 结束统一做）

| # | 修复 | 文件 | 说明 |
|---|---|---|---|
| C1 | **post_trade_reviewer 重构**：要么喂真实逐笔盈亏/平仓原因 + 改提示词问"信号质量"而非"执行质量"；要么停止把它升 high_confidence（降为 medium，挡在检索池外） | `post_trade_reviewer.py`(agents/,非显式冻结但间接影响决策) | 根治幻觉源 |
| C2 | **risk_critic 重构**：喂逐条拒绝原因 + approval 与后续盈亏关联，而非只有计数 | `risk_critic.py` | 根治"橡皮图章"复读 |
| C3 | **dreaming consolidation 接入交易数据**：让 daily_review 看盈亏而非只看 agent runs；增加去重与可执行结论 | `dreaming_agent.py` | 终结自欺式监控 |
| C4 | **存储治理**：批量 archive 历史幻觉 post_trade_review（manager.py 禁硬删，用 archive_item）；注意 archive 已在检索池的条目会改变 ranker → 属 B 类时序 | `manager.py` | 减 388MB 膨胀；须在 B1 之后做 |

### 推荐执行顺序

1. **立刻（A 类，冻结期）**：A2(R0a) 让干净归因数据开始积累 + A1 修通 guard 链(不开关) + A3 监控 + A4 spec。
2. **决策点**：鉴于"实验有效性塌方"（§5.2），与用户确认是否**提前结束/重启当前 Round** —— 因为现 Round 的 PF/胜率已被幻觉惩罚污染，继续观察的边际价值存疑。
3. **Round 结束 / 破冻后**：B1→B2→B5→B4→B3（止血优先），再 C1-C4 治本，最后 C4 存储治理。

---

## 7. 附：已交叉验证的关键事实（防止单 agent 误判）

- 检索池 high+active：post_trade_review **1890** vs trade_case_study **16**（主审计 + 消费 agent 双重实跑确认）。
- `config.yaml` guard.auto_promote_enabled = **false**（主审计实测）。⚠ 流转 agent 曾据 schema 默认值误判为 "开关是开的"，**以 config.yaml 实测 false 为准**。
- `guards/` 目录**不存在**；`guard_candidates/` 有 104 个 .md（主审计 + 流转 agent 确认）。
- `trade_case_study` 565 条中 **433(76%) 已 archived**（主审计实测）。
- `decision_meta`（trading_decision.py:225-233）**不含 scanner_family** → 印证 reasoning_feedback scanner 维度恒 unknown（主审计读原文）。
- ranker top3 实跑 = 100% post_trade_review（消费 agent 实跑 retrieve_lessons 确认）。
