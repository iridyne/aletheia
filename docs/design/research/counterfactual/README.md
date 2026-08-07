# Counterfactual Research — Running Index

> Scope: 반事実分析모듈 `aletheia/counterfactual/` の実証研究成果を蓄積する。
> 方法论约束见 [causal_counterfactual_research_guidance.md](../../subsystems/causal_counterfactual_research_guidance.md)。
> 模块契约见 [counterfactual_contract.md](../../contracts/counterfactual_contract.md)。

所有文档是**只读研究报告**，不是行为授权。任何生产参数变更须走独立 policy epoch 和 owner 批准。

## 文件目录

| 文件 | 范围 | 状态 |
|---|---|---|
| [01_data_audit_2026-07-31.md](01_data_audit_2026-07-31.md) | L0+L1 数据完整性与结算质量审计 | 更新（race case 修正） |
| [02_calibration_findings_2026-07-31.md](02_calibration_findings_2026-07-31.md) | L2 全局 Conviction gate 校准 | 初稿 |
| [03_filter_experiment_status_2026-07-31.md](03_filter_experiment_status_2026-07-31.md) | L4 过滤器前瞻实验状态 | 初稿 |
| [04_per_family_calibration_2026-07-31.md](04_per_family_calibration_2026-07-31.md) | L2 Per-scanner-family 深度校准（新增 --family 参数） | 初稿 |
| [06_universe_score_continuity_and_learning_safety_2026-08-07.md](06_universe_score_continuity_and_learning_safety_2026-08-07.md) | Universe 连续评分、语义修复与因子级 Learning 安全计划 | 已解析为正式 Proposal；未实现 |
| [07_universe_continuous_curve_contract_proposal_2026-08-07.md](07_universe_continuous_curve_contract_proposal_2026-08-07.md) | Universe 连续曲线锚点、缺失值和硬门规范 | 已接受为 Shadow-only 实现证据；未生效 |
| [08_universe_continuous_scoring_adversarial_review_2026-08-07.md](08_universe_continuous_scoring_adversarial_review_2026-08-07.md) | 四道保险的实现者/风险审计双重反向检查 | Task 2 设计通过；Paper/Runtime 未通过 |

连续评分和未来因子 Learning 的正式未来设计归属
[Universe continuous scoring safety Proposal](../../proposals/universe_continuous_scoring_safety.md)；
本目录的日期化文件仍是研究/设计证据，不声称当前 Runtime 已具备相应行为。

## 当前研究快照（2026-07-31）

DB 范围：2026-07-15 至 2026-07-28（dev 环境本地 DB）。
35,726 raw rows / 8,319 distinct candidates / 10,694 settled (29%)。

**运营层行动项（不要求 owner 批准）：**
1. ✅ 应用 `policy_epoch_id` migration（dev DB 已完成，CASI 待验证 #508）
2. `filter_outcome_recorder.settle_due()` 运行状态待生产确认（#508）

**已完成研究（可闭卷）：**
- ✅ `momentum` 60-70 反转：单一 ESPORTS/USDT(-51%) 异常值导致，非真实信号
- ✅ `derivatives` 全频段负收益根因：SKHYNIX/MU short 在 2026-07 半导体反弹期系统亏损
- ✅ `short_bias` 60/90d 延长窗口：dev DB 数据上限 30d，结果与 30d 一致

**owner 级洞察（需要独立策略决策）：**
- `short_bias`：低 conviction（46-55）有正预期，门槛可能偏高（advisory 已触发）；需生产 90d+ 数据验证
- `derivatives`：家族过于异质（半导体/地产/ETF/加密），一刀切门槛无效；建议按 asset class 子分类
- `mean_reversion`：低分段（40-50）极端亏损（-5.5%），regime 敏感，待 L3 回放分层
