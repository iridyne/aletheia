# 数据层增强 — Backlog（Phase 5 & 6）

> 已决定跳过，留作未来参考。触发条件明确时再实施。

## Phase 5: 能力声明模式 (Capability Declaration)

**动机**：FallbackChainProvider 通过 try/except NotImplementedError 发现 provider 限制。

**当前实际**：CoinGeckoProvider 在网络请求前本地检查 timeframe，raise 成本约等于零。3 个 provider 下无实质收益。

**设计**：
- `ProviderCapabilities` dataclass（supported_timeframes, has_mark_price, has_funding_rate, name）
- Protocol 新增可选 `get_capabilities() -> ProviderCapabilities`
- chain.py 按 capabilities 预过滤，跳过无能力的 provider

**触发条件**：
- provider 数量 >5
- 需要按能力路由（如 mark_price 只问 exchange provider）
- get_ohlcv 出现因 provider 网络超时导致的延迟问题

**估计 scope**：M，~180 行跨 5 文件，1.5 天

---

## Phase 6: 多资产扩展准备 (AssetClass Enum)

**动机**：`asset_class` 在 12+ 文件中是裸字符串 "crypto"/"equity"/"prediction"，无中心定义。

**当前实际**：系统只交易 crypto。xStocks 走 Bitget crypto 通道。CLAUDE.md 明确 "no new exchanges, no uncontrolled expansion"。

**设计**：
- `AssetClass(str, Enum)`: CRYPTO / EQUITY / FOREX / COMMODITY / PREDICTION
- `_DATA_SOURCE_STRATEGY` 注册表：per-class ticker/ohlcv provider + has_futures 标记
- `get_data_strategy(asset_class) -> dict`
- str, Enum 与现有字符串比较兼容，零破坏性

**触发条件**：
- 真正需要接入 forex/commodity 数据源
- 出现 per-asset-class 路由需求（不同资产类型用不同 provider factory）

**估计 scope**：S，~100 行，1 天

---

## 已完成的 Phase（参考）

| Phase | 内容 | 状态 |
|-------|------|------|
| 1 | 跨源偏差告警（chain.py notify + cross-source check） | Done |
| 2 | Provider 穿透缓存热路径（price_feed + pnl） | Done |
| 4 | Doctor 数据质量探针 | Done |
| 3 | 价格语义分离（get_price_for_pnl/risk/liquidation） | Done |
