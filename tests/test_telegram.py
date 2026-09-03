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
    cfg = loader.load_config()
    cfg.telegram.enabled = True
    cfg.telegram.allowed_chat_ids = [123]
    loader.save_config(cfg)


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
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.telegram.allowed_chat_ids = [999]
    save_config(cfg)

    bot = TelegramBot()
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
async def test_authorization_tracks_current_config_after_startup(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    import yaml

    import aletheia.config.loader as loader

    bot = TelegramBot()
    bot.allowed_chat_ids = {123}  # Startup snapshot.

    # Warm this process's cache, then emulate another process writing config.
    loader.load_config()
    cache_before = loader._config_cache
    raw = yaml.safe_load(loader.CONFIG_PATH.read_text(encoding="utf-8"))
    raw["telegram"]["allowed_chat_ids"] = [456]
    loader.CONFIG_PATH.write_text(
        yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
    )

    assert await bot.handle_message(123, "/status") is None
    assert loader._config_cache is cache_before
    assert "Gateway" in await bot.handle_message(456, "/status")

    raw["telegram"]["enabled"] = False
    loader.CONFIG_PATH.write_text(
        yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
    )

    assert await bot.handle_message(456, "/status") is None
    reset_db()


@pytest.mark.anyio
async def test_authorization_reads_config_off_event_loop(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    import threading

    import aletheia.telegram.bot as telegram

    event_loop_thread = threading.get_ident()
    reads = []
    real_load_config = telegram.load_config

    def tracked_load_config(**kwargs):
        reads.append((threading.get_ident(), kwargs))
        return real_load_config(**kwargs)

    monkeypatch.setattr(telegram, "load_config", tracked_load_config)

    assert "Gateway" in await telegram.TelegramBot().handle_message(123, "/status")
    fresh_read_threads = [
        thread for thread, kwargs in reads if kwargs.get("use_cache") is False
    ]
    assert fresh_read_threads and all(
        thread != event_loop_thread for thread in fresh_read_threads
    )
    reset_db()


@pytest.mark.anyio
async def test_latest_command_empty(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    bot = TelegramBot()
    resp = await bot.handle_message(123, "/latest")
    assert "No recent" in resp
    reset_db()


def test_pnl_command_uses_one_mode_scoped_portfolio_projection(monkeypatch):
    import aletheia.portfolio.pnl as pnl

    monkeypatch.setattr(
        pnl,
        "today_realized_pnl",
        lambda: pytest.fail("must not read unscoped realized PnL"),
    )
    monkeypatch.setattr(
        pnl,
        "compute_portfolio",
        lambda: {
            "today_realized_pnl": 0.0,
            "today_realized_pnl_complete": True,
            "unrealized_pnl": -5.0,
        },
    )

    response = TelegramBot()._cmd_pnl()

    assert "Realized: +0.00 USDT" in response
    assert "Unrealized: -5.00 USDT" in response
    assert "Total: -5.00 USDT" in response


def test_pnl_command_preserves_incomplete_realized_state(monkeypatch):
    import aletheia.portfolio.pnl as pnl

    monkeypatch.setattr(
        pnl,
        "today_realized_pnl",
        lambda: pytest.fail("must not read unscoped realized PnL"),
    )
    monkeypatch.setattr(
        pnl,
        "compute_portfolio",
        lambda: {
            "today_realized_pnl": 20.0,
            "today_realized_pnl_complete": False,
            "unrealized_pnl": -5.0,
        },
    )

    response = TelegramBot()._cmd_pnl()

    assert "Realized: unavailable" in response
    assert "Total: unavailable" in response
