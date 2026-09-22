# Serenity Lens Subsystem Contract

> 契约级别: L2 — 规范性文档,实现依据。
> 改动信任不变量前必须 change control (see trust_and_change_control.md §6)。
> Source files:
> - `aletheia/trading/serenity_evidence.py` (确定性证据评估器 D2)
> - `aletheia/trading/serenity_scoring.py` (评分卡 + bucket)
> - `extensions/research/research_worker.py` (可选多镜头研究 pipeline + 符号过滤 + 委托晋升)
> - 决策/执行/持仓层的 trade_school 穿透 (见 §8–§10)
> Cross-references: decision_lifecycle_contract.md · knowledge_contract.md · risk_engine_contract.md · live_unlock_contract.md · data_model.md
> Last updated: 2026-09-07 (D2 canonical Candidate entry)

---

## §1 什么是 Serenity Lens

Serenity Lens 是 Aletheia 的**深度研究镜头**:对一个标的做多视角研究、给出结构化裁决与证据质量分级,产出可经真实 PA 准入发布为 durable Candidate 的 `SerenityOpportunity`。它是一条横跨全栈的纵切,而非单个模块:

```
标的 → 多镜头研究(4 lens)   [research_worker.py]
     → 证据质量评估(D2)      [serenity_evidence.py]
     → 可研究性分级(A/B/C)   [serenity_evidence.py]
     → 强制裁决(verdict)     [research_worker Stage 4]
     → 评分卡 + bucket       [serenity_scoring.py]
     → 来源准入 + PA 证据     [publish_serenity_candidates]
     → Candidate / Generation → Planner → Compiler → EntryPlan
     → Plan execution/watcher → Risk → Gateway
     → 持有周期判别(school)   [position_manager / execution 全链]
     → 关仓复盘(PostMortem)   [pnl.py / trade_attribution.py]
```

**范围**: 本契约描述当前 Serenity 研究与 canonical 入场/持仓边界；§12 区分历史 spec 映射和当前验证。不据文档同步宣称已测试、部署或取得真实交易资格。

---

## §2 核心信任不变量

> 这是本契约最关键的部分。以下不变量是 Serenity 存在的理由,改动任何一条须走 change control。

1. **生成者 ≠ 评估者 (D2, owner 决定 2026-06-27)**: 提出证据 tier 的 LLM 是*生成者*,其自评仅作 advisory proposal(存 `evidence_tier`/`tier`);*门控* scoring/promotion 的权威 tier 由独立确定性评估器 `serenity_evidence.py` 赋予(存 `assessed_tier`)。**评估器只会降低,永不升高**生成者的自评。这是打破"自产自评闭环"的根断言。

2. **评估器纯确定性**: `serenity_evidence.py` 无 LLM / DB / I/O,规则完全单元可测。同一输入永远同一输出。

3. **来源天花板不可绕过**: 见 §3.2。`prediction_market` / 各 methodology / `price_action` / `llm` / `model` 来源无论自评多高,硬顶 `speculative`。

4. **信息真空封顶**: 可研究性 C 级(投机/稀薄证据)把最终分数封顶 ≤40,低于 `research_priority` 阈值 52,阻止信息真空通过自动晋升评分门 (§4.2)。

5. **加密-only 符号过滤**: LLM 幻觉出的非加密品种(股票/forex/聚合体)在持久化前被 `_is_tradeable_crypto_symbol()` 拒绝,不得据此取得 Candidate 入场权限 (§10)。历史事故: LLM 曾生成 MSTR/SPY/GBP/USD 写入 serenity_opps。

6. **学习/持有判别不得触碰信任边界**: `trade_school` 判别器穿透执行层(§8),但**对实盘解锁 surface 和风控 guard 完全不可见**(§9)——`live.py` 与 `risk/engine.py` 的实盘门控代码里不得出现 `trade_school`。这是 Serenity 长周期特性与 trust boundary 的隔离墙。

7. **归因无 silent None**: Serenity Plan/Proposal 必须携带可追溯的 source ID、`opportunity_id` 与 `trade_school='serenity'`,不得静默 None (§6)。

---

## §3 Tier 词汇与证据评估器 (serenity_evidence.py)

### 3.1 规范 tier 阶梯 (单一真相源)

```
speculative < likely < high_confidence < disclosed
```

`TIER_LADDER` 定义强度序;`normalize_tier()` 把旧词汇/自由文本映射到规范值,未知/空值 floor 到 `speculative`(安全下限)。旧词同义映射: `verified`→`disclosed`,`credible`→`high_confidence`,`medium`→`likely`,`low`/`weak`/`unsupported`→`speculative`。`serenity_scoring.tier_score_map` 镜像此阶梯,每级严格递增分值。

### 3.2 来源天花板 (双层)

**硬顶** `_SOURCE_CEILING`(永远 speculative,无论自评):
`prediction_market`, `ict_methodology`, `smc_methodology`, `price_action`, `llm`, `model`
> 理由: 加密情绪洗白 / 模型生成的交易剧本 / 权重里回忆出的叙事,均无一手来源。

**软顶** `SOURCE_RELIABILITY_TIER`(证据质量达标时该来源*能*支持的最高 tier):
- `news`/`social`/`sentiment`/`analyst`/`research` → `likely` (二手,可佐证不可证明)
- `market_data`/`exchange_data`/`on_chain` → `high_confidence` (客观可观测)
- `official`/`regulatory`/`sec`/`filing` → `disclosed` (一手披露)
- 未列来源: 不设上限(fall through)

### 3.3 外部引用要求

`_REQUIRES_EXTERNAL_REF = {disclosed, high_confidence}`: 声称这两档但无可验证外部定位符(URL/filing,`_has_external_ref` 认 `http(s)://`/`www.`/`sec.gov`/`ftp://`)→ 降级到 `likely`(over-claim 保护)。

### 3.4 三个评估函数 (SE-S01)

| 函数 | 职责 | 关键断言 |
|------|------|---------|
| `assess_evidence_tier(evidence)` | 单条证据的权威 tier | normalize → clamp 硬顶/软顶 → 无 ref 降级。返回 `(tier, assessor_id)`,`ASSESSOR_ID="deterministic_v1"` |
| `assess_opportunity_tier(opp, evidence)` | 机会的门控 tier | = 其 `supports=True` 证据中**最强的** assessed_tier;无支撑证据 → `speculative`(即使生成者自评 disclosed)。`opportunity` 参数**不被信任** |
| `assess_researchability(evidence)` | A/B/C 可研究性分级 | A: ≥2 条 high_confidence+URL;B: ≥3 条 ≥likely;C: 其余。返回 `(grade, reason)` |

---

## §4 评分卡与 bucket (serenity_scoring.py)

### 4.1 评分构成 `score_serenity_opportunity()`

```
raw = evidence_score          # tier_score_map[assessed_tier], disclosed=28 … speculative=10
    + stage_score             # stage_score_map, research=8 … candidate=26
    + average_score           # avg_score_map, none=2 … historical_mismatch=12
    + evidence_bonus          # min(10, 有效证据数 × 0.8)
    + confidence_component    # max(0, min(20, confidence/5)),缺省 8
    - fail_gates × 18         # 风险 fail 门重罚
    - warn_gates × 6          # 风险 warn 门软摩擦
    - risk_flag_count × 4     # 手动风险标记软摩擦
score = clamp(raw, 0, 100)
```

**证据权重门控在 `assessed_tier`(权威),不在 `evidence_tier`(自评)** — SE-C01。

### 4.2 可研究性天花板 + bucket

- `_RESEARCHABILITY_CEILING = {A:100, B:70, C:40}`,`score = min(score, ceiling)`。
- bucket 三档(0 fail gates 前提): `score≥72 → candidate_position`;`score≥52 → research_priority`;其余 → `watch`。
- **C 级封顶 40 < 52**,故信息真空永远进不了 `research_priority`(不变量 #4)。

### 4.3 可解释性

评分输出携带 `reasons`(权威 tier / 自评 tier / stage / raw / grade / ceiling 全留),并有 `make_replay_snapshot_payload()` 产出审计快照。

> **别名健壮性说明**: `_normalize_stage_for_score` / `_stage_index` / `stage_score_map` 三处各自维护 stage 别名,`stage_score_map` 已补齐所有别名 key(order/candidate/demand 等),`.get()` 命中不走 fallback。评分与阶段索引正确,别名分散是可维护性瑕疵非缺陷(EVO 评估报告 L3 已核实)。

---

## §5 多镜头研究 Pipeline (research_worker.py)

Stage 1 证据收集用**多个 LLM 视角**并行代替单次调用 (S1 multi-lens):
- Lens 系统提示常量: `_LENS_TECHNICAL_SYSTEM` / `_LENS_FLOW_SYSTEM` / `_LENS_SENTIMENT_SYSTEM`(≥3),遍历 `RESEARCH_LENSES` 逐 lens 调 `llm_complete` (ML-S01/ML-B01)。
- 每条证据记 `agent_lens` 归属其视角 (ML-S02)。
- 多 lens 收集完 emit `event_type='lens_synthesis'` 合成事件 (ML-S03)。
- **methodology 旁路**: `_ICT_METHODOLOGY_SYSTEM` 模式不走多 lens (ML-B02);且该来源经 §3.2 硬顶为 speculative。

Stage 4 **强制裁决** (S4 verdict): `_OPPORTUNITIES_SYSTEM` 强制 LLM 输出结构化 `verdict_json`(`recommendation` ∈ long/short/watch/avoid + `bull_case` + `bear_case` + `key_assumption`),消除两边倒措辞 (SV-S02/SV-B01)。裁决是研究证据，不是可执行 TradingDecision；source/hash 冻结后仍须由共享真实 PA evaluator 与 Entry Planner/Compiler 形成入场计划。历史 SV-S03/S04/B02/C01 对旧 PI context 的断言不再是当前入场权限。

生成端**必须**路由过评估器: `research_worker` 与 `prediction_observer` 都调 `assess_evidence_tier` / `assess_opportunity_tier`(SE-S02/S03),否则证据仍是自评。

### 5.1 Research run lifecycle truth

Worker 由既有 CLI/Web composition 绑定到 Core Scheduler 的同步 enqueue callback。
缺失时，create/retry/deep-dive API 在 profile、snapshot、run、retry 首次写入前返回
unavailable（503）；cron 与 startup catch-up 不调度缺失 producer。历史读取、取消、
启动 interrupted-run recovery 及已有证据的 Core publication 不要求 producer 可用。
安装时仍使用原有后台线程、CAS、取消 checkpoint、锁和 durable retry identity；
不新增第二个 scheduler、publisher 或 execution writer。

`serenity_research_runs` 是研究 attempt 生命周期权威。Worker 用 SQLite
compare-and-set 将 `queued→running`；因此 API 先赢得 queued cancel 时可以直接写
terminal `cancelled`，迟到的 worker 不能重新激活该 run。

Running cancel 是协作请求，不是已停止声明：

| 当前状态 | 事件 | 下一状态 | `finished_at` |
|---|---|---|---|
| `queued` | cancel 在 worker claim 前成功 | `cancelled` | 立即写入 |
| `running` | owner 请求 cancel | `cancel_requested` | 保持为空 |
| `cancel_requested` | worker 到达 checkpoint | `cancelled` | worker 确认时写入 |
| `cancel_requested` | gateway restart，旧 worker 已消失 | `cancelled` | startup reconcile 写入 |
| `running` | pipeline 正常终结 | 原有 terminal outcome | guarded write 写入 |

`cancel_requested` 继续属于 active run，占用 Serenity 单一运行槽。完成、失败与
rate-limit 终态只允许 `WHERE status='running'` 的 CAS；并发 cancel 赢得后，迟到的
worker 终态不能覆盖请求。重复 cancel 请求对 `cancel_requested/cancelled` 幂等。

取消只在 stage/lens/provider 调用之间的已有 checkpoint 生效；不会声称能强杀 Python
thread 或中断正在等待的 provider 请求。取消请求后，worker 不再进入 auto-promote，
但 checkpoint 之前已提交的部分研究 artifact 继续保留并可审计。

### 5.2 Durable progress facts

Run ID 同时是 attempt 与 progress generation。当前 `stage`、`updated_at` 和
`serenity_run_events` 提供 durable progress；status、list 与 detail API 暴露
`stage` 和 `lastProgressAt`。系统不输出 overall/stage percentage，因为各 stage
工作量没有可靠 denominator，伪百分比不是当前保证。

---

## §6 不可变来源与归因

Candidate context 出生时包含真实 `source.kind='serenity'`、`source.id=SerenityOpportunity.id`、
`source.trade_school='serenity'`，连同 source cutoff、evidence/material hash 一起冻结。
Universe 使用其真实 member ID 与 short_term；不复制一份来源注册表。Generation 续租/复用
不改写出生 context。Compiler 冻结 Plan 的 source_kind/source_id/trade_school，
`build_entry_plan_order_intent` 将其写入 Proposal attribution，同时显式写
`opportunity_id=plan.source_id` 与 trade_school；Position/thesis/close feedback 保持研究归因。
不按 symbol 匹配 pending，不再通过 PiDecision 或 `_attach_opportunity_context` 授权。

---

## §7 晋升链 promote → durable Candidate

手工 `routes/data.py:promote_serenity_theme` 与自动
`research_worker._auto_promote_serenity_opportunities` 使用同一
`trading.serenity_publication.publish_serenity_candidates(theme_id, opportunity_ids, *, min_score_for_pi=None,
new_candidates_only=False) -> list[str]`。返回按请求去重顺序的 durable Candidate IDs；
`new_candidates_only=True` 只计新建。空请求拒绝；同 source+material 幂等复用，不再叫 pending。
此来源编排不再定义于通用 repository，也不保留 repository 转发别名。Snapshot/member
创建经 Core `publish_candidate_snapshot`，与 Universe 共用唯一持久化入口；来源评分、
完整证据 hash、PA 与 theme 状态仍由本编排管理，不能把来源差异搬进 Core writer。

发布前在 writer 事务外准备研究证据、exact registry instrument、当前真实 feature/行情、
共享 PA evaluator 与 `prepare_candidate_requalification`。研究方向不得冒充机制或反转 PA；
无 compatible Profile/能力证据为 unavailable，方向冲突为 serenity_pa_direction_conflict。
`serenity_research_v1` / `research_authority` 只证明来源准入，不冒充 Universe Utility。
不执行整次 Universe refresh，也不在发布函数内调用模型形成 Proposal。

短事务重验 source material hash 和全局 exact instrument owner，原子写真实
source-scoped snapshot、member 与 current PlanningGeneration，然后才置 theme promoted。
缺来源/证据、hash 改变、exact instrument 不明、不 eligible 或跨来源 instrument_owned
导致全批回滚，不留下半成品晋升。Universe refresh/reconcile 只处理 Universe 来源；
两个来源仍共用一个 exact venue/symbol/market_type owner，全球 attention TTL 继续适用。

自动晋升保留 `auto_promote`（默认 False）与 `min_score_for_pi` 评分门；低于门槛的 child
不发布。手工晋升不应用该分数门，但同样必须通过真实证据、风险 gate 与 PA 准入。
当前 writer 有发布结果才标记 theme promoted；不能把 theme 状态解释成每个 child 都已获交易权。
晋升只唤醒 canonical Planner；不可用/idle 不回到旧 PI evaluator、Ranker 或 signal_pending。

**历史（退役 Option A）**：曾经的 promote→PiDecision(signal_pending)→PI queue、
exact pending hydrate、final trade/skip carrier 去重与 orphan claim 恢复，仅解释旧审计。
这些入口现在不执行、不生成新提案。旧研究待处理项须凭新鲜真实证据重新准入，不能继承
pending 作为批准；已成交持仓和未知订单仍由现有 Gateway/持仓 owner 收口。

---

## §8 持有周期判别 trade_school (holding)

Serenity 是**长周期**策略,不能被短线机制误杀。判别符 `trade_school` 从 Candidate/Plan/Proposal 穿透到仓位层:

- `TradeProposal.trade_school` 与 `Position.trade_school` 默认 `"short_term"`,遗留记录不会意外采用 serenity 行为(SH-S01);additive 迁移双表带 `DEFAULT 'short_term'`(SH-S02)。
- `SerenityConfig.max_hold_days`（默认90）保留持仓上限；旧 stop_loss_pct 设置/API/UI 已退役，磁盘旧字段经配置迁移剥离。
- Compiler 冻结正有限 `holding_horizon_hours=cfg.serenity.max_hold_days*24`；Proposal.expected_horizon_hours 接收 holding。Plan.expected_horizon_hours 仍是 4h/24h 择时（≤48h），expires_at 仍为 60/240 分钟入场有效期；真实 maker Order 另有 60 秒 lifetime。
- max_hold_days=90 时，SWING 择时24h、entry expiry4h、持仓2160h互不替代。PA invalidation 不是持仓周期届满。short_term holding 仍复制其 timing horizon。
- 最终真实结构 stop 保留，Compiler 基于最终 stop 重新约束风险仓位；不再存在 Serenity 固定百分比入场 stop 设置，不能覆盖所选有效结构。
- `position_manager`: serenity 仓位**不受 72h 短期上限**(`_decide_exit` 100h 仍返回 None,SH-B01)、**trailing stop 是 no-op**(SH-B02);short_term 回归——72h cap 仍触发(SH-B03)。serenity 走 `thesis_invalidated` 退出(读 `get_serenity_opportunity`,由 `trade_school=='serenity'` 守卫,SH-S08)。
- 关仓自动复盘: Core `portfolio.outcome_hooks` 持有资格检查、持久化投递任务、租约与重试；既有 Web/CLI 启动入口绑定可选 `learning.outcome_feedback.execute_outcome_feedback`，其中 `_close_serenity_feedback` 写 `SerenityPostMortem`(SH-S09)。组件缺失时任务失败且可重试，不改写已确认的关仓与经济事实；`trade_attribution` 在标准 join 落空时回落 serenity opportunity 查询(SH-S10)。

---

## §9 实盘准备与信任边界隔离 (live_readiness)

> 本节是不变量 #6 的可执行锁。**Serenity 的持有特性绝不能成为绕过实盘门控的通道。**

Proposal→Gateway→Position/reconciliation 必须保留 school 与 source，不能因执行 route 改变持有语义。
本入场切片不宣称已完成六种外部 mode 的 UTA 唯一私有账户切换；那是批准的后续部分，
读取、mutation 资格与实盘解锁仍需各自真实证据。

隔离墙(信任边界不被 school 触及):
- **`server/routes/live.py` 不含 `trade_school`**,仍要求 `confirm=true` + 保留 `"live_disabled"`(LR-S04)。
- **`risk/engine.py` 的 live-disabled guard 不引用 `trade_school`**,仍枚举三种 `*_live_limited` 模式(LR-S05)。
- `LiveConfig.state` 默认 `'live_disabled'`,`first_live_confirmation_done` 默认 False,自主解锁不可能(LR-S06)。

---

## §10 股票代币执行 (xstock)

Serenity 直接产出的股票代币(xStocks)机会使用独立资产类别 `equity_tradeable`,与 equity_observer 的观察专用 `equity` 互不干扰:
- research_worker 的 stock-token 研究分类不是执行批准；只有已登记的 exact venue/symbol/market_type 与受支持 instrument context、真实 PA/eligibility 门可发布 Candidate。纯股票 ticker 或预测问题文本不能伪造成 instrument。
- `equity_observer` 不含 `equity_tradeable`,保持观察-only,OBS-S04 不被破坏(SX-S03)。
- 加密-only 过滤(§2 不变量 #5,SE-S07/I07): `_is_tradeable_crypto_symbol`(= research_worker 的 `_is_allowed_serenity_symbol` 别名) + `_ALLOWED_QUOTE_CURRENCIES` 在持久化前拒绝 MSTR/USD、SPY、GBP/USD、USDT/INR,放行 BTC/USDT、ETH/BTC 等。

---

## §11 数据模型 (data_model.md 交叉引用)

| 表/模型 | Serenity 相关字段 | spec |
|---------|------------------|------|
| `SerenityOpportunity` | `assessed_tier` / `researchability_grade` / `verdict_json` | SE/SR-S02/SV-S01 |
| `SerenityEvidence` | `agent_lens` | ML-S02 |
| Candidate context / EntryPlan | immutable source kind/id/school；Plan holding_horizon_hours | 当前 entry-plan-contract |
| `PiDecision`（历史） | 旧 signal_source，非当前入场事实 | 历史 SA-S01/S03 |
| `TradeProposal` / `Position` | `trade_school`(默认 short_term) | SH-S01/S02 |
| `SerenityConfig`(schema.py) | `max_hold_days` / `auto_promote` / `min_score_for_pi` | SH-S03/SP-S01 |

---

## §12 历史 Spec 映射与当前验证

下表保留旧版9个 logical contract 的定位历史，不宣称这些旧文件或 claim 仍存在/通过。
promote→PI 队列、TradingDecision 和 broker 源码拼写断言已不代表当前入场契约：

| Spec 文件 | 契约名 | 覆盖 | claim |
|-----------|--------|------|-------|
| `spec_serenity_evidence.py` | serenity_evidence_quality | D2 分离 / tier 阶梯 / 来源天花板 / 外部引用 / 符号过滤 | SE-S01~S07, SE-I01~I07, SE-C01~C02 |
| `spec_serenity_researchability.py` | serenity_researchability | A/B/C 分级 + C 级封顶 40 | SR-S01~S03, SR-B01~B02 |
| `spec_serenity_verdict.py` | serenity_verdict | 强制 verdict_json / bear_case / key_assumptions | SV-S01~S04, SV-B01~B02, SV-C01 |
| `spec_serenity_multi_lens.py` | serenity_multi_lens | 4 镜头研究 + 合成事件 + methodology 旁路 | ML-S01~S03, ML-B01~B02 |
| `spec_serenity_attribution.py` | serenity_attribution | signal_source 归因链无 silent None | SA-S01~S05, SA-I01~I03, SA-C01 |
| `spec_serenity_promote.py` | serenity_promote | promote→PI 队列 (Option A) + D5 开关 | SP-S01~S06, SP-B01~B03 |
| `spec_serenity_holding.py` | serenity_holding | trade_school 穿透 + 长周期不被 72h 杀 | SH-S01~S10, SH-B01~B03 |
| `spec_serenity_live_readiness.py` | serenity_live_readiness | live 对称 + 信任边界隔离 | LR-S01~S06 |
| `spec_serenity_xstock_execution.py` | serenity_xstock_execution | equity_tradeable 资产类别 | SX-S01~S03 |

Research attempt lifecycle 的聚焦行为由 `tests/test_serenity_workbench.py` 和
`tests/test_serenity_scheduler_continuity.py` 覆盖：queued/running cancel race、worker
acknowledgement、terminal CAS、durable progress surface 与 restart reconciliation。

当前 publication/holding 聚焦验证：`tests/test_serenity_routes.py`、`tests/test_serenity_manual_opportunity_evidence_gate.py`、`tests/test_serenity_p0_p3_e2e.py`、`tests/test_entry_plan_execution.py`。本次文档编辑没有运行这些测试。

---

## §13 已知限制

- **L1 评分权重硬编码**: `tier_score_map` / `stage_score_map` / `avg_score_map` / `_RESEARCHABILITY_CEILING` 写死在 `serenity_scoring.py`,非 config-driven。注意区分: Serenity 的**行为参数**(持有天数/自动晋升/自动评分阈值)**已** config 化 + 前端控件(§8/§7);硬编码的仅是**内部评分映射**这一实现细节。MVP 阶段不暴露 (owner 待定)。
- **评估器无历史学习**: 评估器是静态规则,不随交易结果自适应(设计如此,保确定性)。
- **取消是协作式**: 当前 provider 请求返回前不能释放 worker thread；若需要有界硬停，
  必须先把研究 worker 隔离到独立 process owner，不能把 thread timeout 当作物理终止。

---

## §14 与其他契约的关系

| 契约 | 关系 |
|------|------|
| decision_lifecycle_contract.md / entry-plan-contract.md | Serenity → durable Candidate/current Generation → Planner/Compiler/Plan → Risk/Gateway，与 Universe 同主线 |
| knowledge_contract.md | 研究证据保留；旧 Layer 3 入场注入仅是历史，不授权新入场 |
| risk_engine_contract.md | trade_school 对风控 guard 不可见(§9 隔离墙) |
| live_unlock_contract.md | trade_school 对实盘解锁 surface 不可见(§9 隔离墙) |
| data_model.md | SerenityOpportunity / SerenityEvidence / trade_school 列 |
| promotion_gates_contract.md | 历史 Option A 仅供追溯；当前 publication 见 §7，不走旧 PI 队列或 strategy_lifecycle |
