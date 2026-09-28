# 17 · 屎山修复任务卡索引（可下发给外部 Agent）

> 日期：2026-09-28
> 性质：**计划文档，不含代码改动，不含 issue 创建操作。**
> 目的：把之前分散在 3 份 audit + 2 条记忆 + 1 份 batch 报告里的"要修的地方"整理成
> 一张可下发的清单。每一项要么指向**已有 open issue**，要么标明**需要新建 issue**并
> 附上给外部 Agent 用的 prompt 模板。
> 主线依据：
> - `DESHITIFY_BATCH_2026-09-27.md` §未拆但已 audit
> - `docs/design/architecture/ARCHITECTURE.md#module-seams`（"未拆的巨型函数"表）
> - 记忆 `project_shit_cleanup_batch1_2026-09-26.md`（段 1-2 已合、段 3-7 待做）
> - 记忆 `project_minimum_profit_loop_shit_map_2026-09-26.md`（7 段全景 + 跨段工具）
> - 归档 audit `docs/archive/legacy/issues/audits/LEARNING_MODULE_SDD_VS_REALITY_AUDIT_2026-07-19.md`
> - 归档 audit `docs/archive/legacy/issues/audits/CANDIDATES_ADMISSION_GAP_AUDIT_2026-07-20.md`
> - 本次 rival-2026-09-28 报告 `09_next_targets.md`

---

## 0. 一句话总览

**六项任务卡里，3 项完全走已有 open issue（#3523/#3618/#3619/#3642/#3643/#3644/#3645），2 项需要新建 issue 前先做一次"审计对齐"（Learning 20-gap 与 Admission GAP-3-5 已过两个月），1 项需要重复现 bug 后再立卡（段 3 `_run_coro` 跨 loop bug）。**

外部 Agent 可以**立刻领**的清单：**已有 5 张 open issue**（表 A）。
外部 Agent **不能盲目领**的清单：**5 项需要先做"是否仍成立"的复核**（表 B），复核完再决定是否新建 issue。

---

## 1. 任务卡分域索引

### 表 A · 立刻可下发（已有 open issue）

| 卡 # | issue | 标题（截断） | 归属 | 状态 | 建议 Agent 档次 |
|---|---|---|---|---|---|
| A1 | [#3523](https://github.com/iridyne/aletheia/issues/3523) | cross-segment: consolidate `_utcnow`×18 / `_canonical_json`×9 / `_table_exists`×6 | 跨段工具集中化 | OPEN · `status/scoped` | **deepseek/omp 小任务**（纯 mechanical import 换名 + 补 tests/mock 修正） |
| A2 | [#3644](https://github.com/iridyne/aletheia/issues/3644) | universe 路径 `opportunity_id` 恒 None → family=unknown | 归因链 | OPEN | **codex+5.6-luna 深度调查** + 落 D1 路线 B（引 EntryPlan 键） |
| A3 | [#3642](https://github.com/iridyne/aletheia/issues/3642) | `/api/universe/planning-history` 20.4MB / `planner_input_json` 92% | API 投影 | OPEN | **deepseek**（照 #3631 的 `PositionSummaryView` 范式复用） |
| A4 | [#3643](https://github.com/iridyne/aletheia/issues/3643) | `candidate_planning_generations` 零 retention | 存储 | OPEN | **deepseek**（与 A3 同表，合并做收益最大） |
| A5 | [#3645](https://github.com/iridyne/aletheia/issues/3645) | 同步 job 裸 `fn()` 无墙钟上界，可永久占死车道 | 调度 | OPEN | **codex+6-astra**（触及 scheduler + 资金安全组合风险） |

### 表 B · 需要先"复核对齐"再决定要不要立卡

| 卡 # | 原始来源 | 主张 | 为什么不能直接立卡 | 建议动作 |
|---|---|---|---|---|
| B1 | ARCHITECTURE.md#module-seams 未拆表 | 7 个巨型函数未拆（refresh 564 / init_scheduler 467 / _evaluate_proposal_locked 595 / _execute_proposal 516 / _fetch_single_feature 487 / settle_due_filter_outcomes 455 / resolve_observation_treatment 480） | 2 个（refresh、init_scheduler）阻拦是 spec 用 `inspect.getsource` AST 断言函数体，**必须先等 [#3561](https://github.com/iridyne/aletheia/issues/3561)** 把行为断言改成契约断言；4 个"未 audit"；1 个 RISKY | 每个函数一张 audit-only issue（先诊断成本/收益，别立刻拆） |
| B2 | 记忆 `project_shit_cleanup_batch1_2026-09-26.md` §段 3 top 4 | `reconciliation.py:1126/1583`、`:1442`、`coordinator.py:823-826`、`gateway.py:560`（+`:653`） | 都是**真 bug**（状态机权威写 / 数据完整性 / 风控 leg / UTA identity），astra 判过要 **owner D1 决定**才动，且 `gateway.py:560+653` 是复合 patch，需 fixture | 每个位点一张 D1 决策 issue，body 写清"改动 = 契约变，需 owner 批准" |
| B3 | 记忆 `project_shit_cleanup_batch1_2026-09-26.md` §段 3 已知 bug | `_run_coro` ×3 跨 loop bug（DECISIONS.md §2026-06-24 §1 已记） | 记忆写"已知"但**未附复现步骤**；不复现即立卡会被外部 Agent 无从下手 | 先落一张"复现脚本 + failing test"审计卡，产出复现后再谈修 |
| B4 | 归档 audit `LEARNING_MODULE_SDD_VS_REALITY_AUDIT_2026-07-19.md` | 20 gaps（4C/9H/7M），修复顺序 GAP-07→14→03→05→01 | **2026-07-19 → 现在两个月过去**；GAP 部分可能已修（例：`persona_rollout runtime_version` 已由 [#3551](https://github.com/iridyne/aletheia/issues/3551) 承接对应 GAP-04-类）；GAP-06 已被自我 REFUTED；直接立 20 卡会撞已修 | 先做一次"20 gap × master@HEAD"的存活性复核（≤2 hour），只对仍成立的立卡 |
| B5 | 归档 audit `CANDIDATES_ADMISSION_GAP_AUDIT_2026-07-20.md` | 5 个 GAP | audit 里已明确记：**GAP-1/2 已由 PR #155 关闭**，GAP-3 已开 [#157](https://github.com/iridyne/aletheia/issues/157) 未做，GAP-4/5 未立卡 | 只需对 GAP-3/4/5 复核仍成立即立 3 张卡（GAP-3 已有 #157，可能只需 2 张） |

### 表 C · 段 6（analytics + economics，32k 行，最脏）—— 独立处理

| 卡 # | 主张 | 现状 | 建议动作 |
|---|---|---|---|
| C1 | `project_minimum_profit_loop_shit_map_2026-09-26.md` 记：段 6 是 32k 行 + 306 try/except，**明显最脏**的一段 | **未开任何 issue** | 立一张 **audit umbrella issue**：不动手，只做"扫段 6 找 top-5 屎点"，产出后再拆子卡。避免一头扎进 32k 行 |

---

## 2. 依赖与优先级图

```
[前置解锁]
   #3561 (spec 行为断言 → 契约断言)
     └─→ B1a: universe/service.py::refresh() 拆分
     └─→ B1b: agent_runtime/scheduler.py::init_scheduler 拆分

[无前置，立刻可做]  按 ROI 排序：
   1. A1 (#3523)  ← 纯 mechanical，deepseek 一次做完
   2. A3 (#3642)  ← 修法已在 #3631 范式，10 倍收益
   3. A4 (#3643)  ← 与 A3 同表合做
   4. A2 (#3644)  ← 归因链，唯一阻塞"alpha 在哪一族"
   5. A5 (#3645)  ← 与 #3612/#3619 组合风险
   6. B4 复核 → 逐个立卡
   7. B5 复核 → GAP-4/5 立卡
   8. C1 段 6 audit umbrella（**不动手，只审计**）

[需 owner D1 决策]  不擅自领：
   B2 · 段 3 top 4（reconciliation / coordinator / gateway 权威路径）
   B3 · _run_coro 跨 loop bug（复现后再谈）
```

---

## 3. 给外部 Agent 的 prompt 模板（按分类）

### 3.1 表 A 型（已有 issue，直接领）

```
你的任务是完成 GitHub issue #<NUMBER>（<url>）。

前置阅读（不动手）：
1. `gh issue view <NUMBER>`
2. 记忆 [[project-shit-cleanup-batch1-2026-09-26]]（"翻车教训"那一节：改 log/exception message 前必须 grep tests/）
3. 契约文档 `docs/design/contracts/`（若 issue 触及交易语义）

工作纪律：
- 每个 PR 单一关注点（记忆 [[feedback-pr-hygiene-and-first-time-right]]）
- 修改前 `grep -rn "<改前字符串>" tests/` 找齐断言（记忆 [[feedback-dev-check-isnt-full-coverage-2026-09-26]]）
- PR body 里量词（"the only" / "the last"）先自 grep 验证（记忆 [[feedback-pr-body-survives-grep]]）
- Attribution 结尾按 CLAUDE.md 规则

产出：一个可 review 的 PR，body 里 `Fixes #<NUMBER>`，跑通 fast 测试。
```

### 3.2 表 B 型（audit-only，不动代码）

```
你的任务是对 <位点/模块> 做一次审计（不写代码）。

前置阅读：
- `docs/design/contracts/<对应契约>.md`
- 相关归档 audit：<路径>
- master@HEAD 的当前实现

产出（一份 markdown，放 `docs/research/audits/`）：
1. 该主张（"这是屎"或"存在 GAP"）在当前 master 是否仍成立
2. 若仍成立：blast radius（哪些文件/契约/测试）、修复成本估计（S/M/L）、
   D 级（D0 语义不变 / D1 契约变 / D2 架构变）
3. 若不成立：证据（commit/PR/文件位置）
4. 建议：立卡（附 prompt 骨架）/ 归档 / 与已有 issue 合并

不做的事：
- 不改代码
- 不创建 issue（把 prompt 骨架写在报告里等 owner 决定）
```

### 3.3 表 C 型（段 6 umbrella audit）

```
你的任务是给 core/aletheia/analytics/ + core/aletheia/economics/ 做一次
"top-5 屎点"扫描，用于制定后续修复计划。

背景：
- 该段 32.2k 行 / 306 try/except，是 7 段主线里最脏的一段
- 已知信号：`_utcnow -> str` ×7、`_utcnow -> datetime` ×4（已在 #3523 处理，
  不再扫）、`_table_exists` ×6（同 #3523）、`_round` ×5、`_finite` ×5

审计维度（每维找 top-5，各带文件:行）：
1. 双主线（同一意图有多份实现，非 #3523 已覆盖的工具函数）
2. 过度防御 `except Exception: return <中性值>` 触及经济/账本语义
3. 死代码 / 死分支（reachable=False 或有 issue 说要下线但代码仍在）
4. 契约漂移（docstring 或契约文档说 X，代码实际 Y）
5. 巨型函数（>300 行）

产出 markdown（放 `docs/research/audits/`）：
- 每维 top-5，附推荐处置（立卡 / 合入已有卡 / 归档 / 不动）
- 最脏 3 个点直接给 prompt 骨架
- **不改代码，不建 issue**
```

---

## 4. 建议执行顺序（给你决策）

**Phase 0（立刻，无 owner 干预）**：
- 把表 A 五张 issue 分派给外部 Agent：A1 → deepseek，A2 → codex+luna，A3+A4 打包 → deepseek，A5 → codex+astra
- 我不动手，只做 orchestrator 角色

**Phase 1（复核，1-2 hour）**：
- B4（Learning 20 gap 存活性）+ B5（Admission GAP-3/4/5）+ C1（段 6 top-5 扫描）
- 全部 audit-only 交给 luna，产出报告后我 review
- 复核完再决定要不要一次性 `gh issue create` 一批

**Phase 2（owner D1 决策后）**：
- B2 · 段 3 top 4 · 逐个开 D1 决策 issue，等你拍板
- B1 拆巨型函数 · 等 #3561 解锁

**Phase 3（复现后）**：
- B3 · `_run_coro` 跨 loop bug · 先审计出复现步骤再谈修

---

## 5. 本文档不做的事

- 不创建任何 GitHub issue（Phase 0/1 完成后一次性开）
- 不动任何代码
- 不给出"具体 commit 应该长什么样"的补丁 —— 那是外部 Agent 领卡后的事
- 不承诺"这些卡全部有效"——B4/B5 明确写了要先复核

---

## 6. 关联

- 主线汇总：[16_alpha_mechanism_comparison.md](16_alpha_mechanism_comparison.md)
- 下一轮靶点：[09_next_targets.md](09_next_targets.md)
- 屎山 batch1 记录：`docs/research/audits/DESHITIFY_BATCH_2026-09-27.md`
- 记忆索引：`~/.claude/projects/-Users-yixian-Projects-aletheia/memory/MEMORY.md`
