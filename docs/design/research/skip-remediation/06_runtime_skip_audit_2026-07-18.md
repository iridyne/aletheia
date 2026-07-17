# 06 — Runtime Skip Audit and Partial Remediation (2026-07-18)

> 状态：**A 类可观测性修复已合入；B 类 direct-skip 语义修复已实现于
> `iridite/direct-skip-semantics`，待独立 PR/新 epoch 生效。**
> 证据快照：2026-07-17T16:58:43Z，本机 `paper_internal`、`live_disabled`。
> 边界：不改 prompt、conviction=55、Debate、Risk、Execution、Exchange、Live、额度或凭据。

## 1. Owner 问题与本轮裁决

Overview 的 Recent 决策持续被 `operator skip` 占满。只读审计把问题分成三层：

1. Overview 相关 rolling-window SQL 把 ISO `T` 时间与 SQLite 空格时间按文本比较，窗口失真；
2. `skip-halt-v1` 在“最近 24h 决策”上等待同一批 24h outcome，持续有新 skip 时结构性地
   无法完成；
3. direct model skip 与 Debate/conviction downgrade 的成熟结果方向相反，前者需要下一次
   单变量 B treatment，而不是删除 skip 或降低安全门。

先前独立 PR 只修 1/2；owner 随后明确授权按顺序修复 3。本文件保留旧窗口证据，并记录新
`b1.2` 行为边界，禁止把两批样本混为一个 cohort。

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

## 4. Direct Skip 行为发现（b1.1 baseline）

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

### 4.3 显性与隐性 bug 定性

1. **显性语义冲突**：B2 `insufficient_evidence` 契约规定为中性，direct skip 却把它当否决。
2. **隐性分支不对称**：trade intent 在 40–80 进入 Bull/Bear debate，direct skip 一次模型输出即可
   终结；正负动作的证据负担不对称。
3. **隐性排序误读**：`trade_score` 是已经通过 upstream eligibility 后的排序量，不是胜率或隐含
   执行阈值；prompt 的 `score=` 命名和重复呈现诱发模型自行发明 threshold。
4. **显性容错错误**：缺失/不支持 action 被归一为 `skip`，malformed conviction 被归零，均可把
   provider/schema 错误伪装为业务决定并消费 pending。
5. **显性审计损失**：command/reflection 分别截断 300/200 字，structured supporting/opposing evidence
   未进入耐久 PiDecision 证据包，事后无法验证模型的真实 adverse basis。
6. **隐性重复成本**：相同 symbol/direction 的新 carrier 约每 50 分钟重新进入，Opportunity id 与时间
   变化掩盖“物质上下文未变化”，造成同质模型调用与 Overview skip 洪泛。

### 4.4 实施前最终运行快照（2026-07-18 CST）

合入前再次只读刷新运行库，最近 24 小时有约 116 个 candidate skip：115 个 direct
`operator_model_skip`，只有 1 个先表达 trade；direct skip 全部 `conviction=0`。B2 状态为
`insufficient_evidence=90`、`qualified_negative=16`、`qualified_positive=10`。理由中 76 条提到
score、53 条提到证据不足、12 条自行发明 threshold、43 条提到 range/sideways；耐久 reasoning 的
最小、最大和平均长度均为 200，确认不是自然短文本而是统一截断。64/116 属于相同
symbol/direction 的重复进入，平均间隔 54 分钟。

同一 b1.1 epoch 已结算 24h 反事实仍为：direct skip `n=106, avg_net=+1.4403%, positive=54.7%`；
trade-intent 后被 Debate/conviction 门控 `n=38, avg_net=-1.1394%, positive=39.5%`。这同时满足
“行为几乎全落在 direct 分支”和“两个分支后续结果方向相反”，足以确认存在过度 direct skip；但它
仍不是止损止盈账户 PnL，不能外推为每个被拦候选都应交易。

## 5. 已实施：单变量 Direct-Skip 语义实验（B 类）

本任务未与 A 类可观测性修复同 PR。owner 在看到后续增长的 direct skip 后明确授权先实施修复；
旧 `b1.1` 窗口在新代码首次运行时由 policy epoch 机制关闭。原建议的三个进入条件状态必须诚实保留：

1. `skip-halt-v2` 成熟窗口：**仍为 `awaiting_outcomes`**，因此不能把本次实现宣称为收益已证明；
2. 当前 epoch 的 direct/gated 分层及 symbol/direction 相关性：**已复算并作为 b1.1 baseline**；
3. owner 确认关闭旧行为窗口并创建新 policy epoch：**已明确授权**。

唯一 treatment：**direct skip 必须经过一次有界反事实确认，并给出至少一个与当前候选相关、独立于
`insufficient_evidence` 的 typed adverse reason/evidence；证据不足本身保持中性。** 第二次仍为中性
或无效时返回 `evaluation_deferred`，不写 final PiDecision、不消费 pending、不自动改成 trade。
同一 epoch、同一物质上下文在 120 分钟窗口内只调用一次“assessment + confirmation”；fingerprint
排除 Opportunity id/时间戳等 carrier churn，包含 instrument、方向、scanner thesis、rank priority、
B2 状态、候选技术特征与 regime，物质变化会重新开放评估。若确认改为 trade，仍完整经过既有 Debate/55/Risk 链。

配套 A 类修复：unsupported/missing action 与 malformed conviction 改为 typed unavailable；权威
reasoning 不再截断；`evidence_manifest.operator_rationale` 保存 skip kind、adverse/supporting/opposing/
risk evidence。已有 submitting claim 的恢复优先级不变。

Doctor 同时分列 `direct_model_skips`、`trade_intent_gated_skips`、`confirmed_adverse_skips` 与
`deferred_unchanged_context_events`。因此 b1.2 生效后可直接验证：旧 direct 洪泛是否下降、确认后的
adverse skip 是否仍有合理占比、neutral defer 是否被上下文去重，而不是只凭 Overview 视觉判断。

明确未改：55 分门槛、Debate 调整公式、Ranker 入选/权重、sizing、Risk、Execution、Exchange、Live、
额度与凭据。

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

本轮成功标准不是强行产生更多交易，而是减少无证据/重复 direct skip，同时保持所有下游安全门与
可重试性。行为贡献必须在 `b1.2` 新窗口用 direct/gated ratio、proposal funnel、queue age、24h net、
PF/ECE/回撤和 trace/seed coverage 判断；实现完成不等于已证明策略收益改善。
