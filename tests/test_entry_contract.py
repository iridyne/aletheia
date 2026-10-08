"""Executable refactors preserve identity; consumed semantic material rotates it."""

from dataclasses import replace

import pytest

from aletheia.trading import entry_contract


@pytest.fixture(autouse=True)
def clear_identity_cache():
    entry_contract.entry_contract_fingerprints.cache_clear()
    yield
    entry_contract.entry_contract_fingerprints.cache_clear()


@pytest.mark.parametrize("component", ["passive", "generation"])
def test_equivalent_callable_refactors_preserve_plan_permission(monkeypatch, component):
    from aletheia.trading import entry_plan
    from aletheia.universe import fast_stream

    baseline = entry_contract.entry_contract_fingerprints()
    if component == "passive":
        original = entry_plan.post_only_price_is_passive

        def extracted_passive_check(*args, **kwargs):
            result = original(*args, **kwargs)
            return result

        for direction, price in (("long", 99.0), ("long", 102.0), ("short", 102.0), ("short", 99.0)):
            assert extracted_passive_check(direction, price, 100.0, 101.0) == original(direction, price, 100.0, 101.0)
        monkeypatch.setattr(entry_plan, "post_only_price_is_passive", extracted_passive_check)
    else:
        original = fast_stream.events_share_generation

        def delegated_generation_check(*events):
            return original(*events)

        assert delegated_generation_check() == original()
        monkeypatch.setattr(fast_stream, "events_share_generation", delegated_generation_check)
    entry_contract.entry_contract_fingerprints.cache_clear()
    assert entry_contract.entry_contract_fingerprints() == baseline


@pytest.mark.parametrize("material", ["compiler", "trigger", "prompt", "schema", "route", "profile", "windows"])
def test_semantic_material_changes_invalidate_old_identity(monkeypatch, material):
    from aletheia.execution import venue_capabilities
    from aletheia.trading import entry_plan_compiler, entry_profiles, trigger_specs
    from aletheia.trading.entry_plan import EntryPlan

    baseline_behavior, baseline_schema = entry_contract.entry_contract_fingerprints()
    if material == "compiler":
        monkeypatch.setitem(entry_plan_compiler._RISK_REWARD_TARGET, "LOW", 1.75)
    elif material == "trigger":
        monkeypatch.setitem(trigger_specs.TRIGGER_RULES, "long_min_buy_ratio", 0.65)
    elif material == "prompt":
        monkeypatch.setattr(entry_contract, "CANONICAL_ENTRY_SYSTEM_PROMPT", "Return NO_PLAN only.")
    elif material == "schema":
        schema = EntryPlan.model_json_schema()
        schema["required"] = [*schema["required"], "new_required_fact"]
        monkeypatch.setattr(EntryPlan, "model_json_schema", lambda: schema)
    elif material == "route":
        monkeypatch.setattr(venue_capabilities, "ENTRY_EXECUTION_MINIMUM_PERMISSION", {})
    elif material == "windows":
        # The frozen planner input must have exactly these closed-bar windows, so a
        # change to them is a change of contract, not an in-place edit.
        from aletheia.trading import entry_analysis

        monkeypatch.setitem(entry_analysis.ENTRY_WINDOW_BARS, "4h", entry_analysis.ENTRY_WINDOW_BARS["4h"] + 1)
    else:
        profiles = entry_profiles.registered_entry_profiles()
        monkeypatch.setattr(entry_profiles, "_REGISTRY", (*profiles[:-1], replace(profiles[-1], trigger_freshness_ms=1000)))
    entry_contract.entry_contract_fingerprints.cache_clear()
    behavior, schema = entry_contract.entry_contract_fingerprints()
    assert behavior != baseline_behavior
    assert (schema != baseline_schema) == (material == "schema")


def test_activation_identity_uses_same_trigger_material_without_source_gate(monkeypatch):
    from aletheia.trading.entry_activation import _spec_identity
    from aletheia.trading import trigger_specs

    spec = trigger_specs.registered_trigger_specs()[0]
    baseline = _spec_identity(spec)

    def delegated_validation(params):
        return spec.validate_params(params)

    assert _spec_identity(replace(spec, validate_params=delegated_validation)) == baseline
    monkeypatch.setitem(trigger_specs.TRIGGER_RULES, "reclaim_confirmation_ms", 500)
    assert _spec_identity(spec) != baseline


def test_retained_activation_survives_validator_refactor_but_rejects_semantic_change(monkeypatch):
    from aletheia.trading.entry_activation import build_entry_trigger_activation
    from aletheia.trading import trigger_specs
    from tests.test_entry_activation import _observed_activation

    # _observed_activation -> _plan() runs the real canonical sizer via
    # apply_entry_financing (#3729); pin the same fixture capital
    # tests/test_entry_capture.py::capture_input uses.
    monkeypatch.setattr("aletheia.risk.engine.account_equity_for_execution", lambda *a, **k: 5000.0)
    monkeypatch.setattr("aletheia.risk.engine._account_equity", lambda *a, **k: 5000.0)
    monkeypatch.setattr("aletheia.risk.engine.account_available_for_execution", lambda *a, **k: 5000.0)
    plan, ready, now = _observed_activation()
    activation = build_entry_trigger_activation(plan, ready, now=now)
    before = (plan.model_dump_json(), activation.model_dump_json())
    spec = trigger_specs.registered_trigger_specs()[0]

    def extracted_validation(params):
        validated = spec.validate_params(params)
        return validated

    monkeypatch.setitem(trigger_specs._REGISTRY, spec.spec_id, replace(spec, validate_params=extracted_validation))
    activation.require_valid(plan, now=now)
    monkeypatch.setitem(trigger_specs.TRIGGER_RULES, "reclaim_confirmation_ms", 500)
    with pytest.raises(ValueError, match="identity_mismatch"):
        activation.require_valid(plan, now=now)
    assert (plan.model_dump_json(), activation.model_dump_json()) == before
