# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Authority

[`AGENTS.md`](AGENTS.md) is the repository router and takes precedence over this
file for safety boundaries, D0/D1/D2 change classification, commit/PR rules, and
which guide to load for a given work area. Read it first. This file only adds the
commands and the architecture map that AGENTS.md deliberately does not carry.

Hard boundaries worth repeating because they are easy to violate accidentally:

- macOS is development/control only. Do not infer Fedora runtime state, and do
  not start a local gateway unless asked.
- Remote GitHub CI is retired. Validation is local only.
- Never run two full pytest suites concurrently — they share the data directory
  and crash each other; the crash reads as a test failure.
- Full suite on macOS is out of scope; run focused tests proportional to the change.

## Commands

Python (always through `uv`; `uv run` adds no need for an activated venv):

```sh
uv sync                                        # install/lock deps
uv run aletheia doctor                         # environment self-check
uv run pytest tests/test_<area>.py -q          # focused test (the normal loop)
uv run pytest tests/test_x.py::test_y -q       # single test
uv run pytest tests -q -m "domain_execution"   # one domain (markers in pyproject.toml)
uv run pytest tests -q -m "fast"               # cheap set for every bug-fix loop
uv run ruff check core extensions ops tests    # lint (config in pyproject.toml)
uv run python scripts/check_docs.py            # documentation-governance checks
uv run python scripts/ci_plan.py               # deterministic local validation plan
uv run --no-sync python scripts/ci_preflight.py --pr-body-file <file>   # PR body preflight
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
after changing backend route models; `export_openapi_schema.py` produces the schema.

Runtime inspection:

```sh
uv run aletheia control status --json          # authoritative read-only runtime snapshot
uv run aletheia gateway <start|stop|restart|status|logs>
```

## Architecture

One installed distribution, four wheel roots (see `[tool.hatch.build.targets.wheel]`):
`core/aletheia` (importable as `aletheia`), `extensions`, `ops`, `legacy`.

**Core must stay provider-agnostic.** Concrete venues, data providers, and model
vendors live in `extensions/`; core receives them as injected factories.
`extensions/exchanges/composition.py` is the seam: it maps an execution mode
(`bitget_demo`, `okx_demo`, `binance_testnet`, …) to a profile plus a CCXT adapter
and registers it into `aletheia.uta.registry`. Core code should never import a
specific exchange module.

Request/execution path, and where each stage lives:

| Stage | Location |
| --- | --- |
| CLI entry (`aletheia` / `ato`) | `core/aletheia/main.py` → `core/aletheia/cli/*` (one module per command group) |
| HTTP app + routes | `core/aletheia/server/app.py`, `server/routes/` (~23 route modules) |
| Process/service lifecycle, supervisor, frontdoor | `core/aletheia/gateway/` |
| Candidate discovery / scoring | `core/aletheia/universe/`, `extensions/volume_scanner`, `extensions/regime` |
| Entry strategy authority (plans, triggers, sizing, TA) | `core/aletheia/trading/` |
| Agent/LLM loop (System Operator) | `core/aletheia/agent_shell/`, `agent_runtime/`, `agents/` |
| Risk approval (precedes every execution) | `core/aletheia/risk/` |
| Execution Gateway, coordinator, stops, reconciliation | `core/aletheia/execution/` |
| Unified trading account abstraction | `core/aletheia/uta/` |
| Positions, accounting, outcomes | `core/aletheia/portfolio/`, `analytics/`, `economics/` |
| Persistence (SQLite, migrations, repositories) | `core/aletheia/storage/` |
| Learning / counterfactual / research | `extensions/learning`, `extensions/counterfactual`, `extensions/serenity`, `extensions/research` |
| Frontend dashboard | `frontend/src` → built into `core/aletheia/dashboard_static/` |

Runtime state is per-user and outside the repo: `~/.aletheia` by default,
overridden by an absolute `ALETHEIA_DATA_DIR` (`core/aletheia/runtime_paths.py`).
Tests that touch the DB must point `ALETHEIA_DATA_DIR` at a temp root.

Storage is additive-only at the data layer (migrations must not rewrite history).
That constraint does **not** license compatibility shims in code: one capability
keeps exactly one live implementation, and the loser gets deleted rather than
aliased (AGENTS.md, "Convergence over compatibility").

## Where truth lives

Before concluding that code is wrong — especially before judging a deletion to be
a regression — read the contract, not the call sites:

- `docs/design/contracts/` — approved behavior (authoritative over calling code).
- `docs/product/ACTIVE_RUNTIME_SCOPE.md` — what is actually admitted at runtime.
- `docs/product/CAPABILITY_GROUND_TRUTH.md` — capability promises and their evidence limits.
- `docs/governance/decisions/DECISIONS.md` — decision record.
- `docs/design/history/` — retired mechanisms (deleted from code, kept as history).

This repo is indexed by CodeGraph (`.codegraph/` exists): prefer
`codegraph explore "<symbols or question>"` over a grep/read sweep when locating
or understanding code, but verify against the file before editing — the index can
lag.
