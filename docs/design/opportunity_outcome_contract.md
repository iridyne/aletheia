# Opportunity Outcome Contract

> 状态：阶段 D 权威设计，2026-07-10。
> 范围：Universe 选出的 Opportunity 在固定持有期后的反事实结果，以及这些结果如何进入诊断和学习。
> 不包含：实盘盈亏记账、风控限额、订单执行、自动调参。

## 1. 目标

结果层回答的不是“价格后来涨了吗”，而是：

> 在当时可见信息、目标市场和目标方向下，按可解释的交易成本估算，这个机会是否留下了可交易的净优势？

毛价格变化只能用于解释市场走势，不能直接驱动 missed-alpha、过滤器评价、scanner 校准或自动守卫。

## 2. 工具身份

每条 `universe_forward_outcomes` 必须保存：

- `venue`：产生该候选的行情场所。
- `symbol`：内部 canonical symbol，例如 `BTC/USDT`。
- `market_type`：`spot` / `perp` / `futures`。
- `direction`：`long` / `short`；未知方向只能记录毛价格变化，不能计算净交易结果。

取价边界必须使用完整的 `InstrumentId(venue, symbol, market_type)`。永续取价失败时不得回退到同名现货。

## 3. 固定持有期

支持 `15m`、`1h`、`4h`、`24h`。结算优先读取从 `entry_ts` 到 `due_at` 的目标市场 OHLCV，并使用目标周期末的 close；不得把任务实际运行时的当前价格伪装成目标周期价格。

只有在结算延迟不超过持有期的 10% 时，才允许使用 ticker 作为近时点 fallback。更晚的 ticker 仍可保存毛走势供排障，但净收益必须为空，不能进入学习。

## 4. 收益字段

字段单位统一为百分比数值，例如 `0.25` 表示 `0.25%`：

- `forward_return_pct`：兼容旧消费者的原始价格变化，等同 `gross_return_pct`。
- `gross_return_pct`：`(exit - entry) / entry * 100`，不含方向。
- `directional_return_pct`：long 使用 gross；short 使用 `-gross`。
- `estimated_fee_pct`：双边手续费，`2 * trading.execution_fee_pct`。
- `estimated_spread_slippage_pct`：一轮完整交易的点差与双边滑点，`(entry_spread_bps + 2 * trading.slippage_bps) / 100`。缺失实测点差时使用 Universe 可交易点差上限并保留成本模型版本。
- `estimated_funding_pct`：正数表示成本，负数表示收入。现货为 0；永续按入场资金费率、8 小时周期和持有时长线性估算。正 funding 下 long 支付、short 收取。
- `net_return_pct`：`directional - fee - spread/slippage - funding`。
- `min_tradable_edge_pct`：估算误差安全余量，取 `max(0.05%, 单边配置滑点)`。

永续缺少入场资金费率、方向未知、价格时点不可信时，`net_return_pct` 必须为 NULL。禁止把未知成本按 0 处理。

## 5. 路径质量

- `mfe_pct`：持有期内按方向计算的最大有利变化，最小为 0。
- `mae_pct`：持有期内按方向计算的最大不利变化，最大为 0。
- `settlement_price_source`：`horizon_ohlcv` / `near_due_ticker` / `late_ticker`。
- `settlement_lag_sec`：实际结算时间相对 `due_at` 的延迟。
- `cost_model_version`：当前为 `v1`，使后续模型升级可分 cohort 比较，禁止静默改写历史。

MFE/MAE 是毛路径指标，用来区分方向错误与出入场表达错误；它们不替代净收益。

## 6. 反事实判定

按以下顺序判定：

1. `net_return_pct > min_tradable_edge_pct`：`missed_alpha_suspect`。
2. 毛方向收益为正，但净收益不足、缺失或未超过安全余量：`positive_gross_move_only`。
3. 有完整净结果且未盈利：`protective`。
4. 其他情况：`insufficient_data`。

因此“价格方向判断正确”与“当时值得交易”是两个不同事实。

## 7. 消费规则

- scanner 校准、family edge、doctor signal health、lesson evidence 和自动 guard 只读取 `net_return_pct IS NOT NULL` 的新结果。
- 历史 `forward_return_pct` 保留用于历史解释，不回填虚构成本，也不混入净收益样本。
- 任何按方向或市场聚合的消费者必须至少按 `scanner_family + direction + market_type + horizon` 区分；若上层暂时不能消费该粒度，应保持中性而不是合并相反经济含义。
- Alpha 对照组在拥有同口径成本前必须明确标为 gross comparison，不得声称是净盈利证据。

## 8. 安全边界

该结果层是 observation-only：不提交订单、不修改风险、不开实盘、不自动放宽过滤器。任何依据净结果改变排序、阈值或仓位的动作必须进入独立受控实验。
