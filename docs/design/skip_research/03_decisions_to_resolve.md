# 03 — 进入实现前必须关闭的决策

> 本清单故意不替 owner 作决定。未关闭的项目只能停在研究/观测，不得以“合理推断”
> 进入行为改动。

| ID | 决策问题 | 推荐默认值 | 为什么必须明确 | 关闭后影响 |
| --- | --- | --- | --- | --- |
| D1 | 数据重载后，保留的 `universe_forward_outcomes`、lesson、review 是否可影响新 PI conviction？ | 否；保留为研究档案，除非新 epoch 显式导入 | 这是本次死锁的核心语义 | evidence epoch manifest、PI context 查询 |
| D2 | 哪些 skip 属于硬安全/能力拒绝，永不进入“放宽”研究？ | risk/live/credential/exchange/data capability 全部硬门 | 避免 missed-alpha 反向腐蚀安全边界 | reason taxonomy、report 分组 |
| D3 | direct skip 证据写入失败时如何处理？ | 对终态唯一证据，状态与事实同成同败；一般观察写入失败须显式 warning | 不能把不可审计 skip 伪装为完整判断 | transaction 语义、retry/incident |
| D4 | 新 epoch 的冷启动应显示什么？ | `insufficient_evidence`，不以旧负 edge 代替 | 空样本不是负 alpha | PI context/rendering、doctor |
| D5 | skip-halt incident 的升级渠道和 SLO 是什么？ | 先 doctor/API/审计可见，不自动调参 | liveness 需要被发现，但不能成为强制交易器 | scheduler、UI/alert 责任 |
| D6 | 是否授权 Phase D 的 paper/shadow treatment？ | 暂不授权；先完成 A–C 并审核结果 | context 改动是 B 类行为窗口 | experiment packet、policy epoch |
| D7 | 若授权实验，首个 treatment 是什么？ | 由 C 的单一 suspect 决定，不预设 | 避免为解决“全 skip”而反向过拟合 | preregistration 内容 |

## 建议的讨论顺序

1. 先关闭 **D1**：这决定“reset”到底意味着什么，也决定本次历史结果是否可继续进入 PI。
2. 再关闭 **D2–D4**：这决定证据缺失、冷启动和安全门的语义。
3. 再审阅 Phase A–C 的数据模型和验收标准；此时仍不改变交易行为。
4. 只有出现成熟、同 epoch、足够覆盖的 `overrestrictive_suspect`，才讨论 D5–D7。

## 每次讨论后的记录模板

```markdown
### D<n> — <标题>

- 日期 / 决策人：
- 选择：
- 不选择的方案及原因：
- 不变边界：
- 受影响的契约 / executable spec：
- 是否创建新的 policy epoch：是 / 否；理由：
- 验收证据：
```

在决定行为改动前，须按 SDD 顺序将这里的选择提升为正式契约、澄清项、任务包和测试 claim；
本文件仅保存讨论输入，不能作为执行授权。

## 已关闭的 B1 治理决定 — 2026-07-15

**决策人：owner。授权依据：按
[完整 Skip 修复实施计划](04_comprehensive_skip_remediation_plan.md) 逐步实施。**

| 决定 | 选择 | B1 的直接约束 |
| --- | --- | --- |
| G1 / D1 | 重载后遗留 outcome、lesson、review 仅作研究档案；遵循恢复 SOP，不新增重载 runtime feature | B1 不读取或新增 reload epoch；实时候选只使用已清理后的当前事实 |
| G2 / D2 | 所有 risk/live/credential/exchange/data-capability/TTL 硬门不参加放宽研究 | B1 不修改这些路径，也不因 missed alpha 绕过它们 |
| G3 / D3 | candidate skip 的必需证据失败即回滚 terminal claim | 已由 TASK-2026-077 实施；B1 不削弱它 |
| G4 / D4 | 冷启动或不成熟 cohort 一律是 `insufficient_evidence` | B1 不把低样本聚合 WR 映射为负 edge 或调高门槛 |
| G5 / D5 | skip-halt 只产生诊断/审计/shadow 草案，不自动调参或开仓 | B1 的 liveness 输出没有配置、Proposal、Risk 或 execution 副作用 |
| G6 / D6-D7 | 首个 B treatment 仅移除 aggregate health 对单候选 PI action 的权限，并将 zero-pending heartbeat 移出交易评估 | 不同时改 family edge、ranker、universe、conviction、debate 或 sizing；它们须另开 epoch |

旧观察窗口在 B1 启动前关闭；B1 以新的 `learning_policy_epoch`、commit、固定 treatment 描述和
从零开始的观测指标记录。若 B1 的 candidate identity、trace/outcome coverage 或安全契约退化，
立即停止该 epoch，恢复上一个已审计 context，而不删除已产生的事实。
