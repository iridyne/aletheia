# Session-Independent Agent Workflow Plan

## Goal

Make repository work recoverable, verifiable, and handoff-ready without relying
on any prior chat session. A newly started agent must be able to inspect the
repository, run one safe command, and determine the work it may continue, the
facts it must re-check, the proof already available, and the next concrete
action.

This is a development-workflow change only. It does not change trading
decisions, risk limits, order execution, credentials, the database schema, or
gateway behaviour.

## Problem confirmed in the current repository

The repository already has useful material: `AGENTS.md`, `CURRENT_TASK.md`,
`docs/_ai/CURRENT_TASK.md`, `docs/_ai/AI_HANDOFF.md`, `docs/_ai/DEVLOG.md`,
`docs/_ai/DECISIONS.md`, `aletheia doctor`, and the worktree playbook. They do
not yet form an enforceable system:

- there are two current-task entry points and the canonical document is large;
- handoffs are free-form and shared, so concurrent tasks overwrite or obscure
  one another;
- a status statement can say a test passed without a machine-checkable command,
  commit, exit status, or freshness rule;
- startup and shutdown are mostly instructions for an agent to remember;
- the versioned pre-commit reminder checks the obsolete `docs/AI_HANDOFF.md`
  path rather than the current document;
- worktree layout is deliberately local and therefore must not be committed as
  durable project truth.

The existing rule in `docs/_ai/DEV_PROTOCOL.md` remains correct: live machine
facts are not Git facts. `aletheia doctor` and local Git inspection remain the
authoritative sources for the machine currently being used.

## Design principles

1. **Evidence beats prose.** Source, Git, CI and a command executed at a known
   commit outrank a task summary. A summary can be stale but must never make a
   stale result look current.
2. **Separate durable intent from local facts.** Git stores task intent,
   decisions, handoff notes and proof metadata. Bootstrap probes Git/worktree
   state locally. It never persists gateway status, DB contents, balances,
   secrets, process IDs, or credentials.
3. **One task owns one packet.** A task has its own file and handoff file. This
   avoids a global, constantly-conflicting `tasks.yaml` when several worktrees
   are active.
4. **A branch is a claim, not proof.** Task ownership is an advisory lease tied
   to a branch and worktree. Bootstrap detects conflicts and expired claims;
   it never assumes an old local layout is reproducible elsewhere.
5. **No automatic completion.** Only a human/agent explicitly changes a task
   to done, after attaching command and commit evidence. The checker can reject
   unsupported claims but never fabricates success.
6. **Safe diagnostics.** All command output and task metadata must be secret
   safe. The tools validate only metadata, redact recognised credential forms
   in display output, and do not run arbitrary recorded commands by default.

## Target repository layout

```text
.agent/
├── README.md                    # short, tool-neutral entry point
├── project.yaml                 # schema version and stable repository policy
├── commands.yaml                # approved setup, diagnostic and test commands
├── tasks/
│   ├── TASK-YYYY-NNN.yaml       # one tracked task packet per task
│   └── archived/                # closed task packets retained as history
├── handoffs/
│   └── TASK-YYYY-NNN.md         # current concise handoff for that task
└── schemas/
    ├── project.schema.json
    ├── task.schema.json
    └── commands.schema.json

aletheia/agent_workflow/          # validation and reporting implementation
scripts/agent_bootstrap.py        # thin `uv run` compatible wrapper
scripts/agent_check.py            # thin wrapper
scripts/agent_handoff.py          # thin wrapper
tests/test_agent_workflow.py
tests/specs/spec_agent_workflow.py
```

`docs/_ai/` remains the home for detailed historical development narrative and
architecture decisions. It is not deleted or rewritten. `CURRENT_TASK.md` and
`docs/_ai/AI_WORKFLOW.md` become short compatibility pointers to the bootstrap
workflow, while preserving their useful long-term planning material behind
explicit links.

## Data model

`project.yaml` contains only stable policy: schema version, repository name,
default branch, source-of-truth order, allowed task states, and the command
catalogue location. It deliberately has no `active_task_id` or runtime health
field because there can be multiple active branches and those values become
false on another machine.

Each task packet is independently parseable. Required fields:

```yaml
schema_version: 1
id: TASK-2026-001
title: Add session-independent agent workflow
status: in_progress # backlog | ready | in_progress | blocked | done | cancelled
scope:
  boundary: developer workflow and repository documentation
  excluded: trading, risk, execution, credentials, runtime configuration
intent: What outcome this task is responsible for.
acceptance:
  - A cold session can identify the task and next action.
  - Completion is rejected without current verification evidence.
ownership:
  branch: iridite/agent-continuity
  lease_holder: optional non-secret agent label
  claimed_at: 2026-07-11T00:00:00Z
  expires_at: 2026-07-12T00:00:00Z
base_commit: <full Git commit>
next_action:
  command_key: agent_workflow_tests
  reason: Add red tests for task validation.
blockers: []
verification:
  - command_key: agent_workflow_tests
    command: uv run pytest tests/test_agent_workflow.py -q
    exit_code: 0
    commit: <full Git commit>
    run_at: 2026-07-11T00:00:00Z
    result: passed
handoff: .agent/handoffs/TASK-2026-001.md
updated_at: 2026-07-11T00:00:00Z
```

`verification.command_key` is mandatory and must refer to `commands.yaml`.
The repeated display command is compared to the catalogue, avoiding arbitrary
shell text and accidental secret storage. Evidence is **current** only at the
recorded commit; bootstrap reports it as stale whenever `HEAD` differs, even if
the recorded commit is an ancestor. This is conservative and honest.

The per-task handoff is concise Markdown with fixed headings: purpose, changes
made, uncommitted-work warning, verification table, remaining work, exact next
action, blockers, and risks. It contains no running-service assertions and no
secret values. It is a summary; the task YAML, Git and command results win on
conflict.

## Commands and behaviour

The public surface is `uv run aletheia agent workflow ...`; script wrappers
support tools that need a file command. All Python runs through `uv run`.

| Command | Behaviour | Side effects |
|---|---|---|
| `agent workflow bootstrap [--json]` | validates metadata, detects current branch/worktree, selects the uniquely matching task, reports proof freshness, dirty files, blockers, and next action; points to `doctor` for runtime facts | read-only |
| `agent workflow check [--strict] [--task ID]` | validates schemas, IDs, task state transitions, handoff references, command catalogue, Git commit existence/ancestry, duplicate active ownership, stale/unsupported completion and secret-like metadata | read-only |
| `agent workflow task create/claim/update/complete` | creates or deliberately edits one task packet; completion requires current successful evidence and a handoff | tracked file edit |
| `agent workflow handoff --task ID --from-file FILE` | validates a structured handoff input, updates only that task packet/handoff, and prints the exact files to stage | tracked file edit |
| `agent workflow recovery-test` | performs a read-only cold-start assessment and returns a report suitable for a fresh agent; it does not invent missing facts | read-only |

Bootstrap returns non-zero for corrupt metadata, ambiguous ownership, an
unknown branch claimed by a task, or a task marked done without acceptable
proof. It returns success with visible warnings for a dirty worktree, expired
lease, stale evidence, or no matching task. This lets a new agent inspect and
repair the record without being silently told it is safe to modify code.

## Implementation phases

### Phase 1 — Contract and migration-safe foundation

1. Add `.agent/README.md`, schemas, `project.yaml`, and `commands.yaml` using
   current project commands (`uv sync`, `aletheia doctor`, focused tests,
   architecture check, contract suite, full suite, frontend build).
2. Add a task-packet and handoff format plus explicit source-of-truth and
   secret-safety rules to `AGENTS.md`.
3. Create only one initial, truthful workflow task. Do not fabricate ownership
   for every currently-open local worktree; their owners must claim/migrate
   their own task packets on their own branches.
4. Convert the root `CURRENT_TASK.md` to a pointer and identify
   `docs/_ai/CURRENT_TASK.md` as long-form planning history, not live status.
   Preserve all historical documents.

Exit: an agent can read the new files manually, and schema tests cover valid
and invalid task packets.

### Phase 2 — Read-only recovery and validation tools

1. Implement parser/validator in a small developer-workflow module, using the
   already-installed `PyYAML` and standard library only.
2. Implement Git inspection through bounded subprocess calls; no command from
   task metadata is executed by bootstrap/check.
3. Implement `bootstrap`, machine-readable JSON output, proof freshness, and
   diagnostics that distinguish repository facts from local runtime facts.
4. Implement `check` with normal and strict modes. Strict mode fails stale
   evidence for `done` tasks and all structural errors; normal mode reports
   stale in-progress evidence as a warning.

Exit: focused unit tests cover malformed YAML, unknown command keys, absent
handoff, invalid commit, non-ancestor base, duplicate active branch ownership,
expired lease, dirty worktree reporting, stale proof, and secret-pattern
rejection/redaction.

### Phase 3 — Controlled task updates and handoff

1. Add task create/claim/update/complete and non-interactive handoff import.
2. Enforce legal state transitions and require a current successful evidence
   record before `done`.
3. Make the writer preserve unrelated task packets and update only one task and
   its matching handoff in an atomic write.
4. Provide a deterministic human-readable handoff template; no LLM-generated
   free-form state is required for correctness.

Exit: subprocess/integration tests use a temporary Git repository to prove a
fresh agent can claim a task, attach evidence, hand it over, and another clone
can recover the next action without chat history.

### Phase 4 — Gentle enforcement and CI

1. Repair the versioned pre-commit reminder and make it warn when source or
   documentation changes are staged without a corresponding `.agent/tasks/` or
   `.agent/handoffs/` update. It remains non-blocking initially.
2. Add an `agent-state-integrity` CI step to the existing workflow. It runs
   `uv run aletheia agent check --strict` and validates tracked state only; it
   never calls `doctor`, starts a gateway, or assumes local credentials.
3. Add an optional PR convention for task IDs in commit/PR text only after the
   basic system has soaked. Do not make external GitHub availability part of
   local recovery.

Exit: CI catches an intentionally invalid completed task; a normal code change
with a valid task packet passes. No gateway or trading test semantics change.

### Phase 5 — Adoption and recovery drill

1. Update `AI_WORKFLOW.md`, `DEV_PROTOCOL.md`, worktree playbook, and session
   close instructions to use bootstrap/check/handoff while retaining their
   rules about local runtime truth and isolated worktrees.
2. Migrate active work gradually: each owner creates a packet on their task
   branch before the next substantive change. Completed legacy items remain
   historical documents rather than being backfilled with invented evidence.
3. Run a cold-session recovery drill from a clean clone or isolated worktree.
   Give a new agent only the path and the fixed startup sentence; require it to
   state the selected task, scope, proof freshness, worktree warning, blocker,
   next command, and completion criteria.
4. After two weeks of successful use, decide whether warnings may become a
   blocking pre-commit/CI requirement for code-bearing tasks.

## Verification matrix

| Required property | Proof |
|---|---|
| Recoverable | clean-clone recovery drill and integration test select the correct task without chat history |
| Verifiable | checker rejects schema errors, invalid Git references, unsupported completion, stale done evidence, and secret-like metadata |
| Handoff-ready | two-worktree test writes one packet/handoff and a second worktree recovers its next action |
| Concurrent-safe | separate task packets avoid shared handoff edits; duplicate branch claims fail validation |
| Runtime-honest | bootstrap labels `doctor` as a local probe and tests ensure no runtime fields are accepted in tracked state |
| No trading impact | changed-file review excludes trust-boundary modules; focused workflow tests plus architecture/contracts suite remain green |
| CI-enforced | CI invokes strict tracked-state check on pull requests and master pushes |

Required commands before calling the first release complete:

```bash
uv run pytest tests/test_agent_workflow.py tests/specs/spec_agent_workflow.py -q
uv run pytest tests/test_architecture_fitness.py tests/specs/ -q
uv run aletheia agent check --strict
uv run aletheia agent recovery-test --json
```

After the tooling has landed, the cold recovery drill is a mandatory manual
acceptance test, not a claim inferred from unit tests alone.

## Explicit non-goals

- No cloud control plane, database, Redis, Docker service, vector store, or
  external task manager.
- No automatic execution of recorded commands, automatic test-passing claims,
  automatic commits, automatic pushes, or automatic task completion.
- No storage of gateway/process/DB/config/credential state in Git.
- No changes to risk, execution, live-trading permissions, exchange clients, or
  secret handling beyond rejecting secret-like text from workflow metadata.
- No promise that a branch or local worktree exists on another machine; Git
  references and bootstrap diagnostics make that absence visible instead.

## Rollout decision

Begin with phases 1 and 2 in a dedicated branch. They establish the minimum
usable recovery loop and are entirely outside trading trust boundaries. Phases
3–5 follow only after the first cold-start test proves the format is useful;
the existing documents remain available throughout the transition.
