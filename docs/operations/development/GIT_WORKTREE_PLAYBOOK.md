# jj Workspace / Worktree 操作手册（Git 后端）

> 沉淀自 2026-06-28 一次惨痛合并：本项目 git 工作区长期高强度竞争，合并因并发抢占 +
> 后台工具故障反复失败。
>
> 跨机器角色、稳定 Runtime 所有权、Qualification 冻结与多 Preview 规则以
> [`Qualification Development Protocol`](QUALIFICATION_DEVELOPMENT_PROTOCOL.md)
> 为准；本文只负责 jj（colocated Git 后端）的 workspace 操作。Orca 管理的 worktree 仍是 Git worktree。

## 1. 为什么总在竞争

多写者同抢 master：多 AI 会话、后台 job、systemd user service 生命周期、Gateway
lifecycle。实测 master HEAD 数十秒连跳多值，commit 被冲成悬空，Worktree 分支被 reset。

Git 操作不再自动重启稳定 Gateway；PR merge 也不等于 Qualification deployment。部署新代码
后，只能由 stable Runtime owner 在明确窗口执行 `gateway restart` 或 release/update 流程。

## 2. 铁律：非平凡改动一律用 Worktree 隔离

- 任何写文件任务 → 先开隔离工作区：`jj workspace add .claude/worktrees/<name> --name <name> -r master@origin`；工作区一律放在已被 `.gitignore` 忽略的 `.claude/worktrees/` 下，不建到仓库外的兄弟目录；
- 只有 jj workspace 能跑本地门禁（`dev_check` / `check_docs` / `ci_preflight`）。Orca 或 EnterWorktree 建的是普通 Git worktree，没有自己的 `.jj`；门禁在那里会拒绝运行（`vcs.py` 校验 `jj workspace root`），否则 jj 会向上找到主 checkout，读错改动集。需要跑门禁的任务不要用 Git worktree；
- 纯只读/查询 → 可在原地；
- 让 master 长期无人争抢，只在确认并发已停、master 稳定的窗口短暂触碰；
- 每个 Agent Prompt 必须写明 goal、allowed paths、excluded paths、acceptance checks；
- Worktree 可以启动自己的 `aletheia dev api --port 879x` 和 Vite Preview，但不得控制稳定
  Gateway。

## 3. Worktree 诀窍

- `data/` 为空陷阱：不要在 Worktree 里 restart Gateway，交易系统会连接错误/空状态；
- 稳定 Runtime 使用 primary data root；Preview 自动使用
  `~/.aletheia/instances/dev-preview/<checkout-id>/<port>`；
- 同一主机并行 Preview 时，每个 API/Vite 进程使用唯一端口；
- 共享 hooks：jj 不运行 Git hooks；任何 machine-level hook 必须有 stable owner/main-worktree 守卫；
- `jj workspace list` 查看所有工作区；每个工作区有独立的 `<name>@` 工作副本提交，彼此不共享未提交改动；
- 并发写者检测：`jj op log --limit 5`，操作历史里出现非本会话的操作即有并发；
- 提交前确认 master 稳定（多次读取 `jj log -r master@origin -T commit_id --no-graph` 不变且无并发）；
- 不要从 Worktree 执行 `gateway start|stop|restart|update`；前端 HMR 不需要重启 Gateway。

## 4. 合并与 push 安全步骤

1. 停止所有会写同一目标 bookmark/checkout 的并发写者；
2. 多次读取 `master@origin`，确认 stable；
3. base 落后时优先 `jj rebase -d master@origin`，在最新 master 上重放加性改动；
4. commit 前只保留本任务文件：`jj commit <paths> -m ...` 只提交指定路径，其余留在 `@`；
5. commit 后立即并隔几秒用 `jj log -r @-` / `jj op log` 复读，确认未被抢占；
6. `jj git push --bookmark <name>` 后用 `jj git fetch` + `jj bookmark list -a <name>` 核对远端（`--dry-run` 可预览推送）；
7. 通过 PR 合并：owner 人工 `gh pr merge <n> --rebase`（无远程 CI gate、无自动合并）；不得把合并动作附带成 qualification host 自动部署。

## 5. 后台工具故障应对

回执可能被伪造、写被吞、命令被吞、输出串扰、Python 间歇 not found。应对：

- 不信单次工具回执，事后用 VCS 真相（`jj log`/`jj op log`/`jj bookmark list -a`/returncode）复核；
- marker 自验证：每条命令前后 echo 新随机 token，只信 token 之间的输出；
- 单行优于多行；需要时在一个进程中完成写入、核实、commit；
- 反复异常则停止当前写者并重启会话/进程，不要继续在不可信状态下合并。

## 6. 收尾

- 合并后清理已并入 master 且不再需要的工作区（`jj workspace forget <name>` 并删除目录）和本地 bookmark；
- 停止该 Worktree 的 Preview API/Vite 前台进程；
- Preview state 仅在确认无需复现证据后删除；
- 不删除 stable Runtime 的 `~/.aletheia`；
- Runtime 当前状态重新用 `doctor` 检查，不写入 Git 作为跨机器真相。

## 7. 一句话

用 Worktree 隔离写操作，用 Preview 隔离开发运行，用 stable owner 隔离长期 Runtime；只在并发已停、
master 稳定、owner 明确批准的窗口触碰主线和 Qualification deployment。
