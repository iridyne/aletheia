# 因果与反事实研究：全框架应用指引

> 状态：**研究与治理指引，不是行为授权。**
> 建立日期：2026-07-15
> 当前分支边界：本指引说明全框架可复用的方法；本分支的任何后续工作只研究
> System Operator（PI）的 `skip`，不改变 universe generation、选币、ranker、Risk、
> Execution、Live 或配置。

## 1. 目的与用语边界

反事实结果回答的是：在某个决策时点已知的身份、方向、价格和成本假设下，若暴露于该
候选，固定 horizon 后会出现什么成本后结果。它使一个决策可被事后检验，但**不是**现实
账户 PnL，也不是因果证明。

因果问题则必须先定义可比较的干预、纳入条件、时间零点、对照、分配机制和结果。没有
预先记录的随机分配或可信的选择机制，历史差异只能称为观察性关联或诊断信号，不能授权
自动改变交易行为。

所有模块复用同一证据阶梯：

```text
L0  完整性：决策身份、first exposure、版本/epoch、结果键是否完整？
L1  固定时点结果：预注册 horizon 的成本后结果是否成熟且覆盖完整？
L2  描述性诊断：在可比 cohort 中，pass/reject 或 treatment/control 有何差异？
L3  有界回放：固定历史、版本和参数，只生成可检验假设。
L4  前瞻实验：预登记分配、paper/shadow、固定指标与停止规则。
L5  人工治理：在风险与运行约束下决定是否另开 policy epoch。
```

任何层级都不能跳过前一层；L0/L1 不足时，诚实结论是
`insufficient_evidence` 或 `data_quality_incident`，不是自动放行、自动封锁或自动调参。

## 2. 在决策链中的适用范围

| 决策点 | 可检验的问题 | 最小可比较单位 | 重要限制 |
| --- | --- | --- | --- |
| Universe generation / membership | 某纳入或排除规则是否提升后续可交易候选的质量？ | 同一 snapshot 中、规则应用前可见的 symbol 候选 | 只有 symbol 而无预先方向/入场定义时，不能声称有 alpha；最多衡量覆盖、流动性、波动和可执行性。 |
| Universe allocation / ranking | 排名前列相对未入选的同 cohort，是否有更好的固定时点成本后结果？ | 同一 scanner 版本、市场、方向、horizon 下的候选 | 不得混合市场、方向、版本、成本模型或不同数据 epoch。 |
| Observer、ranker、filters、guards | 被筛掉的候选是否真比通过者差，过滤是否有保护价值？ | 一次 first exposure 的 pass/reject 候选 | 要保留拒绝原因、证据、固定时点结果；仅报告结果，不自动反写门槛。 |
| System Operator `skip` | `skip` 是保护性、疑似过度限制，还是证据不足？ | 已有方向、身份、可见入场价的 PI 候选 | 本分支的唯一行为研究范围；无 target 的全局 heartbeat 不构成可评估 skip。 |
| Proposal / Risk / execution | 漏斗损失发生在哪里，实际执行与反事实结果差异多大？ | 具备完整 causal ID 链的 proposal | 只做归因与报告；不得以“错过 alpha”为由放宽硬风险、执行、Live 或凭据边界。 |

若要评价 universe 是否贡献 alpha，候选必须在进入筛选时绑定可审计的
`scanner_family + direction + market/symbol + snapshot + horizon + cost model`。没有这些，
“被选/被拒”的结果不能与可交易方向暴露对应，也不能成为 alpha 声明。

## 3. 共同的设计约束

- **固定 first exposure**：结果键使用决策当时可见的信息，不能以后来价格、标签或
  修订后的 universe 倒灌进入解释。
- **cohort 可比性**：至少按数据 epoch、policy/scanner 版本、市场、方向、horizon、
  成本模型和时间段分层；小样本与同批高度相关样本必须标为不确定。
- **观察与权限分离**：L1--L3 仅产生研究结论；任何生产行为改变都须新 policy epoch、
  executable spec、测试和人工批准。
- **安全不可交易**：Risk、执行、交易所、Live、额度、凭据和其他硬安全门不参加
  “为了 alpha 而放松”的实验。
- **拒绝也必须可学习**：每个可评估 reject/skip 都要留下身份、原因、证据、版本与结果
  键；无身份的系统健康告警只能作为运行诊断，不能伪装成候选决策。

## 4. 本分支与后续工作的分界

本文件为 future agents 建立共同语言，并不把 universe 研究并入当前任务。
当前 `skip-problem` 分支仅可：补齐/审计 PI `skip` 的证据与结果语义、诊断 liveness、
并提出受控研究设计。Universe 生成、选币、ranker 或其他过滤器的行为变化应在独立任务中
按上表建立其 own cohort、spec 与 policy epoch 后再讨论。

相关的既有契约与当前 Skip 专项材料：

- [机会固定时点结果契约](../contracts/opportunity_outcome_contract.md)
- [筛选证据契约](../contracts/filter_evidence_contract.md)
- [Learning Contract](../contracts/learning_contract.md)
- [PI Skip 反事实研究与恢复设计](../skip_research/README.md)
