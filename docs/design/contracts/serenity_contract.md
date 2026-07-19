# Serenity Lens Subsystem Contract

> 契约级别: L2 — 规范性文档,实现依据。
> 改动信任不变量前必须 change control (see trust_and_change_control.md §6)。
> Source files:
> - `aletheia/trading/serenity_evidence.py` (确定性证据评估器 D2)
> - `aletheia/trading/serenity_scoring.py` (评分卡 + bucket)
> - `aletheia/agents/research_worker.py` (多镜头研究 pipeline + 符号过滤 + 晋升)
> - 决策/执行/持仓层的 trade_school 穿透 (见 §8–§10)
> Cross-references: decision_lifecycle_contract.md · knowledge_contract.md · risk_engine_contract.md · live_unlock_contract.md · data_model.md
> Last updated: 2026-07-02

---

## §1 什么是 Serenity Lens

Serenity Lens 是 Aletheia 的**深度研究镜头**:对一个标的做多视角研究、给出结构化裁决与证据质量分级,产出可被 System Operator 消费的 `SerenityOpportunity`。它是一条横跨全栈的纵切,而非单个模块:

```
标的 → 多镜头研究(4 lens)   [research_worker.py]
     → 证据质量评估(D2)      [serenity_evidence.py]
     → 可研究性分级(A/B/C)   [serenity_evidence.py]
     → 强制裁决(verdict)     [research_worker Stage 4]
     → 评分卡 + bucket       [serenity_scoring.py]
     → 归因(signal_source)   [trading_context / trading_decision]
     → 晋升到 PI 队列         [routes/data.py + research_worker auto_promote]
     → 持有周期判别(school)   [position_manager / execution 全链]
     → 关仓复盘(PostMortem)   [pnl.py / trade_attribution.py]
```

**目标态声明**: 本契约描述当前**已实现**的 Serenity 子系统。9 个 `spec_serenity_*.py` 是其可执行验证(见 §12),本文档是它们的统一规范入口——此前 Serenity 断言分散在 9 个 logical contract 中,无统一 L2 文档 (EVO 评估报告 L2 gap)。

---

## §2 核心信任不变量

> 这是本契约最关键的部分。以下不变量是 Serenity 存在的理由,改动任何一条须走 change control。

1. **生成者 ≠ 评估者 (D2, owner 决定 2026-06-27)**: 提出证据 tier 的 LLM 是*生成者*,其自评仅作 advisory proposal(存 `evidence_tier`/`tier`);*门控* scoring/promotion 的权威 tier 由独立确定性评估器 `serenity_evidence.py` 赋予(存 `assessed_tier`)。**评估器只会降低,永不升高**生成者的自评。这是打破"自产自评闭环"的根断言。

2. **评估器纯确定性**: `serenity_evidence.py` 无 LLM / DB / I/O,规则完全单元可测。同一输入永远同一输出。

3. **来源天花板不可绕过**: 见 §3.2。`prediction_market` / 各 methodology / `price_action` / `llm` / `model` 来源无论自评多高,硬顶 `speculative`。

4. **信息真空封顶**: 可研究性 C 级(投机/稀薄证据)把最终分数封顶 ≤40,低于 `research_priority` 阈值 52,阻止信息真空进入 PI 可执行队列 (§4.2)。

5. **加密-only 符号过滤**: LLM 幻觉出的非加密品种(股票/forex/聚合体)在持久化前被 `_is_tradeable_crypto_symbol()` 拒绝,不得进入 PI 交易上下文 (§10)。历史事故: LLM 曾生成 MSTR/SPY/GBP/USD 写入 serenity_opps。

6. **学习/持有判别不得触碰信任边界**: `trade_school` 判别器穿透执行层(§8),但**对实盘解锁 surface 和风控 guard 完全不可见**(§9)——`live.py` 与 `risk/engine.py` 的实盘门控代码里不得出现 `trade_school`。这是 Serenity 长周期特性与 trust boundary 的隔离墙。

7. **归因无 silent None**: Serenity pick 必须回填可追溯的 `opportunity_id` + `signal_source='serenity'`,不得静默 None (§6)。

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

Stage 4 **强制裁决** (S4 verdict): `_OPPORTUNITIES_SYSTEM` 强制 LLM 输出结构化 `verdict_json`(`recommendation` ∈ long/short/watch/avoid + `bull_case` + `bear_case` + `key_assumption`),消除两边倒措辞 (SV-S02/SV-B01)。裁决穿透到 `TradingDecision.bear_case` / `key_assumptions` 字段与 PI decision schema (SV-S03/S04/B02),并在 trading_context Layer 3 暴露给 PI (SV-C01)。

生成端**必须**路由过评估器: `research_worker` 与 `prediction_observer` 都调 `assess_evidence_tier` / `assess_opportunity_tier`(SE-S02/S03),否则证据仍是自评。

---

## §6 归因链 signal_source (attribution)

Serenity pick 必须可追溯到来源,防止 opportunity_id 静默 None(历史根因 trading_decision.py:250)。

- `PiDecision.signal_source` 默认 `'market_observer'`,additive 迁移保证旧库有列 (SA-S01/S03)。
- `_attach_opportunity_context` 回填规则: 仅匹配 Serenity → `opportunity_id=serenity_id` + `signal_source='serenity'`(无 silent None,SA-I01);**同时匹配 market_observer 与 Serenity 时 market_observer 优先**(SA-I02)。
- pending_signal 按 symbol+direction 直配优先于可能被 limit=5 截断的 layer3(SA-I03/S05);`record_decision` 持久化 `signal_source` 到 `pi_decisions`(SA-C01)。
- exact Serenity pending carrier 必须按 `opportunity_id` 独立 hydrate thesis、score、
  assessed tier、researchability 与 evidence IDs；全局 active top-five 只是展示窗口，
  不能决定待判断候选是否拥有研究事实。证据身份进入 material fingerprint，补证后
  必须重新开放 defer 判断。

---

## §7 晋升链 promote → PI 队列 (Option A, owner 定 2026-06-27)

**不再写死 draft StrategyRecord**(与 Position C 呼应)。`promote_serenity_theme`(routes/data.py)函数体**不含** `StrategyRecord` / `state='draft'`(SP-S02);改为向 PI 队列写 `signal_pending` 的 `PiDecision`(`signal_source='serenity'`,`action_intent=pi_evaluate`)并 `notify_pi_daemon`(SP-S03)。自动晋升只认达到门槛的 child；同 theme 仍有 active child 未持有 exact carrier 时 theme 保持 active，后续研究/补证可重试完整 active sibling 集且不得重复 carrier。所有 active child 都有 carrier 后才标 `promoted`。新增 evidence 与 active sibling 的 assessed tier/researchability 重评必须在同一短事务；终态 sibling 不改写。

自动晋升的当前 score、risk gates 与 threshold 必须在创建 carrier 的同一
`BEGIN IMMEDIATE` 内重验；手工晋升不应用 score 门槛。一个 exact、唯一的 final
`trade`/`skip` 与 pending carrier 同样构成已决载体，即使 transient pending 已被
retention 删除也不得重新入队。晋升计数和 wake 只统计本次新建 carrier；幂等重试为 0。

System Operator 的 exact terminal Serenity `skip` 与 final PiDecision/filter evidence
同事务推进 source `active→reviewed`，避免已消费 carrier 继续显示 active。`trade` 不在
该处终结，仍由真实持仓关闭路径推进 `traded_closed`。

**D5 自动晋升开关**: `SerenityConfig.auto_promote` 默认 **False**,`min_score_for_pi ∈ [0,100]`;经 `/api/settings/serenity` 暴露 + bounds-check(SP-S01/S04/B03),`api_reference.md` 登记该路由(SP-S05)。auto_promote 打开时 research_worker 用 `score_serenity_opportunity` 打分后自动入队,审计标签 `'serenity_auto_promote'`(SP-S06)。

---

## §8 持有周期判别 trade_school (holding)

Serenity 是**长周期**策略,不能被短线机制误杀。判别符 `trade_school` 从决策层穿透到仓位层:

- `TradeProposal.trade_school` 与 `Position.trade_school` 默认 `"short_term"`,遗留记录不会意外采用 serenity 行为(SH-S01);additive 迁移双表带 `DEFAULT 'short_term'`(SH-S02)。
- `SerenityConfig.stop_loss_pct`(默认 8.0)+ `max_hold_days`(默认 90),前端 Settings 有控件(SH-S03/S07)。
- `submit_decision_as_proposal` 由 `signal_source` 派生 `trade_school`,serenity 用长周期止损(SH-S04);穿透 paper_tools/gateway/4 broker/reconciler 全链(SH-S05)。
- `position_manager`: serenity 仓位**不受 72h 短期上限**(`_decide_exit` 100h 仍返回 None,SH-B01)、**trailing stop 是 no-op**(SH-B02);short_term 回归——72h cap 仍触发(SH-B03)。serenity 走 `thesis_invalidated` 退出(读 `get_serenity_opportunity`,由 `trade_school=='serenity'` 守卫,SH-S08)。
- 关仓自动复盘: `pnl._close_serenity_feedback` 写 `SerenityPostMortem`(SH-S09);`trade_attribution` 在标准 join 落空时回落 serenity opportunity 查询(SH-S10)。

---

## §9 实盘准备与信任边界隔离 (live_readiness)

> 本节是不变量 #6 的可执行锁。**Serenity 的持有特性绝不能成为绕过实盘门控的通道。**

对称性(paper↔live 必须一致):
- 三个 live broker(bitget/binance/okx)的 `execute_live_order` 签名带 `trade_school: str = 'short_term'`(LR-S01),`Position(...)` 构造赋值 `trade_school`(LR-S03);`gateway.submit_proposal` 在 demo/testnet 与 live_limited 三条 dispatch 路径都传 `proposal.trade_school`(LR-S02)。

隔离墙(信任边界不被 school 触及):
- **`server/routes/live.py` 不含 `trade_school`**,仍要求 `confirm=true` + 保留 `"live_disabled"`(LR-S04)。
- **`risk/engine.py` 的 live-disabled guard 不引用 `trade_school`**,仍枚举三种 `*_live_limited` 模式(LR-S05)。
- `LiveConfig.state` 默认 `'live_disabled'`,`first_live_confirmation_done` 默认 False,自主解锁不可能(LR-S06)。

---

## §10 股票代币执行 (xstock)

Serenity 直接产出的股票代币(xStocks)机会使用独立资产类别 `equity_tradeable`,与 equity_observer 的观察专用 `equity` 互不干扰:
- research_worker 对 stock token 打 `equity_tradeable` + `is_stock_token`(SX-S01);trading_context 允许 `equity_tradeable` 执行(不放进 `execution_eligible=False`),但仍屏蔽纯 `equity`(SX-S02)。
- `equity_observer` 不含 `equity_tradeable`,保持观察-only,OBS-S04 不被破坏(SX-S03)。
- 加密-only 过滤(§2 不变量 #5,SE-S07/I07): `_is_tradeable_crypto_symbol`(= research_worker 的 `_is_allowed_serenity_symbol` 别名) + `_ALLOWED_QUOTE_CURRENCIES` 在持久化前拒绝 MSTR/USD、SPY、GBP/USD、USDT/INR,放行 BTC/USDT、ETH/BTC 等。

---

## §11 数据模型 (data_model.md 交叉引用)

| 表/模型 | Serenity 相关字段 | spec |
|---------|------------------|------|
| `SerenityOpportunity` | `assessed_tier` / `researchability_grade` / `verdict_json` | SE/SR-S02/SV-S01 |
| `SerenityEvidence` | `agent_lens` | ML-S02 |
| `PiDecision` | `signal_source`(默认 market_observer) | SA-S01/S03 |
| `TradeProposal` / `Position` | `trade_school`(默认 short_term) | SH-S01/S02 |
| `SerenityConfig`(schema.py) | `stop_loss_pct` / `max_hold_days` / `auto_promote` / `min_score_for_pi` | SH-S03/SP-S01 |

---

## §12 Spec 映射表 (可执行验证)

9 个 logical contract,约 73 个 claim。所有断言的可执行验证在下列文件:

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

> 运行: `uv run pytest tests/test_spec_conformance.py -q`(spec_*.py 不匹配 pytest 默认 python_files,须经 conformance 硬编码导入;直接 `pytest tests/specs/spec_serenity_*.py` 亦可点名收集)。

---

## §13 已知限制

- **L1 评分权重硬编码**: `tier_score_map` / `stage_score_map` / `avg_score_map` / `_RESEARCHABILITY_CEILING` 写死在 `serenity_scoring.py`,非 config-driven。注意区分: Serenity 的**行为参数**(止损/持有天数/自动晋升/PI 阈值)**已** config 化 + 前端控件(§8/§7);硬编码的仅是**内部评分映射**这一实现细节。MVP 阶段不暴露 (owner 待定)。
- **评估器无历史学习**: 评估器是静态规则,不随交易结果自适应(设计如此,保确定性)。

---

## §14 与其他契约的关系

| 契约 | 关系 |
|------|------|
| decision_lifecycle_contract.md | Serenity opportunity → PiDecision(signal_pending)→ TradeProposal 是其一条 signal_source 分支 |
| knowledge_contract.md | Serenity 证据/裁决经 Layer 3 注入 System Operator prompt |
| risk_engine_contract.md | trade_school 对风控 guard 不可见(§9 隔离墙) |
| live_unlock_contract.md | trade_school 对实盘解锁 surface 不可见(§9 隔离墙) |
| data_model.md | SerenityOpportunity / SerenityEvidence / trade_school 列 |
| promotion_gates_contract.md | Serenity 走 PI 队列晋升(Option A),不走已 LEGACY 的 strategy_lifecycle |
