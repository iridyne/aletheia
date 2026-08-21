"""Focused tests for #1209: Windows one-shot task enable/disable restoration."""
from aletheia.gateway import managed_service as _managed_svc


def test_start_windows_finally_restores_disabled_state(monkeypatch):
    """#1209: DISABLE is always attempted in finally, even if run raises."""
    from aletheia.gateway import session_service

    monkeypatch.setattr(
        session_service,
        "_status_windows",
        lambda: {"installed": True, "owned": True, "enabled": False, "running": False},
    )

    enable_called = []
    run_called = []
    disable_called = []

    def fake_change(enabled):
        if enabled:
            enable_called.append(1)
        else:
            disable_called.append(1)
        return {"ok": True}

    def fake_run(_cmd):
        run_called.append(1)
        raise RuntimeError("schtasks run failed")

    monkeypatch.setattr(session_service, "_change_windows", fake_change)
    monkeypatch.setattr(_managed_svc, "_run_schtasks", fake_run)

    result = session_service._start_windows()
    assert result["ok"] is False
    assert enable_called, "expected enable to be called before run"
    assert run_called, "expected run to be attempted"
    # #1209: DISABLE is attempted in finally block
    assert disable_called, "expected disable to be attempted in finally block"


def test_start_windows_disable_fail_returns_partial_transition(monkeypatch):
    """#1209: if DISABLE fails in finally, return partial_transition with enabled=true."""
    from aletheia.gateway import session_service

    class _FakeProc:
        returncode = 0
        stderr = ""

    status_calls = []

    def fake_status():
        status_calls.append(1)
        if len(status_calls) == 1:
            return {"installed": True, "owned": True, "enabled": False, "running": False}
        return {"installed": True, "owned": True, "enabled": True, "running": True}

    def fake_change(enabled):
        if not enabled:
            return {"ok": False, "reason": "access_denied"}
        return {"ok": True}

    monkeypatch.setattr(session_service, "_status_windows", fake_status)
    monkeypatch.setattr(session_service, "_change_windows", fake_change)
    monkeypatch.setattr(_managed_svc, "_run_schtasks", lambda _cmd: _FakeProc())

    result = session_service._start_windows()
    assert result["ok"] is False
    assert result["reason"] == "partial_transition"
    assert result["enabled"] is True


def test_start_windows_success_reads_final_state(monkeypatch):
    """#1209: final result comes from re-read status after all operations."""
    from aletheia.gateway import session_service

    class _FakeProc:
        returncode = 0
        stderr = ""

    status_calls = []

    def fake_status():
        status_calls.append(1)
        if len(status_calls) == 1:
            return {"installed": True, "owned": True, "enabled": False, "running": False}
        return {"installed": True, "owned": True, "enabled": False, "running": True}

    monkeypatch.setattr(session_service, "_status_windows", fake_status)
    monkeypatch.setattr(session_service, "_change_windows", lambda _e: {"ok": True})
    monkeypatch.setattr(_managed_svc, "_run_schtasks", lambda _cmd: _FakeProc())

    result = session_service._start_windows()
    assert result["ok"] is True
    assert result["running"] is True
    assert result["enabled"] is False
