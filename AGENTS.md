# Aletheia Agent Guide

This is the repository-wide router. Read it first, then read every matched
guide completely; a nested `AGENTS.md` overrides this file for its scope.

## Role, priority, and invariants

Aletheia is a local-first automated trading system: market data → agents →
strategy proposal → Risk Engine → Execution Gateway → Bitget. This MVP is being
hardened, not expanded. Preserve this order of priorities:
`State consistency > Safety > Auditability > Semantic honesty > Tests >
Capability > Agent autonomy`.

Before changing code, identify SDD depth (D0 conformance repair, D1 contract
behavior change, D2 substantial design change), the changed boundary, what is
unchanged, and the Contract Delta (`NONE` for D0). Analysis does not authorize
changes; stop if an invariant cannot be proved safely.

- Orders reach exchanges only through the backend Execution Gateway; Risk approval always precedes execution.
- Live trading is off by default. Agents cannot expand tool, credential, Risk, Gateway, or live-unlock authority.
- Never read, reveal, log, prompt, or commit credentials. Never invent healthy UI or runtime state; use `not available` or `not configured`.
- Keep changes and verification proportional: one root cause and the smallest focused regression, with fail-closed handling at trust boundaries.

## Environment and verification boundaries

Use `uv run` for Python. macOS is development/control only; do not infer Fedora
runtime state or start a local Gateway unless explicitly requested. Fedora is the
operational verification target. Windows is not a validation fallback.

Use the repository-native `scripts/ci_plan.py`, `scripts/ci_preflight.py`, and
`scripts/ci_local.py` before publishing a CI-triggering PR; do not hand-select
CI depth. Prefer configured self-hosted CI, and use isolated credential-free
Fedora testing only when a complete suite cannot wait. Let ordinary Ready PRs
accumulate; release/tag work requires explicit operational need. For pending CI,
inspect checks, runner/queue state, and failing logs once, then monitor
non-blockingly at intervals up to 30 minutes.

Before opening or resetting an Observation/qualification window after a merged
behavior or evidence change, deploy the exact merged commit to Fedora, restart
when required, and verify runtime commit, schema, provider, and settlement
health. Merge, release, deployment, and runtime proof are separate claims.

For Orca-owned Mac-to-Windows work, use Orca Remote Runtime and read
[`ORCA_CLI_WORKFLOW.md`](docs/operations/development/ORCA_CLI_WORKFLOW.md)
after `orca skills get orca-cli`; pairing codes are credentials. SSH is for
bootstrap/recovery/OS diagnosis only.

## Load only when matched

| Work area | Required guide |
| --- | --- |
| Trading, live, risk, execution, credentials, permissions, audit | [@SAFETY_AND_TRUST.md](docs/operations/development/agent-guides/SAFETY_AND_TRUST.md) |
| SQLite, runtime files, market data, symbols, paper accounting, config, async API | [@STATE_AND_DATA.md](docs/operations/development/agent-guides/STATE_AND_DATA.md) |
| Files/tools, Orca, worktrees, tests, Git, PR, CI, documentation, preview, Gateway | [@DEVELOPMENT_WORKFLOW.md](docs/operations/development/agent-guides/DEVELOPMENT_WORKFLOW.md) |
| PR review, feedback validity, lifecycle, merge readiness | [@PR_REVIEW_LIFECYCLE.md](docs/operations/development/agent-guides/PR_REVIEW_LIFECYCLE.md) |
| Bug classification, experiments, unfamiliar subsystems, authority selection | [@TASK_ROUTING.md](docs/operations/development/agent-guides/TASK_ROUTING.md) |

Do not bulk-read unrelated guides. Preserve the boundaries and non-goals above.
