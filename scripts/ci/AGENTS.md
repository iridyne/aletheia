# scripts/ci/ — Local gates

Remote CI is retired; these scripts are the local validation surface. They report findings;
they grant no merge or runtime authority.

| Script | Role |
| --- | --- |
| `dev_check.py` | Changed-file gate, first step of every edit loop (diff hygiene, `.env`/secret scan, ruff on changed lines, test hygiene, `pytest --lf`, focused tests, basedpyright) |
| `check_docs.py` | Documentation governance, including the `SYSTEM_MAP.md` navigation-route checks |
| `ci_preflight.py` / `check_change_contract.py` | PR body Change Contract checks |
| `ci_plan.py` / `ci_local.py` | Deterministic local validation plan and its runner |

## Conventions

- VCS: `dev_check`, `check_docs` and `ci_preflight` read the change set through `vcs.py`
  (jj: `@` vs its parent, or `--base <revset>` since `fork_point`). They need jj and run on
  development machines only. `ci_plan.py`, `ci_local.py` and `check_change_contract.py` stay
  on git by construction.
- A gate must fail loudly when its own input disappears (moved file, missing tool); a gate
  that silently stops checking is worse than no gate.
- Changing a gate changes what every PR is held to: add a regression for the new finding and
  for the case it must not flag.

## Tests

```sh
uv run pytest tests/test_dev_check.py tests/test_ci_preflight.py tests/test_ci_plan.py tests/test_documentation_truth.py -q
uv run python scripts/ci/check_docs.py
```
