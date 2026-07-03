# 双机开发金标准(DEV_PROTOCOL)

> 意图进 git，事实绝不进 git；事实只从机器现场探测。

## 1. 为什么会混乱（根因）

git 同步的是**意图**——代码 + 文档（计划、决策、设计）。
git **不**同步**运行态事实**——这些都是每台机器本地的、且 gitignored 的：

| 运行态事实 | 存在哪 | 是否进 git |
|---|---|---|
| gateway 进程是否在跑 | OS 进程表 / pidfile | ✗ 本地 |
| `app.db`（orders/fills/opportunities）| `data/app.db` | ✗ gitignored |
| `config.yaml`（开关 / kill_switch）| `data/config.yaml` | ✗ gitignored |
| secrets（凭证）| Windows 凭据管理器 / 加密文件 | ✗ 本地 |
| 观察期跑了多久 | 由上面派生 | ✗ 本地 |

**错误做法**：把"gateway 在跑、观察期第 30 小时"写进 `CURRENT_TASK.md` 并 commit。B 机 pull 下来这句话**必然是谎**——B 机的 gateway 没在跑、`app.db` 是另一份。这不是忘了更新，是**把易变事实硬编码进版本控制**本身错了。

## 2. 金标准三原则

1. **文档只写机器无关、长期有效的意图**：roadmap、架构决策、设计、阶段目标、待办计划。删掉所有"现在 gateway 在跑 / 凭证已存 / 观察期第 N 小时 / demo 单已落"这类句子。`CURRENT_TASK.md` 是纯计划，不是状态快照。

2. **事实的唯一来源 = 机器本身**，用 `aletheia doctor` 现场探测（只读、零副作用）。`--fix` 保守修复基础设施，**绝不**自动清 kill_switch 或改交易态。**永远信 doctor，不信文档叙事。**

3. **变更史（AI_HANDOFF / DEVLOG）是 append-only 历史**，记录"做了什么改动及其根因"——机器无关、可进 git。其中若不慎写了"当时机器状态"，读者须明白那是写入时刻的快照，当前永远用 doctor 取。

## 3. 开发节奏（2026-06-16）

边开发边观察，无隔离观察窗口。`code_drift` 自愈重启是预期行为，不是失败信号。

唯一硬阻塞线：**Phase 6 (Live)** 需要用户显式授权。其余 Phase 和 Track B 可并行推进，改动不触 `execution/`/凭证/live 即可自主合并。

## 4. 大批量修复经验入口（2026-07-03）

`discussion.md` 批次曾同时打开 20+ worktree,覆盖前端延迟、Universe、Learning、pytest、
侧边栏 IA、System Operator、DB retention 等问题。后续 AI 接类似大任务前,先读:

- `docs/_ai/DISCUSSION_DEVELOPMENT_LESSONS_2026-07-03.md`

核心原则:

1. 先把 owner 长文拆成可验证问题清单,不要直接改代码。
2. 审计分支和修复分支分开。
3. 性能问题先量化,再修 top offender。
4. 前端源码更新后必须 `npm run build` 并重启 gateway,否则 UI 仍是旧静态包。
5. 合并后清理已并入 `master` 的 worktree 和本地分支。
6. `reduce_size`、DB retention、历史审计迁移、live/风控语义必须 owner 决策。
