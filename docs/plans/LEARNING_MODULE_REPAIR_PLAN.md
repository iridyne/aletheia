# Learning 模块完整修复实施计划

> **状态更新（2026-07-12）**：本文件记录 2026-07-02 当时的断路修复基线，部分现状（FTS、guard promotion、SkillStore producer 等）已经过期。后续 Learning 正贡献、自我改进、因果归因和自动回退实施，以 [`learning_positive_contribution_implementation_plan_2026-07-12.md`](learning_positive_contribution_implementation_plan_2026-07-12.md) 为准；当前行为仍以 `docs/design/learning_contract.md` 为准。

> **生成时间**：2026-07-02
> **审计来源**：`docs/issues/LEARNING_LOOP_AUDIT_2026-06-26.md` + `docs/issues/LEARNING_LOOP_CHAIN_AUDIT.md` + 代码实测
> **范围**：Learning 子系统（`aletheia/learning/` + `aletheia/knowledge/`）+ 3 个最小赚钱循环接入点 + `skills.py` 休眠问题
> **目标**：让 Learning 模块从「噪音生产者 / 空转」变为「对赚钱循环有可验证的正向作用」

---

## 0. 执行摘要

| 维度 | 当前状态 | 目标状态 |
|---|---|---|
| Path A (FTS 检索) | 已修 B1（移除幻觉源），但 FTS 池仍空（无数据） | 真实 trade_case_study 进池，ranker 评分有信号 |
| Path B (LessonStore) | 已修断口 B/E/A（部分），但 recommendation 仍可能 NULL | 16+ applied lessons 带 recommendation，风控约束生效 |
| Guard 链 | 已修断口 G（promotion 写惩罚）+ B2/B3（目录/schema），但 auto_promote=false | 自动晋升有损失模式的 guard，ranker 真正被 block/penalize |
| skills.py | 完全休眠，零消费者 | 明确接入点或归档为预留 |
| 最小赚钱循环 | 3 个接入点全部断裂或空转 | 3 个接入点全部连通且可验证 |

**已修复（代码中已体现）**：B1, B2(部分), Break B, Break F, U6, L-Bug-E, L-Bug-D/B2/B3, Break G(promotion代码), Break D(trading_context读文件), Break A(部分—dreaming传recommendation)

**仍需执行**：见下方 §1-§6

---

## 1. 架构与数据流全景

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         最小赚钱循环                                         │
│  扫描 → 机会 → 排名(ranker) → 风控(risk_engine) → 执行 → 归因 → 学习 → 改善   │
└─────────────────────────────────────────────────────────────────────────────┘
                              ↑                ↑                │
         接入点1: lesson_penalty │  接入点2: lesson_constraints│
         接入点3: Layer3 wisdom  │                             │
                                 │                             │
┌────────────────────────────────┴─────────────────────────────┴─────────────┐
│                        Learning 子系统                                       │
│                                                                             │
│  Path A (FTS/knowledge)              Path B (LessonStore)                    │
│  ├─ knowledge/manager.py             ├─ learning/lessons.py                  │
│  ├─ knowledge/retrieval.py           ├─ learning/skills.py (休眠)            │
│  ├─ knowledge/router.py              ├─ agents/lesson_reviewer.py            │
│  ├─ knowledge/guards.py              ├─ agents/dreaming_agent._extract_lessons│
│  ├─ knowledge/seed_guards.py         ├─ risk/engine._check_lesson_constraints │
│  ├─ knowledge/maintenance.py         └─ agent_shell/trading_context Layer3   │
│  ├─ analytics/guard_promotion.py                                             │
│  ├─ analytics/guard_effectiveness.py                                         │
│  ├─ universe/outcome_recorder (生成 guard_candidate)                         │
│  ├─ analytics/trade_case_builder (生成 trade_case_study)                     │
│  └─ agents/post_trade_reviewer (生成 post_trade_review — 已降为 medium)      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 已修复项确认（无需再改）

| # | 修复 | 文件:行号 | 状态 |
|---|---|---|---|
| F1 | ranker `_lesson_penalty` 只检索 trade_case_study | `paper_trader_ranker.py:398` | ✅ 已修 |
| F2 | trading_context 读 `status="applied"` | `trading_context.py:611` | ✅ 已修 |
| F3 | market_observer per-symbol guard advisory | `market_observer.py:362` | ✅ 已修 |
| F4 | dreaming _extract_lessons 独立调度 | `dreaming_agent.py:148` | ✅ 已修 |
| F5 | risk engine 查 `status="applied"` | `risk/engine.py:381` | ✅ 已修 |
| F6 | outcome_recorder 写 guard_id/status/predicates | `outcome_recorder.py:266-282` | ✅ 已修 |
| F7 | guard_promotion 读两个目录 | `guard_promotion.py:73-74` | ✅ 已修 |
| F8 | guard_promotion 写 score_penalty/block | `guard_promotion.py:53-68` | ✅ 已修 |
| F9 | trading_context 读 file_path fallback | `trading_context.py:498-510` | ✅ 已修 |
| F10 | dreaming 传 recommendation 给 store.create | `dreaming_agent.py:279` | ✅ 已修 |
| F11 | post_trade_reviewer 改 prompt 问 signal quality | `post_trade_reviewer.py:113-123` | ✅ 已修 |
| F12 | maintenance.reclassify_legacy_reviews 降旧 review | `knowledge/maintenance.py` | ✅ 已修 |
| F13 | router 排除 post_trade_review | `knowledge/router.py:36-44` | ✅ 已修 |

---

## 3. 仍断裂 — 3 个最小赚钱循环接入点

### 接入点 1：ranker `_lesson_penalty`（Path A → 排名）

**目标**：ranker 评分时能检索到真实的 trade_case_study 并施加有意义的惩罚

| 问题 | 严重度 | 文件 | 详情 |
|---|---|---|---|
| 1a. FTS 池空 | 🔴 | `knowledge_fts` 表 | 当前无数据，检索恒返回 []，lesson_penalty 永远 0.0 |
| 1b. 关键词匹配太粗糙 | 🟠 | `paper_trader_ranker.py:403-405` | `if any(neg in snippet_lower ...)` 匹配 "loss/失败/failed/stopped/drawdown"，标题含 "stop_loss" 也会误命中 |
| 1c. 无 item_types 参数传递 | 🟡 | `paper_trader_ranker.py:398` | 已限定 trade_case_study，但 confidence 门控在 retrieval 内部，无问题 |

**修复方案**：
- 1a：运行 `reindex_all()` + `reclassify_legacy_reviews()` 让 FTS 有真实数据（见 §4）
- 1b：改用结构化 `outcome` 字段判断（`meta.get("outcome") == "loss"`），不再用关键词

### 接入点 2：risk_engine `_check_lesson_constraints`（Path B → 风控）

**目标**：applied lessons 的 recommendation 能真正 block/advise 交易

| 问题 | 严重度 | 文件 | 详情 |
|---|---|---|---|
| 2a. recommendation_json 仍可能 NULL | 🔴 | `learning/lessons.py:create()` | LLM 可能不返回 recommendation，`lesson.get("recommendation") or None` 仍为 NULL |
| 2b. reduce_size action 未实现 | 🟠 | `risk/engine.py:371` | docstring 声明但代码无 `elif action == "reduce_size"` 分支 |
| 2c. 无 recommendation 时静默跳过 | 🟡 | `risk/engine.py:388-389` | `if not lesson.recommendation_json: continue` — 无日志，无法诊断 |

**修复方案**：
- 2a：在 `dreaming_agent._extract_lessons` 中，当 LLM 未返回 recommendation 时，用确定性规则生成（如 loss_rate > 60% → block_scanner）
- 2b：实现 reduce_size 分支（advisory only，log 即可）
- 2c：加 debug 日志

### 接入点 3：trading_context Layer3 wisdom（Path A+B → PI prompt）

**目标**：PI Agent 在决策时能看到有内容的 lessons/guard advisory

| 问题 | 严重度 | 文件 | 详情 |
|---|---|---|---|
| 3a. guard_candidate 是 medium_confidence | 🟠 | `knowledge/manager.py:create_guard_candidate()` | FTS 不索引 medium，guard_candidate 进不了检索池 |
| 3b. Layer3 读 guard_candidate 但无 FTS 内容 | 🟡 | `trading_context.py:560-575` | 读 DB 的 content/summary，但 guard_candidate 的 content 可能很薄 |
| 3c. ps_lessons 读 applied 但可能全 NULL recommendation | 🟡 | `trading_context.py:605-615` | 只读 summary/severity/category，不依赖 recommendation，但仍需有数据 |

**修复方案**：
- 3a：guard_candidate 保持 medium（设计如此），但 promotion 到 active 后自动变 high_confidence
- 3b：确保 guard_candidate 的 content 字段有足够信息（outcome + pnl + factor）
- 3c：确保 lesson_reviewer 正常运行产生 applied lessons

---

## 4. skills.py — 完全休眠问题

**现状**：
- `SkillStore` 有完善的安全设计（FORBIDDEN_LAYERS），但**零消费者**
- 全仓库无 `SkillStore.create()` / `SkillStore.activate()` 调用
- Lesson → Skill 晋升链标注为"预留"（`learning_contract.md §4`）
- 无 scheduler 任务，无 agent 生产 skill

**问题**：
- 占代码量但不产生任何功能
- 与 Path B 的 LessonStore 无连接
- 如果未来要启用，需要从 lesson → skill 的晋升逻辑

**修复方案**（二选一）：

**方案 A（推荐 — 最小改动）**：明确标记为预留，添加 spec 测试锁定接口不变
- 在 `skills.py` 模块 docstring 加 `RESERVED — not connected to any consumer`
- 添加 `test_spec_skill_store_interface.py` 锁定 FORBIDDEN_LAYERS 和 CRUD 接口
- 不删除，不扩展

**方案 B（如果用户要求激活）**：实现 Lesson → Skill 晋升
- 在 `lesson_reviewer.py` Step C 中，当 accepted lessons 达到 N 条时，自动创建 skill
- 定义 skill scope 从 lesson category 映射
- 添加 scheduler 任务定期 review skills

**当前推荐**：方案 A（MVP 阶段不扩展能力）

---

## 5. Guard 链 — 完整连通

**目标**：outcome_recorder 生成 candidate → guard_promotion 晋升为 active → ranker evaluate_guards 真正 block/penalize

| 环节 | 当前状态 | 需要 |
|---|---|---|
| 5a. outcome_recorder 生成 candidate | ✅ 已修（写 guard_id/predicates） | 无 |
| 5b. auto_promote_enabled | ❌ config 中=false | 改为 true 或提供手动触发 |
| 5c. guard_promotion 读 candidates | ✅ 已修（读两个目录） | 无 |
| 5d. guard_promotion 写惩罚值 | ✅ 已修（score_penalty=10, block=True） | 无 |
| 5e. seed_guards 已存在 | ✅ 5 个 seed guard 有惩罚值 | 确认文件已写入磁盘 |
| 5f. ranker evaluate_guards | ✅ 调用 evaluate_guards | 确认 active guards 有非零惩罚 |

**修复方案**：
- 5b：在 config.yaml 中设置 `guard.auto_promote_enabled: true`（或提供 CLI 命令手动触发）
- 5e：运行 `ensure_seed_guards()` 确认 5 个 seed guard 文件存在
- 5f：确认 seed guard 的 predicates 字段在 ranker 的 guard_context 中有对应值

---

## 6. 实施计划（按优先级）

### Phase 0：数据准备（5 min，无代码改动）

**目标**：让 FTS 池有真实数据

```bash
# 1. 降旧 post_trade_review 的 confidence（已在 maintenance.py 实现）
uv run python -c "from aletheia.knowledge.maintenance import reclassify_legacy_reviews; reclassify_legacy_reviews()"

# 2. 重建 FTS 索引
uv run python -c "from aletheia.knowledge.retrieval import reindex_all; reindex_all()"

# 3. 确认 seed guards 已写入
uv run python -c "from aletheia.knowledge.seed_guards import ensure_seed_guards; ensure_seed_guards()"

# 4. 确认 doctor 探针正常
uv run aletheia doctor
```

### Phase 1：接入点修复（核心，~20 行改动）

#### 1a. 修复 `_lesson_penalty` 关键词匹配 → 结构化 outcome

**文件**：`aletheia/agents/paper_trader_ranker.py:401-406`

```python
# before: 关键词匹配（误命中 "stop_loss" 等标题）
for lesson in lessons:
    snippet_lower = lesson.snippet.lower()
    if any(neg in snippet_lower for neg in ("loss", "失败", "failed", "stopped", "drawdown")):
        penalty -= 8.0 * lesson.score

# after: 用 outcome 字段判断（需 retrieval 返回 outcome 元数据）
for lesson in lessons:
    # outcome 字段从 trade_case_study frontmatter 提取，存入 FTS 或 Lesson dataclass
    if getattr(lesson, "outcome", None) == "loss":
        penalty -= 8.0 * lesson.score
```

**注意**：需要 `retrieve_lessons` 返回 outcome 字段。两种方案：
- 方案 1：在 FTS 中加 outcome 列，`index_knowledge_item` 时写入
- 方案 2：在 Lesson dataclass 中加 outcome 字段，从 FTS snippet 解析

**推荐方案 1**（更干净）：
- `knowledge/retrieval.py`：FTS 加 `outcome` 列，`index_knowledge_item` 从 meta 提取
- `paper_trader_ranker.py`：用 `lesson.outcome` 判断

#### 1b. 实现 reduce_size action

**文件**：`aletheia/risk/engine.py:411`（在 block_scanner elif 后追加）

```python
elif action == "reduce_size":
    # Advisory only — log, does not block
    factor = float(rec.get("factor", 1.0))
    logger.info(
        "lesson reduce_size advisory: lesson=%s factor=%.2f (not enforced)",
        lesson.lesson_id, factor,
    )
```

#### 1c. 修复 recommendation_json 可能 NULL 的问题

**文件**：`aletheia/agents/dreaming_agent.py:266-280`

当 LLM 未返回 recommendation 时，用确定性规则生成：

```python
# 在 store.create 之前，如果 LLM 没返回 recommendation，用聚合证据生成
rec = lesson.get("recommendation")
if not rec and agg_evidence.get("outcome_counts"):
    losses = agg_evidence["outcome_counts"].get("loss", 0)
    total = sum(agg_evidence["outcome_counts"].values())
    if total >= 3 and losses / total >= 0.6 and agg_evidence.get("scanner_families"):
        rec = {"action": "block_scanner", "scanner": agg_evidence["scanner_families"][0]}
```

#### 1d. 加 recommendation NULL 诊断日志

**文件**：`aletheia/risk/engine.py:388-389`

```python
if not lesson.recommendation_json:
    logger.debug("lesson %s: recommendation_json is NULL, skipping", lesson.lesson_id)
    continue
```

### Phase 2：Guard 链连通（~10 行改动）

#### 2a. 开启 auto_promote

**文件**：`data/config.yaml`

```yaml
guard:
  auto_promote_enabled: true  # 改为 true
  min_matching_trades: 5      # 确认阈值合理
```

#### 2b. 确认 seed guards 文件存在

运行 `ensure_seed_guards()` 后检查 `data/knowledge/guards/` 目录有 5 个 .md 文件。

#### 2c. 确认 guard promotion 运行

```bash
uv run python -c "from aletheia.analytics.guard_promotion import run_promotion_check; print(run_promotion_check())"
```

### Phase 3：trading_context Layer3 完善（~15 行改动）

#### 3a. guard_candidate content 充实

**文件**：`aletheia/universe/outcome_recorder.py:258-291`

确认 `_build_guard_candidate_content` 生成的 content 包含足够信息（outcome + pnl + factor + close_reason）。

#### 3b. Layer3 读 guard_candidate 时加 file_path fallback

**文件**：`aletheia/agent_shell/trading_context.py:560-575`

与 trade_case_study 类似，当 content 为空时读 file_path。

### Phase 4：skills.py 处理（~5 行改动）

#### 4a. 标记为预留

**文件**：`aletheia/learning/skills.py` 模块 docstring

```python
"""Skill store — learned agent capabilities with permission enforcement.

RESERVED — not connected to any consumer in the current MVP.
Lesson → Skill promotion chain is a future Tier 2/3 feature.
See docs/design/learning_contract.md §4.
"""
```

#### 4b. 添加 spec 测试锁定接口

**文件**：`tests/specs/spec_skill_store.py`（新建）

```python
"""Lock the SkillStore interface — FORBIDDEN_LAYERS and CRUD contract."""

def test_forbidden_layers_include_risk_execution_credentials():
    from aletheia.learning.skills import FORBIDDEN_LAYERS
    assert "risk_limits" in FORBIDDEN_LAYERS
    assert "execution_gateway" in FORBIDDEN_LAYERS
    assert "credentials" in FORBIDDEN_LAYERS
    assert "live_mode" in FORBIDDEN_LAYERS
    assert "order_submission" in FORBIDDEN_LAYERS

def test_skill_create_rejects_forbidden_layer():
    from aletheia.learning.skills import SkillStore, SkillPermissionError
    store = SkillStore()
    try:
        store.create(name="bad", scope="execution_helper", description="test",
                     allowed_layers=["risk_limits"])
        assert False, "Should have raised SkillPermissionError"
    except SkillPermissionError:
        pass
```

### Phase 5：验证

```bash
# 1. 全量测试
uv run pytest tests/specs/ -q

# 2. doctor 探针
uv run aletheia doctor
# 期望：loop.retrieval_pool 不为空，loop.guard_promotion 有 active guards

# 3. 模拟一次 lesson_reviewer 运行
uv run python -c "
import asyncio
from aletheia.agents.lesson_reviewer import run_lesson_reviewer
result = asyncio.run(run_lesson_reviewer(permission_mode='Observe'))
print(result)
"

# 4. 模拟一次 ranker 运行（确认 lesson_penalty 不再为 0）
uv run python -c "
from aletheia.agents.paper_trader_ranker import build_paper_candidates
candidates = build_paper_candidates(limit=10)
for c in candidates[:3]:
    print(f\"{c['symbol']} score={c['trade_score']} guard_penalty={c.get('guard_penalty', 0)}\")
"

# 5. 确认 FTS 检索有结果
uv run python -c "
from aletheia.knowledge.retrieval import retrieve_lessons
lessons = retrieve_lessons('BTC long momentum', k=3)
for l in lessons:
    print(f\"{l.item_type} score={l.score} snippet={l.snippet[:80]}\")
"
```

---

## 7. 风险与注意事项

| 风险 | 缓解 |
|---|---|
| 开启 auto_promote 后 guard 可能误 block 好交易 | seed guard 的 predicates 已用确定性字段（late_entry_penalty, factor_status），不会误命中 |
| recommendation 确定性规则可能过于激进 | 仅当 loss_rate >= 60% 且 sample >= 3 时才生成 block_scanner |
| FTS 重建期间检索不可用 | reindex_all 是原子操作（DELETE + INSERT），短暂空窗可接受 |
| skills.py 标记为预留后未来激活成本 | 接口已锁定（FORBIDDEN_LAYERS），激活时只需添加晋升逻辑 |

---

## 8. 改动文件清单

| 文件 | 改动类型 | Phase |
|---|---|---|
| `aletheia/agents/paper_trader_ranker.py` | 修改 `_lesson_penalty` 关键词→结构化 | 1a |
| `aletheia/risk/engine.py` | 加 reduce_size 分支 + NULL 诊断日志 | 1b, 1d |
| `aletheia/agents/dreaming_agent.py` | 加 recommendation 确定性回退 | 1c |
| `aletheia/knowledge/retrieval.py` | FTS 加 outcome 列 | 1a |
| `data/config.yaml` | auto_promote_enabled: true | 2a |
| `aletheia/agent_shell/trading_context.py` | guard_candidate file_path fallback | 3b |
| `aletheia/learning/skills.py` | 模块 docstring 标记预留 | 4a |
| `tests/specs/spec_skill_store.py` | 新建 spec 测试 | 4b |
| `tests/specs/spec_learning_pipeline.py` | 更新/新增回归测试 | 5 |

---

## 9. 不在本次范围（明确排除）

- ❌ 不实现 Skill → PolicyVersion 晋升链（Tier 2/3 预留）
- ❌ 不修改 post_trade_reviewer 的 LLM prompt（已修，且降为 medium_confidence）
- ❌ 不修改 risk engine 的其他 18 个检查项
- ❌ 不添加新的知识类型或 agent
- ❌ 不修改冻结文件（trading_decision.py 的 opportunity_id 透传留 Round 2）
