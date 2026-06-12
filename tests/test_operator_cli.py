import sqlite3

from typer.testing import CliRunner

from aletheia.main import app

runner = CliRunner()


def _init_test_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def test_agent_status_command(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch)

    result = runner.invoke(app, ["agent", "status"])

    assert result.exit_code == 0
    assert "Operator:" in result.output
    assert "ReadOnly" in result.output


def test_agent_sessions_and_session_show_commands(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import append_message

    append_message(
        session_id="session-1",
        role="assistant",
        content={"text": "hello"},
        message_type="message",
        related_event_id=None,
    )

    listing = runner.invoke(app, ["agent", "sessions"])
    assert listing.exit_code == 0
    assert "session-1" in listing.output

    detail = runner.invoke(app, ["agent", "session", "show", "session-1"])
    assert detail.exit_code == 0
    assert "session-1" in detail.output
    assert "hello" in detail.output


def test_agent_pause_resume_and_wake_commands(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_runtime import resume_gate

    def fake_resume(**kwargs):
        cfg = loader.load_config()
        cfg.runtime.paused = False
        loader.save_config(cfg)
        return {"allowed": True, "reason": None, "checked": 1, "results": []}

    monkeypatch.setattr(resume_gate, "resume_if_llm_available_sync", fake_resume)

    paused = runner.invoke(app, ["agent", "pause"])
    assert paused.exit_code == 0
    assert load_paused(loader) is True

    resumed = runner.invoke(app, ["agent", "resume"])
    assert resumed.exit_code == 0
    assert load_paused(loader) is False

    paused_again = runner.invoke(app, ["agent", "pause"])
    assert paused_again.exit_code == 0
    woke = runner.invoke(app, ["agent", "wake"])
    assert woke.exit_code == 0
    assert load_paused(loader) is False


def test_agent_resume_and_wake_block_when_llm_unavailable(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_runtime import resume_gate

    monkeypatch.setattr(
        resume_gate,
        "resume_if_llm_available_sync",
        lambda **kwargs: {
            "allowed": False,
            "reason": "llm_unavailable",
            "checked": 1,
            "results": [{"provider": "anthropic_compatible", "usable": False, "error": "429"}],
        },
    )

    paused = runner.invoke(app, ["agent", "pause"])
    assert paused.exit_code == 0

    resumed = runner.invoke(app, ["agent", "resume"])
    assert resumed.exit_code == 1
    assert "llm_unavailable" in resumed.output
    assert load_paused(loader) is True

    woke = runner.invoke(app, ["agent", "wake"])
    assert woke.exit_code == 1
    assert "llm_unavailable" in woke.output
    assert load_paused(loader) is True


def test_agent_resume_and_wake_use_running_gateway_api(tmp_path, monkeypatch):
    _init_test_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_runtime import resume_gate
    from aletheia.gateway import lifecycle

    calls = []

    def fake_gateway_resume():
        calls.append("api")
        cfg = loader.load_config()
        cfg.runtime.paused = False
        loader.save_config(cfg)
        return True

    monkeypatch.setattr(lifecycle, "resume_running_gateway_via_api_if_running", fake_gateway_resume)
    monkeypatch.setattr(
        resume_gate,
        "resume_if_llm_available_sync",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("local gate should not run")),
    )

    paused = runner.invoke(app, ["agent", "pause"])
    assert paused.exit_code == 0
    resumed = runner.invoke(app, ["agent", "resume"])
    assert resumed.exit_code == 0
    assert load_paused(loader) is False

    paused_again = runner.invoke(app, ["agent", "pause"])
    assert paused_again.exit_code == 0
    woke = runner.invoke(app, ["agent", "wake"])
    assert woke.exit_code == 0
    assert load_paused(loader) is False
    assert calls == ["api", "api"]


def load_paused(loader_module) -> bool:
    return loader_module.load_config().runtime.paused
