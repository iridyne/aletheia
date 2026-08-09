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
- Before changing code, name the boundary being changed and what remains
  unchanged. Analysis-only work does not authorize code changes. Stop if a task
  weakens an invariant, changes trading safety without approval, or cannot be
  proved safely.
- Orders reach exchanges only through the backend Execution Gateway. Live is
  off by default, and Risk approval always precedes execution.
- Never read, reveal, log, prompt, or commit credentials. Never expand agent
  authority by editing instructions or exposing restricted tools.
- Use `uv run` for Python. Do not run large pytest suites on macOS; use the
  Windows validation host instead.
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

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
