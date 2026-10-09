
# tests/ - Test Suite

One flat directory of test modules plus a few subfolders that carry a **runner
contract** rather than a topic. A test module's location is not how the suite is
selected.

## Select by marker, not by path

Tier and domain markers are declared in `pyproject.toml` and classified from the
module's own AST by `tests/conftest.py`; `tests/test_pytest_tiering.py` checks the
classification against real collected items.

Commands are in [`AGENTS.md`](AGENTS.md).

`tier_unit` admission is a conservative static module-level classification, not a
runtime proof of no I/O; see `docs/operations/development/PYTEST_OPTIMIZATION_GUIDE.md`
for the marker table, the slow-test loop, and the xdist boundaries.

## Subfolders

| Folder | Runner contract |
|---|---|
| `specs/` | Executable contract claims (`spec_*.py`, its own `python_files` pattern) collected as `tier_spec` and matched against the conformance matrix |
| `audit/` | Structural gates run as their own changed-file step, regardless of tier; a gate must not force `tier_unit` against the classifier |
| `helpers/` | Shared test code — fixtures, seeds, repository builders, seams, fakes — imported as `tests.helpers.<module>`. Not collected: no module here is named `test_*` |
| `e2e/` | End-to-end pipeline over the real request path |
| `uta/`, `filter_eval/` | Subsystem suites with their own layout and fixtures |
| `fixtures/` | Data fixtures only: JSON/SQLite goldens with frozen provenance. Shared Python helpers go in `helpers/`, never here |

## Find the test for a task

`docs/design/architecture/SYSTEM_MAP.md` maps task → owner → focused tests, and
`scripts/ci/check_docs.py` verifies each route row still links source that owns the
named symbols. Start there; do not maintain a second list.

## Agent Rule

Keep this file a pointer. A hand-kept inventory of test files goes stale on the
next rename: the project already retired a hand-kept spec index for drifting and
replaced it with a pointer to the live check
(`docs/archive/legacy/ai-context/logs/DEVLOG.md`, ledger finding #76). Add a test,
or add a route row to the System Map, instead of enumerating files here.
