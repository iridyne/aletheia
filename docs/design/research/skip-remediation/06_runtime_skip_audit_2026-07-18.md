# 06 — Runtime Skip Audit and Partial Remediation (2026-07-18)

> 状态：**A 类可观测性修复已实现于 `iridite/skip-observability-repair`；PI 行为未修改。**
> 证据快照：2026-07-17T16:58:43Z，本机 `paper_internal`、`live_disabled`。
> 边界：不改 prompt、conviction=55、Debate、Risk、Execution、Exchange、Live、额度或凭据。

## 1. Owner 问题与本轮裁决

Overview 的 Recent 决策持续被 `operator skip` 占满。只读审计把问题分成三层：

1. Overview 相关 rolling-window SQL 把 ISO `T` 时间与 SQLite 空格时间按文本比较，窗口失真；
2. `skip-halt-v1` 在“最近 24h 决策”上等待同一批 24h outcome，持续有新 skip 时结构性地
   无法完成；
3. direct model skip 与 Debate/conviction downgrade 的成熟结果方向相反，前者需要下一次
   单变量 B treatment，而不是删除 skip 或降低安全门。

本轮只修 1/2，并把 3 固化为后续治理入口。这样先恢复可信测量，再决定是否改变 PI 行为。

## 2. A 类修复一：Overview 时间窗口

### 2.1 复现

同一快照下：

| 口径 | 24h candidate skip |
| --- | ---: |
| 旧文本比较 `created_at >= datetime('now','-24 hours')` | 143 |
| 解析时间点 `julianday(created_at) >= julianday(cutoff)` | 77 |

根因不是数据重复，而是 `2026-07-16T01:...` 在文本排序中大于
`2026-07-16 16:...`。受影响范围为 Overview 使用的
`pipeline-funnel`、`opportunity-funnel`、`operator-decisions` rolling counts/list。

### 2.2 修复与验收

- 后端统一生成 UTC ISO cutoff，并用 `julianday()` 解析列值和 cutoff；保留小数秒和 offset 语义。
- RED fixture 固定构造“cutoff 当日 00:00 的旧 ISO 行 + 5 分钟内新行”，锁定三条路由同口径。
- 这只改变只读显示，不改变候选、PI、Proposal、Risk 或 Execution。

## 3. A 类修复二：`skip-halt-v2` 成熟 cohort

### 3.1 v1 不可完成的原因

v1 同时选取 `[T-24h, T]` 的 candidate，并要求其 24h primary outcome 全部 settled。除边界瞬间外，
该窗口内的新 candidate 必然尚未满 24h；只要 PI 持续产生 skip，finding 就会永久保持
`awaiting_outcomes`。单元测试此前用“刚创建但 24h 已 settled”的不可能 fixture 掩盖了这一点。

### 3.2 v2 双窗口

```text
recent evidence window: [T-24h, T]       -> trace/seed/current-flow integrity
mature effect window:   [T-48h, T-24h)  -> 24h protective/suspect judgement
```

最近窗口的缺 trace/seed 仍立即产生 `recording_incident`。最近未成熟的 skip 计入
`candidate_skips` / `immature_candidate_skips`，但不阻塞成熟 cohort。只有成熟 cohort 自身仍有
overdue outcome 时才保持 `awaiting_outcomes`。

首次对真实库运行 v2：recent=77、mature=84、mature settled=81、mature overdue=3、trace/seed
缺失均为 0。因此当时正确 finding 仍是 `awaiting_outcomes`，但原因已从“所有新样本天然未成熟”
收窄为“成熟窗口还有 3 个结算延迟”；结算后状态能够真实前进。

### 3.3 行政清理行不得伪装成模型 skip

2026-07-17T17:03:34Z，运行库一次性写入 145 条 `signal_source=system_cascade`、reasoning 为
`cascade_expired` 的生命周期清理行。旧 candidate 定义只检查 `action=skip` 与非空
`opportunity_id`，因而把它们误报为缺少 trace/seed 的模型 skip，并触发假 `recording_incident`。

Overview Recent、pipeline skip count 和 v2 诊断明确排除，并由 Doctor 单独展示这类
administrative skip。修复后同一运行库复核为：真实 24h candidate
skip=85、missing trace=0、missing four-horizon seed=0、administrative cascade excluded=145；mature=84，
finding=`awaiting_outcomes`。排除只改变诊断归类，不删除历史行，也不改变清理或交易行为。

## 4. Direct Skip 行为发现（本轮不改）

### 4.1 分层结果

在当前 `system_operator_skip_remediation` epoch 的已结算 24h 反事实结果中：

| terminal 路径 | n | avg net 24h | median | positive |
| --- | ---: | ---: | ---: | ---: |
| direct `operator_model_skip` | 106 | +1.4403% | +0.6535% | 54.7% |
| model trade → Debate/conviction reject | 37 | -1.0410% | -2.5375% | 40.5% |

这是固定时点、成本后反事实，不是账户 PnL 或完整止损止盈模拟；样本还含同 symbol 的相关暴露。
但分层方向足以否决“所有 skip 都同样保护”的说法：Debate/conviction 路径有保护性，direct skip
至少是 overrestriction suspect。

### 4.2 当前语义偏移

严格最近 24h 的 77 个 skip 中：55 个 direct skip 携带 `insufficient_evidence`、11 个携带
`qualified_negative`、10 个携带 `qualified_positive`；另 1 个是 model trade 后被拒。运行理由中
出现把 `insufficient_evidence` 称为 disqualifier 的情况，与现行契约“neutral, not a directional
veto”冲突。`qualified_positive` 也没有形成可观察的 action differentiation。

## 5. 下一任务：单变量 Direct-Skip 语义实验（B 类）

本任务不与 A 类修复同 PR。进入条件：

1. `skip-halt-v2` 的成熟窗口完成结算，并至少产生一次可复现 finding；
2. 当前 epoch 的 direct/gated 分层报告复算一致，记录 symbol/direction 相关性；
3. owner 确认关闭旧行为窗口并创建新 policy epoch。

建议唯一 treatment：**direct skip 必须给出至少一个与当前候选相关、独立于
`insufficient_evidence` 的 adverse reason；证据不足本身保持中性。** 不改 55 分门槛、Debate 权重、
Ranker、sizing 或任何下游安全门；也不把无法解释的 skip 自动改成 trade。具体 retry/unavailable
语义须在该任务的 RED spec 中先定稿，避免制造强制交易器或无限重试。

预注册观察：direct/gated skip ratio、qualified-positive action differentiation、proposal funnel、
queue age、24h 净结果、PF/ECE/回撤、trace/seed coverage。旧 epoch 数据只作 baseline，不与新窗口
合并判决。

## 6. 验证入口

```sh
uv run pytest tests/test_analytics_windowing_fixes.py tests/test_skip_diagnosis.py -q
uv run pytest tests/specs/spec_filter_evidence.py -q
cd frontend && npm run build
uv run aletheia doctor
```

本轮成功标准不是产生更多交易，而是让 Recent 数量真实、诊断能够完成、并为下一次行为实验保留
单一可归因变量。
