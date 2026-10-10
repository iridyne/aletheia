# Retired entry-decision lifecycle (TradingDecision / PiDecision / signal_pending)

> **History, not a contract.** Moved verbatim from
> `docs/design/contracts/decision_lifecycle_contract.md` (former §2.1 and appendix)
> on 2026-10-10. It explains rows written by the retired Ranker → `paper_trader` →
> PiDecision entry path. Nothing here is current behavior, an acceptance criterion,
> or an executable fallback. The current entry spine is the
> [Decision Lifecycle Contract](../contracts/decision_lifecycle_contract.md) and the
> [Entry Plan Contract](../contracts/entry-plan-contract.md).

### 2.1 历史 Opportunity 载体（非当前执行契约）

以下字段/旧状态机仅解释历史记录；其中 Ranker、pending、TTL fallback 与 adapter 均无新入场权限。

| 字段 | 类型 | 语义 / 值域 |
|------|------|------------|
| `market` | `str`(必填) | 机会的市场/场地描述符。跳5 当 PI 未显式选 `market_type` 时由 `_attach_opportunity_context` 作为继承来源(`trading_decision.py:909`)。⚠️ **已知歧义**:`market` 与下游期望的 `market_type` 枚举(`spot`/`usdt_futures`)值域是否一致**需核实并锁定**(报告 A1 design gap)。 |
| `direction_bias` | `str \| None` | `long` / `short` / `neutral`。跳2 `paper_trader_ranker` 直接读它定方向。 |
| `confidence` | `str` | **分类**置信度,默认 `speculative`(同 `VALID_CONFIDENCES`)。**不是**数值。字段由 Opportunity 的原始 producer 持有，后续 Observer assessment 不得覆盖。 |
| `confidence_score` | `int \| None` | Producer-owned 数值信号,跳2 打分用。Universe-linked 新路径不创建 executable Opportunity；历史 `universe_scanner` 行只作 legacy/observation carrier。Observer 的 LLM confidence 只进入 namespaced assessment，禁止与 scanner composite score 混合或把它解释成概率。 |
| `status` | `str` | 状态机:`open`(每个 snapshot 的可排序分析代次)→ `queued`(已认领为耐久 Operator 载体)→ `reviewed`(Operator 最终决定已落库);Filter 的永久拒绝也可从 `open` 直接进入 `reviewed`。父 snapshot supersede 只终结仍为 `open` 的代次；`queued` 由自身 `valid_until`、Operator 终局或 Proposal claim 终结。跳2 以 oldest-first 有界批次查询 `open` 工作集；top-N 只限制执行候选，不能截掉本批已扫描到的永久拒绝。永久拒绝或 TTL 终态与其原因事实同事务提交，证据失败则保持原状态可重试。 |
| `scanner_family` | `str \| None` | 来源扫描器族,跳5 透传进归因链(风控 #16 部门集中度解析用)。 |
| `valid_until` | `str \| None` | 触发后 Opportunity 自身的权威市场失效时间。它不是 Candidate TTL；Candidate TTL 由 Universe refresh/selection 与 `universe_members.expires_at` 管理。`open` 在此后进入 Ranker 的 family soft-TTL 衰减，直到硬顶才终结；`queued` 到此时间立即失去 Proposal 权限。只有缺失/不可解析该字段的历史 queued 载体才使用 pending `created_at + 30min` 兜底。 |
| `setup_id` / `signal_mark` / `trigger_behavior_version` | `str \| None` | 历史 Universe Setup adapter 的不可变审计字段。新 Candidate → Entry Plan 路径不得写入或依赖这些字段。 |
| `generation_version` / `generation_json` / `generation_hash` | `str \| None` | 历史 Opportunity Generation V2 审计载荷；新 Candidate → Entry Plan 路径不得以它取得开仓权限。 |

### 历史 TradingDecision、PiDecision、pending 与 admission slot

本节完整保留旧设计叙述以解释历史证据；所有评估、claim、defer/wake、恢复与队列授权均已退役，不能执行。

| 字段 | 类型 | 语义 |
|------|------|------|
| `action` | `str`(必填) | 活跃入场面只允许 `trade` / `skip`。缺失或返回 `close` / `reduce` / `scale_in` / `flip` / `watch` 是 `DecisionUnavailable(operator_output_validation)`，不得归一成终态 `skip`、绑定身份或消费 pending。 |
| `conviction` | `int`(必填) | 0–100。缺失、非整数或越界是评估不可用；合法值低于 55(初始或 debate 后)是**非终态 defer** (DL-INV-2 v1.5)——`DecisionUnavailable('operator_low_conviction' 或 'debate_not_confirmed')`，pending 载体保留待 material fingerprint 变化重新开放；不合成 final `skip`。 |
| `reasoning` | `str`(必填) | 决策理由(人类可读)。权威 PiDecision 保存全文；短 reflection/UI 可独立截断。 |
| `skip_kind` | `str \| None` | 结构化输出只接受 `adverse_candidate_evidence` / `insufficient_evidence` / JSON `null`；`trade` 可显式返回 `null`。仅 direct skip 使用非空值，`insufficient_evidence` 或 `null` 均为中性未知，不得终结候选。 |
| `adverse_reason_codes` / `adverse_evidence` | `list[str]` | terminal direct skip 必须至少各有一项允许的 typed reason 与候选特定事实；rank priority 或证据不足不能冒充 adverse reason。 |
| `validated_adverse_claims` | `list[dict]` | 仅由确定性 validator 生成；逐项保存 `reason_code + fact_refs`。模型文本不能写入或替代该权限。 |
| `model_reasoning` | `str \| None` | direct skip 的原始模型文本，仅作审计；权威 `reasoning/adverse_evidence` 由 validated fact refs 重建，避免错误判词进入最终状态。 |
| `market_type` | `str \| None` | 新 LLM `trade` 必须显式选择与 exact carrier 一致的 `spot` / `usdt_futures`；缺失或无效值走同一有界输出修复，不再从 carrier 猜测。新 Operator `spot+short` 不得在下游静默重标为 futures，只能 repair/defer；旧 programmatic/历史恢复对象保留兼容 coercion。`skip` 保留可空语义。 |
| `opportunity_id` / `strategy_id` / `scanner_family` | `str \| None` | R0 归因,由 `_attach_opportunity_context` 回填(`trading_decision.py:909`)。 |
| `signal_source` | `str` | 机会来源,默认 `market_observer`;`serenity` 表示解析 `serenity_opportunities.id`(`:85`)。值域 = {`market_observer`,`serenity`}。 |
| `bear_case` | `str` | S4 强制反方论证。新 LLM 决策 schema 必填；空值仅允许历史记录。 |
| `stop_price` / `take_profit` | `float \| None` | `trade` 的 LLM schema 与解析边界要求两者都是显式有限正数；缺失/0/NaN/Infinity 触发有界修复，不能由配置替模型补值。跳6 用新鲜精确 mark 校验多空几何及 `1.00 <= round(abs(tp-entry)/abs(entry-stop), 2) <= 10.00`；初稿越界走一次有界修复，二次仍错或提交时 mark 刷新导致越界则 `DecisionUnavailable(operator_entry_plan)`，不改写模型计划。`skip` 不要求这些字段。 |
| `parameter_rationale` | `dict[str,str]` | 新开仓的五项 AI 理由：`entry_price` / `stop_loss` / `take_profit` / `notional_usdt` / `leverage`。仅 `trade` 的 LLM schema/解析路径要求全部为非空字符串；缺项触发同一有界输出修复，最终仍不完整则不创建 final PiDecision/Proposal。最终入场价同时保存实际 `market_snapshot_id / market_type / snapshot_at`，不是只写“来自快照”；详见 `trade_decision_rationale_contract.md`。 |

跳6 在精确报价通过后，还必须用相同 `symbol + exchange + physical market_type`
读取新鲜订单簿和 24h volume，形成 `market_tradability_gate`。`usdt_futures/swap`
在采集存储层映射为 `perp`；订单簿和 volume 不得跨 venue 或 spot/perp 混用。
缺失、过期、身份不完整的上下文返回
`DecisionUnavailable(reason='market_tradability_unavailable', stage='operator_entry_plan')`；
只有明确 `is_tradable=false` 才返回 `market_not_tradable`。两者均为非终态 defer，
不创建 Proposal、不消费 pending。阈值仍由既有 `compute_tradability` 负责，本关卡
只消费其明确结论，不改变评分公式。通过时，完整 instrument identity 与 tradability
输入写入 hash-covered `TradeProposal.attribution_snapshot`，供事后复盘和 Learning 使用。

#### 历史 2.2a PiDecision（耐久 Operator 事实）

`PiDecision` 对 pending 和最终 action 都保存 `symbol / direction / market_type /
opportunity_id / signal_source`。最终行另存 `proposal_id / proposal_status /
proposal_reason`：已形成的业务决定必须有耐久状态；但评估、必需辩论或 Proposal 前置条件
不可用不是业务决定，必须抛出带稳定 `reason/stage` 的 `DecisionUnavailable`，不写最终行、
不消费队列。`evidence_manifest_json.operator_rationale` 保存 skip kind、adverse codes/evidence、
supporting/opposing signals 与 risk factors；不得用 200/300 字截断替代权威理由。不得把 provider
异常文本写入数据库。历史 NULL 默认保持未知；唯一例外是旧
Serenity pending 的 `market_type`，且只能从相同 `opportunity_id` 的耐久
`SerenityOpportunity` 恢复，并要求两边 `symbol / direction` 精确一致、来源
`market_type` 非空。任何缺行或不一致仍保持未知并拒绝授权。

#### 历史 2.2b Layer 1 technical feature identity

`universe_feature_snapshots` 的物理身份只有 `asset_id`；System Operator 的 Layer 1 技术特征读取
必须经 `asset_id → universe_registry` 取得 `symbol + market_type`，再按完整 instrument identity 去重。
不得把未 join 的 snapshot 当作含 `symbol` 或价格的行：那会把所有资产压缩为一个空标的，令模型把
完整的特征供给误解为数据不足。价格仍只能来自新鲜的 market-data cache；cache 未命中必须诚实为
`None`，不得用 snapshot 身份修复伪造报价。

全局 `feature_index` 的数量上限只用于有界展示，不是候选事实门。每个精确 pending carrier 必须按其
`universe_member_id → asset_id` 单独读取最新技术快照，即使该快照排在全局展示上限之外；同一候选的
queued/reviewed 来源 Opportunity 也必须携带 thesis、observations 与技术身份进入上下文。精确快照缺失、
核心数值不完整或过期时，在模型调用前写候选级 typed defer 并保留 carrier。新的精确 snapshot id/时间/
数值进入 material fingerprint，必须重新开放评估；Universe 特征刷新（含 change-suppressed TTL slide）
必须唤醒 Operator，不能只等待固定 heartbeat。

#### 历史 2.3 signal_pending 载体 (跳2→4 接缝)

`paper_trader` 写 `PiDecision(action='signal_pending')`(`paper_trader.py:143/235`);
`trading_context._build_pending_signal_queue` 读回尚无同 `signal_source + opportunity_id`
最终决定的行,先按 `signal_source + symbol + direction + market_type` 去重,再按最早到达顺序向 PI 暴露 5 条。
`pending_queue` 同时返回真实未完成总数、可见数、overflow、最老时间和已超时未消费数。
**不变量**:市场扫描信号进入 queued 后由来源 Opportunity 的 `valid_until` 单一控制；pending
`created_at + 30min` 只用于没有可解析 `valid_until` 的历史兼容行。不得同时套用两套时钟，否则
30min 会截断 1–4h 家族载体，也令 120min Operator defer 永远无法完成。Serenity 是长周期来源，
不得套用 market compatibility fallback。
Operator 的 Proposal preflight 与最终原子 claim 都必须重查 Opportunity 自身 `valid_until`；两处中先观察到
过期的一处通过同一个仓储 CAS，把 `queued→expired` 与唯一一条
`opportunity_ttl / ttl_expired` 事实放在同一保存点中。证据失败时保持 `queued`，且不得写最终 PiDecision
或 TradeProposal；不能让模型评估期间发生的过期成为“有终态、无原因”的审计断点。
`paper_trader` 只能以条件更新认领 `open→queued`;旧计划不得把 `reviewed/expired` 倒退为 queued。成功写入后先把来源 Opportunity 标记为
`queued`;只有 `reflection.claim_decision` 在最终 PiDecision 与状态更新的同一事务中把它改成
`reviewed`。任何绑定 Opportunity 的活跃最终决定（`trade` / `skip`）都必须命中同一
`signal_source + opportunity_id` 的耐久 `signal_pending` 载体；Layer1 open 视图和 ranker 候选只能
提供分析上下文。heartbeat 零 pending 不是 entry 决定：不得调用交易 LLM、写 final `PiDecision`、
退休 Opportunity 或创建 Proposal；只能写 `operator_evaluation_events(event_kind=no_evaluable_candidate)`，
附队列摘要、触发源和当前 policy epoch。该观察事实不进入 candidate skip/outcome cohort；历史无 target
skip 仅作 legacy audit，不能由新逻辑继续产生。持仓动作不属于这个入口，只能由独立 portfolio review
以 `hold` / `adjust_position` / `close_one` 表达；horizon 到期时 `hold` 不构成终态，
只能明确延长到未来的绝对持仓 age 或平仓；
`reduce_exposure` 等未实现标签不得映射为整仓关闭。命令路径先在内存中准备完整 immutable Proposal，
再以短事务同时写入 `proposal_status=submitting` 的最终 PiDecision 和 TradeProposal,并把 Opportunity
改为 `reviewed`;完成提交后才进入 Risk/Execution。这样崩溃只能留下“两者都没有”或“两者都存在”,
不得留下 reviewed + submitting 但零 Proposal。升级前遗留的该类孤儿 claim 只能在 pending 载体和
symbol/direction/market_type/action/opportunity/source 身份全部匹配时补写一次 Proposal；原 claim 的
conviction 仍是耐久审计事实，但重启后同一身份被重新打成 89/90 不得阻塞恢复或改写原评分。
Direct skip 在 `system_operator_skip_remediation/b1.5` 下必须经过一次有界、blind 反事实确认；确认后仍只有
`adverse_candidate_evidence + typed reason + concrete evidence`，且每个 reason 都由确定性 validator 解析到
同一候选的 typed fact refs，才可终结。B2 `insufficient_evidence`、中性 50/50 regime、可选 OI 缺失、
rank ordering、账户权益或单笔近期亏损都没有 adverse authority。两次输出仍是信息不足或无效时，
写 `operator_evaluation_events(event_kind=operator_decision_deferred_unchanged_context)` 并保持 pending；
同一 policy epoch、同一候选的物质上下文 fingerprint 在 120 分钟窗口内未变化时不再调用模型；
每条 defer 的 `defer_until` 是独立的 Operator attention deadline，但不得晚于来源
Opportunity 的 `valid_until`。defer 事件绝不修改 `valid_until`；PI daemon 必须把仍未兑现的
耐久 `defer_until` 作为独立 wake deadline，持续的新 signal、heartbeat 相位和进程重启都不得
推迟或遗忘该 deadline，且新 Operator 观察、终局 PiDecision 或 Opportunity 过期后不得重挂。
过期载体只能按其市场有效期失去 admission/recovery 资格，不能由 scheduler/API resurrect。
defer 不阻塞同批下一候选。每次模型调用只能暴露一个 pending carrier 及其技术/Opportunity/B2 证据，
不得暴露 analysis-only preview 或另一 pending 候选作为判词依据。fingerprint 排除
Opportunity id/时间戳等载体 churn，但包含 instrument、方向、scanner thesis、rank priority、B2 状态、
实际 B2 `n_total/n_net/coverage/avg/shrunk/verdict`、候选技术特征与 market regime；这些字段变化会重新开放评估。
缺失技术数值显示为 unknown，不得转写为 0。已存在 `proposal_status=submitting` 的 claim 始终优先恢复，
不得被新确认门阻断或覆盖。
`PiDecision` 自身保存 `symbol / direction / market_type / opportunity_id / signal_source`;
`_attach_opportunity_context` 必须把 `pending_signals` 中 `market_observer` 与 `serenity` 两类载体
都视为有效归因来源,并按 `opportunity_id` 回填来源字段,不得只消费 Serenity 载体。对
`market_observer` 载体,队列中的精确编号优先于仅靠相同 symbol/direction/market 匹配到的另一条开放机会;
Opportunity 最终动作只认 pending 权限，因此精确 Serenity pending 与仅用于分析的 Layer1 同时匹配时，
由前者提供身份。portfolio review 只按明确 `position_id` 使用独立持仓权限。

#### 历史 2.3b Admission slot 语义

`paper_trader._plan_signal_batch` 用 `_PENDING_SIGNAL_QUEUE_CAPACITY=5` 限制同时暴露给
Operator 的 signal_pending 载体数量。一个 admission slot 代表 **Operator 尚未做出判断** 的
signal_pending 载体；`count_unresolved_signal_pending` 是此语义的唯一权威计数器。

**已判等**的载体（`operator_evaluation_events` 中最新事件是
`operator_decision_deferred_*` 且 `defer_until > now()`）不占 admission slot——Operator 已经
判断了它，只是在等重评窗口到达。该规则同时被 `pi_daemon._has_pending_signal_work`（决定
是否要 startup 评估）与 `paper_trader.admission`（决定是否接纳更高分新候选）复用；两个消费者
必须共享同一份"活跃 defer opportunity_id 集合"（`active_defer_opportunity_ids()`）。

`_build_pending_signal_queue` 的显示排序层继续把活跃 defer 载体 demote 到队尾，与本 slot
语义相互独立：admission slot 决定"是否允许新候选入场"，display order 决定"入场后先看谁"。
