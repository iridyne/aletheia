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
