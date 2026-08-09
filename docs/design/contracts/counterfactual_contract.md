# Counterfactual Analysis Contract

> 状态：阶段 D 权威设计，2026-07-31。
> 范围：`aletheia/counterfactual/` 子包为决策链提供的可事后检验的固定时点结果、有界回放、前瞻实验与描述性诊断。
> 不包含：策略执行、风控限额、live 授权、订单路由、自动调参。任何"从反事实结论直接触发行为改变"的路径都不属于本合同。

本合同是四份既有文档在反事实模块视角下的聚合：

- [causal_counterfactual_research_guidance.md](../subsystems/causal_counterfactual_research_guidance.md) — 全框架方法阶梯 L0-L5。
- [opportunity_outcome_contract.md](opportunity_outcome_contract.md) — 固定持有期结果字段与结算路径。
- [filter_evidence_contract.md](filter_evidence_contract.md) — 过滤器证据的身份与去重语义。
- [decision_lifecycle_contract.md](decision_lifecycle_contract.md) — 决策生命周期中反事实确认的位置。

以上四份为源约束；本合同不新增约束，只把源约束在**反事实模块内部**的具体落法固定下来。若与源约束矛盾，源约束优先。

## 1. Purpose and current scope

反事实模块回答的是：

> 在决策时可见的身份、方向、价格与成本假设下，若暴露于该候选，固定 horizon 后会得到什么成本后结果？

它使一个决策可被事后检验，但**不是**实盘 PnL，也不是因果证明。除人工治理外，任何生产行为改变（policy epoch rotate、阈值调整、guard 生成、authority grant）都不属于本合同的授权范围。

方法阶梯与代码位置对应：

| Level | 含义 | 模块 |
|---|---|---|
| L0 | 完整性：决策身份、first exposure、版本/epoch、结果键 | `counterfactual/trades.py::persist_shadow_batch` 落库时绑定；`shadow_trades.policy_epoch_id` 是硬字段 |
| L1 | 固定时点结果：预注册 horizon 的成本后结果 | keyed rows 由 canonical `filter_candidate_outcomes` 结算并投影；legacy 无键行保留 Position-close / stale resolver |
| L2 | 描述性诊断：在可比 cohort 中的差异 | `counterfactual/calibration.py::build_calibration` |
| L3 | 有界回放：固定历史、版本和参数，产出可检验假设 | `counterfactual/filter_replay.py` |
| L4 | 前瞻实验：预登记分配、观察 gate | `counterfactual/filter_experiments.py` |
| L5 | 人工治理 | 不在模块内。反事实证据供 owner 审阅，产生新 policy epoch 的决定不由模块发起。 |

任何层级都不能跳过前一层；`L0/L1` 不足时的诚实结论是 `insufficient_evidence`，绝不是"放行"或"封锁"。

## 2. Entry points

生产入口只有下述几个函数；调用它们的位置由 [decision_lifecycle_contract.md](decision_lifecycle_contract.md) 与 [scheduler_contract.md](scheduler_contract.md) 约束：

- `counterfactual.trades.persist_shadow_batch(top_candidates, selected, top_n=5)` — 由 `paper_trader` 在 ranker 决策后同批调用；写入 Top-N shadow 行（含选中与未选中）。
- `counterfactual.trades.resolve_shadow_trades_for_position(position)` — 由 `portfolio.outcome_hooks` 通过常量 `SHADOW_TRADES_HOOK = "shadow_trades"` 在 Position 结束时调用；当前只处理没有 `outcome_candidate_key` 的 legacy 行。
- `storage.repositories.settle_linked_shadow_outcomes(...)` — 固定结果结算器在同一候选的 canonical `24h` `filter_candidate_outcomes` 成熟后，把完全相同的成本后结果投影到带 `outcome_candidate_key` 的 Shadow 行。
- `counterfactual.trades.resolve_stale_shadows(min_age_hours, horizon_hours, limit, dry_run)` — 由 scheduler job `shadow_resolver`（interval=6h，见 [scheduler_contract.md](scheduler_contract.md)）调用。
- `counterfactual.filter_experiments.register_replay_ready_experiments(replays)` — 由 `analytics.filter_controller` 在 shadow 观察周期调用。
- `counterfactual.filter_experiments.evaluate_active_shadow_experiments()` — 由 `analytics.filter_controller` 定期评估收集中的实验。
- `counterfactual.filter_experiments.build_filter_shadow_experiment_report(limit)` — 由 CLI / API 只读消费。
- `counterfactual.calibration.build_calibration(days, symbols, policy_epoch_id)` — 由 `scripts/shadow_threshold_calibration.py` 与内部报告 API 调用。
- `counterfactual.trades.list_deduped_shadows(days, symbols, policy_epoch_id, columns)` — 所有跨候选聚合的 shadow 消费必须走这里，去重按 `outcome_candidate_key`。

## 3. Durable state and consumers

反事实模块的持久化状态**只在三张表**中；schema 见 [data_model.md](../data/data_model.md)：

- **`shadow_trades`** — 每 tick、每候选一行的固定 horizon 反事实账本。写入者是 `persist_shadow_batch`；带键行由 canonical `24h` 候选结果投影结算，legacy 无键行才走 `resolve_shadow_trades_for_position` / `resolve_stale_shadows`。
- **`filter_shadow_experiments`** — 前瞻观察实验的注册记录，`production_mutation_allowed=0` 是硬字段，写入 SQL 保留此列以让观察-only 边界在数据库层显式。
- **`filter_shadow_evaluations`** — 每个实验的证据快照，`disposition` 取值 `collecting` / `ready_for_review` / `completed_no_support` / `invalidated`。

授权消费者：

| 消费者 | 表 | 用途 |
|---|---|---|
| `counterfactual.calibration.build_calibration` | `shadow_trades` | conviction 门槛校准报告 |
| `analytics.family_scorecard.compute_family_scorecard` | `shadow_trades` (via `list_deduped_shadows`) | 每 scanner_family 的已结算候选数（90 天） |
| `analytics.guard_impact._outcomes_for_candidate` | `shadow_trades` | 单 opportunity 最近一次已结算 shadow (per-opp，不需要去重) |
| `analytics.alpha_attribution._ranker_alpha` | `shadow_trades` + `filter_candidate_outcomes` | 用 Shadow 的 rank/batch/epoch 身份连接 canonical `24h` 成本后候选结果 |
| `analytics.alpha_attribution._system_operator_alpha` | `filter_decisions` + `pi_decisions` + `filter_candidate_outcomes` | 同一行为指纹下 passed/rejected 候选的 canonical `24h` 成本后比较 |
| `analytics.alpha_attribution._learning_alpha` | Learning fixed-look decision + contribution verdict + exact candidate outcomes | 只消费已绑定的 confirmatory authority verdict；generic learning 事件仍为弱诊断 |
| `analytics.alpha_attribution._exit_paired_evidence` | `exit_breakeven_shadow_assignments` + `_outcomes` | 投影同一 Position 的冻结 Exit treatment 增量净 PnL；不并入五层 verdict，不授权行为 |
| `analytics.thesis_lineage` | `shadow_trades` | per-opp shadow 汇总 |
| `economics.viability` | `shadow_trades` | 单 opp/batch 的选中 vs 对照的净收益比较 |
| `server.routes.data GET /analytics/shadow-trades` | `shadow_trades` | 只读 API |
| `analytics.filter_controller.evaluate_active_shadow_experiments` | `filter_shadow_experiments` / `_evaluations` | 前瞻实验周期评估 |

**跨候选的聚合消费必须走 `list_deduped_shadows`**（按 `outcome_candidate_key` 去重）。单-opp / 单-batch 的查询可以直接读表；因其查询模式天然不跨候选，不受长寿命候选过度加权影响。

Alpha Audit 的集成边界是显式的：Ranker 与 System Operator 共享 canonical
`24h` `filter_candidate_outcomes.net_return_pct`；Learning 复用自身已消费的
fixed-look authority verdict；Exit 保留其 Position 配对、quote-currency
estimand。不同 estimand 不做原始 delta 排名；统一跨层净经济基准后续见
#803。

## 4. Observable result

- `shadow_trades.pnl_pct` 与 `net_pnl_pct`：在成本模型（费用 + 点差 + 资金费）下的方向性净收益，单位为百分比数值。`net_pnl_pct = NULL` 而 `pnl_pct != NULL` 表示 gross-only（例如 perp 缺资金费），不得进入学习。
- 带 `outcome_candidate_key` 的 `shadow_trades` 以 canonical `24h` `filter_candidate_outcomes` 为结果权威；Position-close 与 stale-forward 路径只保留给历史无键行。`settlement_price_source` 必须沿用产生该结果的实际结算来源，不能由消费方重算或改写。
- `filter_shadow_evaluations.disposition = "ready_for_review"` 是本合同下的**最强观察结论**，它只表示证据 gate 已达标、值得人工审阅，从不表示"应该改变生产行为"。改变生产行为须走新 policy epoch + owner 批准，不属于本合同。
- `build_calibration` 报告是描述性诊断，读者应联合 `avg_net_pct` / `median_net_pct` / `win_rate_pct` 判读；低 n 档必须视为不确定。

## 5. Invariants and trust boundaries

以下不变量在任何执行路径都必须成立；对应的执行证据在 §7：

- **CF-I01 观察 only**：`filter_shadow_experiments.production_mutation_allowed = 0` 是 schema 层硬字段。任何 UPDATE 想改成 1 的路径必须**不存在**——用 spec 断言 code 与 migrations 不含此更新。
- **CF-I02 First exposure 固定**：`shadow_trades.entry_price` / `entry_spread_bps` / `funding_rate_at_entry` / `regime_tag` / `policy_epoch_id` 在 `persist_shadow_batch` 时一次性快照，此后只被 append-only 结算字段扩充；不得回填。
- **CF-I03 结果键完整**：`outcome_candidate_key` 是所有跨候选去重的唯一入口；聚合消费必须调 `list_deduped_shadows`。
- **CF-I04 Epoch 分层**：`shadow_trades.policy_epoch_id` 由 `persist_shadow_batch` 通过 `_current_policy_epoch()` 从 `agent_shell.skip_policy.ensure_skip_remediation_policy_epoch()` 读取；写入失败时降级为 `NULL`，但不 fail-close shadow 写入。跨 epoch 的聚合分析必须传入 `policy_epoch_id` 过滤。
- **CF-I05 成本模型统一**：`_compute_shadow_economics` 是唯一的 cost-model 入口，等同于 `filter_candidate_outcomes` 结算路径；两条结算路径产生的字段形状相同、`cost_model_version` 记录一致。
- **CF-I06 采集频率同步**：`_max_event_snapshot_age_seconds()` 从 `agent_runtime.scheduler.MARKET_DATA_INTERVAL_MINUTES` 派生；若 market_data cadence 变，反事实价格锚定窗口自动同步而非硬编码飘移。
- **CF-I07 硬安全门不可交易**：Risk、Execution、Live、credential、订单授权在任何反事实结果面前都是不动的。反事实模块**不可**能作为放宽这些的证据入口。

信任边界：本模块信任 storage 层（`storage.repositories.*shadow*`）与 `agent_shell.skip_policy` 提供的 policy epoch API；调用 `list_market_snapshots` / `market_ohlcv` 时对外部数据一致性要求由 [opportunity_outcome_contract.md](opportunity_outcome_contract.md) §7 覆盖。

## 6. Failure and recovery semantics

- `persist_shadow_batch` 单行失败不阻塞批次（warning log + 继续），但**批 id 已成型**，下游可容忍缺行；批本身不重试。
- `resolve_shadow_trades_for_position` 若 batch 中任一行失败（invalid entry / exit 价格不可得），**整个 batch 抛 `RuntimeError`**，由 `outcome_hooks` 记为 hook failure、进入 `outcome_hook_replay`。第一次尝试写下的 `exit_price` 锚点在下次 replay 时被 `anchor_shadow_trade_exit_price_if_pending` 通过 CAS 保留，从而幂等。
- `resolve_stale_shadows` 是 backfill 类作业，对 orphaned（`opportunity_id NULL` 或 parent close 时间丢失）的 pre-b9ad957 行做固定 horizon 结算；无 OHLCV 或坏 entry 计入 `skipped_no_ohlcv` / `skipped_bad_entry`，永不 fail-close 整批。
- `_current_policy_epoch` 异常时返回 `None`（see CF-I04）；shadow 写入本身继续。
- `build_calibration` 在 DB 未初始化 / 查询错误时返回带 `errors` 字段的 report，不 raise；这是只读诊断的一贯语义。
- `evaluate_active_shadow_experiments` 检测到 `filter_version` 中途换代时把实验 `status="invalidated"` 并落一条空 evaluation；不做部分归因。

## 7. Executable evidence

反事实模块的行为由以下测试锁定；每条以 `SpecClaim` 或 focused test 形式存在：

- `tests/test_edge_lab.py` — `persist_shadow_batch` 端到端与 first-exposure 语义。
- `tests/test_shadow_trade_economics_enrichment.py` — `_compute_shadow_economics` 与 cost-model 字段。
- `tests/specs/spec_pipeline_outcome_shadow_anchor.py` — Position 关闭锚点、CAS 幂等、historical snapshot 时间钉。
- `tests/specs/spec_pipeline_outcome_feedback.py` — hook replay + shadow batch 完整或全失败的语义。
- `tests/specs/spec_pipeline_paper_close_materialization.py` — paper 结算路径与 shadow 结算的对齐。
- `tests/test_filter_shadow_experiments.py` — 前瞻实验的 register / evaluate / status 转换。
- `tests/test_filter_replay.py` — 有界回放的边界 (`_MAX_ALTERNATIVES`, `_MAX_CANDIDATES`, `read_only`).
- `tests/specs/spec_filter_evidence.py` — 全模块的 `production_mutation_allowed=0` 与 `first exposure` 断言。
- `tests/test_outcome_consumers.py` — 消费者（含 `family_scorecard`）在 shadow_trades 存在或缺席时的降级。
- `tests/test_observer_prefilter_diagnostics.py` — filter_replay 与 filter_controller 报告的读写只读性。

## 8. Known gaps

只链接 GitHub Issues；目标设计放在 Proposals 中。

- Umbrella tracker：#497 (`[counterfactual][umbrella] Complete the L0-L5 counterfactual method ladder for the promoted module`)。
- Phase 2 / L4 覆盖扩展：`filter_experiments.py` 目前只支持 `family_ttl`；扩展到其他 filter kind 的工作在 #497 Phase 2 checkbox 内。
- Phase 3 / L5 反向联动：反事实证据 → policy epoch rotate 建议路径尚未存在，见 #497 Phase 3。
