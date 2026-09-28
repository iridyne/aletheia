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

Before concluding that code is wrong — especially before judging a deletion to be
a regression — read the contract, not the call sites:

- `docs/design/contracts/` — approved behavior (authoritative over calling code).
- `docs/product/ACTIVE_RUNTIME_SCOPE.md` — what is actually admitted at runtime.
- `docs/product/CAPABILITY_GROUND_TRUTH.md` — capability promises and their evidence limits.
- `docs/governance/decisions/DECISIONS.md` — decision record.
- `docs/design/history/` — retired mechanisms (deleted from code, kept as history).

Finding the code a task owns is a separate question, and it has exactly one code
map: [`docs/design/architecture/SYSTEM_MAP.md`](docs/design/architecture/SYSTEM_MAP.md)
(task → owner → source → focused tests, checked by
`scripts/ci/check_docs.py`). Other documents link to it rather than keeping a second
copy of the directory tree, the task routing, or the scheduled-task list.

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

Full-suite and concurrency limits:

- The full suite on macOS is out of scope; run focused tests proportional to the
  change. Fedora is where the full suite is measured.
- Never run two full pytest suites concurrently — they share the data directory
  and crash each other, and the crash reads as a test failure.

Remote GitHub CI is retired/unavailable for the current workflow. Agents must not
start, dispatch, wait for, or claim authority from remote CI. Use focused local
deterministic validation instead (`scripts/ci/ci_plan.py`, `scripts/ci/ci_preflight.py`,
`scripts/ci/ci_local.py`, and `scripts/ci/check_docs.py`/targeted pytest), which is local-only
validation and does not grant merge/runtime authority.

Start every routine change with the changed-file gate
`uv run python scripts/ci/dev_check.py` (ruff `B/ASYNC/RUF` on changed lines, test
skip/xfail census, `pytest --lf`, focused tests, `basedpyright` basic). It is the
mandatory first feedback step and never replaces the PR-level gates above; see
[`DEVELOPMENT_WORKFLOW.md`](docs/operations/development/agent-guides/DEVELOPMENT_WORKFLOW.md#fast-feedback-loop-mandatory-first-step).

Before opening or resetting an Observation/qualification window after a merged
behavior or evidence change, deploy the exact merged commit to Fedora, restart
when required, and verify runtime commit, schema, provider, and settlement
health. Merge, release, deployment, and runtime proof are separate claims.

For Orca-owned Mac-to-Windows work, use Orca Remote Runtime and read
[`ORCA_CLI_WORKFLOW.md`](docs/operations/development/ORCA_CLI_WORKFLOW.md)
after `orca skills get orca-cli`; pairing codes are credentials. SSH is for
bootstrap/recovery/OS diagnosis only.

## Commands and code navigation

Python always through `uv` (`uv run` needs no activated venv):

```sh
uv sync                                        # install/lock deps
uv run aletheia doctor                         # environment self-check
uv run pytest tests/test_<area>.py -q          # focused test (the normal loop)
uv run pytest tests/test_x.py::test_y -q       # single test
uv run pytest tests -q -m "domain_execution"   # one domain (markers in pyproject.toml)
uv run pytest tests -q -m "fast"               # cheap set for every bug-fix loop
uv run ruff check core extensions ops tests    # lint (config in pyproject.toml)
uv run python scripts/ci/check_docs.py            # documentation-governance checks
uv run python scripts/ci/ci_plan.py               # deterministic local validation plan
uv run --no-sync python scripts/ci/ci_preflight.py --pr-body-file <file>   # PR body preflight
```

`tests/` is flat and large: test files are named by area (`test_<area>.py`), and
every test also carries a tier marker (`tier_unit` / `tier_integration` /
`tier_e2e` / `tier_spec`) and a `domain_*` marker. Selecting by marker is usually
faster than guessing file names. Executable specs live in `tests/specs/`.

Frontend (`frontend/`, React 19 + Vite + Tailwind 4):

```sh
npm ci
npm run build          # tsc -b → vite build → compress → perf check
npm run lint           # eslint src/
npm test               # vitest run
npm run check:api-types        # drift check vs backend OpenAPI schema
npm run check:settings-types   # drift check vs backend settings model
```

`npm run build` writes into `core/aletheia/dashboard_static/` (the directory the
backend serves), so a UI change is live after a build plus a browser refresh — do
not start a dev server for that. Regenerate types with `npm run generate:api-types`
after changing backend route models; `scripts/ci/export_openapi_schema.py` produces
the schema.

Runtime inspection:

```sh
uv run aletheia control status --json          # authoritative read-only runtime snapshot
uv run aletheia gateway <start|stop|restart|status|logs>
```

### Where the stages live

One installed distribution, four wheel roots (see `[tool.hatch.build.targets.wheel]`):
`core/aletheia` (importable as `aletheia`), `extensions`, `ops`, `legacy`.

**Core must stay provider-agnostic.** Concrete venues, data providers, and model
vendors live in `extensions/`; core receives them as injected factories.
`extensions/exchanges/composition.py` is the seam: it maps an execution mode
(`bitget_demo`, `okx_demo`, `binance_testnet`, …) to a profile plus a CCXT adapter
and registers it into `aletheia.uta.registry`. Core code should never import a
specific exchange module.

Request/execution path, and where each stage lives. This table maps a stage to a
directory only — task → owner → source → focused-test navigation is the single
code map named above, `SYSTEM_MAP.md`:

| Stage | Location |
| --- | --- |
| CLI entry (`aletheia` / `ato`) | `core/aletheia/main.py` → `core/aletheia/cli/*` (one module per command group) |
| HTTP app + routes | `core/aletheia/server/app.py`, `server/routes/` (one module per route group) |
| Process/service lifecycle, supervisor, frontdoor | `core/aletheia/gateway/` |
| Candidate discovery / scoring | `core/aletheia/universe/`, `extensions/data/collectors/volume_scanner.py`, `extensions/regime` |
| Entry strategy authority (plans, triggers, sizing, TA) | `core/aletheia/trading/` |
| Agent/LLM loop (System Operator) | `core/aletheia/agent_shell/`, `agent_runtime/`, `agents/` |
| Risk approval (precedes every execution) | `core/aletheia/risk/` |
| Execution Gateway, coordinator, stops, reconciliation | `core/aletheia/execution/` |
| Unified trading account abstraction | `core/aletheia/uta/` |
| Positions, accounting, outcomes | `core/aletheia/portfolio/`, `analytics/`, `economics/` |
| Persistence (SQLite, migrations, repositories) | `core/aletheia/storage/` |
| Learning / counterfactual / research | `extensions/learning`, `extensions/counterfactual`, `extensions/research` |
| Serenity research + Candidate publication | `core/aletheia/trading/serenity_scoring.py`, `serenity_publication.py`, `serenity_evidence.py` (there is no `extensions/serenity`) |
| Frontend dashboard | `frontend/src` → built into `core/aletheia/dashboard_static/` |

Runtime state is per-user and outside the repo: `~/.aletheia` by default,
overridden by an absolute `ALETHEIA_DATA_DIR` (`core/aletheia/runtime_paths.py`).
Tests that touch the DB must point `ALETHEIA_DATA_DIR` at a temp root.

Symbol display has exactly one entry point: `frontend/src/lib/format.ts`'s
`fmtSymbol()`. Do not concatenate symbol text anywhere else.

Some machines have a CodeGraph index (`.codegraph/`), but this file cannot promise
that any given checkout has one, or that its index matches the current HEAD. Use it
only after probing: `codegraph explore "<symbols or question>"` if the command and
index are present and the index is not obviously behind. If it is missing or stale,
fall back to `git grep` / language-server lookups — do not build a CodeGraph index
as a prerequisite for ordinary work. Either way, read the file before editing: the
index can lag.

### Tool-specific entry files

`CLAUDE.md` (Claude Code) and `CODEX.md` exist only to import this file
(`@AGENTS.md`). They are pointers, not rule copies: if a tool seems to need a rule
repeated, fix this file instead. `CONTRIBUTING.md` states the same rule to human
contributors.

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
