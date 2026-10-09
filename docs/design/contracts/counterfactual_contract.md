# Counterfactual Analysis Contract

> 状态：当前契约；2026-10-09 按 #3704 / #3708 的 shadow 写路径退役对齐（退役部分见 [history/shadow_trades_write_path.md](../history/shadow_trades_write_path.md)）。
> 范围：`extensions/counterfactual/` 子包为决策链提供的可事后检验的固定时点结果、有界回放、前瞻实验与描述性诊断。
> 不包含：策略执行、风控限额、live 授权、订单路由、自动调参。任何"从反事实结论直接触发行为改变"的路径都不属于本合同。

本合同是四份既有文档在反事实模块视角下的聚合：

- [causal_counterfactual_research_guidance.md](../subsystems/causal_counterfactual_research_guidance.md) — 全框架方法阶梯 L0-L5。
- [opportunity_outcome_contract.md](opportunity_outcome_contract.md) — 固定持有期结果字段与结算路径。
- [filter_evidence_contract.md](filter_evidence_contract.md) — 过滤器证据的身份与去重语义。
- [decision_lifecycle_contract.md](decision_lifecycle_contract.md) — 决策生命周期中反事实确认的位置。

以上四份为源约束；本合同不新增约束，只把源约束在**反事实模块内部**的具体落法固定下来。若与源约束矛盾，源约束优先。

实验 authority 为 `legacy` 时，本合同下列 `filter_shadow_experiments` /
`filter_shadow_evaluations` 条目描述兼容路径。authority 为 `lab` 时，注册、
采集、评估与报告改由 Observation Lab 的 canonical
`observation_windows`/`observation_units`/`observation_exposures`/
`observation_evaluations` 处理；旧表只保留迁移与历史投影用途，不能成为
活动生命周期的写入或读取权威。

## 1. Purpose and current scope

反事实模块回答的是：

> 在决策时可见的身份、方向、价格与成本假设下，若暴露于该候选，固定 horizon 后会得到什么成本后结果？

它使一个决策可被事后检验，但**不是**实盘 PnL，也不是因果证明。除人工治理外，任何生产行为改变（policy epoch rotate、阈值调整、guard 生成、authority grant）都不属于本合同的授权范围。

方法阶梯与代码位置对应：

| Level | 含义 | 模块 |
|---|---|---|
| L0 | 完整性：决策身份、first exposure、版本/epoch、结果键 | 历史 `shadow_trades` 行在写入时已绑定（写路径随 #3704 退役）；读取经 `counterfactual/trades.py::list_deduped_shadows`，`policy_epoch_id` 是硬字段 |
| L1 | 固定时点结果：预注册 horizon 的成本后结果 | canonical `filter_candidate_outcomes`；历史 keyed `shadow_trades` 行只读投影，不再有结算器 |
| L2 | 描述性诊断：在可比 cohort 中的差异 | `counterfactual/calibration.py::build_calibration` |
| L3 | 有界回放：固定历史、版本和参数，产出可检验假设 | `counterfactual/filter_replay.py` |
| L4 | 前瞻实验：预登记分配、观察 gate | `counterfactual/filter_experiments.py`；`ranker_confidence_gate` 的运行时解耦见 [observation_lab_contract.md](observation_lab_contract.md) |
| L5 | 人工治理 | 不在模块内。反事实证据供 owner 审阅，产生新 policy epoch 的决定不由模块发起。 |

任何层级都不能跳过前一层；`L0/L1` 不足时的诚实结论是 `insufficient_evidence`，绝不是"放行"或"封锁"。

## 2. Entry points

生产入口只有下述几个函数；调用它们的位置由 [decision_lifecycle_contract.md](decision_lifecycle_contract.md) 与 [scheduler_contract.md](scheduler_contract.md) 约束：

- `counterfactual.filter_experiments.register_replay_ready_experiments(replays)` — 由 `analytics.filter_controller` 在 shadow 观察周期调用。
- `counterfactual.filter_experiments.evaluate_active_shadow_experiments()` — 按 authority 路由到 legacy 快照或 Lab-owned Evaluation。
- `counterfactual.filter_experiments.build_filter_shadow_experiment_report(limit)` — CLI / API 只读消费对应 authority 的投影。
- `counterfactual.calibration.build_calibration(days, symbols, policy_epoch_id)` — 由 `scripts/analysis/shadow_threshold_calibration.py` 与内部报告 API 调用。
- `counterfactual.trades.list_deduped_shadows(days, symbols, policy_epoch_id, columns)` — 所有跨候选聚合的历史 shadow 消费必须走这里，去重按 `outcome_candidate_key`；表不存在时返回空。

原 shadow 写入与结算入口（`persist_shadow_batch`、`resolve_shadow_trades_for_position`、`settle_linked_shadow_outcomes`、`resolve_stale_shadows`）及 `shadow_resolver` job 已随 #3704 / #3708 删除，见 [history/shadow_trades_write_path.md](../history/shadow_trades_write_path.md)。

## 3. Durable state and consumers

legacy authority 下，反事实模块的兼容持久化状态在下列三张表中；schema 见
[data_model.md](../data/data_model.md)。Lab authority 下，前瞻 filter-shadow
生命周期不再写入这两张实验表，而写入 Observation Lab 的 canonical 表：
`observation_windows`、`observation_units`、`observation_exposures`、
`observation_evaluations` 与 `observation_retention_claims`。

- **`shadow_trades`** — 历史固定 horizon 反事实账本，只读；唯一生产写入者 `paper_trader` 已随 #2945 删除，写路径随 #3704 退役，当前没有写入者或结算器。
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
| `analytics.filter_controller.evaluate_active_shadow_experiments` | `filter_shadow_experiments` / `_evaluations` | 前瞻实验周期评估 |

**跨候选的聚合消费必须走 `list_deduped_shadows`**（按 `outcome_candidate_key` 去重）。单-opp / 单-batch 的查询可以直接读表；因其查询模式天然不跨候选，不受长寿命候选过度加权影响。

Alpha Audit 的集成边界是显式的：Ranker 与 System Operator 共享 canonical
`24h` `filter_candidate_outcomes.net_return_pct`；Learning 复用自身已消费的
fixed-look authority verdict；Exit 保留其 Position 配对、quote-currency
estimand。不同 estimand
不做原始 delta 排名；统一跨层净经济基准尚无现行 Issue（#803 已关闭）。

## 4. Observable result

- `shadow_trades.pnl_pct` 与 `net_pnl_pct`：在成本模型（费用 + 点差 + 资金费）下的方向性净收益，单位为百分比数值。`net_pnl_pct = NULL` 而 `pnl_pct != NULL` 表示 gross-only（例如 perp 缺资金费），不得进入学习。
- 带 `outcome_candidate_key` 的历史 `shadow_trades` 以 canonical `24h` `filter_candidate_outcomes` 为结果权威；`settlement_price_source` 沿用产生该结果的实际结算来源，不能由消费方重算或改写。
- `filter_shadow_evaluations.disposition = "ready_for_review"` 是本合同下的**最强观察结论**，它只表示证据 gate 已达标、值得人工审阅，从不表示"应该改变生产行为"。改变生产行为须走新 policy epoch + owner 批准，不属于本合同。
- `build_calibration` 报告是描述性诊断，读者应联合 `avg_net_pct` / `median_net_pct` / `win_rate_pct` 判读；低 n 档必须视为不确定。

## 5. Invariants and trust boundaries

以下不变量在任何执行路径都必须成立；对应的执行证据在 §7：

- **CF-I01 观察 only**：legacy `filter_shadow_experiments.production_mutation_allowed = 0` 是 schema 层硬字段；Lab canonical roots 使用 `authority_mode='observe_only'`。任何路径都不得把 filter-shadow 变成 Paper treatment——用 spec 断言 code 与 migrations 不含此更新。
- **CF-I03 结果键完整**：`outcome_candidate_key` 是所有跨候选去重的唯一入口；聚合消费必须调 `list_deduped_shadows`。
- CF-I02 / CF-I04 / CF-I05 / CF-I06 约束的是已删除的 shadow 写入与结算路径，随 #3704 退役，原文见 [history/shadow_trades_write_path.md](../history/shadow_trades_write_path.md)。读取历史行跨 epoch 聚合时仍必须传入 `policy_epoch_id` 过滤。
- **CF-I07 硬安全门不可交易**：Risk、Execution、Live、credential、订单授权在任何反事实结果面前都是不动的。反事实模块**不可**能作为放宽这些的证据入口。

信任边界：本模块信任 storage 层与 canonical `filter_candidate_outcomes`；外部数据一致性要求由 [opportunity_outcome_contract.md](opportunity_outcome_contract.md) §7 覆盖。

## 6. Failure and recovery semantics

- **已退役（#3704 / #3708）**：shadow 写入路径整体删除，其失败与恢复语义不再存在——`persist_shadow_batch` 的批级容错、`resolve_shadow_trades_for_position` 的 batch `RuntimeError` + `outcome_hooks` hook failure + `outcome_hook_replay`、`anchor_shadow_trade_exit_price_if_pending` 的 `_current_price` CAS 锚点幂等，以及 `resolve_stale_shadows` 的 `skipped_no_ohlcv` / `skipped_bad_entry` backfill 结算。`shadow_trades` 表是等待 DROP 迁移的墓碑，不再有 owner 结算它。
- `build_calibration` 在 DB 未初始化 / 查询错误时返回带 `errors` 字段的 report，不 raise；这是只读诊断的一贯语义。
- `evaluate_active_shadow_experiments` 检测到 `filter_version` 中途换代时把实验 `status="invalidated"` 并落一条空 evaluation；不做部分归因。

## 7. Executable evidence

反事实模块的行为由以下测试锁定；每条以 `SpecClaim` 或 focused test 形式存在：

- `tests/test_edge_lab.py` — Position 关闭时的 BTC benchmark 记录与 family scorecard 投影。
- `tests/specs/spec_pipeline_outcome_feedback.py` — eligible close 的 durable hook job（enqueue / replay / 并发 lease / 幂等）。
- `tests/test_filter_shadow_experiments.py` — 前瞻实验的 register / evaluate / status 转换。
- `tests/test_filter_replay.py` — 有界回放的边界 (`_MAX_ALTERNATIVES`, `_MAX_CANDIDATES`, `read_only`).
- `tests/specs/spec_filter_evidence.py` — 全模块的 `production_mutation_allowed=0` 与 `first exposure` 断言。
- `tests/test_outcome_consumers.py` — 消费者（含 `family_scorecard`）在 shadow_trades 存在或缺席时的降级。
- `tests/test_observer_prefilter_diagnostics.py` — filter_replay 与 filter_controller 报告的读写只读性。

## 8. Known gaps

只链接 GitHub Issues；目标设计放在 Proposals 中。

- L4 覆盖：`filter_experiments.py` 当前只支持 `family_ttl`；扩展到其他 filter kind 尚无现行 Issue（原 umbrella #497 与 Observation Lab v1 #1072 均已关闭）。
- Experiment 权威切换（legacy → Lab）未完成，见 #2737 与 #3808。
- L5 反向联动：`counterfactual/advisories.py` 只产出 observe-only 的 policy epoch rotate 建议，不自动执行。

Paper Internal 的旧经济可行性生成器已[归档](../history/local_simulated_viability.md)，不再作为当前反事实消费者。

旧模拟账户的 Risk／Execution 贡献测量及整策略盈利资格编译器已[归档](../history/local_simulated_qualification.md)。
