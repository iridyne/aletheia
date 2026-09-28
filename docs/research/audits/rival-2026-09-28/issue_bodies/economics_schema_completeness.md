# [economics][schema] Partial schema skips repair and crashes required-table callers

## Why

共享 economics 初始化只检查少量表，却在后续假定其他自有表存在。支持的缺表恢复场景不能返回“已初始化”后才以 no such table 崩溃，也不能隐式提交调用方事务。

## Current Facts / Evidence

复核日期：2026-09-28。固定 master 源码：`a998070130d2ab892e9ba89b7de48886a1a708e4`。本条所列根因文件已与前轮审计逐文件比对，缺陷相关实现未变化。证据属于源码与历史隔离探针，不是生产事故或运行环境观测。领取时仍须在最新 master 上复核。

- [core/aletheia/economics/schema.py:344](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/economics/schema.py#L344) 当前 required_tables 为六张，不是旧 issue 的五张；DDL 与后续读写还依赖 llm_context_artifacts、economics_settings、tool_cost_profiles 等对象。
- 六张哨兵存在但 llm_context_artifacts 不存在时跳过 DDL，随后 PRAGMA 为空并对不存在表 ALTER；economics_settings 缺失会在 backfill marker 查询失败。
- ensure_economics_schema 被 API/CLI/tracing 共用；正常新库测试无法证明部分 schema 路径正确。旧 viability_reports 等对象必须按当前 DDL/消费者核对，不恢复已退休表。

## Dependencies / Activation Conditions

无跨 issue 的实现前置条件。

## SDD Depth

D0 — 恢复已有行为契约。Contract Delta: NONE。不得通过修改 Contract 将缺陷合法化。

## 修复目标与技术落点

以当前 ECONOMICS_DDL 和实际消费者为准补齐必需 schema 检查，复用既有幂等 DDL、add-column、trigger 更新边界，返回前验证承诺对象。既有事务、SQLite writer permit/并发语义保持。

只对支持的缺失对象执行非破坏性恢复；不能安全修复时在 shared boundary 给出明确 schema unavailable/error，不能留到 route 首个查询爆出虚假初始化成功。executescript 会隐式 commit，所以 caller transaction 内仍拒绝必要 DDL、不得提交调用方数据。不得在每个路由补 CREATE TABLE。

## Non-goals

不改历史成本/预算/报价记录，不启动真实 schema 迁移，不新建通用 schema registry，不重建 retired viability 主线，不绕过中央 migration ledger。

## Affected Authorities

[core/aletheia/economics/schema.py](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/economics/schema.py) 的幂等初始化及事务边界；[docs/operations/development/agent-guides/STATE_AND_DATA.md](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/docs/operations/development/agent-guides/STATE_AND_DATA.md)

## Verification — 本项验收

- 扩展现有 `tests/test_economics_budget_contract.py` 的 schema fixture 和 schema 相关测试：初始化后逐个删除当前遗漏必需表，保留六张哨兵，ensure 后对应 reader 可工作或在约定边界明确拒绝。
- llm_context_artifacts、economics_settings 的具体复现必须覆盖；完整库与连续 ensure 幂等，已有行不变。
- caller transaction 内要求修复时拒绝，证明未 commit/rollback 调用者写入；缺列、触发器升级同样守边界。
- 两个隔离连接并发首次 ensure 不留下部分 schema/重复列；使用确定同步，不在真实 app.db 试验。

## 执行授权与交付边界

Owner 于 2026-09-28 要求将已审查修复整理为可独立领取的 issue，并明确期望领取 Agent 完成修复、验证、PR 和合并到 `master`。本条记录该范围内的授权：承担本 issue 的 Agent 应完成该 D0 修复及必要测试、自审、提交、推送、review-ready PR，并在仓库适用门禁满足后合并到 master、核实实际合并结果；不需要在每个常规工程步骤重复请求批准。**Issue 本身不覆盖更高优先级约束，也不授予新的 D1/D2、部署、重启共享 Gateway、修改真实凭证、Live 解锁、历史数据重写或资格窗口重置权限。**

从领取时最新 master 创建隔离分支/工作区，先读该版本 AGENTS.md、SYSTEM_MAP 和匹配指南，核对本 issue 的函数与生产 caller。本文固定 SHA 是证据，不要求从旧 SHA 开发。若该根因已有在途 PR，复用/协调，避免两个 Agent 同时修；若当前 master 已满足，提交实际代码及验收证据说明，不制造空 PR。保留他人改动。

## 验证与合并完成条件

- 使用合成数据、临时目录/SQLite、替身 provider。不得读取真实密钥、连交易所或使用主库来复现。
- 为下文验收建立最小行为回归，证明原行为失败、修复后同一条件通过；不要只匹配源码字符串，也不要跳过或弱化失败断言。
- 首轮反馈执行 `uv run python scripts/dev_check.py`，再执行下文聚焦用例及改动所需的本地 PR 验证。具体 selector 以当前真实测试收集结果为准，零收集/skip 不算通过。不要在 macOS 跑全套；全套不是本 issue 的默认要求。
- 按 `.github/PULL_REQUEST_TEMPLATE.md` 填 PR body，执行 `uv run --no-sync python scripts/ci_preflight.py --pr-body-file <实际文件>` 和当前 `scripts/ci_local.py` 对该变更要求的本地检查；保留命令、结果、失败日志及其对应 HEAD。此处 `<实际文件>` 是领取者创建的 PR body 文件，不能原样执行。
- 远程 GitHub CI 在本次复核的 root AGENTS.md 中已退役，不能启动或等待它。遵循领取时适用规则；若分支保护仍要求不可用检查，报告具体阻塞，不绕过、不改保护、不伪造绿灯。
- 按 PR_REVIEW_LIFECYCLE 处理当前 HEAD 上的有效反馈，记录 `MERGE_READY_SHA`。合并前重新核对 PR HEAD、目标 master、测试证据及硬阻塞；若解决冲突产生新代码，重新验证受影响路径。
- 完成必须提供：修复 commit、PR URL、实际 `MERGED` 状态、merge commit SHA、更新后的 master 已包含该 merge 的证据，以及本 issue 验收结果。开启自动合并、PR 可合并、测试通过均不等于已合并。合并并核实后关闭 issue；真实门禁阻塞时保持未完成并报告，不能提前关闭。

Runtime / Economic Verification：本条交付是仓库级契约修复与合并，不要求部署或真实经济实验；明确记录 Fedora 未验证，不声称线上已修好。

<details>
<summary>历史 issue 正文（保留原始证据；旧路径、数量、建议以本次复核为准）</summary>

## Review baseline

Read-only schema/repository review of live `origin/master@c86e554cfe0ae6e6311d63a93479420770dcd595` on 2026-08-22. CodeGraph was used first for `ensure_economics_schema()` and its API/CLI callers; cited source was then verified with `git show origin/master`. No code, database, service, Runtime, credentials, or exchange state was changed.

## Problem

`ensure_economics_schema()` describes itself as an idempotent schema initializer, but its completeness test covers only 5 of the 10 tables created by `ECONOMICS_DDL`. If one omitted table is missing while those five sentinels remain, the initializer skips the DDL and either crashes inside its own follow-up logic or returns a connection that immediately fails in the caller.

This makes partial economics upgrades/restores non-recoverable through the shared initializer and can take cost/viability APIs offline while the main migration ledger remains current.

## Trigger

Start from an initialized economics schema, then lose one omitted object while preserving the five sentinel tables:

- Drop `viability_reports`, then call `GET /api/economics/viability/reports`; or
- Drop `llm_context_artifacts`, then call any path using `ensure_economics_schema()`; or
- Drop `economics_settings`, then call an economics path after `agent_tool_calls` gained inference linkage.

For `viability_reports`, the initializer returns without recreating it and the route fails at its first SELECT. For `llm_context_artifacts`, `PRAGMA table_info` returns no columns, the code attempts `ALTER TABLE llm_context_artifacts ...`, and raises `no such table`. For `economics_settings`, the backfill-marker SELECT fails.

## Evidence

- `aletheia/economics/schema.py:16-235` creates 10 tables: `economics_settings`, `pricing_snapshots`, `tool_cost_profiles`, `runtime_cost_events`, `llm_inference_calls`, `llm_structured_attempt_outcomes`, `llm_context_pair_reviews`, `viability_reports`, `economics_budgets`, and `llm_context_artifacts`.
- `ensure_economics_schema()` checks only 5 names at lines 386-392. It omits `economics_settings`, `pricing_snapshots`, `tool_cost_profiles`, `viability_reports`, and `llm_context_artifacts`.
- Lines 422-427 execute the complete DDL only when one of those five sentinels is absent.
- Lines 429-449 assume `llm_context_artifacts` exists and attempt column ALTERs when `PRAGMA table_info` returns an empty set.
- Lines 462-469 assume `economics_settings` exists before reading/writing the tool-backfill marker.
- `aletheia/server/routes/economics.py:194-222` trusts the initializer and immediately queries `viability_reports`, one of the omitted tables.
- Existing economics tests exercise fresh initialization, legacy additions, transaction refusal, and repeated calls, but the live test inventory has no case where an omitted table is absent while all five sentinels remain.

## Expected vs actual

**Expected:** the shared economics schema boundary either proves every table/column/index/trigger it promises, repairs a supported partial legacy state atomically outside caller transactions, or fails with a typed explicit schema error before returning.

**Actual:** five tables are treated as proof of the entire schema. Missing omitted tables become later raw `sqlite3.OperationalError` failures, and the owner-facing viability/cost surfaces can disappear independently of main schema status.

## Impact

- Profitability/viability reports can return 500 after a partial restore or interrupted historical schema initialization.
- Cost tracing can stop recording because a missing settings/profile table also invalidates the trigger path.
- Operators receive no explicit schema-state reason; the main v37 ledger can still report current.
- Repeated calls do not self-heal the state despite the initializer's idempotent contract.

## Minimal shared repair path

1. Define one canonical economics schema inventory from the existing DDL: all 10 required tables, required additive context columns, correctness indexes, and the tool-cost trigger when its source columns are available.
2. Make `ensure_economics_schema()` compare against that complete inventory. For supported missing-object states, run the existing idempotent DDL/additive column path once under the existing SQLite writer permit and outside any caller transaction.
3. After initialization/repair, verify the complete inventory before returning; raise one typed economics-schema error with bounded missing-object details if the state cannot be safely repaired.
4. Keep all economics API/CLI/tracing callers on this single shared boundary. Do not add per-route `CREATE TABLE` logic.
5. Integrate the inventory with the physical-schema verification tracked in #1724, while retaining the economics initializer's own correctness for isolated callers/tests.

## Focused regression proof

- Initialize the full schema, drop each currently omitted table one at a time while retaining all five sentinels, and assert a subsequent ensure restores/proves it (or returns the agreed typed fail-closed error) before callers query it.
- `GET /api/economics/viability/reports` after a missing `viability_reports` table must not leak a raw `no such table` 500.
- A missing `llm_context_artifacts` or `economics_settings` must not enter the current ALTER/SELECT-on-absent-table path.
- Two concurrent first-use calls must leave one complete schema and trigger, without duplicate-column/partial-DDL errors.
- Calling ensure inside an active transaction must continue to refuse DDL and must not commit the caller's work.

## Classification

Class A accounting/runtime state repair. Severity high because the shared cost/viability persistence boundary can declare initialization complete while required evidence tables are absent; no trading policy or observation threshold changes are required.

## Deduplication

The all-state Issue inventory was searched for economics schema/migration/table defects before filing and no match was found. #1724 covers the central migration ledger's lack of physical-schema verification; this Issue is the independent incomplete-sentinel/self-repair bug in the separate runtime-created economics schema.


</details>
