# De-Shitify Batch — 2026-09-27

> 结构性 refactor 的单次批量记录：本 session 用 codex-gpt-6-astra
> 做 orchestrator，将 6 个大文件拆成 15 个 seam 模块 + 抽 40+ 个 helper +
> 删 1770+ 行 dead code。所有 commit 已合入 `origin/master`。

## 范围

一次 session 内 51 个 commit，全部推入 `master`。**没有语义漂移**：所有
helper/seam 的函数体 byte-identical，契约保留（reason strings /
transaction boundaries / authority separation）。

## Facade → Seam 拆分

| Facade | Before | After | Seams |
|---|---:|---:|---|
| `core/aletheia/storage/repositories.py` | 12915 | 6594 (-48.9%) | positions · universe · research · market_data · execution |
| `core/aletheia/diagnostics/doctor.py` | 4179 | 593 (-85.8%) | probes/content · probes/storage_trust · probes/environment · probes/data_quality · probes/trading |
| `extensions/observation_lab/canonical.py` | 4713 | 3327 (-29.4%) | canonical_adapters (Domain A) · canonical_lifecycle (Domain B) |
| `core/aletheia/agent_runtime/scheduler.py` | 4735 | 4630 (-2.2%) | scheduler_retention |
| `core/aletheia/analytics/profit_gradient_evidence.py` | 2107 | 2024 (-3.9%) | profit_gradient_evidence_utils |

拆分模式：facade `from <seam> import *` 再导出（backward-compat），
`_ADAPTERS` 等 singleton 保持文件间同一对象引用（`is` 检验通过）。writer
ownership guard 拒绝 seam 直接导入 generic writers；所以 writes 全部留在
facade，seam 只承担 reads/pure computation。

## 巨型单函数解构（同文件抽 `_` prefix helpers）

| 函数 | Before | After | Helpers |
|---|---:|---:|---|
| `reconcile()` (execution/reconciliation.py) | 767 | 123 (-84%) | 8 helpers |
| `build_recommendations()` (operator_recommendations.py) | 946 单函数 | 11 helpers | factory + 10 build_* |
| `ensure_candidate_planning_generation()` (storage/repositories.py) | 383 | 187 (-51%) | 3 helpers（事务边界保留）|
| `plan_next_candidate()` (agent_shell/entry_planner.py) | 430 | 226 (-47%) | 6 helpers |
| `review_portfolio_state()` (agent_shell/commands.py) | 638 | ~470 | 5 helpers（2 risky 未拆）|
| `build_position_lifecycle_story()` (analytics) | 697 | ~590 | 3 helpers（5 medium/risky 未拆）|

## Dead Code / Storage Model Cleanup

| 类型 | 数量 | 来源 |
|---|---:|---|
| `storage/models.py` Pydantic 类（grep-zero）| 10 | 保留 DB 表和迁移，只删 Python 定义 |
| `storage/repositories.py` grep-zero 函数 | 28 | T2 |
| `storage/repositories.py` test-only 迁到 `tests/fixtures/repo_test_helpers.py` | 45 | T8 |
| `scheduler.py::_get_observed_symbols` | 1 | T26 |
| `analytics/profit_gradient_evidence.py::_GRADIENT_OUTCOME_CONTRACT_VERSION` | 1 | T40 |
| `analytics/position_lifecycle_story.py` 无消费者查询段 | 81 行 | T3 |
| `data.py::/api/knowledge/research-notes` route | 1 | T45（`/dreaming-logs` 和 `/case-studies` 保留 —— 前者有 React consumer，后者响应 shape 不同）|
| `settings.py` unused imports + unreachable bounds | 5 项 | T44 |

## 活 Bug 修复

**`core/aletheia/execution/coordinator.py`**（`6c0406232`）：`_venue_positions()` 直接调
`adapter.get_positions()` 导致 adapter 绑定到 fresh event loop，下一个
`_run_coro(_reanchor_...)` 复用同一 adapter 触发 CCXT
`cross_loop_adapter_reuse` → 提交路径 `AMBIGUOUS` → `SubmissionUnknownError`。
两行修复：`_venue_positions` 改用 `_call_and_close`；`execute_order` 在
re-anchor 成功后创建新 adapter。修好 gen=29 open positions 无法通过 close
收敛的 Fedora runtime 问题。

## Orchestrator 模式（记忆已入库）

参见 `~/.claude/projects/-Users-yixian-Projects-aletheia/memory/feedback_orchestrator_wave_pattern.md`。

关键教训：

1. **严重任务用 codex-gpt-6-astra**，不用 luna / DS Pro / DS Flash pro
2. `codex --sandbox workspace-write` 会 auto-run 全 pytest → prompt 里明说
   "run only the specified test commands, do NOT run dev_check"
3. Codex 遇到已知环境 flaky 会 over-conservatively 全放弃 → prompt 里
   列可忽略的 flakes + 说清哪些测试才算真正回归
4. AST-scan spec（如 `spec_agent_contracts.py`, `spec_universe_pipeline.py`
   用 `inspect.getsource`）阻拦拆分。修 spec 让它跟随 imports 的工作已
   归入 #3561
5. Dead-code grep 必须包含 `frontend/src/`；同名 endpoint 响应 shape 可
   能不同
6. Domain seam 迁移遇到 writer-ownership guard 拒绝 → 保留 writers 在
   facade 是合理设计

## 未拆但已 audit（下次直接抄作业）

见 `docs/design/architecture/ARCHITECTURE.md#module-seams` 表格「未拆的
巨型函数」。这些在 `AI/plans/archive_2026-09-27/` 有对应 audit 报告。

## 相关 issue

- [#3561](https://github.com/iridyne/aletheia/issues/3561) — compile 10
  contract invariants into pytest audit gates（把散落文档的契约编译成
  会翻红的门禁；解锁 refresh() / init_scheduler 拆分）
- [#3562](https://github.com/iridyne/aletheia/issues/3562) — bring
  tier_unit down to 60s（fast tier 用 5+ 分钟；R20 竞品 74s；影响 AI
  迭代速度）

## 契约完整性验证

每一次 refactor 前审计报告都包含契约不变量清单。commit message 每次都
显式列出「preserved: X, Y, Z」。全 fast tier 8397 通过后再合入 master。
没有 flaky 通过（session 中有 4 次并发跑时误报的 pre-existing failure，
串行 retest 都通过；证据 `bpxz7fdin.output`, `b9is5163j.output`,
`b1k65hrxg.output`）。
