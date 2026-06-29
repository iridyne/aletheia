# AGENTS.md - Aletheia Trading OS Agent Rules

## Project State

Aletheia Trading OS is a working MVP that needs trust hardening, not feature expansion.
Prefer closing gaps in the current MVP over adding new capabilities.
Design reference: `docs/governance/implementation_guide.md`

## Core Operating Rule

Before changing code, state what boundary you are touching and what you will not change.
If the task does not require code changes, do not change code.
Inaction, analysis, or asking for clarification is a valid successful outcome when the
requested change would expand scope, weaken safety, or make system state less trustworthy.

## No Uncontrolled Expansion

→ See `CLAUDE.md` Development Rules. Do not add new exchanges, Docker/Postgres/Redis,
vector search, web ingestion, or autonomous agent behaviors that create orders or unlock live.
If the user asks for a fix, implement the fix. Do not build a larger subsystem around it.

## Trust Boundaries

→ See `CLAUDE.md` Development Rules for the full list of change-controlled modules.
For these files, every behavioral change must have a focused regression test.

## Trading State Invariants

- Every order must flow through the backend Execution Gateway.
- Never allow direct `agent → exchange`, `dashboard → exchange`, or `telegram → exchange` order placement.
- Live trading starts disabled and must remain blocked unless explicitly unlocked by a human-controlled route.
- Risk Engine approval is required before any order is executed.
- Proposal, risk decision, order, fill, and position records must keep their original causal IDs.
- Audit logs must describe the real actor, source, entity type, entity ID, and state change.
- API keys and secrets must never be returned by GET endpoints, written to logs, inserted into Markdown, or passed into agent prompts.

## UI Honesty

The dashboard must never display a reassuring state not backed by a real backend value.

- Do not hardcode risk state as "within limits".
- Do not show Daily Reviews unless backend data has `item_type="daily_review"`.
- Do not show live readiness unless live state, permission mode, and risk status all support it.
- Do not create UI tabs for capabilities that only exist in the guide, not the implementation.

When in doubt, show "not available" or "not configured" rather than inventing a positive status.

## Knowledge Base Rules

Knowledge items must be semantically honest:
`research_note` / `strategy_memory` / `dreaming_log` / `daily_review` / `post_trade_review`
— do not store one type as another to make a page populate.
Hard deletion of knowledge is forbidden. Archive or deprecate instead.

## Agent Autonomy Limits

Agents **may**: create research, opportunities, paper proposals, strategy drafts, reviews, and logs.

Agents **must not**:
- Unlock live trading or place live orders directly.
- Change hard risk limits or modify credentials / signer logic.
- Modify execution gateway semantics.
- Edit these project instructions to grant themselves more power.

Restricted tools must remain uncallable by LLM-driven agents.

## Change Discipline

1. Inspect the current code path before editing.
2. Add or update a focused regression test first for safety/trading/audit/secrets/lifecycle changes.
3. Make the smallest change that satisfies the requested behavior.
4. Run the narrow test that proves the fix.
5. Run broader verification only after focused tests pass.
6. Report what changed, what was verified, and what risk remains.

Never claim a trading, risk, live, or secret-handling fix is complete without test evidence.

## Testing Expectations

→ See `CLAUDE.md` Testing Strategy for commands.
Additional: `.\scripts\dev.ps1` runs gateway on `:8787` + `vite build --watch` during UI iteration.
Iterate against `http://127.0.0.1:8787/`; do not use `:5173` as the daily entrypoint.

## Git And Workspace Rules

- Do not revert user changes unless explicitly asked.
- Do not run destructive git commands (`git reset --hard`, `git checkout --`) without explicit approval.
- If the worktree is dirty, identify which changes are yours vs pre-existing before summarizing.
- Do not amend commits unless explicitly asked.
- **Branch isolation**: Every code change must land on its own dedicated branch, not the current open PR/branch. Before making any edit, check the current branch (`git branch --show-current`); if it already has an open PR or belongs to an unrelated task, create a new branch first. Never piggyback unrelated fixes onto an existing PR.

## Escalation Rules

Stop and ask before proceeding if:
- A requested change would weaken a listed invariant.
- A fix requires changing multiple change-controlled modules.
- Tests reveal a broader architectural defect than the requested task.
- The implementation guide and current code conflict in a way that changes product behavior or trading safety.
- You cannot prove the effect of the change with local tests or inspection.

## Preferred Direction

→ See `CLAUDE.md` Development Rules: State consistency > Safety > Auditability > Semantic honesty > Tests > Capability > Agent autonomy.


## Git Workspace / Worktree 操作细则（2026-06-28 经验沉淀）

本项目 git 工作区易处于高强度竞争:多并发会话/后台 job + 自动 git hook(post-merge 重启) + Task Scheduler 看门狗会同时抢 master, 导致 HEAD 频繁移动、commit 被冲成悬空、甚至 worktree 分支被 reset。

铁律:任何会写文件的非平凡改动,一律在独立 worktree 中进行(EnterWorktree 或 git worktree add);纯只读/查询可在原地。让 master 长期处于无人争抢的静态条件,只在确认并发已停、master 稳定的窗口内,用原子化、可复核的方式触碰 master。详见 docs/_ai/GIT_WORKTREE_PLAYBOOK.md。
