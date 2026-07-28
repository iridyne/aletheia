# 文档体系金标准化 — 覆盖度地图与细化计划

> **Frozen migration history**: this plan has been executed and superseded by
> [`documentation_governance.md`](../../governance/policies/documentation_governance.md).
> Do not update its progress, counts, paths, or checklists. It is retained only
> to explain the previous migration.

> 目的:把 `docs/` 重构成"实现交易系统的金标准"金字塔——上层越宏观、下层越具体,
> 任何实现都能在文档里找到金标准依据,任何文档条目都能追到代码。
> 金标准冲突裁决:**文档为准,代码追文档**(已实现部分的偏差记为"待修 issue")。
> Created: 2026-06-21 · 基于一次全量审计(~100 份文件 / >1MB)

> **✅ 进展更新 2026-06-30**：§3「明显缺失需新建」的 P1/P2 契约（Risk/Execution/Live/Promotion/Knowledge/Scheduler/Audit）**均已建为金标准**（批次 1/2/6）。此后再增两份本计划未预见的契约：**`design/decision_lifecycle_contract`**（横切脊柱 seam —— 新契约类别，补"按域竖切、子系统接缝无人管"的结构空白）+ **`design/persistence_recovery_contract`**（D8，🟡 构件已实现/恢复 FSM 未接 runtime）。新增第四种导航 **`design/DOC_ATLAS_BY_DOMAIN`**（按功能域）。**收口纪律**（契约漂移标注须落 BACKEND_ISSUES / 状态≠意图只看 doctor+conformance / 完工即收口）已落入 [design/README §用法](../../design/README.md)——直接修 §1 诊断里"ledger 只进不出"的病根。全景见 [status/REMAINING_WORK_2026-06-30](../../status/snapshots/REMAINING_WORK_2026-06-30.md)。

> **当前定位（2026-07-18）**：本文是已经执行过的文档体系迁移计划，不是当前路径或
> 实现状态的权威清单；其中旧文件位置、数量和完成比例只作历史证据。当前入口依次看
> [`docs/README`](../../README.md)、[`design/README`](../../design/README.md)、
> [`DOC_ATLAS_BY_DOMAIN`](../../design/process/DOC_ATLAS_BY_DOMAIN.md)，运行状态看
> `uv run aletheia doctor`，契约覆盖看 `tests/test_spec_conformance.py`。

---

## 1. 核心诊断:问题不是"不够细",是"层次混淆"

全量审计(product/design/plans/governance/status + 目测 references/research/issues/_ai/archive)
得出的**真问题**不是文档不够详细——很多文档已经很细(agent_contracts 12KB、data_model 80 表、
implementation_guide 44KB)。真问题是:

> **"机器无关的真理"(愿景/规则/契约/架构) 与 "时间相关的快照"(进度/缺口/计划/状态)
> 混在同一批文档里**,导致没人能干净地回答三个不同的问题:
> "系统**该**是什么样?"(契约)· "现在**是**什么样?"(状态)· "**怎么**走过去?"(计划)。

金标准 = 第一类(契约)。它必须从第二、三类里**剥离干净**,否则会随进度过期、失去权威。

---

## 2. 五类归属(每份文档归且仅归一类)

| 类别 | 定义 | 金字塔角色 | 处置原则 |
|------|------|-----------|---------|
| **L0 愿景** | 赌什么/为谁/什么叫赢 | 顶端 | 已完成(VISION) |
| **L1 范围与度量** | 做什么/不做什么/怎么衡量/何时算过 | 上层 | 已较全(PRD/METRICS/PHASE_GATES) |
| **L2 契约-金标准** | 系统**该**是什么样,可作实现依据 | 中层骨干 | **本次细化重点** |
| **L3 计划** | **怎么**从现状走到目标(plan/RFP) | 旁支 | 保留在 plans/,提炼其契约内容上浮 L2 |
| **L4 状态/过程/参考** | 现在**是**什么样 / 怎么走到这 / 外部参考 | 易腐区 | 隔离,绝不当金标准 |

---

## 3. 覆盖度地图(审计结论)

### L2 契约层 — 现有骨干(已是金标准或接近)

| 文档 | 契约对象 | 细度 | 缺口 |
|------|---------|------|------|
| `design/data_model.md` | ~80 表 + 52 Pydantic | 高 | TTL/清理策略、JSON 列 schema 校验、死锁恢复 |
| `design/agent_contracts.md` | 30 job + 14 agent IO | 高 | timeout 配置、故障级联规则、LLM 降级 |
| `design/api_reference.md` | REST 端点目录 | 中高 | 响应体 schema、错误码、缺 operator/* 端点 |
| `governance/trust_and_change_control.md` | 因果链不变式+变更红线+晋级门 | 高 | 补"代码 file:line 证实"映射 |
| `status/WORLD_MAP.md` | 9 步循环+15 模块+数据契约 | 中 | 是目标态非现状,需标注 + 映射到 Phase |
| `design/ui_design_system.md` | 前端设计语言/密度 | 高 | — |
| `design/EQUITY_INTEGRATION_DESIGN.md` | xStocks 集成 | 高 | — |

### L2 契约层 — 明显缺失(需新建,优先级见 §5)

| 缺失契约 | 优先级 | 为什么是金标准骨干 |
|---------|--------|------------------|
| **Risk Engine 决策契约** | P1 | 五关审批/拒绝规则/硬限执行,是 trust 边界核心,目前散在 trust_and_change |
| **Execution 执行通路契约** | P1 | proposal→order→fill→position 原子性+交易所故障降级 |
| **Promotion Gates 规范** | P1 | 晋级门(14天/N笔/正期望/回撤)目前埋在 trust_and_change §5,应独立 |
| **Knowledge/检索契约** | P2 | FTS5 查询规范、item_type taxonomy、注入规则 |
| **Scheduler 调度契约** | P2 | job pool 争用、timeout、coalesce 语义 |
| **Live Unlock 信任边界** | P1 | 不可逆操作清单、approval log 格式、事后审计 |

### L3 计划层 — plans/(16 份)

- **仍有效待办 ~87%**:最近 1-2 周系统性产出的蓝图(PI 决策流/持久化/regime/universe/futures/serenity 等)。
- **应归档 2 份**:NAUTILUS_INTEGRATION_PLAN(明确暂不做)、data_layer_backlog(遥远未来)。
- **处置**:plans/ 保留为"演进中的设计"。把其中达到契约成熟度的内容**提炼上浮**到 L2,
  但**不照搬 agent 建议新建一堆 design 子目录**——按需建,避免过度结构化。

### L4 易腐/过程/参考层 — 需隔离

| 文档 | 现状 | 处置 |
|------|------|------|
| `governance/implementation_guide.md` (44KB) | MVP 初版规范,部分过时(栈/milestone),与 PRD/VISION 重叠 | **不整体归档**(仍含 CLI/目录/Risk 细节有效)。标注"v1 规范,契约以 L2 为准",逐步把有效契约迁出 |
| `status/ROADMAP.md` (62KB) | 混 Phase 定义(长期)+ Track 进度(易腐) | 拆:Phase 定义留 ROADMAP;Track 进度→PROGRESS 文件 |
| `status/PIPELINE_STATUS.md` | 纯进度快照,已过时 10 天 | 降级为周更工作文件,移出金标准集 |
| `status/WORLD_MAP.md` | 见 L2(目标态契约) | 标注"目标架构",建 Phase 映射 |
| `_ai/` `archive/` `research/` `references/` | 协作日志/历史/外部参考 | 维持现状,明确非金标准 |
| `issues/` | bug/contract drift 清单 | 维持;是"文档↔代码偏差"的落点 |

---

## 4. 细化的统一标准(L2 每份契约该长什么样)

一份 L2 契约文档"细到照着能实现"的验收线:

1. **目标态声明**:开篇标注这是"目标态(可能领先代码)"还是"已实现(代码为准)"。
2. **字段级定义**:每个数据结构/参数有类型、取值域、默认值、单位。
3. **边界与错误**:失败路径、降级策略、超时、幂等性。
4. **不变式**:这个契约绝不允许被违反的硬规则(链接 trust_and_change)。
5. **示例**:至少一个具体输入→输出实例。
6. **代码映射**:已实现部分标 `file:line`;未实现标 `[未实现]` + 对应 plan。
7. **新鲜度**:last-updated + 维护者。

---

## 5. 分批推进计划(可逐批验证,不一次吞下)

> 原则:先骨干后枝叶;先补 P1 缺失契约,再细化已有,最后隔离易腐。每批独立可提交。

- **批次 0(已完成)**:VISION.md 落地 + 本计划。
- **批次 1 — 补 P1 缺失契约(trust 边界核心)**:
  Risk Engine 契约、Execution 通路契约、Live Unlock 信任边界、Promotion Gates 规范。
  (从 trust_and_change + execution_path_honesty + 代码提炼)
- **批次 2 — 细化已有 L2 骨干到验收线**:
  data_model 补 TTL/校验;agent_contracts 补 timeout/级联;api_reference 补 schema/operator 端点。
- **批次 3 — 隔离易腐层**:
  ROADMAP 拆分(Phase 定义 vs 进度);PIPELINE_STATUS 降级;WORLD_MAP 标注+Phase 映射;
  implementation_guide 标注 v1。
- **批次 4 — L2/L3 收口**:
  plans/ 归档 2 份;把 plans 里的契约内容上浮;建 design/README 索引串起金字塔。
  > ✅ 完成。归档 NAUTILUS/data_layer_backlog 到 plans/archived/;建 design/README.md 作金字塔导航中枢。
  > **决定(克制)**:plans 里未实现功能的契约**不预先上浮**为 L2——否则制造一堆"未实现"金标准、
  > 违反"代码为底"。新功能契约等实现时再上浮;现有部分实现的已在 README 标"🟡 契约(部分实现)"。
- **批次 5 — 一致性核对**:
  抽样核对 L2 契约 vs 代码,偏差落 issues/。

---

## 6. 我对 audit agent 建议的偏离(避免过度工程化)

审计 agent 提了一些建议我**不采纳或修改**,理由:守 scope、不破坏现有结构:

- ❌ "新建 design/agents、design/storage、design/market... 一整套子目录" → 过度结构化;按需建单文件即可。
- ❌ "把 implementation_guide 整个迁去 archive" → 它仍含有效契约;标注 + 渐迁,不一刀切。
- ❌ "把多份文档改名(加 _TARGET/_V1/_SNAPSHOT 后缀)" → 改名破坏现有链接成本高;用文档内顶部标注代替改名。
- ✅ 采纳:契约/状态分离、补 P1 缺失契约、ROADMAP 拆分、WORLD_MAP 标注目标态。
