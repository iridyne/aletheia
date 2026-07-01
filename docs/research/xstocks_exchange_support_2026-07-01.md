# xStocks 交易所支持性调研报告

**调研日期**: 2026-07-01  
**调研范围**: Bitget、OKX 对链上美股代币（xStocks）的支持性  
**调研方法**: 公开 REST API 实测 + 官方文档

---

## 一、Bitget

### 1.1 链上美股现货（Spot）— ✅ 支持

Bitget 以两套命名体系提供美股代币化现货：

#### ON 系列（主力推荐）

上线时间：~2026-05（listTime 时间戳约 1773828000000）  
报价货币：USDT  
接口：`GET /api/v2/spot/public/symbols`（无独立过滤参数，靠 baseCoin 识别）

| Canonical Symbol | 标的资产 | 类型 |
|-----------------|---------|------|
| TSLAON/USDT | Tesla | 股票 |
| AAPLON/USDT | Apple | 股票 |
| NVDAON/USDT | NVIDIA | 股票 |
| AMZNON/USDT | Amazon | 股票 |
| MSFTON/USDT | Microsoft | 股票 |
| METAON/USDT | Meta | 股票 |
| GOOGLON/USDT | Alphabet | 股票 |
| AMDON/USDT | AMD | 股票 |
| QQQON/USDT | Nasdaq-100 ETF (QQQ) | ETF |
| SPYON/USDT | S&P 500 ETF (SPY) | ETF |
| IVVON/USDT | iShares Core S&P 500 (IVV) | ETF |
| ITOTON/USDT | iShares Russell 2000 (ITOT) | ETF |
| IWMON/USDT | iShares MSCI World (IWM) | ETF |
| IAON/USDT | Gold ETF (IAU) | 贵金属 ETF |
| SLVON/USDT | Silver ETF (SLV) | 贵金属 ETF |

**合计 15 个**（8 只股票 + 7 只 ETF/贵金属 ETF）

交易参数：
- 最小下单：$1 USDT
- 最大限价单：$20,000,000
- 最大市价单：$1,000,000
- 最大委托数量：200 份
- 价格精度：小数 2 位
- 数量精度：小数 3 位
- 手续费：maker/taker 均 0.1%

#### r 前缀系列（辅助，流动性更深）

**113 个品种**，流动性极高（rNVDA 日成交 ~$10B，rTSLA ~$7.86B），但为早期产品。  
主要科技：rNVDA, rTSLA, rAAPL, rAMD, rMSFT, rGOOGL, rAMZN, rMETA  
金融/医疗/能源：rXOM, rPFE, rMRNA, rKKR, rIBKR...（共 113 个）

API 识别方式：`baseCoin` 以小写 `r` 开头

#### 两系列对比

| 维度 | ON 系列 | r 前缀系列 |
|------|---------|-----------|
| 品种数 | 15 | 113 |
| 典型日成交量 | $267k（TSLAON） | $7.86B（rTSLA） |
| 策略可用性 | 流动性不足，不建议 | 主力品种可用 |
| CLAUDE.md 已知 | ✅ 明确提及 | ✅ 明确提及 |

**限制**：全部品种 `areaSymbol: "yes"`，存在地区限制（推测不覆盖中国大陆、美国等主要市场用户）。

### 1.2 美股合约（Perp/Futures）— ❌ 不支持

验证的所有 productType（USDT-FUTURES、XSUSDT、XS-FUTURES-USDT、xstock-futures-usdt、susdt-futures）均无美股品种。Bitget 美股敞口仅限现货。

---

## 二、OKX

### 2.1 链上美股现货（Spot CeFi）— ❌ 不支持

OKX 公开 SPOT API（400+ 品种）中无任何 `instCategory=3`。  
`AAPL-USD`、`TSLA-USD` 等均返回"Instrument doesn't exist"。  
OKX 的美股代币（Ondo 发行）仅存在于 **DEX/Web3 侧**，不通过 CeFi REST API 访问，**现有 Aletheia 技术栈无法接入**。

### 2.2 美股合约（Perp/Futures）— ✅ 支持（重大发现）

OKX 于 **2026 年 6 月**（listTime ~1780988400165）新上线了两类美股合约产品：

#### SWAP 系列（instType=SWAP, instCategory=3）— USDT 结算

16 个品种，**与现有 OKX 线性永续处理完全兼容**：

代表品种：AAPL-USDT-SWAP, TSLA-USDT-SWAP, NVDA-USDT-SWAP, AMD-USDT-SWAP  
以及：ADBE, AMZN, AMAT, ASML, AVGO, ARM...

API 访问：
```
GET /api/v5/public/instruments?instType=SWAP
# 客户端过滤 instCategory=3
```

#### FUTURES xPerp 系列（instType=FUTURES, ruleType=xperp）— USD 结算

20 个品种，5 年到期但行为类似永续：

| instId | 标的 |
|--------|------|
| AAPL-USD_UM_XPERP-310613 | Apple |
| AMZN-USD_UM_XPERP-310613 | Amazon |
| GOOGL-USD_UM_XPERP-310613 | Alphabet |
| META-USD_UM_XPERP-310613 | Meta |
| MSFT-USD_UM_XPERP-310613 | Microsoft |
| NVDA-USD_UM_XPERP-310613 | NVIDIA |
| TSLA-USD_UM_XPERP-310613 | Tesla |
| SPY-USD_UM_XPERP-310613 | S&P 500 ETF |
| QQQ-USD_UM_XPERP-310613 | Nasdaq-100 ETF |
| MSTR-USD_UM_XPERP-310704 | MicroStrategy |
| INTC-USD_UM_XPERP-310627 | Intel |
| MRVL-USD_UM_XPERP-310627 | Marvell |
| MU-USD_UM_XPERP-310627 | Micron |
| SOXL-USD_UM_XPERP-310627 | 半导体 3x ETF |
| EWY-USD_UM_XPERP-310704 | iShares Korea ETF |
| SAMSUNG-USD_UM_XPERP-310704 | Samsung |
| SKHYNIX-USD_UM_XPERP-310704 | SK Hynix |
| ... | 共 20 个 |

合约规格（以 TSLA 为例）：
- `ctVal`: 1（每张合约 = 1 股）
- `ctValCcy`: TSLA（数量以股计）
- `settleCcy`: **USD**（美元结算，非 USDT）
- `lever`: 20x
- `minSz`: 0.01（支持碎股）
- `tickSz`: 0.01
- `alias`: "this_five_years"（5 年期，行为类永续）

API 访问：
```
GET /api/v5/public/instruments?instType=FUTURES
# 客户端过滤 ruleType=xperp 或 instCategory=3
```

---

## 三、对比总结

| 维度 | Bitget | OKX |
|------|--------|-----|
| **现货（链上美股）** | ✅ 113 r系列 + 15 ON系列 | ❌ CeFi 无，DEX 有但无法接 |
| **SWAP 永续（美股）** | ❌ | ✅ 16 个，USDT 结算，2026-06 新上 |
| **FUTURES xPerp** | ❌ | ✅ 20 个，USD 结算，5年期类永续 |
| **API 可访问性** | 公开 REST v2 | 公开 REST v5 |
| **流动性（估算）** | r系列极深；ON系列极浅 | 新品，待观察 |
| **地区限制** | areaSymbol=yes | 不对 US persons 开放 |

---

## 四、Aletheia 集成评估

### Bitget xStocks 现货

**路径**：`CCXTProvider(exchange="bitget", fallback_exchange="bitget")`（CLAUDE.md 已明确，代码已有特殊路径）

- **r 前缀**（推荐）：流动性深，内部 canonical `rNVDA/USDT`，adapter 边界 `rNVDAUSDT`
- **ON 系列**：流动性不足，暂不建议策略接入
- 品种识别：`baseCoin.startswith("r")` 或过滤 `areaSymbol == "yes"`

### OKX SWAP 股票永续（instCategory=3）

**路径**：等同现有 OKX 线性永续处理（`AAPL-USDT-SWAP`），无需特殊 adapter 改动

**注意**：
- SWAP 系列 USDT 结算，与现有处理路径兼容
- FUTURES xPerp 系列为 **USD 结算**，需额外 margin 账户处理，复杂度高
- 建议优先接入 SWAP 系列，xPerp 系列 defer

### 新数据源登记要求

若要接入，按 SDD R5 规则须先在 `docs/design/data_utilization.md` §3 补行，再写 spec DU-* claim。
