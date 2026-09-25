"""Tests for scripts/dev_check.py — the changed-line filtering contract.

The gate's value depends on reporting only diagnostics that land on changed
lines: the repository carries ~1k pre-existing B/ASYNC/RUF findings, so a
whole-file gate would fail on every edit.
"""

from __future__ import annotations

import scripts.dev_check as dev_check

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
    assert dev_check.dotted_module("scripts/ci_plan.py") == "scripts.ci_plan"
    assert dev_check.dotted_module("README.md") is None


def test_focused_tests_finds_tests_naming_a_changed_module():
    """A changed module must pull in the tests that name or import it."""
    focused = dev_check.focused_tests(["scripts/ci_plan.py"])

    assert "tests/test_ci_plan.py" in focused


def test_focused_tests_includes_changed_test_files_verbatim():
    focused = dev_check.focused_tests(["tests/test_ci_local.py", "docs/x.md"])

    assert focused == ["tests/test_ci_local.py"]
