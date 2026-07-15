# PI Skip 反事实研究与恢复设计

> 状态：**讨论草案，不是行为授权。**
> 建立日期：2026-07-15
> 范围：System Operator（PI Agent）对有方向候选作出 `skip` 的证据、
> 固定时点结果、数据 epoch 隔离、liveness 诊断与后续受控实验。

本目录服务于“连续 skip 是否真的保护系统，还是把系统锁死”的设计讨论。它不改变
PI prompt、conviction 门槛、Risk Engine、Execution Gateway、Live、额度或凭据；任何
可能改变候选放行或交易行为的实现，仍须走新的 policy epoch、spec、测试和人工审核。

## 阅读阶梯

1. [00_current_state.md](00_current_state.md)：本次事故的已验证事实、推论和未知项。
2. [01_research_basis.md](01_research_basis.md)：反事实、因果、回放与前瞻实验的
   方法边界；避免把观察数据误称为因果结论。
3. [02_implementation_plan.md](02_implementation_plan.md)：拟议的 SDD 实施蓝图和
   可验证验收条件。
4. [03_decisions_to_resolve.md](03_decisions_to_resolve.md)：必须由 owner 明确决定的
   治理问题。没有这些决定不得进入行为变更。

## 术语与不变边界

- **skip 的反事实净结果**：在决策时已固定方向、市场、可见入场价的假设下，固定
  horizon 的成本后净结果。它不是实际订单 PnL，也不是“如果一定开仓”的完整模拟。
- **evidence epoch**：一次数据重载或明确的数据可用性边界。它与因代码/配置变化而
  旋转的 `policy_epoch` 不同。
- **protective skip**：有足够、同 epoch 的成熟反事实结果支持其拦截价值的 skip。
- **skip-halt incident**：队列有流量但 proposal 长期为零，且证据链、结算或 epoch
  语义异常的诊断事件；它不是强制开仓信号。

保留的硬边界：Risk、live unlock、credential、交易所能力和执行安全门永不因为“可能
错过收益”而自动放宽。现有筛选证据契约也明确禁止由观察结论直接修改配置。

## 与既有契约的关系

本目录是这些现行契约的讨论前置层，不替代它们：

- [机会筛选证据与负反馈契约](../filter_evidence_contract.md)
- [机会固定时点结果契约](../opportunity_outcome_contract.md)
- [决策生命周期契约](../decision_lifecycle_contract.md)
- [Learning Contract](../learning_contract.md)
- [Filter Feedback Assignment and Bounded Replay](../filter_replay.md)
- [Spec-Driven Development 方法论](../SDD_METHODOLOGY.md)
