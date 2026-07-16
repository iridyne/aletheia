# Opportunity 全生命周期与盈利能力评审

> 日期：2026-07-10
> 范围：顶层产品目标、机会发现、粗筛、打分、AI 判断、提案、风控、执行、结果记录、负反馈。
> 方法：当前源码逐段核对 + 可执行规则检查 + 本机历史数据库只读统计。
> 边界：不启用实盘，不修改凭证，不降低风险限制；历史运行数字只是本次诊断证据，不作为跨机器运行状态声明。

## 1. 结论

当前系统已经具备一条可运行的 `Opportunity -> Position` 主链，但还不能证明它是一台以净利润为目标的机会发现系统。问题不是单纯“交易太少”或“AI 太保守”，而是三层同时存在缺口：

1. **确定性断路**：配置允许现货和永续，但 OKX 永续的 24 小时成交额被读成 0，永续品种在最前面的成交额筛选中全部消失。系统表面支持做空，实际发现池长期只剩现货。
2. **经济含义写反**：`short_bias` 把负资金费率当成“多头付费”，奖励已经拥挤的空头，同时把正资金费率误认为空头拥挤。这会把做空候选向更高挤压风险的方向推。
3. **统计闭环不可信**：漏斗按同一时间窗分别数各表，而不是跟踪同一批机会，能产生 400% 转化率；前向收益不含手续费、滑点和资金费；学习权重把方向混在一起，并主要使用胜率而非每笔期望收益。

因此，当前最优路线不是放宽过滤器来增加交易，而是先恢复正确的候选供给和可信的结果度量，再做有对照组的方向/过滤实验。否则“多交易”只会加速学习错误。

## 2. 本次证据快照

以下是 2026-07-10 对本机历史数据的只读诊断，不代表其他机器或未来状态：

- 最近 24 小时：80 个机会、12 条待判断信号、48 条 AI 判断、20 条交易类判断、15 个提案、1 个最终执行/持仓。
- 旧漏斗给出 `signal_pending -> evaluated = 4.0`，证明它在混合不同来源和不同批次的行，不能解释真实转化。
- 最近 7 天 Universe 对照：入选标的平均表现弱于同时间未入选对照，差值约 `-0.55%`；24 小时观察差值更差。重复的 10 分钟快照存在相关性，样本量不能按独立交易理解，但方向性证据足以说明选币尚未产生稳定优势。
- 家族和方向差异很大：`momentum long` 与 `momentum short`、`mean_reversion long` 与 `mean_reversion short` 的结果相反，但自动校准仍按家族合并。
- 8 个可调前置过滤器都处于“证据不足”，因为系统没有持久化每个过滤器具体放行/拒绝了哪些候选。当前拒绝自动调参是正确行为。
- 资产注册表含大量现货和永续，但最近 Universe 特征和成员只有现货。直接复现发现 OKX 永续 ticker 的 `quoteVolume=None`，而真实成交量需要由 `baseVolume * contractSize * last` 换算。
- Round 1 历史结果为负：这说明当前阶段的目标应是找到损失来自供给、方向、入场还是退出，而不是把基础设施“活着”当成盈利健康。

## 3. 顶层设计评审

### 3.1 产品目标存在三个版本

当前权威文档同时描述了三种不同产品：

- `docs/governance/policies/implementation_guide.md` 仍把 MVP 限定为 Bitget + BTC/ETH/SOL，并明确把 OKX、xStocks、新闻和预测市场放到以后。
- `docs/product/definition/PRD.md`、`METRICS.md`、`PHASE_GATES.md` 仍把已删除的 RuleSet、回测和策略晋级引擎写成已完成或当前门槛。
- 当前代码实际是 OKX/Bitget 可切换、动态加密 Universe、新闻、预测市场、股票观察和 Serenity 研究并存。

后果是：测试可以对旧目标全绿，代码也可以对新目标运行，但没有一份顶层文件明确回答“现在什么资产能被发现、什么资产能执行、以什么净收益指标判定好坏”。

### 3.2 盈利目标没有被操作化到发现层

`VISION.md` 把“学习装置产生 alpha”作为核心赌注，这个方向合理，但缺少五个发现层指标：

- **发现覆盖率**：市场中事后有意义的走势，有多少在发生前进入候选池。
- **错误发现率**：进入候选池但扣除成本后无价值的比例。
- **机会成本**：被过滤掉的候选中，有多少本可产生净收益。
- **可执行净期望**：前向收益扣除手续费、双边滑点、资金费和成交失败概率后的期望。
- **容量和时效**：一个信号在被 AI 判断、排队和下单后还剩多少优势。

只统计机会数、胜率、最近提案和基础设施健康，无法判断发现器是否在赚钱。

### 3.3 “Go-Live 分数”不能代表可以盈利

Go-Live 评分的大部分权重来自连通性、数据、模型、对账和无风险违规。它适合表示“系统是否可控”，不适合表示“策略是否有优势”。`PHASE_GATES.md` 另有 12 周正期望前置条件，但 `VISION.md` 把项目终点主要等同于分数达到 70，容易产生误读。

建议以后明确拆成两个互不替代的门：

- **工程可控门**：运行、对账、风控、审计。
- **盈利证据门**：滚动净期望、Profit Factor、最大回撤、不同市场状态稳定性、对照组优势。

任一不通过都不能进入实盘。

### 3.4 指标文档有数学错误和过时消费者

- `METRICS.md` 的 Average R 公式把数量乘在分母外，量纲不正确；风险金额应包含 `abs(entry-stop) * quantity`。
- North Star 的“策略通过 walk-forward”依赖已删除的策略引擎。
- `doctor` 的 `loop.profit=ok` 只说明最近 6 小时有提案，不说明提案被批准、成交或盈利。这个名字应改成“proposal flow”或增加真实盈利状态。

## 4. 生命周期逐段评审

| 阶段 | 当前实现 | 主要问题 | 判断 |
|---|---|---|---|
| 资产注册 | CCXT 同步现货/永续，加入成交量排名标的 | 资产类别固定为 crypto；FX 不存在；XAUT/PAXG 被当稳定币永久排除 | 部分可用 |
| 特征采集 | ticker 预筛后并发 OHLCV、funding/OI/盘口 | OKX 永续 `quoteVolume=None` 被当 0；增强数据缺统一新鲜度门；现货/永续结果记录仍可能混价 | 有 P0 断路 |
| 可交易过滤 | 成交额、点差、新鲜度、黑名单 | 无每个过滤器的持久化放行/拒绝事实，负反馈无法归因 | 规则可跑，无法学习 |
| Scanner | momentum、mean reversion、volatility、derivatives、event、short bias 竞争 | 赢家通吃丢掉同币种的竞争假设；short funding 符号相反；event 只是价格事件，不是真新闻事件 | 有 P0/P1 缺陷 |
| 分配 | 家族配额 + 探索/利用 | 现货/永续先占配额后去重，删除重复项不补位；对照组未按流动性/市场类型匹配 | 有确定性容量损失 |
| Opportunity 生成 | deterministic emitter + market observer LLM | market observer 按固定顺序串行调用 LLM，时间耗尽时尾部品种可永久饥饿；新闻只丰富已入选标的，不能发现 Universe 外标的 | 覆盖有偏 |
| Ranker | 分类置信度硬门 + 连续分数 + guards | 分类和数值置信度可能冲突；没有最低 `trade_score`；同一 late/crowding 信息重复扣分；只奖励胜率，不看盈亏幅度 | 可能错杀/错排 |
| System Operator | LLM 决策 + debate + 两个方向刹车 | `bear_case` 只存在字段，schema/parser 未接通；两个方向刹车都数“提案”而非真实敞口，风险拒绝也会造成后续压制 | 假接通 + 偏差风险 |
| 提案/取价 | 继承市场类型，生成入场/止损/止盈 | 取价发生在市场类型解析前，可能用现货价构造永续提案 | 市场语义缺陷 |
| 风控/执行 | 唯一 Risk Engine + Execution Gateway | 本次未发现需要放宽的风险门；历史大量 `$0 cap` 拒绝来自已修 bug，不应拿来调策略 | 保持不动 |
| 仓位大小 | 波动率目标 + sample-aware Kelly | 大多数提案无 strategy_id，样本数永久为 0；Kelly 在 cap 和最终 sizing 中重复作用 | 长期过小且不学习 |
| 结果记录 | 15m/1h/4h/24h 前向收益 + shadow + closed position | 前向结果无 market_type，永续可能用现货结算；不含成本/资金费；pending shadow 被显示成 resolved | 结果语义不可信 |
| 自动学习 | 家族乘数、因子反馈、memo、guard candidate | 家族乘数混合 long/short，并主要按胜率；真实收益家族也按胜率回灌；负面 lesson 有惩罚但无对称的已验证正面奖励 | 可能强化错误方向 |
| 诊断 | opportunity audit / doctor / alpha | 漏斗不是因果 cohort；风险原因里的动态数字导致同类原因碎片化；任一正收益 horizon 即标“missed alpha”且未扣成本 | 不可作为调参依据 |

## 5. 资产覆盖能力矩阵

| 资产/信号 | 能发现 | 能纸上执行 | 结果可归因 | 当前结论 |
|---|---:|---:|---:|---|
| 加密现货 | 是 | 是 | 部分 | 当前唯一被持续选入的主路径 |
| 加密永续 | 设计上是 | 是 | 不完整 | OKX 成交额 bug 令发现层实际断路；结果缺 market_type |
| 山寨币 | 是 | 取决于交易所和新鲜价格 | 部分 | 动态 Universe 已覆盖，但选币 alpha 为负，需要重做实验 |
| 美股普通股票 | 是，yfinance | 否 | 只有 shadow | `asset_class=equity` 明确 observation-only |
| xStocks/RWA 股票代币 | Serenity 有特殊路径 | 受交易所支持限制 | 部分 | 与普通股票是两套路径，顶层文档未清楚区分 |
| 黄金 RWA（XAUT/PAXG） | 否 | 否 | 否 | 被稳定币列表硬排除，不符合“RWA 可发现”目标 |
| 传统外汇 | 否 | 否 | 否 | 研究提示词还明确拒绝 forex；没有 symbol/market/provider 模型 |
| 新闻 | 只能丰富已入选币 | 不直接执行 | 弱 | 不能从新闻独立发现 Universe 外机会 |
| 预测市场 | 是 | 否 | 观察级 | 作为全局上下文可能污染不相关币种；缺事件到标的映射质量评估 |

结论：不能把“多资产字段存在”描述成“多资产盈利系统”。当前可执行范围仍应诚实定义为加密现货/永续；其他资产先做观察、对照和影子结算，达到数据与执行语义要求后再逐类开放。

## 6. 已确认问题分级

### P0：先修，否则后续实验无效

| ID | 问题 | 直接后果 |
|---|---|---|
| OPR-01 | OKX 永续成交额为空时被当 0 | 永续/做空候选在第一关全部消失 |
| OPR-02 | `short_bias` 资金费率符号相反 | 奖励拥挤空头，增加 squeeze 风险 |
| OPR-03 | 同币种现货/永续分配后才去重 | 重复项占配额，最终池缩小且家族分布失真 |
| OPR-04 | 漏斗按独立时间窗计数 | 产生 >100% 转化，无法定位真实丢失阶段 |
| OPR-05 | `bear_case` schema/parser 未接通 | 文档和测试声称强制反方论证，实际决策永远为空 |
| OPR-06 | 提案按 symbol-only 取价 | 同币种现货/永续价格来源可能混用 |

### P1：修复后才能可信学习

| ID | 问题 | 修复方向 |
|---|---|---|
| OPR-07 | 前向结果无 market_type、成本、资金费 | 按 instrument 结算并保存 gross/net 两套收益 |
| OPR-08 | 家族自动权重混合方向且依赖胜率 | 至少按 family+direction+market+horizon，使用收缩后的净期望 |
| OPR-09 | 两个方向刹车重叠且数所有提案 | 合并；以实际敞口/已执行 cohort 为证据，未验证前 observation-only |
| OPR-10 | 无 strategy 的交易永远冷启动，Kelly 重复缩放 | 用 family+direction 样本数，Kelly 只应用一次 |
| OPR-11 | Ranker 无最低经济分，重复扣 late/crowding | 统一每个因子的唯一责任点；阈值由 shadow 对照实验决定 |
| OPR-12 | 顺序 LLM 循环尾部饥饿 | 持久化 round-robin 游标或确定性轮转 |
| OPR-13 | pending shadow 被标 resolved | 只在有 resolved_at 或 pnl 时生成 resolved 事件 |
| OPR-14 | “missed alpha”不扣成本且任一 horizon 为正即成立 | 改为 gross move / net tradable alpha 两级判断 |
| OPR-15 | 每个前置过滤器没有决策记录 | 先落 filter decision，再允许自动负反馈调参 |

### P2：多资产与新闻扩展

- 建立明确的 `asset_class + instrument_type + venue + execution_capability` 能力表。
- FX、RWA、股票必须先有合法 symbol、交易时段、报价币、成本、结算和影子结果，再进入统一 Opportunity。
- 新闻发现器应先输出“事件 -> 受影响标的 -> 方向 -> 时效 -> 证据”，不允许把所有新闻作为所有币的全局上下文。
- 赢家通吃 scanner 应改为“每个标的可保留有限个互斥假设”，随后由组合层比较假设，而不是在发现层提前丢弃。

## 7. 正确的盈利实验框架

每次只改一个可识别的过滤/排序因素，至少保留四类记录：

1. **候选全集**：进入该过滤器前的 point-in-time 候选。
2. **处理组**：过滤器放行的候选。
3. **对照组**：过滤器拒绝但继续做影子结算的候选。
4. **净结果**：相同持有周期下的方向收益，扣手续费、点差、滑点和资金费。

判定优先级：

- 先看净期望和 Profit Factor，不先看胜率。
- 再看最大不利波动、最大有利波动和成交率，解释优势是否能实现。
- 按 `family + direction + market_type + regime` 分层；样本不足时保持中性，不自动放大。
- 使用不重叠或做相关性修正的样本；10 分钟重复快照不能当作独立交易。
- 任何自动调参必须有最大步长、回滚条件和观察期，不允许同一批数据既选择参数又证明参数有效。

## 8. 完成标准

机会系统只有同时满足以下条件，才可称为“以盈利为导向且可评估”：

- 配置开启的每种市场类型都能进入特征层，doctor 能显示按市场类型的覆盖率。
- 任一漏斗转化率都来自同一机会 cohort，永远不可能超过 100%。
- 每个过滤器的放行和拒绝都有不可变记录，对照组持续结算。
- 结果同时保存 gross 和 net，net 至少包含手续费、点差/滑点和资金费。
- 自动学习不混合经济含义相反的方向或市场类型。
- `doctor` 把“流水线活着”和“滚动净盈利为正”分开报告。
- 股票、FX、RWA、预测市场分别显示 observation-only / shadow / executable，不用一个 `asset_class` 字段制造能力错觉。
- 实盘仍需独立通过工程可控门和长期盈利证据门，任何一个分数不能替代另一个。

## 9. 本轮实施边界

本报告配套的第一批代码只修确定性和语义问题，不直接改变风险限额、实盘门或订单执行语义：

- 恢复 OKX 永续成交额换算。
- 在分配前按首选市场去重。
- 修正做空扫描器资金费率方向。
- 真正接通 `bear_case` schema、提示词和 parser。
- 提案按最终 market_type 读取价格。
- pending shadow 不再伪装成 resolved。

其余会改变交易选择或仓位大小的问题，按配套修复计划逐批做可回放实验后实施。

## 10. 实施状态（2026-07-10）

本轮已经实现并由回归测试锁定：

- OPR-01：OKX 永续成交额可从合约数、合约面值和价格换算，预筛/特征共用同一函数。
- OPR-02：`short_bias` 拒绝深度负 funding 的拥挤空头，正 funding 只支持已有做空论点。
- OPR-03：同 symbol 的首选 spot/perp 在配额分配前去重。
- OPR-04：漏斗保留事件吞吐，但转化、丢失和主要卡点改为同一 Opportunity ID cohort。
- OPR-05：`bear_case` 已进入必填 LLM schema、提示词、parser 和提案元数据。
- OPR-06：提案先确定最终 market type，再读取对应 snapshot。
- OPR-13：pending shadow 不再生成 `shadow_resolved` 事件。
- doctor 增加按 spot/perp 的 Universe feature 覆盖诊断；`loop.profit` 成功信息明确只代表 proposal flow，不代表盈利。
- 风控拒绝原因会归一动态金额，保留原始示例，不再把同类 `$68.8/$71.2/...` 拆成单条原因。

尚未实现且不得在没有对照实验时直接改交易行为：OPR-07~12、OPR-14~15，以及多资产执行扩展。它们继续以修复计划为准。

### 阶段 D 补充（2026-07-10）

- OPR-07：`universe_forward_outcomes` 已持久化 venue、market_type、方向毛收益、手续费、点差/滑点、资金费、净收益、MFE/MAE 和结算质量；结算按目标 InstrumentId 与 due_at OHLCV，不再用同名现货或过晚当前价冒充。
- OPR-14：`missed_alpha_suspect` 只在净收益超过持久化安全余量时成立；正毛走势但成本后不够或成本未知时标 `positive_gross_move_only`。
- 校准、family edge、doctor signal health、lesson evidence 和 Tier-1 guard 改为只消费完整 net 结果；历史 gross-only 样本不再参与自动学习。
- 分层键扩展为 scanner_family + direction + market_type + horizon；Universe UI 同步显示该粒度，避免展示层重新混合。
- 阶段 D 完成当时尚缺逐过滤器对照；该项后续实施状态见下方“阶段 E 补充”。方向刹车/ranker 实验（OPR-08~11）、LLM round-robin（OPR-12）和完整多资产阶段 G 仍未完成。

### 阶段 E 补充（2026-07-10）

- OPR-15：已建立逐候选 `filter_decisions` 和高频无方向 `filter_decision_batches`。ticker 可用性、下载前成交额、稳定币/黑名单/成交额/点差/新鲜度、Observer、Ranker、持仓重复/冷却/批次上限、System Operator、Universe 配额对照和 Risk 统一索引已留痕。
- 无事前方向的前置门不创建可学习收益，避免事后选更赚方向。已有 long/short 的候选共享 `filter_candidate_outcomes` 的 15m/1h/4h/24h exact-instrument 净结果。
- filter controller 不再使用整个 family 的胜率猜测某个门槛，只比较同 filter version、family、direction、market 和 horizon 的 pass/reject 净结果，用 `k=20` 收缩；只可报 `overrestrictive_suspect`，不自动改参数。
- 新 shadow/control 已保存 venue、market 和成本后 net；历史 gross-only 对照不再进入 ranker/System Operator alpha 基线。RiskDecision 仍是唯一权威风控事实。
- 修正了 Top-N 选择顺序：先检查已持仓和冷却，再应用批次上限，高排名候选被拒后低排名可补位。
- `XAUT/PAXG` 已从美元稳定币排除集移出，只恢复参与普通交易所/流动性检查的资格，不绕过风控、执行或实盘锁。
- 新分析证据有界保留：高频批次 30 天，逐候选和结果 90 天。交易提案、权威风控、订单、成交、持仓和审计仍不清理。

仍未实施且必须另立实验：合并两个方向刹车、修改 ranker 经济门/权重、family 生产校准、Kelly/sizing、LLM round-robin 和完整多资产能力表。
