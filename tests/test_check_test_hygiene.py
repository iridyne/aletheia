"""Tests for scripts/ci/check_test_hygiene.py (#3471 candidate 1).

Each rule is proven by a counterexample that must fire and a legitimate pattern
that must stay green, plus one repository-wide assertion: the current test corpus
is hygiene-clean, so a new dead skip/xfail or data-guarded silent pass fails here.
"""

from __future__ import annotations

import scripts.ci.check_test_hygiene as hygiene

LEGIT_SOURCE = '''
import pytest
import os


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only path handling")
def test_posix_only():
    assert True


@pytest.mark.xfail(strict=True, reason="TRIPWIRE: feature not implemented (#1)")
def test_tripwire():
    assert not feature_present()


def test_loops_and_branches_are_not_silent_passes():
    rows = load_rows()
    for row in rows:
        assert row["id"]
    if rows:
        assert len(rows) == 2
    else:
        assert rows == []


def test_nested_helper_guard_is_not_a_silent_pass(monkeypatch):
    def _read(session_id):
        row = read(session_id)
        if row is None:
            return None
        return row

    monkeypatch.setattr(mod, "read", _read)
    assert mod.read("s1") is None


def test_skip_with_reason_inside_environment_check():
    if not os.path.exists("/dev/kvm"):
        pytest.skip("requires /dev/kvm")
    assert True
'''


def test_legitimate_patterns_stay_green():
    assert hygiene.audit_source(LEGIT_SOURCE, "tests/test_legit.py") == []


def test_bare_skip_without_reason_fires():
    findings = hygiene.audit_source("import pytest\n\n\ndef test_x():\n    pytest.skip()\n", "tests/test_a.py")

    assert len(findings) == 1
    assert "H1 pytest.skip() without reason" in findings[0]


def test_skipif_without_reason_fires():
    source = "import pytest\n\n\n@pytest.mark.skipif(True)\ndef test_x():\n    assert True\n"

    findings = hygiene.audit_source(source, "tests/test_a.py")

    assert len(findings) == 1
    assert "H2 skip/skipif without reason" in findings[0]


def test_xfail_without_strict_fires():
    source = 'import pytest\n\n\n@pytest.mark.xfail(reason="gap")\ndef test_x():\n    assert False\n'

    findings = hygiene.audit_source(source, "tests/test_a.py")

    assert len(findings) == 1
    assert "H3 xfail without strict=True" in findings[0]


def test_imperative_xfail_fires_but_mark_xfail_does_not():
    imperative = 'import pytest\n\n\ndef test_x():\n    pytest.xfail("gap")\n'
    decorated = 'import pytest\n\n\n@pytest.mark.xfail(strict=True, reason="gap")\ndef test_x():\n    assert False\n'

    assert any("H4 imperative pytest.xfail" in finding for finding in hygiene.audit_source(imperative, "t.py"))
    assert hygiene.audit_source(decorated, "t.py") == []


def test_module_level_importorskip_and_module_skip_fire():
    importorskip = 'import pytest\n\npytest.importorskip("some_optional_dep")\n\n\ndef test_x():\n    assert True\n'
    module_skip = 'import pytest\n\npytest.skip("no display", allow_module_level=True)\n'

    assert any("H5" in finding for finding in hygiene.audit_source(importorskip, "t.py"))
    assert any("H5" in finding for finding in hygiene.audit_source(module_skip, "t.py"))


def test_data_guarded_early_return_fires():
    source = """
def test_rows_present(tmp_path):
    rows = load_rows(tmp_path / "rows.json")
    if not (tmp_path / "rows.json").exists():
        return
    assert len(rows) == 3
"""

    findings = hygiene.audit_source(source, "tests/test_a.py")

    assert any("H6 data-guarded early return" in finding for finding in findings)


def test_assertions_entirely_behind_a_data_guard_fire():
    source = """
def test_all_assertions_guarded(tmp_path):
    parsed = parse(tmp_path)
    if parsed["value"] is not None:
        assert parsed["value"] > 0
"""

    findings = hygiene.audit_source(source, "tests/test_a.py")

    assert any("H6 every assertion is behind" in finding for finding in findings)


def test_repository_tests_are_hygiene_clean(capsys):
    """The gate itself: any new violation in tests/ fails here."""
    assert hygiene.main([]) == 0, capsys.readouterr().out
