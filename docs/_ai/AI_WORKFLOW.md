# AI Workflow

## Quick Reference

1. **Start session**: /start-session (Claude Code) or read AGENTS.md
2. **End session**: /end-session (Claude Code) or manually update handoff docs
3. **Cross-device**: Push branch, ensure AI_HANDOFF.md is current, pull on new device
4. **Secrets**: Never in git. Use OS keyring or encrypted file (.secret.key, secrets.enc are gitignored).
5. **New project**: Run ~/ai-project-template/scripts/ai-workspace-init.ps1

## Context Recovery Protocol

1. Read CLAUDE.md
2. Read AGENTS.md
3. Read docs/_ai/PROJECT_CONTEXT.md
4. Read docs/_ai/CURRENT_TASK.md
5. Read docs/_ai/AI_HANDOFF.md
6. Read docs/_ai/DECISIONS.md
7. Inspect git status
8. Summarize current state before making changes

## End-of-Session Protocol

1. Update docs/_ai/AI_HANDOFF.md
2. Update docs/_ai/CURRENT_TASK.md
3. Append docs/_ai/DEVLOG.md
4. Append docs/_ai/DECISIONS.md if architectural decisions were made
5. Summarize changed files
6. Identify next recommended action

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
