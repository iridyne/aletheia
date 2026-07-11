# Opportunity 筛选证据与负反馈契约

> 状态：Stage E 证据基础已实施；交易行为调参仍关闭。
> 目标：让每个筛选关卡的放行、拒绝和后续净结果可被真实比较，而不是用整个策略族的总收益猜测某个关卡应该放宽还是收紧。
> 不变边界：本契约不自动改参数、不改风控上限、不启用实盘、不绕过 Execution Gateway。

## 1. 为什么不能只记“最后被拒绝”

Opportunity 主链上的“过滤”实际包含四种不同问题：

1. **市场能力门**：交易所是否支持、数据是否新鲜、点差和成交额是否可执行。这些门不是 alpha 预测器。
2. **方向假设门**：scanner、Observer 或 System Operator 是否给出做多/做空假设。没有事前方向就没有可归因净收益。
3. **质量/排序门**：置信度、TTL、factor、guard、批次上限和冷却时间。这些才是主要负反馈对象。
4. **安全/执行门**：风控、实盘锁、交易所精度和下单失败。这些只能报告，不允许因“错过收益”自动放宽。

只保存终点拒绝会丢失前面已放行的关卡；只保存放行会没有对照组。因此必须记录一个候选在当次评估中实际经过的每一关，到首个拒绝为止。

## 2. 候选身份与分叉

### 2.1 `candidate_key`

`candidate_key` 表示“在某一决策时点的一个交易假设”，不是纯 symbol：

- Universe 选币：`universe_member:<member_id>`。
- Universe 无方向前置门：以 `scanner_run_id + asset_id/feature_snapshot_id`
  在 `filter_decision_batches` 中保存，不伪造交易方向。
- Observer 单次评估：`observer:<run_id>:<venue>:<symbol>:<market_type>`。
- Ranker 单次机会：`opportunity:<opportunity_id>`。
- System Operator：`pi_decision:<decision_id>`。
- Risk：`proposal:<proposal_id>`。

同一 symbol 的现货和永续合约必须是不同候选。新一次 Observer 评估也是新候选，因为它的入场时间和行情已经改变。

### 2.2 必备身份字段

每条筛选记录必须尽可能携带：

- `candidate_kind` / `candidate_key` / `parent_candidate_key`
- `run_id` / `opportunity_id` / `universe_snapshot_id` / `universe_member_id` / `proposal_id`
- `venue` / `symbol` / `market_type`
- `direction` / `scanner_family` / `regime_tag`
- `evaluated_at`

`direction` 允许为 NULL，但 NULL 意味着它只能衡量“市场后来是否有大幅波动”，不能衡量可赚取的净 alpha。

## 3. 不可变筛选记录

`filter_decisions` 每行表示某一候选在某一关的一次事前判断：

- `filter_name` / `stage` / `sequence_no`
- `decision`: `passed | rejected | not_applicable | error`
- `reason_code`：稳定、不带动态金额的机器码
- `reason_detail`：用于人工排查的简短说明
- `input_snapshot`：该关真正消费的最小输入，不保存整段 prompt/context
- `parameter_snapshot`：当时门槛和开关
- `filter_version`：由 schema 版本 + filter 名 + 参数快照的规范 JSON 计算
- `code_commit`：Gateway 启动时代码提交，不在热路径 shell out
- `outcome_candidate_key`：可与同一事前时点的净结果相连

业务写入只允许 INSERT，不回写历史判断。幂等键至少包含
`candidate_key + filter_name + decision + filter_version`，重试不制造重复证据，
但同一候选先被批次上限拒绝、后来真实放行时可分别保留两条事实。
每日数据治理允许整行删除超过保留期的分析证据，这不等于改写决策；
权威 `RiskDecision`、proposal、order、fill、position 和 audit 记录不在此清理范围。

API 密钥、Authorization、原始 LLM prompt/context 和新闻全文禁止写入。

对 registry/ticker/tradability 这类高频、无方向关卡，允许用
`filter_decision_batches` 压缩表达：每个 scanner run + filter version 一行，
`candidate_facts` JSON 内保留该关实际评估的每个候选键、标的身份、
pass/reject 和最小输入。这是存储压缩，不是抽样；候选数和各类决定数
必须与 JSON 事实一致。有方向、可结算的关卡仍必须使用逐候选
`filter_decisions`，不允许批次聚合后丢失 outcome 连接。
为避免 10 分钟批次重复存储每个 JSON 字段名，当前编码为
`{"fields":[...],"rows":[[presence_mask,...values]]}` 的无损行矩阵，再以
`zlib-base64:` 自描述前缀压缩为 SQLite TEXT。`decode_filter_decision_batch()`
同时支持当前压缩编码、未压缩行矩阵和旧的 plain-list JSON；
压缩不得改变候选数、字段值或缺失字段语义。

## 4. 过滤候选的固定时点结果

`filter_candidate_outcomes` 以 `outcome_candidate_key + horizon` 唯一，固定周期为 `15m / 1h / 4h / 24h`。经济公式、时点行情质量和现货/合约隔离完全复用 `opportunity_outcome_contract.md`。

只有同时满足以下条件才创建可学习结果：

1. 事前已有 `long` 或 `short`，禁止事后选更赚的方向。
2. `venue + symbol + market_type` 完整。
3. 入场价大于 0，且是该市场在决策时点可见的价格。
4. 永续合约缺入场资金费时可保存 gross/directional，但 net 必须为 NULL。

同一候选经过多个关卡时只结算一份结果，避免把同一个价格路径当成多个独立样本。

## 5. 现行关卡与证据等级

| 关卡 | 当时是否有方向 | 要记录的事实 | 可否用于净收益负反馈 |
|---|---:|---|---:|
| registry/交易所能力 | 否 | 支持/不支持、数据缺失 | 否，只报能力覆盖 |
| ticker 成交额预筛 | 否 | volume、threshold、market | 否，只报可执行性/波动遗漏 |
| tradability（稳定币/黑名单/volume/spread/freshness） | 否 | 按顺序记每关放行到首个拒绝 | 否，黑名单永不自动调参 |
| scanner 假设 | 是 | family、direction、各 family score | 是 |
| quota/explore-exploit | 是 | selected/control、rank、quota、regime | 是，但必须同池比较 |
| Observer 前置判断 | Universe 路径是；其他路径可能否 | RSI/ATR/keyword 实际值和命中原因 | 仅有事前方向的行 |
| LLM neutral/无效输出 | 可能否 | schema 结果、方向、置信度，不写 prompt | 仅有事前方向的行 |
| Ranker execution/direction/confidence/TTL/factor/guard | 是 | 顺序放行与首个拒绝 | 是（guard 只观测） |
| open-position/cooldown/batch limit | 是 | 当时占用、冷却截止、rank/limit | 是 |
| System Operator watch/conviction/debate/direction brakes | 是 | 原始/辩论后 conviction、实际暴露快照 | 是，但不在有对照前改门槛 |
| Proposal submission 方向/取价 | 是 | 方向集中度，最终 market_type，目标市场价格是否可用 | 是；用于解释 trade 意图为何没有 proposal，不自动放宽 |
| Risk | 是 | `RiskDecision` 为权威，filter 记录只做统一索引 | 报告用，永不自动放宽 |
| Execution adapter | 是 | order/error/venue reason | 用于成交率/实现率，不是方向 alpha |

`XAUT/PAXG` 是价格会波动的黄金 RWA，不是美元稳定币，因此不得在
`tradability_stablecoin` 中提前排除。它们仍必须通过交易所 ticker、成交额、
点差、新鲜度、后续风控和执行能力检查；分类修正不等于授权实盘。

## 6. 负反馈统计契约

统计单元至少为：

`filter_name + stage + filter_version + scanner_family + direction + market_type + horizon`

`stage` 是关卡身份的一部分。同名过滤器如果分别发生在 System Operator 和
proposal submission，必须分别留痕、分别统计；幂等键至少包含
`candidate_key + filter_name + stage + sequence + decision + filter_version`。
控制器展示的当前值优先使用证据行自己的 `parameter_snapshot`，不能用注册表中的
概括值覆盖历史上真正执行过的参数。

regime 样本足够后再增加 `regime_tag`，禁止小样本无限切片。

学习目标不是胜率，而是净期望。每个 pass/reject cell 保存：

- `n_total / n_net / outcome_coverage`
- `avg_net / median_net / profit_factor / MFE / MAE`
- 按同 cohort 总体均值收缩后的 `shrunk_avg_net`
- 相关重复样本按 `outcome_candidate_key` 去重，不按关卡行数数多次

MVP 收缩公式：

`shrunk_mean = (n * sample_mean + k * cohort_prior_mean) / (n + k)`，默认 `k=20`。

一个关卡只有在以下条件全部满足时才可以输出 `overrestrictive_suspect`：

1. pass/reject 两边都至少 20 个完整 net 样本；
2. 两边 outcome coverage 都不低于 70%；
3. 只使用一个 `filter_version`；
4. reject 的收缩净期望高于 pass 至少 5bp，且 reject 自身大于 0；
5. 关卡不是 risk/live/credential/exchange-capability 硬门。

观察数据不是随机实验，因此结论只能是 `suspect`，不能直接改配置。自动收紧也不能只因 pass 亏损就触发；它需要门槛附近的反事实或独立回放。

## 7. 批次选择的特别规则

`open_position -> cooldown -> batch_limit` 是三个不同关卡，不得先截取 Top-3 再检查前两关。否则 Top-1 被冷却拒绝后，Top-4 永远没有补位机会，候选供给会被静默浪费。

正确顺序：

1. 按分数从高到低遍历；
2. 已持仓则记 `open_position_duplicate=rejected`，继续看下一个；
3. 冷却中则记 `signal_cooldown=rejected`，继续看下一个；
4. 未满 limit 则放行；已满后其余候选记 `signal_batch_limit=rejected`。

Shadow 批次的 `was_selected` 必须对应真正写入 `signal_pending` 的候选，不能在冷却/持仓检查之前就标记“已选中”。

## 8. 失败与运维语义

- 证据写入失败不能改变原交易决定，但必须记 warning/doctor coverage，不得沉默当成完整证据。
- outcome 取价失败保持 `failed`/NULL，不用同名现货或过晚价格补齐。
- `doctor` 分开报告：记录覆盖率、可结算比例、到期未结算数、各 filter 是否达到最小对照样本。
- `doctor` 同时校验批次里的候选总数是否等于 pass/reject/not-applicable/error 之和。
- Trade Funnel 优先从 `filter_decisions` 读取窗口内 ranker 历史候选，不在审计时重跑 ranker；
  `proposal_submission` 拒绝原因必须单列，不能统称为未知的 decision-to-proposal 丢失。
- `filter_decision_batches` 保留 30 天；`filter_decisions` 和
  `filter_candidate_outcomes` 保留 90 天。它们是分析证据，不是交易因果原件。

## 9. Stage E 实施状态

1. 已完成：新表、model、repository、只追加/幂等写入与有界保留。
2. 已完成：`UniverseMember.scanner_direction` 持久化，Observer 和 Ranker 顺序轨迹。
3. 已完成：open-position/cooldown/batch-limit 补位顺序与 shadow 真实 selected 语义。
4. 已完成：固定时点净结果，shadow/control 新行的 instrument 和成本迁移。
5. 已完成：Universe ticker/tradability 批次证据、allocation 对照、System Operator 和 Risk 统一索引。

6. 已完成：只读 filter feedback 使用分层收缩净期望，未达样本门只显示证据不足。
7. 已完成：ranker 历史漏斗和 proposal submission 原因直接来自已落库证据；无目标市场价格返回稳定失败原因，不再静默 `None`。
8. 已完成：反馈统计按真实 stage 分组并展示事发参数；同名同参数关卡跨阶段不再因幂等键碰撞丢证据。
9. 待独立实验：合并方向刹车、修改 ranker 权重/经济门和任何自动调参。

## 10. 诊断时间窗口与 sizing 口径

- `opportunity audit --hours N --view controller` 必须按
  `filter_decisions.evaluated_at` 截断证据；交互式近期报告不得暗中回退成全历史。
  scheduler 的长期 shadow controller 不传窗口，继续使用全历史积累。
- Opportunity overview 同时报告窗口内数据库总数、实际重建数和过滤后显示数。
  `--limit` 只是重建成本上限，不得冒充窗口样本量。
- sizing trace 的历史计划值来自持久化 proposal notional，实际成交优先来自该
  proposal 的 fills。按当前配置反算的值只能标为 current-policy projection，不得
  描述成历史决策事实；position 仅是缺 fill 旧链路的 fallback。

## 11. 完成标准

- 相关关卡 pass/reject 都有真实事前记录，不从最终状态反推。
- 无方向候选不会生成可学习净收益。
- 拒绝候选和放行候选使用同一时点/成本模型结算。
- 反馈统计去重候选，按 family+direction+market+horizon+version 分层，优先收缩净期望。
- 未达样本/覆盖门或属于安全硬门时，不产生自动调参建议。
- 所有新证据写入不影响原决策返回值。本阶段明确的行为修正只有：
  批次补位顺序、shadow 真实 selected 标记，以及将 `XAUT/PAXG`
  从错误的美元稳定币分类中移出。
