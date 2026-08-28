"""Telegram bot adapter — light polling-based implementation."""

import asyncio
import logging
import time
from typing import Any

from aletheia.net import httpx_client
from aletheia.config.loader import load_config
from aletheia.config.secrets import get_secret
from aletheia.files.jsonl import write_audit

logger = logging.getLogger("aletheia.telegram")

_KILL_CONFIRM_TIMEOUT_SEC = 300.0  # 5 minutes to confirm kill
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
        self._pending_kills: dict[int, float] = {}  # chat_id → monotonic timestamp
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

    async def handle_message(self, chat_id: int, text: str) -> str | None:
        """Handle a telegram command. Returns response text or None."""
        cfg = await asyncio.to_thread(load_config, use_cache=False)
        if not cfg.telegram.enabled:
            auth_error = "Telegram is disabled."
        elif not cfg.telegram.allowed_chat_ids:
            auth_error = "Telegram is enabled but no allowed_chat_ids are configured."
        elif chat_id not in cfg.telegram.allowed_chat_ids:
            auth_error = "Unauthorized."
        else:
            auth_error = None
        if auth_error:
            write_audit({
                "actor": "telegram",
                "source": "telegram",
                "event_type": "unauthorized_access",
                "message": f"Unauthorized telegram message from chat_id={chat_id}",
            })
            return None

        cmd = text.strip().lower()

        if cmd == "/status":
            return self._cmd_status()
        elif cmd == "/pause":
            return self._cmd_pause()
        elif cmd == "/resume":
            return await self._cmd_resume()
        elif cmd == "/latest":
            return self._cmd_latest()
        elif cmd == "/positions":
            return self._cmd_positions()
        elif cmd == "/pnl":
            return self._cmd_pnl()
        elif cmd == "/kill":
            return self._cmd_kill_request(chat_id)
        elif cmd == "/kill confirm":
            return self._cmd_kill_confirm(chat_id)
        elif cmd in ("/help", "/start"):
            return self._cmd_help()
        else:
            return "Unknown command. Try /help"

    def _cmd_status(self) -> str:
        cfg = load_config()
        exchange_label = (
            "Local paper (no external exchange)"
            if cfg.exchange.paper_execution_mode == "paper_internal"
            else f"{cfg.exchange.name} ({cfg.exchange.mode})"
        )
        # Issue #1745: plain text; _handle_update sends with parse_mode=None.
        return (
            f"Gateway: running\n"
            f"Paused: {cfg.runtime.paused}\n"
            f"Exchange: {exchange_label}\n"
            f"Live: {cfg.live.state}\n"
            f"Permission: {cfg.agent.permission_mode}"
        )

    def _cmd_pause(self) -> str:
        from aletheia.agent_runtime.pause_ownership import (
            MANUAL_OWNER,
            acquire_pause,
            pause_acknowledgement,
        )

        acquire_pause(
            MANUAL_OWNER,
            reason="telegram_pause",
            actor="telegram",
            source="telegram",
        )
        write_audit({
            "actor": "telegram",
            "source": "telegram",
            "event_type": "gateway_paused",
            "message": "Paused via Telegram",
        })
        return f"Gateway paused. {pause_acknowledgement()['message']}"

    async def _cmd_resume(self) -> str:
        from aletheia.agent_runtime import resume_gate

        gate = await resume_gate.resume_if_llm_available(
            actor="telegram",
            source="telegram",
            cause="telegram_resume",
        )
        if not gate["allowed"]:
            return f"Resume blocked: {resume_gate.summarize_resume_gate(gate)}"
        write_audit({
            "actor": "telegram",
            "source": "telegram",
            "event_type": "gateway_resumed",
            "message": "Resumed via Telegram",
        })
        return "Gateway resumed."

    def _cmd_latest(self) -> str:
        from aletheia.storage.repositories import list_agent_runs
        runs = list_agent_runs(limit=3)
        if not runs:
            return "No recent agent runs."
        lines = []
        for r in runs:
            lines.append(f"- {r['agent_name']}: {r['status']} ({r['started_at'][:16]})")
        return "Recent runs:\n" + "\n".join(lines)

    def _cmd_positions(self) -> str:
        try:
            from aletheia.storage.repositories import list_positions
            positions = list_positions(status="open")
            if not positions:
                return "No open positions."
            lines = []
            for p in positions[:10]:
                direction = "L" if p["direction"] in ("long", "buy") else "S"
                lines.append(
                    f"{direction} {p['symbol']} x{p['quantity']:.4f} "
                    f"@ {p['entry_price']:.4f} ({p.get('execution_mode', '?')[:6]})"
                )
            suffix = f"\n...+{len(positions)-10} more" if len(positions) > 10 else ""
            return f"Open positions ({len(positions)}):\n" + "\n".join(lines) + suffix
        except Exception as e:
            return f"Error fetching positions: {e}"

    def _cmd_pnl(self) -> str:
        try:
            from aletheia.portfolio.pnl import compute_portfolio
            portfolio = compute_portfolio()
            realized = (
                portfolio.get("today_realized_pnl")
                if portfolio.get("today_realized_pnl_complete") is True
                else None
            )
            unrealized = float(portfolio.get("unrealized_pnl") or 0.0)
            if realized is None:
                return (
                    "Today PnL\n"
                    "Realized: unavailable\n"
                    f"Unrealized: {unrealized:+.2f} USDT\n"
                    "Total: unavailable\n"
                    "Funding or other close economics are incomplete."
                )
            sign = "+" if realized >= 0 else ""
            return (
                f"Today PnL\n"
                f"Realized: {sign}{realized:.2f} USDT\n"
                f"Unrealized: {unrealized:+.2f} USDT\n"
                f"Total: {realized + unrealized:+.2f} USDT"
            )
        except Exception as e:
            return f"Error fetching PnL: {e}"

    def _cmd_kill_request(self, chat_id: int) -> str:
        self._pending_kills[chat_id] = time.monotonic()
        return (
            "WARNING: This will activate the kill switch and halt all trading.\n"
            "Reply /kill confirm within 5 minutes to proceed, or ignore to cancel."
        )

    def _cmd_kill_confirm(self, chat_id: int) -> str:
        requested_at = self._pending_kills.pop(chat_id, None)
        if requested_at is None:
            return "No pending kill request. Send /kill first."
        if time.monotonic() - requested_at > _KILL_CONFIRM_TIMEOUT_SEC:
            return "Kill request expired (>5 min). Send /kill again."
        try:
            from aletheia.risk import kill_switch

            kill_switch.activate(reason="telegram kill confirmation", actor="telegram")
            write_audit({
                "actor": "telegram",
                "source": "telegram",
                "event_type": "kill_switch_activated",
                "message": f"Kill switch activated via Telegram by chat_id={chat_id}",
            })
            return "Kill switch ACTIVATED. All new trading halted."
        except Exception as e:
            return f"Failed to activate kill switch: {e}"

    def _cmd_help(self) -> str:
        # Issue #1745: plain text; no HTML markup needed.
        return (
            "Aletheia Trading OS\n\n"
            "/status — System status\n"
            "/pause — Pause new decisions/trading\n"
            "/resume — Resume agents\n"
            "/latest — Recent agent runs\n"
            "/positions — Open positions\n"
            "/pnl — Today PnL summary\n"
            "/kill — Activate kill switch (2-step)\n"
            "/help — This message"
        )

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
        response = await self.handle_message(int(chat_id), str(text))
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
