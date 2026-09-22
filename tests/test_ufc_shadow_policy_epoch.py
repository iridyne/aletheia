"""Retired UFC policy-epoch recomputation must not regain online authority."""


def test_historical_ufc_has_no_online_policy_epoch_or_equivalence_pass(monkeypatch):
    from extensions.observation_lab import runner, ufc_shadow

    # Historical decoding and read-only rows are covered by
    # test_ufc_shadow_equivalence.py; no legacy policy-epoch writer is restored.
    assert not hasattr(ufc_shadow, "_resolve_policy_epoch")
    assert not hasattr(ufc_shadow, "_observation_id")
    monkeypatch.setattr(runner, "_WINDOWS", [])
    runner._register_default_windows()
    assert {window.name for window in runner.registered_windows()} == {
        "filter_shadow_evaluate", "durable_observation_windows",
    }
