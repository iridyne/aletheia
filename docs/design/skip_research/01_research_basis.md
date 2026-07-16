# 01 — 方法依据：反事实归因不等于因果证明

> 本文把同一方法应用到 PI `skip`，不是把研究范围限制为 `skip`。其他决策点的共同
> 适用条件与限制见[全框架应用指引](../causal_counterfactual_research_guidance.md)；当前
> 分支不实施 universe generation 或选币机制的任何行为变更。

## 1. 本项目第一阶段可以、也应该做什么

对于一个在时点 `t0` 被 skip 的、已有 `long` 或 `short` 方向的候选，保存当时可见的
价格、市场、方向和成本假设，并在 15m / 1h / 4h / 24h 结算净结果。这是**固定时点的
反事实结果归因**：它能衡量“若按该事前方向暴露，随后价格路径是否有正的成本后空间”。

它不能自动回答“若系统不 skip，最终账户会更好”，因为真实开仓还会经过排序、持仓冲突、
Risk、execution、滑点、资金费和市场冲击。既有
[机会固定时点结果契约](../opportunity_outcome_contract.md) 已将这种结果限定为 observation，
不得提交订单或改变风险。

因此第一阶段的用语必须是：

| 可以说 | 不可以说 |
| --- | --- |
| “该 skip cohort 的反事实净结果显示为保护性/证据不足/疑似过度限制” | “skip 已被证明导致系统亏损” |
| “在同 cohort 内 reject 的估计净期望高于 pass” | “放松该门槛必然提高账户收益” |
| “该现象值得一个受控实验” | “观察数据授权自动改 prompt 或开仓” |

## 2. 为什么需要 target trial，而不只做历史统计

因果问题必须先定义可比较的 intervention、纳入条件、结果、时间零点与对照；观察数据
即使很完整，也会包含策略选择偏差和未观测混杂。这个框架通常称为 *target trial*
思路。[Hernán 与 Robins, *Causal Inference: What If* (2024)](https://www.hsph.harvard.edu/miguel-hernan/wp-content/uploads/sites/1268/2024/04/hernanrobins_WhatIf_26apr24.pdf)

对本问题，未来的 target trial 应明确为：

- **unit**：一个有完整身份和方向的 `pi_decision` 候选；
- **time zero**：PI 开始评估并写入 first exposure 的时刻；
- **control**：当前固定版本的 PI evidence context；
- **treatment**：一个明确且单变量的 context 改动；
- **assignment**：候选键稳定哈希的预先记录随机分配；
- **outcome**：预注册 horizon 的成本后净结果，另报实际 proposal / Risk / execution 漏斗；
- **exclusion**：无方向、无可见价格、缺市场身份、硬安全门拒绝的候选。

## 3. 为什么此刻不引入 doubly robust / off-policy 估计

Doubly robust policy evaluation 需要历史行为策略的选择概率（propensity）或可被合理建模的
日志机制，并依赖结果模型与 propensity 模型的条件。[Dudík、Langford、Li,
*Doubly Robust Policy Evaluation and Learning*](https://arxiv.org/abs/1103.4601)

当前 direct skip 没有完整的 first-exposure trace，更没有可用于比较“会被放行”和“会被
skip”的随机 assignment/propensity。因此在补齐日志之前使用 DR 会把未验证假设包装成精确
统计。阶段一只做描述性、分层、收缩后的反事实结果归因；DR 是未来在受控 assignment 已
落库之后才可评估的工具，不是本次修复的捷径。

## 4. 回放为何只用于选择假设

在历史窗口内反复寻找看起来更优的门槛，会产生 backtest overfitting；最优的历史候选并不
等于前瞻有效。[Bailey 等, *The Probability of Backtest Overfitting*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)

因此本项目已存在的 [filter replay](../filter_replay.md) 边界应继续适用：回放固定版本、
只读、有界；其输出只允许进入预登记 shadow experiment，不能写生产配置。前瞻随机对照
实验是从“有趣的历史相关”进入“可比较效果证据”的必要下一步；实验应先固定分配与度量，
而不是在结果出现后重写标准。[Kohavi 等, *Online randomized controlled experiments at scale*](https://dkf.unibas.ch/fileadmin/user_upload/dkf/Publikationen/Kohavi_etal_Trials2020.pdf)

## 5. 项目内采用的证据层级

```text
L0  完整性：每个有方向 skip 是否留有 trace、epoch、outcome key？
L1  描述性：同一 cohort 的固定时点净结果是否成熟、覆盖完整？
L2  诊断性：reject 相对 pass 是否是 protective / suspect / insufficient？
L3  回放：固定版本、只读地筛选一个可检验 treatment。
L4  前瞻实验：预登记、随机 assignment、paper/shadow、固定停止规则。
L5  人工决策：在证据、风险和运行约束下，决定是否另开行为 epoch。
```

任何层级不能跳过前一层。特别是 L0/L1 失败时，系统的正确状态是 `data_quality_incident`
或 `insufficient_evidence`，不是自动交易。
