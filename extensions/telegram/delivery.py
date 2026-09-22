"""Telegram delivery policy and recipient deduplication; no trading authority."""

import asyncio
import hashlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape

logger = logging.getLogger("aletheia.notifications")

# Message dedup: suppress identical messages within this window
_DEDUP_WINDOW_SEC = 120.0
_recent_messages: dict[str, float] = {}  # hash → monotonic timestamp
_inflight_messages: set[str] = set()
# ponytail: one global lock is enough at notification volume; shard by key if contention appears.
_dedup_lock = threading.Lock()
_DEDUP_MAX_ENTRIES = 200


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


def _dedup_key(message: str, recipient: int) -> str:
    return hashlib.md5(f"{recipient}\0{message}".encode(), usedforsecurity=False).hexdigest()


def _reserve_delivery(message: str, recipient: int) -> bool:
    """Reserve a non-duplicate recipient/message pair for delivery."""
    now = time.monotonic()
    key = _dedup_key(message, recipient)
    with _dedup_lock:
        if len(_recent_messages) > _DEDUP_MAX_ENTRIES:
            stale = [
                k
                for k, ts in _recent_messages.items()
                if now - ts > _DEDUP_WINDOW_SEC
            ]
            for stale_key in stale:
                del _recent_messages[stale_key]
        last_sent = _recent_messages.get(key)
        if key in _inflight_messages or (
            last_sent is not None and now - last_sent < _DEDUP_WINDOW_SEC
        ):
            return False
        _inflight_messages.add(key)
        return True


def _mark_delivered(message: str, recipient: int) -> None:
    key = _dedup_key(message, recipient)
    with _dedup_lock:
        _inflight_messages.discard(key)
        _recent_messages[key] = time.monotonic()


def _release_delivery(message: str, recipient: int) -> None:
    with _dedup_lock:
        _inflight_messages.discard(_dedup_key(message, recipient))


def send_telegram_alert(message: str, *, level: str = "warning",
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
        from extensions.telegram.bot import TelegramBot
        bot = TelegramBot()
        if not bot.configure():
            return

        safe_msg = escape(message, quote=False)

        async def _send_all():
            for chat_id in bot.allowed_chat_ids:
                if not _reserve_delivery(message, chat_id):
                    continue
                delivered = False
                try:
                    await bot.send_message(
                        chat_id,
                        f"⚠️ <b>Aletheia:</b> {safe_msg}",
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    logger.warning("Telegram delivery failed for chat_id=%s: %s", chat_id, type(exc).__name__)
                else:
                    delivered = True
                finally:
                    if delivered:
                        _mark_delivered(message, chat_id)
                    else:
                        _release_delivery(message, chat_id)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_send_all())
        else:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, _send_all()).result()
    except Exception as e:
        logger.warning("Telegram notification failed (non-critical): %s", e)
