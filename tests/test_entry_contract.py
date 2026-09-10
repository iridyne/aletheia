"""Contract identity must track executable Entry semantics, not editorial edits."""

import inspect

import pytest


@pytest.mark.parametrize(
    ("component", "before", "after", "changes_behavior"),
    [
        ("plan_next_candidate", '"""Claim, reason once, and CAS the result against the current generation."""',
         '"""Editorial clarification only."""\n    # No executable change.\n', False),
        ("plan_next_candidate", '"status": "idle"', '"status"  :  "idle"', False),
        ("aletheia.trading.entry_plan_compiler", "max_requotes=0", "max_requotes=1", True),
        ("aletheia.trading.momentum_trigger", "ratio < 0.60", "ratio < 0.65", True),
        ("aletheia.trading.trigger_specs", "freshness_ms=2_000", "freshness_ms=1_000", True),
        ("_load_entry_plan_watchers", "MomentumTriggerEngine(", "ChangedTriggerEngine(", True),
    ],
)
def test_entry_identity_tracks_behavior_without_editorial_cycle_churn(
    monkeypatch, component, before, after, changes_behavior
):
    from aletheia.trading.entry_contract import entry_contract_fingerprints

    source = inspect.getsource
    changed = []

    def edited_source(obj):
        original = source(obj)
        if obj.__name__ == component:
            assert before in original, f"regression probe no longer matches {component}"
            changed.append(component)
            return original.replace(before, after)
        return original

    entry_contract_fingerprints.cache_clear()
    try:
        baseline_behavior, baseline_schema = entry_contract_fingerprints()
        monkeypatch.setattr(inspect, "getsource", edited_source)
        entry_contract_fingerprints.cache_clear()
        behavior, schema = entry_contract_fingerprints()
        assert schema == baseline_schema
        assert (behavior != baseline_behavior) is changes_behavior
        assert changed, f"Entry identity omits {component}"
    finally:
        entry_contract_fingerprints.cache_clear()
