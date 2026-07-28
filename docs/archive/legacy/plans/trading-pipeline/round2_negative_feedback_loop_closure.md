# Round 2 — 负反馈闭环修复计划（高维自进化版）

> **✅ RECONCILED 2026-06-30 — 已合并 master**（见 [REMAINING_WORK_2026-06-30 §3](../../status/snapshots/REMAINING_WORK_2026-06-30.md)）：commit `dd559a7`「close the negative-feedback loop」已在 master（`git branch --no-merged` 已无 neg-feedback 分支）；`spec_insight_feedback_loop` 全绿。下方"待上线：FF merge"为历史状态。剩余仅"后续可选"项（reconciler regime_tag / 历史 case study 回溯）。

> 目标：让"用交易结果调整未来交易行为"**真正闭合**，且符合 owner 定调——**高维模式归因 + 可被后续证据推翻的 self-evolve + 全自动**，摒弃低维 symbol/direction/scanner 硬拦截。
> 状态：**✅ 已实施（分支 worktree-neg-feedback-loop-fix，2026-06-28）**。全量 1758 passed，本次改动零回归（4 个失败为 master 上 pre-existing：config_routes/retention/serenity_workbench，与本次无关）。spec_insight_feedback_loop 7 claims 全绿。**待上线：FF merge 回主仓 + gateway restart + aletheia doctor 验证（worktree data/ 为空，doctor 须在主仓跑）。**
>
> **已交付**：Path6 反思（reflection.py SyntaxError 修复 + import smoke test）｜A2 干净窗过滤（learning_window.clean_window_cutoff，三学习模块接入）｜B3 单边偏向安全阀 enforce（防正反馈陷阱）｜**B1+B2 Module J memo_effectiveness（可推翻进化：回溯评估 prior memo→accuracy 写回 tags→overturned 注入新 memo 的 prior_corrections→render 进 observer/PI prompt）**｜A1 regime 源修复（current_regime/_current_regime 读 market_regimes.state，让高维 regime_playbook 有真维度）。
> **未做（owner 定调摒弃）**：低维硬拦截（guard symbol/direction/scanner predicate、risk engine block_*）。
> **后续可选**：reconciler adopt 路径的 regime_tag（38 NULL 旁路源）；历史 medium case study 回溯升级（Path 2 燃料，本次未含）；契约细补（agent_contracts memo_effectiveness 行）。
> 依据：两轮代码审计 + DB 亲核 + 文献（`insight_feedback_loop.md` / `self_evolution_plan.md` / `confidence_calibration` 的 ECE / reflection arXiv:2510.08068）+ owner 2026-06-28 定调。
> 创建 2026-06-28（已据 owner 升维指示重写）。

---

## 0. 重大重定向（owner 升维后）

owner 明确：学习**不是**"哪个币/方向/信号该拦截"（低级思考、会误杀机会），而是用 Agent 在**更高维度拟合错误模式、追根究底理解亏损真因**，并能**用后续负反馈推翻原先判断、与时俱进 self-evolve**。全自动。学习只用**干净窗**数据。

→ 任务从"修我最初审计的 3 条低维通路"**重定向**为"修复 + 补齐**已经实现的高维进化系统**"。

**关键发现：高维系统已大部分实现且全自动运行**——`insight_synthesizer`（每日 02:30 产 `TradingMemo`：regime_playbook/timing/anti_patterns/symbol_affinity + LLM key_insight）、`confidence_calibration`（每 6h 产 ECE 校准报告）、`reasoning_feedback`（pattern 胜率），三者**全注入 PI prompt + ranker scoring，全 soft（不硬拦截，保留机会）**。但它学到的是**被污染、且不自我纠错**的结论。

---

## 1. 三类致命缺陷（亲核）

### 缺陷 ① 高维退化：regime 全 `unknown`
- `market_regimes` 表有**真实高质量 regime**（state=bear, confidence=0.778, short_bias=0.75, 含 vix/funding/atr，每 10min 更新）。
- 但 `positions.regime_tag`：干净窗 49 笔 = 38 NULL + 16 neutral + **0 bear** → 开仓时没把当时 regime 快照进 position。
- 后果：`regime_playbook`/`anti_patterns` 的 regime 维度全落 "unknown" 桶 → 设计的"regime×direction×timing 高维"退化成**只有 direction 的中维**。
- 待核实根因：`paper_broker.py:95` 已 `regime_tag=_current_regime()`，但多 broker（okx/binance/bitget）建仓可能漏写（类比 [[aletheia-broker-stop-drop-bug]]），或 `_current_regime()` 取值 bug，或早期 regime 真是 neutral。实现时定位。

### 缺陷 ② 被脏数据带偏：无干净窗过滤
- `insight_synthesizer._gather_statistics` 用滚动 30 天全量（sample 152，`confidence_calibration` 用 90 天），纳入 Round1 脏数据（short 17/23 方向集中、归因断裂）。
- 实测 memo：所有 edge="contra"、win_rate 30-40% → 学出"做什么都反指"，正是 owner 担心的被脏数据带偏。
- owner Q2：学习只用**干净窗（≥2026-06-25 15:22）**。

### 缺陷 ③ 不会自我纠错/推翻（owner 最在意）
- ❌ **Module J `memo_effectiveness.py` 完全不存在**：无任何"回溯评估 memo 准确率 vs 实际结果"的机制。
- memo 仅靠 72h 时间衰减 + latest-wins；**没有"某 memo 错了→标记/降权/推翻"**。自信错误的 memo 跑满 72h 误导一批交易、不留痕迹，下个 memo 学不到"这判断错了"。
- `MAX_DIRECTION_BIAS_STRENGTH=0.75` 声明了**但没 enforce**（防单边正反馈陷阱的安全阀是死的）。
- `confidence_calibration` **部分**闭合了 conviction 环（ECE→注入 PI→PI 自愿降 conviction），但只调 conviction 幅度、soft、不碰 memo 方向/权重。
- 整个高维闭环**零 spec 守卫**（Module J 缺失/regime 断裂都不会触发任何 spec 报警）。

### 已修 / 不做
- ✅ **Path 6 reflection**（单步"看上次结果"反馈）：commit 5be7826 的 SyntaxError 已修（删重复 kwarg）+ import smoke test 锁回归。保留——它有益且非低维硬拦截。
- 🚫 **低维硬拦截**（guard symbol/direction/scanner predicate、risk engine `_check_lesson_constraints` block_*）：按 owner 不做（会误杀机会）。`symbol_affinity` 的 cold 是 soft penalty（-8），暂留不主动拆（影响小、非硬拦截），后续可由高维 memo 取代。

---

## 2. 修复方案（复用现有架构，不过度工程）

### A. 修高维输入质量（让"高维"名副其实）
- **A1 regime 快照**：定位并修复开仓时 `position.regime_tag` 未写真 regime 的根因（多 broker 建仓统一写入 `current_regime()`，类比 stop/tp 共用 merge 的修法）。新交易起，memo 才有真 regime 维度。
- **A2 干净窗过滤**：`insight_synthesizer` / `confidence_calibration` / `reasoning_feedback` 的取数加 `learning_data_floor`（默认 2026-06-25T15:22Z，可配置常量），避开脏数据。样本变少但干净（干净窗 closed 49 笔）。

### B. 闭合"可推翻进化"（Module J，owner 最在意）
- **B1 `memo_effectiveness.py`**：新 memo 生成前，回溯评估上一个 memo 的**预测 vs 实际**（regime_playbook 各 rule 的预测 edge vs 实际 win_rate；anti_pattern 是否真亏；timing 窗内外 PnL）→ 算 `accuracy_score`，写回上一个 memo 的 tags。
- **B2 推翻反哺**：(a) 连续 N 次低准确率 → 降低该类 memo 的 ranker scoring 权重；(b) 在新 memo 生成 + PI prompt 注入时显式标注"**先前判断 X 已被后续证据推翻/下调**"，让 Agent 看到判断的修正轨迹（与时俱进）。
- **B3 enforce `MAX_DIRECTION_BIAS_STRENGTH`**：真正 clamp，防单边正反馈陷阱（Round1 short 73.9% 逼近阈值却没触发）。

### C. SDD 守卫（让闭环不再对 spec 透明）
- 新建 `tests/specs/spec_insight_feedback_loop.py`：claim 覆盖——memo 产出非空且字段完整；干净窗过滤生效；regime 维度非全 unknown（L2c 运行时探针）；Module J `memo_effectiveness` 存在且会降权错误 memo；bias cap 真 enforce。
- 加入 `test_spec_conformance.py` 硬编码 import（避开收集陷阱 [[aletheia-sdd-spec-collection-trap]]）。

---

## 3. 实现优先级 & 顺序
1. ✅ Path 6（已完）。
2. **A2 干净窗**（最快止血，小）→ memo 立即不再被脏数据带偏。
3. **A1 regime 快照**（让高维名副其实，中）。
4. **B3 bias cap enforce**（安全阀，小）。
5. **B1 + B2 Module J 可推翻进化**（owner 最在意，最大块）。
6. **C spec 守卫**（贯穿，每步 RED→GREEN）。

每步 spec-first：先 `SpecClaim(status="missing")`/xfail（RED）→ 实现 → GREEN。

## 4. 契约/文档同步（SDD checklist）
- `docs/design/contracts/agent_contracts.md`：新增 `memo_effectiveness` job 行（若加独立 job）；`reasoning_feedback` piggyback 现状补注。
- `docs/design/contracts/knowledge_contract.md`：正式声明 `trading_memo` / `calibration_report` / `reasoning_feedback` item_type。
- `docs/design/data/data_model.md`：若给 memo 加 accuracy 字段（走 knowledge_items.tags，不新建表，避免拆库 [[aletheia-app-db-size-retention]]）。
- `docs/status/roadmap/ROADMAP.md`：Self-Evolution Tier 2 标记 PLANNED→实际已实现，对齐文档-代码背离。
- `docs/design/architecture/SYSTEM_MAP.md`：补 insight_synthesizer→observer/ranker 数据流 + memo_effectiveness 节点。

## 5. 验证
每步针对性测试；新 `spec_insight_feedback_loop.py` + `test_architecture_fitness` + `test_spec_conformance` 全绿；后台全量；`aletheia doctor`；更新 MASTER_FIX_LEDGER / AI_HANDOFF。
