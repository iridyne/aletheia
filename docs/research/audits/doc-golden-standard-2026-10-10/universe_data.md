# 文档审查：Universe + 数据域

审查人：universe_data 子代理；工作树 `docs-spec-cleanup`（只读）；日期 2026-10-10。
证据均来自本次 grep/Read，路径相对仓库根。

## universe_twin_shadow_contract.md（182 行）

**角色**：自称 Contract（"Current behavior as of 2026-08-09"），但描述的生产写入路径已不存在 → 实为历史文档。

**真相核查（逐条 grep）**
- 合同 §1 L18-24 / §6 L148 称 "authority publication 之后，独立非致命 Twin worker 每 55 分钟写一批 cluster"。
  - `build_prospective_twin_shadow_bundle`（`extensions/analytics/twin_experiment.py:148`）在 core/extensions/ops/scripts 中**零生产调用方**，只有 `tests/test_universe_twin_experiment.py:26,167` 调用。
  - `git log -S` 显示该调用于 `578c9abe0`（#900，2026-08-11 "consolidate observation workloads"）从 `universe/service.py` 中删除（删了 3 处 twin + 3 处 relative-rank 调用）。
- 合同 L5/L15 "Authority scorer: `universe-step-v2`，Only step-v2 publishes"：错。现役 scorer 版本 `universe-step-v3`（`core/aletheia/universe/scanners.py:22`），发布权威是 `candidate_selection_utility`，loader 强制迁移（`core/aletheia/config/loader.py:309-325`）。
- 表 `universe_twin_shadow_batches/records` 存在（`core/aletheia/storage/migrations.py:5060`），writer `insert_universe_twin_shadow_bundle`（`core/aletheia/storage/repositories_universe.py:509`）存在且**在生产调用**——但调用方是 Candidate Selection Utility 发布单元（`core/aletheia/universe/service.py:892,1091`，`experiment_id="candidate-selection-utility-shadow:…"`，`core/aletheia/universe/candidate_selection_shadow.py:317`），不是 Twin 实验。即：表被"借壳"复用为 CSU 的权威证据表，`min_interval_sec=0`（每次刷新都写，不是 55 分钟）。
- `evaluate_twin_shadow_epoch`（`extensions/analytics/twin_evaluation.py`）仍被只读审计调用：`core/aletheia/analytics/alpha_attribution.py:332`，经 `cli/alpha.py`、`server/routes/analytics.py:911`、`diagnostics/doctor.py:169` 暴露 `universe_twin_evidence`。
- 保留期：`scheduler.py:3189 prune_universe_twin_shadow_evidence` 存在。
- `candidate_selection_utility_contract.md:21-27` 已正确写明 "Historical Twin evaluation still verifies persisted records by replaying…"——与本合同直接矛盾。
- `docs/design/architecture/SYSTEM_MAP.md:65` 仍写 "每55分钟至多另写一批前瞻 twin… step-v1 仍是唯一权威"——三重过时（55 分钟、step-v1、Twin 写入）。

**具体性**：内部写得很细（cadence、hash、ESS、blocker），但对象是已无生产触发的路径；"current behavior" 不成立。

**有道理吗**：step vs continuous 的连续打分实验是量化选因子研究，与 owner 目标（AI 像人类交易员、不挖因子）不在主线；spec 0001 非目标里明确 "Universe family calibration and filter shadow experiments are outside this loop; their future is decided separately"。实验本身 2026-08-11 就停写了，且合同自承永远不能产生正向/晋级结论（§5 L131-144）。没有存在价值，只剩"历史记录可读"的义务。

**仍活着、必须有归属的部分**：表 schema（迁移冻结，不能删）、`twin_persistence.py`（`TwinShadowBundle` 被 CSU 复用）、`twin_scoring.py`/`twin_allocation.py`（被 persistence 和 relative_rank 引用，用于历史 replay）、retention 剪枝、alpha_attribution 只读评估。

**Verdict：ARCHIVE**（移到 `docs/design/history/universe_twin_shadow.md`），活的部分并入 `candidate_selection_utility_contract.md`。

**具体编辑**
1. 整份移到 `docs/design/history/`，顶部加一行："Retired 2026-08-11 (#900): the prospective Twin writer was removed from `UniverseService.refresh()`; no production caller of `build_prospective_twin_shadow_bundle` remains. Historical rows remain readable."
2. 在 `candidate_selection_utility_contract.md` §（L113 附近 "reuses universe_twin_shadow_batches…"）补一条 EARS：`When a Candidate publication unit commits, the Universe service shall write exactly one universe_twin_shadow_batches row and its records inside the same transaction, with experiment_id prefixed candidate-selection-utility-shadow.` 并写明 Twin 表名是历史名、语义归 CSU 所有。
3. `contracts/README.md:108-110` 删除 "owns the current non-authoritative continuous-score evidence" 句，改为指向 history。
4. `SYSTEM_MAP.md:65` 的 `universe_refresh` 行：删"每55分钟至多另写一批前瞻 Twin…"及"step-v1 仍是唯一权威"，改为 "Candidate Selection Utility 是唯一发布权威；每个发布单元同事务写 CSU 证据（复用 universe_twin_shadow_* 表）"。
5. 后续代码清理（不在文档范围，记为建议）：`extensions/analytics/twin_experiment.py` 的 builder/assign 函数与对应测试可删；`twin_evaluation.py` 是否保留取决于 alpha audit 是否还需要历史 Twin epoch 读数。

## universe_relative_rank_shadow_contract.md（62 行）

**角色**：自称 "current for universe-relative-rank-shadow-v1" Contract；实际无生产写入，属历史。

**真相核查**
- 表 `universe_relative_rank_shadow_batches`（`migrations.py:5938`）存在；**全仓 core/extensions 无任何 INSERT**——只有 retention DELETE（`repositories_universe.py:1880`）和 SELECT（`relative_rank_evaluation.py:55`、`alpha_attribution.py:398,414`）。
- `build_prospective_relative_rank_bundle`（`extensions/analytics/relative_rank_experiment.py:118`）只被 `tests/test_universe_relative_rank_shadow.py:89,171` 调用；`build_relative_rank_shadow_bundle`（`core/aletheia/universe/relative_rank_persistence.py:73`）只被前者调用。同样在 #900 从 service.py 删除。
- `candidate_selection_utility_contract.md:24-26` 已写 "Relative-Rank builders remain for historical fixture reconstruction and reader verification, with no refresh/scheduler caller"——与本合同 "Status: current" 矛盾。
- L7 "universe-step-v2 remains the only authority"：错（同上，现为 CSU / step-v3）。
- L24-25 要求 Perp 记录具备 "Binance global long/short account ratio, Binance top-trader ratio"：Binance 已冻结，现役是 Bitget；`top_trader_position_ratio` 列仍存在（`migrations.py:5934-5935`），但这是写给冻结 venue 的输入要求。
- L45 "spot/perp dedupe"：spot 出范围（owner 2026-10-09）。

**有道理吗**：又一个量化排序因子实验，无生产写入、无晋级路径（L56-59 自承需另立 Paper 合同），依赖 Binance 公开数据。与 owner 目标不符，spec 0001 已把 filter/Universe 校准实验划出学习回路。无存在价值。

**Verdict：ARCHIVE**（并入 Twin 的同一份 history 文件或单独 `history/universe_relative_rank_shadow.md`）。

**具体编辑**
1. 移到 `docs/design/history/`，顶部加："Retired 2026-08-11 (#900); no production writer; `universe_relative_rank_shadow_batches` has no INSERT path. Readers remain for historical rows."
2. `docs/research/universe-scoring/relative-rank-candidate/README.md:22-24` 与 `docs/research/universe-scoring/README.md:31` 的 "current behavior is owned by the runtime contract" 改为指向 history。
3. 代码建议：`relative_rank_experiment.py`、`relative_rank_persistence.py`、`relative_rank_shadow.py` 及其测试（`tests/test_universe_relative_rank_shadow.py`、`tests/specs/spec_data_utilization.py:221,298`）可删；`alpha_attribution` 的 relative-rank evidence 段可删或固定返回 `not available`；表因迁移冻结保留。`baseline_integrity.py:201,205` 的 `relative_rank_*` 字段一并清理。

## universe-baseline-integrity.md（37 行）

**角色**：Contract 头，但内容是 #2556 Discovery Benchmark 的"测量前置冻结"约束 + 研究问题清单（L10、L25-27 "remains a benchmark question"）——混合了 Contract 与研究 Proposal。

**真相核查**
- `core/aletheia/universe/baseline_integrity.py`（227 行）、`tests/test_universe_baseline_integrity.py` 存在；`basis/liquidation/cross_venue_positioning` 缺口声明（baseline_integrity.py:71-73）、`Market Shock / Event Proxy`（:77）存在，与 L23-24 一致。
- L8 "current `universe-step-v3` authority"、L14 "production scanner_family … allocator score … remain unchanged"：**过时**。`run_all_scanners`（step-v3 六族打分）在生产代码中唯一调用点是 `core/aletheia/universe/service.py:529-533` 的 `_capture_universe_discovery_nonfatal`，注释即 "Frozen #2556 research population; never an input to current selection"。现役发布权威是 Candidate Selection Utility（`config/loader.py:309-325`；`candidate_selection_utility_contract.md:17` "Historical six-family and total_score records remain immutable audit evidence, not a runtime fallback"）。
- L36 "no production behavior path imports the adapter"：字面上 `discovery_benchmark.py:29` import 了 baseline_integrity，而 `service.py:527` 在每次刷新调用它（非致命观察）。需要改成"no selection/publication path"。
- L25 "`relative-rank-shadow-v1` remains a challenger"：该挑战者自 2026-08-11 无生产写入（见上节）。
- `discovery_benchmark.py`（1254 行）由 `cli/evidence_cmd.py:67-119` start/status/abort/finalize，`extensions/observation_lab/runner.py:354` 注册观察 handler——**无对应 Contract**（`docs/design/contracts` 无 benchmark 文件；只有本文一句 "Before the Universe Discovery Benchmark"）。

**具体性**：Acceptance 是可检查的；但 L10、L25-27 是研究立场声明，不是行为要求。头部 "Baseline branch point: master@a48c4f…" 与 "Parent authority: #2556" 是 issue/提交号当内容。

**有道理吗**：六族 step 打分已不是选择权威，这份文档冻结的是一个已退役打分器的"测量视图"。Benchmark 是研究功能（比较 Universe 是否胜过 Momentum 基线），owner 目标下属可选 Research，不在交易主线。

**Verdict：ARCHIVE**（连同 Discovery Benchmark 一起判定）。如 owner 仍要保留 Benchmark CLI，则改为 **MERGE** 进一份新的 `universe_discovery_benchmark_contract.md`（研究/可选面），而不是独立 Contract。

**具体编辑**
1. 若归档：移至 `docs/design/history/universe_baseline_integrity.md`，顶部注明 "step-v3 six-family scoring is no longer the Candidate selection authority; it survives only as the frozen #2556 research population (`service.py:_capture_universe_discovery_nonfatal`)."
2. 若保留：删 L3-4（issue/commit 头）、L10、L25-27（研究问题移到 research/）；L8 改为 "freeze a lossless measurement view of the retired `universe-step-v3` family scorer as a research population"; L14 删 "production … allocator score … Candidate admission" 一句；L36 改为 "no Candidate selection or publication path reads the adapter's output"。
3. 代码建议：确认 owner 是否还跑 Benchmark；不跑则 `discovery_benchmark.py`、`baseline_integrity.py`、`service.py:515-540` 的每次刷新重算六族打分可整体删（这是每 10 分钟一次的无主计算）。

## derivatives_economic_semantics.md（46 行）

**角色**：Contract（当前行为），单一角色，基本合格。

**真相核查**
- `core/aletheia/data/derivatives_facts.py` 存在；`annualize_funding` 公式 `rate*(365*86400/interval)` 且 interval 未知→None（:179-181），与 L20-22 一致。
- `open_interest_usd`（:184）、`normalize_open_interest`（:206）存在；`tests/test_derivatives_semantics.py` 存在。
- 45-75 分钟 OI 基线窗口（L23-25）：未在 derivatives_facts.py 中 grep 到字面常量（`2700/4500/45 * 60` 无命中），需在 features.py 或 repo 中再定位；本次未证实。
- 文件中还有 `hyperliquid_metadata`（:394）、`project_liquidation`（:227）——合同未提 Hyperliquid 比较源；L32 "Comparison observations remain observation-only" 隐含覆盖，但没点名来源。

**具体性**：单位/年化/未知→None 写得清楚可测。缺 entry point（哪个 collector/job 调用）、durable state（哪些列/表，`funding_rate_annualized` 的列名）、consumer 列表。

**有道理吗**：资金费率/OI 是永续合约主线 Universe 特征的必要输入，正确单位语义是 justified 支撑功能。

**Bloat**：L3-6 "#3067/#3065/#2890 golden-equivalence boundary is historical" 是变更叙事；L35-41 "Behavior delta" 一节是 PR 说明（"previously receiving … now receives"），不是当前行为；L45-46 Fedora acceptance 是进度/验证义务。

**Verdict：REWRITE（小）**。

**具体编辑**
1. 删 L3-6 的 issue 叙事，改为一句职责声明 "This contract owns the unit, annualization and OI-baseline semantics of every current Funding/OI reader."
2. 删 "Behavior delta and verification" 节 L37-41、L45-46，只留 "Verification: `tests/test_derivatives_semantics.py`"。
3. 把 L15-33 的 bullet 改为 EARS：如 `If the funding settlement interval is unproved, then annualize_funding shall return None.`；`If no same-provider/venue/instrument/unit baseline exists 45–75 min earlier, then the OI change shall be unavailable.`
4. 补 "Entry points / state" 小节：列出调用方（collector、`universe/features.py` 特征采集）与存储列；点名比较源（hyperliquid 等）为 observation-only。
5. L28-29 "Schema v82 only relaxes…" 是迁移历史，移到 data_model 迁移注记或删。

## market_structure_contract.md（107 行）

**角色**：Contract，单一角色；结构（Purpose/Entry points/Durable state/Observable/Invariants/Failure/Evidence/Known gaps）是本域最好的模板。

**真相核查**
- `MarketStructureProvider`（`core/aletheia/data/market_structure.py:533`）、`derive_market_structure`（:319）、`derive_fvg_zones`（`core/aletheia/data/smc_enrichment.py:20`）、`evaluate_market_structure`（`core/aletheia/universe/market_structure.py:50`）全部存在；三份测试文件存在。
- Fast Trigger 消费：实际调用点是 `core/aletheia/trading/momentum_trigger.py:321-323`，由 `agent_runtime/fast_trigger_watch.py:16` 引入，`server/app.py:59,79` 在 lifespan 启动——属实。
- **漏掉主线消费者**：L29 "Fast Trigger remains the current production consumer" 不完整。Entry Planner 主线经 `core/aletheia/trading/entry_analysis.py:480-560 entry_causal_windows()` 直接调用 `derive_market_structure` + `canonical_market_structure_facts`，对 15m/1h/4h 窗口产出 swing / broken-structure 锚点；`entry-plan-contract.md:145-169,299` 用这些锚点决定 Compiler 的止损/失效位，且 `entry_analysis.py:432` 在结构不可用时抛 `hypothesis_profile_market_structure_unavailable`。
- 因此 L7-9 "observation authority only. It does not authorize Candidate admission, Trigger thresholds, Risk…" 字面没错（它不批准），但 "observation only" 低估了它：结构事实是 Compiler 几何的**输入**，结构不可用会让 Entry Plan 失败。这对交易主线是承重的。

**具体性**：好。K=2、ATR14 算术均值而非 Wilder、0.05×ATR14 边界、30-119 bar partial / 120 complete、unavailable≠empty 都可测。

**有道理吗**：Swing/BOS/CHOCH 是人类主观交易员的核心读图工具，符合 "AI 像人一样交易"；主线必需。FVG 是可选富化，无主线消费者（L73-74 自述只在 query 时请求）——可疑但代价小。

**Verdict：KEEP（补一节）**。

**具体编辑**
1. "Durable state and consumers" 节 L29 前加一段：`The Entry Planner context (`trading/entry_analysis.entry_causal_windows`) is the mainline consumer: for each admitted 15m/1h/4h window it derives confirmed Swings and BOS/CHOCH facts that the deterministic Compiler uses as invalidation anchors (entry-plan-contract). If a required window's structure is unavailable, then the hypothesis profile shall fail with hypothesis_profile_market_structure_unavailable rather than compile without anchors.`
2. L7-9 改为 "It grants no approval authority; its facts are inputs to Entry Plan geometry and to the Shadow Fast Trigger assessment."
3. Evidence 节补 Entry Planner 侧的测试文件（需 grep 定位 `entry_causal_windows` 测试）。
4. FVG：若无消费者计划，在 Known gaps 注明 "FVG has no production consumer"，或删 FVG（属 YAGNI，需 owner 决定）。

## market_data_coverage_contract.md（89 行）

**角色**：Contract，单一角色，结构完整。

**真相核查**
- `GET /market/collector-health`（`core/aletheia/server/routes/data.py:1910`）、`GET /market/data-coverage`（:1919）存在；`collector_health_state` 表（`migrations.py:2824`）存在；`core/aletheia/data/data_coverage.py` 存在；7 个测试文件全部存在（ls 无缺失）。
- 覆盖域（L21-22）与 `data_coverage.py:51-86` 一致：long_short_ratio、liquidations（`liquidation_events`）、macro（`macro_snapshots`）、onchain（`onchain_snapshots`）。
- `migrations.py:8354` 已加 `derivatives_coverage_json` 列，合同未提——小缺口。
- 合同未说"primary"派生源是哪个 venue；`data_coverage.py` 用 `_PRIMARY_DERIVATIVE_WHERE`。Memory 记录过 "primary=OKX" 的历史问题；Bitget-only 现状下应写明 primary 跟随 active exchange 或配置键。本次未进一步核实 `_PRIMARY_DERIVATIVE_WHERE` 的取值来源。

**具体性**：状态枚举（fresh/stale/empty/invalid/idle；healthy/attention/empty）清楚，unavailable vs empty 区分到位。

**有道理吗**：Operator 诊断支撑功能，justified。但 macro/onchain/liquidations 是否仍有采集器与主线消费者值得追问——若 Entry Planner 不读 macro/onchain，这两域的 coverage 只是在报告无主数据（本次未追，标为待核）。

**Bloat**：L50-57 三条 ticker/MarketSnapshot 校验不变量属 provider 合同（ohlcv_provider_result / provider 层），在此是复制的事实。

**Verdict：KEEP（小改）**。

**具体编辑**
1. L50-57 三条 ticker/batch-ranking/MarketSnapshot 不变量移到 provider 合同（或 `ohlcv_provider_result_contract.md` 扩展为 provider result 合同），此处留一句链接；Evidence 节 L78-80 相应移走。
2. Durable state 补 `derivatives_coverage_json` 列的含义。
3. 写明 "primary funding/OI" 的 venue 由谁决定（active exchange extension），避免复燃 OKX primary 的历史 bug。
4. 若 macro/onchain 无主线消费者，在 coverage 中标为 `optional` 而非 decision-critical（需先核采集器）。

## ohlcv_provider_result_contract.md（96 行）

**角色**：Contract，单一角色，结构好。

**真相核查**
- `parse_ohlcv_candle`（`core/aletheia/data/providers.py:292`）、`CCXTProvider`（`extensions/data/ccxt_provider.py:189`）、`FallbackChainProvider`（`extensions/data/sources/chain.py:51`）、`BinancePublicProvider`（`extensions/data/sources/binance_public.py:50`）、`CoinGeckoProvider`（`extensions/data/sources/coingecko.py:58`）都存在；3 份测试与 `docs/design/proposals/ohlcv_provider_observation_result.md` 存在。
- 默认装配仍是 `FallbackChainProvider([ccxt, CoinGeckoProvider(), BinancePublicProvider()])`（`ccxt_provider.py:959`），`enable_crypto_fallback: bool = True`（`config/schema.py:424`）。
- L21 "`BinancePublicProvider` is the supported spot fallback path"、L36 "spot-only CoinGecko/Binance fallback path"：**与产品真相冲突**——spot 出范围、Binance 冻结；而合同 L58 已规定 "Derivative requests never fall back to spot-only providers"。也就是说在 perp-only 产品里这两个 fallback 永远不该被命中，但仍默认装配。
- L35 "Protective Barrier use native CCXT"：`core/aletheia/portfolio/protective_barrier.py` **不存在**（随本地模拟器退役；全仓仅 `extensions/learning/registry.py` 残留字符串）。
- `ccxt_fallback: str = "bybit"`（`schema.py:353`）：CCXT "exact-venue fallback" 默认指向 Bybit，与 "Bitget 单一 venue" 冲突；注释 L348 仍写 "ccxt uses CCXT Binance/Bybit"。合同未提 `ccxt_fallback`。

**具体性**：好（timeframe 不静默变化、缺 volume 不补 0、empty/unsupported/failed 三分）。

**有道理吗**：OHLCV 结果语义是主线必需（Entry Planner 15m/1h/4h 窗口、结算）。CoinGecko/Binance spot fallback 在 perp-only 产品里没有用途，是化石。

**Verdict：REWRITE（小）**——收窄到 CCXT（active Bitget venue）一条路径。

**具体编辑**
1. L11-14、L21-24、L45-48 删 Binance Public / CoinGecko 作为"supported"的叙述；改为 "The current product requests only USDT perpetuals; spot-only providers are not part of the supported OHLCV path." 并建议代码侧把 `enable_crypto_fallback` 默认改 False 或删除这两个 provider（D0/D1 由 owner 定）。
2. L35-36 删 "Protective Barrier"；"History Downloader" 保留（`core/aletheia/data/history_downloader.py` 存在）。
3. 增一条：`If the exact-venue CCXT fallback (`market_data.ccxt_fallback`) names a venue other than the active exchange, then results shall carry that venue's identity and shall not be persisted under the active venue's series.`（并核 `ccxt_fallback="bybit"` 是否应删除）。
4. L95-96 history 指针保留即可。

## technical_analysis_contract.md（104 行）

**角色**：Contract，但混了两件事：TA 指标计算（主线）+ OHLCV 冷热分层（`tiering`，无生产调用）。

**真相核查**
- `BatchTechnicalAnalyzer`（`core/aletheia/trading/technical_analysis.py:217`）、`IncrementalTechnicalAnalyzer`（:253）、`TechnicalSnapshotService`（`core/aletheia/trading/technical_snapshot_service.py:42`）存在；Universe 特征采集调用 `snapshot_from_bars` 于 `core/aletheia/universe/features.py:705-713`（15m/1h/4h）——属实。
- **Market Observer 已退役**（`docs/design/proposals/retire_market_observer.md` 存在；`agent_shell/registry.py:239` 注释）。合同 L17-18、L35-45、L57-59 把 Market Observer 写成当前消费者，并描述 `technical-context-v1` block。`core/aletheia/trading/market_context.py:332 build_market_context` 在 core/extensions/ops **零调用方**（只剩 `extensions/data/yfinance_provider.py:7` 注释提及）——这是死代码。
- L43-45 "System Operator consumes the persisted feature row and includes technical version/input identity in its material decision fingerprint"：现主线是 Entry Planner；`core/aletheia/trading/entry_analysis.py:1074-1076` 确实带 `technical_algorithm_version` + `technical_snapshot_manifest_hash`，语义仍在，但主体名称过时。
- Tiering：`repositories.plan_market_ohlcv_tiering`（`repositories.py:6303`）与 `OHLCVTierManager`（`core/aletheia/storage/market_ohlcv_tiering.py:108`）存在，但 `plan_market_ohlcv_tiering` **无任何调用方**；`ColdOHLCVBackend` 只有 Protocol（:69）无实现。L101-104 自承 "owner deferred"。这是投机性抽象（为未来冷存储预留），按 owner 不过度工程化原则应删或冻结。
- 6 个 Evidence 文件全部存在。

**具体性**：指标与不变量写得可测（forming bar 不改、bar_close_ts=open+tf、重复/修订触发 bounded rebuild）。

**有道理吗**：TA 指标是人类交易员读图输入，主线必需（KEEP）。Market Observer 渲染与 tiering 都不在主线。

**Verdict：SPLIT**——TA 部分 REWRITE 保留；tiering 部分移出（ARCHIVE 或若 owner 想保留则单独小合同）；Market Observer 段落删除。

**具体编辑**
1. 标题改为 "Technical Analysis Contract"；删 L9-11、L19-22、L75-78、L87-88、L94、L99-104 的 tiering 内容，迁至 `docs/design/history/ohlcv_tiering.md`（并建议代码侧删 `plan_market_ohlcv_tiering`/`OHLCVTierManager` 与 `tests/test_market_ohlcv_tiering.py`；注意 `analytics/position_replay.py:17` 复用了其中 `_canonical_row/_content_hash`，删前要把这两个函数挪走）。
2. L17-18 改为 "`TechnicalSnapshotService.snapshot_from_bars()` is the process-wide reuse boundary for Universe feature collection (`universe/features.py`)."
3. 删 L35-42、L57-59 Market Observer / `technical-context-v1` 段；建议代码侧删 `trading/market_context.py` 的无调用渲染路径（需先确认 `tests/test_market_context.py` 只测这条死路径）。
4. L43-45 "System Operator" 改为 "The Entry Planner context (`trading/entry_analysis.py`) carries `technical_algorithm_version` and the manifest hash into the Planning material fingerprint."
5. L73-74 "Operator material decision fingerprint" 同步改为 Planning material fingerprint。

## data_model.md（724 行；审阅前 200 行 + 目录 + §3G/§3I-K/§6）

**角色**：自称 "Data Model Reference"（Operations/Reference 类），实际混合：表索引（reference）+ 行为规则（如 L80-100 opportunities workset、market-aware wake 的 fail-closed 规则，属 Contract）+ 迁移历史（§6 第 9-17 条逐版本叙述）+ Learning 测量语义（§C-bis/C-ter，属 learning_contract）。

**真相核查（10 张表 writer 抽查）**
- 有 writer：`ps_lessons`（`extensions/learning/lessons.py`）、`coingecko_top_snapshots`（`repositories.py:5663`）、`perp_structure_snapshots`/`_shadow_outcomes`（`repositories.py:5630,5635`）、`prediction_markets`（`repositories_market_data.py:221`，由 `extensions/research/prediction_observer.py:123`；`main.py:168-178` 仍注册 `prediction_observer` agent）、`pi_decisions`（`repositories.py:845`，`agent_shell/commands.py:1737,2124`）、`market_regimes`（`repositories.py:6267`，**实际调用方是 `universe/service.py:660`**，不是文档 L92 写的 `market_observer`）。
- 无 writer：`universe_policies`（G 节 L276 "Universe selection rules"，core/extensions 零引用）、`ps_order_intents`（只剩 `extensions/sync/exporter.py:254` 导出名单），`strategy_records`（文档已标 legacy read-only，属实）。
- L5 "Source of truth: `aletheia/storage/migrations.py`"：路径应为 `core/aletheia/storage/migrations.py`。L6 "Last updated: 2026-09-10" 是进度戳。
- L8 "#2737 … Before Phase 8 … Before Phase 11"：程序进度叙述。
- §C-bis L132-141：把 `universe_family_calibration`、`ranker_case_score`、"Operator, factor, ranker-memo, learned-guard, automatic Risk-lesson, Observer-bundle" 写成当前 measurement 路径/consumer——Ranker 与 Python Operator、Market Observer 均已退役；与 spec 0001（Playbook 取代 ps_lessons/guards/memos）冲突。
- §G L278-279 `universe_twin_shadow_*` 描述为 "Candidate Selection Utility samples … at a 4h cadence"——与代码一致（CSU 复用表），但与 `universe_twin_shadow_contract.md` 矛盾；§6 第 16 条 "Schema v13 … preserves step-v1 authority" 过时。
- §I "PS (Production System) Layer (14 tables)" 标题说 14 张，表格列 15 行；未标 legacy/retired。§J Prediction Markets 未标出范围（产品真相：prediction markets outside instrument scope）。
- §K L366 `signal_source ∈ {'market_observer','serenity'}`、L368 "propagated through `paper_tools` → `gateway.submit_proposal` → all four brokers (paper/bitget/binance/okx)"：paper broker 已退役、OKX/Binance 冻结、生产入口是 `execute_existing_proposal`；整段描述的是退役路径。
- §D L186 `open_interest_snapshots.long_short_ratio` "Binance globalLongShortAccountRatio"：Binance 冻结；需要写明 Bitget 下该列来源或 unavailable。

**具体性**：表索引本身有用；行为规则散落其中无 EARS、无失败语义归属。

**有道理吗**：一份表索引是 justified 支撑文档（AI 找表用），但不应拥有行为。目前 724 行里大量是别处合同的复制。

**Verdict：REWRITE**（降级为纯 Reference：表名 | 用途 | writer | 状态 current/legacy-read-only/retired），行为规则回归各自 Contract，迁移逐版本叙述删除（ledger 本身即真相）。

**具体编辑**
1. 删 L6 "Last updated"、L8 #2737 Phase 叙述；L5 路径改 `core/aletheia/storage/`。
2. 每张表加一列 `Status`（current / legacy read-only / retired-no-writer）；按本次 grep：`universe_policies`、`ps_*`（除 `ps_lessons`）、`strategy_*`、`backtest_*` 标 retired-no-writer；§J prediction_* 标 "out of product scope; optional research writer `extensions/research/prediction_observer.py`"。
3. `market_regimes` writer 改为 `universe/service.py`（refresh 内）。
4. L80-100 三段（opportunities workset、market-observer queued 5、market-aware wake）移到 `opportunity_outcome_contract.md`/`decision_lifecycle_contract.md`；"Queued market-observer work is admission-bounded to 5" 一句随 Market Observer 退役删除（需核 `opportunities` 生产写入方是否仍存在，memory 记录其生命周期为化石）。
5. §C-bis L132-141、§C-ter 全部替换为一行链接到 `learning_contract.md` 与 spec 0001；不在此复述 measurement 路径。
6. §K L366-368 的 `signal_source`/`trade_school` 注记改为只描述列与取值，删 "paper_tools → … four brokers" 传播链。
7. §6 第 9-17 条（v3/v6/v9-v13/v20 逐版本）删除，保留规则 1-8 和末段 bounded-read 规则。
8. §G twin 两行补 "physical name is historical; owned by Candidate Selection Utility"。

## data_layer.md（211 行；审阅前 150 行 + 余下标题）

**角色**：Contract（"current market-data boundary"），单一角色，L209-211 自声明无阶段。整体是本域第二好的文档。

**真相核查**
- `get_market_data_provider`（`core/aletheia/data/providers.py:61`）、`create_market_data_provider`（`extensions/data/ccxt_provider.py:907`，由 `main.py:252`、`server/app.py:1337`、`data/collector_runner.py:29` 注册）属实。
- `extensions/data/collectors/` 下 `volume_scanner.py`、`microstructure.py`、`derivatives.py`、`options_structure.py`、`coinalyze.py`、`macro.py` 存在；`extensions/data/options_volatility.py`、`core/aletheia/data/options_volatility.py` 存在；scheduler 在 `scheduler.py:4518,4524` 组装 Coinalyze/Options 采集器——属实。
- Mermaid L20-25 与 L37 把 CoinGecko/Binance public fallback 画成现役链（与代码一致：`ccxt_provider.py:959`），但与 spot 出范围、Binance 冻结冲突（同 OHLCV 节）。
- L28 / L166-170 "Optional macro / equity context → YFinanceProvider … supported equity/macro contexts"：`extensions/data/yfinance_provider.py` 存在，`main.py:223`/`server/app.py:1308` 调用 `fetch_macro_context`；"equity contexts" 与 "ordinary equities outside instrument scope" 冲突；L170 "stock-token deviation" 对应已冻结的 stock-linked profile。
- L77 "physical market type (`spot`, `perp`, etc.)"、L80 "A same-symbol Spot and Perp are not interchangeable"：作为不变量无害，但现在应写 "spot is never admitted"。
- 未列出的采集器：`hyperliquid.py`、`vergex_structure.py`、`defillama.py`、`coingecko_top.py`——文档 L39 自承 "not a complete inventory"。这些辅助源（macro/onchain/options/Coinalyze/Hyperliquid/Vergex）在 `trading/entry_analysis.py`/`agent_shell/entry_planner.py` 中 grep 不到；`data/context_builder.py` 读 macro/onchain/options，但其唯一生产调用方 `trading/entry_snapshot.py:186-195` 只取 `data_completeness/tradability/leverage`。是否有主线消费者**未证实**——是潜在的大块无主采集。

**具体性**：失败语义（L172-180）与 identity 规则清楚；"Current consumers"（L152-164）用 "may include" 模糊。

**有道理吗**：public/private 边界、loop-bound CCXT 生命周期是承重规则，KEEP。辅助数据源的存在价值需单独审（不在本文档能决定）。

**Verdict：KEEP（小改）**。

**具体编辑**
1. Mermaid 与 L37：把 CoinGecko/Binance 标为 "frozen spot-only fallback; never used for perp requests"，或在代码收敛后删掉这两个节点。
2. L166-170 yFinance 节：删 "equity" 与 "stock-token deviation"，只保留 "bounded macro context"。
3. L152-154 "may include" 改为确定列表：Universe feature collection（`universe/features.py`）、Entry Planner causal windows（`trading/entry_analysis.py`）、outcome settler、chart/data routes；与 SYSTEM_MAP 对齐。
4. L77-80 加一句 "The product admits only `perp`; spot identities are historical."
5. 在 L39 后补一句指向 `market_data_coverage_contract.md`，并列出全部 `extensions/data/collectors/*` 及各自 consumer（或标 "no mainline consumer"）。

## 跨文档（cross-doc）

**矛盾**
- Twin/Relative-Rank 两份合同称 "current"，而 `candidate_selection_utility_contract.md:21-27` 已写明二者无 refresh/scheduler 调用方、只剩历史 replay——同一事实三处说法（另两处：`SYSTEM_MAP.md:65` 说 "每55分钟写 Twin、step-v1 唯一权威"；`data_model.md` §6-16 说 "preserves step-v1 authority"）。权威版本数也不一致：step-v1（SYSTEM_MAP / data_model）、step-v2（twin、relative-rank 合同）、step-v3（baseline-integrity、`scanners.py:22`）；实际发布权威是 CSU（`config/loader.py:309-325`），step-v3 只在 #2556 研究人口中运行（`service.py:529-533`）。
- `market_structure_contract.md` 称 Fast Trigger 是生产消费者、仅 observation 权威；`entry-plan-contract.md:145-169,299` 让 Compiler 用结构锚点定止损——承重消费者没在结构合同里出现。
- `technical_analysis_contract.md` 把已退役 Market Observer 写成当前消费者；`retire_market_observer.md` 已存在。`data_model.md` §A L92、L86-89、§K L366 同样残留 market_observer。
- OHLCV / data_layer 仍把 Binance Public、CoinGecko spot fallback 写成 supported，与 CAPABILITY_GROUND_TRUTH 的 spot-out / Binance-frozen 冲突；`config/schema.py:353 ccxt_fallback="bybit"` 与单一 Bitget venue 冲突，文档未提。

**重复**
- ticker/MarketSnapshot 有限值校验同时出现在 `market_data_coverage_contract.md:50-57` 与 `data_layer.md:59-68`、`ohlcv_provider_result_contract.md:50-59`。应只由 provider 合同拥有。
- `data_model.md` §C-bis/C-ter 复述 learning_contract 的测量语义。

**缺失合同（有代码无合同）**
- Universe Discovery Benchmark：`core/aletheia/universe/discovery_benchmark.py`（1254 行）+ `cli/evidence_cmd.py:67-119` + `extensions/observation_lab/runner.py:354`，每次 refresh 都非致命地重算六族 step-v3 打分（`service.py:515-540`）；只被 `universe-baseline-integrity.md` 间接提及。需要 owner 决定：保留（补合同）或删除。
- Candidate Selection Utility 对 `universe_twin_shadow_*` 表的所有权：代码已是 CSU 唯一 writer（`service.py:892,1091`），但 CSU 合同只在 L113 一句提及 "reuses"，没有写入触发/事务/失败语义。
- 辅助数据采集器（Coinalyze、Hyperliquid、Vergex、DefiLlama、CoinGecko top、options/Deribit、macro/yFinance）：采集与存储有代码，主线消费者未证实；无统一合同说明谁消费。

**与 spec 0001 的冲突**
- `data_model.md:132-141` 把 `ranker_case_score`、ranker-memo、learned-guard、automatic Risk-lesson、Observer-bundle 列为 consumer-wired 当前路径；spec 0001 用单一 Playbook 取代 ps_lessons/guards/memos，Ranker/Observer 已退役。
- Twin 合同 §2 使用 `learning_policy_epochs` 记 Shadow 实验身份；spec 0001 非目标已把 "Universe family calibration and filter shadow experiments" 划出学习回路，"future is decided separately"——归档 Twin/RR 正好兑现这个"另行决定"。
- spec 0001 LL 判定标签需要 Entry Plan 的确定性几何回放；该几何依赖 market structure 锚点（见上），故 `market_structure_contract.md` 的结构事实确定性（K=2、causal known_at）是 LL-R01 的上游前提，应在结构合同中显式写成不变量。

## 总表

| doc | lines | role | verdict | top problem |
|---|---|---|---|---|
| universe_twin_shadow_contract.md | 182 | Contract（实为历史） | ARCHIVE | 生产 writer 2026-08-11（#900）已删，`build_prospective_twin_shadow_bundle` 只剩测试调用；表被 CSU 借壳复用而合同未说 |
| universe_relative_rank_shadow_contract.md | 62 | Contract（实为历史） | ARCHIVE | `universe_relative_rank_shadow_batches` 全仓无 INSERT；依赖 Binance 多空比、spot/perp dedupe |
| universe-baseline-integrity.md | 37 | Contract + 研究问题混合 | ARCHIVE（或 MERGE 进新 Benchmark 合同） | 冻结的 step-v3 已不是选择权威，只在 #2556 研究人口里运行 |
| derivatives_economic_semantics.md | 46 | Contract | REWRITE（小） | 规则对，但混 PR 叙事/行为 delta；缺 entry point、存储列、比较源 |
| market_structure_contract.md | 107 | Contract | KEEP（补一节） | 漏写 Entry Planner/Compiler 这一承重消费者 |
| market_data_coverage_contract.md | 89 | Contract | KEEP（小改） | ticker 校验不变量复制自 provider 层；primary venue 来源未写 |
| ohlcv_provider_result_contract.md | 96 | Contract | REWRITE（小） | Binance/CoinGecko spot fallback 写成 supported；引用已删的 Protective Barrier |
| technical_analysis_contract.md | 104 | Contract（TA + tiering 混合） | SPLIT | Market Observer 写成当前消费者；tiering 无调用方的投机抽象 |
| data/data_model.md | 724 | Reference + 行为 + 迁移史混合 | REWRITE | 退役机制（market_observer、paper/okx/binance broker、ranker）当现役；无 writer 表未标 |
| data/data_layer.md | 211 | Contract | KEEP（小改） | spot fallback 与 yFinance equity 叙述过时；consumers 用 "may include" |
