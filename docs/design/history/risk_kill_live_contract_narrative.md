# Risk Engine, Kill Conditions and Live Unlock — retired contract narrative

> History (moved 2026-10-10): verbatim passages removed from the three contracts when they were
> rewritten into requirement form. Evidence for provenance only; current behavior lives in
> [risk_engine_contract](../contracts/risk_engine_contract.md),
> [kill_conditions_contract](../contracts/kill_conditions_contract.md) and
> [live_unlock_contract](../contracts/live_unlock_contract.md).

## From risk_engine_contract.md

### Former header

#### Risk Engine 决策契约 (L2 金标准)

> **角色**:定义 Risk Engine 作为"agent 不可绕过的硬墙"的完整决策契约。
> 这是 trust 边界的核心——[Product Direction](../../product/definition/DIRECTION.md) 要求失败可生还且自主权只能逐级获得。
> **基线**：本契约描述当前权威行为；偏差进入 GitHub Issues。
> **产品范围**：只交易 USDT 本位加密永续，唯一获准新入场路由是 `bitget_demo`。下表中涉及现货、`okx_demo`、`binance_testnet` 的分支是主线不可达或仅服务冻结路由的残留，不是支持声明。
> **不变式来源**:[trust_and_change_control §6.3](../../governance/policies/trust_and_change_control.md)。

### Check #5 drawdown source narrative

> ⚠️ 关卡 #5 的回撤缩放(`_portfolio_drawdown_scale`, engine.py:356):缩放系数(0-5% DD→1.0, 5-10%→1.0→0.5, 10-20%→0.5→0.0, >20%→0)由组合回撤驱动。**回撤的数据源**:交易所模式(okx_demo/*_demo/live)读 `portfolio_snapshots` 的 `exchange_account` 行(真实账户 equity),**不读**每个 mode 自己的行——后者的 `total_value_usdt` 是持仓 notional(Σqty×price)而非 equity,平掉盈利持仓会让 notional 暴跌被误判成回撤、把 cap 缩到 $0 锁死开仓。spec: RE-B05（分段）+ RE-B05a（交易所数据源）。

### Guard clarification (2026-06-30)

> **唯一硬门 & guard 澄清(2026-06-30)**:订单通路(`submit_proposal`→执行)上,**`evaluate_proposal()` 是唯一硬门**。另有两套也叫 "guard" 的东西**不在执行通路上裁决**:
> (1) 历史 `trading_decision` 守卫链曾只写 `decision_meta['guards']`、提供 advisory；该旧入场消费者已删除，不是当前执行入口；
> (2) ~~`trust/guard_chain.py:run_guards()`~~ → **已于 2026-09-07 删除**。它从未被任何生产代码调用，因此并不观测任何东西，且其自带的 `max_daily_loss_pct` 默认 `5.0` 与 schema 的 `1.0` 矛盾。spec `EX-C03` 保留并继续生效：它扫的是 `gateway.py`，守的是「唯一硬门是 `risk/engine.py:evaluate_proposal`」这个不变式，而不是该文件的存在。
> 区分：knowledge `trading_guards` 曾在旧 Ranker 排序阶段剔除候选；该消费者现已退役（见 [knowledge_contract §4.5](../contracts/knowledge_contract.md)），历史 guard 命中不授予当前 Entry Plan 执行权，**不是**执行硬门。若未来要把某条 guard 升为硬拦,必须改本契约 + 接线 gateway + 改 EX-C03,不可静默接线。

### Kill switch lesson

**不变式**:kill switch 是 §2 第 0 关,激活后任何 proposal 必被拒;恢复是不可被 agent 触达的人工操作。历史事故教训：daily-loss kill switch 的 UTC 日期边界不可按本地日期推断。

### Former §5 sizing section

#### 5. 仓位计算(当前权威路径)

当前新入场的规划消费者为 System Operator，Entry Plan 经 Risk / Gateway 执行。

System Operator 的权威 sizing 路径是 **自适应 sizing + 确定性杠杆解析**，
实现于 [`entry_sizing_and_financing_contract.md`](../contracts/entry_sizing_and_financing_contract.md)：

```text
baseline_loss_budget = min(hard_capital_base, fresh_available_balance) x max_single_trade_risk_pct/100
effective_loss_budget = baseline_loss_budget x risk_intent_multiplier   # AI 只能保持或降低
target_notional       = effective_loss_budget / stop_fraction           # 与 leverage 解耦
margin_budget         = min(fresh_available_balance, policy.max_position_margin_usdt)
execution_leverage    = 目标敞口的“最低必要且可证明安全”杠杆（不放大 target）
```

- `max_single_trade_risk_pct` 只有一种语义：资本基数中允许在止损处亏损的比例（见关卡 #13）。
- 保证金预算（margin budget）独立于亏损预算，不再由同一字段兼任。
- `policy_leverage_cap`（major/mid/alt 档位 + futures cap）只是上限；它**不等于**实际下单杠杆。
- policy version：`adaptive-risk-intent-sizing/v2`（换代用于隔离 learning evidence cohort）。

历史路径 `available_balance_risk_budget_size()`（`available x risk_pct x leverage`）已无生产
调用者，保留为 sizing analysis helper；`vol_target_size()`、`trading.default_notional_usdt=25`、
`vol_kelly_cap_fraction` 等同为旧版兼容字段，不影响当前权威路径。legacy conviction/Kelly/persona
sizing 不得接回 canonical Entry 路径。

**关键契约**：sizing 计算结果**不是**最终下单量；它仍须通过 §2 各关（#5 保证金上限、#12 总敞口、
#13 单笔风险、#17 账户杠杆比、#18 爆仓缓冲、#20 同标的 risk units）。Risk Engine 只能批准或拒绝，
不会把超限请求优化或裁剪成另一个仓位；已融资 Plan 的冻结果与冻结杠杆必须与提交时 fresh facts
重算结果一致（#21），否则 `SIZING_RESOLUTION_DRIFT` 拒绝而不改写。

### Former §7 and §8

#### 7. 已知缺陷

当前偏差只在 GitHub Issues 中跟踪，本节不维护清单。

---

#### 8. 目标态差距(意图领先代码)

- Planner 的入场方向通过 Entry Plan 进入订单。Risk 验证账户和订单安全边界，不证明方向具有收益优势；旧 Paper Trader 的调用链说明仅作为[历史分析](../history/execution_path_honesty_202606.md)保留。
- ⚠️ 不要把 market-state guard(如资金费率熔断)塞进 Risk Engine——见 [external systems comparison §10.6](../../references/external-systems/comparative_systems.md),那属策略层。

### Former serenity_isolation section

#### serenity_isolation — Serenity 当前隔离边界

##### 当前现状

| 维度 | 当前实现 |
|------|---------|
| 执行模块 | `serenity_executor.py` 不存在；所有提案均通过主赛道 System Operator / Gateway |
| 头寸池 | check #5/#6 共用同一个池，Serenity 占用主赛道配额 |
| PnL 归因 | `analytics/` 和 `portfolio/` 不按 `trade_school` 提供隔离的权威 PnL |

`D2-S02` 保持负向 tripwire，防止独立执行器在没有信任边界审查时
出现。任何独立执行入口、风险池或权威 PnL 隔离都是跨域且触及信任
边界的变化，必须先写成 [spec](../specs/README.md)；它们不是本 Contract 的未来待办。

##### evaluate_proposal 唯一入口不变式

**所有 TradeProposal**（Entry Plan 执行经 `execute_existing_proposal`，调试 CLI 经 `submit_proposal`）在任何 broker 调用之前**必须经过 `evaluate_proposal()`**。

- `gateway._execute_proposal` 是唯一通向 broker 下单的函数
- `evaluate_proposal` 在 `_execute_proposal` 内调用，`risk_result.approved` 守卫在所有 broker 调用之前
- 不存在绕过 risk engine 直接下单的代码路径

spec：`D2-C01`（L2c，spec_risk_engine.py）持续锁住此不变式。

## From live_unlock_contract.md

### Former header

#### Live Unlock 信任边界契约 (L2 金标准)

> **角色**:定义实盘解锁这一最不可逆操作的信任边界——谁能解锁、需什么前置、写什么审计、agent 如何被永久挡在门外。
> 这是 [Product Direction](../../product/definition/DIRECTION.md) 所要求的人类控制资本暴露与逐级获得自主权的最后一道门。
> **基线**：以当前代码为底；本契约描述已实现边界，产品支持状态仍以 Capability Ground Truth 为准。
> **不变式来源**:[trust §6.3 (live.py / permissions)](../../governance/policies/trust_and_change_control.md)。
> Last updated: 2026-06-21 · 维护者:Aletheia Dev

### Former §2 note on permission_mode and #418

> `operator.permission_mode` 管理 Agent/Operator 自主权限，不是人类 Dashboard 解锁的授权门；把它加到此路由会混淆两个信任面，因此旧 `BACKLOG-LU-01` 已否决。Live Unlock 虽有确认、scorecard 与审计门，但当前 System Operator Proposal 仍不会路由到 `*_live_limited`，所以产品能力保持 Experimental，见 GitHub #418。

### Former §3 audit example

```json
// live.py:35-43 → data/logs/audit.jsonl (jsonl.py:36)
{
  "actor": "human", "source": "dashboard",
  "event_type": "live_unlocked", "entity_type": "live_state",
  "before": {"state": "live_disabled"},
  "after": {
    "state": "live_limited",
    "live_execution_identity": {"broker": "<selected broker>", "account_fingerprint": "sha256:<digest>"}
  },
  "live_execution_identity": {
    "broker": "<selected broker>",
    "execution_mode": "<broker>_live_limited",
    "account_environment": "live",
    "account_fingerprint": "sha256:<digest>",
    "credential_revision": "sha256:<digest>",
    "config_revision": "sha256:<digest>"
  },
  "message": "Live trading unlocked to live_limited by human confirmation after selected-account preflight; System Operator elevated to Auto"
}
```
append-only(`jsonl.py` 不删,rotate 改名 `.1`)。完整审计字段见 [trust §1.4](../../governance/policies/trust_and_change_control.md)。

### Former §4a implementation status

**当前实现状态（2026-07-21 更新）**：`server/routes/live.py` 在状态迁移前调用
`compute_scorecard()`；低于 70 分、非绿灯或存在 blocker 均返回 403 并附上分项。
D8-B02 / D8-C01 及负净收益回归 spec 锁定该门禁。

### Former §4b product fact

**当前产品事实（2026-07-28）**：Live Unlock 会在人类确认及 scorecard
门禁后持久化 `live.state=live_limited`，并把 PI Operator 设为 `Auto(4)`。
这只是实验性的权限准备态，不代表新 Proposal 已能选择 `*_live_limited`
执行模式。当前生产 Proposal 入口仍读取 `exchange.paper_execution_mode`，
因此自动交易继续使用 paper/demo 通道。缺失的 proposal-mode join 由
EX-04 / GitHub #418 跟踪。

### Former §4b-2 history note

`agent_shell/commands._configured_paper_execution_mode` 是本规则的历史实现，已随
#2945 删除的 proposal-creation 命令一并移除。spec: LU-C07。

### Former §5 and §6 code copies

#### 5. Permission Mode(双面)

**Worker 面(Agent Shell)** `agent_shell/permissions.py:6`:
```
Observe(0) < Paper(1) < Full(2)
```
默认 `agent.permission_mode = "Paper"`(`schema.py:116`)。

**Operator 面(System Operator/Dashboard)** `agent_shell/permissions.py:10`:
```
ReadOnly(0) < Research(1) < PaperAuto(2) < AllowEditing(3) < Auto(4)
```
默认 `operator.permission_mode = "ReadOnly"`(`schema.py:118`)。

| 模式 | 能做 |
|------|------|
| ReadOnly | 仅读 |
| Research | 建知识/查询 |
| PaperAuto | 触发 paper 交易/策略设计 |
| AllowEditing | 编辑 config/rule |
| Auto | 完全自主 |

---

#### 6. Agent 永久禁止清单(硬防线)

```python
# agent_shell/registry.py:415-419
FORBIDDEN_INTENTS = frozenset([
    "enable_live", "modify_credentials", "bypass_risk",
    "place_live_order", "modify_execution_gateway",
    "delete_knowledge", "change_risk_limits",
])
```

检查实现 `agent_shell/permissions.py:42-72`:命中 `FORBIDDEN_INTENTS` 立即拒("permanently forbidden");未注册命令拒;权限等级不足拒。

> ⚠️ **路径偏差**:[trust §6.1](../../governance/policies/trust_and_change_control.md) 列控制模块为 `core/aletheia/agent_runtime/permissions.py`,实际代码在 `core/aletheia/agent_shell/permissions.py`。批次5核实并统一(以代码为准修文档,或确认 trust 文档需更新)。

## From kill_conditions_contract.md

### Former header

#### Kill Conditions 契约 (L2 金标准)

> **角色**：定义 demo 阶段的三条 Kill 判据及其计算函数。
> **运行时执行者**：只有 K3 有生产调用方（`server/routes/live.py` 的 Live 解锁）。K1、K2 的计算函数
> （`analytics/go_live_scorecard.py`）当前**没有任何生产调用方**，只被 spec 测试调用：它们是 owner
> 在观察窗口中手动评估的判据，不会自动暂停入场或终止实验。需要自动 Kill 时须另写 [spec](../specs/README.md)。
> **不变式来源**：[Product Direction 的永久边界](../../product/definition/DIRECTION.md#enduring-product-boundaries)；[EXPERIMENT_PROTOCOL.md](../../product/experiments/EXPERIMENT_PROTOCOL.md)。

### Former K1 calculation notes

#### K1：Demo 连续两轮 PF < 0.5 → Terminate Experiment

**判决标准**：go_live_scorecard 中 profit_factor criterion 连续两个有合格 outcome 的
14 日 trailing windows 低于 0.5，
触发实验终止——该方向已证明无法盈利，继续运行只消耗验证窗口和资金。

**Kill 阈值**：`PF < 0.5`

**结构与行为前提**：
- `_check_profit_factor()` 返回的 criterion `weight > 0`（D8-S01 锁死，已实现）
- 极低 PF（< kill 阈值）评为 `status='fail'` 且 `score=0`（D8-B01 锁死，已实现）

**计算函数（无运行时调用方）**：
- `check_kill_condition_consecutive_rounds()` 按 14 日窗口计算连续低于 0.5 的轮次；
- 当前函数遇到空窗口即停止，未另设每窗最小样本量；调用方不得把它描述成样本充分的
  盈利性判决；
- `_check_profit_factor()` 对 PF < 0.5 返回 `status='fail'`、`score=0`。

**对应 Spec Claims**：D8-S01 · D8-S02 · D8-B01

### Former K2 section

#### K2：连续 3 天零开仓 → Trading Loop Dead

**判决标准**：系统连续 3 个自然日没有新的 position open，
意味着决策链已经僵死——数据在流动，但系统无法将机会转化为行动。

**计算函数（无运行时调用方）**：
- `count_consecutive_zero_fill_days()` 读取 `positions.opened_at` 并返回连续无新开仓天数；
- 函数名保留历史 `zero_fill` 用语，但当前统计单元是 position open，不是 `fills` 表行。
- 原 doctor 探针 `_probe_profit_loop()`（`loop.profit`）及其 claim D8-S03 已随 #2945（2e69baae9）删除。

**对应 Spec Claims**：D8-S04

### Former relations table

#### 与其他契约的关系

| 契约 | 关系 |
|------|------|
| [live_unlock_contract.md](../contracts/live_unlock_contract.md) | K3 扩展 §2 解锁路径；D8-C01 是 §core_philosophy 的代码层不变式 |
| [risk_engine_contract.md](../contracts/risk_engine_contract.md) | Kill 触发后下游风控继续拦截；两者独立分层 |
| [EXPERIMENT_PROTOCOL.md](../../product/experiments/EXPERIMENT_PROTOCOL.md) | K1/K2 来源于实验协议 §6 终止规则；K3 来源于产品定义 §8 |
