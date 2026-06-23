# 美股集成设计分析

> 本文是实施前的设计分析文档。目标：把 xStocks（链上）和 yfinance（链下）两条孤立路径
> 合并成统一的 "US equity" 观察方向，做到数据互通、知识共享，并与现有 base 框架完整集成。

---

## 一、现状诊断：两条路径的本质差异

| 维度 | yfinance 路径（stock_observer） | xStocks 路径（market_observer） |
|---|---|---|
| 数据源 | Yahoo Finance，日线（90 天） | Bitget CCXT，ON-series 代币化美股 |
| 符号格式 | `AAPL`（纯 ticker，无 `/`） | `AAPLON/USDT`（canonical 格式） |
| 调用时机 | US market hours gate（独立 agent） | 随 crypto market_observer 运行 |
| LLM 提示词 | equity 专属 `_SYSTEM_PROMPT` ✓ | **crypto `_SYSTEM_PROMPT`（语义错误）** |
| asset_class | `"equity"` ✓ | `"equity"` ✓ |
| execution_eligible | 默认 `True`（未显式关闭） | 默认 `True`（未显式关闭） |
| advisory context | 无（不调用 KB router） | **空 `memory_block=""`** |
| 数据持久化 | 不写 `market_ohlcv` | 写入（CCXT 路径自带） |
| linked_strategy_id | 链接活跃策略 | `None` |
| 相互知晓 | AAPL 和 AAPLON/USDT 对彼此无感知 | 同左 |

**三大根本问题**：

1. **符号格式冲突**：`AAPL` 无法构造合法 `InstrumentId`（要求含 `/`）；
   `AAPLON/USDT` → `AAPL` 的反向映射不存在
2. **Universe 硬隔离**：`sync_registry` 只拉 CCXT USDT 对；
   `paper_trader_ranker` 第一道检查就 `return None` for non-crypto
3. **知识循环断路**：stock_observer 不写 KB，无 `equity_observer` role，
   equity 机会的学习输出不回流到任何记忆系统

---

## 二、统一架构设计

### 2.1 核心思路

```
US Equity 大方向
├── sub: yfinance  (asset_subclass="equity_daily")  — 离线日线，权威价格+基本面信号
└── sub: xstocks   (asset_subclass="xstock")         — 在线实时，链上执行代理价格
```

两条 sub-path 产生的数据在 **Opportunity 层合并**：同一标的的两个子机会通过
`canonical_ticker` 字段关联，LLM 可以同时看到两套数据，生成一个统一的 equity signal。

### 2.2 符号统一层设计

**反向映射表**（新增到 `data/symbols.py`）：

```python
# ON-series xStocks: strip "ON" suffix + re-add quote
_XSTOCK_TO_TICKER: dict[str, str] = {
    "AAPLON": "AAPL",
    "TSLAON": "TSLA",
    "NVDAON": "NVDA",
    "SPYON":  "SPY",
    "MSFTLON": "MSFT",   # 按需扩展
    "AMZNON": "AMZN",
    "GOOGLON": "GOOGL",
    "METATAGSON": "META",
}

def xstock_to_ticker(symbol: str) -> str | None:
    """'AAPLON/USDT' → 'AAPL'，不在映射表里返回 None"""
    base = symbol.split("/")[0].upper()
    return _XSTOCK_TO_TICKER.get(base)

def ticker_to_xstock(ticker: str) -> str | None:
    """'AAPL' → 'AAPLON/USDT'"""
    inv = {v: k for k, v in _XSTOCK_TO_TICKER.items()}
    base = inv.get(ticker.upper())
    return f"{base}/USDT" if base else None
```

**InstrumentId 约定**：
- yfinance：`InstrumentId(exchange="yfinance", symbol="AAPL/USDT", market_type="spot")`
  - 符号格式：`{TICKER}/USDT`（与 xStocks canonical 格式保持一致）
  - 这样 `InstrumentId.__post_init__` 的 `/` 检查可以通过
- xStocks：保持现有 `InstrumentId(exchange="bitget", symbol="AAPLON/USDT", market_type="spot")`

`to_canonical()` 对 `AAPL/USDT` 原样返回（已含 `/`），无需修改。

> ⚠️ 设计稿原写 `{TICKER}/USD`；实际实现（`equity_observer.py` line 399）使用
> `symbol=f"{ticker}/USDT"`，与 Bitget xStock canonical 格式一致。文档已同步更新。

---

## 三、数据流设计（抓取 → 存储）

### 3.1 新增 `equity_observer.py`（替代两个现有路径）

```
equity_observer (每 30 min，US market hours gate)
│
├── for each canonical_ticker in cfg.market_data.observed_equities:
│   ├── [yfinance sub-path]
│   │   ├── fetch 90d daily bars via yfinance
│   │   ├── compute equity features (SMA20/50, RSI, ATR, BollingerB%, Mom10, VolZ)
│   │   └── persist to market_ohlcv (exchange="yfinance")
│   │
│   ├── [xStocks sub-path, optional]
│   │   ├── xstock_sym = ticker_to_xstock(ticker)  → "AAPLON/USDT"
│   │   ├── if xstock_sym in cfg.market_data.observed_xstocks:
│   │   │   ├── fetch 1h OHLCV via Bitget CCXTProvider
│   │   │   └── persist to market_snapshots (exchange="bitget", asset_subclass="xstock")
│   │   └── cross-validate: |xstock_price - yfinance_close| / yfinance_close → deviation%
│   │
│   └── [LLM analysis]
│       ├── build equity advisory context (KB role="equity_observer")
│       ├── compose prompt: yfinance_data + xstocks_price + deviation + advisory
│       ├── LLM → MarketRead
│       └── write Opportunity(
│               symbol=f"{ticker}/USDT",         # canonical /USDT format (equity_observer.py:399)
│               asset_class="equity",
│               asset_subclass="xstock" | "equity_daily",  # in metadata
│               execution_eligible=False,         # 显式关闭
│               market="spot",
│               canonical_ticker=ticker,          # AAPL
│               metadata={"yfinance": {...}, "xstock": {...}, "deviation_pct": ...}
│           )
```

### 3.2 配置字段重构

**现在**：
```yaml
market_data:
  observed_stocks: ["AAPL", "TSLA"]        # yfinance tickers
  observed_xstocks: ["AAPLON/USDT", ...]   # Bitget symbols
```

**改后（已实施）**：
```yaml
market_data:
  observed_equities: ["AAPL", "TSLA", "NVDA", "SPY"]  # 唯一入口，canonical ticker
  observed_xstocks: ["AAPLON/USDT", "TSLAON/USDT"]    # 仍需手动配置：决定哪些 ticker 激活 xStock 子路径
```

> ⚠️ 设计稿原计划将 xStocks 激活改为"从 mapping table 自动派生"（无需 `observed_xstocks`）。
> 实际实现（`equity_observer.py` line 285）仍做 `xstock_symbol in observed_xstocks` gate 检查，
> 即 xStock 子路径**仍需手动配置** `observed_xstocks`。自动派生方案尚未实施；`observed_xstocks`
> 字段未废弃。配置说明以当前代码为准。

`config/schema.py` 字段：
```python
observed_equities: list[str] = Field(default_factory=list)
# US equity tickers (Yahoo Finance canonical). equity_observer fetches daily bars
# via yfinance + real-time price via Bitget xStocks if available.

observed_xstocks: list[str] = Field(default_factory=list)
# Bitget ON-series symbols (e.g. "AAPLON/USDT") to activate the xStock sub-path.
# Only tickers whose mapped xstock symbol appears here get live cross-validation.
# Required until auto-derivation from mapping table is implemented.
```

---

## 四、Opportunity 字段扩展

**Opportunity 模型变更**（`storage/models.py`）：

```python
class Opportunity(BaseModel):
    # 现有字段不变...
    asset_class: str = "crypto"
    
    # 新增（via _ADDITIVE_COLUMNS）：
    asset_subclass: str = ""        # "equity_daily" | "xstock" | "polymarket" | ""
    canonical_ticker: str = ""      # "AAPL"（equity 专用，供 cross-path 关联）
    execution_eligible: bool = True  # equity_observer 创建时显式设为 False
```

**`_ADDITIVE_COLUMNS` 追加**：
```python
("asset_subclass", "opportunities", "TEXT NOT NULL DEFAULT ''"),
("canonical_ticker", "opportunities", "TEXT NOT NULL DEFAULT ''"),
```

---

## 五、Universe 集成方案

**结论：equity 暂不进入主 Universe 流水线，改为轻量级 equity registry。**

原因：
- `collect_features` 的特征维度（funding_rate、OI、delta_1h）对 equity 无意义
- crypto 的 scorer/allocator 对 equity 无法评分
- equity_observer 已经独立计算 equity-specific 特征（SMA/RSI/ATR 等）

**最小可行实现**：
- `universe_registry` 中注册 equity 资产（`asset_class="equity"` 字段需加）
- 不进入 `collect_features → service.refresh()` 主流程
- `equity_observer` 维护自己的 equity opportunity pool，前端可以独立展示
- `universe_members` 不含 equity（`execution_eligible=False` + `asset_class="equity"` 的 opp 不进 ranker 主候选）

**未来可选扩展**（不在本次范围）：
- `collect_equity_features()` 并行函数 + `UniverseSnapshot(market_type="equity")`
- equity-specific scorer：PE、relative strength vs SPY、earnings 距离

---

## 六、知识库路由设计

### 6.1 新增 `equity_observer` role

`knowledge/router.py` 修改：
```python
_ITEM_TYPES_BY_ROLE = {
    "strategy_designer": [...],
    "market_observer": [...],   # crypto only
    "equity_observer": [        # 新增
        "research_note",
        "trade_case_study",
        "post_trade_review",
    ],
    "paper_trader": [...],
}

AgentRole = Literal["strategy_designer", "market_observer", "equity_observer", "paper_trader"]
```

### 6.2 equity advisory context 查询词

```python
# equity_observer 调用时用 equity 语境的词
query = f"{ticker} earnings momentum sector rotation relative_strength"
# 而不是 crypto 的 "funding volatility regime risk"
```

### 6.3 equity 知识回流

> ⚠️ **Phase 3，未实现（2026-06-23）**
>
> 下面代码块是设计意图，**尚未在 `equity_observer.py` 中实现**。
> `grep "research_note\|kb_write" aletheia/agents/equity_observer.py` 无匹配。
> 已登记为待办：`docs/issues/BACKEND_ISSUES.md`（equity KB write-back 缺失）。

设计意图（Phase 3 实施时参考）：

```python
# 类似 market_observer 的 experience loop
if read.confidence > 0.7:
    await kb_manager.add_research_note(
        title=f"Equity signal: {ticker} {read.signal_type}",
        body=read.analysis_markdown,
        tags=["equity", ticker, read.signal_type],
    )
```

---

## 七、Ranker 集成（shadow track）

**目标**：equity 机会进入 ranker 但标记为只学习，不下单。

`paper_trader_ranker.py` 改动（已实施，line 121）：
```python
# 现在：
if ac != "crypto":
    return None  # hard kill

# 改为（已实施）：
if ac != "crypto":
    if ac == "equity" and opp.get("execution_eligible") == False:
        # Shadow track only: score but mark no_execute
        return _make_candidate(opp, skip_reason="equity_shadow_track", direction=direction, trade_score=score)
    return None
```

> ⚠️ 设计稿原写 `skip_reason="equity_observation_only"`；实际实现（`paper_trader_ranker.py` line 121）
> 使用 `skip_reason="equity_shadow_track"`。文档已同步更新。

**意义**：
- equity 机会有真实的因子评分
- `post_trade_review` 能比较预测与实际（equity 价格是可以 settle 的）
- 给未来 equity 执行开绿灯时提供历史校准数据

---

## 八、xStocks 数据真实性说明

xStocks（Bitget ON-series）是代币化美股，存在**溢价/折价**（通常 0.1%~2%）。因此：

- **不能**用 xStocks 价格作为 AAPL 的权威价格
- **可以**用 xStocks 价格作为实时代理（NYSE 收盘后）
- cross-validate 字段：`deviation_pct = |xstock_price - yfinance_close| / yfinance_close`
  - > 2%：可能有套利机会，在 metadata 里标注
  - > 5%：数据异常，发出警告，不用于 LLM 分析

---

## 九、实施分层（改动优先级）

### Phase 1：符号统一 + 配置重构（基础层，无破坏）

1. `data/symbols.py`：加 `xstock_to_ticker()`、`ticker_to_xstock()`
2. `config/schema.py`：加 `observed_equities`，保留 `observed_xstocks`（仍为活跃字段；自动派生方案未实施）
3. `storage/models.py`：Opportunity 加 `asset_subclass`、`canonical_ticker`（+ migrations）

### Phase 2：equity_observer agent（核心）

4. 新建 `aletheia/agents/equity_observer.py`
   - 替代 `stock_observer.py` 的 yfinance 路径
   - 替代 `market_observer.py` 里的 xStocks 分支
   - 双路数据 + cross-validate + 统一 LLM prompt（equity 专属）
5. `scheduler.py`：注册 `equity_observer`，US market hours gate，移除 `stock_observer` 调度

### Phase 3：知识库接入（knowledge sharing）

6. `knowledge/router.py`：加 `equity_observer` role + equity 语境查询词
7. `equity_observer`：生成高置信度机会时写 `research_note` 到 KB

### Phase 4：Ranker shadow track（可选，低优先级）

8. `paper_trader_ranker.py`：equity `return None` → shadow track 分支
9. `universe/registry.py`：equity 资产注册（可选，最低优先级）

---

## 十、不改动项（显式声明）

- `paper_trader.py`：paper trader 不执行 equity，无需改动
- `risk/engine.py`：不涉及 equity
- `universe/features.py`、`service.py`：equity 不进主 Universe 流水线
- 现有 crypto 路径完全不受影响（equity_observer 是独立 agent）

---

## 总结

```
【数据互通】
yfinance daily bars  ──┐
                       ├──▶ equity_observer ──▶ unified Opportunity
xStocks 1h OHLCV    ──┘       │
                               ├──▶ market_ohlcv (持久化)
                               └──▶ market_snapshots (持久化)

【知识共享】
equity_observer ──▶ research_note ──▶ KB(equity_observer role)
                                         │
                    ◀──────────────────── advisory_context (FTS 检索)

【不破坏 crypto 流程】
market_observer / universe / paper_trader — 完全不变
```

最小可行版本（Phase 1+2）：~5 个文件改动，1 个新文件，无 DB schema 破坏性变更。
