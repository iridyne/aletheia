# tests/ — Test suite

Agent rules for writing and running tests. Layout, subfolder runner contracts and marker
selection are in [`README.md`](README.md); task → focused test routing is in
[`SYSTEM_MAP.md`](../docs/design/architecture/SYSTEM_MAP.md). Do not copy either here.

## Commands

```sh
uv run pytest tests/test_<area>.py -q          # focused file (the normal loop)
uv run pytest tests/test_x.py::test_y -q       # single test
uv run pytest tests -q -m "domain_execution"   # one domain; markers listed in pyproject.toml
uv run pytest tests -q -m "fast"               # cheap set for every bug-fix loop
uv run pytest tests --collect-only -q          # whole-tree import check (cheap)
```

## Rules

- Every test carries a tier marker (`tier_unit` / `tier_integration` / `tier_e2e` /
  `tier_spec`) and a `domain_*` marker, classified from the module AST by `conftest.py`.
- `conftest.py` roots `ALETHEIA_DATA_DIR` at a temp directory when it is unset. An explicit
  root must be outside the checkout and outside `~/.aletheia`; pytest rejects others.
- The full suite runs only on Fedora, never on macOS, and never two at once.
- `dev_check`'s focused selection only finds tests that name a changed module. After
  deleting or renaming a public symbol, also run the whole-tree `--collect-only` above:
  importers in unrelated test files are otherwise missed.
- Before calling a failure pre-existing, reproduce it on the parent revision in a separate
  workspace ([Development Workflow](../docs/operations/development/agent-guides/DEVELOPMENT_WORKFLOW.md)).
- Executable contract claims live in `specs/`; when a contract changes, its SpecClaim changes
  in the same PR. Never weaken a test to make an implementation pass.
