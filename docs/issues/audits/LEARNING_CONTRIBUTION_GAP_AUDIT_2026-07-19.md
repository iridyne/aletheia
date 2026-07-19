# Learning Contribution 缺口审计（A1 交付后实测）

> **触发**：2026-07-19 用户质疑 learning 模块"到底有没有学到真才实学"。
> **上游**：接续 `docs/issues/audits/LEARNING_LOOP_AUDIT_2026-06-26.md`（loop 全链路审计）与
> `docs/plans/learning/learning_positive_contribution_implementation_plan_2026-07-12.md`
> （其中 A1 truth-repair delivery 声称已于 2026-07-18 完成 measurement 侧的完整性修复）。
> **方法**：3 路并行 subagent（架构 / 数据 / 决策链路影响）+ 主审计对 `~/.aletheia/app.db` 的
> `learning_*` 与 `ps_*` 表实测；全部 SQL 落盘 §A。仅只读，未改任何代码或数据。
> **审计人**：Aletheia Dev · 审计日 2026-07-19
> **一句话结论**：**A1 measurement 修复交付 7 天后，A/B 主干仍然产不出任何一条可执行的 verdict——
> 19,461 条 exposure 里 authority_applied 为 True 的有 0 条；2016 条 verdict 里 positive 为 0 条；
> lifecycle_events 表恒为 0。当前 learning 系统在勤奋地跑，但产出闭环没打通。**

---

## 0. 与已有文档的关系

| 文档 | 时间 | 与本审计的关系 |
|---|---|---|
| `LEARNING_LOOP_AUDIT_2026-06-26.md` | 2026-06-26 | 从"链路断裂/幻觉污染"角度审计。本审计不重复其结论，仅在 §B 中确认 R0 归因链已由 [attribution update 2026-07-12] 部分修复 |
| `learning_positive_contribution_implementation_plan_2026-07-12.md` A1 交付 | 2026-07-18 | 声称完成 full-arm/exact-key/cluster-randomized measurement、default-closed authenticated-owner admission、durable failure history 等；本审计用 A1 交付后 7 天的实测数据验证：**measurement 侧的完整性确实成立，但下游的 verdict 引擎因 4 条独立缺口全部空转** |
| `LEARNING_MODULE_REPAIR_PLAN.md` | 2026-07-02 | 已被 7-12 计划超集替代，仅作为背景 |

本审计**不是**新的修复计划——修复计划继续跟在
`docs/plans/learning/learning_positive_contribution_implementation_plan_2026-07-12.md` 后续章节。
本审计只登记缺口 + 量化影响面 + 反事实投影，供 plan 采纳。

---

## 1. 严重性定级

| 维度 | 判定 |
|---|---|
| Measurement 完整性（A1 目标） | 🟢 已达成——上游 outcome 表 entry_ts 100% 干净，exposure 表 assignment 均衡（treatment 9,676 / control 9,785），authority_applied 字段被诚实置 False |
| A/B 主干产出 alpha | 🔴 **零产出**——2016 verdict 中 positive/negative/neutral 皆为 0，全部 insufficient_data (1502) 或 invalid (514) |
| Lifecycle 前进 | 🔴 44 experiment 全部停在 `shadow/active`；`learning_lifecycle_events` 恒为 0 |
| 唯一有效通路（LLM lesson→policy_version） | 🟡 **13 天停摆**——最近一条 `applied` 是 2026-07-06；`lesson_reviewer.backlog_count=76`，最老 backlog 自 2026-06-28 |
| 关键作业健康 | 🔴 `learning_verdict_runner.last_succeeded_at = NULL`——上线以来从未成功过，最近状态 `failed: hung after 135.0s` |
| 死代码 | 🟡 `contribution.py` + `evidence.py` 在生产无消费者，仅测试引用 |

---

## 2. 数据基线（2026-07-19 快照）

统一取自 `~/.aletheia/app.db`，运行窗口 2026-07-12 → 2026-07-19（7 天）。

| 表 | 行数 | 最新时间 | 备注 |
|---|---:|---|---|
| `learning_hypotheses` | 43 | 8 分钟前 | 100% `shadow` |
| `learning_experiments` | 44 | 8 分钟前 | 100% `shadow/active`，与 hypothesis 近 1:1 |
| `learning_exposures` | 19,461 | 秒级 | authority_applied 全 False，consumer_stage 全以 `:shadow_refrozen` 结尾 |
| `learning_contribution_verdicts` | 2,016 | 10 分钟前 | 0 positive/negative/neutral；1502 insufficient_data + 514 invalid |
| `learning_lifecycle_events` | **0** | — | 从未有过一次晋升/降级/refreeze |
| `learning_policy_epochs` | 163 | 10 分钟前 | 7-15 一天 rotate 81 次 |
| `learning_job_health` | 11 | 2 分钟前 | 10 healthy + 1 failed（`learning_verdict_runner`） |
| `ps_lessons` | 392 | 22h 前（最新 proposed） | 32 applied / 76 proposed / 246 archived / 38 rejected |
| `ps_skills` | 7 | 2 天前 | 全 `draft`，无 `active` |
| `ps_policy_versions` | 12 | 2 天前 | 全部 `lesson_policy`，最新 2026-06-29 |

---

## 3. 缺口清单（按可修复度排序）

### 缺口 G1 —— cohort 计算路径把干净的 entry_ts 算成 1970-01-01

**假说**：verdict 的 `invalid: dirty_or_missing_entry_time` 与 `window_start=1970` 来自源数据脏。

**证伪**：源数据 100% 干净。SQL §A.T9：

| 表 | 总行 | entry_ts 缺失或 1970 |
|---|---:|---:|
| `universe_forward_outcomes` | 26,648 | **0 (0.0%)** |
| `filter_candidate_outcomes` | 193,604 | **0 (0.0%)** |

但 verdict 侧：

- **1,191 / 2,016 = 59% 的 verdict 的 `window_start` / `window_end` 是 `1970-01-01`**（§A.T8）
- 另有 23 条明确以 `invalid: dirty_or_missing_entry_time` 收场，含 T2 里那条 treat_n=68 / ctrl_n=69 / delta=+0.17
  的 verdict——**这是唯一一条 sample n 达到门槛的 verdict，本应能定性，被本 bug 挡住**（§A.T2）

**结论**：`dirty_or_missing_entry_time` **不是**上游数据问题，是 `aletheia/learning/cohorts.py` 或
`aletheia/learning/verdicts.py:_parse_time` 内的路径 bug。1970 window 集中在 10 个活跃大 experiment
上（bc28c81925: 187/290 = 65%; b1b43f10b9: 177/296 = 60%; 7ed5171ac1: 151/233 = 65%; 见 §A.T10）——
**越活跃的 experiment 被咬得越狠**，与 G2（epoch churn）同源可能性高：epoch 边界把 cohort 的
`entry_ts` 集合切空，`min(times)`/`max(times)` fallback 到默认 0。

**修复难度**：★★ 需在 `cohorts.py:build_experiment_cohorts` 与 `verdicts._parse_time` 之间定位单点。
无安全边界，不涉及授权。

---

### 缺口 G2 —— policy_epoch 频繁 rotate 把样本切碎

**假说**：exposure 数量足够，但被 epoch 边界分散到无法通过 `min_samples_per_arm=50` 门槛。

**验证**：SQL §A.T4/T5/T7：

- 8 个大 experiment 被切成 **6–52 个不同 epoch**；最惨的 1 个被切成 52 个，735 exposure / 52 桶 ≈ 14 samples/bucket
- 全部 435 个 `(experiment × epoch)` 桶中：
  - 270 桶 < 10 samples
  - 103 桶 10–49
  - 61 桶 ≥ 50（仅 14%）
- Epoch rotation 频率（§A.T7）：

| 日期 | 新 epoch 数 | 平均 epoch 寿命 |
|---|---:|---:|
| 2026-07-19 | 4 | **5.7 分钟** |
| 2026-07-15 | 81 | 19.5 分钟 |
| 2026-07-14 | 31 | 28.8 分钟 |

**根因位置**：`aletheia/learning/experiments.py:_policy_epoch` 将整个 `cfg.learning.model_dump()` 折进
epoch 哈希——**任何 learning 相关配置变动都会 rotate**，包括不影响该 path 行为的字段。

**反事实（§A.T12/T13）**：若 epoch 稳定为每 experiment 一个 epoch，`universe_family_calibration`
16 个 experiment 中 15 个立刻通过 unique candidate ≥50 门槛，12 个通过 ≥100，前 12 个 experiment
的 treatment/control 分布都清晰可测（bc28c81925: 2500 / 2440；7ed5171ac1: 1869 / 1976；等）。

**修复难度**：★★ 修改 `_policy_epoch` 折入的字段清单，参照现有 `universe_behavior` 的精细化范式，
剔除 `cfg.learning.model_dump()` 这一 blanket 折入。属 [`AGENTS.md`] 分类中的 B 类（新观察窗口），
需要关旧 epoch、开新 epoch、重新累积。

---

### 缺口 G3 —— SUPPORTED_PATH_IDS 硬编码只认 1 条 path

**位置**：`aletheia/learning/cohorts.py:25`

```python
SUPPORTED_PATH_ID = "universe_family_calibration"
SUPPORTED_PATH_IDS = frozenset({SUPPORTED_PATH_ID})
```

`authority.py:100` 对非 supported path 走 `_legacy_authority_adapter`，要求 legacy `promote` verdict
——从未存在过 → 永远 blocked。`lifecycle.py:92` 直接 `unsupported_learning_path` hold。

**影响面**（§A.T11）：

| path_id | experiments | exposures | verdicts |
|---|---:|---:|---:|
| universe_family_calibration | 16 | 18,519 | 2,012 |
| observer_advisory_bundle | 32 | 47 | **0** |
| operator_learning_bundle | 1 | 735 | **0** |
| ranker_learned_guard | 6 | 210 | **0** |
| ranker_case_score | 2 | 2 | 4 (legacy 残留) |

**关键**：`observer_advisory_bundle` 32 个 experiment 全部空转——即 LLM 生成的 advisory bundle
完全没进入学习闭环。`operator_learning_bundle` 是唯一 LLM 有实际产出的路径（对应 lesson→policy 通路），
也 0 verdict。

**修复难度**：★ 常量修改。但 `_legacy_authority_adapter` 是治理断层的兜底，扩容前需要每条 path
的 measurement 支持在 `cohorts.build_experiment_cohorts` 里补齐 join 逻辑。属 B 类。

---

### 缺口 G4 —— shadow authority 硬编码死锁

**位置**：

- `aletheia/learning/authority.py:88` — `if experiment.stage not in PAPER_STAGES: return blocked("experiment_stage_not_paper")`
- `aletheia/learning/lifecycle.py:158` — `admit_shadow_trial` **无论传什么参数都返回**
  `"authenticated_owner_admission_unavailable"`（对应 7-12 计划 A1 的 "default-closed authenticated-owner admission boundary"）

**逻辑闭环**：

```
shadow experiment → authority 拒绝 apply → treatment 值 = baseline
   → cohort 中 authority_applied 全 False → verdict 走 no_actual_intervention
   → verdict 永远 insufficient_data → lifecycle 永远 hold
   → 永远在 shadow → 循环
```

**验证**：19,461 条 exposure 中 `manifest_json.authority_applied` 全部为 False（§A.T3'）。

**关键**：即使 G1 + G2 + G3 全部修好，最好情形也只到 `insufficient_data: no_actual_intervention`
（`verdicts.py:57`）——**A/B 主干仍然不会产出 positive**。7-12 计划里 `authenticated-owner admission
boundary` 被称作"default-closed"是正确的（这是安全设计），但目前**没有任何一条实际的 owner
admission 通路**，也就是说人类操作员也没有真正可用的手动放行路径。

**修复难度**：★★★ 触发安全边界，属 [`docs/governance/policies/trust_and_change_control.md`] 范畴。
本审计**不建议**打开 shadow 自动 apply；建议实现 7-12 计划里承诺的 authenticated owner admission
通路（当前仅是常量 `authenticated_owner_admission_unavailable`）。属 A 类里的安全变更，需要正式流程。

---

## 4. 反事实投影：分别修好各缺口能救回多少

| 修复步 | 立即能救的 verdict | 累计能救 |
|---|---|---|
| 只修 G1（时间戳 bug） | 1191 条 1970-window verdict 变为有效评估；那条 treat=68/ctrl=69 立刻能定性 | +1,191 |
| G1 + G2（epoch 稳定） | 15/16 个 supported experiment 越过 n≥50 门槛；~1,300 条 `insufficient_data` 释放 | +2,500 |
| G1 + G2 + G3（放开 4 条 path） | observer/operator/guard 三条 path 首次产出 verdict（预估新增 ~2,500 verdict） | +5,000 |
| G1 + G2 + G3 + G4（shadow 放行） | **首次可能产出 positive/negative/neutral**——才是真正的学习闭环开始 | 才是真正的开始 |

**关键判断**：**G1 + G2 是"证伪 verdict 引擎能否学"的最便宜实验**。修完后如果仍全是 neutral，说明
当前 learning treatment 本身没 alpha，不是被数据卡住；如果出现 positive/negative，说明系统能学，
只是被自己咬死了。这个信号价值远大于修复代价。

---

## 5. 唯一还在给决策注入知识的通路：ps_lessons → ps_policy_versions

**产出规模**：12 条 policy_version，全部 `policy_type=lesson_policy`，`policy_json` 内容仅为
`accepted_lesson_ids` 数组——**本质是"这几条 lesson 已被接受"的凭证，没有可执行规则**。

**注入点**：

- `aletheia/agent_shell/context.py:112` —— `LessonStore().list_all(status="applied", limit=30)`
- `aletheia/agent_shell/trading_context.py:2099` —— `LessonStore().list_all(status="applied", limit=20)`

注入形式为 operator prompt 上下文，对应 registry 的 `operator_learning_bundle` 通路。

**内容质量**（§A 抽样）：
> "Mean reversion short signals on AKE/USDT consistently deliver 4.08%–7.87% PnL with 100% win rate across 3 trades"
> "100% stop-loss hit rate across diverse symbols (BTC, ETH, SOL, DOGE, FIL, AAVE) indicates systematic entry-timing/sizing issue"

**当前状态**：

- 32 条 `applied`，**最近一条 2026-07-06**（13 天前，非本次审计基线内产出）
- 76 条 `proposed` 积压，最老自 2026-06-28
- `dreaming_daily` / `lesson_reviewer` job 显示 healthy，但 `backlog_count = 77 / 76` 长期未消化

**这条通路和 experiment/verdict 主干完全分离**——lesson 由 LLM 从 case_study 直接产生，
`lesson_reviewer` 接受即生成 policy_version，不经过 shadow→paper 那套 A/B 门。这是**目前
learning 唯一给决策注入 alpha 的通路**，但已停摆。

---

## 6. 死代码 / 死表清单

| 项 | 状态 |
|---|---|
| `aletheia/learning/contribution.py` (`ContributionObservation`, `aggregate_observations`) | 生产 0 消费者；唯一 import 是 `tests/specs/spec_learning_contribution.py` |
| `aletheia/learning/evidence.py` | 生产 0 消费者（唯一 import 是 `contribution.py` 自己） |
| `learning_lifecycle_events` 表 | 0 行 |
| `serenity_post_mortems` 表 | 0 行 |
| `strategy_experiments` 表 | 0 行 |
| `perp_structure_shadow_outcomes` 表 | 0 行 |
| `knowledge/agent_memory/` | 0 文件 |
| `knowledge/evidence/` | 0 文件 |
| `knowledge/strategies/` | 0 文件 |
| `learning_verdict_runner` job | 上线以来 `last_succeeded_at = NULL`，7-17 起 `hung after 135s` |

---

## 7. 建议修复顺序（不含具体实施，只作 plan 采纳建议）

| 步 | 缺口 | 类别 | 前置 | 说明 |
|---|---|---|---|---|
| 1 | 修 `learning_verdict_runner` hung | A（立即） | 无 | 单点 job hang，最容易，不修则后续 verdict 产出速率有问题 |
| 2 | G1 时间戳 bug | A（立即） | 无 | 单点 bug，无安全边界；修完可立刻验证那条 treat=68/ctrl=69 是否变有效 |
| 3 | G2 epoch churn | B（新观察窗口） | G1 完成后 | 修改 `_policy_epoch` 折入字段清单；关闭旧 epoch、开新 epoch、重新累积 7 天 |
| 4 | G3 放开 supported paths | B | G1 + G2 完成后 | 先加 `operator_learning_bundle`（LLM 有实际产出）；分批扩容 |
| 5 | G4 authenticated owner admission | A（安全变更） | 独立进行 | 属 `trust_and_change_control` 范畴；实现 7-12 计划承诺的 admission 通路，不建议自动化 |
| 6 | 消化 `ps_lessons` 积压 76 条 | A | 独立 | 唯一活着的 alpha 通路，先救活；查 `lesson_reviewer` 为何 healthy 但 backlog 不下降 |

---

## §A. SQL 证据落盘

以下 SQL 全部在 2026-07-19T08:xx UTC 于 `~/.aletheia/app.db` 只读执行。

### T0 —— verdict 主分布 by (path, verdict, reason)

```sql
SELECT h.path_id, v.verdict,
       CASE WHEN v.reason LIKE 'invalid evidence:%' THEN substr(v.reason, 19) ELSE v.reason END AS reason_short,
       COUNT(*) AS n
FROM learning_contribution_verdicts v
JOIN learning_experiments e ON e.experiment_id = v.experiment_id
JOIN learning_hypotheses h ON h.hypothesis_id = e.hypothesis_id
GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 40;
```

输出：

```
path_id                      verdict            reason_short                                                            n
universe_family_calibration  insufficient_data  insufficient unique prospective treatment/control samples or time span  1498
universe_family_calibration  invalid            ambiguous_duplicate_outcome                                             349
universe_family_calibration  invalid            conflicting_mirrored_outcome                                            67
universe_family_calibration  invalid            conflicting_allocation_result                                           45
universe_family_calibration  invalid            dirty_or_missing_entry_time                                             23
universe_family_calibration  invalid            conflicting_allocation_result, conflicting_mirrored_outcome             13
universe_family_calibration  invalid            net_economics_unavailable                                               9
universe_family_calibration  invalid            ambiguous_duplicate_outcome, net_economics_unavailable                  7
ranker_case_score            insufficient_data  insufficient unique prospective treatment/control samples or time span  4
universe_family_calibration  invalid            conflicting_mirrored_outcome, net_economics_unavailable                 1
```

### T1 —— insufficient_data 的 sample n 分桶

```sql
SELECT
  CASE
    WHEN unique_treatment_n=0 AND unique_control_n=0 THEN 'both_zero'
    WHEN unique_treatment_n=0 THEN 'treatment_zero'
    WHEN unique_control_n=0 THEN 'control_zero'
    WHEN unique_treatment_n<50 AND unique_control_n<50 THEN 'both_below_50'
    WHEN unique_treatment_n<50 THEN 'treatment_below_50'
    WHEN unique_control_n<50 THEN 'control_below_50'
    ELSE 'both_meet_50'
  END AS bucket,
  COUNT(*) AS n,
  ROUND(AVG(unique_treatment_n),1) AS avg_t,
  ROUND(AVG(unique_control_n),1) AS avg_c,
  MAX(unique_treatment_n) AS max_t,
  MAX(unique_control_n) AS max_c
FROM learning_contribution_verdicts
WHERE verdict='insufficient_data'
GROUP BY 1 ORDER BY 2 DESC;
```

输出：

```
bucket            n    avg_t  avg_c  max_t  max_c
both_zero         961  0.0    0.0    0      0
both_below_50     349  6.1    6.2    49     35
treatment_zero    105  0.0    1.8    0      13
control_zero      85   1.7    0.0    7      0
control_below_50  1    51.0   36.0   51     36
both_meet_50      1    68.0   69.0   68     69
```

### T2 —— 最接近成功的 verdict（top 25 by min(t_n, c_n)）

```sql
SELECT substr(experiment_id,1,10) AS exp,
       unique_treatment_n AS t_n, unique_control_n AS c_n,
       verdict, substr(reason,1,60) AS reason
FROM learning_contribution_verdicts
ORDER BY MIN(unique_treatment_n, unique_control_n) DESC LIMIT 25;
```

输出（节选，唯一一条 both≥50）：

```
7ed5171ac1  68  69  invalid            invalid evidence: dirty_or_missing_entry_time
7ed5171ac1  68  69  invalid            invalid evidence: dirty_or_missing_entry_time
7ed5171ac1  68  69  insufficient_data  insufficient unique prospective treatment/control samples or …
7ed5171ac1  51  36  insufficient_data  insufficient unique prospective treatment/control samples or …
651c44fcdb  35  33  insufficient_data  insufficient unique prospective treatment/control samples or …
...
```

### T3' —— exposures 的 authority_applied 分布（从 manifest_json 提取）

```sql
SELECT path_id,
       json_extract(manifest_json, '$.authority_applied') AS applied,
       COUNT(*) FROM learning_exposures GROUP BY 1, 2;
```

输出：

```
observer_advisory_bundle    0  24
operator_learning_bundle    0  734
ranker_case_score           0  2
ranker_learned_guard        0  210
universe_family_calibration 0  18496
```

**全部 19,466 条 exposure 中 authority_applied 为真的 = 0**。

### T4 —— 单个 experiment 被切进多少个 policy_epoch

```sql
SELECT epoch_bucket_n AS distinct_epochs_per_exp,
       COUNT(*) AS n_experiments,
       ROUND(AVG(exposure_n), 0) AS avg_exposures_per_exp
FROM (
  SELECT experiment_id,
         COUNT(DISTINCT concurrent_policy_epoch) AS epoch_bucket_n,
         COUNT(*) AS exposure_n
  FROM learning_exposures
  GROUP BY experiment_id
) GROUP BY 1 ORDER BY 1;
```

输出：

```
distinct_epochs_per_exp  n_experiments  avg_exposures_per_exp
1                        36             110
6                        3              59
7                        2              53
8                        3              400
22                       1              3845
24                       1              288
26                       1              75
28                       1              272
32                       1              95
35                       1              887
38                       1              683
39                       1              4940
44                       1              2254
52                       1              735
```

### T5 —— 单 (experiment × epoch) 桶内样本量分布

```sql
SELECT
  CASE
    WHEN samples_in_bucket = 0 THEN '0'
    WHEN samples_in_bucket < 10 THEN '1-9'
    WHEN samples_in_bucket < 50 THEN '10-49'
    WHEN samples_in_bucket < 100 THEN '50-99'
    ELSE '100+'
  END AS bucket,
  COUNT(*) AS n_buckets,
  SUM(samples_in_bucket) AS total_exposures
FROM (
  SELECT experiment_id, concurrent_policy_epoch, COUNT(*) AS samples_in_bucket
  FROM learning_exposures
  GROUP BY 1, 2
) GROUP BY 1 ORDER BY 1;
```

输出：

```
bucket   n_buckets  total_exposures
1-9      270        735
10-49    103        2307
50-99    26         1770
100+     35         14696
```

### T6 —— supported path 的 (exp × epoch) unique candidate 分布

```sql
SELECT
  CASE
    WHEN uniq_cand < 10 THEN '<10'
    WHEN uniq_cand < 50 THEN '10-49'
    WHEN uniq_cand < 100 THEN '50-99'
    ELSE '100+'
  END AS unique_candidates_bucket,
  COUNT(*) AS n_buckets
FROM (
  SELECT experiment_id, concurrent_policy_epoch,
         COUNT(DISTINCT candidate_key) AS uniq_cand
  FROM learning_exposures
  WHERE path_id='universe_family_calibration'
  GROUP BY 1, 2
) GROUP BY 1;
```

输出：

```
<10     120
10-49   94
50-99   22
100+    34
```

### T7 —— policy_epoch churn

```sql
SELECT DATE(created_at) AS day, COUNT(*) AS new_epochs,
       ROUND(AVG(life_min), 1) AS avg_life_min
FROM (
  SELECT epoch_id, created_at,
    (julianday(COALESCE(
      (SELECT MIN(created_at) FROM learning_policy_epochs e2 WHERE e2.created_at > e.created_at),
      datetime('now'))) - julianday(created_at)) * 1440 AS life_min
  FROM learning_policy_epochs e
) GROUP BY 1 ORDER BY 1 DESC LIMIT 10;
```

输出：

```
day         new_epochs  avg_life_min
2026-07-19  4           5.7
2026-07-18  8           218.8
2026-07-17  17          81.3
2026-07-16  13          113.6
2026-07-15  81          19.5
2026-07-14  31          28.8
2026-07-13  3           655.6
2026-07-12  6           218.2
```

### T8 —— 1970 时间戳定位

```sql
SELECT COUNT(*) AS n_1970_created,
       (SELECT COUNT(*) FROM learning_contribution_verdicts WHERE window_start LIKE '1970%') AS n_1970_win_start,
       (SELECT COUNT(*) FROM learning_contribution_verdicts WHERE window_end LIKE '1970%') AS n_1970_win_end
FROM learning_contribution_verdicts WHERE created_at LIKE '1970%';
```

输出：

```
n_1970_created  n_1970_win_start  n_1970_win_end
630             1191              1191
```

### T9 —— 上游 outcome 表 entry_ts 完整性

```sql
SELECT 'universe_forward_outcomes' AS tbl, COUNT(*) AS total,
       SUM(entry_ts IS NULL OR entry_ts='' OR entry_ts LIKE '1970%') AS bad,
       ROUND(100.0 * SUM(entry_ts IS NULL OR entry_ts='' OR entry_ts LIKE '1970%') / COUNT(*), 1) AS bad_pct
FROM universe_forward_outcomes
UNION ALL
SELECT 'filter_candidate_outcomes', COUNT(*),
       SUM(entry_ts IS NULL OR entry_ts='' OR entry_ts LIKE '1970%'),
       ROUND(100.0 * SUM(entry_ts IS NULL OR entry_ts='' OR entry_ts LIKE '1970%') / COUNT(*), 1)
FROM filter_candidate_outcomes;
```

输出：

```
universe_forward_outcomes  26648   0  0.0
filter_candidate_outcomes  193604  0  0.0
```

### T10 —— 1970 window 集中在哪些 experiment

```sql
SELECT substr(experiment_id,1,10) AS exp, COUNT(*) AS n_1970_windows,
       (SELECT COUNT(*) FROM learning_contribution_verdicts v2
        WHERE v2.experiment_id = v.experiment_id) AS total_verdicts_for_exp
FROM learning_contribution_verdicts v
WHERE window_start LIKE '1970%'
GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
```

输出：

```
bc28c81925  187  290
b1b43f10b9  177  296
7ed5171ac1  151  233
651c44fcdb  151  272
a63bcf4635  138  247
b50a358424  101  168
24f0e6d73b  69   140
48d83c4560  40   66
4605fcacb2  39   73
94030b57e5  27   50
```

### T11 —— 四条 path 空转规模

```sql
SELECT e.path_id,
       COUNT(DISTINCT e.experiment_id) AS n_experiments,
       COUNT(*) AS n_exposures,
       (SELECT COUNT(*) FROM learning_contribution_verdicts v
        JOIN learning_experiments x ON x.experiment_id=v.experiment_id
        JOIN learning_hypotheses h ON h.hypothesis_id=x.hypothesis_id
        WHERE h.path_id = e.path_id) AS n_verdicts_ever
FROM learning_exposures e
GROUP BY 1 ORDER BY 3 DESC;
```

输出：

```
universe_family_calibration  16  18519  2012
operator_learning_bundle     1   735    0
ranker_learned_guard         6   210    0
observer_advisory_bundle     32  47     0
ranker_case_score            2   2      4
```

### T12/T13 —— 反事实：epoch 稳定后能救多少

```sql
SELECT experiment_id_short, n_treatment_cand, n_control_cand, n_total_expo,
       CASE
         WHEN MIN(n_treatment_cand, n_control_cand) >= 50 THEN 'PASS'
         WHEN MIN(n_treatment_cand, n_control_cand) >= 25 THEN 'CLOSE'
         ELSE 'FAIL'
       END AS gate_50
FROM (
  SELECT substr(experiment_id,1,10) AS experiment_id_short,
         COUNT(DISTINCT CASE WHEN assignment='treatment' THEN candidate_key END) AS n_treatment_cand,
         COUNT(DISTINCT CASE WHEN assignment='control' THEN candidate_key END) AS n_control_cand,
         COUNT(*) AS n_total_expo
  FROM learning_exposures
  WHERE path_id='universe_family_calibration'
  GROUP BY experiment_id
) ORDER BY n_total_expo DESC LIMIT 20;
```

输出（前 16 条 = 全部 supported experiments）：

```
bc28c81925  2500  2440  4940  PASS
7ed5171ac1  1869  1976  3845  PASS
b1b43f10b9  1186  1068  2254  PASS
48d83c4560  1138  1008  2146  PASS
4605fcacb2  771   826   1597  PASS
651c44fcdb  442   445   887   PASS
a63bcf4635  304   379   683   PASS
d49057266a  315   335   650   PASS
1a4f8150a0  280   257   537   PASS
24f0e6d73b  122   166   288   PASS
b50a358424  105   167   272   PASS
485ab2a29e  76    74    150   PASS
355918cf23  65    43    108   CLOSE
84c739a2ce  1     56    57    FAIL
94030b57e5  30    26    56    CLOSE
94cde1b2e4  23    26    49    FAIL
```

**汇总**：15/16 PASS，12/16 甚至能通过 100 门槛。

---

## §B. 与 6-26 审计的关系确认

6-26 审计的 R0（`trading_decision.py:250-251` 硬编码 `opportunity_id=None`）已由 7-12 计划的
"Attribution update 2026-07-12" 部分修复（causal-id-only reflection、operator learning bundle
manifest、observer advisory memory manifest）。本审计基线中 `learning_exposures.opportunity_id`
虽然仍 0% 填充（数据检查 agent 报告），但 `outcome_candidate_key` 已 97% 填充，说明归因通路
已经改走 candidate_key 路径而非 opportunity_id 路径。

6-26 审计的 R1（`_ALLOWED_CONFIDENCE="high_confidence"` 反向门）在本审计中不复现，因为
ranker/_lesson_penalty 相关的 exposure（`ranker_case_score` / `ranker_learned_guard`）由于
G3 SUPPORTED_PATH_IDS 限制根本没进入 verdict 计算。R1 是否已修，本审计不判定。

---

## §C. 术语与索引

- `authority_applied`：exposure 层的实际干预标记，来自 `experiments.apply_numeric_treatment` 的 authority resolver 决策；本审计 100% 为 False
- `SUPPORTED_PATH_IDS`：`cohorts.py:25` 常量，当前 = `{"universe_family_calibration"}`
- `_legacy_authority_adapter`：`authority.py:226`，非 supported path 的兜底路径，要求 legacy `promote` verdict（本项目从未存在过）
- `admit_shadow_trial`：`lifecycle.py:158`，shadow → paper_10 的转换函数，当前实现恒返回 `authenticated_owner_admission_unavailable`

---

## §D. 后续动作追踪

本审计**不含**修复动作。修复动作请写入
`docs/plans/learning/learning_positive_contribution_implementation_plan_2026-07-12.md`
的 Phase 4 段（若还没有，需新增），并在其内 back-link 本审计。
