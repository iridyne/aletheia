# Scanner Architecture Research — 竞品大规模扫描方案对比

> 用户需求：参考市场竞品如何解决大范围扫描、超时、rate limit 等问题
> 关联问题：§16 scanner "swap" ExchangeError (已修复 d8908e5)

---

## 1. 竞品架构对比

### 1.1 freqtrade (最流行的 OSS crypto bot)

**数据获取模型**:
- **Pairlist 分层过滤**: `StaticPairList` → `VolumePairList` → `AgeFilter` → `SpreadFilter`
  每个 filter 是独立 plugin，链式执行，**前置 filter 先裁剪再做昂贵操作**
- **批量 ticker**: 用 `fetch_tickers()` 一次拉全交易所 ticker，再 filter
- 从不为每个币单独调 `fetch_ticker()`——这是关键性能差异
- **OHLCV 串行+缓存**: dataprovider 缓存 candle data，每个 timeframe 只取增量

**Rate Limit 处理**:
- CCXT 内置 `rateLimit` + `enableRateLimit=True`（自动节流）
- freqtrade 额外做 `retrier` 装饰器：指数退避 3 次，每次 exponential backoff
- 429 时不 crash，sleep 后重试

**超时保护**:
- 每个 exchange call 有 `ccxt_async_config.timeout` (30s default)
- 整体 iteration 有 `process_timeout` 限制单次策略循环时长

**Exchange quirks 处理**:
- `exchange/` 目录有 per-exchange wrapper（`exchange/binance.py`, `exchange/bybit.py`）
- 每个 wrapper override 特定行为（market type 映射、symbol 格式转换）
- `_ft_has` dict 声明每个交易所支持的功能集

**关键设计原则**:
> "Cheap filter first, expensive enrichment only on survivors"

---

### 1.2 hummingbot (做市引擎)

**数据获取模型**:
- **Connector 抽象层**: 每个交易所一个完整 connector（不直接用 CCXT）
- **WebSocket-first**: 实时数据全走 WS，REST 只做初始化和恢复
- **Order Book 本地维护**: WS 推送 diff，本地重建 L2 orderbook
- 不需要"扫描"——数据是被推过来的

**超时/断连**:
- WS 心跳检测 + 自动重连（exponential backoff）
- 多级 fallback：WS → REST polling → cached state
- Health monitor 组件独立检测每个 connector 状态

**对 Aletheia 的启示**:
- 我们不做做市（不需要 tick-level 数据），但"推而不拉"的思路适用于 universe refresh
- 可以用 WS ticker stream 做 volume pre-filter，只对通过的做 REST OHLCV

---

### 1.3 Jesse (量化回测框架)

**数据获取**:
- **Import-then-analyze**: 先批量下载历史数据到本地 DB，再跑策略
- 扫描用 `candles` 表的本地数据，不实时拉取
- 适合回测但不直接适用于实时扫描场景

**Symbol Management**:
- 静态 route 配置：用户声明要跟踪的 symbols
- 不做动态 universe discovery

---

### 1.4 OctoBot (多交易所 crypto bot)

**数据获取模型**:
- **Evaluator + Tentacle 架构**: 数据层是独立 service，evaluator 订阅
- **并行 producer-consumer**: 数据 producer 异步推送到 channel，多个 consumer 消费
- **分层更新频率**: 不同 timeframe 不同刷新间隔（1m→1min, 1h→5min, 1d→1h）

**Rate Limit**:
- 每 exchange 独立 rate limiter（令牌桶算法）
- 超限时排队而非报错

**Universe Management**:
- `trading_mode` 定义可交易集
- 支持 wildcard 配置 `*_USDT`，但不做动态排名

---

### 1.5 CCXT 自身的最佳实践

**批量操作**:
- `fetch_tickers()` 一次调用获取所有/指定 symbols（单次 REST call）
- `fetch_ohlcv()` 不支持批量（每 symbol 一次调用）——这是性能瓶颈所在

**Rate Limit 内建机制**:
- `enableRateLimit=True`：自动 sleep 在调用间
- `rateLimit` 属性（毫秒）：两次调用最小间隔
- 429 时 CCXT 抛 `RateLimitExceeded`，调用方需 catch + backoff

**Exchange-specific mapping**:
- `exchange.options["fetchMarkets"]` 是 exchange-specific 的，没有统一标准
- Binance: `["spot", "linear", "inverse"]`
- Bybit: `["spot", "linear", "inverse", "option"]`
- OKX: `["spot", "swap", "futures", "option"]` (OKX 接受 "swap"!)
- **教训**: 不能假设 CCXT 统一类型名直接作为 exchange option

---

## 2. 通用 Quant 架构模式

### 2.1 Tiered Scanning (分层过滤)

```
Layer 0: Static universe (exchange.load_markets)     — 免费
Layer 1: Batch ticker filter (fetch_tickers)         — 1 API call
Layer 2: Volume/spread pre-filter                    — 纯计算
Layer 3: OHLCV enrichment (concurrent, rate-limited) — N API calls
Layer 4: Indicator compute + scoring                 — 纯计算
```

**原则**: 每层淘汰 50-80% 候选，昂贵层只处理幸存者。
我们的 scanner-perf-upgrade (3b9f8d5) 已实现这个模式。

### 2.2 Adaptive Rate Limiting

```python
class AdaptiveRateLimiter:
    def __init__(self, base_delay_ms=100):
        self.delay = base_delay_ms
        self.consecutive_429 = 0

    async def throttle(self):
        await asyncio.sleep(self.delay / 1000)

    def on_success(self):
        self.consecutive_429 = 0
        self.delay = max(self.delay * 0.9, self.base_delay)  # 缓慢恢复

    def on_rate_limit(self):
        self.consecutive_429 += 1
        self.delay = min(self.delay * 2, 30000)  # 指数退避，cap 30s

    def on_timeout(self):
        self.delay = min(self.delay * 1.5, 15000)  # 温和加倍
```

### 2.3 Circuit Breaker Pattern

```python
class ExchangeCircuitBreaker:
    CLOSED = "closed"      # 正常
    OPEN = "open"          # 熔断中，不发请求
    HALF_OPEN = "half"     # 试探恢复

    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.state = self.CLOSED
        self.failures = 0
        self.last_failure = None
        ...
```

当连续 N 次失败时熔断，停止发请求；等待 recovery_timeout 后半开试探。

### 2.4 Stale-While-Revalidate 缓存

```
如果 cache 存在且未过期 → 直接返回
如果 cache 存在但已过期 → 返回旧值 + 后台异步刷新
如果 cache 不存在 → 同步获取
```

适用于 universe snapshot：即使 refresh 失败，旧 snapshot 仍可用（我们已有 TTL 机制）。

### 2.5 Chunked Concurrent Fetch

```python
async def fetch_ohlcv_chunked(symbols, exchange, concurrency=5, chunk_size=50):
    sem = asyncio.Semaphore(concurrency)
    results = {}

    for chunk in [symbols[i:i+chunk_size] for i in range(0, len(symbols), chunk_size)]:
        tasks = []
        for sym in chunk:
            async def fetch_one(s=sym):
                async with sem:
                    try:
                        return s, await exchange.fetch_ohlcv(s, "1h", limit=30)
                    except RateLimitExceeded:
                        await asyncio.sleep(exchange.rateLimit / 500)
                        return s, await exchange.fetch_ohlcv(s, "1h", limit=30)
                    except Exception:
                        return s, None
            tasks.append(fetch_one())
        chunk_results = await asyncio.gather(*tasks)
        results.update(dict(chunk_results))

    return results
```

---

## 3. 对 Aletheia Scanner 的具体改进建议

### 3.1 当前架构 (scanner-perf-upgrade 后)

已实现:
- ✅ Batch ticker fetch (单次 API call)
- ✅ Volume pre-filter (pure compute)
- ✅ Concurrent OHLCV with semaphore (_OHLCV_CONCURRENCY=5)
- ✅ 24h TTL fallback (graceful degradation)

### 3.2 推荐改进 (按 ROI 排序)

| 优先级 | 改进 | 预期收益 | 复杂度 |
|--------|------|----------|--------|
| P0 | Exchange-specific market type mapping | 避免 §16 类回归 | 低 |
| P1 | Exponential backoff retry on OHLCV fetch | 减少单个超时导致整体失败 | 低 |
| P2 | Per-exchange adapter config | 不同交易所不同 fetchMarkets 值 | 中 |
| P3 | Circuit breaker on exchange connection | 连续失败时快速 fail，不浪费超时等待 | 中 |
| P4 | Incremental OHLCV (只取新 candle) | 减少数据传输量 | 中 |
| P5 | WS ticker stream for pre-filter | 消除 batch ticker REST 调用 | 高 |

### 3.3 P0: Exchange Market Type Mapping (立即可做)

```python
# aletheia/data/exchange_config.py (新文件)

EXCHANGE_MARKET_TYPES = {
    "binance": {
        "spot": "spot",
        "perp": "linear",        # Binance 叫 linear
        "inverse": "inverse",
    },
    "bybit": {
        "spot": "spot",
        "perp": "linear",
        "inverse": "inverse",
    },
    "okx": {
        "spot": "spot",
        "perp": "swap",          # OKX 接受 swap
        "futures": "futures",
    },
    "bitget": {
        "spot": "spot",
        "perp": "umcbl",         # Bitget specific
    },
}

def get_fetch_markets(exchange_name: str, include_perp: bool = False) -> list[str]:
    """Get the correct fetchMarkets option for the given exchange."""
    mapping = EXCHANGE_MARKET_TYPES.get(exchange_name, {"spot": "spot", "perp": "swap"})
    types = [mapping["spot"]]
    if include_perp and "perp" in mapping:
        types.append(mapping["perp"])
    return types
```

### 3.4 P1: Retry with Backoff

```python
async def _fetch_ohlcv_with_retry(exchange, symbol, timeframe="1h", limit=30, max_retries=2):
    """Fetch OHLCV with exponential backoff on transient errors."""
    for attempt in range(max_retries + 1):
        try:
            return await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        except (NetworkError, RequestTimeout) as e:
            if attempt == max_retries:
                return []
            delay = (2 ** attempt) * 0.5  # 0.5s, 1s, 2s
            await asyncio.sleep(delay)
        except Exception:
            return []
    return []
```

---

## 4. 关键教训总结

1. **CCXT 不是真正"统一"的** — exchange options (fetchMarkets, defaultType) 的合法值因交易所而异。必须有 per-exchange mapping。

2. **Cheap filter first** — freqtrade 的 pairlist plugin chain 是最佳实践。我们的 volume pre-filter 已实现这个模式。

3. **Never crash on a single symbol failure** — 我们已有 try/except per-asset，这是正确的。

4. **Stale data > no data** — 24h TTL + static fallback 确保系统永不停摆。这是我们已有的优势。

5. **WebSocket > REST for real-time** — 长期方向，但 MVP 阶段 10min REST polling 足够。

6. **Concurrency != parallelism** — semaphore(5) 对 OHLCV 是合理的平衡（freqtrade 用串行 + cache，我们用并发 + pre-filter）。

---

## 5. 实施路线图

**Phase 1 (已完成)**:
- ✅ swap → linear 修复
- ✅ batch ticker + volume pre-filter + concurrent OHLCV

**Phase 2 (推荐下次迭代)**:
- P0: exchange_config.py mapping 层
- P1: OHLCV retry with backoff
- 预计 2h 工作量，0 新依赖

**Phase 3 (未来)**:
- P2-P3: per-exchange adapter + circuit breaker
- P5: WS ticker stream (需要架构变更)

---

---

## 6. 高优先级代码修复 (可直接实施)

### Fix 1: Per-symbol OHLCV timeout (P0, 2 行)

```python
# features.py — 每个 fetch_ohlcv 加 hard timeout
ohlcv_1h = await asyncio.wait_for(
    exchange.fetch_ohlcv(ccxt_sym, timeframe="1h", limit=30),
    timeout=8.0
)
```

**来源**: OctoBot 的 `asyncio.wait_for` 模式。没有 timeout，一个慢 symbol 会阻塞整个 semaphore slot。8s 是 OctoBot 对主流交易所的默认值。

### Fix 2: Batch ticker 分块 (P1, 4 行)

```python
# 当前 fetch_tickers(spot_symbols[:500]) 可能超 Binance URL 长度限制
CHUNK = 200
for chunk in [symbols[i:i+CHUNK] for i in range(0, len(symbols), CHUNK)]:
    tickers.update(await exchange.fetch_tickers(chunk))
```

**来源**: freqtrade 的 chunked batch 模式。当前代码有 `[:500]` 硬截断但无分块循环。

### Fix 3: 单 symbol ticker fallback 重试 (P2, 6 行)

```python
for attempt in range(2):
    try:
        ticker = await exchange.fetch_ticker(symbol)
        break
    except Exception as e:
        if attempt == 1:
            continue  # 放弃
        await asyncio.sleep(2.0)  # 首次失败 sleep 后重试
```

**来源**: freqtrade 的 quadratic backoff 简化版。当前 bare `except: continue` 在瞬态 rate-limit spike 时静默丢弃 symbols。

### Fix 4: Binance symbols 过滤 (P1, 修复 round 20 发现)

```python
# fetch_tickers 前过滤不合法 symbols
import re
_BINANCE_SYMBOL_RE = re.compile(r'^[A-Z0-9\-._/]+$')
valid_symbols = [s for s in spot_symbols if _BINANCE_SYMBOL_RE.match(s)]
```

**来源**: round 20 巡检发现 Binance 拒绝含小写字符的 symbols。预过滤比让 batch 整体失败更好。

---

## 7. 竞品对比矩阵

| 能力 | freqtrade | hummingbot | OctoBot | Jesse | Aletheia (当前) |
|------|-----------|------------|---------|-------|-----------------|
| Batch ticker | ✅ | ✅ WS | ✅ WS | ❌ | ✅ |
| Volume pre-filter | ✅ PairList chain | N/A (做市) | ✅ trading_mode | ❌ | ✅ |
| Concurrent OHLCV | ✅ chunked gather | N/A (WS) | ✅ sem+timeout | ❌ 串行 | ✅ sem(5) |
| Per-symbol timeout | ❌ | ✅ | ✅ 8-15s | ❌ | ❌ 需加 |
| Retry/backoff | ✅ quadratic 3x | ✅ exponential | ✅ | ❌ | ❌ 需加 |
| Exchange quirk map | ✅ per-exchange class | ✅ connector | ✅ | ❌ | ❌ 需加 |
| Circuit breaker | ❌ | ✅ | ❌ | ❌ | ❌ |
| Stale-while-revalidate | ❌ | ✅ | ❌ | ❌ | ✅ (TTL fallback) |

---

*参考框架*: freqtrade v2024.x, hummingbot v2.x, OctoBot v0.4, CCXT v4.x
*编写时间*: 2026-06-17
