def test_exchange_reachable_uses_cached_connectivity_probe(monkeypatch):
    import aletheia.agent_runtime.connectivity as connectivity
    from aletheia.agent_runtime.health import _exchange_reachable

    def fail_if_called(*args, **kwargs):
        raise AssertionError("health hot path must not run a live exchange probe")

    monkeypatch.setattr(connectivity, "probe_exchange_sync", fail_if_called)
    monkeypatch.setattr(
        connectivity,
        "get_status",
        lambda: {"last_probe": {"reachable": True, "exchange": "okx", "ts": 1.0}},
    )

    assert _exchange_reachable() is True


def test_exchange_reachable_reports_cached_failure(monkeypatch):
    import aletheia.agent_runtime.connectivity as connectivity
    from aletheia.agent_runtime.health import _exchange_reachable

    monkeypatch.setattr(
        connectivity,
        "get_status",
        lambda: {"last_probe": {"reachable": False, "exchange": "okx", "ts": 1.0}},
    )

    assert _exchange_reachable() is False
