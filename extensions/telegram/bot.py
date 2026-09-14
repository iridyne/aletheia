"""Telegram bot adapter — light polling-based implementation."""

import asyncio
import logging
import time
from typing import Any

from aletheia.net import httpx_client
from aletheia.config.loader import load_config
from aletheia.config.secrets import get_secret
from aletheia.telegram.commands import TelegramCommands

logger = logging.getLogger("aletheia.telegram")

_RATE_LIMIT_PER_SEC = 1.0  # max 1 message per second per chat (well under Telegram's 30/s)
_UPDATE_FAILURE_RETRY_LIMIT = 5
_rate_limit_last_send: dict[int, float] = {}  # chat_id → monotonic time of last send


class TelegramBot:
    """Minimal Telegram bot with polling. Handles /status /pause /resume /latest /positions /pnl /kill /help."""

    def __init__(self):
        self.token: str | None = None
        self.allowed_chat_ids: set[int] = set()
        self._offset = 0
        self._running = False
        self.commands = TelegramCommands()
        self._consecutive_poll_failures = 0
        self._consecutive_update_failures = 0

    def configure(self) -> bool:
        """Load config and token. Returns True if bot is ready."""
        cfg = load_config()
        if not cfg.telegram.enabled:
            return False
        self.token = get_secret("telegram.bot_token")
        if not self.token:
            logger.warning("Telegram enabled but no bot token configured")
            return False
        self.allowed_chat_ids = set(cfg.telegram.allowed_chat_ids)
        return True

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    async def send_message(self, chat_id: int, text: str, *, parse_mode: str | None = "HTML") -> None:
        # Per-chat rate limit: wait if sending too fast
        now = time.monotonic()
        last = _rate_limit_last_send.get(chat_id, 0.0)
        wait = _RATE_LIMIT_PER_SEC - (now - last)
        if wait > 0:
            await asyncio.sleep(wait)
        _rate_limit_last_send[chat_id] = time.monotonic()

        payload: dict = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            async with httpx_client(timeout=10) as client:
                response = await client.post(
                    self._api_url("sendMessage"), json=payload
                )
        except Exception as exc:
            raise RuntimeError(
                f"Telegram sendMessage transport failed: {type(exc).__name__}"
            ) from None
        status_code = int(getattr(response, "status_code", 200))
        if status_code >= 400:
            raise RuntimeError(f"Telegram sendMessage failed with HTTP {status_code}")
        try:
            accepted = response.json().get("ok") is True
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Telegram sendMessage returned invalid JSON: {type(exc).__name__}"
            ) from None
        if not accepted:
            raise RuntimeError("Telegram sendMessage was rejected")

    async def run(self, poll_interval: float = 2.5) -> None:
        if not self.configure():
            logger.warning("Telegram bot configuration incomplete; polling not started")
            return
        if not self.allowed_chat_ids:
            logger.warning("Telegram enabled but no allowed_chat_ids are configured.")
            return
        self._running = True
        self._consecutive_poll_failures = 0
        self._consecutive_update_failures = 0
        poll_backoff = poll_interval
        update_backoff = poll_interval
        async with httpx_client(timeout=20) as client:
            while self._running:
                try:
                    updates = await self._fetch_updates(client)
                    poll_backoff = poll_interval
                    if self._consecutive_poll_failures > 0:
                        logger.info(
                            "Telegram polling recovered after %d failures",
                            self._consecutive_poll_failures,
                        )
                    self._consecutive_poll_failures = 0
                except Exception as exc:
                    self._consecutive_poll_failures += 1
                    logger.warning(
                        "Telegram polling failed (%d consecutive): %s",
                        self._consecutive_poll_failures,
                        type(exc).__name__,
                    )
                    if self._consecutive_poll_failures == 5:
                        self._escalate_poll_failure(exc)
                    await asyncio.sleep(poll_backoff)
                    poll_backoff = min(poll_backoff * 2, 60.0)
                    continue

                update_failed = False
                for update in updates:
                    try:
                        await self._handle_update(update, client)
                    except Exception as exc:
                        self._consecutive_update_failures += 1
                        logger.warning(
                            "Telegram update processing failed for update_id=%s "
                            "(%d consecutive): %s",
                            update.get("update_id"),
                            self._consecutive_update_failures,
                            type(exc).__name__,
                        )
                        if (
                            self._consecutive_update_failures
                            >= _UPDATE_FAILURE_RETRY_LIMIT
                            and self._escalate_update_failure(update, exc)
                        ):
                            self._offset = max(
                                self._offset,
                                int(update["update_id"]) + 1,
                            )
                            self._consecutive_update_failures = 0
                            update_backoff = poll_interval
                            continue
                        await asyncio.sleep(update_backoff)
                        update_backoff = min(update_backoff * 2, 60.0)
                        update_failed = True
                        break
                    if self._consecutive_update_failures > 0:
                        logger.info(
                            "Telegram update processing recovered after %d failures",
                            self._consecutive_update_failures,
                        )
                    self._consecutive_update_failures = 0
                    update_backoff = poll_interval
                    update_id = update.get("update_id")
                    if update_id is not None:
                        self._offset = max(self._offset, int(update_id) + 1)
                if update_failed:
                    continue
                await asyncio.sleep(poll_interval)

    def stop(self) -> None:
        self._running = False

    def _escalate_poll_failure(self, exc: Exception) -> None:
        """Notify operator via non-Telegram channels when polling repeatedly fails."""
        try:
            from aletheia.files.jsonl import write_error
            write_error({
                "source": "telegram_bot",
                "message": (
                    "Telegram polling failed 5 consecutive times: "
                    f"{type(exc).__name__}"
                ),
            })
        except Exception:
            pass
        try:
            from aletheia.notifications import send_windows_toast

            send_windows_toast(
                "Telegram Bot Disconnected",
                f"Polling failed 5x: {type(exc).__name__}",
            )
        except Exception:
            pass

    def _escalate_update_failure(self, update: dict[str, Any], exc: Exception) -> bool:
        """Durably quarantine a repeatedly failing update without claiming disconnect."""
        try:
            from aletheia.files.jsonl import write_error

            write_error({
                "source": "telegram_bot",
                "message": (
                    "Telegram update quarantined after repeated processing failures "
                    f"for update_id={update.get('update_id')}: {type(exc).__name__}"
                ),
            })
        except Exception:
            return False
        try:
            from aletheia.notifications import send_windows_toast

            send_windows_toast(
                "Telegram Command Processing Failed",
                f"Update failed 5x: {type(exc).__name__}",
            )
        except Exception:
            pass
        return True

    async def _fetch_updates(self, client) -> list[dict[str, Any]]:
        resp = await client.get(self._api_url("getUpdates"), params={"offset": self._offset, "timeout": 20})
        payload = resp.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {payload}")
        updates = payload.get("result")
        if not isinstance(updates, list) or any(
            not isinstance(update, dict)
            or isinstance(update.get("update_id"), bool)
            or not isinstance(update.get("update_id"), int)
            for update in updates
        ):
            raise RuntimeError("Telegram API returned malformed updates")
        return updates

    async def _handle_update(self, update: dict[str, Any], client) -> None:
        message = update.get("message") or {}
        if not message:
            return
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = message.get("text")
        if not chat_id or not text:
            return
        response = await self.commands.handle_message(int(chat_id), str(text))
        if response:
            # Issue #1745: responses are plain text; omit parse_mode so Telegram
            # renders them as-is with no markup interpretation.
            try:
                await self.send_message(int(chat_id), response, parse_mode=None)
            except Exception as exc:
                logger.warning(
                    "Telegram reply delivery failed for update_id=%s: %s",
                    update.get("update_id"),
                    type(exc).__name__,
                )
                try:
                    from aletheia.files.jsonl import write_error

                    write_error({
                        "source": "telegram_bot",
                        "message": f"Telegram reply delivery failed: {type(exc).__name__}",
                    })
                except Exception:
                    pass
