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
| 微观结构/盘口 | MicrostructureCollector | `orderbook_snapshots` | market_observer→tradability→LLM；universe/features 选币；inspect/doctor 诊断 | ✅ CCXT 三列档位与 contractSize 深度已修复；按 venue+symbol+market_type 精确路由；超过 15 分钟整行退出决策 |
| 历史 K 线 | CCXTProvider / history_downloader / ohlcv_active_backfill | `market_ohlcv` + `ohlcv_backfill_state` | 图表、回测、trade path、shadow/alpha 分析、覆盖诊断 | ✅ 按 venue+symbol+market_type+timeframe+ts 隔离；动态活动成员由独立有界任务补齐，持久化失败退避且不阻塞 Universe 刷新；旧不明 venue 诚实标记 |
| 资金费率(主交易所) | DerivativesCollector | `funding_rate_snapshots` | market_observer→leverage→LLM；context_builder；universe/features | ✅ 精确 venue；超过 15 分钟整行退出决策 |
| 持仓量 OI(主交易所) | DerivativesCollector | `open_interest_snapshots` | 同上→crowding_score | ✅ 精确 venue；超过 15 分钟连同变化率/LSR 整行退出决策 |
| 多空比 LSR | DerivativesCollector (Binance fapi) | `open_interest_snapshots.long_short_ratio` | normalizer→crowding_score→leverage→LLM | ✅ 真实 DB 构建路径已保留字段并纳入 crowding |
| 宏观(BTC.D/总市值/VIX/DXY/Nasdaq) | MacroCollector (FRED/CoinGecko/yf fallback) | `macro_snapshots` | market_observer / context_builder / regime / shadow_trades / position_enrichment | ✅ |
| 美股宏观代理(S&P500/VIX/DXY/Gold) | yfinance_provider (enable_yfinance_macro 门控) | 不落库，直接生成 markdown | market_context.build_market_context → "Macro context (daily)" 块 → LLM | ✅ |
| 恐惧贪婪指数 | MacroCollector (alternative.me) | `macro_snapshots.fng_*` | macro 块→LLM | ✅ |
| 涨跌幅榜 | CoinGeckoTopCollector | `coingecko_top_snapshots` + 折叠 summary | macro 块(top_movers_summary)→LLM | ✅ |
| 成交量排名 | VolumeScannerCollector（精确 venue+market InstrumentId） | 内存 `_latest_volume_ranking` + `volume_ranking_snapshots` | universe/service→按原 market_type 排序已登记资产（不创建新资产） | ✅；单标的不支持不再打断整批 |
| 新闻+情绪 | NewsCollector + analyzer | `news_items` | market_observer 新闻块 + research_worker | ✅ |
| 官方公告/监管 RSS（SEC/CFTC/Ethereum Foundation） | NewsCollector（默认 news.feeds） | `news_items` | market_observer 新闻块 + research_worker | ✅ |
| **Hyperliquid OI/资金费率/预测** | HyperliquidCollector | `funding/OI` (exchange=hyperliquid*) | **market_observer→跨venue资金费率对比→LLM** (本次新增路由) | ✅ |
| 永续持仓结构/清算墙 | VergexStructureCollector fixture / 后续 Vergex feed（当前不直连 API） + Position X 离线材料解析器 | `perp_structure_snapshots` + 运行时 knowledge cards | market_observer→Perp structure / Position X advisory 块→LLM；System Operator 上下文→结构证据；doctor 新鲜度检查 | 🟡 observation-only（聚合信号，不参与风控/执行） |
| **DefiLlama 链上(稳定币/TVL/DEX)** | DefiLlamaCollector | `onchain_snapshots` | market_observer→macro 链上块→LLM | ✅ |
| 预测市场 | PolymarketProvider | prediction 表 | market_observer 预测块→LLM (启用时) | ✅ |
| 美股/xStocks (ON-series, 15 tokens) | equity_observer | equity 表 | **observation-only（不交易，仅交叉验证/溢价监测）** | 🟡 |
| **美股 r-series 代币 (113 tokens, Bitget)** | equity_observer (stock token cross-validation) | 不落库，实时抓取作 cross-val | **equity_observer → `_fetch_stock_token_price()` → yfinance 偏差监测 → LLM equity snapshot**；亦注入 Serenity equity_chain lens 上下文 | ✅ |
| 市场状态 regime | MacroCollector._classify_risk_regime()（内联派生，无独立 RegimeCollector 类） | `macro_snapshots.risk_regime` | market_observer / context_builder → regime 评分 → LLM | ✅ |
| OHLCV 历史 | history_downloader | ohlcv | 回测/指标 | ✅ |
| 清算 liquidations | CoinalyzeCollector（key-guard，data_collectors Phase B；DU-S06/DU-T01） | `liquidation_events` | context_builder→`liquidation_1h_usd`→crowding_score→LLM | ✅ 有 key 时写入 / 无 key 时诚实跳过 |
| OI 历史（跨所） | CoinalyzeCollector（key-guard，data_collectors Phase B；DU-S06/DU-T01） | `open_interest_snapshots(exchange=coinalyze)` | research_worker 显式聚合流量上下文；doctor | ✅ 与主交易所 OI 分层，不再参与默认 primary read |
| OHLCV 多周期（1h/4h/1d） | CCXTProvider（market_context._TIMEFRAMES 锁定三周期；DU-S07） | `market_snapshots` | market_context.build_market_context → 多周期技术块 → LLM | ✅；上下文读取和缓存均保留 venue+symbol+market_type，现货与永续不得互用 |
| Opportunity 固定持有期结果 | outcome_recorder（目标 instrument OHLCV/ticker + 入场 spread/funding + 配置 fee/slippage） | `universe_forward_outcomes` | net calibration / family edge → System Operator；doctor；debate/guard/flow/thesis 审计；净亏损 guard candidate | ✅，历史 gross-only 行只解释不学习 |
| Opportunity 筛选证据 | Universe ticker/tradability、Observer、Ranker、System Operator、Risk 的事前判断；结果复用目标 instrument 的固定时点成本模型 | `filter_decision_batches` / `filter_decisions` / `filter_candidate_outcomes` | filter controller（只读）、doctor、shadow/control 净结果、后续受控实验 | ✅，无方向门只报覆盖；风控/交易所能力永不自动放宽 |

---

## 4. Gap 处置

- **Hyperliquid（曾 ❌ orphan）→ 已闭合 ✅**：本次新增 `market_observer` 读 `latest_funding_rate(sym, exchange="hyperliquid"/"hyperliquid_predicted")` → `render_enriched_context_md(xvenue_funding=...)` 渲染"跨venue资金费率对比"（**annualized 比较**，因 HL 1h vs 主所 8h 间隔，原始费率不可比）。揭示基差/情绪背离。锁定于 DU-S01/S02/B01/B02。
  - OI 暂作为 funding 信号的伴随数据存储（cross-venue OI 聚合可作后续宏观杠杆景气信号，未纳入本次以免 prompt 膨胀，遵 R3）。
- **永续持仓结构（🟡）**：当前只支持离线/后续 feed 写入聚合结果，不直连 Vergex API，不导入报告原文或地址明细。消费路径是 `market_observer` 读取 60 分钟内最新行并渲染 POC、价值区间、清算墙、集中度；当行里带 Position X 扩展字段时，再压缩成 `Position X (advisory only)` 结构证据。System Operator 也读取同一行的压缩结构证据，用于持仓/机会判断；`doctor` 暴露空/旧/可用状态。它是观察信号，不参与风控或执行。
- **Position X 离线材料（🟡）**：`aletheia.data.position_x` 只读授权样本中的维度说明、结构总结、拟合结果、`analysis_*.json` 的表达样式和 prompt 组织方式；`extract_position_x_material_bundle()` 可一次性把目录中的授权材料提炼成安全 knowledge cards 与结构化快照。原始 prompt、账户、地址、余额、credential、token 不进入运行时卡片、日志或 prompt；拟合文件只保留公式提示、相关性结论和 source hash。
- **清算 + OI 历史（✅ key-guard 接通）**：CoinGlass 全面 paywall、Glassnode 无免费 tier、Bitget OI 历史死代码已确认（2026-06-30 产品定义）。CCXT 清算死代码（`_fetch_liquidations_ccxt` + `_CCXTLiquidationsUnsupported`）已从 `derivatives.py` 删除（GAP-DU-C02，锁定于 DU-C02）。`CoinalyzeCollector` 已建并接入 `data_collectors` Phase B；有 `coinalyze_api_key` 时写入 `open_interest_snapshots(exchange='coinalyze')` 与 `liquidation_events(exchange='coinalyze')`，无 key 时返回 `errors=["no_key"]` 并诚实跳过。持久化路径锁定于 DU-T01。
- **美股/xStocks（🟡）**：owner 明确 observation-only（不交易），符合 R1 的"显式标注"分支，非违反。

---

## 5. SDD 编码 (DU-* claims)

| Claim | 级别 | 断言 |
|---|---|---|
| DU-S01 | L2a | `render_enriched_context_md` 有 `xvenue_funding` 参数（消费钩子存在） |
| DU-S02 | L2c | market_observer 读 hyperliquid 行 **且** 传 `xvenue_funding` 进 render（Hyperliquid 真达决策，非仅 collector/doctor） |
| DU-S03 | L2c | market_observer 读 `latest_onchain_snapshots` 且传 `onchain=`（DefiLlama 被消费） |
| DU-S13 | L2a | `perp_structure_snapshots` 已登记，存在白名单 fixture collector，market_observer 读 `latest_perp_structure` 并传 `perp_structure=`，doctor 暴露 `data.perp_structure` 新鲜度 |
| DU-S14 | L2a | `Position X` 离线解析器存在，能通过目录式 bundle 提取安全 knowledge cards / 结构快照；market_observer 与 System Operator 只消费压缩后的 advisory 结构证据 |
| DU-B01 | L2b | 给定 HL+主所 annualized funding，render 产出"Cross-venue funding"段、divergence 以 pp 计、含 predicted |
| DU-B02 | L2b | 某 symbol 无 HL 数据时，跨venue段整体省略（不漏空行噪音，遵 R3） |
| DU-C01 | L2a | `CoinalyzeCollector` 存在，有 `collect()` 方法和 `domain` 属性（Coinalyze 清算+OI 采集入口） |
| DU-C02 | L2a | `derivatives.py` 不含 `_fetch_liquidations_ccxt` 及 `_CCXTLiquidationsUnsupported`（死代码已删，R4 诚实降级） |
| DU-S04 | L2a | `yfinance_provider.py` 存在 + `market_context` 通过 `enable_yfinance_macro` 门控调用 + `data_utilization.md` §3 已登记（R5 合规） |
| DU-S05 | L2a | 不存在 `class RegimeCollector`；`MacroCollector._classify_risk_regime()` 是 regime 的唯一实现（防虚构采集器类） |
| DU-S06 | L2a | `scheduler.py` 含 `CoinalyzeCollector` 引用（已注册 `_async_data_collectors`）且 `config/schema.py` 定义 `enable_coinalyze` + `coinalyze_api_key`（调度+配置结构双锁） |
| DU-S07 | L2a | `market_context._TIMEFRAMES` 包含 `'1h'`、`'4h'`、`'1d'` 三值（OHLCV 多周期全量采集，防缩减） |
| DU-S12 | L2a | 默认新闻源包含 SEC/CFTC/Ethereum Foundation 官方 RSS，且 §3 登记为 NewsCollector→`news_items`→market_observer/research_worker |
| DU-T01 | L2b | 离线注入 CoinalyzeCollector 输出，断言 scheduler 写入 `open_interest_snapshots` 与 `liquidation_events`（不依赖真实 API key） |

**维护规则**：新增数据源 → 同步在 §3 登记表加行 + 声明消费者 + 加 DU-* claim（R5）。
