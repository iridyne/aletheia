# Knowledge Subsystem Contract

> 契约级别: L2 — 规范性文档,实现依据。
> 改动信任不变量前必须 change control (§5)。
> Source files: `aletheia/knowledge/manager.py` · `retrieval.py` · `guards.py` · `router.py`
> Cross-references: `docs/governance/policies/trust_and_change_control.md §4` · `docs/design/contracts/agent_contracts.md` · `docs/design/data/data_model.md §3.C`
> Last updated: 2026-07-03

---

## §1 入口与对象

知识子系统的公开接口全部位于 `aletheia/knowledge/manager.py`。每个写入函数对应一种 `item_type`，不存在通用的 `create_item()` 入口——类型由调用方在编译时固定。

### 1.1 创建函数

| 函数 | file:line | 返回类型 | 空返回条件 |
|------|-----------|---------|-----------|
| `create_research_note(title, content, confidence, metadata)` | `manager.py:63` | `KnowledgeItem \| None` | 质量门失败 / 内容重复 |
| `create_dreaming_log(title, content)` | `manager.py:90` | `KnowledgeItem \| None` | 质量门失败 / 6h 内相同标题已存在 / 内容重复 |
| `create_daily_review(title, content)` | `manager.py:118` | `KnowledgeItem` | 不返回 None,直接 raise |
| `create_post_trade_review(title, content, metadata, strategy_id, confidence)` | `manager.py:135` | `KnowledgeItem \| None` | 质量门失败 / 内容重复 |
| `create_strategy_memory(strategy_id, title, content)` | `manager.py:169` | `KnowledgeItem` | 不返回 None |
| `create_trade_case_study(title, content, metadata, strategy_id, confidence, tags)` | `manager.py:186` | `KnowledgeItem \| None` | 质量门失败 / 内容重复 |
| `create_trading_guard(title, content, metadata, confidence, tags)` | `manager.py:224` | `KnowledgeItem` | 不返回 None |
| `create_guard_candidate(title, content, metadata, strategy_id, tags)` | `manager.py:250` | `KnowledgeItem` | 不返回 None |

### 1.2 生命周期函数

| 函数 | file:line | 行为 |
|------|-----------|------|
| `archive_item(item_id)` | `manager.py:285` | 软删除:status → `archived`;并从 FTS 索引移除 |
| `deprecate_item(item_id)` | `manager.py:291` | 标记过时:status → `deprecated`,confidence → `deprecated`;并从 FTS 索引移除 |
| `delete_item(item_id)` | `manager.py:297` | 永远 raise `PermissionError`——硬删除在任何路径下均被禁止 |
| `detect_contradictions(items)` | `manager.py:324` | 保守检测同 symbol/direction 下的 support-vs-block 冲突，返回需人工复核的列表 |

### 1.3 质量门（写入前置守卫）

所有具备"可返回 None"的创建函数在写入前先通过 `_is_quality_pass()` (`manager.py:28`):

- 内容长度 < 80 字符 → 拒绝 (`_MIN_CONTENT_LENGTH = 80`)
- 内容包含任何 `_PLACEHOLDER_PATTERNS`（`manager.py:15`）→ 拒绝

`create_trade_case_study` 的质量门失败使用 `logger.warning`（其他为 `logger.info`），因其失败表明内容生成出现问题。

### 1.4 去重机制

- **哈希去重** (适用 `research_note` / `post_trade_review` / `trade_case_study`)：`_content_hash()` 计算 SHA-256 前 16 hex 位 (`manager.py:23`)；`knowledge_hash_exists()` 在写入前查询同 `item_type` 是否已存在相同 hash。
- **标题时间窗口去重** (适用 `dreaming_log`)：`knowledge_title_exists_within("dreaming_log", title, hours=6)` 防止同名日志在 6h 内重复写入。

---

## §2 item_type 枚举

以下为全量枚举，来自 `trust_and_change_control.md §4.1` 和代码逆向核实。

| item_type | 主要生产者 | 一句话用途 |
|-----------|-----------|-----------|
| `research_note` | Research Agent、Market Observer、risk_critic | 市场证据、假设、反例、风险观察 |
| `strategy_memory` | legacy Strategy Designer (removed Position C) | 历史策略描述、参数、生命周期、决策依据；当前只作 legacy 知识类型保留 |
| `dreaming_log` | Dreaming Agent | 跨策略模式提炼、系统级整合输出 |
| `daily_review` | Dreaming Agent / 定时任务 | 日度运营总结、交易复盘、系统健康摘要 |
| `post_trade_review` | Post-trade Reviewer | 执行质量复盘、学习要点、改进建议 |
| `trade_case_study` | 平仓触发的分析构建器 | 单笔交易结构化叙事，用于回溯与策略晋级门 |
| `trading_guard` | 人工确认或 seed 流程 | 决策时实际执行的行为约束规则 |
| `guard_candidate` | Dreaming Agent / Operator | 待确认的临时 guard，`medium_confidence`，不参与检索 |
| `system_action` | Self-heal 层 (`commands._emit_self_heal_action`) | 无人工触发的自动修正操作审计记录 |
| `operator_assessment` | Operator loop (`operator_loop.py`) | Operator 循环的周期性决策评估摘要 |
| `trading_memo` | Insight Synthesizer (`insight_synthesizer.py`) | 近期信号滚动战术摘要，注入 AI 决策上下文 |
| `reasoning_feedback` | Reasoning feedback tracker (`reasoning_feedback.py`) | 预测结果 vs 实际结果的校准记录 |
| `calibration_report` | Confidence Calibration Engine (`confidence_calibration.py`) | 置信度评分准确性周期性报告 |

### 2.1 类型→生产者映射细节

`risk_critic` 使用 `create_research_note()` 写入，但在 metadata 中携带 `subtype: risk_lesson`
(`agents/risk_critic.py:42-43`)。此条目在 `trust_and_change_control.md §4.1` 表中未单独列出——
`risk_lesson` 是 metadata 子类型，存储 `item_type` 实为 `research_note`。
`agent_contracts.md:185` 将其记为 `item_type=risk_lesson` 属于描述层面简化。

⚠️ **漂移**: `agent_contracts.md §D.risk_critic` 写"Outputs: knowledge_items (item_type=risk_lesson)"，
但代码 `risk_critic.py:42` 实际调用 `create_research_note()`，存储 `item_type="research_note"`，
`risk_lesson` 仅在 metadata.subtype 中。`trust_and_change_control.md §4.1` 表也未列 `risk_lesson`
为独立 item_type。代码与治理文档一致；`agent_contracts.md` 行 185 需修正。

### 2.2 新增 item_type 的同步义务

`trust_and_change_control.md §4.1` 明确：新增类型须同步 5 处：manager.py 创建函数、storage 验证（自由文本列，无枚举约束）、API 路由、`spec_data_model.py` 中的 SpecClaim、本文档。

---

## §3 检索路径

检索核心实现于 `aletheia/knowledge/retrieval.py`。

### 3.1 `retrieve_lessons()` 管道 (`retrieval.py:185`)

```
query (free text)
    │
    ▼
_fts_query()   — 字母数字分词 → "token1" OR "token2" OR ...   (retrieval.py:176)
    │
    ▼ (FTS5 match + confidence filter)
knowledge_fts  — SQLite FTS5 virtual table
               — WHERE confidence = 'high_confidence'
               — optionally: AND item_type IN (...)
               — ORDER BY bm25(knowledge_fts) LIMIT 50     (retrieval.py:200-209)
    │
    ▼ (status cross-check)
repositories._select_one()  — 过滤已 archive/deprecate 但 FTS 行残留的条目  (retrieval.py:220)
    │
    ▼ (scoring)
score = relevance × recency × 1.0
    relevance = 1 / (1 + exp(bm25))       — logistic 映射,BM25 负值越小相关度越高
    recency   = max(0.25, 0.5^(age_days/30))  — 半衰期 30 天,下限 0.25  (retrieval.py:163)
    │
    ▼
sort descending → return top-k Lesson dataclass
```

**关键质量门**: 只有 `confidence = "high_confidence"` + `status = "active"` 的条目才能被检索——
噪声和推测性知识永远无法进入决策 prompt (`retrieval.py:31`, `retrieval.py:219-221`)。

### 3.2 `index_knowledge_item()` (`retrieval.py:78`)

写入路径上的索引操作:
- 仅 `_index()` (`manager.py:48`) 中被调用——`confidence == "high_confidence"` 时才触发
- 幂等:先 DELETE 再 INSERT，允许重建索引 (`retrieval.py:88-89`)
- `_clean_body()` (`retrieval.py:60`) 剥除 Markdown 头部/frontmatter，只索引正文

### 3.3 `format_memory_block()` (`retrieval.py:239`)

将 Lesson 列表渲染为带明确"ADVISORY"标注的 prompt 段落。空列表返回空字符串（agent 无增强运行）。
advisory 标签是**负载关键**的——标明这些是建议而非指令，不能覆盖 risk limits 或解锁 live。

### 3.4 `build_advisory_context()` (`router.py:32`)

上层路由，为每个 agent role 组合检索上下文：
- 按 role 过滤 `item_types`（`router.py:22-29`）：
  - `market_observer`: trade_case_study / research_note / dreaming_log / trading_guard
  - `equity_observer`: research_note / trade_case_study
  - `paper_trader`: trade_case_study / trading_guard
  - (#176) `post_trade_review` is DELIBERATELY excluded from every retrieval role — it
    is medium_confidence LLM narrative that once flooded retrieval ~1900:16; `router.py`
    forbids it and a test locks that. Do NOT re-add it here.
- 附加 guard advisory 块（若 `guard_context` 提供）
- 超过 `max_chars=2000` 截断

---

## §4 trading_guards 子系统

### 4.1 数据源

Guards 是 YAML frontmatter Markdown 文件，不是代码。文件目录：`data/knowledge/guards/`
(`guards.py:97`)。`TradingGuard` Pydantic 模型从 frontmatter 解析（`guards.py:39`）。

### 4.2 Guard 字段

| 字段 | 类型 | 含义 |
|------|------|------|
| `guard_id` | str | 唯一标识 |
| `scope` | GuardScope | 适用阶段：strategy_design / market_read / opportunity / paper_entry / exit_policy |
| `enforcement` | GuardEnforcement | advisory / scoring / blocking |
| `severity` | GuardSeverity | info / warning / critical |
| `score_penalty` | float | scoring 模式下的扣分值 |
| `block_candidate` | bool | True → `GuardEvaluation.blocked = True` |
| `applies_to` | GuardAppliesTo | 多维过滤：asset_class / scanner_family / symbol / direction / market_type / confidence_bucket / late_entry_bucket |
| `predicates` | list[GuardPredicate] | AND 逻辑，操作符白名单 (eq/ne/gt/gte/lt/lte/in/contains) |

### 4.3 evaluate_guards() (`guards.py:147`)

评估是纯确定性的，无 LLM，无风控修改：

1. 若 `guard_cfg.enabled = False`（config）→ 返回空 `GuardEvaluation`
2. 按 `scope` + `status="active"` 加载 guards
3. 对每个 guard：先 `_applies_to_context()`，再 `_predicates_match()`
4. 谓词字段白名单 `ALLOWED_GUARD_FIELDS`（`guards.py:72`，含 market_type）——未知字段 → 谓词不匹配

### 4.4 Guard 来源层级 (Tier)

Guard 的生产路径由 `trust_and_change_control.md §4.2` 定义：

- **Tier 1（自动生成）**: Dreaming Agent 或 Self-evolution 机制写 `guard_candidate`（`medium_confidence`），不可检索，需人工确认才能晋升
- **Tier 2/3（人工确认/seed）**: 通过 `create_trading_guard()` 写入 `trading_guard`（`high_confidence`），进入 FTS 索引，参与决策检索

从 `guard_candidate` 到 `trading_guard` 的晋升路径有两类：
- 人工确认：Dashboard/API 调用 `POST /knowledge/guard-candidates/{guard_id}/promote`。
- 配置开启后的自动晋升：`guard_promotion` 定时任务在 `guard.auto_promote_enabled=True` 且证据达标时晋升。

无论哪条路径，都必须保留原候选记录并写审计事件。

### 4.5 Guard 在决策流中的位置

- **硬阻断**（`paper_trader_ranker`）: `evaluate_guards(context, scope="paper_entry")` 若 `blocked=True` → 候选从排名中剔除（`paper_trader_ranker.py:412`）
- **软 advisory**（LLM prompt）: `build_advisory_context()` 注入 advisory-only guard 块，不硬阻断 (`router.py:90-101`)

---

## §5 写入约束（信任不变量）

### 5.1 禁止硬删除

`delete_item()` (`manager.py:297`) 永远 raise `PermissionError`。任何路径均不允许从 `knowledge_items` 表物理删除行。这与 `data_model.md §7.2` 的 NEVER-pruned 列表一致（`knowledge_items` 与交易因果链并列）。

替代操作：
- `archive_item()` → status = `archived`，从 FTS 移除，不出现在默认视图
- `deprecate_item()` → status = `deprecated` + confidence = `deprecated`，保留可见性作为过时知识

### 5.2 FTS 索引与 status 双轨

FTS 移除（`_unindex()`）与 DB status 更新分离执行（`manager.py:276`）。`retrieve_lessons()` 增加了二次校验（`retrieval.py:219`）以防 FTS 行残留导致已 archive 条目重现——两层独立守卫。

### 5.3 写入→索引原子性保障

`insert_knowledge_item()` 失败时，`path.unlink()` 删除已写磁盘文件（`manager.py:84, 111, 129`）以防止孤立 Markdown。Markdown 写磁盘在 DB 写入前完成，失败时回滚磁盘文件——不存在 DB 有记录但磁盘无文件的状态（反向则可能存在，由 `_read_body()` 容错处理）。

### 5.4 confidence 合法值

`VALID_CONFIDENCES = {"high_confidence", "medium_confidence", "speculative", "deprecated"}` (`manager.py:44`)。传入无效值 → `ValueError`。

### 5.5 知识不变量与审计链

`knowledge_items` 是信任持仓表（`data_model.md §7.2`）。UI 展示知识库状态必须来自真实 `knowledge_items.item_type` 值（`trust_and_change_control.md §3.1`）：

- 不得为让某 tab 有数据而将一种类型存为另一种类型（no type laundering）
- contradiction 检测是保守 support-vs-block 检测。UI 只能呈现"需要复核"数量；
  没有命中不等于所有语义矛盾都已被证明不存在。

---

## §6 当前 Learning 管线状态

### A1. ps_lessons ↔ System Operator prompt

**现状**: `trading_context.py` 读取 `LessonStore().list_all(status="applied", limit=5)` → 注入 `layer["ps_lessons"]`；`trading_decision.py` 将其渲染为 `## Recent Post-trade Lessons` 进入 System Operator prompt。

`ps_lessons` 的消费路径：`dreaming_agent` 提炼 → `LessonStore` → `lesson_reviewer` 审核并 `mark_applied()` → `trading_context` Layer3 → System Operator prompt → risk engine `_check_lesson_constraints()`（`status="applied"` 且未过期的 lesson）。

**历史缺陷（Round 1）**: `ps_lessons` 写入但无消费者，dreaming 工作进死路。该断点已修复；`accepted` 现在只是短暂中间态，稳定消费态是 `applied`。

**双轨说明**: `retrieve_lessons()` 只读 `knowledge_fts`，不读 `ps_lessons`。这不是缺陷：`knowledge_fts` 是自由文本检索，`ps_lessons` 是结构化 LessonStore。需要 guard timestamps、review status、recommendation_json 的消费者必须直接读 LessonStore。

**前向链接**: `docs/issues/archive/audits/PIPELINE_GAPS.md §A1`

### A2. ps_skills 有 draft 生产者（不自动启用）

**现状**: `aletheia/learning/skills.py` 的 SkillStore 有完整的 create/activate/deprecate 生命周期；`lesson_reviewer` 在 lesson 被应用到 policy version 后，会通过 `SkillStore.create_from_lesson()` 生成一个 `status="draft"` 的长期技能候选。

**边界**: draft skill 不会自动启用，不会 grant risk/execution/live/credential/order 权限；`FORBIDDEN_LAYERS` 仍拒绝风险、执行、凭证、live、下单相关层。人工审核后才可 activate。

**前向链接**: `docs/issues/archive/audits/PIPELINE_GAPS.md §A2`

### B1. detect_contradictions() 已有保守实现

**现状**: `manager.py` 已实现确定性、保守的 support-vs-block 检测：同一 symbol/direction 下，如果一条 active 知识支持交易方向，另一条 active guard/知识明确 block/avoid 同方向，则返回需人工复核的 contradiction。

**边界**: 该检测器不会自动裁决哪条知识正确，不会删除/降级任何知识；它只给 Learning UI/报告一个"需要复核"信号。没有命中只能说明当前保守规则未发现冲突，不能代表所有语义矛盾都已穷尽。

**前向链接**: `docs/issues/archive/audits/PIPELINE_GAPS.md §B1`

### B2. guard_candidate → trading_guard 晋升

`guard_candidate` 可以通过两条路径晋升为 `trading_guard`：

1. 人工路径：`POST /knowledge/guard-candidates/{guard_id}/promote`。
2. 自动路径：`guard_promotion` 定时任务，受 `config.guard.auto_promote_enabled`
   控制，并要求满足历史交易证据阈值。

晋升时必须保留审计链：创建 active `trading_guard`，把原候选标为 `deprecated`，
并写入 `TRADING_GUARD_PROMOTED` 事件。硬删除候选记录是违规行为。

---

## §7 与其他契约的边界

### 7.1 知识生产方（agent_contracts.md 中定义的 agents）

| Agent | 写入的 item_type | 代码锚点 |
|-------|----------------|---------|
| `post_trade_reviewer` | `post_trade_review` | `agent_contracts.md:177` |
| `risk_critic` | `research_note`（metadata.subtype=risk_lesson） | `agents/risk_critic.py:42` |
| `dreaming_daily` | `dreaming_log` | `agent_contracts.md:193` |
| `insight_synthesizer` | `trading_memo` / `reasoning_feedback` piggyback | `agent_contracts.md:201` |
| `confidence_calibration` | `calibration_report` | `agent_contracts.md:208` |
| legacy `strategy_designer` | `strategy_memory` | Removed with rules engine Position C; historical rows may remain |
| `pi_operator_loop` | `operator_assessment` | `trust_and_change_control.md §4.1` |
| Self-heal layer | `system_action` | `commands._emit_self_heal_action` |

### 7.2 知识消费方（消费侧文件:行锚点）

| 消费方 | 检索方式 | 代码锚点 |
|--------|---------|---------|
| `paper_trader_ranker` | `retrieve_lessons(query, k=3, item_types=["trade_case_study"])` | `agents/paper_trader_ranker.py:398-405` |
| `market_observer` | `build_advisory_context(role="market_observer", guard_context=..., ...)` | `agents/market_observer.py:386-393` |
| System Operator (trading_context Layer 3) | `trade_case_study` + `guard_candidate` + applied `ps_lessons` | `agent_shell/trading_context.py` Layer 3 |
| `dreaming_agent` | `list_knowledge_items(item_type="trade_case_study", ...)` → proposed `ps_lessons` | `agents/dreaming_agent.py` |

### 7.3 promotion_gates_contract 中的 knowledge 依赖

`promotion_gates_contract.md §5.8` 要求至少一条 `item_type="post_trade_review"` 的 active knowledge_items 行通过 `post_trade_reviews_required` 门。这是 strategy lifecycle 中唯一对 knowledge_items 的强制依赖。

### 7.4 trust_and_change_control.md §4 漂移汇总

| 漂移点 | 现状 | 治理文档声称 |
|--------|------|------------|
| risk_critic 产出类型 | 代码写 `research_note`（metadata.subtype=risk_lesson） | `agent_contracts.md:185` 写 `item_type=risk_lesson` |

以上为文档与代码之间的表述不一致；代码行为符合 `trust_and_change_control.md §4.1` 的枚举定义，需更新 `agent_contracts.md` 描述。

---

## §8 学习产出的可修改范围（产品定义 2026-06-30）

知识子系统的学习产出在早期可以影响以下两个范围（由浅入深，风控阈值不在其中）：

### 8.1 Ranker 权重调整（低风险）

`paper_trader_ranker.py` 只检索可信的 `trade_case_study`，并另行读取最新 `trading_memo`。
两者计算出的历史分值只是候选调整，必须分别经过 `ranker_case_score` 与
`ranker_memo_score` 的前瞻 treatment/control 授权；没有足量、成本后净收益结果与正向结论时，实际调整固定为 0：
- 历史案例负反馈（symbol + direction + scanner）→ 候选降权
- memo 的 regime、symbol affinity 与 anti-pattern → 候选软调整
- 每次候选都记录原始建议、实际增量、实验和 exposure id，两个来源不互相冒领贡献
- **边界**：Ranker 调整只影响机会排序，不影响 risk_engine 关卡，不影响 sizing

### 8.2 System Operator system prompt 片段（中风险）

System Operator 的 system prompt 分为两层：
- **基础层**（宪法约束，人类专有）：角色定义、FORBIDDEN_INTENTS 提示、风格约定
- **追加片段层**（学习可写入）：以下三条路径

**学习内容注入 System Operator prompt 的三条合法路径**（完整枚举，由 LP-S14 spec claim 锁定）：

| 路径 | 数据来源 | 代码锚点 |
|------|---------|---------|
| 1. 近期战术总结 | `trading_memo` item_type（Insight Synthesizer 写入） | `trading_context.py:406-422` → `trading_decision.py:716-719` |
| 2. 推理质量反馈 | `reasoning_feedback` item_type（Reasoning feedback tracker 写入） | `trading_context.py:425-430` → `trading_decision.py:724-727` |
| 3. 建议性事后教训 | `LessonStore` 的 applied 行（dreaming_agent 写入、lesson_reviewer 审核并应用） | `trading_context.py` Layer3 → `trading_decision.py` prompt |

学习不能直接修改 prompt 基础层文件。上述三条是当前完整的学习→推理注入路径；新增路径须同步更新 LP-S14 spec claim 和本表。

> **历史修正**：本节原文写"这是'学习影响 System Operator 推理'的唯一合法路径"（指 `trading_memo`），
> 实际代码同时存在 `reasoning_feedback` 和 `ps_lessons` 两条并行路径
>（`trading_context.py:427` 的 `layer['reasoning_feedback']` 和 `:597` 的 `layer['ps_lessons']`），
> 均通过 `trading_decision.py` 渲染进 System Operator prompt。
> 已于 2026-06-30 修正为三路完整枚举，并补 LP-S14 spec claim 锁住该枚举不变量。

### 8.3 风控阈值（暂不开放）

`risk_engine_contract.md` 中的 20 关数值属于宪法，学习机制不得修改。待 Round 2 数据验证后评估是否部分开放。

### 8.4 推翻机制（部分已实现，仍可扩展）

三路融合方案中，时间 half-life 已实现：`trading_memo` 的 regime guidance 通过
`memo_half_life_weight()` 7 天半衰期降权，并在 System Operator prompt 中显示
`Memo time weight`。`evaluate_prior_memo()` 也能把后续交易结果对旧 memo 的推翻写回
memo tags。

仍可扩展的部分：更完整的置信度更新模型，以及决策时更细粒度的主动过滤策略。
研究计划：`docs/plans/learning/learning_mechanism_research_plan.md`。

---

## §9 FTS 完整性不变量（2026-07-01 根因分析）

### 9.1 三个根因（已复盘）

2026-07-01 审计发现 FTS 记录缺失/无效由三个独立缺陷叠加造成：

| # | 根因 | 受影响类型 | 修复 |
|---|------|-----------|------|
| A | **绕过 manager 反模式** — 调用方直接调 `insert_knowledge_item()` 而不经过 `manager.py`；`_index()` 只存在于 `manager.py`，因此这些 item 永远不会进 FTS | `operator_assessment`（已修）；`trading_memo` / `reasoning_feedback` / `calibration_report` / `system_action`（均为设计上不走 FTS 的类型，见 9.2） | `operator_loop._persist_assessment` 补调 `_index()`（commit 6253679）|
| B | **磁盘文件删除导致 reindex_all() 索引空 body** — `_read_body()` 对不存在的文件返回 `""`；旧版 `reindex_all()` 不区分"文件缺失"和"文件为空"，直接写入空 body FTS 行；空行计数存在但 BM25 查询永远找不到 | `trade_case_study`（历史 reset 删除了磁盘文件，保留了 DB 行） | `reindex_all()` 增加 `if not body.strip(): skip + warn` 守卫（`retrieval.py`，本次修复）|
| C | **_persist_assessment 漏调 _index** — 函数构建了 `content` 字符串但只调用 `insert_knowledge_item()`，没有调 `_index()`；FTS 行存在但 body 为空 | `operator_assessment` | 同上 commit 6253679 |

### 9.2 哪些 item_type 不需要 FTS（设计上的 bypass 是合理的）

以下四个 item_type 绕过 manager 直接调 `insert_knowledge_item()` 是**合理的**，因为它们的消费路径不走 FTS `retrieve_lessons()`：

| item_type | 生产者 | 消费方式 | FTS 必要性 |
|-----------|--------|---------|-----------|
| `system_action` | `commands._emit_self_heal_action` | 结构化 JSON，operator 直接 SQL 查询 | ✗ 不需要 |
| `trading_memo` | `insight_synthesizer._persist_memo` | `trading_context.py` 直接 `get_latest_trading_memo()` | ✗ 不需要 |
| `reasoning_feedback` | `reasoning_feedback._persist_feedback` | `trading_context.py` 直接 `get_latest_reasoning_feedback()` | ✗ 不需要 |
| `calibration_report` | `confidence_calibration._persist_report` | `analytics_routes` 直接 `get_latest_calibration_report()` | ✗ 不需要 |

**规则**：如果新增 item_type 需要走 FTS 检索（即需要被 `retrieve_lessons()` 或 `build_advisory_context()` 找到），**必须**通过 `manager.py` 的 `create_*()` 函数写入，不得直接调 `insert_knowledge_item()`。如果消费路径是直接查询而非 FTS，则直接写入合理，但须在 §2 表中注明"FTS不需要"。

### 9.3 强制性：新 item_type 添加清单

新增 `item_type` 时，除 §2.2 的 5 处同步义务外，还须：

1. **确认消费路径**：FTS（`retrieve_lessons` / `build_advisory_context`）还是直接查询
2. **选择写入路径**：FTS 消费 → 必须在 `manager.py` 增加 `create_*()` 函数；直接查询 → 可绕过，但须在本表 §9.2 登记
3. **为 `create_*()` 函数设置正确的 `confidence`**：只有 `high_confidence` 才会被 `_index()` 索引
4. **KF-C01 spec claim 会在 CI 中自动检测** `create_*()` 是否调用了 `_index()`

### 9.4 磁盘文件与 DB 行的生命周期耦合

写入时：`write_*_file()` 先于 `insert_knowledge_item()` 执行；DB 失败时 `path.unlink()` 回滚磁盘（`manager.py:84`）。
不存在"DB 有行但磁盘无文件"的正向路径，但历史 data/ 目录 reset 可以造成反向不一致（磁盘无文件，DB 有行）。

**运营规则**：
- 不得直接删除 `data/knowledge/` 下的 Markdown 文件
- 需要撤销知识项时，使用 `archive_item()` 或 `deprecate_item()`，而非文件系统 rm
- 若发现"DB 有行磁盘无文件"的孤立状态，用 `reindex_all()` 可发现（会 WARNING 打出数量）；补救只能重新生成内容后调 `_index(item, content)` 手动注入
