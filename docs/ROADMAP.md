# Aletheia Trading OS — Phased Development Roadmap

> Last updated: 2026-06-08
> Sources: PIPELINE_STATUS.md + deep research (102 agents, 20 sources) + WORLD_MAP.md (full architecture vision)
> Nature: **Guiding document, not automatic authorization** — each phase requires explicit confirmation before starting

---

## Design Philosophy

### Verified Industry Consensus

1. **Experience loop tiers**: No learning -> Memory accumulation -> Walk-forward validated edits -> End-to-end RL.
   Skipping tiers is the #1 failure mode. We are at tier 1->2 transition.

2. **Structured signal output**: Strategies must be Pydantic schema / signal+confidence pairs, not free-text.

3. **Agent coordination patterns**:
   - Fan-out / Fan-in (ai-hedge-fund: parallel analysis -> Risk Manager -> Portfolio Manager gate)
   - Adversarial debate (TradingAgents: bull/bear + max_debate_rounds)
   - Chain-based + hard risk override (TradingGroup: chain flow + dynamic risk module hard-stop)

4. **Walk-forward validation (SHARP)**: Any "learned improvement" must prove itself on held-out data before adoption.

5. **Honest positioning**: This is a research scaffold, not a money printer.

### Red Lines (never do)

- Agent generates executable Python strategy code
- Un-gated automatic strategy modification
- End-to-end RL (insufficient data, adds opacity)
- Remove human hard control (Risk Engine is always final gate)
- Introduce heavy frameworks (LangGraph etc.) — use lightweight state machines in orchestrator
- Expand beyond BTC/ETH/SOL in MVP trading scope

---

## Phase Overview

```
Phase 0   Stabilize & Observe       — Fill infrastructure gaps, prove 72h+ stability
Phase 1   Learning Quality           — Prove memory/review output is signal, not noise
Phase 2A  Structured Signals         — Strategy becomes executable rules, not text
Phase 2B  Agent Coordination         — Chain-based workflow (observer -> critic -> trader)
Phase 3A  Experience Closes Loop     — Memory actively influences decisions
Phase 3B  Walk-Forward Evolution     — Self-revision with statistical validation gate
Phase 4   Adversarial Hardening      — Debate, retirement automation, strategy ranking
Phase 5   Intelligence Expansion     — News scanner, synthesizer, multi-domain sensing
Phase 6   Live Readiness             — Capital gates, reconciliation, human confirmation
Phase 7+  Advanced (requires separate authorization per item)
```

Each phase has an **observation gate** — hard criteria that must pass before proceeding.
Phases are **not time-locked**. Move as fast or slow as reality permits.

---

## Cross-Cutting Track: Operator Layer (顶层 LLM 操作员)

> **正交于 Phase 0–7。** Phase 轴衡量*交易智能成熟度*;本 track 衡量*操作/控制面*
> ——用户能否从 Command Center 问 "现在在干什么 / 为什么失败 / 帮我修这个低风险问题"。
> 设计全文见 [OPERATOR_LAYER_DESIGN.md](OPERATOR_LAYER_DESIGN.md)。
> **该文的 `Stage 1–6` 是本 track 内部编号,与 Phase 0–7 是两套独立编号,不可互映。**

本 track **拆两层**(见 [AI_HANDOFF.md](AI_HANDOFF.md) 2026-06-09 判断):

| 层 | 内容 | 状态 | 前置 |
|---|---|---|---|
| **A — 确定性自愈 supervisor** | 无 LLM。code-drift/stale→重启、端口清理、限频持久化。这是稳定性根基,不是操作员。 | **DONE** (`8a6e008` + `abd7773`) | — |
| **B — LLM Apex Agent Shell** | 本文档主体。顶层 LLM 操作员:读全局 context、答疑、解释 agent run/proposal、暂停/恢复 agent、写 research note、开诊断 todo、每个动作审计。 | **NOT STARTED** | **Phase 0 观察门全绿** |

**为什么 B 排在 Phase 0 之后,不是现在:** B 是 8 个新模块(AppContextProvider /
AgentShellRuntime / CommandRegistry / PermissionPolicy / SessionStore / CommandAudit /
Command Center API / Dashboard 集成)的能力扩张。Phase 0 战略冻结律(见
[CURRENT_TASK.md](CURRENT_TASK.md))明确:门全绿前只做稳定性 + doctor 类减人工确定性工具。
**B 是"盖楼",72h 稳跑证明是"地基"——地基不稳不盖楼。**

**B 自身的安全边界(落地时以 [AGENTS.md](../AGENTS.md) 红线为准):** Agent Shell 绝不直接
开 live / 改凭证 / 改硬风控 / 绕过 Risk Engine / 下 live 单 / 改执行网关 / 硬删知识库。
高风险动作只能产 proposal,等人在 Dashboard/CLI 显式确认。Operator Stage 1(只读监督)
是 B 的第一个可交付切片(见设计文档 §16 / §23)。

**启动 B 需显式授权**——和所有 Phase 1+ 工作一样,不在 Phase 0 稳跑证明前启动。

---

## Phase 0 — Stabilize & Observe

**Goal**: All 15 scheduler jobs run autonomously. System proves basic stability.

### Tasks

| ID | Task | Status |
|---|---|---|
| 0.1 | `post_trade_reviewer` scheduled (4h) | DONE |
| 0.2 | `risk_critic` scheduled (6h) | DONE |
| 0.3 | Test baseline green (488 passed) | DONE |
| 0.4 | Config: `permission_mode=Paper` (gates paper_trader via `is_tool_permitted`; the old `autonomous_paper_trading` flag was removed in `7117bdf` 5→3 mode refactor) | DONE |
| 0.5 | Start gateway for unattended run | DONE |
| 0.6 | Pure observation period (72h minimum) | RESET 2026-06-09 — first attempt was a false start (see note) |
| 0.7 | Deterministic self-heal (code-drift/stale→restart, port cleanup, LLM→alert) | DONE 2026-06-09 (`8a6e008` + rate-limit fix `abd7773`) |

> **2026-06-09 false-start note**: A prior "observation period" was recorded as running, but on audit the gateway was a zombie — alive on stale code (`1aa1feb`, 7 commits behind HEAD), `/api/health` degraded with `code_updated_pending_restart` + `market_data_stale:1114min`. `paper_trader` had not fired in 11h; 294 opportunities produced **0** proposals/orders/fills. Restarted gateway → healthy, then built task 0.7 (deterministic self-heal) so this class of zombie auto-resolves within one 10-min health cycle. **Verified end-to-end 2026-06-09**: induced code drift → health flagged it → self-heal auto-restarted → supervisor respawned fresh code → drift cleared (audit `self_heal_restart: code_drift`). This was exactly the "False confidence" failure mode below — process alive ≠ system running.

### Observation Gate

> **Timer reset 2026-06-09 (cutover)** — paper trading moved from local `paper_internal`
> matching on **spot** to **Bitget demo** real-simulation orders on **USDT perpetuals**.
> The prior spot/local baseline is voided by this deliberate higher-fidelity form change
> (not by drift noise). This cutover commit is the intended exception to the no-commit rule;
> the new window resumes the rule: **no commits during the observation window** (drift
> compares booted commit vs `git HEAD`, not the working tree, so editing without committing
> is safe).

All must pass:
- [ ] Gateway 72h+ no crash (health = healthy/degraded, not dead) — and **no `self_heal_restart` from `code_drift`** in the window (those mean someone committed)
- [ ] market_observer producing opportunities every 15min (>70% non-error)
- [ ] paper_trader executed at least **5 fills** — each 15-min cycle that opens or adds counts as one trading action ("one open" by project convention); the `positions` table merges same-symbol/direction adds into one row, so count **fills**, not position rows
- [ ] position_manager closed at least 2 positions (demo closes place a real hedge-mode reduce order; the exchange must go flat too)
- [ ] post_trade_reviewer + risk_critic have output (knowledge_items grows beyond baseline)
- [ ] **demo orders visible on Bitget**: `get_open_orders`/`get_positions` show paper_trader's real demo orders; exchange_sync reflects demo equity/positions

> **Watch (NOT a gate)**: positions stay concentrated in one symbol because paper_trader re-picks the top opportunity each cycle and adds to the same side. **Accepted as fine for Phase 0** — the gate proves the loop *runs*. Symbol diversification is a Phase 2A concern, not a stability gate.

**If gate fails**: Diagnose (LLM unreachable / config error / data source down / demo API rejecting), fix, restart timer.
**Gate caveat**: "5 fills / 2 closes" measures that the *loop runs*, not that it's *profitable* — profitability is a separate Phase 1+ question (see "False confidence" failure mode).

---

## Phase 1 — Learning Quality Diagnosis

**Goal**: Prove that memory/review produces signal, not noise. Accumulate statistical baseline.

### Tasks

| ID | Task |
|---|---|
| 1.1 | Continue unattended run to 14 days total |
| 1.2 | Daily Learning Ledger check: `lessons_consulted`, `activation_ratio` trend |
| 1.3 | Manual audit: sample 10 opportunities for substantive key_observations |
| 1.4 | Manual audit: sample 5 post_trade_reviews for actionable insights |
| 1.5 | Compute paper trading stats: win rate / avg hold / max DD / expectancy |
| 1.6 | Diagnose and fix if quality is poor (prompt tuning, stronger model, richer data) |

### Observation Gate

- [ ] 14 days continuous run without manual restart
- [ ] `lessons_consulted` > 0 (FTS5 retrieval actually firing)
- [ ] Opportunity audit: >=60% have substantive key_observations
- [ ] Review audit: >=50% point to specific actionable improvements
- [ ] Paper trading: >=20 completed trades (sufficient sample)

---

## Phase 2A — Structured Signals & Executable Strategy

**Goal**: Strategy output becomes machine-readable. Paper_trader actually executes strategy rules.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 2A.1 | Extend `MarketRead` -> `TradingSignal` with `entry_trigger: list[Condition]` | WORLD_MAP 4.5 |
| 2A.2 | Define `Condition` Pydantic schema (`indicator`, `op`, `value`, `timeframe`) | TradingAgents structured output |
| 2A.3 | `strategy_designer` outputs structured conditions (JSON), not free-text | WORLD_MAP 4.6 |
| 2A.4 | `paper_trader` reads and checks entry_trigger conditions against market data | Fixes gap C in PIPELINE_STATUS |
| 2A.5 | Add condition evaluator utility (compare indicator value to threshold) | — |
| 2A.6 | Strategy lifecycle: promotion gate now checks "strategy rules were actually tested" | WORLD_MAP 4.6 |

### Observation Gate

- [ ] paper_trader decision logs show entry_trigger evaluation (not just direction+confidence)
- [ ] At least 3 strategies have machine-readable entry/exit conditions
- [ ] Promotion gate distinguishes "strategy rule validated" vs "random signal luck"

---

## Phase 2B — Agent Coordination Flow

**Goal**: Agents form directional data flow instead of independent cron jobs.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 2B.1 | Define workflow in orchestrator: observer -> risk_critic -> paper_trader | WORLD_MAP 4.7 (Decision Layer) |
| 2B.2 | risk_critic can flag opportunity as `risk_flagged` (trader skips) | TradingGroup chain + hard override |
| 2B.3 | Event-driven trigger: observer completion triggers critic, critic triggers trader | WORLD_MAP 6.2 |
| 2B.4 | Retain cron as fallback (if event chain breaks, cron still runs) | Resilience |
| 2B.5 | Add `NoActionDecision` records when critic blocks an opportunity | WORLD_MAP 4.7.3 |
| 2B.6 | Dashboard shows workflow chain events in Activity Timeline | WORLD_MAP 4.14 |

### Observation Gate

- [ ] risk_critic-flagged opportunities are skipped by paper_trader (observable in logs)
- [ ] Workflow chain (observer->critic->trader) fires end-to-end
- [ ] NoActionDecision records explain why blocked opportunities weren't traded
- [ ] System still trades when event chain is delayed (cron fallback works)

---

## Phase 3A — Experience Closes the Loop

**Goal**: Memory actively influences trading decisions. All agents read relevant lessons.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 3A.1 | `paper_trader` reads `retrieve_lessons()` (advisory gate) | Fixes gap A in PIPELINE_STATUS |
| 3A.2 | `risk_critic` reads lessons when evaluating opportunities | — |
| 3A.3 | `dreaming_agent` reads actual trade positions + PnL (not just agent_runs summaries) | WORLD_MAP 4.12 |
| 3A.4 | Playbooks fully activated (all agents inject relevant playbook blocks) | — |
| 3A.5 | `post_trade_reviewer` produces structured `TradeOutcome` (not just text) | WORLD_MAP 4.11 |
| 3A.6 | `TradeOutcome` auto-annotated on originating opportunity | Closes feedback loop |
| 3A.7 | `market_observer` prompt injects "last 5 outcomes for this symbol" | TradingGroup labeled decisions |
| 3A.8 | New metric: `outcome_coverage` (% opportunities with annotated result) | — |

### Observation Gate

- [ ] `lessons_consulted` steadily > Phase 1 baseline
- [ ] `activation_ratio` > 30% (strategies not all stuck in draft)
- [ ] `review_coverage` > 50% (most traded strategies have reviews)
- [ ] paper_trader logs show "skipped/adjusted due to lesson X"
- [ ] Strategies annotated with positive/negative expectancy

---

## Phase 3B — Walk-Forward Strategy Evolution

**Goal**: From "accumulate experience" to "experience changes behavior" — with statistical validation.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 3B.1 | New `strategy_reviewer` agent (every 3 days, checks paper_running outcomes) | SHARP paper |
| 3B.2 | Attribution logic: entry problem vs exit problem vs sizing problem | — |
| 3B.3 | Produce `StrategyEdit` proposals (structured parameter changes) | — |
| 3B.4 | Walk-forward validation: compare pre-edit vs post-edit on held-out data | SHARP epsilon gate |
| 3B.5 | Acceptance threshold: post-edit expectancy >= pre-edit - epsilon (configurable, default 50bps) | — |
| 3B.6 | Validated edits auto-apply; rejected edits stored as negative knowledge | — |
| 3B.7 | All edits versioned (can revert) | Principle: every change is rollable |

### Observation Gate

- [ ] At least 3 StrategyEdit proposals generated
- [ ] At least 1 passes walk-forward validation and is applied
- [ ] Applied edit does not worsen performance over 14-day observation
- [ ] No strategy modified without walk-forward validation

---

## Phase 4 — Adversarial Hardening & Retirement

**Goal**: Not all "improvements" are real. Add robustness through adversarial challenge.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 4.1 | risk_critic challenges each StrategyEdit ("find scenarios where this fails") | TradingAgents bull/bear debate |
| 4.2 | max_debate_rounds = 1 (statistics decide, not LLM persuasion) | — |
| 4.3 | Auto-retirement rule: N consecutive loss days + 3 failed edits -> retired | WORLD_MAP 4.6 lifecycle |
| 4.4 | Strategy leaderboard: Sharpe / win rate / max drawdown ranking | — |
| 4.5 | Retirement reasons auditable and visible in dashboard | — |
| 4.6 | Configurable thresholds: `max_consecutive_losses`, `min_expectancy_threshold` | — |

### Observation Gate

- [ ] At least 2 strategies experience full lifecycle (create -> run -> revise -> retire OR promote)
- [ ] Retirement reasons are traceable (not sudden disappearance)
- [ ] risk_critic successfully blocked at least 1 edit that would have lost money
- [ ] System runs 4+ weeks unattended

---

## Phase 5 — Intelligence Expansion

**Goal**: Multi-domain sensing — news, deeper market structure, cross-asset signals.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 5.1 | Intelligence Synthesizer: deduplicate + link scanner outputs before opportunity creation | WORLD_MAP 4.4 |
| 5.2 | News Intelligence Scanner: fetch, classify, link to assets, score impact | WORLD_MAP 4.3.4 |
| 5.3 | NewsEvent -> ResearchNote -> OpportunityCandidate pipeline | WORLD_MAP 6.4 |
| 5.4 | Regime classification signal (trending / ranging / volatile / quiet) | — |
| 5.5 | Funding rate / open interest integration (deeper crypto signals) | — |
| 5.6 | Cross-scanner consistency check (contradictions surfaced explicitly) | WORLD_MAP 4.4 Contradiction output |

### Observation Gate

- [ ] News events produce structured signals that influence opportunities
- [ ] Synthesizer deduplicates and prevents signal flooding
- [ ] Regime signal observable and correlated with real market behavior
- [ ] No news scanner directly creates orders (sensing only)

---

## Phase 6 — Live Readiness

**Goal**: Prepare (not rush) live-limited trading with maximum safety controls.

### Tasks

| ID | Task | Reference |
|---|---|---|
| 6.1 | Live lock state machine (disabled -> candidate -> limited) | WORLD_MAP 4.9.2 |
| 6.2 | Live unlock flow: CLI + Dashboard, requires explicit human confirmation | WORLD_MAP 7.3 |
| 6.3 | First-live confirmation record (audit event, cannot be automated) | Safety |
| 6.4 | Live capital envelope: strict notional cap per trade and total | WORLD_MAP 7.4 |
| 6.5 | Bitget live-read reconciliation (compare local state vs exchange) | WORLD_MAP 7.5 |
| 6.6 | Position mismatch -> kill switch trigger | WORLD_MAP 4.15 |
| 6.7 | Live-limited order path through Execution Gateway | WORLD_MAP 7.6 |
| 6.8 | Live audit report generator | — |

### Prerequisites (before even starting Phase 6)

- Phase 4 observation gate fully passed
- 12+ weeks of paper trading with positive expectancy
- Human explicitly authorizes beginning of Phase 6 work

### Observation Gate

- [ ] Live remains off unless explicitly unlocked
- [ ] First live_limited action requires confirmed human interaction
- [ ] Live uses strict capital/risk boundaries (cannot exceed configured caps)
- [ ] Position mismatch detection works and triggers kill switch

---

## Phase 7+ — Advanced (Requires Separate Authorization)

| Direction | Description | Prerequisites |
|---|---|---|
| Strategy parameter optimization | Agent tunes within bounded search space | Structured strategies + walk-forward |
| Multi-round adversarial debate | bull/bear + macro/micro cross-validation | Coordination flow stable |
| Multi-strategy portfolio | Portfolio Manager agent allocates weights | Multiple strategies independently profitable |
| Prediction market trading | Polymarket-style edge detection | WORLD_MAP 4.3.3 complete |
| RWA / xStocks trading | Tokenized equity with issuer risk model | WORLD_MAP 4.3.2 complete |
| Strategy Learning Scanner | Internet research -> hypothesis generation | WORLD_MAP 4.3.5 complete |
| Embedding retrieval | FTS5 -> vector search (more precise lesson matching) | FTS5 bottleneck proven |
| RL strategy optimization | AlphaQuanter-style end-to-end | 6+ months labeled data |
| Telegram deep integration | Approvals, alerts, control via bot | Core loop stable |

---

## Common Failure Modes (Must Prevent)

| Failure Mode | Symptom | Defense |
|---|---|---|
| Noise solidification | "Learns" fake patterns from losses, loses more next time | Walk-forward gate; only high_confidence lessons |
| Overfitting history | Perfect on backtest, fails live | No backtest optimization; real paper market validation |
| LLM hallucination in decisions | References non-existent data/patterns | key_observations must cite specific snapshot numbers |
| Single agent bottleneck | One agent dies, whole chain breaks | Cron fallback + health check + graceful degradation |
| Complexity creep | Too many features, impossible to debug | Observation gates; don't proceed until current phase stable |
| False confidence | "Ran 2 weeks without crash" != "can profit" | Separate stability and profitability verification |
| Strategy text-only trap | Beautiful text strategies that are never actually executed | Structured Condition schema (Phase 2A) |
| Feedback echo chamber | Agent reads own bad advice, amplifies mistakes | Adversarial critic + walk-forward validation |

---

## Principles (Apply Throughout)

1. **Observation gate per phase** — no "good enough" judgments; criteria are binary
2. **Risk Engine is always final** — nothing learned by agents bypasses hard limits
3. **Every change is reversible** — strategy edits have version history, can revert
4. **Live is a distant goal** — paper success does not mean live readiness
5. **One change at a time** — never combine new coordination + new format + new learning
6. **Honest positioning** — research scaffold, not deterministic profit tool
7. **Phases are not time-locked** — go fast when ready, pause when unsure
8. **World Map guides design, ROADMAP governs execution** — vision != schedule

---

## File Relationships

```
ROADMAP.md (this file)         — Phase-based execution plan with observation gates
WORLD_MAP.md                   — Full architecture vision (module specs, data contracts, workflows)
OPERATOR_LAYER_DESIGN.md       — Cross-cutting Operator Layer track design (LLM Apex Agent Shell, layer B)
PIPELINE_STATUS.md             — Current implementation status per pipeline stage
north_star_references.md       — Reference systems comparison + gap analysis
AGENTS.md                      — Governance red lines (constraints)
DECISIONS.md                   — Architectural decision records
AI_HANDOFF.md                  — Session handoff state
```
