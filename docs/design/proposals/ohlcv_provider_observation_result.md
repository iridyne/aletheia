---
id: ohlcv-provider-observation-result
status: implemented
related_contracts:
  - technical_analysis_contract
supersedes: []
---

# OHLCV Provider Observation/Result Contract Proposal

## Summary

本提案定义 `MarketDataProvider.get_ohlcv()` 返回非空结果时必须满足的最小
语义：请求的 timeframe 必须精确成立，OHLCV 五类数值必须来自实际观测，返回行
必须保留一致的 source/venue 身份。无法满足时，provider 应明确表示 unsupported，
让现有 fallback chain 继续，而不是返回更粗粒度或缺字段的数据。

Owner 已批准 exact OHLCV 方向，provider boundary 已按本提案实施。当前规范行为由
`docs/design/contracts/ohlcv_provider_result_contract.md` 持有；本文保留设计决策历史。

## Current fact

当前 `OHLCVResult` 已经是 list-compatible 结果，保留 requested count、accepted、
rejected、attempts 和 complete/partial/empty outcome。每根 `Candle` 也保留
`provider` 与 `exchange`。CCXT 和 Binance Public 会解析真实 volume，fallback
chain 返回第一个非空结果，并继续处理 unsupported 或 empty provider。

当前缺口集中在 CoinGecko OHLC adapter：

- `1h` 与 `4h` 请求都使用 `days=7`；
- `1d` 请求使用至少 31 天的窗口；
- endpoint 响应只有 timestamp/OHLC，adapter 把缺失 volume 写成 `0.0`；
- 返回结果没有告诉消费者实际粒度或 volume 缺失。

CoinGecko 官方说明，无显式 `interval` 时，3–30 天窗口自动返回 4 小时 candle，
31 天以上自动返回 4 天 candle；响应结构也不包含 volume。参见
[Coin OHLC Chart by ID](https://docs.coingecko.com/reference/coins-id-ohlc)。当前免费
adapter 没有传入付费计划的 `interval=hourly|daily`。

因此，primary/CCXT fallback 失败后，市场上下文可能把 CoinGecko 4 小时 candle
当成 `1h` 技术输入，并把合成的零 volume 用于 volume statistics。Dashboard route
也可能在响应 instrument header 中继续写请求的 timeframe。结果数量即使为
`complete`，也不能证明分辨率或字段语义正确。

历史下载和 protective-barrier refresh 已分别固定到 native CCXT 与精确 venue，
不会进入 CoinGecko/Binance spot fallback；这两个安全边界保持不变。

## Goals and non-goals

目标：

- `get_ohlcv(timeframe=X)` 的非空结果只包含精确 `X` candle；
- O/H/L/C/V 都是来源提供的观测值，不用 `0.0` 伪装缺失 volume；
- 返回的非空行来自一个 source/venue，身份不会在 fallback 或 cache 中丢失；
- 继续复用现有 `OHLCVResult` 数量、质量和 attempt 事实；
- 不兼容 provider 走现有 unsupported→继续 fallback 路径。

非目标：

- 不建立 Provider descriptor、capability registry 或持久 health snapshot；
- 不改变 provider 顺序、venue cooldown、quota 或网络策略；
- 不允许 consumer 默默接受更粗粒度数据；这种需求属于后续
  consumer-specific fallback contract；
- 不增加 result-level `observed_at`。当前消费者使用 bar timestamp；fetch time 不能
  自动成为 freshness 权威；
- 不改变 Ticker、SQLite schema、Risk、Execution Gateway 或 Live 权限；
- 不引入新的 envelope 类、schema 库或第二套 Candle 类型。

## Proposed design

### Exact OHLCV result

`MarketDataProvider.get_ohlcv()` 的非空返回必须同时满足：

1. 实际 candle interval 与请求的 canonical timeframe 相同；
2. 每根 candle 的 open/high/low/close/volume 都来自实际 source 数据；
3. provider boundary 的 finite、OHLC geometry 和 non-negative volume 校验通过；
4. 所有返回行的 `provider` 与 `exchange` 非空且各自一致；
5. 行保持 oldest→newest，forming bar 继续由 `is_closed=false` 表示。

只要 provider 无法证明其中任何一项，就不得返回非空 `OHLCVResult`。静态不支持
应抛出 `NotImplementedError`；网络或解析失败继续走现有失败 attempt。空响应仍是
`empty`，不能冒充 unsupported 或 failure。

`OHLCVResult.outcome` 继续只描述数量与质量：accepted 少于 requested 或存在
rejected rows 时为 partial。由于 exact timeframe 和真实 OHLCV 已成为返回前置条件，
outcome 不需要再承载第二套 resolution 状态。

### Provider behavior

- CCXTProvider 保持 exact timeframe 请求、实际 venue 身份和真实 volume。
- BinancePublicProvider 只对 `_TF_MAP` 明确支持的 timeframe 提供结果；未知 timeframe
  应是 unsupported，而不是把任意字符串传给远端后再猜测。
- CoinGeckoProvider 继续提供 ticker。当前 `/coins/{id}/ohlc` 路径不提供真实 volume，
  因此暂不具备 `get_ohlcv` capability；所有 OHLCV timeframe 都应明确 unsupported。
- FallbackChainProvider 继续在 `NotImplementedError`、empty 或 failure 后尝试下一个
  eligible provider，且不合并多个 provider 的 candle 行。

未来若 CoinGecko 或另一个 source 能提供精确 timeframe 与真实 OHLCV，必须先用
官方接口事实和 focused fixture 证明，再恢复其 OHLCV capability。不能通过把缺失
volume 填零或把 4 小时 candle 重采样成伪 `1h` 来满足本契约。

### Consumer behavior

- Market Context、Regime、Universe outcome 和 Dashboard chart 继续调用现有
  `get_ohlcv()`；成功返回即代表 exact OHLCV，不需要各自复制 resolution 判断。
- Dashboard route 在 provider 失败时仍可读取 exact instrument/timeframe 的 SQLite
  数据；没有 exact 数据时保持 unavailable。
- History Downloader 与 Protective Barrier 继续固定 native CCXT/exact venue，不扩大
  fallback authority。
- Technical Analysis Contract 的 forming-bar、missing-interval、quality 与 exact
  instrument invariants 保持权威。

## Stories for owner review

### Story 1: requested 1h, primary unavailable

Market Context 请求 BTC/USDT `1h`。CCXT primary 和 CCXT fallback 都失败。当前路径
可接受 CoinGecko `days=7` 返回的 4 小时 OHLC，把它标成 `1h`，并把 volume 设为
零。随后 RSI/ATR/volume statistics 使用了错误时间身份。

提案行为是把 CoinGecko OHLCV 视为 unsupported，继续尝试 Binance Public 的真实
`1h` OHLCV。若仍无结果，消费者得到 unavailable/degraded，而不是错误的技术事实。

### Story 2: requested 1d chart

Dashboard 请求 `1d`。当前 CoinGecko 路径使用 31 天以上自动粒度，官方行为是 4 天
candle，但 route 仍可能显示请求的 `1d` identity。

提案行为是不返回该不兼容结果。Dashboard 使用 exact SQLite fallback；若不存在，
诚实显示 unavailable。

### Story 3: protective 1m bars

Protective Barrier 需要精确 venue 的 completed `1m` bar。它已经使用 native CCXT、
固定 fallback exchange，并从 exact SQLite identity 验证持久结果。

提案不改变该路径，也不允许 CoinGecko/Binance spot fallback 扩大到 derivative 或
保护性价格权限。

## Trust and complexity review

较大的方案是在 `OHLCVResult` 增加 requested/actual timeframe、field availability、
observed time 和 consumer policy，再要求每个调用方选择是否接受。这会把一个可在
provider boundary 解决的语义错误扩散到所有消费者。

本提案采用更小的规则：名为 `get_ohlcv(timeframe)` 的接口只返回 exact OHLCV。
它复用已有 `NotImplementedError` fallback、Candle provenance、quality validator 和
OHLCVResult outcome，不增加新状态或持久化。未来确有“允许 coarse OHLC”的命名
消费者时，再单独定义不同结果类型和明确权限。

## Verification contract

在本提案成为当前 Contract 前，最小可执行证据必须证明：

1. CoinGecko OHLCV 在发起网络请求前明确 unsupported，ticker 行为不变；
2. CoinGecko unsupported 后，FallbackChainProvider 会继续到支持 exact timeframe 与
   真实 volume 的 provider；
3. Binance Public 对未知 timeframe 明确 unsupported；
4. 非空 provider result 的 rows 拥有一致、非空的 provider/exchange identity；
5. Market Context 的 exact `1h` fixture 不会接收 4 小时间隔或合成零 volume 的
   CoinGecko candle；
6. History Downloader、Protective Barrier、Risk、Execution 与 SQLite schema 没有
   行为变化。

验证只需 focused provider/market-context tests、Ruff 和相关 source-contract checks；
不需要在 macOS 运行全量 pytest。

## Rollout and rollback

实现应是一个 provider-boundary 小切片：收窄 CoinGecko OHLCV capability、限制
Binance timeframe、补 focused fixtures。进程级 OHLCV cache 随进程重启清空，
不需要数据库迁移或历史重写。

若实施后 exact fallback 可用性显著不足，应先记录具体 consumer、timeframe 和失败
率，再提出 consumer-specific fallback contract。回滚为静默粗粒度或合成 volume
不符合本提案；临时恢复必须明确标为 degraded observation，且需 owner 重新批准。

## Resolution

Owner 接受了“exact OHLCV，CoinGecko 仅保留 ticker capability”方向。实现收窄了
CoinGecko OHLCV capability，限制 Binance Public timeframe，并保留现有 fallback、
SQLite 与安全边界。规范内容已经进入
`docs/design/contracts/ohlcv_provider_result_contract.md`，本 Proposal 状态为
`implemented`。
