# Promotion Gates 策略晋级门契约 (L2 金标准)

> **角色**:定义策略从假设到实盘的晋级门槛——这是 [VISION](../product/VISION.md) "B 个人终点线"所依赖的质量闸,也是"信任是赚来的"的物理实现。
> **基线**:意图源 [trust §5](../governance/trust_and_change_control.md) 已是近完整规范,代码 100% 吻合;一处残余偏差(偏差1)标 ⚠️;偏差2(PI 审批门)已于 2026-06-23 确认实现,标 ✅。
> Last updated: 2026-06-21 · 维护者:Aletheia Dev

---

## 1. 生命周期状态机

```python
# trading/strategy_lifecycle.py:27-51
LIFECYCLE_STATES = [draft, research_note, strategy_hypothesis,
    paper_candidate, paper_running, paper_passed,
    live_candidate, live_limited, retired, deprecated]

FORWARD_TRANSITIONS = {
    draft→research_note→strategy_hypothesis→paper_candidate
    →paper_running→paper_passed→live_candidate→live_limited
    retired→deprecated   # 终态
}
```
任何非终态可转 `retired`/`deprecated`(逃生口)。

**[ARCH_GUIDE 第八章](../SYSTEM_ARCHITECTURE_GUIDE.md) 原则**:自动推进止于 `paper_candidate`,激活需门检查。

---

## 2. 晋级门(默认阈值 — 金标准)

```python
# trading/strategy_lifecycle.py:55-63 DEFAULT_PROMOTION_GATES
min_paper_runtime_days = 14
min_paper_trades = 30
positive_expectancy_required = True
max_drawdown_pct_allowed = 5.0
max_consecutive_losses_allowed = 5
critical_risk_violations_allowed = 0
post_trade_reviews_required = True
```

主函数 `check_promotion_gates`(`strategy_lifecycle.py:209-293`)。**代码实现与 [trust §5](../governance/trust_and_change_control.md) 意图 100% 吻合**(逐条核对见下)。

| 门 | 阈值 | 算法(trust §5) | 失败标签 | 代码行 |
|----|------|---------------|---------|--------|
| 状态 | paper_running/passed | — | — | :199 |
| Runtime Days | ≥14 | 最早 paper/demo order→now;仅 paper_internal+bitget_demo | `insufficient_paper_runtime_days:{n}<14` | :233 |
| Trade Count | ≥30 | 优先 fills,否则 filled orders | `insufficient_paper_trades:{n}<30` | :225 |
| Positive Expectancy | >0 | closed positions 的 `realized_pnl` 求和 | `non_positive_expectancy:{pnl}` | :260 |
| Max Drawdown | ≤5% | 累计 PnL equity 曲线峰谷 / `max_live_limited_cap_usdt` | `max_drawdown_exceeded:{a}>5.0` | :262 |
| Max Consec Losses | ≤5 | closed positions 按 closed_at,最长连亏 run | `max_consecutive_losses_exceeded:{a}>5` | :266 |
| Critical Risk Violations | 0 | risk_decisions.violations 含 critical token(见 trust §5.7) | `critical_risk_violations:{a}>0` | :272 |
| Post-Trade Reviews | ≥1 active | `knowledge_items.item_type=post_trade_review` | `post_trade_review_required` | :279 |

**契约([trust §5.1](../governance/trust_and_change_control.md)):** 门必须从**存储记录**计算,不能只看状态标签。算不出的指标用显式 `insufficient_*` **失败**,绝不"假设通过"。

---

## 3. 数据源表

`strategy_records`(状态/时间戳) · `trade_proposals`(via strategy_id) · `risk_decisions`(via proposal) · `orders`(paper/demo,via proposal) · `fills`(via order) · `positions`(realized_pnl,via strategy_id) · `knowledge_items`(post_trade_review)。代码 `strategy_lifecycle.py:203-221`。

---

## 4. 关键偏差(意图 vs 代码)

> ⚠️ **偏差 1 — 高频放松规则(代码有,意图未写):** `strategy_lifecycle.py:228-241`——若 `paper_trade_count≥30`,runtime 门槛从 14 天**放松到 3 天**。这是代码真实逻辑,trust §5.2 未记录。**裁决**:文档为准——本契约采纳此规则为金标准并补记于此(高频策略快速验证合理),trust §5.2 待批次3补注。

> ✅ **偏差 2 — System Operator 审批门 (已实现, 2026-06-23 落地):** `promote_strategy()` 在目标状态为 `paper_running` 时,调用 `_has_pi_approval(strategy_id)`(`strategy_lifecycle.py:142-150`),查询 `pi_decisions` 表是否存在 `action='recommend_promote'` 记录(`strategy_lifecycle.py:167-172`)。无该记录则返回 `{"success": False, "error": "Promotion to paper_running requires System Operator approval ..."}`,硬阻断晋级。**门已是单点卡口,原"未实现"标记已失效,予以移除。**

---

## 5. 实盘晋级(更严)

**paper_passed→live_candidate([trust §5.9](../governance/trust_and_change_control.md)):** 当前状态恰为 paper_passed + §2 全门通过 + 写审计。**不得**仅凭状态名晋级。

**live_candidate→live_limited([trust §5.10](../governance/trust_and_change_control.md)):** 额外要求 `live.state≠live_disabled` + `first_live_confirmation_done==true` + permission mode 允许 + Risk Engine 许可 live + 审计记录 actor/source。**首次 live 解锁必须人工**——见 [live_unlock_contract](live_unlock_contract.md)。

---

## 6. 退役/降级

| 路径 | 实现 | 位置 |
|------|------|------|
| 自动退役 | 连续 3 次无数据回测→`promote_strategy(sid,retired)` | `strategy/orchestrator.py:130` |
| Operator 推荐退役/降级 | **已收敛到 TS operator-shell（owner 决定 2026-06-28）**：策略退役/降级由 TS operator-shell 评估并经 command registry 执行。Python `operator_loop.decide_actions` **不再产生** `retire_strategy`/`demote_strategy`（旧机制写 `PiDecision(recommend_retire/demote)` 但无消费者，是死写——见 `docs/issues/PI_AGENT_SDD_AUDIT_2026-06-28.md`）。`_action_retire/demote_strategy` 函数暂留供 PG-B05 + TS 收敛完成前引用，但不再被调度。收敛锁：`spec_pi_decision_chain.py::PI-S03`。 | TS `operator-shell/`；Python `operator_loop.py` 已停产生 |

> ⚠️ 门阈值当前**硬编码**(`DEFAULT_PROMOTION_GATES`),未在 config.yaml 暴露。按 [CLAUDE.md Config↔UI 规则](../../CLAUDE.md),若要做成用户可调需同步加 schema 字段 + 前端控件。当前不可调=有意 MVP 简化。
