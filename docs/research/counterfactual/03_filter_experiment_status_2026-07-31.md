# L4 过滤器前瞻实验状态

> 作成日：2026-07-31
> 数据范围：`filter_shadow_experiments` + `filter_shadow_evaluations` 表，dev DB
> 工具：直接 SQLite 查询

---

## 1. 执行摘要

- L4 `filter_shadow_experiments` 目前**只有 2 条记录，均为 `invalidated`**
- 原因：这两个实验注册时绑定了 `filter_version=v1-50df85d8c3b4`，而生产中该版本已更迭，触发自动 invalidation
- `filter_shadow_evaluations` 有 141 条 `collecting` 状态的评估快照（属于这 2 个实验）
- **`observer_pre_filter` 类型的实验（L4 Phase 2）当前没有注册记录**

---

## 2. 实验明细

### 2.1 已注册实验（全量）

| experiment_key (前8位) | filter_name | filter_version | family/direction | status | invalidation_reason |
|---|---|---|---|---|---|
| `21e718b9` | `family_ttl` | v1-50df85d8c3b4 | momentum/long | `invalidated` | production_filter_version_changed |
| `2f9df256` | `family_ttl` | v1-50df85d8c3b4 | momentum/long | `invalidated` | production_filter_version_changed |

两个实验的区别仅在 `horizon`（一个 4h，一个 1h），其余参数相同：
- control_parameter: `{"ttl_sec": 1800}` (30 分钟 TTL)
- treatment_parameter: `{"ttl_sec": 14400}` (4 小时 TTL)
- min_samples: 20
- production_mutation_allowed: **0**（硬字段，符合 CF-I01）

### 2.2 `source_replay` 片段解读

从 experiment `21e718b9` 的 `source_replay` 字段提取：

```json
{
  "current_pass": {
    "candidate_count": 67,
    "median_net_pct": -1.134,
    "outcome_coverage": 0.70,
    "raw_avg_net_pct": -0.682,
    "settled_n": 47
  },
  "alternatives": [
    {"ttl_sec": 2700, "settled_n": 4, "readiness": "insufficient_data"},
    {"ttl_sec": 3600, "settled_n": 4, "readiness": "insufficient_data"},
    {"ttl_sec": 7200, "settled_n": 11, "readiness": "insufficient_data"},
    {"ttl_sec": 14400, "settled_n": 20, "readiness": "ready_for_controlled_experiment",
     "raw_avg_net_pct": 0.828, "shrunk_avg_net_pct": 0.073}
  ]
}
```

**有价值的观察**（尽管实验已 invalidated）：
- 当前 TTL=1800s（30 分钟）下，momentum/long candidates 平均净收益 **-0.68%**（与整体 momentum -1.2% 一致）
- TTL=14400s（4h）时，`ready_for_controlled_experiment` 条件达成，raw_avg +0.83%，但收缩后仅 +0.07%（Bayesian shrinkage 把 n=20 的高估大幅压缩）

**方法论注释**：`shrunk_avg_net_pct` 是关键；raw_avg +0.83% 被压缩至 +0.07% 表明样本量（n=20）不足以可信推断效果量。filter_replay 的 Bayesian shrinkage 是正确行为，防止噪声信号触发虚假决策。

---

## 3. 评估快照（evaluations）

| disposition | count |
|---|---|
| `collecting` | 141 |
| `invalidated` | 2 |

141 条 `collecting` 评估全部隶属于这 2 个已 invalidated 实验（实验生命周期期间每次 `evaluate_active_shadow_experiments()` 调用生成一条快照）。每次评估记录的是某一时间点的 evidence 积累状态，不是最终结论。

由于实验本身已 invalidated，这些 collecting 快照不再驱动任何决策路径。

---

## 4. Observer Pre-filter 实验（L4 Phase 2）缺失

`filter_experiments.py` 在 PR #503 中扩展了 `observer_pre_filter` adapter，但当前 DB 中**没有任何此 kind 的实验记录**。

可能原因：
1. `ready_for_prospective_shadow` 标记的 replay 还未达成（需要足够的 observer filter 候选）
2. `filter_controller` 尚未调用 `register_replay_ready_experiments()`（scheduler 或 filter_controller 未运行）

这不是代码 bug，是运营状态：observer_pre_filter replay 需要 filter_controller 主动触发 `build_bounded_filter_replays()` 并推进到 `ready_for_prospective_shadow`。

---

## 5. 实验 lifecycle 全景

```
[filter_replay.build_bounded_filter_replays()]
         |
         v  (readiness 满足)
[filter_experiments.register_replay_ready_experiments(replays)]
         |  
         v  (写入 filter_shadow_experiments, status=active)
[filter_experiments.evaluate_active_shadow_experiments()]  <-- scheduler 定期调用
         |
         +-- filter_version 未变 --> collecting → ready_for_controlled_experiment
         |
         +-- filter_version 变更 --> invalidated (当前状态)
         |
         v  (disposition = ready_for_controlled_experiment)
[人工审阅 → 新 policy epoch 决策]  <-- L5，不在模块内
```

当前卡点：生产 filter_version 更换频率 > 实验积累速度。在 filter_version 稳定之前，实验会持续被 invalidated 而无法积累到 `ready_for_review`。

---

## 6. 研究延伸方向

| 方向 | 条件 | 工具 |
|---|---|---|
| 观测 observer_pre_filter 实验能否积累 | filter_controller 需运行且 observer replay 达到 `ready_for_prospective_shadow` | `build_bounded_filter_replays()` |
| filter_version 更换频率评估 | 查 `filter_shadow_experiments.invalidation_reason` 历史 | 直接 SQL |
| TTL 放宽对 momentum 的边际改善估算 | replay 数据（已有 source_replay） | `build_family_ttl_replay()` 重跑 |
| 多 family 实验注册（derivatives/mean_reversion） | 当前 filter_replay 只注册了 momentum | `filter_controller` 配置检查 |

---

## 附录：关键不变量验证

```python
import aletheia.storage.sqlite as db
conn = db.get_db()

# CF-I01: production_mutation_allowed 必须为 0
violations = conn.execute(
    "SELECT count(*) FROM filter_shadow_experiments WHERE production_mutation_allowed != 0"
).fetchone()[0]
assert violations == 0, f"CF-I01 violated: {violations} experiments with mutation_allowed != 0"
print("CF-I01 OK: all experiments have production_mutation_allowed=0")
```
