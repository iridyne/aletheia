# Observation Lab v1 Contract (POC)

> 状态：Issue #1072 POC 首次合入 (2026-08-17)；Clusters 1-5 build-out 2026-08-19。
> 范围：`ranker_confidence_gate` prospective 前瞻实验的运行时解耦；主 Runtime 可高频迭代 `master`，已登记 Observation 仍按冻结身份继续收集证据。加上 Clusters 1-5 引入的 multi-window runner + `ExecutionManifest` + typed exposure fact + joint checkpoint 基础设施（详见 §11）。
> 不包含：新 assignment 语义、`family_ttl` / `observer_pre_filter` 迁移到 adapter identity 模型、生产表迁移或删除、新 Web UI、新 scheduler。

**Companion docs**:
- [observation_lab_v1_design.md](observation_lab_v1_design.md) — 权威 v1 实施设计 + Fedora 部署计划（这是部署前必读）。
- [observation_lab_backup_contract.md](observation_lab_backup_contract.md) — app.db + lab.db + manifest 三方一致性契约。
- [../../research/audits/UFC_OWNERSHIP_MAP_2026-08-18.md](../../research/audits/UFC_OWNERSHIP_MAP_2026-08-18.md) — Packet 0 UFC ownership 审计。

本合同是 [counterfactual_contract.md](counterfactual_contract.md) 的 L4 前瞻实验层在 Issue #1072 后的增量约束。若与源约束矛盾，源约束优先。若与 v1 design 冲突，v1 design（更新更晚）优先。

## 1. Why (第一性原理)

一个可检验 Observation = 冻结假设 + 冻结 treatment/control + 冻结 first-exposure inputs + canonical 结果契约 + 冻结 verdict rule。这些条件独立于"主 Runtime 当前 HEAD"。

Issue #1072 前的耦合：`filter_experiments._production_version_changed` 只要 `filter_decisions` 里出现任何 `filter_version` 变化就 invalidate — 包括与 ranker-confidence 语义完全无关的变化。这使得 dogfood 主 Runtime 与 Observation 的节奏矛盾。

## 2. Reuse, not fork

Issue #1072 body 提议的三张新表 (`observation_specs / observation_units / observation_evaluations`)、独立 `lab.db`、独立 CLI 与 systemd timer 都不在 v1 范围内。字段映射：

| Issue proposal | Existing | 复用/新增 |
|---|---|---|
| `observation_specs` | `filter_shadow_experiments` | 复用；只加一列 `adapter_identity_hash` |
| `observation_evaluations` | `filter_shadow_evaluations` | 完全复用（已 append-only） |
| `observation_units` | 之前不存在 | 新增 `filter_shadow_frozen_units`（POC 唯一新表），落在**独立 `~/.aletheia/observation-lab/lab.db`** 文件而非 `app.db`（Issue #1072 body "建议独立状态根"）。物理隔离让生产 migration 无法回改 Lab 快照。 |
| assignment salt / 随机臂分配 | 无 | **禁止引入**；`ranker_confidence_gate` 仍是 first-exposure cohort，不是 randomized A/B |
| runner | `filter_controller.observe()` | 复用；`register → capture → evaluate` 一次 cycle |
| CLI | `build_filter_shadow_experiment_report` + `aletheia observe {list,status,sync,evaluate,register,close}` | 复用只读 report；新增 6 条 observe-only 子命令（见 `aletheia/cli/observe_cmd.py`） |

## 3. Adapter causal identity vector

每个参与 Observation Lab 的 Source Adapter 必须在 `filter_experiments._ADAPTER_CAUSAL_IDENTITY` 里手工声明它真正消费的语义字段。POC 首个条目：

```python
"ranker_confidence_gate": {
    "adapter_id": "ranker_confidence_gate",
    "adapter_version": "v1",
    "cohort_semantics_version": "prospective_first_exposure/v1",
    "evaluator_version": "shrunk_mean_edge_delta/v1",
    "outcome_contract": "filter_candidate_outcomes/net_return_pct",
    "input_schema": (
        "input_snapshot.confidence",
        "parameter_snapshot.minimum",
    ),
}
```

规则：

- **必须手工声明**。禁止用 git diff、路径分类、LLM 判断或 dependency graph 自动推断（Issue #1072 评审 §4）。
- **`filter_version` 只是 provenance**。它记录在 `filter_shadow_experiments.filter_version` 与 `filter_shadow_frozen_units.source_runtime_commit`，但不参与 identity 比较。
- **hash 变化 = new cohort**。`_adapter_causal_identity_hash()` 是这个 dict 的 SHA-256。任一字段变（含 `adapter_version` bump）都会使当前 collecting 实验 fail closed 为 `invalidated` with `invalidation_reason='adapter_causal_identity_changed'`。
- **历史 evaluation 不可重写**。fail closed 只终止当前 cohort；`filter_shadow_evaluations` 保持 append-only。
- **未在此表内的 filter**（现有 `family_ttl`、`observer_pre_filter`）保留旧的 `filter_version` 严格失效；Phase 2 才迁移。

## 4. Frozen first-exposure snapshots

`filter_shadow_frozen_units` 落在 **`~/.aletheia/observation-lab/lab.db`**（由 `aletheia.observation_lab.db` 首次访问时初始化）。物理隔离让 `app.db` 的 migration 完全绕开 Lab 快照。每行是一个 immutable ObservationUnit：

- `outcome_candidate_key`（+`experiment_id` UNIQUE）唯一定位；
- `decision ∈ {passed, rejected}`（Lab 只承认已 rejected/passed 的 first exposure）；
- `input_snapshot`、`parameter_snapshot` 复制自 first exposure 那一刻，此后**Production schema 变化不影响它**；
- `first_evaluated_at` 是 Lab 时间锚；
- `source_runtime_commit` 仅 provenance（存了当时的 `filter_version`）。

Capture 语义（`filter_experiments.capture_frozen_units_for`）：

- 只对 `_ADAPTER_CAUSAL_IDENTITY` 已声明的 adapter 生效；
- 用一个不按 `filter_version` 分区的 first-exposure CTE — 一个候选跨版本重现时，取**最早**那次；
- INSERT OR IGNORE 保证幂等；同一 candidate 只在一个实验里冻结一次。

Rebuild 语义（`filter_replay.build_ranker_confidence_replay_from_frozen`）：

- `_score_ranker_alternative` 是与 live replay 共用的评分核心，保证 evidence equivalence；
- 输入：`list_frozen_units(experiment_id)` 从 lab.db 读；outcome 由 `_fetch_outcomes_for` 从 app.db 批量读，在 Python 里 join（跨库 JOIN 用 SQL 不方便，批量+内存 join 一次 O(1) round-trip）；
- 若某 Observation 尚无 frozen units（第一次 cycle 还没跑到 capture），rebuild 回退到 live replay。

## 5. Physical read-only 边界

`aletheia.storage.sqlite.open_readonly_snapshot(path=None)` 打开 `mode=ro` URI + `PRAGMA query_only=ON`。写入必须在 SQLite 层就失败，不能靠约定。适用于 `app.db` 与 `lab.db` 两个文件（都是普通 SQLite）。

POC 运行时 Lab 与主 Runtime 同进程：Lab 写 lab.db（`aletheia.observation_lab.db.get_lab_db`），读 app.db 中的 `filter_decisions` 与 `filter_candidate_outcomes` 通过普通 `get_db()`。真正的 out-of-process 拆分未来才做，届时把 app.db 读侧全部改为 `open_readonly_snapshot` 是最小改动。

### 5.1 CLI Surface

`aletheia observe` 是 Lab 的操作面，全部 observe-only：

| 命令 | 作用 | 权限 |
|---|---|---|
| `aletheia observe list [--status …] [--json]` | 列出已注册 Observation + 冻结单元数 | 只读 |
| `aletheia observe status <id>` | 单条 Observation 状态 + 最新 evaluation | 只读 |
| `aletheia observe sync` | 主动触发 capture + evaluate（与 controller cycle 语义一致） | 写 lab.db + append filter_shadow_evaluations |
| `aletheia observe evaluate <id>` | 只对一条 Observation 做一次评估 | 同上 |
| `aletheia observe register <spec.yaml>` | 从 YAML 手工登记（脱离自动 replay 路径） | 写 filter_shadow_experiments |
| `aletheia observe close <id> --reason …` | 强制停止收集，记录 operator reason | 只改 status/invalidation_reason；历史不删 |

`register` YAML 必须字段：`filter_name / stage / scanner_family / direction / market_type / horizon / control / treatment`。可选：`filter_version, assignment_policy, min_samples, min_coverage, min_edge_delta_pct`。所有命令都**不能**触发 Risk / Execution / Gateway / credential / config-mutation 路径。

## 6. Zero authority guarantees

- `filter_shadow_experiments.production_mutation_allowed = 0` 是 SQL `CHECK` 硬约束，Lab 永远不能 flip 它。
- Lab 不 import Risk / Execution / Gateway / credential / config-mutation 路径。
- 任何 `ready_for_review` 结论最多是"人工可审阅的 promotion candidate"；Production 行为变化仍需 PR + policy epoch + owner admission。

## 7. Runtime independence 边界

Lab 保证：**与 adapter causal identity 无关**的 Main Runtime commit 变化不会中断 Observation。

Lab 不保证：source population、canonical outcome contract、cost model 或 `filter_candidate_outcomes` schema 被 Production 删除/改语义后，旧 Observation 仍能收集。这类真实身份变化需在 `_ADAPTER_CAUSAL_IDENTITY` 里显式 bump，历史 evidence 保留但 cohort 结束。

## 8. Not in scope（POC 明确不做）

- `family_ttl` / `observer_pre_filter` 迁移到 adapter identity 模型 — Phase 2；`_ADAPTER_CAUSAL_IDENTITY` 里注释保留了它们的位置，但暂不参与。两者继续走旧的 `_production_version_changed` 严格失效路径。
- 删除或迁移任何现有 `filter_shadow_*` / `learning_*` 数据 — 不属于 #1072。
- Out-of-process observation runner；systemd timer；Web UI；randomized arm assignment；generic behavior-comparability engine — 全部 deferred 直到出现真实运行事实证明必要。
- 破坏 #447 UFC window：Lab 不写任何 `learning_*` 表、不改 UFC 契约、不上 Paper authority。

## 9. Verification hooks

Core semantics（`tests/test_filter_shadow_experiments.py`）:

- `test_ranker_confidence_unrelated_filter_version_bump_does_not_invalidate` — 核心解耦性
- `test_ranker_confidence_adapter_identity_change_invalidates` — fail-closed
- `test_ranker_confidence_evaluation_from_frozen_units_matches_live_replay` — evidence equivalence
- `test_two_ranker_experiments_collect_independently` — 多 Observation 并发独立
- `test_open_readonly_snapshot_rejects_writes` — `app.db` 物理 RO 边界
- `test_lab_db_is_physically_isolated_from_app_db` — lab.db 与 app.db 是不同文件
- `test_lab_db_open_readonly_snapshot_rejects_writes` — `lab.db` 物理 RO 边界

CLI surface（`tests/test_observe_cli.py`）:

- `test_observe_list_empty_and_populated` — list 输出契约
- `test_observe_register_yaml_creates_row` — YAML 登记路径
- `test_observe_status_reports_frozen_count_and_control` — status 报告冻结数
- `test_observe_sync_captures_and_evaluates` — sync 完整流水
- `test_observe_close_stops_collection_without_deleting_history` — 停止不删证据
- `test_observe_evaluate_targets_one_observation` — 单点评估
