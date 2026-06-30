# Serenity 执行路径实现计划（Option A — PI-central / Honest Bridge）

> 2026-06-27 | 上游报告：`docs/research/SERENITY_PHILOSOPHY_AND_INTEGRATION_ANALYSIS_2026-06-27.md`
> Owner 拍板记录：memory `aletheia-serenity-integration-decisions`
> 状态：**✅ RECONCILED 2026-06-30 — 已实现**（见 [REMAINING_WORK_2026-06-30 §3](../status/REMAINING_WORK_2026-06-30.md)）。原"待 owner 审批，审批前不写代码"为历史；owner 已拍板 Option A 并落地：Phase 1–5 各有 spec 守且 conformance 全绿 —— `spec_serenity_evidence`(D2 生成≠评测 + tier 词表统一) / `spec_serenity_attribution`(Phase 2) / `spec_serenity_promote`(Phase 3) / `spec_serenity_holding`(Phase 4 trade_school) / `spec_serenity_live_readiness`(Phase 5 结构预埋)。个别长期持有退出行为细节如有残留，单列核实，不影响"已实现"判定。

---

## 0. 这份计划要解决什么（一句话）

Serenity 是一个**独立交易流派**（深研产业链供需的一手逻辑、找出被市场低估的环节、长期持有），但它生成的研究**今天没有任何人执行**——promote 产出死记录、研究只作为 Layer 3 顾问文本飘过 PI。本计划把 Serenity 研究**升级为 PI Agent 的一等公民输入**，让金字塔顶端的 PI 像人类分析师一样评估它、做风控、下最终命令；并给长期持有的仓位一套不同于短线的退出机制——**入场决定权与风控始终留在 PI / Risk Engine**（不绕过）。

Owner 的 5 项拍板已锁定（见报告 §8）：

| 决策 | 锁定结论 | 本计划对应 |
|---|---|---|
| D1 标的扩展 | 长期 roadmap，现在不做 | Phase 6（纯文档） |
| D2 证据质量 | **必修**：禁止自产自评，拆生成/评测 | Phase 1（前置必做） |
| D3 promote 死记录 / 架构 | **Option A**：交给顶端 PI 评估，PI 仍是唯一决策者，必须经 PI 做风控 | Phase 2 + Phase 3 |
| D4 paper vs live | paper-first 当前可接受，但提前预埋 live-capable 代码 | Phase 5（结构预埋，不开 live） |
| D5 promote 触发 | 做开关，**默认手动** promote | Phase 3（config + Settings 控件） |

---

## 1. 核心架构（Option A 落地形态）

```
research_worker (LLM 5-stage)            [Phase 1: 证据生成/评测分离]
        │  serenity_evidence / serenity_opportunities
        ▼
[评测器：独立 evaluator + 客观 tier 规则]  ← 不再由生成者自评
        │  assessed_tier（gating） + proposed_tier（仅记录）
        ▼
serenity_scoring (确定性 0-100, bucket)   [Phase 1: 统一 tier 词表]
        │
        ▼
promote（手动按钮 / 自动开关 default off）  [Phase 3]
        │  写 PiDecision(action='signal_pending', signal_source='serenity')
        │  + notify_pi_daemon(source='serenity_promote')
        ▼
PI Agent（pi_daemon → evaluate_trading_opportunity）
        │  Serenity 作为 Layer 3 「结构化一等输入」+ 可被 _attach 回填  [Phase 2]
        │  PI 像人类分析师评估证据/合理性 → 决定 action
        ▼
submit_decision_as_proposal → Risk Engine（不绕过，#13 stop 仍强制）
        │  proposal.trade_school='serenity'（更宽的 thesis-based stop）  [Phase 4]
        ▼
execute_paper_order（paper-first；live 路径结构预埋不启用）  [Phase 5]
        ▼
Position(trade_school='serenity')
        │  position_manager._decide_exit：禁 72h max_hold、禁 ATR 追踪、  [Phase 4]
        │  改用论点失效退出（thesis-invalidation）
```

**不变量（D3）**：`agent_contracts.md` 的「PI Agent = 唯一交易决策者」**不修改**。Serenity 不新建绕过 PI 的 `serenity_executor`（报告 Option B 已被 owner 否决）。这反而移除了原报告里最大的治理阻塞。

---

## 2. Phase 1 — D2 证据质量必修（前置，最高优先级）

### 2.1 已亲核的根因（自产自评闭环）

1. `research_worker.py:63-73`（`_EVIDENCE_SYSTEM`）：**同一个** LLM 调用既写 `title`/`summary`，又自填 `"tier"` 与 `"confidence"`（提示词原文 `"confidence" (string: same as tier)`）。生成者 = 评测者。
2. `research_worker.py:96-103`（`_OPPORTUNITIES_SYSTEM`）：LLM 又给每个 opportunity 自评 `evidence_tier` + `confidence_score`。
3. `research_worker.py:114`（`_ICT_METHODOLOGY_SYSTEM`）：无条件硬编码 `tier="verified"`。
4. `prediction_observer.py:413-429`：`source="prediction_market"`、`tier` 仅 medium/speculative、纯 `_CRYPTO_KEYWORDS` 匹配、`themes[0]` 兜底——把加密情绪洗成「产业链证据」（报告 §7.2）。
5. **词表裂缝（潜伏 bug）**：生产端发 `verified`/`credible`/`medium`，而 `serenity_scoring.py:180-187` 的 `tier_score_map` 只认 `disclosed`/`high_confidence`/`likely`/`speculative` → 未命中全部回退 `10`（speculative）。今天 tier 信号几乎是死的。

### 2.2 修复设计（生成者 ≠ 评测者 + 客观化）

**原则**：生成证据的 LLM 可以**建议**一个 tier，但**绝不能**是 gating 的那个 tier。gating tier 由独立评测器赋值，并受客观来源下限约束。

1. **统一 tier 词表**（消除 §2.1.5 裂缝）。定为单一有序枚举（建议）：
   `disclosed > high_confidence > likely > speculative`（与 scorer 既有 map 对齐，改生产端跟随它，避免动 scorer 的分值锚点）。
   - 改 `_EVIDENCE_SYSTEM` / `_OPPORTUNITIES_SYSTEM` / `_ICT_METHODOLOGY_SYSTEM` 提示词，把输出字段从自评 tier 改为 **`proposed_tier`**（仅记录、不 gating），并约束取值在新枚举内。

2. **生成/评测字段分离**（数据层）。`SerenityEvidence` 增列：
   - `proposed_tier`（生成者建议，可空）
   - `assessed_tier`（评测器裁定，**唯一 gating 值**）
   - `assessed_by`（`'rule'` / `'pi'` / `'unassessed'` provenance）
   迁移：`migrations.py` 加列，默认 `assessed_tier='speculative'`、`assessed_by='unassessed'`。`data_model.md` 同步。

3. **客观 tier 下限规则**（确定性评测器，新函数，建议放 `aletheia/trading/serenity_scoring.py` 或新 `serenity_evidence_eval.py`）：
   - `source ∈ {prediction_market, llm, ict_methodology}` 或无可核验 `source_ref` → `assessed_tier` 封顶 `speculative`（这些是模型自知识/情绪，不是一手产业证据）。
   - 仅当 `source_ref` 是可核验主源（公告/财报/披露 URL）→ 允许 `likely`。
   - `disclosed`/`high_confidence` 仅由**独立评测**（PI 或显式人工）赋值，生成者永远拿不到。
   - `score_serenity_opportunity` 改读 `assessed_tier`（不再读自评 `evidence_tier`）。

4. **PI 作为终极独立评测者**：Serenity opp 上交 PI 时（Phase 2），prompt 显式标注「以下 tier 为自动评测下限，请你作为独立分析师复核证据质量后再决策」。PI 的复核结论可回写 `assessed_tier`/`assessed_by='pi'`。

5. **prediction_observer 诚实化**：`prediction_market` 证据保留但**明确标为情绪 catalyst、封顶 speculative、不得作为 candidate_position 入场的唯一支撑**；保留 `_CRYPTO_KEYWORDS` 但 `themes[0]` 任意兜底改为「无明确匹配则不写」（去掉 `:407-408` 的兜底）。

### 2.3 SDD / 测试
- 新 spec：`tests/specs/spec_serenity_evidence.py` — claim「证据生成者不得赋 gating tier」「prediction_market/llm 源封顶 speculative」「scorer 只读 assessed_tier」。加入 `test_spec_conformance` 硬编码导入（见 memory `aletheia-sdd-spec-collection-trap`）。
- 单测：tier 词表统一后 `test_serenity_scoring.py` 回归；新增评测器规则单测（真实数据结果断言，不 mock-call-verify）。
- `data_model.md`（serenity_evidence 加列）同步。

---

## 3. Phase 2 — Serenity 升级为 PI 一等输入 + 修复 attribution 断链

### 3.1 现状（已亲核）
- `trading_context.py:523-538`：Serenity 仅取 `symbol/direction/thesis[:200]/confidence_score/evidence_tier` 进 Layer 3。
- `trading_decision.py:706-709`：渲染成 `## Research-sourced Opportunities` 顾问文本，**不带 opportunity_id**。
- `trading_decision.py:837-863`（`_attach_opportunity_context`）：只对 `layer1.opportunities`（market_observer 表）回填 `opportunity_id`；**Serenity 无等价回填** → Serenity 来源的 PI 决策 `opportunity_id=None`，attribution 链断（呼应 memory `aletheia-pi-decisions-opp-id-still-null`）。

### 3.2 修复设计
1. **结构化上下文**：`_build_layer3_wisdom` 的 serenity 块补充 `id`、`assessed_tier`、`score`、`bucket`、`evidence_count`、关键证据 `source_ref` 摘要——让 PI 能像分析师一样判断证据强度，而非只看一行文字。
2. **attribution 回填**：扩展 `_attach_opportunity_context`（或加并行 `_attach_serenity_context`），当 PI 决策的 symbol 命中某 Serenity opp 时回填来源。
   - **子决策（推荐默认）**：不要把 `serenity_opportunities.id` 直接塞进现有 `opportunity_id`（会污染对 `opportunities` 表的 attribution join）。改为加 `signal_source ∈ {'market_observer','serenity'}` 判别字段，`opportunity_id` 按 source 解释；下游 attribution 消费者（trade_case_study / reasoning_feedback）改为 source-aware。需在实现时核对所有 join 点。
3. **诚实标注**：prompt 里 Serenity 块标明「研究型长持流派、tier 为自动下限、需独立复核」。

### 3.3 SDD / 测试
- 新 spec claim：「Serenity 来源决策必须带可追溯来源（signal_source + id），不得 opportunity_id=None 静默」。
- `agent_contracts.md`：PI 输入层 Layer 3 描述补 Serenity 一等输入（**不改** PI 唯一决策者条款）。
- 单测：构造 Serenity opp → 断言进入 prompt 的结构化字段齐全、回填后来源可追。

---

## 4. Phase 3 — promote → PI 评估队列（杀死记录）+ D5 开关

### 4.1 现状（已亲核）
`data.py:1656-1695`：promote 建 `StrategyRecord(state="draft")`。`strategy_lifecycle.py:98` 的 `is_active_paper_strategy` 只认 `paper_candidate/paper_running`；ranker（`paper_trader_ranker.py:179-192`）与 PI Layer 2（`trading_context.py:381-382`）都过滤掉 draft → **死记录，无人执行**。

### 4.2 修复设计
1. **重写 promote 端点**：不再建 `StrategyRecord(state='draft')`。改为：
   - 把对应 `serenity_opportunities.action_intent` 置 `'pi_evaluate'`（该字段已存在，`migrations.py:218` 默认 `'watch'`，今天无人读 → 正好作路由钩子）。
   - 写一条 `PiDecision(action='signal_pending', signal_source='serenity', serenity ref)` 进 `pi_decisions`（复用 paper_trader 既有的 signal_pending 接缝，`trading_context.py:453-469` 30 分钟窗读取）。
   - `notify_pi_daemon(source='serenity_promote')` 唤醒 PI。
   - 返回值语义改为「已提交 PI 评估队列」，不再返回 `state='draft'` 假动作。
2. **D5 开关**（config）：
   - `serenity.auto_promote: bool = False`（默认手动）。
   - `False`：仅前端手动 promote 按钮触发上述入队。
   - `True`：`research_worker` 生成阶段后，对 `bucket == 'candidate_position'`（score≥72 且 0 fail gate）的 opp 自动入队。**不新增 scheduler job**（搭在既有 research run 尾部），避免动 `agent_contracts.md` job 表。
3. **可选阈值**：`serenity.min_score_for_pi: int = 72`（仅 auto 模式用）。

### 4.3 Config ↔ UI（强制）
新增 config 字段必须同步前端 Settings 控件（CLAUDE.md Config↔UI rule）：
- `serenity.auto_promote`（toggle，默认关）
- `serenity.min_score_for_pi`（数值，auto 模式）
- 见 Phase 4 还会加 `serenity.stop_loss_pct` / `serenity.max_hold_days`
全部加到 `frontend/src/pages/Settings.tsx`，并补 `schema.py`。

### 4.4 SDD / 测试
- `api_reference.md`：promote 端点语义变更同步。
- 新 spec claim：「promote 不再产 state='draft' 死记录；产出 signal_pending 入队」。
- 单测：调 promote → 断言无 draft StrategyRecord、有 signal_pending PiDecision、daemon 被通知（验证数据结果，不只 assert_called）。

---

## 5. Phase 4 — `trade_school` 判别器 + 长持退出机制

### 5.1 现状（已亲核）
- 全仓库**无** `trade_school` 字段（Explore 全仓搜索仅命中本系列计划文档）。所有仓位在 `position_manager._decide_exit`（`position_manager.py:214-234`）与 `_maybe_update_trailing_stop`（`:143-211`）被**一视同仁**。
- `_decide_exit` 用 `cfg.trading.max_position_hold_hours`（默认 72h，非硬编码）→ 到点 `max_hold` 平仓。
- ATR 追踪止损在 `_decide_exit` 前先跑（`evaluate_open_positions:334`）。
- Risk Engine #13 强制数值 stop_price（`engine.py:216-222`，change-controlled 信任边界）。

### 5.2 修复设计
1. **加判别字段** `trade_school`（`'short_term'` 默认 / `'serenity'`）到 `TradeProposal` + `Position` 模型 + 迁移。默认值保证既有短线行为不变（最小改动）。`data_model.md` 同步。
2. **入场仍走 PI + Risk Engine，不绕过**：
   - **不修改 Risk Engine #13**（信任边界 change-controlled）。Serenity 仍提供数值 stop，但用更宽的 thesis-based 止损：新增 `serenity.stop_loss_pct`（远大于短线 `default_stop_loss_pct`），在 `submit_decision_as_proposal`（`trading_decision.py:237-243`）按 trade_school 选择止损 pct。
3. **退出机制按 trade_school 分流**（`position_manager._decide_exit` / `_maybe_update_trailing_stop`）：
   - `serenity`：禁用 72h `max_hold`（改用 `serenity.max_hold_days`，默认很大或 0=不限）；禁用 ATR 追踪止损（`_maybe_update_trailing_stop` 对 serenity 直接 return）。
   - **论点失效退出（thesis-invalidation）**：MVP 最小实现——当 Serenity 仓位关联的 opp `status != active`（被研究循环重估下线）或显式 `thesis_invalidated` 标记时平仓；自动失效检测（research 重评打分跌破地板）列 roadmap。短线仓位完全不受影响。
4. **kill_switch / daily-loss**：Serenity 仓位仍受全局熔断保护（不豁免），符合「必须经 PI 做风控」。

### 5.3 信任边界与 SDD
- `position_manager`（`aletheia/portfolio/`）属决策链敏感但**不在** change-controlled 信任边界清单（`risk/`、`execution/` 等）；仍走 SDD：**先写 RED `spec_*.py`**。
- **不触碰** `aletheia/risk/`、`aletheia/execution/`、`engine.py` #13（保持强制 stop，只调 pct 来源）。
- 新 spec：`spec_serenity_holding.py` — claim「trade_school='serenity' 禁 ATR 追踪 / 禁 72h max_hold / 论点失效可退出 / 短线行为不回归」。加入 `test_spec_conformance` 硬编码导入。
- 改 `DEFAULT_PROMOTION_GATES`？**不改**（Serenity 不走 strategy 晋级门，走 PI 直评）。

---

## 6. Phase 5 — D4 live 预埋（结构，不启用）

- `trade_school` 字段贯通到 live broker dispatch（`gateway.submit_proposal` → broker）作为透传结构位，当前 paper 路径无行为差异，但保证未来上 live 时无需返工模型层。
- **不启用 live**：`execution_mode` 仍限 `_PAPER_MODES`；不动 live unlock / `server/routes/live.py`。符合 CLAUDE.md「Never enable live trading without explicit user confirmation」。
- 仅做「结构 live-capable」：模型字段、proposal 透传、退出机制按 school 分流——这些在 paper 下已可端到端验证。

---

## 7. Phase 6 — D1 标的扩展（纯 roadmap，本期不实现）

记录到 roadmap，不写代码：
- Bitget：不限 xStock ON 系列；调研 RWA（owner 见到 rSpaceX）等真实世界资产可用标的列表。
- OKX：调研可接触的产业链相关标的。
- 目标：找到能真正映射「产业链某低估环节」的标的（当前 10 个 xStock 是 S&P 大盘股、`execution_eligible=False`，非真实供应链——报告 §5 / spec OBS-S04）。
- 产出物：后续单开 `docs/plans/serenity_instrument_roadmap.md`。

---

## 8. 依赖与执行顺序

```
Phase 1 (D2 证据质量) ──┐  必须最先：脏证据进 PI = 喂幻觉
                         ├─→ Phase 2 (一等输入 + attribution)
                         │        │
                         │        ▼
                         └─→ Phase 3 (promote→队列 + D5 开关)
                                  │
                                  ▼
                         Phase 4 (trade_school + 长持退出)  ← SDD 最敏感
                                  │
                                  ▼
                         Phase 5 (live 预埋, 结构)
Phase 6 (标的 roadmap) 独立, 任意时点, 纯文档
```

建议**分 PR 落地**，每个 Phase 一组（Phase 1 单独先行并合并验证，因为它是「喂 PI 之前的质量闸」）。

---

## 9. 跨 Phase 的 SDD / 契约同步清单（强制）

| 动作 | 需同步 |
|---|---|
| serenity_evidence 加列（assessed_tier 等） | `data_model.md` + migration |
| positions/proposals 加 `trade_school` | `data_model.md` + migration |
| 新 config 字段（auto_promote / stop_loss_pct / max_hold_days / min_score_for_pi） | `schema.py` + 前端 Settings 控件（Config↔UI rule） |
| promote 端点语义变更 | `api_reference.md` |
| PI Layer 3 加 Serenity 一等输入 | `agent_contracts.md`（**不改** PI 唯一决策者条款） |
| 新 spec 文件（evidence / holding / routing） | 加进 `test_spec_conformance` 硬编码导入 |
| 改后验证 | `aletheia doctor` + `uv run pytest tests/specs/ -q` + 相关单测 |

**不触碰**（信任边界 / owner 规则）：`aletheia/risk/`、`aletheia/execution/`、`engine.py` #13、`server/routes/live.py`、live unlock、`agent_contracts.md` 的 PI 唯一决策者条款、`DEFAULT_PROMOTION_GATES`。

---

## 10. 需要 owner 拍板的开放子决策（实现前）

1. **attribution 形态**（§3.2.2）：推荐加 `signal_source` 判别字段而非把 serenity id 塞进现有 `opportunity_id`。是否同意默认走「signal_source + source-aware join」？
2. **tier 词表**（§2.2.1）：推荐统一到 scorer 既有的 `disclosed/high_confidence/likely/speculative`（改生产端跟随）。是否同意？
3. **论点失效退出**（§5.2.3）：MVP 仅做「关联 opp 下线 / 显式标记即平仓」，自动失效检测列 roadmap。是否接受这个 MVP 边界？
4. **PR 切分**：是否同意 Phase 1 单独先行合并（质量闸前置），其余按依赖链分批？

---

## 11. 验收标准（全 Phase 合并后）

- D2：证据生成者不再赋 gating tier；prediction_market/llm 源 `assessed_tier` 封顶 speculative；scorer 只读 `assessed_tier`；tier 词表统一无回退裂缝。spec GREEN。
- D3：promote 不再产 `state='draft'` 死记录；Serenity 经 signal_pending 入队、PI 实际评估、决策带可追溯来源（无 opportunity_id=None 静默）。spec GREEN。
- Serenity 来源仓位带 `trade_school='serenity'`；不被 72h max_hold / ATR 追踪误平；短线仓位行为零回归。spec GREEN。
- D4：paper 路径端到端跑通；live 字段结构就位但未启用。
- D5：默认手动 promote；auto 开关在 Settings 可见可控。
- `aletheia doctor` 干净；`tests/specs/` 全绿；全量回归无新失败。
