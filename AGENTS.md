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

### Convergence over compatibility (standing goal)

One capability keeps exactly one live implementation. Backward compatibility is
not a goal here and is the root cause of the dual mainlines this MVP is being
hardened out of. When you find two implementations of one behaviour, converge
them and **delete** the loser — do not add a bridge, a shim, an alias mirror, or
a "soft deprecation".

- `additive-only` constrains the **data layer** (migrations must not rewrite
  history). It does not license a compatibility layer in the **code** layer.
  A legacy read/write alias is not additive; it is a second authority.
- A compatibility shim is a stopgap that must ship with the deletion that
  retires it, or an Issue that owns the deletion. Never leave one as the end
  state. If you add one to stop active bleeding, say so in the commit and name
  the follow-up.
- Retired mechanisms are deleted from code and moved to
  [`docs/design/history/`](docs/design/history/), never left "in case".
- Two implementations cannot be identified by keyword search. Read both and
  prove they answer the same question before converging; two functions with
  similar names may answer different questions (and two with different names may
  be the same mainline).
- Before judging a deletion to be a regression, read
  [`docs/design/contracts/`](docs/design/contracts/) first — the calling code
  alone will tell you the opposite of the truth. See
  [`DEBUG_AUDIT_PLAYBOOK.md`](docs/governance/protocols/DEBUG_AUDIT_PLAYBOOK.md)
  SOP-14.

## Environment and verification boundaries

Use `uv run` for Python. macOS is development/control only; do not infer Fedora
runtime state or start a local Gateway unless explicitly requested. Fedora is the
operational verification target. Windows is not a validation fallback.

Remote GitHub CI is manual-only. Never dispatch, start, or rerun it unless the
owner explicitly requests remote CI in the current task; release, deployment,
merge, or broad-verification intent does not imply that permission. Use focused
local tests by default. `scripts/ci_plan.py`, `scripts/ci_preflight.py`, and
`scripts/ci_local.py` are local validators and do not authorize a GitHub workflow.
If owner-requested CI is pending, inspect it once and monitor non-blockingly.

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
| D1/D2 or high-risk D0 semantic audit (optional shadow workflow) | [@SDD_SHADOW_WORKFLOW.md](docs/operations/development/agent-guides/SDD_SHADOW_WORKFLOW.md) |

Do not bulk-read unrelated guides. Preserve the boundaries and non-goals above.
