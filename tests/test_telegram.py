"""Tests for Telegram bot adapter."""

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.telegram.bot import TelegramBot


def setup_test_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


@pytest.mark.anyio
async def test_status_command(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    bot = TelegramBot()
    resp = await bot.handle_message(123, "/status")
    assert "Gateway" in resp
    assert "live_disabled" in resp
    assert "Paper" in resp
    reset_db()


@pytest.mark.anyio
async def test_status_command_labels_local_paper_without_an_exchange(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.name = "okx"  # Retained preference, not an active connection.
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)

    resp = await TelegramBot().handle_message(123, "/status")

    assert "Local paper (no external exchange)" in resp
    assert "okx (demo)" not in resp
    reset_db()


@pytest.mark.anyio
async def test_pause_command(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    bot = TelegramBot()
    resp = await bot.handle_message(123, "/pause")
    assert "paused" in resp.lower()
    from aletheia.config.loader import load_config
    cfg = load_config()
    assert cfg.runtime.paused is True
    reset_db()


@pytest.mark.anyio
async def test_resume_command(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import resume_gate

    async def fake_resume(*, actor: str, source: str, cause: str = "manual_resume"):
        return {"allowed": True, "reason": None, "checked": 1, "results": []}

    monkeypatch.setattr(resume_gate, "resume_if_llm_available", fake_resume)

    bot = TelegramBot()
    await bot.handle_message(123, "/pause")
    resp = await bot.handle_message(123, "/resume")
    assert "resumed" in resp.lower()
    reset_db()


@pytest.mark.anyio
async def test_resume_command_blocks_when_llm_unavailable(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import resume_gate
    from aletheia.config.loader import load_config

    async def fake_resume(*, actor: str, source: str, cause: str = "manual_resume"):
        return {
            "allowed": False,
            "reason": "llm_unavailable",
            "checked": 1,
            "results": [{"provider": "anthropic_compatible", "usable": False, "error": "429"}],
        }

    monkeypatch.setattr(resume_gate, "resume_if_llm_available", fake_resume)

    bot = TelegramBot()
    await bot.handle_message(123, "/pause")
    resp = await bot.handle_message(123, "/resume")

    assert "resume blocked" in resp.lower()
    assert load_config().runtime.paused is True
    reset_db()


@pytest.mark.anyio
async def test_help_command(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    bot = TelegramBot()
    resp = await bot.handle_message(123, "/help")
    assert "/status" in resp
    assert "/pause" in resp
    reset_db()


@pytest.mark.anyio
async def test_unauthorized_chat_rejected(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    bot = TelegramBot()
    bot.allowed_chat_ids = {999}  # Only chat 999 allowed
    # Unauthorized chats get no reply at all (silent + audited), not an error string.
    resp = await bot.handle_message(123, "/status")
    assert resp is None
    reset_db()


@pytest.mark.anyio
async def test_enabled_telegram_requires_allowed_chat_ids(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.telegram.enabled = True
    cfg.telegram.allowed_chat_ids = []
    save_config(cfg)

    bot = TelegramBot()
    # Misconfigured (enabled but no allow-list) also yields no reply, never a
    # status leak — the condition is audited instead.
    resp = await bot.handle_message(123, "/status")
    assert resp is None
    reset_db()


@pytest.mark.anyio
async def test_latest_command_empty(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    bot = TelegramBot()
    resp = await bot.handle_message(123, "/latest")
    assert "No recent" in resp
    reset_db()
