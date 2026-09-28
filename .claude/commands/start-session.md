请开始一个新的 AI 开发会话。

必须执行：

1. 读取 `AGENTS.md`（`CLAUDE.md` 只是 `@AGENTS.md` 指针，规则只有一份）
2. 读取 `docs/README.md`
3. 从外部编排器恢复当前任务、worktree 和交接状态
4. 按当前任务只读取 `docs/README.md` 指向的最窄文档路线
5. 仅在需要历史背景时读取 `docs/archive/` 中的具体证据
6. 检查 `git status`
7. 用当前代码、测试和运行事实总结项目状态
8. 等待或执行用户给出的下一步任务

不要在未理解上下文前直接修改代码。
