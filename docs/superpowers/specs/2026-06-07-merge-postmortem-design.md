# 合并复审备忘录 — 留远程驾驶舱、弃本地 5 区外壳

- 日期：2026-06-07
- 类型：决策记录（无代码改动）
- 关联合并：`ace780a Merge origin/master: adopt remote cockpit + hardened backend`
- 关联本地分支顶点（已弃）：`02449f0`

## 1. 背景

本地 3 个提交（`9d91b15`/`edb791b`/`02449f0`）和远程 9 个提交（`ab29932`..`eb50f7f`）各自独立实现了一套 "Agentic Trading Cockpit"，但改动落在**同一批前端文件**（`App.tsx`、`index.css`、各 `pages/*.tsx`），不可逐行合并，只能整套二选一。

合并者（上一会话）选择保留远程驾驶舱 + 远程后端硬化。本备忘录是对该决策的事后复审。

## 2. 复审依据（来自项目指导文件）

| 来源 | 关键判据 |
|---|---|
| `AGENTS.md::Preferred Direction` | 优先级 1.状态一致性 → 7.功能容量 → 自主；UI 外壳工作属功能容量层 |
| `AGENTS.md::No Uncontrolled Expansion` | 不做"大重构、样式重写、广泛清理"；推翻 UI 外壳违反此条 |
| `docs/north_star_references.md §7` | 当前在阶段 0（稳跑验证），阶段 2 才考虑经验闭环 |
| `docs/issue_register.md::ISSUE-008` | News Intelligence 应隐藏/标记实验，不应再扩 news 面 |
| memory `aletheia-portfolio-sync-gap` | Portfolio 缺真实账户数据是**后端**缺 Exchange Account Sync 层，不是 UI 问题 |
| memory `aletheia-stay-in-guide-scope` | 不做超出实现指南范围的扩张 |

## 3. 18 个本地独有文件的逐项判定

被合并丢弃的本地独有文件（`git diff --name-only 243c267..02449f0` 减去远程同名变更）：

| # | 文件 | 判定 | 理由 |
|---|---|---|---|
| 1 | `frontend/src/components/LeftNav.tsx` | 弃 | 远程已有等效 NavLink 组导航；挪它=外壳重写 |
| 2 | `frontend/src/components/RightInspector.tsx` | 弃 | 远程 `DetailDrawer/ObjectDetail` 已等效；挪它要改 14 个页面 |
| 3 | `frontend/src/components/BottomTimeline.tsx` | 弃 | 远程 `ActivityTimeline` 已等效 |
| 4 | `frontend/src/components/TopStatusBar.tsx`（本地版） | 弃 | 远程 `App.tsx::StatusBar` 已等效 |
| 5 | `frontend/src/components/CommandCenter.tsx` | 弃 | 远程 `CommandPalette` 已等效 |
| 6 | `frontend/src/context/InspectorContext.tsx` | 弃 | 1-5 的依赖，独存无意义 |
| 7 | `frontend/src/pages/CommandCenterPage.tsx` | 弃 | 五区外壳的派生页 |
| 8 | `frontend/src/pages/NewsIntelligence.tsx`（本地版） | 弃 | 比远程 `NewsIntel.tsx` 更重，违反 `ISSUE-008` |
| 9 | `frontend/src/components/shared/ActionBar.tsx` | 弃 | 与远程 `DataTable` 形成第二套设计系统 |
| 10 | `frontend/src/components/shared/EntityCard.tsx` | 弃 | 同上 |
| 11 | `frontend/src/components/shared/EvidenceStack.tsx` | 弃 | 同上 |
| 12 | `frontend/src/components/shared/MetricTile.tsx` | 弃 | 同上 |
| 13 | `frontend/src/components/shared/StatusPill.tsx` | 弃 | 同上 |
| 14 | `frontend/src/utils/statusColor.ts` | 弃 | shared/* 工具，外壳弃则无依赖者 |
| 15 | `frontend/src/pages/Portfolio.tsx`（合并版） | 弃 + 存档 | 形态贴近 portfolio-sync-gap 的"单页全貌"UI 雏形，但**本身不修 gap**（数据源仍只是本地 SQLite）。真正修复要做后端 Exchange Account Sync。设计意图存档为该 issue 未来 UI 候选 |
| 16 | `aletheia/dashboard_static/assets/index-B7sWHzMK.css` | 弃 | 编译产物，跟源码走 |
| 17 | `aletheia/dashboard_static/assets/index-HIfHPKiO.js` | 弃 | 编译产物，跟源码走 |
| 18 | `frontend/tsconfig.tsbuildinfo` | 弃 | 构建中间文件 |

附：`docs/issue_register.md` 在远程未改、本地新增，已正常保留在合并结果中——不是冲突项。

## 4. 复审结论

**18 项中 0 项值得移植回当前主干。**

- 编辑器与组件层（#1–#7、#9–#14）属"UI 外壳"，AGENTS.md 把功能层排在第 6 优先级，且明确禁止大重构。
- News 增强（#8）与 ISSUE-008 矛盾。
- 合并版 Portfolio（#15）外形贴近 north-star，但内里仍只看本地 SQLite，**不动后端就修不了 portfolio-sync-gap**。把它移植过来等于先建橱窗，再考虑商品——次序错了。
- 编译产物（#16–#18）随源码走。

合并决策（保留远程驾驶舱 + 远程后端硬化）与项目方向**完全对齐**。

## 5. 善后动作（仅文档改动，不碰代码）

下面 3 个动作是本备忘录的**直接产出物**——把决策固化进项目档案，让下个会话不会再问"为什么本地 5 区不要了"。

### 5.1 在 `docs/DECISIONS.md` 追加本次合并决策条目

格式与现有 `2026-06-07 - Gateway: Pure ctypes` 条目一致：Context / Decision / Reasoning / Consequences / Alternatives。内容来自本备忘录第 1-4 节摘要。

### 5.2 在 `docs/BACKEND_ISSUES.md` 立案 `portfolio-sync-gap`

memory 条目应同时落地为可执行的后端 issue：

- 严重度建议 P2（北极星方向，但当前阶段 0 不动）
- 边界：`exchanges/bitget/`、`portfolio/`、`server/routes/data.py`
- 症状：Bitget demo 连接成功（10,000 USDT 模拟资金），Portfolio 显示全 0
- 根因：缺 Exchange Account Sync 层；`compute_portfolio()` 只看本地 SQLite positions
- 修复方向：新增 Exchange Account Sync 服务（只读拉取 account equity/positions/orders），写入本地统一视图区分 `aletheia_managed` vs `external_positions`，Portfolio API merge 后返回。**触及受控模块 `aletheia/exchanges/bitget/client.py`，需显式授权后才能动工**。
- UI 候选方案：参考 git `02449f0:frontend/src/pages/Portfolio.tsx`（合并版三标签 positions/trades/pnl）

### 5.3 更新 `docs/AI_HANDOFF.md` 的 Known Issues

当前 AI_HANDOFF.md::Known Issues 写"All documented issues … No remaining open items"，与 memory 中两条 north-star gap 矛盾。补两行：

- `portfolio-sync-gap`（已立案到 BACKEND_ISSUES.md，见 §5.2）
- `experience-loop-gap`（north-star 阶段 2 范畴，详见 `north_star_references.md §6`）

并把 Next Recommended Step 从"build/test/start"改成对齐 north-star §7 阶段 0 的稳跑验证目标。

## 6. 不做的事（明确边界）

- 不动 `frontend/src/`、`aletheia/dashboard_static/` 任何文件。
- 不 cherry-pick `02449f0` 任何提交。
- 不开始 portfolio-sync-gap 的代码修复——仅立案、留待显式授权。
- 不动测试、不动后端、不动配置。

## 7. 验证

文档落地后：

- `git diff --stat` 应只显示 3 个文件变更：`DECISIONS.md`、`BACKEND_ISSUES.md`、`AI_HANDOFF.md`，外加本备忘录新增。
- `git status` 工作树只在文档面有改动。
- `pytest -q` 不需要重跑（无代码改动），但建议确认仍为 `352 passed`，证明合并基线未被本会话扰动。
