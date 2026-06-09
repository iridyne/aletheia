# AI Handoff

## Last Updated

2026-06-10 (此 checkout 从未运行 + 重建 gitignore 吞掉的 aletheia/data/ 包 + 修复 DNS resolver + boot-prove)

## Recent Change (检出态真相 + aletheia/data/ 重建 + DNS resolver 修复 — 2026-06-10)

> 分支 `fix/restore-data-package`（未合并），3 个 commit。**关键：本 checkout 与文档描述的状态完全不同。**

**1. 本 checkout 从未运行过**（审计发现，与下方 06-09 条目的"作者机器"状态对立）：
无 `data/config.yaml`、无 `.secret.key`/`secrets.enc`、`app.db` 仅 2 条 audit + 1 条 strategy。
所以本文档下方"Phase 0 观察期运行中 / 06-09 真实 demo 单 / 504 测试通过"全部是**原作者机器**的状态，不是这里。**每次会话须用 `data/` + `gateway status` 核实实况，勿信旧文档叙事。**

**2. `aletheia/data/` 整个源码包缺失且从未提交**（`.gitignore` 的裸 `data/` 规则在每一级目录都匹配，连源码包一起吞掉）→ 克隆后 risk/execution/trading/agents 全部 `ModuleNotFoundError`，pytest 在 collection 阶段就 5 个 error。
按**测试即契约**重建全部 7 个文件（committed tests + 17 处调用点完全锁定行为），`.gitignore` 改为根锚定 `/data/`。全套 **515 passed**（原文档称 504）。未改任何信任边界语义。

**3. DNS resolver bug**（本 host 致命，Phase 0 基础设施）：`aiodns` 已装 → aiohttp 默认 c-ares `AsyncResolver`，本机读不到 DNS 配置 → **每个 ccxt 请求都 `ClientConnectorDNSError`**（urllib/OS resolver 却正常 200，所以症状伪装成"交易所不可用")。修复：`CCXTProvider` 注入 `ThreadedResolver`(OS getaddrinfo) 的 aiohttp session，并在 `close()` 里关闭它（注入 session 时 ccxt `own_session=False` 不会自己关，顺带消除 "Unclosed client session" 泄漏）。

**4. Boot-prove 通过**（无持久进程、无 autostart、完全可逆）：config bootstrap（安全默认 `Observe`/`live_disabled`/kill_switch off）→ 15 个 scheduler job 全部构建 → 重建的数据层拉到 binance/bitget 实时 ticker+OHLCV → `collect_market_snapshots` 7 个标的 0 error → `market_observer` 无 LLM 时优雅降级产出 7 个 neutral/speculative opportunity（不崩）。

**Phase 0 观察门卡在仅人能提供的输入**（CLAUDE.md 安全红线，AI 不得伪造）：
(a) LLM provider + API key（Settings→Model Provider 或 `aletheia model set`）——没有它 opportunity 全 neutral/speculative，paper_trader 不会开仓；
(b) Bitget demo API 凭证（`aletheia secrets set bitget.demo.*`）——没有它 `bitget_demo` 单到不了交易所（`paper_internal` 本地撮合仍可兜底）。
两者就位后：`permission_mode=Paper` → 启动 gateway → 跑满 72h 观察窗口。

---


## Recent Change (database is locked 真根因修复 — 2026-06-09)

**这是整个交易管道实际停摆的真根因**(此前误以为是 LLM 不通 / agent 不触发 / 调度延迟）。

**现象**：errors.log 累计 `database is locked` **1652 次**，所有写 job（ticker / exchange_sync / portfolio_snapshot / paper_trader / observer）失败，市场数据停更 90+min。

**根因**（`storage/repositories.py` 的 `_insert`/`_update`/`_delete_old_rows`）：提交条件 `if not was_in_tx and not db.in_transaction: commit()`。但 Python sqlite3 默认 `isolation_level=''` 下，INSERT/UPDATE/DELETE 会**自动开启事务**，写完后 `db.in_transaction` 恒为 True → 条件恒 False → **commit 从不执行**。写事务悬挂、持有 WAL 写锁，每线程独立连接下其他写线程 `busy_timeout=10s` 后全部 locked。bug 自 `d403fcf` 存在，只在持续高频并发写下爆发。

**修复（commit `a33dc86`，504 测试通过）**：三处改为 `if not was_in_tx: commit()`（看写**之前**的状态，非之后）。`transaction()` 嵌套路径不受影响（实证两种场景）。纯修复不改交易语义。

**实证**：复现脚本确认 INSERT 后 in_transaction=True、旧条件求值 False；临时还原旧条件后新回归测试失败 → 证明测试真能抓 bug。新增 `test_storage.py` 两个测试用**真实 file DB + 第二个独立连接**验证写已提交、锁已释放（共享注入连接会掩盖此 bug）。

**教训**：Python sqlite3 隐式事务反直觉——"写后判断要不要提交"必须看写**之前**的状态。测试陷阱:单一共享连接会掩盖锁 bug,要用真实 file DB + 独立第二连接。

---

## Recent Change (确定性自愈层 + Phase 0 观察期重置 — 2026-06-09)

**审计发现**："72h 观察期进行中"是记账假象——网关进程活着，但功能已停摆。

| 表象 | 实情 |
|---|---|
| 网关在跑 | 跑的是旧代码 `1aa1feb`，落后 HEAD **7 个 commit** |
| 文档称 healthy | `/api/health` = **degraded**：`code_updated_pending_restart` + `market_data_stale:1114min` |
| 观察期进行中 | `paper_trader` 已 **11h 未触发**；调度 cadence 残缺 |
| 系统在交易 | 294 opportunity → **0** proposal / 0 order / 0 fill |

**根因**：`run_health_check()`（每 10min）**已经算出 drift 信号、已经告警**，但**没有任何代码消费它去重启**。系统喊了 11 小时"我在跑旧码"，没有耳朵在听。这正是 ROADMAP "False confidence" 失败模式：进程活着 ≠ 系统在运行。

**修复（commit `8a6e008`，501 测试通过）**：给 `run_health_check()` 加确定性（零 LLM）自愈分支。
- `pidfile.py` — `supervisor_owns_pid()`：`read_pid()!=getpid()` 即有 supervisor 兜底（线程上下文等价于 route 的 `app.state._owns_pid`，零 subprocess）
- `config/schema.py` — `HealthConfig` 加 `self_heal_{enabled,min_interval_sec,max_per_hour,code_drift,market_data,port_cleanup}`；`min_interval`(1800s) 刻意 > supervisor 窗口(600s)，自愈单独永不耗尽 supervisor 的 5/600 give-up 预算
- `health.py` — `_trigger_restart`/`_can_self_restart`/`_alert_suppressed`/`_heal_port_cleanup` + `run_health_check()` 决策流。code drift / stale → 守卫+限频 `os._exit(42)` 重生；孤儿端口 → `kill_pid_tree`（**显式排除 self+supervisor**，否则误杀自己的 supervisor → 新僵尸）；LLM/kill_switch/agent_failure → 仅告警
- `tests/test_self_heal.py` — 12 个回归测试（全部 patch `_os_exit` 不真退出）

**端到端验证通过（2026-06-09）**：空 commit 制造 drift → `/api/health` 检测到 `code_updated_pending_restart` → 下个 health 周期自愈自动重启 → supervisor 重生新码 → drift 清除（audit `self_heal_restart: code_drift`）。今天那类僵尸态以后在 ≤10min 内自动消除。

**端到端测试当场抓到一个严重 bug，已修（commit `abd7773`，502 测试通过）**：观察到两次 `self_heal_restart` 正好间隔 10min（一个 health 周期），30min cooldown 没拦住。根因——限频计数器是【内存】list，而自愈动作是 `os._exit(42)`，重启清空内存，新进程永远看不到"刚重启过" → cooldown/ceiling 永不触发 → 任何"重启修不了"的持久 issue（如下方 DB 锁致 `market_data_stale`）会让网关**每 10min 无限重启**（比僵尸态更糟）。修复：限频改从【持久化 audit.jsonl】读 `self_heal_restart` 事件计数（`_recent_self_heal_restarts()`），跨重启有效。已验证：冷启动新进程读到旧进程写的 restart 记录、正确返回 cooldown 拒绝。**教训：任何"动作会重启进程"的状态必须持久化，绝不放内存。**

**两个独立的新发现**（均已在本会话解决，见下方更早条目）：
1. `database is locked` → **真根因，已修**（`a33dc86`，见顶部条目）。这是"重启后 agent 不触发 / 数据停更"的真因。
2. `gateway restart` 早先一次启动失败 → **诊断为误判**：当时网关跑旧码 `1aa1feb` + 测试抢 DB 锁，叠加造成 `do_start` 静默失败假象；三修复到位后实测 CLI restart 干净成功（stop→新 supervisor 重生→healthy→boot 对齐→drift 清除）。restart 无 bug。

**关于 Pi/Agent Shell 设计文档**：用户提供的 `aletheia_pi_agent_shell_integration_design.md` 主张顶层 LLM 操作员 agent。结论：方向对、v0 范围对，但**应分两层**——(A) 确定性自愈 supervisor（即本次交付，无 LLM，是稳定性根基）先做；(B) LLM Apex Agent Shell 作为"Operator Layer"排到 Phase 0/1 稳跑证明之后的平行线。本次完成了 A 的核心切片。

---

## Recent Change (Gateway 稳定性修复 — ghost-socket 彻底消灭 — 2026-06-08)

**问题现象**：前端顶栏状态显示 "Gateway Stopped"（红灯），但页面正常访问。
`gateway stop` 报 "port still in use"，`gateway start` 报 "foreign process holding port"。

**根因链**：
1. 网关 supervisor 被 kill 后，uvicorn 的 multiprocessing worker 子进程变孤儿
2. 孤儿 worker cmdline 是 `multiprocessing.spawn.spawn_main(...)`，不含 `aletheia.server.app:app` 或 `_gateway_runner` 标记
3. `find_gateway_orphans()` 靠 cmdline 标记识别，永远找不到这些 worker
4. Worker 继承了父进程的 listening socket，持续占端口并用旧代码响应请求
5. `/api/status` 用 `pidfile.is_running()` 判断状态，而 PID 文件不存在 → 返回 `false`

**修复（5 个文件，"one stop all" 三层策略）**：
- `pidfile.py` — 新增 `find_port_holders(port)`：用 netstat/lsof 直接按端口找 PID，兜底所有逃逸进程
- `lifecycle.py` — `do_start` + `do_stop` 加入端口兜底：cmdline 扫描 → port holder 扫描 → 强制 tree-kill
- `health.py` — `/api/status` 返回 `gateway_running: True`（能响应请求 = 在运行，不依赖 PID 文件）
- `app.py` — lifespan 启动时自动补写 PID 文件（任何启动方式都有 PID）；追踪 ownership，shutdown 只清自己写的
- `gateway.py` — restart 端点检查 supervisor/autostart 是否活跃，无恢复机制时拒绝自杀

**验证**：46 tests passed；重启后 `gateway_running: true`，PID 文件正确指向 supervisor。

---

## Recent Change (Markets K线动态加载 — 2026-06-08)

**目的**：Markets 页 K 线从「固定一段」改为主流交易所式动态加载 —— 向左滚到边缘自动续加更早历史，右沿 10s 轮询保持实时。

**后端**：`ccxt_provider.py` 加 `_TF_MS` + `timeframe_ms()` + `get_ohlcv(since=)`；`/api/market/ohlcv` 加 `before`（unix 秒）参数做历史分页（`since=before-limit*tf`，过滤 `>=before` 防重叠）；缓存只服务默认最新视图。

**前端**：`getOhlcv` 加 `before` 参；`PriceChart` 重写为命令式 store + refs，`subscribeVisibleLogicalRangeChange` 触发 `loadOlder()`（前插后平移逻辑区间保持视窗），`setInterval` 10s `series.update()` 实时尾部。

**测试/验证**：488 passed（+2）；后端 urllib + Vite 视觉 E2E 均确认三行为触发、零重叠、零 console error。

---

## Recent Change (Pipeline Gap Analysis — 2026-06-08)

**全自动交易闭环现状分析**：对照"全市场扫描→分析→策略→开仓→平仓→评估→学习→总结"
完整闭环，逐环节代码核实。新文档 `docs/PIPELINE_STATUS.md` 记录完整结果。

**核心发现**：
- 管道骨架 80% 接通（扫描→分析→开仓→平仓→总结 均自动运行）
- 三大结构性差距：经验闭环只闭 30%、agent 无协作流、策略是文字非可执行规则
- 两个 agent (`post_trade_reviewer` / `risk_critic`) 代码完整但未排入 scheduler
- 系统从未跑过真正的 unattended paper trading session（stage-0 尚未开始）

**新增文档**：`docs/PIPELINE_STATUS.md`（逐环节现状 + 差距 + scheduler 全景 + 北极星阶段）

---

## Recent Change (xStocks 接入 + 管道 bug fix — 2026-06-08)

**Bitget xStocks (ON-series tokenized equities) — observation-only:**
- `config/schema.py` — `observed_xstocks: list[str]` 默认 `["AAPLON/USDT","TSLAON/USDT","NVDAON/USDT","SPYON/USDT"]`
- `trading/instruments.py` — `xstocks_instruments_from_config()` 返回 `exchange="bitget"` InstrumentId 列表
- `agents/market_observer.py` — xStocks 分析 loop，独立 Bitget provider，`asset_class="equity"`；paper_trader gate 门控 skip（observation-only）
- `trading/market_data.py` — xStocks ticker 写入 market_snapshots；提取 `_fetch_and_store()` helper
- `tests/test_xstocks_observer.py` — 4 个新测试

**管道 bug fix (三项):**
1. `models.py` — `Opportunity.confidence` 默认 `"medium_confidence"` → `"speculative"`（对齐 paper_trader _CONFIDENCE_RANK）
2. `paper_broker.py` — 移除 naive `datetime.utcnow()`；`Order.filled_at` 默认 None
3. `market_data.py` — xStocks 也写入 market_snapshots（position_manager 定价依赖）

**测试**：474 passed，1 flaky（test_write_audit_jsonl，既存，单跑通过）

---

## Recent Change (Chart blackout root-cause + market-data hardening — 2026-06-08)

**Symptom**: all lightweight-charts components blank. **Root cause**: the gateway
process was running stale code (booted before the `CCXTProvider` switch was committed),
so `/api/market/ohlcv` kept hitting legacy `BitgetClient` with a symbol Bitget rejects.
`/api/health` showed healthy throughout — the failure was invisible.

**Immediate fix**: `aletheia gateway restart` — charts restored (verified real candles
for BTC/ETH/SOL).

**Hardening to prevent recurrence:**
- **`aletheia/agent_runtime/build_info.py`** (NEW) — records boot commit; `code_drift_issue()`
  flags when disk HEAD moves past the booted commit. Wired into `lifespan()` (records
  boot commit before scheduler starts) and `compute_health()` (appends
  `code_updated_pending_restart:<old>..<new>` → `/api/health` degraded).
- **`get_market_data_provider()`** in `aletheia/data/ccxt_provider.py` is now the single
  market-data entry point (per-call, never cached). Deleted the dead `get_default_provider()`
  singleton. Routed `routes/data.py`, `trading/market_context.py`, `trading/market_data.py`
  through it. The 2 Bitget-only xStocks sites stay direct `CCXTProvider(exchange="bitget",
  fallback_exchange="bitget")` with deliberate-exception comments.
- **CLAUDE.md** Development Rules: market-data-via-factory rule added.

See DEVLOG `## 2026-06-08 (later³)` for full detail.

## Recent Change (InstrumentId + Symbol Format Unification — 2026-06-08)

Full implementation of `exchange:symbol:market_type` unique instrument identity across the pipeline.

**Core new type — `aletheia/data/symbols.py`:**
- `InstrumentId` frozen dataclass: `exchange`, `symbol` (canonical `BTC/USDT`), `market_type` (`spot`|`perp`|`futures`)
- `str(instrument)` → `"binance:BTC/USDT:spot"`. `InstrumentId.parse("binance:BTC/USDT:spot")` round-trips cleanly.
- `instrument.to_ccxt_symbol()` → `"BTC/USDT"` (spot) or `"BTC/USDT:USDT"` (perp, settle=quote directly, no USD mapping)
- `instrument.to_bitget_symbol()` → `"BTCUSDT"` (execution-boundary only)
- `to_ccxt_perp(symbol)` helper added

**Config (`aletheia/config/schema.py`):**
- `MarketDataConfig.observed_instruments: list[str]` — e.g. `["binance:BTC/USDT:spot"]`
- Legacy `observed_symbols` kept as fallback when `observed_instruments` is empty

**Storage (additive migrations — zero downtime):**
- `exchange TEXT` column added to `market_snapshots` and `market_ohlcv`
- `aletheia/storage/repositories.py` — all query functions accept optional `exchange=` param

**Pipeline — all now canonical `BTC/USDT` end-to-end:**
- `aletheia/trading/instruments.py` — `observed_instruments_from_config()` returns `list[InstrumentId]`
- `aletheia/trading/market_data.py` — passes `InstrumentId` to `CCXTProvider`, stores `exchange=instrument.exchange`
- `aletheia/agents/market_observer.py` — iterates `InstrumentId` objects; `market_type` from instrument
- `aletheia/data/ccxt_provider.py` — accepts `InstrumentId | str`; `_resolve_ccxt_symbol()` handles both
- `aletheia/agents/paper_trader.py` — `allowed_symbols = {to_canonical(s) for s in tradable_symbols_from_config(cfg)}`

**API + frontend:**
- `aletheia/server/routes/data.py` — `/market/ohlcv` and `/market/snapshots` accept `exchange=` + `market_type=`
- `aletheia/server/routes/settings.py` — `GET /settings` now includes `market_data` block; `POST /settings/symbols` syncs `observed_instruments`
- `frontend/src/api/client.ts` — `getOhlcv()` uses `URLSearchParams` (auto-encodes `/` in `BTC/USDT`)
- `frontend/src/pages/Settings.tsx` — read-only "Observed instruments" chip row in Risk & Symbols tab (renders when `observed_instruments` non-empty; requires gateway restart to show after first symbols save)

**Tests:**
- `tests/test_instrument_id.py` — 45 new tests
- `tests/test_entry_logic.py` — fixtures + assertions updated to canonical format
- Final: 1 failed (pre-existing `test_strategy_causal_chain` LLM dependency) / 470 passed

**Invariant verified:**
```
binance:BTC/USDT:spot  ≠  bybit:BTC/USDT:spot  (different exchange)
binance:BTC/USDT:spot  ≠  binance:BTC/USDT:perp (different market type)
```

**Architecture decision:** `docs/DECISIONS.md` 2026-06-08 "InstrumentId" entry.

---

## Prior Change (Full-Market Analyzer + Paper Trading Pipeline Fix — 2026-06-08)

Three-bug paper trading fix + full-market analyzer expansion.

**Paper trading pipeline bugs fixed:**
- `data/config.yaml` — `autonomous_paper_trading: false → true` (main switch was off)
- `aletheia/agents/market_observer.py` — per-symbol loop (one LLM call + one Opportunity per symbol); previously only symbols[0] got an opportunity
- `aletheia/agents/paper_trader.py` — `allowed_symbols` now uses `to_canonical()` so Bitget-format config entries match canonical opportunity symbols
- `aletheia/portfolio/pnl.py` — `_latest_price` now uses `latest_snapshot()` dual-format lookup instead of direct DB query

**Full-market analyzer — new agents:**
- `aletheia/agents/stock_observer.py` — US equity observer; yFinance daily OHLCV + indicators (SMA/RSI/ATR/Bollinger/momentum); `asset_class="equity"`, observation-only; scheduled Mon–Fri 14:30–21:00 UTC
- `aletheia/agents/prediction_observer.py` — Polymarket prediction market observer; Gamma API (public REST); `asset_class="prediction"`, observation-only; scheduled every 30 min
- `aletheia/data/polymarket_provider.py` — Polymarket Gamma API provider (httpx + urllib fallback)

**Config expanded:**
- `observed_symbols`: BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, DOGE/USDT
- `observed_stocks`: SPY, QQQ, NVDA, AAPL
- `observed_prediction_markets`: crypto, politics

**Schema + migrations:**
- `aletheia/config/schema.py` — `MarketDataConfig` extended with `observed_stocks`, `observed_prediction_markets`
- `aletheia/storage/models.py` + `migrations.py` — `asset_class` column on `opportunities`

**Tests fixed:**
- `tests/test_entry_logic.py` — 2 tests updated for per-symbol loop (assert BTC row specifically)
- `tests/test_paper_autoloop.py` — seed symbol changed `BTCUSDT → BTC/USDT` (canonical format)
- `tests/test_runtime.py` — job count updated `11 → 13` (+ stock_observer + prediction_observer)

**Test result:** 398 passed, 19 failed (all 19 are pre-existing; net +4 fixed vs prior baseline).

**Scope decision:** user confirmed system is post-MVP; Polymarket and additional exchanges (xStocks etc.) are now in scope. See `docs/DECISIONS.md` 2026-06-08 entry.

---

## Prior Change (Symbol Format Unification + Data Audit — 2026-06-08)

Comprehensive data audit and symbol format unification across the full agent pipeline.

**Root problem:** CCXTProvider writes snapshots/OHLCV with canonical `BTC/USDT` keys, but
`market_observer.py` was converting to Bitget format (`BTCUSDT`) before storing opportunities,
causing paper_trader's filter to silently drop everything.

**Fixes applied:**
- `aletheia/agents/market_observer.py` — removed `to_bitget()` call; opportunities now stored in canonical `BTC/USDT` format (matching snapshot keys)
- `aletheia/agents/paper_trader.py` — `allowed_symbols` now built as `{to_canonical(s) for s in tradable_symbols_from_config(cfg)}` so Bitget-format config entries still match canonical opportunities
- `aletheia/risk/engine.py` — symbol check normalises both sides with `to_canonical()` before comparing
- `aletheia/server/routes/settings.py` — `/settings/symbols` endpoint now syncs `market_data.observed_symbols` (canonical) whenever tradable symbols are updated
- `frontend/src/api/client.ts` — `getOhlcv()` and `getMarketSnapshots()` both use `encodeURIComponent(symbol)` so `BTC/USDT` `/` doesn't split the URL path
- `frontend/src/components/MarketTicker.tsx` — ticker symbol keys changed from `BTCUSDT` to `BTC/USDT` to match snapshot map
- `frontend/src/pages/Charts.tsx` — opportunities filter uses exact canonical match (no `|| replace('/', '')` workaround)
- `tests/test_entry_logic.py` — 6 tests updated: assertions and fixture data changed from Bitget to canonical format; test renamed `test_observer_stores_canonical_format_symbol`

**Test result:** 22 pre-existing failures only (same as baseline). 0 new failures.

---

## Prior Change (Data Layer Refactor + Markets UI — 2026-06-08)

Complete data source decoupling from Bitget. Market data (OHLCV, tickers) now goes
through CCXT/yFinance; `BitgetClient` is execution-context only.

**New files:**
- `aletheia/data/__init__.py` — package
- `aletheia/data/providers.py` — `MarketDataProvider` Protocol, `Ticker`/`Candle` models (incl. `high_24h`, `low_24h`, `provider`)
- `aletheia/data/symbols.py` — canonical `BTC/USDT` format + per-provider converters
- `aletheia/data/ccxt_provider.py` — CCXT Binance (+ Bybit fallback), auto-persists OHLCV
- `aletheia/data/cache.py` — in-memory TTL cache (10 min default)
- `aletheia/data/yfinance_provider.py` — macro data (SPY/VIX/DXY/Gold), default OFF

**Changed files (backend):**
- `aletheia/config/schema.py` — added `MarketDataConfig` (provider, observed_symbols, cache TTL, yfinance flag)
- `aletheia/trading/market_data.py` — uses CCXTProvider; `latest_snapshot()` dual-format lookup
- `aletheia/trading/market_context.py` — uses provider protocol + cache; optional macro block
- `aletheia/trading/instruments.py` — added `observed_symbols_from_config()`; BITGET_GRANULARITY annotated execution-only
- `aletheia/storage/migrations.py` — new `market_ohlcv` table; `high_24h`/`low_24h`/`provider` columns on `market_snapshots`
- `aletheia/storage/repositories.py` — `upsert_ohlcv_candles()` + `get_ohlcv_candles()`
- `aletheia/server/routes/data.py` — `/api/market/ohlcv` uses CCXTProvider
- `aletheia/agents/market_observer.py` — uses `observed_symbols_from_config()`
- `aletheia/exchanges/bitget/client.py` — `get_ticker`/`get_ohlcv` annotated `# execution-context only`

**Changed files (frontend — FRONTEND_ISSUES §5.1–5.4 resolved):**
- `frontend/src/pages/Charts.tsx` — canonical `BTC/USDT` symbols; `ProviderChip` data-source label; `refetchInterval: 30_000`; H/L display; freshness indicator

**Unchanged:** `execution/`, `risk/`, `portfolio/exchange_sync.py`, `bitget/auth.py`

**Test result:** 424 passed (0 failed). 3 existing tests updated to mock CCXTProvider.

**Open next:** enable `market_data.enable_yfinance_macro: true` in config to activate macro block.

---

## Prior Change (2026-06-08 — SQLite 并发锁 + 核实)

**SQLite 并发锁**：`aletheia/storage/sqlite.py` 改为 `threading.local()` 每线程独立连接。`timeout=30`；`check_same_thread=True` 恢复。

**BACKEND_ISSUES §10 核实**：8 条高风险条目（strategy_id / live-order / asyncio / paper 风险 / clientOid / kill-switch / paper_trader Gate A / commit_all）全部 grep 验证已真修。

---

## Recent Change (WebUI redesign — frontend-only session)

Complete structural redesign of the React dashboard, addressing the owner's pain points
(information density / layout / transparency / workflow — "everything uncomfortable").
**No backend / API contract changes.** Diagnosis: the UI was "14 flat DB-table pages";
the framework wants §23.1's 6 roles organized around the causal chain + a workflow flow.

**Result: 14 pages → 10 role-organized destinations**, grouped by a 5-phase IA
(Observe / Decide / Execute / Evolve / Operate):
- **Cockpit** (already redone earlier): PnL zone + agent feed + right rail.
- **Approval Desk** (Proposals elevated): pending queue as decision cards w/ inline risk
  verdict + confirm-to-approve; decision history table.
- **Trades & Positions** (Positions + Paper Trades merged): open positions → orders/fills
  ledger → closed positions, hosting the **causal-chain inspector** (`CausalChain.tsx`).
- **Agent Activity** (Agent Runs + Opportunities merged): Runs/Opportunities tabs; run
  drawer has the Reasoning-Steps mount point.
- **Learning** (Dreaming + Knowledge Base + Reports merged): 3 tabs; §4 KB taxonomy kept.
- Keepers (Markets/Strategies/Risk & Controls/Settings/News) got a `PageHeader` with a
  one-line role descriptor; News got the spec-mandated "manual entry / ingestion not
  configured" honesty banner.

New shared components: `CausalChain`, `Tabs`, `PageHeader`. Deleted 7 merged page files.
Legacy routes redirect (no dead links). Verified live via preview MCP (nav, tabs, honest
empty states, zero console errors). tsc + vite build green.

**Docs:** `WEBUI_PAGE_MAP.md` (per-page verdicts + build log), `OVERVIEW_REDESIGN.md`
(Cockpit decisions), **`WEBUI_BACKEND_HANDOFF.md`** (parked backend gaps for the backend
agent), cross-referenced in `BACKEND_ISSUES.md §12`. Backend gaps (UI degrades honestly
until built): position→proposal FK, `GET /api/agent-runs/{id}/steps`, real exchange equity.

## Recent Change (feat/strategy-causal-chain)

Two passes on this branch (373 tests pass; branch not yet merged):

**1. Strategy↔trade causal chain (gap #2).** Connected the four-way break in the
`strategy_id` chain (the 2026-06-06 §1.1 "fix" had only wired the downstream fallback).
Now: `create_strategy` persists `parameters`; `strategy_designer` produces a parameter
spec, auto-advances a strategy to `paper_running` (ungated states only — live gates
untouched), and is scheduled daily; `market_observer` binds opportunities to an active
strategy. Promotion-gate `paper_trades` no longer stuck at 0. Makes paper evidence
trustworthy — prerequisite for north-star stage 1/2.

**2. Re-audit of BACKEND_ISSUES §10.** The "all fixed 2026-06-06" archive is ~75%
true, ~25% claimed-but-not. Eight falsely-archived issues really fixed (live-order
ordering, health-alert delivery, paper/demo risk bounds, clientOid, kill-switch
TOCTOU lock, paper_trader autonomous gate, commit_all scheduling, gateway log
rotation). §10 now carries a correction banner. **Lesson: never trust a "fixed"
claim without grepping current code** — see DEVLOG 2026-06-07.

Change-controlled modules touched (risk/, execution/, exchanges/): all changes are
hardening (tighter bounds, idempotency, serialization) — no live gate weakened, no
hard limit relaxed. Each has a focused regression test.

## Current State Summary

Post-MVP trading system with 8+ agents, 474 tests. Full-market observation pipeline operational (crypto + xStocks + US equities + prediction markets). Paper trading pipeline functional end-to-end. Key gap: experience loop (learning→decision feedback) only 30% closed.

## What Was Completed

- **All BACKEND_ISSUES §1 (trust invariants)** — Fixed: execution gateway transaction isolation, git pull ff-only, update confirm gate, strategy_id causal chain, promotion gates
- **All BACKEND_ISSUES §2 (data integrity)** — Fixed: ~25 items including PnL fees, knowledge status filtering, CORS, audit symmetry, risk evaluation, health endpoints, scheduler error handling, telegram bot hardening, model provider healthcheck, codex auth regex, markdown file safety, knowledge cleanup-on-failure
- **All BACKEND_ISSUES §3 (dead code/cosmetic)** — Fixed: ~15 items including orchestrator traceback, run_logger null fields, pidfile validation, redact format, market observer config, bitget orderId check
- **All FRONTEND_ISSUES §3 (engineering)** — Fixed: shared types, DataTable, DetailDrawer, chart theme, markdown styles, marker click callback
- **All FRONTEND_ISSUES §1-§2** — Fixed: proposal risk_decision display, KB markdown reading, autonomous_paper_trading visibility, effective config card, promotion gates display, chart-trade bidirectional linking, risk boundaries with units, dreaming 6-section layout, overview sparkline + run timeline, cross-page navigation, agent run structured tool calls, performance metrics in Reports

## What Is In Progress

Nothing currently in progress.

## Important Files

- `AGENTS.md` — Governance rules and safety boundaries (primary reference)
- `docs/BACKEND_ISSUES.md` — Backend issue tracker (§10 has fixed items)
- `docs/FRONTEND_ISSUES.md` — Frontend issue tracker (§10 has fixed items)
- `docs/aletheia_trading_os_implementation_guide.md` — Full design reference
- `docs/RUNBOOK.md` — Operations guide

## Important Decisions

See docs/DECISIONS.md and the existing AGENTS.md governance framework.

## Known Issues

Code-layer items in BACKEND_ISSUES.md and FRONTEND_ISSUES.md §1-§3 have been worked through. Two **architectural / north-star gaps** remain explicitly open and unresolved — they are not bugs to flip into §10, they are direction-of-travel work:

- **portfolio-sync-gap** — Portfolio dashboard does not reflect real exchange account state (only locally-created positions). Filed as BACKEND_ISSUES.md §2.3 last bullet. Touches change-controlled `aletheia/exchanges/bitget/client.py`; requires explicit authorization before work begins.
- **experience-loop-gap** — knowledge-write-only loop; agents rarely read prior research/reviews back into decisions. Tracked in `docs/north_star_references.md §6`. Stage-2 work per north-star sequencing; do NOT start before stage 0 (stable run) and stage 1 (learning quality) are proven.

Recent:
- 2026-06-07 cockpit merge resolved (`ace780a`); local 5-zone shell discarded after explicit review against AGENTS.md priorities. See DECISIONS.md and `docs/superpowers/specs/2026-06-07-merge-postmortem-design.md`.
- 2026-06-07 webui lightweight optimization: `.brand`/`.topbar` baseline aligned; `<AccountSnapshot/>` (Total Value with portfolio-sync-gap caveat / Today PnL / Open Positions / Pending Proposals) added to Overview + Paper Trades + Positions. Five-zone shell / Right Inspector / global Command Center deferred to stage 1+. See DECISIONS.md.

## Next Recommended Step

Per `docs/PIPELINE_STATUS.md` 北极星阶段图，当前处于 **stage 0 → 0.5 过渡期**：

**阶段 0.5（立即可做）**：
1. 把 `post_trade_reviewer` + `risk_critic` 排入 scheduler（代码完整但从未自动跑）
2. 验证测试基线：`uv run pytest -q` — 应为 474 passed
3. 启动 gateway 并运行 72h unattended paper trading
4. 观察 Learning Ledger 指标（lessons_consulted / activation_ratio）是否非零

**阶段 1（72h 稳跑后）**：
5. 证明学习质量——dreaming_agent 的 Learning Ledger 指标趋势向好
6. 如果指标全零 → 先诊断（LLM 不通 / opportunity 全 neutral / 配置未开）

**阶段 2（需显式授权）**：
7. 最小经验闭环——paper_trader 读 lessons / agent 间工作流 / risk_critic 输出进入决策

详见 `docs/PIPELINE_STATUS.md`。

## Commands to Run

```bash
uv sync
cd frontend && npm run build && cd ..
pytest tests/
uv run aletheia gateway start
```

## Warnings

- Live trading is disabled by default. Never enable without explicit user confirmation.
- Trust boundary modules (risk/, execution/, auth.py, secrets.py) require extra caution.
- Frontend must be rebuilt after source changes: `cd frontend && npm run build`

## Context for Next AI Session

All documented issues have been addressed. The system is ready for integration testing. Key architectural changes: execution gateway no longer wraps order placement in an outer transaction (§1.2 fix), git pull uses ff-only merge (§1.2 fix), promotion gates API exposed for frontend consumption.
