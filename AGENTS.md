# Aletheia Agent Guide

Repository-wide rules. Read this first, then every matched guide below and the `AGENTS.md`
of each directory you touch; nested guides add local conventions and commands but never expand
the decision, safety, or product-truth boundaries here.

## Role, priority, and invariants

Aletheia is a local-first AI trading system. The product boundary and the current
qualification route come from [Capability Ground Truth](docs/product/CAPABILITY_GROUND_TRUTH.md)
and [Active Runtime Scope](docs/product/ACTIVE_RUNTIME_SCOPE.md), not from available
adapters, old diagrams, another repository, or a closed Issue. This MVP is being
hardened, not expanded. Priority order: `State consistency > Safety > Auditability >
Semantic honesty > Tests > Capability > Agent autonomy`.

### Human decision sovereignty

Before changing code, classify each change by semantic nature:

- **D0 = restore truth (conformance repair)**: an existing Contract / invariant already
  defines the target behavior and current code, tests, docs, or runtime facts do not
  conform. The repair changes implementation to the approved semantics. `Contract Delta` is `NONE`.
- **D1 = change semantics**: target behavior or meaning changes; long-lived structure and ownership stay.
- **D2 = change structure**: state ownership, mainline, subsystem topology, authority, or product scope changes.

### Bounded engineering delegation

- The owner selects the outcome, priorities, non-goals, business acceptance, and any new
  material D1/D2. An explicit owner request or accepted Change Contract authorizes work in
  that scope; the AI records it in the task Issue instead of demanding an engineering spec.
- Within scope the AI owns investigation, evidence-backed root cause, impact and failure-mode
  analysis, derived acceptance checks, implementation choices, focused tests, self-review and
  PR preparation. Choices that preserve approved semantics, state ownership and trust
  boundaries need no repeated approval.
- Derive acceptance from the requested outcome and current Contracts; record material
  assumptions and what is not verified. Never reduce business acceptance, redefine
  completion, or weaken a Contract/test to make a failing implementation look correct. A
  root-cause hypothesis is not a fact until current evidence supports it.
- Approval is per material semantic/structural change, not per commit. D0 repairs and
  approved D1/D2 work continue without routine handoffs; for a multi-part task the AI picks
  dependency order and continues independent in-scope work, but never selects unrelated
  backlog or reopens a closed program on its own.
- Escalate only the affected path, for: product/acceptance ambiguity evidence cannot resolve,
  a new unapproved D1/D2, changed risk/economic policy, Live/capital/credential/tool authority,
  an irreversible/destructive migration, contradictory authorities, or verification that
  cannot be done safely. Return facts, uncertainty, one recommended decision and the evidence
  needed; keep doing other authorized independent work.
- Delegation is not permission to merge, release, deploy, restart a shared runtime, change
  secrets, reset qualification windows, or enable Live. Without explicit operational
  authority, stop at a reviewable PR and report the remaining approval or proof.
- An Issue, its labels/comments/closure, merged PRs and green tests grant no extra scope or
  runtime authority; other repositories are evidence only; one-shot mandates are not standing.

### Safety invariants

- Orders reach exchanges only through the backend Execution Gateway; Risk approval always precedes execution.
- Live trading is off by default. Agents cannot expand tool, credential, Risk, Gateway, or live-unlock authority.
- Never read, reveal, log, prompt, or commit credentials. Never invent healthy UI or runtime state; use `not available` or `not configured`.
- Keep changes and verification proportional: one root cause, the smallest focused regression, fail-closed at trust boundaries.
- Runtime/economic evidence is **claim-driven, not template-driven**. A `Runtime evidence` field
  records status; it creates no deployment, Observation/qualification-window, or experiment
  obligation. A D0 repair defaults to reproduction plus a focused deterministic regression
  unless the claim inherently depends on deployed runtime or economics. Use an explicit
  `Not applicable` reason when repository proof suffices; never deploy, restart, open/reset a
  window, or run an experiment just to fill a template field.

### Read the authority that answers the question

The authority map is [documentation governance](docs/governance/policies/documentation_governance.md#authority-and-conflict-resolution):
admission from Runtime Scope, capability promises from Capability Ground Truth, approved
behavior from the relevant Contract, implementation and deployment claims from code/tests and
exact-runtime evidence. READMEs and overviews route readers but override nothing; a target,
Issue status, test count, or historical observation is not proof of current capability.
Before concluding code is wrong — especially that a deletion is a regression — read the
contract, not the call sites:

`docs/design/contracts/` (approved behavior, authoritative over calling code), then
`docs/governance/decisions/DECISIONS.md` and `docs/design/history/` (retired mechanisms).

Finding the code a task owns has exactly one code map:
[`docs/design/architecture/SYSTEM_MAP.md`](docs/design/architecture/SYSTEM_MAP.md) (task → owner
→ source → focused tests, checked by `scripts/ci/check_docs.py`). Other documents, including
nested `AGENTS.md` files, link to it instead of copying the tree, task routing, or job list.

### Convergence over compatibility (standing goal)

One capability keeps exactly one live implementation; backward compatibility is not a goal
and is the root cause of the dual mainlines being hardened out. Converge two implementations
of one behaviour and **delete** the loser — no bridge, shim, alias mirror, or "soft deprecation".

- `additive-only` constrains the **data layer** (migrations never rewrite history), not the
  code layer; a legacy read/write alias is a second authority, not an addition.
- An applied migration's ID and definition are frozen ([data_model.md §6](docs/design/data/data_model.md));
  a database that applied one fails closed against code that no longer knows it. Deleting a
  migration any deployed runtime applied is a breaking cutover, not convergence.
- A compatibility shim ships with the deletion that retires it or an Issue that owns it, and is
  never the end state; if you add one to stop active bleeding, say so and name the follow-up.
- Retired mechanisms move to [`docs/design/history/`](docs/design/history/). Keyword search cannot
  identify duplicates: similar names may answer different questions and different names may be
  one mainline; read both ([`DEBUG_AUDIT_PLAYBOOK.md`](docs/governance/protocols/DEBUG_AUDIT_PLAYBOOK.md) SOP-14).

### Durable checkpoints and version control

Commit each coherent, verified batch before the next; an uncommitted checkpoint says `UNCOMMITTED`.
Commit, PR, merge, deployment, and runtime proof are separate claims. Use `jj` ("commit" = `jj
describe` + `jj new`) and isolated workspaces under `.claude/worktrees/`; raw `git` only where the
surface is git by construction. Recipes: [Development Workflow](docs/operations/development/agent-guides/DEVELOPMENT_WORKFLOW.md#durable-checkpoints).

## Environment and verification boundaries

- Python always through `uv`. macOS is development/control only, Fedora the verification target,
  Windows never a fallback; do not infer Fedora state or start a local Gateway unless asked.
- Run pytest on CASI's WSL, not the Mac or Fedora: the `casi-pytest` skill (sandboxed; see Development Workflow). No full suite on macOS, never two at once (shared data dir). Remote CI is retired: never start,
  wait for, or cite it. Every change starts with `uv run python scripts/ci/dev_check.py`, which
  never replaces the PR-level gates (`ci_plan.py`, `ci_preflight.py`, `ci_local.py`, `check_docs.py`).
- Before opening/resetting an Observation window after a merged change, deploy that exact commit
  to Fedora, restart when required, and verify runtime commit, schema, provider, settlement health.
- Orca-owned Mac-to-Windows work uses Orca Remote Runtime ([`ORCA_CLI_WORKFLOW.md`](docs/operations/development/ORCA_CLI_WORKFLOW.md),
  after `orca skills get orca-cli`); pairing codes are credentials; SSH is bootstrap/recovery/OS diagnosis only.

## Where the stages live

Four wheel roots: `core/aletheia` (importable as `aletheia`), `extensions`, `ops`, `legacy`.
**Core stays provider-agnostic**: venues, data providers, and model vendors
live in `extensions/` and are injected; core never imports a specific exchange module.
Runtime state lives in `~/.aletheia` or an absolute `ALETHEIA_DATA_DIR` (`core/aletheia/runtime_paths.py`).

Request path, under `core/aletheia/`: `main.py` → `cli/` (one module per command group),
`server/` (`app.py`, one route module per group), `gateway/` (lifecycle) → `universe/`
(Candidates) → `trading/` (entry strategy, Serenity) → `agent_shell/`, `agent_runtime/`,
`agents/` (agent loop) → `risk/` → `execution/` (Gateway) → `uta/` → `portfolio/`, `analytics/`,
`economics/` (outcomes), persisted by `storage/`. Wheel roots are declared in
`[tool.hatch.build.targets.wheel]`. Directories with their own `AGENTS.md`: `frontend/`,
`tests/`, `scripts/ci/`, `extensions/`, `core/aletheia/{execution,risk,storage}/`.

## Tool entry files and matched guides

`CLAUDE.md` (root and nested) only imports the adjacent `AGENTS.md`; Codex reads `AGENTS.md` natively. Fix rules there.

| Work area | Required guide |
| --- | --- |
| Trading, live, risk, execution, credentials, permissions, audit | [@SAFETY_AND_TRUST.md](docs/operations/development/agent-guides/SAFETY_AND_TRUST.md) |
| SQLite, runtime files, market data, symbols, paper accounting, config, async API | [@STATE_AND_DATA.md](docs/operations/development/agent-guides/STATE_AND_DATA.md) |
| Files/tools, Orca, worktrees, tests, version control (jj), PR, CI, documentation, preview, Gateway | [@DEVELOPMENT_WORKFLOW.md](docs/operations/development/agent-guides/DEVELOPMENT_WORKFLOW.md) |
| PR review, feedback validity, lifecycle, merge readiness | [@PR_REVIEW_LIFECYCLE.md](docs/operations/development/agent-guides/PR_REVIEW_LIFECYCLE.md) |
| Bug classification, experiments, unfamiliar subsystems, authority selection | [@TASK_ROUTING.md](docs/operations/development/agent-guides/TASK_ROUTING.md) |
| D1/D2 or high-risk D0 semantic audit (optional shadow workflow) | [@SDD_SHADOW_WORKFLOW.md](docs/operations/development/agent-guides/SDD_SHADOW_WORKFLOW.md) |

Do not bulk-read unrelated guides.
