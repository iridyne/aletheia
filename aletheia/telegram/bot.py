"""Telegram bot adapter — light polling-based implementation."""

import asyncio
import logging

import httpx

from aletheia.config.loader import load_config, save_config
from aletheia.config.secrets import get_secret
from aletheia.files.jsonl import write_audit

logger = logging.getLogger("aletheia.telegram")


class TelegramBot:
    """Minimal Telegram bot with polling. Handles /status /pause /resume /latest /help."""

    def __init__(self):
        self.token: str | None = None
        self.allowed_chat_ids: set[int] = set()
        self._offset = 0
        self._running = False

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

    async def send_message(self, chat_id: int, text: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(self._api_url("sendMessage"), json={"chat_id": chat_id, "text": text})

    def _is_authorized(self, chat_id: int) -> bool:
        if not self.allowed_chat_ids:
            return True  # If no IDs configured, allow all (dev convenience)
        return chat_id in self.allowed_chat_ids

    async def handle_message(self, chat_id: int, text: str) -> str | None:
        """Handle a telegram command. Returns response text or None."""
        if not self._is_authorized(chat_id):
            write_audit({
                "actor": "telegram",
                "source": "telegram",
                "event_type": "unauthorized_access",
                "message": f"Unauthorized chat_id: {chat_id}",
            })
            return "Unauthorized."

        cmd = text.strip().lower()

        if cmd == "/status":
            return self._cmd_status()
        elif cmd == "/pause":
            return self._cmd_pause()
        elif cmd == "/resume":
            return self._cmd_resume()
        elif cmd == "/latest":
            return self._cmd_latest()
        elif cmd in ("/help", "/start"):
            return self._cmd_help()
        else:
            return "Unknown command. Try /help"

    def _cmd_status(self) -> str:
        cfg = load_config()
        return (
            f"Gateway: running\n"
            f"Paused: {cfg.runtime.paused}\n"
            f"Exchange: {cfg.exchange.name} ({cfg.exchange.mode})\n"
            f"Live: {cfg.live.state}\n"
            f"Permission: {cfg.agent.permission_mode}"
        )

    def _cmd_pause(self) -> str:
        cfg = load_config()
        cfg.runtime.paused = True
        save_config(cfg)
        write_audit({
            "actor": "telegram",
            "source": "telegram",
            "event_type": "gateway_paused",
            "message": "Paused via Telegram",
        })
        return "Gateway paused. All autonomous jobs suspended."

    def _cmd_resume(self) -> str:
        cfg = load_config()
        cfg.runtime.paused = False
        save_config(cfg)
        write_audit({
            "actor": "telegram",
            "source": "telegram",
            "event_type": "gateway_resumed",
            "message": "Resumed via Telegram",
        })
        return "Gateway resumed."

    def _cmd_latest(self) -> str:
        from aletheia.storage.migrations import run_migrations
        from aletheia.storage.repositories import list_agent_runs
        run_migrations()
        runs = list_agent_runs(limit=3)
        if not runs:
            return "No recent agent runs."
        lines = []
        for r in runs:
            lines.append(f"- {r['agent_name']}: {r['status']} ({r['started_at'][:16]})")
        return "Recent runs:\n" + "\n".join(lines)

    def _cmd_help(self) -> str:
        return (
            "Aletheia Trading OS\n\n"
            "/status — System status\n"
            "/pause — Pause all agents\n"
            "/resume — Resume agents\n"
            "/latest — Recent agent runs\n"
            "/help — This message"
        )
