# 00 — 事故基线：已知事实、推论与未知项

> 证据时间点：2026-07-15 的只读 incident audit。
> 适用范围：当时实际运行数据库的该次 reset 后窗口；不是对未来运行状态的持续断言。

## 1. 已验证的漏斗断点

```text
74 opportunities
  -> 72 ranker eligible
  -> 34 signal_pending
  -> 18 被 System Operator 评估
  -> 18 skip
  -> 0 trade proposals
  -> 0 risk decisions
  -> 0 positions
```

同一审计中，`trade_proposals` 与 `risk_decisions` 均为空，但
`universe_forward_outcomes` 仍有 149,680 条记录。故“Demo Trading 的交易链清空”
不等于“所有被 PI 上下文消费的历史结果都清空”。

当时最近的 regime 是 `SIDEWAYS`，confidence 约为 56%；PI 还看到若干策略族在近
七日窗口的负向净结果，例如 short-bias short perpetual 的样本约为 WR 33%、平均净结果
-2.4%。这些数值与 skip 理由一致，但只证明**该输入被消费且为负**，不证明 skip 策略本身
是错的。

## 2. 证据覆盖断点

18 个 terminal skip 不是同一种可观测路径：

| 路径 | 数量 | 已有 System Operator filter 事实 | outcome 连接 |
| --- | ---: | ---: | ---: |
| 先形成 trade intent，后被 debate / conviction gate 拒绝 | 8 | 有 | 有 `pi_decision:<id>` |
| PI 直接输出 `skip` | 10 | 无 | 无 |

前一类的 outcome 在审计时仍处于未成熟/待结算窗口；后一类连未来结算的身份都没有。因此
当前不能回答“直接 skip 是保护还是错失 alpha”。把所有 skip 合并成“低胜率所以正确”是
不成立的。

## 3. 当前反馈环的最小因果图

```text
保留的 forward outcomes / lessons
          |
          v
family_direction_edge + SIDEWAYS regime --> PI conviction 降低
                                             |
                                             v
                                      final direct skip
                                             |
               +-----------------------------+----------------------------+
               |                                                          |
               v                                                          v
       不创建 proposal / 无实际 PnL                       缺少 filter trace / outcome key
               |                                                          |
               +--------------------------> 无法验证 skip 是否保护 <------+
```

这里的“因果图”描述系统信息流，不是对市场因果关系的统计证明。

## 4. 已知实现语义

- `skip` 是 terminal action：同一 Opportunity/来源有最终 PI 决定后，不会自然回到 pending
  队列再次评估。
- daemon 对 LLM/准备失败的 `unavailable` 会重试，但不会重试合法 terminal skip。
- 现有 DCLS recovery 主要覆盖持仓/填单干旱和可见 blocker，不覆盖“PI 有持续输入但全数
  terminal skip”的证据死锁。
- 现有 filter evidence 契约已要求 System Operator action/conviction/debate/direction
  brake 可被统一索引；本次 direct-skip 路径显示出这个要求在完整性上存在缺口。

## 5. 推论与尚未证实的假设

### 可成立的推论

1. reset 边界没有被建模为“PI 可使用的证据边界”。
2. direct skip 的评估闭环缺失，故系统不能自证其拒绝质量。
3. 长期零 proposal 目前会静默停在 PI 层，未升级为独立的 liveness / evidence incident。

### 必须通过后续证据检验的假设

1. 负向 family edge 在新 reset 后不应继续影响实时 conviction。
2. 放松其中一个 PI 证据输入会改善净期望。
3. 连续 skip 的根因是历史 edge，而非候选本身确实没有可交易 edge。

上述三项都不是本文件授权实施的结论。
