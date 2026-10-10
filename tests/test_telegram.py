"""Tests for Telegram bot adapter."""

from contextlib import asynccontextmanager

import pytest

import extensions.telegram.bot as bot_module
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from extensions.telegram.bot import TelegramBot


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
async def test_status_command_reports_unconfigured_execution(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.name = "okx"  # Retained preference, not an active connection.
    cfg.exchange.paper_execution_mode = ""
    save_config(cfg)

    resp = await TelegramBot().handle_message(123, "/status")

    assert "Not configured" in resp
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

    import extensions.telegram.bot as telegram

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


def test_pnl_command_reports_unavailable_unrealized_instead_of_zero(monkeypatch):
    import aletheia.portfolio.pnl as pnl

    monkeypatch.setattr(
        pnl,
        "compute_portfolio",
        lambda: {
            "today_realized_pnl": 3.0,
            "today_realized_pnl_complete": True,
            "unrealized_pnl": None,
        },
    )

    response = TelegramBot()._cmd_pnl()

    assert "Unrealized: unavailable" in response
    assert "Total: unavailable" in response


@pytest.mark.anyio
async def test_reply_failure_does_not_stop_polling_or_replay_command(monkeypatch, caplog):
    bot = TelegramBot()
    bot.allowed_chat_ids = {123}
    monkeypatch.setattr(bot, "configure", lambda: True)

    client = _FakeTelegramClient([
        [{"update_id": 1, "message": {"chat": {"id": 123}, "text": "/pause"}}],
        [{"update_id": 2, "message": {"chat": {"id": 123}, "text": "/status"}}],
    ])
    handled: list[str] = []

    async def handle(_chat_id, text):
        handled.append(text)
        if text == "/status":
            bot.stop()
        return "reply"

    async def send(_chat_id, _text, *, parse_mode):
        if len(handled) == 1:
            raise ConnectionError("temporary")

    monkeypatch.setattr(bot_module, "httpx_client", lambda *, timeout: _fake_httpx_client(client))
    monkeypatch.setattr(bot_module.asyncio, "sleep", lambda _delay: _async_noop())
    monkeypatch.setattr(bot, "handle_message", handle)
    monkeypatch.setattr(bot, "send_message", send)

    await bot.run(poll_interval=0)

    assert handled == ["/pause", "/status"]
    assert bot._offset == 3
    assert "Telegram reply delivery failed for update_id=1: ConnectionError" in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_code", "payload", "message"),
    [
        (403, {"ok": False}, "HTTP 403"),
        (200, {"ok": False}, "was rejected"),
    ],
)
async def test_send_message_rejects_http_and_api_failures_without_token(
    monkeypatch, status_code, payload, message
):
    bot = TelegramBot()
    bot.token = "secret-token"
    client = _FakeSendClient(_FakeSendResponse(status_code, payload))
    monkeypatch.setattr(
        bot_module, "httpx_client", lambda *, timeout: _fake_httpx_client(client)
    )
    bot_module._rate_limit_last_send.clear()

    with pytest.raises(RuntimeError, match=message) as exc_info:
        await bot.send_message(123, "reply")

    assert "secret-token" not in str(exc_info.value)


@pytest.mark.anyio
async def test_send_message_sanitizes_transport_failure(monkeypatch):
    bot = TelegramBot()
    bot.token = "secret-token"
    client = _FakeSendClient(
        RuntimeError("https://api.telegram.org/botsecret-token/sendMessage")
    )
    monkeypatch.setattr(
        bot_module, "httpx_client", lambda *, timeout: _fake_httpx_client(client)
    )
    bot_module._rate_limit_last_send.clear()

    with pytest.raises(RuntimeError, match="transport failed: RuntimeError") as exc_info:
        await bot.send_message(123, "reply")

    assert "secret-token" not in str(exc_info.value)


@pytest.mark.anyio
async def test_command_failure_retries_without_advancing_offset(monkeypatch, caplog):
    bot = TelegramBot()
    bot.allowed_chat_ids = {123}
    monkeypatch.setattr(bot, "configure", lambda: True)

    update = {"update_id": 7, "message": {"chat": {"id": 123}, "text": "/pause"}}
    client = _FakeTelegramClient([[update], [update]])
    attempts = 0

    async def handle(_chat_id, _text):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("command failed")
        bot.stop()
        return None

    monkeypatch.setattr(bot_module, "httpx_client", lambda *, timeout: _fake_httpx_client(client))
    monkeypatch.setattr(bot_module.asyncio, "sleep", lambda _delay: _async_noop())
    monkeypatch.setattr(bot, "handle_message", handle)

    await bot.run(poll_interval=0)

    assert client.seen_offsets == [0, 0]
    assert attempts == 2
    assert bot._offset == 8
    assert bot._consecutive_poll_failures == 0
    assert "Telegram update processing failed" in caplog.text
    assert "Telegram polling failed" not in caplog.text


@pytest.mark.anyio
async def test_repeated_command_failure_uses_update_escalation(monkeypatch):
    bot = TelegramBot()
    bot.allowed_chat_ids = {123}
    monkeypatch.setattr(bot, "configure", lambda: True)

    update = {"update_id": 7, "message": {"chat": {"id": 123}, "text": "/pause"}}
    later = {"update_id": 8, "message": {"chat": {"id": 123}, "text": "/status"}}
    client = _FakeTelegramClient([[update]] * 4 + [[update, later]])
    handled = []
    update_escalations = []
    poll_escalations = []

    async def handle(_chat_id, text):
        handled.append(text)
        if text == "/pause":
            raise RuntimeError("command failed")
        bot.stop()
        return None

    monkeypatch.setattr(
        bot_module, "httpx_client", lambda *, timeout: _fake_httpx_client(client)
    )
    monkeypatch.setattr(bot_module.asyncio, "sleep", lambda _delay: _async_noop())
    monkeypatch.setattr(bot, "handle_message", handle)
    monkeypatch.setattr(
        bot,
        "_escalate_update_failure",
        lambda failed_update, exc: (
            update_escalations.append(
                (failed_update["update_id"], type(exc).__name__)
            )
            or True
        ),
    )
    monkeypatch.setattr(
        bot,
        "_escalate_poll_failure",
        lambda exc: poll_escalations.append(type(exc).__name__),
    )

    await bot.run(poll_interval=0)

    assert update_escalations == [(7, "RuntimeError")]
    assert poll_escalations == []
    assert handled == ["/pause"] * 5 + ["/status"]
    assert bot._offset == 9


@pytest.mark.anyio
async def test_failed_update_escalation_does_not_advance_offset(monkeypatch):
    bot = TelegramBot()
    bot.allowed_chat_ids = {123}
    monkeypatch.setattr(bot, "configure", lambda: True)

    update = {"update_id": 7, "message": {"chat": {"id": 123}, "text": "/pause"}}
    later = {"update_id": 8, "message": {"chat": {"id": 123}, "text": "/status"}}
    client = _FakeTelegramClient([[update]] * 5 + [[update, later]])
    handled = []
    escalation_results = iter([False, True])

    async def handle(_chat_id, text):
        handled.append(text)
        if text == "/pause":
            raise RuntimeError("command failed")
        bot.stop()
        return None

    monkeypatch.setattr(
        bot_module, "httpx_client", lambda *, timeout: _fake_httpx_client(client)
    )
    monkeypatch.setattr(bot_module.asyncio, "sleep", lambda _delay: _async_noop())
    monkeypatch.setattr(bot, "handle_message", handle)
    monkeypatch.setattr(
        bot,
        "_escalate_update_failure",
        lambda _failed_update, _exc: next(escalation_results),
    )

    await bot.run(poll_interval=0)

    assert client.seen_offsets == [0] * 6
    assert handled == ["/pause"] * 6 + ["/status"]
    assert bot._offset == 9


@pytest.mark.anyio
async def test_malformed_update_batch_retries_as_poll_failure(monkeypatch, caplog):
    bot = TelegramBot()
    bot.allowed_chat_ids = {123}
    monkeypatch.setattr(bot, "configure", lambda: True)

    valid = {"update_id": 2, "message": {"chat": {"id": 123}, "text": "/status"}}
    client = _FakeTelegramClient([None, [valid]])

    async def handle(_chat_id, _text):
        bot.stop()
        return None

    monkeypatch.setattr(
        bot_module, "httpx_client", lambda *, timeout: _fake_httpx_client(client)
    )
    monkeypatch.setattr(bot_module.asyncio, "sleep", lambda _delay: _async_noop())
    monkeypatch.setattr(bot, "handle_message", handle)

    await bot.run(poll_interval=0)

    assert client.seen_offsets == [0, 0]
    assert bot._offset == 3
    assert bot._consecutive_poll_failures == 0
    assert "Telegram polling failed (1 consecutive): RuntimeError" in caplog.text


async def _async_noop() -> None:
    return None


@asynccontextmanager
async def _fake_httpx_client(client):
    yield client


class _FakeTelegramClient:
    def __init__(self, batches):
        self._batches = iter(batches)
        self.seen_offsets: list[int] = []

    async def get(self, _url, *, params):
        self.seen_offsets.append(params["offset"])
        return _FakeTelegramResponse(next(self._batches))


class _FakeTelegramResponse:
    def __init__(self, updates):
        self._updates = updates

    def json(self):
        return {"ok": True, "result": self._updates}


class _FakeSendClient:
    def __init__(self, result):
        self._result = result

    async def post(self, _url, *, json):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeSendResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload
