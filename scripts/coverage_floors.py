"""Enforce per-module coverage floors on trust boundaries.

The repo does not have and does not want a global line-coverage target
(see PR #986 discussion): whole-repo 90% would be a claim the tests do
not support. Instead, each module listed below is a trust boundary where
a wrong answer misleads a decision, corrupts state, or bypasses a check.
Its measured combined line+branch coverage is frozen at the current
level; a PR that lowers it fails, a PR that raises it may (optionally)
ratchet the floor up in a follow-up commit.

Reads the ``.coverage`` SQLite database (or combined data files) via the
coverage.py API so the percentage matches exactly what pytest-cov prints
in the terminal ``Cover`` column.

Usage::

    uv run python scripts/coverage_floors.py            # uses .coverage
    uv run python scripts/coverage_floors.py <data-file>

Exits non-zero on any regression or missing module.
"""

from __future__ import annotations

import sys
from pathlib import Path

import coverage

# Frozen floors: per-module minimum branch-coverage percentage.
# Values below were measured on 2025-11 on the current tip. Raise a floor
# only in the same PR that adds the tests that make it stable; never lower
# a floor to unblock a PR — fix the regression instead.
FLOORS: dict[str, float] = {
    "aletheia/analytics/profit_gradient.py": 90.0,
    "aletheia/analytics/profit_gradient_estimators.py": 90.0,
    "aletheia/analytics/profit_gradient_evidence.py": 50.0,
    "aletheia/analytics/position_replay.py": 75.0,
    "aletheia/counterfactual/filter_replay.py": 50.0,
    "aletheia/economics/context_artifacts.py": 50.0,
    "aletheia/execution/reconciliation.py": 25.0,
    "aletheia/learning/evidence_epoch.py": 85.0,
    "aletheia/learning/policy_epochs.py": 70.0,
}

def _measured_percentages(data_file: Path) -> dict[str, float]:
    """Return {relative_path: combined_coverage_pct} via coverage.py.

    Matches the value pytest-cov's terminal ``Cover`` column prints:
    ``(covered_statements + covered_branches) / (statements + branches)``.
    """
    import json
    import tempfile

    # config_file=False so the reader does not re-apply .coveragerc's
    # include= (which would drop the synthetic module used by --selftest)
    # and does not depend on cwd. FLOORS is the authoritative filter.
    cov = coverage.Coverage(data_file=str(data_file), config_file=False)
    cov.load()
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as fh:
        json_path = fh.name
    cov.json_report(outfile=json_path)
    with open(json_path) as fh:
        data = json.load(fh)
    Path(json_path).unlink(missing_ok=True)

    out: dict[str, float] = {}
    for module in FLOORS:
        entry = data.get("files", {}).get(module)
        if entry is None:
            continue
        s = entry["summary"]
        denom = s["num_statements"] + s.get("num_branches", 0)
        if denom == 0:
            continue
        covered = (
            s["covered_lines"] + s.get("covered_branches", 0)
        )
        out[module] = covered / denom * 100.0
    return out


def main(argv: list[str]) -> int:
    data_file = Path(argv[1]) if len(argv) == 2 else Path(".coverage")
    if not data_file.exists():
        print(f"coverage data not found: {data_file}", file=sys.stderr)
        return 2

    rates = _measured_percentages(data_file)
    failures: list[str] = []
    for module, floor in sorted(FLOORS.items()):
        if module not in rates:
            failures.append(
                f"{module}: no coverage data (module not exercised or path mismatch)"
            )
            continue
        measured = rates[module]
        if measured + 1e-6 < floor:
            failures.append(
                f"{module}: coverage {measured:.1f}% < floor {floor:.1f}%"
            )
        else:
            print(f"OK  {module}: {measured:.1f}% (floor {floor:.1f}%)")

    if failures:
        print("", file=sys.stderr)
        print("Trust-boundary coverage regressions:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


def _selftest() -> None:
    """Minimal runnable check: rate math + regression detection.

    Runs against a synthesized coverage.py data set built in a temp dir so
    the check is hermetic and does not depend on a real test run.
    """
    import os
    import tempfile
    import textwrap

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp).resolve()
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "tiny.py").write_text(
            textwrap.dedent(
                """
                def f(x):
                    if x > 0:
                        return 1
                    return 0
                """
            )
        )
        cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            cov = coverage.Coverage(
                data_file=str(tmp_path / ".cov"),
                branch=True,
                config_file=False,
                source=["."],
            )
            cov.start()
            sys.path.insert(0, str(tmp_path))
            try:
                from pkg import tiny  # type: ignore[import-not-found]

                tiny.f(1)  # only the True branch is taken
            finally:
                sys.path.pop(0)
            cov.stop()
            cov.save()

            module_key = "pkg/tiny.py"
            original = FLOORS.copy()
            try:
                FLOORS.clear()
                FLOORS[module_key] = 0.0
                rates = _measured_percentages(tmp_path / ".cov")
                assert module_key in rates, rates
                # Only True branch taken → partial coverage in the 50-90% band.
                assert 40.0 < rates[module_key] < 95.0, rates[module_key]

                FLOORS[module_key] = 99.0
                assert main(["self", str(tmp_path / ".cov")]) == 1

                FLOORS[module_key] = 0.0
                assert main(["self", str(tmp_path / ".cov")]) == 0
            finally:
                FLOORS.clear()
                FLOORS.update(original)
        finally:
            os.chdir(cwd)
    print("coverage_floors selftest: OK")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest":
        _selftest()
        raise SystemExit(0)
    raise SystemExit(main(sys.argv))
