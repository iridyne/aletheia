---
name: aletheia-dev-loop
description: "Aletheia routine edit loop: run the changed-file fast gate (dev_check), repair in order, and know which checks are deliberately excluded"
---

# Aletheia fast dev loop

Every routine edit in `/Users/yixian/Projects/aletheia` starts with the changed-file gate:

```sh
uv run python scripts/dev_check.py            # working tree vs HEAD
uv run python scripts/dev_check.py --staged   # staged only (hook runs this)
uv run python scripts/dev_check.py --base origin/master
uv run python scripts/dev_check.py --fast     # also -m fast default set
```

Steps, in order, only against changed files: `git diff --check` → `ruff check --extend-select B,ASYNC,RUF` filtered to changed lines → `scripts/check_test_hygiene.py` census → `pytest --lf` (when a cache exists) → focused tests (module name/import match) → `basedpyright --level error` filtered to changed lines.

## Repair order — stop at the first red

1. `dev_check` (seconds).
2. Focused tests for the changed module.
3. `--fast`.
4. Full suite only through the isolated Fedora process; never on macOS, never two suites concurrently.

## Facts that shaped the design (re-measure before changing it)

- `ruff check --extend-select B,ASYNC,RUF` repo-wide: ~1000 findings, dominated by RUF001/002/003 (ambiguous unicode on Chinese prose) → those three stay ignored.
- `ruff format --check` would rewrite 1152 of ~1373 files → never add it to the loop without a repo-wide formatting migration.
- Plain `ruff check` (E/F) is repo-wide clean → the pre-commit hook can run it without false positives.
- Contract enforced is "this change adds no new problem", not "the repo is clean".

## Hooks and enforcement

- `scripts/hooks/pre-commit` = secrets/`.env` refusal + `dev_check --staged --no-tests --no-types`; never pytest; installed by `scripts/dev/dev-init.sh`.
- `git commit --no-verify` only for a verified false positive, stated in the commit.
- `dev_check` is not PR evidence: `scripts/ci_preflight.py` / `scripts/ci_local.py` own that.

## Test hygiene gate (`scripts/check_test_hygiene.py`)

Fails on: bare `pytest.skip()`, skip/skipif without reason, xfail without `strict=True`/reason, imperative `pytest.xfail(...)` (can never turn red), module-level `importorskip`/`allow_module_level`/unconditional `pytestmark`, and assertions that can only run behind a data/environment guard (or after a guard-early-return). Nested helper functions are opaque by design. A repo-wide test asserts the corpus is clean, so new violations fail the suite.
