"""Core Telegram command authorization and operator actions; no transport credentials."""

import asyncio
import time

from aletheia.config.loader import load_config
from aletheia.files.jsonl import write_audit

_KILL_CONFIRM_TIMEOUT_SEC = 300.0  # 5 minutes to confirm kill


class TelegramCommands:
    """One command handler and confirmation state per polling session."""

    def __init__(self):
        self._pending_kills: dict[int, float] = {}  # chat_id → monotonic timestamp

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
        from aletheia.config.exchange_alignment import exchange_alignment
        alignment = exchange_alignment(cfg)
        execution_exchange = alignment.execution_exchange
        exchange_label = (
            f"{execution_exchange} ({cfg.exchange.mode})" if execution_exchange
            else "Not configured" if alignment.status == "not_configured" else "Unavailable"
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
            from aletheia.analytics.execution_mode import resolve_selected_execution_mode
            from aletheia.portfolio.pnl import compute_portfolio
            execution_mode = resolve_selected_execution_mode()
            if execution_mode is None:
                return "Today PnL: unavailable (execution_mode_unresolved)."
            portfolio = compute_portfolio(execution_mode=execution_mode)
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
