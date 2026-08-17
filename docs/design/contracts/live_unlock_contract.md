# Live Unlock 信任边界契约 (L2 金标准)

> **角色**:定义实盘解锁这一最不可逆操作的信任边界——谁能解锁、需什么前置、写什么审计、agent 如何被永久挡在门外。
> 这是 [Product Direction](../../product/definition/DIRECTION.md) 所要求的人类控制资本暴露与逐级获得自主权的最后一道门。
> **基线**：以当前代码为底；本契约描述已实现边界，产品支持状态仍以 Capability Ground Truth 为准。
> **不变式来源**:[trust §6.3 (live.py / permissions)](../../governance/policies/trust_and_change_control.md)。
> Last updated: 2026-06-21 · 维护者:Aletheia Dev

---

## 1. Live 状态标志

```python
# config/schema.py:111-113
class LiveConfig:
    state: str = "live_disabled"               # 默认锁死
    first_live_confirmation_done: bool = False
    unlock_evidence: dict | None = None        # sanitized, expiring selected-account proof
```

状态值:`live_disabled`(默认) / `live_limited`。**契约**:系统出厂即 `live_disabled`,任何 live 执行被 [risk_engine_contract §2 第3关](risk_engine_contract.md) 拒。

---

## 2. 解锁路径与人工确认门

| 项 | 行为 | 位置 |
|----|------|------|
| 入口 | `live_unlock(req: LiveUnlockRequest)` | `server/routes/live.py` |
| 前置 | `confirm=true` **且** 首次需 `acknowledge_first_unlock=true`;当前状态须为 `live_disabled`;随后必须通过当前 `exchange.name` 对应 live broker 的私有账户预检 | `live.py` |
| 方向 | 仅 `live_disabled → live_limited`(单向) | — |
| 反向锁定 | 独立路由；同时清除 unlock evidence | `live.py` |

**不变式([trust §6.3](../../governance/policies/trust_and_change_control.md)):**
- 首次 live 解锁**必须**人工显式确认。
- live lock/unlock **必须**写审计事件。
- `live.py` 路由**不得**下单。
- 预检必须使用选定 broker 的签名私有账户请求；结果明确 `public_reachability=not_used_as_authority`，公共可达性、Bitget legacy cache、demo/testnet 响应不能证明 live 解锁。
- 预检证据只保存 broker、live 环境、账户指纹、凭证/配置修订号、权限和有效期；缺少订单权限时按 `permission_unknown=deny` 失败。
- Bitget 私有账户的 `authorities` 权限范围必须明确包含交易权限；只读或未知范围均拒绝。
- 预检到提交之间若凭证、账户、环境或配置修订号改变，必须拒绝提交；切换选定交易所或修改其 live 凭证时必须先 lock。
- 证据过期或不再匹配选定账户时，`/api/status`、Risk 或 Gateway 发现该状态会撤销 `live_limited`、清除证据并把 `operator.permission_mode` 重置为 `ReadOnly`；撤销原因和审计结果仍公开在状态响应和审计镜像中。
- 测试须覆盖"默认 disabled"+"解锁审计"。

> `operator.permission_mode` 管理 Agent/Operator 自主权限，不是人类 Dashboard 解锁的授权门；把它加到此路由会混淆两个信任面，因此旧 `BACKLOG-LU-01` 已否决。Live Unlock 虽有确认、scorecard 与审计门，但当前 System Operator Proposal 仍不会路由到 `*_live_limited`，所以产品能力保持 Experimental，见 GitHub #418。

---

## 3. 解锁审计格式

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
  "message": "Live trading unlocked to live_limited by human confirmation after selected-account preflight; PI operator elevated to Auto"
}
```
append-only(`jsonl.py` 不删,rotate 改名 `.1`)。完整审计字段见 [trust §1.4](../../governance/policies/trust_and_change_control.md)。

---

## 4. 不可逆操作清单

- **解锁 live**(§2)——单向,审计不可删。
- **真实下单**(`bitget/okx/binance_live_limited`)——经 [execution_contract](execution_contract.md),交易所确认后不可撤回(只能反向平仓)。

---

## 4a. Core Philosophy：Demo 表现是 Live 的唯一入场券

**不变式（D8-C01）**：`POST /api/settings/live-unlock` 在将状态迁移至 `live_limited` 之前，
**必须**调用 `compute_scorecard()`，且仅在 `composite_score >= 70`、`traffic_light == "green"`、
`blockers` 为空时方可放行。评分卡必须把合格交易的净已实现收益作为独立硬门：已有合格交易但
净收益 `<= 0` 时强制红灯并产生 blocker，不得由其他加权维度抵消。
任何绕过此 scorecard 检查的路径均违反本约。

**依据**：产品定义 §8（失败与撤退）。demo 阶段是 live 的前置证明期；
没有量化证据支撑的 live 解锁等同于盲目冒险。

**当前实现状态（2026-07-21 更新）**：`server/routes/live.py` 在状态迁移前调用
`compute_scorecard()`；低于 70 分、非绿灯或存在 blocker 均返回 403 并附上分项。
D8-B02 / D8-C01 及负净收益回归 spec 锁定该门禁。

> 关联契约：[kill_conditions_contract.md § K3](kill_conditions_contract.md)

---

## 4b. Live 权限准备态（Experimental）

**当前产品事实（2026-07-28）**：Live Unlock 会在人类确认及 scorecard
门禁后持久化 `live.state=live_limited`，并把 PI Operator 设为 `Auto(4)`。
这只是实验性的权限准备态，不代表新 Proposal 已能选择 `*_live_limited`
执行模式。当前生产 Proposal 入口仍读取 `exchange.paper_execution_mode`，
因此自动交易继续使用 paper/demo 通道。缺失的 proposal-mode join 由
EX-04 / GitHub #418 跟踪。

**UI 警示墙（强制）**：Dashboard 的 Live 解锁入口必须在人类确认前展示醒目警示，包含以下全部要素：
- 此操作只记录 `live_limited` 权限前置状态，不会把新 Proposal 路由到实盘；
- 当前自主 Proposal 仍使用已选择的 paper/demo 执行通道；
- 只有未来独立完成并验证 live Proposal 路由后，真实资金风险才会出现。

**不变式**：
- Unlock 不得被解释为 live Proposal 路由已经存在，也不得在 Gateway 内把已提交的 demo Proposal 改写成 live
- `Auto(4)` 仍受风控关卡、FORBIDDEN_INTENTS 和宪法文件约束，不能修改自身约束
- Live 解锁仍须遵守 §2 的人工确认门，自动化流程不得绕过
- **risk_engine.evaluate_proposal() 不读取 operator.permission_mode，Auto 模式不豁免任何风控关卡**（见 §4b-1）

### §4b-1 risk_engine 不依赖 operator.permission_mode（L2c 不变式）

`aletheia/risk/engine.py` 中的 `evaluate_proposal()` **不得**读取 `cfg.operator.permission_mode` 并做 skip/bypass 分支判断。

**原因**：Operator 面的 `permission_mode`（ReadOnly/Research/PaperAuto/AllowEditing/Auto）控制 System Operator 可以调用哪些命令，不影响 risk_engine 的判断逻辑。若 Auto 模式能豁免任何关卡，System Operator 可借助权限提升绕过资金安全约束。

**当前实现（代码为准）**：`engine.py` 读取的是 Worker 面 `cfg.agent.permission_mode`，仅对 `Observe` 权限添加 `permission_mode_insufficient` violation，不存在 skip/bypass 分支。`operator.permission_mode` 不在 engine.py 中出现。

**不变式（PCI-G06 锁定）**：
- 任意 `operator.permission_mode` 值下，违规 proposal 均返回 `approved=False`
- engine.py 不得新增形如 `if cfg.operator.permission_mode == "Auto": skip_check()` 的绕过逻辑
- spec `PCI-G06` 通过源码扫描 + 行为断言双重验证此不变式

---

## 5. Permission Mode(双面)

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

## 6. Agent 永久禁止清单(硬防线)

```python
# agent_shell/registry.py:415-419
FORBIDDEN_INTENTS = frozenset([
    "enable_live", "modify_credentials", "bypass_risk",
    "place_live_order", "modify_execution_gateway",
    "delete_knowledge", "change_risk_limits",
])
```

检查实现 `agent_shell/permissions.py:42-72`:命中 `FORBIDDEN_INTENTS` 立即拒("permanently forbidden");未注册命令拒;权限等级不足拒。

> ⚠️ **路径偏差**:[trust §6.1](../../governance/policies/trust_and_change_control.md) 列控制模块为 `aletheia/agent_runtime/permissions.py`,实际代码在 `aletheia/agent_shell/permissions.py`。批次5核实并统一(以代码为准修文档,或确认 trust 文档需更新)。

**不变式([trust §6.3](../../governance/policies/trust_and_change_control.md)):** 受限工具对 LLM-driven agent 不可调用;权限扩展必须显式 + 测试;agent **不得**经注册表失误获得 live 解锁/凭证变更/风控变更/执行网关变更能力。
