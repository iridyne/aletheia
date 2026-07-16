# 02 — 早期高层实施草案（已部分取代）

> **2026-07-15 状态更新**：本文件保留为早期讨论记录。当前实施顺序、七项问题的完整
> 覆盖与验收以 [04_comprehensive_skip_remediation_plan.md](04_comprehensive_skip_remediation_plan.md)
> 为准。特别是 A1 的 `decision_evidence_epochs` 不实施：owner 已要求数据库重载以单一
> [恢复 SOP](../../../operations/runbooks/DATABASE_RELOAD_RECOVERY_SOP.md) 处理，而非新增专门 runtime
> feature。现有 `learning_policy_epochs` 仍负责任何 B 类行为版本的隔离。

> 分类：证据完整性与只读诊断部分可作为数据链修复；任何 PI context、conviction、skip
> 语义或候选放行的改变均为 **B 类新观察窗口**，必须创建新 policy epoch。

## 0. 目标、非目标与完成定义

### 目标

让系统能在不强迫交易的前提下，区分三种状态：

1. 有证据支持的 protective skip；
2. 值得研究但尚未改变行为的 overrestrictive suspect；
3. 因数据断链、epoch 混用或未结算而不能下结论的 skip-halt incident。

### 非目标

- 不删除 skip，不降低最低 conviction，不强制 proposal；
- 不修改 `aletheia/risk/`、`aletheia/execution/`、Live unlock 或交易所逻辑；
- 不把 skip 的理论结果伪装为订单 PnL；
- 不自动写配置、调参或启用 Learning influence。

### 完成定义

每个有方向 terminal PI skip 可审计、可结算、可按正确 epoch 分析；连续零 proposal
会产生诚实诊断；任何后续行为改动都只能来自独立、预登记的实验，而不是当前历史统计。

## 1. Phase A — Epoch 与事实模型（先写契约与 RED spec）

### A1. 增加证据边界，而不篡改历史

拟议新增 `decision_evidence_epochs`（名称待最终 schema review），每一行不可变，至少有：

- `evidence_epoch_id`、`started_at`、`closed_at`、`initiator`、`reason_code`；
- `reset_manifest`：哪些事实表被清空、哪些保留、保留数据是否可用于实时决策；
- `eligible_from` / `eligible_until` 与 `outcome_contract_version`；
- `code_commit` 与仅供审计的配置摘要（不含 secrets）。

“重载”必须显式创建 epoch，而不能依赖某张表的 row count 推断。旧 outcome/lesson/review
可保留为研究档案，但是否可作为新 epoch 的实时 PI 输入由 `reset_manifest` 明确声明。

### A2. Cohort identity

所有 skip 效果统计的最小 cohort key：

```text
policy_epoch + evidence_epoch + filter_name + stage + filter_version
+ scanner_family + direction + market_type + horizon
```

现有 `learning_policy_epochs` 继续处理代码和有效配置变化；新 evidence epoch 只处理数据
可比性边界。二者不可相互替代。

### A3. RED 规格

- reset 后保留的旧 forward outcome 不得默认为新 evidence epoch 的 PI edge；
- 没有成熟样本时返回 `insufficient_evidence`，不得返回负 edge；
- epoch 变更后旧 cohort 不能授权新 cohort 的任何影响；
- schema / writer 异常不能把“未评估”伪装为 `passed` 或 `protective`。

## 2. Phase B — direct skip 证据闭环（不改变决策）

在 PI 已作出 terminal direct skip 后，和终态写入保持同一提交边界，补写：

| 字段/事实 | 要求 |
| --- | --- |
| `candidate_key` | `pi_decision:<decision_id>` |
| `filter_name/stage` | 固定为 System Operator 的 action / conviction 评估身份 |
| `reason_code` | 枚举机器码；例如 `sideways_no_edge`、`family_edge_negative`、`borderline_conviction`、`combined_low_conviction` |
| `input_snapshot` | regime、family edge、score、初始/最终 conviction、epoch/version；不保存 prompt |
| `outcome_candidate_key` | 与该 `pi_decision` 一一连接 |
| outcome | 复用既有 15m/1h/4h/24h 固定时点、市场隔离、净成本与结算状态 |

只有方向、市场、决策时可见入场价齐全的 skip 才创建净结果。无方向或无候选的 skip 应明确
标记 `not_attributable`，而不是猜方向补数据。

## 3. Phase C — 只读 Skip Effectiveness Report 与 liveness

### C1. 报告状态机

| 状态 | 含义 | 行为权限 |
| --- | --- | --- |
| `insufficient_evidence` | 样本/覆盖/成熟度不足 | 保持当前行为；报告不足 |
| `protective_supported` | 同 cohort 下 skip 的净结果支持拦截 | 保持当前行为；不等于永久正确 |
| `overrestrictive_suspect` | 满足既有收缩净期望和对照门槛 | 只允许固定版本 replay |
| `data_quality_incident` | trace、epoch、价格或结算断链 | 创建诊断事件；不强制开仓 |

指标至少包括：first-exposure skip 数、成熟/未成熟/失败 outcome 数、覆盖率、平均/中位/
收缩净结果、pass/reject 对照、按 reason code 的占比、输入 evidence 的 epoch 分布。

### C2. `skip_halt_incident`

当 pending 队列持续有有效流量且 proposal 长期为零时，只有同时或任一明确证据异常才触发：

- direct skip 的 trace 或 outcome key 覆盖不足；
- outcome 超过 cadence 宽限仍未结算；
- PI 消费了不允许用于当前 epoch 的旧证据；
- 一个 reason code 在有足够样本前异常垄断；
- 结果报告将未成熟样本渲染为负 edge。

incident 的产物是 doctor/API/审计可见的原因、数量和最新事件时间。它不能修改 PI 或
把候选绕过 Risk。

## 4. Phase D — 受控研究：replay 后的前瞻实验

只有一个明确的 `overrestrictive_suspect` 通过固定版本、只读 replay 后才可提交实验计划。

预登记内容：

1. 一个单变量 treatment，例如“仅使用当前 evidence epoch 的成熟 family edge”；
2. 明确 control、纳入/排除条件、固定 horizon、净结果公式与最小样本；
3. `candidate_key` 稳定哈希 assignment、assignment salt 与概率；
4. `policy_epoch + evidence_epoch + filter_version + code_commit`；
5. shadow/paper 阶段、伤害门、停止/无支持/失效规则；
6. 真实 proposal、Risk、execution 漏斗只作为分层报告，不能被反事实结果替代。

初始实验保持 `production_mutation_allowed=0`。若它将来会改变 PI context，仍须先获
owner 对 B 类行为窗口的明确授权；Risk 与 Execution 继续是下游独立门。

## 5. Phase E — DCLS 与运营验收

最小确定性场景：

1. “交易表清空、forward outcomes 保留”后，旧 evidence epoch 不进入新 PI edge；
2. direct skip 一定得到 immutable trace 与唯一 outcome key；
3. 无成熟 outcome 只能是 `insufficient_evidence`；
4. 完整且负向的反事实结果支持连续 skip 时，不误报故障；
5. 缺 trace、异 epoch 输入、到期不结算时，产生 skip-halt incident；
6. first exposure 不重复计数，重试不污染 pass/reject cohort；
7. experiment assignment 稳定、版本/epoch 改变即失效，且不写生产配置。

建议依次交付：契约与 executable spec → migration/model/repository → PI trace writer →
outcome settler → 只读 report/doctor → DCLS fixtures → replay / experiment registration。
每一阶段独立 review；在 Phase D 前不改变任何 PI 判断行为。

## 6. 预期修改面（仅供估算，不是授权清单）

- 契约：`filter_evidence_contract.md`、`opportunity_outcome_contract.md`、
  `decision_lifecycle_contract.md`、`learning_contract.md`、data model/API reference（如新增表或端点）。
- 行为外的实现：filter evidence repository、outcome settler、doctor/analytics report、
  policy epoch/learning ledger 的 cohort 适配。
- PI 路径：仅 Phase B 的事实写入；任何 context 或 gate 改动必须独立列为 Phase D 的 B 类任务。
- 测试：`tests/specs/` 的新 claim、PI daemon 回归、filter outcome、DCLS control-loop fixture。
