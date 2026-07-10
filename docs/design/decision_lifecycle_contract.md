# 决策生命周期契约 (Decision Lifecycle Contract, L2 金标准)

> **角色**:本契约**横切**所有交易子系统,定义一笔交易从机会到复盘的完整脊柱,
> 以及每个**接缝(seam)**的前置/后置条件、守卫位置、与中间载体的字段约定。
> 其他契约**竖切**单个子系统(风控/执行/agent/知识);本契约负责它们之间的交接。
> **不重复**子系统细节 —— 指向已有契约。
> **基线**:代码为底(file:line);冲突时文档为准。
> Last updated: 2026-07-10 · 维护者:Aletheia Dev · 上游:`docs/research/PLATFORM_DOC_DESIGN_REVIEW_2026-06-30.md`

---

## 1. 脊柱全景(开仓方向)

一笔开仓决策跨 10 跳,数据流方向:

```
跳0 采集    collectors/* → market_snapshots / funding / microstructure 等原始表
跳0.5 前置门 Universe ticker/tradability → 无方向完整批次证据（不伪造收益）
跳1 观察    market_observer.py:_analyze_symbol → LLM → Opportunity → insert_opportunity → notify_pi_daemon
跳2 排序    paper_trader_ranker(纯确定性打分,无LLM) → 前N候选写 PiDecision(action='signal_pending')
跳3 唤醒    pi_daemon._run_evaluation(防抖8s/冷却60s/300s兜底) → evaluate_trading_opportunity
跳4 组装    commands.py:evaluate_trading_opportunity → build_enriched_trading_context (3层Context)
跳5 定夺    trading_decision.py:evaluate → LLM → TradingDecision  ★唯一开仓决策权★
            conviction∈[40,80] 触发 bull/bear debate;conviction < 55 强制 action='skip'
跳6 提案    submit_decision_as_proposal → guard chain(advisory) → create_paper_proposal(白名单) → gateway.submit_proposal
跳7 风控    risk/engine.py:evaluate_proposal —— 唯一硬门(20关,见 risk_engine_contract)
跳8 执行    gateway → broker → Order → Fill → Position
跳9 复盘    平仓后 post_trade_reviewer → knowledge_items(post_trade_review) → 晋级门 + 学习闭环
旁路结果    universe member → 固定 horizon 的目标 instrument OHLCV → gross/directional/cost/net/MFE/MAE
            → 只把完整 net 结果送入 scanner 校准、family edge、missed-alpha 与 guard candidate
筛选旁路    Observer/Ranker/System Operator/Risk → filter_decisions
            → filter_candidate_outcomes（共享四个固定 horizon）→ 只读分层对照
```

> 固定持有期反事实结果遵循 `docs/design/opportunity_outcome_contract.md`。历史 gross-only 行、
> 永续缺 funding、方向未知或过晚 ticker 只能用于解释，不得进入自动学习。

> **两个控制环**(理解模型,见上游报告 §2):跳1–8 是**交易决策环**(OODA,分钟级);
> 系统监管环(MAPE-K,小时级,见 `operator_layer.md` + `PI_OPERATOR_DESIGN_DECISIONS.md`)与本脊柱
> **正交**,两者只在 `pi_decisions` 表交汇,靠 `action` 命名空间区分(见 `PI_OPERATOR_DESIGN_DECISIONS.md §问题2`)。
> 平仓/退出脊柱见 [`execution_contract.md §10`](execution_contract.md)。

---

## 2. 中间载体契约

跨子系统传递的数据结构,其字段语义在此固定(此前只活在代码里):

### 2.1 Opportunity (`storage/models.py:45`)

| 字段 | 类型 | 语义 / 值域 |
|------|------|------------|
| `market` | `str`(必填) | 机会的市场/场地描述符。跳5 当 PI 未显式选 `market_type` 时由 `_attach_opportunity_context` 作为继承来源(`trading_decision.py:909`)。⚠️ **已知歧义**:`market` 与下游期望的 `market_type` 枚举(`spot`/`usdt_futures`)值域是否一致**需核实并锁定**(报告 A1 design gap)。 |
| `direction_bias` | `str \| None` | `long` / `short` / `neutral`。跳2 `paper_trader_ranker` 直接读它定方向。 |
| `confidence` | `str` | **分类**置信度,默认 `speculative`(同 `VALID_CONFIDENCES`)。**不是**数值。 |
| `confidence_score` | `int \| None` | **数值**置信度(0–100),跳2 打分用。与 `confidence`(分类)是两个字段,勿混。 |
| `status` | `str` | 状态机:`open`(可被排序消费)→ `reviewed`(已评估,防重复)。仅 `open` 进跳2。 |
| `scanner_family` | `str \| None` | 来源扫描器族,跳5 透传进归因链(风控 #16 部门集中度解析用)。 |

### 2.2 TradingDecision (`agent_shell/trading_decision.py:56`,dataclass)

LLM 输出到 proposal 的**唯一中间载体**(跳5→6)。

| 字段 | 类型 | 语义 |
|------|------|------|
| `action` | `str`(必填) | `trade` / `skip` / `close` / `reduce`(dataclass 注释);提案路径只对 trade 类动作提交。 |
| `conviction` | `int`(必填) | 0–100。`< _MIN_CONVICTION_TO_TRADE(=55)` → 强制 `skip`(`trading_decision.py:49,202`)。 |
| `reasoning` | `str`(必填) | 决策理由(人类可读)。 |
| `market_type` | `str \| None` | PI 显式选;省略时继承 `opportunity.market`,再省则"空头→usdt_futures/其余→spot"兜底。 |
| `opportunity_id` / `strategy_id` / `scanner_family` | `str \| None` | R0 归因,由 `_attach_opportunity_context` 回填(`trading_decision.py:909`)。 |
| `signal_source` | `str` | 机会来源,默认 `market_observer`;`serenity` 表示解析 `serenity_opportunities.id`(`:85`)。值域 = {`market_observer`,`serenity`}。 |
| `bear_case` | `str` | S4 强制反方论证。新 LLM 决策 schema 必填；空值仅允许历史记录。 |

### 2.3 signal_pending 载体 (跳2→4 接缝)

`paper_trader` 写 `PiDecision(action='signal_pending')`(`paper_trader.py:143/235`);
`trading_context._build_layer2_short_term` 用 SQL 读回作为 PI 的 Layer2 `pending_signals`
(`trading_context.py:463-465`:`WHERE action='signal_pending' AND created_at >= ? ORDER BY created_at DESC LIMIT 5`)。
**不变量**:TTL 由 `created_at >= cutoff` 控制;只取最近 5 条。

### 2.4 Filter Evidence（评估旁路）

- `filter_decision_batches` 保存 ticker/tradability 等高频无方向门；必须保存本次扫描的全部候选，不得抽样。
- `filter_decisions` 保存已有方向的 Observer、Ranker、System Operator 和 Risk 逐关事实，到首个拒绝停止。
- `filter_candidate_outcomes` 按候选共享一份目标市场净结果，不按筛选关重复结算。
- Risk 证据必须在权威 `RiskDecision` 落库后追加；证据失败不改变原始批准/拒绝。

完整定义见 `docs/design/filter_evidence_contract.md`。

---

## 3. 接缝守卫表

| 接缝 | writer | consumer | 关键不变量 | 守卫/spec |
|------|--------|----------|-----------|-----------|
| 机会落库 | `market_observer.py:361` | ranker / Layer1 | 仅 `status='open'` 进排序;reviewed 不重复 | (本契约 DL-S01) |
| signal_pending | `paper_trader.py:143` | `trading_context.py:463` | TTL cutoff;LIMIT 5 | DL-S03 |
| 决策→提案 | `trading_decision.py:218` | `gateway.py` | `conviction≥55 且 action∈trade类` 才提案;白名单校验 | DL-C01 + execution_contract §3 |
| 提案→风控 | `gateway.py` | `risk/engine.py:67` | `_eval_lock` 串行;每提案恰评估一次;`approved=False` 不执行 | risk_engine_contract / spec_pipeline_risk_gate |
| 平仓脊柱 | — | — | 见 `execution_contract §10`(退出触发器优先级 / paused 不挡平仓) | PL-PM-01~07 |
| 筛选证据 | 各关原 writer | filter controller / doctor | 事前输入+参数版本；无方向不生成可学习净结果；Risk 只报告 | filter_evidence_contract / spec_filter_evidence |

---

## 4. 不变量

- **DL-INV-1 单一开仓决策权**:`trading_decision.evaluate`(LLM)是唯一开仓决策者;`paper_trader` 只收集信号(Phase 4a,System Operator 是唯一 proposal 提交者),不直接提交 proposal。
- **DL-INV-2 conviction 门**:`conviction < 55` 必 skip(`_MIN_CONVICTION_TO_TRADE`)。改此阈值须同步本契约 + DL-C01。
- **DL-INV-3 guard advisory**:跳6 的 guard chain 只写 `decision_meta['guards']`,**不硬拦**;唯一硬门是风控引擎(见 `execution_contract` guard 澄清段 + spec PI-S02)。
- **DL-INV-4 归因继承**:`_attach_opportunity_context` 必须按 `signal_source` 把 opportunity_id/strategy_id/scanner_family 回填到 decision,否则学习闭环断链(LEARNING_LOOP_AUDIT §R0)。
- **DL-INV-5 instrument 取价**:跳6 必须先确定最终 `market_type`,再按该类型读取 snapshot；同 symbol 的 spot/perp 行不得互相替代为首选价格。
- **DL-INV-6 强制反方论证**:新 TradingDecision 的 LLM schema 必须要求非空 `bear_case`,`_parse_decision` 必须透传到提案审计元数据；仅 dataclass 有字段不算实现。
- **DL-INV-7 筛选事实不反推**:放行/拒绝必须在当次评估时记录，不得从最终 opportunity/proposal 状态倒推中间关卡。
- **DL-INV-8 无方向不学收益**:无事前 `long/short` 时只衡量能力和覆盖，禁止事后选最赚方向。

---

## 5. Spec-as-Code

| claim | 级别 | 锁定 |
|-------|------|------|
| `DL-S01` | L2a | Opportunity 模型含 market/direction_bias/confidence/confidence_score/status/scanner_family |
| `DL-S02` | L2a | TradingDecision dataclass 含 action/conviction/reasoning/bear_case/market_type/opportunity_id/signal_source |
| `DL-S03` | L2b | trading_context.py 含 signal_pending 查询并填 pending_signals(跳2→4 接缝存在) |
| `DL-S04` | L2a | TradingDecision.signal_source 默认 `market_observer`(值域含 `serenity`) |
| `DL-S06` | L2a | 提案先解析最终 market_type，再把它传给 latest_snapshot 取价 |
| `DL-C01` | L2c | `_MIN_CONVICTION_TO_TRADE == 55`(改阈值即爆红,强制同步本契约) |

`tests/specs/spec_decision_lifecycle.py`。验证:`uv run pytest tests/specs/spec_decision_lifecycle.py -q`。
筛选证据的结构、行为和跨操作规则在 `tests/specs/spec_filter_evidence.py`。
