import sqlite3
import json

import pytest

from aletheia.storage.migrations import run_migrations
from aletheia.storage.sqlite import reset_db


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_notify_operator_writes_local_records_and_windows_toast(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia import notifications

    toasts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        notifications,
        "send_windows_toast",
        lambda title, message: toasts.append((title, message)),
    )

    notifications.notify_operator(
        "Gateway failed",
        "spawn verifier timed out",
        source="watchdog",
        entity_type="gateway",
        entity_id="gateway",
    )

    assert toasts == [("Gateway failed", "spawn verifier timed out")]

    from aletheia.files.jsonl import read_jsonl
    audit = read_jsonl("audit.jsonl")
    errors = read_jsonl("errors.jsonl")
    assert any(e.get("event_type") == "operator_notification" for e in audit)
    assert any(e.get("source") == "watchdog" and e.get("title") == "Gateway failed" for e in errors)


def test_notify_operator_respects_windows_toast_config(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.health.alert_via_windows_toast = False
    save_config(cfg)

    from aletheia import notifications
    toasts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        notifications,
        "send_windows_toast",
        lambda title, message: toasts.append((title, message)),
    )

    notifications.notify_operator("Health degraded", "market data stale", source="health")

    assert toasts == []


# ─── Issue #407 ───────────────────────────────────────────────────────────────
# alert_via_telegram should gate health/operator alerts only, not trade fills
# or daily summaries.

def test_trade_fill_not_blocked_by_alert_via_telegram_false(tmp_path, monkeypatch):
    """Issue #407: alert_via_telegram=False must not suppress trade-fill notifications."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.telegram.enabled = True
    cfg.telegram.notify_trade_fills = True
    cfg.health.alert_via_telegram = False  # health alerts disabled
    save_config(cfg)

    from aletheia import notifications
    sent: list[str] = []

    def _fake_telegram(message, *, level="warning", _require_alert_switch=True):
        sent.append(message)

    monkeypatch.setattr(notifications, "_send_telegram_alert", _fake_telegram)
    monkeypatch.setattr(notifications, "_send_webhook", lambda *a, **kw: None)

    notifications.notify_trade_fill(
        symbol="BTC/USDT", direction="long", quantity=0.01,
        fill_price=60000.0, position_id="pos-test-1", execution_mode="paper_internal",
    )

    assert len(sent) == 1, "trade fill must still be sent when alert_via_telegram=False"


def test_operator_alert_blocked_by_alert_via_telegram_false(tmp_path, monkeypatch):
    """Issue #407: alert_via_telegram=False must suppress health/operator alerts."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.telegram.enabled = True
    cfg.health.alert_via_telegram = False
    save_config(cfg)

    from aletheia import notifications
    sent: list[str] = []

    def _fake_telegram(message, *, level="warning", _require_alert_switch=True):
        if _require_alert_switch:
            from aletheia.config.loader import load_config as _lc
            _cfg = _lc()
            if not _cfg.health.alert_via_telegram:
                return
        sent.append(message)

    monkeypatch.setattr(notifications, "_send_telegram_alert", _fake_telegram)
    monkeypatch.setattr(notifications, "_send_webhook", lambda *a, **kw: None)
    monkeypatch.setattr(notifications, "send_windows_toast", lambda *a, **kw: None)

    notifications.notify_operator("Test alert", "some operator issue", telegram=True,
                                   audit=False, error_log=False, windows=False)

    assert sent == [], "operator alert must be blocked when alert_via_telegram=False"


# ─── Issue #406 ───────────────────────────────────────────────────────────────
# Webhook category toggles must be respected at delivery time.

def test_webhook_respects_notify_alerts_false(tmp_path, monkeypatch):
    """Issue #406: turning off notify_alerts must suppress alert webhook delivery."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.webhook.url = "http://example.test/hook"
    cfg.webhook.enabled = True
    cfg.webhook.notify_alerts = False
    save_config(cfg)

    from aletheia import notifications
    delivered: list[dict] = []

    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    # Should NOT raise and should NOT deliver
    notifications._send_webhook({"title": "alert", "message": "test"}, event_type="alert")
    # No AssertionError means delivery was correctly suppressed


def test_webhook_respects_notify_trade_fills_false(tmp_path, monkeypatch):
    """Issue #406: turning off notify_trade_fills must suppress trade-fill webhook."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.webhook.url = "http://example.test/hook"
    cfg.webhook.enabled = True
    cfg.webhook.notify_trade_fills = False
    save_config(cfg)

    from aletheia import notifications
    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    notifications._send_webhook({"symbol": "BTC/USDT"}, event_type="trade_fill")


def test_webhook_respects_notify_daily_summary_false(tmp_path, monkeypatch):
    """Issue #406: turning off notify_daily_summary must suppress daily-summary webhook."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.webhook.url = "http://example.test/hook"
    cfg.webhook.enabled = True
    cfg.webhook.notify_daily_summary = False
    save_config(cfg)

    from aletheia import notifications
    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    notifications._send_webhook({"realized_pnl": 10.0}, event_type="daily_summary")


@pytest.mark.parametrize(
    ("event_type", "telegram_enabled", "telegram_category", "webhook_enabled", "webhook_category",
     "expected_telegram", "expected_webhook"),
    [
        ("trade_fill", False, True, True, True, 0, 1),
        ("trade_fill", True, True, False, True, 1, 0),
        ("trade_fill", True, True, True, True, 1, 1),
        ("trade_fill", False, True, False, True, 0, 0),
        ("trade_fill", True, False, True, True, 0, 1),
        ("trade_fill", True, True, True, False, 1, 0),
        ("trade_fill", True, False, True, False, 0, 0),
        ("daily_summary", False, True, True, True, 0, 1),
        ("daily_summary", True, True, False, True, 1, 0),
        ("daily_summary", True, True, True, True, 1, 1),
        ("daily_summary", False, True, False, True, 0, 0),
        ("daily_summary", True, False, True, True, 0, 1),
        ("daily_summary", True, True, True, False, 1, 0),
        ("daily_summary", True, False, True, False, 0, 0),
    ],
)
def test_trade_and_summary_channels_are_independent(
    tmp_path,
    monkeypatch,
    event_type,
    telegram_enabled,
    telegram_category,
    webhook_enabled,
    webhook_category,
    expected_telegram,
    expected_webhook,
):
    """Issue #1226: Telegram switches must not gate Webhook delivery."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.telegram.enabled = telegram_enabled
    cfg.telegram.notify_trade_fills = telegram_category
    cfg.telegram.notify_daily_summary = telegram_category
    cfg.webhook.url = "http://example.test/hook"
    cfg.webhook.enabled = webhook_enabled
    cfg.webhook.notify_trade_fills = webhook_category
    cfg.webhook.notify_daily_summary = webhook_category
    save_config(cfg)

    from aletheia import notifications

    telegram_calls: list[tuple[str, dict]] = []
    webhook_requests = []
    monkeypatch.setattr(
        notifications,
        "_send_telegram_alert",
        lambda message, **kwargs: telegram_calls.append((message, kwargs)),
    )

    class FakeOpener:
        def open(self, request, *, timeout):
            webhook_requests.append((request, timeout))

    import aletheia.net as net
    monkeypatch.setattr(net, "build_urllib_opener", lambda: FakeOpener())

    if event_type == "trade_fill":
        notifications.notify_trade_fill(
            symbol="BTC/USDT",
            direction="long",
            quantity=0.01,
            fill_price=60000.0,
            position_id="pos-1226",
            execution_mode="paper_internal",
        )
    else:
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
                "today_realized_pnl": 12.34,
                "today_realized_pnl_complete": True,
                "unrealized_pnl": 5.67,
                "positions_count": 2,
                "total_value_usdt": 123.45,
            },
        )
        notifications.notify_daily_summary()

    assert len(telegram_calls) == expected_telegram
    assert len(webhook_requests) == expected_webhook
    if webhook_requests:
        payload = json.loads(webhook_requests[0][0].data)
        assert payload["event"] == event_type
        if event_type == "trade_fill":
            assert payload["symbol"] == "BTC/USDT"
            assert payload["direction"] == "LONG"
        else:
            assert payload["realized_pnl"] == 12.34
            assert payload["unrealized_pnl"] == 5.67


# ─── Issue #1745 ──────────────────────────────────────────────────────────────
# Fill/summary producers must emit plain text; command replies must be routed
# through send_message() with parse_mode=None (explicit plain-text delivery).

def test_trade_fill_escapes_dynamic_text_once_at_final_telegram_boundary(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia import notifications
    from aletheia.telegram.bot import TelegramBot

    cfg = load_config()
    cfg.telegram.enabled = True
    cfg.telegram.notify_trade_fills = True
    save_config(cfg)
    delivered = []

    def configure(bot):
        bot.allowed_chat_ids = [999]
        return True

    async def send_message(bot, chat_id, text, *, parse_mode):
        delivered.append((chat_id, text, parse_mode))

    monkeypatch.setattr(notifications, "_is_duplicate", lambda message: False)
    monkeypatch.setattr(notifications, "_is_dnd", lambda: False)
    monkeypatch.setattr(TelegramBot, "configure", configure)
    monkeypatch.setattr(TelegramBot, "send_message", send_message)
    monkeypatch.setattr(notifications, "_send_webhook", lambda *args, **kwargs: None)

    notifications.notify_trade_fill(
        symbol="BTC&USDT<x>",
        direction="long",
        quantity=0.01,
        fill_price=60000.0,
        position_id="pos-1745-fill",
        execution_mode="paper_internal",
    )

    assert len(delivered) == 1
    chat_id, text, parse_mode = delivered[0]
    assert chat_id == 999
    assert parse_mode == "HTML"
    assert text.startswith("⚠️ <b>Aletheia:</b> Trade Fill [Paper Internal]")
    assert "BTC&amp;USDT&lt;x&gt;" in text
    assert "&amp;amp;" not in text
    assert "<code>" not in text


def test_daily_summary_reaches_final_telegram_boundary_without_literal_markup(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia import notifications
    import aletheia.portfolio.pnl as pnl
    from aletheia.telegram.bot import TelegramBot

    cfg = load_config()
    cfg.telegram.enabled = True
    cfg.telegram.notify_daily_summary = True
    save_config(cfg)
    delivered = []

    def configure(bot):
        bot.allowed_chat_ids = [999]
        return True

    async def send_message(bot, chat_id, text, *, parse_mode):
        delivered.append((chat_id, text, parse_mode))

    monkeypatch.setattr(notifications, "_is_duplicate", lambda message: False)
    monkeypatch.setattr(notifications, "_is_dnd", lambda: False)
    monkeypatch.setattr(TelegramBot, "configure", configure)
    monkeypatch.setattr(TelegramBot, "send_message", send_message)
    monkeypatch.setattr(notifications, "_send_webhook", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pnl,
        "today_realized_pnl",
        lambda: pytest.fail("must not read unscoped realized PnL"),
    )
    monkeypatch.setattr(
        pnl,
        "compute_portfolio",
        lambda: {
            "today_realized_pnl": 9.99,
            "today_realized_pnl_complete": True,
            "unrealized_pnl": 1.23,
            "positions_count": 3,
            "total_value_usdt": 500.0,
        },
    )

    notifications.notify_daily_summary()

    assert len(delivered) == 1
    chat_id, text, parse_mode = delivered[0]
    assert chat_id == 999
    assert parse_mode == "HTML"
    assert text.startswith("⚠️ <b>Aletheia:</b> Daily Summary")
    assert "<code>" not in text
    assert "Realized: +9.99 USDT" in text


def test_daily_summary_webhook_preserves_incomplete_mode_scoped_realized_pnl(
    tmp_path,
    monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia import notifications
    from aletheia.config.loader import load_config, save_config
    import aletheia.portfolio.pnl as pnl

    cfg = load_config()
    cfg.telegram.enabled = False
    cfg.webhook.url = "http://example.test/hook"
    cfg.webhook.enabled = True
    cfg.webhook.notify_daily_summary = True
    save_config(cfg)
    delivered = []
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
            "positions_count": 1,
            "total_value_usdt": 95.0,
        },
    )
    monkeypatch.setattr(
        notifications,
        "_send_webhook",
        lambda payload, *, event_type: delivered.append((payload, event_type)),
    )

    notifications.notify_daily_summary()

    [(payload, event_type)] = delivered
    assert event_type == "daily_summary"
    assert payload["realized_pnl"] is None
    assert payload["realized_pnl_complete"] is False
    assert payload["unrealized_pnl"] == -5.0


@pytest.mark.anyio
async def test_command_reply_plain_text(tmp_path, monkeypatch):
    """Issue #1745: _handle_update() routes replies through send_message(parse_mode=None)
    and command output contains no literal HTML tags."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    import aletheia.telegram.bot as bot_module
    from aletheia.telegram.bot import TelegramBot

    cfg = load_config()
    cfg.telegram.enabled = True
    cfg.telegram.allowed_chat_ids = [999]
    save_config(cfg)
    # Patch get_secret at its import site inside aletheia.telegram.bot
    monkeypatch.setattr(bot_module, "get_secret",
                        lambda key: "fake-token" if "telegram" in key else None)

    delivered: list[tuple[int, str, str | None]] = []

    async def fake_send_message(self, chat_id: int, text: str, *, parse_mode="HTML") -> None:
        delivered.append((chat_id, text, parse_mode))

    monkeypatch.setattr(TelegramBot, "send_message", fake_send_message)

    bot = TelegramBot()
    bot.configure()

    class _NoPost:
        async def post(self, url, *, json=None):
            raise AssertionError("raw client.post must not be called for command replies")

    for cmd in ("/status", "/help", "/positions", "/pnl"):
        delivered.clear()
        update = {"message": {"chat": {"id": 999}, "text": cmd}}
        await bot._handle_update(update, _NoPost())
        assert len(delivered) == 1, f"{cmd}: must route through send_message"
        _, text, parse_mode = delivered[0]
        assert parse_mode is None, f"{cmd}: must use parse_mode=None (plain text)"
        assert "<b>" not in text and "<code>" not in text, \
            f"{cmd}: response must contain no HTML markup"
