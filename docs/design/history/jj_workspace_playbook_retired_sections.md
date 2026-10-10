# jj workspace playbook — retired sections

> **History (moved 2026-10-10).** Retired or superseded operations text kept as evidence; not current authority. See [design history](README.md).

Moved verbatim from `GIT_WORKTREE_PLAYBOOK.md` (now
[`JJ_WORKSPACE_PLAYBOOK.md`](../../operations/development/JJ_WORKSPACE_PLAYBOOK.md)) when it was
rewritten. These were defences from the 2026-06 period when many writers raced on the local
`master` checkout; with jj workspaces, pushed bookmarks and owner `gh pr merge --rebase`, nothing
writes local `master` directly.

> 沉淀自 2026-06-28 一次惨痛合并：本项目 git 工作区长期高强度竞争，合并因并发抢占 +
> 后台工具故障反复失败。

### 1. 为什么总在竞争

多写者同抢 master：多 AI 会话、后台 job、systemd user service 生命周期、Gateway
lifecycle。实测 master HEAD 数十秒连跳多值，commit 被冲成悬空，Worktree 分支被 reset。

- 让 master 长期无人争抢，只在确认并发已停、master 稳定的窗口短暂触碰；
- 并发写者检测：`jj op log --limit 5`，操作历史里出现非本会话的操作即有并发；
- 提交前确认 master 稳定（多次读取 `jj log -r master@origin -T commit_id --no-graph` 不变且无并发）；

### 4. 合并与 push 安全步骤

1. 停止所有会写同一目标 bookmark/checkout 的并发写者；
2. 多次读取 `master@origin`，确认 stable；
3. base 落后时优先 `jj rebase -d master@origin`，在最新 master 上重放加性改动；
4. commit 前只保留本任务文件：`jj commit <paths> -m ...` 只提交指定路径，其余留在 `@`；
5. commit 后立即并隔几秒用 `jj log -r @-` / `jj op log` 复读，确认未被抢占；
6. `jj git push --bookmark <name>` 后用 `jj git fetch` + `jj bookmark list -a <name>` 核对远端（`--dry-run` 可预览推送）；

### 5. 后台工具故障应对

回执可能被伪造、写被吞、命令被吞、输出串扰、Python 间歇 not found。应对：

- 不信单次工具回执，事后用 VCS 真相（`jj log`/`jj op log`/`jj bookmark list -a`/returncode）复核；
- marker 自验证：每条命令前后 echo 新随机 token，只信 token 之间的输出；
- 单行优于多行；需要时在一个进程中完成写入、核实、commit；
- 反复异常则停止当前写者并重启会话/进程，不要继续在不可信状态下合并。

### 7. 一句话

用 Worktree 隔离写操作，用 Preview 隔离开发运行，用 stable owner 隔离长期 Runtime；只在并发已停、
master 稳定、owner 明确批准的窗口触碰主线和 Qualification deployment。
