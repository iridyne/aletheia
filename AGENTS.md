# AGENTS.md - Aletheia Trading OS Agent Rules

## Project State

Aletheia Trading OS is a local-first, file-based, AI-native trading control system.
It is not ready for unconstrained feature expansion. Treat the current codebase as
a working MVP that needs trust hardening, not as a blank canvas.

The main design reference is:

- `docs/aletheia_trading_os_implementation_guide.md`

The implementation guide is a target and constraint document, not permission to
implement every future idea in it. Prefer closing gaps in the current MVP over
adding new capabilities.

## Core Operating Rule

Before changing code, state what boundary you are touching and what you will not
change. If the task does not require code changes, do not change code.

Inaction, analysis, or asking for clarification is a valid successful outcome when
the requested change would expand scope, weaken safety, or make system state less
trustworthy.

## No Uncontrolled Expansion

Do not add any of the following unless the user explicitly asks for that exact
capability in the current turn:

- New exchanges, markets, symbols, asset classes, or brokers.
- New live trading modes beyond the existing gated live-limited path.
- Docker, Postgres, Redis, queues, remote deployment, or multi-user auth.
- Vector search, web ingestion, YouTube ingestion, news ingestion, or knowledge
  graph features.
- New autonomous agent behaviors that create orders, unlock live mode, mutate
  credentials, or change hard risk limits.
- Large refactors, framework swaps, styling rewrites, or broad cleanup passes.

If the user asks for a fix, implement the fix. Do not opportunistically build a
larger subsystem around it.

## Trust Boundaries

These modules are change-controlled. Touch them only for a task that explicitly
requires it, and explain the exact invariant being protected:

- `aletheia/risk/`
- `aletheia/execution/`
- `aletheia/exchanges/bitget/auth.py`
- `aletheia/exchanges/bitget/client.py`
- `aletheia/config/secrets.py`
- `aletheia/agent_runtime/permissions.py`
- `aletheia/server/routes/live.py`

For these files, every behavioral change must have a focused regression test.

## Trading State Invariants

Preserve these invariants at all times:

- Every order must flow through the backend Execution Gateway.
- Never allow direct `agent -> exchange`, `dashboard -> exchange`, or
  `telegram -> exchange` order placement.
- Live trading starts disabled and must remain blocked unless explicitly unlocked
  by a human-controlled route or CLI command.
- Risk Engine approval is required before any order is executed.
- Proposal, risk decision, order, fill, and position records must keep their
  original causal IDs. Do not duplicate rows to simulate state transitions.
- Audit logs must describe the real actor, source, entity type, entity ID, and
  state change.
- API keys and secrets must never be returned by GET endpoints, written to logs,
  inserted into Markdown, or passed into agent prompts.

## UI Honesty

The dashboard must never display a reassuring state that is not backed by a real
backend value.

Examples:

- Do not hardcode risk state as "within limits".
- Do not show Daily Reviews unless backend data has `item_type="daily_review"`.
- Do not show live readiness unless live state, permission mode, and risk status
  all support it.
- Do not create UI tabs for capabilities that only exist in the guide and not in
  the implementation.

When in doubt, show "not available" or "not configured" rather than inventing a
positive status.

## Knowledge Base Rules

Knowledge items are part of the system's memory and must be semantically honest.

- Research notes must use `item_type="research_note"`.
- Strategy memory must use `item_type="strategy_memory"`.
- Dreaming logs must use `item_type="dreaming_log"`.
- Daily reviews must use `item_type="daily_review"`.
- Post-trade reviews must use `item_type="post_trade_review"`.

Do not store one type as another just to make a page populate.

Hard deletion of knowledge is forbidden. Archive or deprecate instead.

## Agent Autonomy Limits

Agents may create research, opportunities, paper proposals, strategy drafts,
reviews, and logs inside the current permission mode.

Agents must not:

- Unlock live trading.
- Place live orders directly.
- Change hard risk limits.
- Modify credentials or signer logic.
- Modify execution gateway semantics.
- Edit these project instructions to grant themselves more power.

Restricted tools must remain uncallable by LLM-driven agents.

## Change Discipline

For every non-trivial change:

1. Inspect the current code path before editing.
2. Add or update a focused regression test first when the behavior is safety,
   trading, audit, secrets, permission, or lifecycle related.
3. Make the smallest change that satisfies the requested behavior.
4. Run the narrow test that proves the fix.
5. Run broader verification only after focused tests pass.
6. Report what changed, what was verified, and what risk remains.

Never claim a trading, risk, live, or secret-handling fix is complete without test
evidence.

## Testing Expectations

Use these commands unless a task has a narrower justified command:

- Backend focused tests: `uv run pytest tests/<file>.py -q`
- Backend full suite: `uv run pytest -q`
- Frontend build: run in `frontend/` with `npm run build`
- Gateway smoke: `uv run aletheia gateway status`

If frontend source changes, rebuild static assets under
`aletheia/dashboard_static/` because they are part of the shipped local dashboard.

## Git And Workspace Rules

- Do not revert user changes unless the user explicitly asks.
- Do not run destructive git commands such as `git reset --hard` or
  `git checkout --` without explicit user approval.
- If the worktree is dirty, identify which changes are yours and which are
  pre-existing before summarizing.
- Do not amend commits unless explicitly asked.

## Escalation Rules

Stop and ask before proceeding if:

- A requested change would weaken a listed invariant.
- A fix requires changing multiple change-controlled modules.
- Tests reveal a broader architectural defect than the requested task.
- The implementation guide and current code conflict in a way that changes
  product behavior or trading safety.
- You cannot prove the effect of the change with local tests or inspection.

## Preferred Direction

The priority order for this project is:

1. State consistency.
2. Safety and live-trading containment.
3. Auditability.
4. Semantic honesty in the dashboard and Knowledge Base.
5. Focused tests.
6. User-facing capability.
7. Agent autonomy.

When these priorities conflict, choose the earlier item.
