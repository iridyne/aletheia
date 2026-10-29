# Orchestrator 自查：scheduler.py 实际注册的 job id（权威，直接 grep core 代码）

与 SYSTEM_MAP.md 对照：
- SYSTEM_MAP 列出的 job 基本能在代码里找到：market_data, price_feed, data_collectors,
  data_collector_context, exchange_sync, portfolio_snapshot, position_path_observer,
  universe_refresh, outcome_settler, outcome_hook_replay, cleanup, ohlcv_nightly_backfill,
  ohlcv_active_backfill, news_fetch, news_analyze, demo_capabilities, venue_margin_facts,
  pi_trading_decision, position_manager, position_reconcile, tpsl_fill_check,
  post_trade_reviewer, risk_critic, equity_observer, prediction_observer, guard_promotion,
  dreaming_daily, serenity_startup_catchup, learning_startup_catchup, insight_synthesizer,
  confidence_calibration, filter_controller_observe — 全部命中，职责描述基本准确。

- SYSTEM_MAP **未列出**但代码里确实注册的 job（遗漏，不是虚构）：
  reconciliation(2min), health_check(10min), pricing_reference_refresh,
  universe_scanner_audit_reconciler(5min), observation_lab_bridge(1min),
  funding_audit_reconciler, broker_funding_settler, persona_rollout, lesson_reviewer,
  daily_summary, connectivity_monitor。
  → SYSTEM_MAP 的运行托管表不完整，应补齐，尤其 observation_lab_bridge（关系到 Observation
    Lab 四份合同是否真的在跑）、persona_rollout（关系到 persona sizing learning 行是否准确）、
    lesson_reviewer（学习范围内，由另一会话处理，不需我动）。

此文件供最终综合报告引用，不是独立审查产出。
# 附带发现（不属文档范围，但应开 issue）

`.git/hooks/pre-commit`（.git/hooks 被全部 worktree 共享）已失效：
- 它执行 `uv run python scripts/ci/dev_check.py --staged --no-tests --no-types`，
  但 dev_check.py 的参数只有 `--base/--no-secrets-scan/--no-tests/--no-types/--fast`，
  没有 `--staged` → argparse 直接退出 2。
- 它的安装源 `scripts/hooks/pre-commit` 在当前树里已不存在（git ls-files 无此路径）。
- 后果：任何 worktree 里的 `git commit` 都被钩子拦住，必须 `--no-verify`，
  因此 secret 扫描/空白/ruff 的廉价门禁在提交时实际从未执行。
- 根因推测：仓库从 git 迁到 jj 时 dev_check.py 的 diff 视图从 staged 改为 `--base` 的
  jj revset，钩子与安装源一起被删/改，但已安装的副本留在 .git/hooks 里。
- 建议：要么恢复一个 jj 兼容的 hook 源文件并改成无参调用，要么明确记录"jj 工作流下不使用
  git pre-commit 钩子"，并让 dev-init 删除旧副本。
