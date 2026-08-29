# Aletheia Agent Guide

This file is the repository-wide router, not the full handbook. Read it first,
then read only the `@` guide whose scope matches the task.

## Always

- Aletheia is a local-first automated trading system: market data → agents →
  strategy proposal → Risk Engine → Execution Gateway → Bitget.
- This MVP is being hardened, not expanded. Do not add exchanges or broad
  infrastructure such as Docker, Postgres, Redis, or vector search without
  explicit scope.
- Optimize for:
  `State consistency > Safety > Auditability > Semantic honesty > Tests > Capability > Agent autonomy`.
- Before changing code, identify the SDD depth (D0 — conformance repair,
  D1 — contract behavior change, D2 — substantial design change) and state
  the Contract Delta or `NONE`. See
  [@DEVELOPMENT_WORKFLOW.md](docs/operations/development/agent-guides/DEVELOPMENT_WORKFLOW.md)
  for the required pre-change steps. Name the boundary being changed and what
  remains unchanged. Analysis-only work does not authorize code changes. Stop
  if a task weakens an invariant, changes trading safety without approval, or
  cannot be proved safely.
- Keep implementation and verification proportional. Default to one root-cause
  change and the smallest runnable regression that proves it. Do not duplicate
  the same edge-case test across DB, API, CLI, and UI layers unless they have
  independent failure modes; expand proof only for money, safety, permission,
  data-loss, or irreversible-state boundaries.
- Orders reach exchanges only through the backend Execution Gateway. Live is
  off by default, and Risk approval always precedes execution.
- Never read, reveal, log, prompt, or commit credentials. Never expand agent
  authority by editing instructions or exposing restricted tools.
- Use `uv run` for Python. Do not run large pytest suites on macOS. Prefer the
  repository's GitHub Actions self-hosted runners. If they are unavailable and
  verification cannot wait, use only an isolated CI/Test process on remote
  Fedora with a temporary data root and no secrets or execution authority.
  Windows is not a validation dependency or fallback.
- Before publishing a CI-triggering PR head, use the repository-native local CI
  entrypoint when available. Do not hand-select CI depth from prose:
  `scripts/ci_plan.py` owns lane selection and `scripts/ci_local.py` executes
  the required local checks.
- Let ordinary Ready PRs accumulate. Run `Prepare and tag releases` only when
  the accumulated batch creates an operational need to update a Runtime; do
  not cut one product version per fix.
- For CI waits, inspect checks, runner/queue state, and failing logs once, then
  monitor non-blockingly at intervals up to 30 minutes while continuing
  independent work. Do not busy-poll GitHub Actions.
- The operational Gateway and model-provider configuration live on the
  owner-selected remote Fedora runtime. Treat macOS as a development/control
  environment: do not infer operational state from local config or start a
  local Gateway unless explicitly requested.
- Before opening or resetting an Observation/qualification window after a
  merged behavior or evidence change, deploy the exact merged commit to the
  Fedora Paper runtime, restart it when required, and verify the runtime commit,
  schema, provider, and settlement health. Never qualify observations from a
  stale or unrecorded build.
- For Mac-to-Windows Agent work, prefer Orca Remote Runtime over ad-hoc SSH:
  Windows runs `orca serve` on the EasyTier-private network, and Mac connects
  through a paired Orca Environment. This makes remote worktrees, terminals,
  Agent sessions, output cursors, and supervised workers first-class Orca
  objects. Before the first Orca operation, run `orca skills get orca-cli` and
  read the returned version-matched guide. SSH remains for bootstrap, recovery,
  or OS-level diagnosis, not the normal Agent control plane. Treat an Orca
  pairing code as a high-privilege credential: never log, prompt, commit, or
  share it outside the intended Mac/Windows pair.
- Never invent reassuring UI/runtime state. Use `not available` or
  `not configured` when no backend fact exists.

## Load only when matched

- Trading, live, risk, execution, credentials, permissions, audit, or controlled
  modules → [@SAFETY_AND_TRUST.md](docs/operations/development/agent-guides/SAFETY_AND_TRUST.md)
- SQLite, runtime files, market data, symbols, paper accounting, config, or async
  API behavior → [@STATE_AND_DATA.md](docs/operations/development/agent-guides/STATE_AND_DATA.md)
- File/tool use, Orca, worktrees, tests, Git/PR/CI, documentation, preview, or
  gateway operation → [@DEVELOPMENT_WORKFLOW.md](docs/operations/development/agent-guides/DEVELOPMENT_WORKFLOW.md)
- Bug classification, experiments, unfamiliar subsystems, or deciding which
  authority to read → [@TASK_ROUTING.md](docs/operations/development/agent-guides/TASK_ROUTING.md)

Read a matched guide completely before acting. Do not bulk-load unrelated
guides. More specific nested `AGENTS.md` files override this file for their
directory scope.
