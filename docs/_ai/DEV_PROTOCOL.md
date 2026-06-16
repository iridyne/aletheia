# 双机开发金标准(DEV_PROTOCOL)

> 本项目在两台机器上通过 git 同步开发。本文件是避免"文档与事实不符"的金标准。
> 一句话:**意图进 git,事实绝不进 git;事实只从机器现场探测。**

## 1. 为什么会混乱(根因)

git 同步的是**意图**——代码 + 文档(计划、决策、设计)。
git **不**同步**运行态事实**——这些都是每台机器本地的、且 gitignored 的:

| 运行态事实 | 存在哪 | 是否进 git |
|---|---|---|
| gateway 进程是否在跑 | OS 进程表 / pidfile | ✗ 本地 |
| `app.db`(orders/fills/opportunities) | `data/app.db` | ✗ gitignored |
| `config.yaml`(开关 / kill_switch) | `data/config.yaml` | ✗ gitignored |
| secrets(凭证) | Windows 凭据管理器 / 加密文件 | ✗ 本地 |
| 观察期跑了多久 | 由上面派生 | ✗ 本地 |

**错误做法**(过去的病):把"A 机此刻 gateway 在跑、观察期第 30 小时"写进
`CURRENT_TASK.md` 并 commit。B 机 pull 下来,这句话**必然是谎**——B 机的
gateway 根本没在跑,B 机的 `app.db` 是另一份。这不是谁忘了更新文档,是
**把易变事实硬编码进版本控制**这个结构本身错了。

## 2. 金标准三原则

1. **文档只写机器无关、长期有效的意图**:roadmap、架构决策、设计、阶段目标、
   待办计划。删掉所有"现在 gateway 在跑 / 凭证已存 / 观察期第 N 小时 / demo
   单已落交易所"这类句子。`CURRENT_TASK.md` 是纯计划,不是状态快照。

2. **事实的唯一来源 = 机器本身**,用一条命令现场探测:**`aletheia doctor`**。
   它只读、零副作用,探测 7 个维度的真实状态(代码同步 / 进程 / 配置门 /
   凭证 / 数据新鲜度 / 闭环活性 / 健康)。**永远信 doctor,不信文档叙事。**

3. **变更史(AI_HANDOFF / DEVLOG)是 append-only 的历史记录**,记录"做了什么改动
   及其根因",这类内容机器无关、可以进 git。但其中若不慎写了"当时机器状态",
   读者须明白那是**写入时刻的快照**,不代表当前——当前永远用 doctor 取。

## 3. 标准流程

### 会话开始(每台机器,每次)
```
git pull --ff-only        # 1. 拿最新意图
aletheia doctor           # 2. 拿真实事实(只读)
# 3. 再读 CLAUDE.md / CURRENT_TASK.md(计划) / AI_HANDOFF.md(变更史)
```
pull 拿意图,doctor 拿事实,两者天然分离,再不会打架。

### 切换机器前(收尾)
```
git status                # 确认无未提交改动(doctor 的 code.sync 也会提示 dirty)
git push                  # 推送意图;事实留在本地,不带走
```
若 `doctor` 的 `code.sync` 显示 `N unpushed` 或 `dirty worktree`,说明这台机器
有意图还没同步出去——push / commit 后再切。

### 日常维护(减少人工)
```
aletheia doctor           # 看真相
aletheia doctor --fix     # 保守修复:清孤儿进程 + 起 gateway(永不动 kill_switch / 交易态)
aletheia doctor --fetch   # 先 git fetch,让 code.sync 反映 live origin 而非上次 fetch
```
`--fix` 刻意保守:它只修基础设施(进程 / 端口 / 启动),**绝不**自动清
kill_switch、**绝不**碰交易状态——那些是红线,留人决定(doctor 会打印提示)。

## 4. 开发节奏(2026-06-16 更新)

边开发边观察。Phase 0 闭环健康指标(fills 累积、position close、knowledge_items
增长)作为**持续监测的趋势**——不设隔离观察窗口,不因观察未完成阻塞后续能力线。
`code_drift` 自愈重启是预期行为(代表代码改动后自动换上新码),不是失败信号。

唯一硬阻塞线:**Phase 6 (Live)** 需要用户显式授权——这是 trust 边界,与开发
节奏无关。其余 Phase 和 Track B 可并行推进,改动不触 `execution/`/凭证/live
即可自主合并。

## 5. doctor 探测维度速查

| 维度 | 回答的问题 |
|---|---|
| `code.sync` | 这台机器领先/落后 origin 几个 commit?有没有未提交改动? |
| `process.gateway` | gateway 真在跑吗?是不是在跑旧码(code drift)? |
| `config.*` | permission_mode / live.state / exchange.mode / kill_switch 现值 |
| `creds.*` | 哪些凭证 SET、哪些 MISSING(什么卡住了 demo/live) |
| `data.market` | 最新行情快照多久前——feed 还在更新吗? |
| `loop.*` | orders/fills/positions/opportunities 计数 + 最新 opportunity 年龄 |
| `health.rollup` | compute_health() 综合信号(与自愈层同源) |
