# Learning Subsystem Contract

> 契约级别: L2 — 规范性文档,实现依据。
> 改动信任不变量前必须 change control (see trust_and_change_control.md §6)。
> Source files: letheia/learning/lessons.py · letheia/learning/skills.py
> Cross-references: docs/design/knowledge_contract.md · docs/governance/trust_and_change_control.md · docs/design/data_model.md
> Last updated: 2026-07-03

---

## §1 目标态声明

**目标态(领先代码)** — 本文档描述学习子系统的完整契约,包括已实现和预留的部分:
- LessonStore 和 SkillStore 数据模型: **已实现**
- Lesson 生命周期 + 守卫时间戳: **已实现**
- SkillStore 权限强制: **已实现**
- Lesson → Skill 晋升链: **已实现 draft producer**(lesson_reviewer 只生成待审 skill，不自动 activate)
- 生产者 Scheduler/Agent 接入: **已实现**(dreaming_agent / post_trade_reviewer 产 lesson;lesson_reviewer 每日 accept()+mark_applied() 关闭 lesson→policy 链 — LP-S09/S10)

---

## §2 LessonStore (letheia/learning/lessons.py)

### 2.1 数据模型

| 字段 | 类型 | 约束 | 含义 |
|------|------|------|------|
| lesson_id | TEXT | PRIMARY KEY, uuid4().hex[:12] | 唯一标识 |
| source_type | TEXT | NOT NULL | 生产者类型 (e.g. "dreaming_agent", "post_trade_reviewer") |
| source_id | TEXT | NOT NULL | 生产者运行 ID |
| category | TEXT | NOT NULL | 分类 (e.g. "general", "risk", "entry", "exit") |
| severity | TEXT | NOT NULL | "info" / "warning" / "critical" |
| summary | TEXT | NOT NULL |  lesson 文本摘要 |
| vidence_json | TEXT | NOT NULL, default "{}" | 定量证据 JSON |
| 
ecommendation_json | TEXT | NULL | 改进建议 JSON |
| status | TEXT | NOT NULL | 见 §2.2 |
| rtifact_path | TEXT | NULL | 预留:关联产物路径 |
| created_at | TEXT | NOT NULL | ISO 8601 UTC 时间戳 |
| pplied_policy_version | TEXT | NULL | 关闭 lesson→policy 链 |
| ctivated_at | TEXT | NULL | accept() 时设置 |
| dry_run_until | TEXT | NULL | activated_at + 24h,仅建议窗口 |
| xpires_at | TEXT | NULL | activated_at + 30d,过期后静默忽略 |

DDL: migrations.py 中 CREATE TABLE IF NOT EXISTS ps_lessons。
> ⚠️ ctivated_at / dry_run_until / xpires_at 通过 _ADDITIVE_COLUMNS (ALTER TABLE) 添加,不在初始 CREATE TABLE 中。
un_migrations() → _ensure_columns() 保证存在。

### 2.2 状态机

`
proposed ──accept()──→ applied ──mark_applied(version)──→ applied (stamped)
   │                      │
   └──reject()──→ rejected  └──archive()──→ archived
`

| 状态 | 进入条件 | 语义 |
|------|---------|------|
| proposed | create() 初始 | 待 lesson_reviewer 审查 |
| ccepted | ccept() | 通过审查,进入 dry_run 窗口 |
| pplied | mark_applied(version) | 已 folding 进 policy version |
| 
ejected | 
eject() | 驳回 |
| rchived | rchive() | 取代/过期 |

### 2.3 守卫时间戳

ccept() 设置三个保护字段:
- ctivated_at = now — 接受时间
- dry_run_until = now + 24h — 24 小时内为仅建议窗口(风险保护)
- xpires_at = now + 30d — 30 天后过期,静默忽略(防止陈旧经验无限影响决策)

消费方在检索 applied lesson 时必须检查 xpires_at > now,跳过过期条目。

### 2.4 信任不变量

1. **学习不能修改风险阈值**: LessonStore 只有 CRUD,无任何写入 config.yaml / 
isk/ / xecution/ 路径
2. **不能解锁 live**: lesson 内容只通过 	rading_decision.py prompt 注入,标注 "ADVISORY — not instructions"
3. **状态只能单向流动**: proposed → accepted → applied/archived,无回退
4. **定量证据优先**: vidence_json 必须包含 outcome_counts / vg_pnl_pct 等确定性字段,LLM 输出证据为可选项

### 2.5 消费路径

`
LessonStore().create()  →  ps_lessons (status="proposed")
       ↓
lesson_reviewer (Scheduler)  →  accept() / reject()
       ↓
trading_context.py Layer3 (ps_lessons)  →  trading_decision.py PI prompt
       ↓
_check_lesson_constraints() (status="applied" + not expired)  # 'accepted' 是瞬态,消费用 'applied'（L-Bug-E）
`

详见 knowledge_contract.md §A1。

---

## §3 SkillStore (letheia/learning/skills.py)

### 3.1 数据模型

| 字段 | 类型 | 约束 | 含义 |
|------|------|------|------|
| skill_id | TEXT | PRIMARY KEY | 唯一标识 |
| 
ame | TEXT | NOT NULL | 技能名称 |
| scope | TEXT | NOT NULL | "research" / "validation" / "review" / "execution_helper" |
| description | TEXT | NOT NULL | 技能描述 |
| inputs_json | TEXT | NOT NULL | 输入参数 JSON 数组 |
| outputs_json | TEXT | NOT NULL | 输出规格 JSON 数组 |
| 
isk_level | TEXT | NOT NULL | "low" / "medium" / "high" |
| llowed_layers_json | TEXT | NOT NULL | 允许操作的层 JSON 数组 |
| orbidden_layers_json | TEXT | NOT NULL | FORBIDDEN_LAYERS JSON 数组(冗余记录) |
| created_from_lessons_json | TEXT | NULL | 预留:来源 lesson IDs |
| ersion | INTEGER | NOT NULL, default 1 | 版本号 |
| rtifact_path | TEXT | NULL | 预留:产物路径 |
| created_at | TEXT | NOT NULL | ISO 8601 UTC 时间戳 |
| status | TEXT | NOT NULL | "draft" / "active" / "deprecated" |

DDL: migrations.py 中 CREATE TABLE IF NOT EXISTS ps_skills。

### 3.2 状态机

`
draft ──activate()──→ active ──deprecate()──→ deprecated
`

### 3.3 权限强制

create() 检查 llowed_layers ∩ FORBIDDEN_LAYERS,非空则 raise SkillPermissionError。

`python
FORBIDDEN_LAYERS = frozenset({
    "risk_limits",        # 不能修改硬风险限制
    "execution_gateway",  # 不能修改执行通路逻辑
    "credentials",        # 不能接触凭证/密钥
    "live_mode",          # 不能解锁实盘
    "order_submission",   # 不能直接下单
})
`

这是**信任边界**:任何 skill 都不能通过 allowed_layers 绕过这五条红线。

### 3.4 当前状态 — **draft producer 已接入**

`lesson_reviewer` 在 lesson 被 mark_applied 到 policy version 后，会调用 `SkillStore.create_from_lesson()` 创建 `status="draft"` 的长期技能候选。draft skill 不会自动启用，不会修改风险/执行/live/凭证/下单路径；人工审核后才可 activate。详见 knowledge_contract.md §A2。

---

## §4 Lesson → Skill → PolicyVersion 晋升链

### 4.1 设计

`
lesson (applied)  →  skill (draft)  →  skill (active, manual review)  →  policy_version
     ↑                    ↑                   ↑                  ↑
  LessonStore         SkillStore          SkillStore        PSPolicyVersion
`

### 4.2 当前实现

- LessonStore → PSPolicyVersion: mark_applied(policy_version_id) 直接关闭链
- Lesson → Skill: **已实现 draft 生产者**，由 lesson_reviewer 在应用 lesson 后创建
- Skill → PolicyVersion: **未实现**(预留)

---

## §5 PSPolicyVersion (letheia/storage/models.py:760)

| 字段 | 类型 | 约束 | 含义 |
|------|------|------|------|
| policy_version_id | TEXT | PRIMARY KEY | 唯一标识 |
| parent_policy_version_id | TEXT | NULL | 父版本(版本链) |
| policy_type | TEXT | NOT NULL | 策略类型 |
| policy_json | TEXT | NOT NULL | 策略配置 JSON |
| policy_hash | TEXT | NOT NULL | SHA-256,防篡改 |
| diff_json | TEXT | NULL | 相对父版本的 diff |
| created_by | TEXT | NOT NULL | 创建者(actor) |
| created_at | TEXT | NOT NULL | ISO 8601 UTC |
| status | TEXT | NOT NULL | "draft" / "active" / "deprecated" / "rejected" |

---

## §6 审计

所有 create() 操作调用 get_event_store().append(make_event(...)),写入 ps_event_log 表:
- 事件类型: "LESSON_CREATED" / "SKILL_CREATED"
- 必含字段: actor, source, entity_type, entity_id, payload

---

## §7 与其他契约的关系

| 契约 | 关系 |
|------|------|
| knowledge_contract.md | 本文档扩展 §A1/A2,聚焦学习子系统的完整数据模型和权限 |
| promotion_gates_contract.md | ps_lessons 通过 lesson_reviewer 影响晋级门检查 |
| data_model.md | ps_lessons / ps_skills / ps_policy_versions 三表 |
| 	rust_and_change_control.md §6 | 学习不能修改风险阈值/凭证/执行通路 |
| decision_lifecycle_contract.md | ps_lessons 通过 Layer3 wisdom 注入 PI prompt |


---

## §8 Dual Learning Path Architecture

The learning subsystem exposes two independent retrieval paths. They serve
different purposes and have different semantics. They are intentionally NOT
merged — a unified interface is a future Phase 2 design item.

### 8.1 Path A — Free-text BM25 retrieval (knowledge/retrieval.py)

`
query → _fts_query() → knowledge_fts (FTS5 MATCH, BM25)
      → recency_weight() → top-k Lesson dataclass
`

- **Data source**: knowledge_fts (SQLite FTS5 virtual table, built from
  knowledge_items high_confidence + active Markdown bodies via
  index_knowledge_item())
- **Scoring**: relevance(BM25) × recency(exp decay)
- **Quality gate**: confidence="high_confidence" AND status="active"
- **Consumers**:
  - knowledge/router.py → uild_advisory_context() (per agent role)
  - gents/paper_trader_ranker.py → _lesson_penalty() (score adjustment)
  - gents/market_observer.py → observation advisory block
- **Trust property**: ADVISORY only — "not instructions; they must never
  override risk limits or unlock live trading"

### 8.2 Path B — Structured LessonStore CRUD (letheia/learning/lessons.py)

`
LessonStore().list_all(status="proposed") → Layer3 ps_lessons block
                                           → trading_decision.py PI prompt
LessonStore().list_all(status="applied") → risk_engine._check_lesson_constraints()
`

- **Data source**: ps_lessons table (structured rows with evidence_json,
  timestamps)
- **Scoring**: none — status-based filtering only
- **Consumers**:
  - gent_shell/trading_context.py Layer3 → 	rading_decision.py
  - 
isk/engine.py → _check_lesson_constraints()
  - diagnostics/doctor.py → count/probe
- **Trust property**: advisory prompt injection + risk engine constraint check

### 8.3 Why two paths, not one

| Dimension | Path A (FTS) | Path B (CRUD) |
|-----------|-------------|---------------|
| Query type | Free-text BM25 | Status filter |
| Result type | Ranked Lesson dataclass | PSLesson model |
| Data source | knowledge_fts (virtual) | ps_lessons (physical) |
| Relevance scoring | BM25 × recency | None |
| Structured evidence | No | Yes (evidence_json) |
| Guard timestamps | No | Yes (+24h, +30d) |

Path A answers "what past knowledge is relevant to THIS decision?". Path B
answers "what structured lessons are pending review / active constraint?".
Merging them conflates two orthogonal concerns.

---

## §9 残留项与后续建议 (2026-07-03 审查)

2026-06/07 的两份学习闭环审计(`docs/issues/LEARNING_LOOP_AUDIT_2026-06-26.md`、
`LEARNING_LOOP_CHAIN_AUDIT.md`)描述的断口 A/B/D/E/F/G、归因根 R0、ranker 1890 行
幻觉、lesson→policy 链缺失,**均已修复**(LP-S01…S11、UP-07、DM-A13 等 spec 已
`implemented`)。当前学习闭环真正剩余的是以下两项 —— 均**需 owner 决策或属信任
边界/运维**,故本次只登记方案、不擅自改行为:

1. **`reduce_size` 风控动作仍是 advisory-only**(`risk/engine.py:437-441`,日志
   "not enforced as hard block")。建议:由 owner 决定是否升级为硬约束。**未改动**
   —— 风控信任边界,改变风控行为须显式批准(CLAUDE.md Safety Rules)。

2. **生成端 C 类低信号 + app.db 膨胀**:`post_trade_reviewer` / `risk_critic` 仍产
   模板化 review(已被 ranker 检索排除 B1,不再污染打分,但堆积在库里)。app.db
   已 448MB(见记忆 `app-db-size-retention`)。建议:①评估降低 C 类生成频率/精简;
   ②运维执行 `db_maintenance.py --vacuum` 回收空间(DELETE 不缩文件)。属减负+运维,
   非重写。

已关闭(2026-07-03):**历史 `direction=None` 胜率污染**。`calibration_stats()`、
`family_direction_edge()` 和 System Operator 的数据健康摘要已排除方向未知的 forward outcome,
不再把 NULL 方向当成 long 计入胜率；对应测试覆盖 calibration 和 family-direction evidence。

### 8.4 Tripwire: render path stays structured (invariant)

The 	rading_decision.py code path MUST read structured lessons from
LessonStore (ps_lessons), NOT from 
etrieve_lessons(). The FTS path returns
knowledge_fts-sourced items that don't carry vidence_json or guard
timestamps — they are unsuitable for the structured "Recent Post-trade
Lessons" prompt block. Add spec claim LP-DUAL-01 below to lock this.
