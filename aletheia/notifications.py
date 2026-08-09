"""Best-effort operator notifications. Never raises.

Notifications are deliberately advisory only: they must never block the gateway,
and they must never mutate trading state. The sink fan-outs to local audit/error
streams, Windows toast, and Telegram when configured.
"""

import asyncio
import hashlib
import logging
import os
import platform
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape

logger = logging.getLogger("aletheia.notifications")

_IS_WINDOWS = platform.system() == "Windows"

# Message dedup: suppress identical messages within this window
_DEDUP_WINDOW_SEC = 120.0
_recent_messages: dict[str, float] = {}  # hash → monotonic timestamp
_DEDUP_MAX_ENTRIES = 200
# Unconditional periodic cleanup counter: every _DEDUP_CLEANUP_INTERVAL calls we
# sweep all entries for expiry regardless of dict size.  This bounds memory even
# during an alert storm where >200 unique messages arrive within the 120s window
# (the old size-gated cleanup could never trigger in that case — M2 fix).
_DEDUP_CLEANUP_INTERVAL = 50
_dedup_call_count = 0


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


def _is_dnd() -> bool:
    """Return True if current local hour is within do-not-disturb window."""
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        if not cfg.telegram.dnd_enabled:
            return False
        h = datetime.now().hour
        start, end = cfg.telegram.dnd_start_hour, cfg.telegram.dnd_end_hour
        if start <= end:
            return start <= h < end
        return h >= start or h < end
    except Exception:
        return False


def _is_duplicate(message: str) -> bool:
    """Return True if this message was sent within the dedup window."""
    global _dedup_call_count
    now = time.monotonic()
    key = hashlib.md5(message.encode(), usedforsecurity=False).hexdigest()
    # Unconditional periodic cleanup: every _DEDUP_CLEANUP_INTERVAL calls, sweep
    # ALL entries for expiry regardless of dict size.  This bounds memory even
    # during an alert storm where >_DEDUP_MAX_ENTRIES unique messages arrive
    # within the 120s window (the old size-gated cleanup could never trigger in
    # that case).  The cheap counter increment costs nothing on the hot path.
    _dedup_call_count += 1
    if _dedup_call_count >= _DEDUP_CLEANUP_INTERVAL:
        _dedup_call_count = 0
        stale = [k for k, ts in _recent_messages.items() if now - ts > _DEDUP_WINDOW_SEC]
        for k in stale:
            del _recent_messages[k]
    last_sent = _recent_messages.get(key)
    if last_sent is not None and now - last_sent < _DEDUP_WINDOW_SEC:
        return True
    _recent_messages[key] = now
    return False


def _send_telegram_alert(message: str, *, level: str = "warning",
                         _require_alert_switch: bool = True) -> None:
    """Send a Telegram alert to allowed chats when configured. Never raises.

    P1 alerts (info level) are suppressed during DND hours.
    P0 alerts (warning/error/critical) always go through.
    Duplicate messages within 120s are suppressed.

    _require_alert_switch: when True (default), obeys cfg.health.alert_via_telegram
    so health-alert topics are gated by that toggle.  Non-alert topics (trade fills,
    daily summary) pass False so that the health-alert setting does not accidentally
    suppress trade or summary notifications (Issue #407).
    """
    try:
        if _is_duplicate(message):
            return
        from aletheia.config.loader import load_config
        cfg = load_config()
        if not cfg.telegram.enabled:
            return
        # Issue #407: alert_via_telegram gates *health/operator alerts only*.
        # Trade fills and daily summaries have their own per-category switches and
        # must not be blocked by the health-alert toggle.
        if _require_alert_switch and not cfg.health.alert_via_telegram:
            return
        if level == "info" and _is_dnd():
            return
        from aletheia.telegram.bot import TelegramBot
        bot = TelegramBot()
        if not bot.configure():
            return

        safe_msg = escape(message, quote=False)

        async def _send_all():
            for chat_id in bot.allowed_chat_ids:
                try:
                    await bot.send_message(chat_id, f"⚠️ <b>Aletheia:</b> {safe_msg}")
                except Exception:
                    pass

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_send_all())
        else:
            # Non-blocking: schedule on the event loop without waiting.
            # Telegram alerts are advisory — no need to block the caller.
            asyncio.ensure_future(_send_all(), loop=loop)
    except Exception as e:
        logger.warning("Telegram notification failed (non-critical): %s", e)


def _send_webhook(payload: dict, *, event_type: str = "alert") -> None:
    """POST JSON payload to configured webhook URL. Never raises."""
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        if not cfg.webhook.enabled or not cfg.webhook.url:
            return
        # Issue #406: honour the per-category delivery toggles.
        # alert / operator_notification → notify_alerts
        # trade_fill → notify_trade_fills
        # daily_summary → notify_daily_summary
        if event_type in ("alert", "operator_notification") and not cfg.webhook.notify_alerts:
            return
        if event_type == "trade_fill" and not cfg.webhook.notify_trade_fills:
            return
        if event_type == "daily_summary" and not cfg.webhook.notify_daily_summary:
            return
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
        # Always read+close the response to avoid leaking sockets / file descriptors
        # on every notification (trade fills, alerts, daily summary).
        from aletheia.net import build_urllib_opener
        resp = build_urllib_opener().open(req, timeout=timeout)
        try:
            resp.read()
        finally:
            resp.close()
    except Exception as e:
        logger.debug("Webhook notification failed (non-critical): %s", e)


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
            pass

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
            pass

    if windows:
        try:
            from aletheia.config.loader import load_config
            cfg = load_config()
            if getattr(cfg.health, "alert_via_windows_toast", True):
                send_windows_toast(clean_title, clean_message)
        except Exception:
            pass

    if telegram:
        _send_telegram_alert(f"{clean_title}: {clean_message}", level=level)

    _send_webhook({
        "title": clean_title,
        "message": clean_message,
        "level": level,
        "source": source,
    }, event_type="alert")


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
    """Push trade fill notification to Telegram. No-op if disabled or DND.

    Best-effort only — never raises, never blocks the execution path.
    """
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        if not cfg.telegram.enabled or not cfg.telegram.notify_trade_fills:
            return
    except Exception:
        return

    dir_label = "LONG" if direction in ("long", "buy") else "SHORT"
    mode_label = execution_mode.replace("_", " ").title()
    safe_symbol = escape(symbol, quote=False)
    lines = [f"<b>Trade Fill [{mode_label}]</b>",
             f"{dir_label} {safe_symbol}",
             f"<code>Qty: {quantity}  @ {fill_price:.4f}</code>"]
    if stop_price:
        lines.append(f"<code>SL: {stop_price:.4f}</code>")
    if take_profit_price:
        lines.append(f"<code>TP: {take_profit_price:.4f}</code>")
    lines.append(f"<code>pos/{position_id[:8]}</code>")
    msg = "\n".join(lines)
    # Issue #407: trade fills are not "alerts" — bypass the health-alert toggle.
    _send_telegram_alert(msg, level="info", _require_alert_switch=False)
    _send_webhook({
        "symbol": symbol,
        "direction": dir_label,
        "quantity": quantity,
        "fill_price": fill_price,
        "position_id": position_id,
        "execution_mode": execution_mode,
        "stop_price": stop_price,
        "take_profit_price": take_profit_price,
    }, event_type="trade_fill")


def notify_daily_summary() -> None:
    """Push daily PnL summary to Telegram at scheduled time. Best-effort."""
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        if not cfg.telegram.enabled or not cfg.telegram.notify_daily_summary:
            return
    except Exception:
        return

    try:
        from aletheia.portfolio.pnl import today_realized_pnl, compute_portfolio
        realized = today_realized_pnl()
        portfolio = compute_portfolio()
        unrealized = float(portfolio.get("unrealized_pnl") or 0.0)
        open_count = portfolio.get("positions_count", 0)
        total = portfolio.get("total_value_usdt", 0.0)
        realized_text = (
            "unavailable"
            if realized is None
            else f"{'+' if realized >= 0 else ''}{realized:.2f} USDT"
        )
        lines = [
            "<b>Daily Summary</b>",
            f"Realized: <code>{realized_text}</code>",
            f"Unrealized: <code>{unrealized:+.2f} USDT</code>",
            f"Open positions: {open_count}",
            f"Portfolio value: <code>{total:.2f} USDT</code>",
        ]
        # Issue #407: daily summary is not an "alert" — bypass the health-alert toggle.
        _send_telegram_alert("\n".join(lines), level="info", _require_alert_switch=False)
        _send_webhook({
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "open_positions": open_count,
            "total_value_usdt": total,
            "realized_pnl_complete": realized is not None,
        }, event_type="daily_summary")
    except Exception as e:
        logger.warning("notify_daily_summary failed: %s", e)
