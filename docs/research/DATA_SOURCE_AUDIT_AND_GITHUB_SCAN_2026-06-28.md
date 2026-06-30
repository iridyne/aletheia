# 数据来源审计 + GitHub 外部扫描 (2026-06-28)

> 目的：对 Aletheia 现有数据收集源做"真 / 假"评估，并扫描 GitHub 上 agentic / quant trading 生态中**能提供数据来源**的 repo，做缺口映射。
> 方法：内部 = Explore agent 全量读代码（按代码证据定级）；外部 = `gh search repos` 多关键词扫描（systems/datasrc/gaps/fill 四批，去重后 1021 unique repo），按 star 排序 + 免费/付费核实。

---

## 0. TL;DR — 三句话

1. **"像假的"源集中在 4 处**：CoinGlass（三路径全空 + 未注册）、Liquidations（CCXT conditional + Binance REST 死代码 → 实际清算数据 = 0）、OI 历史(Bitget, `NotSupported` 被吞)、Glassnode（代码里根本不存在）。这些恰好都是**衍生品聚合 + 链上**领域。
2. **真实工作的源**是价格/OHLCV（CCXT 多级 fallback）、资金费率、多空比、orderbook 微观结构、宏观（FRED+F&G+CoinGecko global）、新闻 RSS+LLM 情绪、Polymarket。这些是健康的。
3. **填洞的最优解多数免费**：Hyperliquid 公开 API（OI/funding/清算/L2，零 key）填衍生品洞；DefiLlama（TVL/稳定币/DEX量，零 key）+ Dune/Etherscan（免费 tier）填链上洞；Coinalyze（免费 key）是 CoinGlass 的直接免费替代。

---

## Part A — 内部数据源审计（按代码证据定级）

定级：**REAL** 真实接入工作 / **STUB** 占位返回空假 / **PAYWALLED** 需付费实际拿不到 / **BROKEN** 崩溃或异常被吞 / **UNWIRED** 写了没接进 pipeline / **CONDITIONAL** 依赖交易所支持。

### A.1 🔴 假 / 坏 / 没接（重点）

| 源 | 文件 | 状态 | 证据 |
|---|---|---|---|
| **CoinGlass** | `data/collectors/coinglass.py` | **PAYWALLED + STUB** | 三条路径全返回 `{"rows":[]}`：①无 key ②free tier 撞 `"Upgrade plan"` paywall ③paid tier 是 `TODO` no-op（line 110-117，`_ = client` 空转）。**任何路径都不出数据。** |
| CoinGlass 调度 | `agent_runtime/scheduler.py` | **UNWIRED** | scheduler 里没有任何 job 调用它，类存在但永不执行。 |
| **Glassnode** | — | **NOT PRESENT** | 全代码库无文件/类/import/config 字段。文档与记忆提过，代码从未建。 |
| **清算 (CCXT)** | `data/collectors/derivatives.py` | **CONDITIONAL** | `exchange.has.get("fetchLiquidations")` 守卫；多数交易所驱动不实现 → 不调用。 |
| **清算 (Binance REST fallback)** | `derivatives.py:396-451` | **BROKEN / 死代码** | `_fetch_liquidations_binance_rest()` 整个函数**从不被调用**；2025 起 forceOrders 需 auth，except 块被改成静默 `logger.debug` 跳过。→ **清算数据实际为 0**。 |
| OI 历史 (Bitget) | `derivatives.py:288-293` | **BROKEN(吞)** | Bitget `fetch_open_interest_history` 抛 `NotSupported`，被 swallow 返回 `[]`。 |
| Volume Scanner | `data/collectors/volume_scanner.py` | **REAL 但不落库** | 只写 module-level `_latest_volume_ranking` 内存 dict，无 SQLite 写；进程重启即丢。 |

### A.2 🟡 真实但脆弱 / 默认关

| 源 | 文件 | 状态 | 备注 |
|---|---|---|---|
| CoinGecko provider (fallback) | `data/sources/coingecko.py` | REAL(受限) | 仅 30 个硬编码 symbol；1m/5m/15m 直接 `NotImplementedError`；6s/req。 |
| FRED (VIX/DXY/SPY/Nasdaq) | `data/collectors/macro.py` | REAL(需免费 key) | `FRED_API_KEY` 缺失则跳过。 |
| yFinance tradfi fallback | `data/collectors/macro.py` | REAL(不稳) | 代码自带注释"yfinance/Yahoo outage"；阻塞 executor。 |
| yFinance macro provider | `data/yfinance_provider.py` | REAL(默认 OFF) | `enable_yfinance_macro=False`。 |
| Equity observer (yFinance 日线) | `agents/equity_observer.py:104` | REAL(不稳) | 90d 日线算指标；yFinance 著名不稳定，仅 1 次重试。 |
| Polymarket | `data/polymarket_provider.py` | REAL(默认 OFF) | `prediction_enabled=False`。 |

### A.3 🟢 真实健康（无需动）

价格/OHLCV 多级链 `CCXTProvider→CoinGecko→BinancePublic`（`sources/chain.py` + `ccxt_provider.py`）、资金费率(CCXT)、OI 当前值(CCXT)、多空比(Binance fapi 公开)、微观结构 orderbook(CCXT `fetch_order_book`)、CoinGecko global 宏观、Fear&Greed(alternative.me)、CoinGecko top movers、新闻 RSS(feedparser 5 源)、LLM 新闻情绪、OHLCV 历史下载、Regime 聚合(纯 DB 读)、Price feed(30s)、Bitget xStocks 实时价。

---

## Part B — GitHub 外部扫描结果

扫描批次（`gh search repos`，每词 top 25，去重 1021 unique）：systems(236) / datasrc(417) / gaps(376) / fill(375)。

### B.1 旗舰 agentic / quant 系统（借鉴架构，非数据源）

| ★ | repo | 说明 |
|---|---|---|
| 89k | TauricResearch/TradingAgents | 多 agent LLM 金融交易框架（标杆）；中文版 hsliuping/TradingAgents-CN(29k) |
| 60k | virattt/ai-hedge-fund | AI 对冲基金团队；financial-datasets(428) 是其数据层 |
| 51k | freqtrade/freqtrade | 老牌开源 crypto bot；策略/回测/数据下载体系成熟 |
| 20k | QuantConnect/Lean | 机构级算法交易引擎（多资产数据抽象值得看） |
| 19k | hummingbot/hummingbot | 高频做市；多交易所 connector |
| 14k | HKUDS/Vibe-Trading | 个人交易 agent（新） |
| 1.7k | Lumiwealth/lumibot | 可回测 AI agent + 多资产 |

### B.2 🎯 数据来源 repo / API（核心，按数据类型 + 免费核实）

> 免费置信度：✅高(确知) / 🟨中(需采纳前实测) / 💰付费或受限

**① 加密价格 / 行情（已覆盖）**
- ✅ `ccxt/ccxt` (43k) — 已用。
- 🟨 `tardis-dev/tardis-node`(353)/`tardis-machine`(304) — tick 历史付费，实时归一化免费。
- ✅ `man-c/pycoingecko`(1k) — 免费 demo tier。

**② 衍生品聚合（OI/funding/清算）— 填 CoinGlass 洞 ⭐最高价值**
- ✅ `hyperliquid-dex/hyperliquid-python-sdk`(1.7k) — **公开 info API 完全免费零 key**：OI、funding、L2 book、candles、trades、清算。
- 🟨 **Coinalyze API**（无官方 repo，REST + 免费 key）— **跨交易所聚合 OI/funding/清算**，最接近 CoinGlass 的免费替代。
- ✅ Binance fapi 公开端点（多空比已用）可扩展取 OI/历史。

**③ 链上 / DeFi — 填 Glassnode 洞**
- ✅ `DefiLlama/DefiLlama-Adapters`(1.2k) + `defillama-app`(292) — **API 大量端点零 key 免费**：TVL、稳定币流通、DEX 量、收益率、链上价格。
- 🟨 `paradigmxyz/spice`(210) / `duneanalytics/dune-client`(134) — Dune；**免费 tier 月度查询额度**。
- ✅ `sebs/etherscan-api`(552) / `corpetty/py-etherscan-api`(517) — Etherscan **免费 tier 5 req/s**。
- 🟨 `lagerfeuer/cryptocompare`(182) — 免费 tier 需 key。

**④ 美股 / 传统金融（替换不稳的 yFinance）**
- 🟨 `defeat-beta/defeatbeta-api`(678) — 开源 Yahoo 替代：价格/财报/电话会纪要/新闻/营收分段；开源无 key（采纳前实测）。
- 🟨 `Finnhub-Stock-API/finnhub-python`(1k) — 免费 tier 有限。
- 🟨 `hydrosquall/tiingo-python`(313) — 免费 tier（EOD+部分实时）。
- 💰 `twelvedata/twelvedata-python`(748) — 免费 tier 限频严。
- 💰 `virattt/financial-datasets`(428) + `financial-datasets/mcp-server`(2.2k) — financialdatasets.ai 付费。
- ✅ `mortada/fredapi`(1.6k) — FRED 完全免费（已部分接入，建议扩端点）。

**⑤ 社交 / 情绪**
- 🟨 `guanquann/Stocksera`(771) — 聚合 60+ 另类数据（Reddit/短仓/内部交易）。
- 🟨 `danilobatson/ai-trading-agent-gemini`(246) — 社媒情绪→信号（参考 LunarCrush 接法）。
- 数据集：`yumoxu/stocknet-dataset`(679) 推文+股价。

### B.3 🤖 MCP server / agent skills（与本项目架构最契合）

| ★ | repo | 数据 | 免费 |
|---|---|---|---|
| 2.2k | financial-datasets/mcp-server | 美股行情/基本面 | 💰付费后端 |
| 614 | guangxiangdebizi/FinanceMCP | Tushare(A股)+Binance | 🟨Tushare 需积分 |
| 345 | **okx/agent-trade-kit** | **OKX MCP：现货/合约/期权/网格** | ✅ — **本项目已用 OKX，官方 MCP 直接相关** |
| 325 | guangxiangdebizi/TradingAgents-MCPmode | TradingAgents + MCP | — |
| 318 | Alex2Yang97/yahoo-finance-mcp | Yahoo 全套 | 🟨 |
| 181 | armorwallet/armor-crypto-mcp | 链上/swap | 🟨 |
| **324** | **LLMQuant/awesome-trading-agents** | LLM trading agent + MCP + skill 精选目录 | 二次挖掘入口 |
| 157 | BlockRunAI/awesome-finance-mcp | finance MCP 精选目录 | 二次挖掘入口 |

---

## Part C — 缺口映射 + 推荐

| 内部缺口 | 现状 | 推荐填补（优先免费） | 工作量 |
|---|---|---|---|
| **衍生品聚合 (OI/funding/清算)** | CoinGlass 全空 + 清算死代码 | **A 案**：删 CoinGlass 门面，接 **Hyperliquid 免费 API** + 扩 **Binance fapi 公开 OI**。**B 案**：接 **Coinalyze 免费 key**（跨所聚合，最贴 CoinGlass 语义）。 | 中 |
| **清算数据** | 实际为 0 | Hyperliquid 清算 / Coinalyze liquidation；或删死代码停止自欺 | 中 |
| **链上 / Glassnode** | 根本不存在 | **DefiLlama 免费 API**（稳定币/TVL/DEX量做链上宏观）+ Etherscan 免费 tier；进阶 Dune 免费 tier | 中 |
| **美股稳定性** | yFinance 不稳 | defeatbeta-api / Finnhub / Tiingo 免费 tier 做主源，yFinance 降级 | 小-中 |
| **Volume Scanner 丢数据** | 内存 dict 重启丢 | 落 SQLite | 小 |
| **OKX agent 数据** | 自建 adapter | 评估 okx/agent-trade-kit 官方 MCP 可复用部分 | 调研 |

### 关键原则（呼应 owner 治理铁律）
- **先 SDD 再动**：衍生品/清算属高敏决策链外围，新增源前先在 `tests/specs/spec_*.py` 声明 claim → L2a RED → 实现。
- **交易所无关**：Hyperliquid/Coinalyze 接入落在 collector/adapter 层，不污染核心逻辑。
- **停止自欺优先**：CoinGlass 门面 + 清算死代码即使不补新源，也应先**显式标记/移除**，避免"看起来在收集实则空转"喂幻觉——这正是 owner 担心的"像假的"本质。
- **免费够用**：Hyperliquid + DefiLlama + Coinalyze + FRED + alternative.me 这套免费组合覆盖度已远超空转的 CoinGlass，无需付费接入。

---

## 附录 — 扫描原始数据
四批 JSON 与 top 列表保存在 job tmp（`scan.sh` + `systems/datasrc/gaps/fill/all.json`，去重 1021 unique repo）。
