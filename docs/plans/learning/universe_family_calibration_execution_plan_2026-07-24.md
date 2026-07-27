# Universe Family Calibration 正向贡献执行计划

> 日期：2026-07-24
> 状态：Batch 1–3 已交付；单路径 shadow qualification 运行中，尚未准入 `paper_10`
> 范围：仅 `universe_family_calibration` 的测量、owner 可观测性、准入与 paper 实验
> 分类：测量/契约漂移修复为 A 类；任何改变候选分配的 paper treatment 为 B 类
> 上位契约：
> [`learning_contract`](../../design/contracts/learning_contract.md)、
> [`learning_evolution_contract`](../../design/contracts/learning_evolution_contract.md)、
> [`learning_observability_contract`](../../design/contracts/learning_observability_contract.md)
> 总体交付序列：
> [`learning_positive_contribution_implementation_plan_2026-07-12`](learning_positive_contribution_implementation_plan_2026-07-12.md)

## 1. 结论先行

Learning 现在最需要的不是“学得更多”，而是证明一条经验是否真的让同一类候选的
成本后净结果优于未使用该经验的对照组。

本计划只选择第一条最短、最可验证的路径：

```text
Universe 候选族群
  → 固定 treatment/control 分配
  → 精确连接同一候选的未来结果
  → 形成成本后增量贡献 verdict
  → owner 看到证据
  → owner 允许 10% paper treatment
  → 新窗口重新证明
  → 有害、失真或无证据时回到静态基线
```

“产生正向贡献”分为两个不同层级，不能混报：

1. **Level 1，路径贡献**：treatment 相对 control 的成本后候选结果有正增量，
   且样本、时间、身份、置信度和伤害门全部满足。这证明 Learning 对这条路径有用。
2. **Level 2，账户贡献**：在与真实执行语义匹配的 `bitget_demo` 新实验窗口中，
   Learning treatment 对账户净值、Profit Factor、回撤等有正向影响。这才允许说
   Learning 对赚钱产生了可验证贡献。

`paper_internal` 足以验证 Level 1 的因果账本和系统闭环，但不能替代
[`EXPERIMENT_PROTOCOL`](../../product/experiments/EXPERIMENT_PROTOCOL.md) 要求的
`bitget_demo` 来证明 Level 2。当前缺少 demo credentials，因此 Level 2 是明确的
外部前置条件，不以模拟结果冒充完成。

## 2. 第一性原理

一条经验只有同时回答以下问题，才是可用学习：

- 它改变了谁的行为？
- 哪些候选接受 treatment，哪些保留 control？
- 两组是否来自同一个可比较窗口？
- 后续结果是否精确属于原候选、原 horizon、原 venue 和原 policy epoch？
- 扣除费用后，treatment 是否比 control 更好？
- 这种改善是否以更大回撤、集中度、漏选机会或数据污染为代价？
- 证据失效时，系统是否自动撤回影响？

因此：

```text
经验数量 ≠ 学习
被调用次数 ≠ 贡献
treatment 单边盈利 ≠ 增量贡献
历史相关性 ≠ 可晋级的因果证据
```

唯一可接受的主张是：

```text
同一预声明窗口内，
treatment - control 的成本后增量超过预声明阈值，
且无伤害门失败、无身份缺口、无数据事故。
```

## 3. 当前事实基线

2026-07-24 在隔离 worktree、`origin/master` 基线上执行 `uv sync` 与
`uv run aletheia doctor`。运行事实会漂移，实施每一批前必须重新采样。

| 当前事实 | 含义 |
| --- | --- |
| `paper_influence_enabled=false` | Learning 当前没有 paper 行为权，净值平坦不能直接归因于 Learning 失败 |
| `lifecycle_automation_enabled=false` | 不存在自动扩权 |
| Round 1 当前 35/60 笔、约 +0.80 USDT、PF 约 1.086 | 尚未达到全局盈利判决门，且不能说明 Learning 的增量 |
| 24h 信号胜率约 44.1%，平均净结果约 -0.515% | 整体候选质量仍弱，必须防止把市场/决策链损失错归给 Learning |
| shadow backlog 很大 | 是运行健康债，但不是 Universe canonical outcome 的贡献判决分母 |
| 无 Bitget demo credentials | Level 2 暂不能开始 |

先前数据库审计曾看到大量 hypothesis、experiment、exposure 和 verdict，但没有
positive verdict。数量本身不能证明“经验在积累”；它只证明系统在产生日志。
本计划以后只按当前 path、当前 experiment、当前 policy epoch 的完整 cohort 判决，
不以全库累计数量作为成功指标。

## 4. 反向评估发现的设计漂移

### 4.1 Owner admission 不是“已完成”

代码已有：

```text
POST /api/settings/learning/experiments/{experiment_id}/admit-trial
```

但当前事实是：

- 路由不在 `app.py` 的 tunnel protected path 集合中；
- 请求没有独立的认证 dependency；
- `admitted_by="authenticated_dashboard_operator"` 是服务端常量，不是认证证据；
- L2 契约与 `LE-L04` 仍声明 authenticated-owner mutation boundary 不存在；
- owner observability spec 仍要求 `mutation_available=false`。

所以不能直接调用或展示它为可用能力。必须先决定并证明统一语义：

1. 若产品仍采用 local-first + tunnel token 的现有安全模型，则 admission 路径必须
   进入同一受保护边界，并由测试证明 tunnel 开启时 fail-closed；
2. audit actor 必须来自已经验证的边界语义，不能由客户端自由传入；
3. API、L2 contract、API reference 和 executable spec 同一 PR 更新；
4. 在这些证据全绿前，UI 只能显示只读 recommendation。

这不是新增认证平台，也不引入用户系统、OAuth、RBAC 或 secrets 变化。

### 4.2 实验唯一性无需过度设计

数据库已有 partial unique index：

```text
UNIQUE (hypothesis_id) WHERE ended_at IS NULL
```

它比“每个 stage/epoch 一个 current experiment”更严格：同一 hypothesis 同时只能有
一个未结束实验，历史 successor 通过 parent identity 保留。第一阶段只需要验证：

- predecessor 关闭与 successor 创建在同一事务；
- 并发/重试不会产生两个 open experiment；
- policy epoch 变化会关闭或作废旧窗口，而不是把两个 epoch 混合。

除非测试证明现有约束无法表达生命周期，否则不增加新表、锁服务或第二套 current
pointer。

### 4.3 目标 cohort 与全局 backlog 分离

- Universe 的权威 outcome 是 canonical `filter_candidate_outcomes`。
- `shadow_trades` 全局积压属于运行健康问题，不作为本路径 verdict 的直接分母。
- 不要求全库历史 `invalid=0`；旧错误记录必须保留。
- 要求的是当前 experiment/epoch 的结构性 invalid 在连续 evaluator 窗口为 0，
  且所有 exclusion 都有 typed reason。

### 4.4 支持路径声明需要收敛

当前 `SUPPORTED_PATH_IDS` 已包含 `operator_learning_bundle`，但文件注释、L2 文档和
先前审计对“当前唯一可晋级路径”的描述不一致。本计划不顺手扩展 Operator 路径。
第一批只收敛声明与测试，使 Universe 是本次唯一启动的 contribution experiment；
Operator 保持无行为权，另立计划后再评估。

## 5. 最小实施序列

只有四批。每批独立 PR、独立证据、可停可回退；上一批未通过不得启动下一批。

### Batch 1 — 测量与契约完整性

**类别：A。目标：先让事实可信，不改变候选选择。**

检查并只在证据显示需要时修复：

- Universe exposure → canonical candidate outcome 的 exact identity；
- treatment/control 完整 cluster、去重、fallback/mirror disagreement；
- policy epoch、stage、experiment、verdict 的 current identity；
- open experiment 唯一性、重试幂等、predecessor/successor 事务性；
- evaluator catch-up、failure identity 和当前 cohort 的 typed exclusions；
- `SUPPORTED_PATH_IDS`、注释、L2 contracts 与 specs 的漂移；
- 未认证 admission 路由在收敛前保持不可用。

预期主要触及：

```text
aletheia/learning/{cohorts,experiments,ledger,lifecycle,contribution_runner}.py
aletheia/storage/migrations.py                 # 仅当现有约束确有缺口
tests/specs/spec_learning_*.py
tests/test_learning_*.py
docs/design/contracts/learning_*.md
docs/design/api/api_reference.md
```

退出门：

- focused specs 证明 exact identity、完整 cluster、无镜像双计数；
- 当前 Universe cohort 连续两个 evaluator 窗口结构性 invalid 为 0；
- policy epoch 变化不能混池；
- 同一 hypothesis 不可能存在两个 open experiments；
- `paper_influence_enabled` 仍为 false；
- 无交易方向、size、Risk、Execution 或 Live 行为变化。

### Batch 2 — 最小 owner 贡献投影

**类别：A。目标：让 owner 看见证据，不造一个“Learning 工作台”。**

只提供一个聚合详情面：

- current path / hypothesis / experiment / stage / policy epoch；
- treatment/control 样本、市场天数、cost-aware delta；
- practical threshold 与 hypothesis effect threshold；
- confidence、harm gates、typed exclusions；
- source fallback/disagreement；
- evaluator last success/failure/catch-up；
- recommended lifecycle action；
- `mutation_available=false`，直到 Batch 3 通过。

复用现有 read model 和 status query；异步 route 不做热路径阻塞扫描。前端只做诚实状态：
`insufficient data`、`invalid`、`negative`、`eligible for owner review`，不显示
“AI improving”或虚构 readiness。

退出门：

- `LCO-*` executable specs 由 missing/xfail 转为真实 passing claims；
- API 与 UI 对同一 fixture 得出一致状态；
- endpoint 性能按 async checklist 实测；
- `cd frontend && npm run build` 通过；
- 仍无 mutation 和 paper influence。

### Batch 3 — 认证 owner admission 边界

**类别：A（安全/契约修复）。目标：只允许 owner 把一个合格 shadow 实验送入
`paper_10`，不自动晋级。**

最小方案优先复用现有 tunnel token 边界：

- admission 路径加入 protected path；
- tunnel 开启且 token 缺失/错误时 401 fail-closed；
- admission 只接受 exact current experiment、exact current verdict、
  exact policy epoch 和非空 reason；
- verdict 必须是完整的
  `insufficient_data/no_actual_intervention` admission recommendation；
- 事务内关闭 shadow predecessor，创建 fresh-salt `paper_10` successor；
- audit 保留 actor/source/entity/before/after/causal identity；
- 重试幂等，陈旧 verdict、旧 epoch、错误 path 全部 hold；
- 非 HTTP 的 `admit_shadow_trial()` 继续 fail-closed。

该批触及 server route/auth guard，因此必须同步：

```text
docs/design/contracts/learning_{contract,evolution_contract,observability_contract}.md
docs/design/api/api_reference.md
tests/specs/spec_{api_reference,learning_lifecycle,learning_observability}.py
tests/test_learning_lifecycle.py
```

退出门：

- 认证、未认证、陈旧 identity、并发重试、审计链测试全绿；
- UI 在且仅在边界可用并且证据合格时显示 owner action；
- 不提供 auto-admit；
- 不改变 Risk、Execution、credentials、Live 或 hard limits。

### Batch 4 — 单一 `paper_10` B 类实验

**类别：B。目标：第一次让 Universe Learning 获得 10% paper treatment，并证明或
否证 Level 1。**

启动前预声明并冻结：

- 唯一 treatment：`universe_family_calibration`；
- allocation：10%，保留永久 control；
- assignment salt、policy epoch、metric version、horizon、venue；
- primary metric：cost-aware net return delta；
- practical neutral floor 与 hypothesis `effect_threshold`；
- 生效阈值：
  `required_delta = max(practical_neutral_floor, hypothesis.effect_threshold)`；
- harm gates：loss rate、MAE、drawdown、concentration、candidate supply/no-trade、
  freshness/unresolved；
- minimum samples per arm 和 minimum market days；
- 唯一允许比较的 current experiment window。

该批只允许**一个**会改变行为的变量。冻结 Universe 分配逻辑、Ranker weights、
Operator prompt/conviction、sizing、Risk、Execution 与 venue。安全收紧始终允许，
但一旦发生即终止当前实验并创建新 epoch，不能继续混池。

状态机：

```text
shadow
  -- authenticated owner admission -->
paper_10
  -- fresh experiment + fresh salt + positive gates -->
paper_25 → paper_50 → paper_active
```

本计划只负责启动并判决 `paper_10`；后续 stage 不是默认工作，必须由当时的新证据和
owner 决定。

立即 refreeze/stop 的条件：

- invalid verdict 或 identity/data incident；
- cost-aware delta 为 negative；
- 任一 harm gate 失败；
- assignment、epoch、metric version 或 outcome link 不完整；
- evaluator 连续失败或结果超时；
- control 比例被侵蚀；
- 任一未预声明的行为变量变化。

Level 1 通过后，才可在具备 credentials、venue/execution identity 匹配并重新预声明
窗口的 `bitget_demo` 中验证 Level 2。两个环境的数据不合并。

## 6. 判决矩阵

| 证据 | 继续 | 保持 | 立即撤回 |
| --- | --- | --- | --- |
| 样本/天数不足但 identity 完整 | 收集 | 是 | 否 |
| delta 未超过 required threshold | 否 | control/static | 否 |
| positive + confidence + all harm green | 允许 owner review | 否 | 否 |
| negative | 否 | 否 | 是 |
| invalid / identity incomplete | 否 | 否 | 是 |
| safety tightening / policy drift | 新 epoch 后重来 | 否 | 当前窗口作废 |
| evaluator/data health red | 否 | 否 | 是 |

“positive”必须同时满足：

```text
minimum sample/time
AND delta > required_delta
AND confidence gate
AND every harm gate
AND complete current identity
AND evaluator/data health green
AND no stronger contradictory evidence
```

## 7. 验证与证据

每批都从新基线开始：

```bash
git pull --ff-only
uv sync
uv run aletheia doctor
```

macOS 只运行 focused tests，不运行大规模 pytest。建议证据序列：

```bash
uv run pytest tests/specs/spec_learning_ledger.py -q
uv run pytest tests/specs/spec_learning_contribution.py -q
uv run pytest tests/specs/spec_learning_lifecycle.py -q
uv run pytest tests/specs/spec_learning_observability.py -q
uv run pytest tests/test_learning_lifecycle.py -q
uv run pytest tests/test_learning_verdict_runtime.py -q
uv run pytest tests/test_spec_conformance.py -q
```

只执行与本批修改直接相关的子集；扩大测试在 self-hosted CI 完成。前端批次另运行：

```bash
cd frontend && npm run build
```

运行判决的证据包至少包含：

- Git commit / PR / green CI；
- experiment、parent、stage、salt、epoch、metric version；
- admission verdict 与 owner audit identity；
- treatment/control sample、market days、delta、confidence；
- harm gates、exclusion reasons、data/evaluator health；
- stop/promote/refreeze 结论；
- `doctor` 当时的 environment/execution-mode 事实。

## 8. Issue 对照

| Issue | 本计划处理方式 |
| --- | --- |
| #160 Learning umbrella | 用四批 PR 和运行 verdict 回填，不以 artifact 数量关闭 |
| #323 observer/outcome identity | Batch 1 复核 current identity 与 residual drift；不重开已证明修复 |
| #324 allocation semantics | Batch 1 复核 current assignment/epoch；Batch 4 创建唯一新 B 类窗口 |

任何新发现追加到对应 existing issue；只有无法归入上述 scope 的独立缺陷才新建 issue。

## 9. 明确不做

- 不承诺盈利，不把净值短期上涨写成 Learning 成功；
- 不同时启动多个 learning path；
- 不实现 RL、vector DB、在线自改 prompt、自动策略生成；
- 不建立新的 auth 平台、任务数据库、实验编排服务或第二套 ledger；
- 不修改 Risk Engine、Execution Gateway、exchange client、credentials 或 Live；
- 不清洗/删除历史 invalid verdict；
- 不把全局 shadow backlog 当作 Universe verdict；
- 不在第一版构建复杂 dashboard；
- 不越过 `paper_10` 自动扩权。

## 10. 执行顺序与当前 checkpoint

```text
[x] 完整诊断与第一性原理反向评估
[x] 发现 admission 实现与 SDD/auth 的关键漂移
[x] 删除 stage/epoch 新唯一性框架等过度设计
[x] Batch 1：测量与契约完整性（PR #378）
[x] Batch 2：最小 owner 投影（PR #382）
[x] Batch 3：认证 owner admission（PR #383）
[ ] Pre-Batch 4：当前 UFC shadow epoch 取得可执行 admission verdict
[ ] Batch 4：启动并判决一个 paper_10 实验
[ ] Level 2：具备 bitget_demo 前置条件后另开新窗口
```

### 2026-07-27 运行 checkpoint

- 部署身份仍为 `a8c1b786`，UFC policy epoch 仍为 `2cea85ac734b`，
  `live_disabled + paper_internal` 未变。
- Learning 三个运行 gate 已开启，但 allowlist 仍只包含
  `universe_family_calibration`；当前 exposure 的 `authority_applied` 仍为 0，
  所以这仍是 fail-closed shadow qualification，不是 `paper_10`。
- 当前 epoch 已积累数千条 4h canonical outcome，但它们全部排在约 16.6 万条
  历史 overdue canonical backlog 后，LCO 因此持续得到 0 个 settled
  treatment/control sample。单纯等待 7 天不能解决这个测量饥饿。
- 当前修复只在既有 canonical settlement quota 内优先结算 open UFC epoch 的
  exact outcome；不改变评分、assignment、经济公式、阈值、Risk、Execution 或
  trading behavior，分类为 A。
- 7 天只是一项必要条件。只有 settled sample、完整 assignment unit、identity、
  confidence 与 harm gates 同时满足，owner 才能考虑 admission；服务中断或 backlog
  造成的无数据时段不能被当作有效证据。

当前安全状态是：

```text
paper_influence_enabled = true
lifecycle_automation_enabled = true
paper_learning_paths = [universe_family_calibration]
current stage = shadow
authority_applied = 0
owner admission = not yet eligible
```

这里的两个 enabled gate 只让受保护的 lifecycle/authority 路径具备运行条件；
它们不等于已经获得 treatment authority。直到当前 shadow cohort 取得 exact current
admission verdict 且 owner 明确确认，才允许 Batch 4 的单路径 10% paper treatment。
任何无法证明的地方都保持 baseline，而不是猜测性修复。
