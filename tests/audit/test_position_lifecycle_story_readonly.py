"""Profit Loop Observability Contract §1/§2: Position Story is a read-only projection.

Scan the current package, including its readers, rather than a retired .py path.
Reuse the diagnostics guard's declared repository-writer/SQL shapes; actual
read-only behavior is also exercised by test_position_lifecycle_story.py.
"""

from tests.audit._segment_guard import ROOT
from tests.audit.test_doctor_no_execution_writes import _violations_in


def test_position_story_never_writes_trading_state():
    paths = sorted((ROOT / "core/aletheia/analytics/position_lifecycle_story").rglob("*.py"))
    assert paths, "Position Story source not found; read-only gate is vacuous"
    violations = [
        f"{path.relative_to(ROOT)}:{detail}"
        for path in paths
        for detail in _violations_in(path)
    ]
    assert not violations, "Position Story must remain read-only:\n" + "\n".join(violations)
