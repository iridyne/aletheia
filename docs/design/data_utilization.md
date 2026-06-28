# 数据利用契约 (Data Utilization Contract)

> L2 契约。约束：**每一个采集到的数据域，都必须分门别类路由到一个具体的消费模块**——做到"物尽其用"，杜绝 collected-but-unused。
> SDD 编码：`tests/specs/spec_data_utilization.py`（DU-* claims），逻辑契约名 `data_utilization_contract`。

---

## 1. 第一性原理（理论支撑）

**数据存在的唯一理由是影响某个决策。** 从第一性原理推：

1. 一条采集来的数据，其价值 = 它对某个决策的**边际影响**。若无任何决策读它，价值 = 0。
2. 但它的成本 ≠ 0：采集 I/O、存储、prune 维护、审计链记录、doctor 探针、以及读代码时的认知负担。
3. 更危险的是**虚假丰富感**：表里躺着 `open_interest(hyperliquid)` 会让人（和未来的自己）误以为"系统在用跨所 OI"，从而在评估信号质量时高估覆盖面——这正是本项目反复踩的"喂幻觉"坑（[[aletheia-learning-loop-noise-not-signal]]）。
4. 推论：**无消费者的数据是纯负债**（成本>0，收益=0，且有误导风险）。

**但"物尽其用"≠把所有数据塞进 LLM。** 同样从第一性原理：LLM 的注意力是有限资源，prompt 里每多一行低相关数据，都在稀释高相关信号的权重（信噪比下降）。所以"用上"的正确含义是：

> 每个数据域要么被路由到**真正需要它的那个决策模块**（且以该模块能消化的形式），要么被**显式标注为 observation-only 并附理由**。两者之外的状态（采了、存了、没人读、也没标注）= 契约违反。

---

## 2. 契约规则

| 规则 | 说明 |
|---|---|
| **R1 单一去向** | 每个数据域至少有一个声明的决策链消费者，或显式 observation-only。 |
| **R2 消化形式** | 数据以消费模块能直接用的形式提供（已归一化/已聚合/单位可比），不是把原始 JSON 丢过去。 |
| **R3 信噪优先** | 进 LLM 上下文的数据必须高相关；宁可放进专用聚合块也不堆进主 prompt。 |
| **R4 诚实降级** | 消费者存在但数据采不到时（如清算），渲染端必须显式缺省/标注，不得静默填 0 伪装。 |
| **R5 防回归** | 新增数据源时，必须同时声明消费者并在 `spec_data_utilization.py` 加 claim，否则 SDD 挡下。 |

---

## 3. 全量数据域 → 消费模块登记表（分门别类）

> 状态：✅ 已消费 / 🟡 observation-only(显式) / 🟠 消费者在但数据断(上游采集 gap) / ❌ orphan(违反契约)

| 数据域 | 采集器 | 存储 | **消费模块（去向）** | 状态 |
|---|---|---|---|---|
| 现货价/OHLCV | CCXTProvider / market_data job | `market_snapshots` | market_observer / paper_trader / trading_decision / price_feed / shadow_trades | ✅ |
| 微观结构/盘口 | MicrostructureCollector | `orderbook_snapshots` | market_observer→tradability→LLM；universe/features 选币 | ✅ |
| 资金费率(主交易所) | DerivativesCollector | `funding_rate_snapshots` (exchange=okx) | market_observer→leverage→LLM；context_builder；universe/features | ✅ |
| 持仓量 OI(主交易所) | DerivativesCollector | `open_interest_snapshots` | 同上→crowding_score | ✅ |
| 多空比 LSR | DerivativesCollector (Binance fapi) | `open_interest_snapshots.long_short_ratio` | normalizer→crowding_score→leverage→LLM | ✅ |
| 宏观(BTC.D/总市值/VIX/DXY/Nasdaq) | MacroCollector (FRED/CoinGecko/yf) | `macro_snapshots` | market_observer / context_builder / regime / shadow_trades / position_enrichment | ✅ |
| 恐惧贪婪指数 | MacroCollector (alternative.me) | `macro_snapshots.fng_*` | macro 块→LLM | ✅ |
| 涨跌幅榜 | CoinGeckoTopCollector | `coingecko_top_snapshots` + 折叠 summary | macro 块(top_movers_summary)→LLM | ✅ |
| 成交量排名 | VolumeScannerCollector | 内存 `_latest_volume_ranking` | universe/service→选币注入 | ✅ (进程内) |
| 新闻+情绪 | NewsCollector + analyzer | `news_items` | market_observer 新闻块 + research_worker | ✅ |
| **Hyperliquid OI/资金费率/预测** | HyperliquidCollector | `funding/OI` (exchange=hyperliquid*) | **market_observer→跨venue资金费率对比→LLM** (本次新增路由) | ✅ |
| **DefiLlama 链上(稳定币/TVL/DEX)** | DefiLlamaCollector | `onchain_snapshots` | market_observer→macro 链上块→LLM | ✅ |
| 预测市场 | PolymarketProvider | prediction 表 | market_observer 预测块→LLM (启用时) | ✅ |
| 美股/xStocks | equity_observer | equity 表 | **observation-only（不交易，仅交叉验证/溢价监测）** | 🟡 |
| 市场状态 regime | RegimeCollector | (派生, 读 macro/funding) | regime 消费者 | ✅ |
| OHLCV 历史 | history_downloader | ohlcv | 回测/指标 | ✅ |
| 清算 liquidations | DerivativesCollector (CCXT 条件) | `liquidation_events` | context_builder→`liquidation_1h_usd`→crowding_score→LLM（**消费者在**） | 🟠 数据=0 |

---

## 4. Gap 处置

- **Hyperliquid（曾 ❌ orphan）→ 已闭合 ✅**：本次新增 `market_observer` 读 `latest_funding_rate(sym, exchange="hyperliquid"/"hyperliquid_predicted")` → `render_enriched_context_md(xvenue_funding=...)` 渲染"跨venue资金费率对比"（**annualized 比较**，因 HL 1h vs 主所 8h 间隔，原始费率不可比）。揭示基差/情绪背离。锁定于 DU-S01/S02/B01/B02。
  - OI 暂作为 funding 信号的伴随数据存储（cross-venue OI 聚合可作后续宏观杠杆景气信号，未纳入本次以免 prompt 膨胀，遵 R3）。
- **清算（🟠 消费者在、数据断）**：属**上游采集** gap 非路由 gap——CCXT 多数所不支持 `fetchLiquidations`、Binance REST 2025 起需 auth（死代码已删）。"已获取资料"里不含清算，故不在本契约"物尽其用"范围。处置遵 R4：`liquidation_1h_usd` 缺省为 None（已是现状，诚实）。**后续可选**：Hyperliquid API 暴露清算流，可让 HyperliquidCollector 兼采清算喂已存在的消费者——单独立项。
- **美股/xStocks（🟡）**：owner 明确 observation-only（不交易），符合 R1 的"显式标注"分支，非违反。

---

## 5. SDD 编码 (DU-* claims)

| Claim | 级别 | 断言 |
|---|---|---|
| DU-S01 | L2a | `render_enriched_context_md` 有 `xvenue_funding` 参数（消费钩子存在） |
| DU-S02 | L2c | market_observer 读 hyperliquid 行 **且** 传 `xvenue_funding` 进 render（Hyperliquid 真达决策，非仅 collector/doctor） |
| DU-S03 | L2c | market_observer 读 `latest_onchain_snapshots` 且传 `onchain=`（DefiLlama 被消费） |
| DU-B01 | L2b | 给定 HL+主所 annualized funding，render 产出"Cross-venue funding"段、divergence 以 pp 计、含 predicted |
| DU-B02 | L2b | 某 symbol 无 HL 数据时，跨venue段整体省略（不漏空行噪音，遵 R3） |

**维护规则**：新增数据源 → 同步在 §3 登记表加行 + 声明消费者 + 加 DU-* claim（R5）。
