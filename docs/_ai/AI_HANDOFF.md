# AI Handoff

> **本文件是 append-only 变更史**(记录"做了什么改动 + 根因",机器无关)。
> 其中任何"当时机器状态"(gateway 在跑 / 观察期第 N 小时 / 凭证已存 / demo 单已落)
> 都是**写入时刻的快照,不代表当前**。要知道当前真实状态,运行 **`aletheia doctor`**。
> 双机意图/事实分离的金标准见 `docs/_ai/DEV_PROTOCOL.md`。

## Last Updated

2026-06-29 (OpenAlice TaG 深度研究 + P0-1 hash 基础设施 + P1-2 reconcile audit 链入 + 全量文档同步;同日 master 已合 Evolve 模块审计 + Learning 5-bug 修复 + Guards 子标签 — 两条并行工作合并)

---

## Recent Change (OpenAlice TaG 借鉴 P0-1 + P1-2 + 文档全量同步 — 2026-06-29)

分支 `worktree-openalice-tag-research`,5 commit:`b8ed990`(P0-1)+ `2359971`(Vibe Trading 交叉核查)+ `31eb1a9`(P1-2)+ `67ed379`(L2 design 契约同步)+ `cb4a303`(L4 status 同步)。

**研究**(13-agent workflow,797k tokens):三层 ground truth(博客原文 / 本地 `_refs/OpenAlice` 真实 TS 源码 / Aletheia 自身对照)+ 5 镜头分析 + 5 轮对抗核查。报告 `docs/research/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md`(含 Vibe Trading 帖交叉核查 §八点五)。核心发现:**TaG 真正值得借鉴的是工程纪律(reject 一等审计 + 执行后 state_after 快照),不是 git 比喻包装本身**;博客 "1 月崩盘预判" 自标 backtest 逻辑自相矛盾零证据值;"Trade Primitive auto-learn effect" 经源码 grep 命中零是纯营销话术。

**P0-1**(`b8ed990`):TradeProposal 加 `commit_hash` + `parent_hash`(16-char SHA-256 prefix,排除 `status` 与 `commit_hash` 自身后哈希所有不可变字段)+ migrations 加列 + 索引 + `insert_trade_proposal()` 事务内自动填且全局单链(按 `rowid DESC` 选 parent)。SDD:DM-S08/S09 L2a + DM-B06 L2b + DM-C02 L2c(re-compute 确定性 + 链完整性 + **status 免疫**)。不触 trust boundary,改 4 文件 +241 行。

**P1-2**(`31eb1a9`):`position_reconciler.adopt_position()` 写 `position_adopted` audit event 时,在 `after_state` 加 `commit_hash` + `parent_hash` 两字段(直接读 insert_trade_proposal 事务内填充的 proposal 对象,无需重算)。SDD:PL-ADOPT-09 L2b。改 2 文件 +99 行。**价值闭合**:P0-1 给提案打了内容指纹,P1-2 让 reconcile audit 第一个用上这个指纹——审计消费者从此单看 `audit.jsonl` 行就能还原 hash 链关联。

**A FORBIDDEN_INTENTS hardening 核查发现是 no-op**:4-agent explore workflow 发现 `deactivate_kill_switch` + `modify_permissions` **早在 2026-06-23 就加进** `agent_shell/registry.py:415-426` 并配 "Defense-in-depth double-lock" 注释,OL-C03 spec status=implemented 无 xfail,BACKEND_ISSUES §15.1 已标 ✅。`MASTER_FIX_LEDGER_2026-06-27.md` 本就没列(verifier 误指控);典型 memory `aletheia-backend-issues-archive-unreliable` 应用场景——文档标"未修"≠代码真未修,grep 优先。

**文档全量同步**(5-agent parallel workflow + 1 个 adversarial verifier):L2 design 契约(`data_model.md` 加 trade_proposals.commit_hash/parent_hash blockquote + `audit_chain_contract.md` §1 范围扩展 + §6.3 表加 position_adopted 行 + §6.4 两链对照表 + §7.3 doctor 探针 gap + `trust_and_change_control.md §1.1` 末尾加 hash chain 锁定 + `design/README.md` spec 表纠正 spec_data_model.py 8→18 + 补齐 spec_audit_chain.py / spec_pipeline_adopted_position.py 漏列);L4 status(本会话 4 文档 prepend);docs/references 同步(INDEX §3 加新研究报告 + openalice_prism_review.md 标 ✅);新建 `docs/issues/TAG_BORROW_BACKLOG.md` 列剩余借鉴清单状态。

**验证**:DM-S08/S09/B06/C02 + PL-ADOPT-09 全 GREEN;邻近 57 specs passed;全量 1802 passed / 3 skipped / 0 failed(2:08,与 baseline 一致零回归);conformance 360→361 / 99% coverage 维持;doctor `trust.audit_chain` 通过。Merge 进 master 时与 master 同日的 lesson guard fields 加列 (`activated_at`/`dry_run_until`/`expires_at` 进 ps_lessons,见下一章) 在 `_ADDITIVE_COLUMNS` 列表末尾共存,无逻辑冲突。

**剩余 TaG 借鉴清单**(`docs/issues/TAG_BORROW_BACKLOG.md`):
- P0-2 执行后 state_after 账户全量快照(`gateway.py` 已有 audit 写入点加字段;触 trust boundary,需 owner 显式批准)
- P0-3 Guard 责任链拆分(`risk/engine.py:77-332` 20 个 check 拆 `risk/guards/`;trust boundary 红线最严,需 SDD 全套行为零变化)
- P1-1 reject 与 push 同结构(三路拒绝 audit 补 hash 字段;触 trust boundary)

memory:`aletheia-tag-borrow-plan` 已同步落地状态。

---

## Recent Change (Evolve 模块 — Learning 修复 + Guards 子标签 — 2026-06-29)

**背景**：全量 Debug 批次完成后，审计 Evolve 模块下所有子功能的健康状况。

**d85f7b3 — fix(learning): 5-bug Learning module fix — settler/guard/spec/docs**：
1. Signal Settler 挂起 — `settle_due()` 缺独立 scheduler job；新增 `outcome_settler` 15min 定时任务（unconditional）。
2. Guard UI 缺失 — 新增 `POST /settings/guard` 端点 + Settings 页面 "Guard Promotion" 区域含 auto_promote_enabled 开关。
3. `spec_lesson_guards.py` — master 已有完整 4 claims（LG-S01~S04）。
4. LP spec 状态标注 — LP-S01~S06 加 explicit `status="implemented"`。
5. `docs/design/knowledge_contract.md §6.A1` — 更新为 L-Bug-B 已修复后的真实状态。

**7c21813 — fix(ui): post-trade lessons — click-to-expand drawer + trim ellipsis**：
Lessons 列表行可点击展开 DetailDrawer，正文超 120 字符显示 "…"。

**6df3999 — feat(evolve): Guards sub-tab + LessonRow TTL fields + stale comment cleanup**：
1. `client.ts:199` 陈旧注释（称 L-Bug-B 未接通）已移除。
2. `LessonRow` 类型补齐 `activated_at/dry_run_until/expires_at`；lesson 抽屉渲染 TTL 字段。
3. Knowledge Base 新增 "Active Guards" 子标签 — 接通后端 `GET /api/knowledge/guards`（之前零前端消费者）。

**Ledger 核实**（大量"待修"项实际早已修复，ledger 未同步）：
- show_errors yfinance bug、B1 ranker post_trade_review、A9 operator win_rate、L-Bug-D B2/B3 — 均已修复（代码注释明确标注）

---

## Recent Change (负反馈闭环：高维 + 可推翻 self-evolve — 2026-06-28)

分支 `worktree-neg-feedback-loop-fix`。审计发现"用交易结果调整未来交易"的 4 条通路 3 条坏；owner 定调（记忆 [[aletheia-negfeedback-high-dimensional-evolve]]）：**要高维模式归因 + 可被后续证据推翻的 self-evolve + 全自动，摒弃低维 symbol/direction/scanner 硬拦截**。设计见 `docs/plans/round2_negative_feedback_loop_closure.md`。

**根因 + 修复（5 项核心）**：
- **Path 6 反思**：`reflection.py:62/65` 重复 `opportunity_id=` kwarg → 整模块 `SyntaxError`，被调用方 `try/except` 静默吞 → record_decision/get_last_reflection 双失效、doctor 不报警。提交 5be7826（A13）引入。修=删重复行 + `test_agent_shell::test_reflection_module_imports_cleanly` import smoke 锁回归。
- **缺陷②脏数据带偏**：三学习模块用滚动 30/90 天全量、含 Round1 脏数据 → 学出"全 contra"。修=新增 `analytics/learning_window.clean_window_cutoff`（floor=2026-06-25T15:22Z），insight_synthesizer/confidence_calibration/reasoning_feedback 三处取数接入。
- **缺陷③不会自我纠错（owner 最在意）**：高维 memo 系统已全自动注入决策链，但 **Module J 缺失**——无回溯评估、memo 仅 72h 时间衰减、`MAX_DIRECTION_BIAS_STRENGTH` 死常量。修=新增 `analytics/memo_effectiveness.evaluate_prior_memo`（回溯评估上一 memo regime_playbook 预测 vs 实际→accuracy 写回 tags→overturned 注入新 memo `prior_corrections`→render 进 observer/PI prompt）+ enforce bias cap（单边占比>0.75 force balanced）。
- **缺陷①高维退化**：`current_regime`/`_current_regime` 读 macro `risk_regime` 而非 `market_regimes.state` → positions.regime_tag 全 neutral/unknown，regime_playbook 名存实亡。修=两函数优先读 market_regimes.state（fallback macro→unknown），所有 broker 经 enrichment 受益；reconciler adopt 旁路（38 NULL）留后续。
- **不做**：低维硬拦截（guard predicate / risk engine block_*），按 owner 定调。

**SDD**：新增 `tests/specs/spec_insight_feedback_loop.py`（IFL-S01/B01/B03/S02/B04/B05/S03，7 claims 全绿）+ 接入 test_spec_conformance；ROADMAP Self-Evolution Tier2/2.5 标记 DONE（修文档-代码背离）。

**附带修复（owner 追加，4 个 pre-existing 全量失败，与上面无关）**：
- `settings.py` GET /settings 漏 `serenity` section（schema+POST+前端控件已就绪，仅 GET 漏）→ 加一行。
- `repositories.py` 缺 `prune_agent_shell_{heartbeats,events,messages,artifacts}`（scheduler 调用但 ba80a62 不完整重构未实现 → cleanup AttributeError 被吞、连带 data_pruned audit 没写）→ 加 4 函数（was_in_tx 模式）。
- `test_serenity_workbench::test_promote_theme` 过时（断言旧 `draft`/`proposalCandidateId`，代码已是 Phase3 `submitted_to_pi`）→ 改测试匹配契约（代码对、测试漂移）。

**验证**：全量 1762 passed / 0 failed / 3 skipped（修复前 1758+4 failed）；architecture_fitness 11、spec_conformance 全绿；零回归。
**已合并主线**：3 commit（feat(learning) 负反馈 / fix 4 测试 / fix(operator,pi) PI 审计）+ merge master。待主仓 `aletheia gateway restart` + `doctor` 生效（worktree data/ 为空）。

---

## Recent Change (AI Berkshire 整合 — 2026-06-28)

**目标**：把 GitHub xbtlin/ai-berkshire 的价值投研框架模式吸收进 Serenity Lens。

**5 个子功能（SDD first，全部 GREEN）**：

- **S1 多透镜研究** (`087ec9d`)：4 个视角(technical/flow/sentiment/macro)顺序 LLM 调用
  替换原单次 Stage 1 调用；每条 evidence 带 `agent_lens` 字段标记来源视角。
  顺序设计因 Databricks rate limit（并发 → 429 全灭）。

- **S2 可研究性评级** (`cdad0c3`)：`assess_researchability_grade()` 确定性算法产出 A/B/C
  评级；A=≥2 high_confidence+URL，B=≥3 likely，C=其余；C 级上限封顶 score≤40，
  防止低质证据撑出高分机会。

- **S3 来源可靠性天花板** (`372fda3`)：`SOURCE_RELIABILITY_TIER` 软天花板；news/social→
  likely，market_data→high_confidence，official→disclosed；阻止来源本身可信度不足
  的证据被评为 disclosed（e.g. "看了新闻就说已公开披露"）。

- **S4 Forced Verdict** (`cdad0c3`)：每个 opportunity 必须带 `verdict_json`（含
  recommendation/bull_case/bear_case/key_assumption/invalidation）；防止 AI 空谈
  不下判断。

- **P1 key_assumptions** (`ddad0cb`)：`TradingDecision` 新增 `key_assumptions: list[str]`
  字段；PI 被要求写出≤3 条核心前提假设；落库至 `pi_decisions.key_assumptions`（JSON
  数组）；reflection.py 同步持久化。

- **U1/U2 前端显示** (`c14469e`)：SerenityLens 机会表格新增 Verdict(彩色徽章)/Grade(A/B/C)
  /可展开 bear_case；证据表格新增 Lens 列（agent_lens 标签）。

**spec 覆盖**：303 claims GREEN（ML-S01~S03/B01~B02 + SE-S06/I06 + SV-S03/S04/B02 + 迁移修正）。

**待合并主仓**：分支 `worktree-lazy-drifting-clarke`。合并后须在主仓 `gateway restart`
才能生效（worktree data/ 为空，见记忆 [[aletheia-worktree-gateway-empty-data-trap]]）。

**剩余 P3 项（数据积累后再推）**：
- D1: xStock 可研究性数据质量管理（等 researchability_grade 数据积累后再做）
- D2: evidence URL 提取增强（LLM prompt 改善，等运行数据后迭代）

---

## Recent Change (Equity Curve 误报 + 诊断代理陷阱勘误 — 2026-06-24)

**前端 bug**:`PnLChart.tsx` useQuery 未处理 `isError`,请求失败时 data 为空 →
chartData.length===0 → 走"还没平仓"空态文案,与 DB 里 68 笔平仓事实矛盾。
违反"onError 必须处理"。修复 dbde229:isError 分支显示 "Failed to load equity
curve." + Retry 按钮。

**勘误**:dbde229 commit body 把诊断时 curl 探活的 502 错误地外推为"浏览器
走系统代理被转坏",并建议用户"代理软件绕过列表加 127.0.0.1"。用户当场指出
后实测纠正:WinINET 系统代理 `ProxyEnable=0` 且 127.* 已 DIRECT,浏览器直连
无碍;只是**环境变量代理** `HTTP_PROXY=127.0.0.1:7897`+NO_PROXY 空,导致 curl
误把 localhost 丢给代理产生 502 假象。记忆 [[aletheia-localhost-probe-proxy-trap]]
已写入。以后诊断:curl 探活 localhost 必须 `--noproxy '*'`;后端死活以 gateway
status + /api/health 的 status 字段为准。

不 amend dbde229 历史,以 DEVLOG 2026-06-24 条勘误为准。

---

## Recent Change (最小赚钱循环修复 — 2026-06-23→24)

> **背景**：用户观察到"系统不怎么开仓"。逐层排查发现 PI Agent(Phase 4a 后唯一
> proposal 创建者)被**五层叠加阻碍**卡死,20h+ 零开仓。每修一层暴露下一层。
> 最终全部清除后 PI 恢复正常多空交易(02:00+ 5 trade/2 skip,3 笔持仓)。

**五层阻碍 + 修复(按发现顺序)**：
1. **OKX contractSize 单位混淆** (`650f608`)：CCXT swap `amount`/`contracts` 单位是
   合约张数非币数,放大 contractSize 倍(TRX 1000×)且绕过风控 notional。
   okx/client.py 边界换算 coins↔contracts。
2. **PI 余额幻觉破产** (`6c006cb` 冻结例外#1)：8 个 context-injection bug 喂 LLM
   错误事实(余额读 bitget 闲置$167 vs 真实 okx_demo $88k / 统计被 cleanup 污染 /
   kill_switch AttributeError / 策略 status-vs-state typo / win_rate 误读为 drawdown /
   bias 标签 / debate 公式丢 initial)。trading_context + trading_decision 修。
3. **OKX 跨 loop 回归** (`d6fc427`)：`_contract_size` 的 `load_markets` 在 `_run_coro`
   新 loop 跑,但 exchange 在 __init__ 旧 loop 建 → 跨 loop Future → contractSize
   回退 1.0 → contractSize 修复破功。OKXClient 改 lazy-init(`_ensure_exchange`)。
4. **日损 $1 误熔断** (`5831bd1` CR-RE-03)：demo 日损门控误用 `max_live_limited_cap`
   ($100×1%=$1)作基数,清账 -23.26 损失触发熔断阻断当日全部交易。engine.py:154
   按 `_is_live()` 分流,demo 用 `starting_capital_usdt`。config starting_capital→88000。
5. **signal 重复→"数据损坏"skip** (`edc15b2` 冻结例外#2 + `079bb9d`)：paper_trader 写
   signal_pending 后不标 opportunity=reviewed → 每 5min 重复入选 → PI 读重复判
   "data integrity compromised"。paper_trader.py:153 加 update_opportunity(reviewed);
   清理 1676 条历史重复;新增 prune_pi_decisions(7d,保护决策行)防堆积。

**修改文件(生产代码)**：okx/client.py / okx_broker.py / exchange_sync.py / pnl.py /
risk/engine.py(+models/migrations proposal_execution_mode 列) / strategy_lifecycle.py /
reconciliation.py(OKX/Binance live 订单告警) / paper_trader.py / trading_context.py /
trading_decision.py / instruments.py(tradable_symbols canonical) / fallback.py(_MODEL_HEALTH 锁) /
manager.py(daily_review 质量门) / market_data.py(xStock infra_event) / jsonl.py(tz 后缀+guard) /
doctor.py(_probe_profit_loop 探针) / repositories.py(prune serenity/shadow/pi_decisions) /
settings.py(HealthCard 422 修) / analytics.py(equity-curve uPnL) / 前端 Settings.tsx+client.ts

**冻结例外(3 次,用户授权,登记 EXPERIMENT_PROTOCOL)**：#1 profit-loop 8 bug /
#2 PT-1 signal 去重 / #3 CR-RE-03 日损基数。Round 1 起点保留 6/22,**所有修复
生效前的数据不计入策略评估**,6/24 起为干净观察窗口。

**监测固化**：doctor `loop.profit` 探针(opportunities 流动但 proposal 停滞 6h+ 即告警) +
`spec_pipeline_profit_loop.py` E2E。这类静默断流再不会无声发生。

**验证**：全量 1651 tests passed;生产路径 E2E(DOGE demo 单 notional $11.84 正确);
loop.profit ✓ healthy;PI 恢复 trade 决策(SOL/ETH/BTC short,notional 规格正确)。

---

## Recent Change (SDD 体系扩张 + 全面代码审计 — 2026-06-23)

> 在修 bug 之外,本会话还做了 SDD 文档体系的大规模加固 + 一次全面代码审计。

**SDD 扩张**：spec claims 45→114;新建 4 份 spec(api_reference/data_layer/operator_loop/
subsystem_observers) + 3 份契约(knowledge/scheduler/audit_chain);修 12 份契约漂移;
扩 6 份现有 spec 到 L2b/L2c。详见 `docs/research/DOC_CONTROL_ANALYSIS_2026-06-23.md`。

**代码审计**：16 契约 spec↔code 对账 + 7 管道 E2E + git log 挖掘 → 74 findings,
Top 25 修复优先级。详见 `docs/research/CODE_AUDIT_REPORT_2026-06-23.md`。已修 trust 项
(OL-C03 双锁/CR-RE-01 proposal_execution_mode/CR-EX-01 reconcile 告警/CR-DM-01 ID 决议)。
未决项分类登记 BACKEND_ISSUES §15-18(trust 待授权/frozen 待 Round2/卫生下 sprint)。

**Code review(11 角度)**：修 2 个 get_cached_state 改动遗漏(exchange_sync None 防御 +
inspect_cmd 兜底);否决 6 个 agent 误报(混淆历史 diff)。新发现 OP-WINRATE
(operator_loop fills 无 realized_pnl → win_rate 恒 0,pre-existing,Round 2) + APP-HMAC
(tunnel auth timing side-channel,待修)。

---

## Recent Change (Exchange 统一选择 — 2026-06-20)

> **目标**：消除三轴分裂（market_data.ccxt_exchange / exchange.paper_execution_mode / exchange.name 各指不同交易所）。
> 新增 `POST /api/settings/primary-exchange`，一次调用原子同步全部 4 个字段。

**根因**：系统原本允许用户在不同地方单独配置数据源和执行交易所，导致产生三轴不一致的状态（如 name=bitget，ccxt=okx，paper_mode=okx_demo）。

**修改文件**：
- `aletheia/server/routes/settings.py`：新增 `_EXCHANGE_UNIFICATION_MAP`（okx/bitget/binance 三个映射）+ `PrimaryExchangeRequest` + `POST /primary-exchange` endpoint；paper_internal 选项只改 paper_execution_mode，不动 name/ccxt；直接写 `cfg.risk.allowed_exchange`（绕过 `_RISK_IMMUTABLE`，这是设计）
- `aletheia/diagnostics/config_consistency.py`：扩展 `_DEMO_CRED_MAP` 覆盖 okx_demo + binance_testnet；新增 `_check_exchange_consistency` 检查 name≠ccxt 时输出 info warning
- `frontend/src/api/client.ts`：新增 `setPrimaryExchange()` API 方法
- `frontend/src/pages/Settings.tsx`：新增 `PrimaryExchangeCard`（4-tile 选择器，放在 ExchangeConnectionCard 前）；ExchangeModeRow hint 改为动态；Trading Mode → "Trading Mode — Advanced"
- `frontend/src/index.css`：新增 `.exchange-tiles` / `.exchange-tile` / `.exchange-tile-active` 等 CSS

**验证**：
- `npm run build` 0 error（777 modules）
- `pytest -k "config or settings"` 79/79 pass
- API smoke test：POST `{"exchange_id":"okx"}` → 4 字段全部变为 okx；切回 bitget → 全部变为 bitget

**xStocks 例外**：3 处 `CCXTProvider(exchange="bitget")` 硬编码（ON-series 代币仅在 Bitget 上市），未动。

---

## Recent Change (P1 竞品 Gap 三功能 — 2026-06-20, c1e67a5)

> **目标**：竞品差距分析 P1 — 加速策略迭代：(A) 本地历史数据避免每次回测拉 API；
> (B) Optuna Bayesian 搜索替换暴力 param_sweep；(C) 事件驱动 30s 价格/量监测 + 触发。

**Feature A: 历史数据管理**
- `aletheia/data/history_downloader.py` (新建)：`download_symbol_history()` 增量下载 + `check_coverage()`；分页 500 bars，0.35s 限速，检测 `get_ohlcv_newest_ts()` 决定增量起点
- `aletheia/storage/repositories.py`：`upsert_ohlcv_candles` 新增 `window=None` 动态裁剪；新增 `get_ohlcv_coverage()` + `get_ohlcv_newest_ts()`
- `aletheia/strategy/backtest.py`：`load_bars_from_db(limit=None)` 读 config `max_bars_per_pair`
- `aletheia/agent_runtime/scheduler.py`：`_run_ohlcv_nightly_backfill()` + cron 注册（CST 03:30）+ price_feed timeout 25s→45s
- `aletheia/cli/data_cmd.py` (新建)：`aletheia data download SYMBOL` + `aletheia data coverage SYMBOL`
- `aletheia/main.py`：挂载 `data_app` typer

**Feature B: Optuna 智能参数优化**
- `pyproject.toml`：新增 `optuna>=3.6`
- `aletheia/config/schema.py`：`StrategyFactoryConfig` 新增 `sweep_use_optuna=False`、`sweep_n_trials=60`、`sweep_optuna_timeout_sec=120.0`、`sweep_top_k=10`
- `aletheia/strategy/param_sweep.py`：`_run_optuna_sweep()` 用 `TPESampler(seed=42)` + `MedianPruner`；`run_parameter_sweep()` 按 flag 派发；`_run_brute_force_sweep()` 提取原逻辑
- Settings 前端 + route：4 个新字段复用 `POST /strategy_factory`

**Feature C: 事件驱动触发**
- `aletheia/config/schema.py`：新增 `EventTriggerConfig`（enabled / price_move_pct_threshold / volume_surge_multiplier 等）
- `aletheia/data/price_feed.py`：缓存结构扩展 `prev_price` + `volume_24h`；tick-gating（universe 每 3 tick/90s）；`_check_event_triggers()` 检测价格/量突破；`_notify()` 封装
- 新增 `POST /settings/event_triggers` route

**测试**：17 new tests 全绿（test_history_downloader.py × 5、test_param_sweep_optuna.py × 3、test_event_triggers.py × 9）

---

---

## Recent Change (Exchange-native TPSL bracket — 2026-06-20, f9e8dfb)

> **目标**：竞品 Gap 分析后，实施最高 ROI 改进：在 Bitget 交易所层面挂 SL + TP trigger
> orders，让止损/止盈在价格触及时毫秒级响应，不再依赖 scheduler 5-min 轮询。

**设计决策**：
- 两个独立 trigger（`pos_loss` + `pos_profit`）而非 combined `profit_loss` planType，
  因为 Bitget OCO 联动语义文档不明确，独立挂单行为确定。
- 本地 position_manager 轮询保留为 fallback（belt-and-suspenders）。
- 失败不阻塞开仓：TPSL 挂单失败仅 log warning，position 仍正常创建。
- 功能开关 `exchange_tpsl_enabled: bool = False`，需用户在 Settings 前端显式开启。
- Exchange-triggered close 检测：`_sync_exchange_closed()` 在每次 evaluate 周期开头
  调用 `get_positions()` 对比，发现本地持仓已不在交易所 → 以 `exchange_bracket_triggered` 关仓。

**文件变动**：
- `aletheia/exchanges/bitget/client.py`：新增 place_tpsl_order / cancel_plan_order / get_pending_plan_orders
- `aletheia/execution/bitget_broker.py`：_try_place_tpsl / _try_cancel_tpsl / replace_exchange_stop；
  execute_live_order 新增 stop_price + take_profit_price 参数；平仓前取消 bracket
- `aletheia/execution/gateway.py`：demo + live bitget 分支均计算 TP 并传 broker
- `aletheia/portfolio/position_manager.py`：_sync_exchange_closed；trailing stop 联动 replace_exchange_stop；
  close_position 改为 top-level import（修复 patch 路径以支持测试）
- `aletheia/storage/migrations.py + models.py`：exchange_tpsl_order_id TEXT 列（additive）
- `aletheia/config/schema.py`：exchange_tpsl_enabled: bool = False
- `aletheia/server/routes/settings.py`：TradingConfigUpdate 新增字段
- `frontend/src/pages/Settings.tsx`：Exchange-native TPSL Toggle UI
- `tests/test_tpsl.py`：11 tests，全部通过

**也修复了 4 个 pre-existing test failures**（commit e069d56）：
- Universe.tsx Scanner Calibration 表：6 个 inline style → CSS class
- Settings.tsx UniverseConfigCard：去除多余 onDirty prop
- index.css：新增 button:active { transform: none; }
- docs/_ai/PROJECT_CONTEXT.md：补充 :8787 端口

---

## Recent Change (Trade Analytics 面板 + Batch 2 bug 修复 — 2026-06-19, 0b3b1a6)

> **目标**：在 Trades & Positions 页面增加 Analytics sub-tab，可视化展示
> 交易模式差异（RuleSet vs Opportunity）、scanner family 表现、持仓时间分布。

**Batch 2 Code-Review 修复（4 个 bugs，优先于 analytics 实施）**：
- **CRITICAL** `aletheia/storage/migrations.py`：`_ADDITIVE_COLUMNS` 补入 `("atr_pct", "universe_members", "REAL")`；
  缺失导致现有 DB 升级后 ATR 仓位定规模链路静默失败（`_atr_adjusted_notional()` 读到 None）。
- **HIGH** `aletheia/agents/strategy_designer.py`：`_covered` 集合改为仅含 active 状态策略；
  原代码把 retired/failed 策略也排除，导致 ranker 优先给已失败币种生成新策略。
  同时用 `to_bitget()` 标准化符号，消除 `BTC/USDT` vs `BTCUSDT` 比较总为 False 的 bug。
- **MEDIUM** symbol 格式：`strategy_designer.py` 原用 `.replace("/","")` 手动转换，
  违反 CLAUDE.md canonical symbol 规则，改为 `to_bitget()` 统一入口。
- **INFO** `aletheia/universe/allocator.py`：`get_family_multipliers()` 文档字符串更正
  （"momentum/derivatives → 4h" → 实际 "momentum → 4h, derivatives/mean_reversion/volatility → 1h"）。

**Trade Analytics 面板**：
- `aletheia/analytics/trade_stats.py`（新建）：`build_trade_stats()` 聚合已平仓
  持仓，按 source type / family / hold-time 分桶。RuleSet 判断用
  `json_extract(s.parameters, '$.ruleset') IS NOT NULL`（`strategy_evidence.py` 同标准）。
  family 数据复用 `compute_family_scorecard()`。
- `aletheia/server/routes/analytics.py`：追加 `GET /api/analytics/trade-stats`。
- `frontend/src/charts/TradeAnalyticsPanel.tsx`（新建）：4 区块 CSS bar chart 面板，
  `useQuery(staleTime=60s)`，零新依赖。
- `frontend/src/api/client.ts`：追加 `getTradeStats()` 及 TypeScript 接口。
- `frontend/src/pages/TradesPositions.tsx`：用 `<Tabs>` 拆出 Blotter / Analytics 两个 tab。
- `frontend/src/index.css`：追加 `.analytics-*` CSS 类。
- `aletheia/dashboard_static/`：重新构建（203 modules）。

**验证**：`GET /api/analytics/trade-stats` 返回正确 JSON（59 trades，by_source/by_family/
hold_distribution/totals 字段完整）；前端构建 0 error；gateway 已重启。

---

## Last Updated (前次)

2026-06-17 (Self-Evolution Tier 1 实施完毕；worktree 待合入 master)

---

## Recent Change (Self-Evolution Tier 1 — 2026-06-17, worktree-self-evolution-tier1)

> **目标**：关闭 `universe_forward_outcomes` → knowledge base 反馈回路。
> 此前坏结果有数据却从未反哺规则；Tier 1 是零 LLM 成本的最低风险起点。

**核心逻辑**（`aletheia/universe/outcome_recorder.py`）：
- `settle_due()` 结算后调用 `_maybe_generate_guard_candidates(settled_rows)`
- 只处理 `horizon=4h`；方向感知阈值：long `return < -3%`，short `return > +3%`
- 7 天内同 `symbol+scanner_family` 去重（`repositories.guard_candidate_exists_recent()`）
- 生成 `medium_confidence` guard_candidate（不进 FTS 检索，需人工确认升级）
- 失败只 `logger.warning`，不影响 `settle_due()` 返回值

**修改文件**：
- `aletheia/universe/outcome_recorder.py`：`_GUARD_HORIZON` / `_LOSS_THRESHOLD_*` 常量 + `_maybe_generate_guard_candidates()` + `_build_guard_candidate_content()`
- `aletheia/storage/repositories.py`：`guard_candidate_exists_recent()` dedup 查询
- `tests/test_self_evolution_tier1.py`（新建）：10 个单元测试，全通过

**文档整理**（第二个提交）：
- `docs/_ai/SELF_EVOLUTION_DESIGN.md` → `docs/plans/self_evolution_plan.md`（移入 plans/，加明确日期）
- `docs/status/ROADMAP.md`：新增 Self-Evolution 横切轨道（Tier 1/2/3 状态表）
- `docs/_ai/CURRENT_TASK.md`：待办第 0 项写入 Tier 2（2026-07-01）、Tier 3（2026-08-17）

**验证**：用 master `.venv` 跑全量 suite，**1311 passed, 0 failed**。

**待操作**：worktree 分支 `worktree-self-evolution-tier1` 尚未合入 master，
因 master 有未提交的 in-progress 工作（equity observer、futures integration 等）。
合并命令：`git merge worktree-self-evolution-tier1 --no-ff`（处理完 master 未提交变更后执行）。

---

## Last Updated (前次)

2026-06-16 (项目级 code-review:契约漂移 + 运行态正确性,15 findings → 修复)

---

## Recent Change (项目级 Code-Review 修复 — 2026-06-16, uncommitted)

> 全项目 code-review(契约对应 + 运行态正确性 + 潜在 bug)。15 条确认 findings:
> 7 条机械修复已先行落地,4 条设计级(A/B/C/D)在本轮完整实现。

**A — universe high-confidence 标签不可达**
- 根因:`signal_emitter._HIGH_CONF_THRESHOLD=70`,但 allocator 总分天花板 ≈68.5
  (opportunity 0.35×100 + tradability 0.25×100 + liquidity 0.15×90 − min cost 5.0),
  所以每个 deterministic opportunity 都被强制 "medium","high" 永不出现。
- 修复:阈值降到 60,保留有意义的 medium=[45,60) / high=[60,ceiling] 区分。

**B — forward outcome 只能给 BTC/ETH/SOL 定价**
- 根因:`settle_due` 只读存量 market snapshot,而 snapshot 仅覆盖静态观测的
  3 个标的;轮动 universe 成员全部 None → 标 `failed`,校准永远攒不到数据。
- 修复:`settle_due`/`_batch_fetch_prices` 改 async,用共享 CCXT provider 给全部
  symbol 取 live ticker(provider 关闭一次),snapshot 仅作兜底。

**C — calibration 胜率对 short 方向算反了**
- 根因:`calibration_stats` 把 `forward_return_pct > 0` 一律算赢,但 short 仓在
  价格下跌时才赢 → derivatives/mean_reversion 的 short 选择被系统性误判。
- 修复:`UniverseForwardOutcome` 加 `direction` 列(model + 迁移:CREATE TABLE
  与 `_ADDITIVE_COLUMNS` 双路,startup-only,幂等);`service.refresh` 用
  `_infer_direction` 写入方向;`register_pending` 落库;胜率 SQL 改方向感知
  (long/NULL: return>0 赢;short: return<0 赢),`positive_count`→`win_count`
  (语义诚实,short 赢在负收益),`routes/universe.py` 同步改名。

**D — 设置保存后刷新间隔不生效**
- 根因:APScheduler IntervalTrigger 添加后不可变,保存的 `refresh_interval_min`
  要等下次重启才生效。
- 修复:新增 `scheduler.reschedule_universe_refresh(interval_min)`(锁内
  `reschedule_job`,job 不存在/异常返回 False 不阻断保存);`settings.py` 在
  `refresh_interval_min` 变化时调用,返回 `rescheduled` 标志。

**Tests**: 新增 `tests/test_universe_calibration.py`(3 方向感知用例);改 signal_emitter
阈值用例。ABCD 受影响测试单独跑 36/36 全绿,且未新增 DB writer / scheduler job。

**顺带修复(同批)**:`test_startup_gate::test_empty_response_not_usable` 原失败——测试桩
`_FakeResp` 缺 `.raw`、与生产 `startup_gate.py:64`(`resp.raw` 兜底判定)漂移。给
`_FakeResp.__init__` 加 `raw=None` 参数对齐真实 `ModelResponse`,零生产代码改动
(详见 `docs/issues/BACKEND_ISSUES.md` §6.5,已标 ✅)。修复后 `test_startup_gate` 14/14 绿,
全量基线恢复全绿。

## Recent Change (Phase 0 节奏调整 + 文档统一 — 2026-06-16)

> **节奏调整**:取消专门隔离 72h 不 commit 的观察窗口,改为边开发边持续观察闭环健康。
> `code_drift` 自愈重启是预期行为;Phase 间不再硬性串行;Phase 6 Live 仍需显式授权。

- `docs/_ai/CURRENT_TASK.md`:重写 Phase 0 段(binary gates → continuous trends),Pi Agent 段压缩为摘要
- `docs/status/ROADMAP.md`:Phase 0 / Track B / 红线调整,去掉"Phase 0 门全绿"前置
- `docs/_ai/PROJECT_CONTEXT.md`:全面重写(777→1295 tests, 新增 operator/edge lab/universe 等里程碑)
- `docs/_ai/DEV_PROTOCOL.md` §4:删除"战略冻结"段,替换为"边开发边观察"
- `docs/_ai/AI_HANDOFF.md`:压缩 2447→<500 行(50+ Operator 增量条目合并为一段摘要)
- `docs/_ai/REPAIR_PLAN.md`:标记归档(全部 Wave 完成)

> **Model provider thinking-mode fix** (uncommitted):
> `openai_compatible.py:145` + `routes/model.py:259`: `max_tokens` 5/10→200,
> thinking-mode 模型把 budget 花在推理上产出空 text → 误判 unavailable。

## Recent Change (P1 FTS5 + Universe + UI — 2026-06-16, commits cb9692e..5f2433f)

- **FTS5 experience-loop fix** (cb9692e): startup `reindex_all()` backfill + doctor `fts.coverage` probe + 5 tests
- **Universe forward outcomes**: 15m/1h/4h/24h 收益追踪 + `GET /api/universe/outcomes`
- **Deterministic opportunity path** (a0c7cfa): `signal_emitter.py` — universe members → Opportunity (无 LLM); 32 tests
- **Test fixture refactor** (ed1015d, f113230): LLM-skip behavior alignment + startup_gate sleep skip (21min→9min)
- **Universe snapshot TTL** (d0dc48d, 8f69a44, 5b61fe5): 24h TTL + stale markers + Settings TTL control
- **Model provider**: thinking-mode content extraction + inference probe fallback (14321f1, 540c0ab)
- **Settings UI**: real values instead of placeholder hints (5f2433f)
- Tests: 1295/1295 pass

## Recent Change (决策层重构 — 2026-06-15, commits ef3e765, 73703ef)

- **RuleSet-driven trading**: `ruleset_scanner.py` 从 OHLCV 直接生成 entry/exit 信号
- **PI Agent conviction sizing**: structured LLM 评估 → adversarial debate → fractional Kelly
- **paper_trader v3**: Path A (RuleSet multi-strategy) + Path B (opportunity fallback)
- **Strategy lifecycle automation**: auto-promote / auto-pause

## Recent Change (Edge Lab — 2026-06-14, commits 9fe5c5f..ab4a522)

- Shadow trades (Top-5 反事实), configurable slippage (5bps), BTC benchmark, regime tags
- Family scorecard API (`GET /api/analytics/family-scorecard`)
- 11 tests; SQL fix (`scanner_family` JOIN path)

## Recent Change (全局代理配置 — 2026-06-14, commit 28eb357)

- `aletheia/net.py`: httpx/aiohttp 统一代理工厂
- 13 文件 30+ 处调用点替换; Settings UI NetworkCard; doctor `network.proxy` probe

## Recent Change (9域数据收集层 — 2026-06-14, commit 84a66fd)

- microstructure/derivatives/macro collectors + normalizer + context_builder
- 5 DB tables, scheduler 2min tick, 16 tests
- market_observer 自动追加 enriched context block

---

## Operator Layer Track B — 累积实现摘要 (2026-06-11, 50+ 增量)

> 以下是 2026-06-11 期间在 `operator-shell/` + `aletheia/agent_shell/` + `aletheia/server/routes/operator.py`
> 上完成的大批量增量工作的压缩摘要。逐条原始记录已归档;详见 git log 该日期范围。

**Python truth layer**:
- `/api/operator/internal/context/query` (分级安全 context)
- `/api/operator/internal/commands/execute` (typed command loopback)
- `/api/operator/internal/decisions` + `/repair-decisions` + `/command-intents`
- `/api/operator/extensions` (inspect/enable)
- `/api/risk/summary` (boundary change detection)
- `/api/strategies/summary` (promotion/evaluation detection)
- `/api/reviews/summary` + `/api/dreaming/summary`
- Session-kind child sessions (diagnostic/dreaming/strategy/review/command)
- Command audit persistence + inspectable working-memory files (JSONL/Markdown)

**Node Operator Shell**:
- `OperatorLoop` with 3-tier cadence (10s/30s/5min)
- `RecommendationEngine`: 15+ recommendation types
- `TaskRuntime`: priority queue, in-flight task re-entry, permission-aware materialization
- `RepairEngine`: proposal-only decisions (9 specialist paths)
- `SessionRuntime`: budgeted recovery folding, active branch persistence, parent trace inheritance
- Heartbeat mode state machine (Observing/Trading/Reviewing/Dreaming/Repairing/Degraded)
- Extension sandbox: draft → test → propose → enable → slash dispatch

**Command Center frontend**:
- SSE-first event feed
- Authoritative command preview (Ready/Blocked/Query/Forbidden)
- Structured trace/result cards
- Context inspector
- Session tree with nested branch create/select
- Cross-session command search
- Enabled extensions panel

**Tests**: 154 Python operator tests + 80 Node tests; builds pass.

---

## Historical Changes (2026-06-13 and earlier)

### 2026-06-13 — Gateway 静态文件启动可靠性 (db2423f)
- preflight.py 启动预检 + lifespan mount + doctor webui.static 诊断

### 2026-06-13 — DX 收口 (18 commits)
- Single-port :8787 dev (vite build --watch + uvicorn --reload)
- LLM startup gate 3次重试 + Windows toast
- Native Anthropic + OpenAI provider (SSE 流式)
- symbol_list 双模式过滤
- Clustr100 社区知识库

### 2026-06-13 — Persistence/Sync/Recovery (1075c8e)
- 18 新模块: EventStore, StateStore, ArtifactStore, GatewayFSM, Reconciliation, Recovery, Checkpoints, Export/Import, Backup/Restore, Learning/Policies
- 15 张 `ps_*` 前缀表(additive overlay)

### 2026-06-12 — 账户级仓位发现 + 纳管 (P1-P2)
- position_reconciler.classify_positions() + adopt_position()
- source 列(native/adopted) + 诚实合成链
- trust §1.1 修订

### 2026-06-21 — PI Agent 信号增强 + Liquidation 修复 + Operator ICT 研究 (ce0360d)
- Task A: UniverseFeatureSnapshot 新增 4 列 (taker_buy_ratio/vol_ratio/relative_strength_24h/oi_price_divergence)
  features.py 写入; trading_context.py Layer 1 读取+crowding_signal; trading_decision.py prompt 增强+_SIGNAL_RULES
- Task B: derivatives.py 日志提升 debug→warning; 时间窗口 1h→4h; "0 in window" 诊断; scheduler persist 计数日志
- Task C: operator_loop.py trigger_strategy_research action (每日限频1次); research_worker.py _ICT_METHODOLOGY_SYSTEM +
  mode="methodology" 分支; trading_context.py Layer 3 注入 serenity_opportunities; trading_decision.py 渲染 Research-sourced 小节

### 2026-06-10 — CLI 分层 + 运营命令软弃用
- bootstrap 内核(gateway/doctor/version)保留
- 运营命令(permission/model/risk/strategy)指向 Web UI

### 2026-06-08 — 多轮基础设施修复
- Gateway ghost-socket 根治 (a90ea84)
- Markets K线动态加载 (scroll-back + live tail)
- Deep Research → ROADMAP v1
- Market context layer (multi-TF indicators → LLM)
- Exchange account sync
- Experience loop (FTS5 retrieval → agent prompts)

### 2026-06-07 — WebUI 结构性重设计
- 14→10 pages (5-phase IA)
- CausalChain inspector
- Strategy↔trade causal chain fix (4 places)
- §10 re-audit (8 false-positive fixes)

### 2026-06-06 — 项目初始化
- CLAUDE.md + docs/_ai/ 全套
- Backend 80+ fixes + Frontend 24 fixes (§1-§3)

---

## Commands to Run

```bash
uv sync
uv run pytest -q                              # 1295+ tests
uv run aletheia gateway start --no-browser    # 启动闭环
uv run aletheia doctor                        # 取真实事实
```

## Warnings

- Live trading is disabled by default. Never enable without explicit user confirmation.
- Trust boundary modules (risk/, execution/, auth.py, secrets.py) require user authorization.
- Frontend must be rebuilt after source changes: `cd frontend && npm run build`
- After backend .py changes: `uv run aletheia gateway restart --no-browser`
