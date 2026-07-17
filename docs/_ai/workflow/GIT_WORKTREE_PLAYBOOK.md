# Git Workspace / Worktree 操作手册

> 沉淀自 2026-06-28 一次惨痛合并:本项目 git 工作区长期高强度竞争,合并因并发抢占 + 后台工具故障反复失败。

## 1. 为什么总在竞争
多写者同抢 master:多 AI 会话/后台 job;Task Scheduler/launchd 生命周期;gateway lifecycle。实测 master HEAD 数十秒连跳多值, commit 被冲成悬空, worktree 分支被 reset。Git 操作不再自动重启 gateway；部署新代码后由 owner 显式执行 `gateway restart`。

## 2. 铁律:非平凡改动一律用 Worktree 隔离
- 任何写文件任务 → 先开 worktree(EnterWorktree 或 git worktree add)。
- 纯只读/查询 → 可在原地。
- 让 master 长期无人争抢, 只在确认并发已停 + master 稳定的窗口短暂触碰。

## 3. Worktree 诀窍
- data/ 为空陷阱:别在 worktree 里 restart gateway(交易系统空跑)。
- 共享 hooks:自动重启 hook 需主工作树守卫。
- .git 是文件非目录:stale lock 在 主仓/.git/worktrees/<name>/index.lock。
- 提交前确认 master 稳定(多次读 git rev-parse master 不变 + 无并发)。

## 4. 合并/push 安全步骤
1. 先停所有并发写者。
2. 多次读 master 确认稳定。
3. base 落后时优先在最新 master 上重放加性改动(新文件直接写、已存在文件锚点插入)。
4. commit 后立即 + 隔几秒复读 HEAD 确认没被冲。
5. push 后用 git fetch + git ls-remote origin refs/heads/master 直接查服务器确认。

## 5. 后台 job 工具故障应对
回执会被伪造、写被吞、命令被吞、输出串扰、python 间歇 not found。应对:
- 不信工具回执, 事后用 git 真相(hash-object/rev-parse/ls-remote/returncode)复核。
- marker 自验证(每条命令前后 echo 全新随机 token, 只信夹在 token 间的输出)。
- 单行优于多行; 进程内原子化(写+核实+git add+commit 全塞进一个 uv run python, 让文件立即 tracked 免被清; 文件内容用 cat heredoc 字面写避免转义)。
- 反复异常 → 重启会话/进程。

## 6. 一句话
多用 worktree 把写操作隔离, 让 master 长期处于无人争抢的静态条件; 只在并发已停、master 稳定的窗口用原子化可复核方式触碰 master。
