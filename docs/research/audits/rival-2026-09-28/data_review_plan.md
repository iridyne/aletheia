# Core data / extensions data：分析与修复方案

日期：2026-09-28。源码基线 `0318e5efc6bca6ac5be3393c68fcd90cc5195d70`。主 Agent 独立静态分析；没有交易所请求、数据库写入、行为测试或 Fedora 数据检查。

## 覆盖与结论

Core data 为 23 个 Python 文件、6,769 行、56 个 except；extensions/data 为 27 个文件、7,513 行、101 个 except。所有文件经过 AST 与宽异常入口扫描；重点追踪历史下载、CCXT 精确 venue、fallback chain、OHLCV 写入、价格缓存和 freshness。collector_health / runner 检查了状态分类及异常边界，没有逐项深审所有外部 feed 与 collector。

确认一条 D0 历史持久化契约偏差，编号 **DATA-1**：尚未收盘的 K 线可由历史下载器写入 market_ohlcv。下载失败后 CLI exit 0 已在 [CLI F6](cli_shitmap.md) / [#1395](https://github.com/iridyne/aletheia/issues/1395) 记录，不重复计数。

## DATA-1：历史下载器绕过 closed-bar 边界

证据链（路径均相对仓库根）：

| 边界 | 当前行为 |
|---|---|
| `docs/design/contracts/ohlcv_provider_result_contract.md:59` | forming bar 保留 is_closed=false；只持久化确认收盘的 CCXT bar |
| `extensions/data/ccxt_provider.py:691` | 最新 bar 的开盘时间 + timeframe 晚于当前时间时，标为未收盘 |
| 同文件 `:719` `_persist` | 正常路径过滤 is_closed，说明返回形成中 bar 与持久化确认 bar 是不同语义 |
| `core/aletheia/data/history_downloader.py:90` `_record_page` | 未检查 is_closed，整页传给 upsert，并把这些行计入 bars_fetched / newest_ts |
| 同文件 `:123,198` | forward/bootstrap 只要求 bar 开始时间早于 now；形成中 bar 满足此条件 |
| `core/aletheia/storage/repositories_market_data.py:519` | upsert 写所有传入 bar，表字段不保存 is_closed |
| `core/aletheia/cli/data_cmd.py:306`、`agent_runtime/scheduler.py:3855`、`data/active_ohlcv_backfill.py:16` | 真实 CLI、scheduler、active backfill 使用同一下载器 |

触发条件：下载到当前尚未结束的周期，provider 返回合法且 is_closed=false 的尾部 bar。历史表会存入其暂态 OHLCV。随后增量 forward 从 newest_existing + tf_ms 开始，这个下载路径可能不再请求上一根暂态 bar 的最终值。其他实时刷新可能补写，故这里不声称“永久错误”或已污染真实收益，只证明历史下载本身没有保证最终修正。

## 完整修复方案

1. 在下载器共享 `_record_page` 入口过滤明确未收盘的行，再调用现有 upsert。forward、backward、bootstrap 都必须经过此入口；正常 provider 返回形成中 bar 的契约保留，不能粗暴在 get_ohlcv 中删掉所有形成中数据。
2. bars_fetched、first_ts、last_ts、progress 只计实际接受用于历史持久化的行。分页推进仍依据 provider 原始页的时间进度，避免过滤后空页造成重复请求或死循环；页长终止判断也应使用正确的原始页语义。不要在不同分支复制三份过滤。
3. 新写入修复与存量处置分开。存量表无 is_closed 标记，仅凭时间戳不能证明历史值是否暂态。可以提出对每个精确 instrument/timeframe 的尾部重叠重取，但先证明 provider 已收盘并保持 exchange/market_type 身份；不默认清表、不改写全部历史、不把重取范围扩展为无限回填。是否需要处理真实存量必须用只读证据和授权的数据修复范围决定。
4. 不改变 OHLCV window 保留数量、市场选择、Risk 或执行资格；仓库级 upsert 若要统一拒绝形成中数据，必须先审完全部 writer 的输入契约，本轮最小根因修复在下载器共享入口即可。

验收设计：固定时钟 + 合成 provider + 隔离 SQLite，返回一根 closed 和一根 forming，断言仅 closed 入库且返回计数/范围一致；覆盖全 forming 页、bootstrap、新上市空页、已有记录增量路径；保证游标单调且请求数量有界。到下一周期再调用时，原 forming bar 的最终值能够被请求并写入。验证 market_type/venue 不变，provider.close 仍在成功/异常路径执行。若另行批准存量重叠修复，再加遗留暂态尾行被最终值替换且历史其余行不变的案例。本轮未执行这些测试。

查重：查询 history_downloader、OHLCV unclosed、historical open candle 等；发现 #1503 涉及未来时间行/非尾部未来 bar，#2801 涉及 market type 丢失，均不能当成本问题已修复的证明。未找到明确覆盖 DATA-1 的记录；这是有限查询结论，未建新 issue。

## 保留的防御与后续深审

exact_venue 回填不会静默换源；衍生品 fallback chain 不退回 spot-only provider。freshness 明确区分 missing/invalid/future/stale，protective price 路径核对精确 venue/market identity 与价格时间，不能把展示价格缓存的宽松策略直接套到保护性退出。collector runner 的异常日志及 collector 结果中的 errors/partial 需要结合消费者判断，不能批量删除 except。

本轮没有证明全部 collector 的持久化失败都反映到 health，也没有完成所有来源时钟、macro/positioning/options provenance、cache 并发和 fallback 总墙钟的深审。它们仍是后续核对范围，不得据此声称 data 模块“无剩余问题”。

## 全文件结构扫描清单

口径：物理行包含空行/注释；except 为 AST ExceptHandler；宽指 bare except 或捕获 Exception/BaseException。该清单证明结构扫描覆盖，不代表每个文件都完成调用链深审。

| 文件 | 物理行 | except / 宽 |
|---|---:|---:|
| `core/aletheia/data/__init__.py` | 0 | 0 / 0 |
| `core/aletheia/data/active_ohlcv_backfill.py` | 407 | 8 / 4 |
| `core/aletheia/data/cache.py` | 117 | 1 / 1 |
| `core/aletheia/data/collector_health.py` | 503 | 6 / 2 |
| `core/aletheia/data/collector_runner.py` | 74 | 3 / 2 |
| `core/aletheia/data/collectors/__init__.py` | 1 | 0 / 0 |
| `core/aletheia/data/context_builder.py` | 572 | 4 / 0 |
| `core/aletheia/data/data_coverage.py` | 330 | 2 / 0 |
| `core/aletheia/data/derivatives_facts.py` | 619 | 3 / 0 |
| `core/aletheia/data/freshness.py` | 190 | 3 / 0 |
| `core/aletheia/data/history_downloader.py` | 242 | 3 / 3 |
| `core/aletheia/data/macro_liquidity.py` | 218 | 2 / 0 |
| `core/aletheia/data/market_structure.py` | 886 | 4 / 3 |
| `core/aletheia/data/models.py` | 205 | 0 / 0 |
| `core/aletheia/data/news_entities.py` | 68 | 0 / 0 |
| `core/aletheia/data/normalizer.py` | 102 | 0 / 0 |
| `core/aletheia/data/options_volatility.py` | 35 | 0 / 0 |
| `core/aletheia/data/price_feed.py` | 695 | 12 / 8 |
| `core/aletheia/data/providers.py` | 403 | 2 / 0 |
| `core/aletheia/data/smc_enrichment.py` | 76 | 0 / 0 |
| `core/aletheia/data/symbols.py` | 644 | 1 / 0 |
| `core/aletheia/data/venue_health.py` | 180 | 0 / 0 |
| `core/aletheia/data/venue_listing_cache.py` | 202 | 2 / 1 |
| `extensions/data/__init__.py` | 1 | 0 / 0 |
| `extensions/data/ccxt_provider.py` | 910 | 12 / 8 |
| `extensions/data/collectors/__init__.py` | 1 | 0 / 0 |
| `extensions/data/collectors/coinalyze.py` | 401 | 8 / 5 |
| `extensions/data/collectors/coingecko_top.py` | 160 | 2 / 1 |
| `extensions/data/collectors/defillama.py` | 287 | 2 / 0 |
| `extensions/data/collectors/derivatives.py` | 705 | 15 / 13 |
| `extensions/data/collectors/hyperliquid.py` | 277 | 5 / 3 |
| `extensions/data/collectors/macro.py` | 364 | 8 / 6 |
| `extensions/data/collectors/microstructure.py` | 207 | 5 / 2 |
| `extensions/data/collectors/options_structure.py` | 331 | 5 / 2 |
| `extensions/data/collectors/vergex_structure.py` | 350 | 1 / 1 |
| `extensions/data/collectors/volume_scanner.py` | 123 | 3 / 1 |
| `extensions/data/news_analyzer.py` | 277 | 5 / 1 |
| `extensions/data/news_collector.py` | 226 | 3 / 1 |
| `extensions/data/news_routes.py` | 140 | 2 / 0 |
| `extensions/data/options_volatility.py` | 173 | 1 / 0 |
| `extensions/data/perp_structure_shadow.py` | 355 | 3 / 0 |
| `extensions/data/polymarket_provider.py` | 177 | 3 / 1 |
| `extensions/data/position_x.py` | 1231 | 5 / 0 |
| `extensions/data/positioning_evidence.py` | 214 | 1 / 0 |
| `extensions/data/public_pricing.py` | 13 | 0 / 0 |
| `extensions/data/sources/__init__.py` | 1 | 0 / 0 |
| `extensions/data/sources/binance_public.py` | 175 | 2 / 2 |
| `extensions/data/sources/chain.py` | 212 | 6 / 5 |
| `extensions/data/sources/coingecko.py` | 115 | 0 / 0 |
| `extensions/data/yfinance_provider.py` | 87 | 4 / 3 |
