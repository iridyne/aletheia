# Round 2 Immediate Data Integration Plan

> **状态**：DRAFT（待用户审阅 → 批准后实施）
> **日期**：2026-06-27
> **触发**：Round 1 终止后 ([[aletheia-round1-terminated-fix-batch]])，扩展 market_observer 上下文以提升信号质量
> **审阅入口**：本文档；批准后逐步实施，每步独立可上线/可回滚
> **关联**：
> - 候选数据源调研：`url-to-markdown/claw402.ai/claw402-platform-research.md`
> - Round 1 总账：`docs/issues/backlog/MASTER_FIX_LEDGER_2026-06-27.md`
> - 实验协议：`docs/product/experiments/EXPERIMENT_PROTOCOL.md`

---

## 0. 执行摘要

**核心发现：** 大多数"想要的零成本数据"基础设施**已存在并运行中**。本次扩展的真实代码增量远小于初印象。

| 已有 (✅) | 缺口 (❌) |
|----------|---------|
| `DerivativesCollector`（funding/OI/liquidation, 单交易所） | F&G 指数（Alternative.me）零接入 |
| `MacroCollector`（BTC dominance/total cap/VIX/DXY） | Binance Long/Short Ratio 零接入 |
| `MicrostructureCollector`（orderbook） | CoinGecko Top-N 报价（仅 global） |
| `data_collectors` 调度（2min/tick） | 历史时序回填（仅当前快照） |
| 3 个 storage 表 + repo readers | 多交易所 funding/OI 聚合（仅 primary） |
| `market_observer` 已渲染 enriched context | LSR 字段已在 schema 但永远=None |

**结论**：4 个新 collector + 1 个 schema/migration 微调 + 1 个 enriched context patch。预估代码量 ~500 LOC（含测试），分 5 步上线。

**上线优先级（推荐顺序）**：
1. ⭐ Long/Short Ratio（Binance） — 直接填上 `LeverageState.long_short_ratio`，复用 `crowding_score`
2. ⭐ Fear & Greed（Alt.me） — 单条全市场风险情绪指标，零依赖
3. CoinGecko Top-N — 给 universe 扫描/排序提供宏观参照
4. 历史时序回填 — funding/OI 多点 → trend 而非快照
5. CoinGlass 补强（第二批，key 已就绪） — 多交易所 OI 聚合 + 清算热图

---

## 1. 当前数据采集层现状（factual baseline）

### 1.1 Collectors（`aletheia/data/collectors/`）

| 文件 | Class | domain | 当前能力 |
|------|-------|--------|---------|
| `microstructure.py` | `MicrostructureCollector` | microstructure | orderbook 深度+spread |
| `derivatives.py` | `DerivativesCollector` | derivatives | funding_rate / open_interest / liquidations（**仅 primary 交易所**） |
| `macro.py` | `MacroCollector` | macro | BTC dominance / total_cap / VIX / DXY / SPY / Nasdaq |
| `volume_scanner.py` | `VolumeScannerCollector` | volume | 成交量异常扫描 |

### 1.2 调度（`aletheia/agent_runtime/scheduler.py`）

```
[scheduler.py:463-466]  data_collectors  interval=2min  executor=io
  ├─ Phase A (every tick): MicrostructureCollector + DerivativesCollector → symbols
  └─ Phase B (every 5th tick = ~10min): MacroCollector + VolumeScannerCollector
[scheduler.py:1425-1510]  _run_data_collectors / _async_data_collectors
[scheduler.py:1497-1510]  Phase B macro persist → repo.insert_macro_snapshot
```

### 1.3 Storage（`aletheia/storage/migrations.py`）

| 表 | 关键字段 | 索引 |
|----|---------|------|
| `funding_rate_snapshots` | symbol, exchange, funding_rate, funding_rate_annualized, observed_at | (symbol, observed_at DESC) |
| `open_interest_snapshots` | symbol, exchange, open_interest_usd, open_interest_coin, oi_change_1h_pct, **long_short_ratio**, observed_at | (symbol, observed_at DESC) |
| `liquidation_events` | symbol, exchange, side, quantity_usd, price, event_time, observed_at | — |
| `macro_snapshots` | btc_dominance, total_market_cap_usd, total_market_cap_change_24h_pct, spy_close, vix_close, dxy_close, nasdaq_close, risk_regime, observed_at | (observed_at DESC) |

> **重要发现**：`open_interest_snapshots.long_short_ratio` 列**已存在但永不写入**——`derivatives.py` 没采它。这是第一个零增量胜利。

### 1.4 Repo readers（`aletheia/storage/repositories.py`）

```
[repositories.py:1536]  latest_orderbook(symbol)
[repositories.py:1554]  latest_funding_rate(symbol)
[repositories.py:1572]  latest_open_interest(symbol)
[repositories.py:1590]  recent_liquidations(symbol, hours=1)
[repositories.py:1608]  latest_macro_snapshot()
```

### 1.5 Market observer 集成点（`aletheia/agents/market_observer.py`）

```python
[market_observer.py:127-161]
ob = repo.latest_orderbook(symbol)
fr = repo.latest_funding_rate(symbol)
oi = repo.latest_open_interest(symbol)
macro_row = repo.latest_macro_snapshot()
# → compute_tradability + compute_leverage_state + render_enriched_context_md
# → context = context + "\n\n" + enriched   （喂给 LLM market read）
```

**结论**：新增数据进 `MacroSnapshot` / `LeverageState` 字段后，**LLM 提示词自动看到**——无需改 prompt builder。

### 1.6 Models（`aletheia/data/models.py`）

```python
LeverageState.long_short_ratio: float | None = None   # [models.py:75]  ← 字段已留位
LeverageState.crowding_score: float = 0~100           # [models.py:77]  ← 推荐用 LSR 喂这个
MacroSnapshot.{btc_dominance, total_cap, vix, dxy, ...}  # [models.py:82-91]
```

---

## 2. API 实测证据（本次会话直采，2026-06-27）

### 2.1 CCXT funding rate （Binance/OKX/Bybit/Bitget 全通过）

```
binance fundingRate= 3.62e-05 ts=1782539364000 keys=[datetime,fundingRate,fundingTimestamp,
  indexPrice,markPrice,nextFundingDatetime,nextFundingRate,nextFundingTimestamp,
  previousFundingRate,previousFundingTimestamp,symbol,timestamp,...]
okx fundingRate= 5.27e-05  bybit fundingRate= -5.24e-06  bitget fundingRate= 0.0001
```

**结论**：CCXT 统一 dict shape 已完整可用，`derivatives.py:115-134` `_parse_funding()` 解析方式正确。**多交易所聚合**只是把循环从单 primary 扩到 4 所。

### 2.2 CCXT open interest（3/4 通过）

```
binance OI: oi=104643 keys=[baseVolume, openInterestAmount, openInterestValue, symbol, ...]
okx OI: oi=3176048 (note: 合约张数, 单位差异)
bybit OI: oi=57989
bitget OI: oi=33917
```

**历史 API**：
- ✅ Binance `fetch_open_interest_history` 工作正常
- ✅ OKX 工作（但 sample.symbol=None,需用 perp_sym 自补）
- ✅ Bybit 工作
- ❌ **Bitget `fetchOpenInterestHistory() is not supported yet`** — 必须 fallback 到单点快照

### 2.3 Binance Futures Long/Short Ratio（通过）

```bash
GET https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=1h&limit=3
→ [{"symbol":"BTCUSDT","longAccount":"0.6769","longShortRatio":"2.0950",
    "shortAccount":"0.3231","timestamp":1782529200000}, ...]
```

**字段**：`longAccount` / `shortAccount`（比例 0-1）, `longShortRatio`（=long/short）, `timestamp`（ms）。无 API key，公开免费。

### 2.4 Alternative.me Fear & Greed（通过）

```bash
GET https://api.alternative.me/fng/?limit=3&format=json
→ {"name":"Fear and Greed Index","data":[
    {"value":"15","value_classification":"Extreme Fear","timestamp":"1782518400",
     "time_until_update":"65440"}, ...]}
```

**字段**：`value`（0-100 字符串）、`value_classification`（Extreme Fear / Fear / Neutral / Greed / Extreme Greed）、`timestamp`（unix 秒字符串）。无 API key，无注册，无频率限制文档（实测每小时拉一次绰绰有余）。

### 2.5 CoinGecko Top-N markets（通过）

```bash
GET https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=N
→ [{"id":"bitcoin","symbol":"btc","current_price":60161,"market_cap":1206114337614,
    "total_volume":33322109807,"price_change_24h":446.61,
    "price_change_percentage_24h":0.74791,...}, ...]
```

每个 coin 元素 30+ 字段。无 key 免费 tier ≈30 req/min。

---

## 3. 集成方案逐项

### 3.1 数据源 A — Binance Long/Short Ratio  ⭐ 优先级 1

**为什么先做**：`LeverageState.long_short_ratio` 字段已存在但永远=None；`OpenInterestSnapshot.long_short_ratio` 列已建。**零 schema 改动**即可填满。直接喂 `compute_leverage_state` 已有的 `crowding_score` 逻辑，自动进 LLM 提示词。

**集成位置**：扩展 `aletheia/data/collectors/derivatives.py:DerivativesCollector.collect()` 在 funding+OI 之后增加 LSR 子段。

**实现要点**：
```python
# derivatives.py 内新增
_BINANCE_LSR_URL = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"

async def _fetch_lsr_binance(symbols: list[str]) -> dict[str, float]:
    """symbol → most-recent long/short ratio (1h period). Binance only."""
    results = {}
    async with httpx_client(timeout=10) as c:
        for sym in symbols[:10]:
            binance_sym = sym.replace("/", "")
            try:
                r = await c.get(_BINANCE_LSR_URL,
                    params={"symbol": binance_sym, "period": "1h", "limit": 1})
                items = r.json()
                if items:
                    results[sym] = float(items[0]["longShortRatio"])
            except Exception:
                pass
    return results
```

调用：在 `collect()` 循环之后批量拉，然后回写到对应 symbol 的 `OpenInterestSnapshot.long_short_ratio` 后再持久化。

**新增 provider 方法**：无（直接 httpx，复用现有 `httpx_client`）。

**Storage**：**零改动**——`open_interest_snapshots.long_short_ratio` 列已存在 (migrations.py:1135-1146 区域)。**写入路径**：在 `_parse_oi()` 之前批量拉 LSR → 把值塞进传给 `OpenInterestSnapshot(...)` 构造的 dict 内，单次 `insert_open_interest_snapshot()` 写入。**不**做后续 UPDATE（避免双 commit + (symbol,observed_at) race，per SQLite reviewer feedback）。

**渲染路径（重要修订）**：
- 直接路径已存在：`compute_leverage_state` (normalizer.py:73-77) **已消费 `long_short_ratio`** → 算进 `crowding_score`（skew>0.3 时 `+min(25, skew*30)`）。所以 collector 把数据填进字段后，`crowding_score` 会自动反映 LSR 偏度。
- 但 LLM 只看到 `crowding_score=80/100` 这种聚合数，看不到原始 ratio。**修订**：在 `context_builder.py:94-106` derivatives table 表头追加 `| LSR |` 列，显示 `lv.long_short_ratio` 原始数字（保留 crowding 列）。
- 改动量：context_builder.py 表头字面量改一行 + 表体格式化加一字段（约 +3 行）。

**Config**：`aletheia/config/schema.py` `MarketDataConfig` 内增加一个开关（默认 True）：
```python
class MarketDataConfig(BaseModel):
    ...
    enable_long_short_ratio: bool = True   # Binance LSR; falls back gracefully
```

**Frontend Settings**：`frontend/src/pages/Settings.tsx` 现有 Market Data section 内增加一个 checkbox（沿用现有 toggle 模式，不改组件库）。

**SDD spec**：新增 `tests/specs/spec_macro_signals.py`（新文件，需注册到 `tests/test_spec_conformance.py` 硬编码导入——参见 [[aletheia-sdd-spec-collection-trap]]），加 claim：

| claim_id | level | 断言 |
|---------|-------|------|
| MS-S01 | L2a | `aletheia/data/collectors/derivatives.py` 引用 `_BINANCE_LSR_URL` 或 `globalLongShortAccountRatio` |
| MS-I01 | L2c | `_parse_oi` 或上游写入路径在配置开启时填充 `OpenInterestSnapshot.long_short_ratio` |

**测试**：`tests/test_derivatives_collector.py`（已存在则补 case）：
- mock Binance httpx response → 断言 `OpenInterestSnapshot.long_short_ratio` 被填
- mock 失败 → 断言不抛异常、OI snapshot 仍正常落库（degraded mode）

**频率/速率**：复用 `data_collectors` 2min tick；每 tick ≤10 symbol × 1 req = ~5 req/min，远低于 Binance Futures 公开端点 limit。

**风险**：
- Binance fapi 在某些区域可能被代理拦截 → 已有 `httpx_client` 透传 trust_env 处理 ([[aletheia-proxy-mode-aiohttp]])
- LSR 字段被错误用作交易信号（应只作上下文） → 不进 `paper_trader` 决策，只进 `enriched_context`

---

### 3.2 数据源 B — Fear & Greed Index（Alternative.me）  ⭐ 优先级 2

**为什么早做**：单值市场情绪指标，覆盖全市场而非 per-symbol，**适合放进 `MacroSnapshot`**。零 key，最简单。

**集成位置**：扩展 `aletheia/data/collectors/macro.py:MacroCollector.collect()`，在 CoinGecko global 之后追加 F&G 子段。

**实现要点**：
```python
# macro.py 内新增
_ALT_FNG_URL = "https://api.alternative.me/fng/"

async def _fetch_fng() -> tuple[int | None, str | None]:
    async with httpx_client(timeout=8) as c:
        r = await c.get(_ALT_FNG_URL, params={"limit": 1, "format": "json"})
        d = r.json().get("data", [])
        if d:
            return int(d[0]["value"]), d[0]["value_classification"]
    return None, None
```

**新增 storage 字段（修订版，post-review）**：

**不要手写 ALTER**。必须走 canonical 路径——把列追加到 `aletheia/storage/migrations.py:1265` 的 `_ADDITIVE_COLUMNS` 列表，由 `_ensure_columns()` (migrations.py:1438) 在 `run_migrations()` 内统一处理。`_ensure_columns` 已含 PRAGMA-then-ALTER + duplicate-column race 保护。

**具体追加**：
```python
# 在 _ADDITIVE_COLUMNS 列表末尾（migrations.py:~1434 行附近）追加：
    ("fng_value", "macro_snapshots", "INTEGER"),
    ("fng_classification", "macro_snapshots", "TEXT"),
```

**禁止**：直接在代码中调 `db.execute("ALTER TABLE ...")` 或新写 migration 函数——会绕开 race 保护，参考 [[aletheia-db-lock-root-cause]]。

**新增 model 字段**：`aletheia/data/models.py:MacroSnapshot`：
```python
fng_value: int | None = None
fng_classification: str | None = None
```

**`render_enriched_context_md`**（`aletheia/data/context_builder.py`）：增加一行 `"Fear & Greed: {value} ({classification})"`。**这样 LLM 提示词自动看到**——无需改 market_observer。

**Config**：`MarketDataConfig.enable_fear_greed: bool = True`。

**Frontend Settings**：同 §3.1，Market Data section 内 checkbox。

**SDD spec**：`spec_macro_signals.py`：
| claim_id | level | 断言 |
|---------|-------|------|
| MS-S02 | L2a | `MacroCollector` 引用 `_ALT_FNG_URL` |
| MS-S03 | L2a | `MacroSnapshot` 有 `fng_value` 字段 |
| MS-I02 | L2c | `render_enriched_context_md` 输出包含 F&G 行（当 fng_value 非 None） |

**测试**：`tests/test_macro_collector.py`：
- mock Alt.me → 断言 `MacroSnapshot.fng_value=15`、`fng_classification='Extreme Fear'`
- Alt.me 超时 → 断言不污染 CoinGecko/FRED 已有字段（部分失败仍上报已有 fields）

**频率**：F&G 每天更新一次。MacroCollector 已是 10min cadence (Phase B)，远高于需要——可在 collector 内自加 1h 缓存避免无意义 HTTP。

**风险**：Alt.me 偶尔 500（无 SLA）→ try/except 静默降级，errors 数组登记。

---

### 3.3 数据源 C — CoinGecko Top-N markets  优先级 3

**为什么三梯队**：现有 `MacroCollector` 已拉 CoinGecko `/global`，但只取 BTC dominance + total_cap。Top-N coins 报价目前**未采集**，无法用作 universe 扫描的宏观参照（哪些币在跑、24h vol/mcap 排名变化）。

**集成位置**：**新增** `aletheia/data/collectors/coingecko_top.py`，独立 collector（不挤进 MacroCollector，因为它是 per-coin 数据，不是 global），写入新表 `coingecko_top_snapshots`。

**实现要点**：
```python
# coingecko_top.py
_CG_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"

class CoinGeckoTopCollector:
    domain = "coingecko_top"

    async def collect(self, top_n: int = 100) -> dict:
        rows = []
        async with httpx_client(timeout=10) as c:
            r = await c.get(_CG_MARKETS_URL, params={
                "vs_currency": "usd", "order": "market_cap_desc",
                "per_page": top_n, "page": 1,
                "price_change_percentage": "1h,24h,7d",
            })
            data = r.json()
            now = datetime.now(timezone.utc).isoformat()
            for d in data:
                rows.append({
                    "coin_id": d["id"], "symbol": d["symbol"].upper(),
                    "price_usd": d.get("current_price"),
                    "market_cap_usd": d.get("market_cap"),
                    "market_cap_rank": d.get("market_cap_rank"),
                    "volume_24h_usd": d.get("total_volume"),
                    "price_change_1h_pct": d.get("price_change_percentage_1h_in_currency"),
                    "price_change_24h_pct": d.get("price_change_percentage_24h_in_currency"),
                    "price_change_7d_pct": d.get("price_change_percentage_7d_in_currency"),
                    "observed_at": now,
                })
        return {"rows": rows, "errors": []}
```

**新增 storage 表**（`migrations.py`）：
```sql
CREATE TABLE IF NOT EXISTS coingecko_top_snapshots (
    id TEXT PRIMARY KEY,
    coin_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price_usd REAL,
    market_cap_usd REAL,
    market_cap_rank INTEGER,
    volume_24h_usd REAL,
    price_change_1h_pct REAL,
    price_change_24h_pct REAL,
    price_change_7d_pct REAL,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coingecko_top_obs
    ON coingecko_top_snapshots (observed_at DESC, market_cap_rank ASC);
```

**新增 repo helper（修订版，post-review）**：

⚠️ **关键修正**：`sqlite.py:34` 的连接是 `isolation_level=None`（autocommit），**`was_in_tx` 守卫对 N 个 `_insert()` 循环无效**——每个 _insert 自己自动提交，会变成 N-row commit storm。**必须用 canonical bulk pattern**，参考 `insert_serenity_claims_bulk` (`repositories.py:767-786`)：

```python
def insert_coingecko_top_rows(rows: list[dict]) -> int:
    """Bulk insert. Canonical BEGIN/COMMIT pattern (see insert_serenity_claims_bulk)."""
    if not rows:
        return 0
    from aletheia.storage.models import new_id
    db = get_db()
    already_in_tx = db.in_transaction
    if not already_in_tx:
        db.execute("BEGIN")
    try:
        n = 0
        for row in rows:
            if "id" not in row:
                row["id"] = new_id()
            cols = ", ".join(row.keys())
            placeholders = ", ".join(["?"] * len(row))
            db.execute(
                f"INSERT INTO coingecko_top_snapshots ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
            n += 1
        if not already_in_tx:
            db.commit()
        return n
    except Exception:
        if not already_in_tx:
            db.rollback()
        raise

def latest_coingecko_top(top_n: int = 50) -> list[dict]:
    """Return top_n rows from the most recent batch — single observed_at value.
    
    Two-step read avoids cross-batch mixing: first find max(observed_at),
    then filter rows that share it.
    """
    latest = db.execute(
        "SELECT MAX(observed_at) FROM coingecko_top_snapshots"
    ).fetchone()
    if not latest or not latest[0]:
        return []
    return _select_all(
        "coingecko_top_snapshots",
        where="observed_at = ?",
        params=(latest[0],),
        order="market_cap_rank ASC",
        limit=top_n,
    )

def prune_coingecko_top_snapshots(days: int = 14) -> int:
    """Required: ~14,400 rows/day × 14d = ~200k row ceiling. Canonical was_in_tx pattern."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = db.execute(
        "DELETE FROM coingecko_top_snapshots WHERE observed_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0
```

**调度 prune**：在 `scheduler.py` 的现有 prune 调度块（grep `prune_funding_rate_snapshots`）旁边追加 `prune_coingecko_top_snapshots` 调用。沿用同周期（每日一次）。

**调度**：`scheduler.py` `_async_data_collectors` Phase B 块（每 10min）追加一个 `CoinGeckoTopCollector().collect(top_n=cfg.market_data.coingecko_top_n)`。

**Config**：
```python
class MarketDataConfig(BaseModel):
    ...
    enable_coingecko_top: bool = True
    coingecko_top_n: int = 100
```

**Frontend Settings**：Market Data section 内 toggle + number input（既有模式：`StrategyLifecycle.tsx` 等组件已有类似 input 模式可参考）。

**`market_observer` 集成（修订版，post-review）**：

**渲染路径决策**：选项 (a) 给 `MacroSnapshot` 加 `top_movers_summary: str | None = None` 字段（同 §3.2 F&G 方案，走 `_ADDITIVE_COLUMNS`），由 collector 在写入 `macro_snapshots` 前把 top movers 拼成短串预存。

**为什么不用 `MarketContextSnapshot.macro_movers`**：market_observer.py:130 只读 `repo.latest_macro_snapshot()`，没有 `MarketContextSnapshot` 读取路径——加新 repo reader 增量更大。

**渲染**：在 `context_builder.py:114-130` Macro context 块末尾追加：
```python
if macro.top_movers_summary:
    lines.append(f"24h movers: {macro.top_movers_summary}")
```

**生成逻辑**（在 `CoinGeckoTopCollector.collect()` 之后，由 `_async_data_collectors` Phase B 调用）：取 top 50 行，按 `price_change_percentage_24h_in_currency` 排序，取前 5 涨幅 + 前 5 跌幅，拼接：

> `Top Gainers: SUI +12%, DOT +8%, ARB +6%, ... | Top Losers: APT -7%, ATOM -5%, ...`

新增 storage 字段（`MacroSnapshot.top_movers_summary` + `_ADDITIVE_COLUMNS` 增 1 行）。

**SDD spec**：
| claim_id | level | 断言 |
|---------|-------|------|
| MS-S04 | L2a | `aletheia/data/collectors/coingecko_top.py` 存在并 export `CoinGeckoTopCollector` |
| MS-S05 | L2a | scheduler.py Phase B 注册 `CoinGeckoTopCollector` |
| MS-S06 | L2a | migrations.py 包含 `coingecko_top_snapshots` 表定义 |
| MS-S07 | L2a | repositories.py 包含 `insert_coingecko_top_rows` 和 `latest_coingecko_top` |

**测试**：`tests/test_coingecko_top_collector.py`：
- mock httpx → 断言 rows 行数 + 字段名映射正确
- 网络失败 → 不抛、errors 数组登记
- DB 写入 → 真实 SQLite（不 mock，per [[feedback-testing-no-mock-call-verify]]）

**频率/速率**：10min/tick × 1 req = 6 req/h，远低于 30 req/min 的 CoinGecko 公开 tier。

**风险**：CoinGecko 偶有 429（共享 IP）→ 退避 30s 重试 1 次；多次失败 → 静默降级（macro_snapshot 仍可入库）。

---

### 3.4 数据源 D — 历史时序回填  优先级 4

**为什么后做**：当前 collectors 只采"current"快照，新增历史 N 点可让 LLM 看到 trend 而非孤立值。但这是**提升类**而非**补缺类**——当前快照已基本满足"上下文丰富度"目标。

**集成位置**：扩展 `DerivativesCollector` 增加 `_backfill_history()` 调用，**仅在首次启动或 N 小时无数据时触发**（避免每 tick 都拉历史）。

**实现要点**：
```python
# derivatives.py 内新增
async def _maybe_backfill_funding_history(exchange, sym, since_hours=24) -> int:
    """Pull recent funding rate history for trend context."""
    from aletheia.storage.repositories import _select_all
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    existing = _select_all("funding_rate_snapshots",
        where="symbol=? AND observed_at>?", params=(sym, cutoff), limit=3)
    if len(existing) >= 3:
        return 0  # already have enough trend data
    history = await exchange.fetch_funding_rate_history(
        _to_perp_symbol(sym, exchange), limit=20)
    # ... convert to FundingRateSnapshot rows and insert
```

OI 历史类似，**Bitget 跳过**（CCXT not supported；记 errors）。

**Storage**：**零改动**——直接复用 `funding_rate_snapshots` / `open_interest_snapshots`。

**Config**：`MarketDataConfig.derivatives_history_hours: int = 24`。

**SDD spec**：
| claim_id | level | 断言 |
|---------|-------|------|
| MS-S08 | L2a | `derivatives.py` 引用 `fetch_funding_rate_history` |
| MS-I03 | L2c | Bitget 路径在 NotSupported 时不抛异常 |

**测试**：mock CCXT 返回 history 列表 → 断言 N 行入库 + 不重复（同 timestamp 不双写）。

**风险**：CCXT history 端点速率限制各所不一；只在启动时拉一次，运行期不重复。

---

### 3.5 多交易所聚合（**Round 3 候选，本次不做**）

`derivatives.py` 当前仅取 `provider._primary_name`（默认 Binance）。改为聚合 Binance+OKX+Bybit 需要：
- 改 collector 循环为 multi-exchange
- 表层增加 `exchange='aggregate'` 行或多行去重逻辑
- `latest_funding_rate(symbol)` 含义改变

**判断**：增益（多源 cross-check）vs 复杂度（aggregator 逻辑 + 数据语义变更）不成正比。**留待 CoinGlass 替代**（§4.1）。

---

## 4. 中优先级（key 就绪后实施）

### 4.1 CoinGlass 接入（key 已在 `.env`）  ⭐ 第二批

**用户已注册**（2026-06-27），key 写在项目根 `.env`。

**密钥读取策略**（[[aletheia-coinglass-key-loading]]）：

```python
def _get_coinglass_key() -> str | None:
    from aletheia.config.secrets import get_secret
    import os
    return get_secret("coinglass.api_key") or os.environ.get("COINGLASS_API_KEY")
```

**理由**：keyring 优先（保留前端 Settings 管理路径），os.environ fallback 兼容用户当前 `.env` 写法。**不破坏** `aletheia/config/secrets.py` 既有约定。

**用途**：
1. **多交易所 OI 聚合**（替代 §3.5 自建路径）
2. **清算热图近 30 天历史**（自建无替代，是 CoinGlass 真实价值集中点）
3. **资金费率多所聚合**（补强 §3.4）

**集成位置**：新增 `aletheia/data/collectors/coinglass.py`，独立 collector。

**Storage**：扩展现有 `liquidation_events` + `funding_rate_snapshots`（用 `exchange='aggregate'` 标记聚合行），或新增 `coinglass_*_snapshots` 表族（视 schema 兼容性）。

**Settings UI**：`Settings.tsx` Market Data 段增加 "CoinGlass API Key" 输入框（密码型，redacted 显示 `***xxxx`，沿用 `redact()` 模式 — `secrets.py:81`），保存调用后端 `set_secret("coinglass.api_key", value)`。

**doctor 健康检查**：`aletheia/diagnostics/doctor.py` 增加一项 "coinglass key present"，显示来源（keyring / env / none），redacted。

**SDD spec**：`spec_coinglass.py`（新文件）：
| claim_id | level | 断言 |
|---------|-------|------|
| CG-S01 | L2a | `_get_coinglass_key` 双路读取（keyring 优先 + env fallback） |
| CG-S02 | L2a | collector 在无 key 时静默 skip（不抛、不刷错误日志） |

**优先级标注**：本批落地后启动第二轮 plan 评审。

---

### 4.2 Glassnode  ❌ 已剔除

**原因**：2026-06-27 用户实测确认，Glassnode **已无免费 API tier**，所有 endpoint 必须付费。

**替代路径**（链上指标自算）：

| 指标 | 公开公式 | 数据需求 |
|------|---------|---------|
| AHR999 | BTC 价 / 200d_MA × 几何均值修正项 | yfinance / CCXT 日线 BTC |
| Pi Cycle Top | 111d MA vs 350d MA × 2 | 同上 |
| Rainbow Chart | log 回归 | 同上 |
| Puell Multiple | 矿工日收入 / 365d MA | mempool.space + blockchain.info（自由 API） |
| 200-week MA Heatmap | BTC 价 vs 200w MA | 同上 |

**判断**：链上指标对短期信号增益弱，**优先级最低**，可延后到 Round 3 再做。

记忆已落：[[aletheia-glassnode-paywall]]。

---

## 5. 统一新增 specs 清单（修订版，post-review）

> **关键陷阱**：[[aletheia-sdd-spec-collection-trap]] — 新建 `spec_*.py` 后必须在 `tests/test_spec_conformance.py` 增加硬编码 `import`，否则 pytest 收集 0 个 case。**Step 1 verify 必须先跑 `pytest --collect-only` canary 确认 case 数非 0**。
>
> **SpecClaim 签名**（`tests/specs/spec_meta.py:13`）：6 字段 `SpecClaim(claim_id, contract, section, level, invariant, status='implemented')`。**contract 字段必填，否则 SPEC_REGISTRY 分组错乱**。

### 5.1 contract 复用决策

| 新建 spec | 选用 contract | 加入 REQUIRED_CONTRACTS? |
|----------|---------------|------------------------|
| `tests/specs/spec_macro_signals.py` | `"data_layer_contract"`（复用） | ❌ 否——本批是数据层扩展，**不**构成新信任边界，无需 docs/governance/ 新文档 |
| `tests/specs/spec_coinglass.py` | `"data_layer_contract"`（复用） | ❌ 同上 |

**为什么不开新 contract**：CLAUDE.md SDD §9 仅在「新增信任边界子系统」时要求新增 contract 文档。macro signals 是 collector 层扩展，属于 `data_layer_contract` 已有范围。OBS-* claim 沿用 `agent_contracts` 是同一思路。

### 5.2 完整 claim 表（按数据源分组）

> **assertion_shape** 列锁定校验形态，防止 implementer 用更弱的 grep 替代 AST 检查。

#### MS-* claims（spec_macro_signals.py，全部 `contract="data_layer_contract"`）

| claim_id | level | section | invariant | assertion shape |
|---------|-------|---------|----------|----------------|
| MS-S01 | L2a | "§3.1 LSR collector" | `aletheia/data/collectors/derivatives.py` 模块定义 `_BINANCE_LSR_URL` 常量 | `ast.parse` + `ast.walk` 寻 `ast.Assign(target.id=='_BINANCE_LSR_URL')` |
| MS-S02 | L2a | "§3.1 LSR write path" | `_parse_oi()` 或上游路径将 LSR 填入 `OpenInterestSnapshot.long_short_ratio` 字段（**同一 INSERT 内，非 UPDATE**） | 行为测试: mock httpx LSR → 调 collector → query SQLite `open_interest_snapshots` → 断言 `long_short_ratio` 列非 NULL，且每 (symbol, tick) 至多 1 行 |
| MS-S03 | L2a | "§3.1 derivatives render" | `aletheia/data/context_builder.py` 的 derivatives table 表头**含 "LSR" 列** | 字符串包含 `'\| LSR \|'` 或 AST 寻 derivatives 块字符串字面量 |
| MS-S04 | L2a | "§3.2 F&G collector" | `aletheia/data/collectors/macro.py` 模块定义 `_ALT_FNG_URL` 常量 | 同 MS-S01 的 AST 模式 |
| MS-S05 | L2a | "§3.2 schema" | `MacroSnapshot` Pydantic model 含 `fng_value: int \| None` 和 `fng_classification: str \| None` | `pydantic_model.model_fields` 字典含两 key |
| MS-S06 | L2a | "§3.2 migration" | `aletheia/storage/migrations.py:_ADDITIVE_COLUMNS` 含 tuples `("fng_value","macro_snapshots","INTEGER")` 和 `("fng_classification","macro_snapshots","TEXT")` | 字符串 grep + AST 寻 `_ADDITIVE_COLUMNS` 列表字面量 |
| MS-S07 | L2a | "§3.2 macro render" | `context_builder.render_enriched_context_md` 当 `macro.fng_value` 非 None 时输出 `'Fear & Greed:'` 行 | 行为测试: 构造 `MacroSnapshot(fng_value=15, fng_classification='Extreme Fear', ...)` → 调 render → 断言子串 `'Fear & Greed:'` 在输出 |
| MS-S08 | L2a | "§3.3 CoinGecko collector" | `aletheia/data/collectors/coingecko_top.py` 存在并 export `CoinGeckoTopCollector` 类 | `importlib.import_module` + `hasattr` 类名 |
| MS-S09 | L2a | "§3.3 scheduler" | `aletheia/agent_runtime/scheduler.py` Phase B 块（`_async_data_collectors`）引用 `CoinGeckoTopCollector` | 字符串包含 + 行号位置约束（在 `Phase B` 注释附近 ±15 行） |
| MS-S10 | L2a | "§3.3 migration" | migrations.py `SCHEMA_SQL` 或独立 DDL 含 `CREATE TABLE IF NOT EXISTS coingecko_top_snapshots` | 字符串包含 |
| MS-S11 | L2a | "§3.3 repo helpers" | repositories.py 含 `insert_coingecko_top_rows`、`latest_coingecko_top`、`prune_coingecko_top_snapshots` 三个函数 | AST 寻 `ast.FunctionDef(name in {...})` |
| MS-S12 | L2a | "§3.3 bulk pattern" | `insert_coingecko_top_rows` 函数源码包含 `db.execute("BEGIN")` 或 `db.execute('BEGIN')` | 字符串包含——防止退回 per-row commit storm（**详见 §3.3 修订**） |
| MS-S13 | L2a | "§3.4 history backfill" | derivatives.py 引用 `fetch_funding_rate_history` 或 `_maybe_backfill_funding_history` | 字符串包含 |
| MS-S14 | L2a | "§6 Settings UI coupling" | `frontend/src/pages/Settings.tsx` 含 4 字面量字符串：`'enable_long_short_ratio'`、`'enable_fear_greed'`、`'enable_coingecko_top'`、`'coingecko_top_n'` | Path.read_text() + `all(s in src for s in [...])` |

#### MS-C* claims（L2c invariants，反幻觉/降级路径）

| claim_id | level | section | invariant | assertion shape |
|---------|-------|---------|----------|----------------|
| MS-C01 | L2c | "§3.1 LSR degraded" | `_fetch_lsr_binance` 抛任何异常时，**该 symbol 的 OpenInterestSnapshot 仍正常落库**，`long_short_ratio=None`，error 进 collector errors 列表 | 行为测试: monkeypatch LSR fetcher raise → 调 collector → 断言 `latest_open_interest(symbol)` 非 None 且 `long_short_ratio is None` |
| MS-C02 | L2c | "§3.2 F&G degraded" | `MacroCollector.collect()` 当 `_fetch_fng` 抛异常时，`btc_dominance/total_cap/vix/dxy/nasdaq` 字段仍按各自独立路径填充，`fng_value=None` | 行为测试: monkeypatch `_fetch_fng` raise → 调 collect → 断言其他字段非 None |
| MS-C03 | L2c | "§3.3 CoinGecko degraded" | `CoinGeckoTopCollector.collect()` 在 httpx 异常时**不抛**，返回 `{"rows": [], "errors": [...]}` | 行为测试: monkeypatch httpx_client 抛 RuntimeError → assert no raise + rows 空 |
| MS-C04 | L2c | "§3.3 latest_top batch isolation" | `latest_coingecko_top(N)` 返回的 N 行**共享同一个 observed_at**（不跨 batch 混合） | 行为测试: 注入两批数据（观察时间不同）→ 调 latest_coingecko_top(50) → assert len(set(row['observed_at'])) == 1 |
| MS-C05 | L2c | "§3.4 backfill idempotency" | `_maybe_backfill_funding_history` 在已有 ≥3 行 in `since_hours` 窗口时返回 0（不重复插入） | 行为测试: 预 seed 3 行 → 调 backfill → assert row count 未变 |
| MS-C06 | L2c | "§3.4 Bitget NotSupported" | Bitget OI history 路径在 CCXT NotSupported 时不抛异常，error 进 errors 列表 | 行为测试: 实跑 bitget exchange → 断言 collector 返回正常结构 |
| MS-C07 | L2c | "§3.2 migration idempotent" | 重跑 `run_migrations()` 在列已存在 DB 上不抛——`_ensure_columns` 的 PRAGMA-then-ALTER 已含 try/except `duplicate column` | 行为测试: 在测试 DB 上跑 run_migrations 两次 → assert 第二次不抛 |

#### CG-* claims（spec_coinglass.py，第二批，全部 `contract="data_layer_contract"`）

| claim_id | level | section | invariant | assertion shape |
|---------|-------|---------|----------|----------------|
| CG-S01 | L2a | "§4.1 key loader" | `_get_coinglass_key()` 函数存在，源码包含 `get_secret("coinglass.api_key")` 调用，紧跟 `or os.environ.get("COINGLASS_API_KEY")` fallback | 字符串包含 + 顺序断言（keyring 优先） |
| CG-C01 | L2c | "§4.1 no-key safety" | 当 `_get_coinglass_key()` 返回 None，`CoinGlassCollector.collect()` 早返回 `{"rows": [], "errors": [...]}`，**且未构造任何 httpx 请求** | AST 检查：key 检查节点的 `body` 内**先**于任何 `httpx_client(...)` 调用出现 |
| CG-C02 | L2c | "§4.1 doctor redact" | doctor 显示 coinglass key 时通过 `redact()`（aletheia/config/secrets.py:81），只显示最后 4 字符 | AST 寻 doctor 中对 coinglass key 字符串的 `redact(...)` 调用包裹 |

### 5.3 `tests/test_spec_conformance.py` 必修步骤（**verbatim diff**）

在 `_import_all_specs()` 函数体内的 `from tests.specs import (...)` 块**最末尾**（紧接 `spec_pipeline_entry_enrichment,` 之后、闭合 `)` 之前），追加以下两行 import：

```python
        # Round 2 macro signals (zero-cost data integration)
        spec_macro_signals,
        spec_coinglass,  # second batch, after CoinGlass key wiring
```

**REQUIRED_CONTRACTS 不需要新增**——MS-*/CG-* 均挂在已存在的 `data_layer_contract` 下，`test_every_required_contract_has_claims` 会因 data_layer_contract 已有 claims 而 GREEN。

### 5.4 collection trap canary（每 step 必跑）

```bash
# 必须在 step 标记 done 之前确认 case 收集非 0：
uv run pytest tests/specs/spec_macro_signals.py --collect-only -q | tail -1
# 期望输出: "≥14 tests collected" (MS-S01..S14 + MS-C01..C07 = 21 cases)

# Conformance matrix 必须含 MS-*：
uv run pytest tests/test_spec_conformance.py -v -s 2>&1 | grep -E "(MS-|CG-)" | head
# 期望: 至少 14 行 MS-* 输出
```

---

## 6. 前端 UI 改动汇总（修订版，post-review）

### 6.1 控件放置决策

| 字段 | 放置位置 | 理由 |
|------|---------|------|
| `enable_long_short_ratio` | `MarketDataCard` 内（Settings.tsx:929+，tab='execution'，Section "Market Data Sources"） | per-symbol derivatives 增强，与现有 ccxt_exchange 同类 |
| `enable_fear_greed` | 新建 `MacroSignalsCard`，tab='execution'，新 Section **"Macro & Sentiment Signals"** | 全市场情绪指标，不属于"data source override" |
| `enable_coingecko_top` | 同上 (`MacroSignalsCard`) | 全市场参照（top movers），同上 |
| `coingecko_top_n` | 同上 (`MacroSignalsCard`) | number input，配合上一个 toggle |
| `derivatives_history_hours` | `MarketDataCard` 内 | 同 LSR，derivatives 类 |
| CoinGlass API Key（第二批） | 新 Section **"Macro & Sentiment Signals"** 内 | 密码型输入，redacted 显示 `***xxxx` |

### 6.2 SETTINGS_INDEX 更新（**必修**）

`Settings.tsx:66-99` 的 `SETTINGS_INDEX` 必须新增一行——否则 inline search 找不到新 Section（CLAUDE.md 文件内已注明 "MAINTAIN INVARIANT: every `<Section title="X">` in this file must have a matching entry below"）：

```typescript
// 在 Execution 段（line 77 附近）追加：
{ title: 'Macro & Sentiment Signals', tab: 'execution',
  alt: ['fear greed', 'long short', 'coingecko', 'top movers', 'macro', 'sentiment', 'coinglass'] },
```

### 6.3 后端 API 路由

| 路由 | 文件 | 改动 |
|------|------|------|
| settings GET/POST | `aletheia/server/routes/` 内对应文件（grep `updateMarketDataConfig` 找入口） | 接收新 4 字段写 `data/config.yaml` |
| secrets POST（第二批）| 同 secrets 路由 | 接收 CoinGlass key，调 `set_secret("coinglass.api_key", value)`，response 返回 `redact()` 形式 |

### 6.4 前端 API client

`frontend/src/api/client.ts`（grep `updateMarketDataConfig`）必须把 4 新字段加进 `MarketDataConfig` TypeScript 接口和 `updateMarketDataConfig` 请求体——否则 toggle 切了不保存。

### 6.5 fmtSymbol 范围确认

**遵守 [[aletheia-symbol-display-convention]]**：本批新增 UI 元素**不包含任何交易对符号渲染**——CoinGecko Top-N rows、LSR、F&G 值**只**进 LLM enriched context 和 doctor 诊断，**不**在前端任何页面（Universe.tsx / MarketScan / 新 widget）展示。**显式锁定 scope**——若后续任务给新数据加 per-symbol 前端显示，**必须**走 `fmtSymbol(symbol, marketType)`。

---

## 7. Roll-out 顺序

每步独立可上线、独立可回滚。每步完成后跑 `aletheia doctor` 验证。

### Step 1 — Long/Short Ratio
- 改动：`derivatives.py` + `context_builder.py`（表头加 LSR 列）+ `schema.py` + `Settings.tsx`（MarketDataCard toggle）+ `client.ts` + `spec_macro_signals.py`（MS-S01/S02/S03 + MS-C01）+ `test_spec_conformance.py`（import）
- 验证（必须全过）：
  1. **collection canary**：`uv run pytest tests/specs/spec_macro_signals.py --collect-only -q | tail -1` 必须见 ≥3 cases，否则触发 [[aletheia-sdd-spec-collection-trap]]
  2. **spec 跑通**：`uv run pytest tests/specs/spec_macro_signals.py tests/test_spec_conformance.py -v -s`
  3. **collector 单元测试**：`uv run pytest tests/test_derivatives_collector.py -q`
  4. **DB 实测**：`sqlite3 data/app.db "SELECT symbol, long_short_ratio FROM open_interest_snapshots WHERE long_short_ratio IS NOT NULL ORDER BY observed_at DESC LIMIT 5;"` 应见 ≥3 行非 NULL
  5. **LLM-visible token**：`uv run python -c "from aletheia.data.context_builder import render_enriched_context_md; from aletheia.data.models import LeverageState; s = render_enriched_context_md(['BTC/USDT'], {}, {'BTC/USDT': LeverageState(symbol='BTC/USDT', long_short_ratio=2.09, crowding_score=80, observed_at='2026-06-27T00:00:00Z')}, None); assert '| LSR |' in s and '2.09' in s, s"` 不抛断言
  6. `aletheia doctor` 健康项含 "LSR last age" 显示
- 回滚：`config.market_data.enable_long_short_ratio=false` 即停采，已落库数据自然过期 prune

### Step 2 — Fear & Greed
- 改动：`macro.py` + `migrations.py`（ALTER macro_snapshots） + `models.py`（MacroSnapshot 增字段） + `context_builder.py` + `Settings.tsx` + spec MS-S02/S03/I02
- 验证：
  - 跑 migration（自动幂等）
  - 等 10min 一轮 Phase B → `SELECT fng_value, fng_classification FROM macro_snapshots ORDER BY observed_at DESC LIMIT 1;` 应非 NULL
  - market_observer LLM 输入含 "Fear & Greed: ..."
- 回滚：`enable_fear_greed=false`；列保留（无侵入）

### Step 3 — CoinGecko Top-N
- 改动：新文件 `coingecko_top.py` + `migrations.py` 新表 + `repositories.py` 新 helper + `scheduler.py` Phase B 注册 + `Settings.tsx` + spec MS-S04~S07
- 验证：
  - 表创建：`sqlite3 data/app.db ".schema coingecko_top_snapshots"` 应见
  - 一轮 Phase B → `SELECT symbol, market_cap_rank FROM coingecko_top_snapshots ORDER BY observed_at DESC LIMIT 5;` 应见 top 5
  - market_observer enriched context 含 "24h Top Gainers/Losers" 段
- 回滚：`enable_coingecko_top=false`；表保留

### Step 4 — Historical backfill
- 改动：`derivatives.py` 增加 `_maybe_backfill_history` + spec MS-S08/I03
- 验证：
  - 重启 gateway → 首轮 collector 触发 backfill → `SELECT count(*) FROM funding_rate_snapshots WHERE symbol='BTC/USDT';` 应 >3
  - Bitget 不抛异常
- 回滚：删除 `_maybe_backfill_history` 调用，单点快照行为恢复

### Step 5 — CoinGlass（key 就绪后单独批）
- 改动：见 §4.1
- 验证：`doctor` 显示 key present + redacted；collector 拉到清算热图历史
- 回滚：`enable_coinglass=false`

---

## 8. 风险与边界（修订版，post-review）

| 风险 | 影响 | 缓解 |
|------|------|------|
| Alt.me / CoinGecko 无 SLA → 偶发 5xx | enriched context 缺一行 | try/except 静默，errors 数组登记，doctor 标记 stale_domains；**MS-C02/C03 L2c 锁定降级不污染其他字段** |
| Binance fapi 区域限制 | LSR 长期空 | `httpx_client` 已透传 trust_env，依赖项目级 proxy 配置；**MS-C01 L2c 锁定 LSR fail 不阻塞 OI 写入** |
| 新数据进 LLM 提示词后误用为信号 | 决策链幻觉风险（[[aletheia-round1-terminated-fix-batch]] 教训） | 见 §8.6 完整审计 |
| 新建 spec 不被收集（[[aletheia-sdd-spec-collection-trap]]） | 看似 GREEN 实则 0 case | §5.4 canary 必跑；Step 1-5 每步 done 之前必须见 `--collect-only` 非 0 |
| migration ALTER 与现存数据冲突 | 老 row 字段 NULL | **必走 `_ADDITIVE_COLUMNS` + `_ensure_columns`**（migrations.py:1438）——含 PRAGMA 预检 + duplicate-column race 保护；**禁止**手写 `ALTER TABLE` |
| bulk insert commit storm | SQLite WAL checkpoint 风暴 / 写延迟 | §3.3 已用 canonical `BEGIN/COMMIT` 模式（insert_serenity_claims_bulk:767 范式）；**MS-S12 L2a 锁定不退回 per-row commit** |
| coingecko_top_snapshots 表无 prune → 5M 行/年 | SQLite 文件膨胀 + WAL 写入延迟 | §3.3 已含 `prune_coingecko_top_snapshots(days=14)` + 调度注册 |
| `latest_coingecko_top` 跨 batch 混 | 返回 50 行可能来自两个观察时间 | §3.3 实现已用两步读（max(observed_at) → filter）；**MS-C04 L2c 锁定同一 observed_at** |
| backfill 重复插入 | funding_rate_snapshots 无 UNIQUE 索引，重启可能重写 | §3.4 已含"len(existing)>=3 跳过"guard；**MS-C05 L2c 锁定 idempotency** |
| `data_collectors` job 时间窗超载 | 错过 misfire_grace_time=60 | 当前 Phase A 单 symbol 约 0.5s，10 symbol ≈ 5s × 3 子段 ≈ 15s，加 LSR (+5s) + 加 F&G (+2s, Phase B) + 加 CoinGecko top (+3s, Phase B) ≈ 25s peak，仍 << 2min 窗口 |
| 信任边界影响 | risk/execution 路径无改动 | 本 plan 只动 data layer / observer / config / migration / spec，**不动** `aletheia/risk/`、`aletheia/execution/`、`exchanges/`、`config/secrets.py`（除 doctor 显示用 `redact()` 调用） |
| Binance symbol 转换硬编码 | inverse perp / 非 USDT margined 路径炸 | 本批 LSR 仅支持 USDT-margined linear perp（`sym.replace("/", "")`）；**MS-S01 L2a 测试用例必须含一条"已知 USDT linear works, inverse not in scope"注释**，未来扩展时再走 `to_binance()` helper |

### 8.6 Round 1 反幻觉边界审计（**Step 1 实施前必跑**）

Round 1 终止根因：唯一活学习闭环（trading_decision.py:250 `opportunity_id=None`）→ ranker 喂幻觉 → 池 1890:16 失效 ([[aletheia-learning-loop-noise-not-signal]])。本批新增信号**必须**不污染同一闭环。

#### 8.6.1 grep audit（PR 提交前必跑）

```bash
# 新信号必须不在以下消费路径出现：
for field in long_short_ratio fng_value fng_classification top_movers_summary; do
  for path in aletheia/agent_shell/trading_decision.py aletheia/agents/pi_agent_*.py \
              aletheia/learning/ aletheia/dreaming/ aletheia/research/; do
    if [ -e "$path" ]; then
      grep -rn "$field" "$path" && echo "❌ LEAK: $field in $path" && exit 1
    fi
  done
done
echo "✓ All new fields contained to data layer + market_observer enriched context"
```

#### 8.6.2 _SYSTEM_PROMPT 更新决策

`market_observer.py:55-89` 的 `_SYSTEM_PROMPT` 当前未提及新字段语义。**两条路径**：

| 路径 | 优点 | 缺点 |
|------|------|------|
| (a) **不改 prompt**——依赖 LLM 从 markdown 表头/标签自行推断 | 改动最小、合规 [[aletheia-round1-terminated-fix-batch]] 教训"少干预" | LLM 可能误解 LSR>1 直觉（实际是 retail futures 偏 long 的 contra-indicator） |
| (b) **改 prompt**——加 1-2 行说明（"LSR>1.5 表示零售期货偏 long, 历史是 contra-signal"; "F&G<20 = 极度恐慌, 历史是 mean-reversion 入场区") | LLM 信号识别更准 | 注入特定先验，仍可能被幻觉放大 |

**推荐 (a)**——本批先观察 LSR/F&G 进入 enriched context 后 LLM 自然如何处理；Round 3 评估是否需要补 prompt。**spec MS-S15 L2a**：源码注释明确说明此决策（"_SYSTEM_PROMPT 故意不提及 LSR/F&G，等 Round 3 再评估"）。

#### 8.6.3 enriched context only（不进 Opportunity row）

新字段**不**写入 `Opportunity` 表的任何字段（`key_observations` / `thesis` 仍由 LLM 生成）。LLM 可能在 `key_observations` 里**引用** LSR=2.09（这是合规的，因为它来自快照），但**不**自动注入。

**审计点**：`tests/test_market_observer_integration.py`（已存在则补 case）—— 模拟 LLM 返回包含 LSR 数字的 observation → assert Opportunity row 落库 → assert downstream learning ranker 输入路径**不**访问 `long_short_ratio` 字段（grep tests/ + behavioral）。

---

## 9. 测试计划

### 9.1 单元测试

| 文件 | 覆盖 |
|------|------|
| `tests/test_derivatives_collector.py` | LSR 字段填充、Bitget OI history fallback |
| `tests/test_macro_collector.py` | F&G 字段填充、部分失败降级 |
| `tests/test_coingecko_top_collector.py`（新建） | rows 字段映射、网络失败、DB 写入（真实 SQLite，per [[feedback-testing-no-mock-call-verify]]） |

### 9.2 Spec 测试

```bash
uv run pytest tests/specs/spec_macro_signals.py -v -s  # 必须 ≥11 case
uv run pytest tests/test_spec_conformance.py -v -s     # Conformance Matrix 应含 MS-* 项
```

### 9.3 集成测试

`tests/test_data_collectors_integration.py`（已存在则补 case）：
- 启动 gateway → 2min 后查 funding+OI+LSR+F&G+top 5 项均有最近 1 条记录
- enriched context render 输出格式不破坏既有 `compute_leverage_state` 调用

### 9.4 Doctor 检查

`aletheia doctor` 新增显示：
- LSR 最近一条年龄
- F&G 最近值 + 年龄
- CoinGecko top 表行数 + 最新 observed_at

---

## 10. 下一阶段预留

| 项目 | 触发条件 |
|------|---------|
| CoinGlass 接入 | 完成 §4.1 步骤 — key 已就绪 |
| 多交易所 funding/OI 聚合 | CoinGlass 数据稳定后评估是否还需自建 |
| Glassnode 替代（链上指标自算） | Round 3 评估优先级 |
| LSR 历史时序（多周期） | Step 1 上线观察后视效果 |
| F&G 历史趋势（不仅当前值） | 同上 |

---

## 11. 实施前 checklist（修订版，post-review）

- [ ] 用户审阅本 plan，确认接入顺序 + 是否接受字段 schema 增量
- [ ] 在 `docs/issues/backlog/MASTER_FIX_LEDGER_2026-06-27.md` 加一行指向本 plan（接续追踪）
- [ ] 创建 git 分支 `feat/round2-macro-signals`（独立于现 `fix/round1-learning-fixes` 修复分支）
- [ ] **§8.6.1 grep audit** 在每 step PR 提交前必跑——零 LEAK 才能合并
- [ ] **§5.4 collection canary** 在每 step done 之前必跑——`--collect-only` 非 0
- [ ] **docs/contracts/data_model.md 更新**（CLAUDE.md SDD §7）——记录：
  - `macro_snapshots` 增 `fng_value INTEGER NULL` + `fng_classification TEXT NULL` + `top_movers_summary TEXT NULL`
  - 新表 `coingecko_top_snapshots`（完整 schema）
  - `open_interest_snapshots.long_short_ratio` 字段**含义首次实际启用**（之前列存在但永不写）
- [ ] **SETTINGS_INDEX**（Settings.tsx:66-99）新增 "Macro & Sentiment Signals" 条目
- [ ] **api/client.ts** `MarketDataConfig` TypeScript 接口加 4 新字段
- [ ] Step 1 完成后跑全量测试 `uv run pytest tests/ -q --tb=line`（后台执行，参考 CLAUDE.md Testing Strategy）

---

## 附录 A — Review 修订溯源

本 plan v2（2026-06-27）针对 3 路 adversarial review 反馈做了以下修订：

| Reviewer | 关键发现 | 修订位置 |
|----------|---------|---------|
| SDD | SpecClaim 缺 contract/section 字段（critical） | §5 全表重写, 含 contract + section + assertion shape 列 |
| SDD | per-source L2c 缺失（high × 3） | §5.2 新增 MS-C01~C07 共 7 个 L2c invariants |
| SDD | test_spec_conformance.py 注册位置未明确 | §5.3 verbatim diff |
| SQLite | bulk insert commit storm（high） | §3.3 改 canonical BEGIN/COMMIT 模式 (`insert_serenity_claims_bulk:767`) |
| SQLite | ALTER 未走 `_ADDITIVE_COLUMNS`（high） | §3.2/§3.3 改走 `_ensure_columns` 路径 |
| SQLite | `latest_coingecko_top` 跨 batch 混（high） | §3.3 改两步读（max(observed_at) → filter） |
| SQLite | 缺 prune helper（high） | §3.3 加 `prune_coingecko_top_snapshots` |
| Frontend | Settings UI 未指名 component（high） | §6.1 决策表（MarketDataCard vs MacroSignalsCard） |
| Frontend | context_builder 不自动 render 新 field（high） | §3.1/§3.3 显式写明渲染层改动 |
| Frontend | Round 1 反幻觉边界审计不完整（high） | §8.6 新增三步审计（grep / prompt / Opportunity boundary） |
| Frontend | LSR 渲染路径未定（high） | §3.1 决策：context_builder 加 LSR 列 |

---

*文档完成于 2026-06-27 — 基于实际 codebase 现状测绘 + 5 API 实测证据生成，非纯推测。*
