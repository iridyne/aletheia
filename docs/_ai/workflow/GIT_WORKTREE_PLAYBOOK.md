# Git Workspace / Worktree 操作手册

> 沉淀自 2026-06-28 一次惨痛合并：本项目 git 工作区长期高强度竞争，合并因并发抢占 +
> 后台工具故障反复失败。
>
> 跨机器角色、稳定 Runtime 所有权、Qualification 冻结与多 Preview 规则以
> `DEV_PROTOCOL.md` 为准；本文只负责 Git/Worktree 操作。

## 1. 为什么总在竞争

多写者同抢 master：多 AI 会话、后台 job、Task Scheduler/launchd 生命周期、Gateway
lifecycle。实测 master HEAD 数十秒连跳多值，commit 被冲成悬空，Worktree 分支被 reset。

Git 操作不再自动重启稳定 Gateway；PR merge 也不等于 Qualification deployment。部署新代码
后，只能由 stable Runtime owner 在明确窗口执行 `gateway restart` 或 release/update 流程。

## 2. 铁律：非平凡改动一律用 Worktree 隔离

- 任何写文件任务 → 先开 Worktree（EnterWorktree 或 `git worktree add`）；
- 纯只读/查询 → 可在原地；
- 让 master 长期无人争抢，只在确认并发已停、master 稳定的窗口短暂触碰；
- 每个 Agent Prompt 必须写明 goal、allowed paths、excluded paths、acceptance checks；
- Worktree 可以启动自己的 `aletheia dev api --port 879x` 和 Vite Preview，但不得控制稳定
  Gateway。

## 3. Worktree 诀窍

- `data/` 为空陷阱：不要在 Worktree 里 restart Gateway，交易系统会连接错误/空状态；
- 稳定 Runtime 使用 primary data root；Preview 自动使用
  `~/.aletheia/instances/dev-preview/<checkout-id>/<port>`；
- 同一 Windows 主机并行 Preview 时，每个 API/Vite 进程使用唯一端口；
- 共享 hooks：任何 machine-level hook 必须有 stable owner/main-worktree 守卫；
- `.git` 是文件而非目录；stale lock 位于主仓
  `.git/worktrees/<name>/index.lock`；
- 提交前确认 master 稳定（多次读取 `git rev-parse master` 不变且无并发）；
- 不要从 Worktree 执行 `gateway start|stop|restart|update`；前端 HMR 不需要重启 Gateway。

## 4. 合并与 push 安全步骤

1. 停止所有会写同一目标分支/checkout 的并发写者；
2. 多次读取 master，确认 stable；
3. base 落后时优先在最新 master 上重放加性改动；
4. commit 前只 stage 本任务文件，保留无关修改；
5. commit 后立即并隔几秒复读 HEAD，确认未被抢占；
6. push 后用 `git fetch` + `git ls-remote origin refs/heads/<branch>` 直接核对远端；
7. 通过正常 PR/CI 合并；不得把合并动作附带成 qualification host 自动部署。

## 5. 后台工具故障应对

回执可能被伪造、写被吞、命令被吞、输出串扰、Python 间歇 not found。应对：

- 不信单次工具回执，事后用 Git 真相（hash-object/rev-parse/ls-remote/returncode）复核；
- marker 自验证：每条命令前后 echo 新随机 token，只信 token 之间的输出；
- 单行优于多行；需要时在一个进程中完成写入、核实、stage、commit；
- 反复异常则停止当前写者并重启会话/进程，不要继续在不可信状态下合并。

## 6. 收尾

- 合并后清理已并入 master 且不再需要的 Worktree 和本地分支；
- 停止该 Worktree 的 Preview API/Vite 前台进程；
- Preview state 仅在确认无需复现证据后删除；
- 不删除 stable Runtime 的 `~/.aletheia`；
- Runtime 当前状态重新用 `doctor` 检查，不写入 Git 作为跨机器真相。

## 7. 一句话

用 Worktree 隔离写操作，用 Preview 隔离开发运行，用 stable owner 隔离长期 Runtime；只在并发已停、
master 稳定、owner 明确批准的窗口触碰主线和 Qualification deployment。
