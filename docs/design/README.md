# Design Layer — L2 契约金标准索引

> 这是文档金字塔**中层契约**的导航中枢:回答"系统**该**是什么样",可作实现依据。
> 冲突裁决:文档为准,代码追文档;已实现部分的偏差落 [issues](../issues/BACKEND_ISSUES.md)。
> Last updated: 2026-06-21

---

## 金字塔全景

```
L0 愿景        VISION ───────────────── 赌什么/为谁/什么叫赢
L1 范围度量    PRD · METRICS · PHASE_GATES ── 做什么/怎么衡量/何时算过
L2 契约(本层)  design/*_contract + data_model + agent_contracts + api_reference
                                          ── 系统该是什么样(实现依据)
L3 计划        plans/* ──────────────── 怎么从现状走到目标
L4 状态/参考   status/* · references/* · _ai/* ── 现在是什么样/外部参考(易腐,非金标准)
```

- 顶层:[VISION](../product/VISION.md) · [PRD](../product/PRD.md) · [METRICS](../product/METRICS.md) · [PHASE_GATES](../product/PHASE_GATES.md)
- 全局重构计划与五类归属:[DOC_SYSTEM_PLAN](../product/DOC_SYSTEM_PLAN.md)
- 长期开发红线:[trust_and_change_control](../governance/trust_and_change_control.md)

---

## L2 契约文档(按对象)

### 信任边界(trust boundary — 改动需 change control)

| 契约 | 对象 | 状态 |
|------|------|------|
| [risk_engine_contract](risk_engine_contract.md) | Risk Engine 16 关审批 / kill switch / sizing / 硬限保证 | ✅ 金标准(批次1新建) |
| [execution_contract](execution_contract.md) | proposal→order→fill→position 因果链 / 执行模式 / 原子性 / 对账 | ✅ 金标准(批次1新建) |
| [live_unlock_contract](live_unlock_contract.md) | live 解锁信任边界 / permission 双面 / FORBIDDEN_INTENTS | ✅ 金标准(批次1新建) |
| [promotion_gates_contract](promotion_gates_contract.md) | 策略晋级门 8 阈值 / 生命周期状态机 / 实盘晋级双门 | ✅ 金标准(批次1新建) |

### 数据与接口

| 契约 | 对象 | 状态 |
|------|------|------|
| [data_model](data_model.md) | ~80 表 + 52 Pydantic / retention / JSON / 并发恢复 | ✅ 金标准(批次2细化) |
| [agent_contracts](agent_contracts.md) | 14 agent + 30 job IO / timeout / 降级 / 故障隔离 | ✅ 金标准(批次2细化) |
| [api_reference](api_reference.md) | REST 端点索引 / 状态码 / trust 端点义务 | ✅ 金标准(批次2细化) |
| [data_layer](data_layer.md) | CCXT provider 重构 / symbol 格式 | 🟡 契约(部分 Phase 已实现) |
| [knowledge_contract](knowledge_contract.md) | Knowledge 检索子系统 / LessonStore / GuardStore / 双轨断裂清单 | ✅ 金标准(批次6 新建) |
| [scheduler_contract](scheduler_contract.md) | 调度器 job 清单 / 频率 / 执行器分组 / 故障隔离 | ✅ 金标准(批次6 新建) |
| [audit_chain_contract](audit_chain_contract.md) | 审计链完整性 / risk_decisions / 操作日志 / 不可变性要求 | ✅ 金标准(批次6 新建) |

### 子系统设计

| 契约 | 对象 | 状态 |
|------|------|------|
| [EQUITY_INTEGRATION_DESIGN](EQUITY_INTEGRATION_DESIGN.md) | xStocks/美股集成 | 🟡 契约 |
| [PREDICTION_MARKET_DESIGN](PREDICTION_MARKET_DESIGN.md) | 预测市场观察模块 | 🟡 契约(观察阶段) |
| [operator_layer](operator_layer.md) | Operator 系统顶层设计 | 🟡 契约(与 specs 双进程设计配合) |
| [PI_OPERATOR_DESIGN_DECISIONS](PI_OPERATOR_DESIGN_DECISIONS.md) | PI Operator 权限/触发决策 | 🟡 契约 |
| [ui_design_system](ui_design_system.md) | 前端设计语言/密度规范 | ✅ 契约 |

### 过程产物(决策记录,非实时契约)

| 文档 | 性质 |
|------|------|
| [overview_redesign](overview_redesign.md) · [webui_page_map](webui_page_map.md) · [webui_backend_handoff](webui_backend_handoff.md) | 2026-06-07 前端重设计决策(已交付) |
| [specs/](specs/) | PI Operator 设计审计/crosswalk(冻结) |

---

### 全局导航

| 文档 | 性质 |
|------|------|
| [SYSTEM_MAP](SYSTEM_MAP.md) | 全模块概念地图 — 数据采集/Agent决策/风控执行/知识学习/配置 全景 + 管道状态 |
| [SDD_METHODOLOGY](SDD_METHODOLOGY.md) | Spec-Driven Development 六阶段工作流 + 11原则 + Clarify 检查清单 |

---

## Spec-as-Code 可执行层

L2 契约的关键断言已编码为 `tests/specs/spec_*.py`，形成机器可验证的闭环：

```
docs/design/*_contract.md  →  tests/specs/spec_*.py  →  Conformance Matrix
     (人类可读 spec)          (@spec_claim 装饰器)       (claim_id → test → pass/fail)
```

| Spec 文件 | 对应契约 | Claims |
|-----------|---------|--------|
| `spec_risk_engine.py` | risk_engine_contract | 7 |
| `spec_execution.py` | execution_contract | 7 |
| `spec_live_unlock.py` | live_unlock_contract | 5 |
| `spec_promotion_gates.py` | promotion_gates_contract | 7 |
| `spec_data_model.py` | data_model | 8 |
| `spec_agent_contracts.py` | agent_contracts | 8 |
| `spec_api_reference.py` | api_reference | 7 |
| `spec_data_layer.py` | data_layer | 10 |
| `spec_operator_loop.py` | operator_layer + PI_OPERATOR_DESIGN_DECISIONS | 7 |
| `spec_subsystem_observers.py` | EQUITY_INTEGRATION_DESIGN + PREDICTION_MARKET_DESIGN | 8 |

另有 `test_architecture_fitness.py`（依赖方向 fitness functions）独立于 claim 体系。

**验证命令**: `uv run pytest tests/specs/ tests/test_architecture_fitness.py -q`（< 2s）

---

## 缺口(待补,见 [DOC_SYSTEM_PLAN §3](../product/DOC_SYSTEM_PLAN.md))

批次6 已补全知识、调度器、审计链三份契约，新增四份 spec_*.py（32 新增 claims）。

当前已知缺口：
- P2: `PREDICTION_MARKET_DESIGN` 契约仅观察阶段，spec_subsystem_observers.py 中对应 claims 标记 `status="missing"`，待 Polymarket/Kalshi 集成实现后升至 L2b。
- P3: `ui_design_system` 无对应 spec_*.py — 前端设计规范可执行性低，暂不强制。
- P3: `RegimeConfig` / `GuardConfig` / `MarketAwareExitsConfig` 三个 config 类无前端控制（见 SYSTEM_MAP §五）。

---

## 用法

- **实现某功能前**:先在本索引找对应契约,以它为"该是什么样"的依据。
- **发现代码与契约不符**:文档为准——记 [issue](../issues/BACKEND_ISSUES.md),不擅自改契约迁就代码。
- **契约确需改**:触及信任边界的(risk/execution/live)按 [trust §6](../governance/trust_and_change_control.md) change control 流程。
- **新功能开发**:走 [SDD 六阶段](SDD_METHODOLOGY.md) → Constitution → Specify → Clarify → Plan → Tasks → Implement。
