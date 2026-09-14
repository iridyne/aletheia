# Aletheia Agent Guide

This is the repository-wide router. Read it first, then read every matched
guide completely; a nested `AGENTS.md` overrides this file for its scope.

## Role, priority, and invariants

Aletheia is a local-first automated trading system: market data → agents →
strategy proposal → Risk Engine → Execution Gateway → Bitget. This MVP is being
hardened, not expanded. Preserve this order of priorities:
`State consistency > Safety > Auditability > Semantic honesty > Tests >
Capability > Agent autonomy`.

### Human decision sovereignty

Before changing code, classify each change by semantic nature:

- **D0 = restore truth (conformance repair)**: an existing Contract / invariant already
  defines the target behavior. Current code, tests, docs, or runtime facts are non-conformant.
  Repair restores conformance by changing implementation/runtime behavior to the already-approved
  semantics. `Contract Delta` is `NONE`.
- **D1 = change semantics**: the target behavior or meaning changes while long-lived
  structure and ownership stay largely stable.
- **D2 = change structure**: state ownership, mainline, subsystem topology, authority,
  or product scope changes.

AI execution authority is constrained by the following:

- Owner alone defines current problem statement, root-cause acceptance, completion
  criteria, phase promotion, and any D1/D2 semantic/structure decision.
- Only the owner selects project priority and the next problem; backlog labels,
  closed Issues, merged PRs, or green tests do not authorize either or prove runtime success.
- Approval is per semantic change, not per implementation commit. Once the owner
  accepts the Change Contract, AI may execute it without repeated approval. Ordinary
  D0 repairs may proceed within approved scope while Delta is `NONE` and no ambiguity arises.
- A new Issue records evidence or a defect; it is not execution authorization for
  changed scope.
- AI may investigate, reproduce, test, review, and implement only inside an owner-approved
  issue scope.
- If investigation reaches an unapproved D1/D2, ambiguity, or scope expansion,
  the affected implementation stops and a decision packet is produced:
  facts, inference with confidence, unresolved uncertainty, one recommended decision,
  changed boundary, and required evidence.
- Ask the owner the specific decision needed. Do not substitute more agents, parallel
  implementations, or changed tests/contracts for that decision, or promote inference
  into project truth. Nested guides inherit this boundary rather than redefining it.

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

### Durable Git checkpoints

Issue comments and progress checkpoints are evidence, not durable source state.
Once one independently coherent bounded mutation batch has passed its focused
verification and self-review, commit that scoped batch before starting the next
independent mutation batch.

- An uncommitted checkpoint must say `UNCOMMITTED` explicitly and remains
  incomplete until committed or reverted. Do not accumulate several already-
  verified checkpoints in one growing dirty worktree.
- A completed-batch checkpoint references the resulting commit SHA and, after
  publication, its PR. Without a commit SHA, do not describe source convergence
  as completed, landed, or durable.
- In long-running owner-approved work, open or update a normal reviewable PR once
  a coherent slice exists; do not wait for the entire program or several unrelated
  ownership domains to accumulate. Split PRs by independently reviewable root
  cause / ownership boundary, not by session order or arbitrary file count.
- A PR may contain several tightly related commits. Do not create micro-commits or
  micro-PRs for trivial edits merely to satisfy this rule.
- A batch may remain temporarily uncommitted only when the same accepted change
  requires an atomic cross-file/package cutover to stay buildable or testable.
  Record that exact reason; this exception never carries completed unrelated work.
- Commit, PR, merge, deployment, and runtime proof remain separate claims. Follow
  the matched Development Workflow and PR Review Lifecycle guides for details.

## Environment and verification boundaries

Use `uv run` for Python. macOS is development/control only; do not infer Fedora
runtime state or start a local Gateway unless explicitly requested. Fedora is the
operational verification target. Windows is not a validation fallback.

Remote GitHub CI is retired/unavailable for the current workflow. Agents must not
start, dispatch, wait for, or claim authority from remote CI. Use focused local
deterministic validation instead (`scripts/ci_plan.py`, `scripts/ci_preflight.py`,
`scripts/ci_local.py`, and `scripts/check_docs.py`/targeted pytest), which is local-only
validation and does not grant merge/runtime authority.

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
