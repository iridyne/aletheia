"""Best-effort operator notifications. Never raises.

Notifications are deliberately advisory only: they must never block the gateway,
and they must never mutate trading state. The sink fan-outs to local audit/error
streams, Windows toast, Telegram, and Webhook when configured.
"""

import logging
import os
import platform
import sqlite3
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlsplit

logger = logging.getLogger("aletheia.notifications")

_IS_WINDOWS = platform.system() == "Windows"

_telegram_sender: Callable[..., None] | None = None


def configure_telegram_sender(sender: Callable[..., None] | None) -> None:
    """Bind optional delivery at the existing CLI/Web composition roots."""
    global _telegram_sender
    _telegram_sender = sender


def send_windows_toast(title: str, message: str) -> None:
    """Send a Windows toast notification via PowerShell. No-op on non-Windows."""
    if not _IS_WINDOWS or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        safe_title = escape(title, quote=False).replace("'", "''")
        safe_message = escape(message, quote=False).replace("'", "''")
        script = (
            f"$ErrorActionPreference='SilentlyContinue'; "
            f"if (Get-Module -ListAvailable -Name BurntToast) {{ "
            f"  Import-Module BurntToast; "
            f"  New-BurntToastNotification -Text '{safe_title}','{safe_message}' "
            f"}} else {{ "
            f"  [void][Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]; "
            f"  [void][Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]; "
            f"  $xml = [Windows.Data.Xml.Dom.XmlDocument]::new(); "
            f"  $xml.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{safe_title}</text><text>{safe_message}</text></binding></visual></toast>'); "
            f"  $toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
            f"  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Aletheia').Show($toast) "
            f"}}"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        logger.debug("Toast notification failed (non-critical): %s", e)


def _send_telegram_alert(message: str, *, level: str = "warning",
                         _require_alert_switch: bool = True) -> None:
    """Deliver advisory text through the bound adapter; absence stays inert."""
    if _telegram_sender is None:
        return
    try:
        _telegram_sender(message, level=level, _require_alert_switch=_require_alert_switch)
    except Exception as exc:
        logger.warning("Telegram notification failed (non-critical): %s", type(exc).__name__)


def _record_webhook_outcome(outcome: dict) -> None:
    try:
        import json

        from aletheia.storage import repositories as repo
        from aletheia.storage.models import AuditEvent

        event = AuditEvent(
            actor="system",
            source="webhook_delivery",
            event_type="webhook_delivery",
            entity_type=outcome.get("entity_type") or "notification",
            entity_id=outcome.get("entity_id") or outcome["event_type"],
            after_state=json.dumps(outcome, ensure_ascii=False),
            message=(
                f"Webhook delivery {outcome['status']} "
                f"for {outcome['receiver_host']}"
            ),
        )
        with repo.transaction(immediate=True):
            repo.insert_audit_event_outbox(event)
        repo.reconcile_audit_event_outbox(("webhook_delivery",))
    except Exception as exc:
        logger.warning(
            "Webhook delivery audit persistence failed: %s",
            type(exc).__name__,
        )
    if outcome["status"] == "failed":
        try:
            from aletheia.files.jsonl import write_error

            write_error({
                **outcome,
                "source": "webhook_delivery",
                "event_type": "webhook_delivery_failed",
            })
        except Exception:
            logger.warning("notifications: webhook delivery audit write failed", exc_info=True)


def webhook_delivery_status() -> dict | None:
    """Return the newest validated delivery attempt for read-only projections."""
    try:
        import json

        from aletheia.storage import repositories as repo

        repo.reconcile_audit_event_outbox(("webhook_delivery",))
        row = repo.latest_mirrored_webhook_delivery_event()
        if not row:
            return None
        outcome = json.loads(row.get("after_state") or "null")
        if not isinstance(outcome, dict) or outcome.get("status") not in {"delivered", "failed"}:
            return None
        receiver_host = outcome.get("receiver_host")
        if (
            not isinstance(receiver_host, str)
            or not receiver_host
            or len(receiver_host) > 253
            or any(char in receiver_host for char in "/?#@")
        ):
            return None
        attempted_at = datetime.fromisoformat(str(outcome["attempted_at"]))
        if attempted_at.tzinfo is None:
            return None
        return {
            "status": outcome["status"],
            "attempted_at": attempted_at.isoformat(),
            "receiver_host": receiver_host,
            "event_type": str(outcome["event_type"]),
            "entity_type": str(outcome["entity_type"]) if outcome.get("entity_type") else None,
            "entity_id": str(outcome["entity_id"]) if outcome.get("entity_id") else None,
            "http_status": outcome.get("http_status") if isinstance(outcome.get("http_status"), int) else None,
            "error_class": str(outcome["error_class"]) if outcome.get("error_class") else None,
            "latency_ms": max(0, int(outcome["latency_ms"])),
        }
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        OSError,
        RuntimeError,
        sqlite3.Error,
    ):
        return None


def _send_webhook(
    payload: dict,
    *,
    event_type: str = "alert",
    source: str = "system",
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> dict:
    """POST JSON payload and return a sanitized delivery outcome. Never raises."""
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
    except Exception:
        return {"status": "not_attempted", "reason": "config_unavailable"}
    if not cfg.webhook.enabled or not cfg.webhook.url:
        return {"status": "not_attempted", "reason": "disabled"}
    if event_type in ("alert", "operator_notification") and not cfg.webhook.notify_alerts:
        return {"status": "not_attempted", "reason": "category_suppressed"}
    if event_type == "trade_fill" and not cfg.webhook.notify_trade_fills:
        return {"status": "not_attempted", "reason": "category_suppressed"}
    if event_type == "daily_summary" and not cfg.webhook.notify_daily_summary:
        return {"status": "not_attempted", "reason": "category_suppressed"}

    attempted_at = datetime.now(timezone.utc).isoformat()
    started_at = time.monotonic()
    receiver_host = (urlsplit(cfg.webhook.url).hostname or "unavailable")[:253]
    http_status: int | None = None
    error_class: str | None = None
    try:
        # Issue #406: honour the per-category delivery toggles.
        # alert / operator_notification → notify_alerts
        # trade_fill → notify_trade_fills
        # daily_summary → notify_daily_summary
        import hashlib
        import hmac
        import json
        import urllib.request

        body = json.dumps({"event": event_type, **payload}).encode()
        req = urllib.request.Request(
            cfg.webhook.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if cfg.webhook.secret:
            sig = hmac.new(cfg.webhook.secret.encode(), body, hashlib.sha256).hexdigest()
            req.add_header("X-Aletheia-Signature", sig)

        timeout = cfg.webhook.timeout_sec or 10.0
        # Route through the managed proxy (aletheia.net) rather than a raw urlopen,
        # so the outbound webhook honours the configured proxy / enforce policy.
        from aletheia.net import build_urllib_opener
        response = build_urllib_opener().open(req, timeout=timeout)
        status = getattr(response, "status", None)
        if isinstance(status, int) and 100 <= status <= 599:
            http_status = status
    except Exception as e:
        error_class = type(e).__name__[:120]
        status = getattr(e, "code", None)
        if isinstance(status, int) and 100 <= status <= 599:
            http_status = status

    outcome = {
        "status": "failed" if error_class else "delivered",
        "attempted_at": attempted_at,
        "receiver_host": receiver_host,
        "event_type": event_type,
        "producer_source": source[:120],
        "entity_type": entity_type[:120] if entity_type else None,
        "entity_id": entity_id[:200] if entity_id else None,
        "http_status": http_status,
        "error_class": error_class,
        "latency_ms": max(0, round((time.monotonic() - started_at) * 1000)),
    }
    _record_webhook_outcome(outcome)
    if error_class:
        logger.warning("Webhook delivery failed for %s: %s", receiver_host, error_class)
    return outcome


def notify_operator(
    title: str,
    message: str,
    *,
    level: str = "warning",
    source: str = "system",
    event_type: str | None = "operator_notification",
    entity_type: str = "system",
    entity_id: str = "system",
    audit: bool = True,
    error_log: bool = True,
    windows: bool = True,
    telegram: bool = True,
) -> None:
    """Best-effort fan-out notification for operator-visible failures.

    This is intentionally small and synchronous: callers use it only on degraded
    transitions, supervisor failures, and startup faults. Every branch catches
    its own errors so notification failures cannot take down the gateway.
    """
    clean_title = str(title)[:120]
    clean_message = str(message)[:1000]

    if audit and event_type:
        try:
            from aletheia.files.jsonl import write_audit
            write_audit({
                "actor": "system",
                "source": source,
                "event_type": event_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "level": level,
                "message": f"{clean_title}: {clean_message}",
            })
        except Exception:
            logger.warning("notifications: notification audit write failed", exc_info=True)

    if error_log:
        try:
            from aletheia.files.jsonl import write_error
            write_error({
                "source": source,
                "level": level,
                "title": clean_title,
                "message": clean_message,
            })
        except Exception:
            logger.warning("notifications: notification audit write failed", exc_info=True)

    if windows:
        try:
            from aletheia.config.loader import load_config
            cfg = load_config()
            if getattr(cfg.health, "alert_via_windows_toast", True):
                send_windows_toast(clean_title, clean_message)
        except Exception:
            pass  # Best-effort OS notification; failure must not affect runtime

    if telegram:
        _send_telegram_alert(f"{clean_title}: {clean_message}", level=level)

    _send_webhook({
        "title": clean_title,
        "message": clean_message,
        "level": level,
        "source": source,
    }, event_type="alert", source=source, entity_type=entity_type, entity_id=entity_id)


def notify_trade_fill(
    symbol: str,
    direction: str,
    quantity: float,
    fill_price: float,
    position_id: str,
    execution_mode: str,
    *,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
) -> None:
    """Push trade fill notification to Telegram and Webhook when configured.

    Best-effort only — never raises, never blocks the execution path.
    """
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        # Issue #1226: Telegram and Webhook are independent notification channels.
        send_telegram = cfg.telegram.enabled and cfg.telegram.notify_trade_fills
        send_webhook = (
            cfg.webhook.enabled
            and bool(cfg.webhook.url)
            and cfg.webhook.notify_trade_fills
        )
    except Exception:
        return

    dir_label = "LONG" if direction in ("long", "buy") else "SHORT"
    mode_label = execution_mode.replace("_", " ").title()
    # Issue #1745: build plain text; the shared sink applies a single escape before
    # sending with parse_mode="HTML", so no per-field escaping is needed here.
    lines = [f"Trade Fill [{mode_label}]",
             f"{dir_label} {symbol}",
             f"Qty: {quantity}  @ {fill_price:.4f}"]
    if stop_price:
        lines.append(f"SL: {stop_price:.4f}")
    if take_profit_price:
        lines.append(f"TP: {take_profit_price:.4f}")
    lines.append(f"pos/{position_id[:8]}")
    msg = "\n".join(lines)
    if send_telegram:
        # Issue #407: trade fills are not "alerts" — bypass the health-alert toggle.
        _send_telegram_alert(msg, level="info", _require_alert_switch=False)
    if send_webhook:
        _send_webhook({
            "symbol": symbol,
            "direction": dir_label,
            "quantity": quantity,
            "fill_price": fill_price,
            "position_id": position_id,
            "execution_mode": execution_mode,
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
        }, event_type="trade_fill", source="execution_gateway", entity_type="position", entity_id=position_id)


def notify_daily_summary() -> None:
    """Push daily PnL summary to Telegram and Webhook when configured."""
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        # Issue #1226: Telegram and Webhook are independent notification channels.
        send_telegram = cfg.telegram.enabled and cfg.telegram.notify_daily_summary
        send_webhook = (
            cfg.webhook.enabled
            and bool(cfg.webhook.url)
            and cfg.webhook.notify_daily_summary
        )
    except Exception:
        return

    try:
        from aletheia.portfolio.pnl import compute_portfolio
        portfolio = compute_portfolio()
        realized_complete = portfolio.get("today_realized_pnl_complete") is True
        realized = (
            portfolio.get("today_realized_pnl") if realized_complete else None
        )
        unrealized = float(portfolio.get("unrealized_pnl") or 0.0)
        open_count = portfolio.get("positions_count", 0)
        total = portfolio.get("total_value_usdt", 0.0)
        realized_text = (
            "unavailable"
            if realized is None
            else f"{'+' if realized >= 0 else ''}{realized:.2f} USDT"
        )
        # Issue #1745: build plain text; the shared sink escapes before sending.
        lines = [
            "Daily Summary",
            f"Realized: {realized_text}",
            f"Unrealized: {unrealized:+.2f} USDT",
            f"Open positions: {open_count}",
            f"Portfolio value: {total:.2f} USDT",
        ]
        if send_telegram:
            # Issue #407: daily summary is not an "alert" — bypass the health-alert toggle.
            _send_telegram_alert("\n".join(lines), level="info", _require_alert_switch=False)
        if send_webhook:
            _send_webhook({
                "realized_pnl": realized,
                "unrealized_pnl": unrealized,
                "open_positions": open_count,
                "total_value_usdt": total,
                "realized_pnl_complete": realized_complete,
            }, event_type="daily_summary", source="portfolio", entity_type="daily_summary",
                entity_id=datetime.now(timezone.utc).date().isoformat())
    except Exception as e:
        logger.warning("notify_daily_summary failed: %s", e)
