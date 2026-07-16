# 2026-07-15 数据库重载：学习证据恢复记录

> 状态：已完成的一次性维护记录。
> 执行依据：[数据库重载后的学习证据恢复 SOP](../runbooks/DATABASE_RELOAD_RECOVERY_SOP.md)
> 边界：仅处理重载前仍留在**活动**学习输入中的历史证据；不改变代码、PI 门槛、Risk、
> Execution、Live、凭据或交易链事实。

## 事故语义

本次数据库重载已使 paper account 的 proposal、risk decision、order、fill 与 position
因果链归零，但部分市场 forward outcome 与知识条目仍留在活动库。若不隔离，System
Operator 会把旧账户/旧窗口的结果当作当前 conviction 的依据。

确认的因果连续性边界：`2026-07-14T03:12:00+00:00`。

## 已执行处理

1. 创建并验证重载前完整备份；完整性验证通过（135 个文件，无 mismatch 或 error）。
2. 在 gateway 停止、交易链表均为零的前提下，使用单一 SQLite 事务：
   - 从活动 `universe_forward_outcomes` 移出 145,344 条 `entry_ts` 早于边界的记录；
     它们保留在已验证备份中供研究与回滚。
   - 将 191 条重载前仍为 active 的 `guard_candidate` 标为 `archived`。
3. 保持 `ps_lessons`、`trading_guard`、`research_note`、filter evidence、audit 与全部交易链
   表不变。

## 验收

- SQLite `integrity_check` 返回 `ok`。
- 活动 `universe_forward_outcomes` 的重载前行数为 0；保留 4,496 条重载后入场记录。
- 重载前的 `trading_memo`、`reasoning_feedback`、`calibration_report`、`trade_case_study` 和
  `guard_candidate` 均不再作为 active PI prompt 材料。
- gateway 恢复后 backend ready、startup gate 允许 AI/trading、operator shell heartbeat 正常；
  scheduler 已产生新的 agent run。

## 结论与非结论

本次维护消除了“重载前遗留学习证据仍影响当前 PI”的状态错配；它**不**保证每个候选都会
产生 proposal，也不改变当前市场、当前 regime 或重载后新产生 outcome 的判断。重载后仍可能
出现合法 skip；其长期效果应按 Skip 反事实研究计划另行讨论，不能用本次数据维护替代。

若边界被证明错误或恢复后发现交易链本不应为零，应停止运行并使用受管备份回滚，再做只读
审计。
