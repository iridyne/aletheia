# 执行路径语义诚实 — LLM 究竟在决策回路的哪个位置

## 2026-06-30 Phase 2A status amendment — DSL implemented

- The "悬空的死字符串策略层" conclusion (2026-06-13) is **no longer accurate** for strategy_designer.
- `strategy_designer.py` now has a complete `entry_conditions` → `_compile_ruleset` path (lines 248-289, 391-423): LLM is prompted for structured JSON conditions, compiled to a `RuleSet` DSL object, stored in `parameters.ruleset`, and executed at runtime by `scan_active_rulesets` (trading/ruleset_scanner.py).
- `paper_trader.py` Path A (lines 91-113) consumes RuleSet signals from `scan_active_rulesets` directly; Path B (opportunity-driven) is the fallback for strategies without a compiled RuleSet.
- The updated "one-sentence conclusion" would be: **LLM decides direction AND entry/exit conditions via structured RuleSet (when compilation succeeds); deterministic risk/execution pipeline is unchanged.**
- What remains unresolved: promotion gates do not verify "RuleSet was actually evaluated before promotion" vs. pure direction-bias performance. The `entry_rule`/`exit_rule` text fields (:68-69) still exist as advisory fallback strings. Backtest integration is not wired.

## 2026-06-15 P0/P1 status amendment

- The 2026-06-13 analysis below is a historical snapshot; quote it together with this amendment.
- Current code now has an audit JSONL _prev_hash chain, plus the 2026-06-15 P0 hardening for serialized concurrent writes, verification across rotated files, and preservation of old rotations.
- The current runtime/DB snapshot still shows only one paper_running strategy with has_ruleset=False and backtest_runs=0, so do not claim the current paper/live decision loop is fully driven by executable DSL or backtests.
- This P0/P1 change only hardens trust state: audit, agent run lifecycle, operator shell single-instance behavior, docs, and version facts. It does not change risk, execution, live unlock, trading direction, or order semantics.


> 状态：一次性事实记录（2026-06-13）。机器无关、长期有效的*意图/事实*，非运行态事实，**非施工授权**。
> 性质：本文件回答一个被反复误述的问题——"Aletheia 的 LLM 在不在交易执行路径上？"
> 通过通读当前代码（非文档叙述）给出**带 file:line 证据**的精确答案，并与原始设计 v1、
> Prism 反面教材对照。
>
> **结论先行**：Aletheia 的真相**既不是**原始设计 v1 宣称的"LLM 不在执行路径 + 确定性 DSL"，
> **也不是** Prism 式的"假 LLM 真规则脚手架"，而是**第三种状态**：
> **真 LLM 决策方向 + 真确定性管控下单 + 悬空的死字符串策略层**。

最后核实：2026-06-13 · 核实者：Aletheia Dev（通读 paper_trader / market_observer / strategy_designer / strategy_lifecycle / retrieval / jsonl 全文）

---

## 0. 一句话结论

> **最关键的"做多还是做空"决策来自 LLM 的市场解读**（market_observer 读快照 → 输出 direction_bias），
> **下单的数量/止损/风控是纯确定性的**（固定 notional、固定止损百分比、Risk Engine 硬限），
> **而 strategy_designer 产出的 entry_rule/exit_rule 是从不被解析执行的自由文本**——策略层与交易决策完全脱节。
>
> 所以"LLM 不在执行路径"这句话是**半真半假的危险简化**：下单*机制*确实没有 LLM，
> 但下单*方向*完全是 LLM 决定的，且没有任何确定性规则或回测在 LLM 与真实下单之间做验证。

---

## 1. 三类拆开看：确定性的 / LLM 的 / 悬空的

把从"观察市场"到"下单"的完整链路拆成三类，每类附当前代码证据（可 grep 复核）：

### 1.1 ✅ 确定性的部分（符合原始设计，做得对）

| 环节 | 机制 | 证据 |
|---|---|---|
| 下单数量 | 固定 notional($25) / 当前价 | `aletheia/agents/paper_trader.py:124` `quantity = round(_PAPER_NOTIONAL_USDT / price, 6)` |
| 止损价 | 入场价 × 固定百分比（config 驱动） | `paper_trader.py:126-130`，`stop_pct = cfg.trading.default_stop_loss_pct` |
| 风控 | Risk Engine 硬限（kill_switch / 日损 / 仓位上限 / 杠杆…） | 所有 proposal 过 `aletheia/risk/engine.py`；`paper_trader.py:154-165` 经 `create_paper_proposal` 提交 |
| 下单通道 | 仅 paper 包装器，live 路径在此不可达 | `paper_trader.py` docstring + `execution/paper_tools.py` |
| 置信度门 | `_CONFIDENCE_RANK` 阈值比较 | `paper_trader.py:39,107-108` `conf_ok = _CONFIDENCE_RANK.get(confidence) >= ...` |

→ **这一层是真护栏**。LLM 在 paper_trader 里只生成一个 rationale **字符串**（`paper_trader.py:132`），
不参与任何数值/买卖决策。

### 1.2 ⚠️ LLM 的部分（与"LLM 不在执行路径"矛盾的核心）

| 环节 | 机制 | 证据 |
|---|---|---|
| **交易方向 direction_bias** | LLM 读市场快照 → 输出 long/short/neutral | `aletheia/agents/market_observer.py:125-152` `llm_structured(...)` → `read["direction_bias"]` |
| **置信度 confidence** | LLM 输出 high/medium/speculative + 0-100 分 | `market_observer.py:36-46`（`MarketRead` schema），`:143-152` 解析 |
| 方向被直接消费 | paper_trader 直接读 opportunity 的 direction_bias 决定做多/做空 | `paper_trader.py:104-108` `bias = opp["direction_bias"]; direction = "long" if bias in (...) else "short" ...` |

→ **这是被掩盖的真相**：`paper_trader.py:104` 读的 `direction_bias` 一路回溯，
源头就是 `market_observer.py:143` 的 LLM 输出。**"做多还是做空"这个最关键的方向决策完全来自 LLM 的市场解读，
中间没有任何确定性规则或回测来验证 LLM 的方向判断是否成立。** 下单*机制*无 LLM，但下单*方向*=LLM。

### 1.3 🟡 悬空的部分（策略层与交易决策脱节）

| 现象 | 证据 |
|---|---|
| strategy_designer 让 LLM 产出 entry_rule/exit_rule **自由文本** | `aletheia/agents/strategy_designer.py:80-104`（LLM 产出），`:102-103`（截断 500 字符） |
| entry/exit rule 存进 `parameters` JSON 字符串 blob | `strategy_designer.py:107-113` + `storage/models.py:163` `parameters: str = "{}"` |
| **全仓库没有任何 DSL parser / compiler / rule evaluator** | grep `dsl`/`parse_strategy`/`compile`/`rule_engine` 全仓 0 命中 |
| paper_trader **不读** strategy 的 entry_rule，只把交易"绑定"到 strategy_id | `paper_trader.py:136-149` 只解析 `strategy_id` 做关联，交易决策完全来自 opportunity，**从不读 `parameters` 里的 rule** |

→ **strategy 的 entry_rule/exit_rule 是悬空的死字符串**：被 LLM 生成、被截断、被存进 JSON、
然后**永远不被任何代码解析或执行**。strategy 在交易里的唯一作用是提供一个 `strategy_id` 让 paper trade
挂靠（以便晋级门统计），它声称的"入场/出场规则"对实际交易**零影响**。

---

## 2. 与原始设计 v1 的差距对照

原始设计文档（`C:\Users\ollama\Downloads\Aletheia_Implementation_Plan_v1.md`，用户提供的蓝图）的承诺：

| 原始设计 v1 承诺 | 当前真相 | 差距性质 |
|---|---|---|
| §5.1 "No LLM in trade execution path" | 下单机制无 LLM ✅，但**下单方向=LLM** ⚠️ | **半兑现**：机制确定性，方向非确定性 |
| §3.5 "All strategies must compile into deterministic format"（DSL） | entry/exit rule 是死字符串，**无 DSL、无 compile** ❌ | **未兑现**：策略层悬空 |
| §3.6 五层验证（Backtest / Walk-forward / Slippage / PBO / Paper） | **仅 Paper 一层**（见 §3） | **1/5 兑现** |
| §3.8 "LLM has NO direct execution permission" | ✅ 真兑现（LLM 不直接调下单 API） | 兑现 |
| §5.3 状态版本化 + 完整重放 | 策略有 lifecycle 状态机 ✅；audit append-only ✅ 但无 hash 链（见 §4） | **部分兑现** |

**关键**：原始设计的"确定性优先 + DSL + 五层验证"组合，本意是让 LLM 只产出**假设**，
再由确定性引擎**验证后**才交易。当前实现跳过了"DSL + 验证"，让 LLM 的方向判断**直接**驱动下单——
这正是原始设计 §9 列为头号失败模式的 **"LLM hallucinated strategies entering execution"** 的温和版本：
LLM 幻觉的不是"策略代码"，而是"市场方向"，且没有确定性 gate 拦它。

---

## 3. 验证层的真实状态：只有 paper 一层

原始设计 §3.6 要求 5 层验证。当前真相：

- ✅ **Paper gate 真实且严格**：`aletheia/trading/strategy_lifecycle.py:184-273` 的 promotion gates——
  14 天最短运行 / 30 笔最少交易 / 正期望 / 最大回撤 ≤5% / 最大连亏 ≤5 / 关键风险违规=0 / 需 post-trade review。
  这是**真护栏**，要肯定。
- ❌ **无 Backtest**：策略必须真实跑 14 天 paper 才能积累统计，**无法用历史数据提前回测**。
- ❌ **无 Walk-forward 验证**。
- ❌ **无 Slippage 模拟**（注：用 Bitget demo 真实模拟盘部分缓解了撮合滑点失真——见 demo cutover 决策）。
- ❌ **无 PBO / 过拟合检测**。

→ 后果：验证周期被迫拉到"真实 14 天 paper"这一条慢路径，且 LLM 方向判断在进入 paper 前
**没有任何前置确定性筛查**。回测层的缺失也意味着无法快速淘汰坏方向。

---

## 4. 与 Prism 教训的对照：我们是"反向 Prism"

`docs/references/external-systems/openalice_prism_review.md` §三记录了 Prism 的反面教训：
**Prism 搭了一整套漂亮的 LLM 多 agent 脚手架，后台实跑纯规则算术** → 死代码 + 语义不诚实。

Aletheia 是**镜像的另一半**：

| | Prism | Aletheia |
|---|---|---|
| LLM 脚手架 | 有（COTEngine/Coordinator/3 analyst） | 有（market_observer/strategy_designer 等） |
| LLM 真在决策回路？ | ❌ 死代码，后台跑规则 | ✅ **真在**（direction_bias 驱动交易） |
| 死代码在哪？ | LLM agent 整套不接通 | **strategy 的 entry_rule/exit_rule 不接通** |
| 语义不诚实点 | "假 LLM 真规则" | **"真 LLM 决策方向 + 悬空策略层"**——声称"策略驱动交易"，实际策略 rule 零影响 |

→ Prism 的病是"LLM 是摆设"；Aletheia 的病是"strategy 的规则是摆设"。
两者都违反 **"语义诚实 > 能力"** 的优先级——都让一个声称做某事的组件实际什么都不做。
`openalice_prism_review.md` 的教训"要升级策略就让 LLM 真进决策回路（哪怕一个节点）"，
对 Aletheia 的对应版本是：**要么让 strategy 的 rule 真正驱动交易，要么诚实标注它是 advisory 而非 executed。**

---

## 5. 审计链现状（补充事实）

- ✅ append-only：`aletheia/files/jsonl.py:25-42` 只 append、rotate 时把旧文件改名为 `.1` 不删内容。
- ❌ **无 hash 链**：每条 audit 记录独立（`write_audit` `jsonl.py:60-69`），**不引用前一条的 hash**，
  无法做防篡改的链式验证。
- → 这正是 `openalice_prism_review.md` **蓝图 B（commit-hash 审计链）** 要补的（对照 OpenAlice `TradingGit`），
  目前待授权。补上后才算名实相符的 "Trading-as-Git"。

---

## 6. 经验闭环现状（补充事实，更正旧表述）

- ✅ **已闭合，但仅靠 prompt 注入**：`aletheia/knowledge/retrieval.py:177-228`（FTS5 + BM25×recency×importance，
  quality-gated 仅 high_confidence + active）→ `market_observer.py:251-263` 与 `strategy_designer.py:60-70`
  把 memory block 注入 LLM prompt。
- ⚠️ **只影响 LLM 的"读"，不改任何确定性参数**：学到的 lesson 进 prompt 影响下一次 LLM 市场解读，
  但**不调 scanner 权重 / 不调 TTL / 不改 risk 限额**——与原始设计 §3.9 的"scanner weight updates / TTL tuning"
  这类**参数化反馈**有明确差距。当前是 advisory 文本注入，不是闭环控制。
- 📌 **更正**：`docs/references/external-systems/north_star.md` §6（2026-06-08 表述）称 "market_observer：LLM 只看 latest_snapshot，
  不读任何历史/教训"——**已过时**。现在 market_observer 确实注入 memory block。该文件已同步更正。

---

## 7. 三条诚实化路径（仅记录为方向，**非本轮实施、非施工授权**）

面对 §1.3 的"悬空策略层"和 §1.2 的"LLM 隐性在方向回路"，有三条可选方向，按改动大小排序：

### A. 诚实化命名（最小，纯语义诚实）
- 把 strategy 的 `entry_rule`/`exit_rule` 在字段注释 / UI / 文档里**明确标注为 "advisory rationale, NOT executed"**，
  消除"策略规则驱动交易"的误导。
- 同步在 market_observer/paper_trader 的 docstring 里诚实说明"交易方向来自 LLM market read"。
- 收益：零行为改动，立即消除语义不诚实。代价：承认当前策略层是描述性的。

### B. 补确定性验证层（中等，对应用户曾考虑的选项「3」）
- 在 LLM 产出 direction_bias 之后、paper_trader 下单之前，加一层**确定性 gate**——
  用规则（如指标一致性校验）或轻量回测验证 LLM 的方向判断，不通过则不交易。
- 收益：让 LLM 方向必须过确定性检验才能下单，兑现原始设计"验证后才交易"的精神。
- 代价：触及交易决策路径，需显式授权 + explore→plan + 回归测试；需要先有回测/校验原语。

### C. 真兑现 DSL（最大，远期）
- 让 entry_rule 结构化（受限词表 JSON）+ 被 paper_trader 真正解析执行，
  使 strategy 的规则真正驱动交易方向，而非依赖 opportunity 的 LLM direction_bias。
- 收益：名实相符的"确定性 DSL 策略"，LLM 退回到"产出假设"的本位。
- 代价：大改，触及策略生成 + 交易决策两条链；属推迟队列（北极星 Phase 2A 在册）。

> **优先级提示**：A 是纯文档/语义、零风险，可随时做；B/C 触及 change-controlled 的交易决策路径，
> 必须遵守 AGENTS.md 红线——先稳跑、再分项显式授权。本文件**不构成对 B/C 的施工授权**。

---

## 8. 治理红线（本文件专项）

- 🟢 本文件保存长期执行诚实边界；其中 dated file:line 证据须按当前代码复核。
- 🔴 不因本文件揭示的差距就擅自改交易决策路径（§7 的 B/C 是 change-controlled，需显式授权）。
- 🟡 引用本文件结论时，务必保留"确定性 / LLM / 悬空"三类的区分——
  **不要**再简化成"LLM 不在执行路径"（半假）或"LLM 直接交易"（也不准确）。
- 区分"声称 vs 已证实"：本文 file:line 断言来自 2026-06-13 的代码快照，
  不是当前运行事实；当前偏差通过 GitHub Issues 跟踪。

---

## 9. 相关文档

- `docs/references/external-systems/north_star.md` — 方向地图（§6 经验闭环主题已更正）
- `docs/references/external-systems/openalice_prism_review.md` — OpenAlice/Prism 对照（蓝图 B = 审计链，与本文 §5 呼应）
- `docs/references/papers-and-benchmarks/agent_framework_research_2025_2026.md` — 外部综述验证镜（"确定性优先 + LLM 影响最小"佐证）
- `docs/archive/legacy/issues/domain/BACKEND_ISSUES.md` — 冻结的历史问题证据；当前偏差只在 GitHub Issues 跟踪
- `C:\Users\ollama\Downloads\Aletheia_Implementation_Plan_v1.md` — 原始设计蓝图（仓库外，§2 对照来源）
