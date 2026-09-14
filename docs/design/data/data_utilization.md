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
| Positioning Evidence（DU-S32） | 复用 Canonical Derivatives Facts，无新增采集 | 读取既有 Funding/OI/price 表，不新增写表 | `aletheia data positioning-evidence --symbol BTC/USDT --cutoff <UTC> --venue binance --venue okx` | 🟡 observation/research-only；冻结输入与 cutoff 可用于全观察样本的 15m/1h/4h outcome join，不依赖成交筛选 |
| FRED rates/liquidity（DU-S33） | 扩展现有 FRED adapter，仅六个 series；日/周限频 | 独立 `fred_source_batches`，90 天保留 | `aletheia data macro-liquidity --cutoff <UTC>` 只读导出，规则见 [FRED source contract](fred_liquidity.md) | 🟡 observation/research-only；current-known/revised 非历史 vintage；不进入现有 MacroSnapshot、LLM 或交易决策 |
| Deribit IV surface（DU-S31） | `OptionsStructureCollector` 复用同一次 BTC/ETH public chain response | 独立 `options_volatility_snapshots`，30 天保留 | `aletheia data options-volatility --symbol BTC --cutoff <UTC>` 只读研究导出 | 🟡 observation/research-only；不进入 LLM 或交易决策；规则见 §4 |
| Canonical Derivatives Facts（DU-S29） | 现有 `DerivativesCollector` 的 primary/comparison 路径与 native Hyperliquid；一个 exact venue/instrument/fact 保留一个当前 writer | 原 Funding/OI 表的 nullable provenance；独立 `perp_price_snapshots`；`collector_health_state` 的最新比较覆盖 | `data/derivatives_facts.py` 只读规范投影及 `GET /api/market/derivatives-facts` 的 Positioning readiness 数据观察；比较行明确 `observation_only`，旧 scalar/context/evidence readers 排除这些新增行 | 🟡 observation-only；不改变 Candidate、Entry、Risk 或交易权限；未知 interval/单位/历史 provenance 保留 unavailable，不把采集成功当作经济证据 |
| 现货价/OHLCV | CCXTProvider / market_data job | `market_snapshots` | market_observer / paper_trader / trading_decision / price_feed / shadow_trades | ✅ |
| 微观结构/盘口 | MicrostructureCollector | `orderbook_snapshots` | market_observer→tradability→LLM；universe/features 选币；inspect/doctor 诊断 | ✅ CCXT 三列档位与 contractSize 深度已修复；按 venue+symbol+market_type 精确路由；超过 15 分钟整行退出决策 |
| Binance public fast-market quote/trade/Kline/depth（DU-S28） | frozen Extension：`extensions/exchanges/binance/fast_stream.py`（Spot + USD-M；Spot depth 保留 REST snapshot 连续桥接） | 不持久化原始报文；保留的解析器测试仅使用内存状态 | 当前产品启动不注册，无 active Fast Monitoring 消费路径 | frozen，须另行批准才可提升；保留实现不读取凭证/账户流、不进入订单路由；缺 ACK、snapshot、连续性或有效值时 unavailable |
| 历史 K 线 | CCXTProvider / history_downloader / ohlcv_active_backfill | `market_ohlcv` + `ohlcv_backfill_state` | 图表、回测、trade path、shadow/alpha 分析、覆盖诊断 | ✅ 按 venue+symbol+market_type+timeframe+ts 隔离；动态活动成员由独立有界任务补齐，持久化失败退避且不阻塞 Universe 刷新；旧不明 venue 诚实标记 |
| 持仓量 OI(主交易所) | DerivativesCollector | `open_interest_snapshots` | 同上→crowding_score | ✅ 精确 venue；超过 15 分钟连同变化率/LSR 整行退出决策 |
| 衍生品仓位比（DU-S26） | DerivativesCollector (Binance public fapi global account ratio + top-trader position ratio) | `open_interest_snapshots.long_short_ratio/top_trader_position_ratio` → `universe_feature_snapshots` | normalizer→crowding_score→leverage→LLM；Relative Rank Shadow 的 perp 准入和 positioning rank | ✅ 两种 ratio 保持独立语义；缺 funding/OI/OI delta/任一 ratio 的 perp 候选在新 Shadow 中 fail closed，不读取本地私有账户仓位 |
| 宏观(BTC.D/总市值/VIX/DXY/Nasdaq) | MacroCollector (FRED/CoinGecko/yf fallback) | `macro_snapshots` | market_observer / context_builder / regime / shadow_trades / position_enrichment | ✅ |
| 美股宏观代理(S&P500/VIX/DXY/Gold) | extensions/yahoo/market_data (enable_yfinance_macro 门控) | 不落库，直接生成 markdown | market_context.build_market_context → "Macro context (daily)" 块 → LLM | ✅ |
| 恐惧贪婪指数 | MacroCollector (alternative.me) | `macro_snapshots.fng_*` | macro 块→LLM | ✅ |
| 涨跌幅榜 | CoinGeckoTopCollector | `coingecko_top_snapshots` + 折叠 summary | macro 块(top_movers_summary)→LLM | ✅ |
| 成交量排名 | VolumeScannerCollector（精确 venue+market InstrumentId） | 内存 `_latest_volume_ranking` + `volume_ranking_snapshots` | universe/service→按原 market_type 排序已登记资产（不创建新资产） | ✅；单标的不支持不再打断整批 |
| 新闻+情绪 | NewsCollector + independent bounded analyzer | `news_items` | 仅 `analyzed`、relevance≥0.6、exact ticker/asset-name 匹配的行进入 market_observer；research_worker 同门槛消费 | ✅ fetch/analyze 分离；单批3条、单条30s、整体105s；一次失败退避30min、二次失败终止，不伪装 analyzed |
| 官方公告/监管 RSS（SEC/CFTC/Ethereum Foundation） | NewsCollector（默认 news.feeds） | `news_items` | 与新闻质量门相同；Opportunity metadata 保留实际渲染 news id/source/published_at | ✅ |
| **Hyperliquid OI/资金费率/预测** | HyperliquidCollector | `funding/OI` (exchange=hyperliquid*) | **market_observer→跨 venue annualized funding + OI/真实1h OI delta（含 source timestamp）→LLM**；System Operator 决策写入精确 source IDs | ✅；OI 只用同 source 45-75min baseline，缺基线或>300%异常为 unavailable；30 分钟 freshness fail-closed |
| 永续持仓结构/清算墙 | owner-authorized aggregate rows 或脱敏多市场 holder batch（`aletheia data ingest-perp-structure PATH`）+ fixture/offline Position X parser；仅显式按需导入，无自动 API/调度 | holder batch 先计算为带 batch hash/coverage/universe 的白名单聚合行，再写 `perp_structure_snapshots` + `perp_structure_shadow_outcomes`；不保存 holder 原始行/身份 | market_observer / System Operator 只读 advisory；1h/4h/24h shadow settlement；`evaluate-perp-structure` 只读比较 weighted/equal/单因子/零结构 gross baseline；doctor 诚实显示未配置/新鲜度 | 🟡 observation-only（默认至少 3 个 perp 市场、每市场 coverage≥80%、source skew≤300s；评估默认每 horizon 30 个合格样本、14 天、80% coverage，并拒绝回溯导入/混合 epoch 或 feature contract；cost-aware unavailable；当前不参与排序/风控/执行） |
| **DefiLlama 链上(稳定币/TVL/DEX)** | DefiLlamaCollector | `onchain_snapshots` | market_observer→macro 链上块→LLM | ✅ |
| 预测市场 | PolymarketProvider | prediction 表 | 原始 Opportunity observation-only；market_observer 预测块→LLM；派生 divergence 可进入 crypto 决策链，但必须先确定 question polarity | ✅ unknown/negated/mixed polarity fail-closed |
| 美股/xStocks (ON-series, 15 tokens) | equity_observer | equity 表 | **observation-only（不交易，仅交叉验证/溢价监测）** | 🟡 |
| xStock 上市状态（责任交易所 active spot market list） | market_data 失败路径经 `get_market_data_provider(native_ccxt=True)` 查询 | 不落行情表；确认缺失时更新 `observed_stock_tokens`/`observed_xstocks` 并写 audit | 仅维护 observation-only xStock 采集范围 | 🟡；单轮每 venue 一次、10s 硬截止；超时/网络失败/空列表均为 indeterminate，保留配置 |
| **美股 r-series 代币 (113 tokens, Bitget)** | equity_observer (stock token cross-validation) | 不落库，实时抓取作 cross-val | **equity_observer → `_fetch_stock_token_price()` → yfinance 偏差监测 → LLM equity snapshot**；亦注入 Serenity equity_chain lens 上下文 | ✅ |
| 市场状态 regime | MacroCollector 内联风险标签 + `regime/collector.py`/detector | `macro_snapshots.risk_regime` + `market_regimes` | market_observer / emitter / ranker / System Operator | ✅ Universe regime 使用真实约7天 market-cap baseline 与已完成日K true range；缺历史时 unavailable，不回落24h proxy |
| OHLCV 历史 | history_downloader | ohlcv | 回测/指标 | ✅ |
| 清算 liquidations | CoinalyzeCollector（key-guard，data_collector_context；DU-S06/DU-T01） | `liquidation_events` | context_builder→`liquidation_1h_usd`→crowding_score→LLM | ✅ 有 key 时写入 / 无 key 时诚实跳过 |
| LLM 逻辑调用/物理 attempt/context manifest | inference harness + provider tracing | `llm_inference_calls` + `runtime_cost_events` + `llm_context_artifacts` | tokenomics 审查、caller coverage、Learning B1/technical-context paired evaluation | ✅ observation-only；仅身份/版本/哈希/计数，不存 prompt、response 或 source body（DU-S25） |
| LiteLLM 公共模型/原生工具价格目录 | Gateway startup 自动刷新（单飞、5s timeout、校验失败保留旧快照） | 原始 JSON 缓存在 runtime data dir；按实际响应模型名/目录 alias/受限 family suffix 匹配，跨 provider 同模型取中位数；普通 input/output、cache read/cache creation、reasoning 与可测原生工具费率写入 immutable `pricing_snapshots` | LLM attempt 优先记录 provider 明确 USD；否则按 `effective_from <= started_at` 的最新快照确定计价。未返回 cache counter 时全部 input 按普通费率；已返回 counter 时必须使用对应特殊费率。任一可能计费事实缺失时窗口、桶和归因行均为 `null`。本地工具外部边际成本为 0 且不重复 LLM 费用 | ✅ 唯一模型/原生工具价格源；与 runtime provider/API base 无关；无前端或人工价格 writer；不进入 Risk/Execution，不回写历史事件；歧义、缺 usage unit 或特殊费率保持 unavailable（DU-S27） |
| OI 历史（跨所） | CoinalyzeCollector（key-guard，data_collector_context；DU-S06/DU-T01） | `open_interest_snapshots(exchange=coinalyze)` | research_worker 显式聚合流量上下文；doctor | ✅ 与主交易所 OI 分层；1h delta 同样使用同 source 45-75min baseline；coin 数量不得冒充 USD |
| OHLCV 多周期（15m/1h/4h/1d） | CCXTProvider；Universe 为候选持久化 15m/1h/4h，market_context 保留 1h/4h/1d | `universe_feature_snapshots` + `market_snapshots` | candidate-specific System Operator 技术块；Market Observer `technical-context-v1` 归一化闭合 bar 块；15m >1.5 ATR 不追涨跌 | ✅；按 venue+symbol+market_type 精确路由，spot/perp 不互用；warmup 不足或 gap degraded 时不渲染部分指标 |
| Opportunity 固定持有期结果 | outcome_recorder（目标 instrument OHLCV/ticker + 入场 spread/funding + 配置 fee/slippage） | `universe_forward_outcomes` | net calibration / family edge → System Operator；doctor；debate/guard/flow/thesis 审计；净亏损 guard candidate | ✅，历史 gross-only 行只解释不学习 |
| Opportunity 筛选证据 | Universe ticker/tradability、Observer、Ranker、System Operator、Risk 的事前判断；结果复用目标 instrument 的固定时点成本模型，Universe 实验额外冻结 allocator selected/dropped 事实 | `filter_decision_batches` / `filter_decisions` / `filter_candidate_outcomes` | filter controller（只读）、doctor、shadow/control 净结果、cluster allocation utility、后续受控实验 | ✅，无方向门保存 `unknown` gross-only 且不声称 net/missed alpha；事前已有方向的 regime/crowding 拒绝保留成本后结果；风控/交易所能力永不自动放宽；retention 30d/45d/30d with clean_window_cutoff guard，详见 [`RETENTION_POLICY.md`](RETENTION_POLICY.md) |
| Universe 连续评分 Twin 证据 | `universe_refresh` 在 step-v1 权威分配后重放 continuous point/pessimistic；每55分钟至多采样一批，并将完整 scanner run 以独立 SHA-256 流分入 step control 或 pessimistic treatment（logged propensity 0.5） | `universe_twin_shadow_batches` / `universe_twin_shadow_records` + assigned-policy selected 候选的 `filter_candidate_outcomes(horizon='4h')` | 完整性重放、observed-arm cluster ITT allocation utility、propensity ESS、损失/MAE/CVaR/成本/turnover 的只读诊断；未选候选在完整 scanner-run 分母中记 0 | 🟡 prospective Shadow-only；有独立 policy epoch/assignment/propensity/outcome linkage，但 exact observation lineage、availability profile、NI margins、anytime-valid lower bound 与 actual continuous intervention unavailable，`shadow_authority_applied=false`；Twin 固定30d且不受 Learning clean window 延长，不进入 Paper/Risk/Execution/Learning |
| Universe Relative Rank Shadow（DU-S26） | 同一 `universe_refresh` 的 coarse survivors；observed spread/depth/freshness 为硬证据，perp 额外要求 funding/OI/OI delta/global account ratio/top-trader position ratio | `universe_relative_rank_shadow_batches` + namespaced `filter_candidate_outcomes(horizon='4h')` | Step-v1 control vs `50/30/20` 连续横截面 treatment 的完整 scanner-run cluster utility；Alpha Audit 只读投影 | 🟡 独立 prospective Shadow-only；动态使用实际 eligible `N`，`N<20` unavailable；50 complete clusters/arm + 7 UTC dates才结束样本不足，但仍不授予 promotion/authority |
| Opportunity/决策来源证据 | 原始 scanner trigger + Observer/System Operator 实际渲染 source identities | `opportunities.metadata.evidence_manifest` + `pi_decisions.evidence_manifest_json` | Operator、trace、case study、post-trade reviewer | ✅ `opportunity_information_integrity/v1` 分离 T0/T1；prediction divergence 写标准 T0；Observer 原始来源只留在 `opportunity_t1`，Operator direct 只记实际展示的技术/Position X；Serenity 按独立 id namespace 保存 exact Layer 3 evidence ids；历史缺失显示 unavailable，不按当前 latest row 反推 |
| System Operator 无候选观察 | PI daemon 的 explicit zero-pending queue summary（5 分钟去重） | `operator_evaluation_events` | doctor `loop.skip_halt`、运行诊断与后续只读 Skip report | 🟡 observation-only；30 天 retention（详见 [`RETENTION_POLICY.md`](RETENTION_POLICY.md)）；不是 PiDecision、不进 candidate outcome、不改配置或交易权限 |
| Broker 私有仓位/成交历史 | Bitget/OKX/Binance execution adapter 按完整 account+instrument identity 直查 | 不保存原始响应；只返回标准化 `VerifiedExitResult` | `execution.verified_exit` → 后续 RG-T07 verified/admin close 分流 | ✅ 只读、按 venue 分页；网络/权限/游标/时间窗/歧义失败统一 `inconclusive`，不影响下单路径 |
| Measured Learning ledger + canonical/fallback outcome provenance | Universe measurement-supported 路径与 evaluator 写入的内部事实；不是外部采集器 | 7 个永久 Learning 表 + `filter_candidate_outcomes` + selected-member `universe_forward_outcomes` mirror/fallback | evaluator/lifecycle/doctor + cached 有界只读 `lco-v1` summary/registry-backed dashboard selector | 🟠 Universe 测量闭环、owner projection、当前路由性能与响应式无障碍已实现；Operator bundle 因缺少 decision-utility causal outcome 保持 static fallback；明细/分页和完整 workbench 仍缺失；不需要新增采集 DU claim |

---

> Retention 参考：所有数据域的保留窗口、write-guardrail、VACUUM 策略统一在
> [`RETENTION_POLICY.md`](RETENTION_POLICY.md) 声明。审计链 12 表 + Measured Learning
> Ledger 7 表永不裁剪；其余为 telemetry-only。任何"物尽其用"的路由变更必须同步核对
> retention 窗口是否覆盖新消费者的读取需求（Phase 3, 2026-07-17）。

## 4. Gap 处置

### Positioning Evidence as-of contract

`data/positioning_evidence.py` 复用 canonical `project_snapshot`、`project_price_snapshot`
与 `annualize_funding`，不改变现有 latest/readiness 或交易消费者。
cutoff、source time、acquisition time 均须有时区，source 不得晚于 acquisition 或 cutoff；
当前事实最大年龄 30 分钟，同一比较组 source skew 最大 5 分钟。
相同 base/market/quote/settle 的 exact venue 才构成比较组，不假定 USDT/USDC 平价。
Funding 年化采用 365 天，单位为 ratio/year；median 和 dispersion(range=max-min) 至少需两家。
Basis 仅使用同响应的 mark/index premium ×10000 bps，oracle 等 reference 不冒充 index。
OI 只接受 canonical 证明的 USD notional，缺 venue 降低 coverage；subtotal 允许部分覆盖，
share 仅在 subtotal>0 时定义，disagreement 是 (max-min)/median，median=0 时不可用。
Price×OI state 使用同 provider/instrument/unit/value-kind 的 45–75 分钟历史，
当前 price/OI 和两条 baseline 分别要求 source skew≤5 分钟；明确输出各自变化和 input IDs，
上涨/下跌/不变均为描述，不构成 directional trade recommendation。

查询只读既有表的 cutoff 前 105 分钟，每表最多 2048 行，超过上限整个导出 unavailable。
新行不可替代 cutoff 当时未知的来源；缺 acquisition/source 证明不可猜时点。
导出冻结参与当前和 baseline 判断的 canonical facts、原始 row IDs、来源/venue/单位/时间、
algorithm version 与输入 hash，无新增 Provider、请求、scheduler、存储或交易 authority。
Funding extreme、OI acceleration、crowding 保持 unavailable：现有有界历史未证明相应
历史分布/等间隔窗口和规则；Coinalyze population/unit/source contract 未证明时，
aggregate-vs-exact OI comparison 和 liquidation impulse 保持 unavailable。

### Options volatility observation contract

`OptionsVolatilitySnapshot` 与 GEX 独立，使用现有
[`get_book_summary_by_currency`](https://docs.deribit.com/api-reference/market-data/public-get_book_summary_by_currency)
的 `mark_iv`（年化百分数）、`underlying_price`、`underlying_index` 和
`creation_timestamp`。每币种每 10 分钟最多增加一个摘要行，不增加请求、Provider 或 scheduler。
仅冻结参与派生的 instrument、source timestamp、strike、expiry、IV、forward 和 input hash，
不保存整条 chain。source time 与收到响应后的 acquisition time 分开。

7/30/60D 分别选择距离目标不超过 2/7/14 天的最近 expiry（等距取较早 expiry）；
精确 tenor 标记 `exact`，否则 `nearest`，超界 `unavailable`，输出实际 tenor 和 expiry。
不做跨 expiry 插值或外推。ATM 是同 expiry、同 strike 的 call/put mark IV 均值，
strike 必须位于 forward 的 ±5%，最近 strike 优先；缺任一侧不可用。
25Δ 使用 Black 未贴现、非 premium-adjusted forward Delta：
`d1=(ln(F/K)+sigma²*T/2)/(sigma*sqrt(T))`，call=`N(d1)`，put=`N(d1)-1`，
ACT/365.25。仅接受 underlying_index 精确匹配该 expiry 的期货；
不把 index_price 当 forward，不假定利率，不声称等于账户 Delta。
同 expiry 的每侧以 absolute Delta 对 IV 线性插值到 0.25；必须双侧包围，
端点在 [0.10,0.40] 且 Delta 间距 ≤0.15，不外推，记录 `exact/interpolated/unavailable`。
RR=`call_25d_iv-put_25d_iv`；put/call skew=`put_25d_iv-call_25d_iv`，单位均为 IV 百分点。
term slopes 是后端减前端 IV 百分点；front/back regime 比较有效 7D 与 60D：
正 slope=`back_higher`，负=`front_higher`，精确相等=`flat`，缺失=`unavailable`。

输入 source age 必须处于 [0,120] 秒，入选输入 source skew ≤30 秒；不明时间、
重复 instrument、NaN/inf、非正 IV/strike/forward、到期或标的歧义不得贡献事实。
不可用结果保留原因和 coverage，不补 0。读取要求 acquisition/source time 均不晚于 cutoff，
且 acquisition 距 cutoff ≤30 分钟，不回退到更旧的可用值。
这些事实没有 Candidate、Entry、Prompt、Risk、Sizing、Compiler、Gateway、UTA 或 Live authority。

- **Hyperliquid（曾 ❌ orphan）→ 已闭合 ✅**：`market_observer` 同时读取明确标记为 Hyperliquid 的 funding、predicted funding 与 OI；funding 只用 annualized 值跨 venue 比较，OI 显示绝对值、真实 1h delta 和 observed timestamp。System Operator 的 `evidence_manifest_json` 保存实际命中的 funding/OI row id，不把“已采集”误写成“已使用”。锁定于 DU-S01/S02/S21/B01/B02。
- **永续持仓结构（🟡）**：没有可信自动 endpoint 时，`doctor` 明确显示 not configured；不生成假数据。Owner 可通过显式 CLI 导入白名单 aggregate export，系统保存 source ref/hash 和审计事件，并为有 reference price + directional bias 的行建立 1h/4h/24h shadow outcome。14 天窗口成熟前 `decision_influence=disabled`，之后也只进入人工评审，不自动进入 Risk/Execution。
- **Position X 离线材料（🟡）**：`aletheia.data.position_x` 只读授权样本中的维度说明、结构总结、拟合结果、`analysis_*.json` 的表达样式和 prompt 组织方式；`extract_position_x_material_bundle()` 可一次性把目录中的授权材料提炼成安全 knowledge cards 与结构化快照。原始 prompt、账户、地址、余额、credential、token 不进入运行时卡片、日志或 prompt；拟合文件只保留公式提示、相关性结论和 source hash。
- **清算 + OI 历史（✅ key-guard 接通）**：CoinGlass 全面 paywall、Glassnode 无免费 tier、Bitget OI 历史死代码已确认（2026-06-30 产品定义）。CCXT 清算死代码（`_fetch_liquidations_ccxt` + `_CCXTLiquidationsUnsupported`）已从 `derivatives.py` 删除（GAP-DU-C02，锁定于 DU-C02）。`CoinalyzeCollector` 已建并接入 `data_collector_context`；有 `coinalyze_api_key` 时写入 `open_interest_snapshots(exchange='coinalyze')` 与 `liquidation_events(exchange='coinalyze')`，无 key 时返回 `errors=["no_key"]` 并诚实跳过。持久化路径锁定于 DU-T01。
- **美股/xStocks（🟡）**：owner 明确 observation-only（不交易），符合 R1 的"显式标注"分支，非违反。
- **xStock 上市状态（🟡）**：只在 ticker 失败后查询责任交易所 active spot market list；结果只维护 observation-only 采集配置，不进入排序、风控或执行。查询按 venue 单轮复用并有硬截止，任何不确定结果都保留用户配置。锁定于 DU-S24。
- **Learning Contribution Observability（🟠）**：这里登记的是已有内部事实的展示消费者，
  不是 collected-but-unused 外部数据源。A1 evaluator/lifecycle 已消费这些表；有界、只读、诚实的
  `lco-v1` summary、多路径 dashboard contribution selector、当前路由性能/cache 与当前 surface 无障碍已实现。
  明细/分页、完整 workbench 及其对应无障碍证据仍未实现。不得为填页面新增采集、复制表或把 activity counts 当 contribution。
  对应剩余 owner-workbench 缺口由 `LCO-*` claims 与 GitHub issue #614 管理；Learning
  路线图统一由 #470 管理，不新增 DU acquisition claim。

---

## 5. SDD 编码 (DU-* claims)

| Claim | 级别 | 断言 |
|---|---|---|
| DU-S01 | L2a | `render_enriched_context_md` 有 `xvenue_funding` 参数（消费钩子存在） |
| DU-S02 | L2c | market_observer 读 hyperliquid 行 **且** 传 `xvenue_funding` 进 render（Hyperliquid 真达决策，非仅 collector/doctor） |
| DU-S03 | L2c | market_observer 读 `latest_onchain_snapshots` 且传 `onchain=`（DefiLlama 被消费） |
| DU-S13 | L2a | `perp_structure_snapshots` 已登记，存在白名单 fixture collector，market_observer 读 `latest_perp_structure` 并传 `perp_structure=`，doctor 暴露 `data.perp_structure` 新鲜度 |
| DU-S14 | L2a | `Position X` 离线解析器存在，能通过目录式 bundle 提取安全 knowledge cards / 结构快照；market_observer 与 System Operator 只消费压缩后的 advisory 结构证据 |
| DU-S21 | L2c | Hyperliquid OI 按 source + freshness 读取并与 funding 一起渲染，System Operator manifest 保存实际 row ids |
| DU-S22 | L2c | owner aggregate import 保存 source ref/hash + audit，建立 1h/4h/24h shadow outcomes；14 天前及无数据时均不影响决策 |
| DU-S24 | L2a | xStock 上市状态源在 §3 登记；实现经 provider factory 精确查询责任 venue，单轮复用、有硬截止、不确定时保留配置，确认缺失的配置变更写 system audit |
| DU-S25 | L2a | LLM 逻辑调用、物理 attempt 与 context manifest 三层数据已登记并以 logical_call_id 关联；持久化契约不含 prompt/response/source body |
| DU-S27 | L2a | LiteLLM 公共模型/原生工具价格目录生成不可变、事件时点解析的快照；Gateway startup 单飞 refresh，且没有前端或人工价格 writer；cache/reasoning/web-search 的已观察计数必须有对应费率，Usage 只读计算且历史账本不回写 |
| DU-S28 | L2a | Binance Spot/USD-M fast-market 实现仅保留在 frozen Extension，产品启动不注册且工厂不可启用；保留实现不持久化、不读取凭证、不进入订单路由，Spot depth 未完成 snapshot/diff 连续桥接时 unavailable |
| DU-B01 | L2b | 给定 HL+主所 annualized funding，render 产出"Cross-venue funding"段、divergence 以 pp 计、含 predicted |
| DU-B02 | L2b | 某 symbol 无 HL 数据时，跨venue段整体省略（不漏空行噪音，遵 R3） |
| DU-C01 | L2a | `CoinalyzeCollector` 存在，有 `collect()` 方法和 `domain` 属性（Coinalyze 清算+OI 采集入口） |
| DU-C02 | L2a | `derivatives.py` 不含 `_fetch_liquidations_ccxt` 及 `_CCXTLiquidationsUnsupported`（死代码已删，R4 诚实降级） |
| DU-S04 | L2a | `extensions/yahoo/market_data.py` 存在 + `market_context` 通过可选 binding 与 `enable_yfinance_macro` 门控调用 + `data_utilization.md` §3 已登记（R5 合规） |
| DU-S05 | L2a | 不存在 `class RegimeCollector`；`MacroCollector._classify_risk_regime()` 是 regime 的唯一实现（防虚构采集器类） |
| DU-S06 | L2a | `scheduler.py` 含 `CoinalyzeCollector` 引用（已注册 `_async_data_collector_context`）且 `config/schema.py` 定义 `enable_coinalyze` + `coinalyze_api_key`（调度+配置结构双锁） |
| DU-S07 | L2a | `market_context._TIMEFRAMES` 包含 `'1h'`、`'4h'`、`'1d'` 三值（OHLCV 多周期全量采集，防缩减） |
| DU-S12 | L2a | 默认新闻源包含 SEC/CFTC/Ethereum Foundation 官方 RSS，且 §3 登记为 NewsCollector→`news_items`→market_observer/research_worker |
| DU-T01 | L2b | 离线注入 CoinalyzeCollector 输出，断言 scheduler 写入 `open_interest_snapshots` 与 `liquidation_events`（不依赖真实 API key） |
| DU-S19 | L2a | 三家 execution adapter 的私有仓位/成交历史已登记，统一消费为 `VerifiedExitResult` 三态；原始响应不落库，失败不得伪装 absent |

**维护规则**：新增数据源 → 同步在 §3 登记表加行 + 声明消费者 + 加 DU-* claim（R5）。
