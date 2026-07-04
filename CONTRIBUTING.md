# Contributing to Aletheia Trading OS

Aletheia is an AI-native trading system. Contributions are welcome, but this is
not a normal web app: small changes can affect money movement, audit integrity,
credentials, or the operator's trust in the system. Optimize for correctness,
traceability, and semantic honesty before new capability.

## Start Here

Read these files before making a non-trivial change:

1. `README.md` for setup and the product overview.
2. `docs/README.md` for the documentation map.
3. `AGENTS.md` for project rules, safety boundaries, test expectations, and AI-agent workflow.
4. `docs/governance/trust_and_change_control.md` before touching risk, execution, live trading, secrets, or permissions.
5. `docs/design/data_model.md` and `docs/design/api_reference.md` when changing schema or API behavior.

`CLAUDE.md` and `CODEX.md` intentionally point back to `AGENTS.md`; do not
maintain separate rule copies.

## Contribution Scope

Good first contributions:

- Documentation fixes that improve onboarding without adding stale runtime facts.
- Focused tests for existing behavior.
- Small bug fixes with clear reproduction and verification.
- Read-only diagnostics and audit improvements.
- UI honesty fixes where the dashboard currently implies a state not backed by backend data.

High-sensitivity areas:

- `aletheia/risk/`
- `aletheia/execution/`
- exchange authentication and client code
- secrets/config permission handling
- live trading routes
- System Operator decision-chain files

Changes in these areas need focused regression tests and a clear explanation of
what behavior changed. When in doubt, ask before broadening scope.

Out of scope unless explicitly approved:

- New exchanges or brokers.
- Docker/Postgres/Redis/vector search migrations.
- Direct agent-to-exchange order placement.
- Any live-trading unlock automation.
- Risk-limit changes without owner approval.
- LLM calls inside read-only audit layers.

## Local Setup

Use Python through `uv` only:

```powershell
uv sync
uv run python -c "import aletheia.risk.engine"
uv run aletheia doctor
```

Start the gateway through the project CLI:

```powershell
uv run aletheia gateway start --no-browser
```

For frontend iteration:

```powershell
.\scripts\dev.ps1
```

Use `http://127.0.0.1:8787/` as the canonical dashboard entrypoint.

## Development Workflow

1. Create a dedicated branch or worktree for each task.
2. Inspect the current code path before editing.
3. Keep the change narrowly scoped.
4. Add or update a focused test for behavioral changes.
5. Run the narrow tests first.
6. Run broader verification when the change touches shared or sensitive behavior.
7. Summarize changed files, verification, and residual risk.

Recommended commands:

```powershell
uv run pytest tests/test_targeted_file.py -q
uv run pytest tests/specs/ -q
uv run aletheia doctor
```

Do not commit runtime state:

- `data/`
- `.env`
- local databases
- credentials
- generated dashboard static files
- transient diagnostic outputs

## Testing Expectations

Documentation-only changes usually need spelling/link review rather than the full
test suite.

Code changes should include a focused regression test unless the behavior is
purely mechanical. For trading, risk, execution, secrets, lifecycle, audit, or
schema changes, do not claim the fix is complete without test evidence.

When changing frontend code, build before handoff:

```powershell
cd frontend
npm run build
cd ..
```

## Documentation Rules

Keep git-tracked documents machine-independent. Write intent, design, rules, and
repeatable procedures. Do not write facts that will go stale on another machine,
such as current gateway PID, current database size, current warnings, or local
credentials.

When adding or removing:

- API endpoints: update `docs/design/api_reference.md`.
- Data tables: update `docs/design/data_model.md`.
- Data sources: update `docs/design/data_utilization.md`.
- Scheduler jobs: update `docs/design/agent_contracts.md` and related system maps.
- Config fields: update backend schema and the frontend Settings page.

## AI Agent Contributions

AI agents are contributors and must follow the same boundaries as humans, plus
the rules in `AGENTS.md`.

Minimum agent checklist:

1. State the boundary being touched before editing.
2. Use a dedicated worktree for non-trivial file writes.
3. Never read or print secrets.
4. Never enable live trading or place orders.
5. Never fabricate missing audit, thesis, or market evidence.
6. Prefer read-only diagnostics when the requested change is unclear or unsafe.
7. Report tests and limitations plainly.

For AI handoffs, update only intention and coordination documents. Do not encode
current runtime facts as if they were durable project state.

## Pull Request Checklist

Before opening or merging a PR:

- The scope is narrow and explained.
- Sensitive boundaries are called out.
- Tests are listed, or the reason for not running tests is stated.
- Docs are updated when contracts, APIs, data models, config, or workflows changed.
- No secrets or runtime artifacts are included.
- Live trading, risk, and execution invariants remain intact.
