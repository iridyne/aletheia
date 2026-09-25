import asyncio
import sqlite3
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.error import HTTPError

import pytest

from aletheia.storage.migrations import run_migrations
from aletheia.storage.sqlite import reset_db
from extensions.telegram import delivery as telegram_delivery


def setup_env(tmp_path, monkeypatch):
    from aletheia import notifications
    monkeypatch.setattr(notifications, "_telegram_sender", telegram_delivery.send_telegram_alert)
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
        fill_price=60000.0, position_id="pos-test-1", execution_mode="bitget_demo",
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
    outcome = notifications._send_webhook({"title": "alert", "message": "test"}, event_type="alert")
    # No AssertionError means delivery was correctly suppressed
    assert outcome["status"] == "not_attempted" and outcome["reason"] == "category_suppressed", (
        f"alert delivery must be suppressed when notify_alerts=false (got {outcome})"
    )


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

    outcome = notifications._send_webhook({"symbol": "BTC/USDT"}, event_type="trade_fill")
    assert outcome["status"] == "not_attempted" and outcome["reason"] == "category_suppressed", (
        f"trade-fill delivery must be suppressed when notify_trade_fills=false (got {outcome})"
    )


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

    outcome = notifications._send_webhook({"realized_pnl": 10.0}, event_type="daily_summary")
    assert outcome["status"] == "not_attempted" and outcome["reason"] == "category_suppressed", (
        f"daily-summary delivery must be suppressed when notify_daily_summary=false (got {outcome})"
    )


def test_webhook_success_records_sanitized_delivery_outcome(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.files.jsonl import read_jsonl
    from aletheia import notifications

    cfg = load_config()
    cfg.webhook.url = "https://receiver.example/path-secret?query-secret"
    cfg.webhook.secret = "hmac-secret"
    cfg.webhook.enabled = True
    save_config(cfg)

    class Response:
        status = 204

    class Opener:
        def open(self, request, *, timeout):
            return Response()

    import aletheia.net as net
    monkeypatch.setattr(net, "build_urllib_opener", lambda: Opener())

    outcome = notifications._send_webhook(
        {"title": "safe"},
        event_type="alert",
        source="health",
        entity_type="gateway",
        entity_id="gateway",
    )

    assert outcome["status"] == "delivered"
    assert outcome["receiver_host"] == "receiver.example"
    assert outcome["http_status"] == 204
    delivery = [e for e in read_jsonl("audit.jsonl") if e.get("event_type") == "webhook_delivery"][-1]
    serialized = json.dumps(delivery)
    assert "path-secret" not in serialized
    assert "query-secret" not in serialized
    assert "hmac-secret" not in serialized


@pytest.mark.parametrize(
    "error",
    [
        HTTPError("https://receiver.example/path-secret", 500, "body-secret", {}, None),
        TimeoutError("timeout-secret"),
        OSError("dns-secret"),
    ],
)
def test_webhook_failure_records_only_bounded_safe_fields(tmp_path, monkeypatch, caplog, error):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.files.jsonl import read_jsonl
    from aletheia import notifications

    cfg = load_config()
    cfg.webhook.url = "https://receiver.example/path-secret?query-secret"
    cfg.webhook.secret = "hmac-secret"
    cfg.webhook.enabled = True
    save_config(cfg)

    class Opener:
        def open(self, request, *, timeout):
            raise error

    import aletheia.net as net
    monkeypatch.setattr(net, "build_urllib_opener", lambda: Opener())

    outcome = notifications._send_webhook(
        {"message": "payload-secret"},
        event_type="alert",
        source="health",
        entity_type="gateway",
        entity_id="gateway",
    )

    assert outcome["status"] == "failed"
    assert outcome["error_class"] == type(error).__name__
    records = read_jsonl("audit.jsonl") + read_jsonl("errors.jsonl")
    serialized = json.dumps(records) + caplog.text
    for secret in ("path-secret", "query-secret", "hmac-secret", "payload-secret", "body-secret", "timeout-secret", "dns-secret"):
        assert secret not in serialized
    error_record = [
        record
        for record in read_jsonl("errors.jsonl")
        if record.get("event_type") == "webhook_delivery_failed"
    ][-1]
    assert error_record["source"] == "webhook_delivery"
    assert error_record["producer_source"] == "health"


def test_webhook_status_uses_attempt_time_not_insert_order(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia import notifications
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import AuditEvent

    def outcome(attempted_at, entity_id):
        return {
            "status": "delivered",
            "attempted_at": attempted_at,
            "receiver_host": "receiver.example",
            "event_type": "alert",
            "producer_source": "health",
            "entity_type": "gateway",
            "entity_id": entity_id,
            "http_status": 204,
            "error_class": None,
            "latency_ms": 1,
        }

    with repo.transaction(immediate=True):
        for attempted_at, entity_id in (
            ("2026-09-02T08:01:00+00:00", "newer"),
            ("2026-09-02T08:00:00+00:00", "older-finished-last"),
        ):
            event = AuditEvent(
                actor="system",
                source="webhook_delivery",
                event_type="webhook_delivery",
                after_state=json.dumps(outcome(attempted_at, entity_id)),
            )
            repo.insert_audit_event(event)
            repo.get_db().execute(
                "UPDATE audit_events SET jsonl_mirror_status='mirrored' WHERE id=?",
                (event.id,),
            )
    monkeypatch.setattr(
        repo, "reconcile_audit_event_outbox", lambda *_args, **_kwargs: True
    )

    assert notifications.webhook_delivery_status()["entity_id"] == "newer"


def test_webhook_status_waits_for_audit_mirror_and_retries(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia import notifications
    from aletheia.config.loader import load_config, save_config
    from aletheia.files import jsonl

    cfg = load_config()
    cfg.webhook.url = "https://receiver.example/hook"
    cfg.webhook.enabled = True
    save_config(cfg)

    class Response:
        status = 204

    class Opener:
        def open(self, request, *, timeout):
            return Response()

    import aletheia.net as net
    monkeypatch.setattr(net, "build_urllib_opener", lambda: Opener())
    monkeypatch.setattr(jsonl, "write_audits_deduplicated", lambda *_args, **_kwargs: set())

    notifications._send_webhook({"title": "safe"}, event_type="alert")
    assert notifications.webhook_delivery_status() is None

    monkeypatch.setattr(
        jsonl,
        "write_audits_deduplicated",
        lambda events, **_kwargs: {event["id"] for event in events},
    )
    assert notifications.webhook_delivery_status()["status"] == "delivered"


def test_webhook_disabled_and_suppressed_are_not_attempted(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.files.jsonl import read_jsonl
    from aletheia import notifications

    disabled = notifications._send_webhook({"title": "disabled"}, event_type="alert")
    assert disabled == {"status": "not_attempted", "reason": "disabled"}

    cfg = load_config()
    cfg.webhook.url = "https://receiver.example/hook"
    cfg.webhook.enabled = True
    cfg.webhook.notify_alerts = False
    save_config(cfg)
    suppressed = notifications._send_webhook({"title": "suppressed"}, event_type="alert")
    assert suppressed == {"status": "not_attempted", "reason": "category_suppressed"}
    assert not [e for e in read_jsonl("audit.jsonl") if e.get("event_type") == "webhook_delivery"]


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
            execution_mode="bitget_demo",
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
    from extensions.telegram.bot import TelegramBot

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

    monkeypatch.setattr(telegram_delivery, "_reserve_delivery", lambda message, recipient: True)
    monkeypatch.setattr(telegram_delivery, "_is_dnd", lambda: False)
    monkeypatch.setattr(TelegramBot, "configure", configure)
    monkeypatch.setattr(TelegramBot, "send_message", send_message)
    monkeypatch.setattr(notifications, "_send_webhook", lambda *args, **kwargs: None)

    notifications.notify_trade_fill(
        symbol="BTC&USDT<x>",
        direction="long",
        quantity=0.01,
        fill_price=60000.0,
        position_id="pos-1745-fill",
        execution_mode="bitget_demo",
    )

    assert len(delivered) == 1
    chat_id, text, parse_mode = delivered[0]
    assert chat_id == 999
    assert parse_mode == "HTML"
    assert text.startswith("⚠️ <b>Aletheia:</b> Trade Fill [Bitget Demo]")
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
    from extensions.telegram.bot import TelegramBot

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

    monkeypatch.setattr(telegram_delivery, "_reserve_delivery", lambda message, recipient: True)
    monkeypatch.setattr(telegram_delivery, "_is_dnd", lambda: False)
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
        lambda payload, **kwargs: delivered.append((payload, kwargs)),
    )

    notifications.notify_daily_summary()

    [(payload, delivery_context)] = delivered
    assert delivery_context == {
        "event_type": "daily_summary",
        "source": "portfolio",
        "entity_type": "daily_summary",
        "entity_id": datetime.now(timezone.utc).date().isoformat(),
    }
    assert payload["realized_pnl"] is None
    assert payload["realized_pnl_complete"] is False
    assert payload["unrealized_pnl"] == -5.0


def test_telegram_fanout_retries_only_failed_recipient(tmp_path, monkeypatch, caplog):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia import notifications
    from extensions.telegram.bot import TelegramBot

    cfg = load_config()
    cfg.telegram.enabled = True
    cfg.health.alert_via_telegram = True
    save_config(cfg)
    telegram_delivery._recent_messages.clear()
    telegram_delivery._inflight_messages.clear()
    attempts: list[int] = []

    def configure(bot):
        bot.allowed_chat_ids = [1, 2]
        return True

    async def send_message(_bot, chat_id, _text, *, parse_mode):
        attempts.append(chat_id)
        if chat_id == 1 and attempts.count(1) == 1:
            raise RuntimeError("rejected")

    monkeypatch.setattr(telegram_delivery, "_is_dnd", lambda: False)
    monkeypatch.setattr(TelegramBot, "configure", configure)
    monkeypatch.setattr(TelegramBot, "send_message", send_message)

    notifications._send_telegram_alert("mixed result")
    notifications._send_telegram_alert("mixed result")

    assert attempts == [1, 2, 1]
    assert "Telegram delivery failed for chat_id=1: RuntimeError" in caplog.text


def test_telegram_fanout_reserves_concurrent_delivery(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia import notifications
    from extensions.telegram.bot import TelegramBot

    cfg = load_config()
    cfg.telegram.enabled = True
    cfg.health.alert_via_telegram = True
    save_config(cfg)
    telegram_delivery._recent_messages.clear()
    telegram_delivery._inflight_messages.clear()
    entered = threading.Event()
    release = threading.Event()
    attempts = []

    def configure(bot):
        bot.allowed_chat_ids = [1]
        return True

    async def send_message(_bot, chat_id, _text, *, parse_mode):
        attempts.append(chat_id)
        entered.set()
        await asyncio.to_thread(release.wait)

    monkeypatch.setattr(telegram_delivery, "_is_dnd", lambda: False)
    monkeypatch.setattr(TelegramBot, "configure", configure)
    monkeypatch.setattr(TelegramBot, "send_message", send_message)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(notifications._send_telegram_alert, "same message")
        assert entered.wait(timeout=5)
        second = pool.submit(notifications._send_telegram_alert, "same message")
        second.result(timeout=5)
        release.set()
        first.result(timeout=5)

    assert attempts == [1]


@pytest.mark.anyio
async def test_command_reply_plain_text(tmp_path, monkeypatch):
    """Issue #1745: _handle_update() routes replies through send_message(parse_mode=None)
    and command output contains no literal HTML tags."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    import extensions.telegram.bot as bot_module
    from extensions.telegram.bot import TelegramBot

    cfg = load_config()
    cfg.telegram.enabled = True
    cfg.telegram.allowed_chat_ids = [999]
    save_config(cfg)
    # Patch get_secret at its import site inside extensions.telegram.bot
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
