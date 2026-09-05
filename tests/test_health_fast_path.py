import time


def test_candidate_progress_cache_requires_clock_and_is_detached(monkeypatch):
    from datetime import datetime, timezone
    from aletheia.agent_runtime import health
    monkeypatch.setattr(health, "_candidate_path_progress", {
        "availability": {"available": True, "reason": None}, "issues": [],
    })
    assert health.get_candidate_path_progress_snapshot()["availability"]["reason"] == "cache_invalid"
    health._candidate_path_progress["generated_at"] = datetime.now(timezone.utc).isoformat()
    snapshot = health.get_candidate_path_progress_snapshot()
    snapshot["issues"].append("candidate_refresh_failed")
    snapshot["availability"]["available"] = False
    assert health.get_candidate_path_progress_snapshot()["issues"] == []
    assert health.get_candidate_path_progress_snapshot()["availability"]["available"] is True


def test_candidate_progress_cache_missing_is_unavailable(monkeypatch):
    from aletheia.agent_runtime import health
    monkeypatch.setattr(health, "_candidate_path_progress", None, raising=False)
    assert health.get_candidate_path_progress_snapshot()["availability"]["available"] is False


def test_exchange_reachable_uses_cached_connectivity_probe(monkeypatch):
    import aletheia.agent_runtime.connectivity as connectivity
    from aletheia.agent_runtime.health import _exchange_reachable

    def fail_if_called(*args, **kwargs):
        raise AssertionError("health hot path must not run a live exchange probe")

    monkeypatch.setattr(connectivity, "probe_exchange_sync", fail_if_called)
    monkeypatch.setattr(
        connectivity,
        "get_status",
        lambda: {"last_probe": {"reachable": True, "exchange": "okx", "ts": time.time()}},
    )

    assert _exchange_reachable() is True


def test_exchange_reachable_reports_cached_failure(monkeypatch):
    import aletheia.agent_runtime.connectivity as connectivity
    from aletheia.agent_runtime.health import _exchange_reachable

    monkeypatch.setattr(
        connectivity,
        "get_status",
        lambda: {"last_probe": {"reachable": False, "exchange": "okx", "ts": time.time()}},
    )

    assert _exchange_reachable() is False


def test_exchange_reachable_returns_none_when_no_probe(monkeypatch):
    """No cached probe result must return None (unknown), not True (reachable).

    Before the connectivity monitor completes its first probe, health must be
    initializing rather than falsely healthy (issue #393).
    """
    import aletheia.agent_runtime.connectivity as connectivity
    from aletheia.agent_runtime.health import _exchange_reachable

    monkeypatch.setattr(
        connectivity,
        "get_status",
        lambda: {"last_probe": None},
    )

    assert _exchange_reachable() is None


def test_exchange_reachable_returns_none_on_connectivity_error(monkeypatch):
    """An exception in the connectivity module returns None (unknown), not False."""
    import aletheia.agent_runtime.connectivity as connectivity
    from aletheia.agent_runtime.health import _exchange_reachable

    def _explode():
        raise RuntimeError("connectivity module gone")

    monkeypatch.setattr(connectivity, "get_status", _explode)

    assert _exchange_reachable() is None
