# Operator 自监管整合 — 让顶层管理者读自己的诊断面

**状态**: 实施中(2026-06-28)。**owner 目标(/goal)**: 把 CLI/doctor 诊断面整合进金字塔顶层的管理者 agent,让它「自己监管自己」。
**关联**: [`PI_OPERATOR_DESIGN_DECISIONS.md`](../../design/operator/PI_OPERATOR_DESIGN_DECISIONS.md)(权威契约)、[`ai_native_bugfix_path.md`](../engineering/ai_native_bugfix_path.md)(诊断面来源 = content.* 探针)、CLAUDE.md SDD + 信任边界。

---

## 0. 架构对齐:三组件别混(动手前的硬前提)

| 组件 | 角色 | 文件 |
|---|---|---|
| **PI Operator** | **金字塔顶层「管理者」**(觉察+决策+指挥);职责 #1 = System Health Monitor | `aletheia/agent_shell/operator_loop.py`(4h assessment + decide_actions);`operator-shell/` TS(session/recommendation 呈现) |
| PI Agent | 交易者(逐笔 open/scale/skip) | `agent_shell/trading_decision.py` + `trading_context.py`(三层 context) |
| operator_loop.py 的部分 action | 收敛中:retire/demote/backfill 正迁往 TS | — |

owner 说的「顶层 Pi agent / 真实的管理者 / 自己监管自己」= 设计契约里的 **PI Operator**(契约原文:"PI Operator(觉察+决策+指挥)" 居顶,supervise 底层 jobs)。**术语对得上,意图无歧义。**

## 1. 缺口

PI Operator 的**头号职责就是 System Health Monitor**(契约 问题4 #1),但当前只消费:
- `data_health`(行情数据新鲜度)
- KPI 聚合(win_rate / PF / streak / exit_composition)
- `compute_health()` 的运行态 issue 串(operator_recommendations.py)

**它看不到我刚建的 `content.*` 内容质量诊断面**(僵尸策略 / memo 幻觉 / lesson 链死 / 归因断链 / 影子单滞留 / 结算停摆 / 提案偏置)。也就是说:管理者能监管「系统活没活」,但监管不了「系统产出的内容是不是垃圾/幻觉」——而后者正是 owner 原本要肉眼盯 Evolve 找的。**把 content.* 喂进 Operator 的健康监管 = 让顶层管理者真正自监管。**

## 2. 整合设计(v1,信任安全,全程 Level 1)

**核心**:Operator 的 assessment 增加内容健康维度,并以现有分级权限模型的最安全档(Level 1 = notify/收紧)做出反应。

1. **诊断面复用**:`doctor.py` 暴露轻量 `build_content_report()` —— 只跑 7 个 `_probe_content_*` 探针(不跑 23 个全量,避免 HTTP frontdoor 探针等重活)。返回 `Report`。
2. **assessment 摄入**:`SystemAssessment` 增字段 `content_health: list[dict]`(只收 severity∈{warn,blocker} 的 `{name,severity,detail}`)。`build_system_assessment()` 调 `build_content_report()` 填充。→ 随 assessment 写入 `operator_assessment` knowledge_item,**4h Operator LLM 循环自然「看见」内容健康并在 reasoning 里自监管**。
3. **gated 反应**:`decide_actions()` 在 `content_health` 非空时发一个 Level-1 `notify_operator`(已在 `_LEVEL_1_ACTIONS`)汇总异常。**仅通知**——不新增自动放松、不越权。
4. 僵尸策略已有路径(`underperforming_strategies` → `recommend_retire`,Phase 4d 走 pi_decisions 建议),**不重复处理**;content.strategy_health 仅作为 assessment 可见信号 + notify 汇总的一部分。

## 3. 信任安全(为什么这样设计不越界)

- 完全落在契约的 **Level 1(AUTO:只收紧 + 通知)**;不碰 Level 2(放松,需人批)/ Level 3(FORBIDDEN:kill_switch/live/risk 硬限/凭证/删审计)。
- Operator 「Supervisor not Executor」原则不变:它**读诊断、写 assessment、发通知/建议**,不直接修代码、不绕 risk engine、不解 kill_switch。
- 呼应上一轮评估([`ai_native_bugfix_path.md`] §Phase4 评估):自动「修」分级——数据态可逆才自动、代码态只出建议;本 v1 **连数据态都不自动改**,只让管理者「看见 + 通知」,是最保守的一档。把「自动执行修复」继续 defer 到有信噪比证据后。
- 复用既有限频:notify ≤10/天(契约 问题6);content 探针本身遵守诚实模式(已结案/owner关闭报 info 不触发 notify)。

## 4. SDD(spec-first)

- `tests/specs/spec_operator_loop.py` 新增 `SpecClaim`:
  - **OP-S?? content-health ingestion**:`SystemAssessment` 含 `content_health` 字段;`build_system_assessment()` 在内容探针 warn 时填充之。
  - **OP-S?? gated surfacing**:`content_health` 非空 → `decide_actions()` 产出 `notify_operator`(Level 1),**不**产出任何 Level 2/3 动作。
- 先 RED(声明 + 测试失败)→ 实现 → GREEN。改 `DEFAULT_*`/level 集合无(不动现有权限集),故无需同步 promotion_gates 契约。

## 5. 实施步骤
1. `doctor.py`:`build_content_report()` 辅助(只跑 content 探针)。
2. `operator_loop.py`:`SystemAssessment.content_health` 字段 + `build_system_assessment()` 填充 + `decide_actions()` 内容异常→`notify_operator`。
3. `spec_operator_loop.py`:2 条 SpecClaim + 测试。
4. 单测:assessment 填充(真实临时 DB 造 zombie/lesson-dead)、decide_actions 发 notify、Level2/3 不被触发。
5. 验证:`pytest tests/specs/ tests/test_operator_loop.py -q` + `aletheia doctor --json`。
6. 合并:主仓 FF + restart(post-merge hook 已激活,会自动重启);doctor 实测 Operator assessment 含 content_health。

## 6. 不做什么(防 scope 膨胀)
- 不新增任何自动 config patch / 自动 retire / 自动数据修(那是后续 evidence-gated 阶段)。
- 不碰 TS operator-shell 的 retire/demote 迁移(正交,别混)。
- 不动 PI Agent 的 trading_context(交易者 ≠ 管理者;它的 Layer1 已有 data_health,本次不扩)。
- 不改任何 Level 2/3 权限或 risk/execution/trust 模块。

## 7. 进度
- [x] doctor.build_content_report()(只跑 _CONTENT_PROBES;build_report 改用 *_CONTENT_PROBES 去重)
- [x] SystemAssessment.content_health 字段 + build_system_assessment 填充 + decide_actions Level-1 notify + summary 摄入
- [x] spec 2 claims(OL-S10 摄入面 / OL-S11 仅 Level-1 notify)+ 测试
- [x] 单测(3 行为 test:notify-only/clean-no-notify/真实DB造zombie填充)+ 全 spec 回归(335 claims 99%,架构 fitness 绿)
- [x] 合并 master(4d49cdc）+ post-merge hook 自动重启 + LIVE 实测:build_system_assessment().content_health=4 条真实异常,decide_actions→1 个 Level-1 notify(零 Level2/3)
