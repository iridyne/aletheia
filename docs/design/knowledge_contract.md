# Knowledge Subsystem Contract

> 契约级别: L2 — 规范性文档,实现依据。
> 改动信任不变量前必须 change control (§5)。
> Source files: `aletheia/knowledge/manager.py` · `retrieval.py` · `guards.py` · `router.py`
> Cross-references: `docs/governance/trust_and_change_control.md §4` · `docs/design/agent_contracts.md` · `docs/design/data_model.md §3.C`
> Last updated: 2026-06-23

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
| `detect_contradictions(items)` | `manager.py:302` | **STUB** — 永远返回 `[]`,见 §6.B1 |

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
| `strategy_memory` | Strategy Designer | 策略描述、参数、生命周期、决策依据 |
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
  - `strategy_designer`: trade_case_study / research_note / post_trade_review / strategy_memory
  - `market_observer`: trade_case_study / research_note / post_trade_review / dreaming_log / trading_guard
  - `equity_observer`: research_note / trade_case_study / post_trade_review
  - `paper_trader`: trade_case_study / post_trade_review / trading_guard
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
| `applies_to` | GuardAppliesTo | 多维过滤：asset_class / scanner_family / symbol / direction / confidence_bucket / late_entry_bucket |
| `predicates` | list[GuardPredicate] | AND 逻辑，操作符白名单 (eq/ne/gt/gte/lt/lte/in/contains) |

### 4.3 evaluate_guards() (`guards.py:147`)

评估是纯确定性的，无 LLM，无风控修改：

1. 若 `guard_cfg.enabled = False`（config）→ 返回空 `GuardEvaluation`
2. 按 `scope` + `status="active"` 加载 guards
3. 对每个 guard：先 `_applies_to_context()`，再 `_predicates_match()`
4. 谓词字段白名单 `ALLOWED_GUARD_FIELDS`（`guards.py:71`，19 个字段）——未知字段 → 谓词不匹配

### 4.4 Guard 来源层级 (Tier)

Guard 的生产路径由 `trust_and_change_control.md §4.2` 定义：

- **Tier 1（自动生成）**: Dreaming Agent 或 Self-evolution 机制写 `guard_candidate`（`medium_confidence`），不可检索，需人工确认才能晋升
- **Tier 2/3（人工确认/seed）**: 通过 `create_trading_guard()` 写入 `trading_guard`（`high_confidence`），进入 FTS 索引，参与决策检索

从 `guard_candidate` 到 `trading_guard` 的晋升路径：无自动晋升机制——必须由 operator 手动调用 `create_trading_guard()` 或通过 Dashboard 确认。

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
- contradiction 检测是 stub——UI 禁止呈现"无矛盾"作为正面信号 (`trust_and_change_control.md §4.4`)

---

## §6 已知管道断裂

### A1. ps_lessons ↔ knowledge_fts 双轨脱钩

**现状**: `dreaming_agent.py:177-205` 从 `post_trade_review` 条目提炼结构化 lesson 并写入 `ps_lessons` 表（LessonStore）。而 `retrieve_lessons()` 只读 `knowledge_fts`（`retrieval.py:185`）。ps_lessons 数据永远不会进入任何 agent 的 prompt context。

**影响**: Dreaming Agent 的结构化提炼工作完全进入死路，不影响任何后续决策。

**前向链接**: `docs/issues/archive/PIPELINE_GAPS.md §A1`

### A2. ps_skills 无生产者（SkillStore 空壳）

**现状**: `aletheia/learning/skills.py` 的 SkillStore 有完整的 create/activate/deprecate 生命周期，但无任何 Agent 或 Scheduler 调用 `SkillStore().create()`。唯一写入入口是 `server/routes/persistence.py` 的手工 REST API。这是 Self-Evolution Tier 2/3 的预留架构。

**影响**: "技能学习"系统有 schema 无内容；如有相关 UI 页面，应标注"coming soon"而非空白。

**前向链接**: `docs/issues/archive/PIPELINE_GAPS.md §A2`

### B1. detect_contradictions() 是 stub

**现状**: `manager.py:302-310` 明确标注 `IMPLEMENTATION_STATUS: stub — returns empty for MVP`，永远返回 `[]`。

**影响**: 知识库中可能同时存在矛盾的 guard/lesson/research_note，系统无法检测。调用方不得将 stub 结果呈现为"已检查无矛盾"。

**前向链接**: `docs/issues/archive/PIPELINE_GAPS.md §B1`

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
| `strategy_designer` | `strategy_memory` | `agents/strategy_designer.py:20`（import） |
| `pi_operator_loop` | `operator_assessment` | `trust_and_change_control.md §4.1` |
| Self-heal layer | `system_action` | `commands._emit_self_heal_action` |

### 7.2 知识消费方（消费侧文件:行锚点）

| 消费方 | 检索方式 | 代码锚点 |
|--------|---------|---------|
| `paper_trader_ranker` | `retrieve_lessons(query, k=3, item_types=["trade_case_study","post_trade_review"])` | `agents/paper_trader_ranker.py:405-412` |
| `strategy_designer` | `build_advisory_context(role="strategy_designer", ...)` | `agents/strategy_designer.py:339-344` |
| `market_observer` | `build_advisory_context(role="market_observer", guard_context=..., ...)` | `agents/market_observer.py:386-393` |
| PI Agent (trading_context Layer 3) | 直接 `list_knowledge_items(limit=20)` + item_type 过滤 `lesson / accepted_lesson / trading_guard` | `agent_shell/trading_context.py:362-378` |
| `dreaming_agent` | `list_knowledge_items(item_type="post_trade_review", ...)` | `agents/dreaming_agent.py:69, 184, 302` |

⚠️ **注意**: `trading_context.py:367` 过滤 `item_type in ("lesson", "accepted_lesson", "trading_guard")`。
`lesson` 和 `accepted_lesson` 均不在 `trust_and_change_control.md §4.1` 的允许枚举中。这两个类型如果有历史存量数据则可被 PI Agent 消费，但当前生产者无人写入这两个类型——属于遗留字符串，实际无效。

### 7.3 promotion_gates_contract 中的 knowledge 依赖

`promotion_gates_contract.md §5.8` 要求至少一条 `item_type="post_trade_review"` 的 active knowledge_items 行通过 `post_trade_reviews_required` 门。这是 strategy lifecycle 中唯一对 knowledge_items 的强制依赖。

### 7.4 trust_and_change_control.md §4 漂移汇总

| 漂移点 | 现状 | 治理文档声称 |
|--------|------|------------|
| risk_critic 产出类型 | 代码写 `research_note`（metadata.subtype=risk_lesson） | `agent_contracts.md:185` 写 `item_type=risk_lesson` |
| trading_context.py 消费类型 | 过滤 `lesson` / `accepted_lesson`（无生产者写入） | §4.1 枚举未包含这两个类型 |

以上两处为文档与代码之间的表述不一致；代码行为符合 `trust_and_change_control.md §4.1` 的枚举定义，需更新 `agent_contracts.md` 和 `trading_context.py` 的过滤条件（或补充说明为遗留兼容）。
