# Opportunity 盈利闭环修复计划

> 来源：`docs/research/OPPORTUNITY_LIFECYCLE_PROFITABILITY_REVIEW_2026-07-10.md`
> 原则：先修断路和统计真相，再做策略实验；每批可单独验证和回滚。
> 不变边界：不启用实盘，不降低风险门，不让新闻/股票/预测市场绕过 Execution Gateway。

## 当前进度（2026-07-10）

- **已完成**：阶段 A、阶段 B、阶段 C；阶段 D 的 market-aware gross/net 结果层、目标 horizon 取价、成本/MFE/MAE、净收益消费者迁移已实现。
- **待实施**：阶段 E 至 G。阶段 E/F 会改变交易选择或仓位，只能在 filter 决策记录和拒绝候选对照完成后分项实验。
- **明确不做**：因当前 7 天样本表现差就直接放宽过滤、删除方向刹车或增大仓位；现有统计还不足以证明这些改动会增加净利润。

## 阶段 A：恢复真实候选供给

### A1. OKX 永续成交额

- `features.py` 优先使用 `quoteVolume`。
- 缺失时用合约的 `baseVolume * contractSize * last` 换算报价币成交额；现货缺失时用 `baseVolume * last`。
- 预筛和最终特征必须调用同一个函数，避免“第一关通过、第二关又变 0”。
- 单元测试覆盖 OKX 永续、正常 quoteVolume、无法换算三种情况。

### A2. 首选市场先去重再分配

- 在 family quota / explore-exploit 之前，对同 symbol 的 spot/perp 选出配置要求的市场。
- 最终数量和配额都基于唯一 symbol，避免删掉重复项后不补位。
- 端到端测试构造重复 BTC + 足量其他币，断言最终能填满名额且 BTC 保留 perp。

### A3. 做空资金费率语义

- 深度负 funding 表示空头付费/拥挤，阻止 `short_bias`。
- 正 funding 表示多头付费，可作为做空的温和支持，但不能单独产生信号。
- 只修正符号，不在本批猜测新阈值；阈值另走 shadow 实验。

## 阶段 B：修复生命周期语义

### B1. 强制反方论证真实接通

- `_DECISION_SCHEMA` 定义并要求 `bear_case`。
- 提示词明确返回该字段。
- `_parse_decision` 读取并限制为字符串。
- 把只检查“源码里有 bear_case 字样”的假测试改为行为测试。

### B2. 市场类型取价

- 在读取 snapshot 前完成 `market_type` 解析。
- `latest_snapshot(symbol, market_type=...)` 精确优先，保留旧数据 fallback。
- 测试同币种 spot/perp 两个价格，断言 proposal 使用目标市场价格。

### B3. 影子结果诚实

- pending shadow 只显示 pending，不创建 `shadow_resolved` timeline event。
- 有 `resolved_at` 或 PnL 后才创建 resolved event。

## 阶段 C：重建可解释漏斗

- 保留“时间窗事件量”用于看系统吞吐，但不再把它叫转化率。
- 新增以 `opportunities.created_at` 为 cohort 的唯一机会计数：
  `opportunity_id -> pi_decision -> proposal -> risk -> order -> position`。
- 只对能因果关联的行计算转化，旧数据缺 ID 时显示覆盖率不足。
- 风险原因移除动态金额后聚类，同时保留一个原始样例。
- 完成标准：任何 conversion 都在 `[0, 1]`，且测试构造跨窗口事件不能污染 cohort。

实施说明：事件量保留在 `counts`；同批机会转化放在 `causal_cohort_counts`，所有 `conversion_rates` 和主要卡点只读取后者。风险拒绝原因中的动态金额归一后聚类，同时返回一个原始示例。

## 阶段 D：净收益结果层

- `universe_forward_outcomes` 增加 market_type；按 spot/perp 分别取价。
- 记录 gross return、estimated fee、spread/slippage、funding、net return。
- 保存 MFE/MAE 或至少用周期内高低价近似，区分“方向对但入场/退出差”。
- `missed_alpha_suspect` 只有 net return 超过最小可交易优势时成立；否则显示 `positive_gross_move_only`。
- 数据表变更同步 `data_model.md`、`api_reference.md` 和对应可执行规则。

实施说明：权威公式见 `docs/design/opportunity_outcome_contract.md`。历史 gross-only 行不回填净收益；永续缺入场 funding、方向未知或价格时点不可信时 net 保持 NULL。`missed_alpha_suspect` 只由高于安全余量的 net 结果触发，毛方向正确单独标为 `positive_gross_move_only`。

## 阶段 E：负反馈重构

- 新表或不可变事件记录每个过滤器的：输入、决定、原因、版本、候选快照 ID。
- 被拒绝候选继续 shadow 结算，形成同批对照。
- 家族学习至少分 `scanner_family + direction + market_type + horizon`。
- 学习目标从胜率改为收缩后的净期望；胜率仅作解释变量。
- 删除/合并两个重叠方向刹车；在有对照证据前只告警，不硬挡。
- Ranker 每个特征只扣一次；增加最低经济质量门时，阈值必须来自历史回放而非拍脑袋。

## 阶段 F：仓位和样本身份

- strategy engine 删除后，样本身份改为 family+direction+market+regime 或明确的 hypothesis ID。
- Kelly 只作用一次；波动率 cap 与 conviction multiplier 分工明确。
- 仓位修复先做离线 sizing trace 对比，不改变风险引擎硬上限。

## 阶段 G：公平覆盖和多资产

- market observer 使用持久化 round-robin，避免固定尾部长期没有 LLM 机会。
- 新闻先做事件到标的映射，允许发现 Universe 外候选，但先 observation/shadow。
- RWA、股票、FX 分别建立 capability matrix；没有报价、成本、交易时段和合法执行 adapter 前不得标 executable。
- XAUT/PAXG 从“稳定币”概念中拆出；是否纳入交易仍需专门的流动性和报价实验。

## 验证顺序

1. 每个行为改动先有聚焦回归。
2. `uv run pytest tests/specs/spec_universe_pipeline.py tests/specs/spec_decision_lifecycle.py -q`
3. 对应模块测试。
4. `uv run pytest tests/specs/ -q`
5. `uv run aletheia doctor`
6. 合并到主仓后才重启主 gateway；禁止从空数据 worktree 重启。

## 交付批次

| 批次 | 内容 | 风险 |
|---|---|---|
| 1 | A1/A2/A3 + B1/B2/B3 | 低到中；恢复供给和语义，不动风控/执行 |
| 2 | 因果漏斗 + 原因聚类 | 低；只读分析口径变化 |
| 3 | market-aware gross/net outcome | 中；数据模型和学习输入变化 |
| 4 | filter decision + shadow 对照 | 中；新增观察数据，不自动调参 |
| 5 | 学习权重/方向刹车/ranker | 高；改变交易选择，必须分实验 |
| 6 | sizing identity + Kelly | 高；改变名义仓位，风险上限保持不变 |
| 7 | 新闻/RWA/股票/FX 扩展 | 高；逐资产授权，不做一次性大爆炸 |
