# Serenity × AI Berkshire 整合实施计划

**日期**: 2026-06-28  
**来源**: docs/issues/AI_BERKSHIRE_INTEGRATION_ANALYSIS_2026-06-28.md  
**当前分支**: worktree-lazy-drifting-clarke → merge 后入 master

> **✅ RECONCILED 2026-06-30**（见 [REMAINING_WORK_2026-06-30 §3](../../status/REMAINING_WORK_2026-06-30.md)）：**P0(S4 强制裁决 / S2 可研究性) + P1 之 S1(4 视角并行) 已实现**——`spec_serenity_verdict` / `spec_serenity_researchability` / `spec_serenity_multi_lens` 均注册且 conformance 绿；P2 的 `TradingDecision.bear_case` 字段已落地（`trading_decision.py:89`）。**待核实**：S3 信息质量门控、P3(D1/D2/U1/U2 数据质量/日志) 的实现度，下次收口逐条确认。

---

## 总览：整合的核心思路

AI Berkshire 是 Serenity Lens 的**手工原型**：两者都做"研究目标 → 采集证据 → 打分 → 决策"，只是自动化程度不同。整合不是"把外来功能嫁接进来"，而是**用 AI Berkshire 的优秀设计填补 Serenity 现有的结构性缺口**。

选取的 10 个整合点分 4 个优先级批次：

| 批次 | 条目 | 描述 |
|------|------|------|
| P0 | S4, S2 | 强制裁决输出、可研究性评级 |
| P1 | S1, S3 | 4 视角并行研究、信息质量门控 |
| P2 | P1, P2 | 信念论据结构化、TradingDecision bear_case |
| P3 | D1, D2, U1, U2 | 数据质量管理、日志与操作改进 |

---

## P0 批次（最高优先级）

### S4：Serenity 强制裁决（Forced Verdict）

**对标 AI Berkshire**: `forced_verdict.py` — 禁止"一方面…另一方面…"废话，必须给出 buy/sell/watch + 价格区间。  
**填补的 Serenity 缺口**: `generate_opportunities` 阶段只生成 thesis 文字，没有机器可读的 bull case / bear case / verdict 三元组。

#### 改动清单

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `aletheia/storage/models.py` | `SerenityOpportunity` 加 `verdict_json: str = "{}"` 字段 |
| 2 | `aletheia/storage/migrations.py` | 在 `_ADDITIVE_COLUMNS` 增加 `("verdict_json", "serenity_opportunities", "TEXT DEFAULT '{}'")` |
| 3 | `aletheia/agents/research_worker.py` | `_OPPORTUNITIES_SYSTEM` 提示词增加 `verdict` 对象要求；Stage 4 写 `verdict_json` |
| 4 | `agent_shell/trading_context.py` | Layer 3 serenity_opps 块增加 `verdict` 字段（从 verdict_json 解析） |
| 5 | `aletheia/server/routes/data.py` | `/api/serenity/opportunities/:id` 响应增加 `verdict` 字段 |
| 6 | `tests/specs/spec_serenity_verdict.py` | 新建 SDD spec 文件，4 个 claims（SV-S01~SV-C01） |
| 7 | `tests/test_serenity_verdict.py` | E2E 管道测试 + 单元测试 |

#### `verdict_json` 结构（TypedDict 形式）

```python
{
  "recommendation": "long" | "short" | "watch" | "avoid",  # 强制非废话
  "conviction": int,        # 0-100，禁止 null
  "bull_case": str,         # 支持论点（必填，≥1句）
  "bear_case": str,         # 反对论点（必填，≥1句，不得省略）
  "key_assumption": str,    # 策略成立的核心前提（AI Berkshire thesis-tracker 对应物）
  "invalidation": str,      # 假设破损触发退出的条件
  "price_target": float | None,  # 可选
  "assessed_at": str,       # ISO 时间戳
}
```

#### SDD Claims

- **SV-S01** L2a: `SerenityOpportunity.verdict_json` 字段存在且 DB 列已迁移
- **SV-S02** L2a: `research_worker.py` Stage 4 prompt 包含 verdict 对象要求
- **SV-B01** L2b: 管道运行后生成的 opportunity 的 `verdict_json` 包含 `recommendation` + `bear_case`（非空字符串）
- **SV-C01** L2c: `trading_context.py` Layer 3 serenity_opps 中的 `verdict` 字段能被 PI Agent 读取

#### 依赖顺序

无前置依赖，可独立实施。

---

### S2：机会级别可研究性评级（Researchability Grade A/B/C）

**对标 AI Berkshire**: 研究可行性分级 — A级可充分研究、B级信息有限、C级黑箱。  
**填补的 Serenity 缺口**: `generate_opportunities` 产出的 opportunity 没有反映"这个标的的信息质量是否足以支撑高分"。导致 LLM 幻觉高自评蒙混过关。

#### 改动清单

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `aletheia/trading/serenity_evidence.py` | 新增 `assess_researchability(evidence_list) -> tuple[str, str]` 函数（纯确定性） |
| 2 | `aletheia/storage/models.py` | `SerenityOpportunity` 加 `researchability_grade: str = "C"` |
| 3 | `aletheia/storage/migrations.py` | `_ADDITIVE_COLUMNS` 增加 `("researchability_grade", "serenity_opportunities", "TEXT DEFAULT 'C'")` |
| 4 | `aletheia/agents/research_worker.py` | Stage 4 在构造 `SerenityOpportunity` 时调用 `assess_researchability(theme_evidence)` |
| 5 | `aletheia/trading/serenity_scoring.py` | `score_serenity_opportunity` 增加 C 级评分天花板（≤40分，避免幻觉机会进入 research_priority） |
| 6 | `tests/specs/spec_serenity_researchability.py` | 新建 spec，4 claims（SR-S01~SR-B02） |
| 7 | `tests/test_serenity_researchability.py` | 单元测试 `assess_researchability` + 评分天花板验证 |

#### 评级算法（`assess_researchability` 确定性规则）

```
A = 至少 2 条 assessed_tier ≥ high_confidence 的支持证据，且至少 1 条有 URL
B = 至少 3 条 assessed_tier ≥ likely 的支持证据
C = 其余（speculative 为主，或证据 < 3 条）
```

#### 评分天花板

```python
RESEARCHABILITY_CEILING = {"A": 100, "B": 70, "C": 40}
```

C 级机会永远不会超过 40 分 → 自动进入 `watch`，不可能进 `research_priority`（需 52+）。

#### SDD Claims

- **SR-S01** L2a: `serenity_evidence.py` 导出 `assess_researchability`
- **SR-S02** L2a: `SerenityOpportunity.researchability_grade` 字段存在
- **SR-B01** L2b: C 级机会评分被天花板截断到 ≤40
- **SR-B02** L2b: A 级机会（≥2 条 high_confidence + URL）评分无天花板约束

#### 依赖顺序

无前置依赖。S4 和 S2 可并行实施。

---

## P1 批次（第二优先级）

### S1：research_worker 4 视角并行对抗研究

**对标 AI Berkshire**: 4 个 investor persona（Buffett/Munger/段永平/李录）并行研究，team-lead 综合矛盾。  
**填补的 Serenity 缺口**: Stage 1 `collect_evidence` 是单次 LLM 调用，同一视角，容易产生方向单一化偏差（Round 1 short 17/23 根因之一）。

#### 改动清单

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `aletheia/agents/research_worker.py` | 新增 4 个视角 system prompt（`_LENS_TECHNICAL_SYSTEM`, `_LENS_FLOW_SYSTEM`, `_LENS_SENTIMENT_SYSTEM`, `_LENS_MACRO_SYSTEM`）；Stage 1 改为 `asyncio.gather` 并行 4 次调用；每条 evidence 加 `agent_lens` 字段 |
| 2 | `aletheia/storage/models.py` | `SerenityEvidence` 加 `agent_lens: str | None = None` |
| 3 | `aletheia/storage/migrations.py` | `_ADDITIVE_COLUMNS` 增加 `("agent_lens", "serenity_evidence", "TEXT")` |
| 4 | Stage 1 后 | 新增 synthesis step：`_SYNTHESIS_SYSTEM` 提示词综合 4 组证据的矛盾，产出 `contradictions` + `consensus` 摘要，写入 run event |
| 5 | `tests/specs/spec_serenity_multi_lens.py` | 新建 spec，5 claims（ML-S01~ML-B02） |
| 6 | `tests/test_serenity_multi_lens.py` | 管道 E2E 测试（mock LLM，验证 4 个 lens 都被调用） |

#### 4 视角定义

| Lens | 类比 AI Berkshire 角色 | 关注维度 |
|------|----------------------|---------|
| technical | 定量分析师 | OHLCV、指标、技术形态 |
| flow | 资金流向专家 | 资金费率、清算、持仓比、microstructure |
| sentiment | 情绪研究员 | news、social、fear/greed、宏观叙事 |
| macro | 大局观 | BTC 周期位置、宏观信号（COIN/MSTR 等代理） |

#### SDD Claims

- **ML-S01** L2a: `research_worker.py` Stage 1 调用 ≥3 个不同 system prompt
- **ML-S02** L2a: `SerenityEvidence.agent_lens` 字段存在且 DB 已迁移
- **ML-B01** L2b: 管道运行后 evidence 记录中存在至少 2 种不同的 `agent_lens` 值
- **ML-B02** L2b: Stage 1 synthesis 事件写入 run events（event_type="lens_synthesis"）
- **ML-C01** L2c: 4 lens 数据均到达 Stage 2 claims 提取

#### 依赖顺序

依赖 S4 和 S2 完成后（verdict 和 researchability 需要稳定的 evidence 结构）。

#### 注意

本项目 LLM 调用最多（Stage 1 由 1 次变 4 次）。需评估 token 预算影响：  
- 每次研究运行额外 3 次 LLM 调用，约 3× Stage 1 成本  
- 已知 Databricks workspace rate limit 问题 → 并发控制需要加 `asyncio.Semaphore(2)` 或顺序执行（由 `_EXECUTOR_LOCK` 控制）

---

### S3：信息质量门控（Quality Gate on Evidence Sources）

**对标 AI Berkshire**: `financial_rigor.py` 的精度理念 + A/B/C 可研究性分级。  
**填补的 Serenity 缺口**: 目前 `serenity_evidence.py` 的 `_SOURCE_CEILING` 只处理"必然投机"的来源，没有处理"相对可信"的分级。

#### 改动清单

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `aletheia/trading/serenity_evidence.py` | 新增 `SOURCE_RELIABILITY_TIER`（来源可靠性映射，如 `"news": "likely"`, `"market_data": "high_confidence"`） |
| 2 | `assess_evidence_tier` | 增加来源可靠性上限（来源可靠性 < proposed → clamp down） |
| 3 | `tests/specs/spec_serenity_evidence.py` | 在现有 spec 追加 SE-S03（来源可靠性天花板）+ SE-B02（数据合规） |

#### 依赖顺序

S2 实施完成后追加（共用 `serenity_evidence.py` 的工具函数）。

---

## P2 批次（第三优先级）

### P1：TradingDecision 信念论据结构化（Belief Decomposition）

**对标 AI Berkshire**: 论据健康评分 — 每个持仓附带"假设清单"，假设破损则触发重评。  
**填补的 Serenity 缺口**: PI Agent 的 `TradingDecision` 只有 `reasoning`（自由文字），没有结构化的"基于哪些假设做此决定"。

#### 改动清单

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `agent_shell/trading_decision.py` | `TradingDecision` 加 `key_assumptions: list[str] = field(default_factory=list)` |
| 2 | `aletheia/storage/models.py` | `PIDecision` 加 `key_assumptions: str = "[]"` |
| 3 | `aletheia/storage/migrations.py` | `_ADDITIVE_COLUMNS` 增加 `("key_assumptions", "pi_decisions", "TEXT DEFAULT '[]'")` |
| 4 | PI Agent prompt | 要求 PI 在决策时明确列出 `key_assumptions`（3 条以内，简洁） |
| 5 | `tests/specs/spec_serenity_verdict.py` | 在 S4 spec 中追加 SV-B02（PI verdict 包含 assumptions） |

#### 依赖顺序

依赖 S4（verdict 机制先建立，assumptions 是 verdict.key_assumption 的 PI 侧映射）。

---

### P2：TradingDecision bear_case 必填字段

**对标 AI Berkshire**: 强制裁决机制 — bear case 不能为空，不能逃避反向论据。  
**填补的 Serenity 缺口**: `TradingDecision` 有 `opposing_signals: list[str]` 但可以为空，且没有强制的反向论据文字。

#### 改动清单

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `agent_shell/trading_decision.py` | 加 `bear_case: str = ""` 字段（向后兼容，默认空字符串） |
| 2 | `aletheia/storage/models.py` | `PIDecision` 加 `bear_case: str = ""` |
| 3 | `aletheia/storage/migrations.py` | `_ADDITIVE_COLUMNS` 增加 `("bear_case", "pi_decisions", "TEXT DEFAULT ''")` |
| 4 | PI Agent prompt（`trading_context.py` 末尾 system prompt） | 要求 `bear_case` 非空（即使结论是 trade，也要写"哪里可能错了"） |
| 5 | `tests/specs/spec_serenity_verdict.py` | 追加 SV-S03（`TradingDecision.bear_case` 字段存在） |

#### 依赖顺序

无前置依赖，可最早实施（纯 dataclass + DB column 变化，低风险）。

---

## P3 批次（数据/基础设施，积累后实施）

### D1：xStock 可研究性数据质量管理

**描述**: 某些 xStock 标的信息极少（`serenity_evidence` 全为 speculative），但 score 仍可能被 confidence_score 撑到较高分。D1 需要在数据层面标记"信息黑箱"标的，防止进入 PI 队列。

**依赖**: S2 的 researchability_grade 机制先落地，D1 是对它的配置层延伸。

---

### D2：证据 URL 提取增强

**描述**: `_has_external_ref()` 目前检查 `url` 和 `source_ref` 是否以 http 开头。LLM 有时写如 `"source_ref": "Binance官方公告"` 不带 URL，导致 high_confidence 被错误降级。需要改善 LLM 提示词，要求 source_ref 为机器可验证的 URL 形式。

---

### U1：研究质量 UI 展示

**描述**: 在 SerenityLens.tsx 的 opportunity 卡片上显示：
- `verdict.recommendation`（颜色标签：long=绿, short=红, watch=黄, avoid=灰）
- `researchability_grade`（A/B/C 徽章）
- `verdict.bear_case`（可展开）

**依赖**: S4 + S2 完成后。

---

### U2：4 视角证据展示（研究报告风格）

**描述**: 在 SerenityLens.tsx 的 evidence 列表按 `agent_lens` 分组展示，类似 AI Berkshire 的研究报告布局。

**依赖**: S1 完成后。

---

## 实施顺序

```
Day 1 (2026-06-28+)
  P2: bear_case 字段  [低风险，先做收益]
  S4: Forced Verdict spec + 实现 + 测试  [P0 最高价值]

Day 2
  S2: Researchability Grade  [P0，与 S4 并行可行]
  S3: 来源可靠性天花板追加

Day 3-4
  P1: key_assumptions 结构化
  S1: 4 视角并行 [成本评估后决定并行/顺序]

Day 5+（积累数据后）
  D1, D2, U1, U2
```

---

## 测试策略

| 类型 | 位置 | 目标 |
|------|------|------|
| SDD L2a（结构） | `tests/specs/spec_serenity_verdict.py` 等 | 字段存在、文件引用有效 |
| SDD L2b（行为） | 同上 | 管道运行后产物包含预期结构 |
| 单元测试 | `tests/test_serenity_researchability.py` 等 | 确定性函数（evaluate、grade） |
| E2E 管道测试 | `tests/test_serenity_verdict.py` 等 | 完整 mock-LLM 管道运行验证 |

所有新 spec 文件必须被 `tests/test_spec_conformance.py` 硬编码导入（见 SDD spec 收集陷阱记忆）。

---

## 风险与注意事项

1. **SDD 收集陷阱**: 每个新 `spec_serenity_*.py` 必须加入 `test_spec_conformance.py` 硬编码导入列表
2. **LLM 格式遵从**: Forced Verdict 要求 LLM 产出结构化 JSON，需测试 prompt 是否稳定
3. **DB 迁移幂等**: `_ADDITIVE_COLUMNS` 模式已有先例，遵循即可
4. **trading_context.py 是决策链**: 改动前必须运行 `uv run pytest tests/specs/ -q`
5. **worktree 数据空**: 实现完成后必须走主仓 merge + restart + HTTP 实测（见 Worktree gateway 空 data 陷阱记忆）
