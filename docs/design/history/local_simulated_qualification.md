# Archived local simulated contribution and qualification

The following Paper Internal projections are retired. Their Python implementations,
Alpha Audit / doctor fields and `inspect profit-loop-qualification` command are removed.
Existing Position, Order, Fill, Risk, filter outcome, evidence epoch and artifact
identities remain unchanged. No simulated verdict qualifies an external account.

Profit Gradient retains its general layers, but Execution has no external paired
reference and is unavailable. The former local-account pairing validator is removed.
Current cost evidence and other independently supported layers keep their own contracts.

## Former frozen whole-policy qualification

`profit_loop_qualification/v1` accepted only `paper_internal`. It required a frozen
registration containing `deploy_sha`, `runtime_session_id`, `execution_mode`,
`policy_epoch_id`, `improvement_id`, `registration_at`, `stopping_rule`,
`cost_model_id`, `cutoff_at` and `max_drawdown_cap`. Durable registration hashes,
policy identity and runtime provenance were checked before complete cohort reads.

Its gates required eligible outcomes, positive net PnL, profit factor above one,
at least two eligible days, profit concentration within preregistered caps
(default top one 25%, top five 50%) and bounded drawdown. Output contained the
registration, metrics, gates, cohort completeness/digests and a content hash.
It emitted `insufficient_data`, `minimum_profitable_loop_rejected` or
`minimum_profitable_loop_proven`; it never granted execution authority.
The compiler is removed; existing JSON artifacts are historical evidence only.

## Former Risk contribution contract

### 3.1 只读贡献测量

`analytics.risk_contribution.build_risk_policy_contribution` 只读消费已经存在的
authoritative `RiskDecision`、其 exact Risk filter trace 与 canonical settled
`24h` candidate outcome。对每个 Proposal，control 是不执行的
`pass-all-candidates/v1` shadow：approved 时 treatment/control 同值；rejected
是实际 Risk intervention，treatment 为零暴露、control 为该候选的成本后
`net_return_pct`。报告因此得到 Proposal 统计单位上的 paired policy-value
delta。

测量必须锁定单一 Demo policy id/revision/hash、producer commit、behavior
fingerprint 与 cost model，并逐笔验证 Proposal→RiskDecision 的 runtime session、
producer commit、behavior fingerprint 及 Proposal commit/quantity、instrument、
entry、24h horizon、settlement 与净收益算术。一个窗口可跨 clean Gateway
restart，因此 runtime session 不要求全 cohort 相同，但每条因果链必须完全匹配。
身份混杂、成本缺失、样本不足或实际 rejection 不足一律 `insufficient_data`。该 projection 不调用 Risk Engine，
不改任何 check，不提交 Order，也不授予自动 policy 修改；portfolio capacity /
interference 与 downstream Execution/Exit 不在其 estimand 内。


## Former execution contribution contract

### 11.7 Native Paper paired execution-cost measurement（schema v16）

新建 native `paper_internal` entry Order 在 slippage 前持久化
`reference_price=Risk 已核验的 Proposal.price` 与来源
`risk_verified_proposal_price`；close Order 在 slippage 前持久化 exact fresh
mark 与来源 `paper_close_mark`。两个字段由数据库 trigger 禁止更新，不回填历史行，
也不参与 broker 的提交价、Risk 判定或 Position 行为。

`analytics.execution_contribution.build_execution_cost_contribution` 只读消费
`eligible_strategy_outcomes`，并再次闭合 authoritative Risk、entry/close
Order/Fill、Position、数量、费用、runtime/build/behavior provenance 与 funding。
同一 Position 的 control 固定相同数量、方向、开平时点和 funding，但假设在两个
reference price 零费用成交；treatment 使用真实 local Fill 与 fee。差值因此只归因于
Paper 执行价格影响与费用，单位同时报告 `net_return_pct` 和 quote-currency PnL。

该结果不证明当前 router 相对另一算法的正向价值，不覆盖 exchange/demo/live、legacy、
multi-entry、partial close 或 adopted Position；multi-entry 以
`unsupported_execution_multi_entry` 明确排除，不伪装成数据链缺失。该测量不授权改变
slippage、fee、broker、Risk 或 Live 配置；
任一身份/经济字段缺失均为 `insufficient_data`。


## Former local cash completeness presentation

因此本地 paper 账户继续以已结算的现金账本为权威，并明确返回
`economics_complete=false` 和 `unsettled_closed_positions_count`。`available_balance_usdt` 仍反映
已确认现金减预留资金，`equity_usdt` 仍反映已确认现金加未实现损益；两者都不补入这笔未完成的
关闭损益，也不得被解释为完整经济结果。Portfolio 的相关 realized 汇总同样返回 `NULL` 并附未完成
数量，而不是用 0 提供一个看似完整的数字。spot 不产生 funding，关闭时明确写 `funding_cost=0`、
`funding_source='not_applicable'`，不受此冻结影响。


The retired `test_pipeline_feedback_loop.py` inserted simulated PnL rows and
computed its own sums and win rates; it did not execute a review, scorecard or
Learning consumer. It has been removed. Current consumer tests use verified
external outcomes and assert administrative exclusions. The guard-promotion
pipeline now uses the same external close producer for its losing cohort and
still requires owner authority rather than promoting a loss correlation.

The former anti-hallucination pipeline fabricated an internal-mode verified exit
and required a StrategyRecord for high-confidence case studies. It now uses the
shared external close producer and the current Proposal/Opportunity attribution
contract. A reconciler-adopted position with a verified exit but unknown entry
economics remains excluded from case creation and FTS retrieval. Symbol and daily
loss rejection tests use external Demo configuration while retaining durable Risk
and rejected-Proposal assertions.

## Former factor-to-entry signal consumer

`lookup_factor_signal` translated retrospective factor status into Ranker score
changes or candidate blocking through `ranker_factor_filter` experiments. Its
PaperTrader/Ranker consumers are retired and it had no production caller at
`b6007795f`; the unused consumer and its dedicated tests are removed.
`build_factor_feedback` remains the read-only CLI diagnostic, with verified
external closes and administrative-record exclusions. Historical factor labels
and persisted experiment identities are unchanged.
