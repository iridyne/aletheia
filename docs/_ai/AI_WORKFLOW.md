# AI Workflow

## Quick Reference

1. **Start session**: run `uv run aletheia agent workflow bootstrap`, then read AGENTS.md and the selected task handoff. `Evidence: historical` is context, not permission to continue the completed task.
2. **End session**: run `task verify` for an approved command, update the selected `.agent/tasks/` packet and structured handoff, then run `task complete` and `workflow check --strict`
3. **Cross-device**: Push branch, ensure AI_HANDOFF.md is current, pull on new device
4. **Secrets**: Never in git. Use OS keyring or encrypted file (.secret.key, secrets.enc are gitignored).
5. **New project**: Run ~/ai-project-template/scripts/ai-workspace-init.ps1

## Context Recovery Protocol

1. Run `uv run aletheia agent workflow bootstrap`
2. Read AGENTS.md and the selected `.agent/tasks/` + `.agent/handoffs/` pair
3. Run `uv run aletheia doctor` for local runtime facts
4. Read relevant decisions and detailed history only when the task requires them
5. Inspect git status and summarize current state before making changes

## End-of-Session Protocol

1. Commit the implementation so verification runs against a stable `HEAD`.
2. Run `uv run aletheia agent workflow task verify TASK-ID --command-key KEY`.
3. Update the task's structured `.agent/handoffs/TASK-ID.md`.
4. Run `uv run aletheia agent workflow task complete TASK-ID` when no work remains.
5. Run `uv run aletheia agent workflow check --strict` and commit the task/handoff evidence.
6. Append detailed history to `AI_HANDOFF.md` / `DEVLOG.md`, and decisions to `DECISIONS.md`, only when useful.

## SDD Development Cycle（非 trivial 功能必须遵循）

参考: [SDD_METHODOLOGY](../design/SDD_METHODOLOGY.md)

### 快速流程

```
1. Constitution  确认 CLAUDE.md/AGENTS.md 约束仍适用
2. Specify       找到或创建对应 L2 契约(docs/design/)
3. Clarify       跑消歧检查清单(错误路径/空态/边界/并发/幂等/安全/可观测)
4. Plan          写 docs/plans/ 或 CURRENT_TASK.md
5. Tasks         拆成 1-4h 原子任务
6. Implement     写 spec_claim(status="missing") → RED → 实现 → GREEN → commit
```

### Spec-Code 同步规则

- **改动先改 spec**: 发现需要改行为 → 先更新 `*_contract.md` → 再改代码+spec test
- **Drift = Issue**: `uv run pytest tests/specs/ -q` 失败 → 记 BACKEND_ISSUES.md
- **Checkpoint**: commit 前 `uv run pytest tests/specs/ tests/test_architecture_fitness.py -q` 必须全绿
