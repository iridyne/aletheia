"""Best-effort operator notifications. Never raises.

Notifications are deliberately advisory only: they must never block the gateway,
and they must never mutate trading state. The sink fan-outs to local audit/error
streams, Windows toast, and Telegram when configured.
"""

import asyncio
import logging
import os
import platform
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape

logger = logging.getLogger("aletheia.notifications")

_IS_WINDOWS = platform.system() == "Windows"


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


def _send_telegram_alert(message: str, *, level: str = "warning") -> None:
    """Send a Telegram alert to allowed chats when configured. Never raises.

    P1 alerts (info level) are suppressed during DND hours.
    P0 alerts (warning/error/critical) always go through.
    """
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        if not cfg.health.alert_via_telegram or not cfg.telegram.enabled:
            return
        if level == "info" and _is_dnd():
            return
        from aletheia.telegram.bot import TelegramBot
        bot = TelegramBot()
        if not bot.configure():
            return

        async def _send_all():
            for chat_id in bot.allowed_chat_ids:
                try:
                    await bot.send_message(chat_id, f"⚠️ Aletheia: {message}")
                except Exception:
                    pass

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_send_all())
        else:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, _send_all()).result()
    except Exception as e:
        logger.warning("Telegram notification failed (non-critical): %s", e)


def _send_webhook(payload: dict, *, event_type: str = "alert") -> None:
    """POST JSON payload to configured webhook URL. Never raises."""
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        if not cfg.webhook.enabled or not cfg.webhook.url:
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
        urllib.request.urlopen(req, timeout=timeout)
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
    lines = [f"Trade Fill [{mode_label}]",
             f"{dir_label} {symbol}",
             f"Qty: {quantity}  @ {fill_price:.4f}"]
    if stop_price:
        lines.append(f"SL: {stop_price:.4f}")
    if take_profit_price:
        lines.append(f"TP: {take_profit_price:.4f}")
    lines.append(f"pos/{position_id[:8]}")
    msg = "\n".join(lines)
    _send_telegram_alert(msg, level="info")
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
        unrealized = portfolio.get("unrealized_pnl", 0.0)
        open_count = portfolio.get("positions_count", 0)
        total = portfolio.get("total_value_usdt", 0.0)
        sign = "+" if realized >= 0 else ""
        lines = [
            "Daily Summary",
            f"Realized: {sign}{realized:.2f} USDT",
            f"Unrealized: {unrealized:+.2f} USDT",
            f"Open positions: {open_count}",
            f"Portfolio value: {total:.2f} USDT",
        ]
        _send_telegram_alert("\n".join(lines), level="info")
        _send_webhook({
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "open_positions": open_count,
            "total_value_usdt": total,
        }, event_type="daily_summary")
    except Exception as e:
        logger.warning("notify_daily_summary failed: %s", e)
