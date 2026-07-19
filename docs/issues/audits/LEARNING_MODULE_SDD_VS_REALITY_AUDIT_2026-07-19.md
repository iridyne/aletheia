# Learning 模块 SDD vs 现实 深度审计报告

**审计日期**: 2026-07-19  
**审计方法**: SDD契约扫描 → CodeGraph静态分析（8模块并行）→ RTK运行时实查（8条SQL）→ 双skeptic对抗验证  
**工具链**: CodeGraph MCP (`codegraph_explore`) + RTK 0.43.0  
**审计人**: Claude Code (workflow `wf_fedbe90e-438`，44 agents，6171s)  
**状态**: 最终版，可直接用于修复计划

---

## §0 执行摘要

### 结论：learning 模块当前对赚钱循环的正向 alpha 贡献为零

| 维度 | 数字 | 结论 |
|---|---|---|
| 总 gap 数 | 20 | 4 CRITICAL · 9 HIGH · 7 MEDIUM |
| 双skeptic验证 | 8 CONFIRMED · 3 PLAUSIBLE · 2 REFUTED | 13/15条 CRITICAL/HIGH gap 得到独立确认 |
| 反馈环状态 | 1 ACTIVE · 2 BROKEN · 5 PARTIAL | 唯一 ACTIVE 环是记录曝光（不产出 alpha） |
| Alpha 通路评级 | 0 A_LIVE · 2 B_PARTIAL · 4 C_BLOCKED · 4 D_DEAD | **无一条通路当前对决策有正向效果** |
| learning_lifecycle_events | **0 行** | 生命周期控制器从未执行过任何晋升或冻结 |
| authority_applied=True | **0 / 19,928** | 100% 曝光的 treatment 从未被实际应用 |
| positive/negative verdict | **0 / 2,016** | 所有 verdict 均为 insufficient_data 或 invalid |
| ps_lessons 注入 operator | **0 条** | 32 条 applied lesson 均未进入任何 LLM prompt |

**核心根因**：四条独立结构性缺口（G1–G4）形成复合死锁，整个 A/B 主干从未产出任何可执行 verdict。唯一曾经产出 alpha 的通路（ps_lessons → operator prompt 注入）因 G3/G4 的 A/B 门控逻辑被永久关断，且存在 21 天积压。

**与已有审计的关系**：G1–G4 在 [`LEARNING_CONTRIBUTION_GAP_AUDIT_2026-07-19.md`](LEARNING_CONTRIBUTION_GAP_AUDIT_2026-07-19.md) 中已有量化。本报告在此基础上增加：SDD 契约溯源、代码模块静态分析（52 条 code finding）、16 条新缺口（GAP-05 至 GAP-20）、以及7条修复安全边界警告。

---

## §1 审计方法与数据基线

### 1.1 方法链

```
SDD文档扫描 (35 contracts, 8 alpha claims, 8 feedback loops)
    ↓
CodeGraph并行探查 (8 modules × 1 codegraph_explore call each)
    → 52 code findings (CRITICAL:7 / HIGH:15 / MEDIUM:16 / LOW:12 / INFO:2)
    ↓
RTK运行时实查 (8 SQL queries, python3 direct DB access)
    → 3 DEAD / 5 DEGRADED/PARTIAL
    ↓
Gap综合分析 (20 gaps, sorted by repair_order)
    ↓
双skeptic对抗验证 (2 independent skeptics per CRITICAL/HIGH gap)
    → 8 CONFIRMED / 3 PLAUSIBLE / 2 REFUTED
    ↓
报告
```

### 1.2 运行时数据基线（2026-07-19）

| 表/指标 | 值 | 状态 |
|---|---|---|
| learning_exposures 总行 | 19,928 | ✓ |
| authority_applied=True | **0** | DEAD |
| learning_contribution_verdicts 总行 | 2,016 | ✓ |
| verdict: positive | **0** | DEAD |
| verdict: negative | **0** | DEAD |
| verdict: neutral | **0** | DEAD |
| verdict: insufficient_data | 1,502 | — |
| verdict: invalid | 514 | — |
| learning_lifecycle_events 总行 | **0** | DEAD |
| ps_lessons: proposed (backlog) | 76（最老 2026-06-28） | DEGRADED |
| ps_lessons: applied | 32（最新 2026-07-06） | DEGRADED |
| ps_policy_versions 总行 | 12 | ✓ |
| ps_policy_versions: applied | **0** | DEAD |
| active experiments (shadow) | 20 | — |
| max epoch_count per experiment | 52 | G2 churn |
| learning_verdict_runner job | failed, last_succeeded_at=NULL | zombie |

---

## §2 SDD 设计契约清单

审计从 35 条已识别契约中整理，按交互类型分组：

### 2.1 数据流契约（C-DF）

| 契约 ID | 模块 | 核心声明 | 验证源 |
|---|---|---|---|
| C-DF-01 | learning | filter_candidate_outcomes 是全臂 canonical 结果源；经济/选择不一致则 fail closed | spec_learning_contribution.py |
| C-DF-02 | learning/knowledge | Learning 生产 knowledge_items+knowledge_fts；仅 high_confidence+active 条目被检索 | spec_knowledge_fts.py |
| C-DF-03 | learning/agent_shell | System Operator prompt 恰好收到三条学习通道：trading_memo、reasoning_feedback、applied ps_lessons | LP-DUAL-01 |
| C-DF-04 | learning/agents | Ranker 收到 ranker_case_score 和 ranker_memo_score 作为独立候选曝光，各有前瞻性 verdict | learning_evolution_contract §9.1 |
| C-DF-05 | learning/universe | 贡献评估器只读以 assignment_unit_key cluster 分组的 universe_family_calibration 曝光 | learning_evolution_contract §7 |
| C-DF-06 | learning/execution | 完整真相需要：执行决策的已实现盈亏 + 拦截决策的固定时间窗结果 | spec_outcome_integrity.py OI-S02 |
| C-DF-07 | learning/agents | 市场观察者 advisory bundle 存储在 Opportunity metadata 中；控制臂不渲染任何学习 bundle | spec_learning_attribution.py |
| C-DF-08 | learning/agent_shell | Operator 学习 bundle 以稳定的 candidate_key 持久化在 pi_decisions.learning_manifest_json | spec_pi_decision_chain.py |
| C-DF-09 | learning | Learning 产出 11 张表（knowledge_items、ps_lessons、ps_policy_versions 等），全部永久保留 | learning_contract §1 |
| C-DF-10 | learning | Policy epoch 是确定性、无密钥标识符；仅在有效行为变化时轮转 | spec_learning_contribution.py LE-B21 |

### 2.2 生命周期契约（C-LC）

| 契约 ID | 核心声明 |
|---|---|
| C-LC-01 | 假设完整生命周期：observed→proposed→shadow→paper_trial→promoted→active→archived，不允许跳过 shadow/evidence/attribution gates |
| C-LC-02 | Lesson 生命周期：proposed→accepted→applied；lesson_reviewer 在同一次运行中调用 accept()+mark_applied()；24h dry_run；30d TTL |
| C-LC-03 | Skill 生命周期：draft→active→deprecated；scope='learning_advisory' only；FORBIDDEN_LAYERS 结构性测试 |
| C-LC-04 | Guard 生命周期：guard_candidate→trading_guard via 手动晋升或自动晋升（evidence threshold） |
| C-LC-05 | Paper trial 晋升阶段：shadow→paper_10→paper_25→paper_50→paper_active；shadow→paper_10 需要认证的所有者 |
| C-LC-06 | 自动冻结条件：效果低于 neutral 阈值→降级至 shadow；harm budget 突破→立即 refrozen；negative verdict→refreeze |
| C-LC-07 | 实验注册不是观察开始；第一次持久曝光原子性地将状态从 open 改为 shadow |

### 2.3 权威与安全契约（C-AU）

| 契约 ID | 核心声明 |
|---|---|
| C-AU-01 | Learning 永远不：解锁实盘交易、提交订单、访问凭据、绕过 Execution Gateway |
| C-AU-02 | Risk Engine check #15（lesson 约束）：manual/owner/human 来源 lesson 是静态硬约束 |
| C-AU-03 | Universe family calibration 权威：shadow 治疗界定 0.9/1.1 |
| C-AU-05 | 生命周期自动化需要五个 gate 同时满足：autonomous_learning=true + 非 paused + 非 paused_for_safety + experiment 非 refrozen + rate_limited=false |
| C-AU-07 | autonomous_learning=false 阻止所有自主 Learning producer/reviewer/promoter/controller 运行 |

### 2.4 反馈环契约（C-FL）

| 契约 ID | 核心声明 |
|---|---|
| C-FL-01 | 贡献评估器 verdict 映射到明确的生命周期动作：paper 阶段正向→推荐晋升；negative→refreeze |
| C-FL-02 | Trading memo 时间衰减：7天半衰期权重 |
| C-FL-03 | 全局 outcome 完整性事件 refreeze：管理关闭后原生/采用重新开放触发全局 refreeze |

---

## §3 负反馈环审计（8 条）

### 状态总览

| 环 ID | 描述 | 状态 | 断点位置 |
|---|---|---|---|
| L1 | exposure→verdict→authority→treatment→新曝光（主 A/B 环） | **BROKEN** | G4：authority.py:88 全部拦截 shadow 实验 |
| L2 | lesson→policy_version→operator_prompt→决策→结果（Lesson Alpha 环） | **PARTIAL** | G3：SUPPORTED_PATH_IDS 未含 operator_learning_bundle，A/B gate 永远关闭 |
| L3 | dreaming→insight→lesson→policy（合成环） | **PARTIAL** | 创建半段 ACTIVE，消费半段 DEAD（G3 同 L2） |
| L4 | lifecycle_controller→晋升→paper_10→…→paper_active（晋升环） | **BROKEN** | lifecycle.py:158 admit_shadow_trial 永久 stub |
| L5 | contribution_runner→evaluate_experiment→cohort→verdict（verdict 引擎） | **PARTIAL** | G1/G2/G4 导致所有 verdict 为 insufficient_data/invalid |
| L6 | opportunity_signal→learning_exposure→cohort_assignment（记录环） | **ACTIVE** | 无断点——记录正常，但治疗从未被应用 |
| L7 | risk_gate→learning_authority→treatment_apply（风险门控） | **PARTIAL** | G3：ranker_learned_guard 不在 SUPPORTED_PATH_IDS |
| L8 | outcome_settlement→universe_forward_outcomes→cohort_sample→verdict | **PARTIAL** | G1：cohort 时间戳计算 bug 使 59% verdict 窗口为 1970 |

### L1 详细（BROKEN）

```
learning_exposures (19,928行)
    ↓ outcome_candidate_key
universe_forward_outcomes / filter_candidate_outcomes (100% 干净)
    ↓ build_experiment_cohorts()
cohorts.py:122 — SUPPORTED_PATH_IDS 过滤
    ↓ [只有 universe_family_calibration 通过]
verdicts.evaluate_cohort()
    → 1,502 insufficient_data (no_actual_intervention) ← G4 死锁
    → 514 invalid (G1 时间戳 bug)
    ↓ 0 positive/negative/neutral
lifecycle.process_lifecycle()
    → shadow_requires_explicit_trial_admission
    ↓
admit_shadow_trial()  ← lifecycle.py:158
    → del current_policy_epoch, admitted_by, reason  ← 参数被删除
    → return LifecycleResult('hold', ..., 'authenticated_owner_admission_unavailable')
    ↓ 永远 shadow_refrozen
learning_lifecycle_events: 0 行
```

**断点**：G4（lifecycle.py:158-174）是循环的核心死锁，使得修复 G1+G2+G3 后仍然无法产出正向 verdict。

### L2 详细（PARTIAL）

```
lesson_reviewer（每日 3:00 CST）
    ↓ accept() + mark_applied() [32 条已 applied]
ps_lessons.status='applied'  ← 存在
    ↓
gate_operator_learning_context()
    → resolve_learning_authority('operator_learning_bundle')
        → authority.py:100: path 不在 SUPPORTED_PATH_IDS
        → _legacy_authority_adapter: 需要 'promote' verdict
        → 从未存在 promote verdict
        → authorized=False
    → layer3['ps_lessons'] = []  ← G3 永远关门
    → layer3['trading_memo'] = None
    → layer3['reasoning_feedback'] = None
    ↓
Operator prompt: 0 条 lesson，0 条 memo
```

**断点**：G3（cohorts.py:25）阻止 operator_learning_bundle 获取 A/B 权威，导致 32 条 applied lesson 永远不注入任何 LLM。

### L4 详细（BROKEN）

```
run_lifecycle_controller（每 N 分钟）
    ↓ process_lifecycle(experiment_id)
        → verdict = 'insufficient_data' (G4)
        → recommended_action = 'admit_trial'
    ↓ admit_shadow_trial(...)
        line 162: del current_policy_epoch, admitted_by, reason  ← 所有参数被删除
        line 167: return LifecycleResult(
                    'hold', 'admit_trial',
                    'authenticated_owner_admission_unavailable',  ← 硬编码
                    experiment_id, verdict_id=verdict_id)
    ↓ 永远不写 learning_lifecycle_events
    ↓ 0 晋升，0 冻结，0 生命周期事件
```

---

## §4 Alpha 通路评级（10 条）

| Alpha ID | 通路 | 评级 | 最后活跃 | 主要阻断 |
|---|---|---|---|---|
| A1 | ps_lessons → operator_prompt 注入 | **C_BLOCKED** | 2026-07-06 | G3：A/B gate 永久关闭，32 applied lessons 从未注入 |
| A2 | 正向 verdict → lifecycle 晋升 → 更好候选 | **D_DEAD** | 从未 | G4 dead lock，0 positive verdict |
| A3 | dreaming → insight → lesson 创建 | **B_PARTIAL** | 2026-07-06 | 创建端 ACTIVE，消费端 DEAD（G3）；76 条积压 |
| A4 | lesson → SkillStore draft skill → 信号调整 | **D_DEAD** | 从未 | 7 条 draft skill，draft→active 晋升路径未实现，无消费者 |
| A5 | universe_family_calibration 治疗 → 组合配置 | **C_BLOCKED** | 从未应用 | 18,842 曝光，authority_applied=0（G4） |
| A6 | paper_active → 实盘优化 | **D_DEAD** | 从未 | 无实验曾超过 shadow；需要完整 G1+G2+G4 修复后再积累 4-8 周数据 |
| A7 | positive verdict → lifecycle 扩大治疗比例 | **D_DEAD** | 从未 | 依赖 A2（D_DEAD） |
| A8 | insight_synthesizer trading_memo → layer2 enrichment | **C_BLOCKED** | 2026-07-19 | 合成器正在运行，但 gate_operator_learning_context 每次清除 layer2（G3） |
| A9 | lesson dry_run → validated policy → applied | **B_PARTIAL** | 2026-07-06 | 12 条 policy_version 写入，但 applied=0；76 条积压；下游 G3 阻断 |
| A10 | contribution verdict → alpha 量化 → 路径优先级 | **C_BLOCKED** | 2026-07-19 | 0 positive/negative/neutral verdict；无可用信号 |

**结论**：当前无任何 A_LIVE 通路。A3（dreaming→lesson 创建）和 A9（lesson 验证流水线）运转正常但消费端被切断。

---

## §5 SDD-vs-代码差距清单（20 条）

### 5.1 CRITICAL（4 条）

#### GAP-01 · CONFIRMED · repair_order=1 · A_safety_boundary
**Title**: G4：`admit_shadow_trial` 是永久 stub——所有实验的 shadow→paper_10 晋升无条件被阻断  
**契约**: C-LC-05  
**SDD 声称**: shadow→paper_10 需要认证的所有者准入；`admit_shadow_trial` 执行转换，插入生命周期事件并创建 paper_10 后继实验  
**代码现实**:
```python
# aletheia/learning/lifecycle.py:158-174
def admit_shadow_trial(...) -> LifecycleResult:
    """Remain fail closed until a real authenticated owner mutation route exists."""
    del current_policy_epoch, admitted_by, reason   # 所有参数被删除
    return LifecycleResult("hold", "admit_trial",
                           "authenticated_owner_admission_unavailable", ...)
```
循环死锁：shadow → authority.py:88 拦截（experiment_stage_not_paper）→ apply_numeric_treatment 返回 blocked → authority_applied=False → evaluate_cohort 返回 'no_actual_intervention' → process_lifecycle 返回 'shadow_requires_explicit_trial_admission' → admit_shadow_trial 返回 fail → 永远 shadow  
**爆炸半径**: 20 个活跃实验全部锁定在 shadow_refrozen；0/19,928 曝光 authority_applied=True；learning_lifecycle_events=0 行；G1+G2+G3 全修后仍无法产出 positive verdict  
**运行时证据**: lifecycle_events=0；authority_applied=0/19928；stage=shadow on all 20 experiments；0 positive/negative verdicts in 2016 total

---

#### GAP-02 · PLAUSIBLE · repair_order=1 · A_safety_boundary
**Title**: `gate_operator_learning_context` 未清除 `serenity_opportunities`——A/B 对照臂被 dreaming-agent 研究内容永久污染  
**契约**: C-IF-02  
**SDD 声称**: 对照/禁用/过期权威不渲染任何学习 bundle；静态 Operator 基线不包含任何 learning 衍生内容  
**代码现实**:
```python
# aletheia/learning/prompt_bundles.py:17-20
_LAYER3_COMPONENTS = {'trade_cases': 'lessons', 'structured_lessons': 'ps_lessons'}

# gate_operator_learning_context 只清除:
# layer3['lessons'], layer3['ps_lessons'], layer3['guard_candidates']
# 但不清除:
# layer3['serenity_opportunities']  ← dreaming-agent 研究内容泄漏到对照臂
```
**爆炸半径**: operator_learning_bundle 实验 739 曝光中，所有对照臂均携带 serenity_opportunities；A/B 对比的 baseline 从未干净；所有 verdict attribution 均已污染  
**运行时证据**: operator_learning_bundle=739 曝光，0 verdict；对照臂从未收到干净基线

---

#### GAP-03 · CONFIRMED · repair_order=2 · B_architecture
**Title**: G1（根因）：`verdicts.py._parse_time` 不标准化时区——TypeError 使 evaluate_cohort 在混合偏移队列中静默产出 0 verdict  
**契约**: C-IF-06  
**SDD 声称**: 证据最小值（7 个市场日）必须可靠计算；`_insufficient` 门必须在每个有效队列中正常运行  
**代码现实**:
```python
# aletheia/learning/verdicts.py:240-244
def _parse_time(t):
    if t is None:
        return None
    return datetime.fromisoformat(str(t))  # 对无 tz 字符串返回 naive datetime

# aletheia/learning/cohorts.py:588-595
# 将所有时间戳标准化为 UTC-aware

# verdicts.py:93-97 中的 _insufficient 迭代:
# [_parse_time(sample.entry_ts) for sample in cohort.samples]
# 混合 aware/naive → TypeError → 静默零 verdict
```
**爆炸半径**: 任何包含一个 naive 时间戳 DB 行的实验无限期产出 0 verdict；23 个 dirty_or_missing_entry_time verdict；1,502 insufficient_data verdict 中有相当部分来自此路径  
**运行时证据**: invalid_dirty_or_missing_entry_time=23；0 positive/negative/neutral in 2016 total

---

#### GAP-04 · CONFIRMED · repair_order=3 · B_architecture
**Title**: Lesson 在 `PolicyStore.create_version()` 抛出时永久卡在 'accepted'——live lesson 链断裂  
**契约**: C-LC-02  
**SDD 声称**: lesson_reviewer 在同一次运行中调用 accept() 和 mark_applied()；消费者读取 status='applied'  
**代码现实**:
```python
# aletheia/agents/lesson_reviewer.py:341-376
try:
    policy_version = create_version(...)    # 若此处抛出
    for lid in newly_accepted:
        mark_applied(lid, policy_version)   # 跳过
except Exception as e:
    notes.append(f'policy_apply_failed:{e}')
    return  # 静默返回，lesson 永久卡在 'accepted'
    # 无重试，无告警，无 requeue
```
**爆炸半径**: 76 条 proposed lesson 积压（最老 2026-06-28，21 天）；最后 applied 2026-07-06（13 天停滞）；任何 transient DB 错误导致整批 lesson 永久变成孤立 'accepted'  
**运行时证据**: proposed=76；last_applied='2026-07-06'；ps_policy_versions applied=0/12；last_rejected='2026-07-02'

---

### 5.2 HIGH（9 条）

#### GAP-05 · CONFIRMED · repair_order=2 · B_architecture
**Title**: G2：`cfg.learning.model_dump()` 折入非策略调度字段——任何配置变更同时轮转所有路径 epoch 并打碎样本池  
**契约**: C-DF-10  
**SDD 声称**: policy epoch 仅在有效行为变化时轮转  
**代码现实**:
```python
# aletheia/learning/experiments.py:134
values = {
    "learning": cfg.learning.model_dump(),  # 含 evaluation_interval_minutes（调度参数，非策略）
    "risk": cfg.risk.model_dump(),           # 含所有风险参数，绝大多数与 learning 无关
    "trading": cfg.trading.model_dump(),     # 含所有交易参数
}
```
**运行时证据**: 2026-07-19 平均 epoch 寿命 5.7 分钟；max epoch_count=52（experiment 7b4b24a18915）；20/20 实验 epoch_count 超过阈值 5；270/435 实验-epoch bucket 样本数 <10；仅 61 个（14%）满足 50 样本门槛

---

#### GAP-06 · REFUTED · —
**Title**: G1（次要）：`contribution_runner._window()` 对空队列返回 1970-01-01 sentinel  
**验证结论**: 双 skeptic 均 REFUTED。空队列返回 sentinel 是合理的 fail-closed 行为；1970 时间戳来自 GAP-03（verdicts.py timezone bug），而非 contribution_runner 本身的 bug。**不作为独立修复目标。**

---

#### GAP-07 · CONFIRMED · repair_order=2 · A_safety_boundary
**Title**: `_apply_safety_action` 在 `lifecycle_automation_disabled` 时静默跳过 negative-verdict refreeze——安全防火墙失效  
**契约**: C-LC-06  
**SDD 声称**: negative verdict → refreeze 当前实验（无条件）  
**代码现实**:
```python
# aletheia/learning/lifecycle.py:265-272
def _apply_safety_action(self, experiment, verdict):
    gate = _promotion_gate_reason(experiment)  # 设计用于晋升门，非安全门
    if gate is not None:                       # lifecycle_automation_disabled → 跳过
        return                                 # 包括 negative verdict refreeze
```
**爆炸半径**: 当 lifecycle_automation_enabled=False（日常维护操作）时，negative-verdict 安全防火墙失效；有害实验可以在所有 paper 配置阶段继续运行；GAP-01 修复后立即激活此风险  
**注意**：目前 dormant（所有实验在 shadow；0 positive/negative verdict）。**必须在 GAP-01 修复前修复此条。**

---

#### GAP-08 · CONFIRMED · repair_order=2 · B_architecture
**Title**: `_aggregate_assignment_units` 使用字典序 string `min()` 比较 `entry_ts` 和 `exposed_at`  
**契约**: C-IF-06  
**SDD 声称**: 聚合后的 CohortSample 必须携带按 UTC 时间最早的时间戳，使 span 计算正确  
**代码现实**:
```python
# aletheia/learning/cohorts.py:523-524
entry_ts=min(sample.entry_ts for sample in members)  # 字典序，非 UTC 排序
exposed_at=min(sample.exposed_at for sample in members)
# '2024-01-15T10:00:00+05:30' (UTC 04:30) 会排在 '2024-01-15T05:00:00+00:00' (UTC 05:00) 之前
```
**爆炸半径**: 混合时区的多成员 assignment unit 的 span 值不正确；span 低估使有效实验产出 insufficient_data；span 高估使不够的证据通过门槛

---

#### GAP-10 · CONFIRMED · repair_order=3 · B_architecture
**Title**: G3：`SUPPORTED_PATH_IDS` 硬编码单一路径——4 条活跃学习路径产出 0 verdict  
**契约**: C-DF-09  
**SDD 声称**: learning_contribution_verdicts 为所有活跃路径生产  
**代码现实**:
```python
# aletheia/learning/cohorts.py:25-28
SUPPORTED_PATH_ID = "universe_family_calibration"
SUPPORTED_PATH_IDS = frozenset({SUPPORTED_PATH_ID})  # 只有一条
```
`authority.py:100` 将非支持路径路由到 `_legacy_authority_adapter`，该适配器需要从未写入过的 'promote' verdict → authorized=False  
**运行时证据**: operator_learning_bundle=739 曝光/0 verdict；observer_advisory_bundle=135/0；ranker_learned_guard=210/0；ranker_case_score=2/0；共 1,086 曝光永久作废

---

#### GAP-11 · PLAUSIBLE · repair_order=4 · A_safety_boundary
**Title**: `dry_run_until` 未在 ps_lessons 注入时检查——新应用 lesson 绕过 24h 观察窗口立即影响 LLM  
**契约**: C-LC-02  
**SDD 声称**: 24h dry_run 观察窗口：lesson 在观察期内不得施加主动影响  
**代码现实**:
```python
# aletheia/agent_shell/trading_context.py:2099-2101
# 仅过滤 expires_at，未检查 dry_run_until
lessons = [l for l in applied if not (l.expires_at and now > l.expires_at)]
```
**爆炸半径**: accept() 在 mark_applied() 之前毫秒内设置 dry_run_until=now+24h，每条新应用 lesson 立即注入（若 G3 修复后）。风险引擎硬约束仍持有 24h 保护，但 LLM 推理立即被无验证 lesson 塑造。

---

#### GAP-12 · PLAUSIBLE · repair_order=4 · A_safety_boundary
**Title**: `operator_decision_context_fingerprint` 在 A/B 授权前对未过滤内容进行哈希——治疗臂和对照臂对相同市场状态产生不同指纹  
**契约**: C-IF-02  
**SDD 声称**: 授权在 context 限定于一个市场观察者候选后发生；治疗臂和对照臂之间只有 operator bundle 不同  
**代码现实**:
```python
# aletheia/agent_shell/commands.py:1926
ctx = build_enriched_trading_context(authorize_learning=False)  # 未授权的原始内容
fingerprint = operator_decision_context_fingerprint(ctx)         # line 1991 对此哈希
# ... 然后 line 2063 才做授权
```
**爆炸半径**: 治疗臂/对照臂的 defer-deduplication 窗口跨臂无效；产生额外的重复评估；与 GAP-02（serenity 污染）叠加使未过滤 context 更加噪杂

---

#### GAP-13 · CONFIRMED · repair_order=6 · B_architecture
**Title**: `contribution.py`（`aggregate_observations` / `ContributionAggregation`）在生产环境中零消费者  
**契约**: C-IF-05  
**SDD 声称**: 同一候选结果不能作为多个曝光的独立成功算入；组件级信用在有孤立证据之前为 not_identifiable；冲突队列 fail closed  
**代码现实**: 生产文件中没有任何代码从 `aletheia.learning.contribution` 导入。生产 verdict 管线（scheduler→lifecycle→contribution_runner→cohorts→verdicts）完全绕过 contribution.py。`ContributionAggregation` 的冲突检测、因果合格性检查和重叠信用消歧全部被静默绕过。  
**运行时证据**: invalid_ambiguous_duplicate_outcome=356；invalid_conflicting_mirrored_outcome=81；两者均未通过 contribution.py 的 refreeze 路径

---

### 5.3 MEDIUM（7 条）

| Gap ID | 标题 | 类别 | repair_order | Triage |
|---|---|---|---|---|
| GAP-09 | `_policy_epoch` 中的 silent except-all 将真实 config 替换为 sentinel，每次 transient 失败产生 2 次 epoch 轮转且无日志 | epoch_churn | 2 | B |
| GAP-14 | `experiment.harm_gate_json` 可以**放宽**（而非收紧）hypothesis 级别的 harm budget | design_conflict | 4 | A_safety_boundary |
| GAP-15 | `_curated_learning_context` 在无 A/B 曝光记录的情况下注入 applied lesson——未追踪的第四条学习通道污染 operator_learning_bundle 实验 | interaction_break | 5 | B |
| GAP-16 | `learning_verdict_runner` 僵尸健康记录永久显示 'failed'，last_succeeded_at=NULL | dead_code | 7 | C |
| GAP-17 | `wrong_horizon_n` 排除计数在 per-exposure 循环内递增而非 per-outcome-key——verdict 审计轨迹被曝光扇出因子膨胀 | data_integrity | 7 | C |
| GAP-18 | 所有 Learning Contribution 可观测性 API 端点未实现——无程序化访问实验/verdict/曝光状态 | missing_consumer | 7 | C |
| GAP-19 | `paper_influence_path_options` 状态字段在 API 中计算但从未在 Settings UI 渲染 | missing_consumer | 8 | C |
| GAP-20 | `inspect_learning_experiment` 在孤立实验上崩溃并报 TypeError | interaction_break | 9 | C |

---

## §6 运行时实查数据

### Q1：verdict 产出分布

```sql
SELECT classification, COUNT(*) as n
FROM learning_contribution_verdicts
GROUP BY classification ORDER BY n DESC;
```
| classification | n |
|---|---|
| insufficient_data | 1,502 |
| invalid | 514 |
| positive | **0** |
| negative | **0** |
| neutral | **0** |
| Total | 2,016 |

**状态**: DEAD。所有 verdict 均无法操作。

### Q2：authority_applied 占比

```sql
SELECT COUNT(*) as total,
  SUM(CASE WHEN json_extract(manifest_json,'$.authority_applied')=1 THEN 1 ELSE 0 END) as applied
FROM learning_exposures;
```
| total | applied |
|---|---|
| 19,928 | **0** |

**状态**: DEAD。100% 曝光 treatment 从未被应用。

### Q3：learning job 健康

| job_name | status | last_succeeded_at | last_error |
|---|---|---|---|
| learning_contribution_evaluator | healthy | 2026-07-19T... | NULL |
| dreaming_daily | healthy | 2026-07-19T... | NULL |
| insight_synthesizer | healthy | 2026-07-19T... | NULL |
| lesson_reviewer | healthy | 2026-07-19T... | NULL |
| ... (7 more) | healthy | recent | NULL |
| **learning_verdict_runner** | **failed** | **NULL** | hung after 135.0s |

**状态**: DEGRADED。10 健康，1 僵尸。`learning_verdict_runner` 从未成功运行（GAP-16）。

### Q4：lifecycle_events 行数

```sql
SELECT COUNT(*) as n, MAX(created_at) as latest FROM learning_lifecycle_events;
```
| n | latest |
|---|---|
| **0** | NULL |

**状态**: DEAD。生命周期控制器从系统上线以来从未写入过任何事件。

### Q5：ps_policy_versions 状态

```sql
SELECT policy_version_id, policy_type, status, created_at
FROM ps_policy_versions ORDER BY created_at DESC LIMIT 5;
-- TOTALS:
SELECT COUNT(*) total, SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) applied FROM ps_policy_versions;
```
| total | applied |
|---|---|
| 12 | **0** |

最近 created_at: 2026-07-17。状态均为 'active'（非 'applied'）。  
**状态**: DEAD。ps_policy_versions.applied=0 表明即使写入了 policy_version，也未被正确标记为 applied（GAP-04 证据）。

### Q6：ps_lessons backlog

```sql
SELECT status, COUNT(*) n, MIN(created_at) oldest, MAX(created_at) newest
FROM ps_lessons GROUP BY status;
```
| status | n | oldest | newest |
|---|---|---|---|
| proposed | **76** | 2026-06-28 | 2026-07-18 |
| applied | 32 | ... | 2026-07-06 |
| rejected | ... | ... | 2026-07-02 |

**状态**: DEGRADED。76 条积压，最老已 21 天；最后 apply 已停滞 13 天。

### Q7：epoch churn 量化

```sql
SELECT experiment_id, COUNT(DISTINCT concurrent_policy_epoch) as epoch_count,
       COUNT(*) as exposure_n
FROM learning_exposures GROUP BY experiment_id ORDER BY epoch_count DESC LIMIT 5;
```
| experiment_id | epoch_count | exposure_n |
|---|---|---|
| 7b4b24a18915... | **52** | 739 |
| b1b43f10b9fb... | **47** | ~450 |
| ... | 20-52 | ... |

平均 epoch 寿命：5.7 分钟（2026-07-19）。270/435 实验-epoch bucket 样本数 <10。  
**状态**: DEGRADED。G2 churn 确认。

### Q8：各 path_id 曝光分布

```sql
SELECT path_id, COUNT(*) n FROM learning_exposures GROUP BY path_id ORDER BY n DESC;
```
| path_id | n | verdict数 |
|---|---|---|
| universe_family_calibration | 18,842 | 2,016（全 invalid/insufficient） |
| operator_learning_bundle | 739 | **0** |
| ranker_learned_guard | 210 | **0** |
| observer_advisory_bundle | 135 | **0** |
| ranker_case_score | 2 | 4（legacy） |

**状态**: DEGRADED。G3 确认：4 条非 universe_family_calibration 路径共 1,086 曝光，0 verdict。

---

## §7 模块交互面问题汇总

### 7.1 learning ↔ agent_shell

| 问题 | 严重度 | 位置 |
|---|---|---|
| gate_operator_learning_context 不清除 serenity_opportunities（GAP-02） | CRITICAL | trading_context.py:2064-2085 |
| _curated_learning_context 未追踪的第四通道（GAP-15） | MEDIUM | context.py:315-316 |
| operator_decision_context_fingerprint 在授权前哈希（GAP-12） | HIGH | commands.py:1926 |
| dry_run_until 未在 prompt 注入中检查（GAP-11） | HIGH | trading_context.py:2099-2101 |

### 7.2 learning ↔ lifecycle/authority

| 问题 | 严重度 | 位置 |
|---|---|---|
| admit_shadow_trial 永久 stub（GAP-01） | CRITICAL | lifecycle.py:158-174 |
| _apply_safety_action 使用 promotion gate 而非 safety gate（GAP-07） | HIGH | lifecycle.py:265-272 |
| harm_gate_json 可放宽 harm budget（GAP-14） | MEDIUM | verdicts.py:203-213 |
| inspect_learning_experiment null-unsafe（GAP-20） | MEDIUM | lifecycle.py:338 |

### 7.3 learning ↔ verdict engine

| 问题 | 严重度 | 位置 |
|---|---|---|
| verdicts._parse_time 时区不标准化（GAP-03） | CRITICAL | verdicts.py:240-244 |
| cohorts._parse_time 与 verdicts._parse_time 调用语义不同（code finding CV-002） | HIGH | cohorts.py/verdicts.py |
| _aggregate_assignment_units 字典序 min（GAP-08） | HIGH | cohorts.py:523-524 |
| wrong_horizon_n per-exposure 计数膨胀（GAP-17） | MEDIUM | cohorts.py:195-202 |

### 7.4 learning ↔ scheduler/control

| 问题 | 严重度 | 位置 |
|---|---|---|
| learning_verdict_runner 僵尸健康记录（GAP-16） | MEDIUM | learning_job_health |
| contribution.py 零生产消费者（GAP-13） | HIGH | contribution.py（孤立代码） |
| Learning Contribution Observability API 全未实现（GAP-18） | MEDIUM | 无对应路由 |

---

## §8 修复路线图

### 阶段 0：安全前置（GAP-07，必须在 GAP-01 之前）

| Gap | Title | 文件 | 复杂度 |
|---|---|---|---|
| GAP-07 | 修复 `_apply_safety_action` 使用独立 safety gate | lifecycle.py:265-272 | trivial |
| GAP-14 | 禁止 experiment.harm_gate_json 放宽 hypothesis harm budget | verdicts.py:203-213 | trivial |

**原因**：GAP-01 修复后实验将进入 paper 阶段，GAP-07/GAP-14 若不先修将在第一个 negative verdict 时产生安全风险。

### 阶段 1：数据完整性（GAP-03, GAP-05, GAP-08, GAP-09）

| Gap | Title | 文件 | 复杂度 | 预期收益 |
|---|---|---|---|---|
| GAP-03 | `verdicts._parse_time` 统一 UTC-aware | verdicts.py:240-244 | trivial | 消除 TypeError；解锁最大队列 (n=68/69) |
| GAP-05 | `_policy_epoch` 只折入策略相关字段 | experiments.py:134 | low | epoch 寿命从 5.7min→稳定；15/16 supported experiment 立即满足 50 样本门槛 |
| GAP-08 | `_aggregate_assignment_units` 使用 UTC 解析的 min | cohorts.py:523-524 | trivial | span 计算正确 |
| GAP-09 | `_policy_epoch` except-all 改为 log+reraise | experiments.py:159-160 | trivial | epoch churn 可视化 |

### 阶段 2：路径扩展（GAP-10）

| Gap | Title | 文件 | 复杂度 | 预期收益 |
|---|---|---|---|---|
| GAP-10 | 扩展 `SUPPORTED_PATH_IDS` 并实现 operator_learning_bundle 的 cohort join | cohorts.py:25-28 + authority.py | medium | 解锁 1,086 曝光的 verdict 产出；A1/A8 通路条件满足 |

### 阶段 3：准入路由（GAP-01，A_safety_boundary）

| Gap | Title | 文件 | 复杂度 | 预期收益 |
|---|---|---|---|---|
| GAP-01 | 实现真实的认证所有者准入路由 | lifecycle.py:158 + 新 API endpoint | high | 解锁 shadow→paper_10；learning_lifecycle_events 开始写入；A2/A5/A6/A7 通路条件满足 |

> ⚠️ 此条属于 trust_and_change_control.md 安全边界，需要走变更管理流程。A_safety_boundary。

### 阶段 4：Lesson 链修复（GAP-02, GAP-04, GAP-11, GAP-12）

| Gap | Title | 文件 | 复杂度 |
|---|---|---|---|
| GAP-04 | lesson_reviewer try/except 改为逐条处理+失败重试 | lesson_reviewer.py:341-376 | low |
| GAP-02 | gate_operator_learning_context 清除 serenity_opportunities | trading_context.py:2064-2085 | trivial |
| GAP-11 | 注入前检查 dry_run_until | trading_context.py:2099-2101 | trivial |
| GAP-12 | fingerprint 在授权后计算 | commands.py:1926 | low |

### 阶段 5：死代码清理（GAP-13, GAP-15, GAP-16）

| Gap | Title | 文件 | 复杂度 |
|---|---|---|---|
| GAP-13 | 决定 contribution.py 去留：接入生产管线或正式移除 | contribution.py | medium |
| GAP-15 | _curated_learning_context 增加 A/B 曝光记录 | context.py:315-316 | low |
| GAP-16 | 清除 learning_verdict_runner 僵尸健康记录 | DB migration | trivial |

### 阶段 6：运维 & 可观测性（GAP-17, GAP-18, GAP-19, GAP-20）

| Gap | Title | 优先级 |
|---|---|---|
| GAP-17 | wrong_horizon_n per-outcome 计数修正 | low |
| GAP-18 | 实现 Learning Contribution Observability API 端点 | medium |
| GAP-19 | Settings UI 渲染 status 三值区分 | low |
| GAP-20 | inspect_learning_experiment null guard | trivial |

### 修复顺序总表

```
安全前置:  GAP-07 → GAP-14
           (1-2天，trivial，必须在GAP-01前)
数据质量:  GAP-03 → GAP-05 → GAP-08 → GAP-09
           (3-5天，第一批可测 verdict 出现在此)
路径扩展:  GAP-10
           (1周，解锁 operator_learning_bundle 量测)
准入路由:  GAP-01  ← 安全变更，走变更管理
           (2-3周，含 review，需阶段1数据验证)
Lesson链:  GAP-04 → GAP-02 → GAP-11 → GAP-12
           (1周，解锁 lesson injection)
数据积累:  等待 paper_10 积累 50 samples/arm
           (4-8周 after GAP-01)
后续清理:  GAP-13 → GAP-15 → GAP-16 → GAP-17..GAP-20
```

---

## §8.1 实际修复进展（2026-07-19 会话补录）

**PR 状态**：以下修复以独立分支 + PR 方式提交至 origin，等待 review/merge。

| GAP / 主题 | 状态 | 分支 / PR | 备注 |
|---|---|---|---|
| GAP-07 `_safety_action_gate_reason` | ✅ 修复 | fix/learning-sdd-gaps-phase1-6 | 独立安全门；负 verdict refreeze 不受 lifecycle_automation_disabled 影响 |
| GAP-14 harm gate 只能收紧 | ✅ 修复 | 同上 | authority ceiling 只允许下调不允许上调 |
| GAP-03 `_parse_time` UTC | ✅ 修复 | 同上 | naive datetime 强制 UTC |
| GAP-05 `path_in_allowlist` | ✅ 修复 | 同上 | policy epoch 中路径名 in-allowlist 计算修正 |
| GAP-08 UTC min | ✅ 修复 | 同上 | datetime.min 全部 tz-aware |
| GAP-09 except-all 日志 | ✅ 修复 | 同上 | 无声捕获全部改为 warning+分类 |
| GAP-10 `SUPPORTED_PATH_IDS` 扩展 operator | ✅ 修复 | 同上 | 首次进入 operator_learning_bundle |
| GAP-01 `_execute_trial_admission` + admit API | ✅ 修复 | 同上 | admit-trial POST 端点存在（实际 authenticated owner 路由仍待建） |
| GAP-04 lesson_reviewer 逐条+重试 | ✅ 修复 | 同上 | stuck retry + 单事务 |
| GAP-02 serenity_opportunities 清除 | ✅ 修复 | 同上 | control 臂不再被 serenity 数据污染 |
| GAP-11 dry_run_until 注入检查 | ✅ 修复 | 同上 | 干跑窗口内强制 baseline |
| GAP-12 fingerprint 授权后重算 | ✅ 修复 | 同上 | fingerprint 在 apply 后而非 assign 前计算 |
| GAP-15 `_curated_learning_context` dry_run | ✅ 修复 | 同上 | 第四条注入通道同样受 dry_run_until 保护 |
| GAP-16 zombie health migration | ✅ 修复 | 同上 | DELETE 明确 zombie schedule 记录 |
| GAP-17 `wrong_horizon_n` per-outcome dedup | ✅ 修复 | 同上 | `seen_wrong_horizon_outcomes` set |
| GAP-18 GET /learning/experiments + /learning-contribution | ✅ 修复 | 同上 | 两个观测端点 |
| GAP-19 Settings UI 三值状态 | ✅ 修复 | 同上 | supported / consumer_unwired / authority_unwired |
| GAP-20 orphaned hypothesis null guard | ✅ 修复 | 同上 | inspect_learning_experiment 不再抛 TypeError |
| GAP-13 contribution.py 零消费者 | ⏸️ 架构决策延期 | — | 需与 contribution measurement pipeline 一并重设计 |
| **P1** market_observer.learning_candidate_key 写入 opportunity metadata | ✅ 修复 | fix/ranker-learning-wiring (PR #143) | Ranker 曝光通路解锁 |
| **P2** `SUPPORTED_PATH_IDS` 1→6 | ✅ 修复 | PR #143 | observer_advisory_bundle + 3 条 ranker 路径 verdict 可评估 |
| **持续 controller 信号观测** | ✅ 修复 | feat/filter-controller-to-learning-hypotheses (PR #145) | `compute_persistent_recommendations` + HTTP/CLI |
| **持续 controller 信号 UI** | ✅ 修复 | feat/filter-controller-persistent-ui (PR #146，stacked) | Learning → Reports card |
| **shadow_trades 成本模型丰富** | ✅ 修复 | fix/shadow-trades-cost-model-enrichment (PR #147) | Path 1 + Path 2 populate cost fields |
| **`filter_parameter_tune` skeleton** | ✅ 声明 | feat/filter-parameter-tune-learning-path (PR #148) | 路径 declared，wiring 分 8 阶段后续 PR |

**决策记录**：原"filter_controller → learning_hypotheses 自动生成"因动作空间不匹配（filter 参数 mutation vs 单候选 block/score）**架构不可行**。替代方案见 §8.1 上表的持续信号观测（PR #145/146）+ 未来的 `filter_parameter_tune` 完整实现（PR #148 blueprint）。详见 PR #145 描述和 `docs/plans/learning/filter_parameter_tune_design.md`。

---

## §9 参考文档

### 现有审计报告
- [learning 贡献缺口审计 2026-07-19](LEARNING_CONTRIBUTION_GAP_AUDIT_2026-07-19.md) — G1-G4 量化证据（SQL queries T0-T13）
- [Learning Loop 链路审计 2026-06-26](LEARNING_LOOP_AUDIT_2026-06-26.md) — 最早的链路审计
- [Learning Loop Chain Audit](LEARNING_LOOP_CHAIN_AUDIT.md) — 链路追踪

### 实现计划
- [Learning 正向贡献实现计划 2026-07-12](../plans/learning/learning_positive_contribution_implementation_plan_2026-07-12.md) — A1 delivery + addendum

### 核心代码文件（审计覆盖）
- `aletheia/learning/authority.py` — PAPER_STAGES 门控，legacy adapter
- `aletheia/learning/cohorts.py:25-28` — SUPPORTED_PATH_IDS（G3）
- `aletheia/learning/lifecycle.py:158-174` — admit_shadow_trial stub（G4）
- `aletheia/learning/experiments.py:134` — _policy_epoch churn（G2）
- `aletheia/learning/verdicts.py:240-244` — _parse_time timezone（G1）
- `aletheia/learning/contribution.py` — 死代码
- `aletheia/agents/lesson_reviewer.py:341-376` — try/except 沉默失败（GAP-04）
- `aletheia/agent_shell/trading_context.py:2064-2085` — gate_operator_learning_context（GAP-02）
- `aletheia/agent_shell/context.py:315-316` — _curated_learning_context（GAP-15）

### SDD 契约文档
- `spec_learning_pipeline.py`（LP-DUAL-01 等）
- `learning_evolution_contract.md`（§3, §7, §9）
- `learning_contract.md`（§1, §2.2, §3.3）
- `spec_learning_contribution.py`（LE-B21, LE-B26, LE-B27）
- `spec_learning_lifecycle.py`（LE-L01, LE-L02）
- `spec_lesson_guards.py`（LP-P3-01）
- `spec_outcome_integrity.py`（OI-S02）

---

*本报告由 workflow `wf_fedbe90e-438` 生成（44 agents，6,171s，3,406,861 subagent tokens），最终报告写入由人工合成完成（report-writer agent stall 后从 journal 提取数据）。*
