# 决策生命周期契约 (Decision Lifecycle Contract, L2 金标准)

> **角色**:本契约**横切**所有交易子系统,定义一笔交易从机会到复盘的完整脊柱,
> 以及每个**接缝(seam)**的前置/后置条件、守卫位置、与中间载体的字段约定。
> 其他契约**竖切**单个子系统(风控/执行/agent/知识);本契约负责它们之间的交接。
> **不重复**子系统细节 —— 指向已有契约。
> **基线**:代码为底(file:line);冲突时文档为准。
> Last updated: 2026-07-13 · 维护者:Aletheia Dev · 上游:`docs/research/PLATFORM_DOC_DESIGN_REVIEW_2026-06-30.md`

---

## 1. 脊柱全景(开仓方向)

一笔开仓决策跨 10 跳,数据流方向:

```
跳0 采集    collectors/* → market_snapshots / funding / microstructure 等原始表
跳0.5 前置门 Universe ticker/tradability → 无方向完整批次证据（不伪造收益）
跳1 观察    market_observer.py:_analyze_symbol → LLM → Opportunity → insert_opportunity
跳2 排序    paper_trader_ranker(纯确定性打分,无LLM) → 前N候选写 PiDecision(action='signal_pending') → notify_pi_daemon
跳3 唤醒    pi_daemon._run_evaluation(防抖8s/冷却60s/300s兜底) → evaluate_trading_opportunity
跳4 组装    commands.py:evaluate_trading_opportunity → build_enriched_trading_context (3层Context)
跳5 定夺    trading_decision.py:evaluate → LLM → TradingDecision  ★唯一开仓决策权★
            conviction∈[40,80] 触发 bull/bear debate;conviction < 55 强制 action='skip'
跳6 提案    submit_decision_as_proposal → 方向/取价门留下结构化结果 → guard chain(advisory)
            → create_paper_proposal(白名单) → gateway.submit_proposal
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
| `status` | `str` | 状态机:`open`(可被排序消费)→ `queued`(已写入待处理信号)→ `reviewed`(Operator 最终决定已落库);Filter 的永久拒绝也可从 `open` 直接进入 `reviewed`,TTL/待处理窗口超时则进入 `expired`。跳2 直接查询 `open` 工作集后再应用数量上限,避免大量非开放历史把仍开放的行挤出扫描窗口。 |
| `scanner_family` | `str \| None` | 来源扫描器族,跳5 透传进归因链(风控 #16 部门集中度解析用)。 |
| `valid_until` | `str \| None` | 机会自身的失效时间。即使关联 Universe member 缺失,跳2 仍须按此字段拒绝过期机会。 |

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
| `parameter_rationale` | `dict[str,str]` | 新开仓的五项 AI 理由：`entry_price` / `stop_loss` / `take_profit` / `notional_usdt` / `leverage`。仅 LLM 解析路径要求齐全；缺项在跳6留下 `missing_parameter_rationale`，不创建 proposal。最终入场价同时保存实际 `market_snapshot_id / market_type / snapshot_at`，不是只写“来自快照”；详见 `trade_decision_rationale_contract.md`。 |

### 2.2a PiDecision（耐久 Operator 事实）

`PiDecision` 对 pending 和最终 action 都保存 `symbol / direction / market_type /
opportunity_id / signal_source`。最终行另存 `proposal_id / proposal_status /
proposal_reason`：成功、跳过、失败都必须有耐久状态；异常只保存稳定码
`submission_exception`，不得保存 provider 异常文本。历史 NULL 保持未知，不事后猜测。

### 2.3 signal_pending 载体 (跳2→4 接缝)

`paper_trader` 写 `PiDecision(action='signal_pending')`(`paper_trader.py:143/235`);
`trading_context._build_pending_signal_queue` 读回尚无同 `signal_source + opportunity_id`
最终决定的行,先按 `symbol + direction` 去重,再按最早到达顺序向 PI 暴露 5 条。
`pending_queue` 同时返回真实未完成总数、可见数、overflow、最老时间和已超时未消费数。
**不变量**:市场扫描信号的 TTL 由 `created_at >= cutoff` 控制;超过 30 分钟仍无最终决定的 market opportunity
转为 `expired`,不再永久停在 queued。Serenity 是长周期来源,在没有对应过期状态迁移前不得套用这条 30 分钟截断。
`paper_trader` 只能以条件更新认领 `open→queued`;旧计划不得把 `reviewed/expired` 倒退为 queued。成功写入后先把来源 Opportunity 标记为
`queued`;只有 `reflection.claim_decision` 在最终 PiDecision 与状态更新的同一事务中把它改成
`reviewed`。命令路径在任何 proposal 副作用前以短事务写入 `proposal_status=submitting` 的最终意图 claim,
完成后原位更新 proposal 结果;并发/崩溃重放只能读回这条 claim,不得再次提交。
`PiDecision` 自身保存 `symbol / direction / market_type / opportunity_id / signal_source`;
`_attach_opportunity_context` 必须把 `pending_signals` 中 `market_observer` 与 `serenity` 两类载体
都视为有效归因来源,并按 `opportunity_id` 回填来源字段,不得只消费 Serenity 载体。对
`market_observer` 载体,队列中的精确编号优先于仅靠相同 symbol/direction 匹配到的另一条开放机会;
Serenity 与 Layer1 同时匹配时仍保持 Layer1 的既有优先级。

### 2.4 Filter Evidence（评估旁路）

- `filter_decision_batches` 保存 ticker/tradability 等高频无方向门；必须保存本次扫描的全部候选，不得抽样。
- `filter_decisions` 保存已有方向的 Observer、Ranker、System Operator 和 Risk 逐关事实，到首个拒绝停止。
- `filter_candidate_outcomes` 按候选共享一份目标市场净结果，不按筛选关重复结算。
- Risk 证据必须在权威 `RiskDecision` 落库后追加；证据失败不改变原始批准/拒绝。
- `TradingDecision.action='trade'` 表示 System Operator 的交易意图，不等于提案已经创建。
  跳6 的方向过热或目标市场无价格必须写 `stage='proposal_submission'` 的结构化拒绝原因；
  诊断通过这条证据区分“模型没想交易”和“想交易但提案前被拒绝”。

完整定义见 `docs/design/filter_evidence_contract.md`。

---

## 3. 接缝守卫表

| 接缝 | writer | consumer | 关键不变量 | 守卫/spec |
|------|--------|----------|-----------|-----------|
| 机会落库 | `market_observer.py:361` | ranker / Layer1 | 仅 `status='open'` 进排序;reviewed 不重复 | (本契约 DL-S01) |
| signal_pending | `paper_trader.py` | `trading_context._build_pending_signal_queue` | 仅未完成；先去重再限量；oldest-first；显式 backlog/expiry；queued→reviewed 原子终结 | DL-S03 + DL-S10~12 |
| 决策→提案 | `trading_decision.py` | `gateway.py` | `conviction≥55 且 action∈trade类` 才尝试提案；LLM 开仓还必须有五项参数理由；未创建时必须留下 proposal_submission 原因；白名单校验 | DL-C01 + DL-C02 + TDR-C01 + execution_contract §3 |
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
- **DL-INV-9 提案结果不丢失**:`trade/scale_in/flip` 是模型意图；若方向门或目标市场取价使 proposal 未创建，必须返回稳定机器原因并写 `proposal_submission` 筛选证据，禁止只留下无法解释的 `proposal_id=NULL`。
- **DL-INV-10 参数理由不伪造**:System Operator 的新开仓须带五项参数理由；提案快照必须把最终值与 `market_snapshot` / `default_config` / `risk_cap_clamped` 等实际来源分开，不能把后端计算说成 AI 选择。详见 `trade_decision_rationale_contract.md`。
- **DL-INV-11 排序后唤醒**:市场机会生产者只落 Opportunity；只有 `paper_trader` 在排序并成功写入 `signal_pending` 后唤醒 System Operator。已有非 pending 最终决策的 `opportunity_id` 不得再次入队。
- **DL-INV-12 公平待处理队列**:只向 Operator 暴露尚无最终决定的信号；必须先去重再限量、最早信号优先，并报告 overflow/过期，禁止用“最新 5 条”静默覆盖旧工作。
- **DL-INV-13 最终决定幂等**:同 `signal_source + opportunity_id` 的首条非 pending PiDecision 是权威结果；重复唤醒必须在创建 proposal 前返回该结果，不得生成第二份提案或最终决定。
- **DL-INV-14 提案尝试耐久**:trade 类意图无论建成、被稳定规则拒绝或抛异常，都必须先后得到一条最终 PiDecision，并保存 `proposal_status/proposal_reason`；`proposal_id=NULL` 不能再等于“原因未知”。
- **DL-INV-15 副作用前认领**:最终 PiDecision 必须在创建 proposal 前耐久落库;同一来源机会的并发唤醒只能有一个认领者。认领/终结写失败必须向调用方报错,不得返回成功。
- **DL-INV-16 降级结果也消费载体**:LLM 不可用时的 skip 必须绑定本轮最早待处理信号并终结其队列状态,禁止写匿名 skip 后无限重试。
- **DL-INV-17 状态只前进**:`paper_trader` 只允许 `open→queued`;任何旧批次不得把 `reviewed/expired` 倒退。Serenity 待处理信号在没有对称过期迁移时不得被市场信号 TTL 静默隐藏。

---

## 5. Spec-as-Code

| claim | 级别 | 锁定 |
|-------|------|------|
| `DL-S01` | L2a | Opportunity 模型含 market/direction_bias/confidence/confidence_score/status/scanner_family |
| `DL-S02` | L2a | TradingDecision dataclass 含 action/conviction/reasoning/bear_case/market_type/opportunity_id/signal_source |
| `DL-S03` | L2b | trading_context.py 含 signal_pending 查询并填 pending_signals(跳2→4 接缝存在) |
| `DL-S04` | L2a | TradingDecision.signal_source 默认 `market_observer`(值域含 `serenity`) |
| `DL-S06` | L2a | 提案先解析最终 market_type，再把它传给 latest_snapshot 取价 |
| `DL-S07` | L2b | reviewed Opportunity 仍可经 market_observer signal_pending 把 opportunity_id 交给最终决策 |
| `DL-S08` | L2a | 市场 Opportunity 生产者不绕过 ranker 唤醒 Operator；paper_trader 写 pending 后唤醒 |
| `DL-S09` | L2b | skip/watch 最终决定保存 symbol、direction、market_type、opportunity_id、signal_source，并终结 queued 状态 |
| `DL-S10` | L2a | PiDecision 载体包含 market_type，pending/final 不依赖事后猜市场 |
| `DL-S11` | L2b | pending 队列只读未完成项，先去重再限量，oldest-first 并报告 backlog |
| `DL-S12` | L2b | 同来源机会已有最终决定时，重复唤醒不再创建 proposal/最终行 |
| `DL-S13` | L2b | 提案异常仍保存最终 trade 意图、failed 状态和脱敏稳定原因 |
| `DL-S14` | L2b | 同一待处理机会并发评估时先耐久认领,仅一次 proposal 副作用 |
| `DL-S15` | L2b | 最终认领写失败时零 proposal 副作用且调用失败可见 |
| `DL-S16` | L2b | LLM 不可用的 skip 仍绑定并消费最早待处理信号 |
| `DL-S17` | L2b | Serenity 长周期待处理信号不被 market 30 分钟 TTL 静默隐藏 |
| `DL-C01` | L2c | `_MIN_CONVICTION_TO_TRADE == 55`(改阈值即爆红,强制同步本契约) |
| `DL-C02` | L2c | trade 类意图未创建 proposal 时保留结构化 proposal_submission 原因 |

`tests/specs/spec_decision_lifecycle.py`。验证:`uv run pytest tests/specs/spec_decision_lifecycle.py -q`。
筛选证据的结构、行为和跨操作规则在 `tests/specs/spec_filter_evidence.py`。
