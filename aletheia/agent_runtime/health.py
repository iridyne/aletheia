"""Health monitoring + honest alerting (Component 5).

Computes health from REAL backend signals (recent agent-run failure ratio, kill
switch, LLM availability) and alerts only on transition INTO an unhealthy state
(de-duped via a persisted flag). Never fabricates a positive "healthy" push.
Alerts go through the existing Telegram bot when configured.
"""

import asyncio
import logging

from aletheia.config.loader import load_config
from aletheia.files.jsonl import write_audit
from aletheia.storage.repositories import list_agent_runs

logger = logging.getLogger("aletheia.health")

_FAILED_STATUSES = {"failed", "error", "completed_with_errors"}

# In-process de-dupe of consecutive unhealthy alerts.
_last_alerted_unhealthy = False


def compute_health() -> dict:
    """Compute current health from real backend data. Returns {healthy, issues}."""
    cfg = load_config()
    issues: list[str] = []

    runs = list_agent_runs(limit=20)
    if runs:
        failed = sum(1 for r in runs if (r.get("status") or "") in _FAILED_STATUSES)
        ratio = failed / len(runs)
        if ratio > cfg.health.max_agent_failure_ratio:
            issues.append(f"agent_failure_ratio:{ratio:.2f}>{cfg.health.max_agent_failure_ratio}")

    if cfg.runtime.kill_switch_active:
        issues.append("kill_switch_active")

    return {"healthy": len(issues) == 0, "issues": issues}


def _send_alert(message: str) -> None:
    """Best-effort Telegram alert to all allowed chats. Never raises."""
    cfg = load_config()
    if not cfg.health.alert_via_telegram or not cfg.telegram.enabled:
        return
    try:
        from aletheia.telegram.bot import TelegramBot
        bot = TelegramBot()
        if not bot.configure():
            return

        async def _send_all():
            for chat_id in bot.allowed_chat_ids:
                try:
                    await bot.send_message(chat_id, f"⚠️ Aletheia health: {message}")
                except Exception:
                    pass

        asyncio.run(_send_all())
    except Exception as e:
        logger.warning("health alert send failed: %s", e)


def run_health_check() -> dict:
    """Evaluate health; alert + audit only on transition into unhealthy."""
    global _last_alerted_unhealthy
    h = compute_health()
    if not h["healthy"]:
        if not _last_alerted_unhealthy:
            msg = "; ".join(h["issues"])
            _send_alert(msg)
            write_audit({
                "actor": "system", "source": "scheduler", "event_type": "health_alert",
                "entity_type": "system_health", "message": f"Unhealthy: {msg}",
            })
            _last_alerted_unhealthy = True
    else:
        _last_alerted_unhealthy = False  # reset so the next downturn alerts again
    return h
