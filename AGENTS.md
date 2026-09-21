# Aletheia Agent Guide

This is the repository-wide router. Read it first, then read every matched
guide completely. Nested guides specialize their scope without expanding the
human-decision, safety, or product-truth boundaries defined here.

## Role, priority, and invariants

Aletheia is a local-first AI trading system. The accepted product boundary and
current qualification route are defined in
[Capability Ground Truth](docs/product/CAPABILITY_GROUND_TRUTH.md) and
[Active Runtime Scope](docs/product/ACTIVE_RUNTIME_SCOPE.md), not inferred from
available adapters, old diagrams, another repository, or a closed Issue.
This MVP is being hardened, not expanded. Preserve this order of priorities:
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

### Bounded engineering delegation

- The owner selects the desired outcome, product priorities, non-goals, business
  acceptance, and any new material D1/D2 decision. An explicit owner request or
  an already accepted Change Contract authorizes work within that scope; the AI
  records it in the task Issue rather than requiring the owner to write an
  engineering specification first.
- Within that scope, the AI owns technical investigation, evidence-backed root
  cause analysis, impact and relevant failure-mode analysis, derived technical
  acceptance checks, implementation choices, focused tests, self-review, and PR
  preparation. Routine engineering choices that preserve approved semantics,
  state ownership and trust boundaries do not require repeated owner approval.
- Derive acceptance checks from the requested outcome and current Contracts;
  record material assumptions and what is not verified. The AI must not reduce
  business acceptance, redefine completion, or weaken a Contract/test merely to
  make a failing implementation appear correct. A root-cause hypothesis is not
  a fact until supported by current evidence.
- Approval is per material semantic/structural change, not per commit or
  checkpoint. D0 repairs and implementation of an already approved D1/D2 may
  continue without routine handoffs. For an authorized multi-part task, the AI
  selects dependency order and continues independent in-scope work; it must not
  select unrelated backlog work or reopen a closed program on its own.
- Escalate the affected path only for a product/acceptance ambiguity that current
  evidence cannot resolve, a new unapproved D1/D2, changed risk/economic policy,
  Live/capital/credential/tool authority, an irreversible/destructive migration,
  contradictory current authorities, or verification that cannot be performed
  safely. Return facts, uncertainty, one recommended decision and required
  evidence; continue other authorized independent work where possible.
- Development delegation is not permission to merge, release, deploy, restart a
  shared runtime, change secrets, reset qualification windows, or enable Live.
  Follow the task's explicit operational authority and the PR/release rules;
  otherwise stop at a reviewable PR and report the remaining approval or proof.
- A new Issue records the task/evidence; its creation, labels, comments, closure,
  merged PRs and green tests grant no additional scope or runtime authority.
  External references and other repositories are evidence only. Historical
  one-shot mandates do not become permanent background-development permission.

### Safety invariants

- Orders reach exchanges only through the backend Execution Gateway; Risk approval always precedes execution.
- Live trading is off by default. Agents cannot expand tool, credential, Risk, Gateway, or live-unlock authority.
- Never read, reveal, log, prompt, or commit credentials. Never invent healthy UI or runtime state; use `not available` or `not configured`.
- Keep changes and verification proportional: one root cause and the smallest focused regression, with fail-closed handling at trust boundaries.

### Read the authority that answers the question

Use [documentation governance](docs/governance/policies/documentation_governance.md#authority-and-conflict-resolution)
for the authority map. Active admission comes only from the existing Runtime
Scope; capability promises come from Capability Ground Truth; approved behavior
comes from the relevant Contract; actual implementation and deployment claims
require code/tests and exact-runtime evidence respectively. README files and
architecture overviews route readers but do not override these authorities.
Do not duplicate the manifest or convert a target, Issue status, test count, or
historical observation into proof of current capability.

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
