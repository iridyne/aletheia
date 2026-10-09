# Retired shadow_trades write path

> 2026-10-09 迁入 history：原为 `docs/design/contracts/counterfactual_contract.md` 中描述
> shadow 写入与结算的条目。唯一生产写入者 `paper_trader` 已随 #2945（2e69baae9）删除，
> 写路径与 `shadow_resolver` job 随 #3704（384cd0cb2）/ #3708 退役；`extensions/counterfactual/trades.py`
> 只保留只读的 `list_deduped_shadows`。`capture_frozen_units_for` 与
> `GET /analytics/shadow-trades` 在当前代码中也已不存在。当前契约见
> [counterfactual_contract.md](../contracts/counterfactual_contract.md)。

## Former entry points (§2)

- `counterfactual.trades.persist_shadow_batch(top_candidates, selected, top_n=5)` — 由 `paper_trader` 在 ranker 决策后同批调用；写入 Top-N shadow 行（含选中与未选中）。
- `counterfactual.trades.resolve_shadow_trades_for_position(position)` — 由 `portfolio.outcome_hooks` 通过常量 `SHADOW_TRADES_HOOK = "shadow_trades"` 在 Position 结束时调用；当前只处理没有 `outcome_candidate_key` 的 legacy 行。
- `storage.repositories.settle_linked_shadow_outcomes(...)` — 固定结果结算器在同一候选的 canonical `24h` `filter_candidate_outcomes` 成熟后，把完全相同的成本后结果投影到带 `outcome_candidate_key` 的 Shadow 行。
- `counterfactual.trades.resolve_stale_shadows(min_age_hours, horizon_hours, limit, dry_run)` — 由 scheduler job `shadow_resolver`（interval=6h，见 [scheduler_contract.md](../contracts/scheduler_contract.md)）调用。
- `counterfactual.filter_experiments.capture_frozen_units_for(experiment)` — legacy authority 下由 `analytics.filter_controller` 冻结兼容表单元；Lab authority 下同一入口投影到 canonical Lab 适配器，规则见 [observation_lab_contract.md](../contracts/observation_lab_contract.md)。

## Former durable state and consumer (§3)

- **`shadow_trades`** — 每 tick、每候选一行的固定 horizon 反事实账本。写入者是 `persist_shadow_batch`；带键行由 canonical `24h` 候选结果投影结算，legacy 无键行才走 `resolve_shadow_trades_for_position` / `resolve_stale_shadows`。
- 消费者 `server.routes.data GET /analytics/shadow-trades`（`shadow_trades` 只读 API）。

## Former invariants (§5)

- **CF-I02 First exposure 固定**：`shadow_trades.entry_price` / `entry_spread_bps` / `funding_rate_at_entry` / `regime_tag` / `policy_epoch_id` 在 `persist_shadow_batch` 时一次性快照，此后只被 append-only 结算字段扩充；不得回填。
- **CF-I04 Epoch 分层**：`shadow_trades.policy_epoch_id` 由 `persist_shadow_batch` 通过 `_current_policy_epoch()` 从 `agent_shell.skip_policy.ensure_skip_remediation_policy_epoch()` 读取；写入失败时降级为 `NULL`，但不 fail-close shadow 写入。跨 epoch 的聚合分析必须传入 `policy_epoch_id` 过滤。
- **CF-I05 成本模型统一**：`_compute_shadow_economics` 是唯一的 cost-model 入口，等同于 `filter_candidate_outcomes` 结算路径；两条结算路径产生的字段形状相同、`cost_model_version` 记录一致。
- **CF-I06 采集频率同步**：`_max_event_snapshot_age_seconds()` 从 `agent_runtime.scheduler.MARKET_DATA_INTERVAL_MINUTES` 派生；若 market_data cadence 变，反事实价格锚定窗口自动同步而非硬编码飘移。
