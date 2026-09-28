"""Tests for scripts/ci/dev_check.py — the changed-line filtering contract.

The gate's value depends on reporting only diagnostics that land on changed
lines: the repository carries ~1k pre-existing B/ASYNC/RUF findings, so a
whole-file gate would fail on every edit.
"""

from __future__ import annotations

import json
import os
import subprocess
import xml.etree.ElementTree as ET

import scripts.ci.dev_check as dev_check

DIFF = """\
diff --git a/core/aletheia/analytics/candidate_facts.py b/core/aletheia/analytics/candidate_facts.py
--- a/core/aletheia/analytics/candidate_facts.py
+++ b/core/aletheia/analytics/candidate_facts.py
@@ -10,0 +11,3 @@ def build():
+    value = load()
+    return value
+
@@ -40 +44 @@ def other():
-    return old
+    return new
diff --git a/tests/test_x.py b/tests/test_x.py
--- a/tests/test_x.py
+++ b/tests/test_x.py
@@ -1,0 +2,0 @@
"""


def test_parse_added_lines_maps_new_side_ranges_per_file():
    ranges = dev_check.parse_added_lines(DIFF)

    assert ranges["core/aletheia/analytics/candidate_facts.py"] == {11, 12, 13, 44}
    assert ranges["tests/test_x.py"] == set()


def test_diagnostics_on_unchanged_lines_are_dropped():
    diagnostics = [
        {
            "filename": "core/aletheia/analytics/candidate_facts.py",
            "location": {"row": 11, "column": 5},
            "code": "B008",
            "message": "Do not perform function call in argument defaults",
        },
        {
            "filename": "core/aletheia/analytics/candidate_facts.py",
            "location": {"row": 12, "column": 5},
            "code": "RUF059",
            "message": "Unpacked variable is never used",
        },
        {
            "filename": "core/aletheia/analytics/candidate_facts.py",
            "location": {"row": 999, "column": 1},
            "code": "B905",
            "message": "zip() without an explicit strict=",
        },
    ]
    ranges = {"core/aletheia/analytics/candidate_facts.py": {11}}

    findings = dev_check.filter_ruff_diagnostics(diagnostics, ranges)

    assert findings == [
        "core/aletheia/analytics/candidate_facts.py:11:5 B008 Do not perform function call in argument defaults"
    ]


def test_new_file_reports_every_diagnostic():
    diagnostics = [
        {
            "filename": "scripts/new_tool.py",
            "location": {"row": 3, "column": 1},
            "code": "B018",
            "message": "Found useless expression",
        }
    ]

    findings = dev_check.filter_ruff_diagnostics(diagnostics, {"scripts/new_tool.py": None})

    assert findings == ["scripts/new_tool.py:3:1 B018 Found useless expression"]


def test_unknown_path_is_treated_as_changed():
    """Renames and unparsable hunks must not silently hide findings."""
    assert dev_check.is_changed_line({}, "core/aletheia/moved.py", 7) is True


def test_dotted_module_maps_source_paths_to_import_names():
    assert dev_check.dotted_module("core/aletheia/analytics/candidate_facts.py") == (
        "aletheia.analytics.candidate_facts"
    )
    assert dev_check.dotted_module("scripts/ci/ci_plan.py") == "scripts.ci.ci_plan"
    assert dev_check.dotted_module("README.md") is None


def test_focused_tests_finds_tests_naming_a_changed_module():
    """A changed module must pull in the tests that name or import it."""
    focused = dev_check.focused_tests(["scripts/ci/ci_plan.py"])

    assert "tests/test_ci_plan.py" in focused


def test_focused_tests_includes_changed_test_files_verbatim():
    focused = dev_check.focused_tests(["tests/test_ci_local.py", "docs/x.md"])

    assert focused == ["tests/test_ci_local.py"]


def test_cached_failures_rerun_async_inherited_and_parametrized_tests(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_check, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    (tmp_path / "pytest.ini").write_text("[pytest]\nasyncio_mode=auto\n")
    (tmp_path / "test_cached.py").write_text(
        "import pytest\n"
        "def test_sync():\n    assert False\n"
        "async def test_async():\n    assert False\n"
        "class BaseChecks:\n"
        "    def test_inherited(self):\n        assert False\n"
        "class TestGroup(BaseChecks):\n"
        "    @pytest.mark.parametrize('value', [1], ids=['scope::case'])\n"
        "    def test_parameter(self, value):\n        assert value == 2\n"
    )
    report = tmp_path / "failures.xml"
    assert dev_check.run_pytest(["test_cached.py", f"--junitxml={report}"]) == 1
    original = {
        (case.attrib["classname"], case.attrib["name"])
        for case in ET.parse(report).iter("testcase") if case.find("failure") is not None
    }
    assert len(original) == 4
    (tmp_path / "test_unrelated.py").write_text(
        "raise RuntimeError('unrelated module must not be collected')\n"
    )
    cache = tmp_path / ".pytest_cache/v/cache/lastfailed"
    failed = json.loads(cache.read_text())
    failed["test_deleted.py::test_old"] = True
    failed["test_cached.py::test_removed"] = True
    cache.write_text(json.dumps(failed))
    selected = dev_check.previous_failed_test_files()
    assert selected
    assert dev_check.run_pytest([
        "--lf", "--last-failed-no-failures=none", *selected, f"--junitxml={report}",
    ]) == 1
    rerun = {
        (case.attrib["classname"], case.attrib["name"])
        for case in ET.parse(report).iter("testcase") if case.find("failure") is not None
    }
    assert rerun == original


def test_missing_cached_files_do_not_modify_failure_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_check, "REPO_ROOT", tmp_path)
    cache = tmp_path / ".pytest_cache/v/cache/lastfailed"
    cache.parent.mkdir(parents=True)
    failed = {"test_removed.py::TestOld::test_failure": True}
    cache.write_text(json.dumps(failed))
    assert dev_check.previous_failed_test_files() == []
    assert json.loads(cache.read_text()) == failed



def test_empty_cached_file_does_not_hide_a_focused_failure(tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(dev_check, "REPO_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_unrelated.py").write_text("raise RuntimeError('unrelated module collected')\n")
    for args in (
        ["init"], ["add", "tests/test_unrelated.py"],
        ["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-m", "baseline"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    (tests / "test_removed.py").write_text("# The formerly failing test was removed.\n")
    (tests / "test_focused.py").write_text(
        "def test_focused():\n    observed = 1\n    assert observed == 2, 'focused_failure_visible'\n"
    )
    cache = tmp_path / ".pytest_cache/v/cache/lastfailed"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"tests/test_removed.py::test_old": True}))

    assert dev_check.main(["--no-types"]) == dev_check.EXIT_FINDINGS
    assert "focused_failure_visible" in capfd.readouterr().out
