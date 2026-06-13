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
3. Read docs/PROJECT_CONTEXT.md
4. Read docs/CURRENT_TASK.md
5. Read docs/AI_HANDOFF.md
6. Read docs/DECISIONS.md
7. Inspect git status
8. Summarize current state before making changes

## End-of-Session Protocol

1. Update docs/AI_HANDOFF.md
2. Update docs/CURRENT_TASK.md
3. Append docs/DEVLOG.md
4. Append docs/DECISIONS.md if architectural decisions were made
5. Summarize changed files
6. Identify next recommended action
