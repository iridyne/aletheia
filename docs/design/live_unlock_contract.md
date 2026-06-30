# Live Unlock 信任边界契约 (L2 金标准)

> **角色**:定义实盘解锁这一最不可逆操作的信任边界——谁能解锁、需什么前置、写什么审计、agent 如何被永久挡在门外。
> 这是 [VISION](../product/VISION.md) "hard control = 试错许可证" 的最后一道门。
> **基线**:代码为底(file:line),意图差距标 ⚠️;冲突时文档为准。
> **不变式来源**:[trust §6.3 (live.py / permissions)](../governance/trust_and_change_control.md)。
> Last updated: 2026-06-21 · 维护者:Aletheia Dev

---

## 1. Live 状态标志

```python
# config/schema.py:111-113
class LiveConfig:
    state: str = "live_disabled"               # 默认锁死
    first_live_confirmation_done: bool = False
```

状态值:`live_disabled`(默认) / `live_limited`。**契约**:系统出厂即 `live_disabled`,任何 live 执行被 [risk_engine_contract §2 第3关](risk_engine_contract.md) 拒。

---

## 2. 解锁路径与人工确认门

| 项 | 行为 | 位置 |
|----|------|------|
| 入口 | `live_unlock(req: LiveUnlockRequest)` | `server/routes/live.py:17` |
| 前置 | `confirm=true` **且** 首次需 `acknowledge_first_unlock=true`;当前状态须为 `live_disabled` | `live.py:17-45` |
| 方向 | 仅 `live_disabled → live_limited`(单向) | — |
| 反向锁定 | 独立路由 | `live.py:48-66` |

**不变式([trust §6.3](../governance/trust_and_change_control.md)):**
- 首次 live 解锁**必须**人工显式确认。
- live lock/unlock **必须**写审计事件。
- `live.py` 路由**不得**下单。
- 测试须覆盖"默认 disabled"+"解锁审计"。

> **[BACKLOG-LU-01] severity: moderate** — 解锁路由没有操作员 `permission_mode` 代码层检查,当前仅靠 dashboard HTTP 层隔离。目标态:解锁路由应在服务端显式校验 operator permission_mode。详见 [docs/issues/BACKEND_ISSUES.md](../issues/BACKEND_ISSUES.md)。

---

## 3. 解锁审计格式

```json
// live.py:35-43 → data/logs/audit.jsonl (jsonl.py:36)
{
  "actor": "human", "source": "dashboard",
  "event_type": "live_unlocked", "entity_type": "live_state",
  "before": {"state": "live_disabled"},
  "after": {"state": "live_limited"},
  "message": "Live trading unlocked to live_limited by human confirmation"
}
```
append-only(`jsonl.py` 不删,rotate 改名 `.1`)。完整审计字段见 [trust §1.4](../governance/trust_and_change_control.md)。

---

## 4. 不可逆操作清单

- **解锁 live**(§2)——单向,审计不可删。
- **真实下单**(`bitget/okx/binance_live_limited`)——经 [execution_contract](execution_contract.md),交易所确认后不可撤回(只能反向平仓)。

---

## 4b. 开启 Live 后的 PI 全权模型

**产品定义（2026-06-30）**：Live 模式一旦由人类开启，PI Operator 在 `Auto(4)` 权限下对主赛道（perp + spot）享有全权操作能力——开仓、平仓、调仓，无需逐笔人工确认。

**UI 警示墙（强制）**：Dashboard 的 Live 解锁入口必须在人类确认前展示醒目警示，包含以下全部要素：
- "PI Agent 将完全自主执行交易，操作不可预测"
- "真实资金损失可能发生"
- "开启后所有操作由人类承担后果"

**不变式**：
- PI 全权限于**主赛道**（perp/spot），第二赛道（xStocks/Serenity）由 serenity_executor 独立管辖
- 全权 ≠ 宪法豁免：风控关卡（risk_engine 16→20 关）、FORBIDDEN_INTENTS、宪法文件（本契约 + risk_engine_contract）在 Live 全权下仍全部生效，PI 不能修改自身约束
- Live 解锁仍须遵守 §2 的人工确认门，自动化流程不得绕过

---

## 5. Permission Mode(双面)

**Worker 面(Agent Shell)** `agent_shell/permissions.py:6`:
```
Observe(0) < Paper(1) < Full(2)
```
默认 `agent.permission_mode = "Paper"`(`schema.py:116`)。

**Operator 面(PI Agent/Dashboard)** `agent_shell/permissions.py:10`:
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

> ⚠️ **路径偏差**:[trust §6.1](../governance/trust_and_change_control.md) 列控制模块为 `aletheia/agent_runtime/permissions.py`,实际代码在 `aletheia/agent_shell/permissions.py`。批次5核实并统一(以代码为准修文档,或确认 trust 文档需更新)。

**不变式([trust §6.3](../governance/trust_and_change_control.md)):** 受限工具对 LLM-driven agent 不可调用;权限扩展必须显式 + 测试;agent **不得**经注册表失误获得 live 解锁/凭证变更/风控变更/执行网关变更能力。
