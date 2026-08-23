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
    cfg.webhook.enabled = True
    cfg.webhook.url = "http://example.test/hook"
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
    cfg.webhook.enabled = True
    cfg.webhook.url = "http://example.test/hook"
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
    cfg.webhook.enabled = True
    cfg.webhook.url = "http://example.test/hook"
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
    cfg.webhook.enabled = webhook_enabled
    cfg.webhook.url = "http://example.test/hook"
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
        monkeypatch.setattr(pnl, "today_realized_pnl", lambda: 12.34)
        monkeypatch.setattr(
            pnl,
            "compute_portfolio",
            lambda: {"unrealized_pnl": 5.67, "positions_count": 2, "total_value_usdt": 123.45},
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
